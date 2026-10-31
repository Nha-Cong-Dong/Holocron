"""Chọn tầng theo điểm và cờ trang, dựng `PageExtraction` (ADR-008 mục 6, 8–11; SPECIFICATION §5.7).

Có tầng 1 (lớp chữ) và tầng 2 (OCR Tesseract, đối chiếu chéo bằng RapidOCR); chưa có tầng 3 và VLM.
- Trang cần OCR theo `text_layer.needs_ocr_page` (lớp chữ rỗng, lớp chữ rác `text_score < TEXT_LAYER_MIN`, trang
  ảnh chỉ có ít chữ (luật (c) `sparse_text_on_image`), hoặc ảnh phủ gần kín với ít chữ (luật (d)
  `image_covers_page`); cùng hàm worker dùng) mà worker không gửi kết quả OCR (không có engine) thành `failed` với
  `OCR_ENGINE_MISSING` (mục 8). Văn bản tầng 1 của trang đó không được trả ra, vì có thể là ký tự rác.
- Độ phủ ảnh của luật (c), (d) và phần ảnh của cờ hình chỉ tính ảnh KHÔNG phải template (ảnh lặp lại cùng nội dung,
  cùng chỗ trên đa số trang, `template_images`; sửa đổi 2026-10-30), qua `text_layer.content_image_area`.
- Trang lẽ ra lên tầng 3 nhận `TIER3_UNAVAILABLE` (mục 9): trang PDF số có bảng hoặc công thức; trang scan có
  bảng (lưới toạ độ chữ OCR là việc của tầng 3). Từ GĐ0 (sửa đổi 2026-10-31) hình trên PDF số KHÔNG còn sinh lý do
  này: luật 0C (`text_layer.ocr_reason` luật (e)) đưa trang ít chữ có ảnh nội dung lớn đi OCR kèm
  `IMAGE_NEEDS_READING`; trang giữ lớp chữ có hình chỉ ghi vùng ảnh (`own_images`), trừ khi khử nhiễu không chạy được
  (`DENOISE_UNAVAILABLE`).
- GĐ0 0B: cờ dòng lớp chữ (`LOW_TEXT_CONTRAST`, `TEXT_OCCLUDED` do worker đo; `FONT_ENCODING_SUSPECT` theo cổng
  glyph) thành lý do xem lại cùng tên; dòng header/footer mang `is_header_footer`.
- Trang OCR: văn bản từ lượt chính, lý do xem lại theo §5.7 (điểm, độ phủ dòng, mực ngoài hộp chữ, hình/công
  thức trên trang scan, dòng điểm thấp, token lệch, thiếu lượt đối chiếu).
"""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from ...domain.documents import (
    CrossCheck,
    ExtractedLine,
    FailureReason,
    LineFlag,
    PageExtraction,
    PageImage,
    ReviewReason,
    TokenMismatch,
)
from ...domain.limits import MAX_LINE_CHARS
from .config import PdfReaderConfig
from .cross_check import CrossCheckLimitError, CrossCheckLimits, coverage_ratio, cross_check_lines
from .protocol import RawOcrLine, RawOcrPage, RawPdfPage, RawTextLine
from .text_layer import (
    content_image_area,
    content_path_area,
    content_token_count,
    font_encoding_suspect,
    has_figure,
    has_formula,
    has_table,
    image_needs_reading,
    ocr_reason,
    page_text,
    text_score,
)

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
    """Chọn tầng theo chất lượng lớp chữ và cờ trang (ADR-008 mục 6, 9; sửa đổi 2026-10-29).

    `score` là `text_score`, chỉ đo lớp chữ có rác hay không (không đo nhiều hay ít chữ): `score < TEXT_LAYER_MIN`
    ⇒ OCR (tầng 2). Hàm này KHÔNG xét luật lớp chữ rỗng hay trang ảnh ít chữ: luật chọn tầng đầy đủ là
    `text_layer.ocr_reason` (`needs_ocr_page`), nơi gọi xét nó trước và chỉ gọi hàm này cho trang giữ lớp chữ
    (`assemble_text_layer_page`). Trên PDF số, `has_table`, `has_formula` hoặc `has_figure` đưa trang lên tầng 3;
    tầng 3 chưa có thì giữ lớp chữ và đánh dấu `TIER3_UNAVAILABLE`. Cờ của trang scan do tầng 2 xử lý.
    """
    if score < config.text_layer_min:
        return TierDecision(tier="ocr")
    if not (flags.has_table or flags.has_formula or flags.has_figure):
        return TierDecision(tier="text_layer")
    if layout_available:
        return TierDecision(tier="layout")
    return TierDecision(tier="text_layer", review_reasons=("TIER3_UNAVAILABLE",))


