"""Test lấp các đột biến còn sống của nhánh blueprint lý thuyết (ADR-007; SPECIFICATION §5.9, §5.11, §6.3, §6.8).

Mỗi test khoá một hành vi mà bộ test sẵn có chưa phân biệt được với một đột biến: chuẩn hoá khoá phương án, cận dưới
của `slot_index`/`version`, hình thức ESSAY của câu tính toán, `cognitive_level` của slot lý thuyết, dấu vân tay R6,
ticket đầu tiên của slot, `review_status` của twin lý thuyết và ngân sách seed của một twin. Văn bản mẫu tự viết cho
test (không phải tài liệu môn học có bản quyền).
"""

import unicodedata
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from holocron_core.domain.schemas import (
    CognitiveLevel,
    ExamBlueprint,
    OptionKey,
    QuestionFormat,
    ReviewStatus,
    Subject,
    TheorySlot,
    TwinQuestion,
)
from holocron_core.domain.theory import TheoryQuestion, TheoryQuestionForm
from holocron_core.domain.twins import ReviewTicket, TheoryTwin, TwinBatch, TwinBatchRequest
from holocron_core.infrastructure.memory_store import IngestionStore
from holocron_core.infrastructure.template_library import InMemoryTemplateRegistry
from holocron_core.services import twin_batch
from holocron_core.services.pipeline import SEED_REROLL_BUDGET
from holocron_core.services.twin_batch import (
    THEORY_DUPLICATE_REASON,
    NoCitationProvider,
    UnconfiguredTemplateAuthor,
    generate_twin_batch,
    theory_fingerprint,
)
from tests.test_theory_twins import BASE_SEED, SUBJECT, seed_philosophy

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
NOW = datetime(2026, 10, 28, 8, 0, tzinfo=UTC)


def _theory_blueprint(*chapters: str) -> ExamBlueprint:
    slots = [
        TheorySlot(slot_index=index, chapter_code=chapter, format=QuestionFormat.MCQ4)
        for index, chapter in enumerate(chapters, start=1)
    ]
    return ExamBlueprint(subject_code=SUBJECT, total_questions=len(slots), slots=slots)


def _batch(blueprint: ExamBlueprint, vault: IngestionStore | None, count: int) -> TwinBatch:
    return generate_twin_batch(
        blueprint,
        TwinBatchRequest(count=count, base_seed=BASE_SEED),
        author=UnconfiguredTemplateAuthor(),
        registry=InMemoryTemplateRegistry(),
        citations=NoCitationProvider(),
        vault=vault,
    )


def _philosophy_batch(count: int) -> TwinBatch:
    vault = IngestionStore()
    seed_philosophy(vault)
    return _batch(_theory_blueprint("ch1", "ch2"), vault, count)


def _question() -> TheoryQuestion:
    """Một câu lý thuyết hợp lệ do bộ sinh thật ghép."""
    twin = _philosophy_batch(1).twins[0]
    assert isinstance(twin, TheoryTwin)
    return twin.question


def _same_question_every_call(monkeypatch: pytest.MonkeyPatch, question: TheoryQuestion) -> list[int]:
    """Bộ sinh trong `twin_batch` luôn trả `question` (nhóm chỉ có một câu phân biệt); trả bộ đếm lượt gọi."""
    calls = [0]

    def generator(*args: Any, **kwargs: Any) -> TheoryQuestion | ReviewTicket:
        calls[0] += 1
        return question

    replacement: Callable[..., TheoryQuestion | ReviewTicket] = generator
    monkeypatch.setattr(twin_batch, "generate_theory_question", replacement)
    return calls


# --------------------------------------------------------------------------- #
# domain/theory.py: khoá so trùng phương án
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "variant",
    [
        pytest.param(unicodedata.normalize("NFD", "Tiến trình zeta"), id="nfd"),
        pytest.param("Tiến  trình\tzeta", id="whitespace"),
    ],
)
def test_options_equal_after_nfc_and_whitespace_collapse_are_duplicates(variant: str) -> None:
    question = _question()
    first, second = [key for key in OptionKey if key is not question.correct_key][:2]
    assert variant != "Tiến trình zeta"
    data = question.model_dump(mode="json")
    data["options"][first.value] = "Tiến trình zeta"
    data["options"][second.value] = variant
    with pytest.raises(ValidationError, match="4 options phải khác nhau"):
        TheoryQuestion.model_validate(data)


# --------------------------------------------------------------------------- #
# domain/twins.py, domain/schemas.py: cận và mặc định của model
# --------------------------------------------------------------------------- #


