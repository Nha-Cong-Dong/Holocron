"""Bộ nhập tài liệu PDF, phần nhận file, đọc nền, xem và xoá (SPECIFICATION §5.7, §6.5; ADR-008 mục 3, 4, 7).

- `accept_document` kiểm file theo đúng thứ tự của §6.5, mở thử trong worker (qua `PdfReader`), lưu file gốc, tạo
  tài liệu cùng `page_count` trang `pending` trong MỘT giao dịch của kho, rồi xếp tài liệu vào hàng đợi đọc. Mọi bước
  với cùng một nội dung chạy dưới khoá theo `document_id` (`KeyedLocks`), nên hai request cùng file không tạo trùng.
- `read_document` đọc các trang còn `pending` qua `PdfReader` và ghi từng trang vào kho ngay khi đọc xong.
- `BackgroundImportQueue`: hàng đợi FIFO, một luồng nền đọc một tài liệu mỗi lúc (§6.5). Đối tượng này, khoá theo
  `document_id` và cổng nhận việc (`AdmissionGate`) nằm trong `ImportRuntime`, do `lifespan` của app tạo và đóng,
  không phải biến toàn cục; test dùng `SynchronousImportQueue`.

Các hàm ở đây chạy đồng bộ và có thể chặn lâu (worker mở thử tới vài chục giây, ghi file 50 MiB): router PHẢI gọi
chúng ngoài event loop (handler `def`, FastAPI chạy trong threadpool).

Không văn bản nào đi vào Vault hay Gate ở đây: trang chỉ được lưu để người dùng xem lại (ADR-008 mục 7). File PDF là
đầu vào không tin cậy, chỉ được mở trong worker có giới hạn của `PdfReader` (CLAUDE.md §2.2).
"""

from __future__ import annotations

import contextlib
import functools
import hashlib
import logging
import queue
import threading
import time
from collections.abc import Callable, Collection, Generator, Iterator
from dataclasses import dataclass, field
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
from ..domain.repositories import (
    DocumentExistsError,
    DocumentRepository,
    ImportedDocument,
    RecordInUseError,
    StorageBusyError,
    StorageError,
)
from ..domain.schemas import DocumentMeta, SourceKind
from ..infrastructure.document_readers.config import PdfReaderConfig
from ..infrastructure.document_readers.pdf_reader import PDF_MAGIC, PdfInputError, PdfReader, PdfWorkerError
from ..infrastructure.document_readers.tiers import failed_page
from ..infrastructure.storage.original_files import StagedFile

MAX_FILENAME_LENGTH = 255
DEFAULT_FILENAME = "upload.pdf"
DEFAULT_TITLE = "Tài liệu"
MAX_TITLE_LENGTH = 300
PDF_MEDIA_TYPE = "application/pdf"
# `Content-Type` khai báo được chấp nhận (§6.5 bước 2): rỗng, `application/octet-stream` hoặc đúng PDF.
ACCEPTED_DECLARED_TYPES = frozenset({"", "application/octet-stream", PDF_MEDIA_TYPE})
# Thời gian chờ tối đa khi tắt app: request đang lưu tài liệu, rồi luồng đọc nền (đọc lúc gọi, test được đổi).
QUEUE_CLOSE_TIMEOUT_S = 10.0
# Ghi một trang gặp kho bận (`StorageBusyError`, sau `busy_timeout` của SQLite): thử lại tối đa chừng ấy lần,
# lần thứ k chờ k × `WRITE_RETRY_DELAY_S`; vẫn bận thì dừng đọc tài liệu, trang chưa ghi giữ `pending`.
WRITE_ATTEMPTS = 3
WRITE_RETRY_DELAY_S = 0.2
IMPORT_UNAVAILABLE_DETAIL = "Bộ nhập tài liệu đang dừng (app đang tắt); hãy gửi lại sau khi khởi động lại"

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
    "TOO_LARGE": "File vượt giới hạn dung lượng",
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

    def __init__(self, code: ImportErrorCode, message: str | None = None) -> None:
        super().__init__(message or IMPORT_ERROR_MESSAGES[code])
        self.code: ImportErrorCode = code


