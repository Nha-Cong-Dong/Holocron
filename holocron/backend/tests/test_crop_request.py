"""`CropRequest` dùng chung và `RegionRenderer` (ADR-010 mục 9; ADR-009 mục 5)."""

from __future__ import annotations

import base64
import builtins
import hashlib
import math
import os
import random
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import ValidationError

from holocron_core.infrastructure.document_readers import _pdf_worker
from holocron_core.infrastructure.document_readers._pdf_worker import CROP_ERROR_RESERVE_BYTES, render_crop_batch
from holocron_core.infrastructure.document_readers.config import PdfReaderConfig
from holocron_core.infrastructure.document_readers.pdf_reader import (
    CropFailure,
    CropOutcome,
    RenderedCrop,
    WorkerRegionRenderer,
    verify_crop,
)
from holocron_core.infrastructure.document_readers.pdfium_adapter import PdfiumBackend
from holocron_core.infrastructure.document_readers.protocol import (
    STDIN_REQUEST,
    CropBatchMessage,
    CropError,
    CropPng,
    CropRegion,
    CropRequest,
    PageRequest,
    ReadyMessage,
    WorkerRequest,
    encode_message,
)
from holocron_core.infrastructure.document_readers.raster import (
    GrayImage,
    encode_png_gray,
    expand_box,
    fit_long_edge,
    png_dimensions,
)
from tests.pdf_samples import Box, Page, text_lines, write_pdf

BODY = ["Bang so lieu R13 = 42", "Dong thu hai 0x1F"]


def _png(width: int = 8, height: int = 6) -> bytes:
    return encode_png_gray(GrayImage(pixels=bytes(width * height), width=width, height=height, dpi=72.0))


def _crop(png: bytes, **overrides: object) -> CropPng:
    width, height = png_dimensions(png) or (0, 0)
    fields: dict[str, object] = {
        "region_id": 0,
        "width_px": width,
        "height_px": height,
        "size_bytes": len(png),
        "sha256": hashlib.sha256(png).hexdigest(),
        "png_base64": base64.b64encode(png).decode("ascii"),
    }
    fields.update(overrides)
    return CropPng.model_validate(fields)


# --------------------------------------------------------------------------- #
# Kiểm của tiến trình cha
# --------------------------------------------------------------------------- #


def test_verify_crop_accepts_a_well_formed_png() -> None:
    png = _png()
    assert verify_crop(_crop(png), max_edge_px=64, max_image_bytes=4096) == png


@pytest.mark.parametrize(
    "overrides",
    [
        {"sha256": "0" * 64},
        {"size_bytes": 1},
        {"width_px": 9},
        {"height_px": 5},
        {"png_base64": "not base64!"},
    ],
    ids=["sha", "size", "ihdr-width", "ihdr-height", "base64"],
)
def test_verify_crop_rejects_tampered_fields(overrides: dict[str, object]) -> None:
    assert verify_crop(_crop(_png(), **overrides), max_edge_px=64, max_image_bytes=4096) is None


def test_verify_crop_rejects_wrong_magic() -> None:
    fake = b"GIF89a" + bytes(40)
    crop = CropPng(
        region_id=0,
        width_px=8,
        height_px=6,
        size_bytes=len(fake),
        sha256=hashlib.sha256(fake).hexdigest(),
        png_base64=base64.b64encode(fake).decode("ascii"),
    )
    assert verify_crop(crop, max_edge_px=64, max_image_bytes=4096) is None


def test_verify_crop_rejects_over_edge_and_over_bytes() -> None:
    png = _png(40, 10)
    assert verify_crop(_crop(png), max_edge_px=39, max_image_bytes=4096) is None
    assert verify_crop(_crop(png), max_edge_px=64, max_image_bytes=len(png) - 1) is None