def test_theory_twin_slot_index_starts_at_one() -> None:
    question = _question()
    TheoryTwin(blueprint_id="bp_x", slot_index=1, question=question, review_status=ReviewStatus.AUTO_PASS)
    with pytest.raises(ValidationError, match="slot_index"):
        TheoryTwin(blueprint_id="bp_x", slot_index=0, question=question, review_status=ReviewStatus.AUTO_PASS)


def test_computational_twin_rejects_essay_format() -> None:
    data = TwinQuestion.model_validate_json((EXAMPLES / "twin_question_rr_mcq.json").read_text(encoding="utf-8"))
    payload = data.model_dump(mode="json")
    payload["format"] = QuestionFormat.ESSAY.value
    with pytest.raises(ValidationError, match="câu tính toán không có hình thức ESSAY"):
        TwinQuestion.model_validate(payload)


@pytest.mark.parametrize("level", [CognitiveLevel.APPLY, CognitiveLevel.ANALYZE])
def test_theory_slot_rejects_cognitive_levels_above_understand(level: CognitiveLevel) -> None:
    with pytest.raises(ValidationError, match="cognitive_level remember hoặc understand"):
        TheorySlot(slot_index=1, chapter_code="ch1", format=QuestionFormat.MCQ4, cognitive_level=level)


def test_theory_slot_cognitive_level_defaults_to_remember() -> None:
    slot = TheorySlot(slot_index=1, chapter_code="ch1", format=QuestionFormat.MCQ4)
    assert slot.cognitive_level is CognitiveLevel.REMEMBER
    assert slot.model_dump(mode="json")["cognitive_level"] == "remember"


def test_subject_version_starts_at_one() -> None:
    subject = Subject(subject_code=SUBJECT, display_name="Triết học", created_at=NOW, updated_at=NOW)
    assert subject.version == 1
    Subject(subject_code=SUBJECT, display_name="Triết học", version=1, created_at=NOW, updated_at=NOW)
    with pytest.raises(ValidationError, match="version"):
        Subject(subject_code=SUBJECT, display_name="Triết học", version=0, created_at=NOW, updated_at=NOW)


# --------------------------------------------------------------------------- #
# services/twin_batch.py: dấu vân tay R6
# --------------------------------------------------------------------------- #


def test_fingerprint_depends_on_the_answer_chunk_id() -> None:
    question = _question()
    moved = question.model_copy(
        update={"citation": question.citation.model_copy(update={"chunk_id": question.citation.chunk_id + "_khac"})}
    )
    assert moved.options == question.options and moved.form is question.form
    assert theory_fingerprint(moved) != theory_fingerprint(question)


def test_fingerprint_depends_on_the_form() -> None:
    question = _question()
    for form in TheoryQuestionForm:
        if form is question.form:
            continue
        reshaped = question.model_copy(update={"form": form})
        assert reshaped.options == question.options and reshaped.citation == question.citation
        assert theory_fingerprint(reshaped) != theory_fingerprint(question)


# --------------------------------------------------------------------------- #
# services/twin_batch.py: ticket đầu tiên, review_status, ngân sách seed
# --------------------------------------------------------------------------- #


def test_single_distinct_question_gives_one_ticket_naming_the_first_exhausted_twin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    question = _question()
    _same_question_every_call(monkeypatch, question)
    batch = _batch(_theory_blueprint("ch1"), IngestionStore(), count=3)
    assert [twin.slot_index for twin in batch.twins] == [1]
    [ticket] = batch.review_tickets
    assert ticket.slot_index == 1 and ticket.topic_key is None and ticket.attempts == []
    assert ticket.reason == THEORY_DUPLICATE_REASON.format(budget=SEED_REROLL_BUDGET, k=1)
    assert ticket.reason != THEORY_DUPLICATE_REASON.format(budget=SEED_REROLL_BUDGET, k=2)


def test_successful_theory_twins_are_auto_pass() -> None:
    batch = _philosophy_batch(2)
    assert batch.review_tickets == [] and len(batch.twins) == 4
    for twin in batch.twins:
        assert isinstance(twin, TheoryTwin)
        assert twin.review_status is ReviewStatus.AUTO_PASS
    assert {twin["review_status"] for twin in batch.model_dump(mode="json")["twins"]} == {"AUTO_PASS"}


def test_duplicate_twin_tries_exactly_the_reroll_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    question = _question()
    calls = _same_question_every_call(monkeypatch, question)
    batch = _batch(_theory_blueprint("ch1"), IngestionStore(), count=2)
    assert calls[0] == 1 + SEED_REROLL_BUDGET
    assert len(batch.twins) == 1
    assert len(batch.review_tickets) == 1
