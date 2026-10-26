"""Bộ nhập tài liệu (SPECIFICATION §6.5): hàng đợi đọc nền FIFO, dừng giữa chừng, xếp lại sau khởi động, `lifespan`
của app và một test tích hợp đọc PDF thật tự sinh qua worker thật (chỉ tầng 1).
"""

import hashlib
import threading
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from holocron_core.domain.documents import DocumentImport, PageExtraction
from holocron_core.domain.repositories import AppStore, DocumentRepository
from holocron_core.domain.schemas import SourceKind
from holocron_core.infrastructure.document_readers.config import PdfReaderConfig
from holocron_core.infrastructure.document_readers.pdf_reader import WorkerPdfReader
from holocron_core.infrastructure.document_readers.tiers import failed_page
from holocron_core.infrastructure.llm_config import ConfigError
from holocron_core.infrastructure.storage import DATA_DIR_ENV, DATABASE_FILENAME, SQLiteStore
from holocron_core.infrastructure.storage.original_files import OriginalFileStore
from holocron_core.routers import deps, documents
from holocron_core.routers.api import app, get_clock, get_store
from holocron_core.services.document_import import (
    BackgroundImportQueue,
    ImportRequest,
    ImportRuntime,
    ReadJob,
    SynchronousImportQueue,
    accept_document,
    read_document,
    requeue_pending,
)
from tests.document_import_support import FakePdfReader, ok_page, pdf_bytes
from tests.pdf_samples import Page, text_lines, write_pdf
from tests.vault_support import NOW, STORE_KINDS, open_store

WAIT_S = 10.0
BODY = [
    "Each lecture note in this sample was written for the importer tests only.",
    "The text layer must be long enough so that the page score passes the bar.",
    "A register holds a small amount of data close to the arithmetic logic unit.",
    "The status register keeps the flags that the last instruction has produced.",
]


