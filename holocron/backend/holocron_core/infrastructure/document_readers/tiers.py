"""Chọn tầng theo điểm và cờ trang, dựng `PageExtraction` (ADR-008 mục 6, 8–11; SPECIFICATION §5.7).

Có tầng 1 (lớp chữ) và tầng 2 (OCR Tesseract, đối chiếu chéo bằng RapidOCR); chưa có tầng 3 và VLM.
- Trang cần OCR (`text_score < TEXT_LAYER_MIN`) mà worker không gửi kết quả OCR (không có engine) thành `failed`
  với `OCR_ENGINE_MISSING` (mục 8). Văn bản tầng 1 của trang đó không được trả ra, vì có thể là ký tự rác.
- Trang lẽ ra lên tầng 3 nhận `TIER3_UNAVAILABLE` (mục 9): trang PDF số có bảng, công thức hoặc hình; trang scan
  có bảng (lưới toạ độ chữ OCR là việc của tầng 3).
- Trang OCR: văn bản từ lượt chính, lý do xem lại theo §5.7 (điểm, độ phủ dòng, mực ngoài hộp chữ, hình/công
  thức trên trang scan, dòng điểm thấp, token lệch, thiếu lượt đối chiếu).
"""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from ...domain.documents import (
    MAX_LINE_CHARS,
    CrossCheck,
    ExtractedLine,
    FailureReason,
    LineFlag,
    PageExtraction,
    ReviewReason,
    TokenMismatch,
)
from .config import PdfReaderConfig
from .cross_check import CrossCheckLimitError, CrossCheckLimits, coverage_ratio, cross_check_lines
from .protocol import RawOcrLine, RawOcrPage, RawPdfPage
from .text_layer import has_figure, has_formula, has_table, page_text, text_score

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
    """Dựng `PageExtraction` từ dữ liệu worker mà tiến trình cha đã validate (tầng 3 chưa có).

    Trang đủ tốt dùng lớp chữ; trang cần OCR dùng `raw.ocr` khi có (`assemble_ocr_page`), không có thì `failed`
    với `OCR_ENGINE_MISSING`.
    """
    text = page_text(raw.lines)
    score = text_score(text, config)
    flags = page_flags(raw, text, config)
    decision = select_tier(score, flags, config, layout_available=False)
    page_number = raw.page_index + 1
    if decision.tier == "ocr":
        if raw.ocr is not None:
            return assemble_ocr_page(raw, raw.ocr, document_id, score, config)
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


# --------------------------------------------------------------------------- #
# Tầng 2: trang OCR
# --------------------------------------------------------------------------- #


def ocr_score(lines: Sequence[RawOcrLine]) -> float:
    """Trung bình `conf` các dòng, trọng số theo số ký tự không trắng (ADR-008 mục 6); trang không có chữ ⇒ 0.0.

    0.0 cho trang trống là chủ ý: trang scan không đọc được chữ nào phải qua người xem (`LOW_OCR_SCORE`).
    """
    weights = [sum(1 for c in line.text if not c.isspace()) for line in lines]
    total = sum(weights)
    if total == 0:
        return 0.0
    weighted = sum(line.conf * weight for line, weight in zip(lines, weights, strict=True))
    return min(1.0, max(0.0, weighted / total))


def scan_flags(raw: RawPdfPage, ocr: RawOcrPage, config: PdfReaderConfig) -> PageFlags:
    """Cờ trang scan (ADR-008 mục 9).

    Bảng theo lưới toạ độ chữ OCR, công thức theo mật độ ký hiệu toán của văn bản OCR, hình khi
    `ink_outside_text_ratio ≥ FIGURE_INK_MIN`.
    """
    return PageFlags(
        has_table=has_table(ocr.lines, raw.width_pt, raw.height_pt, config),
        has_formula=has_formula(page_text(ocr.lines), config),
        has_figure=ocr.ink_outside_text_ratio >= config.figure_ink_min,
    )


def scan_review_reasons(flags: PageFlags, *, layout_available: bool) -> tuple[ReviewReason, ...]:
    """Lý do xem lại do cờ của trang scan (ADR-008 mục 9): hình và công thức không lên tầng 3 trên trang scan."""
    reasons: list[ReviewReason] = []
    if flags.has_figure:
        reasons.append("FIGURE_ON_SCAN")
    if flags.has_formula:
        reasons.append("FORMULA_ON_SCAN")
    if flags.has_table and not layout_available:
        reasons.append("TIER3_UNAVAILABLE")
    return tuple(reasons)


