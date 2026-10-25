"""Model kết quả đọc PDF theo trang (SPECIFICATION §5.7): field, ràng buộc và validator."""

from __future__ import annotations

import unicodedata
from typing import Any, get_args

import pytest
from pydantic import ValidationError

from holocron_core.domain.documents import (
    REVIEW_REASON_ORDER,
    CrossCheck,
    ExtractedLine,
    FailureReason,
    LineEngine,
    LineFlag,
    PageExtraction,
    ReviewReason,
    TierUsed,
    TokenMismatch,
)

DOC_ID = "doc_0123456789abcdef"
NOT_APPLICABLE = CrossCheck(status="not_applicable", secondary_engine=None)


def _line(index: int = 0, **overrides: Any) -> ExtractedLine:
    data: dict[str, Any] = {
        "line_index": index,
        "text": "MOV R0, #0x1F",
        "bbox": (0.1, 0.1, 0.5, 0.12),
        "conf": 0.9,
        "engine": "text_layer",
    }
    data.update(overrides)
    return ExtractedLine(**data)


def _page(**overrides: Any) -> PageExtraction:
    data: dict[str, Any] = {
        "document_id": DOC_ID,
        "page_number": 1,
        "status": "ok",
        "tier_used": "text_layer",
        "text_score": 0.95,
        "lines": [_line(0), _line(1)],
        "cross_check": NOT_APPLICABLE,
    }
    data.update(overrides)
    return PageExtraction(**data)


def test_literals_match_spec() -> None:
    assert set(get_args(LineEngine)) == {"text_layer", "tesseract", "vlm", "human"}
    assert get_args(TierUsed) == ("text_layer", "ocr", "layout", "vlm")
    assert get_args(LineFlag) == ("LOW_LINE_CONF", "TOKEN_MISMATCH", "HIGH_LINE_CER")
    assert get_args(FailureReason) == ("PAGE_TIMEOUT", "PAGE_ERROR", "OCR_ENGINE_MISSING")
    assert REVIEW_REASON_ORDER == get_args(ReviewReason)
    assert REVIEW_REASON_ORDER == (
        "LOW_OCR_SCORE",
        "LOW_LINE_COVERAGE",
        "HIGH_INK_OUTSIDE_TEXT",
        "FIGURE_ON_SCAN",
        "FORMULA_ON_SCAN",
        "TIER3_UNAVAILABLE",
        "LOW_CONF_LINES",
        "TOKEN_MISMATCH",
        "CROSS_CHECK_UNAVAILABLE",
        "HIGH_PAGE_CER",
        "OCR_ENGINE_MISSING",
    )


def test_page_defaults_follow_spec() -> None:
    page = _page()
    assert page.version == 1
    assert page.ocr_score is None and page.coverage_ratio is None and page.ink_outside_text_ratio is None
    assert (page.has_figure, page.has_table, page.has_formula) == (False, False, False)
    assert page.tables == [] and page.review_reasons == [] and page.failure_reason is None
    assert page.confirmed is False and page.confirmed_at is None
    assert page.lines[0].flags == [] and page.lines[0].edited_by_human is False


def test_line_text_is_nfc_normalized() -> None:
    nfd = unicodedata.normalize("NFD", "Hệ điều hành")
    line = _line(text=nfd)
    assert line.text == unicodedata.normalize("NFC", "Hệ điều hành")
    assert unicodedata.is_normalized("NFC", line.text)


def test_line_text_length_limit() -> None:
    assert len(_line(text="a" * 2000).text) == 2000
    with pytest.raises(ValidationError):
        _line(text="a" * 2001)


@pytest.mark.parametrize(
    "bbox",
    [(-0.1, 0.0, 0.5, 0.5), (0.0, 0.0, 1.2, 0.5), (0.6, 0.0, 0.5, 0.5), (0.0, 0.6, 0.5, 0.5)],
)
def test_line_bbox_must_be_ordered_inside_unit_square(bbox: tuple[float, float, float, float]) -> None:
    with pytest.raises(ValidationError):
        _line(bbox=bbox)


def test_line_bbox_may_be_null_and_conf_is_bounded() -> None:
    assert _line(bbox=None, engine="human", conf=1.0).bbox is None
    for conf in (-0.01, 1.01):
        with pytest.raises(ValidationError):
            _line(conf=conf)
    with pytest.raises(ValidationError):
        _line(engine="rapidocr")


def test_cross_check_consistency() -> None:
    mismatch = TokenMismatch(line_index=0, primary_value="0x1F", secondary_value="0x1E")
    assert CrossCheck(status="mismatch", secondary_engine="rapidocr", mismatches=[mismatch]).mismatches
    assert CrossCheck(status="ok", secondary_engine="rapidocr").mismatches == []
    with pytest.raises(ValidationError):
        CrossCheck(status="mismatch", secondary_engine="rapidocr")
    with pytest.raises(ValidationError):
        CrossCheck(status="ok", secondary_engine="rapidocr", mismatches=[mismatch])
    for status in ("unavailable", "not_applicable"):
        with pytest.raises(ValidationError):
            CrossCheck(status=status, secondary_engine="rapidocr")
    with pytest.raises(ValidationError):
        CrossCheck(status="ok", secondary_engine="tesseract")


