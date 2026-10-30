"""Cổng tích sẵn của cặp khái niệm, luật TẠM `preselect_page_flags_v1` (spec §5.8; ADR-007 mục 3).

Mỗi cổng xét trên các TRANG nguồn của chunk (`page_number` tới `page_end`) bằng cờ cấp trang đang lưu trong JSON trang
(`pages.extraction_json`). Trang được đọc bằng `PageGateView`, model chỉ lấy đúng các field cần và bỏ qua field khác,
nên đọc được cả JSON trang trước và sau GĐ0. Quy tắc an toàn: THIẾU DỮ LIỆU ⇒ CỔNG TRƯỢT, không bao giờ "qua":
- trang nguồn không còn trong kho (tài liệu đã xoá, hoặc chunk không có trang) ⇒ mọi cổng xét theo trang trượt (spec
  nêu G1; các cổng còn lại cùng quy tắc thiếu dữ liệu);
- field của bộ khử nhiễu GĐ0 (`scan_text_layer`, `page_role`) vắng trong JSON trang ⇒ nửa cổng cần field đó trượt.
  `scan_text_layer` có mặt là dấu hiệu trang đã qua GĐ0, nên cũng là điều kiện để tin việc vắng các lý do
  `FONT_ENCODING_SUSPECT`, `TEXT_OCCLUDED`, `LOW_TEXT_CONTRAST`.
- nhãn `is_exercise` của chunk chưa xác định (LLM không trả, trả null, hoặc trả xung đột) ⇒ `EXERCISE` trượt.

Code thuần, không gọi LLM; không rẽ nhánh theo môn hay chương.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Callable, Iterable
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from ..domain.schemas import KnowledgeChunk
from ..domain.vault import PAIR_GATE_ORDER, PairGate

RULE_NAME = "preselect_page_flags_v1"

_CROSS_CHECK_PASS = frozenset({"ok", "not_applicable"})
_GLYPH_REASONS = frozenset({"FONT_ENCODING_SUSPECT"})
_OCCLUSION_REASONS = frozenset({"TEXT_OCCLUDED", "LOW_TEXT_CONTRAST"})
_REPLACEMENT_CHAR = "�"


class CrossCheckView(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    status: str


class PageGateView(BaseModel):
    """Các field cấp trang mà luật tạm dùng; `None` nghĩa là JSON trang không có field đó (trang trước GĐ0)."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    tier_used: str | None = None
    scan_text_layer: bool | None = None
    review_reasons: list[str] = Field(default_factory=list)
    has_table: bool | None = None
    cross_check: CrossCheckView | None = None
    page_role: str | None = None


class PageSource(Protocol):
    """Đọc JSON trang theo model (`DocumentRepository.get_page`)."""

    def get_page[M: BaseModel](self, document_id: str, page_number: int, model: type[M]) -> M | None: ...


PageLookup = Callable[[str, int], PageGateView | None]


def page_lookup(pages: PageSource | None) -> PageLookup:
    """Hàm đọc trang có nhớ đệm trong một lần đề xuất; `pages = None` ⇒ mọi trang coi như không có."""
    cache: dict[tuple[str, int], PageGateView | None] = {}

    def lookup(document_id: str, page_number: int) -> PageGateView | None:
        key = (document_id, page_number)
        if key not in cache:
            cache[key] = None if pages is None else pages.get_page(document_id, page_number, PageGateView)
        return cache[key]

    return lookup


def _suspect_glyph(text: str) -> bool:
    """U+FFFD hoặc ký tự vùng dùng riêng (PUA, nhóm `Co`)."""
    return any(char == _REPLACEMENT_CHAR or unicodedata.category(char) == "Co" for char in text)


def _source_pages(chunk: KnowledgeChunk) -> range:
    return range(chunk.page_number, (chunk.page_end or chunk.page_number) + 1)


def _page_gates(page: PageGateView | None) -> set[PairGate]:
    """Cổng trượt vì một trang nguồn (thiếu trang hay thiếu field ⇒ trượt)."""
    if page is None:
        return {"G1_SOURCE", "G2_GLYPH", "G3_OCCLUDED", "G4_COLUMN_MIX", "G5_NUMBERS", "EXERCISE"}
    failed: set[PairGate] = set()
    reasons = set(page.review_reasons)
    denoised = page.scan_text_layer is not None
    if page.tier_used != "text_layer" or page.scan_text_layer is not False:
        failed.add("G1_SOURCE")
    if not denoised or reasons & _GLYPH_REASONS:
        failed.add("G2_GLYPH")
    if not denoised or reasons & _OCCLUSION_REASONS:
        failed.add("G3_OCCLUDED")
    if page.has_table is not False:
        failed.add("G4_COLUMN_MIX")
    if page.cross_check is None or page.cross_check.status not in _CROSS_CHECK_PASS:
        failed.add("G5_NUMBERS")
    if page.page_role is None or page.page_role == "exercise":
        failed.add("EXERCISE")
    return failed


def failed_gates(
    chunk: KnowledgeChunk, definition_quote: str, exercise_label: bool | None, lookup: PageLookup
) -> list[PairGate]:
    """Các cổng trượt của một cặp theo `preselect_page_flags_v1`, sắp theo thứ tự khai báo của `PairGate`.

    `exercise_label` là nhãn `is_exercise` đã xác định của chunk (`None` khi chưa xác định ⇒ `EXERCISE` trượt).
    """
    failed: set[PairGate] = set()
    for page_number in _source_pages(chunk):
        failed |= _page_gates(lookup(chunk.doc_id, page_number))
    if _suspect_glyph(definition_quote):
        failed.add("G2_GLYPH")
    if exercise_label is not False:
        failed.add("EXERCISE")
    return _ordered(failed)


def _ordered(gates: Iterable[PairGate]) -> list[PairGate]:
    present = set(gates)
    return [gate for gate in PAIR_GATE_ORDER if gate in present]


def pre_selected(gates: list[PairGate], enabled: bool) -> bool:
    """Tích sẵn khi và chỉ khi luật đang bật VÀ không cổng nào trượt. Không phải xác nhận (status vẫn `proposed`)."""
    return enabled and not gates
