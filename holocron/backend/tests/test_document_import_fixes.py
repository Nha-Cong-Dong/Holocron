"""Bộ nhập tài liệu (SPECIFICATION §6.0, §6.5): các sửa sau review phần 1.

Lỗi ném lại có `{"detail": ...}`; lỗi môi trường khi mở bộ đọc không làm tài liệu `failed` vĩnh viễn; bộ đọc trả
thiếu trang; gỡ tài liệu kẹt `processing`; cổng nhận việc tính cả lượt đang mở thử; `DocumentMeta` dựng trước khi lưu
file; cấu hình thử lại; `GET` không dựng lại dòng của mọi trang; tài liệu nhập trước khi có `chapter_code`.
"""

import hashlib
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from holocron_core.domain.documents import CrossCheck, PageExtraction
from holocron_core.domain.repositories import AppStore, DocumentExistsError, ImportedDocument
from holocron_core.domain.schemas import DocumentMeta, SourceKind
from holocron_core.infrastructure.document_readers.config import MIB, PdfReaderConfig
from holocron_core.infrastructure.document_readers.pdf_reader import PdfInputError, PdfWorkerError
from holocron_core.infrastructure.storage.original_files import OriginalFileStore
from holocron_core.routers import documents
from holocron_core.routers.api import app
from holocron_core.services import document_import
from holocron_core.services.document_import import (
    CHAPTER_MISSING_DETAIL,
    IMPORT_UNAVAILABLE_DETAIL,
    MAX_TITLE_LENGTH,
    BackgroundImportQueue,
    ImportRequest,
    ImportRuntime,
    ReadJob,
    RetryPolicy,
    SynchronousImportQueue,
    accept_document,
    read_document,
)
from tests.document_import_support import FakePdfReader, HoldingQueue, ImportEnv, install, pdf_bytes
from tests.test_document_import_failures import _accept_idle, _busy_update_page, _job, _status, _statuses
from tests.vault_support import NOW, STORE_KINDS, open_store

WAIT_S = 10.0


@pytest.fixture(params=STORE_KINDS)
def env(request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ImportEnv]:
    for store in open_store(str(request.param), tmp_path, monkeypatch):
        yield install(store, tmp_path / "files", SynchronousImportQueue(), FakePdfReader())
        app.dependency_overrides.clear()


