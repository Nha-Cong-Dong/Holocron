"""`routers/vault.py` (spec §6.6, phần không cần LLM) qua `TestClient`, kho cấp bằng `dependency_overrides`.

Router được gắn vào một app FastAPI riêng cho test: gắn vào `routers.api.app` làm đổi danh sách path mà
`tests/test_router_split.py` khoá, việc đó chờ người dùng đồng ý.
"""

import unicodedata
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from holocron_core.domain.repositories import AppStore
from holocron_core.domain.schemas import KnowledgeChunk
from holocron_core.routers import vault
from holocron_core.routers.deps import get_clock, get_store, get_vault
from holocron_core.services.vault import ingest_confirmed_text
from tests.vault_support import (
    ARM_PAGES,
    NOW,
    PHILOSOPHY_PAGES,
    STORE_KINDS,
    add_document,
    confirmed,
    document_id,
    open_store,
)

ARM_TOPIC = "mp.arm.cpsr.flags"
PHIL_QUOTE = "Vật chất là phạm trù triết học dùng để chỉ thực tại khách quan."


@pytest.fixture(params=STORE_KINDS)
def store(request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[AppStore]:
    yield from open_store(str(request.param), tmp_path, monkeypatch)


@pytest.fixture
def chunks(store: AppStore) -> dict[str, list[KnowledgeChunk]]:
    arm_doc, phil_doc = document_id("a"), document_id("b")
    add_document(store, arm_doc)
    add_document(store, phil_doc)
    return {
        "ET4010": ingest_confirmed_text(store, confirmed(arm_doc, ARM_PAGES, topic_keys=[ARM_TOPIC])),
        "SSH1110": ingest_confirmed_text(
            store, confirmed(phil_doc, PHILOSOPHY_PAGES, subject_code="SSH1110", chapter_code="ch1")
        ),
    }


@pytest.fixture
def client(store: AppStore) -> TestClient:
    app = FastAPI()
    app.include_router(vault.router)
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_clock] = lambda: lambda: NOW
    return TestClient(app)


def test_get_vault_is_the_store() -> None:
    from holocron_core.infrastructure.memory_store import IngestionStore

    fresh = IngestionStore()
    assert get_vault(fresh) is fresh


# --------------------------------------------------------------------------- #
# Chunk
# --------------------------------------------------------------------------- #


def test_list_chunks(client: TestClient, chunks: dict[str, list[KnowledgeChunk]]) -> None:
    response = client.get("/v1/vault/chunks", params={"subject_code": "ET4010"})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert [c["chunk_id"] for c in body["chunks"]] == [c.chunk_id for c in chunks["ET4010"]]
    first = body["chunks"][0]
    assert first["label_status"] == "proposed" and first["version"] == 1 and first["mapping_confidence"] is None

    paged = client.get("/v1/vault/chunks", params={"subject_code": "ET4010", "offset": 1, "limit": 1}).json()
    assert paged["total"] == 2 and [c["chunk_id"] for c in paged["chunks"]] == [chunks["ET4010"][1].chunk_id]
    by_topic = client.get(
        "/v1/vault/chunks", params=[("subject_code", "ET4010"), ("topic_key", "x.y.z"), ("topic_key", ARM_TOPIC)]
    ).json()
    assert by_topic["total"] == 2
    assert client.get("/v1/vault/chunks", params={"subject_code": "ET4010", "chapter_code": "ch9"}).json() == {
        "total": 0,
        "chunks": [],
    }
    confirmed_only = client.get("/v1/vault/chunks", params={"subject_code": "ET4010", "label_status": "confirmed"})
    assert confirmed_only.json()["total"] == 0


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"subject_code": "et4010"},
        {"subject_code": "ET4010", "limit": 0},
        {"subject_code": "ET4010", "limit": 201},
        {"subject_code": "ET4010", "offset": -1},
        {"subject_code": "ET4010", "chapter_code": "chuong3"},
        {"subject_code": "ET4010", "topic_key": "Sai Mau"},
        {"subject_code": "ET4010", "label_status": "draft"},
    ],
)
def test_list_chunks_validation(client: TestClient, params: dict[str, Any]) -> None:
    assert client.get("/v1/vault/chunks", params=params).status_code == 422


