"""Tầng 2 của bộ nhập PDF: OCR, độ phủ dòng, đối chiếu chéo và phần Gate (ADR-008 mục 6, 8–11, 14; spec §5.7, §7.4).

Bốn test bắt buộc của ADR-008 mục 14 (test 1–4) nằm ở đây, thuộc dòng I5 của bảng test cưỡng chế (CLAUDE.md §9).
Không gọi Tesseract hay RapidOCR thật: kết quả của hai lượt đọc là dữ liệu thô dựng tay, đúng dạng worker gửi về.
"""

from __future__ import annotations

import struct
import unicodedata

import pytest

from holocron_core.domain.documents import PageExtraction, TokenMismatch
from holocron_core.domain.ingestion import ExtractedCell, ExtractedTable, GateStatus, Severity
from holocron_core.infrastructure.document_readers.config import PdfReaderConfig
from holocron_core.infrastructure.document_readers.cross_check import (
    assign_secondary_lines,
    compare_tokens,
    coverage_ratio,
    numeric_tokens,
)
from holocron_core.infrastructure.document_readers.protocol import (
    RawOcrLine,
    RawOcrPage,
    RawPdfPage,
    RawSecondaryLine,
    RawSecondaryPass,
    RawTextLine,
    RawWord,
)
from holocron_core.infrastructure.document_readers.tesseract import (
    TESSDATA_LSTM_UNICHARSET,
    TESSDATA_UNICHARSET,
    missing_vietnamese_letters,
    read_unicharset,
    vietnamese_letters,
)
from holocron_core.infrastructure.document_readers.tiers import (
    PageFlags,
    assemble_text_layer_page,
    ocr_score,
    scan_review_reasons,
)
from holocron_core.services.cross_check_gate import cross_check_issues, secondary_table
from holocron_core.services.ingestion_gate import CellPatch, apply_patches, check_table, gate_status

CONFIG = PdfReaderConfig()
DOC_ID = "doc_00000000000000ee"
TOPIC = "subject.unknown.topic"  # topic không có schema: chỉ các tín hiệu chung của Gate chạy
# Lớp chữ TCVN3 (đọc như Latin-1): text_score thấp ⇒ trang cần OCR.
GARBLED = "HÖ ®iÒu hµnh cÊp ph¸t CPU cho c¸c tiÕn tr×nh theo l−ît vµ ®−îc ch¹y tèi ®a mét l−îng tö."
OCR_TEXT = "Hệ điều hành cấp phát CPU cho các tiến trình theo lượt"


# --------------------------------------------------------------------------- #
# Dữ liệu thô dựng tay
# --------------------------------------------------------------------------- #


def _box(row: int, x0: float = 0.1, x1: float = 0.9) -> tuple[float, float, float, float]:
    top = 0.05 + 0.04 * row
    return (x0, top, x1, top + 0.02)


def _ocr_line(text: str, row: int, conf: float = 0.95) -> RawOcrLine:
    box = _box(row)
    return RawOcrLine(text=text, bbox=box, words=[RawWord(text=w, bbox=box) for w in text.split()], conf=conf)


def _secondary(*rows: tuple[int, list[str]]) -> RawSecondaryPass:
    return RawSecondaryPass(
        engine="rapidocr", lines=[RawSecondaryLine(bbox=_box(row, 0.08, 0.92), tokens=tokens) for row, tokens in rows]
    )


def _scan_page(
    lines: list[RawOcrLine], secondary: RawSecondaryPass | None, *, ink: float = 0.002, index: int = 0
) -> RawPdfPage:
    garbled = RawTextLine(text=GARBLED, bbox=_box(0), words=[])
    ocr = RawOcrPage(dpi=300.0, lines=lines, ink_outside_text_ratio=ink, secondary=secondary)
    return RawPdfPage(
        page_index=index,
        width_pt=612.0,
        height_pt=792.0,
        lines=[garbled],
        image_area_ratio=1.0,
        path_area_ratio=0.0,
        ocr=ocr,
    )


def _assemble(raw: RawPdfPage, config: PdfReaderConfig = CONFIG) -> PageExtraction:
    return assemble_text_layer_page(raw, DOC_ID, config)