@pytest.fixture(params=STORE_KINDS)
def store(request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[AppStore]:
    yield from open_store(str(request.param), tmp_path, monkeypatch)


def _doc_id(content: bytes) -> str:
    return "doc_" + hashlib.sha256(content).hexdigest()[:16]


def _files(env: ImportEnv) -> list[str]:
    return sorted(path.name for path in env.files.root.iterdir())


# --------------------------------------------------------------------------- #
# Lỗi ném lại luôn có {"detail": ...} (§6.0)
# --------------------------------------------------------------------------- #


def test_document_exists_race_is_a_500_with_a_detail_and_leaves_no_file(
    env: ImportEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tiến trình khác tạo rồi xoá cùng tài liệu giữa hai bước: 500 kèm `detail` tiếng Việt, không 500 trần."""

    def racing_create(document: ImportedDocument, pending_pages: object) -> ImportedDocument:
        raise DocumentExistsError(document.meta.doc_id)

    monkeypatch.setattr(env.store, "create_imported_document", racing_create)
    content = pdf_bytes("dua")
    response = TestClient(app, raise_server_exceptions=False).post(
        "/v1/documents",
        data={"subject_code": "ET4010", "source_kind": "textbook", "chapter_code": "ch3"},
        files={"file": ("a.pdf", content, "application/pdf")},
    )
    assert response.status_code == 500
    assert response.json() == {"detail": documents.IMPORT_RACE_DETAIL}
    assert _files(env) == [] and env.queue.submitted == []


def test_request_after_close_gets_a_detail_and_stages_nothing(env: ImportEnv) -> None:
    assert env.runtime.close(0.1)
    response = TestClient(app, raise_server_exceptions=False).post(
        "/v1/documents",
        data={"subject_code": "ET4010", "source_kind": "textbook", "chapter_code": "ch3"},
        files={"file": ("a.pdf", pdf_bytes("sau-khi-tat"), "application/pdf")},
    )
    assert response.status_code == 500 and response.json() == {"detail": IMPORT_UNAVAILABLE_DETAIL}
    assert env.reader.probed == []


# --------------------------------------------------------------------------- #
# Đọc nền: lỗi môi trường, file mất, bộ đọc trả thiếu trang
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("error", [PdfWorkerError("worker không khởi động được"), OSError("thiếu tài nguyên")])
def test_environment_error_at_the_first_page_is_retried_then_left_processing(
    store: AppStore, tmp_path: Path, error: Exception
) -> None:
    files = OriginalFileStore(tmp_path / "files")
    document_id = _accept_idle(store, files, "moi-truong")
    reader = FakePdfReader(page_count=3, read_errors=[error] * 5)
    pauses: list[float] = []
    job = ReadJob(
        document_id=document_id,
        path=_job(store, files, document_id, reader).path,
        store=store,
        reader=reader,
        retry=RetryPolicy(attempts=3, delay_s=0.5),
    )
    read_document(job, pause=pauses.append)
    assert len(reader.reads) == 3 and pauses == [0.5, 1.0]
    assert _status(store, document_id) == "processing", "lỗi môi trường không làm tài liệu failed vĩnh viễn"
    assert _statuses(store, document_id) == ["pending", "pending", "pending"]


def test_environment_error_that_goes_away_is_read_normally(store: AppStore, tmp_path: Path) -> None:
    files = OriginalFileStore(tmp_path / "files")
    document_id = _accept_idle(store, files, "het-loi")
    reader = FakePdfReader(page_count=3, read_errors=[PdfWorkerError("tạm thời")])
    pauses: list[float] = []
    read_document(_job(store, files, document_id, reader), pause=pauses.append)
    assert len(pauses) == 1 and len(reader.reads) == 2
    assert (_status(store, document_id), _statuses(store, document_id)) == ("review", ["ok", "ok", "ok"])


def test_stop_request_ends_the_retries_at_once(store: AppStore, tmp_path: Path) -> None:
    files = OriginalFileStore(tmp_path / "files")
    document_id = _accept_idle(store, files, "dung")
    reader = FakePdfReader(page_count=3, read_errors=[PdfWorkerError("x")] * 5)
    calls: list[int] = []

    def should_stop() -> bool:
        calls.append(1)
        return len(calls) > 1

    read_document(_job(store, files, document_id, reader), should_stop=should_stop, pause=lambda s: None)
    assert len(reader.reads) == 1 and _status(store, document_id) == "processing"


@pytest.mark.parametrize("error", [PdfInputError("OPEN_FAILED"), FileNotFoundError("mất file gốc"), ValueError("x")])
def test_document_errors_at_the_first_page_mark_it_failed(store: AppStore, tmp_path: Path, error: Exception) -> None:
    files = OriginalFileStore(tmp_path / "files")
    document_id = _accept_idle(store, files, "loi-file")
    reader = FakePdfReader(page_count=3, read_errors=[error])
    read_document(_job(store, files, document_id, reader), pause=lambda s: None)
    assert len(reader.reads) == 1, "lỗi của chính file không được thử lại"
    assert (_status(store, document_id), _statuses(store, document_id)) == ("failed", ["failed"] * 3)


def test_reader_that_returns_fewer_pages_fails_the_missing_ones(store: AppStore, tmp_path: Path) -> None:
    files = OriginalFileStore(tmp_path / "files")
    document_id = _accept_idle(store, files, "thieu-trang")
    reader = FakePdfReader(page_count=3, max_pages=1)
    read_document(_job(store, files, document_id, reader))
    assert _statuses(store, document_id) == ["ok", "failed", "failed"]
    assert _status(store, document_id) == "review"
    missing = store.get_page(document_id, 2, PageExtraction)
    assert missing is not None and missing.failure_reason == "PAGE_ERROR"


def test_reader_that_returns_nothing_fails_every_pending_page(store: AppStore, tmp_path: Path) -> None:
    files = OriginalFileStore(tmp_path / "files")
    document_id = _accept_idle(store, files, "khong-trang")
    read_document(_job(store, files, document_id, FakePdfReader(page_count=3, max_pages=0)))
    assert (_status(store, document_id), _statuses(store, document_id)) == ("review", ["failed"] * 3)


def test_retry_policy_comes_from_the_runtime(store: AppStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    files = OriginalFileStore(tmp_path / "files")
    document_id = _accept_idle(store, files, "mot-lan")
    raised = _busy_update_page(store, monkeypatch, 1, 1)
    runtime = ImportRuntime(files=files, queue=SynchronousImportQueue(), retry=RetryPolicy(attempts=1))
    job = _job(store, files, document_id, FakePdfReader(page_count=3))
    job = ReadJob(document_id=job.document_id, path=job.path, store=store, reader=job.reader, retry=runtime.retry)
    runtime.queue.submit(job)
    assert len(raised) == 1 and _status(store, document_id) == "processing"
    assert _statuses(store, document_id) == ["pending", "pending", "pending"]


@pytest.mark.parametrize(("attempts", "delay_s"), [(0, 0.1), (1, -0.1)])
def test_retry_policy_rejects_invalid_values(attempts: int, delay_s: float) -> None:
    with pytest.raises(ValueError, match="RetryPolicy"):
        RetryPolicy(attempts=attempts, delay_s=delay_s)


def test_accepted_document_carries_the_runtime_retry_policy(env: ImportEnv) -> None:
    held = HoldingQueue()
    policy = RetryPolicy(attempts=7, delay_s=0.0)
    runtime = ImportRuntime(files=env.files, queue=held, retry=policy)
    accept_document(
        ImportRequest(
            content=pdf_bytes("chinh-sach"),
            filename="a.pdf",
            declared_type="application/pdf",
            subject_code="ET4010",
            source_kind=SourceKind.TEXTBOOK,
            chapter_code="ch3",
        ),
        store=env.store,
        runtime=runtime,
        reader=env.reader,
        now=NOW,
    )
    assert [job.retry for job in held.held] == [policy]


# --------------------------------------------------------------------------- #
# Gỡ tài liệu kẹt `processing`
# --------------------------------------------------------------------------- #


def test_document_stuck_processing_without_a_job_can_be_deleted(env: ImportEnv) -> None:
    held = HoldingQueue()
    reading = install(env.store, env.files.root, SynchronousImportQueue(), env.reader, runtime_queue=held)
    content = pdf_bytes("ket")
    assert reading.post(content).status_code == 202
    doc_id = _doc_id(content)
    assert reading.client.delete(f"/v1/documents/{doc_id}").status_code == 409, "việc đọc còn trong hàng đợi"
    held.release()  # luồng đọc dừng giữa chừng (lỗi tạm thời), không còn việc nào giữ tài liệu
    assert reading.client.get(f"/v1/documents/{doc_id}").json()["status"] == "processing"
    assert reading.client.delete(f"/v1/documents/{doc_id}").status_code == 204
    assert reading.client.get(f"/v1/documents/{doc_id}").status_code == 404
    assert _files(reading) == []
    assert reading.post(content).status_code == 202, "nhập lại được sau khi gỡ"


def test_background_queue_reports_queued_and_running_documents(store: AppStore, tmp_path: Path) -> None:
    files = OriginalFileStore(tmp_path / "files")
    first, second = _accept_idle(store, files, "q1"), _accept_idle(store, files, "q2")
    started, release = threading.Event(), threading.Event()

    def run(job: ReadJob, should_stop: object) -> None:
        if job.document_id == first:
            started.set()
            assert release.wait(WAIT_S)

    jobs = BackgroundImportQueue(run=run)
    try:
        jobs.submit(_job(store, files, first, FakePdfReader()))
        jobs.submit(_job(store, files, second, FakePdfReader()))
        assert started.wait(WAIT_S)
        assert jobs.is_active(first) and jobs.is_active(second) and not jobs.is_active("doc_0000000000000000")
        release.set()
        assert jobs.wait_until_idle(WAIT_S)
        assert not jobs.is_active(first) and not jobs.is_active(second)
    finally:
        release.set()
        jobs.close()


# --------------------------------------------------------------------------- #
# Cổng nhận việc tính cả lượt đang mở thử
# --------------------------------------------------------------------------- #


@dataclass
class BlockingProbeReader(FakePdfReader):
    probing: threading.Event = field(default_factory=threading.Event)
    release: threading.Event = field(default_factory=threading.Event)

    def probe(self, path: Path) -> int:
        self.probing.set()
        assert self.release.wait(WAIT_S), "test không thả bước mở thử"
        return super().probe(path)


def test_close_waits_for_a_request_still_probing(env: ImportEnv) -> None:
    reader = BlockingProbeReader()
    probing = install(env.store, env.files.root, SynchronousImportQueue(), reader)
    statuses: list[int] = []
    worker = threading.Thread(target=lambda: statuses.append(probing.post(pdf_bytes("dang-mo")).status_code))
    worker.start()
    try:
        assert reader.probing.wait(WAIT_S)
        assert probing.runtime.gate.close(0.05) is False, "lượt đang mở thử chưa xong thì cổng chưa rảnh"
    finally:
        reader.release.set()
        worker.join(WAIT_S)
    assert statuses == [202]
    assert probing.runtime.gate.close(0.05) is True
    late = probing.post(pdf_bytes("den-muon"))
    assert late.status_code == 500 and late.json() == {"detail": IMPORT_UNAVAILABLE_DETAIL}
    assert len(reader.probed) == 1


# --------------------------------------------------------------------------- #
# DocumentMeta dựng trước khi lưu file gốc
# --------------------------------------------------------------------------- #


def test_invalid_metadata_is_rejected_before_the_original_file_is_saved(env: ImportEnv) -> None:
    request = ImportRequest(
        content=pdf_bytes("meta-sai"),
        filename="a.pdf",
        declared_type="application/pdf",
        subject_code="khong-hop-le",
        source_kind=SourceKind.TEXTBOOK,
        chapter_code="ch3",
    )
    with pytest.raises(ValidationError):
        accept_document(request, store=env.store, runtime=env.runtime, reader=env.reader, now=NOW)
    assert _files(env) == [], "không file gốc, không file tạm"
    assert env.store.get_document(_doc_id(request.content)) is None


# --------------------------------------------------------------------------- #
# Hằng giới hạn của router lấy từ cấu hình chung
# --------------------------------------------------------------------------- #


def test_router_limits_come_from_the_shared_config() -> None:
    config = PdfReaderConfig()
    assert f"{config.max_bytes // MIB} MiB" in documents.FILE_FIELD_DESCRIPTION
    assert f"{config.max_pages} trang" in documents.FILE_FIELD_DESCRIPTION
    schema = app.openapi()["components"]["schemas"]
    form = next(value for key, value in schema.items() if key.startswith("Body_import_document"))
    title = form["properties"]["title"]
    lengths = [option.get("maxLength") for option in title.get("anyOf", [title])]
    assert MAX_TITLE_LENGTH in lengths


# --------------------------------------------------------------------------- #
# GET không dựng lại dòng của mọi trang
# --------------------------------------------------------------------------- #


def test_status_requests_do_not_rebuild_page_lines(env: ImportEnv, monkeypatch: pytest.MonkeyPatch) -> None:
    content = pdf_bytes("hoi-trang-thai")
    env.post(content)

    def no_lines(*args: object, **kwargs: object) -> list[PageExtraction]:
        raise AssertionError("GET trạng thái không được đọc toàn bộ trang")

    monkeypatch.setattr(env.store, "list_pages", no_lines)
    assert env.client.get(f"/v1/documents/{_doc_id(content)}").status_code == 200
    listed = env.client.get("/v1/documents")
    assert listed.status_code == 200 and listed.json()[0]["pages_pending"] == 0


# --------------------------------------------------------------------------- #
# Tài liệu đã xác nhận giữ trạng thái khi luồng đọc kết thúc muộn
# --------------------------------------------------------------------------- #


def test_late_finish_of_the_reader_keeps_a_confirmed_document_confirmed(env: ImportEnv) -> None:
    content = pdf_bytes("xong-muon")
    env.post(content)
    doc_id = _doc_id(content)
    assert env.client.post(f"/v1/documents/{doc_id}/confirm", json={"editor_id": "u"}).json()["status"] == "confirmed"
    document = env.store.get_imported_document(doc_id)
    assert document is not None
    read_document(
        ReadJob(document_id=doc_id, path=env.files.path_for(document.meta.sha256), store=env.store, reader=env.reader)
    )
    assert _status(env.store, doc_id) == "confirmed"


# --------------------------------------------------------------------------- #
# Tài liệu nhập trước khi có chapter_code
# --------------------------------------------------------------------------- #


def test_legacy_document_without_chapter_code_answers_409_with_a_detail(env: ImportEnv) -> None:
    doc_id = "doc_dddddddddddddddd"
    legacy = ImportedDocument(
        meta=DocumentMeta(
            doc_id=doc_id,
            subject_code="ET4010",
            title="Cũ",
            source_kind=SourceKind.TEXTBOOK,
            sha256=("dddddddddddddddd" * 4)[:64],
            page_count=1,
            has_text_layer=False,
            uploaded_at=NOW,
        ),
        filename="cu.pdf",
        size_bytes=10,
        status="review",
    )
    pending = PageExtraction(
        document_id=doc_id,
        page_number=1,
        status="pending",
        tier_used=None,
        text_score=None,
        cross_check=CrossCheck(status="not_applicable", secondary_engine=None),
    )
    env.store.create_imported_document(legacy, [pending])
    response = env.client.get(f"/v1/documents/{doc_id}")
    assert response.status_code == 409 and response.json() == {"detail": CHAPTER_MISSING_DETAIL}
    assert env.client.get("/v1/documents").status_code == 200, "danh sách không cần chapter_code"
    assert env.client.delete(f"/v1/documents/{doc_id}").status_code == 204, "xoá được để nhập lại"


def test_module_constants_are_the_retry_defaults() -> None:
    assert RetryPolicy() == RetryPolicy(document_import.WRITE_ATTEMPTS, document_import.WRITE_RETRY_DELAY_S)
