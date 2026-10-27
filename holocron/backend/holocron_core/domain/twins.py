"""Model dùng chung của luồng sinh twin (SPECIFICATION §5.6, §5.11, §6.3).

Đặt ở `domain/` để mọi tầng (kể cả repository ở `infrastructure/`) dùng được mà không vi phạm phân tầng.
Logic sinh twin nằm ở `services/pipeline.py` và `services/twin_batch.py`; module này chỉ chứa dữ liệu.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from .schemas import QuestionFormat, TemplateProvenance, TwinQuestion

SEED_SPACE = 2**63


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
    topic_key: str
    reason: str
    attempts: list[AttemptLog]


class TwinBatchRequest(BaseModel):
    count: int = Field(default=3, ge=1, le=10)
    format: QuestionFormat = QuestionFormat.MCQ4
    base_seed: int | None = Field(default=None, ge=0, lt=SEED_SPACE)


class TwinBatch(BaseModel):
    blueprint_id: str
    base_seed: int
    twins: list[TwinQuestion]
    review_tickets: list[ReviewTicket]


class IdempotencyRecord(BaseModel):
    body_digest: str
    stored_at: datetime
    batch: TwinBatch