def _table(rows: list[tuple[str, ...]], conf: float = 0.97) -> ExtractedTable:
    cells = [
        ExtractedCell(row=r, col=c, text=t, confidence=conf) for r, row in enumerate(rows) for c, t in enumerate(row)
    ]
    return ExtractedTable(table_id="tbl_pdf", page_number=1, headers=["Nhãn", "A", "B"], cells=cells)


# --------------------------------------------------------------------------- #
# Test 1: bộ chữ của engine chính đủ 134 chữ cái tiếng Việt ngoài ASCII
# --------------------------------------------------------------------------- #


def _traineddata(components: dict[int, bytes], count: int = 24) -> bytes:
    """File `.traineddata` tối thiểu: số thành phần, bảng offset int64 (−1 khi vắng), rồi dữ liệu."""
    offsets = [-1] * count
    body = b""
    start = 4 + 8 * count
    for index in sorted(components):
        offsets[index] = start + len(body)
        body += components[index]
    return struct.pack(f"<i{count}q", count, *offsets) + body


def _unicharset(letters: list[str]) -> bytes:
    entries = [
        "NULL 0 Common 0",
        "Joined 7 0,255,0,255 Latin 1 0 1 Joined",
        *[f"{c} 3 0,255 Latin 5 0 5 {c}" for c in letters],
    ]
    return (f"{len(entries)}\n" + "\n".join(entries) + "\n").encode("utf-8")


def test_ocr_charset_covers_vietnamese() -> None:
    """ADR-008 mục 14, test 1: hàm thuần đọc unicharset của `.traineddata` và trả các chữ còn thiếu."""
    letters = vietnamese_letters()
    assert len(letters) == 134
    assert len({c for c in letters if c.islower()}) == 67 and "đ" in letters and "Đ" in letters
    assert all(not c.isascii() and unicodedata.is_normalized("NFC", c) for c in letters)
    ascii_letters = [chr(c) for c in range(ord("a"), ord("z") + 1)]

    # File đủ chữ (unicharset của LSTM, kèm thành phần khác phía sau để kiểm cách cắt theo offset).
    full = _traineddata({TESSDATA_LSTM_UNICHARSET: _unicharset(ascii_letters + sorted(letters)), 23: b"4.00.00alpha"})
    assert missing_vietnamese_letters(full) == []

    # File thiếu chữ: bỏ "ỵ", "Ỵ" và "ơ"; một chữ lưu ở dạng NFD vẫn được tính (so sau chuẩn hoá NFC).
    lacking = sorted(letters - {"ỵ", "Ỵ", "ơ"} - {"ệ"}) + [unicodedata.normalize("NFD", "ệ")]
    partial = _traineddata({TESSDATA_LSTM_UNICHARSET: _unicharset(ascii_letters + lacking)})
    assert missing_vietnamese_letters(partial) == sorted(["ỵ", "Ỵ", "ơ"])

    # Không có unicharset của LSTM thì dùng unicharset của engine cũ.
    legacy = _traineddata({TESSDATA_UNICHARSET: _unicharset(sorted(letters))})
    assert read_unicharset(legacy) is not None and missing_vietnamese_letters(legacy) == []

    # File hỏng hoặc không có unicharset: coi như thiếu toàn bộ 134 chữ.
    for broken in (b"", b"\x01\x00", _traineddata({}), _traineddata({TESSDATA_LSTM_UNICHARSET: b"\xff\xfe"})):
        assert missing_vietnamese_letters(broken) == sorted(letters)
    assert read_unicharset(struct.pack("<i", 1000)) is None


# --------------------------------------------------------------------------- #
# Test 2: độ phủ dòng thấp ⇒ needs_review dù điểm trung bình cao
# --------------------------------------------------------------------------- #


