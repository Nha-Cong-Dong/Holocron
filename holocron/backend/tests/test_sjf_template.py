"""Template SJF non-preemptive: bộ giải tham chiếu độc lập, property test, tất định (I4), độ ổn định sandbox.

Bộ giải tham chiếu mô phỏng TỪNG ms (khác thuật toán nhảy theo burst của template) để hai bên không chung lỗi.
"""

import ast
from collections.abc import Sequence
from fractions import Fraction

import pytest

from holocron_core.domain.schemas import (
    BlueprintSlot,
    KnowledgeChunk,
    TemplateProvenance,
)
from holocron_core.infrastructure.template_library import (
    SJF_TOPIC,
    InMemoryTemplateRegistry,
    load_template_source,
    sjf_avg_waiting_template,
)
from holocron_core.sandbox.sandbox import SandboxResult, SandboxStatus, run_template
from tests.test_pipeline_fallback import FixedRegistry, ScriptedAuthor, _run

SOURCE = load_template_source("os_sjf_avg_waiting.py")
SEED_SAMPLES = 200
MIN_ACCEPT_RATE = 0.10
ALLOWED_PREFIXES = ("VALIDITY_", "TIE_", "NONDEG_", "DISTINCT_")
ABNORMAL = frozenset(
    {
        SandboxStatus.CRASHED,
        SandboxStatus.TIMEOUT,
        SandboxStatus.MEMORY_EXCEEDED,
        SandboxStatus.RUNTIME_ERROR,
        SandboxStatus.CONTRACT_ERROR,
        SandboxStatus.AST_REJECTED,
    }
)
EXPECTED_CODES = {"WAIT_CONFUSED_WITH_TURNAROUND", "SJF_IGNORES_ARRIVAL", "SJF_COMPUTED_AS_SRTF"}


# ------------------------------------------------------------------------------ bộ giải tham chiếu độc lập


def reference_sjf(arrivals: Sequence[int], bursts: Sequence[int]) -> tuple[list[int], list[int]]:
    """SJF không ưu tiên, mô phỏng từng ms. Hòa burst: đến sớm hơn, rồi theo thứ tự bảng. Trả (start, completion)."""
    n = len(arrivals)
    start = [-1] * n
    finish = [-1] * n
    remaining = list(bursts)
    running: int | None = None
    clock = 0
    finished = 0
    while finished < n:
        if running is None:
            ready = [i for i in range(n) if start[i] < 0 and arrivals[i] <= clock]
            if ready:
                running = min(ready, key=lambda i: (bursts[i], arrivals[i], i))
                start[running] = clock
        if running is None:
            clock += 1
            continue
        remaining[running] -= 1
        clock += 1
        if remaining[running] == 0:
            finish[running] = clock
            running = None
            finished += 1
    return start, finish


def reference_srtf_waits(arrivals: Sequence[int], bursts: Sequence[int]) -> list[int]:
    """SJF có ưu tiên (SRTF), mô phỏng từng ms."""
    n = len(arrivals)
    remaining = list(bursts)
    finish = [-1] * n
    clock = 0
    finished = 0
    while finished < n:
        ready = [i for i in range(n) if remaining[i] > 0 and arrivals[i] <= clock]
        if not ready:
            clock += 1
            continue
        chosen = min(ready, key=lambda i: (remaining[i], arrivals[i], i))
        remaining[chosen] -= 1
        clock += 1
        if remaining[chosen] == 0:
            finish[chosen] = clock
            finished += 1
    return [finish[i] - arrivals[i] - bursts[i] for i in range(n)]


def reference_ignore_arrival_waits(bursts: Sequence[int]) -> list[int]:
    """Sai lầm: sắp theo burst như thể mọi tiến trình đến lúc 0; chờ = thời điểm bắt đầu."""
    waits = [0] * len(bursts)
    clock = 0
    for i in sorted(range(len(bursts)), key=lambda i: (bursts[i], i)):
        waits[i] = clock
        clock += bursts[i]
    return waits


