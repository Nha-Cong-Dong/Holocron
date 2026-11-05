"""Luật tự xác nhận cấp trang (SPECIFICATION §5.7 "Tự xác nhận cấp trang", GĐ1 1B; ADR-008 mục 7).

Hai hàm thuần, KHÔNG rẽ nhánh theo môn, chương hay topic:

- `is_trusted_source` cài luật `source_trust_v1`: một nơi duy nhất quyết định trang có phải nguồn đáng tin hay không,
  để `page_clean_v1` ở đây và cổng G1 của cặp khái niệm (§5.8) dùng lại, không viết lại điều kiện.
- `passes_page_clean` cài luật `page_clean_v1`: trang đủ sạch để máy tự xác nhận thay người dùng.

Tên luật là hằng ở file này (một nơi); `PageExtraction.confirm_rule` lưu `AUTO_CONFIRM_RULE`.
"""

from __future__ import annotations

from collections.abc import Iterable

from ..domain.blocks import Block, BlockFlag
from ..domain.documents import PageExtraction
from ..domain.schemas import SourceKind

AUTO_CONFIRM_RULE = "page_clean_v1"
SOURCE_TRUST_RULE = "source_trust_v1"

# Cờ DỮ LIỆU của khối chặn tự xác nhận trang (ADR-010 mục 13 "Tự xác nhận"; SPEC §5.7). Cờ ảnh cắt `crop_pending`,
# `crop_failed`, `crop_capped` chỉ báo thiếu ảnh cắt nên KHÔNG chặn (người dùng 2026-11-03); `code_layout_uncertain`
# cả hai nghĩa đều không chặn (duyệt 2026-11-03).
BLOCKING_BLOCK_FLAGS: frozenset[BlockFlag] = frozenset(
    {"layout_uncertain", "table_unresolved", "table_token_mismatch", "vlm_gate_rejected", "ai_proposal_rejected"}
)

__all__ = [
    "AUTO_CONFIRM_RULE",
    "BLOCKING_BLOCK_FLAGS",
    "SOURCE_TRUST_RULE",
    "has_blocking_block_flags",
    "is_trusted_source",
    "passes_page_clean",
]


def has_blocking_block_flags(blocks: Iterable[Block]) -> bool:
    """Có khối mang ít nhất một cờ thuộc `BLOCKING_BLOCK_FLAGS`."""
    return any(BLOCKING_BLOCK_FLAGS.intersection(block.flags) for block in blocks)


def is_trusted_source(page: PageExtraction) -> bool:
    """`source_trust_v1`: trang là nguồn đáng tin khi (a) `tier_used = "text_layer"` và `scan_text_layer = false`,
    hoặc (b) `tier_used = "ocr"` và `cross_check.status = "ok"`. Mọi trường hợp khác không đáng tin: lớp chữ là lớp
    OCR của PDF scan, tầng `vlm` hay `layout`, OCR có đối chiếu `unavailable`, `mismatch` hay `not_applicable`, trang
    `pending` hoặc `failed` (không có `tier_used`).

    ADR-009 mục 3.6 (xét theo khối, không theo `tier_used`): trang có `VLM_REGION` (có vùng đã nhận kết quả VLM; khối
    `source = "vlm"` chỉ được ghi cùng lý do này và lý do không bao giờ bị bỏ) KHÔNG đáng tin, bất kể `tier_used` và
    `cross_check`, kể cả sau khi người dùng xác nhận."""
    if "VLM_REGION" in page.review_reasons:
        return False
    if page.tier_used == "text_layer":
        return not page.scan_text_layer
    if page.tier_used == "ocr":
        return page.cross_check.status == "ok"
    return False


def passes_page_clean(
    page: PageExtraction,
    *,
    auto_confirm: bool,
    source_kind: SourceKind,
    has_text: bool,
    fits_vault: bool,
    has_blocking_block_flags: bool = False,
) -> bool:
    """`page_clean_v1`: trang được tự xác nhận khi TẤT CẢ điều kiện đúng.

    - tài liệu bật `auto_confirm` và không phải `past_exam` (trang đề đi tiếp vào Gate, giữ người xác nhận);
    - trang qua `is_trusted_source`;
    - `status = "ok"` (tức `review_reasons` rỗng), `page_role = "content"`, không bỏ mềm, chưa xác nhận, chưa từng bị
      người dùng hoàn tác tự xác nhận, chưa được người dùng hoàn tác bỏ mềm (`restored`: người dùng đã chủ động giữ
      trang, để họ tự xác nhận; spec chưa liệt kê điều kiện này, chọn phía an toàn);
    - `has_text` (có ít nhất một dòng có chữ) và `fits_vault` (vừa giới hạn trang của Vault) do người gọi tính bằng
      đúng định nghĩa dùng cho xác nhận thường;
    - (X09, ADR-010 mục 13) không khối đã lưu nào của trang mang cờ thuộc `BLOCKING_BLOCK_FLAGS`
      (`has_blocking_block_flags`, người gọi tính từ khối đã lưu). Cờ ảnh cắt (`crop_pending`, `crop_failed`,
      `crop_capped`) KHÔNG chặn: khối chữ trên trang có bảng/hình/công thức tự xác nhận như thường (người dùng
      2026-11-03). Trang `LAYOUT_FAILED` đã bị chặn vì có `review_reasons`.
    """
    return (
        not has_blocking_block_flags
        and auto_confirm
        and source_kind != SourceKind.PAST_EXAM
        and is_trusted_source(page)
        and page.status == "ok"
        and not page.review_reasons
        and page.page_role == "content"
        and not page.dropped
        and not page.restored
        and not page.confirmed
        and not page.auto_confirm_undone
        and has_text
        and fits_vault
    )
