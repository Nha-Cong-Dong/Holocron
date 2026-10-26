"""Router Vault (SPECIFICATION §6.6) gắn vào `routers.api.app`: request đi qua `app`, không qua app riêng của test.

Kho cấp bằng `app.dependency_overrides[get_store]` (in-memory); văn bản chunk là mẫu tự viết (`tests/vault_support.py`).
"""

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from holocron_core.domain.schemas import KnowledgeChunk
from holocron_core.infrastructure.memory_store import IngestionStore
from holocron_core.routers.api import app, get_clock, get_store
from holocron_core.services.vault import ingest_confirmed_text
from tests.vault_support import ARM_PAGES, NOW, PHILOSOPHY_PAGES, add_document, confirmed, document_id

ARM_TOPIC = "mp.arm.cpsr.flags"
PHIL_QUOTE = "Vật chất là phạm trù triết học dùng để chỉ thực tại khách quan."
VAULT_PATHS = [
    ("/v1/vault/chunks", "get"),
    ("/v1/vault/search", "get"),
    ("/v1/vault/chunks/{chunk_id}/labels", "patch"),
    ("/v1/vault/concept-pairs", "post"),
    ("/v1/vault/concept-pairs", "get"),
    ("/v1/vault/concept-pairs/{pair_id}/confirm", "post"),
    ("/v1/vault/concept-pairs/{pair_id}/reject", "post"),
]


@pytest.fixture
def store() -> Iterator[IngestionStore]:
    fresh = IngestionStore()
    app.dependency_overrides[get_store] = lambda: fresh
    app.dependency_overrides[get_clock] = lambda: lambda: NOW
    yield fresh
    app.dependency_overrides.clear()


@pytest.fixture
def chunks(store: IngestionStore) -> dict[str, list[KnowledgeChunk]]:
    arm_doc, phil_doc = document_id("a"), document_id("b")
    add_document(store, arm_doc)
    add_document(store, phil_doc)
    return {
        "ET4010": ingest_confirmed_text(store, confirmed(arm_doc, ARM_PAGES, topic_keys=[ARM_TOPIC])),
        "SSH1110": ingest_confirmed_text(
            store, confirmed(phil_doc, PHILOSOPHY_PAGES, subject_code="SSH1110", chapter_code="ch1")
        ),
    }


def _create_pair(client: TestClient, chunk_id: str, term: str, quote: str) -> Any:
    return client.post(
        "/v1/vault/concept-pairs",
        json={"editor_id": "u1", "chunk_id": chunk_id, "term": term, "definition_quote": quote},
    )


def test_app_openapi_lists_the_vault_routes() -> None:
    paths = app.openapi()["paths"]
    for path, method in VAULT_PATHS:
        assert method in paths.get(path, {}), (path, method)


def test_list_chunks_through_app(chunks: dict[str, list[KnowledgeChunk]]) -> None:
    client = TestClient(app)
    response = client.get("/v1/vault/chunks", params={"subject_code": "ET4010"})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert [c["chunk_id"] for c in body["chunks"]] == [c.chunk_id for c in chunks["ET4010"]]
    assert all(c["subject_code"] == "ET4010" for c in body["chunks"])
    assert client.get("/v1/vault/chunks", params={"subject_code": "et4010"}).status_code == 422


def test_search_through_app_is_filtered_by_subject(chunks: dict[str, list[KnowledgeChunk]]) -> None:
    client = TestClient(app)
    response = client.get("/v1/vault/search", params={"q": "cờ trạng thái", "subject_code": "ET4010"})
    assert response.status_code == 200
    hits = response.json()
    assert [hit["chunk"]["chunk_id"] for hit in hits] == [c.chunk_id for c in chunks["ET4010"]]
    assert all(hit["index"] == "primary" and hit["score"] > 0 for hit in hits)
    other = client.get("/v1/vault/search", params={"q": "cờ trạng thái", "subject_code": "SSH1110"})
    assert other.status_code == 200 and other.json() == []
    assert client.get("/v1/vault/search", params={"q": "cờ"}).status_code == 422


def test_patch_labels_through_app(chunks: dict[str, list[KnowledgeChunk]]) -> None:
    client = TestClient(app)
    chunk_id = chunks["ET4010"][0].chunk_id
    url = f"/v1/vault/chunks/{chunk_id}/labels"
    patched = client.patch(url, json={"editor_id": "u1", "expected_version": 1, "topic_keys": ["mp.arm.alu.add"]})
    assert patched.status_code == 200
    assert patched.json()["label_status"] == "confirmed" and patched.json()["version"] == 2
    assert client.patch(url, json={"editor_id": "u1", "expected_version": 1}).status_code == 409


def test_create_and_confirm_pair_through_app(chunks: dict[str, list[KnowledgeChunk]]) -> None:
    client = TestClient(app)
    created = _create_pair(client, chunks["SSH1110"][0].chunk_id, "Vật chất", PHIL_QUOTE)
    assert created.status_code == 201
    pair = created.json()
    assert (pair["status"], pair["proposed_by"], pair["version"]) == ("confirmed", "user", 1)
    assert pair["subject_code"] == "SSH1110" and pair["definition_quote"] == PHIL_QUOTE

    confirmed_pair = client.post(
        f"/v1/vault/concept-pairs/{pair['pair_id']}/confirm",
        json={"editor_id": "u2", "expected_version": 1, "term": "Phạm trù vật chất"},
    )
    assert confirmed_pair.status_code == 200
    assert confirmed_pair.json()["term"] == "Phạm trù vật chất"
    assert (confirmed_pair.json()["status"], confirmed_pair.json()["version"]) == ("confirmed", 2)
    listed = client.get("/v1/vault/concept-pairs", params={"subject_code": "SSH1110"}).json()
    assert [p["pair_id"] for p in listed] == [pair["pair_id"]]
    missing = client.post("/v1/vault/concept-pairs/cp_missing/confirm", json={"editor_id": "u", "expected_version": 1})
    assert missing.status_code == 404


def test_pair_quote_not_in_chunk_is_rejected_through_app(chunks: dict[str, list[KnowledgeChunk]]) -> None:
    """I3: `definition_quote` không phải chuỗi con (sau NFC, gộp khoảng trắng) của văn bản chunk ⇒ 422, không lưu."""
    client = TestClient(app)
    chunk_id = chunks["SSH1110"][0].chunk_id
    rejected = _create_pair(client, chunk_id, "Vật chất", "Vật chất là phạm trù kinh tế học.")
    assert rejected.status_code == 422
    assert rejected.json()["detail"][0]["loc"] == ["body", "definition_quote"]
    assert client.get("/v1/vault/concept-pairs", params={"subject_code": "SSH1110"}).json() == []

    pair = _create_pair(client, chunk_id, "Vật chất", PHIL_QUOTE).json()
    edit = client.post(
        f"/v1/vault/concept-pairs/{pair['pair_id']}/confirm",
        json={"editor_id": "u1", "expected_version": 1, "definition_quote": "không có trong văn bản chunk"},
    )
    assert edit.status_code == 422
    assert edit.json()["detail"][0]["loc"] == ["body", "definition_quote"]
    stored = client.get("/v1/vault/concept-pairs", params={"subject_code": "SSH1110"}).json()
    assert [(p["definition_quote"], p["version"]) for p in stored] == [(PHIL_QUOTE, 1)]
