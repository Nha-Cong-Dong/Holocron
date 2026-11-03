"""Model và số đếm tóm tắt của màn kết quả nhập (T1e; SPECIFICATION §5.7 `DocumentImport`): `count` khớp số trang
theo cả hai chiều, `count >= 1`, và chỉ trang `needs_review` được đếm theo lý do xem lại.

Bổ sung cho `test_document_import_summary.py`; thuần domain, không kho, không mạng.
"""

import pytest
from pydantic import ValidationError

from holocron_core.domain.documents import DroppedRoleCount, PageSummary, ReviewReasonCount, review_reason_counts
from tests.test_document_import_summary import _summary


@pytest.mark.parametrize("model", [ReviewReasonCount, DroppedRoleCount])
def test_count_must_equal_the_number_of_pages_both_ways(model: type[ReviewReasonCount | DroppedRoleCount]) -> None:
    reason = "LOW_OCR_SCORE" if model is ReviewReasonCount else "chapter_nav"
    model.model_validate({"reason": reason, "count": 2, "pages": [1, 2]})
    for count, pages in ((1, [1, 2]), (2, [1]), (3, [1, 2])):
        with pytest.raises(ValidationError, match="count"):
            model.model_validate({"reason": reason, "count": count, "pages": pages})


def test_dropped_role_count_is_at_least_one() -> None:
    with pytest.raises(ValidationError):
        DroppedRoleCount.model_validate({"reason": "chapter_nav", "count": 0, "pages": []})
    with pytest.raises(ValidationError):
        ReviewReasonCount.model_validate({"reason": "LOW_OCR_SCORE", "count": 0, "pages": []})


@pytest.mark.parametrize("status", ["failed", "ok", "pending"])
def test_only_needs_review_pages_are_counted_by_reason(status: str) -> None:
    pages: list[PageSummary] = [
        _summary(1, status=status, tier_used=None, review_reasons=["LOW_OCR_SCORE"]),
        _summary(2, status="needs_review", review_reasons=["LOW_OCR_SCORE"]),
    ]
    assert review_reason_counts(pages) == [ReviewReasonCount(reason="LOW_OCR_SCORE", count=1, pages=[2])]
