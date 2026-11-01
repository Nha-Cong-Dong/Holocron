"""Luật tự xác nhận cấp trang (SPECIFICATION §5.7 "Tự xác nhận cấp trang", GĐ1 1B): hàm thuần `is_trusted_source`
(`source_trust_v1`) và `passes_page_clean` (`page_clean_v1`), cùng validator của `PageExtraction`.

Không có kho, mạng hay file: trang dựng thẳng bằng model. Văn bản là mẫu tự viết.
"""

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from holocron_core.domain.documents import (
    PageExtraction,
    PageSummary,
    count_auto_confirmed,
    count_kept_unconfirmed,
)
from holocron_core.domain.schemas import SourceKind
from holocron_core.services import auto_confirm
from holocron_core.services.auto_confirm import (
    AUTO_CONFIRM_RULE,
    SOURCE_TRUST_RULE,
    is_trusted_source,
    passes_page_clean,
)
from tests.vault_support import NOW

DOC = "doc_" + "a" * 16
EVIDENCE = {"rule": "build_step", "related_pages": [{"document_id": DOC, "page_number": 2}]}
SERIES_EVIDENCE = {"rule": "parallel_visual_series", "related_pages": [{"document_id": DOC, "page_number": 2}]}


def make_page(**overrides: Any) -> PageExtraction:
    """Trang lớp chữ sạch (qua `source_trust_v1`, đủ điều kiện `page_clean_v1`); `overrides` ghi đè field."""
    data: dict[str, Any] = {
        "document_id": DOC,
        "page_number": 1,
        "status": "ok",
        "tier_used": "text_layer",
        "text_score": 0.95,
        "lines": [
            {
                "line_index": 0,
                "text": "Nội dung mẫu tự viết",
                "bbox": [0.1, 0.1, 0.9, 0.15],
                "conf": 0.95,
                "engine": "text_layer",
            }
        ],
        "cross_check": {"status": "not_applicable", "secondary_engine": None},
    }
    data.update(overrides)
    return PageExtraction.model_validate(data)


def clean(page: PageExtraction, **overrides: Any) -> bool:
    arguments: dict[str, Any] = {
        "auto_confirm": True,
        "source_kind": SourceKind.LECTURE_SLIDES,
        "has_text": True,
        "fits_vault": True,
    }
    arguments.update(overrides)
    return passes_page_clean(page, **arguments)


def test_rule_names_are_defined_in_exactly_one_place() -> None:
    assert (AUTO_CONFIRM_RULE, SOURCE_TRUST_RULE) == ("page_clean_v1", "source_trust_v1")
    root = Path(auto_confirm.__file__).resolve().parent.parent
    holders = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*.py")
        if '"page_clean_v1"' in path.read_text(encoding="utf-8")
    }
    assert holders == {"services/auto_confirm.py"}


# --- source_trust_v1: bảng chân trị -------------------------------------------------------------------------------


def _mismatch(**extra: Any) -> dict[str, Any]:
    line = {
        "line_index": 0,
        "text": "MOV R0, #0x1E",
        "bbox": [0.1, 0.1, 0.9, 0.15],
        "conf": 0.9,
        "engine": "tesseract",
        "flags": ["TOKEN_MISMATCH"],
    }
    return {
        "status": "needs_review",
        "review_reasons": ["TOKEN_MISMATCH"],
        "lines": [line],
        "cross_check": {
            "status": "mismatch",
            "secondary_engine": "rapidocr",
            "mismatches": [{"line_index": 0, "primary_value": "#0x1E", "secondary_value": "#0x1F"}],
        },
        **extra,
    }


def _ocr(cross_status: str, **extra: Any) -> dict[str, Any]:
    secondary = "rapidocr" if cross_status == "ok" else None
    return {
        "tier_used": "ocr",
        "ocr_score": 0.9,
        "cross_check": {"status": cross_status, "secondary_engine": secondary},
        **extra,
    }


TRUTH_TABLE: list[tuple[str, dict[str, Any], bool]] = [
    ("text_layer, không scan", {}, True),
    ("text_layer, lớp chữ của PDF scan", {"scan_text_layer": True}, False),
    ("ocr, đối chiếu ok", _ocr("ok"), True),
    ("ocr, đối chiếu unavailable", _ocr("unavailable"), False),
    ("ocr, đối chiếu not_applicable", _ocr("not_applicable"), False),
    ("ocr, đối chiếu mismatch", {"tier_used": "ocr", "ocr_score": 0.9, **_mismatch()}, False),
    ("ocr, đối chiếu ok nhưng lớp chữ scan", _ocr("ok", scan_text_layer=True), True),
    ("vlm", {"tier_used": "vlm"}, False),
    (
        "vlm dù đối chiếu ok",
        {"tier_used": "vlm", "cross_check": {"status": "ok", "secondary_engine": "rapidocr"}},
        False,
    ),
    ("layout", {"tier_used": "layout"}, False),
    (
        "layout dù đối chiếu ok",
        {"tier_used": "layout", "cross_check": {"status": "ok", "secondary_engine": "rapidocr"}},
        False,
    ),
    ("pending", {"status": "pending", "tier_used": None, "text_score": None, "lines": []}, False),
    (
        "failed",
        {
            "status": "failed",
            "tier_used": None,
            "failure_reason": "PAGE_ERROR",
            "text_score": None,
            "lines": [],
        },
        False,
    ),
]


@pytest.mark.parametrize(("name", "overrides", "trusted"), TRUTH_TABLE, ids=[row[0] for row in TRUTH_TABLE])
def test_source_trust_v1_truth_table(name: str, overrides: dict[str, Any], trusted: bool) -> None:
    assert is_trusted_source(make_page(**overrides)) is trusted, name