def _line_pieces(lines: Sequence[RawOcrLine]) -> list[list[int]]:
    """Chỉ số `ExtractedLine` mà mỗi dòng OCR thô sinh ra (dòng dài bị cắt thành nhiều đoạn ≤ 2000 ký tự)."""
    pieces: list[list[int]] = []
    next_index = 0
    for line in lines:
        count = len(_chunks(line.text))
        pieces.append(list(range(next_index, next_index + count)))
        next_index += count
    return pieces


def _cross_check(
    ocr: RawOcrPage, pieces: Sequence[Sequence[int]], config: PdfReaderConfig
) -> tuple[CrossCheck, set[int]]:
    """`CrossCheck` của trang và chỉ số các `ExtractedLine` có token lệch.

    Không có lượt thứ hai, hoặc dữ liệu hai lượt vượt trần độ phức tạp của đối chiếu (`cross_check_max_*`) ⇒
    `unavailable`: trang vào `needs_review` với `CROSS_CHECK_UNAVAILABLE`, và ô số của bảng không được AUTO_PASS.
    """
    unavailable = CrossCheck(status="unavailable", secondary_engine=None)
    if ocr.secondary is None:
        return unavailable, set()
    try:
        comparisons = cross_check_lines(
            [line.text for line in ocr.lines],
            [line.bbox for line in ocr.lines],
            ocr.secondary.lines,
            config.line_match_min_overlap,
            limits=CrossCheckLimits.from_config(config),
        )
    except CrossCheckLimitError:
        return unavailable, set()
    mismatches: list[TokenMismatch] = []
    flagged: set[int] = set()
    for comparison in comparisons:
        targets = pieces[comparison.line]
        if not targets:
            continue  # dòng chỉ có khoảng trắng: không có token nào để lệch
        flagged.update(targets)
        mismatches.extend(
            TokenMismatch(line_index=targets[0], primary_value=primary, secondary_value=secondary)
            for primary, secondary in comparison.mismatches
        )
    status: Literal["ok", "mismatch"] = "mismatch" if mismatches else "ok"
    return CrossCheck(status=status, secondary_engine=ocr.secondary.engine, mismatches=mismatches), flagged


def assemble_ocr_page(
    raw: RawPdfPage, ocr: RawOcrPage, document_id: str, score: float, config: PdfReaderConfig
) -> PageExtraction:
    """Dựng trang OCR (tầng 2): văn bản lượt chính, đối chiếu token với lượt thứ hai, lý do xem lại theo §5.7."""
    pieces = _line_pieces(ocr.lines)
    cross_check, mismatched = _cross_check(ocr, pieces, config)
    coverage = None if ocr.secondary is None else coverage_ratio(len(ocr.lines), len(ocr.secondary.lines))

    lines: list[ExtractedLine] = []
    for line, targets in zip(ocr.lines, pieces, strict=True):
        for target, chunk in zip(targets, _chunks(line.text), strict=True):
            flags: list[LineFlag] = []
            if line.conf < config.line_conf_min:
                flags.append("LOW_LINE_CONF")
            if target in mismatched:
                flags.append("TOKEN_MISMATCH")
            lines.append(
                ExtractedLine(
                    line_index=target, text=chunk, bbox=line.bbox, conf=line.conf, engine="tesseract", flags=flags
                )
            )

    flags_of_page = scan_flags(raw, ocr, config)
    page_ocr_score = ocr_score(ocr.lines)
    reasons = list(scan_review_reasons(flags_of_page, layout_available=False))
    if page_ocr_score < config.ocr_score_min:
        reasons.append("LOW_OCR_SCORE")
    if coverage is not None and coverage < config.coverage_min:
        reasons.append("LOW_LINE_COVERAGE")
    if ocr.secondary is None and ocr.ink_outside_text_ratio >= config.figure_ink_min:
        reasons.append("HIGH_INK_OUTSIDE_TEXT")
    if any("LOW_LINE_CONF" in line.flags for line in lines):
        reasons.append("LOW_CONF_LINES")
    if cross_check.status == "mismatch":
        reasons.append("TOKEN_MISMATCH")
    if cross_check.status == "unavailable":
        reasons.append("CROSS_CHECK_UNAVAILABLE")
    return PageExtraction(
        document_id=document_id,
        page_number=raw.page_index + 1,
        status="needs_review" if reasons else "ok",
        tier_used="ocr",
        text_score=score,
        ocr_score=page_ocr_score,
        coverage_ratio=coverage,
        ink_outside_text_ratio=ocr.ink_outside_text_ratio,
        has_table=flags_of_page.has_table,
        has_formula=flags_of_page.has_formula,
        has_figure=flags_of_page.has_figure,
        lines=lines,
        cross_check=cross_check,
        review_reasons=reasons,
    )