def test_low_line_coverage_marks_page_needs_review() -> None:
    """ADR-008 mục 14, test 2: lượt chính đọc 10 dòng, lượt thứ hai thấy 5 dòng ⇒ `LOW_LINE_COVERAGE`."""
    lines = [_ocr_line(f"{OCR_TEXT} {chr(ord('a') + i)}", i) for i in range(10)]  # không có token số
    full = _secondary(*[(i, []) for i in range(10)])
    half = _secondary(*[(i, []) for i in range(5)])

    complete = _assemble(_scan_page(lines, full))
    assert (complete.status, complete.tier_used, complete.review_reasons) == ("ok", "ocr", [])
    assert complete.coverage_ratio == 1.0 and complete.cross_check.status == "ok"

    page = _assemble(_scan_page(lines, half))
    assert page.ocr_score is not None and page.ocr_score >= CONFIG.ocr_score_min  # điểm trung bình vẫn cao
    assert page.coverage_ratio == pytest.approx(0.5)
    assert page.status == "needs_review" and page.review_reasons == ["LOW_LINE_COVERAGE"]
    assert page.cross_check.status == "ok"  # không có token nào lệch: chỉ độ phủ đưa trang vào xem lại

    # Lượt thứ hai đọc NHIỀU dòng hơn cũng là độ phủ thấp (min/max).
    extra = _secondary(*[(i, []) for i in range(22)])
    assert _assemble(_scan_page(lines, extra)).review_reasons == ["LOW_LINE_COVERAGE"]
    # Ngưỡng COVERAGE_MIN là cấu hình.
    lenient = PdfReaderConfig(coverage_min=0.4)
    assert "LOW_LINE_COVERAGE" not in _assemble(_scan_page(lines, half), lenient).review_reasons


# --------------------------------------------------------------------------- #
# Test 3: token số lệch ⇒ DUAL_PASS_MISMATCH kèm alternatives
# --------------------------------------------------------------------------- #


def test_numeric_token_mismatch_raises_dual_pass_mismatch() -> None:
    """ADR-008 mục 14, test 3: token lệch được ghi ở trang, và ô tương ứng sinh `DUAL_PASS_MISMATCH` ở Gate."""
    lines = [_ocr_line(OCR_TEXT, 0), _ocr_line("P1 0 5", 1), _ocr_line("P2 1 0x1F", 2)]
    secondary = _secondary((0, []), (1, ["1", "0", "6"]), (2, ["2", "1", "0x1F"]))
    page = _assemble(_scan_page(lines, secondary))
    assert page.cross_check.status == "mismatch" and page.cross_check.secondary_engine == "rapidocr"
    assert page.cross_check.mismatches == [TokenMismatch(line_index=1, primary_value="5", secondary_value="6")]
    assert page.lines[1].flags == ["TOKEN_MISMATCH"] and page.lines[2].flags == []
    assert page.status == "needs_review" and page.review_reasons == ["TOKEN_MISMATCH"]

    # Gate (§7.4): `table_secondary` mang token của lượt thứ hai ⇒ S1 sẵn có sinh DUAL_PASS_MISMATCH (BLOCKING).
    primary = _table([("P1", "0", "5"), ("P2", "1", "7")])
    second_pass = [
        ExtractedCell(row=0, col=1, text="0", confidence=0.99),
        ExtractedCell(row=0, col=2, text="6", confidence=0.99),
        ExtractedCell(row=1, col=1, text="1", confidence=0.99),
        ExtractedCell(row=1, col=2, text="7", confidence=0.99),
        ExtractedCell(row=0, col=0, text="P1", confidence=0.99),
        ExtractedCell(row=1, col=0, text="P2", confidence=0.99),
    ]
    table_secondary = secondary_table(primary, page.cross_check.status, second_pass)
    assert table_secondary is not None and table_secondary.table_id == primary.table_id
    issues = check_table(TOPIC, primary, table_secondary, None, None)
    issues += cross_check_issues(primary, page.cross_check.status, numeric_columns={1, 2})
    [mismatch] = [i for i in issues if i.code == "DUAL_PASS_MISMATCH"]
    assert (mismatch.row, mismatch.col, mismatch.severity) == (0, 2, Severity.BLOCKING)
    assert mismatch.alternatives == ["5", "6"]
    assert gate_status(issues) is GateStatus.BLOCKED

    # Ô lượt thứ hai không đọc được (vắng mặt) cũng là lệch; ô ngoài lưới bị từ chối.
    missing = secondary_table(primary, "mismatch", second_pass[:1])
    assert missing is not None
    assert {(i.row, i.col) for i in check_table(TOPIC, primary, missing, None, None)} >= {(0, 2), (1, 2)}
    with pytest.raises(ValueError, match="lưới"):
        secondary_table(primary, "ok", [ExtractedCell(row=5, col=0, text="x", confidence=1.0)])
    # Người dùng xác nhận ô thì S1 bỏ qua ô đó, như hiện nay.
    patched, _ = apply_patches(primary, [CellPatch(row=0, col=2, new_text="6")], editor_id="tester")
    assert not [i for i in check_table(TOPIC, patched, table_secondary, None, None) if i.code == "DUAL_PASS_MISMATCH"]


