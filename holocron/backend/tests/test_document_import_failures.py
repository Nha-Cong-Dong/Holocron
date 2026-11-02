"""Bộ nhập tài liệu (SPECIFICATION §5.7, §6.0, §6.5) khi có lỗi: kho lỗi giữa lúc tạo, hàng đợi từ chối, app đang tắt,
cấu hình OCR sai, lỗi tạm thời khi đọc, tắt app khi đang đọc và xoá file gốc lỗi.

Không có sleep dài: luồng đồng bộ bằng `threading.Event`, mọi lần chờ đều có hạn.
"""

import hashlib
import logging
import sqlite3
import threading
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from holocron_core.domain.documents import PageEditAudit, PageExtraction
from holocron_core.domain.ingestion import IngestionItem, UploadedDocument
from holocron_core.domain.repositories import AppStore, DocumentRepository, PageStatus, StorageBusyError
from holocron_core.domain.schemas import SourceKind
from holocron_core.infrastructure import memory_store
from holocron_core.infrastructure.document_readers.config import ENV_PDF_OCR_PRIMARY, PdfReaderConfig
from holocron_core.infrastructure.document_readers.pdf_reader import WorkerPdfReader
from holocron_core.infrastructure.memory_store import IngestionStore
from holocron_core.infrastructure.storage import DATA_DIR_ENV, DATABASE_FILENAME, SQLiteStore, sqlite_store
from holocron_core.infrastructure.storage.original_files import OriginalFileStore
from holocron_core.routers import deps, documents
from holocron_core.routers.api import STORE_BUSY_DETAIL, app
from holocron_core.services import document_import
from holocron_core.services.document_import import (
    IMPORT_UNAVAILABLE_DETAIL,
    BackgroundImportQueue,
    ImportRequest,
    ImportRuntime,
    ReadJob,
    SynchronousImportQueue,
    accept_document,
    read_document,
    remove_orphan_files,
    requeue_pending,
)
from holocron_core.services.ingestion_gate import recompute_item
from tests.document_import_support import FakePdfReader, ImportEnv, install, ok_page, pdf_bytes
from tests.test_api_item_state import _body
from tests.vault_support import NOW, STORE_KINDS, open_store

WAIT_S = 10.0
BAD_OCR_VALUE = "engine_khong_ton_tai_42"


@pytest.fixture(params=STORE_KINDS)
def env(request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ImportEnv]:
    for store in open_store(str(request.param), tmp_path, monkeypatch):
        yield install(store, tmp_path / "files", SynchronousImportQueue(), FakePdfReader())
        app.dependency_overrides.clear()


@pytest.fixture
def fresh_default_store() -> Iterator[None]:
    deps._default_store.cache_clear()
    yield
    deps._default_store.cache_clear()


def _doc_id(content: bytes) -> str:
    return "doc_" + hashlib.sha256(content).hexdigest()[:16]


def _stored_files(files: OriginalFileStore) -> list[str]:
    return sorted(path.name for path in files.root.iterdir())


def _statuses(store: DocumentRepository, document_id: str) -> list[str]:
    return [page.status for page in store.list_pages(document_id, PageExtraction)]


def _status(store: DocumentRepository, document_id: str) -> str:
    document = store.get_imported_document(document_id)
    assert document is not None
    return document.status


def _busy_dump(real: Callable[[BaseModel], str], page_number: int) -> Callable[[BaseModel], str]:
    def dump(model: BaseModel) -> str:
        if isinstance(model, PageExtraction) and model.page_number == page_number and model.status == "pending":
            raise StorageBusyError("kho bận giả lập")
        return real(model)

    return dump


def _patch_busy_at_page(store: AppStore, monkeypatch: pytest.MonkeyPatch, page_number: int) -> None:
    module = memory_store if isinstance(store, IngestionStore) else sqlite_store
    monkeypatch.setattr(module, "_dump", _busy_dump(module._dump, page_number))


# --------------------------------------------------------------------------- #
# Tạo tài liệu: kho lỗi giữa chừng, hàng đợi từ chối, app đang tắt, tạo trùng
# --------------------------------------------------------------------------- #


