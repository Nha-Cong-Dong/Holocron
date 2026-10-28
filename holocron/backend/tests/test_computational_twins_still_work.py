"""Luồng sinh câu song sinh có tính toán vẫn chạy sau khi nối câu lý thuyết (quyết định 2026-10-28 câu 15).

`POST /v1/blueprints` trả 422 với `item_ids` (câu tính toán chưa hỗ trợ ở đường blueprint mới), nhưng blueprint tính
toán nạp bằng `store.save_blueprint(..., item_ids=[...])` (như `tests/test_api_sqlite.py`) vẫn sinh twin qua
`POST /v1/blueprints/{id}/twins` trên cả ba loại kho: 201, câu tính toán (JSON không có `question_kind`, đọc lại là
`TwinQuestion`), có `gantt` từ sandbox, và batch được lưu (`batch_count` tăng).
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from holocron_core.domain.repositories import AppStore
from holocron_core.domain.schemas import BlueprintSlot, ExamBlueprint, KnowledgeChunk, TwinQuestion
from holocron_core.domain.twins import TwinBatch
from holocron_core.routers.api import app, get_citation_provider, get_store, get_template_author
from tests.test_api_sqlite import FixedCitations, RrAuthor, _approved_item
from tests.test_theory_twins import STORE_KINDS, close_kind, open_kind

BASE_SEED = 20260419
SUBJECT = "IT3070"


@pytest.fixture(params=STORE_KINDS)
def store(request: pytest.FixtureRequest, tmp_path: Path, theory_chunk: KnowledgeChunk) -> Iterator[AppStore]:
    opened = open_kind(str(request.param), tmp_path)
    app.dependency_overrides[get_store] = lambda: opened
    app.dependency_overrides[get_template_author] = lambda: RrAuthor()
    app.dependency_overrides[get_citation_provider] = lambda: FixedCitations(theory_chunk.to_citation())
    yield opened
    app.dependency_overrides.clear()
    close_kind(opened)


def _computational_blueprint(store: AppStore, rr_slot: BlueprintSlot, item_id: str) -> str:
    blueprint = ExamBlueprint(
        blueprint_id="bp_computational_1",
        source_exam_doc_id="doc_exam_1",
        subject_code=SUBJECT,
        total_questions=1,
        slots=[rr_slot.model_copy(update={"slot_index": 1})],
    )
    store.save_blueprint(blueprint, item_ids=[item_id])
    return blueprint.blueprint_id


def test_computational_blueprint_still_produces_twins(store: AppStore, rr_slot: BlueprintSlot) -> None:
    client = TestClient(app)
    blueprint_id = _computational_blueprint(store, rr_slot, _approved_item(client))
    response = client.post(f"/v1/blueprints/{blueprint_id}/twins", json={"count": 2, "base_seed": BASE_SEED})
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["review_tickets"] == []
    assert len(body["twins"]) == 2
    for twin in body["twins"]:
        assert "question_kind" not in twin, "JSON câu tính toán giữ nguyên, không có question_kind"
        assert twin["gantt"], "gantt vẫn đến từ sandbox"
        assert twin["review_status"] == "AUTO_PASS"
    batch = TwinBatch.model_validate(body)
    assert all(isinstance(twin, TwinQuestion) for twin in batch.twins)
    assert batch.batch_id.startswith("tb_")
    [summary] = client.get("/v1/blueprints", params={"subject_code": SUBJECT}).json()
    assert summary["blueprint_id"] == blueprint_id and summary["batch_count"] == 1


def test_computational_blueprint_reads_back_unchanged(store: AppStore, rr_slot: BlueprintSlot) -> None:
    client = TestClient(app)
    blueprint_id = _computational_blueprint(store, rr_slot, _approved_item(client))
    response = client.get(f"/v1/blueprints/{blueprint_id}")
    assert response.status_code == 200
    stored = ExamBlueprint.model_validate(
        {k: v for k, v in response.json().items() if k not in ExamBlueprint.model_computed_fields}
    )
    assert isinstance(stored.slots[0], BlueprintSlot)
    assert stored.slots[0] == rr_slot.model_copy(update={"slot_index": 1})


def test_computational_item_ids_are_rejected_on_the_new_blueprint_route_only(
    store: AppStore, rr_slot: BlueprintSlot
) -> None:
    """422 "chưa hỗ trợ" chỉ ở `POST /v1/blueprints`; sinh twin cho blueprint tính toán đã có vẫn 201."""
    client = TestClient(app)
    item_id = _approved_item(client)
    rejected = client.post("/v1/blueprints", json={"subject_code": SUBJECT, "item_ids": [item_id]})
    assert rejected.status_code == 422
    blueprint_id = _computational_blueprint(store, rr_slot, item_id)
    assert client.post(f"/v1/blueprints/{blueprint_id}/twins", json={"count": 1}).status_code == 201