def display(value: Fraction) -> str:
    return f"{float(round(value, 2)):.2f} ms"


def expected_displays(arrivals: Sequence[int], bursts: Sequence[int]) -> dict[str, str]:
    n = len(arrivals)
    start, finish = reference_sjf(arrivals, bursts)
    waiting = [start[i] - arrivals[i] for i in range(n)]
    turnaround = [finish[i] - arrivals[i] for i in range(n)]
    return {
        "correct": display(Fraction(sum(waiting), n)),
        "WAIT_CONFUSED_WITH_TURNAROUND": display(Fraction(sum(turnaround), n)),
        "SJF_IGNORES_ARRIVAL": display(Fraction(sum(reference_ignore_arrival_waits(bursts)), n)),
        "SJF_COMPUTED_AS_SRTF": display(Fraction(sum(reference_srtf_waits(arrivals, bursts)), n)),
    }


# ------------------------------------------------------------------------------ dữ liệu dùng chung


@pytest.fixture(scope="module")
def results() -> list[SandboxResult]:
    return [run_template(SOURCE, seed) for seed in range(SEED_SAMPLES)]


@pytest.fixture(scope="module")
def accepted(results: list[SandboxResult]) -> list[SandboxResult]:
    return [r for r in results if r.status is SandboxStatus.OK]


# ------------------------------------------------------------------------------ ca đã biết


TEXTBOOK = {"names": ["P1", "P2", "P3", "P4"], "arrivals": [0, 2, 4, 5], "bursts": [7, 4, 1, 4]}


def test_textbook_case_via_self_solve_mode() -> None:
    """P1(0,7) P2(2,4) P3(4,1) P4(5,4): P1 0-7, P3 7-8, P2 8-12 (hòa burst với P4, P2 đến sớm hơn), P4 12-16."""
    result = run_template(SOURCE, seed=0, params_override=TEXTBOOK)
    assert result.status is SandboxStatus.OK and result.solution is not None
    assert result.solution["display"] == "4.00 ms"
    assert result.solution["gantt"] == [
        {"process": "P1", "start": 0, "end": 7},
        {"process": "P3", "start": 7, "end": 8},
        {"process": "P2", "start": 8, "end": 12},
        {"process": "P4", "start": 12, "end": 16},
    ]
    assert expected_displays(TEXTBOOK["arrivals"], TEXTBOOK["bursts"])["correct"] == "4.00 ms"


def test_cpu_idle_between_processes_is_handled_in_self_solve_mode() -> None:
    params = {"names": ["P1", "P2"], "arrivals": [0, 10], "bursts": [2, 3]}
    result = run_template(SOURCE, seed=0, params_override=params)
    assert result.status is SandboxStatus.OK and result.solution is not None
    assert result.solution["gantt"] == [
        {"process": "P1", "start": 0, "end": 2},
        {"process": "P2", "start": 10, "end": 13},
    ]
    assert result.solution["display"] == "0.00 ms"


# ------------------------------------------------------------------------------ property test


def test_sandbox_never_exits_abnormally_and_accepts_enough_seeds(results: list[SandboxResult]) -> None:
    statuses = [r.status for r in results]
    assert len(results) >= 100
    assert not [s for s in statuses if s in ABNORMAL], "sandbox thoát bất thường"
    accepted = sum(1 for s in statuses if s is SandboxStatus.OK)
    assert accepted / len(results) >= MIN_ACCEPT_RATE, f"chỉ {accepted}/{len(results)} seed được nhận"


def test_answer_and_distractors_match_the_independent_reference(accepted: list[SandboxResult]) -> None:
    assert len(accepted) >= 20
    for r in accepted:
        assert r.params is not None and r.solution is not None and r.distractors is not None
        expected = expected_displays(r.params["arrivals"], r.params["bursts"])
        assert r.solution["display"] == expected["correct"], f"seed {r.seed}"
        by_code = {d["misconception_code"]: d["display"] for d in r.distractors}
        assert set(by_code) == EXPECTED_CODES
        for code in EXPECTED_CODES:
            assert by_code[code] == expected[code], f"seed {r.seed}, {code}"
        values = [r.solution["value"]] + [d["value"] for d in r.distractors]
        assert len(set(values)) == 4
        assert min(abs(a - b) for i, a in enumerate(values) for b in values[i + 1 :]) >= 0.25