def test_fit_long_edge_keeps_aspect_and_bound() -> None:
    image = GrayImage(pixels=bytes(range(200)) * 30, width=300, height=20, dpi=300.0)
    fitted = fit_long_edge(image, 100)
    assert (fitted.width, fitted.height) == (100, 6)


# --------------------------------------------------------------------------- #
# Giao thức
# --------------------------------------------------------------------------- #


def test_stdin_request_parses_page_and_crop_requests() -> None:
    assert STDIN_REQUEST.validate_json('{"page_index": 2}') == PageRequest(page_index=2)
    crop = CropRequest(regions=[CropRegion(region_id=1, page_index=0, bbox=(0.1, 0.1, 0.5, 0.5))], max_edge_px=512)
    assert STDIN_REQUEST.validate_json(crop.model_dump_json()) == crop


def test_crop_batch_rejects_duplicate_region_ids() -> None:
    png = _png()
    with pytest.raises(ValidationError):
        CropBatchMessage(crops=[_crop(png)], errors=[CropError(region_id=0, code="render_error")])


# --------------------------------------------------------------------------- #
# Worker: trần ảnh, thông điệp, thời gian (tài liệu giả, không tiến trình con)
# --------------------------------------------------------------------------- #


class _NoisyDocument:
    """Thay `PdfiumDocument`: mọi trang là ảnh nhiễu (PNG khó nén) 612 × 792 point."""

    def render_gray(self, index: int, dpi: int, max_pixels: int, *, shrink_factor: float = 0.9) -> GrayImage:
        width, height = max(1, round(612 * dpi / 72)), max(1, round(792 * dpi / 72))
        rng = random.Random(index * 1000 + dpi)
        return GrayImage(pixels=rng.randbytes(width * height), width=width, height=height, dpi=float(dpi))


def _regions(count: int) -> list[CropRegion]:
    return [CropRegion(region_id=k, page_index=0, bbox=(0.1, 0.1, 0.3, 0.2)) for k in range(count)]


def _batch(
    config: PdfReaderConfig, regions: list[CropRegion], monotonic: Callable[[], float] | None = None
) -> CropBatchMessage:
    request = CropRequest(regions=regions, max_edge_px=128)
    if monotonic is None:
        return render_crop_batch(_NoisyDocument(), 1, request, config)
    return render_crop_batch(_NoisyDocument(), 1, request, config, monotonic=monotonic)


def _message_bytes(message: CropBatchMessage) -> int:
    return len(encode_message(message).encode("utf-8")) + 2


def test_image_over_byte_cap_is_rejected_by_the_worker() -> None:
    config = PdfReaderConfig(crop_max_image_bytes=1024)
    batch = _batch(config, _regions(2))
    assert batch.crops == []
    assert [(error.region_id, error.code) for error in batch.errors] == [
        (0, "over_image_bytes"),
        (1, "over_image_bytes"),
    ]


def test_message_cap_is_measured_on_the_encoded_message() -> None:
    config = PdfReaderConfig(max_message_bytes=40_000)
    batch = _batch(config, _regions(10))
    assert 1 <= len(batch.crops) < 10
    assert _message_bytes(batch) <= config.max_message_bytes
    stopped = [error for error in batch.errors if error.code == "over_message_bytes"]
    assert stopped and stopped[0].region_id == len(batch.crops)
    assert {error.region_id for error in stopped} == set(range(len(batch.crops), 10))


def test_time_budget_marks_remaining_regions_over_budget() -> None:
    clock = iter([0.0, 0.0, 31.0, 31.0, 31.0])
    batch = _batch(PdfReaderConfig(), _regions(3), monotonic=lambda: next(clock))
    assert [crop.region_id for crop in batch.crops] == [0]
    assert [(error.region_id, error.code) for error in batch.errors] == [(1, "over_budget"), (2, "over_budget")]


def test_regions_over_request_cap_are_over_budget() -> None:
    config = PdfReaderConfig(crop_max_regions_per_request=2)
    batch = _batch(config, _regions(3))
    assert [crop.region_id for crop in batch.crops] == [0, 1]
    assert [(error.region_id, error.code) for error in batch.errors] == [(2, "over_budget")]