# --------------------------------------------------------------------------- #
# Test 4: không có lượt thứ hai ⇒ CROSS_CHECK_UNAVAILABLE và ô số không AUTO_PASS
# --------------------------------------------------------------------------- #


def test_cross_check_unavailable_blocks_auto_pass() -> None:
    """ADR-008 mục 14, test 4: `cross_check = unavailable` ⇒ WARNING cho mỗi ô số chưa xác nhận, không AUTO_PASS."""
    lines = [_ocr_line(OCR_TEXT, 0), _ocr_line("P1 0 5", 1)]
    page = _assemble(_scan_page(lines, None))
    assert page.cross_check.status == "unavailable" and page.cross_check.secondary_engine is None
    assert page.coverage_ratio is None
    assert page.status == "needs_review" and page.review_reasons == ["CROSS_CHECK_UNAVAILABLE"]

    primary = _table([("P1", "0", "5"), ("P2", "1", "7")])
    assert secondary_table(primary, page.cross_check.status, []) is None  # S1 im lặng
    issues = check_table(TOPIC, primary, None, None, None) + cross_check_issues(primary, "unavailable", {1, 2})
    assert {(i.code, i.severity) for i in issues} == {("CROSS_CHECK_UNAVAILABLE", Severity.WARNING)}
    assert sorted((i.row, i.col) for i in issues) == [(0, 1), (0, 2), (1, 1), (1, 2)]
    assert gate_status(issues) is GateStatus.NEEDS_REVIEW  # không thể AUTO_PASS

    # Người dùng xác nhận ô thì issue của ô đó hết; xác nhận hết ô số thì bảng mới AUTO_PASS được.
    patches = [CellPatch(row=r, col=c, new_text=t) for r, c, t in [(0, 1, "0"), (0, 2, "5"), (1, 1, "1")]]
    patched, _ = apply_patches(primary, patches, editor_id="tester")
    remaining = cross_check_issues(patched, "unavailable", {1, 2})
    assert [(i.row, i.col) for i in remaining] == [(1, 2)]
    confirmed, _ = apply_patches(patched, [CellPatch(row=1, col=2, new_text="7")], editor_id="tester")
    assert cross_check_issues(confirmed, "unavailable", {1, 2}) == []

    # Trang đọc bằng lớp chữ, item không tạo từ PDF, hoặc trang có lượt thứ hai: không sinh issue này.
    for status in ("not_applicable", None, "ok", "mismatch"):
        assert cross_check_issues(primary, status, {1, 2}) == []
    assert gate_status(check_table(TOPIC, primary, None, None, None)) is GateStatus.AUTO_PASS


# --------------------------------------------------------------------------- #
# Trang OCR: điểm, cờ và lý do xem lại (§5.7)
# --------------------------------------------------------------------------- #


def test_ocr_page_uses_primary_text_and_weighted_score() -> None:
    lines = [_ocr_line("Chương 1", 0, conf=0.5), _ocr_line(OCR_TEXT, 1, conf=1.0)]
    page = _assemble(_scan_page(lines, _secondary((0, ["1"]), (1, []))))
    assert [line.text for line in page.lines] == ["Chương 1", OCR_TEXT]
    assert all(line.engine == "tesseract" for line in page.lines)
    assert page.lines[0].flags == ["LOW_LINE_CONF"] and page.lines[0].conf == 0.5
    weights = (7, sum(1 for c in OCR_TEXT if not c.isspace()))
    assert page.ocr_score == pytest.approx((0.5 * weights[0] + weights[1]) / sum(weights))
    assert page.review_reasons == ["LOW_CONF_LINES"]
    assert page.text_score is not None and page.text_score < CONFIG.text_layer_min
    assert page.ink_outside_text_ratio == 0.002


def test_ocr_page_text_is_nfc() -> None:
    nfd = unicodedata.normalize("NFD", OCR_TEXT)
    page = _assemble(_scan_page([_ocr_line(nfd, 0)], _secondary((0, []))))
    assert page.lines[0].text == OCR_TEXT