def test_patch_labels(client: TestClient, chunks: dict[str, list[KnowledgeChunk]]) -> None:
    chunk_id = chunks["ET4010"][0].chunk_id
    url = f"/v1/vault/chunks/{chunk_id}/labels"
    body = {"editor_id": "u1", "expected_version": 1, "category": "PRACTICE", "topic_keys": ["mp.arm.alu.add"]}
    response = client.patch(url, json=body)
    assert response.status_code == 200
    patched = response.json()
    assert patched["label_status"] == "confirmed" and patched["version"] == 2
    assert patched["category"] == "PRACTICE" and patched["tags"] == ["#practice_example"]
    assert patched["topic_keys"] == ["mp.arm.alu.add"]
    assert patched["raw_text"] == chunks["ET4010"][0].raw_text

    stale = client.patch(url, json={"editor_id": "u2", "expected_version": 1, "chapter_code": "ch4"})
    assert stale.status_code == 409 and stale.json()["detail"] == "version lệch: hiện tại 2"
    missing = client.patch("/v1/vault/chunks/chk_missing/labels", json={"editor_id": "u", "expected_version": 1})
    assert missing.status_code == 404
    for bad in (
        {"editor_id": "u", "expected_version": 0},
        {"editor_id": "u", "expected_version": 2, "chapter_code": "3"},
        {"editor_id": "u", "expected_version": 2, "topic_keys": ["Sai"]},
        {"editor_id": "u", "expected_version": 2, "raw_text": "sửa văn bản"},
    ):
        assert client.patch(url, json=bad).status_code == 422, bad


# --------------------------------------------------------------------------- #
# Tìm kiếm
# --------------------------------------------------------------------------- #


def test_search(client: TestClient, chunks: dict[str, list[KnowledgeChunk]]) -> None:
    response = client.get("/v1/vault/search", params={"q": "cờ trạng thái", "subject_code": "ET4010"})
    assert response.status_code == 200
    hits = response.json()
    assert [hit["chunk"]["chunk_id"] for hit in hits] == [c.chunk_id for c in chunks["ET4010"]]
    assert all(hit["index"] == "primary" and hit["score"] > 0 for hit in hits)

    fallback = client.get("/v1/vault/search", params={"q": "co trang thai", "subject_code": "ET4010", "limit": 1})
    assert [hit["index"] for hit in fallback.json()] == ["no_diacritics"]
    other_subject = client.get("/v1/vault/search", params={"q": "cờ trạng thái", "subject_code": "SSH1110"})
    assert other_subject.json() == []
    filtered = client.get("/v1/vault/search", params=[("q", "cờ"), ("subject_code", "ET4010"), ("topic_key", "x.y.z")])
    assert filtered.json() == []


@pytest.mark.parametrize(
    "params",
    [
        {"subject_code": "ET4010"},
        {"q": "", "subject_code": "ET4010"},
        {"q": "x" * 201, "subject_code": "ET4010"},
        {"q": "cờ"},
        {"q": "cờ", "subject_code": "ET4010", "limit": 51},
    ],
)
def test_search_validation(client: TestClient, params: dict[str, Any]) -> None:
    assert client.get("/v1/vault/search", params=params).status_code == 422


# --------------------------------------------------------------------------- #
# Cặp khái niệm
# --------------------------------------------------------------------------- #


def _create(client: TestClient, chunk_id: str, term: str, quote: str) -> Any:
    return client.post(
        "/v1/vault/concept-pairs",
        json={"editor_id": "u1", "chunk_id": chunk_id, "term": term, "definition_quote": quote},
    )


