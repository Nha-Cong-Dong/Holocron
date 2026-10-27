"""`POST /v1/blueprints/{id}/twins` (spec §6.3): seed, count, idempotency, 404/409/422 và `review_tickets`."""

import hashlib
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from holocron_core.domain.schemas import (
    BlueprintSlot,
    Citation,
    ExamBlueprint,
    KnowledgeChunk,
    QuestionTemplate,
    TemplateProvenance,
)
from holocron_core.infrastructure.template_library import rr_avg_waiting_template
from holocron_core.routers import api
from holocron_core.routers.api import (
    IngestionStore,
    app,
    get_citation_provider,
    get_clock,
    get_store,
    get_template_author,
)
from holocron_core.services import twin_batch
from holocron_core.services.pipeline import SEED_STRIDE
from holocron_core.services.twin_batch import (
    NoCitationProvider,
    TwinBatchRequest,
    UnconfiguredTemplateAuthor,
    default_base_seed,
)
from tests.test_api_item_state import _create

BASE_SEED = 20260419


class RrAuthor:
    """Author giả lập không gọi mạng: luôn trả template RR do LLM 'viết'."""

    def author(self, slot: BlueprintSlot, feedback: list[str]) -> QuestionTemplate:
        return rr_avg_waiting_template(TemplateProvenance.LLM_GENERATED)


class FixedCitations:
    def __init__(self, citation: Citation | None) -> None:
        self._citation = citation

    def citation_for(self, subject_code: str, slot: BlueprintSlot) -> Citation | None:
        return self._citation


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 1, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def store() -> Iterator[IngestionStore]:
    fresh = IngestionStore()
    app.dependency_overrides[get_store] = lambda: fresh
    yield fresh
    app.dependency_overrides.clear()


@pytest.fixture
def clock() -> Clock:
    fake = Clock()
    app.dependency_overrides[get_clock] = lambda: fake
    return fake


@pytest.fixture
def stubs(theory_chunk: KnowledgeChunk) -> None:
    """Author và nguồn trích dẫn giả lập; cả hai là phụ thuộc ngoài nên phải thay bằng stub."""
    app.dependency_overrides[get_template_author] = lambda: RrAuthor()
    app.dependency_overrides[get_citation_provider] = lambda: FixedCitations(theory_chunk.to_citation())


def _blueprint(rr_slot: BlueprintSlot, slots: int = 1, blueprint_id: str = "bp_test_1") -> ExamBlueprint:
    built = [rr_slot.model_copy(update={"slot_index": index + 1}) for index in range(slots)]
    return ExamBlueprint(
        blueprint_id=blueprint_id,
        source_exam_doc_id="doc_exam_1",
        subject_code="IT3070",
        total_questions=slots,
        slots=built,
    )


def _post(client: TestClient, blueprint_id: str, body: dict[str, Any], key: str | None = None) -> Any:
    headers = {"Idempotency-Key": key} if key is not None else {}
    return client.post(f"/v1/blueprints/{blueprint_id}/twins", json=body, headers=headers)


def test_default_base_seed_follows_the_spec_formula() -> None:
    expected = int(hashlib.sha256(b"bp_test_1").hexdigest()[:15], 16)
    assert default_base_seed("bp_test_1") == expected
    assert 0 <= expected < 2**63


def test_request_defaults_and_bounds() -> None:
    request = TwinBatchRequest()
    assert (request.count, request.format.value, request.base_seed) == (3, "MCQ4", None)
    for bad in ({"count": 0}, {"count": 11}, {"base_seed": -1}, {"base_seed": 2**63}, {"format": "ESSAY"}):
        with pytest.raises(ValueError):
            TwinBatchRequest.model_validate(bad)


