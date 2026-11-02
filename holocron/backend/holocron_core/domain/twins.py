"""Model dùng chung của luồng sinh twin (SPECIFICATION §5.6, §5.11, §6.3).

Đặt ở `domain/` để mọi tầng (kể cả repository ở `infrastructure/`) dùng được mà không vi phạm phân tầng.
Logic sinh twin nằm ở `services/pipeline.py` và `services/twin_batch.py`; module này chỉ chứa dữ liệu.

Câu lý thuyết vào `TwinBatch` theo quyết định 14(b) ngày 2026-10-28: `TwinBatch.twins` là union phân biệt theo
`question_kind` giữa `TwinQuestion` (câu tính toán, JSON không có `question_kind`) và `TheoryTwin` (`question_kind =
"theory"`, câu nằm trong `question`). Đây là nợ ADR-004 so với §5.11 (một `TwinQuestion` đa hình, `TheoryTrace`), chuyển
sang phương án (a) sau 29/10.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Discriminator, Field, Tag, field_validator, model_validator

from .schemas import (
    COMPUTATIONAL_FORMATS,
    SEED_SPACE,
    QuestionFormat,
    ReviewStatus,
    StrictModel,
    TemplateProvenance,
    TwinQuestion,
    _new_id,
    _utcnow,
)
from .theory import TheoryQuestion

__all__ = [
    "BATCH_ID_PATTERN",
    "SEED_SPACE",
    "AttemptLog",
    "BatchTwin",
    "IdempotencyRecord",
    "ReviewTicket",
    "TheoryTwin",
    "TwinBatch",
    "TwinBatchRequest",
]

BATCH_ID_PATTERN = r"^tb_[0-9a-f]{16}$"


def _new_batch_id() -> str:
    return _new_id("tb")


class AttemptLog(BaseModel):
    attempt: int
    provenance: TemplateProvenance
    template_sha256: str | None
    outcome: str
    seeds_tried: int
    assertion_histogram: dict[str, int] = Field(default_factory=dict)
    feedback_sent: list[str] = Field(default_factory=list)


class ReviewTicket(BaseModel):
    blueprint_id: str
    slot_index: int
    topic_key: str | None  # null với slot lý thuyết (§5.9, duyệt 2026-10-26)
    reason: str
    attempts: list[AttemptLog]


class TwinBatchRequest(BaseModel):
    count: int = Field(default=3, ge=1, le=10)
    format: QuestionFormat = QuestionFormat.MCQ4  # chỉ ghi đè slot tính toán (§6.3)
    base_seed: int | None = Field(default=None, ge=0, lt=SEED_SPACE)

    @field_validator("format")
    @classmethod
    def _computational_format(cls, value: QuestionFormat) -> QuestionFormat:
        if value not in COMPUTATIONAL_FORMATS:
            raise ValueError("format của request chỉ nhận MCQ4 hoặc NUMERICAL")
        return value


class TheoryTwin(StrictModel):
    """Một câu lý thuyết MCQ4 trong `TwinBatch` (ADR-007; quyết định 14(b)).

    `generator_version`, `pool_sha256` và `seed` (bộ ba I4) nằm trong `question`; không có `TheoryTrace` (nợ ADR-004).
    `twin_id`, `review_status`, `generated_at` cùng kiểu và cách sinh như `TwinQuestion`.
    """

    question_kind: Literal["theory"] = "theory"
    twin_id: str = Field(default_factory=lambda: _new_id("twin"))
    blueprint_id: str
    slot_index: int = Field(ge=1)
    format: Literal["MCQ4"] = "MCQ4"
    question: TheoryQuestion
    review_status: ReviewStatus
    generated_at: datetime = Field(default_factory=_utcnow)


def _twin_kind(value: Any) -> str:
    """Khoá phân biệt của `TwinBatch.twins`: `question_kind == "theory"` ⇒ `"theory"`, còn lại `"computational"`.

    Nhận cả dict lẫn object; câu tính toán (`TwinQuestion`) không có `question_kind`.
    """
    kind = value.get("question_kind") if isinstance(value, dict) else getattr(value, "question_kind", None)
    return "theory" if kind == "theory" else "computational"


BatchTwin = Annotated[
    Annotated[TwinQuestion, Tag("computational")] | Annotated[TheoryTwin, Tag("theory")],
    Discriminator(_twin_kind),
]


class TwinBatch(BaseModel):
    batch_id: str = Field(default_factory=_new_batch_id, pattern=BATCH_ID_PATTERN)
    blueprint_id: str
    base_seed: int
    twins: list[BatchTwin]
    review_tickets: list[ReviewTicket]
    created_at: datetime = Field(default_factory=_utcnow)


class IdempotencyRecord(BaseModel):
    """Batch đã lưu kèm khoá idempotency của nó; `stored_at` chính là `batch.created_at`.

    Kho SQLite chỉ có một cột thời gian cho batch (`twin_batches.created_at`, §5.13), nên hai giá trị được giữ trùng
    nhau: dựng bản ghi với `stored_at` khác `batch.created_at` thì `batch.created_at` lấy theo `stored_at`.
    """

    body_digest: str
    stored_at: datetime
    batch: TwinBatch

    @model_validator(mode="after")
    def _batch_created_at_is_stored_at(self) -> IdempotencyRecord:
        if self.batch.created_at != self.stored_at:
            self.batch = self.batch.model_copy(update={"created_at": self.stored_at})
        return self
