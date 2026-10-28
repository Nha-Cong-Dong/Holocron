"""Vault không nhất quán trong slot lý thuyết thành `ReviewTicket`, không làm hỏng batch (SPECIFICATION §6.3).

Hai lần đọc của `generate_twin_batch` (cặp khái niệm, rồi chunk) không cùng snapshot, nên có thể gặp:
- cặp đã xác nhận mà chunk nguồn không có trong lần đọc chunk ⇒ ticket trước khi gọi bộ sinh;
- trích dẫn không còn khớp nguyên văn chunk (`QuoteMismatchError`, I3) ⇒ ticket, giữ các twin đã sinh của slot.

Lý do ticket là chuỗi cố định, không chép topic, `chunk_id` hay trích dẫn. Lỗi khác của bộ sinh vẫn nổi lên. Slot khác
(lý thuyết hay tính toán) vẫn sinh. Văn bản mẫu tự viết cho test (không phải tài liệu môn học có bản quyền).
"""

from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from holocron_core.domain.repositories import AppStore
from holocron_core.domain.schemas import (
    BlueprintSlot,
    ExamBlueprint,
    KnowledgeChunk,
    QuestionFormat,
    TheorySlot,
    TwinQuestion,
)
from holocron_core.domain.theory import TheoryQuestion
from holocron_core.domain.twins import ReviewTicket, TheoryTwin, TwinBatch, TwinBatchRequest
from holocron_core.domain.vault import QuoteMismatchError
from holocron_core.infrastructure.memory_store import IngestionStore
from holocron_core.infrastructure.template_library import InMemoryTemplateRegistry
from holocron_core.routers.api import app, get_clock, get_store
from holocron_core.services import twin_batch
from holocron_core.services.twin_batch import (
    THEORY_MISSING_CHUNK_REASON,
    THEORY_QUOTE_MISMATCH_REASON,
    NoCitationProvider,
    UnconfiguredTemplateAuthor,
    generate_twin_batch,
)
from tests.test_theory_twins import (
    BASE_SEED,
    CH1_DEFINITIONS,
    STORE_KINDS,
    SUBJECT,
    Clock,
    close_kind,
    open_kind,
    post_twins,
    seed_philosophy,
    theory_blueprint,
)
from tests.test_twin_batch import FixedCitations

GeneratorFn = Callable[..., TheoryQuestion | ReviewTicket]


