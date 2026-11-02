"""Khoá ảnh template không giải mã ảnh, nhánh lỗi của khoá, và dung sai vị trí đối xứng (sửa đổi 2026-10-30).

- Khoá chỉ dùng dữ liệu thô của luồng ảnh, kích thước điểm ảnh và tên bộ lọc: không lời gọi metadata hay giải mã ảnh
  nào của PDFium (`FPDFImageObj_GetImageMetadata` có thể giải mã JPX, JBIG2).
- Lỗi khi lấy khoá (PDFium trả sai kiểu, dữ liệu ảnh đổi độ dài giữa hai lần đọc) ⇒ tài liệu không có ảnh template
  (hành vi cũ), không trang nào `failed`.
- Hai hộp lệch không quá một bước lưới luôn cùng chỗ, kể cả khi vắt qua biên ô lưới; kết quả không phụ thuộc thứ tự.

PDF mẫu tự tạo (`tests/pdf_samples.py`), worker chạy trong tiến trình test với OCR giả; không có Tesseract thật.
"""

from __future__ import annotations

import importlib
import itertools
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import BaseModel

from holocron_core.infrastructure.document_readers import _pdf_worker
from holocron_core.infrastructure.document_readers._pdf_worker import OcrRunner
from holocron_core.infrastructure.document_readers.config import OcrSettings, PdfReaderConfig
from holocron_core.infrastructure.document_readers.pdfium_adapter import PdfiumBackend, PdfiumDocument
from holocron_core.infrastructure.document_readers.protocol import (
    ImageKey,
    PageErrorMessage,
    PageMessage,
    PageRequest,
    RawOcrLine,
    RawPdfPage,
    TemplateScan,
    TemplateScanMessage,
    WorkerRequest,
)
from holocron_core.infrastructure.document_readers.raster import GrayImage, PixelBox
from holocron_core.infrastructure.document_readers.template_images import (
    TemplateFilter,
    find_template_images,
    image_key,
    scan_template_images,
)
from holocron_core.infrastructure.document_readers.tiers import assemble_text_layer_page
from tests.pdf_samples import Box, Page, gray_raster, text_lines, write_pdf

# Các test biên ở đây viết cho tỉ lệ trang 0.60; mặc định `template_min_page_ratio` đổi sang 0.40 theo phép đo
# GĐ0 (2026-10-31), nên ghi tường minh tỉ lệ cũ để giữ đúng các biên đã kiểm.
CONFIG = PdfReaderConfig(template_min_page_ratio=0.60)
DOC_ID = "doc_00000000000000cd"
SETTINGS = OcrSettings(tesseract_cmd="tesseract-test", tessdata_dir=None, secondary=None)
FULL = Box(0.0, 0.0, 612.0, 792.0)
BACKGROUND = 200
BODY = [
    "The scheduler gives each ready process the processor for one time quantum.",
    "When the quantum expires the process moves to the tail of the ready queue.",
    "Shorter quanta improve response time but add context switch overhead.",
    "The average waiting time depends on the quantum and on arrival order.",
]
# Lời gọi PDFium có thể giải mã dữ liệu ảnh; lấy khoá không được gọi lời nào trong số này.
DECODING_CALLS = (
    "FPDFImageObj_GetImageMetadata",
    "FPDFImageObj_GetImageDataDecoded",
    "FPDFImageObj_GetBitmap",
    "FPDFImageObj_GetRenderedBitmap",
)


class StubPrimary:
    def recognize(
        self, png: bytes, image: GrayImage, config: PdfReaderConfig
    ) -> tuple[list[RawOcrLine], list[PixelBox]]:
        line = RawOcrLine(text="Sơ đồ khối", bbox=(0.1, 0.1, 0.5, 0.15), words=[], conf=0.95)
        return [line], [(0, 0, image.width, image.height)]


def _slides(tmp_path: Path, count: int = 6) -> Path:
    """Slide có lớp chữ tốt và ảnh nền phủ kín (cùng điểm ảnh, cùng chỗ trên mọi trang)."""
    pages = [Page(texts=text_lines(BODY), rasters=[gray_raster(FULL, BACKGROUND)]) for _ in range(count)]
    return write_pdf(tmp_path / "slides.pdf", pages)


def _open(path: Path) -> PdfiumDocument:
    document = PdfiumBackend.load().open(path.read_bytes())
    assert isinstance(document, PdfiumDocument)
    return document


def _scan_file(path: Path, config: PdfReaderConfig = CONFIG) -> TemplateScan:
    with _open(path) as document:
        return scan_template_images(document, document.page_count(), config)


def _run_worker(path: Path, page_count: int, *, template_scan: TemplateScan | None = None) -> list[BaseModel]:
    emitted: list[BaseModel] = []
    request = WorkerRequest(
        path=str(path),
        page_budget=page_count,
        config=CONFIG,
        ocr=SETTINGS,
        scan_templates=template_scan is None,
        template_scan=template_scan,
    )
    ocr = OcrRunner(primary=StubPrimary(), secondary=None)
    requests = [PageRequest(page_index=i) for i in range(page_count)]
    _pdf_worker.run(request, PdfiumBackend.load(), requests, emitted.append, ocr)
    return emitted