def test_generates_count_twins_per_slot_with_the_default_seed(
    store: IngestionStore, stubs: None, rr_slot: BlueprintSlot, theory_chunk: KnowledgeChunk
) -> None:
    store.save_blueprint(_blueprint(rr_slot, slots=2, blueprint_id="bp_default_seed"))
    response = _post(TestClient(app), "bp_default_seed", {"count": 2})
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["blueprint_id"] == "bp_default_seed"
    assert body["base_seed"] == default_base_seed("bp_default_seed")
    assert body["review_tickets"] == []
    assert [twin["slot_index"] for twin in body["twins"]] == [1, 1, 2, 2]
    for twin in body["twins"]:
        assert twin["blueprint_id"] == "bp_default_seed"
        assert twin["review_status"] == "AUTO_PASS"
        assert twin["gantt"], "twin CPU Scheduling phải có gantt"
        assert twin["citation"]["verbatim_quote"] == theory_chunk.to_citation().verbatim_quote


def test_each_twin_seed_follows_base_seed_plus_k_times_stride(
    store: IngestionStore, stubs: None, rr_slot: BlueprintSlot
) -> None:
    store.save_blueprint(_blueprint(rr_slot))
    body = _post(TestClient(app), "bp_test_1", {"count": 3, "base_seed": BASE_SEED}).json()
    assert body["base_seed"] == BASE_SEED and len(body["twins"]) == 3
    for k, twin in enumerate(body["twins"]):
        offset = twin["seed"] - (BASE_SEED + k * SEED_STRIDE)
        assert offset >= 0 and offset % SEED_STRIDE == 0, f"twin {k}: seed {twin['seed']} lệch dãy tất định"


def test_same_request_is_reproducible_I4(store: IngestionStore, stubs: None, rr_slot: BlueprintSlot) -> None:
    store.save_blueprint(_blueprint(rr_slot))
    client = TestClient(app)

    def stable(response: Any) -> list[dict[str, Any]]:
        return [
            {k: v for k, v in twin.items() if k not in ("twin_id", "generated_at", "trace")}
            for twin in response.json()["twins"]
        ]

    first = _post(client, "bp_test_1", {"count": 2, "base_seed": BASE_SEED})
    second = _post(client, "bp_test_1", {"count": 2, "base_seed": BASE_SEED})
    assert stable(first) == stable(second)


@pytest.mark.parametrize(
    "body",
    [{"count": 0}, {"count": 11}, {"base_seed": -1}, {"format": "ESSAY"}, {"count": "ba"}],
)
def test_invalid_body_is_422(store: IngestionStore, rr_slot: BlueprintSlot, body: dict[str, Any]) -> None:
    store.save_blueprint(_blueprint(rr_slot))
    assert _post(TestClient(app), "bp_test_1", body).status_code == 422


def test_unknown_blueprint_is_404(store: IngestionStore) -> None:
    assert _post(TestClient(app), "bp_missing", {}).status_code == 404


def _approved_item(client: TestClient) -> str:
    item_id = _create(client)
    assert client.post(f"/v1/ingestion/items/{item_id}/approve").status_code == 200
    return item_id


def test_blueprint_linked_to_approved_items_is_accepted(
    store: IngestionStore, stubs: None, rr_slot: BlueprintSlot
) -> None:
    client = TestClient(app)
    item_id = _approved_item(client)
    store.save_blueprint(_blueprint(rr_slot), item_ids=[item_id])
    assert _post(client, "bp_test_1", {"count": 1, "base_seed": BASE_SEED}).status_code == 201


def test_blueprint_with_an_item_that_is_not_approved_is_409_I5(
    store: IngestionStore, stubs: None, rr_slot: BlueprintSlot
) -> None:
    client = TestClient(app)
    item_id = _create(client)  # đã AUTO_PASS nhưng CHƯA approve
    store.save_blueprint(_blueprint(rr_slot), item_ids=[item_id])
    response = _post(client, "bp_test_1", {"count": 1})
    assert response.status_code == 409
    assert item_id in str(response.json()["detail"])


