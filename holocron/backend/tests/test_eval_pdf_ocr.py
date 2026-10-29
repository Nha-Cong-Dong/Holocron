"""Cách đếm của các script đo bộ nhập PDF (`scripts/eval_pdf_ocr.py`, `scripts/eval_pdf_tiers.py`); không chạy OCR."""

from __future__ import annotations

import pytest

from holocron_core.infrastructure.document_readers.config import PdfReaderConfig
from holocron_core.infrastructure.document_readers.protocol import RawPdfPage, RawTextLine
from scripts.eval_pdf_ocr import digit_accuracy_micro, digit_correct, digits, edit_distance
from scripts.eval_pdf_tiers import old_text_score, page_row


def test_digit_correct_clamps_insertions_per_page() -> None:
    assert digit_correct(10, 2) == 8
    assert digit_correct(10, 10) == 0
    assert digit_correct(12, 64) == 0  # OCR đọc thêm chữ số từ ảnh: không âm
    assert digit_correct(0, 5) == 0


def test_digit_accuracy_micro_is_not_dragged_below_zero_by_one_page() -> None:
    # Hai trang đọc tốt (83 + 86 chữ số, 3 + 4 lỗi) và một trang OCR đọc thêm 84 chữ số không có trong lớp chữ.
    pages = [(83, 3), (86, 4), (17, 84)]
    assert digit_accuracy_micro(pages) == pytest.approx((80 + 82 + 0) / (83 + 86 + 17))
    # Cách tính cũ (1 − Σ lỗi / Σ chữ số) cho 1 − 91/186 ≈ 0.51, và về âm nếu trang lỗi lớn hơn nữa.
    assert digit_accuracy_micro([(5, 0), (5, 50)]) == pytest.approx(0.5)
    assert digit_accuracy_micro([]) is None
    assert digit_accuracy_micro([(0, 3)]) is None


def test_digit_errors_are_edit_distance_on_digit_sequence() -> None:
    assert digits("P1 đến 0x2A, 10µs") == "10210"
    assert edit_distance(digits("P1 0 5"), digits("P1 0 5")) == 0
    assert edit_distance("123", "1234") == 1
    assert edit_distance("123", "") == 3


def test_tier_eval_compares_old_and_new_rules() -> None:
    config = PdfReaderConfig()
    assert old_text_score("a" * 100) == pytest.approx(0.5)
    assert old_text_score("a " * 400) == pytest.approx(1.0)
    lines = [RawTextLine(text="Chương 1", bbox=None, words=[])]
    title = RawPdfPage(page_index=1, width_pt=720, height_pt=540, lines=lines, image_area_ratio=0.1, path_area_ratio=0)
    row = page_row("a.pdf", title, config)
    assert (row.page_number, row.non_ws, row.old_tier, row.new_tier, row.new_reason) == (
        2,
        7,
        "ocr",
        "text_layer",
        None,
    )
    image_page = title.model_copy(update={"image_area_ratio": 0.9})
    row = page_row("a.pdf", image_page, config)
    assert (row.old_tier, row.new_tier, row.new_reason) == ("ocr", "ocr", "sparse_text_on_image")
