"""Bộ nhập tài liệu PDF, phần nhận file, đọc nền, xem và xoá (SPECIFICATION §5.7, §6.5; ADR-008 mục 3, 4, 7).

- `accept_document` kiểm file theo đúng thứ tự của §6.5, mở thử trong worker (qua `PdfReader`), lưu file gốc, tạo
  `page_count` trang `pending` rồi xếp tài liệu vào hàng đợi đọc.
- `read_document` đọc các trang còn `pending` qua `PdfReader` và ghi từng trang vào kho ngay khi đọc xong.
- `BackgroundImportQueue`: hàng đợi FIFO, một luồng nền đọc một tài liệu mỗi lúc (§6.5). Đối tượng này do `lifespan`
  của app tạo và đóng, không phải biến toàn cục; test dùng `SynchronousImportQueue`.

Không văn bản nào đi vào Vault hay Gate ở đây: trang chỉ được lưu để người dùng xem lại (ADR-008 mục 7). File PDF là
đầu vào không tin cậy, chỉ được mở trong worker có giới hạn của `PdfReader` (CLAUDE.md §2.2).
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import queue
import threading
from collections.abc import Callable, Generator, Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol

from ..domain.documents import (
    CrossCheck,
    DocumentImport,
    DocumentSummary,
    FailureReason,
    OutputLanguage,
    PageExtraction,
    PageSummary,
)
from ..domain.repositories import DocumentRepository, ImportedDocument, RecordInUseError, StorageError
from ..domain.schemas import DocumentMeta, SourceKind
from ..infrastructure.document_readers.pdf_reader import PDF_MAGIC, PdfInputError, PdfReader, PdfWorkerError
from ..infrastructure.document_readers.tiers import failed_page
from ..infrastructure.storage.original_files import StagedFile

MAX_DOCUMENT_BYTES = 50 * 1024 * 1024  # §6.5, ADR-008 mục 4
MAX_FILENAME_LENGTH = 255
DEFAULT_FILENAME = "upload.pdf"
DEFAULT_TITLE = "Tài liệu"
MAX_TITLE_LENGTH = 300
PDF_MEDIA_TYPE = "application/pdf"
# `Content-Type` khai báo được chấp nhận (§6.5 bước 2): rỗng, `application/octet-stream` hoặc đúng PDF.
ACCEPTED_DECLARED_TYPES = frozenset({"", "application/octet-stream", PDF_MEDIA_TYPE})
QUEUE_CLOSE_TIMEOUT_S = 10.0

_LOGGER = logging.getLogger(__name__)

ImportErrorCode = Literal[
    "TOO_LARGE",
    "EMPTY",
    "FILENAME_TOO_LONG",
    "NOT_PDF",
    "CONTENT_TYPE_MISMATCH",
    "SUBJECT_CONFLICT",
    "PASSWORD_REQUIRED",
    "OPEN_FAILED",
    "TOO_MANY_PAGES",
]

IMPORT_ERROR_MESSAGES: dict[ImportErrorCode, str] = {
    "TOO_LARGE": f"File vượt giới hạn {MAX_DOCUMENT_BYTES} bytes",
    "EMPTY": "File rỗng",
    "FILENAME_TOO_LONG": f"Tên file dài quá {MAX_FILENAME_LENGTH} ký tự",
    "NOT_PDF": "Chỉ nhận PDF (thiếu magic bytes %PDF-); ảnh đề thi tải lên qua /v1/ingestion/upload",
    "CONTENT_TYPE_MISMATCH": "Content-Type khai báo không khớp nội dung PDF",
    "SUBJECT_CONFLICT": "Tài liệu này đã được nhập cho một môn khác",
    "PASSWORD_REQUIRED": "PDF có mật khẩu",
    "OPEN_FAILED": "Không mở được tài liệu",
    "TOO_MANY_PAGES": "PDF vượt giới hạn số trang",
}

# Lỗi của bước mở thử (§6.5 bước 4) theo mã của `PdfReader`.
_PROBE_ERRORS: dict[str, ImportErrorCode] = {
    "EMPTY": "EMPTY",
    "TOO_LARGE": "TOO_LARGE",
    "NOT_PDF": "NOT_PDF",
    "PASSWORD_REQUIRED": "PASSWORD_REQUIRED",
    "OPEN_FAILED": "OPEN_FAILED",
    "OPEN_TIMEOUT": "OPEN_FAILED",
    "TOO_MANY_PAGES": "TOO_MANY_PAGES",
}


class DocumentImportError(Exception):
    """Tài liệu bị từ chối ở một bước của §6.5; tầng router đổi `code` thành mã HTTP."""

    def __init__(self, code: ImportErrorCode) -> None:
        super().__init__(IMPORT_ERROR_MESSAGES[code])
        self.code: ImportErrorCode = code


class DocumentNotFoundError(Exception):
    """Không có tài liệu nhập qua §6.5 với id này (bản ghi chỉ do upload đề tạo cũng tính là không có)."""


class DocumentBusyError(Exception):
    """Không xoá được tài liệu: đang đọc, hoặc còn `IngestionItem` tham chiếu (§6.5)."""


class OriginalFiles(Protocol):
    """Kho file gốc (`infrastructure/storage/original_files.py`)."""

    def stage(self, content: bytes) -> StagedFile: ...

    def commit(self, staged: StagedFile) -> Path: ...

    def discard(self, staged: StagedFile) -> None: ...

    def path_for(self, sha256: str) -> Path: ...

    def exists(self, sha256: str) -> bool: ...

    def delete(self, sha256: str) -> bool: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class ReadJob:
    """Một tài liệu chờ đọc: kho và bộ đọc đi cùng việc, nên hàng đợi không giữ phụ thuộc nào của riêng nó."""

    document_id: str
    path: Path
    store: DocumentRepository
    reader: PdfReader


class ImportQueue(Protocol):
    def submit(self, job: ReadJob) -> None:
        """Xếp `job` vào cuối hàng đợi (FIFO)."""
        ...

    def close(self) -> None:
        """Ngừng nhận việc; việc đang đọc dừng sau trang hiện tại, trang chưa đọc giữ `pending`."""
        ...


@dataclass(frozen=True)
class ImportRuntime:
    """Kho file gốc và hàng đợi đọc của app; `lifespan` tạo và đóng, router lấy qua `Depends`."""

    files: OriginalFiles
    queue: ImportQueue

    def close(self) -> None:
        self.queue.close()
        self.files.close()


@dataclass(frozen=True)
class ImportRequest:
    """Dữ liệu form của `POST /v1/documents` (§6.5) sau khi router đọc tối đa `MAX_DOCUMENT_BYTES + 1` byte."""

    content: bytes
    filename: str | None
    declared_type: str | None
    subject_code: str
    source_kind: SourceKind
    output_language: OutputLanguage = "vi"
    title: str | None = None


@dataclass(frozen=True)
class ImportOutcome:
    """`created = False`: tài liệu cùng nội dung đã được nhập cho cùng môn (§6.5 bước 3, trả 200 thay vì 202)."""

    created: bool
    document: DocumentImport


# --------------------------------------------------------------------------- #
# Dựng response
# --------------------------------------------------------------------------- #


def pending_page(document_id: str, page_number: int) -> PageExtraction:
    """Trang chưa đọc: chưa có tầng, điểm hay dòng nào."""
    return PageExtraction(
        document_id=document_id,
        page_number=page_number,
        status="pending",
        tier_used=None,
        text_score=None,
        cross_check=CrossCheck(status="not_applicable", secondary_engine=None),
    )


def document_view(store: DocumentRepository, document: ImportedDocument) -> DocumentImport:
    pages = store.list_pages(document.meta.doc_id, PageExtraction)
    return DocumentImport(
        meta=document.meta,
        filename=document.filename,
        size_bytes=document.size_bytes,
        output_language=document.output_language,
        status=document.status,
        pages=[PageSummary.from_page(page) for page in pages],
    )


def document_summary(store: DocumentRepository, document: ImportedDocument) -> DocumentSummary:
    pages = store.list_pages(document.meta.doc_id, PageExtraction)
    return DocumentSummary(
        meta=document.meta,
        status=document.status,
        pages_pending=sum(page.status == "pending" for page in pages),
        pages_needs_review=sum(page.status == "needs_review" for page in pages),
        pages_failed=sum(page.status == "failed" for page in pages),
        pages_confirmed=sum(page.confirmed for page in pages),
    )


def get_document_view(store: DocumentRepository, document_id: str) -> DocumentImport:
    document = store.get_imported_document(document_id)
    if document is None:
        raise DocumentNotFoundError(document_id)
    return document_view(store, document)


def list_document_summaries(store: DocumentRepository, subject_code: str | None = None) -> list[DocumentSummary]:
    """Mới nhất trước (thứ tự của kho)."""
    return [document_summary(store, document) for document in store.list_imported_documents(subject_code)]


def get_page_extraction(store: DocumentRepository, document_id: str, page_number: int) -> PageExtraction:
    """Trang `page_number` của tài liệu; tài liệu không có hoặc số trang ngoài `1..page_count` ⇒ không tìm thấy."""
    document = store.get_imported_document(document_id)
    if document is None or not 1 <= page_number <= document.meta.page_count:
        raise DocumentNotFoundError(document_id)
    page = store.get_page(document_id, page_number, PageExtraction)
    if page is None:
        raise DocumentNotFoundError(document_id)
    return page


# --------------------------------------------------------------------------- #
# Nhận file (§6.5 POST /v1/documents)
# --------------------------------------------------------------------------- #


def default_title(filename: str) -> str:
    """Tên file bỏ đuôi (§6.5); tên chỉ có đuôi hoặc toàn khoảng trắng thì dùng tiêu đề mặc định."""
    stem, dot, _ = filename.rpartition(".")
    base = (stem if dot and stem.strip() else filename).strip()
    return base[:MAX_TITLE_LENGTH] or DEFAULT_TITLE


def _check_upload(request: ImportRequest, max_bytes: int) -> str:
    """Bước 1–2 của §6.5, đúng thứ tự; trả tên file đã chuẩn hoá."""
    if len(request.content) > max_bytes:
        raise DocumentImportError("TOO_LARGE")
    if not request.content:
        raise DocumentImportError("EMPTY")
    raw_filename = request.filename or ""
    if len(raw_filename) > MAX_FILENAME_LENGTH:
        raise DocumentImportError("FILENAME_TOO_LONG")
    if not request.content.startswith(PDF_MAGIC):
        raise DocumentImportError("NOT_PDF")
    declared = (request.declared_type or "").split(";", 1)[0].strip().lower()
    if declared not in ACCEPTED_DECLARED_TYPES:
        raise DocumentImportError("CONTENT_TYPE_MISMATCH")
    return raw_filename.strip() or DEFAULT_FILENAME


def _probe(reader: PdfReader, staged: StagedFile) -> int:
    try:
        return reader.probe(staged.path)
    except PdfInputError as exc:
        raise DocumentImportError(_PROBE_ERRORS[exc.code]) from exc


def accept_document(
    request: ImportRequest,
    *,
    store: DocumentRepository,
    runtime: ImportRuntime,
    reader: PdfReader,
    now: datetime,
    max_bytes: int = MAX_DOCUMENT_BYTES,
) -> ImportOutcome:
    """Nhận một PDF theo đúng thứ tự của §6.5; lỗi ⇒ `DocumentImportError` và không để lại file hay bản ghi nào.

    `PdfWorkerError` (worker không khởi động được, lỗi môi trường chứ không phải lỗi file) được ném nguyên.
    """
    filename = _check_upload(request, max_bytes)
    sha256 = hashlib.sha256(request.content).hexdigest()
    document_id = f"doc_{sha256[:16]}"
    existing = store.get_imported_document(document_id)
    if existing is not None:
        if existing.meta.subject_code != request.subject_code:
            raise DocumentImportError("SUBJECT_CONFLICT")
        return ImportOutcome(created=False, document=document_view(store, existing))

    staged = runtime.files.stage(request.content)
    try:
        page_count = _probe(reader, staged)
    except BaseException:
        runtime.files.discard(staged)
        raise
    path = runtime.files.commit(staged)

    document = ImportedDocument(
        meta=DocumentMeta(
            doc_id=document_id,
            subject_code=request.subject_code,
            title=request.title.strip() if request.title is not None else default_title(filename),
            source_kind=request.source_kind,
            sha256=sha256,
            page_count=page_count,
            has_text_layer=False,
            uploaded_at=now,
        ),
        filename=filename,
        size_bytes=len(request.content),
        output_language=request.output_language,
        status="processing",
    )
    stored = store.save_imported_document(document)
    for page_number in range(1, page_count + 1):
        store.add_page(
            document_id, page_number, pending_page(document_id, page_number), status="pending", confirmed=False
        )
    view = document_view(store, stored)
    runtime.queue.submit(ReadJob(document_id=document_id, path=path, store=store, reader=reader))
    return ImportOutcome(created=True, document=view)


# --------------------------------------------------------------------------- #
# Đọc nền
# --------------------------------------------------------------------------- #


def _write_page(store: DocumentRepository, document_id: str, page: PageExtraction) -> None:
    """Ghi một trang vừa đọc vào chỗ trang `pending` tương ứng; `version` của trang khớp version của dòng trong kho."""
    if page.document_id != document_id:
        return
    stored = store.get_page(document_id, page.page_number, PageExtraction)
    if stored is None or stored.status != "pending":
        return
    read = page.model_copy(update={"version": stored.version + 1, "confirmed": False, "confirmed_at": None})
    store.update_page(
        document_id, page.page_number, read, status=read.status, confirmed=False, expected_version=stored.version
    )


def _fail_pending(store: DocumentRepository, document_id: str, reason: FailureReason) -> None:
    for page in store.list_pages(document_id, PageExtraction):
        if page.status == "pending":
            _write_page(store, document_id, failed_page(document_id, page.page_number, reason))


def _finish(store: DocumentRepository, document_id: str, status: Literal["review", "failed"]) -> None:
    """Đặt trạng thái cuối của lần đọc và `has_text_layer` (true khi ≥ 1 trang dùng tầng 1, §5.8)."""
    document = store.get_imported_document(document_id)
    if document is None:
        return
    pages = store.list_pages(document_id, PageExtraction)
    meta = document.meta.model_copy(update={"has_text_layer": any(p.tier_used == "text_layer" for p in pages)})
    store.update_imported_document(document.model_copy(update={"status": status, "meta": meta}), document.version)


def mark_document_failed(store: DocumentRepository, document_id: str) -> None:
    """Tài liệu không đọc tiếp được: trang còn `pending` thành `failed` (`PAGE_ERROR`), tài liệu thành `failed`."""
    _fail_pending(store, document_id, "PAGE_ERROR")
    _finish(store, document_id, "failed")


def _close_pages(pages: Iterator[PageExtraction]) -> None:
    """Đóng generator của bộ đọc để worker được dọn ngay (khi dừng giữa chừng)."""
    if isinstance(pages, Generator):
        pages.close()


def read_document(job: ReadJob, should_stop: Callable[[], bool] = lambda: False) -> None:
    """Đọc các trang còn `pending` của tài liệu, ghi từng trang khi đọc xong; xong thì tài liệu thành `review`.

    Bộ đọc không mở lại được tài liệu (`PdfInputError`, `PdfWorkerError`, file mất) ⇒ tài liệu `failed` (§5.7).
    `should_stop()` đúng ⇒ dừng sau trang hiện tại; trang chưa đọc giữ `pending`, tài liệu giữ `processing`.
    """
    store, document_id = job.store, job.document_id
    pending = [p.page_number for p in store.list_pages(document_id, PageExtraction) if p.status == "pending"]
    if pending:
        pages = iter(job.reader.read_pages(job.path, document_id, pending))
        try:
            first = next(pages, None)
        except (PdfInputError, PdfWorkerError, ValueError, OSError):
            _LOGGER.warning("không mở lại được tài liệu %s để đọc", document_id)
            mark_document_failed(store, document_id)
            return
        try:
            page = first
            while page is not None:
                _write_page(store, document_id, page)
                if should_stop():
                    return
                page = next(pages, None)
        finally:
            _close_pages(pages)
    _finish(store, document_id, "review")


class SynchronousImportQueue:
    """Đọc ngay trong lời gọi `submit` (test và script); cùng thứ tự FIFO như bản chạy nền."""

    def __init__(self, run: Callable[[ReadJob], None] = read_document) -> None:
        self._run = run
        self.submitted: list[str] = []

    def submit(self, job: ReadJob) -> None:
        self.submitted.append(job.document_id)
        self._run(job)

    def close(self) -> None:
        return None


class BackgroundImportQueue:
    """Hàng đợi FIFO trong tiến trình backend; một luồng nền đọc một tài liệu mỗi lúc (§6.5)."""

    def __init__(self, run: Callable[[ReadJob, Callable[[], bool]], None] = read_document) -> None:
        self._run = run
        self._jobs: queue.Queue[ReadJob | None] = queue.Queue()
        self._stop = threading.Event()
        self._idle = threading.Condition()
        self._unfinished = 0
        self._thread = threading.Thread(target=self._loop, name="holocron-document-import", daemon=True)
        self._thread.start()

    def submit(self, job: ReadJob) -> None:
        if self._stop.is_set():
            raise RuntimeError("hàng đợi đọc tài liệu đã đóng")
        with self._idle:
            self._unfinished += 1
        self._jobs.put(job)

    def _loop(self) -> None:
        while True:
            job = self._jobs.get()
            if job is None:
                return
            try:
                if not self._stop.is_set():
                    self._run(job, self._stop.is_set)
            except Exception:
                # Không để một tài liệu hỏng làm chết luồng đọc; log chỉ ghi id, không ghi nội dung tài liệu.
                _LOGGER.exception("lỗi khi đọc tài liệu %s", job.document_id)
                with contextlib.suppress(StorageError, ValueError):
                    mark_document_failed(job.store, job.document_id)
            finally:
                with self._idle:
                    self._unfinished -= 1
                    self._idle.notify_all()

    def wait_until_idle(self, timeout_s: float) -> bool:
        """Chờ tới khi mọi việc đã nhận xong; `False` khi hết `timeout_s`."""
        with self._idle:
            return self._idle.wait_for(lambda: self._unfinished == 0, timeout=timeout_s)

    def close(self, timeout_s: float = QUEUE_CLOSE_TIMEOUT_S) -> None:
        self._stop.set()
        self._jobs.put(None)
        self._thread.join(timeout=timeout_s)


def requeue_pending(store: DocumentRepository, files: OriginalFiles, reader: PdfReader, jobs: ImportQueue) -> list[str]:
    """Khi khởi động: xếp lại tài liệu còn `processing` theo thứ tự nhận (cũ trước), từ file gốc đã lưu (§6.5).

    File gốc không còn ⇒ tài liệu `failed`. Trả id các tài liệu đã xếp lại.
    """
    queued: list[str] = []
    for document in reversed(store.list_imported_documents()):
        if document.status != "processing":
            continue
        document_id = document.meta.doc_id
        if not files.exists(document.meta.sha256):
            _LOGGER.warning("mất file gốc của tài liệu %s; đánh dấu failed", document_id)
            mark_document_failed(store, document_id)
            continue
        path = files.path_for(document.meta.sha256)
        jobs.submit(ReadJob(document_id=document_id, path=path, store=store, reader=reader))
        queued.append(document_id)
    return queued


# --------------------------------------------------------------------------- #
# Xoá (§6.5 DELETE /v1/documents/{id})
# --------------------------------------------------------------------------- #


def delete_imported_document(store: DocumentRepository, files: OriginalFiles, document_id: str) -> None:
    """Xoá tài liệu cùng trang, chunk, chỉ mục, cặp khái niệm (kho) và file gốc.

    Không có ⇒ `DocumentNotFoundError`; đang `processing` hoặc còn item tham chiếu ⇒ `DocumentBusyError`.
    """
    document = store.get_imported_document(document_id)
    if document is None:
        raise DocumentNotFoundError(document_id)
    if document.status == "processing":
        raise DocumentBusyError("tài liệu đang được đọc, chưa xoá được")
    try:
        deleted = store.delete_document(document_id)
    except RecordInUseError as exc:
        raise DocumentBusyError("tài liệu còn được ingestion item tham chiếu") from exc
    if not deleted:
        raise DocumentNotFoundError(document_id)
    # `document_id` suy ra từ sha256 (cột UNIQUE), nên sau khi xoá không còn tài liệu nào dùng file gốc này.
    files.delete(document.meta.sha256)
