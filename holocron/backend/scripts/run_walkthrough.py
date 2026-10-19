"""Walkthrough end-to-end: Blueprint slot (RR) -> template -> sandbox -> TwinQuestion JSON.

Chạy:  cd backend && python -m scripts.run_walkthrough
Sinh ra: examples/twin_question_rr_mcq.json, examples/twin_question_rr_numerical.json,
         examples/retry_trace_rr.json
"""

from __future__ import annotations

import io
import json
import sys
import uuid
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime, tzinfo
from pathlib import Path
from typing import Any
from unittest.mock import patch

from holocron_core.pipeline import produce_twin
from holocron_core.sandbox import SandboxResult
from holocron_core.sandbox import run_template as _real_run_template
from holocron_core.schemas import (
    BlueprintSlot,
    ChunkCategory,
    Citation,
    CognitiveLevel,
    DistractorArchetype,
    ExamBlueprint,
    KnowledgeChunk,
    OptionKey,
    QuestionFormat,
    QuestionTemplate,
    ReviewStatus,
    SelfSolveResult,
    TemplateProvenance,
)
from holocron_core.template_library import (
    InMemoryTemplateRegistry,
    rr_avg_waiting_template,
)

OUT = Path(__file__).resolve().parents[1] / "examples"

# Đóng băng mọi nguồn không tất định để examples/*.json giống hệt từng byte giữa các lần chạy.
FROZEN_UUID = uuid.UUID("00000000-0000-4000-8000-000000001234")
FROZEN_NOW = datetime(2026, 1, 1, tzinfo=UTC)
FROZEN_DURATION_MS = 42


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz: tzinfo | None = None) -> _FrozenDatetime:
        return cls.fromtimestamp(FROZEN_NOW.timestamp(), tz)


def _run_template_fixed_duration(*args: Any, **kwargs: Any) -> SandboxResult:
    return _real_run_template(*args, **kwargs).model_copy(update={"duration_ms": FROZEN_DURATION_MS})


@contextmanager
def frozen_nondeterminism() -> Iterator[None]:
    """Chỉ áp dụng trong lúc walkthrough chạy; không ảnh hưởng code production hay test."""
    with ExitStack() as stack:
        stack.enter_context(patch("holocron_core.schemas.uuid4", return_value=FROZEN_UUID))
        stack.enter_context(patch("holocron_core.schemas.datetime", _FrozenDatetime))
        stack.enter_context(patch("holocron_core.pipeline.run_template", _run_template_fixed_duration))
        yield


THEORY_CHUNK = KnowledgeChunk(
    chunk_id="chk_os05_rr_definition",
    doc_id="doc_os_lecture05_cpu_scheduling",
    subject_code="IT3070",
    chapter_code="ch5.3",
    page_number=14,
    raw_text=(
        "5.3.4 Round Robin. Mỗi tiến trình được cấp CPU tối đa một lượng tử thời gian q. "
        "Hết q mà chưa xong, tiến trình bị ngắt và đưa về cuối hàng đợi sẵn sàng. "
        "Thời gian chờ của tiến trình i: WT_i = CT_i - AT_i - BT_i."
    ),
    verbatim_quote="Hết q mà chưa xong, tiến trình bị ngắt và đưa về cuối hàng đợi sẵn sàng.",
    category=ChunkCategory.THEORY,
    tags=["#theory", "#cpu_scheduling"],
    topic_keys=["os.cpu_sched.rr.avg_waiting_time"],
    linked_chunk_ids=["chk_os05_rr_example_2"],
    token_count=62,
)

SLOT = BlueprintSlot(
    slot_index=7,
    source_page_number=2,
    format=QuestionFormat.MCQ4,
    chapter_code="ch5.3",
    topic_key="os.cpu_sched.rr.avg_waiting_time",
    cognitive_level=CognitiveLevel.APPLY,
    is_computational=True,
    distractor_archetypes=[
        DistractorArchetype(
            misconception_code="RR_TREATED_AS_FCFS",
            description="Phương án B = kết quả FCFS",
            observed_option_key=OptionKey.B,
            observed_option_text="5.25",
            reproduction_confidence=0.93,
        ),
        DistractorArchetype(
            misconception_code="WAIT_EQUALS_FIRST_RESPONSE",
            description="Phương án C = response time",
            observed_option_key=OptionKey.C,
            observed_option_text="3.00",
            reproduction_confidence=0.88,
        ),
        DistractorArchetype(
            misconception_code="WAIT_CONFUSED_WITH_TURNAROUND",
            description="Phương án D = turnaround trung bình",
            observed_option_key=OptionKey.D,
            observed_option_text="11.50",
            reproduction_confidence=0.91,
        ),
    ],
    self_solve=SelfSolveResult(
        status="MATCHED",
        solved_answer_display="6.75 ms",
        matched_option_key=OptionKey.A,
        solver_confidence=1.0,
        supporting_citations=[THEORY_CHUNK.to_citation()],
        solver_template_id="tpl_os_rr_avg_wait_v1",
    ),
    mapping_confidence=0.91,
    review_status=ReviewStatus.AUTO_PASS,
)


