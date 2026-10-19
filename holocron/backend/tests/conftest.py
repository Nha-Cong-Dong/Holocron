"""Fixture dùng chung cho test; độc lập với thư mục scripts/."""

import pytest

from holocron_core.domain.schemas import (
    BlueprintSlot,
    CognitiveLevel,
    DistractorArchetype,
    KnowledgeChunk,
    OptionKey,
    QuestionFormat,
    SelfSolveResult,
)

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
    category="THEORY",
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
    review_status="AUTO_PASS",
)


@pytest.fixture
def theory_chunk() -> KnowledgeChunk:
    return THEORY_CHUNK


@pytest.fixture
def rr_slot() -> BlueprintSlot:
    return SLOT