def test_needs_review_iff_review_reasons() -> None:
    page = _page(status="needs_review", review_reasons=["TIER3_UNAVAILABLE"])
    assert page.review_reasons == ["TIER3_UNAVAILABLE"]
    with pytest.raises(ValidationError):
        _page(status="needs_review")
    with pytest.raises(ValidationError):
        _page(status="ok", review_reasons=["TIER3_UNAVAILABLE"])


def test_review_reasons_sorted_in_declaration_order_without_duplicates() -> None:
    page = _page(
        status="needs_review",
        review_reasons=["OCR_ENGINE_MISSING", "LOW_OCR_SCORE", "TIER3_UNAVAILABLE", "LOW_OCR_SCORE"],
    )
    assert page.review_reasons == ["LOW_OCR_SCORE", "TIER3_UNAVAILABLE", "OCR_ENGINE_MISSING"]
    with pytest.raises(ValidationError):
        _page(status="needs_review", review_reasons=["NOT_A_REASON"])


def test_failure_reason_iff_failed() -> None:
    page = _page(status="failed", tier_used=None, lines=[], failure_reason="PAGE_TIMEOUT")
    assert page.failure_reason == "PAGE_TIMEOUT"
    with pytest.raises(ValidationError):
        _page(status="failed", tier_used=None, lines=[])
    with pytest.raises(ValidationError):
        _page(failure_reason="PAGE_ERROR")


def test_failed_page_may_keep_review_reasons_but_not_a_tier() -> None:
    page = _page(status="failed", tier_used=None, failure_reason="PAGE_ERROR", review_reasons=["OCR_ENGINE_MISSING"])
    assert page.status == "failed"
    with pytest.raises(ValidationError):
        _page(status="failed", tier_used="text_layer", failure_reason="PAGE_ERROR")


def test_pending_page_has_no_tier_and_no_score() -> None:
    page = _page(status="pending", tier_used=None, text_score=None, lines=[])
    assert page.status == "pending"
    with pytest.raises(ValidationError):
        _page(status="pending", tier_used=None, text_score=0.5, lines=[])
    with pytest.raises(ValidationError):
        _page(status="pending", tier_used="text_layer", text_score=None, lines=[])


def test_line_indexes_must_be_contiguous() -> None:
    with pytest.raises(ValidationError):
        _page(lines=[_line(0), _line(2)])
    with pytest.raises(ValidationError):
        _page(lines=[_line(1)])


def test_token_mismatch_must_point_to_flagged_line() -> None:
    mismatch = TokenMismatch(line_index=1, primary_value="12", secondary_value=None)
    check = CrossCheck(status="mismatch", secondary_engine="rapidocr", mismatches=[mismatch])
    flagged = [_line(0, engine="tesseract"), _line(1, engine="tesseract", flags=["TOKEN_MISMATCH"])]
    page = _page(
        tier_used="ocr",
        status="needs_review",
        review_reasons=["TOKEN_MISMATCH"],
        lines=flagged,
        cross_check=check,
    )
    assert page.cross_check.status == "mismatch"
    unflagged = [_line(0, engine="tesseract"), _line(1, engine="tesseract")]
    with pytest.raises(ValidationError):
        _page(
            tier_used="ocr",
            status="needs_review",
            review_reasons=["TOKEN_MISMATCH"],
            lines=unflagged,
            cross_check=check,
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"document_id": "doc_XYZ"},
        {"page_number": 0},
        {"version": 0},
        {"text_score": 1.5},
        {"ocr_score": -0.1},
        {"coverage_ratio": 1.1},
        {"ink_outside_text_ratio": 2.0},
        {"status": "done"},
        {"unknown_field": 1},
    ],
)
def test_page_field_constraints(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        _page(**overrides)


def test_text_score_is_required() -> None:
    data: dict[str, Any] = {
        "document_id": DOC_ID,
        "page_number": 1,
        "status": "ok",
        "tier_used": "text_layer",
        "cross_check": NOT_APPLICABLE.model_dump(),
    }
    with pytest.raises(ValidationError):
        PageExtraction.model_validate(data)
    assert PageExtraction.model_validate({**data, "text_score": None}).text_score is None


def test_page_round_trips_through_json() -> None:
    page = _page(status="needs_review", review_reasons=["TIER3_UNAVAILABLE"], has_table=True)
    assert PageExtraction.model_validate_json(page.model_dump_json()) == page


def test_line_text_too_long_after_nfc_is_rejected() -> None:
    expanding = chr(0x0344) * 1500  # NFC tách mỗi ký tự thành hai: 3000 ký tự
    assert len(unicodedata.normalize("NFC", expanding)) == 3000
    with pytest.raises(ValidationError, match="NFC"):
        _line(text=expanding)