class ImportUnavailableError(Exception):
    """Bộ nhập đã ngừng nhận việc (app đang tắt) hoặc hàng đợi đọc từ chối việc; không có bản ghi nào được giữ lại."""

    def __init__(self) -> None:
        super().__init__(IMPORT_UNAVAILABLE_DETAIL)


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

    def remove_orphans(self, keep: Collection[str]) -> list[str]: ...

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

    def close(self, timeout_s: float = QUEUE_CLOSE_TIMEOUT_S) -> bool:
        """Ngừng nhận việc; việc đang đọc dừng trước lần ghi trang kế tiếp, trang chưa ghi giữ `pending`.

        Chờ luồng đọc tối đa `timeout_s`; `True` khi không còn luồng nào đang đọc.
        """
        ...


class KeyedLocks:
    """Khoá theo khoá chuỗi (ở đây `document_id`), cấp phát khi cần và bỏ khi không còn ai giữ hay chờ."""

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._locks: dict[str, tuple[threading.Lock, int]] = {}

    @contextlib.contextmanager
    def hold(self, key: str) -> Iterator[None]:
        with self._guard:
            lock, users = self._locks.get(key, (threading.Lock(), 0))
            self._locks[key] = (lock, users + 1)
        try:
            with lock:
                yield
        finally:
            with self._guard:
                lock, users = self._locks[key]
                if users == 1:
                    del self._locks[key]
                else:
                    self._locks[key] = (lock, users - 1)

    def __len__(self) -> int:
        """Số khoá đang có người giữ hoặc chờ."""
        with self._guard:
            return len(self._locks)


class AdmissionGate:
    """Cổng nhận việc của bộ nhập: `close` chặn lượt nhận mới và chờ các lượt đang lưu tài liệu xong.

    Nhờ vậy hàng đợi chỉ bị đóng khi không request nào nằm giữa "đã tạo bản ghi" và "đã xếp vào hàng đợi".
    """

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._closed = False
        self._active = 0

    def enter(self) -> None:
        """Bắt đầu một lượt nhận; cổng đã đóng ⇒ `ImportUnavailableError`."""
        with self._condition:
            if self._closed:
                raise ImportUnavailableError()
            self._active += 1

    def leave(self) -> None:
        with self._condition:
            self._active -= 1
            self._condition.notify_all()

    def close(self, timeout_s: float) -> bool:
        """Đóng cổng rồi chờ tối đa `timeout_s`; `True` khi không còn lượt nhận nào đang chạy."""
        with self._condition:
            self._closed = True
            return self._condition.wait_for(lambda: self._active == 0, timeout=timeout_s)


@dataclass(frozen=True)
class ImportRuntime:
    """Kho file gốc, hàng đợi đọc, cấu hình bộ đọc, khoá theo tài liệu và cổng nhận việc của app.

    `lifespan` tạo và đóng; router lấy qua `Depends`. `reader_config` là nguồn duy nhất của giới hạn dung lượng
    (`max_bytes`): router, `accept_document`, bộ đọc của `get_pdf_reader` và kho file gốc đều dùng nó.
    """

    files: OriginalFiles
    queue: ImportQueue
    reader_config: PdfReaderConfig = field(default_factory=PdfReaderConfig)
    locks: KeyedLocks = field(default_factory=KeyedLocks)
    gate: AdmissionGate = field(default_factory=AdmissionGate)

    def close(self, timeout_s: float | None = None) -> bool:
        """Ngừng nhận tài liệu, dừng hàng đợi rồi dọn kho file gốc; `True` khi mọi luồng đã dừng hẳn.

        Hết `timeout_s` (mặc định `QUEUE_CLOSE_TIMEOUT_S`) mà luồng đọc nền hoặc một request vẫn chạy thì KHÔNG xoá
        thư mục file tạm dưới chân chúng: ghi cảnh báo, thư mục được dọn ở lần khởi động sau
        (`original_files.remove_stale_temp_dirs`).
        """
        timeout = QUEUE_CLOSE_TIMEOUT_S if timeout_s is None else timeout_s
        admissions_done = self.gate.close(timeout)
        stopped = self.queue.close(timeout)
        if admissions_done and stopped:
            self.files.close()
            return True
        _LOGGER.warning("bộ nhập tài liệu chưa dừng hẳn sau %.1f s; giữ nguyên thư mục file gốc", timeout)
        return False


