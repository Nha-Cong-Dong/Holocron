"""Tầng 1 của bộ nhập PDF: `text_score`, cờ trang và chọn tầng (ADR-008 mục 6, 9, 14).

Phần áp dụng được cho tầng 1 của `test_page_flags_select_tier`; cờ của trang scan thuộc tầng 2.
"""

from __future__ import annotations

import unicodedata

import pytest

from holocron_core.infrastructure.document_readers.config import PdfReaderConfig
from holocron_core.infrastructure.document_readers.protocol import RawPdfPage, RawTextLine, RawWord
from holocron_core.infrastructure.document_readers.text_layer import (
    PdfChar,
    clean_char_ratio,
    group_lines,
    has_figure,
    has_formula,
    has_table,
    is_legacy_encoded_word,
    is_math_symbol,
    normalize_box,
    text_score,
    valid_word_ratio,
)
from holocron_core.infrastructure.document_readers.tiers import (
    PageFlags,
    assemble_text_layer_page,
    failed_page,
    select_tier,
)

CONFIG = PdfReaderConfig()
DOC_ID = "doc_00000000000000aa"

CLEAN_VIETNAMESE = (
    "Hệ điều hành cấp phát CPU cho các tiến trình theo lượt. Mỗi tiến trình được chạy tối đa một lượng tử "
    "thời gian; hết lượng tử mà chưa xong thì tiến trình bị đưa về cuối hàng đợi sẵn sàng. Thời gian chờ "
    "trung bình phụ thuộc vào độ dài lượng tử và thứ tự đến của các tiến trình trong hệ thống."
)
# Cùng nội dung tiếng Việt khi PDF dùng font TCVN3 (ABC) và lớp chữ bị đọc như Latin-1.
TCVN3_GARBLED = (
    "HÖ ®iÒu hµnh cÊp ph¸t CPU cho c¸c tiÕn tr×nh theo l−ît. Mçi tiÕn tr×nh ®−îc ch¹y tèi ®a mét l−îng tö "
    "thêi gian; hÕt l−îng tö mµ ch−a xong th× tiÕn tr×nh bÞ ®−a vÒ cuèi hµng ®îi s½n sµng. Thêi gian chê "
    "trung b×nh phô thuéc vµo ®é dµi l−îng tö vµ thø tù ®Õn cña c¸c tiÕn tr×nh trong hÖ thèng."
)
# Cùng nội dung khi PDF dùng font VNI.
VNI_GARBLED = (
    "Heä ñieàu haønh caáp phaùt CPU cho caùc tieán trình theo löôït. Moãi tieán trình ñöôïc chaïy toái ña moät "
    "löôïng töû thôøi gian; heát löôïng töû maø chöa xong thì tieán trình bò ñöa veà cuoái haøng ñôïi saün saøng."
)
ENGLISH = (
    "The scheduler gives each ready process the processor for at most one time quantum. When the quantum "
    "expires the process is moved to the tail of the ready queue, and the next process is dispatched. "
    "Shorter quanta improve response time but add context switch overhead to every scheduling decision."
)


def _word(text: str, x0: float, x1: float, y0: float = 0.10, y1: float = 0.12) -> RawWord:
    return RawWord(text=text, bbox=(x0, y0, x1, y1))


def _row(cells: list[tuple[str, float]], y: float) -> RawTextLine:
    words = [_word(text, x, x + 0.04, y, y + 0.015) for text, x in cells]
    return RawTextLine(
        text="   ".join(text for text, _ in cells),
        bbox=(words[0].bbox[0], y, words[-1].bbox[2], y + 0.015),
        words=words,
    )


def _prose_line(text: str, y: float) -> RawTextLine:
    return RawTextLine(text=text, bbox=(0.1, y, 0.9, y + 0.015), words=[_word(text, 0.1, 0.9, y, y + 0.015)])


def _raw_page(lines: list[RawTextLine], *, image: float = 0.0, path: float = 0.0) -> RawPdfPage:
    return RawPdfPage(
        page_index=2,
        width_pt=612.0,
        height_pt=792.0,
        lines=lines,
        image_area_ratio=image,
        path_area_ratio=path,
    )


TABLE_ROWS = [
    _row([("P1", 0.10), ("0", 0.40), ("5", 0.70)], 0.30),
    _row([("P2", 0.10), ("1", 0.40), ("3", 0.70)], 0.33),
    _row([("P3", 0.10), ("2", 0.40), ("8", 0.70)], 0.36),
]


