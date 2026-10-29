"""Luật chọn tầng của bộ nhập PDF (ADR-008 mục 6; sửa đổi 2026-10-29, người dùng duyệt).

Ít chữ KHÔNG phải lý do bỏ lớp chữ. Trang chỉ OCR cả trang khi lớp chữ rỗng, lớp chữ rác (TCVN3/VNI, ký tự rác),
hoặc trang ảnh: lớp chữ sạch nhưng có dưới `text_sparse_chars` ký tự không trắng trong khi ảnh nhúng phủ ít nhất
`text_sparse_image_min` diện tích trang. Worker (quyết định OCR) và tiến trình cha (dựng trang) dùng chung
`needs_ocr_page`; test cuối kiểm hai đường cho cùng kết quả ở đúng ngưỡng biên, trên PDF tự tạo.
"""

from __future__ import annotations

import unicodedata
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

from holocron_core.infrastructure.document_readers import _pdf_worker
from holocron_core.infrastructure.document_readers._pdf_worker import OcrRunner
from holocron_core.infrastructure.document_readers.config import OcrSettings, PdfReaderConfig
from holocron_core.infrastructure.document_readers.pdf_reader import WorkerPdfReader
from holocron_core.infrastructure.document_readers.pdfium_adapter import PdfiumBackend, PdfiumDataError, PdfiumDocument
from holocron_core.infrastructure.document_readers.protocol import (
    OcrStartedMessage,
    PageMessage,
    PageRequest,
    RawOcrLine,
    RawOcrPage,
    RawPdfPage,
    RawTextLine,
    WorkerRequest,
)
from holocron_core.infrastructure.document_readers.raster import GrayImage, PixelBox
from holocron_core.infrastructure.document_readers.text_layer import (
    clean_char_ratio,
    is_legacy_encoded_word,
    is_vietnamese_syllable_shape,
    needs_ocr_page,
    non_whitespace_count,
    ocr_reason,
    page_text,
    text_score,
    valid_word_ratio,
)
from holocron_core.infrastructure.document_readers.tiers import assemble_text_layer_page
from scripts.eval_pdf_tiers import evaluate_file
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


# --------------------------------------------------------------------------- #
# Heuristic TCVN3/VNI: ký hiệu ở mép từ, chữ Latin-1 của từ nước ngoài
# --------------------------------------------------------------------------- #


def test_trademark_and_quote_symbols_at_word_edges_are_valid() -> None:
    title = _raw(["Kiến trúc ARM® Cortex-M4"])
    assert valid_word_ratio(page_text(title.lines)) == 1.0
    assert ocr_reason(title, CONFIG) is None
    assert assemble_text_layer_page(title, DOC_ID, CONFIG).tier_used == "text_layer"
    assert valid_word_ratio("«trích dẫn» Windows© ARM®-based (ARM®).") == 1.0
    for word in ("ARM®", "«trích", "dẫn»", "dẫn».", "Windows©", "(ARM®)", "ARM®-based", "Mục§", "Hình¶", "dẫn».Tiếp"):
        assert not is_legacy_encoded_word(word), word
    # `«`, `»` kẹp giữa hai chữ cái vẫn là dấu TCVN3 (`«` = ô), kể cả qua dấu TCVN3 khác.
    for word in ("m«n", "c«¬ng", "h»a"):
        assert is_legacy_encoded_word(word), word


def test_tcvn3_letters_at_word_start_are_still_caught() -> None:
    # `®` = đ, `§` = Đ, `©` = â, `¶` = ả của TCVN3 rất hay đứng ĐẦU từ.
    for word in ("®a", "®iÒu", "®iÓm", "®Þa", "(®a)", "®­îc", "§iÒu", "©n", "¶nh", "x®y"):
        assert is_legacy_encoded_word(word), word
    short = _raw(["®a ®iÓm"])
    assert valid_word_ratio(page_text(short.lines)) == 0.0
    assert ocr_reason(short, CONFIG) == "garbled_text_layer"
    page = assemble_text_layer_page(short, DOC_ID, CONFIG)
    assert (page.status, page.failure_reason) == ("failed", "OCR_ENGINE_MISSING")


def test_foreign_words_with_latin1_letters_are_valid() -> None:
    # Hai cụm nguyên âm (Müller, Mädchen), phụ âm đầu (Grün, Größe) hoặc phụ âm cuối (Köln, Kür) không phải tiếng Việt.
    for word in ("Müller", "Mädchen", "Grün", "Größe", "Köln", "Kür", "Zürich", "naïve", "España", "Noël"):
        assert not is_legacy_encoded_word(word), word
    assert valid_word_ratio("Định lý Müller–Gödel ở Köln") == 1.0
    # Cùng chữ Latin-1 trong cụm có dạng âm tiết tiếng Việt vẫn bị bắt.
    for word in ("häc", "ViÖt", "HÖ", "Vieät", "ñöôïc", "tö.", "ngöôøi", "chaïy", "khoäng", "quÖt", "Thöôøng"):
        assert is_legacy_encoded_word(word), word


@pytest.mark.parametrize(
    ("run", "expected"),
    [
        ("tr", False),  # không có nguyên âm
        ("ngh", False),
        ("nghieâng", True),
        ("Chaïy", True),  # `y` là nguyên âm
        ("Müller", False),  # hai cụm nguyên âm
        ("Grün", False),  # phụ âm đầu `gr`
        ("Kür", False),  # phụ âm cuối `r`
        ("ä", True),
    ],
)
def test_vietnamese_syllable_shape(run: str, expected: bool) -> None:
    assert is_vietnamese_syllable_shape(run) is expected


def test_math_like_markers_at_word_edges_and_lone_letters() -> None:
    for word in ("−log", "ab×", "÷x", "Ö", "ß", "ü"):
        assert not is_legacy_encoded_word(word), word