@pytest.fixture(params=STORE_KINDS)
def store(request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[AppStore]:
    yield from open_store(str(request.param), tmp_path, monkeypatch)


@pytest.fixture
def fresh_default_store() -> Iterator[None]:
    """Bỏ kho mặc định đã mở trong tiến trình test, rồi khôi phục sau test (như `test_storage_sqlite.py`)."""
    deps._default_store.cache_clear()
    yield
    deps._default_store.cache_clear()


def _accept(
    store: DocumentRepository,
    files: OriginalFileStore,
    reader: FakePdfReader,
    tag: str,
    queue: SynchronousImportQueue | None = None,
) -> str:
    """Nhận một tài liệu; mặc định hàng đợi không chạy việc nên tài liệu giữ `processing`. Trả `document_id`."""
    jobs = SynchronousImportQueue(run=lambda job: None) if queue is None else queue
    outcome = accept_document(
        ImportRequest(
            content=pdf_bytes(tag),
            filename=f"{tag}.pdf",
            declared_type="application/pdf",
            subject_code="ET4010",
            source_kind=SourceKind.TEXTBOOK,
        ),
        store=store,
        runtime=ImportRuntime(files=files, queue=jobs),
        reader=reader,
        now=NOW,
    )
    return outcome.document.meta.doc_id


def _status(store: DocumentRepository, document_id: str) -> str:
    document = store.get_imported_document(document_id)
    assert document is not None
    return document.status


def _page_statuses(store: DocumentRepository, document_id: str) -> list[str]:
    return [page.status for page in store.list_pages(document_id, PageExtraction)]


def _job(store: DocumentRepository, files: OriginalFileStore, reader: FakePdfReader, document_id: str) -> ReadJob:
    document = store.get_imported_document(document_id)
    assert document is not None
    return ReadJob(document_id=document_id, path=files.path_for(document.meta.sha256), store=store, reader=reader)


# --------------------------------------------------------------------------- #
# Hàng đợi nền
# --------------------------------------------------------------------------- #


def test_background_queue_reads_one_document_at_a_time_in_fifo_order(store: AppStore, tmp_path: Path) -> None:
    files, reader = OriginalFileStore(tmp_path / "files"), FakePdfReader()
    ids = [_accept(store, files, reader, tag) for tag in ("a", "b", "c")]
    running = threading.Lock()
    order: list[str] = []

    def run(job: ReadJob, should_stop: Callable[[], bool]) -> None:
        assert running.acquire(blocking=False), "hai tài liệu không được đọc cùng lúc"
        try:
            order.append(job.document_id)
            read_document(job, should_stop)
        finally:
            running.release()

    jobs = BackgroundImportQueue(run=run)
    for document_id in ids:
        jobs.submit(_job(store, files, reader, document_id))
    assert jobs.wait_until_idle(WAIT_S)
    jobs.close()
    assert order == ids
    assert all(_status(store, document_id) == "review" for document_id in ids)
    assert all(_page_statuses(store, document_id) == ["ok", "ok"] for document_id in ids)


def test_a_failing_document_does_not_stop_the_queue(store: AppStore, tmp_path: Path) -> None:
    files, reader = OriginalFileStore(tmp_path / "files"), FakePdfReader()
    broken, fine = _accept(store, files, reader, "broken"), _accept(store, files, reader, "fine")

    def run(job: ReadJob, should_stop: Callable[[], bool]) -> None:
        if job.document_id == broken:
            raise RuntimeError("lỗi giả lập")
        read_document(job, should_stop)

    jobs = BackgroundImportQueue(run=run)
    jobs.submit(_job(store, files, reader, broken))
    jobs.submit(_job(store, files, reader, fine))
    assert jobs.wait_until_idle(WAIT_S)
    jobs.close()
    assert (_status(store, broken), _page_statuses(store, broken)) == ("failed", ["failed", "failed"])
    assert _status(store, fine) == "review"


def test_closing_the_queue_stops_after_the_current_document(store: AppStore, tmp_path: Path) -> None:
    files, reader = OriginalFileStore(tmp_path / "files"), FakePdfReader()
    first, second = _accept(store, files, reader, "first"), _accept(store, files, reader, "second")
    started, release = threading.Event(), threading.Event()
    seen_stop: list[Callable[[], bool]] = []
    ran: list[str] = []

    def run(job: ReadJob, should_stop: Callable[[], bool]) -> None:
        ran.append(job.document_id)
        seen_stop.append(should_stop)
        started.set()
        assert release.wait(WAIT_S)
        read_document(job, should_stop)

    jobs = BackgroundImportQueue(run=run)
    jobs.submit(_job(store, files, reader, first))
    jobs.submit(_job(store, files, reader, second))
    assert started.wait(WAIT_S)
    closer = threading.Thread(target=jobs.close)
    closer.start()
    stop_seen = threading.Event()
    for _ in range(int(WAIT_S / 0.01)):
        if seen_stop[0]():
            stop_seen.set()
            break
        stop_seen.wait(0.01)
    assert stop_seen.is_set()
    release.set()
    closer.join(WAIT_S)
    assert ran == [first], "việc sau khi đóng hàng đợi không được chạy"
    assert _page_statuses(store, first) == ["pending", "pending"], "đã dừng thì không ghi thêm trang nào"
    assert _status(store, first) == "processing" and _status(store, second) == "processing"
    with pytest.raises(RuntimeError):
        jobs.submit(_job(store, files, reader, second))


# --------------------------------------------------------------------------- #
# Khởi động lại (§6.5: trang pending được xếp lại từ file gốc)
# --------------------------------------------------------------------------- #


def test_requeue_reads_pending_documents_oldest_first_and_fails_lost_files(store: AppStore, tmp_path: Path) -> None:
    files, reader = OriginalFileStore(tmp_path / "files"), FakePdfReader()
    older, lost = _accept(store, files, reader, "older"), _accept(store, files, reader, "lost")
    done = _accept(store, files, reader, "done", queue=SynchronousImportQueue())
    lost_doc = store.get_imported_document(lost)
    assert lost_doc is not None and files.delete(lost_doc.meta.sha256)
    jobs = SynchronousImportQueue()
    assert requeue_pending(store, files, reader, jobs) == [older]
    assert jobs.submitted == [older]
    assert (_status(store, older), _page_statuses(store, older)) == ("review", ["ok", "ok"])
    assert (_status(store, lost), _page_statuses(store, lost)) == ("failed", ["failed", "failed"])
    assert _status(store, done) == "review" and len(reader.reads) == 2


def test_requeue_only_reads_pages_still_pending(store: AppStore, tmp_path: Path) -> None:
    files, reader = OriginalFileStore(tmp_path / "files"), FakePdfReader(page_count=3)
    document_id = _accept(store, files, reader, "partial")
    read_document(
        _job(store, files, reader, document_id), should_stop=lambda: _page_statuses(store, document_id)[0] == "ok"
    )
    assert _page_statuses(store, document_id) == ["ok", "pending", "pending"]
    requeue_pending(store, files, reader, SynchronousImportQueue())
    assert reader.reads[-1] == (document_id, [2, 3])
    assert _page_statuses(store, document_id) == ["ok", "ok", "ok"] and _status(store, document_id) == "review"


def test_pending_pages_survive_a_restart_of_the_sqlite_store(tmp_path: Path) -> None:
    files, reader = OriginalFileStore(tmp_path / "files"), FakePdfReader()
    first = SQLiteStore.open(tmp_path / DATABASE_FILENAME)
    document_id = _accept(first, files, reader, "restart")
    first.close()
    reopened = SQLiteStore.open(tmp_path / DATABASE_FILENAME)
    try:
        assert _page_statuses(reopened, document_id) == ["pending", "pending"]
        assert requeue_pending(reopened, OriginalFileStore(tmp_path / "files"), reader, SynchronousImportQueue()) == [
            document_id
        ]
        assert _status(reopened, document_id) == "review"
    finally:
        reopened.close()


# --------------------------------------------------------------------------- #
# lifespan của app
# --------------------------------------------------------------------------- #


def test_lifespan_creates_the_runtime_and_cleans_the_temp_files_dir(
    monkeypatch: pytest.MonkeyPatch, fresh_default_store: None
) -> None:
    monkeypatch.delenv(DATA_DIR_ENV, raising=False)
    with TestClient(app) as client:
        runtime = getattr(app.state, documents.RUNTIME_STATE_ATTR)
        assert isinstance(runtime, ImportRuntime) and isinstance(runtime.queue, BackgroundImportQueue)
        assert isinstance(runtime.files, OriginalFileStore) and runtime.files.temporary
        root = runtime.files.root
        assert root.is_dir()
        assert client.get("/v1/documents").json() == []
    assert not root.exists(), "thư mục file tạm bị xoá khi tắt app"
    assert not hasattr(app.state, documents.RUNTIME_STATE_ATTR)


def _seed_processing_document(data_dir: Path) -> str:
    """Kho SQLite trong `data_dir` có một tài liệu đang đọc dở (trang pending) và file gốc của nó."""
    store = SQLiteStore.open(data_dir / DATABASE_FILENAME)
    try:
        return _accept(store, OriginalFileStore.open(data_dir), FakePdfReader(), "seed")
    finally:
        store.close()


def test_lifespan_requeues_pending_documents_from_the_data_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fresh_default_store: None
) -> None:
    document_id = _seed_processing_document(tmp_path)
    reader = FakePdfReader()
    monkeypatch.setattr(documents, "load_pdf_reader", lambda config: reader)
    monkeypatch.setenv(DATA_DIR_ENV, str(tmp_path))
    with TestClient(app) as client:
        runtime = getattr(app.state, documents.RUNTIME_STATE_ATTR)
        assert runtime.files.root == (tmp_path / "files").resolve() and not runtime.files.temporary
        assert runtime.queue.wait_until_idle(WAIT_S)
        body = DocumentImport.model_validate(client.get(f"/v1/documents/{document_id}").json())
        assert body.status == "review" and [p.status for p in body.pages] == ["ok", "ok"]
    assert reader.reads == [(document_id, [1, 2])]
    assert any(path.suffix == ".pdf" for path in (tmp_path / "files").iterdir()), "thư mục dữ liệu được giữ lại"