def test_worker_crops_respect_max_edge_and_hash() -> None:
    batch = _batch(PdfReaderConfig(), [CropRegion(region_id=0, page_index=0, bbox=(0.0, 0.0, 1.0, 1.0))])
    crop = batch.crops[0]
    assert max(crop.width_px, crop.height_px) <= 128
    assert verify_crop(crop, max_edge_px=128, max_image_bytes=1024 * 1024) is not None


# --------------------------------------------------------------------------- #
# Tiến trình cha + worker thật
# --------------------------------------------------------------------------- #


def _sample(tmp_path: Path) -> Path:
    page = Page(texts=text_lines(BODY), rects=[Box(300.0, 300.0, 200.0, 100.0)])
    rotated = Page(texts=text_lines(BODY), width=612.0, height=792.0, rotate=90)
    return write_pdf(tmp_path / "crops.pdf", [page, rotated])


def test_worker_renders_regions_in_request_order(tmp_path: Path) -> None:
    path = _sample(tmp_path)
    regions = [
        CropRegion(region_id=7, page_index=0, bbox=(0.05, 0.03, 0.6, 0.12)),
        CropRegion(region_id=3, page_index=0, bbox=(0.0, 0.0, 1.0, 1.0)),
        CropRegion(region_id=5, page_index=0, bbox=(0.5, 0.5, 0.4, 0.6)),  # hộp đảo ⇒ bad_region
        CropRegion(region_id=9, page_index=5, bbox=(0.1, 0.1, 0.2, 0.2)),  # trang không có ⇒ bad_region
    ]
    outcomes = WorkerRegionRenderer().render_regions(path, regions, max_edge_px=512)
    assert [outcome.region_id for outcome in outcomes] == [7, 3, 5, 9]
    first, whole, bad, missing = outcomes
    assert isinstance(first, RenderedCrop) and isinstance(whole, RenderedCrop)
    assert first.width_px > first.height_px and max(first.width_px, first.height_px) <= 512
    # dpi nguyên làm tròn xuống: cạnh dài gần sát nhưng không vượt trần.
    assert 480 <= max(whole.width_px, whole.height_px) <= 512 and whole.height_px > whole.width_px
    for crop in (first, whole):
        assert hashlib.sha256(crop.png).hexdigest() == crop.sha256
        assert png_dimensions(crop.png) == (crop.width_px, crop.height_px)
    assert bad == CropFailure(5, "bad_region") and missing == CropFailure(9, "bad_region")


def test_rotated_page_crop_uses_display_orientation(tmp_path: Path) -> None:
    path = _sample(tmp_path)
    region = CropRegion(region_id=0, page_index=1, bbox=(0.0, 0.0, 1.0, 1.0))
    (outcome,) = WorkerRegionRenderer().render_regions(path, [region], max_edge_px=256)
    assert isinstance(outcome, RenderedCrop)
    assert outcome.width_px > outcome.height_px  # 792 × 612 sau khi xoay 90°


def test_per_page_and_per_document_caps_mark_regions_capped(tmp_path: Path) -> None:
    path = _sample(tmp_path)
    config = PdfReaderConfig(crop_max_per_page=2, crop_max_per_document=3)
    regions = [
        *(CropRegion(region_id=k, page_index=0, bbox=(0.1, 0.1, 0.2, 0.2)) for k in range(3)),
        *(CropRegion(region_id=10 + k, page_index=1, bbox=(0.1, 0.1, 0.2, 0.2)) for k in range(2)),
    ]
    outcomes = WorkerRegionRenderer(config).render_regions(path, regions, max_edge_px=256)
    codes = [outcome.code if isinstance(outcome, CropFailure) else "ok" for outcome in outcomes]
    assert codes == ["ok", "ok", "capped", "ok", "capped"]


