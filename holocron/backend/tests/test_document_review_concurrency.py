"""Đồng thời ở sửa dòng và xác nhận (SPECIFICATION §6.5): nhiều request cùng version chỉ một bên thắng, xác nhận song
song không nhập Vault trùng, xác nhận trong lúc luồng đọc nền còn đọc trang khác cho trạng thái cuối nhất quán. Luồng
xuất phát cùng lúc bằng `threading.Barrier`, mọi lần chờ đều có hạn.
"""

import dataclasses
import hashlib
import threading
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from holocron_core.domain.documents import PageExtraction
from holocron_core.domain.repositories import AppStore
from holocron_core.routers.api import app
from holocron_core.services.document_import import BackgroundImportQueue, SynchronousImportQueue, confirmed_text
from holocron_core.services.vault_chunking import build_chunks
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


def _chunk_ids(store: AppStore, doc_id: str) -> list[str]:
    return sorted(chunk.chunk_id for chunk in store.list_chunks("ET4010") if chunk.doc_id == doc_id)


def _expected_chunk_ids(store: AppStore, doc_id: str) -> list[str]:
    """Kết quả cắt văn bản đã xác nhận MỘT lần bằng bộ cắt tất định (§12.1)."""
    document = store.get_imported_document(doc_id)
    assert document is not None
    text = confirmed_text(document, store.list_pages(doc_id, PageExtraction))
    assert text is not None
    ids = sorted(chunk.chunk_id for chunk in build_chunks(text))
    assert ids
    return ids


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
    # Trang đã được lượt khác xác nhận trả 200 không đổi gì (§6.5), nên mọi lượt đều 200, kể cả version cũ.
    assert results == [200] * len(calls)
    document = env.client.get(f"/v1/documents/{doc_id}").json()
    assert document["status"] == "confirmed"
    assert all(page["confirmed"] for page in document["pages"])
    assert [p.version for p in env.store.list_pages(doc_id, PageExtraction)] == [page.version + 1 for page in pages]
    assert _chunk_ids(env.store, doc_id) == _expected_chunk_ids(env.store, doc_id), "đúng một lần cắt, không trùng"
    chunks = env.store.list_chunks("ET4010")
    again = env.client.post(f"/v1/documents/{doc_id}/confirm", json={"editor_id": "u"})
    assert again.status_code == 200 and env.store.list_chunks("ET4010") == chunks


# --------------------------------------------------------------------------- #
# Xác nhận trong lúc luồng đọc nền (`BackgroundImportQueue`) còn đọc trang khác


@dataclasses.dataclass
class PausingReader(FakePdfReader):
    """Đọc trang đầu, rồi dừng trước trang thứ hai cho tới khi test cho đi tiếp (`proceed`).

    `paused` được đặt khi trang đầu ĐÃ được ghi vào kho (luồng đọc ghi trang rồi mới xin trang kế tiếp).
    """

    paused: threading.Event = dataclasses.field(default_factory=threading.Event)
    proceed: threading.Event = dataclasses.field(default_factory=threading.Event)

    def read_pages(
        self, path: Path, document_id: str, page_numbers: Sequence[int] | None = None
    ) -> Iterator[PageExtraction]:
        for index, page in enumerate(super().read_pages(path, document_id, page_numbers)):
            if index == 1:
                self.paused.set()
                if not self.proceed.wait(WAIT_S):
                    raise RuntimeError("test không cho luồng đọc đi tiếp")
            yield page


@dataclasses.dataclass
class BackgroundEnv:
    env: ImportEnv
    queue: BackgroundImportQueue
    reader: PausingReader


@pytest.fixture(params=STORE_KINDS)
def background(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[BackgroundEnv]:
    for store in open_store(str(request.param), tmp_path, monkeypatch):
        queue = BackgroundImportQueue()
        reader = PausingReader(page_count=3, failed_pages=frozenset({2, 3}))
        env = install(store, tmp_path / "files", SynchronousImportQueue(), reader, runtime_queue=queue)
        try:
            yield BackgroundEnv(env=env, queue=queue, reader=reader)
        finally:
            reader.proceed.set()
            assert queue.close(WAIT_S), "luồng đọc nền không dừng"
            app.dependency_overrides.clear()


def _confirm_first_page(env: ImportEnv, doc_id: str) -> int:
    page = PageExtraction.model_validate(env.client.get(f"/v1/documents/{doc_id}/pages/1").json())
    body = {"editor_id": "u", "expected_version": page.version}
    return _client().post(f"/v1/documents/{doc_id}/pages/1/confirm", json=body).status_code


def _assert_settled_once(env: ImportEnv, doc_id: str) -> None:
    """Trạng thái cuối nhất quán: trang 1 xác nhận, trang 2–3 `failed`, tài liệu `confirmed`, Vault có đúng kết quả
    của một lần cắt."""
    document = env.client.get(f"/v1/documents/{doc_id}").json()
    assert document["status"] == "confirmed"
    assert [(p["status"], p["confirmed"]) for p in document["pages"]] == [
        ("ok", True),
        ("failed", False),
        ("failed", False),
    ]
    assert document["meta"]["has_text_layer"] is True
    assert _chunk_ids(env.store, doc_id) == _expected_chunk_ids(env.store, doc_id)


def test_confirm_while_the_background_reader_reads_other_pages(background: BackgroundEnv) -> None:
    env, queue, reader = background.env, background.queue, background.reader
    doc_id = _accepted(env, "xac-nhan-khi-doc-nen")
    assert reader.paused.wait(WAIT_S), "luồng đọc nền chưa đọc xong trang 1"
    assert _confirm_first_page(env, doc_id) == 200
    assert env.client.get(f"/v1/documents/{doc_id}").json()["status"] == "processing"
    pending = env.client.post(f"/v1/documents/{doc_id}/confirm", json={"editor_id": "u"})
    assert pending.status_code == 409, "còn trang đang đọc"
    assert env.store.list_chunks("ET4010") == []
    reader.proceed.set()
    assert queue.wait_until_idle(WAIT_S)
    _assert_settled_once(env, doc_id)


def test_confirm_racing_the_end_of_background_reading(background: BackgroundEnv) -> None:
    env, queue, reader = background.env, background.queue, background.reader
    doc_id = _accepted(env, "dua-voi-doc-nen")
    assert reader.paused.wait(WAIT_S)

    def let_reader_finish() -> int:
        reader.proceed.set()
        return 0

    results = _run_together([let_reader_finish, lambda: _confirm_first_page(env, doc_id)])
    assert results == [0, 200]
    assert queue.wait_until_idle(WAIT_S)
    _assert_settled_once(env, doc_id)
