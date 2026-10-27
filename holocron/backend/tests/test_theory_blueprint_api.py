"""`POST /v1/blueprints` chỉ slot lý thuyết và các GET blueprint (SPECIFICATION §5.9, §6.7; brief B5a mục 2, 4, 5).

Mỗi nhánh lỗi theo đúng thứ tự: 422 body sai (gồm vượt 50 slot lý thuyết hay 50 item) → `item_ids` khác rỗng ⇒ 422
"chưa hỗ trợ" (trước mọi lần ghi) → `source_chunk_id` không có, khác môn hoặc khác chương ⇒ 422 → `format` null lấy
mặc định từ `exam_format` của môn → slot ESSAY ⇒ 409 `CapabilityError` (không đổi sang MCQ4) → `201` + `Location`.
Mọi request lỗi không tạo môn hay blueprint nào. Blueprint trộn slot tính toán và lý thuyết có đủ bốn số tổng hợp.

Văn bản mẫu tự viết cho test (không phải tài liệu môn học có bản quyền).
"""

from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from holocron_core.domain.repositories import AppStore
from holocron_core.domain.schemas import (
    MAX_THEORY_SLOTS,
    BlueprintSlot,
    ExamBlueprint,
    QuestionFormat,
    TheorySlot,
)
from holocron_core.routers.api import app, get_clock, get_store
from holocron_core.routers.blueprints import COMPUTATIONAL_NOT_SUPPORTED
from tests.test_theory_twins import (
    CH1_DEFINITIONS,
    CH2_DEFINITIONS,
    NOW,
    OTHER_SUBJECT,
    OTHER_SUBJECT_DEFINITIONS,
    STORE_KINDS,
    SUBJECT,
    Clock,
    add_chapter,
    close_kind,
    open_kind,
)

NEW_SUBJECT = "MTH2020"


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


def _post(client: TestClient, body: dict[str, Any]) -> Any:
    return client.post("/v1/blueprints", json=body)


def _slots(*chapters: str, **extra: Any) -> list[dict[str, Any]]:
    return [{"chapter_code": chapter, **extra} for chapter in chapters]


def _nothing_written(client: TestClient, store: AppStore, subject: str) -> None:
    assert store.get_subject(subject) is None, "request lỗi không được tạo môn"
    assert client.get("/v1/blueprints", params={"subject_code": subject}).json() == []


# --------------------------------------------------------------------------- #
# Bước 1: body sai ⇒ 422 (validate), kể cả khi có item_ids
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "body",
    [
        {"theory_slots": _slots("ch1")},
        {"subject_code": "toan", "theory_slots": _slots("ch1")},
        {"subject_code": NEW_SUBJECT},
        {"subject_code": NEW_SUBJECT, "item_ids": [], "theory_slots": []},
        {"subject_code": NEW_SUBJECT, "theory_slots": _slots("chuong1")},
        {"subject_code": NEW_SUBJECT, "theory_slots": _slots("ch1", format="NUMERICAL")},
        {"subject_code": NEW_SUBJECT, "theory_slots": _slots("ch1", cognitive_level="apply")},
        {"subject_code": NEW_SUBJECT, "theory_slots": _slots("ch1", unknown_field=1)},
        {"subject_code": NEW_SUBJECT, "theory_slots": _slots("ch1"), "duration_minutes": 1},
        {"subject_code": NEW_SUBJECT, "theory_slots": _slots(*["ch1"] * (MAX_THEORY_SLOTS + 1))},
        {"subject_code": NEW_SUBJECT, "item_ids": [f"ing_{i}" for i in range(51)]},
        {"subject_code": NEW_SUBJECT, "item_ids": ["ing_a", "ing_a"]},
        {"subject_code": NEW_SUBJECT, "item_ids": ["ing_a"], "theory_slots": _slots(*["ch1"] * 51)},
    ],
)
def test_invalid_body_is_a_validation_422_before_anything_else(
    store: AppStore, clock: Clock, body: dict[str, Any]
) -> None:
    client = TestClient(app)
    response = _post(client, body)
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list), "lỗi validate, chưa tới bước kiểm item_ids"
    _nothing_written(client, store, NEW_SUBJECT)