def build_blueprint() -> ExamBlueprint:
    return ExamBlueprint(
        blueprint_id="bp_os_midterm_2025_de2",
        source_exam_doc_id="doc_os_midterm_2025_de2_photo",
        subject_code="IT3070",
        total_questions=1,
        duration_minutes=60,
        slots=[SLOT.model_copy(update={"slot_index": 1})],
    )


BROKEN_SOURCES = [
    (
        "import os\n\ndef generate_params(seed):\n    return {}\n\ndef validate(p):\n    assert True\n\n"
        "def solve(p):\n    return os.listdir('.')\n\ndef make_distractors(p, s):\n    return []\n\n"
        "def render_stem(p):\n    return ''\n"
    ),
    (
        "def generate_params(seed):\n    return {'x': 1}\n\ndef validate(p):\n    assert p['x'] == 1\n\n"
        "def solve(p):\n    return {'value': p['y']}\n\ndef make_distractors(p, s):\n    return []\n\n"
        "def render_stem(p):\n    return 'stem'\n"
    ),
]


class ScriptedAuthor:
    """Giả lập LLM: trả lần lượt các source đã soạn (để demo retry), cuối cùng là template đúng."""

    def __init__(self, broken: list[str]) -> None:
        self._broken = list(broken)
        self.feedback_received: list[list[str]] = []

    def author(self, slot: BlueprintSlot, feedback: list[str]) -> QuestionTemplate:
        self.feedback_received.append(feedback)
        good = rr_avg_waiting_template(TemplateProvenance.LLM_GENERATED)
        if self._broken:
            return good.model_copy(update={"generator_source": self._broken.pop(0)})
        return good


def main() -> None:
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")  # console Windows mặc định cp1252, không in được tiếng Việt
    OUT.mkdir(parents=True, exist_ok=True)
    blueprint = build_blueprint()
    citation: Citation = THEORY_CHUNK.to_citation()
    registry = InMemoryTemplateRegistry()

    happy = produce_twin(
        SLOT,
        blueprint_id=blueprint.blueprint_id,
        citation=citation,
        author=ScriptedAuthor([]),
        registry=registry,
        base_seed=20260419,
    )
    assert happy.twin is not None
    (OUT / "twin_question_rr_mcq.json").write_text(happy.twin.model_dump_json(indent=2), encoding="utf-8")

    numerical_slot = SLOT.model_copy(update={"format": QuestionFormat.NUMERICAL, "distractor_archetypes": []})
    numerical = produce_twin(
        numerical_slot,
        blueprint_id=blueprint.blueprint_id,
        citation=citation,
        author=ScriptedAuthor([]),
        registry=registry,
        base_seed=20260419,
    )
    assert numerical.twin is not None
    (OUT / "twin_question_rr_numerical.json").write_text(numerical.twin.model_dump_json(indent=2), encoding="utf-8")

    author = ScriptedAuthor(BROKEN_SOURCES)
    retried = produce_twin(
        SLOT,
        blueprint_id=blueprint.blueprint_id,
        citation=citation,
        author=author,
        registry=registry,
        base_seed=20260419,
    )
    trace = {
        "final_review_status": retried.twin.review_status if retried.twin else None,
        "attempts": [a.model_dump() for a in retried.attempts],
        "feedback_seen_by_llm": author.feedback_received,
    }
    (OUT / "retry_trace_rr.json").write_text(
        json.dumps(trace, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    (OUT / "exam_blueprint_sample.json").write_text(blueprint.model_dump_json(indent=2), encoding="utf-8")

    print(happy.twin.model_dump_json(indent=2))
    print("retry outcomes:", [a.outcome for a in retried.attempts])


if __name__ == "__main__":
    with frozen_nondeterminism():
        main()
