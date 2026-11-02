"""Sinh bộ Twin Questions cho mọi slot của một blueprint (SPECIFICATION §5.6, §6.3).

**Slot tính toán** (`BlueprintSlot`): với mỗi `k ∈ [0, count)` gọi `produce_twin(base_seed = base_seed + k·7919)`
(stride hiện có, chưa đổi theo R6). Mọi con số và `gantt` của twin đến từ sandbox (I1); module này KHÔNG tính gì
thêm. `format` của request ghi đè `format` của slot tính toán.

**Slot lý thuyết** (`TheorySlot`, ADR-007; quyết định 14(b) ngày 2026-10-28): bộ sinh tất định
`generate_theory_question` trên các cặp khái niệm `confirmed` của môn, giới hạn đáp án vào `chapter_code` của slot;
không gọi sandbox hay `CitationProvider`; `format` của request không áp dụng. Seed theo R6 (§6.3): twin k có
`seed_k = (base_seed + k·7919·40) mod SEED_SPACE`, lần thử j ∈ [0, 40) dùng `(seed_k + j·7919) mod SEED_SPACE`. Câu có
dấu vân tay trùng một câu lý thuyết đã sinh trong batch thì thử j tiếp theo; hết dải ⇒ `ReviewTicket`, không sinh câu
trùng. Dấu vân tay là sha256 của JSON chính tắc gồm `generator_version`, `form`, bốn phương án theo thứ tự A–D và
`citation.chunk_id` của đáp án: bộ sinh không lộ `pair_id`, nên đây là dấu vân tay thay thế cho "danh sách `pair_id`
theo thứ tự phương án" của §6.3 (nợ ADR-004).

Lỗi template/sandbox hay thiếu căn cứ không làm hỏng batch: slot không sinh được đi vào `review_tickets` (mỗi slot
tối đa một ticket). Với slot lý thuyết, Vault không nhất quán (cặp đã xác nhận mà chunk nguồn không có trong lần
đọc chunk, hay trích dẫn không còn khớp nguyên văn chunk, `QuoteMismatchError`) cũng thành ticket với lý do cố định,
không chép topic, `chunk_id` hay trích dẫn; lỗi khác của bộ sinh (vd seed ngoài khoảng) vẫn nổi lên.

Phụ thuộc ngoài đi qua `Protocol` (`TemplateAuthor`, `TemplateRegistry`, `CitationProvider`, `VaultRepository`) để
test thay bằng stub.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Protocol

from ..domain.repositories import VaultRepository
from ..domain.schemas import (
    BlueprintSlot,
    Citation,
    ExamBlueprint,
    KnowledgeChunk,
    QuestionFormat,
    QuestionTemplate,
    ReviewStatus,
    TheorySlot,
    TwinQuestion,
)
from ..domain.theory import TheoryQuestion
from ..domain.twins import SEED_SPACE, ReviewTicket, TheoryTwin, TwinBatch, TwinBatchRequest
from ..domain.vault import ConceptPair, ConceptPairStatus, QuoteMismatchError
from .pipeline import SEED_REROLL_BUDGET, SEED_STRIDE, TemplateAuthor, TemplateRegistry, produce_twin
from .theory_generator import candidate_pool, generate_theory_question

# `TwinBatchRequest`, `TwinBatch` và `SEED_SPACE` đã chuyển về `domain/twins.py`; tên cũ vẫn import được từ module này.
__all__ = [
    "SEED_SPACE",
    "THEORY_MISSING_CHUNK_REASON",
    "THEORY_QUOTE_MISMATCH_REASON",
    "THEORY_SEED_BAND",
    "CitationProvider",
    "NoCitationProvider",
    "TwinBatch",
    "TwinBatchRequest",
    "UnconfiguredTemplateAuthor",
    "default_base_seed",
    "generate_twin_batch",
    "theory_fingerprint",
    "theory_seed",
]

THEORY_SEED_BAND = SEED_STRIDE * SEED_REROLL_BUDGET
"""Độ dài dải seed của một twin lý thuyết (`7919·40`, §6.3 R6): dải của các twin không chồng nhau."""

THEORY_DUPLICATE_REASON = (
    "không sinh được câu lý thuyết khác các câu đã có trong batch sau {budget} lần thử trong dải seed của twin {k}"
)

THEORY_MISSING_CHUNK_REASON = (
    "Vault không nhất quán: có cặp khái niệm đã xác nhận của môn mà đoạn tài liệu nguồn không còn trong kho; "
    "cần đối soát lại Vault rồi sinh lại"
)
"""Lý do cố định (§6.3): không chép topic, `chunk_id` hay trích dẫn vào ticket."""

THEORY_QUOTE_MISMATCH_REASON = (
    "Vault không nhất quán: trích dẫn của một cặp khái niệm không còn khớp nguyên văn đoạn tài liệu nguồn (I3); "
    "cần đối soát lại Vault rồi sinh lại"
)
"""Lý do cố định (§6.3): không chép topic, `chunk_id` hay trích dẫn vào ticket."""


class CitationProvider(Protocol):
    """Nguồn trích dẫn (Knowledge Vault, §12): trả `Citation` cho slot của blueprint môn `subject_code`, hoặc `None`
    nếu không có chunk phù hợp. Chunk của môn khác không bao giờ được trả (ADR-006 mục 6, SPECIFICATION §5.8)."""

    def citation_for(self, subject_code: str, slot: BlueprintSlot) -> Citation | None: ...


class NoCitationProvider:
    """Mặc định khi chưa có Knowledge Vault: không có trích dẫn ⇒ mọi slot vào `review_tickets`."""

    def citation_for(self, subject_code: str, slot: BlueprintSlot) -> Citation | None:
        return None


class UnconfiguredTemplateAuthor:
    """Mặc định khi chưa có adapter LLM (T2-N5): luôn báo lỗi để pipeline rơi về template pre-computed."""

    def author(self, slot: BlueprintSlot, feedback: list[str]) -> QuestionTemplate:
        raise ValueError("chưa cấu hình adapter LLM viết template")


def default_base_seed(blueprint_id: str) -> int:
    """`int(sha256(blueprint_id)[:15], 16)`: cùng blueprint ⇒ cùng seed gốc (I4); 60 bit nên luôn < 2**63."""
    return int(hashlib.sha256(blueprint_id.encode("utf-8")).hexdigest()[:15], 16)


def theory_seed(base_seed: int, k: int, j: int) -> int:
    """Seed lần thử j của twin lý thuyết k (§6.3 R6): `seed_k = (base_seed + k·7919·40) mod SEED_SPACE`, rồi
    `(seed_k + j·7919) mod SEED_SPACE`."""
    seed_k = (base_seed + k * THEORY_SEED_BAND) % SEED_SPACE
    return (seed_k + j * SEED_STRIDE) % SEED_SPACE


def theory_fingerprint(question: TheoryQuestion) -> str:
    """Dấu vân tay R6 của câu lý thuyết: sha256 của JSON chính tắc (`sort_keys`, không ASCII-hoá, không khoảng trắng)."""
    payload = {
        "generator_version": question.generator_version,
        "form": question.form.value,
        "options": [question.options[key] for key in sorted(question.options, key=lambda option: option.value)],
        "chunk_id": question.citation.chunk_id,
    }
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _with_format(slot: BlueprintSlot, fmt: QuestionFormat) -> BlueprintSlot:
    """`format` của request ghi đè format của slot; câu NUMERICAL không có `distractor_archetypes`."""
    archetypes = [] if fmt is QuestionFormat.NUMERICAL else [a.model_dump() for a in slot.distractor_archetypes]
    return BlueprintSlot.model_validate({**slot.model_dump(), "format": fmt, "distractor_archetypes": archetypes})


class _TheoryPool:
    """Cặp đã xác nhận của môn và chunk nguồn của chúng, đọc một lần cho cả batch.

    Hai lần đọc (cặp, rồi chunk) không cùng snapshot: cặp nào trong nhóm ứng viên của bộ sinh mà chunk nguồn không có
    trong lần đọc chunk thì `missing_chunks` bật. Bộ sinh có thể trích dẫn bất kỳ cặp nào của môn (đáp án hay phương án
    nhiễu), nên kiểm trên cả nhóm ứng viên chứ không chỉ cặp của chương.
    """

    def __init__(self, vault: VaultRepository | None, subject_code: str) -> None:
        self.pairs: Sequence[ConceptPair] = ()
        self.chunks: Mapping[str, KnowledgeChunk] = {}
        if vault is not None:
            self.pairs = vault.list_concept_pairs(subject_code, status=ConceptPairStatus.CONFIRMED)
            sources = {pair.chunk_id for pair in self.pairs}
            self.chunks = {
                chunk.chunk_id: chunk for chunk in vault.list_chunks(subject_code) if chunk.chunk_id in sources
            }
        self.missing_chunks = any(pair.chunk_id not in self.chunks for pair in candidate_pool(self.pairs, subject_code))


def _theory_twins(
    blueprint: ExamBlueprint,
    slot: TheorySlot,
    *,
    count: int,
    base_seed: int,
    pool: _TheoryPool,
    seen: set[str],
) -> tuple[list[TheoryTwin], ReviewTicket | None]:
    """Twin lý thuyết của một slot và ticket đầu tiên của slot (nếu có).

    Thiếu căn cứ trong Vault không phụ thuộc seed (ADR-007, `feasible_forms`), nên gặp ticket đó thì dừng slot. Vault
    không nhất quán (`missing_chunks`, hay `QuoteMismatchError` ở twin k) cũng dừng slot: giữ các twin đã sinh, ticket
    mang lý do cố định. Lỗi khác của bộ sinh không bị bắt.
    """

    def ticket(reason: str) -> ReviewTicket:
        return ReviewTicket(
            blueprint_id=blueprint.blueprint_id,
            slot_index=slot.slot_index,
            topic_key=slot.topic_key,
            reason=reason,
            attempts=[],
        )

    if slot.format is not QuestionFormat.MCQ4:
        return [], ticket(f"chưa sinh được câu lý thuyết hình thức {slot.format.value}")
    if pool.missing_chunks:
        return [], ticket(THEORY_MISSING_CHUNK_REASON)
    twins: list[TheoryTwin] = []
    first_ticket: ReviewTicket | None = None
    for k in range(count):
        produced: TheoryQuestion | None = None
        for j in range(SEED_REROLL_BUDGET):
            try:
                result = generate_theory_question(
                    pool.pairs,
                    pool.chunks,
                    subject_code=blueprint.subject_code,
                    seed=theory_seed(base_seed, k, j),
                    blueprint_id=blueprint.blueprint_id,
                    slot_index=slot.slot_index,
                    topic_key=slot.topic_key,
                    chapter_code=slot.chapter_code,
                )
            except QuoteMismatchError:
                return twins, ticket(THEORY_QUOTE_MISMATCH_REASON)
            if isinstance(result, ReviewTicket):
                return twins, result
            fingerprint = theory_fingerprint(result)
            if fingerprint not in seen:
                seen.add(fingerprint)
                produced = result
                break
        if produced is None:
            if first_ticket is None:
                first_ticket = ticket(THEORY_DUPLICATE_REASON.format(budget=SEED_REROLL_BUDGET, k=k))
            continue
        twins.append(
            TheoryTwin(
                blueprint_id=blueprint.blueprint_id,
                slot_index=slot.slot_index,
                question=produced,
                review_status=ReviewStatus.AUTO_PASS,
            )
        )
    return twins, first_ticket


def generate_twin_batch(
    blueprint: ExamBlueprint,
    request: TwinBatchRequest,
    *,
    author: TemplateAuthor,
    registry: TemplateRegistry,
    citations: CitationProvider,
    vault: VaultRepository | None = None,
    created_at: datetime | None = None,
) -> TwinBatch:
    """`vault`: nguồn cặp khái niệm cho slot lý thuyết (không có ⇒ slot lý thuyết thành ticket thiếu căn cứ).
    `created_at`: thời điểm tạo batch (router truyền đồng hồ của request); không có ⇒ thời điểm hiện tại."""
    base_seed = request.base_seed if request.base_seed is not None else default_base_seed(blueprint.blueprint_id)
    twins: list[TwinQuestion | TheoryTwin] = []
    tickets: list[ReviewTicket] = []
    theory_pool: _TheoryPool | None = None
    seen_theory: set[str] = set()
    for slot in blueprint.slots:
        if isinstance(slot, TheorySlot):
            if theory_pool is None:
                theory_pool = _TheoryPool(vault, blueprint.subject_code)
            produced, theory_ticket = _theory_twins(
                blueprint, slot, count=request.count, base_seed=base_seed, pool=theory_pool, seen=seen_theory
            )
            twins.extend(produced)
            if theory_ticket is not None:
                tickets.append(theory_ticket)
            continue
        citation = citations.citation_for(blueprint.subject_code, slot)
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
    batch = TwinBatch(blueprint_id=blueprint.blueprint_id, base_seed=base_seed, twins=twins, review_tickets=tickets)
    return batch if created_at is None else batch.model_copy(update={"created_at": created_at})
