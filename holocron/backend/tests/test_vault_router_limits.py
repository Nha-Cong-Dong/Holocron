"""`routers/vault.py`: giới hạn độ dài đầu vào và việc lưu người sửa (hàng Repudiation của §13).

Router được gắn vào một app FastAPI riêng cho test, như `tests/test_vault_router.py`.
"""

from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from holocron_core.domain.repositories import AppStore
from holocron_core.domain.schemas import KnowledgeChunk
from holocron_core.domain.vault import VaultEdit
from holocron_core.routers import vault
from holocron_core.routers.deps import get_clock, get_store
from holocron_core.services.vault import ingest_confirmed_text
from tests.vault_support import NOW, PHILOSOPHY_PAGES, STORE_KINDS, add_document, confirmed, document_id, open_store

PHIL_QUOTE = "Vật chất là phạm trù triết học dùng để chỉ thực tại khách quan."
LATER = NOW + timedelta(hours=1)


@pytest.fixture(params=STORE_KINDS)
def store(request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[AppStore]:
    yield from open_store(str(request.param), tmp_path, monkeypatch)


@pytest.fixture
def chunk(store: AppStore) -> KnowledgeChunk:
    doc_id = document_id("f")
    add_document(store, doc_id)
    return ingest_confirmed_text(
        store, confirmed(doc_id, PHILOSOPHY_PAGES, subject_code="SSH1110", chapter_code="ch1")
    )[0]


def _client(store: AppStore, moment: Any = NOW) -> TestClient:
    app = FastAPI()
    app.include_router(vault.router)
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_clock] = lambda: lambda: moment
    return TestClient(app)


@pytest.fixture
def client(store: AppStore) -> TestClient:
    return _client(store)


def _labels_url(chunk_id: str) -> str:
    return f"/v1/vault/chunks/{chunk_id}/labels"


@pytest.mark.parametrize(
    "editor_id",
    ["", "u" * 129],
    ids=["empty", "too_long"],
)
def test_editor_id_is_bounded_on_every_write(client: TestClient, chunk: KnowledgeChunk, editor_id: str) -> None:
    labels = client.patch(_labels_url(chunk.chunk_id), json={"editor_id": editor_id, "expected_version": 1})
    assert labels.status_code == 422
    create = client.post(
        "/v1/vault/concept-pairs",
        json={"editor_id": editor_id, "chunk_id": chunk.chunk_id, "term": "Vật chất", "definition_quote": PHIL_QUOTE},
    )
    assert create.status_code == 422
    for action in ("confirm", "reject"):
        response = client.post(
            f"/v1/vault/concept-pairs/cp_x/{action}", json={"editor_id": editor_id, "expected_version": 1}
        )
        assert response.status_code == 422, action
    assert client.patch(_labels_url(chunk.chunk_id), json={"editor_id": "u" * 128, "expected_version": 1}).is_success


def test_path_ids_are_bounded(client: TestClient, chunk: KnowledgeChunk) -> None:
    long_id = "c" * 129
    assert client.patch(_labels_url(long_id), json={"editor_id": "u", "expected_version": 1}).status_code == 422
    for action in ("confirm", "reject"):
        response = client.post(
            f"/v1/vault/concept-pairs/{long_id}/{action}", json={"editor_id": "u", "expected_version": 1}
        )
        assert response.status_code == 422, action
    # Đúng giới hạn thì qua validate và nhận 404 như thường.
    assert client.patch(_labels_url("c" * 128), json={"editor_id": "u", "expected_version": 1}).status_code == 404
    assert (
        client.post(
            f"/v1/vault/concept-pairs/{'p' * 128}/reject", json={"editor_id": "u", "expected_version": 1}
        ).status_code
        == 404
    )


def test_topic_keys_are_bounded_in_body_and_query(client: TestClient, chunk: KnowledgeChunk) -> None:
    url = _labels_url(chunk.chunk_id)
    too_many = [f"phil.topic.k{index}" for index in range(33)]
    too_long = ["phil.topic." + "k" * 120]
    for topic_keys in (too_many, too_long):
        body = {"editor_id": "u", "expected_version": 1, "topic_keys": topic_keys}
        assert client.patch(url, json=body).status_code == 422
    for path in ("/v1/vault/chunks", "/v1/vault/search"):
        params: list[tuple[str, str]] = [("subject_code", "SSH1110"), ("q", "vật chất")]
        assert client.get(path, params=params + [("topic_key", key) for key in too_many]).status_code == 422, path
        assert client.get(path, params=params + [("topic_key", too_long[0])]).status_code == 422, path
        assert client.get(path, params=params + [("topic_key", key) for key in too_many[:32]]).status_code == 200, path
    ok = client.patch(url, json={"editor_id": "u", "expected_version": 1, "topic_keys": too_many[:32]})
    assert ok.status_code == 200 and len(ok.json()["topic_keys"]) == 32


def test_label_patch_records_the_editor(store: AppStore, client: TestClient, chunk: KnowledgeChunk) -> None:
    response = client.patch(_labels_url(chunk.chunk_id), json={"editor_id": "giang_vien_1", "expected_version": 1})
    assert response.status_code == 200
    assert "editor_id" not in response.json()  # contract của response không đổi
    assert store.get_last_edit("chunk", chunk.chunk_id) == VaultEdit(editor_id="giang_vien_1", at=NOW)
    stale = client.patch(_labels_url(chunk.chunk_id), json={"editor_id": "nguoi_khac", "expected_version": 1})
    assert stale.status_code == 409
    assert store.get_last_edit("chunk", chunk.chunk_id) == VaultEdit(editor_id="giang_vien_1", at=NOW)


def test_pair_create_confirm_reject_record_the_editor(store: AppStore, chunk: KnowledgeChunk) -> None:
    created = _client(store).post(
        "/v1/vault/concept-pairs",
        json={"editor_id": "u_create", "chunk_id": chunk.chunk_id, "term": "Vật chất", "definition_quote": PHIL_QUOTE},
    )
    assert created.status_code == 201
    pair_id = created.json()["pair_id"]
    assert store.get_last_edit("concept_pair", pair_id) == VaultEdit(editor_id="u_create", at=NOW)

    later = _client(store, LATER)
    confirmed_pair = later.post(
        f"/v1/vault/concept-pairs/{pair_id}/confirm", json={"editor_id": "u_confirm", "expected_version": 1}
    )
    assert confirmed_pair.status_code == 200
    assert store.get_last_edit("concept_pair", pair_id) == VaultEdit(editor_id="u_confirm", at=LATER)

    rejected = later.post(
        f"/v1/vault/concept-pairs/{pair_id}/reject", json={"editor_id": "u_reject", "expected_version": 2}
    )
    assert rejected.status_code == 200 and rejected.json()["status"] == "rejected"
    assert store.get_last_edit("concept_pair", pair_id) == VaultEdit(editor_id="u_reject", at=LATER)

    mismatch = later.post(
        "/v1/vault/concept-pairs",
        json={
            "editor_id": "u_bad",
            "chunk_id": chunk.chunk_id,
            "term": "x",
            "definition_quote": "không có trong chunk",
        },
    )
    assert mismatch.status_code == 422
    assert mismatch.json()["detail"][0]["loc"] == ["body", "definition_quote"]