@dataclass(frozen=True)
class ImportRequest:
    """Dữ liệu form của `POST /v1/documents` (§6.5) sau khi router đọc tối đa `max_bytes + 1` byte."""

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


def _view(document: ImportedDocument, pages: list[PageExtraction]) -> DocumentImport:
    return DocumentImport(
        meta=document.meta,
        filename=document.filename,
        size_bytes=document.size_bytes,
        output_language=document.output_language,
        status=document.status,
        pages=[PageSummary.from_page(page) for page in pages],
    )


def document_view(store: DocumentRepository, document: ImportedDocument) -> DocumentImport:
    return _view(document, store.list_pages(document.meta.doc_id, PageExtraction))


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
        raise DocumentImportError("TOO_LARGE", f"File vượt giới hạn {max_bytes} bytes")
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


def _existing_outcome(store: DocumentRepository, existing: ImportedDocument, subject_code: str) -> ImportOutcome:
    """§6.5 bước 3: cùng môn ⇒ 200 với tài liệu hiện có; khác môn ⇒ `SUBJECT_CONFLICT`."""
    if existing.meta.subject_code != subject_code:
        raise DocumentImportError("SUBJECT_CONFLICT")
    return ImportOutcome(created=False, document=document_view(store, existing))


def _discard_unused_file(store: DocumentRepository, files: OriginalFiles, document_id: str, sha256: str) -> None:
    """Xoá file gốc vừa lưu khi không còn tài liệu nhập nào dùng nó; không xoá được ⇒ cảnh báo, file mồ côi được
    dọn ở lần khởi động sau (`remove_orphan_files`)."""
    try:
        if store.get_imported_document(document_id) is None:
            files.delete(sha256)
    except (StorageError, OSError) as exc:
        _LOGGER.warning("chưa dọn được file gốc của tài liệu %s (%s)", document_id, type(exc).__name__)


def _undo_create(store: DocumentRepository, files: OriginalFiles, document_id: str, sha256: str) -> None:
    """Huỷ tài liệu vừa tạo nhưng không xếp được vào hàng đợi (chưa trang nào được đọc)."""
    try:
        store.delete_document(document_id)
    except StorageError as exc:
        _LOGGER.warning(
            "không huỷ được tài liệu %s (%s); tài liệu giữ processing và được xếp lại khi khởi động",
            document_id,
            type(exc).__name__,
        )
        return
    _discard_unused_file(store, files, document_id, sha256)


