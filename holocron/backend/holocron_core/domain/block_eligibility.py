"""Khối ứng viên của tập đủ điều kiện (SPECIFICATION §5.15 "Tập khối đủ điều kiện"; ADR-010 mục 13).

Hàm thuần, MỘT nguồn cho cả bước (1) của settle (tập ứng viên từ trạng thái trang hiện tại) lẫn
`refresh_eligible_blocks` của hai kho (ghi `blocks.eligible`). "Tài liệu đã đóng" KHÔNG thuộc hàm này: cây và tra cứu
khối lọc theo nó lúc truy vấn.
"""

from __future__ import annotations

from .blocks import Block, BlockType
from .documents import PageSummary
from .schemas import SourceKind

__all__ = ["NON_CANDIDATE_TYPES", "is_candidate_block"]

# Loại khối không bao giờ là ứng viên: lề trang và số trang không phải nội dung.
NON_CANDIDATE_TYPES: frozenset[BlockType] = frozenset({"header_footer", "page_number"})


def is_candidate_block(block: Block, page: PageSummary, source_kind: SourceKind) -> bool:
    """Khối ứng viên ⇔ tài liệu không phải `past_exam`; trang `confirmed`, không `dropped`, không `failed`; `type` không
    thuộc {`header_footer`, `page_number`}; khối không mang `table_token_mismatch`.

    `page` là tóm tắt trạng thái HIỆN TẠI của trang chứa khối; trang khác trang của khối ⇒ `ValueError`.
    """
    if page.page_number != block.page_number:
        raise ValueError("tóm tắt trang không phải trang của khối")
    if source_kind == SourceKind.PAST_EXAM:
        return False
    if not page.confirmed or page.dropped or page.status == "failed":
        return False
    if block.type in NON_CANDIDATE_TYPES:
        return False
    return "table_token_mismatch" not in block.flags