def test_storage_busy_while_creating_leaves_no_record_and_no_file(
    env: ImportEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    content = pdf_bytes("ban")
    _patch_busy_at_page(env.store, monkeypatch, 2)
    response = env.post(content)
    assert response.status_code == 503 and response.json() == {"detail": STORE_BUSY_DETAIL}
    assert env.store.get_document(_doc_id(content)) is None
    assert env.store.list_pages(_doc_id(content), PageExtraction) == []
    assert _stored_files(env.files) == [] and env.queue.submitted == []
    monkeypatch.undo()
    assert env.post(content).status_code == 202, "thử lại sau khi kho hết bận thì nhận bình thường"


def test_unremovable_file_after_a_failed_create_is_cleaned_on_startup(
    env: ImportEnv, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    content = pdf_bytes("ban-file")
    _patch_busy_at_page(env.store, monkeypatch, 1)

    def locked(sha256: str) -> bool:
        raise PermissionError(32, "file đang mở")

    monkeypatch.setattr(env.files, "delete", locked)
    with caplog.at_level(logging.WARNING, logger=document_import.__name__):
        assert env.post(content).status_code == 503
    assert _stored_files(env.files) == [f"{hashlib.sha256(content).hexdigest()}.pdf"] and caplog.records
    monkeypatch.undo()
    assert remove_orphan_files(env.store, env.files) == [hashlib.sha256(content).hexdigest()]
    assert _stored_files(env.files) == []


class RefusingQueue:
    """Hàng đợi từ chối mọi việc, như hàng đợi đã đóng."""

    def submit(self, job: ReadJob) -> None:
        raise RuntimeError("hàng đợi đọc tài liệu đã đóng")

    def close(self, timeout_s: float = 0.0) -> bool:
        return True

    def is_active(self, document_id: str) -> bool:
        return False


def test_rejected_submit_undoes_the_import(env: ImportEnv) -> None:
    refusing = install(env.store, env.files.root, env.queue, env.reader, runtime_queue=RefusingQueue())
    content = pdf_bytes("tu-choi")
    response = TestClient(app).post(
        "/v1/documents",
        data={"subject_code": "ET4010", "source_kind": "textbook", "chapter_code": "ch3"},
        files={"file": ("a.pdf", content, "application/pdf")},
    )
    assert response.status_code == 500 and response.json() == {"detail": IMPORT_UNAVAILABLE_DETAIL}
    assert refusing.store.get_imported_document(_doc_id(content)) is None, "không kẹt processing"
    assert refusing.store.list_pages(_doc_id(content), PageExtraction) == []
    assert _stored_files(refusing.files) == []


def test_rejected_submit_keeps_the_exam_upload_record_and_its_item(env: ImportEnv) -> None:
    """Bản ghi do upload đề §6.1 tạo trước, còn item tham chiếu: chỉ gỡ phần do lần nhập này thêm."""
    content = pdf_bytes("de-cu")
    upload = UploadedDocument.from_content(content, filename="de.pdf", media_type="application/pdf")
    env.store.save_document(upload)
    item = IngestionItem(item_id="ing_cu", **{**_body({"quantum": 4}), "source_doc_id": _doc_id(content)})
    env.store.save_item(recompute_item(item, deps.REGISTRY))
    refusing = install(env.store, env.files.root, env.queue, env.reader, runtime_queue=RefusingQueue())
    response = refusing.post(content, filename="tai_lieu_khac_ten.pdf")
    assert response.status_code == 500 and response.json() == {"detail": IMPORT_UNAVAILABLE_DETAIL}
    assert env.store.get_imported_document(_doc_id(content)) is None, "không kẹt processing"
    assert env.store.get_document(_doc_id(content)) == upload, "dòng của upload đề giữ nguyên như trước lần nhập"
    assert env.store.get_item("ing_cu") is not None
    assert env.store.list_pages(_doc_id(content), PageExtraction) == []
    assert _stored_files(env.files) == []
    accepting = install(env.store, env.files.root, SynchronousImportQueue(), env.reader)
    assert accepting.post(content).status_code == 202, "nhập lại được như tài liệu mới"


def test_closed_gate_refuses_before_writing_anything(env: ImportEnv) -> None:
    assert env.runtime.gate.close(0.0)
    content = pdf_bytes("dang-tat")
    response = env.post(content)
    assert response.status_code == 500 and response.json() == {"detail": IMPORT_UNAVAILABLE_DETAIL}
    assert env.store.get_document(_doc_id(content)) is None
    assert _stored_files(env.files) == [] and env.queue.submitted == []


@dataclass
class RacingReader(FakePdfReader):
    """Trong lúc mở thử, một tiến trình khác (giả lập) tạo xong cùng tài liệu cho môn `subject_code`."""

    store: AppStore | None = None
    subject_code: str = "ET4010"
    files: OriginalFileStore | None = None

    def probe(self, path: Path) -> int:
        assert self.store is not None and self.files is not None
        content = path.read_bytes()
        accept_document(
            ImportRequest(
                content=content,
                filename="khac.pdf",
                declared_type="application/pdf",
                subject_code=self.subject_code,
                source_kind=SourceKind.TEXTBOOK,
                chapter_code="ch3",
            ),
            store=self.store,
            runtime=ImportRuntime(files=self.files, queue=SynchronousImportQueue(run=lambda job: None)),
            reader=FakePdfReader(),
            now=NOW,
        )
        return super().probe(path)


@pytest.mark.parametrize(("other_subject", "status"), [("ET4010", 200), ("IT3070", 409)])
def test_document_created_elsewhere_during_the_probe_is_returned_as_existing(
    env: ImportEnv, other_subject: str, status: int
) -> None:
    reader = RacingReader(store=env.store, subject_code=other_subject, files=env.files)
    racing = install(env.store, env.files.root, env.queue, reader)
    content = pdf_bytes("chay-dua")
    response = racing.post(content)
    assert response.status_code == status, response.text
    assert env.queue.submitted == [], "không xếp hàng đợi lần thứ hai"
    stored = env.store.get_imported_document(_doc_id(content))
    assert stored is not None and stored.meta.subject_code == other_subject and stored.filename == "khac.pdf"
    assert _stored_files(env.files) == [f"{stored.meta.sha256}.pdf"], "file gốc thuộc tài liệu kia được giữ"


# --------------------------------------------------------------------------- #
# Cấu hình OCR sai
# --------------------------------------------------------------------------- #


def test_invalid_ocr_config_gives_a_reader_without_ocr(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    monkeypatch.setenv(ENV_PDF_OCR_PRIMARY, BAD_OCR_VALUE)
    config = PdfReaderConfig(max_bytes=1234)
    runtime = ImportRuntime(files=OriginalFileStore(tmp_path), queue=SynchronousImportQueue(), reader_config=config)
    with caplog.at_level(logging.WARNING, logger=documents.__name__):
        reader = documents.get_pdf_reader(runtime)
    assert isinstance(reader, WorkerPdfReader) and reader.ocr is None and reader.config is config
    messages = " ".join(record.getMessage() for record in caplog.records)
    assert ENV_PDF_OCR_PRIMARY in messages and BAD_OCR_VALUE not in messages


def test_invalid_ocr_config_keeps_the_upload_checks_and_never_500(
    env: ImportEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ENV_PDF_OCR_PRIMARY, BAD_OCR_VALUE)
    del app.dependency_overrides[documents.get_pdf_reader]
    max_bytes = env.runtime.reader_config.max_bytes
    assert env.post(b"x" * (max_bytes + 1), filename="a" * 300, content_type="image/png").status_code == 413
    assert env.post(b"", filename="a" * 300, content_type="image/png").status_code == 400
    assert env.post(pdf_bytes("dai"), filename="a" * 300).status_code == 422
    assert env.post(b"\x89PNG\r\n\x1a\n" + b"0" * 32, filename="de.png").status_code == 415
    broken = env.post(pdf_bytes("hong"))  # byte tự tạo, không phải PDF hợp lệ: worker thật không mở được
    assert broken.status_code == 422 and broken.json() == {"detail": "Không mở được tài liệu"}


# --------------------------------------------------------------------------- #
# Lỗi tạm thời khi đọc (§5.7: chỉ `failed` khi không mở lại được tài liệu)
# --------------------------------------------------------------------------- #


class BreaksAfterFirstPage(FakePdfReader):
    """Trả trang đầu rồi ném lỗi (worker chết bất thường giữa chừng, trái `PdfReader`)."""

    def read_pages(
        self, path: Path, document_id: str, page_numbers: Sequence[int] | None = None
    ) -> Iterator[PageExtraction]:
        numbers = list(range(1, self.page_count + 1)) if page_numbers is None else list(page_numbers)
        self.reads.append((document_id, numbers))
        yield ok_page(document_id, numbers[0])
        raise OSError("worker chết giữa chừng (giả lập)")


def test_reader_error_after_the_first_page_keeps_the_document_processing(env: ImportEnv) -> None:
    breaking = install(env.store, env.files.root, env.queue, BreaksAfterFirstPage(page_count=3))
    content = pdf_bytes("dut-giua")
    assert breaking.post(content).status_code == 202
    doc_id = _doc_id(content)
    assert _status(env.store, doc_id) == "processing", "lỗi tạm thời không làm tài liệu failed"
    assert _statuses(env.store, doc_id) == ["ok", "pending", "pending"]
    assert requeue_pending(env.store, env.files, FakePdfReader(page_count=3), SynchronousImportQueue()) == [doc_id]
    assert _status(env.store, doc_id) == "review" and _statuses(env.store, doc_id) == ["ok", "ok", "ok"]


def _busy_update_page(store: AppStore, monkeypatch: pytest.MonkeyPatch, page_number: int, failures: int) -> list[int]:
    """`update_page` của trang `page_number` ném `StorageBusyError` `failures` lần đầu; trả danh sách lần đã ném."""
    real = store.update_page
    raised: list[int] = []

    def update_page(
        document_id: str,
        number: int,
        page: BaseModel,
        *,
        status: PageStatus,
        confirmed: bool,
        expected_version: int,
        audit: PageEditAudit | None = None,
    ) -> int:
        if number == page_number and len(raised) < failures:
            raised.append(number)
            raise StorageBusyError("kho bận giả lập")
        return real(
            document_id,
            number,
            page,
            status=status,
            confirmed=confirmed,
            expected_version=expected_version,
            audit=audit,
        )

    monkeypatch.setattr(store, "update_page", update_page)
    return raised


def _accept_idle(store: AppStore, files: OriginalFileStore, tag: str, page_count: int = 3) -> str:
    outcome = accept_document(
        ImportRequest(
            content=pdf_bytes(tag),
            filename=f"{tag}.pdf",
            declared_type="application/pdf",
            subject_code="ET4010",
            source_kind=SourceKind.TEXTBOOK,
            chapter_code="ch3",
        ),
        store=store,
        runtime=ImportRuntime(files=files, queue=SynchronousImportQueue(run=lambda job: None)),
        reader=FakePdfReader(page_count=page_count),
        now=NOW,
    )
    return outcome.document.meta.doc_id


def _job(store: AppStore, files: OriginalFileStore, document_id: str, reader: FakePdfReader) -> ReadJob:
    document = store.get_imported_document(document_id)
    assert document is not None
    return ReadJob(document_id=document_id, path=files.path_for(document.meta.sha256), store=store, reader=reader)


@pytest.fixture(params=STORE_KINDS)
def store_kind(request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[AppStore]:
    yield from open_store(str(request.param), tmp_path, monkeypatch)


@pytest.mark.parametrize(
    ("failures", "expected_status", "expected_pages"),
    [
        (document_import.WRITE_ATTEMPTS - 1, "review", ["ok", "ok", "ok"]),
        (document_import.WRITE_ATTEMPTS, "processing", ["ok", "pending", "pending"]),
    ],
)
def test_busy_store_while_writing_a_page_is_retried_then_left_pending(
    store_kind: AppStore,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failures: int,
    expected_status: str,
    expected_pages: list[str],
) -> None:
    store, files = store_kind, OriginalFileStore(tmp_path / "files")
    document_id = _accept_idle(store, files, "ban-khi-ghi")
    raised = _busy_update_page(store, monkeypatch, 2, failures)
    pauses: list[float] = []
    queue = SynchronousImportQueue(run=lambda job: read_document(job, pause=pauses.append))
    queue.submit(_job(store, files, document_id, FakePdfReader(page_count=3)))
    assert len(raised) == failures and len(pauses) == min(failures, document_import.WRITE_ATTEMPTS - 1)
    assert _status(store, document_id) == expected_status, "kho bận không bao giờ làm tài liệu failed"
    assert _statuses(store, document_id) == expected_pages


# --------------------------------------------------------------------------- #
# Tắt app khi đang đọc
# --------------------------------------------------------------------------- #


@dataclass
class PausingReader(FakePdfReader):
    """Trả trang đầu, báo `first_yielded`, rồi chờ `release` trước khi trả các trang sau."""

    first_yielded: threading.Event = field(default_factory=threading.Event)
    release: threading.Event = field(default_factory=threading.Event)

    def read_pages(
        self, path: Path, document_id: str, page_numbers: Sequence[int] | None = None
    ) -> Iterator[PageExtraction]:
        numbers = list(range(1, self.page_count + 1)) if page_numbers is None else list(page_numbers)
        self.reads.append((document_id, numbers))
        for index, number in enumerate(numbers):
            if index == 1:
                self.first_yielded.set()
                assert self.release.wait(WAIT_S), "test không thả bộ đọc"
            yield ok_page(document_id, number)


def _errors(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [record.getMessage() for record in caplog.records if record.levelno >= logging.ERROR]


def test_shutdown_while_reading_keeps_the_temp_dir_and_the_thread_stops_cleanly(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, fresh_default_store: None
) -> None:
    monkeypatch.delenv(DATA_DIR_ENV, raising=False)
    monkeypatch.setattr(document_import, "QUEUE_CLOSE_TIMEOUT_S", 0.05)
    reader = PausingReader(page_count=3)
    app.dependency_overrides[documents.get_pdf_reader] = lambda: reader
    content = pdf_bytes("tat-khi-doc")
    try:
        with caplog.at_level(logging.INFO), TestClient(app) as client:
            runtime = getattr(app.state, documents.RUNTIME_STATE_ATTR)
            store = deps.get_store()
            response = client.post(
                "/v1/documents",
                data={"subject_code": "ET4010", "source_kind": "textbook", "chapter_code": "ch3"},
                files={"file": ("a.pdf", content, "application/pdf")},
            )
            assert response.status_code == 202
            assert reader.first_yielded.wait(WAIT_S)
        root = runtime.files.root
        assert root.is_dir(), "luồng đọc chưa dừng kịp: thư mục file tạm được giữ, không xoá dưới chân nó"
        assert any("chưa dừng hẳn" in record.getMessage() for record in caplog.records)
        reader.release.set()
        assert runtime.queue.close(WAIT_S), "luồng đọc thoát sau khi bộ đọc được thả"
        assert not runtime.queue.running
        assert _statuses(store, _doc_id(content)) == ["ok", "pending", "pending"], "đã dừng thì không ghi thêm"
        assert _status(store, _doc_id(content)) == "processing"
        assert _errors(caplog) == []
        runtime.files.close()
        assert not root.exists()
    finally:
        reader.release.set()
        app.dependency_overrides.clear()


def test_reader_thread_exits_quietly_when_the_store_was_closed_under_it(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / DATABASE_FILENAME
    store, files = SQLiteStore.open(path), OriginalFileStore(tmp_path / "files")
    document_id = _accept_idle(store, files, "kho-dong")
    started, release = threading.Event(), threading.Event()

    def run(job: ReadJob, should_stop: Callable[[], bool]) -> None:
        started.set()
        assert release.wait(WAIT_S)
        read_document(job, should_stop)

    jobs = BackgroundImportQueue(run=run)
    jobs.submit(_job(store, files, document_id, FakePdfReader(page_count=3)))
    assert started.wait(WAIT_S)
    with caplog.at_level(logging.INFO):
        assert jobs.close(timeout_s=0.05) is False, "luồng còn đang chờ trong việc đọc"
        store.close()
        release.set()
        assert jobs.close(timeout_s=WAIT_S)
    assert not jobs.running
    assert _errors(caplog) == [] and any("app đang tắt" in r.getMessage() for r in caplog.records)
    reopened = SQLiteStore.open(path)
    try:
        assert _status(reopened, document_id) == "processing", "không bị đánh dấu failed khi tắt app"
        assert _statuses(reopened, document_id) == ["pending"] * 3
    finally:
        reopened.close()


def test_lifespan_closes_the_store_when_creating_the_import_runtime_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fresh_default_store: None
) -> None:
    monkeypatch.setenv(DATA_DIR_ENV, str(tmp_path))
    store = deps.get_store()
    assert isinstance(store, SQLiteStore)

    def broken(data_dir: Path | None) -> ImportRuntime:
        raise OSError("không tạo được thư mục file gốc (giả lập)")

    monkeypatch.setattr(documents, "create_import_runtime", broken)
    with pytest.raises(OSError, match="giả lập"), TestClient(app):
        pass
    assert deps._default_store.cache_info().currsize == 0
    with pytest.raises(sqlite3.ProgrammingError):
        store.get_imported_document("doc_0000000000000000")
    assert not hasattr(app.state, documents.RUNTIME_STATE_ATTR)


# --------------------------------------------------------------------------- #
# Xoá: file gốc không xoá được
# --------------------------------------------------------------------------- #


def test_delete_still_204_when_the_original_file_cannot_be_removed(
    env: ImportEnv, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    content = pdf_bytes("khoa-file")
    assert env.post(content).status_code == 202
    real_delete = env.files.delete

    def locked(sha256: str) -> bool:
        raise PermissionError(32, "file đang được tiến trình khác mở")

    monkeypatch.setattr(env.files, "delete", locked)
    with caplog.at_level(logging.WARNING, logger=document_import.__name__):
        response = env.client.delete(f"/v1/documents/{_doc_id(content)}")
    assert response.status_code == 204 and caplog.records
    assert env.client.get(f"/v1/documents/{_doc_id(content)}").status_code == 404
    assert _stored_files(env.files) == [f"{hashlib.sha256(content).hexdigest()}.pdf"]
    monkeypatch.setattr(env.files, "delete", real_delete)
    assert documents.requeue_on_startup(env.store, env.runtime) == []
    assert _stored_files(env.files) == [], "lần khởi động sau dọn file mồ côi"
