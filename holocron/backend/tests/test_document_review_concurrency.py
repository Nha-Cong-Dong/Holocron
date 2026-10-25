"""Đồng thời ở sửa dòng và xác nhận (SPECIFICATION §6.5): nhiều request cùng version chỉ một bên thắng, xác nhận song
song không nhập Vault trùng. Luồng xuất phát cùng lúc bằng `threading.Barrier`, mọi lần chờ đều có hạn.
"""

import hashlib
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from holocron_core.domain.documents import PageExtraction
from holocron_core.routers.api import app
from holocron_core.services.document_import import SynchronousImportQueue
from tests.document_import_support import FakePdfReader, ImportEnv, install, pdf_bytes
from tests.vault_support import STORE_KINDS, open_store

WAIT_S = 20.0
THREADS = 6


@pytest.fixture(params=STORE_KINDS)
def env(request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ImportEnv]:
    for store in open_store(str(request.param), tmp_path, monkeypatch):
        yield install(store, tmp_path / "files", SynchronousImportQueue(), FakePdfReader(page_count=3))
        app.dependency_overrides.clear()


def _accepted(env: ImportEnv, tag: str) -> str:
    content = pdf_bytes(tag)
    assert env.post(content).status_code == 202
    return "doc_" + hashlib.sha256(content).hexdigest()[:16]


def _run_together(calls: list[Callable[[], int]]) -> list[int]:
    barrier = threading.Barrier(len(calls))
    results: list[int] = []
    guard = threading.Lock()

    def run(call: Callable[[], int]) -> None:
        barrier.wait(WAIT_S)
        status = call()
        with guard:
            results.append(status)

    threads = [threading.Thread(target=run, args=(call,)) for call in calls]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(WAIT_S)
        assert not thread.is_alive(), "request treo"
    return sorted(results)


def _client() -> TestClient:
    return TestClient(app)


def test_concurrent_edits_with_one_version_have_exactly_one_winner(env: ImportEnv) -> None:
    doc_id = _accepted(env, "sua-song-song")
    page = PageExtraction.model_validate(env.client.get(f"/v1/documents/{doc_id}/pages/1").json())

    def edit(text: str) -> Callable[[], int]:
        def call() -> int:
            body: dict[str, Any] = {
                "editor_id": "u",
                "expected_version": page.version,
                "edits": [{"line_index": 0, "text": text}],
            }
            return _client().patch(f"/v1/documents/{doc_id}/pages/1/lines", json=body).status_code

        return call

    results = _run_together([edit(f"bản sửa {i}") for i in range(THREADS)])
    assert results == [200] + [409] * (THREADS - 1)
    audits = env.store.list_page_audits(doc_id, 1)
    assert len(audits) == 1
    stored = PageExtraction.model_validate(env.client.get(f"/v1/documents/{doc_id}/pages/1").json())
    assert stored.version == page.version + 1 and stored.lines[0].text == audits[0].changes[0].new


def test_concurrent_confirms_ingest_the_document_once(env: ImportEnv) -> None:
    doc_id = _accepted(env, "xac-nhan-song-song")
    pages = [
        PageExtraction.model_validate(env.client.get(f"/v1/documents/{doc_id}/pages/{n}").json()) for n in (1, 2, 3)
    ]

    def confirm_page(page: PageExtraction) -> Callable[[], int]:
        def call() -> int:
            body = {"editor_id": "u", "expected_version": page.version}
            url = f"/v1/documents/{doc_id}/pages/{page.page_number}/confirm"
            return _client().post(url, json=body).status_code

        return call

    def confirm_document() -> int:
        return _client().post(f"/v1/documents/{doc_id}/confirm", json={"editor_id": "u"}).status_code

    calls = [confirm_page(page) for page in pages] + [confirm_document, confirm_document, confirm_document]
    results = _run_together(calls)
    assert all(status in (200, 409) for status in results)
    assert results.count(200) >= 3, "xác nhận tài liệu luôn thành công; trang có thể 409 khi bị xác nhận theo lô trước"
    document = env.client.get(f"/v1/documents/{doc_id}").json()
    assert document["status"] == "confirmed"
    assert all(page["confirmed"] for page in document["pages"])
    chunks = env.store.list_chunks("ET4010")
    assert chunks and len({c.chunk_id for c in chunks}) == len(chunks)
    again = env.client.post(f"/v1/documents/{doc_id}/confirm", json={"editor_id": "u"})
    assert again.status_code == 200 and env.store.list_chunks("ET4010") == chunks