def test_blank_or_low_confidence_scan_needs_review() -> None:
    blank = _assemble(_scan_page([], _secondary()))
    assert blank.ocr_score == 0.0 and blank.coverage_ratio == 1.0
    assert blank.review_reasons == ["LOW_OCR_SCORE"]
    blurry = _assemble(
        _scan_page([_ocr_line(OCR_TEXT, i, conf=0.6) for i in range(3)], _secondary(*[(i, []) for i in range(3)]))
    )
    assert blurry.review_reasons == ["LOW_OCR_SCORE", "LOW_CONF_LINES"]
    assert ocr_score([]) == 0.0


def test_scan_page_flags_mark_review() -> None:
    """Phần trang scan của test chọn tầng (ADR-008 mục 9): hình và công thức ⇒ xem lại; bảng ⇒ chờ tầng 3."""
    assert scan_review_reasons(PageFlags(has_figure=True), layout_available=False) == ("FIGURE_ON_SCAN",)
    assert scan_review_reasons(PageFlags(has_formula=True), layout_available=True) == ("FORMULA_ON_SCAN",)
    assert scan_review_reasons(PageFlags(has_table=True), layout_available=False) == ("TIER3_UNAVAILABLE",)
    assert scan_review_reasons(PageFlags(has_table=True), layout_available=True) == ()

    text = [_ocr_line(OCR_TEXT, 0)]
    figure = _assemble(_scan_page(text, _secondary((0, [])), ink=0.17))
    assert figure.has_figure and figure.review_reasons == ["FIGURE_ON_SCAN"]
    # Không có lượt thứ hai: mực ngoài hộp chữ cao còn là một lý do riêng (§5.7).
    no_second = _assemble(_scan_page(text, None, ink=0.17))
    assert no_second.review_reasons == ["HIGH_INK_OUTSIDE_TEXT", "FIGURE_ON_SCAN", "CROSS_CHECK_UNAVAILABLE"]
    plain = _assemble(_scan_page(text, None, ink=CONFIG.figure_ink_min - 0.01))
    assert not plain.has_figure and plain.review_reasons == ["CROSS_CHECK_UNAVAILABLE"]

    formula = [_ocr_line("∑ ∫ √ ≤ ≥ ± α β γ δ", 0)]
    assert _assemble(_scan_page(formula, _secondary((0, [])))).review_reasons == ["FORMULA_ON_SCAN"]

    def cell(text: str, x: float, row: int) -> RawWord:
        top = 0.3 + 0.03 * row
        return RawWord(text=text, bbox=(x, top, x + 0.04, top + 0.015))

    rows = [("P1", "0", "5"), ("P2", "1", "3"), ("P3", "2", "8")]
    grid = [
        RawOcrLine(
            text="   ".join(row),
            bbox=(0.1, 0.3 + 0.03 * r, 0.74, 0.315 + 0.03 * r),
            words=[cell(t, x, r) for t, x in zip(row, (0.1, 0.4, 0.7), strict=True)],
            conf=0.95,
        )
        for r, row in enumerate(rows)
    ]
    secondary = RawSecondaryPass(
        engine="rapidocr",
        lines=[RawSecondaryLine(bbox=line.bbox or (0, 0, 1, 1), tokens=numeric_tokens(line.text)) for line in grid],
    )
    table = _assemble(_scan_page(grid, secondary))
    assert table.has_table and table.review_reasons == ["TIER3_UNAVAILABLE"] and table.tier_used == "ocr"


def test_long_ocr_line_is_chunked_and_mismatch_points_to_first_chunk() -> None:
    long_text = "ab" * 1100 + " 42"  # 2203 ký tự ⇒ hai dòng ≤ 2000
    raw_line = RawOcrLine(text=long_text, bbox=_box(0), words=[], conf=0.95)
    page = _assemble(_scan_page([raw_line, _ocr_line("7", 1)], _secondary((0, ["43"]), (1, ["7"]))))
    assert [len(line.text) for line in page.lines] == [2000, 203, 1]
    assert page.cross_check.mismatches == [TokenMismatch(line_index=0, primary_value="42", secondary_value="43")]
    assert [line.flags for line in page.lines] == [["TOKEN_MISMATCH"], ["TOKEN_MISMATCH"], []]
    # Dòng OCR chỉ có khoảng trắng không sinh dòng nào và không làm lệch chỉ số.
    spaces = RawOcrLine(text="   ", bbox=_box(0), words=[], conf=0.95)
    page = _assemble(_scan_page([spaces, _ocr_line("7", 1)], _secondary((1, ["8"]))))
    assert [line.line_index for line in page.lines] == [0]
    assert page.cross_check.mismatches == [TokenMismatch(line_index=0, primary_value="7", secondary_value="8")]


