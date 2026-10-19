"""Validator của domain: KnowledgeChunk, SelfSolveResult, BlueprintSlot, ExamBlueprint, QuestionTemplate, TwinQuestion."""

from typing import Any

import pytest
from pydantic import ValidationError

from holocron_core.domain.schemas import (
    BlueprintSlot,
    ExamBlueprint,
    KnowledgeChunk,
    OptionKey,
    QuestionFormat,
    QuestionTemplate,
    ReviewStatus,
    SelfSolveResult,
    TemplateProvenance,
    TemplateVariable,
    TwinQuestion,
)
from holocron_core.infrastructure.template_library import rr_avg_waiting_template
from holocron_core.services.pipeline import produce_twin
from tests.test_pipeline_fallback import FixedRegistry, ScriptedAuthor


def _chunk(theory_chunk: KnowledgeChunk, **overrides: Any) -> KnowledgeChunk:
    return KnowledgeChunk(**{**theory_chunk.model_dump(exclude={"tags"}), "tags": ["#theory"], **overrides})


def test_chunk_adds_required_tag_and_rejects_wrong_category_tag(theory_chunk: KnowledgeChunk) -> None:
    assert "#theory" in _chunk(theory_chunk, tags=[]).tags
    with pytest.raises(ValidationError):
        _chunk(theory_chunk, tags=["#practice_example"])