def test_exactly_fifty_theory_slots_is_accepted(store: AppStore, clock: Clock) -> None:
    response = _post(
        TestClient(app), {"subject_code": NEW_SUBJECT, "theory_slots": _slots(*["ch1"] * MAX_THEORY_SLOTS)}
    )
    assert response.status_code == 201, response.text
    assert response.json()["total_questions"] == MAX_THEORY_SLOTS


# --------------------------------------------------------------------------- #
# Bước 2: item_ids ⇒ 422 "chưa hỗ trợ", trước mọi kiểm tra khác và mọi lần ghi
# --------------------------------------------------------------------------- #


def test_item_ids_are_422_not_supported_before_any_write(store: AppStore, clock: Clock) -> None:
    client = TestClient(app)
    response = _post(client, {"subject_code": NEW_SUBJECT, "item_ids": ["ing_khong_co"], "theory_slots": _slots("ch1")})
    assert response.status_code == 422
    assert response.json() == {"detail": COMPUTATIONAL_NOT_SUPPORTED}
    assert COMPUTATIONAL_NOT_SUPPORTED == "Blueprint có câu tính toán chưa được hỗ trợ trong bản này"
    _nothing_written(client, store, NEW_SUBJECT)


def test_item_ids_are_checked_before_source_chunk_and_essay(store: AppStore, clock: Clock) -> None:
    client = TestClient(app)
    body = {
        "subject_code": NEW_SUBJECT,
        "item_ids": ["ing_a"],
        "theory_slots": _slots("ch1", source_chunk_id="chk_khong_co", format="ESSAY"),
    }
    response = _post(client, body)
    assert response.status_code == 422 and response.json() == {"detail": COMPUTATIONAL_NOT_SUPPORTED}
    _nothing_written(client, store, NEW_SUBJECT)


# --------------------------------------------------------------------------- #
# Bước 3: source_chunk_id
# --------------------------------------------------------------------------- #


def test_unknown_source_chunk_is_422(store: AppStore, clock: Clock) -> None:
    client = TestClient(app)
    response = _post(client, {"subject_code": SUBJECT, "theory_slots": _slots("ch1", source_chunk_id="chk_khong_co")})
    assert response.status_code == 422
    assert "chk_khong_co" in response.json()["detail"]
    _nothing_written(client, store, SUBJECT)


def test_source_chunk_of_another_subject_is_422(store: AppStore, clock: Clock) -> None:
    other = add_chapter(store, "c", OTHER_SUBJECT, "ch1", OTHER_SUBJECT_DEFINITIONS)
    client = TestClient(app)
    response = _post(client, {"subject_code": SUBJECT, "theory_slots": _slots("ch1", source_chunk_id=other.chunk_id)})
    assert response.status_code == 422 and "môn khác" in response.json()["detail"]
    _nothing_written(client, store, SUBJECT)


def test_source_chunk_of_another_chapter_is_422(store: AppStore, clock: Clock) -> None:
    chunk = add_chapter(store, "a", SUBJECT, "ch1", CH1_DEFINITIONS)
    client = TestClient(app)
    response = _post(client, {"subject_code": SUBJECT, "theory_slots": _slots("ch2", source_chunk_id=chunk.chunk_id)})
    assert response.status_code == 422 and "chương khác" in response.json()["detail"]
    _nothing_written(client, store, SUBJECT)


def test_source_chunk_is_checked_before_the_essay_capability(store: AppStore, clock: Clock) -> None:
    client = TestClient(app)
    body = {"subject_code": SUBJECT, "theory_slots": _slots("ch1", source_chunk_id="chk_khong_co", format="ESSAY")}
    assert _post(client, body).status_code == 422
    _nothing_written(client, store, SUBJECT)