# --------------------------------------------------------------------------- #
# text_score
# --------------------------------------------------------------------------- #


def test_text_score_clean_vietnamese_is_high() -> None:
    assert text_score(CLEAN_VIETNAMESE, CONFIG) == pytest.approx(1.0)
    assert text_score(unicodedata.normalize("NFD", CLEAN_VIETNAMESE), CONFIG) == pytest.approx(1.0)
    assert text_score(ENGLISH, CONFIG) == pytest.approx(1.0)
    assert text_score(CLEAN_VIETNAMESE, CONFIG) >= CONFIG.text_layer_min


def test_text_score_tcvn3_text_falls_below_text_layer_threshold() -> None:
    assert valid_word_ratio(TCVN3_GARBLED) < 0.6
    assert text_score(TCVN3_GARBLED, CONFIG) < CONFIG.text_layer_min


def test_text_score_vni_text_falls_below_text_layer_threshold() -> None:
    assert text_score(VNI_GARBLED, CONFIG) < CONFIG.text_layer_min


@pytest.mark.parametrize("word", ["®¹i", "l−îng", "c¸c", "ViÖt", "m«n", "Vieät", "ñöôïc"])
def test_legacy_encoded_words_are_invalid(word: str) -> None:
    assert is_legacy_encoded_word(word)


@pytest.mark.parametrize("word", ["Việt", "đại", "lượng", "các", "môn", "café", "®", "±5", "2024"])
def test_correct_words_and_lone_symbols_are_valid(word: str) -> None:
    assert not is_legacy_encoded_word(word)


def test_text_score_components() -> None:
    assert text_score("a", CONFIG) == pytest.approx(1.0)
    assert text_score("a" * 100, CONFIG) == pytest.approx(1.0)
    assert text_score("a " * 400, CONFIG) == 1.0
    assert clean_char_ratio("") == 1.0
    assert clean_char_ratio("ab\ufffd\ue000") == pytest.approx(0.5)
    assert clean_char_ratio("ab\x01\x02") == pytest.approx(0.5)
    assert clean_char_ratio("ab\t\n") == 1.0
    assert valid_word_ratio("123 456 !!") == 1.0
    assert text_score("", CONFIG) == 0.0


def test_text_score_penalizes_garbage_not_short_text() -> None:
    garbage = ENGLISH + " " + "\ufffd" * 60
    assert text_score(garbage, CONFIG) < CONFIG.text_layer_min
    assert text_score("Chương 1", CONFIG) >= CONFIG.text_layer_min


def test_text_score_does_not_depend_on_length() -> None:
    assert text_score("Chương 1", CONFIG) == text_score(CLEAN_VIETNAMESE, CONFIG) == pytest.approx(1.0)


# --------------------------------------------------------------------------- #
# Cờ trang
# --------------------------------------------------------------------------- #


def test_formula_flag_uses_math_symbol_density() -> None:
    formula = "Σ α β ∑ ∫ √ ≤ ≥ ± x"
    assert has_formula(formula, CONFIG)
    assert has_formula("Cho ma trận 𝐀 𝐁 𝐂 và vectơ 𝑥 𝑦 𝑧 thoả mãn", CONFIG)  # U+1D400–1D7FF
    assert not has_formula(ENGLISH, CONFIG)
    assert not has_formula("", CONFIG)
    assert not has_formula("α β", CONFIG)  # dưới số ký hiệu tối thiểu
    assert not has_formula(ENGLISH + " → ← + = < >", CONFIG)  # mũi tên và toán tử ASCII không tính
    assert all(is_math_symbol(c) for c in "∑∫√≤≥±×÷αΩ⁲₂𝑥𝟿")
    assert not any(is_math_symbol(c) for c in "a+=<>→¬ă")


def test_figure_flag_requires_image_and_does_not_trust_path_area_alone() -> None:
    assert has_figure(0.25, 0.0, CONFIG)
    assert not has_figure(0.0, 0.9, CONFIG)  # khung viền vector không đủ
    assert has_figure(0.02, 0.40, CONFIG)  # đường vẽ lớn + có ảnh
    assert not has_figure(0.02, 0.10, CONFIG)


