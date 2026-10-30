"""Ảnh template trong luật độ phủ ảnh của bộ nhập PDF (ADR-008 mục 6; sửa đổi 2026-10-30, người dùng duyệt).

Ảnh template là ảnh cùng nội dung, cùng vị trí và kích thước xuất hiện trên ≥ 60% số trang VÀ ≥ 3 trang. Độ phủ ảnh
của mọi luật dựa trên độ phủ (trang ảnh ít chữ, ảnh phủ gần kín, phần ảnh của cờ hình) chỉ tính ảnh KHÔNG phải
template. Mọi nhánh lỗi của lượt quét (vượt trần, quá hạn, lỗi) giữ hành vi cũ. Ảnh template không bao giờ làm trang
có lớp chữ rỗng hay rác thoát OCR (I5). PDF mẫu tự tạo (`tests/pdf_samples.py`), không có Tesseract thật.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

from holocron_core.infrastructure.document_readers import _pdf_worker, pdf_reader
from holocron_core.infrastructure.document_readers._pdf_worker import OcrRunner
from holocron_core.infrastructure.document_readers.config import OcrSettings, PdfReaderConfig
from holocron_core.infrastructure.document_readers.pdf_reader import WorkerPdfReader, WorkerSession
from holocron_core.infrastructure.document_readers.pdfium_adapter import PdfiumBackend, PdfiumDocument
from holocron_core.infrastructure.document_readers.protocol import (
    MAX_TEMPLATE_KEYS,
    ImageKey,
    OcrStartedMessage,
    OpenedMessage,
    PageMessage,
    PageRequest,
    RawOcrLine,
    RawPdfPage,
    RawTextLine,
    TemplateScan,
    TemplateScanMessage,
    TemplateScanStartedMessage,
    WorkerRequest,
    decode_message,
    encode_message,
)
from holocron_core.infrastructure.document_readers.raster import GrayImage, PixelBox
from holocron_core.infrastructure.document_readers.template_images import (
    UNREAD_BITS_PER_PIXEL,
    PageImageKeys,
    TemplateFilter,
    find_template_images,
    image_key,
    quantize_bbox,
    scan_template_images,
)
from holocron_core.infrastructure.document_readers.text_layer import content_image_area, needs_ocr_page, ocr_reason
from holocron_core.infrastructure.document_readers.tiers import assemble_text_layer_page, page_flags
from tests.pdf_samples import Box, Page, Text, gray_raster, slow_page, text_lines, write_pdf

CONFIG = PdfReaderConfig()
DOC_ID = "doc_00000000000000ab"
SETTINGS = OcrSettings(tesseract_cmd="tesseract-test", tessdata_dir=None, secondary=None)
FULL = Box(0.0, 0.0, 612.0, 792.0)
# Tiêu đề 40 ký tự không trắng: ≥ `text_sparse_chars` (32) và < `text_full_image_max_chars` (200), nên trên ảnh phủ
# kín chỉ luật (d) `image_covers_page` áp dụng.
TITLE_40 = "Chuong1Gioithieuvixulyvacautrucbonho2026"
TITLE_SHORT = "Chuong 1"  # 7 ký tự: luật (c) `sparse_text_on_image`
BODY = [
    "The scheduler gives each ready process the processor for one time quantum.",
    "When the quantum expires the process moves to the tail of the ready queue.",
    "Shorter quanta improve response time but add context switch overhead.",
    "The average waiting time depends on the quantum and on arrival order.",
]
TCVN3 = ["HÖ ®iÒu hµnh cÊp ph¸t CPU cho c¸c tiÕn tr×nh theo l­ît vµ ®­îc ch¹y tèi ®a mét l­îng tö."] * 4
BACKGROUND = 200  # giá trị điểm ảnh của ảnh nền khung slide (cùng nội dung trên mọi trang)
LOGGER = "holocron_core.infrastructure.document_readers.pdf_reader"


def _title(value: str = TITLE_40) -> Text:
    return Text(x=20.0, y=770.0, value=value, size=8.0)


def _slide(value: str = TITLE_40) -> Page:
    """Slide có ảnh nền phủ kín (cùng điểm ảnh trên mọi trang, mỗi trang một đối tượng ảnh) và một dòng tiêu đề."""
    return Page(texts=[_title(value)], rasters=[gray_raster(FULL, BACKGROUND)])


def _summary(path: Path, config: PdfReaderConfig = CONFIG) -> list[tuple[str, str | None, str | None, bool]]:
    pages = list(WorkerPdfReader(config).read_pages(path, DOC_ID))
    return [(p.status, p.tier_used, p.failure_reason, p.has_figure) for p in pages]


class StubPrimary:
    def recognize(
        self, png: bytes, image: GrayImage, config: PdfReaderConfig
    ) -> tuple[list[RawOcrLine], list[PixelBox]]:
        line = RawOcrLine(text="Sơ đồ khối", bbox=(0.1, 0.1, 0.5, 0.15), words=[], conf=0.95)
        return [line], [(0, 0, image.width, image.height)]


def _run_worker(
    path: Path, page_count: int, config: PdfReaderConfig = CONFIG, *, template_scan: TemplateScan | None = None
) -> list[BaseModel]:
    """Vòng đọc của worker thật (PDFium thật, OCR giả `StubPrimary`), có yêu cầu quét ảnh template."""
    emitted: list[BaseModel] = []
    request = WorkerRequest(
        path=str(path),
        page_budget=page_count,
        config=config,
        ocr=SETTINGS,
        scan_templates=template_scan is None,
        template_scan=template_scan,
    )
    ocr = OcrRunner(primary=StubPrimary(), secondary=None)
    requests = [PageRequest(page_index=i) for i in range(page_count)]
    _pdf_worker.run(request, PdfiumBackend.load(), requests, emitted.append, ocr)
    return emitted


def _scan(emitted: list[BaseModel]) -> TemplateScan:
    [scan] = [m.scan for m in emitted if isinstance(m, TemplateScanMessage)]
    return scan


def _pages(emitted: list[BaseModel]) -> list[RawPdfPage]:
    return [m.page for m in emitted if isinstance(m, PageMessage)]


def _ocr_pages(emitted: list[BaseModel]) -> list[int]:
    return [m.page_index for m in emitted if isinstance(m, OcrStartedMessage)]


def _open(path: Path) -> PdfiumDocument:
    document = PdfiumBackend.load().open(path.read_bytes())
    assert isinstance(document, PdfiumDocument)
    return document


def _scan_file(path: Path, config: PdfReaderConfig = CONFIG) -> TemplateScan:
    with _open(path) as document:
        return scan_template_images(document, document.page_count(), config)


def _config(**update: object) -> PdfReaderConfig:
    return PdfReaderConfig.model_validate({**CONFIG.model_dump(), "ocr_dpi": 36, **update})


# --------------------------------------------------------------------------- #
# Hàm thuần: khoá, làm tròn, định nghĩa ảnh template
# --------------------------------------------------------------------------- #


def _key(digest_char: str = "a", bbox: tuple[int, int, int, int] = (0, 0, 100, 100)) -> ImageKey:
    return ImageKey(digest=digest_char * 64, width_px=4, height_px=4, bits_per_pixel=0, filters=(), bbox=bbox)


def test_quantize_bbox_rounds_half_up_on_the_configured_grid() -> None:
    assert quantize_bbox((0.0, 0.0, 1.0, 1.0), 0.01) == (0, 0, 100, 100)
    assert quantize_bbox((0.004, 0.005, 0.996, 0.994), 0.01) == (0, 1, 100, 99)
    assert quantize_bbox((0.024, 0.026, 0.5, 0.5), 0.05) == (0, 1, 10, 10)
    assert quantize_bbox((1.0, 1.0, 1.0, 1.0), 1e-4) == (10_000, 10_000, 10_000, 10_000)


def test_image_key_uses_content_and_rounded_position() -> None:
    # Sửa 2026-10-30 (review ảnh template): khoá không còn số bit mỗi điểm ảnh (chỉ đọc được qua metadata của PDFium,
    # lời gọi có thể giải mã ảnh); trường `bits_per_pixel` luôn là `UNREAD_BITS_PER_PIXEL`.
    first = image_key("f" * 64, 4, 4, ["FlateDecode"], (0.0, 0.0, 0.5, 0.5), 0.01)
    assert first.bbox == (0, 0, 50, 50) and first.filters == ("FlateDecode",)
    assert first.bits_per_pixel == UNREAD_BITS_PER_PIXEL
    assert first == image_key("f" * 64, 4, 4, ["FlateDecode"], (0.001, 0.0, 0.5, 0.5), 0.01)
    assert first != image_key("e" * 64, 4, 4, ["FlateDecode"], (0.0, 0.0, 0.5, 0.5), 0.01)
    assert first != image_key("f" * 64, 4, 4, ["FlateDecode"], (0.0, 0.0, 0.5, 0.6), 0.01)
    assert first != image_key("f" * 64, 4, 4, [], (0.0, 0.0, 0.5, 0.5), 0.01)
    assert first != image_key("f" * 64, 8, 4, ["FlateDecode"], (0.0, 0.0, 0.5, 0.5), 0.01)
    assert first != image_key("f" * 64, 4, 8, ["FlateDecode"], (0.0, 0.0, 0.5, 0.5), 0.01)


@pytest.mark.parametrize(
    ("pages_with_key", "page_count", "expected"),
    [
        (3, 5, True),  # 60% và 3 trang: đúng biên cả hai điều kiện
        (2, 3, False),  # 67% nhưng chỉ 2 trang
        (3, 6, False),  # 3 trang nhưng 50%
        (3, 3, True),
        (6, 10, True),
        (5, 10, False),
        (2, 2, False),  # tài liệu 2 trang: không có ảnh template
        (1, 1, False),
    ],
)
def test_template_needs_both_page_ratio_and_page_count(pages_with_key: int, page_count: int, expected: bool) -> None:
    key = _key()
    pages = [[key] if i < pages_with_key else [] for i in range(page_count)]
    assert (key in find_template_images(pages, page_count, CONFIG)) == expected


def test_denominator_is_all_pages_and_repeats_on_one_page_count_once() -> None:
    key = _key()
    # Ba trang đã quét đều có ảnh, nhưng tài liệu có 6 trang: 50% < 60%.
    assert find_template_images([[key]] * 3, 6, CONFIG) == frozenset()
    # Ảnh lặp 3 lần trên cùng một trang chỉ tính một trang.
    assert find_template_images([[key, key, key], [], [], [], []], 5, CONFIG) == frozenset()
    with pytest.raises(ValueError, match="số trang"):
        find_template_images([[key]] * 4, 3, CONFIG)


def test_template_thresholds_are_configurable() -> None:
    key = _key()
    pages = [[key], [key], [key], [], []]
    assert find_template_images(pages, 5, CONFIG) == {key}
    assert find_template_images(pages, 5, _config(template_image_min_pages=4)) == frozenset()
    assert find_template_images(pages, 5, _config(template_image_min_page_ratio=0.61)) == frozenset()
    assert find_template_images(pages, 5, _config(template_image_min_page_ratio=0.4)) == {key}


def test_only_identical_keys_are_counted_together() -> None:
    # Sửa 2026-10-30 (dung sai đối xứng): hộp lệch MỘT ô lưới nay là cùng chỗ (xem test dung sai bên dưới), nên ca
    # "khác chỗ" dùng hộp lệch hai ô.
    pages = [[_key("a")], [_key("a", (0, 0, 100, 98))], [_key("b")], [_key("a")], [_key("a")]]
    assert find_template_images(pages, 5, CONFIG) == {_key("a")}


@pytest.mark.parametrize(
    "bad",
    [
        {"template_image_min_page_ratio": 0.0},
        {"template_image_min_page_ratio": 1.01},
        {"template_image_min_pages": 2},
        {"template_image_bbox_step": 0.0},
        {"template_image_bbox_step": 0.2},
        {"template_scan_timeout_s": 0.0},
        {"template_scan_max_images_per_page": 0},
        {"template_scan_max_images": 0},
        {"template_scan_max_image_bytes": 0},
        {"template_scan_max_bytes": 0},
        {"template_scan_max_images_per_page": 11, "template_scan_max_images": 10},
        {"template_scan_max_image_bytes": 11, "template_scan_max_bytes": 10},
    ],
)
def test_template_config_is_validated(bad: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        PdfReaderConfig.model_validate(bad)


def test_default_template_config() -> None:
    assert (CONFIG.template_image_min_page_ratio, CONFIG.template_image_min_pages) == (0.60, 3)
    assert CONFIG.template_image_bbox_step == 0.01
    assert CONFIG.template_scan_timeout_s == 20.0
    assert (CONFIG.template_scan_max_images_per_page, CONFIG.template_scan_max_images) == (256, 20_000)
    assert (CONFIG.template_scan_max_image_bytes, CONFIG.template_scan_max_bytes) == (16 * 2**20, 512 * 2**20)


# --------------------------------------------------------------------------- #
# Model giao thức
# --------------------------------------------------------------------------- #


def test_protocol_models_are_bounded_and_strict() -> None:
    with pytest.raises(ValidationError):
        TemplateScan(status="timeout", keys=[_key()])
    with pytest.raises(ValidationError):
        TemplateScan(status="ok", keys=[_key()] * (MAX_TEMPLATE_KEYS + 1))
    assert len(TemplateScan(status="ok", keys=[_key()] * MAX_TEMPLATE_KEYS).keys) == MAX_TEMPLATE_KEYS
    for bad_digest in ("A" * 64, "a" * 63, "g" * 64):
        with pytest.raises(ValidationError):
            ImageKey(digest=bad_digest, width_px=1, height_px=1, bits_per_pixel=0, filters=(), bbox=(0, 0, 1, 1))
    with pytest.raises(ValidationError):
        ImageKey(digest="a" * 64, width_px=1, height_px=1, bits_per_pixel=0, filters=("x" * 65,), bbox=(0, 0, 1, 1))
    with pytest.raises(ValidationError):
        ImageKey(digest="a" * 64, width_px=1, height_px=1, bits_per_pixel=0, filters=(), bbox=(0, 0, 1, 10_001))
    with pytest.raises(ValidationError):  # khoá không dùng bpp: chỉ nhận 0
        ImageKey(digest="a" * 64, width_px=1, height_px=1, bits_per_pixel=8, filters=(), bbox=(0, 0, 1, 1))
    for field in ("width_px", "height_px"):
        sizes = {"width_px": 1, "height_px": 1, field: 1_000_001}
        with pytest.raises(ValidationError):
            ImageKey(digest="a" * 64, **sizes, bits_per_pixel=0, filters=(), bbox=(0, 0, 1, 1))
    with pytest.raises(ValidationError):
        WorkerRequest(
            path="x.pdf", page_budget=1, config=CONFIG, scan_templates=True, template_scan=TemplateScan(status="ok")
        )
    with pytest.raises(ValidationError):
        TemplateScan.model_validate({"status": "ok", "keys": [], "extra": 1})
    page = RawPdfPage(page_index=0, width_pt=612, height_pt=792, lines=[], image_area_ratio=0.5, path_area_ratio=0)
    with pytest.raises(ValidationError):
        RawPdfPage.model_validate({**page.model_dump(), "content_image_area_ratio": 1.5})
    message = TemplateScanMessage(scan=TemplateScan(status="ok", keys=[_key()]))
    assert decode_message(encode_message(message)) == message
    assert decode_message(encode_message(TemplateScanStartedMessage())) == TemplateScanStartedMessage()


def test_content_image_area_falls_back_to_all_images() -> None:
    page = RawPdfPage(page_index=0, width_pt=612, height_pt=792, lines=[], image_area_ratio=0.5, path_area_ratio=0)
    assert content_image_area(page) == 0.5
    assert content_image_area(page.model_copy(update={"content_image_area_ratio": 0.0})) == 0.0
    assert content_image_area(page.model_copy(update={"content_image_area_ratio": 0.2})) == 0.2


def test_template_filter_only_for_successful_scans_with_keys() -> None:
    assert TemplateFilter.from_scan(TemplateScan(status="ok"), CONFIG) is None
    for status in ("over_cap", "timeout", "error", "skipped"):
        assert TemplateFilter.from_scan(TemplateScan(status=status), CONFIG) is None
    templates = TemplateFilter.from_scan(TemplateScan(status="ok", keys=[_key()]), CONFIG)
    assert templates is not None and templates.keys == {_key()}
    assert (templates.bbox_step, templates.max_image_bytes, templates.max_images_per_page) == (
        CONFIG.template_image_bbox_step,
        CONFIG.template_scan_max_image_bytes,
        CONFIG.template_scan_max_images_per_page,
    )


# --------------------------------------------------------------------------- #
# Lượt quét trên nguồn giả: trần, hạn, lỗi
# --------------------------------------------------------------------------- #


class FakeSource:
    def __init__(self, pages: list[PageImageKeys | None | Exception]) -> None:
        self.pages = pages
        self.read: list[int] = []

    def page_image_keys(self, index: int, config: PdfReaderConfig) -> PageImageKeys | None:
        self.read.append(index)
        page = self.pages[index]
        if isinstance(page, Exception):
            raise page
        return page


def _keys(count: int, size: int = 10, key: ImageKey | None = None) -> PageImageKeys:
    return PageImageKeys(keys=[key or _key()] * count, hashed_bytes=size * count)


def test_scan_finds_templates_and_sorts_keys() -> None:
    a, b = _key("b"), _key("a")
    source = FakeSource([PageImageKeys(keys=[a, b, None], hashed_bytes=20)] * 4)
    scan = scan_template_images(source, 4, CONFIG)
    assert scan == TemplateScan(status="ok", keys=[b, a])
    assert source.read == [0, 1, 2, 3]


def test_scan_of_short_document_reads_nothing() -> None:
    source = FakeSource([_keys(1)] * 2)
    assert scan_template_images(source, 2, CONFIG) == TemplateScan(status="ok")
    assert source.read == []


@pytest.mark.parametrize(
    ("pages", "update"),
    [
        ([_keys(1), None, _keys(1)], {}),  # một trang vượt trần số ảnh mỗi trang
        ([_keys(2)] * 3, {"template_scan_max_images": 5, "template_scan_max_images_per_page": 2}),
        ([_keys(1, size=10)] * 3, {"template_scan_max_bytes": 25, "template_scan_max_image_bytes": 10}),
    ],
)
def test_scan_over_cap_has_no_templates(pages: list[PageImageKeys | None], update: dict[str, object]) -> None:
    config = _config(**update)
    assert scan_template_images(FakeSource(list(pages)), 3, config) == TemplateScan(status="over_cap")


def test_scan_at_caps_is_still_ok() -> None:
    config = _config(
        template_scan_max_images=6,
        template_scan_max_images_per_page=2,
        template_scan_max_bytes=60,
        template_scan_max_image_bytes=20,
    )
    assert scan_template_images(FakeSource([_keys(2, size=10)] * 3), 3, config).status == "ok"


def test_scan_with_too_many_template_keys_is_over_cap() -> None:
    keys = [_key(digest_char="a", bbox=(i % 100, i // 100, 100, 100)) for i in range(MAX_TEMPLATE_KEYS + 1)]
    page = PageImageKeys(keys=list(keys), hashed_bytes=0)
    config = _config(template_scan_max_images_per_page=2000, template_scan_max_images=10_000)
    assert scan_template_images(FakeSource([page] * 3), 3, config) == TemplateScan(status="over_cap")


def test_scan_error_on_any_page_has_no_templates() -> None:
    source = FakeSource([_keys(1), RuntimeError("hỏng"), _keys(1), _keys(1)])
    assert scan_template_images(source, 4, CONFIG) == TemplateScan(status="error")
    assert source.read == [0, 1]


def test_scan_timeout_has_no_templates() -> None:
    ticks = iter([0.0, 0.0, 5.0, 25.0])

    def clock() -> float:
        return next(ticks)

    source = FakeSource([_keys(1)] * 4)
    assert scan_template_images(source, 4, CONFIG, clock=clock) == TemplateScan(status="timeout")
    assert source.read == [0, 1]
    # Hết hạn đúng sau trang cuối cũng là quá hạn.
    late = iter([0.0, 0.0, 0.0, 0.0, 30.0])
    assert scan_template_images(FakeSource([_keys(1)] * 3), 3, CONFIG, clock=lambda: next(late)).status == "timeout"


def test_scan_deadline_has_no_grace() -> None:
    # Vượt hạn dưới 1 s ⇒ đã quá hạn (hạn = lúc bắt đầu + `template_scan_timeout_s`, không cộng dư).
    over = iter([0.0, CONFIG.template_scan_timeout_s + 0.5])
    source = FakeSource([_keys(1)] * 3)
    assert scan_template_images(source, 3, CONFIG, clock=lambda: next(over)) == TemplateScan(status="timeout")
    assert source.read == []


def test_scan_exactly_at_deadline_is_still_ok() -> None:
    # Đồng hồ đúng bằng hạn ở mọi lần kiểm ⇒ chưa quá hạn (chỉ `clock() > deadline` mới là quá hạn).
    ticks = iter([0.0])

    def clock() -> float:
        return next(ticks, CONFIG.template_scan_timeout_s)

    source = FakeSource([_keys(1)] * 3)
    assert scan_template_images(source, 3, CONFIG, clock=clock) == TemplateScan(status="ok", keys=[_key()])
    assert source.read == [0, 1, 2]


def test_scan_with_exactly_1024_template_keys_is_ok() -> None:
    keys = [_key(digest_char="a", bbox=(i % 100, i // 100, 100, 100)) for i in range(1024)]
    page = PageImageKeys(keys=list(keys), hashed_bytes=0)
    config = _config(template_scan_max_images_per_page=2000, template_scan_max_images=10_000)
    scan = scan_template_images(FakeSource([page] * 3), 3, config)
    assert scan.status == "ok" and len(scan.keys) == 1024 and set(scan.keys) == set(keys)


def test_template_scan_accepts_1024_keys_and_rejects_1025() -> None:
    # Số viết thẳng (không dùng hằng): trần giao thức là đúng 1024 khoá.
    assert len(TemplateScan(status="ok", keys=[_key()] * 1024).keys) == 1024
    with pytest.raises(ValidationError):
        TemplateScan(status="ok", keys=[_key()] * 1025)


# --------------------------------------------------------------------------- #
# Khoá ảnh thật qua PDFium
# --------------------------------------------------------------------------- #


def test_same_pixels_in_different_objects_are_a_template(tmp_path: Path) -> None:
    path = write_pdf(tmp_path / "slides.pdf", [_slide() for _ in range(3)])
    scan = _scan_file(path)
    assert scan.status == "ok" and len(scan.keys) == 1
    [key] = scan.keys
    assert (key.width_px, key.height_px, key.bits_per_pixel, key.filters) == (4, 4, 0, ("FlateDecode",))
    assert key.bbox == (0, 0, 100, 100)


def test_shared_image_object_at_same_place_is_a_template(tmp_path: Path) -> None:
    pages = [Page(texts=[_title()], images=[FULL]) for _ in range(3)]
    assert len(_scan_file(write_pdf(tmp_path / "shared.pdf", pages)).keys) == 1


def test_same_place_different_pixels_is_not_a_template(tmp_path: Path) -> None:
    pages = [Page(texts=[_title()], rasters=[gray_raster(FULL, 10 * i)]) for i in range(6)]
    assert _scan_file(write_pdf(tmp_path / "scan.pdf", pages)) == TemplateScan(status="ok")


def test_position_tolerance(tmp_path: Path) -> None:
    near = [Page(rasters=[gray_raster(Box(float(i), 0.0, 300.0, 300.0), BACKGROUND)]) for i in range(3)]
    assert len(_scan_file(write_pdf(tmp_path / "near.pdf", near)).keys) == 1  # lệch ≤ 2 pt: cùng ô lưới
    far = [Page(rasters=[gray_raster(Box(30.0 * i, 0.0, 300.0, 300.0), BACKGROUND)]) for i in range(3)]
    assert _scan_file(write_pdf(tmp_path / "far.pdf", far)) == TemplateScan(status="ok")
    resized = [Page(rasters=[gray_raster(Box(0.0, 0.0, 300.0 + 40.0 * i, 300.0), BACKGROUND)]) for i in range(3)]
    assert _scan_file(write_pdf(tmp_path / "resized.pdf", resized)) == TemplateScan(status="ok")
    coarse = _config(template_image_bbox_step=0.1)
    assert len(_scan_file(write_pdf(tmp_path / "far2.pdf", far[:1] * 3), coarse).keys) == 1


def test_position_is_normalized_to_the_page_box(tmp_path: Path) -> None:
    # Cùng ảnh ở cùng vị trí tương đối trên trang khác khổ ⇒ cùng khoá.
    pages = [
        Page(rasters=[gray_raster(Box(0.0, 0.0, 306.0 * k, 396.0 * k), BACKGROUND)], width=612.0 * k, height=792.0 * k)
        for k in (1.0, 2.0, 1.5)
    ]
    scan = _scan_file(write_pdf(tmp_path / "sizes.pdf", pages))
    assert len(scan.keys) == 1 and scan.keys[0].bbox == (0, 50, 50, 100)


def test_template_boundary_on_generated_pdf(tmp_path: Path) -> None:
    def document(with_background: int, total: int) -> Path:
        pages = [
            Page(texts=[_title()], rasters=[gray_raster(FULL, BACKGROUND)] if i < with_background else [])
            for i in range(total)
        ]
        return write_pdf(tmp_path / f"b{with_background}-{total}.pdf", pages)

    assert len(_scan_file(document(3, 5)).keys) == 1  # 60%, 3 trang
    assert _scan_file(document(2, 3)).keys == []  # 2 trang
    assert _scan_file(document(3, 6)).keys == []  # 50%


def test_image_byte_cap_makes_images_keyless(tmp_path: Path) -> None:
    path = write_pdf(tmp_path / "slides.pdf", [_slide() for _ in range(3)])
    assert _scan_file(path, _config(template_scan_max_image_bytes=1)) == TemplateScan(status="ok")
    assert _scan_file(path, _config(template_scan_max_bytes=20, template_scan_max_image_bytes=20)).status == "over_cap"


def test_image_count_cap_on_generated_pdf(tmp_path: Path) -> None:
    logo = gray_raster(Box(500.0, 700.0, 100.0, 80.0), 30)
    pages = [Page(texts=[_title()], rasters=[gray_raster(FULL, BACKGROUND), logo]) for _ in range(3)]
    path = write_pdf(tmp_path / "two-images.pdf", pages)
    assert len(_scan_file(path).keys) == 2
    assert _scan_file(path, _config(template_scan_max_images_per_page=1)).status == "over_cap"
    assert (
        _scan_file(path, _config(template_scan_max_images=5, template_scan_max_images_per_page=2)).status == "over_cap"
    )
    assert _scan_file(path, _config(template_scan_max_images=6, template_scan_max_images_per_page=2)).status == "ok"


def test_read_page_subtracts_only_template_images(tmp_path: Path) -> None:
    content = gray_raster(Box(0.0, 0.0, 612.0, 396.0), 40)
    pages = [_slide() for _ in range(3)]
    pages[1].rasters.append(content)
    path = write_pdf(tmp_path / "slides.pdf", pages)
    scan = _scan_file(path)
    templates = TemplateFilter.from_scan(scan, CONFIG)
    assert templates is not None
    with _open(path) as document:
        plain = document.read_page(1)
        filtered = [document.read_page(i, templates) for i in range(3)]
    assert plain.content_image_area_ratio is None and plain.image_area_ratio == pytest.approx(1.0)
    assert [p.image_area_ratio for p in filtered] == [pytest.approx(1.0)] * 3
    assert [p.content_image_area_ratio for p in filtered] == [0.0, pytest.approx(0.5), 0.0]
    # Trang vượt trần số ảnh khi đọc: không loại ảnh nào.
    capped = TemplateFilter(templates.keys, templates.bbox_step, templates.max_image_bytes, max_images_per_page=1)
    with _open(path) as document:
        assert document.read_page(1, capped).content_image_area_ratio == pytest.approx(1.0)
        assert document.read_page(0, capped).content_image_area_ratio == 0.0


def test_unreadable_image_counts_as_content(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = write_pdf(tmp_path / "slides.pdf", [_slide() for _ in range(3)])
    templates = TemplateFilter.from_scan(_scan_file(path), CONFIG)
    assert templates is not None

    def broken(self: PdfiumBackend, image: object, max_bytes: int) -> tuple[str, int] | None:
        raise RuntimeError("hỏng")

    monkeypatch.setattr(PdfiumBackend, "image_digest", broken)
    with _open(path) as document:
        assert document.read_page(0, templates).content_image_area_ratio == pytest.approx(1.0)
        assert scan_template_images(document, 3, CONFIG) == TemplateScan(status="error")


# --------------------------------------------------------------------------- #
# Worker thật (trong tiến trình test, OCR giả) và tiến trình cha
# --------------------------------------------------------------------------- #


def test_worker_scans_once_before_the_first_page_and_reports_it(tmp_path: Path) -> None:
    path = write_pdf(tmp_path / "slides.pdf", [_slide() for _ in range(6)])
    emitted = _run_worker(path, 6, _config())
    kinds = [type(m).__name__ for m in emitted]
    assert kinds[:3] == ["OpenedMessage", "TemplateScanStartedMessage", "TemplateScanMessage"]
    assert kinds.count("TemplateScanMessage") == 1 and kinds.count("PageMessage") == 6
    assert len(_scan(emitted).keys) == 1
    assert [p.content_image_area_ratio for p in _pages(emitted)] == [0.0] * 6
    assert _ocr_pages(emitted) == []


def test_worker_reuses_the_given_scan_without_scanning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = write_pdf(tmp_path / "slides.pdf", [_slide() for _ in range(6)])
    held = _scan(_run_worker(path, 6, _config()))

    def no_scan(*_: object) -> TemplateScan:
        raise AssertionError("worker dựng lại không được quét lại")

    monkeypatch.setattr(_pdf_worker, "scan_template_images", no_scan)
    emitted = _run_worker(path, 6, _config(), template_scan=held)
    assert not any(isinstance(m, TemplateScanStartedMessage | TemplateScanMessage) for m in emitted)
    assert [p.content_image_area_ratio for p in _pages(emitted)] == [0.0] * 6
    # Kết quả quét giữ lại không thành công ⇒ không loại ảnh nào, kể cả khi tài liệu có ảnh lặp.
    emitted = _run_worker(path, 6, _config(), template_scan=TemplateScan(status="timeout"))
    assert [p.content_image_area_ratio for p in _pages(emitted)] == [None] * 6
    assert _ocr_pages(emitted) == list(range(6))


def test_probe_worker_does_not_scan(tmp_path: Path) -> None:
    path = write_pdf(tmp_path / "slides.pdf", [_slide() for _ in range(3)])
    emitted: list[BaseModel] = []
    request = WorkerRequest(path=str(path), page_budget=0, config=CONFIG, scan_templates=True)
    _pdf_worker.run(request, PdfiumBackend.load(), [], emitted.append)
    assert emitted == [OpenedMessage(page_count=3)]


def test_worker_with_a_one_page_budget_still_scans(tmp_path: Path) -> None:
    path = write_pdf(tmp_path / "slides.pdf", [_slide() for _ in range(4)])
    emitted: list[BaseModel] = []
    request = WorkerRequest(path=str(path), page_budget=1, config=_config(), scan_templates=True)
    ocr = OcrRunner(primary=StubPrimary(), secondary=None)
    _pdf_worker.run(request, PdfiumBackend.load(), [PageRequest(page_index=2)], emitted.append, ocr)
    kinds = [type(m).__name__ for m in emitted]
    assert kinds == ["OpenedMessage", "TemplateScanStartedMessage", "TemplateScanMessage", "PageMessage"]
    assert len(_scan(emitted).keys) == 1
    [page] = _pages(emitted)
    assert page.page_index == 2 and page.content_image_area_ratio == 0.0


def test_scan_cpu_budget_for_a_one_page_worker() -> None:
    config = _config(template_scan_timeout_s=7.5)
    request = WorkerRequest(path="x.pdf", page_budget=1, config=config, scan_templates=True)
    assert _pdf_worker.scan_cpu_seconds(request) == 8
    assert _pdf_worker.scan_cpu_seconds(request.model_copy(update={"page_budget": 0})) == 0


def test_scan_adds_explicit_cpu_budget(tmp_path: Path) -> None:
    limits: list[tuple[int, int]] = []
    config = _config(template_scan_timeout_s=7.5)
    for scan_templates, budget, extra in ((True, 3, 8), (False, 3, 0), (True, 0, 0)):
        request = WorkerRequest(path="x.pdf", page_budget=budget, config=config, scan_templates=scan_templates)
        _pdf_worker.start(
            request,
            load=PdfiumBackend.load,
            warm=lambda _backend: None,
            limit=lambda mem, cpu: limits.append((mem, cpu)),
            emit=lambda _message: None,
        )
        assert limits[-1] == (config.worker_mem_bytes, config.worker_cpu_seconds_per_page * (budget + 1) + extra)


@pytest.mark.parametrize("title", [TITLE_40, TITLE_SHORT])
def test_slides_with_template_background_keep_text_layer(tmp_path: Path, title: str) -> None:
    # (1) 6 slide, ảnh nền phủ kín cùng nội dung, cùng chỗ, lớp chữ < 200 ký tự, không có engine OCR.
    path = write_pdf(tmp_path / "slides.pdf", [_slide(title) for _ in range(6)])
    pages = list(WorkerPdfReader(CONFIG).read_pages(path, DOC_ID))
    assert [(p.status, p.tier_used, p.has_figure, p.review_reasons) for p in pages] == [
        ("ok", "text_layer", False, [])
    ] * 6
    assert all(p.lines[0].text == title for p in pages)


@pytest.mark.parametrize(("title", "reason"), [(TITLE_40, "image_covers_page"), (TITLE_SHORT, "sparse_text_on_image")])
def test_content_image_on_one_slide_sends_only_that_slide_to_ocr(tmp_path: Path, title: str, reason: str) -> None:
    # (2) cùng bộ, một trang thêm ảnh nội dung phủ ≥ 90% ⇒ riêng trang đó OCR.
    pages = [_slide(title) for _ in range(6)]
    pages[2].rasters.append(gray_raster(Box(0.0, 0.0, 612.0, 760.0), 60))
    path = write_pdf(tmp_path / "slides.pdf", pages)
    summary = _summary(path)
    assert summary[2] == ("failed", None, "OCR_ENGINE_MISSING", True)
    assert summary[:2] + summary[3:] == [("ok", "text_layer", None, False)] * 5
    emitted = _run_worker(path, 6, _config())
    assert _ocr_pages(emitted) == [2]
    raws = _pages(emitted)
    assert raws[2].ocr is not None and raws[2].content_image_area_ratio == pytest.approx(760.0 / 792.0)
    assert ocr_reason(raws[2], CONFIG) == reason
    page = assemble_text_layer_page(raws[2], DOC_ID, CONFIG)
    assert page.tier_used == "ocr" and [line.text for line in page.lines] == ["Sơ đồ khối"]


def test_content_image_above_figure_threshold_flags_only_that_slide(tmp_path: Path) -> None:
    # has_figure (người dùng quyết 30/10): ảnh nội dung ≥ 0,10 ⇒ riêng trang đó có cờ hình.
    pages = [_slide() for _ in range(6)]
    pages[3].rasters.append(gray_raster(Box(100.0, 100.0, 306.0, 400.0), 60))  # ≈ 0,25 trang
    summary = _summary(write_pdf(tmp_path / "slides.pdf", pages))
    assert summary[3] == ("needs_review", "text_layer", None, True)
    assert summary[:3] + summary[4:] == [("ok", "text_layer", None, False)] * 5


def test_vector_figure_keeps_its_flag(tmp_path: Path) -> None:
    # (3) trang có hình vẽ vector (đường vẽ ≥ `figure_path_area_min`) kèm ảnh nội dung nhỏ ⇒ vẫn có cờ như cũ.
    diagram = Box(50.0, 50.0, 500.0, 600.0)
    small = gray_raster(Box(60.0, 60.0, 100.0, 100.0), 60)  # ≈ 0,02 trang, dưới `figure_image_area_min`
    pages = [_slide() for _ in range(6)]
    pages[1].rects.append(diagram)
    pages[1].rasters.append(small)
    # Trang có đường vẽ và chỉ có ảnh template: nhánh đường vẽ tính mọi ảnh (người dùng quyết 30/10) ⇒ có cờ.
    pages[4].rects.append(diagram)
    path = write_pdf(tmp_path / "vector.pdf", pages)
    pages_read = list(WorkerPdfReader(CONFIG).read_pages(path, DOC_ID))
    flagged = ("needs_review", True, ["TIER3_UNAVAILABLE"])
    assert (pages_read[1].status, pages_read[1].has_figure, pages_read[1].review_reasons) == flagged
    assert (pages_read[4].status, pages_read[4].has_figure, pages_read[4].review_reasons) == flagged
    # Cùng trang vector trong tài liệu không có ảnh template: cờ như cũ.
    alone = write_pdf(tmp_path / "alone.pdf", [Page(texts=[_title()], rects=[diagram], rasters=[small])])
    assert _summary(alone) == [("needs_review", "text_layer", None, True)]


def test_shape_diagram_on_template_background_is_flagged(tmp_path: Path) -> None:
    """Chốt (1), người dùng quyết 30/10: sơ đồ vẽ bằng shape trên slide chỉ có ảnh nền template ⇒ CÓ cờ hình.

    Nhánh đường vẽ của `has_figure` giữ như develop: điều kiện "có ảnh" tính mọi ảnh, kể cả ảnh template.
    """
    pages = [_slide() for _ in range(6)]
    pages[2].rects.append(Box(80.0, 80.0, 450.0, 500.0))  # ≈ 0,46 trang, không có ảnh nội dung
    path = write_pdf(tmp_path / "shapes.pdf", pages)
    raws = _pages(_run_worker(path, 6, _config()))
    assert raws[2].content_image_area_ratio == 0.0 and raws[2].image_area_ratio == pytest.approx(1.0)
    assert raws[2].path_area_ratio >= CONFIG.figure_path_area_min
    pages_read = list(WorkerPdfReader(CONFIG).read_pages(path, DOC_ID))
    assert [(p.status, p.tier_used, p.has_figure, p.review_reasons) for p in pages_read] == [
        ("ok", "text_layer", False, [])
    ] * 2 + [("needs_review", "text_layer", True, ["TIER3_UNAVAILABLE"])] + [("ok", "text_layer", False, [])] * 3


def test_template_frame_drawn_as_paths_is_flagged_for_now(tmp_path: Path) -> None:
    """Chốt (2), người dùng quyết 30/10: trang chỉ có khung template (ảnh nền template + khung vẽ vector) ⇒ CÓ cờ.

    Hành vi tạm, sẽ đổi khi loại đường vẽ template (nợ ADR-008: đường vẽ lặp cùng vị trí trên đa số trang, dùng chung
    hàm xác định template với ảnh, làm cùng việc che template ở khung VLM).
    """
    frame = Box(18.0, 18.0, 576.0, 720.0)  # khung slide lặp trên mọi trang, ≈ 0,86 trang
    pages = [Page(texts=[_title()], rasters=[gray_raster(FULL, BACKGROUND)], rects=[frame]) for _ in range(6)]
    path = write_pdf(tmp_path / "frames.pdf", pages)
    raws = _pages(_run_worker(path, 6, _config()))
    assert [r.content_image_area_ratio for r in raws] == [0.0] * 6
    assert all(r.path_area_ratio >= CONFIG.figure_path_area_min for r in raws)
    assert _summary(path) == [("needs_review", "text_layer", None, True)] * 6


@pytest.mark.parametrize(
    ("image", "content", "path", "expected"),
    [
        (1.0, 0.0, 0.25, True),  # chỉ ảnh template + đường vẽ đúng ngưỡng: nhánh đường vẽ tính mọi ảnh
        (1.0, 0.0, 0.24, False),
        (1.0, None, 0.25, True),
        (0.05, None, 0.25, True),  # không có thông tin template: dùng toàn bộ độ phủ ảnh
        (0.05, None, 0.24, False),
        (0.0, 0.0, 0.9, False),  # không ảnh nào: đường vẽ không dùng một mình
        (1.0, 0.10, 0.0, True),  # nhánh ảnh: ảnh nội dung đúng ngưỡng
        (1.0, 0.09, 0.0, False),  # nhánh ảnh dùng độ phủ không-template, không dùng độ phủ toàn bộ
    ],
)
def test_figure_branches_use_their_own_image_coverage(
    image: float, content: float | None, path: float, expected: bool
) -> None:
    raw = RawPdfPage(
        page_index=0,
        width_pt=612,
        height_pt=792,
        lines=[],
        image_area_ratio=image,
        path_area_ratio=path,
        content_image_area_ratio=content,
    )
    assert page_flags(raw, "", CONFIG).has_figure is expected


def test_scanned_pages_with_different_images_have_no_template(tmp_path: Path) -> None:
    # (3) PDF scan: mỗi trang một ảnh khác nhau phủ kín ⇒ không ảnh nào là template, luật như cũ.
    pages = [Page(texts=[_title()], rasters=[gray_raster(FULL, 10 * i)]) for i in range(6)]
    path = write_pdf(tmp_path / "scan.pdf", pages)
    assert _summary(path) == [("failed", None, "OCR_ENGINE_MISSING", True)] * 6
    emitted = _run_worker(path, 6, _config())
    assert _scan(emitted) == TemplateScan(status="ok")
    assert [p.content_image_area_ratio for p in _pages(emitted)] == [None] * 6
    assert _ocr_pages(emitted) == list(range(6))


def test_two_page_document_with_same_background_keeps_old_rules(tmp_path: Path) -> None:
    path = write_pdf(tmp_path / "two.pdf", [_slide() for _ in range(2)])
    assert _summary(path) == [("failed", None, "OCR_ENGINE_MISSING", True)] * 2


def test_template_never_lets_empty_or_garbled_text_layer_skip_ocr(tmp_path: Path) -> None:
    # I5: luật lớp chữ rỗng và lớp chữ rác không dùng độ phủ ảnh.
    pages = [_slide() for _ in range(6)]
    pages[1] = Page(rasters=[gray_raster(FULL, BACKGROUND)])  # lớp chữ rỗng
    pages[2] = Page(texts=text_lines(TCVN3), rasters=[gray_raster(FULL, BACKGROUND)])  # TCVN3
    path = write_pdf(tmp_path / "slides.pdf", pages)
    summary = _summary(path)
    assert summary[1][:3] == ("failed", None, "OCR_ENGINE_MISSING")
    assert summary[2][:3] == ("failed", None, "OCR_ENGINE_MISSING")
    emitted = _run_worker(path, 6, _config())
    raws = _pages(emitted)
    assert raws[1].content_image_area_ratio == 0.0 and raws[2].content_image_area_ratio == 0.0
    assert [ocr_reason(raw, CONFIG) for raw in raws] == [
        None,
        "empty_text_layer",
        "garbled_text_layer",
        None,
        None,
        None,
    ]
    assert _ocr_pages(emitted) == [1, 2]


@pytest.mark.parametrize("content", [None, 0.0, 0.5, 0.95])
@pytest.mark.parametrize("title", [TITLE_40, TITLE_SHORT, " ".join(BODY)])
def test_parent_and_worker_agree_with_and_without_content_ratio(content: float | None, title: str) -> None:
    line = RawTextLine(text=title, bbox=None, words=[])
    raw = RawPdfPage(
        page_index=0,
        width_pt=612,
        height_pt=792,
        lines=[line],
        image_area_ratio=1.0,
        path_area_ratio=0.0,
        content_image_area_ratio=content,
    )
    image = 1.0 if content is None else content
    plain = raw.model_copy(update={"content_image_area_ratio": None, "image_area_ratio": image})
    # Trang có độ phủ không-template x quyết định y hệt trang cũ có độ phủ toàn bộ x.
    assert ocr_reason(raw, CONFIG) == ocr_reason(plain, CONFIG)
    assert needs_ocr_page(raw, CONFIG) == needs_ocr_page(plain, CONFIG)
    assert page_flags(raw, title, CONFIG) == page_flags(plain, title, CONFIG)
    built = assemble_text_layer_page(raw, DOC_ID, CONFIG)
    expected = assemble_text_layer_page(plain, DOC_ID, CONFIG)
    assert (built.status, built.tier_used, built.has_figure, built.review_reasons) == (
        expected.status,
        expected.tier_used,
        expected.has_figure,
        expected.review_reasons,
    )


def test_parent_logs_one_line_without_path_when_scan_is_over_cap(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # (4) vượt trần số ảnh ⇒ không loại ảnh nào (hành vi cũ) và một dòng log ở tiến trình cha, không có đường dẫn.
    logo = gray_raster(Box(500.0, 700.0, 100.0, 80.0), 30)
    pages = [Page(texts=[_title()], rasters=[gray_raster(FULL, BACKGROUND), logo]) for _ in range(6)]
    path = write_pdf(tmp_path / "secret-name.pdf", pages)
    config = _config(template_scan_max_images_per_page=1)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        summary = _summary(path, config)
    assert summary == [("failed", None, "OCR_ENGINE_MISSING", True)] * 6
    records = [r for r in caplog.records if r.name == LOGGER]
    assert len(records) == 1
    message = records[0].getMessage()
    assert "over_cap" in message and "secret-name" not in message and str(tmp_path) not in message
    # Quét bình thường: không có dòng log nào.
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        _summary(path)
    assert [r for r in caplog.records if r.name == LOGGER] == []


def _recording_sessions(monkeypatch: pytest.MonkeyPatch) -> list[WorkerRequest]:
    requests: list[WorkerRequest] = []

    class Recording(WorkerSession):
        def __init__(self, request: WorkerRequest) -> None:
            requests.append(request)
            super().__init__(request)

    monkeypatch.setattr(pdf_reader, "WorkerSession", Recording)
    return requests


def test_scan_timeout_reads_on_without_templates_and_without_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    requests = _recording_sessions(monkeypatch)
    pages = [Page(texts=text_lines(BODY), rasters=[gray_raster(FULL, BACKGROUND)]) for _ in range(6)]
    path = write_pdf(tmp_path / "slides.pdf", pages)
    config = _config(template_scan_timeout_s=1e-6)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        summary = _summary(path, config)
    # Không loại ảnh nền: hành vi cũ (giữ lớp chữ, cờ hình), không trang nào `failed`.
    assert summary == [("needs_review", "text_layer", None, True)] * 6
    records = [r for r in caplog.records if r.name == LOGGER]
    assert len(records) == 1 and "timeout" in records[0].getMessage()
    assert requests[0].scan_templates and requests[0].template_scan is None
    # Tiến trình cha quá hạn thì dựng worker mới với kết quả `timeout`, worker mới không quét lại.
    assert all(r.template_scan == TemplateScan(status="timeout") and not r.scan_templates for r in requests[1:])


def test_reading_one_selected_page_asks_the_worker_to_scan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    requests = _recording_sessions(monkeypatch)
    path = write_pdf(tmp_path / "slides.pdf", [_slide() for _ in range(4)])
    [page] = list(WorkerPdfReader(CONFIG).read_pages(path, DOC_ID, page_numbers=[2]))
    assert [(r.page_budget, r.scan_templates, r.template_scan) for r in requests] == [(1, True, None)]
    assert (page.page_number, page.status, page.tier_used, page.has_figure) == (2, "ok", "text_layer", False)
    assert [line.text for line in page.lines] == [TITLE_40]


def test_worker_restart_keeps_the_same_template_decisions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    requests = _recording_sessions(monkeypatch)
    pages = [_slide() for _ in range(6)]
    slow = slow_page()
    slow.rasters.append(gray_raster(FULL, BACKGROUND))
    pages[3] = slow
    path = write_pdf(tmp_path / "slides.pdf", pages)
    config = _config(text_layer_page_timeout_s=0.5)
    summary = _summary(path, config)
    assert summary[3][:3] == ("failed", None, "PAGE_TIMEOUT")
    assert summary[:3] + summary[4:] == [("ok", "text_layer", None, False)] * 5
    assert len(requests) == 2
    first, restarted = requests
    assert first.scan_templates and first.template_scan is None
    assert not restarted.scan_templates and restarted.template_scan is not None
    assert restarted.template_scan.status == "ok" and len(restarted.template_scan.keys) == 1


# --------------------------------------------------------------------------- #
# Tiến trình cha với session giả
# --------------------------------------------------------------------------- #


class FakeSession:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = outcomes
        self.killed = False
        self.closed = False
        self.requested: list[int] = []
        self.timeouts: list[float] = []

    def request_page(self, index: int) -> None:
        self.requested.append(index)

    def receive(self, timeout_s: float) -> object:
        self.timeouts.append(timeout_s)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def kill(self) -> None:
        self.killed = True

    def close(self) -> None:
        self.closed = True


def _good_page(index: int) -> RawPdfPage:
    line = RawTextLine(text=" ".join(BODY), bbox=None, words=[])
    return RawPdfPage(
        page_index=index, width_pt=612, height_pt=792, lines=[line], image_area_ratio=0.0, path_area_ratio=0.0
    )


OK_SCAN = TemplateScan(status="ok", keys=[_key()])


@pytest.mark.parametrize(
    ("after_start", "status"),
    [
        (TimeoutError(), "timeout"),
        (None, "error"),  # EOF
        (PageMessage(page=_good_page(0)), "error"),  # thông điệp khác
        (ValidationError.from_exception_data("x", []), "error"),
    ],
)
def test_failed_scan_wait_kills_worker_and_asks_for_rerun(after_start: object, status: str) -> None:
    session = FakeSession([TemplateScanStartedMessage(), after_start])
    config = _config(template_scan_timeout_s=3.0)
    outcome = WorkerPdfReader(config)._read_page(session, 0, DOC_ID, 1.0, 2.0, accept_scan=True)
    assert outcome.rerun and not outcome.healthy and session.killed
    assert outcome.template_scan == TemplateScan(status=status)
    assert session.timeouts == [1.0, 3.0]


def test_successful_scan_wait_then_reads_page_with_page_timeout() -> None:
    session = FakeSession(
        [TemplateScanStartedMessage(), TemplateScanMessage(scan=OK_SCAN), PageMessage(page=_good_page(0))]
    )
    reader = WorkerPdfReader(_config(template_scan_timeout_s=3.0))
    outcome = reader._read_page(session, 0, DOC_ID, 1.0, 2.0, accept_scan=True)
    assert (outcome.page.status, outcome.healthy, outcome.rerun, outcome.template_scan) == ("ok", True, False, OK_SCAN)
    assert session.timeouts == [1.0, 3.0, 1.0] and not session.killed
    assert outcome.scan_wait_s >= 0.0


def _patch_start(monkeypatch: pytest.MonkeyPatch, sessions: list[FakeSession]) -> list[dict[str, object]]:
    calls: list[dict[str, object]] = []

    def start(
        self: WorkerPdfReader, path: Path, page_budget: int, template_scan: TemplateScan | None = None
    ) -> tuple[FakeSession, int]:
        calls.append({"page_budget": page_budget, "template_scan": template_scan})
        return sessions.pop(0), 3

    monkeypatch.setattr(WorkerPdfReader, "_start", start)
    return calls


def _stub(tmp_path: Path) -> Path:
    path = tmp_path / "stub.pdf"
    path.write_bytes(b"%PDF-1.7\n")
    return path


def test_rerun_after_scan_timeout_passes_the_held_scan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first = FakeSession([TemplateScanStartedMessage(), TimeoutError()])
    second = FakeSession([PageMessage(page=_good_page(i)) for i in range(3)])
    calls = _patch_start(monkeypatch, [first, second])
    pages = list(WorkerPdfReader(_config()).read_pages(_stub(tmp_path), DOC_ID))
    assert [(p.page_number, p.status) for p in pages] == [(1, "ok"), (2, "ok"), (3, "ok")]
    assert first.closed and second.requested == [0, 1, 2]
    assert calls == [
        {"page_budget": CONFIG.max_pages, "template_scan": None},
        {"page_budget": 3, "template_scan": TemplateScan(status="timeout")},
    ]


def test_later_restart_passes_the_held_scan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first = FakeSession(
        [TemplateScanStartedMessage(), TemplateScanMessage(scan=OK_SCAN), PageMessage(page=_good_page(0)), None]
    )
    second = FakeSession([PageMessage(page=_good_page(2))])
    calls = _patch_start(monkeypatch, [first, second])
    pages = list(WorkerPdfReader(_config()).read_pages(_stub(tmp_path), DOC_ID))
    assert [(p.page_number, p.status, p.failure_reason) for p in pages] == [
        (1, "ok", None),
        (2, "failed", "PAGE_ERROR"),
        (3, "ok", None),
    ]
    assert calls[1] == {"page_budget": 1, "template_scan": OK_SCAN}


def test_rerun_that_cannot_restart_fails_pages_without_raising(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first = FakeSession([TemplateScanStartedMessage(), TimeoutError()])
    calls: list[int] = []

    def start(
        self: WorkerPdfReader, path: Path, page_budget: int, template_scan: TemplateScan | None = None
    ) -> tuple[FakeSession, int]:
        calls.append(page_budget)
        if len(calls) > 1:
            raise pdf_reader.PdfWorkerError("không khởi động được")
        return first, 3

    monkeypatch.setattr(WorkerPdfReader, "_start", start)
    pages = list(WorkerPdfReader(_config()).read_pages(_stub(tmp_path), DOC_ID))
    assert [(p.status, p.failure_reason) for p in pages] == [("failed", "PAGE_ERROR")] * 3
    assert calls == [CONFIG.max_pages, 3]


def test_scan_wait_does_not_count_toward_the_document_deadline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class SlowScanSession(FakeSession):
        def receive(self, timeout_s: float) -> object:
            outcome = super().receive(timeout_s)
            if isinstance(outcome, TemplateScanMessage):
                pdf_reader.time.sleep(0.4)
            return outcome

    session = SlowScanSession(
        [TemplateScanStartedMessage(), TemplateScanMessage(scan=OK_SCAN)]
        + [PageMessage(page=_good_page(i)) for i in range(3)]
    )
    _patch_start(monkeypatch, [session])
    config = _config(document_timeout_s=0.3)
    pages = list(WorkerPdfReader(config).read_pages(_stub(tmp_path), DOC_ID))
    assert [p.status for p in pages] == ["ok"] * 3
