"""Mặc định an toàn của `cross_check_issues` (spec §7.4; ADR-008 mục 11; review tầng 2, lỗi 4; dòng I5).

Bảng lấy từ trang PDF không phải lớp chữ mà thiếu trạng thái đối chiếu thì coi như `unavailable`: ô số chưa xác
nhận nhận `CROSS_CHECK_UNAVAILABLE` và bảng không thể AUTO_PASS. Không rõ cột số thì mọi ô có token số đều tính.
"""

from __future__ import annotations

import pytest

from holocron_core.domain.documents import CrossCheckStatus
from holocron_core.domain.ingestion import ExtractedCell, ExtractedTable, GateStatus, Severity
from holocron_core.services.cross_check_gate import PdfPageSource, cross_check_issues
from holocron_core.services.ingestion_gate import CellPatch, apply_patches, check_table, gate_status

TOPIC = "subject.unknown.topic"
ROWS = [("P1", "0", "5"), ("Tổng", "—", "0x1F"), ("ghi chú", "không", "#-3")]
NUMERIC_CELLS = [(0, 0), (0, 1), (0, 2), (1, 2), (2, 2)]  # mọi ô có token số theo regex của §5.7
COLUMN_CELLS = [(r, c) for r in range(3) for c in (1, 2)]  # mọi ô của cột số {1, 2}


def _table() -> ExtractedTable:
    cells = [
        ExtractedCell(row=r, col=c, text=t, confidence=0.97) for r, row in enumerate(ROWS) for c, t in enumerate(row)
    ]
    return ExtractedTable(table_id="tbl_pdf", page_number=1, headers=["Nhãn", "A", "B"], cells=cells)


def _positions(
    primary: ExtractedTable, status: CrossCheckStatus | None, source: PdfPageSource | None
) -> list[tuple[int, int]]:
    issues = cross_check_issues(primary, status, {1, 2}, page_source=source)
    assert all(i.code == "CROSS_CHECK_UNAVAILABLE" and i.severity is Severity.WARNING for i in issues)
    return [(i.row, i.col) for i in issues]


@pytest.mark.parametrize("source", ["ocr", "layout", "vlm", "unknown"])
@pytest.mark.parametrize("status", [None, "not_applicable", "unavailable"])
def test_non_text_layer_source_without_cross_check_is_unavailable(
    source: PdfPageSource, status: CrossCheckStatus | None
) -> None:
    primary = _table()
    assert _positions(primary, status, source) == COLUMN_CELLS
    issues = check_table(TOPIC, primary, None, None, None) + cross_check_issues(
        primary, status, {1, 2}, page_source=source
    )
    assert gate_status(issues) is GateStatus.NEEDS_REVIEW  # không thể AUTO_PASS


@pytest.mark.parametrize("source", ["ocr", "unknown", None])
@pytest.mark.parametrize("status", ["ok", "mismatch"])
def test_pages_with_a_second_pass_leave_numbers_to_s1(source: PdfPageSource | None, status: CrossCheckStatus) -> None:
    assert _positions(_table(), status, source) == []


def test_only_the_text_layer_skips_the_cross_check() -> None:
    primary = _table()
    assert _positions(primary, None, "text_layer") == []
    assert _positions(primary, "not_applicable", "text_layer") == []
    # Nguồn lớp chữ nhưng trạng thái báo không có lượt thứ hai: theo trạng thái (an toàn hơn).
    assert _positions(primary, "unavailable", "text_layer") == COLUMN_CELLS


def test_without_a_source_the_status_decides_as_in_spec() -> None:
    """Không truyền nguồn: `None` là item không tạo từ trang PDF (§7.4), hành vi cũ của Gate giữ nguyên."""
    primary = _table()
    assert _positions(primary, None, None) == []
    assert _positions(primary, "not_applicable", None) == []
    assert _positions(primary, "unavailable", None) == COLUMN_CELLS


@pytest.mark.parametrize("columns", [set(), (), frozenset()])
def test_unknown_numeric_columns_mark_every_cell_with_a_number(columns: set[int] | tuple[int, ...]) -> None:
    primary = _table()
    issues = cross_check_issues(primary, "unavailable", columns)
    assert [(i.row, i.col) for i in issues] == NUMERIC_CELLS
    assert [(i.row, i.col) for i in cross_check_issues(primary, None, page_source="ocr")] == NUMERIC_CELLS
    # Ô đã được con người xác nhận thì hết issue, như với cột số tường minh.
    patched, _ = apply_patches(primary, [CellPatch(row=0, col=0, new_text="P1")], editor_id="tester")
    assert (0, 0) not in [(i.row, i.col) for i in cross_check_issues(patched, "unavailable", columns)]