def test_create_confirm_reject_and_list_pairs(client: TestClient, chunks: dict[str, list[KnowledgeChunk]]) -> None:
    chunk_id = chunks["SSH1110"][0].chunk_id
    created = _create(client, chunk_id, "Vật chất", unicodedata.normalize("NFD", PHIL_QUOTE))
    assert created.status_code == 201
    pair = created.json()
    assert pair["status"] == "confirmed" and pair["proposed_by"] == "user"
    assert pair["definition_quote"] == PHIL_QUOTE
    assert pair["term_spans"] == [[0, 8]]
    assert pair["created_at"] == pair["confirmed_at"] == "2026-10-25T08:00:00Z"
    assert (pair["subject_code"], pair["chapter_code"], pair["version"]) == ("SSH1110", "ch1", 1)

    second = _create(client, chunk_id, "Ý thức", "Ý thức là sự phản ánh năng động").json()
    listed = client.get("/v1/vault/concept-pairs", params={"subject_code": "SSH1110"}).json()
    assert [p["pair_id"] for p in listed] == [pair["pair_id"], second["pair_id"]]
    assert client.get("/v1/vault/concept-pairs", params={"subject_code": "ET4010"}).json() == []

    url = f"/v1/vault/concept-pairs/{pair['pair_id']}"
    rejected = client.post(f"{url}/reject", json={"editor_id": "u1", "expected_version": 1})
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected" and rejected.json()["confirmed_at"] is None
    only_confirmed = client.get("/v1/vault/concept-pairs", params={"subject_code": "SSH1110", "status": "confirmed"})
    assert [p["pair_id"] for p in only_confirmed.json()] == [second["pair_id"]]

    reconfirmed = client.post(
        f"{url}/confirm", json={"editor_id": "u1", "expected_version": 2, "term": "Phạm trù vật chất"}
    )
    assert reconfirmed.status_code == 200
    assert reconfirmed.json()["status"] == "confirmed" and reconfirmed.json()["version"] == 3
    assert reconfirmed.json()["term"] == "Phạm trù vật chất"

    assert client.post(f"{url}/confirm", json={"editor_id": "u1", "expected_version": 2}).status_code == 409
    assert client.post(f"{url}/reject", json={"editor_id": "u1", "expected_version": 2}).status_code == 409
    by_chapter = client.get("/v1/vault/concept-pairs", params={"subject_code": "SSH1110", "chapter_code": "ch2"})
    assert by_chapter.json() == []


def test_pair_quote_not_verbatim_is_422_and_not_stored(
    client: TestClient, chunks: dict[str, list[KnowledgeChunk]]
) -> None:
    chunk_id = chunks["SSH1110"][0].chunk_id
    response = _create(client, chunk_id, "Vật chất", "Vật chất là phạm trù kinh tế học.")
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "definition_quote"]
    assert "I3" in response.json()["detail"][0]["msg"]
    assert client.get("/v1/vault/concept-pairs", params={"subject_code": "SSH1110"}).json() == []

    pair = _create(client, chunk_id, "Vật chất", PHIL_QUOTE).json()
    edit = client.post(
        f"/v1/vault/concept-pairs/{pair['pair_id']}/confirm",
        json={"editor_id": "u1", "expected_version": 1, "definition_quote": "không có trong văn bản chunk"},
    )
    assert edit.status_code == 422
    assert edit.json()["detail"][0]["loc"] == ["body", "definition_quote"]


def test_pair_errors(client: TestClient, chunks: dict[str, list[KnowledgeChunk]]) -> None:
    chunk_id = chunks["SSH1110"][0].chunk_id
    assert _create(client, "chk_missing", "x", PHIL_QUOTE).status_code == 404
    assert _create(client, chunk_id, "", PHIL_QUOTE).status_code == 422
    assert _create(client, chunk_id, "x", "ngắn").status_code == 422
    # Đủ 8 ký tự trước khi gộp khoảng trắng nhưng không còn đủ sau khi gộp: model báo 422 dạng danh sách lỗi.
    collapsed = _create(client, chunk_id, "Vật", "Vật    ch")
    assert collapsed.status_code == 422
    assert collapsed.json()["detail"][0]["loc"] == ["body", "definition_quote"]
    assert client.get("/v1/vault/concept-pairs", params={"subject_code": "sai"}).status_code == 422
    assert client.get("/v1/vault/concept-pairs", params={"subject_code": "SSH1110", "status": "x"}).status_code == 422
    missing = {"editor_id": "u", "expected_version": 1}
    assert client.post("/v1/vault/concept-pairs/cp_missing/confirm", json=missing).status_code == 404
    assert client.post("/v1/vault/concept-pairs/cp_missing/reject", json=missing).status_code == 404

    pair = _create(client, chunk_id, "Vật chất", PHIL_QUOTE).json()
    too_short = client.post(
        f"/v1/vault/concept-pairs/{pair['pair_id']}/confirm",
        json={"editor_id": "u", "expected_version": 1, "definition_quote": "Vật    ch"},
    )
    assert too_short.status_code == 422