def test_page_without_ocr_data_keeps_engine_missing() -> None:
    raw = _scan_page([_ocr_line(OCR_TEXT, 0)], None)
    without = RawPdfPage(**{**raw.model_dump(), "ocr": None})
    page = _assemble(without)
    assert (page.status, page.failure_reason, page.lines) == ("failed", "OCR_ENGINE_MISSING", [])
    # Trang lớp chữ tốt không dùng kết quả OCR dù worker có gửi.
    good = RawTextLine(text=OCR_TEXT * 5, bbox=_box(0), words=[])
    digital = RawPdfPage(**{**raw.model_dump(), "lines": [good.model_dump()]})
    assert _assemble(digital).tier_used == "text_layer"


# --------------------------------------------------------------------------- #
# Đối chiếu chéo: token, ghép dòng theo hộp toạ độ, độ phủ
# --------------------------------------------------------------------------- #


def test_numeric_tokens_follow_spec_regex() -> None:
    assert numeric_tokens("ADD R1, R2, #-12 rồi 0x1F và 42") == ["1", "2", "#-12", "0x1F", "42"]
    # Đúng regex của spec: "#0x1F" tách thành "#0" và "1" (nhánh `#?-?\d+` khớp trước tại ký tự "#"); hai lượt
    # đọc tách cùng một cách nên vẫn so được, nhưng token không phải cả giá trị hex.
    assert numeric_tokens("MOV R0, #0x1F") == ["0", "#0", "1"]
    assert numeric_tokens(unicodedata.normalize("NFD", "Bước 3")) == ["3"]
    assert numeric_tokens("0xff là chữ thường") == ["0"]  # regex chỉ nhận hex chữ hoa
    assert numeric_tokens("không có số") == []


def test_compare_tokens_aligns_sequences() -> None:
    assert compare_tokens(["1", "0", "5"], ["1", "0", "5"]) == []
    assert compare_tokens(["1", "0", "5"], ["1", "0", "6"]) == [("5", "6")]
    assert compare_tokens(["105", "#12"], ["1", "0", "5", "#12"]) == [("105", "1")]
    assert compare_tokens(["1", "2", "3"], ["1", "3"]) == [("2", None)]
    assert compare_tokens(["1", "3"], ["1", "2", "3"]) == []  # token chỉ có ở lượt thứ hai không tạo cặp
    assert compare_tokens(["7"], []) == [("7", None)]


def test_secondary_lines_attach_to_the_best_overlapping_primary_line() -> None:
    primary = [(0.1, 0.10, 0.9, 0.12), None, (0.1, 0.13, 0.9, 0.15)]
    left = RawSecondaryLine(bbox=(0.5, 0.095, 0.9, 0.125), tokens=["2"])
    right = RawSecondaryLine(bbox=(0.1, 0.10, 0.4, 0.12), tokens=["1"])
    lower = RawSecondaryLine(bbox=(0.1, 0.128, 0.9, 0.152), tokens=["3"])
    elsewhere = RawSecondaryLine(bbox=(0.1, 0.5, 0.9, 0.52), tokens=["9"])
    other_column = RawSecondaryLine(bbox=(0.92, 0.10, 0.99, 0.12), tokens=["8"])
    assigned = assign_secondary_lines(primary, [left, right, lower, elsewhere, other_column], 0.5)
    assert [[line.tokens for line in lines] for lines in assigned] == [[["1"], ["2"]], [], [["3"]]]
    assert assign_secondary_lines(primary, [lower], 1.0)[2] == [lower]
    barely = RawSecondaryLine(bbox=(0.1, 0.118, 0.9, 0.138), tokens=["4"])  # chồng 0.002/0.02 với dòng đầu
    assert assign_secondary_lines(primary, [barely], 0.5) == [[], [], []]
    flat = RawSecondaryLine(bbox=(0.1, 0.11, 0.9, 0.11), tokens=["5"])
    assert assign_secondary_lines(primary, [flat], 0.5) == [[], [], []]


def test_coverage_ratio() -> None:
    assert coverage_ratio(10, 5) == coverage_ratio(5, 10) == 0.5
    assert coverage_ratio(0, 0) == 1.0 and coverage_ratio(0, 3) == 0.0
