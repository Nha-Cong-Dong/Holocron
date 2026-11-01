"""Luật tự xác nhận cấp trang (SPECIFICATION §5.7 "Tự xác nhận cấp trang", GĐ1 1B; ADR-008 mục 7).

Hai hàm thuần, KHÔNG rẽ nhánh theo môn, chương hay topic:

- `is_trusted_source` cài luật `source_trust_v1`: một nơi duy nhất quyết định trang có phải nguồn đáng tin hay không,
  để `page_clean_v1` ở đây và cổng G1 của cặp khái niệm (§5.8) dùng lại, không viết lại điều kiện.
- `passes_page_clean` cài luật `page_clean_v1`: trang đủ sạch để máy tự xác nhận thay người dùng.

Tên luật là hằng ở file này (một nơi); `PageExtraction.confirm_rule` lưu `AUTO_CONFIRM_RULE`.
"""

from __future__ import annotations

from ..domain.documents import PageExtraction
from ..domain.schemas import SourceKind

AUTO_CONFIRM_RULE = "page_clean_v1"
SOURCE_TRUST_RULE = "source_trust_v1"

__all__ = ["AUTO_CONFIRM_RULE", "SOURCE_TRUST_RULE", "is_trusted_source", "passes_page_clean"]


def is_trusted_source(page: PageExtraction) -> bool:
    """`source_trust_v1`: trang là nguồn đáng tin khi (a) `tier_used = "text_layer"` và `scan_text_layer = false`,
    hoặc (b) `tier_used = "ocr"` và `cross_check.status = "ok"`. Mọi trường hợp khác không đáng tin: lớp chữ là lớp
    OCR của PDF scan, tầng `vlm` hay `layout`, OCR có đối chiếu `unavailable`, `mismatch` hay `not_applicable`, trang
    `pending` hoặc `failed` (không có `tier_used`)."""
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
) -> bool:
    """`page_clean_v1`: trang được tự xác nhận khi TẤT CẢ điều kiện đúng.

    - tài liệu bật `auto_confirm` và không phải `past_exam` (trang đề đi tiếp vào Gate, giữ người xác nhận);
    - trang qua `is_trusted_source`;
    - `status = "ok"` (tức `review_reasons` rỗng), `page_role = "content"`, không bỏ mềm, chưa xác nhận, chưa từng bị
      người dùng hoàn tác tự xác nhận, chưa được người dùng hoàn tác bỏ mềm (`restored`: người dùng đã chủ động giữ
      trang, để họ tự xác nhận; spec chưa liệt kê điều kiện này, chọn phía an toàn);
    - `has_text` (có ít nhất một dòng có chữ) và `fits_vault` (vừa giới hạn trang của Vault) do người gọi tính bằng
      đúng định nghĩa dùng cho xác nhận thường.
    """
    return (
        auto_confirm
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