def test_blueprint_with_a_missing_item_is_409(store: IngestionStore, stubs: None, rr_slot: BlueprintSlot) -> None:
    store.save_blueprint(_blueprint(rr_slot), item_ids=["ing_does_not_exist"])
    assert _post(TestClient(app), "bp_test_1", {"count": 1}).status_code == 409


def test_blueprint_with_a_non_cloneable_item_is_409(store: IngestionStore, stubs: None, rr_slot: BlueprintSlot) -> None:
    client = TestClient(app)
    item_id = _approved_item(client)
    item = store.get_item(item_id)
    assert item is not None
    item.non_cloneable = True
    store.save_item(item)
    store.save_blueprint(_blueprint(rr_slot), item_ids=[item_id])
    assert _post(client, "bp_test_1", {"count": 1}).status_code == 409


def test_no_citation_source_puts_every_slot_in_review_tickets_not_5xx(
    store: IngestionStore, rr_slot: BlueprintSlot
) -> None:
    """Mặc định chưa có Knowledge Vault ⇒ không có Citation để dựng twin: mỗi slot một ReviewTicket."""
    store.save_blueprint(_blueprint(rr_slot, slots=2))
    response = _post(TestClient(app), "bp_test_1", {"count": 3})
    assert response.status_code == 201
    body = response.json()
    assert body["twins"] == []
    assert [ticket["slot_index"] for ticket in body["review_tickets"]] == [1, 2]
    assert all("trích dẫn" in ticket["reason"] for ticket in body["review_tickets"])


def test_unconfigured_llm_falls_back_to_the_verified_template(
    store: IngestionStore, theory_chunk: KnowledgeChunk, rr_slot: BlueprintSlot
) -> None:
    app.dependency_overrides[get_citation_provider] = lambda: FixedCitations(theory_chunk.to_citation())
    store.save_blueprint(_blueprint(rr_slot))
    response = _post(TestClient(app), "bp_test_1", {"count": 1, "base_seed": BASE_SEED})
    assert response.status_code == 201, response.text
    twin = response.json()["twins"][0]
    assert twin["review_status"] == "AUTO_FALLBACK"
    assert twin["trace"]["provenance"] == "precomputed_verified"
    assert response.json()["review_tickets"] == []


def test_unproducible_slot_becomes_a_ticket_without_failing_the_batch(
    store: IngestionStore, stubs: None, rr_slot: BlueprintSlot
) -> None:
    blueprint = _blueprint(rr_slot, slots=2)
    blocked = blueprint.slots[0].model_copy(update={"non_cloneable": True, "non_cloneable_reason": "đề cần hình vẽ"})
    store.save_blueprint(blueprint.model_copy(update={"slots": [blocked, blueprint.slots[1]]}))
    response = _post(TestClient(app), "bp_test_1", {"count": 2, "base_seed": BASE_SEED})
    assert response.status_code == 201
    body = response.json()
    assert [ticket["slot_index"] for ticket in body["review_tickets"]] == [1]
    assert body["review_tickets"][0]["reason"].startswith("non_cloneable:")
    assert [twin["slot_index"] for twin in body["twins"]] == [2, 2]


def test_numerical_format_overrides_the_slot_format(store: IngestionStore, stubs: None, rr_slot: BlueprintSlot) -> None:
    store.save_blueprint(_blueprint(rr_slot))
    response = _post(TestClient(app), "bp_test_1", {"count": 1, "format": "NUMERICAL", "base_seed": BASE_SEED})
    assert response.status_code == 201, response.text
    twin = response.json()["twins"][0]
    assert twin["format"] == "NUMERICAL" and twin["options"] is None and twin["numerical_answer"] is not None


