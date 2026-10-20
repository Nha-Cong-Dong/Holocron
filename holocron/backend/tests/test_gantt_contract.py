"""Hợp đồng `gantt`: `GanttSegment`, `TwinQuestion.gantt` và đường đi sandbox → twin (spec §5.6, §8.1, I1)."""

import itertools
from typing import Any

import pytest
from pydantic import ValidationError

from holocron_core.domain.schemas import (
    BlueprintSlot,
    GanttSegment,
    KnowledgeChunk,
    QuestionTemplate,
    TemplateProvenance,
    TwinQuestion,
)
from holocron_core.infrastructure.template_library import load_template_source, rr_avg_waiting_template
from holocron_core.sandbox.sandbox import SandboxStatus, run_template
from holocron_core.services.pipeline import ProductionOutcome
from tests.test_pipeline_fallback import FixedRegistry, ScriptedAuthor, _run

RR_SOURCE = load_template_source("os_rr_avg_waiting.py")
GANTT_MARKER = '"gantt": ['


def _segment(process: str = "P1", start: int = 0, end: int = 3) -> GanttSegment:
    return GanttSegment(process=process, start=start, end=end)


def test_segment_requires_end_after_start_and_non_negative_start() -> None:
    assert _segment().end == 3
    with pytest.raises(ValidationError):
        _segment(start=3, end=3)
    with pytest.raises(ValidationError):
        _segment(start=5, end=2)
    with pytest.raises(ValidationError):
        _segment(start=-1, end=2)
    with pytest.raises(ValidationError):
        _segment(process="")
    with pytest.raises(ValidationError):
        _segment(process="P" * 33)
    with pytest.raises(ValidationError):
        GanttSegment.model_validate({"process": "P1", "start": 0, "end": 1, "extra": 1})


def _good_twin(rr_slot: BlueprintSlot, theory_chunk: KnowledgeChunk) -> TwinQuestion:
    author = ScriptedAuthor([rr_avg_waiting_template(TemplateProvenance.LLM_GENERATED)])
    outcome: ProductionOutcome = _run(rr_slot, theory_chunk, author)
    assert outcome.twin is not None
    return outcome.twin


def _rebuild(twin: TwinQuestion, **overrides: Any) -> TwinQuestion:
    return TwinQuestion(**{**twin.model_dump(), **overrides})


def test_cpu_scheduling_twin_requires_non_empty_gantt(rr_slot: BlueprintSlot, theory_chunk: KnowledgeChunk) -> None:
    twin = _good_twin(rr_slot, theory_chunk)
    assert twin.gantt
    with pytest.raises(ValidationError, match="gantt"):
        _rebuild(twin, gantt=[])


def test_gantt_must_be_sorted_by_start(rr_slot: BlueprintSlot, theory_chunk: KnowledgeChunk) -> None:
    twin = _good_twin(rr_slot, theory_chunk)
    unsorted = [{"process": "P2", "start": 3, "end": 5}, {"process": "P1", "start": 0, "end": 3}]
    with pytest.raises(ValidationError, match="start"):
        _rebuild(twin, gantt=unsorted)


def test_gantt_must_not_overlap(rr_slot: BlueprintSlot, theory_chunk: KnowledgeChunk) -> None:
    twin = _good_twin(rr_slot, theory_chunk)
    overlapping = [{"process": "P1", "start": 0, "end": 4}, {"process": "P2", "start": 3, "end": 6}]
    with pytest.raises(ValidationError, match="chồng"):
        _rebuild(twin, gantt=overlapping)


def test_gantt_allows_adjacent_segments_and_idle_gaps(rr_slot: BlueprintSlot, theory_chunk: KnowledgeChunk) -> None:
    twin = _good_twin(rr_slot, theory_chunk)
    with_gap = [{"process": "P1", "start": 0, "end": 3}, {"process": "P2", "start": 3, "end": 5}]
    assert len(_rebuild(twin, gantt=with_gap).gantt) == 2
    idle = [{"process": "P1", "start": 0, "end": 3}, {"process": "P2", "start": 7, "end": 9}]
    assert len(_rebuild(twin, gantt=idle).gantt) == 2