def test_matching_source_chunk_is_kept_on_the_slot(store: AppStore, clock: Clock) -> None:
    chunk = add_chapter(store, "b", SUBJECT, "ch2", CH2_DEFINITIONS)
    response = _post(
        TestClient(app), {"subject_code": SUBJECT, "theory_slots": _slots("ch2", source_chunk_id=chunk.chunk_id)}
    )
    assert response.status_code == 201, response.text
    assert response.json()["slots"][0]["source_chunk_id"] == chunk.chunk_id


# --------------------------------------------------------------------------- #
# Bước 4–5: format mặc định theo exam_format; ESSAY ⇒ 409 CapabilityError
# --------------------------------------------------------------------------- #


def _create_subject(client: TestClient, exam_format: str) -> None:
    created = client.post("/v1/subjects", json={"subject_code": SUBJECT, "exam_format": exam_format})
    assert created.status_code == 201, created.text


@pytest.mark.parametrize(("exam_format", "expected"), [("mcq", "MCQ4"), ("mixed", "MCQ4")])
def test_null_format_defaults_from_the_subject_exam_format(
    store: AppStore, clock: Clock, exam_format: str, expected: str
) -> None:
    client = TestClient(app)
    _create_subject(client, exam_format)
    response = _post(client, {"subject_code": SUBJECT, "theory_slots": _slots("ch1")})
    assert response.status_code == 201, response.text
    assert [slot["format"] for slot in response.json()["slots"]] == [expected]


def test_null_format_on_an_unknown_subject_defaults_to_mcq4(store: AppStore, clock: Clock) -> None:
    response = _post(TestClient(app), {"subject_code": NEW_SUBJECT, "theory_slots": _slots("ch1")})
    assert response.status_code == 201
    assert response.json()["slots"][0]["format"] == "MCQ4"


def _assert_essay_capability_error(response: Any) -> None:
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == {
        "code": "CAPABILITY_UNAVAILABLE",
        "capability": "essay_grading_offline",
        "reason": "not_implemented",
        "message": response.json()["detail"]["message"],
    }
    assert response.json()["detail"]["message"].startswith("Khả năng essay_grading_offline không khả dụng.")


def test_essay_subject_with_null_format_is_409_and_not_turned_into_mcq4(store: AppStore, clock: Clock) -> None:
    client = TestClient(app)
    _create_subject(client, "essay")
    response = _post(client, {"subject_code": SUBJECT, "theory_slots": _slots("ch1")})
    _assert_essay_capability_error(response)
    assert client.get("/v1/blueprints", params={"subject_code": SUBJECT}).json() == []


def test_explicit_essay_slot_is_409_even_for_a_mcq_subject(store: AppStore, clock: Clock) -> None:
    client = TestClient(app)
    _create_subject(client, "mcq")
    body = {"subject_code": SUBJECT, "theory_slots": [*_slots("ch1"), *_slots("ch2", format="ESSAY")]}
    _assert_essay_capability_error(_post(client, body))
    assert client.get("/v1/blueprints", params={"subject_code": SUBJECT}).json() == []


def test_essay_slot_on_an_unknown_subject_is_409_and_creates_no_subject(store: AppStore, clock: Clock) -> None:
    client = TestClient(app)
    _assert_essay_capability_error(
        _post(client, {"subject_code": NEW_SUBJECT, "theory_slots": _slots("ch1", format="ESSAY")})
    )
    _nothing_written(client, store, NEW_SUBJECT)


def test_explicit_mcq4_wins_over_an_essay_subject(store: AppStore, clock: Clock) -> None:
    client = TestClient(app)
    _create_subject(client, "essay")
    response = _post(client, {"subject_code": SUBJECT, "theory_slots": _slots("ch1", format="MCQ4")})
    assert response.status_code == 201 and response.json()["slots"][0]["format"] == "MCQ4"


# --------------------------------------------------------------------------- #
# Bước 6: lưu, 201 + Location, GET
# --------------------------------------------------------------------------- #