def test_lifespan_still_starts_when_the_ocr_config_is_invalid(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fresh_default_store: None
) -> None:
    document_id = _seed_processing_document(tmp_path)

    def invalid(config: PdfReaderConfig) -> FakePdfReader:
        raise ConfigError("HOLOCRON_PDF_OCR_PRIMARY không hợp lệ")

    monkeypatch.setattr(documents, "load_pdf_reader", invalid)
    monkeypatch.setenv(DATA_DIR_ENV, str(tmp_path))
    with TestClient(app) as client:
        assert client.get(f"/v1/documents/{document_id}").json()["status"] == "processing"


# --------------------------------------------------------------------------- #
# Tích hợp: PDF thật tự sinh, worker thật, chỉ tầng 1
# --------------------------------------------------------------------------- #


def test_real_pdf_is_probed_and_read_by_the_real_worker(tmp_path: Path) -> None:
    path = write_pdf(tmp_path / "sample.pdf", [Page(texts=text_lines(BODY)), Page(texts=text_lines(BODY[::-1]))])
    content = path.read_bytes()
    document_id = "doc_" + hashlib.sha256(content).hexdigest()[:16]
    store = SQLiteStore.open(tmp_path / DATABASE_FILENAME)
    files = OriginalFileStore(tmp_path / "files")
    runtime = ImportRuntime(files=files, queue=SynchronousImportQueue())
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[documents.get_import_runtime] = lambda: runtime
    app.dependency_overrides[documents.get_pdf_reader] = lambda: WorkerPdfReader(PdfReaderConfig(), ocr=None)
    app.dependency_overrides[get_clock] = lambda: lambda: NOW
    try:
        client = TestClient(app)
        response = client.post(
            "/v1/documents",
            data={"subject_code": "ET4010", "source_kind": "lecture_slides"},
            files={"file": ("sample.pdf", content, "application/pdf")},
        )
        assert response.status_code == 202, response.text
        assert response.json()["meta"]["page_count"] == 2
        body = DocumentImport.model_validate(client.get(f"/v1/documents/{document_id}").json())
        assert body.status == "review" and body.meta.has_text_layer
        assert [(p.status, p.tier_used) for p in body.pages] == [("ok", "text_layer"), ("ok", "text_layer")]
        page = PageExtraction.model_validate(client.get(f"/v1/documents/{document_id}/pages/2").json())
        assert [line.text for line in page.lines] == BODY[::-1]
        assert page.text_score is not None and page.text_score >= 0.85
        assert (files.root / f"{hashlib.sha256(content).hexdigest()}.pdf").read_bytes() == content
    finally:
        app.dependency_overrides.clear()
        store.close()