@pytest.mark.parametrize(
    "overrides",
    [{"tags": ["khong-hop-le"]}, {"topic_keys": ["Sai Dinh Dang"]}, {"linked_chunk_ids": ["chk_os05_rr_definition"]}],
)
def test_chunk_rejects_bad_tag_topic_key_and_self_link(theory_chunk: KnowledgeChunk, overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        _chunk(theory_chunk, **overrides)


def test_chunk_rejects_page_end_before_page_number(theory_chunk: KnowledgeChunk) -> None:
    with pytest.raises(ValidationError):
        _chunk(theory_chunk, page_end=theory_chunk.page_number - 1)


def test_self_solve_status_consistency() -> None:
    with pytest.raises(ValidationError):
        SelfSolveResult(status="MATCHED", solved_answer_display="1", solver_confidence=1.0)
    with pytest.raises(ValidationError):
        SelfSolveResult(status="AMBIGUOUS", matched_option_key=OptionKey.A, solver_confidence=0.5)
    with pytest.raises(ValidationError):
        SelfSolveResult(status="NO_MATCH", solver_confidence=1.0)
    assert SelfSolveResult(status="SKIPPED", solver_confidence=0.0).status == "SKIPPED"


def _slot(rr_slot: BlueprintSlot, **overrides: Any) -> BlueprintSlot:
    return BlueprintSlot(**{**rr_slot.model_dump(), **overrides})


def test_slot_rejects_inconsistent_non_cloneable_fields(rr_slot: BlueprintSlot) -> None:
    with pytest.raises(ValidationError):
        _slot(rr_slot, non_cloneable=True)
    with pytest.raises(ValidationError):
        _slot(rr_slot, non_cloneable_reason="không có lý do hợp lệ")


def test_slot_rejects_numerical_with_distractors_and_duplicate_codes(rr_slot: BlueprintSlot) -> None:
    with pytest.raises(ValidationError):
        _slot(rr_slot, format=QuestionFormat.NUMERICAL)
    archetypes = [a.model_dump() for a in rr_slot.distractor_archetypes]
    archetypes[1]["misconception_code"] = archetypes[0]["misconception_code"]
    with pytest.raises(ValidationError):
        _slot(rr_slot, distractor_archetypes=archetypes)


def test_slot_rejects_auto_pass_when_self_solve_not_matched(rr_slot: BlueprintSlot) -> None:
    no_match = {"status": "NO_MATCH", "solved_answer_display": "1 ms", "solver_confidence": 1.0}
    with pytest.raises(ValidationError):
        _slot(rr_slot, self_solve=no_match, review_status=ReviewStatus.AUTO_PASS)
    assert _slot(rr_slot, self_solve=no_match, review_status=ReviewStatus.NEEDS_REVIEW)


def _blueprint(slots: list[BlueprintSlot], total: int) -> ExamBlueprint:
    return ExamBlueprint(source_exam_doc_id="doc_exam_1", subject_code="IT3070", total_questions=total, slots=slots)


def test_blueprint_computed_fields_and_validators(rr_slot: BlueprintSlot) -> None:
    first = _slot(rr_slot, slot_index=1)
    second = _slot(
        rr_slot, slot_index=2, non_cloneable=True, non_cloneable_reason="đề cần hình vẽ", mapping_confidence=0.5
    )
    blueprint = _blueprint([first, second], total=2)
    assert blueprint.chapter_distribution == {"ch5.3": 2}
    assert sum(blueprint.format_distribution.values()) == 2
    assert blueprint.cloneable_ratio == 0.5
    assert blueprint.mean_mapping_confidence == round((rr_slot.mapping_confidence + 0.5) / 2, 4)
    with pytest.raises(ValidationError):  # tổng số câu không khớp số slot
        _blueprint([first], total=2)
    with pytest.raises(ValidationError):  # slot_index không liên tục 1..N
        _blueprint([first, _slot(rr_slot, slot_index=5)], total=2)
    with pytest.raises(ValidationError):  # slot_index trùng
        _blueprint([first, _slot(rr_slot, slot_index=1)], total=2)


def _template_kwargs(**overrides: Any) -> dict[str, Any]:
    base = rr_avg_waiting_template(TemplateProvenance.LLM_GENERATED).model_dump(exclude={"source_sha256"})
    return {**base, **overrides}


def test_template_source_validators() -> None:
    good = rr_avg_waiting_template(TemplateProvenance.LLM_GENERATED)
    assert len(good.source_sha256) == 64
    padding = "# " + "x" * 60 + "\n"
    with pytest.raises(ValidationError, match="cú pháp"):
        QuestionTemplate(**_template_kwargs(generator_source=padding + "def broken(:\n    pass\n"))
    with pytest.raises(ValidationError, match="thiếu hàm"):
        QuestionTemplate(**_template_kwargs(generator_source=padding + "def solve(p):\n    assert True\n"))
    no_assert = good.generator_source.replace("assert ", "check = ")
    with pytest.raises(ValidationError):
        QuestionTemplate(**_template_kwargs(generator_source=no_assert))


def test_template_rejects_duplicate_misconceptions_and_assert_ids() -> None:
    mis = [m.model_dump() for m in rr_avg_waiting_template(TemplateProvenance.LLM_GENERATED).misconceptions]
    mis[1]["misconception_code"] = mis[0]["misconception_code"]
    with pytest.raises(ValidationError):
        QuestionTemplate(**_template_kwargs(misconceptions=mis))
    asserts = _template_kwargs()["assertions"]
    asserts[1]["assert_id"] = asserts[0]["assert_id"]
    with pytest.raises(ValidationError):
        QuestionTemplate(**_template_kwargs(assertions=asserts))


def test_template_variable_domain_rules() -> None:
    with pytest.raises(ValidationError):
        TemplateVariable(name="n", kind="int", min_value=5, max_value=1, description="miền ngược")
    with pytest.raises(ValidationError):
        TemplateVariable(name="q", kind="choice_int", description="thiếu choices")
    with pytest.raises(ValidationError):
        TemplateVariable(name="a", kind="list_int", description="thiếu độ dài")


def _twin(
    rr_slot: BlueprintSlot, theory_chunk: KnowledgeChunk, fmt: QuestionFormat = QuestionFormat.MCQ4
) -> TwinQuestion:
    slot = rr_slot
    if fmt is QuestionFormat.NUMERICAL:
        slot = rr_slot.model_copy(update={"format": fmt, "distractor_archetypes": []})
    outcome = produce_twin(
        slot,
        blueprint_id="bp_t",
        citation=theory_chunk.to_citation(),
        author=ScriptedAuthor([rr_avg_waiting_template(TemplateProvenance.LLM_GENERATED)]),
        registry=FixedRegistry(None),
        base_seed=5,
    )
    assert outcome.twin is not None
    return outcome.twin


def test_twin_mcq_rules(rr_slot: BlueprintSlot, theory_chunk: KnowledgeChunk) -> None:
    twin = _twin(rr_slot, theory_chunk)
    assert twin.correct_key is not None and twin.options is not None
    base = twin.model_dump()
    assert twin.correct_key not in twin.distractor_to_misconception_map

    def rebuild(**overrides: Any) -> TwinQuestion:
        return TwinQuestion(**{**base, **overrides})

    options = dict(base["options"])
    with pytest.raises(ValidationError):  # thiếu option
        rebuild(options={k: v for k, v in options.items() if k != OptionKey.A})
    with pytest.raises(ValidationError):  # option trùng nhau
        rebuild(options={**options, OptionKey.A: options[OptionKey.B]})
    with pytest.raises(ValidationError):  # thiếu correct_key
        rebuild(correct_key=None)
    mapping = dict(base["distractor_to_misconception_map"])
    first_ref = next(iter(mapping.values()))
    with pytest.raises(ValidationError):  # map không phủ đủ 3 phương án sai
        rebuild(distractor_to_misconception_map={k: v for k, v in list(mapping.items())[1:]})
    with pytest.raises(ValidationError):  # correct_key bị gán misconception
        rebuild(distractor_to_misconception_map={**mapping, twin.correct_key: first_ref})
    with pytest.raises(ValidationError):  # MCQ không dùng numerical_answer
        rebuild(numerical_answer={"value": 1.0, "tolerance_abs": 0.01, "decimals": 2})


def test_twin_numerical_rules(rr_slot: BlueprintSlot, theory_chunk: KnowledgeChunk) -> None:
    twin = _twin(rr_slot, theory_chunk, QuestionFormat.NUMERICAL)
    base = twin.model_dump()
    with pytest.raises(ValidationError):
        TwinQuestion(**{**base, "numerical_answer": None})
    with pytest.raises(ValidationError):
        TwinQuestion(**{**base, "correct_key": OptionKey.A})
