"""Đồng thời ở `POST /v1/documents` (SPECIFICATION §6.5): nhận file chạy ngoài event loop và hai request cùng file
không tạo trùng. Không có sleep dài: luồng được đồng bộ bằng `threading.Event`, mọi lần chờ đều có hạn.
"""

import contextlib
import hashlib
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from holocron_core.domain.documents import DocumentImport, PageExtraction
from holocron_core.infrastructure.storage import DATA_DIR_ENV
from holocron_core.routers import deps
from holocron_core.routers.api import app
from holocron_core.services.document_import import KeyedLocks, SynchronousImportQueue
from tests.document_import_support import FakePdfReader, ImportEnv, install, pdf_bytes
from tests.vault_support import STORE_KINDS, open_store

WAIT_S = 10.0


@dataclass
class BlockingProbeReader(FakePdfReader):
    """`probe` báo đã vào (`entered`) rồi chờ `release`, như worker mở thử một PDF chậm."""

    entered: threading.Event = field(default_factory=threading.Event)
    release: threading.Event = field(default_factory=threading.Event)

    def probe(self, path: Path) -> int:
        self.entered.set()
        assert self.release.wait(WAIT_S), "test không thả bộ đọc"
        return super().probe(path)


class ObservedLocks(KeyedLocks):
    """`KeyedLocks` báo khi có người thứ hai xin khoá (ngay trước khi người đó phải chờ)."""

    def __init__(self) -> None:
        super().__init__()
        self.second_holder = threading.Event()
        self._count = 0
        self._count_guard = threading.Lock()

    @contextlib.contextmanager
    def hold(self, key: str) -> Iterator[None]:
        with self._count_guard:
            self._count += 1
            if self._count == 2:
                self.second_holder.set()
        with super().hold(key):
            yield


@pytest.fixture
def lifespan_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """`TestClient` dùng như context manager chạy mọi request trên MỘT event loop (như uvicorn); kho mặc định của
    `lifespan` là in-memory, được bỏ khỏi cache trước và sau test."""
    monkeypatch.delenv(DATA_DIR_ENV, raising=False)
    deps._default_store.cache_clear()
    yield
    deps._default_store.cache_clear()


@pytest.fixture(params=STORE_KINDS)
def env(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, lifespan_client: None
) -> Iterator[ImportEnv]:
    for store in open_store(str(request.param), tmp_path, monkeypatch):
        yield install(
            store,
            tmp_path / "files",
            SynchronousImportQueue(),
            BlockingProbeReader(),
            locks=ObservedLocks(),
            subject_locks=ObservedLocks(),
        )
        app.dependency_overrides.clear()


def _post(client: TestClient, content: bytes, subject_code: str, results: list[Response]) -> None:
    results.append(
        client.post(
            "/v1/documents",
            data={"subject_code": subject_code, "source_kind": "lecture_slides", "chapter_code": "ch3"},
            files={"file": ("tai_lieu.pdf", content, "application/pdf")},
        )
    )


def _reader(env: ImportEnv) -> BlockingProbeReader:
    assert isinstance(env.reader, BlockingProbeReader)
    return env.reader


@pytest.mark.parametrize(("second_subject", "expected"), [("ET4010", [200, 202]), ("IT3070", [202, 409])])
def test_two_concurrent_posts_of_one_file_create_one_document(
    env: ImportEnv, second_subject: str, expected: list[int]
) -> None:
    content = pdf_bytes("song-song")
    document_id = "doc_" + hashlib.sha256(content).hexdigest()[:16]
    reader, locks = _reader(env), env.runtime.locks
    assert isinstance(locks, ObservedLocks)
    first: list[Response] = []
    second: list[Response] = []
    with TestClient(app) as client:
        first_thread = threading.Thread(target=_post, args=(client, content, "ET4010", first))
        second_thread = threading.Thread(target=_post, args=(client, content, second_subject, second))
        try:
            first_thread.start()
            assert reader.entered.wait(WAIT_S), "request đầu chưa tới bước mở thử"
            second_thread.start()
            # Sửa đổi 2026-10-31 (§6.5 bước 5, GĐ1 1A): khoá môn lấy TRƯỚC khoá tài liệu, nên cùng môn thì request thứ
            # hai chờ ở khoá môn; khác môn thì chờ ở khoá tài liệu như trước.
            waiting = env.runtime.subject_locks if second_subject == "ET4010" else locks
            assert isinstance(waiting, ObservedLocks)
            assert waiting.second_holder.wait(WAIT_S), "request thứ hai chưa xin khoá của môn hoặc của tài liệu"
        finally:
            reader.release.set()
            first_thread.join(WAIT_S)
            second_thread.join(WAIT_S)
    assert not first_thread.is_alive() and not second_thread.is_alive()
    assert first[0].status_code == 202, first[0].text
    assert sorted([first[0].status_code, second[0].status_code]) == expected, second[0].text
    assert len(reader.probed) == 1, "request thứ hai không mở thử lại khi request đầu đang giữ khoá"
    assert env.queue.submitted == [document_id]
    assert [d.meta.doc_id for d in env.store.list_imported_documents()] == [document_id]
    stored = env.store.get_imported_document(document_id)
    assert stored is not None and stored.meta.subject_code == "ET4010" and stored.status == "review"
    assert [p.status for p in env.store.list_pages(document_id, PageExtraction)] == ["ok", "ok"]
    assert sorted(path.name for path in env.files.root.iterdir()) == [f"{stored.meta.sha256}.pdf"]
    if second[0].status_code == 200:
        assert DocumentImport.model_validate(second[0].json()).meta.doc_id == document_id
    assert len(locks) == 0, "khoá được bỏ khi không còn ai giữ"
    assert len(env.runtime.subject_locks) == 0, "khoá môn được bỏ khi không còn ai giữ"


def test_other_requests_are_served_while_a_probe_is_slow(env: ImportEnv) -> None:
    reader = _reader(env)
    posted: list[Response] = []
    served: list[Response] = []
    with TestClient(app) as client:
        poster = threading.Thread(target=_post, args=(client, pdf_bytes("cham"), "ET4010", posted))
        getter = threading.Thread(target=lambda: served.append(client.get("/v1/runtime")))
        try:
            poster.start()
            assert reader.entered.wait(WAIT_S), "request nhập chưa tới bước mở thử"
            getter.start()
            getter.join(WAIT_S)
            assert not getter.is_alive(), "GET /v1/runtime bị chặn khi worker đang mở thử (event loop bị chặn)"
            assert served[0].status_code == 200
            assert posted == [], "request nhập vẫn đang chờ bộ đọc"
        finally:
            reader.release.set()
            poster.join(WAIT_S)
            getter.join(WAIT_S)
    assert posted[0].status_code == 202, posted[0].text


def test_locks_of_different_documents_do_not_block_each_other() -> None:
    locks = KeyedLocks()
    inside = threading.Event()
    with locks.hold("doc_a"):
        worker = threading.Thread(target=lambda: _enter(locks, "doc_b", inside))
        worker.start()
        assert inside.wait(WAIT_S), "khoá của tài liệu khác không phải chờ"
        worker.join(WAIT_S)
        assert len(locks) == 1
    assert len(locks) == 0


def _enter(locks: KeyedLocks, key: str, inside: threading.Event) -> None:
    with locks.hold(key):
        inside.set()