class StrayPagesReader(FakePdfReader):
    """Bộ đọc trả thêm trang của tài liệu khác và trang trùng: chỉ trang đúng tài liệu, còn `pending` được ghi."""

    def read_pages(
        self, path: Path, document_id: str, page_numbers: Sequence[int] | None = None
    ) -> Iterator[PageExtraction]:
        yield ok_page("doc_ffffffffffffffff", 1)
        yield from super().read_pages(path, document_id, page_numbers)
        yield failed_page(document_id, 1, "PAGE_ERROR")


def test_pages_of_other_documents_or_already_read_are_ignored(store: AppStore, tmp_path: Path) -> None:
    files, reader = OriginalFileStore(tmp_path / "files"), StrayPagesReader()
    document_id = _accept(store, files, reader, "stray")
    read_document(_job(store, files, reader, document_id))
    assert _page_statuses(store, document_id) == ["ok", "ok"] and _status(store, document_id) == "review"
    page = store.get_page(document_id, 1, PageExtraction)
    assert page is not None and page.version == 2


def test_runtime_dependency_requires_the_lifespan() -> None:
    client = TestClient(app, raise_server_exceptions=False)
    assert client.delete("/v1/documents/doc_0000000000000000").status_code == 500


def test_runtime_dependency_inside_the_lifespan(monkeypatch: pytest.MonkeyPatch, fresh_default_store: None) -> None:
    monkeypatch.delenv(DATA_DIR_ENV, raising=False)
    with TestClient(app) as client:
        assert client.delete("/v1/documents/doc_0000000000000000").status_code == 404
