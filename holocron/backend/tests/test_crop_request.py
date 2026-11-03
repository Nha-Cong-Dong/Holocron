"""`CropRequest` dùng chung và `RegionRenderer` (ADR-010 mục 9; ADR-009 mục 5)."""

from __future__ import annotations

import base64
import hashlib
import random
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import ValidationError

from holocron_core.infrastructure.document_readers._pdf_worker import render_crop_batch
from holocron_core.infrastructure.document_readers.config import PdfReaderConfig
from holocron_core.infrastructure.document_readers.pdf_reader import (
    CropFailure,
    RenderedCrop,
    WorkerRegionRenderer,
    verify_crop,
)
from holocron_core.infrastructure.document_readers.protocol import (
    STDIN_REQUEST,
    CropBatchMessage,
    CropError,
    CropPng,
    CropRegion,
    CropRequest,
    PageRequest,
    encode_message,
)
from holocron_core.infrastructure.document_readers.raster import (
    GrayImage,
    encode_png_gray,
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