def accept_document(
    request: ImportRequest,
    *,
    store: DocumentRepository,
    runtime: ImportRuntime,
    reader: PdfReader,
    now: datetime,
) -> ImportOutcome:
    """Nhận một PDF theo đúng thứ tự của §6.5. Chặn tới vài chục giây: gọi ngoài event loop.

    Cả lần nhận chạy dưới khoá của `document_id` (`runtime.locks`): request thứ hai cùng nội dung chờ request đầu
    xong rồi thấy tài liệu đã có (200, hoặc 409 khi khác môn). Tài liệu cùng mọi trang `pending` được tạo trong MỘT
    giao dịch (`create_imported_document`); nếu một tiến trình khác vẫn kịp tạo trước (`DocumentExistsError`) thì
    trả tài liệu đó như bước 3.

    Lỗi:
    - `DocumentImportError` (bước 1–4): không để lại file hay bản ghi nào.
    - `PdfWorkerError` (worker không khởi động được, lỗi môi trường chứ không phải lỗi file): ném nguyên, không để
      lại file hay bản ghi nào.
    - Lỗi kho khi tạo tài liệu (ví dụ `StorageBusyError`): không có bản ghi nào; file gốc vừa lưu được xoá nếu
      không còn tài liệu nào dùng nó (xoá lỗi thì dọn ở lần khởi động sau). Lỗi được ném nguyên.
    - `ImportUnavailableError`: bộ nhập đang tắt (cổng đã đóng; không ghi gì) hoặc hàng đợi từ chối việc (tài liệu
      vừa tạo bị huỷ cùng file gốc, nên không kẹt `processing`).
    """
    filename = _check_upload(request, runtime.reader_config.max_bytes)
    sha256 = hashlib.sha256(request.content).hexdigest()
    document_id = f"doc_{sha256[:16]}"
    with runtime.locks.hold(document_id):
        existing = store.get_imported_document(document_id)
        if existing is not None:
            return _existing_outcome(store, existing, request.subject_code)
        staged = runtime.files.stage(request.content)
        try:
            page_count = _probe(reader, staged)
            runtime.gate.enter()
        except BaseException:
            runtime.files.discard(staged)
            raise
        try:
            return _store_and_queue(
                request,
                filename=filename,
                document_id=document_id,
                page_count=page_count,
                staged=staged,
                store=store,
                runtime=runtime,
                reader=reader,
                now=now,
            )
        finally:
            runtime.gate.leave()


def _store_and_queue(
    request: ImportRequest,
    *,
    filename: str,
    document_id: str,
    page_count: int,
    staged: StagedFile,
    store: DocumentRepository,
    runtime: ImportRuntime,
    reader: PdfReader,
    now: datetime,
) -> ImportOutcome:
    """§6.5 bước 5: lưu file gốc, tạo tài liệu và trang `pending` (một giao dịch), xếp hàng đợi."""
    try:
        path = runtime.files.commit(staged)
    except BaseException:
        runtime.files.discard(staged)
        raise
    document = ImportedDocument(
        meta=DocumentMeta(
            doc_id=document_id,
            subject_code=request.subject_code,
            title=request.title.strip() if request.title is not None else default_title(filename),
            source_kind=request.source_kind,
            sha256=staged.sha256,
            page_count=page_count,
            has_text_layer=False,
            uploaded_at=now,
        ),
        filename=filename,
        size_bytes=len(request.content),
        output_language=request.output_language,
        status="processing",
    )
    pages = [pending_page(document_id, number) for number in range(1, page_count + 1)]
    try:
        stored = store.create_imported_document(document, pages)
    except DocumentExistsError:
        existing = store.get_imported_document(document_id)
        if existing is None:
            raise
        return _existing_outcome(store, existing, request.subject_code)
    except BaseException:
        _discard_unused_file(store, runtime.files, document_id, staged.sha256)
        raise
    # Dựng response từ dữ liệu vừa ghi, không đọc lại kho: tài liệu đã tạo thì luôn được xếp hàng đợi ngay sau đây.
    view = _view(stored, pages)
    try:
        runtime.queue.submit(ReadJob(document_id=document_id, path=path, store=store, reader=reader))
    except Exception as exc:
        _LOGGER.warning("hàng đợi đọc từ chối tài liệu %s (%s); huỷ lần nhập", document_id, type(exc).__name__)
        _undo_create(store, runtime.files, document_id, staged.sha256)
        raise ImportUnavailableError() from exc
    return ImportOutcome(created=True, document=view)


# --------------------------------------------------------------------------- #
# Đọc nền
# --------------------------------------------------------------------------- #


def _never() -> bool:
    return False


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