def test_unreadable_file_turns_every_region_into_render_error(tmp_path: Path) -> None:
    path = tmp_path / "broken.pdf"
    path.write_bytes(b"not a pdf")
    region = CropRegion(region_id=0, page_index=0, bbox=(0.1, 0.1, 0.2, 0.2))
    assert WorkerRegionRenderer().render_regions(path, [region], max_edge_px=256) == [CropFailure(0, "render_error")]


def test_duplicate_region_ids_are_a_caller_error(tmp_path: Path) -> None:
    region = CropRegion(region_id=0, page_index=0, bbox=(0.1, 0.1, 0.2, 0.2))
    with pytest.raises(ValueError, match="region_id"):
        WorkerRegionRenderer().render_regions(_sample(tmp_path), [region, region], max_edge_px=256)


# --------------------------------------------------------------------------- #
# Review/mutation V02: biên trần của worker (tài liệu giả, không tiến trình con)
# --------------------------------------------------------------------------- #


class _RecordingDocument(_NoisyDocument):
    def __init__(self) -> None:
        self.dpis: list[int] = []

    def render_gray(self, index: int, dpi: int, max_pixels: int, *, shrink_factor: float = 0.9) -> GrayImage:
        self.dpis.append(dpi)
        return super().render_gray(index, dpi, max_pixels, shrink_factor=shrink_factor)


class _OversamplingDocument:
    """Render gấp đôi số điểm ảnh được yêu cầu (khai báo dpi tương ứng): worker vẫn phải thu cạnh dài về trần."""

    def render_gray(self, index: int, dpi: int, max_pixels: int, *, shrink_factor: float = 0.9) -> GrayImage:
        width, height = round(612 * dpi * 2 / 72) + 1, round(792 * dpi * 2 / 72) + 1
        return GrayImage(pixels=bytes(width * height), width=width, height=height, dpi=float(dpi * 2))


def _one(bbox: tuple[float, float, float, float] = (0.1, 0.1, 0.3, 0.2), region_id: int = 0) -> CropRegion:
    return CropRegion(region_id=region_id, page_index=0, bbox=bbox)


def _single_crop_bytes(config: PdfReaderConfig, region: CropRegion) -> tuple[int, int]:
    """(byte PNG, byte thông điệp đã mã hoá) khi vùng được render một mình với trần rộng."""
    batch = _batch(config, [region])
    (crop,) = batch.crops
    return crop.size_bytes, _message_bytes(batch)


def test_region_at_request_deadline_is_over_budget() -> None:
    config = PdfReaderConfig(crop_request_timeout_s=5.0)
    clock = iter([0.0, 5.0, 5.0])
    batch = _batch(config, _regions(1), monotonic=lambda: next(clock))
    assert batch.crops == [] and [(e.region_id, e.code) for e in batch.errors] == [(0, "over_budget")]


def test_png_exactly_at_image_cap_is_kept() -> None:
    size, _ = _single_crop_bytes(PdfReaderConfig(), _one())
    batch = _batch(PdfReaderConfig(crop_max_image_bytes=max(1024, size)), [_one()])
    assert [crop.region_id for crop in batch.crops] == [0] and size >= 1024


def test_message_exactly_at_cap_is_kept() -> None:
    _, message = _single_crop_bytes(PdfReaderConfig(), _one())
    batch = _batch(PdfReaderConfig(max_message_bytes=message), [_one()])
    assert [crop.region_id for crop in batch.crops] == [0] and _message_bytes(batch) == message
    over = _batch(PdfReaderConfig(max_message_bytes=message - 1), [_one()])
    assert over.crops == [] and [e.code for e in over.errors] == ["over_message_bytes"]