def page_flags(raw: RawPdfPage, text: str, config: PdfReaderConfig) -> PageFlags:
    """Cờ trang PDF số. Cờ hình (GĐ0, sửa đổi 2026-10-31; trả ADR-004 BE-007) chỉ tính đối tượng không-template,
    không-nền: độ phủ ảnh nội dung, và diện tích đường vẽ nội dung khi trang có ảnh nội dung (`has_figure`)."""
    content = content_image_area(raw)
    return PageFlags(
        has_table=has_table(raw.lines, raw.width_pt, raw.height_pt, config),
        has_formula=has_formula(text, config),
        has_figure=has_figure(content, content_path_area(raw), config, content_image_area_ratio=content),
    )


def denoise_unavailable(raw: RawPdfPage) -> bool:
    """Khử nhiễu không chạy đầy đủ cho trang (0C): lượt quét cấp tài liệu không `ok`, hoặc trang vượt trần đối tượng."""
    return raw.objects_over_cap or not raw.template_scan_ok


def _page_images(raw: RawPdfPage) -> list[PageImage]:
    return [PageImage(bbox=image.bbox, sha256=image.sha256, dhash=image.dhash) for image in raw.own_images]


def _denoise_fields(raw: RawPdfPage) -> dict[str, object]:
    """Field GĐ0 của `PageExtraction` lấy thẳng từ dữ liệu worker (0A, 0B(1), 0C)."""
    return {
        "own_images": _page_images(raw),
        "hidden_chars_dropped": raw.hidden_chars_dropped,
        "scan_text_layer": raw.scan_text_layer,
        "content_token_count": content_token_count(raw.lines),
    }


def _text_layer_flags(line: RawTextLine) -> list[LineFlag]:
    """Cờ dòng lớp chữ: cờ worker đo (0B(1), 0B(2)) và cổng glyph (0B(3)).

    Dòng header/footer không mang cờ chất lượng chữ (đề xuất suy ra): dòng đó không vào văn bản xác nhận, chunk hay
    token, nên cờ trên nó không có gì để người dùng xem lại và chỉ làm trang thành `needs_review` vô ích.
    """
    if line.header_footer:
        return []
    flags: list[LineFlag] = list(line.flags)
    if font_encoding_suspect(line.text):
        flags.append("FONT_ENCODING_SUSPECT")
    return flags


_LINE_FLAG_REASONS: tuple[tuple[LineFlag, ReviewReason], ...] = (
    ("LOW_TEXT_CONTRAST", "LOW_TEXT_CONTRAST"),
    ("TEXT_OCCLUDED", "TEXT_OCCLUDED"),
    ("FONT_ENCODING_SUSPECT", "FONT_ENCODING_SUSPECT"),
)


def line_flag_reasons(lines: Sequence[ExtractedLine]) -> list[ReviewReason]:
    """Lý do xem lại cùng tên với cờ dòng GĐ0 còn trên trang."""
    present = {flag for line in lines for flag in line.flags}
    return [reason for flag, reason in _LINE_FLAG_REASONS if flag in present]


def failed_page(
    document_id: str,
    page_number: int,
    reason: FailureReason,
    *,
    score: float | None = None,
    flags: PageFlags | None = None,
    raw: RawPdfPage | None = None,
) -> PageExtraction:
    """Trang `failed`; có `raw` (lớp chữ đã đọc được) thì giữ thêm các field GĐ0 của worker."""
    flags = flags or PageFlags()
    return PageExtraction.model_validate(
        {
            "document_id": document_id,
            "page_number": page_number,
            "status": "failed",
            "tier_used": None,
            "text_score": score,
            "has_table": flags.has_table,
            "has_formula": flags.has_formula,
            "has_figure": flags.has_figure,
            "cross_check": NOT_APPLICABLE,
            "failure_reason": reason,
            **({} if raw is None else _denoise_fields(raw)),
        }
    )