def _write_page_retrying(
    store: DocumentRepository,
    document_id: str,
    page: PageExtraction,
    should_stop: Callable[[], bool],
    pause: Callable[[float], None],
) -> None:
    """`_write_page`, thử lại tối đa `WRITE_ATTEMPTS` lần khi kho bận; vẫn bận (hoặc đang dừng) ⇒ ném lỗi."""
    for attempt in range(1, WRITE_ATTEMPTS + 1):
        try:
            _write_page(store, document_id, page)
            return
        except StorageBusyError:
            if attempt == WRITE_ATTEMPTS or should_stop():
                raise
            _LOGGER.warning(
                "kho bận khi ghi trang %d của tài liệu %s; thử lại (%d/%d)",
                page.page_number,
                document_id,
                attempt,
                WRITE_ATTEMPTS,
            )
            pause(WRITE_RETRY_DELAY_S * attempt)


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


def read_document(
    job: ReadJob, should_stop: Callable[[], bool] = _never, pause: Callable[[float], None] = time.sleep
) -> None:
    """Đọc các trang còn `pending` của tài liệu, ghi từng trang khi đọc xong; xong thì tài liệu thành `review`.

    - Bộ đọc không mở lại được tài liệu (`PdfInputError`, `PdfWorkerError`, file mất) ⇒ tài liệu `failed` (§5.7).
    - `should_stop()` được kiểm trước khi mở bộ đọc, trước và sau mỗi lần ghi trang: đúng ⇒ dừng ngay, không ghi
      thêm; trang chưa ghi giữ `pending`, tài liệu giữ `processing` (được xếp lại khi khởi động, §6.5).
    - Lỗi tạm thời KHÔNG làm tài liệu `failed`: kho bận khi ghi một trang được thử lại có giới hạn
      (`_write_page_retrying`), vẫn bận thì `StorageBusyError` được ném ra cho hàng đợi (tài liệu giữ
      `processing`); bộ đọc ném `PdfInputError`, `RuntimeError` (gồm `PdfWorkerError`), `ValueError` hoặc `OSError`
      sau khi đã trả trang đầu (trái `PdfReader`, ví dụ worker chết bất thường) ⇒ dừng đọc, trang chưa đọc giữ
      `pending` cho lần khởi động sau.
    """
    store, document_id = job.store, job.document_id
    pending = [p.page_number for p in store.list_pages(document_id, PageExtraction) if p.status == "pending"]
    if pending:
        if should_stop():
            return
        pages: Iterator[PageExtraction] = iter(())
        try:
            pages = iter(job.reader.read_pages(job.path, document_id, pending))
            first = next(pages, None)
        except (PdfInputError, PdfWorkerError, ValueError, OSError):
            _close_pages(pages)
            _LOGGER.warning("không mở lại được tài liệu %s để đọc", document_id)
            mark_document_failed(store, document_id)
            return
        try:
            page = first
            while page is not None:
                if should_stop():
                    return
                _write_page_retrying(store, document_id, page, should_stop, pause)
                if should_stop():
                    return
                try:
                    page = next(pages, None)
                except (PdfInputError, RuntimeError, ValueError, OSError) as exc:
                    _LOGGER.warning(
                        "bộ đọc lỗi giữa chừng ở tài liệu %s (%s); trang chưa đọc giữ pending",
                        document_id,
                        type(exc).__name__,
                    )
                    return
        finally:
            _close_pages(pages)
    if should_stop():
        return
    _finish(store, document_id, "review")