def test_after_first_overflow_every_later_region_is_over_message_bytes() -> None:
    big = _one((0.0, 0.0, 1.0, 1.0), 0)
    small = _one((0.1, 0.1, 0.12, 0.12), 1)
    low_dpi = PdfReaderConfig(crop_max_dpi=72)
    _, small_message = _single_crop_bytes(low_dpi, small)
    _, big_message = _single_crop_bytes(low_dpi, big)
    cap = small_message + CROP_ERROR_RESERVE_BYTES + 200
    assert cap < big_message
    batch = _batch(PdfReaderConfig(crop_max_dpi=72, max_message_bytes=cap), [big, small])
    assert batch.crops == []
    assert [(e.region_id, e.code) for e in batch.errors] == [(0, "over_message_bytes"), (1, "over_message_bytes")]


def test_pending_regions_reserve_room_for_their_errors() -> None:
    region = _one()
    _, alone = _single_crop_bytes(PdfReaderConfig(), region)
    cap = alone + 10
    bad = [CropRegion(region_id=k, page_index=0, bbox=(0.5, 0.5, 0.4, 0.6)) for k in range(1, 6)]
    batch = _batch(PdfReaderConfig(max_message_bytes=cap), [region, *bad])
    assert _message_bytes(batch) <= cap
    assert batch.crops == [] and batch.errors[0] == CropError(region_id=0, code="over_message_bytes")


def test_incremental_message_size_matches_the_encoded_message() -> None:
    size = _pdf_worker._BatchSize()
    crops: list[CropPng] = []
    errors: list[CropError] = []
    for index in range(6):
        if index % 2:
            error = CropError(region_id=index, code="bad_region")
            errors.append(error)
            size.add_error(error)
            continue
        crop = _crop_png(index, 8 + index, 6)
        for pending in (0, 3):
            expected = _message_bytes(CropBatchMessage(crops=[*crops, crop], errors=errors))
            assert size.total_with(crop, pending) == expected + pending * CROP_ERROR_RESERVE_BYTES
        crops.append(crop)
        size.add_crop(crop)


def test_page_index_equal_to_page_count_is_bad_region() -> None:
    request = CropRequest(regions=[CropRegion(region_id=0, page_index=2, bbox=(0.1, 0.1, 0.2, 0.2))], max_edge_px=128)
    batch = render_crop_batch(_NoisyDocument(), 2, request, PdfReaderConfig())
    assert [(e.region_id, e.code) for e in batch.errors] == [(0, "bad_region")]


def test_render_dpi_is_rounded_down() -> None:
    config = PdfReaderConfig()
    box = expand_box((0.1, 0.1, 0.3, 0.2), config.crop_margin)
    assert box is not None
    long_pt = max((box[2] - box[0]) * 612, (box[3] - box[1]) * 792)
    exact = 128 * 72 / long_pt
    assert exact != math.floor(exact)
    document = _RecordingDocument()
    render_crop_batch(document, 1, CropRequest(regions=[_one()], max_edge_px=128), config)
    assert document.dpis[-1] == math.floor(exact)


def test_request_edge_is_capped_by_config_edge() -> None:
    config = PdfReaderConfig(crop_max_edge_px=128)
    request = CropRequest(regions=[_one((0.0, 0.0, 1.0, 1.0))], max_edge_px=1024)
    (crop,) = render_crop_batch(_NoisyDocument(), 1, request, config).crops
    assert max(crop.width_px, crop.height_px) <= 128


def test_oversampled_render_is_fitted_to_the_edge() -> None:
    request = CropRequest(regions=[_one((0.03, 0.07, 0.71, 0.53))], max_edge_px=128)
    (crop,) = render_crop_batch(_OversamplingDocument(), 1, request, PdfReaderConfig()).crops
    assert max(crop.width_px, crop.height_px) == 128