@pytest.fixture
def sandbox_calls(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    calls: list[int] = []
    real = twin_batch.produce_twin

    def counting(*args: Any, **kwargs: Any) -> Any:
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(twin_batch, "produce_twin", counting)
    return calls


def test_idempotency_key_replays_the_same_batch_without_running_the_sandbox_again(
    store: IngestionStore, stubs: None, clock: Clock, sandbox_calls: list[int], rr_slot: BlueprintSlot
) -> None:
    store.save_blueprint(_blueprint(rr_slot))
    client = TestClient(app)
    body = {"count": 1, "base_seed": BASE_SEED}
    first = _post(client, "bp_test_1", body, key="key-1")
    runs = len(sandbox_calls)
    assert first.status_code == 201 and runs == 1
    clock.now += timedelta(hours=23)
    second = _post(client, "bp_test_1", body, key="key-1")
    assert second.status_code == 201 and second.json() == first.json()
    assert len(sandbox_calls) == runs, "replay không được chạy lại sandbox"


def test_idempotency_replay_ignores_how_the_default_body_is_spelled(
    store: IngestionStore, stubs: None, clock: Clock, sandbox_calls: list[int], rr_slot: BlueprintSlot
) -> None:
    store.save_blueprint(_blueprint(rr_slot))
    client = TestClient(app)
    first = _post(client, "bp_test_1", {"count": 1}, key="key-2")
    second = _post(client, "bp_test_1", {"count": 1, "format": "MCQ4", "base_seed": None}, key="key-2")
    assert second.json() == first.json() and len(sandbox_calls) == 1


def test_idempotency_key_with_a_different_body_is_a_new_request_and_keeps_the_original_record(
    store: IngestionStore, stubs: None, clock: Clock, sandbox_calls: list[int], rr_slot: BlueprintSlot
) -> None:
    store.save_blueprint(_blueprint(rr_slot))
    client = TestClient(app)
    original = _post(client, "bp_test_1", {"count": 1, "base_seed": BASE_SEED}, key="key-3")
    other = _post(client, "bp_test_1", {"count": 1, "base_seed": BASE_SEED + 1}, key="key-3")
    assert other.json()["base_seed"] == BASE_SEED + 1 and other.json() != original.json()
    runs = len(sandbox_calls)
    replay = _post(client, "bp_test_1", {"count": 1, "base_seed": BASE_SEED}, key="key-3")
    assert replay.json() == original.json() and len(sandbox_calls) == runs


def test_idempotency_record_expires_after_24_hours(
    store: IngestionStore, stubs: None, clock: Clock, sandbox_calls: list[int], rr_slot: BlueprintSlot
) -> None:
    store.save_blueprint(_blueprint(rr_slot))
    client = TestClient(app)
    body = {"count": 1, "base_seed": BASE_SEED}
    first = _post(client, "bp_test_1", body, key="key-4")
    clock.now += timedelta(hours=24, seconds=1)
    second = _post(client, "bp_test_1", body, key="key-4")
    assert len(sandbox_calls) == 2
    assert second.json()["twins"][0]["twin_id"] != first.json()["twins"][0]["twin_id"]


def test_idempotency_key_is_scoped_to_the_blueprint(
    store: IngestionStore, stubs: None, clock: Clock, sandbox_calls: list[int], rr_slot: BlueprintSlot
) -> None:
    store.save_blueprint(_blueprint(rr_slot, blueprint_id="bp_a"))
    store.save_blueprint(_blueprint(rr_slot, blueprint_id="bp_b"))
    client = TestClient(app)
    body = {"count": 1, "base_seed": BASE_SEED}
    a = _post(client, "bp_a", body, key="shared")
    b = _post(client, "bp_b", body, key="shared")
    assert a.json()["blueprint_id"] == "bp_a" and b.json()["blueprint_id"] == "bp_b"
    assert len(sandbox_calls) == 2


def test_default_dependencies_are_the_safe_unconfigured_ones() -> None:
    assert isinstance(api.get_citation_provider(), NoCitationProvider)
    assert isinstance(api.get_template_author(), UnconfiguredTemplateAuthor)
    assert api.get_clock()().tzinfo is not None
