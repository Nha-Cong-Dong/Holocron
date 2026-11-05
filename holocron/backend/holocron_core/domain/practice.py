"""Lượt làm bài và câu sai để ôn (SPECIFICATION §5.17, §6.12; ADR-004 PRAC-001).

Đúng/sai và mọi số đếm do code tính từ twin đã lưu (I1): request chỉ mang `twin_id` và `chosen_key`, không mang kết
quả. Câu ôn trả nguyên bản chụp twin đã lưu (đáp án, phương án nhiễu kèm misconception, trích dẫn nguyên văn), nên
I2/I3 giữ nguyên như lúc sinh. Module này chỉ chứa dữ liệu và hàm thuần; lưu trữ ở `PracticeAttemptRepository`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from .schemas import OptionKey, QuestionFormat, StrictModel, TwinQuestion, _new_id
from .twins import BATCH_ID_PATTERN, BatchTwin, TheoryTwin

__all__ = [
    "ATTEMPT_ID_PATTERN",
    "MAX_PRACTICE_ANSWERS",
    "AnswerOutcome",
    "PracticeAnswer",
    "PracticeAnswerIn",
    "PracticeAttempt",
    "PracticeAttemptCreate",
    "PracticeAttemptPage",
    "PracticeAttemptSummary",
    "PracticeMode",
    "ReviewQuestion",
    "ReviewQuestionPage",
    "StoredTwin",
    "build_attempt",
    "grade_answer",
    "new_attempt_id",
    "twin_correct_key",
]

ATTEMPT_ID_PATTERN = r"^pa_[0-9a-f]{16}$"
MAX_PRACTICE_ANSWERS = 500  # 50 slot × count tối đa 10 (§6.7, §5.6)

AnswerOutcome = Literal["correct", "incorrect", "skipped"]
PracticeMode = Literal["batch", "review"]


def new_attempt_id() -> str:
    return _new_id("pa")


class PracticeAnswerIn(StrictModel):
    """Một câu trong request ghi lượt; KHÔNG có field đúng/sai, điểm hay `correct_key` (I1)."""

    twin_id: str
    chosen_key: OptionKey | None = None  # null = bỏ qua câu


class PracticeAttemptCreate(StrictModel):
    """Body của `POST /v1/practice/attempts` (§6.12)."""

    mode: PracticeMode
    batch_id: str | None = Field(default=None, pattern=BATCH_ID_PATTERN)
    answers: list[PracticeAnswerIn] = Field(min_length=1, max_length=MAX_PRACTICE_ANSWERS)

    @model_validator(mode="after")
    def _batch_matches_mode(self) -> PracticeAttemptCreate:
        if self.mode == "batch" and self.batch_id is None:
            raise ValueError("mode = batch cần batch_id")
        if self.mode == "review" and self.batch_id is not None:
            raise ValueError("mode = review không nhận batch_id")
        seen: set[str] = set()
        for answer in self.answers:
            if answer.twin_id in seen:
                raise ValueError(f"twin_id trùng trong answers: {answer.twin_id}")
            seen.add(answer.twin_id)
        return self


def grade_answer(chosen_key: OptionKey | None, correct_key: OptionKey) -> AnswerOutcome:
    """Kết quả một câu (§5.17): null ⇒ `skipped`; trùng `correct_key` ⇒ `correct`; còn lại ⇒ `incorrect`."""
    if chosen_key is None:
        return "skipped"
    return "correct" if chosen_key == correct_key else "incorrect"


class PracticeAnswer(StrictModel):
    """Một câu đã ghi; mọi field trừ `twin_id`, `chosen_key` do code điền từ twin đã lưu."""

    ordinal: int = Field(ge=0)
    twin_id: str
    batch_id: str
    slot_index: int
    chosen_key: OptionKey | None
    correct_key: OptionKey
    outcome: AnswerOutcome

    @model_validator(mode="after")
    def _outcome_is_computed(self) -> PracticeAnswer:
        if self.outcome != grade_answer(self.chosen_key, self.correct_key):
            raise ValueError("outcome phải khớp chosen_key và correct_key (I1)")
        return self


class PracticeAttemptSummary(StrictModel):
    attempt_id: str = Field(pattern=ATTEMPT_ID_PATTERN)
    subject_code: str
    mode: PracticeMode
    batch_id: str | None
    blueprint_id: str | None
    submitted_at: datetime
    total_count: int = Field(ge=0)
    correct_count: int = Field(ge=0)
    incorrect_count: int = Field(ge=0)
    skipped_count: int = Field(ge=0)

    @model_validator(mode="after")
    def _counts_add_up(self) -> PracticeAttemptSummary:
        if self.correct_count + self.incorrect_count + self.skipped_count != self.total_count:
            raise ValueError("correct_count + incorrect_count + skipped_count phải bằng total_count")
        return self


class PracticeAttempt(PracticeAttemptSummary):
    answers: list[PracticeAnswer]

    @model_validator(mode="after")
    def _counts_match_answers(self) -> PracticeAttempt:
        ordinals = [answer.ordinal for answer in self.answers]
        if ordinals != sorted(ordinals):
            raise ValueError("answers phải sắp theo ordinal")
        outcomes = [answer.outcome for answer in self.answers]
        if (self.total_count, self.correct_count, self.incorrect_count, self.skipped_count) != (
            len(outcomes),
            outcomes.count("correct"),
            outcomes.count("incorrect"),
            outcomes.count("skipped"),
        ):
            raise ValueError("số đếm của lượt phải khớp các câu đã ghi")
        return self


class PracticeAttemptPage(StrictModel):
    total: int = Field(ge=0)
    attempts: list[PracticeAttemptSummary]


class ReviewQuestion(StrictModel):
    twin: BatchTwin
    batch_id: str
    last_attempt_id: str
    last_answered_at: datetime
    last_chosen_key: OptionKey


class ReviewQuestionPage(StrictModel):
    total: int = Field(ge=0)
    questions: list[ReviewQuestion]


@dataclass(frozen=True)
class StoredTwin:
    """Twin đã lưu kèm batch, blueprint và môn chứa nó (tra theo `twin_id`, §5.17); không ra response."""

    twin: TwinQuestion | TheoryTwin
    batch_id: str
    blueprint_id: str
    subject_code: str


def twin_correct_key(twin: TwinQuestion | TheoryTwin) -> OptionKey | None:
    """`correct_key` đã lưu của twin; `None` với câu không phải MCQ4 (NUMERICAL)."""
    if isinstance(twin, TheoryTwin):
        return twin.question.correct_key
    return twin.correct_key if twin.format is QuestionFormat.MCQ4 else None


def build_attempt(
    *,
    attempt_id: str,
    subject_code: str,
    mode: PracticeMode,
    batch_id: str | None,
    blueprint_id: str | None,
    submitted_at: datetime,
    answers: Sequence[PracticeAnswer],
) -> PracticeAttempt:
    """Dựng lượt từ các câu đã ghi; số đếm do code đếm trên `answers` (I1)."""
    ordered = sorted(answers, key=lambda answer: answer.ordinal)
    outcomes = [answer.outcome for answer in ordered]
    return PracticeAttempt(
        attempt_id=attempt_id,
        subject_code=subject_code,
        mode=mode,
        batch_id=batch_id,
        blueprint_id=blueprint_id,
        submitted_at=submitted_at,
        total_count=len(outcomes),
        correct_count=outcomes.count("correct"),
        incorrect_count=outcomes.count("incorrect"),
        skipped_count=outcomes.count("skipped"),
        answers=ordered,
    )