def test_worker_exits_when_crop_budget_is_exceeded(tmp_path: Path) -> None:
    path = _sample(tmp_path)
    config = PdfReaderConfig()
    request = WorkerRequest(path=str(path), page_budget=0, config=config, crop_budget=1)
    emitted: list[object] = []
    two = CropRequest(regions=_regions(2), max_edge_px=128)
    _pdf_worker.run(request, PdfiumBackend.load(), [two], emitted.append)
    assert not any(isinstance(message, CropBatchMessage) for message in emitted)
    within = WorkerRequest(path=str(path), page_budget=0, config=config, crop_budget=2)
    emitted.clear()
    _pdf_worker.run(within, PdfiumBackend.load(), [two], emitted.append)
    assert sum(isinstance(message, CropBatchMessage) for message in emitted) == 1


def test_worker_never_writes_files_on_the_crop_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """ADR-009 mục 5, §14: đường `CropRequest` chỉ đọc PDF và trả byte qua thông điệp, không ghi file nào."""
    path = _sample(tmp_path)
    before = sorted(item.name for item in tmp_path.iterdir())
    writes: list[str] = []
    real_open = builtins.open
    real_os_open = os.open

    def guarded_open(file: object, mode: str = "r", *args: object, **kwargs: object) -> object:
        if any(flag in mode for flag in "wax+"):
            writes.append(f"open:{file}:{mode}")
        return real_open(file, mode, *args, **kwargs)

    def guarded_os_open(file: object, flags: int, *args: object, **kwargs: object) -> int:
        if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND):
            writes.append(f"os.open:{file}")
        return real_os_open(file, flags, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded_open)
    monkeypatch.setattr(os, "open", guarded_os_open)
    request = WorkerRequest(path=str(path), page_budget=0, config=PdfReaderConfig(), crop_budget=4)
    emitted: list[object] = []
    regions = [_one((0.0, 0.0, 1.0, 1.0), 0), CropRegion(region_id=1, page_index=1, bbox=(0.1, 0.1, 0.5, 0.5))]
    _pdf_worker.run(request, PdfiumBackend.load(), [CropRequest(regions=regions, max_edge_px=256)], emitted.append)
    monkeypatch.undo()
    batches = [message for message in emitted if isinstance(message, CropBatchMessage)]
    assert len(batches) == 1 and len(batches[0].crops) == 2
    assert writes == []
    assert sorted(item.name for item in tmp_path.iterdir()) == before


# --------------------------------------------------------------------------- #
# Review/mutation V02: tiến trình cha (phiên worker giả)
# --------------------------------------------------------------------------- #


def _crop_png(region_id: int, width: int = 8, height: int = 6) -> CropPng:
    return _crop(_png(width, height), region_id=region_id)


class _FakeSession:
    """Phiên worker giả: mỗi `request_crops` lấy một phản hồi kịch bản (thông điệp hoặc exception)."""

    def __init__(self, replies: list[object], clock: list[float] | None = None, step: float = 0.0) -> None:
        self.replies = replies
        self.sent: list[CropRequest] = []
        self.killed = False
        self.closed = False
        self.clock = clock
        self.step = step

    def request_crops(self, request: CropRequest) -> None:
        self.sent.append(request)

    def receive(self, timeout_s: float) -> object:
        if self.clock is not None:
            self.clock[0] += self.step
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply

    def kill(self) -> None:
        self.killed = True

    def close(self) -> None:
        self.closed = True


class _ScriptedRenderer(WorkerRegionRenderer):
    def __init__(self, session: _FakeSession, config: PdfReaderConfig, clock: list[float] | None = None) -> None:
        super().__init__(config, monotonic=(lambda: clock[0]) if clock is not None else time.monotonic)
        self.session = session

    def _start(self, path: Path, crop_budget: int) -> _FakeSession:
        return self.session