def test_created_theory_blueprint_has_location_and_reads_back(store: AppStore, clock: Clock) -> None:
    client = TestClient(app)
    response = _post(
        client,
        {
            "subject_code": NEW_SUBJECT,
            "theory_slots": [{"chapter_code": "ch1"}, {"chapter_code": "ch2.1", "cognitive_level": "understand"}],
            "duration_minutes": 45,
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert response.headers["Location"] == f"/v1/blueprints/{body['blueprint_id']}"
    assert body["source_exam_doc_id"] is None
    assert body["subject_code"] == NEW_SUBJECT and body["total_questions"] == 2 and body["duration_minutes"] == 45
    assert body["created_at"].startswith("2026-10-28T08:00:00")
    assert body["slots"] == [
        {
            "slot_index": 1,
            "is_computational": False,
            "question_kind": "theory",
            "chapter_code": "ch1",
            "source_chunk_id": None,
            "format": "MCQ4",
            "topic_key": None,
            "cognitive_level": "remember",
            "non_cloneable": False,
            "mapping_confidence": 1.0,
            "review_status": "NEEDS_REVIEW",
        },
        {
            "slot_index": 2,
            "is_computational": False,
            "question_kind": "theory",
            "chapter_code": "ch2.1",
            "source_chunk_id": None,
            "format": "MCQ4",
            "topic_key": None,
            "cognitive_level": "understand",
            "non_cloneable": False,
            "mapping_confidence": 1.0,
            "review_status": "NEEDS_REVIEW",
        },
    ]
    assert body["chapter_distribution"] == {"ch1": 1, "ch2.1": 1}
    assert body["format_distribution"] == {"MCQ4": 2}
    assert body["cloneable_ratio"] == 1.0
    assert body["mean_mapping_confidence"] == 1.0
    fetched = client.get(response.headers["Location"])
    assert fetched.status_code == 200 and fetched.json() == body


def test_unknown_blueprint_is_404(store: AppStore) -> None:
    assert TestClient(app).get("/v1/blueprints/bp_khong_co").status_code == 404


def test_blueprint_list_filters_by_subject_newest_first(store: AppStore, clock: Clock) -> None:
    client = TestClient(app)
    ids = []
    for subject in (SUBJECT, NEW_SUBJECT, SUBJECT):
        ids.append(
            _post(client, {"subject_code": subject, "theory_slots": _slots("ch1", "ch2")}).json()["blueprint_id"]
        )
        clock.now += timedelta(minutes=1)
    rows = client.get("/v1/blueprints", params={"subject_code": SUBJECT}).json()
    assert [row["blueprint_id"] for row in rows] == [ids[2], ids[0]]
    assert rows[0] == {
        "blueprint_id": ids[2],
        "subject_code": SUBJECT,
        "total_questions": 2,
        "created_at": rows[0]["created_at"],
        "batch_count": 0,
    }
    assert rows[0]["created_at"].startswith("2026-10-28T08:02:00")
    every = client.get("/v1/blueprints").json()
    assert [row["blueprint_id"] for row in every] == [ids[2], ids[1], ids[0]]
    assert client.get("/v1/blueprints", params={"subject_code": "sai-mau"}).status_code == 422


def test_success_creates_the_subject_implicitly(store: AppStore, clock: Clock) -> None:
    client = TestClient(app)
    assert _post(client, {"subject_code": NEW_SUBJECT, "theory_slots": _slots("ch1")}).status_code == 201
    subject = client.get(f"/v1/subjects/{NEW_SUBJECT}")
    assert subject.status_code == 200
    assert subject.json() == {
        "subject_code": NEW_SUBJECT,
        "display_name": NEW_SUBJECT,
        "exam_format": "mixed",
        "version": 1,
        "created_at": subject.json()["created_at"],
        "updated_at": subject.json()["created_at"],
    }
    assert subject.json()["created_at"].startswith("2026-10-28T08:00:00")


def test_existing_subject_is_left_untouched(store: AppStore, clock: Clock) -> None:
    client = TestClient(app)
    created = client.post(
        "/v1/subjects", json={"subject_code": SUBJECT, "display_name": "Triết học", "exam_format": "mcq"}
    )
    assert created.status_code == 201
    clock.now += timedelta(hours=1)
    assert _post(client, {"subject_code": SUBJECT, "theory_slots": _slots("ch1")}).status_code == 201
    assert client.get(f"/v1/subjects/{SUBJECT}").json() == created.json()


# --------------------------------------------------------------------------- #
# Model: blueprint trộn slot tính toán và lý thuyết, source_exam_doc_id
# --------------------------------------------------------------------------- #


def _mixed(rr_slot: BlueprintSlot) -> ExamBlueprint:
    return ExamBlueprint(
        blueprint_id="bp_mixed_1",
        source_exam_doc_id="doc_exam_1",
        subject_code="IT3070",
        total_questions=3,
        slots=[
            rr_slot.model_copy(update={"slot_index": 1, "mapping_confidence": 0.7}),
            TheorySlot(slot_index=2, chapter_code="ch5.3", format=QuestionFormat.MCQ4),
            TheorySlot(slot_index=3, chapter_code="ch1", format=QuestionFormat.ESSAY),
        ],
        created_at=NOW,
    )


def test_mixed_blueprint_has_all_four_aggregates_through_get(store: AppStore, rr_slot: BlueprintSlot) -> None:
    store.save_blueprint(_mixed(rr_slot))
    body = TestClient(app).get("/v1/blueprints/bp_mixed_1").json()
    assert [slot.get("question_kind", "computational") for slot in body["slots"]] == [
        "computational",
        "theory",
        "theory",
    ]
    assert body["chapter_distribution"] == {"ch1": 1, "ch5.3": 2}
    assert body["format_distribution"] == {"ESSAY": 1, "MCQ4": 2}
    assert body["cloneable_ratio"] == 1.0
    assert body["mean_mapping_confidence"] == round((0.7 + 1.0 + 1.0) / 3, 4)
    restored = ExamBlueprint.model_validate(
        {k: v for k, v in body.items() if k not in ExamBlueprint.model_computed_fields}
    )
    assert restored == _mixed(rr_slot)
    assert isinstance(restored.slots[0], BlueprintSlot) and isinstance(restored.slots[1], TheorySlot)


def test_source_exam_doc_id_may_be_null_only_without_computational_slots(rr_slot: BlueprintSlot) -> None:
    theory_only = ExamBlueprint(
        subject_code=SUBJECT,
        total_questions=1,
        slots=[TheorySlot(slot_index=1, chapter_code="ch1", format=QuestionFormat.MCQ4)],
    )
    assert theory_only.source_exam_doc_id is None
    data = _mixed(rr_slot).model_dump(exclude=set(ExamBlueprint.model_computed_fields))
    with pytest.raises(ValidationError, match="source_exam_doc_id"):
        ExamBlueprint.model_validate({**data, "source_exam_doc_id": None})


def test_theory_slot_rejects_numerical_format() -> None:
    with pytest.raises(ValidationError, match="MCQ4 hoặc ESSAY"):
        TheorySlot(slot_index=1, chapter_code="ch1", format=QuestionFormat.NUMERICAL)


def test_essay_slot_stored_directly_becomes_a_ticket_not_an_error(store: AppStore, clock: Clock) -> None:
    store.save_blueprint(_mixed_theory_essay())
    response = TestClient(app).post("/v1/blueprints/bp_essay_1/twins", json={"count": 1})
    assert response.status_code == 201
    [ticket] = response.json()["review_tickets"]
    assert ticket["slot_index"] == 1 and "ESSAY" in ticket["reason"]


def _mixed_theory_essay() -> ExamBlueprint:
    return ExamBlueprint(
        blueprint_id="bp_essay_1",
        subject_code=SUBJECT,
        total_questions=1,
        slots=[TheorySlot(slot_index=1, chapter_code="ch1", format=QuestionFormat.ESSAY)],
        created_at=NOW,
    )