def test_table_flag_uses_text_coordinate_grid() -> None:
    assert has_table(TABLE_ROWS, 612.0, 792.0, CONFIG)
    assert not has_table(TABLE_ROWS[:2], 612.0, 792.0, CONFIG)
    prose = [_prose_line("Mỗi tiến trình được chạy tối đa một lượng tử.", 0.3 + 0.03 * i) for i in range(5)]
    assert not has_table(prose, 612.0, 792.0, CONFIG)
    misaligned = [*TABLE_ROWS[:2], _row([("P3", 0.10), ("2", 0.55), ("8", 0.70)], 0.36)]
    assert not has_table(misaligned, 612.0, 792.0, CONFIG)
    interrupted = [TABLE_ROWS[0], prose[0], TABLE_ROWS[1], TABLE_ROWS[2]]
    assert not has_table(interrupted, 612.0, 792.0, CONFIG)
    empty = RawTextLine(text=" ", bbox=None, words=[])
    assert not has_table([empty, *TABLE_ROWS[:2]], 612.0, 792.0, CONFIG)


def test_page_flags_select_tier() -> None:
    """ADR-008 mục 14, test 5 (phần tầng 1): cờ trang và điểm lớp chữ chọn đúng tầng."""
    high, low = 0.95, 0.40
    # PDF số không cờ: dùng lớp chữ, không lý do xem lại.
    assert select_tier(high, PageFlags(), CONFIG, layout_available=False).tier == "text_layer"
    assert select_tier(high, PageFlags(), CONFIG, layout_available=False).review_reasons == ()
    # PDF số có bảng / công thức / hình: lên tầng 3 khi có; chưa có thì giữ lớp chữ và đánh dấu TIER3_UNAVAILABLE.
    for flags in (PageFlags(has_table=True), PageFlags(has_formula=True), PageFlags(has_figure=True)):
        assert select_tier(high, flags, CONFIG, layout_available=True).tier == "layout"
        decision = select_tier(high, flags, CONFIG, layout_available=False)
        assert decision.tier == "text_layer"
        assert decision.review_reasons == ("TIER3_UNAVAILABLE",)
    # Lớp chữ kém (trang scan hoặc TCVN3): cần OCR dù có cờ gì.
    for flags in (PageFlags(), PageFlags(has_table=True), PageFlags(has_figure=True)):
        assert select_tier(low, flags, CONFIG, layout_available=True).tier == "ocr"
    # Ngưỡng TEXT_LAYER_MIN là cấu hình, biên dưới thuộc lớp chữ.
    assert select_tier(CONFIG.text_layer_min, PageFlags(), CONFIG, layout_available=False).tier == "text_layer"
    lenient = PdfReaderConfig(text_layer_min=0.3)
    assert select_tier(low, PageFlags(), lenient, layout_available=False).tier == "text_layer"

    # Dựng trang từ dữ liệu tầng 1: cờ được tính từ dữ liệu và quyết định status.
    body = [_prose_line(sentence, 0.1 + 0.03 * i) for i, sentence in enumerate(CLEAN_VIETNAMESE.split(". "))]
    digital = assemble_text_layer_page(_raw_page(body), DOC_ID, CONFIG)
    assert (digital.status, digital.tier_used, digital.review_reasons) == ("ok", "text_layer", [])
    with_table = assemble_text_layer_page(_raw_page(body + TABLE_ROWS), DOC_ID, CONFIG)
    assert with_table.has_table and with_table.status == "needs_review"
    assert (with_table.tier_used, with_table.review_reasons) == ("text_layer", ["TIER3_UNAVAILABLE"])
    with_image = assemble_text_layer_page(_raw_page(body, image=0.3), DOC_ID, CONFIG)
    assert with_image.has_figure and with_image.review_reasons == ["TIER3_UNAVAILABLE"]
    frame_only = assemble_text_layer_page(_raw_page(body, path=0.9), DOC_ID, CONFIG)
    assert not frame_only.has_figure and frame_only.status == "ok"
    formula = body + [_prose_line("∑ ∫ √ ≤ ≥ ± α β γ δ ε ζ η θ λ μ π σ φ ω", 0.8)]
    assert assemble_text_layer_page(_raw_page(formula), DOC_ID, CONFIG).has_formula
    # Lớp chữ TCVN3: cần OCR; tầng 2 chưa có ⇒ failed OCR_ENGINE_MISSING, không trả chữ rác.
    garbled = [_prose_line(TCVN3_GARBLED, 0.1)]
    scan = assemble_text_layer_page(_raw_page(garbled), DOC_ID, CONFIG)
    assert (scan.status, scan.failure_reason, scan.tier_used, scan.lines) == ("failed", "OCR_ENGINE_MISSING", None, [])
    assert scan.text_score is not None and scan.text_score < CONFIG.text_layer_min
    empty = assemble_text_layer_page(_raw_page([]), DOC_ID, CONFIG)
    assert (empty.status, empty.failure_reason, empty.text_score) == ("failed", "OCR_ENGINE_MISSING", 0.0)


