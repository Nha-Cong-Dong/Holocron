"""Phần Gate của bảng lấy từ bộ nhập PDF: đối chiếu chéo hai lượt OCR (SPECIFICATION §7.4; ADR-008 mục 11).

Hàm thuần, chưa nối với API nhập đề (việc đó làm ở nhánh khác):
- `secondary_table`: dựng `table_secondary` cùng lưới với `table_primary` khi trang có lượt đọc thứ hai
  (`cross_check` là `ok` hoặc `mismatch`). Tín hiệu S1 sẵn có của `check_table` khi đó sinh `DUAL_PASS_MISMATCH`
  (BLOCKING) kèm `alternatives` là hai giá trị, không cần mã mới. Việc lấy token của lượt thứ hai trong hộp toạ độ
  của từng ô cần bảng của tầng 3 (chưa có), nên ô của lượt thứ hai do nơi gọi truyền vào.
- `cross_check_issues`: trang không có lượt thứ hai (`unavailable`) ⇒ một issue `CROSS_CHECK_UNAVAILABLE` (WARNING)
  cho mỗi ô số chưa được con người xác nhận, nên bảng không thể AUTO_PASS. Cột nào là cột số (`int`/`float` của
  `input_schema`, spec §5.10) do nơi gọi truyền vào, không rẽ nhánh theo môn hay topic; nơi gọi không biết cột
  số (tập rỗng) thì mọi ô có token số (cùng regex của đối chiếu chéo, spec §5.7) đều coi là ô số.

Mặc định an toàn theo nguồn của bảng (`page_source`, truyền tường minh khi item tạo từ trang PDF):
- `"text_layer"` (tầng 1, trang đọc bằng lớp chữ) là nguồn DUY NHẤT được bỏ qua bước này khi trang không có trạng
  thái đối chiếu (`None` hoặc `not_applicable`); S4 `NOT_IN_TEXT_LAYER` vẫn áp dụng.
- Nguồn khác (`"ocr"`, `"layout"`, `"vlm"`) hoặc `"unknown"` (biết là trang PDF nhưng không rõ tầng, coi như OCR)
  mà thiếu trạng thái đối chiếu (`None`) hay mang `not_applicable` (mâu thuẫn với nguồn) ⇒ coi như `unavailable`.
- Không truyền `page_source` (`None`): nguồn suy từ trạng thái theo spec §7.4. `cross_check = None` là item không
  tạo từ trang PDF (§6.1, `POST /v1/ingestion/items`), hành vi cũ của Gate giữ nguyên; `not_applicable` là trang
  lớp chữ; các trạng thái còn lại là trang OCR.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from typing import Literal

from ..domain.documents import CrossCheckStatus, TierUsed
from ..domain.ingestion import ExtractedCell, ExtractedTable, GateIssue, Severity
from ..infrastructure.document_readers.cross_check import numeric_tokens

# Nguồn của bảng khi item tạo từ trang PDF: tầng đã cho ra văn bản của trang (`PageExtraction.tier_used`), hoặc
# `"unknown"` khi biết là trang PDF nhưng không rõ tầng (được xử lý như trang OCR).
PdfPageSource = TierUsed | Literal["unknown"]

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


def _effective_status(
    cross_check: CrossCheckStatus | None, page_source: PdfPageSource | None
) -> CrossCheckStatus | None:
    """Trạng thái đối chiếu dùng để sinh issue, sau khi áp mặc định an toàn theo nguồn (xem docstring của module)."""
    if page_source is None or page_source == "text_layer":
        return cross_check
    if cross_check is None or cross_check == "not_applicable":
        return "unavailable"
    return cross_check


def _is_numeric_cell(cell: ExtractedCell, numeric_columns: Collection[int]) -> bool:
    if numeric_columns:
        return cell.col in numeric_columns
    return bool(numeric_tokens(cell.text))


def cross_check_issues(
    primary: ExtractedTable,
    cross_check: CrossCheckStatus | None,
    numeric_columns: Collection[int] = (),
    *,
    page_source: PdfPageSource | None = None,
) -> list[GateIssue]:
    """`CROSS_CHECK_UNAVAILABLE` (WARNING, có `row`/`col`) cho mỗi ô số chưa xác nhận khi trang không có đối chiếu.

    "Không có đối chiếu" là `cross_check = unavailable`, hoặc nguồn khác lớp chữ mà thiếu trạng thái (mặc định an
    toàn, xem docstring của module). `numeric_columns` rỗng ⇒ mọi ô có token số là ô số.
    """
    if _effective_status(cross_check, page_source) != "unavailable":
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
        if _is_numeric_cell(cell, numeric_columns) and not cell.verified_by_human
    ]
