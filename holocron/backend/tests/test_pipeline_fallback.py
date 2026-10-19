"""Pipeline: phân loại lỗi, retry budget, fallback pre-computed, ReviewTicket và định dạng NUMERICAL."""

import pytest

from holocron_core.domain.schemas import (
    BlueprintSlot,
    KnowledgeChunk,
    QuestionFormat,
    QuestionTemplate,
    ReviewStatus,
    TemplateProvenance,
)
from holocron_core.infrastructure.template_library import rr_avg_waiting_template
from holocron_core.services import pipeline
from holocron_core.services.pipeline import LLM_ATTEMPT_BUDGET, ProductionOutcome, produce_twin

VALIDITY_LINE = 'assert 4 <= n <= 5, "VALIDITY_PROCESS_COUNT"'


def _mutate(replacement: str, provenance: TemplateProvenance = TemplateProvenance.LLM_GENERATED) -> QuestionTemplate:
    good = rr_avg_waiting_template(provenance)
    assert VALIDITY_LINE in good.generator_source
    return good.model_copy(update={"generator_source": good.generator_source.replace(VALIDITY_LINE, replacement)})


def _ast_rejected() -> QuestionTemplate:
    good = rr_avg_waiting_template(TemplateProvenance.LLM_GENERATED)
    return good.model_copy(update={"generator_source": "import os\n" + good.generator_source})


def _runtime_error(provenance: TemplateProvenance = TemplateProvenance.LLM_GENERATED) -> QuestionTemplate:
    return _mutate("x = 1 // 0", provenance)


def _ill_posed() -> QuestionTemplate:
    return _mutate('assert n > 100, "VALIDITY_PROCESS_COUNT"')


class ScriptedAuthor:
    """Trả lần lượt các template; template cuối được lặp lại. Phần tử là `ValueError` thì ném ra."""

    def __init__(self, steps: list[QuestionTemplate | ValueError]) -> None:
        self._steps = list(steps)
        self.feedback_received: list[list[str]] = []

    def author(self, slot: BlueprintSlot, feedback: list[str]) -> QuestionTemplate:
        self.feedback_received.append(feedback)
        step = self._steps.pop(0) if len(self._steps) > 1 else self._steps[0]
        if isinstance(step, ValueError):
            raise step
        return step


class FixedRegistry:
    def __init__(self, template: QuestionTemplate | None) -> None:
        self._template = template

    def get_verified(self, topic_key: str) -> QuestionTemplate | None:
        return self._template


def _run(
    slot: BlueprintSlot,
    chunk: KnowledgeChunk,
    author: ScriptedAuthor,
    registry: FixedRegistry | None = None,
) -> ProductionOutcome:
    return produce_twin(
        slot,
        blueprint_id="bp_test",
        citation=chunk.to_citation(),
        author=author,
        registry=registry or FixedRegistry(None),
        base_seed=5,
    )


def test_non_cloneable_slot_returns_ticket_without_calling_author(
    rr_slot: BlueprintSlot, theory_chunk: KnowledgeChunk
) -> None:
    slot = rr_slot.model_copy(update={"non_cloneable": True, "non_cloneable_reason": "đề cần hình vẽ"})
    author = ScriptedAuthor([rr_avg_waiting_template(TemplateProvenance.LLM_GENERATED)])
    outcome = _run(slot, theory_chunk, author)
    assert outcome.twin is None and outcome.ticket is not None
    assert outcome.ticket.reason.startswith("non_cloneable:")
    assert author.feedback_received == []


def test_schema_invalid_is_fed_back_then_recovers(rr_slot: BlueprintSlot, theory_chunk: KnowledgeChunk) -> None:
    author = ScriptedAuthor([ValueError("thiếu entrypoint"), rr_avg_waiting_template(TemplateProvenance.LLM_GENERATED)])
    outcome = _run(rr_slot, theory_chunk, author)
    assert outcome.twin is not None
    assert [a.outcome for a in outcome.attempts] == ["schema_invalid", "ok"]
    assert author.feedback_received[1] == ["SCHEMA_INVALID: thiếu entrypoint"]


def test_ast_rejected_feedback_lists_violations(rr_slot: BlueprintSlot, theory_chunk: KnowledgeChunk) -> None:
    author = ScriptedAuthor([_ast_rejected(), rr_avg_waiting_template(TemplateProvenance.LLM_GENERATED)])
    outcome = _run(rr_slot, theory_chunk, author)
    assert [a.outcome for a in outcome.attempts] == ["ast_rejected", "ok"]
    assert author.feedback_received[1][0].startswith("AST_REJECTED:")
    assert "import bị cấm: os" in author.feedback_received[1][0]