@pytest.fixture(params=STORE_KINDS)
def store(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[AppStore]:
    opened = open_kind(str(request.param), tmp_path)
    app.dependency_overrides[get_store] = lambda: opened
    yield opened
    app.dependency_overrides.clear()
    close_kind(opened)


@pytest.fixture
def clock() -> Clock:
    fake = Clock()
    app.dependency_overrides[get_clock] = lambda: fake
    return fake


def _drop_chunk(store: AppStore, monkeypatch: pytest.MonkeyPatch, chunk_id: str) -> None:
    """Lần đọc chunk không còn thấy `chunk_id` (xoá giữa hai lần đọc của batch)."""
    original = store.list_chunks

    def listed(*args: Any, **kwargs: Any) -> list[KnowledgeChunk]:
        return [chunk for chunk in original(*args, **kwargs) if chunk.chunk_id != chunk_id]

    monkeypatch.setattr(store, "list_chunks", listed)


def _rewrite_chunk(store: AppStore, monkeypatch: pytest.MonkeyPatch, chunk_id: str) -> None:
    """Lần đọc chunk thấy `chunk_id` với văn bản đã đổi: trích dẫn của các cặp không còn khớp nguyên văn."""
    original = store.list_chunks

    def listed(*args: Any, **kwargs: Any) -> list[KnowledgeChunk]:
        return [
            chunk.model_copy(update={"raw_text": "Văn bản đã được biên tập lại hoàn toàn."})
            if chunk.chunk_id == chunk_id
            else chunk
            for chunk in original(*args, **kwargs)
        ]

    monkeypatch.setattr(store, "list_chunks", listed)


def _assert_fixed_reason(ticket: dict[str, Any], reason: str, leaked: Sequence[str]) -> None:
    assert ticket["reason"] == reason
    assert ticket["topic_key"] is None and ticket["attempts"] == []
    for text in leaked:
        assert text not in ticket["reason"]


def _theory_blueprint(*chapters: str) -> ExamBlueprint:
    slots = [
        TheorySlot(slot_index=index, chapter_code=chapter, format=QuestionFormat.MCQ4)
        for index, chapter in enumerate(chapters, start=1)
    ]
    return ExamBlueprint(subject_code=SUBJECT, total_questions=len(slots), slots=slots)


def _batch(blueprint: ExamBlueprint, vault: IngestionStore, count: int) -> TwinBatch:
    return generate_twin_batch(
        blueprint,
        TwinBatchRequest(count=count, base_seed=BASE_SEED),
        author=UnconfiguredTemplateAuthor(),
        registry=InMemoryTemplateRegistry(),
        citations=NoCitationProvider(),
        vault=vault,
    )


def _failing_on_call(monkeypatch: pytest.MonkeyPatch, call: int, error: Exception) -> None:
    """Lần gọi thứ `call` (đếm từ 1) của bộ sinh trong `twin_batch` ném `error`; các lần khác gọi bộ sinh thật."""
    real: GeneratorFn = twin_batch.generate_theory_question
    calls = [0]

    def generator(*args: Any, **kwargs: Any) -> TheoryQuestion | ReviewTicket:
        calls[0] += 1
        if calls[0] == call:
            raise error
        return real(*args, **kwargs)

    monkeypatch.setattr(twin_batch, "generate_theory_question", generator)


# --------------------------------------------------------------------------- #
# Qua router, ba loại kho
# --------------------------------------------------------------------------- #


def test_missing_source_chunk_is_a_ticket_not_a_500(
    store: AppStore, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    chunks = seed_philosophy(store)
    client = TestClient(app, raise_server_exceptions=False)
    blueprint_id = theory_blueprint(client, SUBJECT, ["ch1", "ch2"])
    missing = chunks["ch2"].chunk_id
    _drop_chunk(store, monkeypatch, missing)
    response = post_twins(client, blueprint_id, {"count": 2, "base_seed": BASE_SEED})
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["twins"] == []
    assert [ticket["slot_index"] for ticket in body["review_tickets"]] == [1, 2]
    for ticket in body["review_tickets"]:
        _assert_fixed_reason(ticket, THEORY_MISSING_CHUNK_REASON, [missing, "ch1", "ch2"])


def test_quote_mismatch_is_a_ticket_not_a_500(store: AppStore, clock: Clock, monkeypatch: pytest.MonkeyPatch) -> None:
    chunks = seed_philosophy(store)
    client = TestClient(app, raise_server_exceptions=False)
    blueprint_id = theory_blueprint(client, SUBJECT, ["ch1"])
    rewritten = chunks["ch1"].chunk_id
    _rewrite_chunk(store, monkeypatch, rewritten)
    response = post_twins(client, blueprint_id, {"count": 3, "base_seed": BASE_SEED})
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["twins"] == [], "đáp án của slot ch1 luôn trích từ chunk ch1 đã đổi"
    [ticket] = body["review_tickets"]
    assert ticket["slot_index"] == 1
    _assert_fixed_reason(ticket, THEORY_QUOTE_MISMATCH_REASON, [rewritten, *(quote for _, quote in CH1_DEFINITIONS)])


# --------------------------------------------------------------------------- #
# Service: giữ twin đã sinh, slot khác vẫn sinh, lỗi khác không bị nuốt
# --------------------------------------------------------------------------- #


def test_quote_mismatch_at_twin_k_keeps_earlier_twins_and_other_slots(monkeypatch: pytest.MonkeyPatch) -> None:
    vault = IngestionStore()
    seed_philosophy(vault)
    blueprint = _theory_blueprint("ch1", "ch2")
    expected = _batch(blueprint, vault, count=2)
    assert len(expected.twins) == 4 and expected.review_tickets == []

    _failing_on_call(monkeypatch, call=2, error=QuoteMismatchError("chk_hong"))
    batch = _batch(blueprint, vault, count=2)
    theory = [twin for twin in batch.twins if isinstance(twin, TheoryTwin)]
    assert [twin.slot_index for twin in theory] == [1, 2, 2]
    expected_questions = [twin.question for twin in expected.twins if isinstance(twin, TheoryTwin)]
    assert [twin.question for twin in theory] == [expected_questions[0], *expected_questions[2:]]
    [ticket] = batch.review_tickets
    assert ticket.slot_index == 1 and ticket.attempts == []
    assert ticket.reason == THEORY_QUOTE_MISMATCH_REASON and "chk_hong" not in ticket.reason


@pytest.mark.parametrize("error", [ValueError("seed phải trong [0, 2**63)"), LookupError("khác")])
def test_other_generator_errors_are_not_swallowed(monkeypatch: pytest.MonkeyPatch, error: Exception) -> None:
    vault = IngestionStore()
    seed_philosophy(vault)
    _failing_on_call(monkeypatch, call=1, error=error)
    with pytest.raises(type(error)):
        _batch(_theory_blueprint("ch1"), vault, count=1)


def test_missing_chunk_ticket_leaves_computational_slot_generating(
    monkeypatch: pytest.MonkeyPatch, rr_slot: BlueprintSlot, theory_chunk: KnowledgeChunk
) -> None:
    vault = IngestionStore()
    chunks = seed_philosophy(vault)
    _drop_chunk(vault, monkeypatch, chunks["ch1"].chunk_id)
    blueprint = ExamBlueprint(
        source_exam_doc_id="doc_exam_1",
        subject_code=SUBJECT,
        total_questions=2,
        slots=[
            rr_slot.model_copy(update={"slot_index": 1}),
            TheorySlot(slot_index=2, chapter_code="ch2", format=QuestionFormat.MCQ4),
        ],
    )
    batch = generate_twin_batch(
        blueprint,
        TwinBatchRequest(count=1, base_seed=BASE_SEED),
        author=UnconfiguredTemplateAuthor(),
        registry=InMemoryTemplateRegistry(),
        citations=FixedCitations(theory_chunk.to_citation()),
        vault=vault,
    )
    [computational] = batch.twins
    assert isinstance(computational, TwinQuestion) and computational.slot_index == 1
    [ticket] = batch.review_tickets
    assert ticket.slot_index == 2 and ticket.reason == THEORY_MISSING_CHUNK_REASON