def _render(
    tmp_path: Path,
    replies: list[object],
    regions: list[CropRegion],
    *,
    per_request: int = 1,
    clock: list[float] | None = None,
    step: float = 0.0,
    **config_fields: object,
) -> tuple[list[CropOutcome], _FakeSession]:
    config = PdfReaderConfig.model_validate({"crop_max_regions_per_request": per_request, **config_fields})
    session = _FakeSession(replies, clock, step)
    renderer = _ScriptedRenderer(session, config, clock)
    outcomes = renderer.render_regions(_sample(tmp_path), regions, max_edge_px=1024)
    return outcomes, session


def test_parent_rejects_crop_over_config_edge_even_if_request_edge_is_larger(tmp_path: Path) -> None:
    reply = CropBatchMessage(crops=[_crop_png(0, 100, 10)])
    outcomes, _ = _render(tmp_path, [reply], _regions(1), crop_max_edge_px=64)
    assert outcomes == [CropFailure(0, "render_error")]


def test_parent_marks_batches_capped_at_document_deadline(tmp_path: Path) -> None:
    clock = [0.0]
    first = CropBatchMessage(crops=[_crop_png(0)])
    config_budget = PdfReaderConfig().effective_crop_budget_s
    outcomes, session = _render(tmp_path, [first], _regions(2), clock=clock, step=config_budget)
    assert isinstance(outcomes[0], RenderedCrop) and outcomes[1] == CropFailure(1, "capped")
    assert len(session.sent) == 1


def test_timeout_on_first_batch_marks_rest_over_budget_and_kills(tmp_path: Path) -> None:
    outcomes, session = _render(tmp_path, [TimeoutError()], _regions(2))
    assert outcomes == [CropFailure(0, "over_budget"), CropFailure(1, "over_budget")]
    assert session.killed and len(session.sent) == 1


def test_bad_first_message_stops_sending_and_rest_over_budget(tmp_path: Path) -> None:
    outcomes, session = _render(tmp_path, [ReadyMessage()], _regions(2))
    assert outcomes == [CropFailure(0, "render_error"), CropFailure(1, "over_budget")]
    assert session.killed and len(session.sent) == 1


def test_results_for_regions_outside_the_batch_are_ignored(tmp_path: Path) -> None:
    first = CropBatchMessage(crops=[_crop_png(0)], errors=[CropError(region_id=1, code="bad_region")])
    second = CropBatchMessage(crops=[_crop_png(1), _crop_png(0, 16, 6)])
    outcomes, _ = _render(tmp_path, [first, second], _regions(2))
    assert isinstance(outcomes[0], RenderedCrop) and outcomes[0].width_px == 8
    assert isinstance(outcomes[1], RenderedCrop)
    stray_error = CropBatchMessage(crops=[_crop_png(0)], errors=[CropError(region_id=1, code="bad_region")])
    late = CropBatchMessage(crops=[_crop_png(1)], errors=[CropError(region_id=0, code="render_error")])
    outcomes, _ = _render(tmp_path, [stray_error, late], _regions(2))
    assert isinstance(outcomes[0], RenderedCrop) and isinstance(outcomes[1], RenderedCrop)


def test_batch_missing_a_region_marks_it_render_error(tmp_path: Path) -> None:
    reply = CropBatchMessage(crops=[_crop_png(0)])
    outcomes, _ = _render(tmp_path, [reply], _regions(2), per_request=2)
    assert isinstance(outcomes[0], RenderedCrop) and outcomes[1] == CropFailure(1, "render_error")


def test_verify_crop_rejects_non_canonical_base64_padding_bits() -> None:
    png = _png(9, 7)
    canonical = base64.b64encode(png).decode("ascii")
    assert canonical.endswith("=")
    body = canonical.rstrip("=")
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
    tweaked = next(
        body[:-1] + char + canonical[len(body) :]
        for char in alphabet
        if char != body[-1] and base64.b64decode(body[:-1] + char + canonical[len(body) :]) == png
    )
    assert verify_crop(_crop(png, png_base64=tweaked), max_edge_px=64, max_image_bytes=4096) is None
    assert verify_crop(_crop(png), max_edge_px=64, max_image_bytes=len(png)) == png
