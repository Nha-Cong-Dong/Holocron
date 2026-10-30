"""Cách đếm của các script đo bộ nhập PDF (`scripts/eval_pdf_ocr.py`, `scripts/eval_pdf_tiers.py`); không chạy OCR."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from holocron_core.infrastructure.document_readers.config import PdfReaderConfig
from holocron_core.infrastructure.document_readers.protocol import (
    ImageKey,
    RawOcrPage,
    RawPdfPage,
    RawTextLine,
    TemplateScan,
)
from scripts import eval_pdf_tiers
from scripts.eval_pdf_ocr import digit_accuracy_micro, digit_correct, digits, edit_distance
from scripts.eval_pdf_tiers import (
    DirReport,
    PageRow,
    app_page,
    is_text_layer_with_content_image,
    is_text_layer_with_image,
    is_unflagged_figure,
    markdown,
    old_text_score,
    page_row,
    with_template_scan,
)


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


def _row(**changes: object) -> PageRow:
    base = PageRow(
        file="a.pdf",
        page_number=1,
        non_ws=40,
        clean=1.0,
        valid=1.0,
        image_area_ratio=0.5,
        path_area_ratio=0.0,
        old_score=0.2,
        old_tier="ocr",
        new_tier="text_layer",
        new_reason=None,
        status="needs_review",
        review_reasons=["TIER3_UNAVAILABLE"],
        has_figure=True,
        has_table=False,
        has_formula=False,
    )
    return replace(base, **changes)


def test_tier_eval_rows_carry_app_review_reasons_and_flags() -> None:
    config = PdfReaderConfig()
    lines = [RawTextLine(text="Chương 1", bbox=None, words=[])]
    framed = RawPdfPage(page_index=0, width_pt=720, height_pt=540, lines=lines, image_area_ratio=0.1, path_area_ratio=0)
    row = page_row("a.pdf", framed, config)
    assert (row.status, row.review_reasons, row.has_figure, row.has_table, row.has_formula) == (
        "needs_review",
        ["TIER3_UNAVAILABLE"],
        True,
        False,
        False,
    )
    assert row.path_area_ratio == 0.0
    # Cùng kết quả với hàm dựng trang của app.
    assert row.review_reasons == app_page(framed, config).review_reasons
    plain = page_row("a.pdf", framed.model_copy(update={"image_area_ratio": 0.0}), config)
    assert (plain.status, plain.review_reasons, plain.has_figure) == ("ok", [], False)
    long_title = [RawTextLine(text="x" * 64, bbox=None, words=[])]
    covered = framed.model_copy(update={"lines": long_title, "image_area_ratio": 0.98})
    row = page_row("a.pdf", covered, config)
    assert (row.new_tier, row.new_reason, row.status, row.review_reasons) == ("ocr", "image_covers_page", "failed", [])


def test_tier_eval_app_page_ignores_ocr_result() -> None:
    # Script không gọi OCR: kể cả trang có sẵn kết quả OCR cũng được dựng như khi không có engine.
    config = PdfReaderConfig()
    lines = [RawTextLine(text="x" * 64, bbox=None, words=[])]
    raw = RawPdfPage(page_index=0, width_pt=720, height_pt=540, lines=lines, image_area_ratio=0.98, path_area_ratio=0)
    ocr = RawOcrPage(dpi=300.0, lines=[], ink_outside_text_ratio=0.0, secondary=None)
    page = app_page(raw.model_copy(update={"ocr": ocr}), config)
    assert (page.status, page.failure_reason) == ("failed", "OCR_ENGINE_MISSING")


def test_tier_eval_figure_table_filter() -> None:
    assert is_text_layer_with_image(_row(image_area_ratio=0.30), 0.30)
    assert not is_text_layer_with_image(_row(image_area_ratio=0.2999), 0.30)
    assert not is_text_layer_with_image(_row(new_tier="ocr", image_area_ratio=0.98), 0.30)
    assert not is_unflagged_figure(_row())
    assert is_unflagged_figure(_row(review_reasons=[], status="ok"))
    assert not is_unflagged_figure(_row(review_reasons=[], has_figure=False))
    assert not is_unflagged_figure(_row(new_tier="ocr", review_reasons=[]))


# --------------------------------------------------------------------------- #
# Bảng "trang giữ lớp chữ có ảnh" và bảng ảnh template của `eval_pdf_tiers` (sửa đổi 2026-10-30)
# --------------------------------------------------------------------------- #


def _report(rows: list[PageRow]) -> DirReport:
    return DirReport(pdf_dir="arm_ch9", files=sorted({row.file for row in rows}), pages=rows)


def test_tier_eval_figure_table_cells_show_empty_and_joined_reasons() -> None:
    config = PdfReaderConfig()
    rows = [
        _row(page_number=1, review_reasons=[], status="ok"),
        _row(page_number=2, review_reasons=["TIER3_UNAVAILABLE", "FIGURE_ON_SCAN"]),
    ]
    text = markdown([_report(rows)], config, 120, 0.30, 0.30)
    assert "| a.pdf | 1 | 40 | 50.0 | text_layer | (rỗng) |" in text
    assert "| a.pdf | 2 | 40 | 50.0 | text_layer | TIER3_UNAVAILABLE, FIGURE_ON_SCAN |" in text


def test_tier_eval_figure_table_uses_its_own_threshold() -> None:
    config = PdfReaderConfig()
    text = markdown([_report([_row(image_area_ratio=0.5)])], config, 120, 0.9, 0.3)
    assert "## arm_ch9: trang giữ lớp chữ có ảnh ≥ 0.3 (1)" in text
    assert "## arm_ch9: lớp chữ ít (0 < non_ws < 120) + ảnh phủ ≥ 0.9 (0)" in text
    text = markdown([_report([_row(image_area_ratio=0.5)])], config, 120, 0.3, 0.9)
    assert "## arm_ch9: trang giữ lớp chữ có ảnh ≥ 0.9 (0)" in text


def test_tier_eval_cli_default_figure_threshold(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    pdf_dir = tmp_path / "arm_empty"
    pdf_dir.mkdir()
    out_dir = tmp_path / "out"
    assert eval_pdf_tiers.main([str(out_dir), str(pdf_dir)]) == 0
    text = (out_dir / "results.md").read_text(encoding="utf-8")
    assert "## arm_empty: trang giữ lớp chữ có ảnh ≥ 0.3 (0)" in text
    assert "## arm_empty: lớp chữ ít (0 < non_ws < 120) + ảnh phủ ≥ 0.3 (0)" in text
    assert "## arm_empty: trang giữ lớp chữ có ảnh không phải template ≥ 0.3 (0)" in text
    capsys.readouterr()


def test_tier_eval_template_tables_are_appended() -> None:
    config = PdfReaderConfig()
    rows = [
        _row(page_number=1, image_area_ratio=1.0, content_image_area_ratio=0.0, template_scan="ok", template_images=2),
        _row(page_number=2, image_area_ratio=0.6, content_image_area_ratio=0.45, template_scan="ok", template_images=2),
    ]
    text = markdown([_report(rows)], config, 120, 0.30, 0.30)
    assert "| a.pdf | ok | 2 |" in text
    assert "## arm_ch9: trang giữ lớp chữ có ảnh không phải template ≥ 0.3 (1)" in text
    assert "| a.pdf | 2 | 40 | 60.0 | 45.0 | text_layer | TIER3_UNAVAILABLE |" in text
    # Bảng cũ giữ nguyên định dạng và vẫn lọc theo độ phủ toàn bộ.
    assert "## arm_ch9: trang giữ lớp chữ có ảnh ≥ 0.3 (2)" in text
    assert text.index("ảnh template theo file") > text.index("trang giữ lớp chữ có ảnh ≥ 0.3")
    assert is_text_layer_with_content_image(rows[1], 0.45)
    assert not is_text_layer_with_content_image(rows[0], 0.01)
    assert not is_text_layer_with_content_image(replace(rows[1], new_tier="ocr"), 0.3)


def test_tier_eval_rows_record_template_scan_and_content_area() -> None:
    config = PdfReaderConfig()
    lines = [RawTextLine(text="x" * 64, bbox=None, words=[])]
    raw = RawPdfPage(
        page_index=0,
        width_pt=720,
        height_pt=540,
        lines=lines,
        image_area_ratio=0.98,
        path_area_ratio=0,
        content_image_area_ratio=0.0,
    )
    scan = TemplateScan(
        status="ok",
        keys=[ImageKey(digest="a" * 64, width_px=1, height_px=1, bits_per_pixel=0, filters=(), bbox=(0, 0, 1, 1))],
    )
    row = with_template_scan(page_row("a.pdf", raw, config), scan)
    assert (row.content_image_area_ratio, row.template_scan, row.template_images) == (0.0, "ok", 1)
    assert (row.new_tier, row.status) == ("text_layer", "ok")  # ảnh template không làm trang thành trang ảnh
    plain = page_row("a.pdf", raw.model_copy(update={"content_image_area_ratio": None}), config)
    assert (plain.content_image_area_ratio, plain.template_scan, plain.template_images) == (0.98, "skipped", 0)
    assert plain.new_reason == "image_covers_page"


def test_tier_eval_ocr_table_shows_all_and_non_template_coverage() -> None:
    config = PdfReaderConfig()
    row = _row(
        page_number=3,
        image_area_ratio=0.98,
        content_image_area_ratio=0.25,
        new_tier="ocr",
        new_reason="image_covers_page",
        status="failed",
        review_reasons=[],
    )
    text = markdown([_report([row])], config, 120, 0.30, 0.30)
    header = (
        "| Trang | non_ws | clean | valid | image_area_ratio | điểm cũ | tầng cũ | tầng mới | lý do | "
        "content_image_area_ratio |"
    )
    assert header in text and "|---|---|---|---|---|---|---|---|---|---|" in text
    assert "| 3 | 40 | 1.000 | 1.000 | 0.980 | 0.200 | ocr | ocr | image_covers_page | 0.250 |" in text
    # Dòng mô tả luật mới ghi đúng độ phủ dùng cho hai luật ảnh: độ phủ ảnh không-template.
    assert f"và độ phủ ảnh không-template ≥ {config.text_sparse_image_min} ⇒ OCR" in text
    assert f"độ phủ ảnh không-template ≥ {config.text_full_image_min} ⇒ OCR" in text
    assert "`image_area_ratio ≥" not in text