# --- page_clean_v1: từng điều kiện --------------------------------------------------------------------------------


def test_page_clean_accepts_a_clean_trusted_page() -> None:
    assert clean(make_page()) is True
    assert clean(make_page(**_ocr("ok"))) is True, "trang OCR có đối chiếu ok cũng qua"


def test_page_clean_requires_the_document_setting() -> None:
    assert clean(make_page(), auto_confirm=False) is False


def test_page_clean_never_applies_to_past_exam() -> None:
    assert clean(make_page(), source_kind=SourceKind.PAST_EXAM) is False
    for kind in SourceKind:
        assert clean(make_page(), source_kind=kind) is (kind != SourceKind.PAST_EXAM)


def test_page_clean_requires_a_trusted_source() -> None:
    assert clean(make_page(scan_text_layer=True)) is False
    assert clean(make_page(**_ocr("unavailable"))) is False
    assert clean(make_page(tier_used="vlm")) is False


def test_page_clean_rejects_any_review_reason() -> None:
    assert clean(make_page(status="needs_review", review_reasons=["IMAGE_NEEDS_READING"])) is False


def test_page_clean_rejects_non_content_roles_and_soft_dropped_pages() -> None:
    assert clean(make_page(page_role="exercise")) is False
    assert clean(make_page(page_role="parallel_visual_series", role_evidence=SERIES_EVIDENCE)) is False
    assert clean(make_page(page_role="build_step", role_evidence=EVIDENCE, dropped=True)) is False
    assert clean(make_page(page_role="build_step", role_evidence=EVIDENCE)) is False


def test_page_clean_rejects_confirmed_and_undone_pages() -> None:
    assert clean(make_page(confirmed=True, confirmed_at=NOW, confirmed_by="user")) is False
    assert clean(make_page(auto_confirm_undone=True)) is False


def test_page_clean_requires_text_and_vault_fit() -> None:
    assert clean(make_page(), has_text=False) is False
    assert clean(make_page(), fits_vault=False) is False


# --- validator của PageExtraction (§5.7) --------------------------------------------------------------------------


def test_validator_accepts_the_confirmation_states() -> None:
    assert make_page().confirmed_by is None
    legacy = make_page(confirmed=True, confirmed_at=NOW)
    assert legacy.confirmed_by is None, "trang xác nhận trước GĐ1: confirmed_by null (coi như user)"
    user = make_page(confirmed=True, confirmed_at=NOW, confirmed_by="user")
    assert user.confirm_rule is None
    auto = make_page(confirmed=True, confirmed_at=NOW, confirmed_by="auto", confirm_rule="page_clean_v1")
    assert (auto.confirmed_by, auto.confirm_rule) == ("auto", "page_clean_v1")
    assert make_page(auto_confirm_undone=True).auto_confirm_undone is True
    assert make_page(confirmed=True, confirmed_at=NOW, confirmed_by="user", auto_confirm_undone=True)


AUTO = {"confirmed": True, "confirmed_at": NOW, "confirmed_by": "auto", "confirm_rule": "page_clean_v1"}

REJECTED: list[tuple[str, dict[str, Any]]] = [
    ("confirmed = false mà confirmed_by có giá trị", {"confirmed_by": "user"}),
    ("confirmed = false mà confirmed_by = auto", {"confirmed_by": "auto", "confirm_rule": "page_clean_v1"}),
    ("auto thiếu confirm_rule", {**AUTO, "confirm_rule": None}),
    ("confirm_rule mà confirmed_by không phải auto", {"confirmed": True, "confirm_rule": "page_clean_v1"}),
    ("confirm_rule của user", {"confirmed": True, "confirmed_by": "user", "confirm_rule": "page_clean_v1"}),
    ("confirm_rule sai mẫu", {**AUTO, "confirm_rule": "PageClean"}),
    (
        "auto mà còn review_reasons",
        {**AUTO, "status": "needs_review", "review_reasons": ["IMAGE_NEEDS_READING"]},
    ),
    ("auto mà page_role khác content", {**AUTO, "page_role": "exercise"}),
    (
        "auto mà page_role có bằng chứng",
        {**AUTO, "page_role": "parallel_visual_series", "role_evidence": SERIES_EVIDENCE},
    ),
    ("auto mà đã hoàn tác", {**AUTO, "auto_confirm_undone": True}),
]


@pytest.mark.parametrize(("name", "overrides"), REJECTED, ids=[row[0] for row in REJECTED])
def test_validator_rejects_inconsistent_confirmation(name: str, overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        make_page(**overrides)


def test_validator_rejects_auto_confirmed_dropped_page() -> None:
    with pytest.raises(ValidationError):
        make_page(**AUTO, page_role="build_step", role_evidence=EVIDENCE, dropped=True)


def test_summary_counts_come_from_the_pages() -> None:
    auto = make_page(page_number=1, **AUTO)
    user = make_page(page_number=2, confirmed=True, confirmed_at=NOW, confirmed_by="user")
    open_ok = make_page(page_number=3)
    review = make_page(page_number=4, status="needs_review", review_reasons=["IMAGE_NEEDS_READING"])
    dropped = make_page(page_number=5, page_role="build_step", role_evidence=EVIDENCE, dropped=True)
    summaries = [PageSummary.from_page(page) for page in (auto, user, open_ok, review, dropped)]
    assert [summary.confirmed_by for summary in summaries] == ["auto", "user", None, None, None]
    assert count_auto_confirmed(summaries) == 1
    assert count_kept_unconfirmed(summaries) == 1, "chỉ trang ok, chưa xác nhận, không bỏ mềm"