def test_assembled_lines_follow_spec() -> None:
    long_text = "abc " * 700  # 2800 ký tự ⇒ tách thành hai dòng ≤ 2000
    lines = [_prose_line(ENGLISH, 0.1), _prose_line(long_text, 0.2)]
    page = assemble_text_layer_page(_raw_page(lines), DOC_ID, CONFIG)
    assert page.page_number == 3 and page.document_id == DOC_ID
    assert [line.line_index for line in page.lines] == [0, 1, 2]
    assert all(line.engine == "text_layer" and line.conf == page.text_score for line in page.lines)
    assert all(len(line.text) <= 2000 for line in page.lines)
    assert page.lines[1].bbox == page.lines[2].bbox == pytest.approx((0.1, 0.2, 0.9, 0.215))
    assert page.cross_check.status == "not_applicable" and page.cross_check.secondary_engine is None


def test_failed_page_helper() -> None:
    page = failed_page(DOC_ID, 4, "PAGE_TIMEOUT")
    assert (page.status, page.failure_reason, page.tier_used, page.text_score) == ("failed", "PAGE_TIMEOUT", None, None)


# --------------------------------------------------------------------------- #
# Dựng dòng và toạ độ
# --------------------------------------------------------------------------- #


def test_normalize_box_uses_top_left_origin_and_rotation() -> None:
    page_box = (0.0, 0.0, 600.0, 800.0)
    box = (60.0, 700.0, 120.0, 720.0)  # gần mép trên-trái
    assert normalize_box(box, page_box, 0) == pytest.approx((0.1, 0.1, 0.2, 0.125))
    assert normalize_box(box, page_box, 180) == pytest.approx((0.8, 0.875, 0.9, 0.9))
    assert normalize_box(box, page_box, 90) == pytest.approx((0.875, 0.1, 0.9, 0.2))
    assert normalize_box(box, page_box, 270) == pytest.approx((0.1, 0.8, 0.125, 0.9))
    assert normalize_box((-50.0, -10.0, 700.0, 900.0), page_box, 0) == (0.0, 0.0, 1.0, 1.0)
    shifted = (100.0, 100.0, 700.0, 900.0)
    assert normalize_box((160.0, 800.0, 220.0, 820.0), shifted, 0) == pytest.approx((0.1, 0.1, 0.2, 0.125))


def test_group_lines_splits_on_line_breaks_and_words() -> None:
    page_box = (0.0, 0.0, 100.0, 100.0)
    chars = [
        PdfChar("A", (10.0, 80.0, 12.0, 90.0)),
        PdfChar("b", (12.0, 80.0, 14.0, 90.0)),
        PdfChar(" ", (14.0, 80.0, 16.0, 80.0)),
        PdfChar("c", (30.0, 80.0, 32.0, 90.0)),
        PdfChar("\r", None),
        PdfChar("\n", None),
        PdfChar("\r", None),
        PdfChar(" ", None),
        PdfChar("\n", None),
        PdfChar("d", None),
        PdfChar("e", (50.0, 60.0, 52.0, 70.0)),
    ]
    lines = group_lines(chars, page_box, 0)
    assert [line.text for line in lines] == ["Ab c", "de"]
    assert [word.text for word in lines[0].words] == ["Ab", "c"]
    assert lines[0].words[0].bbox == pytest.approx((0.10, 0.10, 0.14, 0.20))
    assert lines[0].bbox == pytest.approx((0.10, 0.10, 0.32, 0.20))
    assert lines[1].bbox == pytest.approx((0.50, 0.30, 0.52, 0.40))
    assert group_lines([PdfChar("x", None)], page_box, 0)[0].bbox is None
    assert group_lines([], page_box, 0) == []


def test_assembly_chunks_after_nfc_expansion() -> None:
    expanding = chr(0x0344) * 1500  # 3000 ký tự sau NFC
    lines = [_prose_line(CLEAN_VIETNAMESE, 0.1), _prose_line(expanding, 0.2)]
    page = assemble_text_layer_page(_raw_page(lines), DOC_ID, PdfReaderConfig(text_layer_min=0.0))
    assert [len(line.text) for line in page.lines[1:]] == [2000, 1000]
