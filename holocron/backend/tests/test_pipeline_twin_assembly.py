from holocron_core.pipeline import ProductionOutcome, produce_twin
from holocron_core.schemas import BlueprintSlot, KnowledgeChunk, QuestionTemplate, TemplateProvenance
from holocron_core.template_library import rr_avg_waiting_template

UNDECLARED_CODE = "UNDECLARED_MISCONCEPTION"


def _template_with_undeclared_misconception() -> QuestionTemplate:
    good = rr_avg_waiting_template(TemplateProvenance.LLM_GENERATED)
    source = good.generator_source.replace('"RR_TREATED_AS_FCFS"', f'"{UNDECLARED_CODE}"')
    assert source != good.generator_source
    return good.model_copy(update={"generator_source": source})


class SequenceAuthor:
    def __init__(self, templates: list[QuestionTemplate]) -> None:
        self._templates = list(templates)
        self.feedback_received: list[list[str]] = []

    def author(self, slot: BlueprintSlot, feedback: list[str]) -> QuestionTemplate:
        self.feedback_received.append(feedback)
        return self._templates.pop(0) if len(self._templates) > 1 else self._templates[0]


class EmptyRegistry:
    def get_verified(self, topic_key: str) -> QuestionTemplate | None:
        return None


def _produce(author: SequenceAuthor, slot: BlueprintSlot, chunk: KnowledgeChunk) -> ProductionOutcome:
    return produce_twin(
        slot,
        blueprint_id="bp_test",
        citation=chunk.to_citation(),
        author=author,
        registry=EmptyRegistry(),
        base_seed=5,
    )


def test_undeclared_misconception_is_fed_back_and_retried(rr_slot: BlueprintSlot, theory_chunk: KnowledgeChunk) -> None:
    author = SequenceAuthor(
        [_template_with_undeclared_misconception(), rr_avg_waiting_template(TemplateProvenance.LLM_GENERATED)]
    )
    outcome = _produce(author, rr_slot, theory_chunk)

    assert outcome.twin is not None and outcome.ticket is None
    assert [a.outcome for a in outcome.attempts] == ["twin_assembly_failed", "ok"]
    assert outcome.twin.trace.llm_attempts == 2
    assert author.feedback_received[0] == []
    assert len(author.feedback_received[1]) == 1
    assert author.feedback_received[1][0].startswith("TWIN_ASSEMBLY_FAILED:")
    assert UNDECLARED_CODE in author.feedback_received[1][0]


def test_persistent_undeclared_misconception_exhausts_budget_without_crash(
    rr_slot: BlueprintSlot, theory_chunk: KnowledgeChunk
) -> None:
    author = SequenceAuthor([_template_with_undeclared_misconception()])
    outcome = _produce(author, rr_slot, theory_chunk)

    assert outcome.twin is None and outcome.ticket is not None
    assert [a.outcome for a in outcome.attempts] == ["twin_assembly_failed"] * 3