def _pages(emitted: list[BaseModel]) -> list[RawPdfPage]:
    return [m.page for m in emitted if isinstance(m, PageMessage)]


def _scans(emitted: list[BaseModel]) -> list[TemplateScan]:
    return [m.scan for m in emitted if isinstance(m, TemplateScanMessage)]


# --------------------------------------------------------------------------- #
# Không giải mã ảnh khi lấy khoá
# --------------------------------------------------------------------------- #


def test_keys_never_call_image_metadata_or_decoding(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    raw = importlib.import_module("pypdfium2.raw")
    calls: list[str] = []

    def forbidden(name: str) -> Callable[..., object]:
        def call(*args: object) -> object:
            calls.append(name)
            raise AssertionError(f"lấy khoá ảnh không được gọi {name}")

        return call

    for name in DECODING_CALLS:
        monkeypatch.setattr(raw, name, forbidden(name))
    path = _slides(tmp_path, 3)
    scan = _scan_file(path)
    assert scan.status == "ok" and len(scan.keys) == 1
    templates = TemplateFilter.from_scan(scan, CONFIG)
    assert templates is not None
    with _open(path) as document:
        assert [document.read_page(i, templates).content_image_area_ratio for i in range(3)] == [0.0] * 3
    assert calls == []


# --------------------------------------------------------------------------- #
# Nhánh lỗi khi lấy khoá: không ảnh template, không trang nào `failed`
# --------------------------------------------------------------------------- #


def _patch_call(monkeypatch: pytest.MonkeyPatch, method: str, value: object, *, scan_only: bool = False) -> None:
    """`PdfiumBackend.call(…, method, …)` trả `value`; `scan_only` ⇒ chỉ lời gọi `get_objects` của lượt quét.

    GĐ0 (§5.7 0A): lượt quét duyệt ảnh, đường vẽ và form (`template_scan_kinds`), không còn chỉ ảnh.
    """
    original = PdfiumBackend.call

    def call(self: PdfiumBackend, target: object, name: str, *args: object, **kwargs: object) -> object:
        if name == method and (not scan_only or args == (self.template_scan_kinds,)):
            return value
        return original(self, target, name, *args, **kwargs)

    monkeypatch.setattr(PdfiumBackend, "call", call)


def _patch_changing_length(monkeypatch: pytest.MonkeyPatch) -> None:
    """Lần đọc thứ hai của dữ liệu thô báo độ dài khác lần hỏi độ dài."""
    raw = importlib.import_module("pypdfium2.raw")
    original = raw.FPDFImageObj_GetImageDataRaw

    def changing(image: object, buffer: object, size: int) -> object:
        result = original(image, buffer, size)
        return result if buffer is None else result - 1

    monkeypatch.setattr(raw, "FPDFImageObj_GetImageDataRaw", changing)


BROKEN_KEYS: dict[str, Callable[[pytest.MonkeyPatch], None]] = {
    "get_objects_not_iterable": lambda mp: _patch_call(mp, "get_objects", 42, scan_only=True),
    "get_px_size_wrong_shape": lambda mp: _patch_call(mp, "get_px_size", (4,)),
    "get_px_size_not_int": lambda mp: _patch_call(mp, "get_px_size", (4.0, 4)),
    "get_filters_not_list": lambda mp: _patch_call(mp, "get_filters", "FlateDecode"),
    "get_filters_not_str": lambda mp: _patch_call(mp, "get_filters", [b"FlateDecode"]),
    "data_length_changes": _patch_changing_length,
}


@pytest.mark.parametrize("broken", sorted(BROKEN_KEYS))
def test_broken_key_reads_leave_the_document_without_templates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, broken: str
) -> None:
    path = _slides(tmp_path)
    baseline = _pages(_run_worker(path, 6, template_scan=TemplateScan(status="skipped")))
    BROKEN_KEYS[broken](monkeypatch)
    emitted = _run_worker(path, 6)
    assert _scans(emitted) == [TemplateScan(status="error")]
    assert not [m for m in emitted if isinstance(m, PageErrorMessage)]
    pages = _pages(emitted)
    # Hành vi cũ: đúng các trang như khi không quét, không loại ảnh nào, không trang nào `failed`. GĐ0 (§5.7 0C):
    # trang ghi thêm lượt quét không `ok` (`template_scan_ok = False` ⇒ `DENOISE_UNAVAILABLE` khi có hình).
    assert all(not p.template_scan_ok for p in pages)

    def comparable(page: RawPdfPage) -> RawPdfPage:
        # GĐ0 0A: ảnh nội dung mang SHA-256 dữ liệu thô; lần đọc lỗi (bản vá ở trên) cho `sha256 = None`.
        images = [image.model_copy(update={"sha256": None}) for image in page.own_images]
        return page.model_copy(update={"template_scan_ok": True, "own_images": images})

    assert [comparable(p) for p in pages] == [comparable(p) for p in baseline]
    assert all(p.content_image_area_ratio is None for p in pages)
    assert [assemble_text_layer_page(p, DOC_ID, CONFIG).status for p in pages] == ["needs_review"] * 6


