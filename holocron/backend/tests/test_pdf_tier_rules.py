"""Luật chọn tầng của bộ nhập PDF (ADR-008 mục 6; sửa đổi 2026-10-29, người dùng duyệt).

Ít chữ KHÔNG phải lý do bỏ lớp chữ. Trang chỉ OCR cả trang khi lớp chữ rỗng, lớp chữ rác (TCVN3/VNI, ký tự rác),
hoặc trang ảnh: lớp chữ sạch nhưng có dưới `text_sparse_chars` ký tự không trắng trong khi ảnh nhúng phủ ít nhất
`text_sparse_image_min` diện tích trang. Worker (quyết định OCR) và tiến trình cha (dựng trang) dùng chung
`needs_ocr_page`; test cuối kiểm hai đường cho cùng kết quả ở đúng ngưỡng biên, trên PDF tự tạo.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

from holocron_core.infrastructure.document_readers import _pdf_worker
from holocron_core.infrastructure.document_readers._pdf_worker import OcrRunner
from holocron_core.infrastructure.document_readers.config import OcrSettings, PdfReaderConfig
from holocron_core.infrastructure.document_readers.pdf_reader import WorkerPdfReader
from holocron_core.infrastructure.document_readers.pdfium_adapter import PdfiumBackend
from holocron_core.infrastructure.document_readers.protocol import (
    OcrStartedMessage,
    PageMessage,
    PageRequest,
    RawOcrLine,
    RawPdfPage,
    RawTextLine,
    WorkerRequest,
)
from holocron_core.infrastructure.document_readers.raster import GrayImage, PixelBox
from holocron_core.infrastructure.document_readers.text_layer import (
    needs_ocr_page,
    non_whitespace_count,
    ocr_reason,
    page_text,
    text_score,
)
from holocron_core.infrastructure.document_readers.tiers import assemble_text_layer_page
from tests.pdf_samples import Box, Page, Text, text_lines, write_pdf

CONFIG = PdfReaderConfig()
DOC_ID = "doc_00000000000000dd"
SETTINGS = OcrSettings(tesseract_cmd="tesseract-test", tessdata_dir=None, secondary=None)
# TCVN3 trong PDF WinAnsi: "ư" là byte 0xAD (U+00AD).
TCVN3 = ["HÖ ®iÒu hµnh cÊp ph¸t CPU cho c¸c tiÕn tr×nh theo l­ît vµ ®­îc ch¹y tèi ®a mét l­îng tö."] * 4
TCVN3_SHORT = "Ch­¬ng 1"  # "Chương 1" mã TCVN3, cũng ít chữ như slide tiêu đề


def _raw(texts: list[str], image: float = 0.0) -> RawPdfPage:
    lines = [RawTextLine(text=text, bbox=None, words=[]) for text in texts]
    return RawPdfPage(
        page_index=0, width_pt=720.0, height_pt=540.0, lines=lines, image_area_ratio=image, path_area_ratio=0.0
    )


# --------------------------------------------------------------------------- #
# Luật trên dữ liệu thô
# --------------------------------------------------------------------------- #


def test_short_clean_slide_keeps_text_layer() -> None:
    title = _raw(["Chương 1"])
    assert non_whitespace_count("Chương 1") == 7
    assert text_score(page_text(title.lines), CONFIG) == pytest.approx(1.0)
    assert ocr_reason(title, CONFIG) is None
    assert assemble_text_layer_page(title, DOC_ID, CONFIG).tier_used == "text_layer"
    # Ảnh nền khung slide (phủ nhỏ hơn ngưỡng trang ảnh) không đổi quyết định; ảnh vẫn bật cờ hình như cũ.
    framed = _raw(["Chương 1"], image=0.17)
    page = assemble_text_layer_page(framed, DOC_ID, CONFIG)
    assert (page.tier_used, page.has_figure, page.review_reasons) == ("text_layer", True, ["TIER3_UNAVAILABLE"])
    # Ít chữ mà không có ảnh lớn: giữ lớp chữ dù chỉ có một ký tự.
    assert ocr_reason(_raw(["7"]), CONFIG) is None


def test_empty_text_layer_always_needs_ocr() -> None:
    for raw in (_raw([]), _raw(["   ", "\t"]), _raw([], image=1.0)):
        assert ocr_reason(raw, CONFIG) == "empty_text_layer"
        page = assemble_text_layer_page(raw, DOC_ID, CONFIG)
        assert (page.status, page.failure_reason, page.text_score) == ("failed", "OCR_ENGINE_MISSING", 0.0)
    # Kể cả khi cấu hình cho mọi lớp chữ qua (`text_layer_min = 0`): trang rỗng không có gì để dùng.
    assert needs_ocr_page(_raw([]), PdfReaderConfig(text_layer_min=0.0))


def test_garbled_text_layer_needs_ocr_regardless_of_length() -> None:
    for texts in (TCVN3, [TCVN3_SHORT], ["Chương 1 ���"]):
        raw = _raw(texts)
        assert ocr_reason(raw, CONFIG) == "garbled_text_layer"
        page = assemble_text_layer_page(raw, DOC_ID, CONFIG)
        assert (page.status, page.failure_reason, page.lines) == ("failed", "OCR_ENGINE_MISSING", [])
        assert page.text_score is not None and page.text_score < CONFIG.text_layer_min


def test_sparse_text_on_large_image_needs_ocr() -> None:
    # Slide mà bảng là ảnh: lớp chữ chỉ còn tiêu đề khung và số trang.
    raw = _raw(["23", "CHƯƠNG 2-BỘ VI XỬ LÝ ARM"], image=0.89)
    assert non_whitespace_count(page_text(raw.lines)) == 21
    assert text_score(page_text(raw.lines), CONFIG) == pytest.approx(1.0)
    assert ocr_reason(raw, CONFIG) == "sparse_text_on_image"
    page = assemble_text_layer_page(raw, DOC_ID, CONFIG)
    assert (page.status, page.failure_reason, page.text_score) == ("failed", "OCR_ENGINE_MISSING", pytest.approx(1.0))


def test_sparse_rule_boundaries() -> None:
    chars, image = CONFIG.text_sparse_chars, CONFIG.text_sparse_image_min
    below, at = "x" * (chars - 1), "x" * chars
    assert ocr_reason(_raw([below], image=image), CONFIG) == "sparse_text_on_image"  # biên ảnh thuộc trang ảnh
    assert ocr_reason(_raw([below], image=1.0), CONFIG) == "sparse_text_on_image"
    assert ocr_reason(_raw([at], image=1.0), CONFIG) is None  # đủ `text_sparse_chars` ký tự: giữ lớp chữ
    assert ocr_reason(_raw([below], image=image - 1e-6), CONFIG) is None
    # Khoảng trắng không tính vào số ký tự.
    assert ocr_reason(_raw([" ".join(below)], image=1.0), CONFIG) == "sparse_text_on_image"
    # Đếm sau chuẩn hoá NFC: chữ có dấu tổ hợp (NFD) không bị đếm thành hai ký tự.
    nfd = "é" * (chars - 1)
    assert non_whitespace_count(nfd) == chars - 1
    assert ocr_reason(_raw([nfd], image=1.0), CONFIG) == "sparse_text_on_image"


def test_sparse_thresholds_are_configurable_and_validated() -> None:
    raw = _raw(["Chương 1"], image=0.5)
    assert ocr_reason(raw, CONFIG) == "sparse_text_on_image"
    assert ocr_reason(raw, PdfReaderConfig(text_sparse_chars=7)) is None
    assert ocr_reason(raw, PdfReaderConfig(text_sparse_image_min=0.6)) is None
    assert ocr_reason(_raw(["Chương 1"], image=0.3), PdfReaderConfig(text_sparse_image_min=0.3)) is not None
    # Trang không có ảnh không bao giờ là trang ảnh: ngưỡng ảnh phải > 0.
    for bad in ({"text_sparse_image_min": 0.0}, {"text_sparse_image_min": 1.1}, {"text_sparse_chars": 0}):
        with pytest.raises(ValidationError):
            PdfReaderConfig.model_validate(bad)
    with pytest.raises(ValidationError):
        PdfReaderConfig.model_validate({"text_full_chars": 200})  # công thức cũ đã bỏ


# --------------------------------------------------------------------------- #
# PDF tự tạo qua worker thật (không có engine OCR)
# --------------------------------------------------------------------------- #


def test_reader_applies_tier_rules_on_generated_pdf(tmp_path: Path) -> None:
    full_image = Box(0.0, 0.0, 612.0, 792.0)
    pages = [
        Page(texts=[Text(x=72.0, y=400.0, value="Bài 1", size=40.0)]),  # slide tiêu đề, 4 ký tự
        Page(texts=[Text(x=72.0, y=400.0, value="Bài 1", size=40.0)], images=[Box(0.0, 700.0, 612.0, 92.0)]),
        Page(texts=text_lines(TCVN3)),  # lớp chữ TCVN3
        Page(images=[full_image]),  # trang toàn ảnh, không có chữ
        Page(texts=[Text(x=560.0, y=20.0, value="23")], images=[Box(0.0, 60.0, 612.0, 700.0)]),  # bảng là ảnh
    ]
    path = write_pdf(tmp_path / "slides.pdf", pages)
    result = list(WorkerPdfReader(CONFIG).read_pages(path, DOC_ID))
    summary = [(p.status, p.tier_used, p.failure_reason) for p in result]
    assert summary == [
        ("ok", "text_layer", None),
        ("needs_review", "text_layer", None),  # ảnh khung nhỏ: giữ lớp chữ, cờ hình ⇒ TIER3_UNAVAILABLE
        ("failed", None, "OCR_ENGINE_MISSING"),
        ("failed", None, "OCR_ENGINE_MISSING"),
        ("failed", None, "OCR_ENGINE_MISSING"),
    ]
    assert [line.text for line in result[0].lines] == ["Bài 1"]
    assert result[1].review_reasons == ["TIER3_UNAVAILABLE"]
    assert result[2].text_score is not None and result[2].text_score < CONFIG.text_layer_min
    assert result[3].text_score == 0.0
    assert result[4].text_score == pytest.approx(1.0)  # lớp chữ sạch nhưng là trang ảnh


# --------------------------------------------------------------------------- #
# Worker và tiến trình cha quyết định giống nhau ở ngưỡng biên
# --------------------------------------------------------------------------- #


class StubPrimary:
    def recognize(
        self, png: bytes, image: GrayImage, config: PdfReaderConfig
    ) -> tuple[list[RawOcrLine], list[PixelBox]]:
        line = RawOcrLine(text="Bảng 1", bbox=(0.1, 0.1, 0.5, 0.15), words=[], conf=0.95)
        return [line], [(0, 0, image.width, image.height)]


def _read_page(path: Path) -> RawPdfPage:
    document = PdfiumBackend.load().open(path.read_bytes())
    assert not isinstance(document, str)
    with document:
        return document.read_page(0)


def _worker(path: Path, config: PdfReaderConfig) -> tuple[bool, RawPdfPage]:
    """(worker có OCR trang không, trang worker gửi về)."""
    emitted: list[BaseModel] = []
    request = WorkerRequest(path=str(path), page_budget=1, config=config, ocr=SETTINGS)
    ocr = OcrRunner(primary=StubPrimary(), secondary=None)
    _pdf_worker.run(request, PdfiumBackend.load(), [PageRequest(page_index=0)], emitted.append, ocr)
    [page] = [m.page for m in emitted if isinstance(m, PageMessage)]
    return any(isinstance(m, OcrStartedMessage) for m in emitted), page


@pytest.mark.parametrize(
    ("char_delta", "image_delta", "expect_ocr"),
    [
        (-1, 0.0, True),  # đúng biên ảnh, dưới biên chữ ⇒ trang ảnh
        (0, 0.0, False),  # đúng biên chữ ⇒ giữ lớp chữ
        (-1, 1e-9, False),  # ảnh dưới biên một chút ⇒ giữ lớp chữ
    ],
)
def test_worker_and_parent_agree_at_boundaries(
    tmp_path: Path, char_delta: int, image_delta: float, expect_ocr: bool
) -> None:
    text = "ABCDEFGHIJ"
    path = write_pdf(
        tmp_path / "edge.pdf",
        [Page(texts=[Text(x=72.0, y=40.0, value=text)], images=[Box(0.0, 100.0, 612.0, 500.0)])],
    )
    measured = _read_page(path)
    non_ws = non_whitespace_count(page_text(measured.lines))
    assert non_ws == len(text) and 0.0 < measured.image_area_ratio < 1.0
    # Đặt ngưỡng đúng bằng số đo của trang (độ phủ ảnh tính bằng số thực của PDFium, không làm tròn).
    config = CONFIG.model_copy(
        update={
            "ocr_dpi": 36,
            "text_sparse_chars": non_ws - char_delta,
            "text_sparse_image_min": measured.image_area_ratio + image_delta,
        }
    )
    config = PdfReaderConfig.model_validate(config.model_dump())
    worker_ocr, raw = _worker(path, config)
    page = assemble_text_layer_page(raw, DOC_ID, config)
    assert worker_ocr == expect_ocr == needs_ocr_page(raw, config)
    assert (raw.ocr is not None) == expect_ocr
    assert page.tier_used == ("ocr" if expect_ocr else "text_layer")
    # Không có kết quả OCR thì tiến trình cha đánh dấu thiếu engine đúng cho những trang worker lẽ ra OCR.
    without_ocr = assemble_text_layer_page(raw.model_copy(update={"ocr": None}), DOC_ID, config)
    assert (without_ocr.failure_reason == "OCR_ENGINE_MISSING") == expect_ocr