def test_runtime_error_feedback_names_stage(rr_slot: BlueprintSlot, theory_chunk: KnowledgeChunk) -> None:
    author = ScriptedAuthor([_runtime_error(), rr_avg_waiting_template(TemplateProvenance.LLM_GENERATED)])
    outcome = _run(rr_slot, theory_chunk, author)
    assert [a.outcome for a in outcome.attempts] == ["runtime_error", "ok"]
    assert author.feedback_received[1][0].startswith("RUNTIME_ERROR")
    assert "ZeroDivisionError" in author.feedback_received[1][0]


def test_ill_posed_template_costs_one_llm_attempt_and_reports_histogram(
    rr_slot: BlueprintSlot, theory_chunk: KnowledgeChunk, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pipeline, "SEED_REROLL_BUDGET", 3)
    author = ScriptedAuthor([_ill_posed(), rr_avg_waiting_template(TemplateProvenance.LLM_GENERATED)])
    outcome = _run(rr_slot, theory_chunk, author)
    assert [a.outcome for a in outcome.attempts] == ["ill_posed", "ok"]
    assert outcome.attempts[0].seeds_tried == 3
    assert outcome.attempts[0].assertion_histogram == {"VALIDITY_PROCESS_COUNT": 3}
    assert author.feedback_received[1][0].startswith("ILL_POSED: 0/3")


def test_budget_exhausted_uses_precomputed_fallback(rr_slot: BlueprintSlot, theory_chunk: KnowledgeChunk) -> None:
    verified = rr_avg_waiting_template(TemplateProvenance.PRECOMPUTED_VERIFIED)
    outcome = _run(rr_slot, theory_chunk, ScriptedAuthor([_ast_rejected()]), FixedRegistry(verified))
    assert outcome.ticket is None and outcome.twin is not None
    assert outcome.twin.review_status is ReviewStatus.AUTO_FALLBACK
    assert outcome.twin.trace.provenance is TemplateProvenance.PRECOMPUTED_VERIFIED
    assert [a.outcome for a in outcome.attempts] == ["ast_rejected"] * LLM_ATTEMPT_BUDGET + ["ok"]


def test_broken_fallback_ends_in_review_ticket(rr_slot: BlueprintSlot, theory_chunk: KnowledgeChunk) -> None:
    broken = _runtime_error(TemplateProvenance.PRECOMPUTED_VERIFIED)
    outcome = _run(rr_slot, theory_chunk, ScriptedAuthor([_ast_rejected()]), FixedRegistry(broken))
    assert outcome.twin is None and outcome.ticket is not None
    assert outcome.attempts[-1].outcome == "runtime_error"
    assert outcome.ticket.attempts == outcome.attempts


def test_fallback_with_undeclared_misconception_is_recorded_not_raised(
    rr_slot: BlueprintSlot, theory_chunk: KnowledgeChunk
) -> None:
    good = rr_avg_waiting_template(TemplateProvenance.PRECOMPUTED_VERIFIED)
    bad = good.model_copy(
        update={"generator_source": good.generator_source.replace('"RR_TREATED_AS_FCFS"', '"UNDECLARED_ONE"')}
    )
    outcome = _run(rr_slot, theory_chunk, ScriptedAuthor([_ast_rejected()]), FixedRegistry(bad))
    assert outcome.twin is None and outcome.ticket is not None
    assert outcome.attempts[-1].outcome == "twin_assembly_failed"
    assert "UNDECLARED_ONE" in outcome.attempts[-1].feedback_sent[0]


def test_numerical_format_builds_numerical_answer(rr_slot: BlueprintSlot, theory_chunk: KnowledgeChunk) -> None:
    slot = rr_slot.model_copy(update={"format": QuestionFormat.NUMERICAL, "distractor_archetypes": []})
    author = ScriptedAuthor([rr_avg_waiting_template(TemplateProvenance.LLM_GENERATED)])
    outcome = _run(slot, theory_chunk, author)
    assert outcome.twin is not None
    assert outcome.twin.options is None and outcome.twin.correct_key is None
    assert outcome.twin.numerical_answer is not None
    assert outcome.twin.numerical_answer.decimals == 2
