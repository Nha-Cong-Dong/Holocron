"""Lượt làm bài và câu sai để ôn (SPECIFICATION §5.17, §6.12, §5.13; ADR-004 PRAC-001).

Mọi test chạy trên kho in-memory và SQLite (tham số hoá): đúng/sai do backend tính (I1), thứ tự lỗi của `POST`,
giới hạn và phân trang, luật "câu sai để ôn", xoá và cascade, câu ôn trả nguyên bản chụp twin (I2, I3).
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from holocron_core.domain.practice import (
    PracticeAnswer,
    PracticeAttempt,
    PracticeAttemptSummary,
    build_attempt,
    twin_correct_key,
)
from holocron_core.domain.repositories import AppStore, MissingReferenceError, StorageError
from holocron_core.domain.schemas import (
    BlueprintSlot,
    Citation,
    ExamBlueprint,
    MisconceptionRef,
    OptionKey,
    ReviewStatus,
    TwinQuestion,
)
from holocron_core.domain.theory import THEORY_SAME_CHAPTER_CONCEPT, TheoryQuestion, TheoryQuestionForm
from holocron_core.domain.twins import TheoryTwin, TwinBatch
from holocron_core.infrastructure.memory_store import IngestionStore
from holocron_core.infrastructure.storage import SQLiteStore
from holocron_core.infrastructure.storage.connection import connect
from holocron_core.routers.api import app
from holocron_core.routers.deps import get_clock, get_store

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
T0 = datetime(2026, 11, 4, 8, 0, tzinfo=UTC)
SUBJECT = "IT3070"
OTHER_SUBJECT = "ET4010"
STORE_KINDS = ["memory", "sqlite_memory"]
URL = "/v1/practice/attempts"
REVIEW_URL = "/v1/practice/review-questions"

BP_MAIN = "bp_practice_main"
BP_SECOND = "bp_practice_second"
BP_OTHER = "bp_practice_other"
BATCH_MAIN = "tb_00000000000000a1"
BATCH_SECOND = "tb_00000000000000a2"
BATCH_OTHER = "tb_00000000000000b1"
BATCH_UNKNOWN = "tb_00000000000000ff"

RR = "twin_rr_mcq"  # câu tính toán MCQ4, đáp án B
NUM = "twin_rr_numerical"  # câu NUMERICAL: chưa ghi lượt được
TH1 = "twin_theory_1"  # đáp án A
TH2 = "twin_theory_2"  # đáp án C
TH_SECOND = "twin_theory_second"  # batch thứ hai cùng môn, đáp án B
TH_OTHER = "twin_theory_other"  # môn khác, đáp án D


class Clock:
    """Đồng hồ giả: mỗi lần gọi tiến 1 phút (thứ tự lượt vẫn theo `seq`, không theo đồng hồ)."""

    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        self.now += timedelta(minutes=1)
        return self.now


# --------------------------------------------------------------------------- #
# Dữ liệu mẫu
# --------------------------------------------------------------------------- #


def _blueprint(rr_slot: BlueprintSlot, blueprint_id: str, subject_code: str) -> ExamBlueprint:
    return ExamBlueprint(
        blueprint_id=blueprint_id,
        source_exam_doc_id="doc_exam_practice",
        subject_code=subject_code,
        total_questions=1,
        slots=[rr_slot.model_copy(update={"slot_index": 1})],
        created_at=T0,
    )


def _computational(name: str, twin_id: str, blueprint_id: str, slot_index: int) -> TwinQuestion:
    twin = TwinQuestion.model_validate_json((EXAMPLES / name).read_text(encoding="utf-8"))
    return twin.model_copy(update={"twin_id": twin_id, "blueprint_id": blueprint_id, "slot_index": slot_index})


def _citation(page: int, quote: str) -> Citation:
    return Citation(doc_id="doc_practice_source", page_number=page, verbatim_quote=quote, chunk_id=f"chk_p{page}")


def _theory(twin_id: str, blueprint_id: str, slot_index: int, correct: OptionKey, subject_code: str) -> TheoryTwin:
    terms = {OptionKey.A: "Tiến trình", OptionKey.B: "Luồng", OptionKey.C: "Semaphore", OptionKey.D: "Bộ nhớ ảo"}
    quotes = {
        OptionKey.A: "Tiến trình là một chương trình đang thực thi.",
        OptionKey.B: "Luồng là đơn vị cơ bản được cấp CPU.",
        OptionKey.C: "Semaphore là biến nguyên dùng để đồng bộ.",
        OptionKey.D: "Bộ nhớ ảo tách bộ nhớ logic khỏi bộ nhớ vật lý.",
    }
    wrong = [key for key in OptionKey if key is not correct]
    question = TheoryQuestion(
        form=TheoryQuestionForm.CONCEPT_MATCH,
        subject_code=subject_code,
        chapter_code="ch1",
        seed=42,
        generator_version="theory-v1",
        pool_sha256="0" * 64,
        stem=f"Khái niệm nào ứng với định nghĩa sau: {quotes[correct]}",
        options=terms,
        correct_key=correct,
        distractor_to_misconception_map={
            key: MisconceptionRef(
                misconception_code=THEORY_SAME_CHAPTER_CONCEPT,
                label_vi="Nhầm khái niệm cùng chương",
                rationale_vi="Phương án là một khái niệm khác của cùng chương.",
            )
            for key in wrong
        },
        distractor_citations={key: _citation(index + 2, quotes[key]) for index, key in enumerate(wrong)},
        solution_explanation_vi="Định nghĩa trích nguyên văn từ tài liệu nguồn.",
        citation=_citation(1, quotes[correct]),
    )
    return TheoryTwin(
        twin_id=twin_id,
        blueprint_id=blueprint_id,
        slot_index=slot_index,
        question=question,
        review_status=ReviewStatus.AUTO_PASS,
        generated_at=T0,
    )


def _seed(store: AppStore, rr_slot: BlueprintSlot) -> dict[str, Any]:
    """Ba blueprint (hai cùng `SUBJECT`, một môn khác), mỗi blueprint một batch; trả twin theo id."""
    batches = [
        TwinBatch(
            batch_id=BATCH_MAIN,
            blueprint_id=BP_MAIN,
            base_seed=1,
            twins=[
                _computational("twin_question_rr_mcq.json", RR, BP_MAIN, 1),
                _theory(TH1, BP_MAIN, 1, OptionKey.A, SUBJECT),
                _theory(TH2, BP_MAIN, 1, OptionKey.C, SUBJECT),
                _computational("twin_question_rr_numerical.json", NUM, BP_MAIN, 1),
            ],
            review_tickets=[],
            created_at=T0,
        ),
        TwinBatch(
            batch_id=BATCH_SECOND,
            blueprint_id=BP_SECOND,
            base_seed=2,
            twins=[_theory(TH_SECOND, BP_SECOND, 1, OptionKey.B, SUBJECT)],
            review_tickets=[],
            created_at=T0,
        ),
        TwinBatch(
            batch_id=BATCH_OTHER,
            blueprint_id=BP_OTHER,
            base_seed=3,
            twins=[_theory(TH_OTHER, BP_OTHER, 1, OptionKey.D, OTHER_SUBJECT)],
            review_tickets=[],
            created_at=T0,
        ),
    ]
    store.save_blueprint(_blueprint(rr_slot, BP_MAIN, SUBJECT))
    store.save_blueprint(_blueprint(rr_slot, BP_SECOND, SUBJECT))
    store.save_blueprint(_blueprint(rr_slot, BP_OTHER, OTHER_SUBJECT))
    for batch in batches:
        store.save_batch(batch, None, f"digest-{batch.batch_id}")
    return {twin.twin_id: twin for batch in batches for twin in batch.twins}


def _open(kind: str, tmp_path: Path) -> AppStore:
    if kind == "memory":
        return IngestionStore()
    if kind == "sqlite_memory":
        return SQLiteStore.open(":memory:")
    return SQLiteStore.open(tmp_path / "holocron.sqlite3")


@pytest.fixture(params=STORE_KINDS)
def store(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[AppStore]:
    opened = _open(str(request.param), tmp_path)
    clock = Clock()
    app.dependency_overrides[get_store] = lambda: opened
    app.dependency_overrides[get_clock] = lambda: clock
    yield opened
    app.dependency_overrides.clear()
    if isinstance(opened, SQLiteStore):
        opened.close()


@pytest.fixture
def twins(store: AppStore, rr_slot: BlueprintSlot) -> dict[str, Any]:
    return _seed(store, rr_slot)


@pytest.fixture
def client(twins: dict[str, Any]) -> TestClient:
    return TestClient(app)


def _answers(*pairs: tuple[str, str | None]) -> list[dict[str, Any]]:
    return [{"twin_id": twin_id, "chosen_key": key} for twin_id, key in pairs]


def _post(client: TestClient, mode: str, *pairs: tuple[str, str | None], batch_id: str | None = None) -> Any:
    body: dict[str, Any] = {"mode": mode, "answers": _answers(*pairs)}
    if batch_id is not None:
        body["batch_id"] = batch_id
    return client.post(URL, json=body)


def _ok(client: TestClient, mode: str, *pairs: tuple[str, str | None], batch_id: str | None = None) -> Any:
    response = _post(client, mode, *pairs, batch_id=batch_id)
    assert response.status_code == 201, response.text
    return response.json()


def _review(client: TestClient, subject_code: str = SUBJECT, **params: int) -> Any:
    response = client.get(REVIEW_URL, params={"subject_code": subject_code, **params})
    assert response.status_code == 200, response.text
    return response.json()


def _review_ids(client: TestClient, subject_code: str = SUBJECT) -> list[str]:
    return [question["twin"]["twin_id"] for question in _review(client, subject_code)["questions"]]


# --------------------------------------------------------------------------- #
# Đúng/sai do backend tính (I1)
# --------------------------------------------------------------------------- #


def test_outcome_and_counts_are_computed_by_the_backend(client: TestClient) -> None:
    response = _post(client, "batch", (RR, "B"), (TH1, "D"), (TH2, None), batch_id=BATCH_MAIN)
    assert response.status_code == 201
    body = response.json()
    assert response.headers["Location"] == f"{URL}/{body['attempt_id']}"
    assert body["subject_code"] == SUBJECT and body["blueprint_id"] == BP_MAIN and body["batch_id"] == BATCH_MAIN
    assert [(a["ordinal"], a["correct_key"], a["outcome"]) for a in body["answers"]] == [
        (0, "B", "correct"),
        (1, "A", "incorrect"),
        (2, "C", "skipped"),
    ]
    assert (body["total_count"], body["correct_count"], body["incorrect_count"], body["skipped_count"]) == (3, 1, 1, 1)
    assert body["submitted_at"] == (T0 + timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
    assert client.get(f"{URL}/{body['attempt_id']}").json() == body


def test_null_chosen_key_is_skipped(client: TestClient) -> None:
    body = _ok(client, "review", (TH1, None))
    assert body["answers"][0]["outcome"] == "skipped" and body["skipped_count"] == 1
    assert body["batch_id"] is None and body["blueprint_id"] is None


@pytest.mark.parametrize(
    "extra",
    [{"outcome": "correct"}, {"correct_key": "A"}, {"is_correct": True}, {"score": 1}],
)
def test_client_cannot_send_a_result_per_answer(client: TestClient, extra: dict[str, Any]) -> None:
    answer = {"twin_id": TH1, "chosen_key": "D", **extra}
    response = client.post(URL, json={"mode": "review", "answers": [answer]})
    assert response.status_code == 422
    assert client.get(URL, params={"subject_code": SUBJECT}).json()["total"] == 0


@pytest.mark.parametrize("extra", [{"correct_count": 3}, {"total_count": 1}, {"submitted_at": "2026-01-01T00:00:00Z"}])
def test_client_cannot_send_counts_or_time(client: TestClient, extra: dict[str, Any]) -> None:
    response = client.post(URL, json={"mode": "review", "answers": _answers((TH1, "A")), **extra})
    assert response.status_code == 422


def test_correct_key_is_read_from_the_stored_twin(twins: dict[str, Any]) -> None:
    assert twin_correct_key(twins[RR]) is OptionKey.B
    assert twin_correct_key(twins[TH2]) is OptionKey.C
    assert twin_correct_key(twins[NUM]) is None


# --------------------------------------------------------------------------- #
# POST: body sai (bước 1) và thứ tự lỗi (bước 2–7)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "body",
    [
        {"mode": "batch", "answers": _answers((RR, "A"))},  # thiếu batch_id
        {"mode": "review", "batch_id": BATCH_MAIN, "answers": _answers((RR, "A"))},  # review có batch_id
        {"mode": "review", "answers": _answers((RR, "A"), (RR, "B"))},  # twin_id trùng
        {"mode": "review", "answers": []},
        {"mode": "review", "answers": _answers(*[(f"twin_{i}", "A") for i in range(501)])},
        {"mode": "exam", "answers": _answers((RR, "A"))},
        {"mode": "batch", "batch_id": "batch_1", "answers": _answers((RR, "A"))},
        {"mode": "review", "answers": _answers((RR, "E"))},
        {"mode": "review", "answers": [{"chosen_key": "A"}]},
    ],
)
def test_invalid_body_is_422(client: TestClient, body: dict[str, Any]) -> None:
    assert client.post(URL, json=body).status_code == 422


def test_500_answers_pass_body_validation(client: TestClient) -> None:
    pairs = [(f"twin_missing_{i}", "A") for i in range(500)]
    response = _post(client, "review", *pairs)
    assert response.status_code == 404  # qua bước 1, dừng ở bước 3


def test_unknown_batch_is_404_before_unknown_twin(client: TestClient) -> None:
    response = _post(client, "batch", ("twin_nope", "A"), batch_id=BATCH_UNKNOWN)
    assert response.status_code == 404 and response.json()["detail"] == "không tìm thấy batch"


def test_unknown_twin_is_404_listing_positions(client: TestClient) -> None:
    response = _post(client, "review", (TH1, "A"), ("twin_nope", "A"), (NUM, None), ("twin_gone", "B"))
    assert response.status_code == 404
    detail = response.json()["detail"]
    assert "answers[1]" in detail and "answers[3]" in detail
    assert "answers[0]" not in detail and "answers[2]" not in detail


def test_twin_outside_the_batch_is_422_before_format(client: TestClient) -> None:
    response = _post(client, "batch", (NUM, None), (TH_SECOND, "B"), batch_id=BATCH_MAIN)
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert BATCH_MAIN in detail and "answers[1]" in detail and "hình thức" not in detail


def test_numerical_twin_is_422(client: TestClient) -> None:
    response = _post(client, "batch", (RR, "A"), (NUM, None), batch_id=BATCH_MAIN)
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail.startswith("hình thức câu này chưa ghi lượt làm bài được") and "answers[1]" in detail


def test_numerical_twin_is_422_before_subject_mix(client: TestClient) -> None:
    response = _post(client, "review", (TH_OTHER, "D"), (NUM, None))
    assert response.status_code == 422 and response.json()["detail"].startswith("hình thức câu này")


def test_twins_of_two_subjects_are_422(client: TestClient) -> None:
    response = _post(client, "review", (TH1, "A"), (TH_OTHER, "D"))
    assert response.status_code == 422
    assert SUBJECT in response.json()["detail"] and OTHER_SUBJECT in response.json()["detail"]
    for subject_code in (SUBJECT, OTHER_SUBJECT):
        assert client.get(URL, params={"subject_code": subject_code}).json()["total"] == 0


def test_partial_batch_is_accepted(client: TestClient) -> None:
    body = _ok(client, "batch", (TH2, "C"), batch_id=BATCH_MAIN)
    assert body["total_count"] == 1 and body["answers"][0]["slot_index"] == 1


def test_twin_removed_between_lookup_and_write_is_404(
    client: TestClient, store: AppStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    def vanish(attempt: PracticeAttempt) -> PracticeAttempt:
        raise MissingReferenceError(f"lượt {attempt.attempt_id} trỏ tới batch hoặc twin không còn")

    monkeypatch.setattr(store, "save_practice_attempt", vanish)
    assert _post(client, "review", (TH1, "A")).status_code == 404


# --------------------------------------------------------------------------- #
# Liệt kê, đọc, phân trang
# --------------------------------------------------------------------------- #


def test_list_is_newest_first_and_paginated(client: TestClient) -> None:
    first = _ok(client, "batch", (RR, "A"), batch_id=BATCH_MAIN)
    second = _ok(client, "review", (TH1, "A"), (TH2, "A"))
    third = _ok(client, "review", (TH_SECOND, None))
    _ok(client, "review", (TH_OTHER, "D"))
    page = client.get(URL, params={"subject_code": SUBJECT}).json()
    assert page["total"] == 3
    assert [a["attempt_id"] for a in page["attempts"]] == [
        third["attempt_id"],
        second["attempt_id"],
        first["attempt_id"],
    ]
    assert "answers" not in page["attempts"][0]
    assert (page["attempts"][1]["correct_count"], page["attempts"][1]["incorrect_count"]) == (1, 1)
    sliced = client.get(URL, params={"subject_code": SUBJECT, "offset": 1, "limit": 1}).json()
    assert sliced["total"] == 3 and [a["attempt_id"] for a in sliced["attempts"]] == [second["attempt_id"]]
    assert client.get(URL, params={"subject_code": SUBJECT, "offset": 5}).json()["attempts"] == []


@pytest.mark.parametrize("url", [URL, REVIEW_URL])
@pytest.mark.parametrize(
    "params",
    [
        {},
        {"subject_code": "it3070"},
        {"subject_code": SUBJECT, "limit": 0},
        {"subject_code": SUBJECT, "limit": 201},
        {"subject_code": SUBJECT, "offset": -1},
    ],
)
def test_list_query_limits_are_422(client: TestClient, url: str, params: dict[str, Any]) -> None:
    assert client.get(url, params=params).status_code == 422


@pytest.mark.parametrize("url", [URL, REVIEW_URL])
def test_limit_200_is_accepted(client: TestClient, url: str) -> None:
    assert client.get(url, params={"subject_code": SUBJECT, "limit": 200}).status_code == 200


def test_unknown_attempt_is_404(client: TestClient) -> None:
    assert client.get(f"{URL}/pa_0000000000000000").status_code == 404
    assert client.delete(f"{URL}/pa_0000000000000000").status_code == 404


# --------------------------------------------------------------------------- #
# Luật "câu sai để ôn" (§5.17)
# --------------------------------------------------------------------------- #


def test_wrong_then_right_leaves_the_review_list_and_skip_changes_nothing(client: TestClient) -> None:
    first = _ok(client, "batch", (RR, "A"), (TH1, "B"), (TH2, None), batch_id=BATCH_MAIN)
    assert _review_ids(client) == [RR, TH1]  # cùng lượt: ordinal tăng dần
    _ok(client, "review", (TH1, "A"), (RR, None))  # TH1 làm đúng ⇒ gỡ; RR bỏ qua ⇒ giữ nguyên
    review = _review(client)
    assert review["total"] == 1
    only = review["questions"][0]
    assert only["twin"]["twin_id"] == RR and only["last_attempt_id"] == first["attempt_id"]
    assert only["last_chosen_key"] == "A" and only["batch_id"] == BATCH_MAIN
    assert only["last_answered_at"] == first["submitted_at"]
    third = _ok(client, "review", (TH2, "D"))
    assert _review_ids(client) == [TH2, RR]  # lượt mới nhất trước
    _ok(client, "review", (TH2, None))  # bỏ qua: không gỡ
    assert _review_ids(client) == [TH2, RR]
    assert _review(client)["questions"][0]["last_attempt_id"] == third["attempt_id"]
    _ok(client, "batch", (RR, "B"), batch_id=BATCH_MAIN)
    _ok(client, "review", (TH1, "C"))  # đúng rồi sai lại ⇒ vào lại danh sách
    assert _review_ids(client) == [TH1, TH2]


def test_skipped_only_twin_never_enters_the_review_list(client: TestClient) -> None:
    _ok(client, "review", (TH1, None), (TH2, None))
    assert _review(client) == {"total": 0, "questions": []}


def test_review_list_is_per_subject_and_paginated(client: TestClient) -> None:
    _ok(client, "review", (TH1, "B"), (TH2, "A"), (TH_SECOND, "A"))
    _ok(client, "review", (TH_OTHER, "A"))
    assert _review_ids(client, OTHER_SUBJECT) == [TH_OTHER]
    page = _review(client, offset=1, limit=1)
    assert page["total"] == 3 and [q["twin"]["twin_id"] for q in page["questions"]] == [TH2]


def test_review_question_is_the_stored_twin_snapshot(client: TestClient, twins: dict[str, Any]) -> None:
    _ok(client, "batch", (RR, "D"), (TH1, "C"), batch_id=BATCH_MAIN)
    by_id = {question["twin"]["twin_id"]: question["twin"] for question in _review(client)["questions"]}
    assert by_id[RR] == twins[RR].model_dump(mode="json")
    assert by_id[TH1] == twins[TH1].model_dump(mode="json")
    theory = by_id[TH1]["question"]
    assert theory["correct_key"] == "A" and set(theory["distractor_citations"]) == {"B", "C", "D"}
    assert theory["citation"]["verbatim_quote"] == "Tiến trình là một chương trình đang thực thi."
    assert by_id[RR]["citation"] == twins[RR].citation.model_dump(mode="json")
    assert set(by_id[RR]["distractor_to_misconception_map"]) == {"A", "C", "D"}


def test_review_attempt_can_reach_another_batch_of_the_subject(client: TestClient) -> None:
    body = _ok(client, "review", (TH1, "B"), (TH_SECOND, "B"))
    assert [a["batch_id"] for a in body["answers"]] == [BATCH_MAIN, BATCH_SECOND]


# --------------------------------------------------------------------------- #
# Xoá
# --------------------------------------------------------------------------- #


def test_delete_one_attempt(client: TestClient) -> None:
    kept = _ok(client, "review", (TH2, "A"))
    gone = _ok(client, "review", (TH1, "B"))
    response = client.delete(f"{URL}/{gone['attempt_id']}")
    assert response.status_code == 204 and response.content == b""
    assert client.get(f"{URL}/{gone['attempt_id']}").status_code == 404
    assert client.delete(f"{URL}/{gone['attempt_id']}").status_code == 404
    assert [a["attempt_id"] for a in client.get(URL, params={"subject_code": SUBJECT}).json()["attempts"]] == [
        kept["attempt_id"]
    ]
    assert _review_ids(client) == [TH2]


def test_delete_by_subject_keeps_other_subjects_and_twins(client: TestClient, store: AppStore) -> None:
    _ok(client, "review", (TH1, "B"))
    _ok(client, "batch", (RR, "A"), batch_id=BATCH_MAIN)
    _ok(client, "review", (TH_OTHER, "A"))
    assert client.delete(URL, params={"subject_code": SUBJECT}).status_code == 204
    assert client.get(URL, params={"subject_code": SUBJECT}).json() == {"total": 0, "attempts": []}
    assert _review(client)["total"] == 0
    assert client.get(URL, params={"subject_code": OTHER_SUBJECT}).json()["total"] == 1
    assert _review_ids(client, OTHER_SUBJECT) == [TH_OTHER]
    assert set(store.get_twins([RR, TH1])) == {RR, TH1}  # không đụng twin
    assert client.delete(URL, params={"subject_code": SUBJECT}).status_code == 204  # môn không còn lượt: vẫn 204


@pytest.mark.parametrize("params", [{}, {"subject_code": "bad"}])
def test_delete_by_subject_needs_a_valid_subject(client: TestClient, params: dict[str, Any]) -> None:
    assert client.delete(URL, params=params).status_code == 422


def test_deleting_a_batch_cascades_to_attempts(tmp_path: Path, rr_slot: BlueprintSlot) -> None:
    """Xoá batch (qua cascade `blueprints` → `twin_batches`, §5.13): lượt `batch` của batch mất; hàng câu trỏ tới twin
    của batch mất; lượt `review` không còn câu nào bị xoá; lượt còn câu thì số đếm đếm lại trên hàng còn lại."""
    path = tmp_path / "holocron.sqlite3"
    store = SQLiteStore.open(path)
    clock = Clock()
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_clock] = lambda: clock
    try:
        _seed(store, rr_slot)
        client = TestClient(app)
        batch_attempt = _ok(client, "batch", (RR, "A"), batch_id=BATCH_MAIN)
        only_main = _ok(client, "review", (TH1, "B"))
        mixed = _ok(client, "review", (TH1, "B"), (TH_SECOND, "A"))
        other = _ok(client, "review", (TH_OTHER, "A"))
        raw = connect(path)
        try:
            raw.execute("DELETE FROM blueprints WHERE blueprint_id = ?", (BP_MAIN,))
        finally:
            raw.close()
        for gone in (batch_attempt, only_main):
            assert client.get(f"{URL}/{gone['attempt_id']}").status_code == 404
        left = client.get(f"{URL}/{mixed['attempt_id']}").json()
        assert [a["twin_id"] for a in left["answers"]] == [TH_SECOND]
        assert (left["total_count"], left["incorrect_count"]) == (1, 1)
        page = client.get(URL, params={"subject_code": SUBJECT}).json()
        assert page["total"] == 1 and page["attempts"][0]["total_count"] == 1
        assert _review_ids(client) == [TH_SECOND]
        assert client.get(f"{URL}/{other['attempt_id']}").status_code == 200
        assert _post(client, "batch", (TH1, "A"), batch_id=BATCH_MAIN).status_code == 404
    finally:
        app.dependency_overrides.clear()
        store.close()


def test_attempts_survive_a_restart(tmp_path: Path, rr_slot: BlueprintSlot) -> None:
    path = tmp_path / "holocron.sqlite3"
    store = SQLiteStore.open(path)
    _seed(store, rr_slot)
    attempt = build_attempt(
        attempt_id="pa_00000000000000c1",
        subject_code=SUBJECT,
        mode="review",
        batch_id=None,
        blueprint_id=None,
        submitted_at=T0,
        answers=[
            PracticeAnswer(
                ordinal=0,
                twin_id=TH1,
                batch_id=BATCH_MAIN,
                slot_index=1,
                chosen_key=OptionKey.B,
                correct_key=OptionKey.A,
                outcome="incorrect",
            )
        ],
    )
    store.save_practice_attempt(attempt)
    store.close()
    reopened = SQLiteStore.open(path)
    try:
        assert reopened.get_practice_attempt(attempt.attempt_id) == attempt
        assert [q.twin.twin_id for q in reopened.list_review_questions(SUBJECT, 0, 50).questions] == [TH1]
    finally:
        reopened.close()


# --------------------------------------------------------------------------- #
# Repository và model
# --------------------------------------------------------------------------- #


def _attempt(attempt_id: str, twin_id: str, batch_id: str | None = None) -> PracticeAttempt:
    return build_attempt(
        attempt_id=attempt_id,
        subject_code=SUBJECT,
        mode="review" if batch_id is None else "batch",
        batch_id=batch_id,
        blueprint_id=None if batch_id is None else BP_MAIN,
        submitted_at=T0,
        answers=[
            PracticeAnswer(
                ordinal=0,
                twin_id=twin_id,
                batch_id=BATCH_MAIN,
                slot_index=1,
                chosen_key=None,
                correct_key=OptionKey.A,
                outcome="skipped",
            )
        ],
    )


def test_repository_rejects_missing_twin_or_batch(store: AppStore, twins: dict[str, Any]) -> None:
    with pytest.raises(MissingReferenceError):
        store.save_practice_attempt(_attempt("pa_00000000000000d1", "twin_nope"))
    with pytest.raises(MissingReferenceError):
        store.save_practice_attempt(_attempt("pa_00000000000000d2", TH1, batch_id=BATCH_UNKNOWN))
    assert store.list_practice_attempts(SUBJECT, 0, 50).total == 0


def test_repository_rejects_duplicate_attempt_id(store: AppStore, twins: dict[str, Any]) -> None:
    store.save_practice_attempt(_attempt("pa_00000000000000e1", TH1))
    with pytest.raises(StorageError):
        store.save_practice_attempt(_attempt("pa_00000000000000e1", TH2))
    assert store.delete_practice_attempts(SUBJECT) == 1


def test_get_batch_blueprint_id(store: AppStore, twins: dict[str, Any]) -> None:
    assert store.get_batch_blueprint_id(BATCH_SECOND) == BP_SECOND
    assert store.get_batch_blueprint_id(BATCH_UNKNOWN) is None
    found = store.get_twins([TH_OTHER, "twin_nope"])
    assert set(found) == {TH_OTHER}
    assert (found[TH_OTHER].batch_id, found[TH_OTHER].subject_code) == (BATCH_OTHER, OTHER_SUBJECT)


def test_answer_outcome_must_match_the_keys() -> None:
    with pytest.raises(ValueError, match="outcome"):
        PracticeAnswer(
            ordinal=0,
            twin_id=TH1,
            batch_id=BATCH_MAIN,
            slot_index=1,
            chosen_key=OptionKey.B,
            correct_key=OptionKey.A,
            outcome="correct",
        )


def test_summary_counts_must_add_up() -> None:
    with pytest.raises(ValueError, match="total_count"):
        PracticeAttemptSummary(
            attempt_id="pa_00000000000000f1",
            subject_code=SUBJECT,
            mode="review",
            batch_id=None,
            blueprint_id=None,
            submitted_at=T0,
            total_count=2,
            correct_count=1,
            incorrect_count=0,
            skipped_count=0,
        )


def test_attempt_counts_must_match_answers() -> None:
    attempt = _attempt("pa_00000000000000f2", TH1)
    data = attempt.model_dump()
    data.update(total_count=1, skipped_count=0, correct_count=1)
    with pytest.raises(ValueError, match="khớp"):
        PracticeAttempt.model_validate(data)
    second = {**data["answers"][0], "ordinal": 1, "twin_id": TH2}
    unordered = {**attempt.model_dump(), "answers": [second, data["answers"][0]], "total_count": 2, "skipped_count": 2}
    with pytest.raises(ValueError, match="ordinal"):
        PracticeAttempt.model_validate(unordered)