def test_gantt_is_a_valid_non_preemptive_sjf_schedule(accepted: list[SandboxResult]) -> None:
    """Mỗi tiến trình đúng một đoạn dài bằng burst; mỗi lần chọn là burst nhỏ nhất trong số đã đến."""
    for r in accepted:
        assert r.params is not None and r.solution is not None
        names, arrivals, bursts = r.params["names"], r.params["arrivals"], r.params["bursts"]
        gantt = r.solution["gantt"]
        assert sorted(s["process"] for s in gantt) == sorted(names), f"seed {r.seed}"
        index = {name: i for i, name in enumerate(names)}
        not_run = set(range(len(names)))
        previous_end = 0
        for segment in gantt:
            i = index[segment["process"]]
            assert segment["end"] - segment["start"] == bursts[i]
            assert segment["start"] >= max(previous_end, arrivals[i])
            ready = [j for j in not_run if arrivals[j] <= segment["start"]]
            assert i in ready
            assert all(bursts[j] > bursts[i] for j in ready if j != i), f"seed {r.seed}: không phải burst nhỏ nhất"
            not_run.discard(i)
            previous_end = segment["end"]


def test_same_seed_gives_identical_output(accepted: list[SandboxResult]) -> None:
    for first in accepted[:8]:
        again = run_template(SOURCE, first.seed)
        assert again.model_dump(exclude={"duration_ms"}) == first.model_dump(exclude={"duration_ms"})


# ------------------------------------------------------------------------------ cấu trúc template


def _assert_labels(source: str) -> list[str]:
    labels: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Assert):
            assert isinstance(node.msg, ast.Constant) and isinstance(node.msg.value, str), "assert thiếu nhãn"
            labels.append(node.msg.value)
    return labels


def test_every_assert_is_labelled_and_documented() -> None:
    labels = _assert_labels(SOURCE)
    assert labels and all(label.startswith(ALLOWED_PREFIXES) for label in labels)
    assert "TIE_EQUAL_BURST_READY" in labels
    template = sjf_avg_waiting_template(TemplateProvenance.PRECOMPUTED_VERIFIED)
    assert {a.assert_id for a in template.assertions} == set(labels)


def test_template_metadata_and_registry() -> None:
    template = sjf_avg_waiting_template(TemplateProvenance.PRECOMPUTED_VERIFIED)
    assert template.topic_key == SJF_TOPIC == "os.cpu_sched.sjf.avg_waiting_time"
    assert {m.misconception_code for m in template.misconceptions} == EXPECTED_CODES
    registry = InMemoryTemplateRegistry()
    verified = registry.get_verified(SJF_TOPIC)
    assert verified is not None and verified.provenance is TemplateProvenance.PRECOMPUTED_VERIFIED
    assert registry.get_verified("os.cpu_sched.rr.avg_waiting_time") is not None


# ------------------------------------------------------------------------------ pipeline


def test_pipeline_builds_a_sjf_twin_with_declared_misconceptions(
    rr_slot: BlueprintSlot, theory_chunk: KnowledgeChunk
) -> None:
    slot = rr_slot.model_copy(update={"topic_key": SJF_TOPIC, "distractor_archetypes": []})
    template = sjf_avg_waiting_template(TemplateProvenance.LLM_GENERATED)
    outcome = _run(slot, theory_chunk, ScriptedAuthor([template]), FixedRegistry(None))
    assert outcome.twin is not None and outcome.ticket is None
    assert {ref.misconception_code for ref in outcome.twin.distractor_to_misconception_map.values()} == EXPECTED_CODES
    assert outcome.twin.gantt
    assert outcome.twin.topic_key == SJF_TOPIC