def _chunks(text: str) -> list[str]:
    """Chuẩn hoá NFC trước rồi mới cắt, để mỗi đoạn vẫn ≤ `MAX_LINE_CHARS` sau khi model chuẩn hoá lại."""
    stripped = unicodedata.normalize("NFC", text).strip()
    return [stripped[i : i + MAX_LINE_CHARS] for i in range(0, len(stripped), MAX_LINE_CHARS)]


def assemble_text_layer_page(raw: RawPdfPage, document_id: str, config: PdfReaderConfig) -> PageExtraction:
    """Dựng `PageExtraction` từ dữ liệu worker mà tiến trình cha đã validate (tầng 3 chưa có).

    Trang cần OCR theo `ocr_reason` (cùng hàm worker dùng để quyết định OCR) dùng `raw.ocr` khi có
    (`assemble_ocr_page`), không có thì `failed` với `OCR_ENGINE_MISSING`; trang còn lại chọn tầng bằng `select_tier`
    với cờ bảng và công thức (GĐ0: cờ hình không đưa trang lên tầng 3 nữa, luật 0C). Trang giữ lớp chữ có hình mà
    khử nhiễu không chạy đầy đủ (`denoise_unavailable`) mang `DENOISE_UNAVAILABLE`.
    """
    text = page_text(raw.lines)
    score = text_score(text, config)
    flags = page_flags(raw, text, config)
    reason = ocr_reason(raw, config)
    page_number = raw.page_index + 1
    if reason is not None:
        if raw.ocr is not None:
            reading = image_needs_reading(raw, config)
            return assemble_ocr_page(raw, raw.ocr, document_id, score, config, image_needs_reading=reading)
        return failed_page(document_id, page_number, "OCR_ENGINE_MISSING", score=score, flags=flags, raw=raw)
    tier_flags = PageFlags(has_table=flags.has_table, has_formula=flags.has_formula)
    decision = select_tier(score, tier_flags, config, layout_available=False)
    pieces = [(chunk, line) for line in raw.lines for chunk in _chunks(line.text)]
    lines = [
        ExtractedLine(
            line_index=index,
            text=chunk,
            bbox=line.bbox,
            conf=score,
            engine="text_layer",
            flags=_text_layer_flags(line),
            is_header_footer=line.header_footer,
        )
        for index, (chunk, line) in enumerate(pieces)
    ]
    reasons: list[ReviewReason] = [*decision.review_reasons, *line_flag_reasons(lines)]
    if flags.has_figure and denoise_unavailable(raw):
        reasons.append("DENOISE_UNAVAILABLE")
    return PageExtraction.model_validate(
        {
            "document_id": document_id,
            "page_number": page_number,
            "status": "needs_review" if reasons else "ok",
            "tier_used": "text_layer",
            "text_score": score,
            "has_table": flags.has_table,
            "has_formula": flags.has_formula,
            "has_figure": flags.has_figure,
            "lines": lines,
            "cross_check": NOT_APPLICABLE,
            "review_reasons": reasons,
            **_denoise_fields(raw),
        }
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
    raw: RawPdfPage,
    ocr: RawOcrPage,
    document_id: str,
    score: float,
    config: PdfReaderConfig,
    *,
    image_needs_reading: bool = False,
) -> PageExtraction:
    """Dựng trang OCR (tầng 2): văn bản lượt chính, đối chiếu token với lượt thứ hai, lý do xem lại theo §5.7.

    `image_needs_reading` (GĐ0 0C): trang thoả điều kiện (e), dù OCR vì luật nào (spec §5.7) ⇒ thêm `IMAGE_NEEDS_READING`. Trang OCR
    giữ nguyên luật tầng 2, chỉ thêm các field GĐ0 của worker (`own_images`…).
    """
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
    if image_needs_reading:
        reasons.append("IMAGE_NEEDS_READING")
    return PageExtraction.model_validate(
        {
            "document_id": document_id,
            "page_number": raw.page_index + 1,
            "status": "needs_review" if reasons else "ok",
            "tier_used": "ocr",
            "text_score": score,
            "ocr_score": page_ocr_score,
            "coverage_ratio": coverage,
            "ink_outside_text_ratio": ocr.ink_outside_text_ratio,
            "has_table": flags_of_page.has_table,
            "has_formula": flags_of_page.has_formula,
            "has_figure": flags_of_page.has_figure,
            "lines": lines,
            "cross_check": cross_check,
            "review_reasons": reasons,
            **_denoise_fields(raw),
        }
    )
