"""Phần Gate của bảng lấy từ bộ nhập PDF: đối chiếu chéo hai lượt OCR (SPECIFICATION §7.4; ADR-008 mục 11).

Hàm thuần, chưa nối với API nhập đề (việc đó làm ở nhánh khác):
- `secondary_table`: dựng `table_secondary` cùng lưới với `table_primary` khi trang có lượt đọc thứ hai
  (`cross_check` là `ok` hoặc `mismatch`). Tín hiệu S1 sẵn có của `check_table` khi đó sinh `DUAL_PASS_MISMATCH`
  (BLOCKING) kèm `alternatives` là hai giá trị, không cần mã mới. Việc lấy token của lượt thứ hai trong hộp toạ độ
  của từng ô cần bảng của tầng 3 (chưa có), nên ô của lượt thứ hai do nơi gọi truyền vào.
- `cross_check_issues`: trang không có lượt thứ hai (`unavailable`) ⇒ một issue `CROSS_CHECK_UNAVAILABLE` (WARNING)
  cho mỗi ô số chưa được con người xác nhận, nên bảng không thể AUTO_PASS. Cột nào là cột số (`int`/`float` của
  `input_schema`, spec §5.10) do nơi gọi truyền vào, không rẽ nhánh theo môn hay topic.
- `cross_check = "not_applicable"` (trang đọc bằng lớp chữ) hoặc `None` (item không tạo từ trang PDF) không sinh
  issue nào ở đây; hành vi cũ của Gate giữ nguyên.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence

from ..domain.documents import CrossCheckStatus
from ..domain.ingestion import ExtractedCell, ExtractedTable, GateIssue, Severity

CROSS_CHECK_UNAVAILABLE_MESSAGE = (
    "Trang chưa có lượt đọc thứ hai để đối chiếu số này; hãy kiểm tra với bản gốc và xác nhận ô."
)


def secondary_table(
    primary: ExtractedTable, cross_check: CrossCheckStatus | None, cells: Sequence[ExtractedCell]
) -> ExtractedTable | None:
    """`table_secondary` cùng lưới với `primary` (§7.4); `None` khi trang không có lượt thứ hai để đối chiếu.

    Ô vắng mặt trong `cells` là ô lượt thứ hai không đọc được; S1 khi đó cũng sinh `DUAL_PASS_MISMATCH`.
    """
    if cross_check not in ("ok", "mismatch"):
        return None
    grid = {(cell.row, cell.col) for cell in primary.cells}
    if any((cell.row, cell.col) not in grid for cell in cells):
        raise ValueError("ô của lượt đọc thứ hai phải nằm trong lưới của bảng lượt chính")
    return ExtractedTable(
        table_id=primary.table_id,
        page_number=primary.page_number,
        version=primary.version,
        headers=list(primary.headers),
        cells=list(cells),
    )


def cross_check_issues(
    primary: ExtractedTable, cross_check: CrossCheckStatus | None, numeric_columns: Collection[int]
) -> list[GateIssue]:
    """`CROSS_CHECK_UNAVAILABLE` (WARNING, có `row`/`col`) cho mỗi ô số chưa xác nhận khi `cross_check = unavailable`."""
    if cross_check != "unavailable":
        return []
    return [
        GateIssue(
            code="CROSS_CHECK_UNAVAILABLE",
            severity=Severity.WARNING,
            row=cell.row,
            col=cell.col,
            message_vi=CROSS_CHECK_UNAVAILABLE_MESSAGE,
        )
        for cell in sorted(primary.cells, key=lambda c: (c.row, c.col))
        if cell.col in numeric_columns and not cell.verified_by_human
    ]
