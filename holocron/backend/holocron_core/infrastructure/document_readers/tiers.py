"""Chọn tầng theo điểm và cờ trang, dựng `PageExtraction` (ADR-008 mục 6, 9; SPECIFICATION §5.7).

Nhánh này mới có tầng 1. Trang cần OCR (`text_score < TEXT_LAYER_MIN`) thành `failed` với `OCR_ENGINE_MISSING`,
đúng như khi thiếu engine OCR mà không có VLM (ADR-008 mục 8); trang lẽ ra lên tầng 3 nhận `TIER3_UNAVAILABLE`
(mục 9). Văn bản tầng 1 của trang cần OCR không được trả ra, vì có thể là ký tự rác (TCVN3/VNI).
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Literal

from ...domain.documents import (
    MAX_LINE_CHARS,
    CrossCheck,
    ExtractedLine,
    FailureReason,
    PageExtraction,
    ReviewReason,
)
from .config import PdfReaderConfig
from .protocol import RawPdfPage
from .text_layer import has_figure, has_formula, has_table, text_score

NOT_APPLICABLE = CrossCheck(status="not_applicable", secondary_engine=None)


@dataclass(frozen=True)
class PageFlags:
    has_table: bool = False
    has_formula: bool = False
    has_figure: bool = False


@dataclass(frozen=True)
class TierDecision:
    """`tier` là tầng cho văn bản của trang; `review_reasons` là lý do do chính việc chọn tầng sinh ra."""

    tier: Literal["text_layer", "ocr", "layout"]
    review_reasons: tuple[ReviewReason, ...] = ()


def select_tier(score: float, flags: PageFlags, config: PdfReaderConfig, *, layout_available: bool) -> TierDecision:
    """Lớp chữ đủ tốt (`score ≥ TEXT_LAYER_MIN`) ⇒ PDF số; ngược lại cần OCR (tầng 2).

    Trên PDF số, `has_table`, `has_formula` hoặc `has_figure` đưa trang lên tầng 3; tầng 3 chưa có thì giữ lớp chữ
    và đánh dấu `TIER3_UNAVAILABLE`. Cờ của trang scan do tầng 2 xử lý (ngoài phạm vi tầng 1).
    """
    if score < config.text_layer_min:
        return TierDecision(tier="ocr")
    if not (flags.has_table or flags.has_formula or flags.has_figure):
        return TierDecision(tier="text_layer")
    if layout_available:
        return TierDecision(tier="layout")
    return TierDecision(tier="text_layer", review_reasons=("TIER3_UNAVAILABLE",))


def page_flags(raw: RawPdfPage, text: str, config: PdfReaderConfig) -> PageFlags:
    return PageFlags(
        has_table=has_table(raw.lines, raw.width_pt, raw.height_pt, config),
        has_formula=has_formula(text, config),
        has_figure=has_figure(raw.image_area_ratio, raw.path_area_ratio, config),
    )


def failed_page(
    document_id: str,
    page_number: int,
    reason: FailureReason,
    *,
    score: float | None = None,
    flags: PageFlags | None = None,
) -> PageExtraction:
    flags = flags or PageFlags()
    return PageExtraction(
        document_id=document_id,
        page_number=page_number,
        status="failed",
        tier_used=None,
        text_score=score,
        has_table=flags.has_table,
        has_formula=flags.has_formula,
        has_figure=flags.has_figure,
        cross_check=NOT_APPLICABLE,
        failure_reason=reason,
    )


def _chunks(text: str) -> list[str]:
    """Chuẩn hoá NFC trước rồi mới cắt, để mỗi đoạn vẫn ≤ `MAX_LINE_CHARS` sau khi model chuẩn hoá lại."""
    stripped = unicodedata.normalize("NFC", text).strip()
    return [stripped[i : i + MAX_LINE_CHARS] for i in range(0, len(stripped), MAX_LINE_CHARS)]


def assemble_text_layer_page(raw: RawPdfPage, document_id: str, config: PdfReaderConfig) -> PageExtraction:
    """Dựng `PageExtraction` từ dữ liệu tầng 1 mà tiến trình cha đã validate (tầng 3 chưa có)."""
    text = "\n".join(line.text for line in raw.lines)
    score = text_score(text, config)
    flags = page_flags(raw, text, config)
    decision = select_tier(score, flags, config, layout_available=False)
    page_number = raw.page_index + 1
    if decision.tier == "ocr":
        return failed_page(document_id, page_number, "OCR_ENGINE_MISSING", score=score, flags=flags)
    pieces = [(chunk, line.bbox) for line in raw.lines for chunk in _chunks(line.text)]
    lines = [
        ExtractedLine(line_index=index, text=chunk, bbox=bbox, conf=score, engine="text_layer")
        for index, (chunk, bbox) in enumerate(pieces)
    ]
    return PageExtraction(
        document_id=document_id,
        page_number=page_number,
        status="needs_review" if decision.review_reasons else "ok",
        tier_used="text_layer",
        text_score=score,
        has_table=flags.has_table,
        has_formula=flags.has_formula,
        has_figure=flags.has_figure,
        lines=lines,
        cross_check=NOT_APPLICABLE,
        review_reasons=list(decision.review_reasons),
    )
