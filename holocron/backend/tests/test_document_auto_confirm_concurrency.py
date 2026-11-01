"""Tự xác nhận (mặc định bật) với nhập đồng thời (SPECIFICATION §5.7, §6.5): hai request cùng file chỉ tự xác nhận
một lần, hai tài liệu cùng môn có bìa trùng nhập song song thì sau cùng cả hai bìa bị bỏ mềm và không bìa nào còn dấu
tự xác nhận, bất kể thứ tự hoàn thành. Dựa `tests/test_document_import_concurrency.py`; luồng đồng bộ bằng
`threading.Event`, mọi lần chờ đều có hạn. Văn bản là mẫu tự viết."""

from __future__ import annotations

import contextlib
import hashlib
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from holocron_core.domain.documents import PageExtraction
from holocron_core.infrastructure.storage import DATA_DIR_ENV
from holocron_core.routers import deps
from holocron_core.routers.api import app
from holocron_core.services.document_import import BackgroundImportQueue, KeyedLocks, SynchronousImportQueue
from tests.document_import_support import FakePdfReader, ImportEnv, install, pdf_bytes
from tests.test_document_auto_confirm import EDITOR, _text_page, clean_page
from tests.test_document_import_concurrency import WAIT_S, BlockingProbeReader, ObservedLocks
from tests.vault_support import STORE_KINDS, open_store

COVER = ["Truong dai hoc mau", "Khoa dien tu vien thong", "Mon vi xu ly", "Giang vien mau", "Hoc ky mau nam"]


@pytest.fixture
def lifespan_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """`TestClient` dùng như context manager chạy mọi request trên MỘT event loop; kho mặc định của `lifespan` là
    in-memory, được bỏ khỏi cache trước và sau test."""
    monkeypatch.delenv(DATA_DIR_ENV, raising=False)
    deps._default_store.cache_clear()
    yield
    deps._default_store.cache_clear()


def _reader_with_cover() -> BlockingProbeReader:
    return BlockingProbeReader(
        page_count=3,
        custom_pages={
            1: lambda document_id: _text_page(document_id, 1, COVER),
            2: clean_page(2),
            3: clean_page(3),
        },
    )


@pytest.fixture(params=STORE_KINDS)
def env(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, lifespan_client: None
) -> Iterator[ImportEnv]:
    for store in open_store(str(request.param), tmp_path, monkeypatch):
        yield install(
            store,
            tmp_path / "files",
            SynchronousImportQueue(),
            _reader_with_cover(),
            locks=ObservedLocks(),
            subject_locks=ObservedLocks(),
        )
        app.dependency_overrides.clear()


@pytest.fixture(params=STORE_KINDS)
def background_env(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, lifespan_client: None
) -> Iterator[tuple[ImportEnv, BackgroundImportQueue]]:
    """Hàng đợi nền thật (một luồng đọc một tài liệu mỗi lúc, như bản chạy thật): luồng request chỉ giữ khoá tài liệu
    lúc nhận file, không giữ khi luồng nền đọc."""
    for store in open_store(str(request.param), tmp_path, monkeypatch):
        jobs = BackgroundImportQueue()
        try:
            yield (
                install(
                    store,
                    tmp_path / "files",
                    SynchronousImportQueue(),
                    _reader_with_cover(),
                    runtime_queue=jobs,
                ),
                jobs,
            )
        finally:
            jobs.close()
            app.dependency_overrides.clear()


def _post_default(client: TestClient, content: bytes, chapter_code: str, results: list[Response]) -> None:
    """Không gửi field `auto_confirm`: mặc định bật."""
    results.append(
        client.post(
            "/v1/documents",
            data={"subject_code": "ET4010", "source_kind": "lecture_slides", "chapter_code": chapter_code},
            files={"file": ("tai_lieu.pdf", content, "application/pdf")},
        )
    )


def _reader(env: ImportEnv) -> BlockingProbeReader:
    assert isinstance(env.reader, BlockingProbeReader)
    return env.reader


def _doc_id(content: bytes) -> str:
    return "doc_" + hashlib.sha256(content).hexdigest()[:16]


def test_two_concurrent_posts_of_one_file_auto_confirm_once(env: ImportEnv) -> None:
    content = pdf_bytes("song-song-auto")
    document_id = _doc_id(content)
    reader, locks = _reader(env), env.runtime.locks
    assert isinstance(locks, ObservedLocks)
    first: list[Response] = []
    second: list[Response] = []
    with TestClient(app) as client:
        threads = [
            threading.Thread(target=_post_default, args=(client, content, "ch3", first)),
            threading.Thread(target=_post_default, args=(client, content, "ch3", second)),
        ]
        try:
            threads[0].start()
            assert reader.entered.wait(WAIT_S), "request đầu chưa tới bước mở thử"
            threads[1].start()
            assert locks.second_holder.wait(WAIT_S), "request thứ hai chưa xin khoá của tài liệu"
        finally:
            reader.release.set()
            for thread in threads:
                thread.join(WAIT_S)
    assert not any(thread.is_alive() for thread in threads)
    assert sorted([first[0].status_code, second[0].status_code]) == [200, 202], (first[0].text, second[0].text)
    stored = env.store.get_imported_document(document_id)
    assert stored is not None and stored.auto_confirm is True and stored.status == "review"
    pages = env.store.list_pages(document_id, PageExtraction)
    assert [(p.page_number, p.confirmed_by) for p in pages] == [(1, "auto"), (2, "auto"), (3, "auto")]
    assert [p.version for p in pages] == [3, 3, 3], "đọc một lần, tự xác nhận một lần (pending v1, đọc v2, auto v3)"
    assert not env.store.has_document_chunks(document_id), "tự xác nhận không đóng tài liệu"
    assert len(reader.reads) == 1


