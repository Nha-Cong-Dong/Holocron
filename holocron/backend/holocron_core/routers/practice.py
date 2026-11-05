"""Lượt làm bài và câu sai để ôn (SPECIFICATION §5.17, §6.12; ADR-004 PRAC-001).

Mọi route chạy offline, không LLM, không sandbox. Đúng/sai do code so `chosen_key` với `correct_key` của twin đã lưu
(I1); request không mang kết quả (model `extra="forbid"`). Câu ôn trả nguyên bản chụp twin đã lưu (I2, I3).
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response

from ..domain.practice import (
    PracticeAnswer,
    PracticeAttempt,
    PracticeAttemptCreate,
    PracticeAttemptPage,
    ReviewQuestionPage,
    build_attempt,
    grade_answer,
    new_attempt_id,
    twin_correct_key,
)
from ..domain.repositories import MissingReferenceError
from ..domain.vault import SUBJECT_CODE_PATTERN
from .deps import ClockDep, StoreDep

router = APIRouter()
_LOGGER = logging.getLogger(__name__)

BATCH_NOT_FOUND = "không tìm thấy batch"
ATTEMPT_NOT_FOUND = "không tìm thấy lượt làm bài"
FORMAT_NOT_RECORDABLE = "hình thức câu này chưa ghi lượt làm bài được"
MAX_PAGE_LIMIT = 200
DEFAULT_PAGE_LIMIT = 50

SubjectCodeQuery = Annotated[str, Query(pattern=SUBJECT_CODE_PATTERN)]
OffsetQuery = Annotated[int, Query(ge=0)]
LimitQuery = Annotated[int, Query(ge=1, le=MAX_PAGE_LIMIT)]


def _positions(indexes: list[int]) -> str:
    return ", ".join(f"answers[{index}]" for index in indexes)


@router.post("/v1/practice/attempts", response_model=PracticeAttempt, status_code=201)
def create_attempt(
    store: StoreDep, clock: ClockDep, body: PracticeAttemptCreate, response: Response
) -> PracticeAttempt:
    """Ghi một lượt theo thứ tự lỗi của §6.12; bước (1) (body sai) do FastAPI trả 422.

    Bước (6) "chosen_key không thuộc khoá options" không có nhánh riêng: `chosen_key` là `OptionKey` (A–D) và mọi twin
    MCQ4 đã lưu có đủ bốn phương án A–D (validator của `TwinQuestion`, `TheoryQuestion`).
    """
    blueprint_id: str | None = None
    if body.batch_id is not None:  # (2) — validator bảo đảm batch_id có ⇔ mode = "batch"
        blueprint_id = store.get_batch_blueprint_id(body.batch_id)
        if blueprint_id is None:
            raise HTTPException(status_code=404, detail=BATCH_NOT_FOUND)
    twins = store.get_twins([answer.twin_id for answer in body.answers])
    missing = [index for index, answer in enumerate(body.answers) if answer.twin_id not in twins]
    if missing:  # (3)
        raise HTTPException(status_code=404, detail=f"không tìm thấy twin ở {_positions(missing)}")
    stored = [twins[answer.twin_id] for answer in body.answers]
    if body.batch_id is not None:  # (4)
        outside = [index for index, twin in enumerate(stored) if twin.batch_id != body.batch_id]
        if outside:
            raise HTTPException(
                status_code=422, detail=f"twin không thuộc batch {body.batch_id}: {_positions(outside)}"
            )
    answers: list[PracticeAnswer] = []
    for index, (answer, twin) in enumerate(zip(body.answers, stored, strict=True)):
        correct_key = twin_correct_key(twin.twin)
        if correct_key is None:  # (5) twin không phải MCQ4 (NUMERICAL) thì không có correct_key
            raise HTTPException(status_code=422, detail=f"{FORMAT_NOT_RECORDABLE}: {_positions([index])}")
        answers.append(
            PracticeAnswer(
                ordinal=index,
                twin_id=answer.twin_id,
                batch_id=twin.batch_id,
                slot_index=twin.twin.slot_index,
                chosen_key=answer.chosen_key,
                correct_key=correct_key,
                outcome=grade_answer(answer.chosen_key, correct_key),
            )
        )
    subjects = sorted({twin.subject_code for twin in stored})
    if len(subjects) != 1:  # (7)
        raise HTTPException(
            status_code=422, detail=f"các câu của một lượt phải cùng môn; đang có {', '.join(subjects)}"
        )
    attempt = build_attempt(
        attempt_id=new_attempt_id(),
        subject_code=subjects[0],
        mode=body.mode,
        batch_id=body.batch_id,
        blueprint_id=blueprint_id,
        submitted_at=clock(),
        answers=answers,
    )
    try:  # (8)
        store.save_practice_attempt(attempt)
    except MissingReferenceError as exc:  # batch/twin bị xoá giữa lúc tra và lúc ghi
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    _LOGGER.info(
        "ghi lượt làm bài %s mode=%s total=%d correct=%d incorrect=%d skipped=%d",
        attempt.attempt_id,
        attempt.mode,
        attempt.total_count,
        attempt.correct_count,
        attempt.incorrect_count,
        attempt.skipped_count,
    )
    response.headers["Location"] = f"/v1/practice/attempts/{attempt.attempt_id}"
    return attempt


@router.get("/v1/practice/attempts", response_model=PracticeAttemptPage)
def list_attempts(
    store: StoreDep,
    subject_code: SubjectCodeQuery,
    offset: OffsetQuery = 0,
    limit: LimitQuery = DEFAULT_PAGE_LIMIT,
) -> PracticeAttemptPage:
    return store.list_practice_attempts(subject_code, offset, limit)


@router.get("/v1/practice/attempts/{attempt_id}", response_model=PracticeAttempt)
def get_attempt(store: StoreDep, attempt_id: str) -> PracticeAttempt:
    attempt = store.get_practice_attempt(attempt_id)
    if attempt is None:
        raise HTTPException(status_code=404, detail=ATTEMPT_NOT_FOUND)
    return attempt


@router.get("/v1/practice/review-questions", response_model=ReviewQuestionPage)
def list_review_questions(
    store: StoreDep,
    subject_code: SubjectCodeQuery,
    offset: OffsetQuery = 0,
    limit: LimitQuery = DEFAULT_PAGE_LIMIT,
) -> ReviewQuestionPage:
    return store.list_review_questions(subject_code, offset, limit)


@router.delete("/v1/practice/attempts/{attempt_id}", status_code=204)
def delete_attempt(store: StoreDep, attempt_id: str) -> Response:
    if not store.delete_practice_attempt(attempt_id):
        raise HTTPException(status_code=404, detail=ATTEMPT_NOT_FOUND)
    return Response(status_code=204)


@router.delete("/v1/practice/attempts", status_code=204)
def delete_subject_attempts(store: StoreDep, subject_code: SubjectCodeQuery) -> Response:
    """Xoá mọi lượt của môn (môn chưa có lượt nào cũng 204); không đụng twin, batch hay Vault."""
    store.delete_practice_attempts(subject_code)
    return Response(status_code=204)