@pytest.mark.parametrize("broken", sorted(set(BROKEN_KEYS) - {"get_objects_not_iterable"}))
def test_broken_key_reads_with_a_held_scan_count_images_as_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, broken: str
) -> None:
    # Worker dựng lại nhận kết quả quét `ok` đã giữ; khoá đọc lỗi ⇒ ảnh là ảnh nội dung (độ phủ như cũ).
    path = _slides(tmp_path)
    held = _scan_file(path)
    assert held.status == "ok" and len(held.keys) == 1
    BROKEN_KEYS[broken](monkeypatch)
    emitted = _run_worker(path, 6, template_scan=held)
    assert _scans(emitted) == [] and not [m for m in emitted if isinstance(m, PageErrorMessage)]
    pages = _pages(emitted)
    assert [p.content_image_area_ratio for p in pages] == [pytest.approx(p.image_area_ratio) for p in pages]
    assert [assemble_text_layer_page(p, DOC_ID, CONFIG).status for p in pages] == ["needs_review"] * 6


# --------------------------------------------------------------------------- #
# Dung sai vị trí đối xứng
# --------------------------------------------------------------------------- #


def _at(x0: float, digest: str = "f") -> ImageKey:
    return image_key(digest * 64, 4, 4, ["FlateDecode"], (x0, 0.0, 0.5, 0.5), 0.01)


def test_boxes_within_one_step_across_a_grid_edge_are_the_same_place() -> None:
    low, high = _at(0.0049), _at(0.0051)
    assert (low.bbox[0], high.bbox[0]) == (0, 1)  # vắt qua biên ô lưới
    pages = [[low], [high], [low], [high], [low], [high]]
    templates = find_template_images(pages, 6, CONFIG)
    assert templates == {low, high}
    # Không phụ thuộc thứ tự trang hay thứ tự ảnh trên trang.
    for order in itertools.permutations(range(6)):
        assert find_template_images([pages[i] for i in order], 6, CONFIG) == templates
    assert find_template_images([[low, high], [high, low], [low], [], [], []], 6, CONFIG) == frozenset()


def test_boxes_two_cells_apart_or_with_other_content_are_not_grouped() -> None:
    low, far = _at(0.0049), _at(0.0151)
    assert (low.bbox[0], far.bbox[0]) == (0, 2)
    assert find_template_images([[low], [far]] * 3, 6, CONFIG) == frozenset()
    other = _at(0.0051, digest="e")
    assert find_template_images([[low], [other]] * 3, 6, CONFIG) == frozenset()


def test_tolerance_does_not_chain_a_drifting_image() -> None:
    # Ảnh trôi một ô mỗi trang: mỗi cặp liền kề cùng chỗ, nhưng không khoá nào có đủ 60% trang lân cận.
    drifting = [[image_key("f" * 64, 4, 4, [], (0.01 * i, 0.0, 0.5, 0.5), 0.01)] for i in range(6)]
    assert find_template_images(drifting, 6, CONFIG) == frozenset()
    # Cùng ảnh lệch một ô trên 2/6 trang: khoá ở giữa có đủ trang lân cận, khoá lệch hai ô so với số đông thì không.
    centre, edge = (image_key("f" * 64, 4, 4, [], (x, 0.0, 0.5, 0.5), 0.01) for x in (0.01, 0.02))
    base = image_key("f" * 64, 4, 4, [], (0.0, 0.0, 0.5, 0.5), 0.01)
    templates = find_template_images([[base]] * 4 + [[centre], [edge]], 6, CONFIG)
    assert templates == {base, centre}


def test_tolerance_on_generated_pdf_across_a_grid_edge(tmp_path: Path) -> None:
    # 0,0049 × 612 = 3,00 pt và 0,0051 × 612 = 3,12 pt: hai ô lưới liền kề với bước 0,01.
    pages = [
        Page(texts=text_lines(BODY), rasters=[gray_raster(Box(612.0 * x, 0.0, 300.0, 300.0), BACKGROUND)])
        for x in (0.0049, 0.0051) * 3
    ]
    path = write_pdf(tmp_path / "edge.pdf", pages)
    scan = _scan_file(path)
    assert scan.status == "ok" and sorted(key.bbox[0] for key in scan.keys) == [0, 1]
    templates = TemplateFilter.from_scan(scan, CONFIG)
    assert templates is not None
    with _open(path) as document:
        assert [document.read_page(i, templates).content_image_area_ratio for i in range(6)] == [0.0] * 6
