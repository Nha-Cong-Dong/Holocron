"""Câu lý thuyết MCQ4 trong `POST /v1/blueprints/{id}/twins` (SPECIFICATION §6.3, ADR-007; brief B5a mục 1, 5, 6).

Chạy qua router trên ba loại kho (in-memory, SQLite `:memory:`, SQLite file):
- slot chương A không ra đáp án chương B;
- I2: mọi phương án nhiễu mang mã thuộc `THEORY_MISCONCEPTIONS`;
- I3: trích dẫn của đáp án và của từng phương án nhiễu là chuỗi con (NFC + gộp khoảng trắng) của chunk nguồn;
- I4: cùng `base_seed` ⇒ cùng batch; thêm cặp của môn khác không đổi `pool_sha256` hay câu sinh ra;
- R6: seed theo dải `7919·40`, không có câu trùng trong batch, hết dải ⇒ `ReviewTicket`;
- `format = ESSAY` ⇒ 422; replay cùng `Idempotency-Key` trả đúng batch cũ; mọi POST đều tăng `batch_count`;
  seed sát `2**63` không lỗi; `TwinBatch` trộn hai loại câu round-trip qua JSON.

Văn bản mẫu tự viết cho test (không phải tài liệu môn học có bản quyền).
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from holocron_core.domain.repositories import AppStore
from holocron_core.domain.schemas import (
    ExamBlueprint,
    KnowledgeChunk,
    QuestionFormat,
    TheorySlot,
    TwinQuestion,
    quote_in_text,
)
from holocron_core.domain.theory import THEORY_MISCONCEPTIONS, TheoryQuestion
from holocron_core.domain.twins import SEED_SPACE, TheoryTwin, TwinBatch, TwinBatchRequest
from holocron_core.infrastructure.memory_store import IngestionStore
from holocron_core.infrastructure.storage import SQLiteStore
from holocron_core.infrastructure.template_library import InMemoryTemplateRegistry
from holocron_core.routers.api import app, get_clock, get_store
from holocron_core.services.pipeline import SEED_REROLL_BUDGET, SEED_STRIDE
from holocron_core.services.theory_generator import GENERATOR_VERSION, pool_sha256
from holocron_core.services.twin_batch import (
    THEORY_SEED_BAND,
    NoCitationProvider,
    UnconfiguredTemplateAuthor,
    generate_twin_batch,
    theory_fingerprint,
    theory_seed,
)
from holocron_core.services.vault import create_concept_pair, ingest_confirmed_text
from tests.vault_support import add_document, confirmed, document_id

NOW = datetime(2026, 10, 28, 8, 0, tzinfo=UTC)
SUBJECT = "SSH1110"
OTHER_SUBJECT = "ET4010"
BASE_SEED = 20261028
STORE_KINDS = ("memory", "sqlite_memory", "sqlite_file")

CH1_DEFINITIONS = [
    ("Vật chất", "Vật chất là phạm trù triết học dùng để chỉ thực tại khách quan."),
    ("Ý thức", "Ý thức là sự phản ánh năng động, sáng tạo thế giới khách quan vào bộ óc con người."),
    ("Vận động", "Vận động là phương thức tồn tại của vật chất."),
    ("Không gian", "Không gian là hình thức tồn tại của vật chất xét về quảng tính."),
    ("Thời gian", "Thời gian là hình thức tồn tại của vật chất xét về độ dài diễn biến."),
]
CH2_DEFINITIONS = [
    ("Thực tiễn", "Thực tiễn là toàn bộ hoạt động vật chất có mục đích của con người."),
    ("Nhận thức", "Nhận thức là quá trình phản ánh hiện thực khách quan vào bộ óc con người."),
    ("Chân lý", "Chân lý là tri thức phù hợp với hiện thực khách quan."),
]
OTHER_SUBJECT_DEFINITIONS = [
    ("Cờ Z", "Cờ Z được đặt khi kết quả phép toán bằng không."),
    ("Cờ C", "Cờ C được đặt khi phép cộng có nhớ ra khỏi bit cao nhất."),
    ("Cờ N", "Cờ N được đặt khi bit cao nhất của kết quả bằng một."),
    ("Cờ V", "Cờ V được đặt khi phép toán có dấu bị tràn."),
]
# Nhóm nhỏ cho phép đếm số câu phân biệt: chương ch1 chỉ có một cặp (một đáp án, không dạng điền khuyết hay chọn định
# nghĩa nào khả thi), ba cặp chương ch2 làm phương án nhiễu cùng môn ⇒ đúng 4! = 24 câu phân biệt (thứ tự phương án).
SMALL_SUBJECT = "PHY1010"
SMALL_CH1 = [("Quán tính", "Quán tính là tính chất giữ nguyên trạng thái chuyển động của mọi vật.")]
SMALL_CH2 = [
    ("Gia tốc", "Gia tốc là đại lượng đo độ biến thiên vận tốc theo thời gian."),
    ("Động lượng", "Động lượng là tích của khối lượng và vận tốc."),
    ("Công suất", "Công suất là công thực hiện được trong một đơn vị thời gian."),
]
SMALL_DISTINCT = 24


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


def open_kind(kind: str, tmp_path: Path) -> AppStore:
    if kind == "memory":
        return IngestionStore()
    if kind == "sqlite_memory":
        return SQLiteStore.open(":memory:")
    return SQLiteStore.open(tmp_path / "holocron.sqlite3")


def close_kind(store: AppStore) -> None:
    if isinstance(store, SQLiteStore):
        store.close()


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


def add_chapter(
    store: AppStore, seed: str, subject: str, chapter: str, definitions: list[tuple[str, str]]
) -> KnowledgeChunk:
    """Một tài liệu một chương, một chunk chứa mọi định nghĩa, mỗi định nghĩa một cặp khái niệm đã xác nhận."""
    doc_id = document_id(seed)
    add_document(store, doc_id)
    lines = ["Mở đầu phần lý thuyết."] + [quote for _, quote in definitions]
    [chunk] = ingest_confirmed_text(store, confirmed(doc_id, [lines], subject_code=subject, chapter_code=chapter))
    for term, quote in definitions:
        create_concept_pair(store, chunk_id=chunk.chunk_id, term=term, definition_quote=quote, now=NOW)
    return chunk


def seed_philosophy(store: AppStore) -> dict[str, KnowledgeChunk]:
    """Hai chương của môn `SUBJECT`; trả chunk theo chương."""
    return {
        "ch1": add_chapter(store, "a", SUBJECT, "ch1", CH1_DEFINITIONS),
        "ch2": add_chapter(store, "b", SUBJECT, "ch2", CH2_DEFINITIONS),
    }


def theory_blueprint(client: TestClient, subject: str, chapters: list[str]) -> str:
    response = client.post(
        "/v1/blueprints",
        json={"subject_code": subject, "theory_slots": [{"chapter_code": chapter} for chapter in chapters]},
    )
    assert response.status_code == 201, response.text
    blueprint_id: str = response.json()["blueprint_id"]
    return blueprint_id


def post_twins(client: TestClient, blueprint_id: str, body: dict[str, Any], key: str | None = None) -> Any:
    headers = {"Idempotency-Key": key} if key is not None else {}
    return client.post(f"/v1/blueprints/{blueprint_id}/twins", json=body, headers=headers)


def theory_questions(body: dict[str, Any]) -> list[TheoryQuestion]:
    """Câu lý thuyết trong response, đọc lại qua union của `TwinBatch` (không đọc dict trần)."""
    batch = TwinBatch.model_validate(body)
    twins = [twin for twin in batch.twins if isinstance(twin, TheoryTwin)]
    assert len(twins) == len(batch.twins), "blueprint chỉ có slot lý thuyết"
    return [twin.question for twin in twins]


def stable(body: dict[str, Any]) -> dict[str, Any]:
    """Phần tất định của batch (I4): bỏ id và thời điểm, giữ câu và ticket."""
    return {
        "twins": [{k: v for k, v in twin.items() if k not in ("twin_id", "generated_at")} for twin in body["twins"]],
        "review_tickets": body["review_tickets"],
    }


def batch_count(client: TestClient, subject: str, blueprint_id: str) -> int:
    rows = client.get("/v1/blueprints", params={"subject_code": subject}).json()
    [row] = [row for row in rows if row["blueprint_id"] == blueprint_id]
    count: int = row["batch_count"]
    return count


# --------------------------------------------------------------------------- #
# Chương, I2, I3 qua router
# --------------------------------------------------------------------------- #


def test_theory_slot_of_chapter_a_never_answers_from_chapter_b(store: AppStore, clock: Clock) -> None:
    chunks = seed_philosophy(store)
    client = TestClient(app)
    blueprint_id = theory_blueprint(client, SUBJECT, ["ch2", "ch1"])
    response = post_twins(client, blueprint_id, {"count": 10, "base_seed": BASE_SEED})
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["review_tickets"] == []
    by_slot: dict[int, list[TheoryQuestion]] = {1: [], 2: []}
    for twin in TwinBatch.model_validate(body).twins:
        assert isinstance(twin, TheoryTwin)
        by_slot[twin.slot_index].append(twin.question)
    for slot_index, chapter in ((1, "ch2"), (2, "ch1")):
        assert len(by_slot[slot_index]) == 10
        for question in by_slot[slot_index]:
            assert question.chapter_code == chapter
            assert question.citation.chunk_id == chunks[chapter].chunk_id


def test_every_distractor_carries_a_theory_misconception_code_I2(store: AppStore, clock: Clock) -> None:
    seed_philosophy(store)
    client = TestClient(app)
    blueprint_id = theory_blueprint(client, SUBJECT, ["ch1", "ch2"])
    body = post_twins(client, blueprint_id, {"count": 10, "base_seed": BASE_SEED}).json()
    twins = body["twins"]
    assert len(twins) == 20
    for twin in twins:
        question = twin["question"]
        wrong = {key for key in ("A", "B", "C", "D") if key != question["correct_key"]}
        assert set(question["distractor_to_misconception_map"]) == wrong
        for ref in question["distractor_to_misconception_map"].values():
            assert ref["misconception_code"] in THEORY_MISCONCEPTIONS


def test_every_citation_is_verbatim_in_its_source_chunk_I3(store: AppStore, clock: Clock) -> None:
    seed_philosophy(store)
    client = TestClient(app)
    blueprint_id = theory_blueprint(client, SUBJECT, ["ch1", "ch2"])
    questions = theory_questions(post_twins(client, blueprint_id, {"count": 10, "base_seed": BASE_SEED}).json())
    assert len(questions) == 20
    for question in questions:
        for citation in [question.citation, *question.distractor_citations.values()]:
            assert citation.chunk_id is not None
            chunk = store.get_chunk(citation.chunk_id)
            assert chunk is not None and chunk.subject_code == SUBJECT
            assert quote_in_text(citation.verbatim_quote, chunk.raw_text)


# --------------------------------------------------------------------------- #
# I4 và R6
# --------------------------------------------------------------------------- #


def test_same_base_seed_gives_the_same_batch_I4(store: AppStore, clock: Clock) -> None:
    seed_philosophy(store)
    client = TestClient(app)
    blueprint_id = theory_blueprint(client, SUBJECT, ["ch1", "ch2"])
    first = post_twins(client, blueprint_id, {"count": 4, "base_seed": BASE_SEED}).json()
    second = post_twins(client, blueprint_id, {"count": 4, "base_seed": BASE_SEED}).json()
    assert first["batch_id"] != second["batch_id"]
    assert stable(first) == stable(second)
    other = post_twins(client, blueprint_id, {"count": 4, "base_seed": BASE_SEED + 1}).json()
    assert stable(other) != stable(first)


def test_pairs_of_another_subject_change_neither_pool_sha256_nor_questions_I4(store: AppStore, clock: Clock) -> None:
    seed_philosophy(store)
    client = TestClient(app)
    blueprint_id = theory_blueprint(client, SUBJECT, ["ch1"])
    before = post_twins(client, blueprint_id, {"count": 3, "base_seed": BASE_SEED}).json()
    pool_before = pool_sha256(store.list_concept_pairs(SUBJECT), SUBJECT)
    add_chapter(store, "c", OTHER_SUBJECT, "ch3", OTHER_SUBJECT_DEFINITIONS)
    after = post_twins(client, blueprint_id, {"count": 3, "base_seed": BASE_SEED}).json()
    assert pool_sha256([*store.list_concept_pairs(SUBJECT), *store.list_concept_pairs(OTHER_SUBJECT)], SUBJECT) == (
        pool_before
    )
    assert {question.pool_sha256 for question in theory_questions(after)} == {pool_before}
    assert stable(after) == stable(before)


def test_theory_seeds_follow_the_r6_bands() -> None:
    assert THEORY_SEED_BAND == SEED_STRIDE * SEED_REROLL_BUDGET == 7919 * 40
    assert theory_seed(BASE_SEED, 0, 0) == BASE_SEED
    assert theory_seed(BASE_SEED, 2, 5) == BASE_SEED + 2 * 7919 * 40 + 5 * 7919
    near = SEED_SPACE - 1
    assert theory_seed(near, 1, 0) == (near + 7919 * 40) % SEED_SPACE
    assert theory_seed(near, 0, 1) == (near + 7919) % SEED_SPACE


def test_twin_k_uses_a_seed_from_its_own_band(store: AppStore, clock: Clock) -> None:
    seed_philosophy(store)
    client = TestClient(app)
    blueprint_id = theory_blueprint(client, SUBJECT, ["ch1"])
    questions = theory_questions(post_twins(client, blueprint_id, {"count": 5, "base_seed": BASE_SEED}).json())
    for k, question in enumerate(questions):
        offset = question.seed - (BASE_SEED + k * THEORY_SEED_BAND)
        assert 0 <= offset < THEORY_SEED_BAND and offset % SEED_STRIDE == 0
        assert question.generator_version == GENERATOR_VERSION


def test_count_above_the_distinct_questions_gives_no_duplicate_and_a_ticket(store: AppStore, clock: Clock) -> None:
    add_chapter(store, "d", SMALL_SUBJECT, "ch1", SMALL_CH1)
    add_chapter(store, "e", SMALL_SUBJECT, "ch2", SMALL_CH2)
    client = TestClient(app)
    blueprint_id = theory_blueprint(client, SMALL_SUBJECT, ["ch1", "ch1", "ch1"])
    response = post_twins(client, blueprint_id, {"count": 10, "base_seed": BASE_SEED})
    assert response.status_code == 201, response.text
    body = response.json()
    questions = theory_questions(body)
    fingerprints = [theory_fingerprint(question) for question in questions]
    assert len(set(fingerprints)) == len(fingerprints), "không được sinh câu trùng trong batch"
    assert len(questions) <= SMALL_DISTINCT < 30
    assert body["review_tickets"], "thiếu câu phân biệt phải thành ReviewTicket"
    for ticket in body["review_tickets"]:
        assert ticket["topic_key"] is None
        assert "trong batch" in ticket["reason"]


def test_pool_without_confirmed_pairs_is_a_ticket_not_an_error(store: AppStore, clock: Clock) -> None:
    seed_philosophy(store)
    client = TestClient(app)
    blueprint_id = theory_blueprint(client, SUBJECT, ["ch9"])
    response = post_twins(client, blueprint_id, {"count": 3, "base_seed": BASE_SEED})
    assert response.status_code == 201, response.text
    assert response.json()["twins"] == []
    [ticket] = response.json()["review_tickets"]
    assert ticket["slot_index"] == 1 and ticket["topic_key"] is None
    assert "không đủ căn cứ trong Vault" in ticket["reason"]


def test_seed_close_to_2_pow_63_does_not_fail(store: AppStore, clock: Clock) -> None:
    seed_philosophy(store)
    client = TestClient(app)
    blueprint_id = theory_blueprint(client, SUBJECT, ["ch1"])
    response = post_twins(client, blueprint_id, {"count": 3, "base_seed": SEED_SPACE - 1})
    assert response.status_code == 201, response.text
    questions = theory_questions(response.json())
    assert len(questions) == 3
    assert all(0 <= question.seed < SEED_SPACE for question in questions)
    assert questions[1].seed < SEED_SPACE - 1, "dải của twin 1 quay vòng qua 0"


# --------------------------------------------------------------------------- #
# Request, idempotency, batch_count
# --------------------------------------------------------------------------- #


def test_essay_format_in_the_twin_request_is_422(store: AppStore, clock: Clock) -> None:
    seed_philosophy(store)
    client = TestClient(app)
    blueprint_id = theory_blueprint(client, SUBJECT, ["ch1"])
    assert post_twins(client, blueprint_id, {"count": 1, "format": "ESSAY"}).status_code == 422
    assert batch_count(client, SUBJECT, blueprint_id) == 0


def test_request_format_does_not_override_theory_slots(store: AppStore, clock: Clock) -> None:
    seed_philosophy(store)
    client = TestClient(app)
    blueprint_id = theory_blueprint(client, SUBJECT, ["ch1"])
    body = post_twins(client, blueprint_id, {"count": 2, "format": "NUMERICAL", "base_seed": BASE_SEED}).json()
    assert [twin["format"] for twin in body["twins"]] == ["MCQ4", "MCQ4"]
    assert body["review_tickets"] == []


def test_replaying_the_idempotency_key_returns_the_same_theory_batch(store: AppStore, clock: Clock) -> None:
    seed_philosophy(store)
    client = TestClient(app)
    blueprint_id = theory_blueprint(client, SUBJECT, ["ch1", "ch2"])
    first = post_twins(client, blueprint_id, {"count": 3, "base_seed": BASE_SEED}, key="key-theory")
    assert first.status_code == 201, first.text
    clock.now += timedelta(hours=23)
    second = post_twins(client, blueprint_id, {"count": 3, "base_seed": BASE_SEED}, key="key-theory")
    assert second.status_code == 201
    assert second.json() == first.json()
    assert batch_count(client, SUBJECT, blueprint_id) == 1


def test_key_reused_with_another_body_keeps_the_first_batch(store: AppStore, clock: Clock) -> None:
    """Chuỗi K+A, K+B, K+A: lần 3 trả đúng batch của lần 1 (§6.3)."""
    seed_philosophy(store)
    client = TestClient(app)
    blueprint_id = theory_blueprint(client, SUBJECT, ["ch1"])
    body_a = {"count": 2, "base_seed": BASE_SEED}
    body_b = {"count": 2, "base_seed": BASE_SEED + 7}
    first = post_twins(client, blueprint_id, body_a, key="K").json()
    other = post_twins(client, blueprint_id, body_b, key="K").json()
    third = post_twins(client, blueprint_id, body_a, key="K").json()
    assert other["batch_id"] != first["batch_id"]
    assert third == first
    assert batch_count(client, SUBJECT, blueprint_id) == 2


def test_post_without_idempotency_key_still_increments_batch_count(store: AppStore, clock: Clock) -> None:
    seed_philosophy(store)
    client = TestClient(app)
    blueprint_id = theory_blueprint(client, SUBJECT, ["ch1"])
    assert batch_count(client, SUBJECT, blueprint_id) == 0
    for expected in (1, 2):
        assert post_twins(client, blueprint_id, {"count": 1}).status_code == 201
        assert batch_count(client, SUBJECT, blueprint_id) == expected


def test_stored_theory_batch_reads_back_through_the_union(store: AppStore, clock: Clock) -> None:
    seed_philosophy(store)
    client = TestClient(app)
    blueprint_id = theory_blueprint(client, SUBJECT, ["ch1"])
    sent = post_twins(client, blueprint_id, {"count": 2, "base_seed": BASE_SEED}, key="read-back").json()
    record = store.get_idempotent(blueprint_id, "read-back", clock.now)
    assert record is not None
    assert all(isinstance(twin, TheoryTwin) for twin in record.batch.twins)
    assert record.batch.model_dump(mode="json") == sent


# --------------------------------------------------------------------------- #
# Model: union của TwinBatch
# --------------------------------------------------------------------------- #


def test_mixed_twin_batch_round_trips_through_json() -> None:
    store = IngestionStore()
    seed_philosophy(store)
    blueprint = ExamBlueprint(
        subject_code=SUBJECT,
        total_questions=1,
        slots=[TheorySlot(slot_index=1, chapter_code="ch1", format=QuestionFormat.MCQ4)],
    )
    theory = generate_twin_batch(
        blueprint,
        TwinBatchRequest(count=1, base_seed=BASE_SEED),
        author=UnconfiguredTemplateAuthor(),
        registry=InMemoryTemplateRegistry(),
        citations=NoCitationProvider(),
        vault=store,
    )
    computational = TwinQuestion.model_validate_json(
        (Path(__file__).parents[1] / "examples" / "twin_question_rr_mcq.json").read_text(encoding="utf-8")
    )
    mixed = theory.model_copy(update={"twins": [computational, *theory.twins]})
    restored = TwinBatch.model_validate_json(mixed.model_dump_json())
    assert restored == mixed
    assert isinstance(restored.twins[0], TwinQuestion) and isinstance(restored.twins[1], TheoryTwin)
    dumped = mixed.model_dump(mode="json")
    assert "question_kind" not in dumped["twins"][0]
    assert dumped["twins"][1]["question_kind"] == "theory"


# --------------------------------------------------------------------------- #
# TwinBatchRepository.save_batch: bốn trường hợp của khoá idempotency (§6.3)
# --------------------------------------------------------------------------- #


def _empty_batch(blueprint_id: str, at: datetime, base_seed: int = 1) -> TwinBatch:
    return TwinBatch(blueprint_id=blueprint_id, base_seed=base_seed, twins=[], review_tickets=[], created_at=at)


def _saved_theory_blueprint(store: AppStore) -> str:
    blueprint = ExamBlueprint(
        blueprint_id="bp_repo_1",
        subject_code=SUBJECT,
        total_questions=1,
        slots=[TheorySlot(slot_index=1, chapter_code="ch1", format=QuestionFormat.MCQ4)],
        created_at=NOW,
    )
    store.save_blueprint(blueprint)
    return blueprint.blueprint_id


def _batch_count(store: AppStore, blueprint_id: str) -> int:
    [summary] = [row for row in store.list_blueprint_summaries(SUBJECT) if row.blueprint_id == blueprint_id]
    return summary.batch_count


def test_save_batch_without_key_always_writes(store: AppStore) -> None:
    blueprint_id = _saved_theory_blueprint(store)
    first = store.save_batch(_empty_batch(blueprint_id, NOW), None, "d1")
    second = store.save_batch(_empty_batch(blueprint_id, NOW), None, "d1")
    assert first.batch_id != second.batch_id
    assert _batch_count(store, blueprint_id) == 2


def test_save_batch_replay_with_the_same_body_returns_the_stored_batch(store: AppStore) -> None:
    blueprint_id = _saved_theory_blueprint(store)
    first = store.save_batch(_empty_batch(blueprint_id, NOW), "K", "A")
    replay = store.save_batch(_empty_batch(blueprint_id, NOW + timedelta(hours=24), base_seed=2), "K", "A")
    assert replay == first
    assert _batch_count(store, blueprint_id) == 1


def test_save_batch_k_a_then_k_b_then_k_a_returns_the_first_batch(store: AppStore) -> None:
    blueprint_id = _saved_theory_blueprint(store)
    first = store.save_batch(_empty_batch(blueprint_id, NOW), "K", "A")
    other = store.save_batch(_empty_batch(blueprint_id, NOW + timedelta(minutes=1), base_seed=2), "K", "B")
    assert other.batch_id != first.batch_id and other.base_seed == 2
    third = store.save_batch(_empty_batch(blueprint_id, NOW + timedelta(minutes=2), base_seed=3), "K", "A")
    assert third == first
    record = store.get_idempotent(blueprint_id, "K", NOW + timedelta(minutes=3))
    assert record is not None and record.batch == first and record.body_digest == "A"
    assert _batch_count(store, blueprint_id) == 2


def test_save_batch_after_the_ttl_reuses_the_key_and_keeps_the_old_batch(store: AppStore) -> None:
    blueprint_id = _saved_theory_blueprint(store)
    first = store.save_batch(_empty_batch(blueprint_id, NOW), "K", "A")
    later = NOW + timedelta(hours=24, seconds=1)
    fresh = store.save_batch(_empty_batch(blueprint_id, later, base_seed=2), "K", "A")
    assert fresh.batch_id != first.batch_id
    record = store.get_idempotent(blueprint_id, "K", later)
    assert record is not None and record.batch == fresh
    assert _batch_count(store, blueprint_id) == 2