def test_garbled_short_layer_scores_zero_and_wins_over_sparse_rule() -> None:
    text = "Ch­¬ng 1 2 3 4 5 6"
    assert valid_word_ratio(text) == 0.0
    assert ocr_reason(_raw([text]), CONFIG) == "garbled_text_layer"
    # Lớp chữ rác được xét trước luật trang ảnh.
    assert ocr_reason(_raw([TCVN3_SHORT], image=0.9), CONFIG) == "garbled_text_layer"


def test_text_score_is_product_of_quality_ratios_on_nfc() -> None:
    legacy = "ViÖt Nam ®iÒu hµnh"
    nfd = unicodedata.normalize("NFD", legacy)
    assert nfd != legacy
    # ViÖt, ®iÒu, hµnh không hợp lệ; Nam hợp lệ.
    assert text_score(nfd, CONFIG) == text_score(legacy, CONFIG) == pytest.approx(0.25)
    assert text_score(legacy, CONFIG) < CONFIG.text_layer_min
    mixed = "hµnh Nam \ufffd ok"
    clean, valid = clean_char_ratio(mixed), valid_word_ratio(mixed)
    assert (clean, valid) == (pytest.approx(0.9), pytest.approx(2 / 3))
    assert text_score(mixed, CONFIG) == pytest.approx(clean * valid) == pytest.approx(0.6)


def test_lone_symbol_line_keeps_text_layer() -> None:
    raw = _raw(["Mục", "®"])
    assert page_text(raw.lines) == "Mục\n®"
    assert ocr_reason(raw, CONFIG) is None


def test_perfect_score_meets_strictest_threshold() -> None:
    strict = PdfReaderConfig(text_layer_min=1.0)
    title = _raw(["Chương 1"])
    assert ocr_reason(title, strict) is None
    assert assemble_text_layer_page(title, DOC_ID, strict).tier_used == "text_layer"


def test_text_layer_lines_carry_the_page_score() -> None:
    lenient = PdfReaderConfig(text_layer_min=0.3)
    page = assemble_text_layer_page(_raw(["Nam hµnh"]), DOC_ID, lenient)
    assert page.tier_used == "text_layer"
    assert page.text_score == pytest.approx(0.5)
    assert [line.conf for line in page.lines] == [page.text_score]


SPARSE_ON_IMAGE = ["23", "CHƯƠNG 2-BỘ VI XỬ LÝ ARM"]


def test_sparse_page_without_engine_keeps_figure_flag() -> None:
    page = assemble_text_layer_page(_raw(SPARSE_ON_IMAGE, image=0.89), DOC_ID, CONFIG)
    assert (page.status, page.failure_reason, page.has_figure) == ("failed", "OCR_ENGINE_MISSING", True)


def test_sparse_page_with_ocr_uses_ocr_tier_and_keeps_text_score() -> None:
    line = RawOcrLine(text="Bảng 1 thanh ghi", bbox=(0.1, 0.1, 0.5, 0.15), words=[], conf=0.95)
    ocr = RawOcrPage(dpi=300.0, lines=[line], ink_outside_text_ratio=0.0, secondary=None)
    raw = _raw(SPARSE_ON_IMAGE, image=0.89).model_copy(update={"ocr": ocr})
    page = assemble_text_layer_page(raw, DOC_ID, CONFIG)
    assert page.tier_used == "ocr"
    assert page.text_score == pytest.approx(1.0)
    assert [extracted.text for extracted in page.lines] == ["Bảng 1 thanh ghi"]


def test_default_sparse_thresholds() -> None:
    assert (CONFIG.text_sparse_chars, CONFIG.text_sparse_image_min) == (32, 0.40)
    assert ocr_reason(_raw(["x" * 29], image=0.9), CONFIG) == "sparse_text_on_image"
    assert ocr_reason(_raw(["x" * 31], image=0.40), CONFIG) == "sparse_text_on_image"
    assert ocr_reason(_raw(["x" * 32], image=0.9), CONFIG) is None
    assert ocr_reason(_raw(["Chương 1"], image=0.39), CONFIG) is None


# --------------------------------------------------------------------------- #
# Script đo `eval_pdf_tiers`: trần và PDF hỏng theo từng file
# --------------------------------------------------------------------------- #


def test_eval_tiers_skips_oversized_long_and_broken_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    backend = PdfiumBackend.load()
    good = write_pdf(tmp_path / "good.pdf", [Page(texts=[Text(x=72.0, y=400.0, value="Bai 1")])] * 2)
    rows = evaluate_file(backend, good, CONFIG)
    assert not isinstance(rows, str)
    assert [(row.page_number, row.new_tier) for row in rows] == [(1, "text_layer"), (2, "text_layer")]
    size = good.stat().st_size
    assert evaluate_file(backend, good, CONFIG.model_copy(update={"max_bytes": size - 1})) == (
        f"TOO_LARGE: {size} > {size - 1} byte"
    )
    assert not isinstance(evaluate_file(backend, good, CONFIG.model_copy(update={"max_bytes": size})), str)
    assert evaluate_file(backend, good, CONFIG.model_copy(update={"max_pages": 1})) == "TOO_MANY_PAGES: 2 > 1 trang"
    assert not isinstance(evaluate_file(backend, good, CONFIG.model_copy(update={"max_pages": 2})), str)
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-1.4 broken")
    assert evaluate_file(backend, broken, CONFIG) == "OPEN_FAILED"

    def fail(self: PdfiumDocument, index: int) -> RawPdfPage:
        raise PdfiumDataError("hỏng")

    monkeypatch.setattr(PdfiumDocument, "read_page", fail)
    assert evaluate_file(backend, good, CONFIG) == "READ_FAILED: PdfiumDataError"