def run_job(run: Callable[[], None], job: ReadJob, should_stop: Callable[[], bool]) -> None:
    """Chạy một việc đọc mà không để lỗi thoát ra luồng của hàng đợi; log chỉ ghi id, không ghi nội dung tài liệu.

    - Đang dừng (`should_stop()`): mọi lỗi, kể cả do kho đã đóng, chỉ ghi log mức info rồi thoát êm.
    - `StorageError` (kho bận, xung đột ghi): tạm thời, tài liệu giữ `processing`, không đánh dấu `failed`.
    - Lỗi khác (lỗi lập trình của bộ đọc hay của việc): tài liệu `failed`, như khi không mở lại được.
    """
    try:
        run()
    except Exception as exc:
        if should_stop():
            _LOGGER.info("dừng đọc tài liệu %s vì app đang tắt (%s)", job.document_id, type(exc).__name__)
            return
        if isinstance(exc, StorageError):
            _LOGGER.warning(
                "lỗi kho khi đọc tài liệu %s (%s); tài liệu giữ processing, trang chưa ghi giữ pending",
                job.document_id,
                type(exc).__name__,
            )
            return
        _LOGGER.exception("lỗi khi đọc tài liệu %s", job.document_id)
        try:
            mark_document_failed(job.store, job.document_id)
        except Exception:
            _LOGGER.exception("không đánh dấu được tài liệu %s là failed", job.document_id)


class SynchronousImportQueue:
    """Đọc ngay trong lời gọi `submit` (test và script); cùng thứ tự FIFO và cùng cách xử lý lỗi như bản chạy nền."""

    def __init__(self, run: Callable[[ReadJob], None] = read_document) -> None:
        self._run = run
        self.submitted: list[str] = []

    def submit(self, job: ReadJob) -> None:
        self.submitted.append(job.document_id)
        run_job(functools.partial(self._run, job), job, _never)

    def close(self, timeout_s: float = QUEUE_CLOSE_TIMEOUT_S) -> bool:
        return True


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
                    run_job(functools.partial(self._run, job, self._stop.is_set), job, self._stop.is_set)
            finally:
                with self._idle:
                    self._unfinished -= 1
                    self._idle.notify_all()

    def wait_until_idle(self, timeout_s: float) -> bool:
        """Chờ tới khi mọi việc đã nhận xong; `False` khi hết `timeout_s`."""
        with self._idle:
            return self._idle.wait_for(lambda: self._unfinished == 0, timeout=timeout_s)

    @property
    def running(self) -> bool:
        """Luồng nền còn chạy (chưa thoát)."""
        return self._thread.is_alive()

    def close(self, timeout_s: float = QUEUE_CLOSE_TIMEOUT_S) -> bool:
        self._stop.set()
        self._jobs.put(None)
        self._thread.join(timeout=timeout_s)
        return not self._thread.is_alive()


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


def remove_orphan_files(store: DocumentRepository, files: OriginalFiles) -> list[str]:
    """Khi khởi động: xoá file gốc không thuộc tài liệu nhập nào (xoá file lỗi ở lần `DELETE` trước, hoặc tiến trình
    bị ngắt giữa lúc lưu file và lúc tạo bản ghi). Trả sha256 các file đã xoá."""
    return files.remove_orphans({document.meta.sha256 for document in store.list_imported_documents()})


# --------------------------------------------------------------------------- #
# Xoá (§6.5 DELETE /v1/documents/{id})
# --------------------------------------------------------------------------- #


def delete_imported_document(store: DocumentRepository, runtime: ImportRuntime, document_id: str) -> None:
    """Xoá tài liệu cùng trang, chunk, chỉ mục, cặp khái niệm (kho) và file gốc, dưới khoá của `document_id`.

    Không có ⇒ `DocumentNotFoundError`; đang `processing` hoặc còn item tham chiếu ⇒ `DocumentBusyError`. Kho đã xoá
    mà xoá file gốc lỗi (ví dụ Windows `PermissionError` khi file đang mở) ⇒ chỉ ghi cảnh báo: file mồ côi được dọn ở
    lần khởi động sau (`remove_orphan_files`).
    """
    with runtime.locks.hold(document_id):
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
        try:
            runtime.files.delete(document.meta.sha256)
        except OSError as exc:
            _LOGGER.warning(
                "đã xoá tài liệu %s nhưng chưa xoá được file gốc (%s); dọn ở lần khởi động sau",
                document_id,
                type(exc).__name__,
            )