def test_concurrent_documents_with_the_same_cover_end_with_no_auto_confirmed_cover(
    background_env: tuple[ImportEnv, BackgroundImportQueue],
) -> None:
    env, jobs = background_env
    contents = {"ch2": pdf_bytes("song-song-a"), "ch3": pdf_bytes("song-song-b")}
    reader = _reader(env)
    results: list[Response] = []
    with TestClient(app) as client:
        threads = [
            threading.Thread(target=_post_default, args=(client, content, chapter, results))
            for chapter, content in contents.items()
        ]
        try:
            for thread in threads:
                thread.start()
            assert reader.entered.wait(WAIT_S), "chưa request nào tới bước mở thử"
        finally:
            reader.release.set()
            for thread in threads:
                thread.join(WAIT_S)
        assert not any(thread.is_alive() for thread in threads)
        assert jobs.wait_until_idle(WAIT_S), "luồng nền chưa đọc xong hai tài liệu"
    assert sorted(r.status_code for r in results) == [202, 202], [r.text for r in results]
    for content in contents.values():
        document_id = _doc_id(content)
        cover = env.store.get_page(document_id, 1, PageExtraction)
        assert cover is not None
        assert (cover.page_role, cover.dropped) == ("cover_or_course_nav", True), "bìa trùng bị bỏ mềm ở cả hai"
        assert (cover.confirmed, cover.confirmed_by, cover.confirm_rule) == (False, None, None)
        others = env.store.list_pages(document_id, PageExtraction)[1:]
        assert [p.confirmed_by for p in others] == ["auto", "auto"], "trang nội dung vẫn tự xác nhận"
        assert not env.store.has_document_chunks(document_id)


class WatchedLocks(KeyedLocks):
    """`KeyedLocks` báo (`contended`) khi một lời xin khoá của `armed` tới, ngay trước khi nó phải chờ."""

    def __init__(self) -> None:
        super().__init__()
        self.armed: str | None = None
        self.contended = threading.Event()

    @contextlib.contextmanager
    def hold(self, key: str) -> Iterator[None]:
        if key == self.armed:
            self.contended.set()
        with super().hold(key):
            yield


@pytest.fixture(params=STORE_KINDS)
def watched_env(request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ImportEnv]:
    for store in open_store(str(request.param), tmp_path, monkeypatch):
        reader = FakePdfReader(page_count=1, custom_pages={1: clean_page(1)})
        yield install(store, tmp_path / "files", SynchronousImportQueue(), reader, locks=WatchedLocks())
        app.dependency_overrides.clear()


def test_unconfirm_takes_the_lock_of_its_own_document(watched_env: ImportEnv) -> None:
    """Đột biến U13: bỏ `locks.hold(document_id)` của `unconfirm_page`. Giữ khoá tài liệu ở luồng này; request
    hoàn tác phải xin đúng khoá đó và chờ (không ghi gì) tới khi nhả, rồi mới ghi."""
    env = watched_env
    locks = env.runtime.locks
    assert isinstance(locks, WatchedLocks)
    content = pdf_bytes("unconfirm-khoa")
    assert env.post(content, auto_confirm="true").status_code == 202
    document_id = _doc_id(content)
    before = env.store.get_page(document_id, 1, PageExtraction)
    assert before is not None and before.confirmed_by == "auto"
    results: list[Response] = []

    def _request() -> None:
        results.append(
            env.client.post(
                f"/v1/documents/{document_id}/pages/1/unconfirm",
                json={"editor_id": EDITOR, "expected_version": before.version},
            )
        )

    thread = threading.Thread(target=_request)
    with locks.hold(document_id):
        locks.armed = document_id
        thread.start()
        try:
            assert locks.contended.wait(WAIT_S), "request hoàn tác không xin khoá của tài liệu"
            waiting = env.store.get_page(document_id, 1, PageExtraction)
            assert waiting == before, "request hoàn tác đã ghi khi khoá tài liệu còn bị giữ"
            assert results == []
        finally:
            locks.armed = None
    thread.join(WAIT_S)
    assert not thread.is_alive()
    assert [r.status_code for r in results] == [200], [r.text for r in results]
    after = env.store.get_page(document_id, 1, PageExtraction)
    assert after is not None and (after.confirmed, after.auto_confirm_undone) == (False, True)
    assert after.version == before.version + 1