def test_non_cpu_scheduling_topic_may_have_empty_gantt(rr_slot: BlueprintSlot, theory_chunk: KnowledgeChunk) -> None:
    twin = _good_twin(rr_slot, theory_chunk)
    other = _rebuild(twin, topic_key="os.paging.fifo.page_faults", gantt=[])
    assert other.gantt == []


def test_rr_solve_returns_gantt_in_spec_format() -> None:
    result = run_template(RR_SOURCE, seed=5)
    assert result.status is SandboxStatus.OK
    assert result.params is not None and result.solution is not None
    gantt = result.solution["gantt"]
    assert isinstance(gantt, list) and gantt
    names = set(result.params["names"])
    for segment in gantt:
        assert set(segment) == {"process", "start", "end"}
        assert segment["process"] in names
        assert isinstance(segment["start"], int) and isinstance(segment["end"], int)
        assert segment["start"] < segment["end"]


def test_rr_gantt_is_ordered_and_consumes_exactly_the_bursts() -> None:
    """Bất biến độc lập với thuật toán của template: thứ tự, không chồng lấn, tổng thời gian chạy = burst."""
    for seed in range(60):
        result = run_template(RR_SOURCE, seed=seed)
        if result.status is not SandboxStatus.OK:
            continue
        assert result.params is not None and result.solution is not None
        gantt = result.solution["gantt"]
        for previous, current in itertools.pairwise(gantt):
            assert previous["end"] <= current["start"]
        ran: dict[str, int] = {}
        for segment in gantt:
            ran[segment["process"]] = ran.get(segment["process"], 0) + segment["end"] - segment["start"]
        expected = dict(zip(result.params["names"], result.params["bursts"], strict=True))
        assert ran == expected
        assert max(segment["end"] for segment in gantt) <= max(
            arrival + sum(result.params["bursts"]) for arrival in result.params["arrivals"]
        )


def test_twin_gantt_comes_from_the_sandbox_result(rr_slot: BlueprintSlot, theory_chunk: KnowledgeChunk) -> None:
    """I1: gantt của twin là đúng khoá `gantt` do sandbox trả cho seed đó, không tính lại ở nơi khác."""
    twin = _good_twin(rr_slot, theory_chunk)
    sandbox = run_template(RR_SOURCE, seed=twin.seed)
    assert sandbox.status is SandboxStatus.OK and sandbox.solution is not None
    assert [segment.model_dump() for segment in twin.gantt] == sandbox.solution["gantt"]


@pytest.mark.parametrize(
    "bad_gantt",
    [
        "[[1, 2, 3]]",
        '[{"process": "P1", "start": "0", "end": 3}]',
        '[{"process": 1, "start": 0, "end": 3}]',
        '[{"process": "P1", "start": True, "end": 3}]',
        '[{"process": "P1", "start": 0}]',
        '"khong-phai-danh-sach"',
    ],
)
def test_sandbox_rejects_malformed_gantt_as_contract_error(bad_gantt: str) -> None:
    source = RR_SOURCE.replace(GANTT_MARKER, f'"gantt": {bad_gantt}, "_unused": [', 1)
    assert source != RR_SOURCE
    assert run_template(source, seed=5).status is SandboxStatus.CONTRACT_ERROR


def test_template_without_gantt_cannot_build_a_cpu_scheduling_twin(
    rr_slot: BlueprintSlot, theory_chunk: KnowledgeChunk
) -> None:
    good = rr_avg_waiting_template(TemplateProvenance.LLM_GENERATED)
    source = good.generator_source.replace(GANTT_MARKER, '"_no_gantt": [', 1)
    template: QuestionTemplate = good.model_copy(update={"generator_source": source})
    outcome = _run(rr_slot, theory_chunk, ScriptedAuthor([template]), FixedRegistry(None))
    assert outcome.twin is None and outcome.ticket is not None
    assert [a.outcome for a in outcome.attempts] == ["twin_assembly_failed"] * 3
    assert "gantt" in outcome.attempts[0].feedback_sent[0]
