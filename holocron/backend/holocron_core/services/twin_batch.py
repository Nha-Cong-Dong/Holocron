"""Sinh bộ Twin Questions cho mọi slot của một blueprint (SPECIFICATION §5.6, §6.3).

Với mỗi slot và mỗi `k ∈ [0, count)` gọi `produce_twin(base_seed = base_seed + k·7919)`. Mọi con số và `gantt` của
twin đến từ sandbox (I1); module này KHÔNG tính gì thêm. Lỗi template/sandbox không làm hỏng batch: slot không sinh
được đi vào `review_tickets` (mỗi slot tối đa một ticket).

Phụ thuộc ngoài đi qua `Protocol` (`TemplateAuthor`, `TemplateRegistry`, `CitationProvider`) để test thay bằng stub.
"""

from __future__ import annotations

import hashlib
from typing import Protocol

from ..domain.schemas import BlueprintSlot, Citation, ExamBlueprint, QuestionFormat, QuestionTemplate, TwinQuestion
from ..domain.twins import SEED_SPACE, ReviewTicket, TwinBatch, TwinBatchRequest
from .pipeline import SEED_STRIDE, TemplateAuthor, TemplateRegistry, produce_twin

# `TwinBatchRequest`, `TwinBatch` và `SEED_SPACE` đã chuyển về `domain/twins.py`; tên cũ vẫn import được từ module này.
__all__ = [
    "SEED_SPACE",
    "CitationProvider",
    "NoCitationProvider",
    "TwinBatch",
    "TwinBatchRequest",
    "UnconfiguredTemplateAuthor",
    "default_base_seed",
    "generate_twin_batch",
]


class CitationProvider(Protocol):
    """Nguồn trích dẫn (Knowledge Vault, §12): trả `Citation` cho slot, hoặc `None` nếu không có chunk phù hợp."""

    def citation_for(self, slot: BlueprintSlot) -> Citation | None: ...


class NoCitationProvider:
    """Mặc định khi chưa có Knowledge Vault: không có trích dẫn ⇒ mọi slot vào `review_tickets`."""

    def citation_for(self, slot: BlueprintSlot) -> Citation | None:
        return None


class UnconfiguredTemplateAuthor:
    """Mặc định khi chưa có adapter LLM (T2-N5): luôn báo lỗi để pipeline rơi về template pre-computed."""

    def author(self, slot: BlueprintSlot, feedback: list[str]) -> QuestionTemplate:
        raise ValueError("chưa cấu hình adapter LLM viết template")


def default_base_seed(blueprint_id: str) -> int:
    """`int(sha256(blueprint_id)[:15], 16)`: cùng blueprint ⇒ cùng seed gốc (I4); 60 bit nên luôn < 2**63."""
    return int(hashlib.sha256(blueprint_id.encode("utf-8")).hexdigest()[:15], 16)


def _with_format(slot: BlueprintSlot, fmt: QuestionFormat) -> BlueprintSlot:
    """`format` của request ghi đè format của slot; câu NUMERICAL không có `distractor_archetypes`."""
    archetypes = [] if fmt is QuestionFormat.NUMERICAL else [a.model_dump() for a in slot.distractor_archetypes]
    return BlueprintSlot.model_validate({**slot.model_dump(), "format": fmt, "distractor_archetypes": archetypes})


def generate_twin_batch(
    blueprint: ExamBlueprint,
    request: TwinBatchRequest,
    *,
    author: TemplateAuthor,
    registry: TemplateRegistry,
    citations: CitationProvider,
) -> TwinBatch:
    base_seed = request.base_seed if request.base_seed is not None else default_base_seed(blueprint.blueprint_id)
    twins: list[TwinQuestion] = []
    tickets: list[ReviewTicket] = []
    for slot in blueprint.slots:
        citation = citations.citation_for(slot)
        if citation is None:
            tickets.append(
                ReviewTicket(
                    blueprint_id=blueprint.blueprint_id,
                    slot_index=slot.slot_index,
                    topic_key=slot.topic_key,
                    reason="không có nguồn trích dẫn (chunk lý thuyết) cho topic này",
                    attempts=[],
                )
            )
            continue
        shaped = _with_format(slot, request.format)
        slot_ticket: ReviewTicket | None = None
        for k in range(request.count):
            outcome = produce_twin(
                shaped,
                blueprint_id=blueprint.blueprint_id,
                citation=citation,
                author=author,
                registry=registry,
                base_seed=(base_seed + k * SEED_STRIDE) % SEED_SPACE,
            )
            if outcome.twin is not None:
                twins.append(outcome.twin)
            elif slot_ticket is None:
                slot_ticket = outcome.ticket
        if slot_ticket is not None:
            tickets.append(slot_ticket)
    return TwinBatch(blueprint_id=blueprint.blueprint_id, base_seed=base_seed, twins=twins, review_tickets=tickets)
