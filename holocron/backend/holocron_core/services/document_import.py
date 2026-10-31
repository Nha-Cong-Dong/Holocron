"""Bộ nhập tài liệu PDF: nhận file, đọc nền, xem, sửa dòng, xác nhận và xoá (SPECIFICATION §5.7, §6.5; ADR-008 mục
3, 4, 7; ADR-006 mục 1).

- `accept_document` kiểm file theo đúng thứ tự của §6.5, mở thử trong worker (qua `PdfReader`), lưu file gốc, tạo
  tài liệu cùng `page_count` trang `pending` trong MỘT giao dịch của kho, rồi xếp tài liệu vào hàng đợi đọc. Mọi bước
  với cùng một nội dung chạy dưới khoá theo `document_id` (`KeyedLocks`), nên hai request cùng file không tạo trùng.
- `read_document` đọc các trang còn `pending` qua `PdfReader` và ghi từng trang vào kho ngay khi đọc xong.
- `edit_page_lines`, `confirm_page`, `confirm_document`: người dùng sửa dòng và xác nhận văn bản (§6.5). Văn bản CHỈ
  vào Knowledge Vault khi tài liệu thành `confirmed` (mọi trang có văn bản đã xác nhận), qua bộ cắt chunk tất định
  (`services.vault.ingest_confirmed_text`, §12.1).
- `BackgroundImportQueue`: hàng đợi FIFO, một luồng nền đọc một tài liệu mỗi lúc (§6.5). Đối tượng này, khoá theo
  `document_id` và cổng nhận việc (`AdmissionGate`) nằm trong `ImportRuntime`, do `lifespan` của app tạo và đóng,
  không phải biến toàn cục; test dùng `SynchronousImportQueue`.

Các hàm ở đây chạy đồng bộ và có thể chặn lâu (worker mở thử tới vài chục giây, ghi file 50 MiB): router PHẢI gọi
chúng ngoài event loop (handler `def`, FastAPI chạy trong threadpool).

Trước khi người dùng xác nhận, không văn bản nào đi vào Vault hay Gate: trang chỉ được lưu để xem lại (ADR-008 mục
7). File PDF là đầu vào không tin cậy, chỉ được mở trong worker có giới hạn của `PdfReader` (CLAUDE.md §2.2).

GĐ0 0D (sửa đổi 2026-10-31, spec §5.7 "Khử nhiễu trang và vai trò trang"): ở bước kết thúc lần đọc (`_finish`), dưới
khoá của tài liệu và trước khi ghi trạng thái cuối, service gán vai trò trang và bỏ mềm (`page_roles.assign_roles`);
sau khi nhả khoá, đánh giá lại trang bìa của các tài liệu nhập trước cùng môn (`_reassess_previous_covers`, lấy khoá
từng tài liệu một). Trang bỏ mềm không vào văn bản xác nhận hay chunk, không chặn bước chốt, không sửa hay xác nhận
được cho tới khi hoàn tác (`restore_page`). Dòng header/footer không phải dòng có chữ và không vào văn bản xác nhận.

GĐ1 1B (thêm 2026-10-31, spec §5.7 "Tự xác nhận cấp trang"): ngay sau bước 0D, cũng dưới khoá của tài liệu,
`_auto_confirm_pages` tự xác nhận trang đủ sạch theo luật `page_clean_v1` (`auto_confirm.passes_page_clean`). Tự xác
nhận chỉ đặt trạng thái trang: tài liệu có trang `confirmed_by = "auto"` chỉ được chốt `confirmed` (và cắt chunk) khi
người dùng gọi `confirm_document` (`settle_document(..., by_user=True)`); bước chốt ở cuối lần đọc, ở đường `failed`
và sau `confirm_page` không đóng nó. `unconfirm_page` hoàn tác một trang tự xác nhận khi văn bản tài liệu còn mở.
"""

from __future__ import annotations

import contextlib
import functools
import hashlib
import logging
import queue
import threading
import time
from collections import Counter
from collections.abc import Callable, Collection, Generator, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

from ..domain.documents import (
    ChapterSource,
    ConfirmedBy,
    CrossCheck,
    DocumentConfirmRequest,
    DocumentImport,
    DocumentSummary,
    ExtractedLine,
    FailureReason,
    OutputLanguage,
    PageConfirmRequest,
    PageEditAudit,
    PageExtraction,
    PageLineChange,
    PageLinesPatch,
    PageRestoreRequest,
    PageSummary,
    PageUnconfirmRequest,
    ReviewReason,
    count_auto_confirmed,
    count_dropped,
    count_kept,
    count_kept_unconfirmed,
    count_needs_review,
)
from ..domain.ingestion import UploadedDocument
from ..domain.limits import MAX_LINE_CHARS
from ..domain.repositories import (
    DocumentExistsError,
    DocumentRepository,
    ImportedDocument,
    RecordInUseError,
    StorageBusyError,
    StorageError,
    VaultRepository,
    VersionConflictError,
)
from ..domain.schemas import DocumentMeta, SourceKind
from ..domain.vault import MAX_PAGE_LINES, ConfirmedDocumentText, ConfirmedPage
from ..infrastructure.document_readers.config import PdfReaderConfig
from ..infrastructure.document_readers.pdf_reader import PDF_MAGIC, PdfInputError, PdfReader, PdfWorkerError
from ..infrastructure.document_readers.tiers import failed_page
from ..infrastructure.storage.original_files import StagedFile
from .auto_confirm import AUTO_CONFIRM_RULE, passes_page_clean
from .chapter_naming import DEFAULT_TITLE, InitialChapter
from .chapter_reservation import ChapterReservations, ChapterTurn, reserve_chapter
from .page_roles import RoleDecision, apply_decision, assign_roles, changeable, reassess_cover
from .vault import ingest_confirmed_text

MAX_FILENAME_LENGTH = 255
DEFAULT_FILENAME = "upload.pdf"
MAX_TITLE_LENGTH = 300
PDF_MEDIA_TYPE = "application/pdf"
# `Content-Type` khai báo được chấp nhận (§6.5 bước 2): rỗng, `application/octet-stream` hoặc đúng PDF.
ACCEPTED_DECLARED_TYPES = frozenset({"", "application/octet-stream", PDF_MEDIA_TYPE})
# Thời gian chờ tối đa khi tắt app: request đang lưu tài liệu, rồi luồng đọc nền (đọc lúc gọi, test được đổi).
QUEUE_CLOSE_TIMEOUT_S = 10.0
# Mặc định của `RetryPolicy` (cấu hình của `ImportRuntime`): số lần thử và bước chờ khi ghi một trang gặp kho bận
# hoặc khi mở bộ đọc gặp lỗi môi trường.
WRITE_ATTEMPTS = 3
WRITE_RETRY_DELAY_S = 0.2
# Đặt tài liệu thành `confirmed` mà luồng đọc nền vừa ghi tài liệu (version lệch): đọc lại rồi thử tối đa chừng ấy lần.
SETTLE_ATTEMPTS = 3
IMPORT_UNAVAILABLE_DETAIL = "Bộ nhập tài liệu đang dừng (app đang tắt); hãy gửi lại sau khi khởi động lại"
CHAPTER_MISSING_DETAIL = "Tài liệu được nhập trước khi có chapter_code; hãy xoá tài liệu rồi nhập lại"
SETTLE_PENDING_DETAIL = (
    "Văn bản của tài liệu đã vào Knowledge Vault nhưng tài liệu chưa hoàn tất xác nhận; hãy gọi lại xác nhận tài "
    "liệu, không sửa thêm được"
)
# Giới hạn trang của Knowledge Vault lấy từ `domain.vault` (`ConfirmedPage`), không ghi lại con số ở đây.
# 409 khi thao tác trên trang đang bị bỏ mềm (GĐ0 0D; `detail` đề xuất của §6.5).
DROPPED_EDIT_DETAIL = "Trang đang bị bỏ qua; hãy hoàn tác trước khi sửa"
DROPPED_CONFIRM_DETAIL = "Trang đang bị bỏ qua; hãy hoàn tác trước khi xác nhận"
USER_CONFIRMED_UNDO_DETAIL = "Trang do người dùng xác nhận, không hoàn tác được"
PAGE_TOO_LARGE_DETAIL = (
    f"Trang vượt giới hạn của Knowledge Vault (tối đa {MAX_PAGE_LINES} dòng có văn bản, mỗi dòng tối đa "
    f"{MAX_LINE_CHARS} ký tự) nên không xác nhận được"
)

_LOGGER = logging.getLogger(__name__)

ImportErrorCode = Literal[
    "TOO_LARGE",
    "EMPTY",
    "FILENAME_TOO_LONG",
    "NOT_PDF",
    "CONTENT_TYPE_MISMATCH",
    "SUBJECT_CONFLICT",
    "CHAPTER_CONFLICT",
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
    "CHAPTER_CONFLICT": (
        "Tài liệu này đã được nhập cho một chương khác của môn này; muốn đổi chương thì sửa nhãn chunk hoặc xoá tài "
        "liệu rồi nhập lại"
    ),
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


class PageStateError(Exception):
    """Trạng thái trang hoặc tài liệu không cho phép thao tác (§6.5, 409): trang `pending`, `failed`, đã xác nhận,
    không có văn bản; tài liệu còn trang `pending` hoặc đã xác nhận."""


class InvalidPageEditError(ValueError):
    """Body của `PATCH …/lines` sai so với trang đang lưu (§6.5, 422): `line_index` không tồn tại, quá nhiều dòng."""


class PageTooLargeError(Exception):
    """Trang vượt giới hạn trang của Knowledge Vault (`ConfirmedPage`): không xác nhận được (422)."""

    def __init__(self) -> None:
        super().__init__(PAGE_TOO_LARGE_DETAIL)


class DocumentChapterMissingError(Exception):
    """Tài liệu nhập trước khi có `chapter_code` (cột thêm ở migration 0002): không dựng được `DocumentImport`."""

    def __init__(self) -> None:
        super().__init__(CHAPTER_MISSING_DETAIL)


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
class RetryPolicy:
    """Thử lại có giới hạn cho lỗi tạm thời của luồng đọc nền: lần thứ k thất bại chờ k × `delay_s` rồi thử lại.

    Áp dụng khi ghi một trang gặp kho bận (`StorageBusyError`) và khi mở bộ đọc gặp lỗi môi trường
    (`PdfWorkerError`, `OSError`); hết `attempts` lần thì dừng đọc, trang chưa ghi giữ `pending`.
    """

    attempts: int = WRITE_ATTEMPTS
    delay_s: float = WRITE_RETRY_DELAY_S

    def __post_init__(self) -> None:
        if self.attempts < 1 or self.delay_s < 0:
            raise ValueError("RetryPolicy cần attempts ≥ 1 và delay_s ≥ 0")


@dataclass(frozen=True)
class ReadJob:
    """Một tài liệu chờ đọc: kho, bộ đọc và chính sách thử lại đi cùng việc, nên hàng đợi không giữ phụ thuộc nào.

    `vault` và `locks` dùng ở bước kết thúc lần đọc (`read_document`): đọc xong mà mọi trang có văn bản đã được xác
    nhận từ trước thì văn bản vào Vault và tài liệu thành `confirmed` ngay (`settle_document`), dưới khoá của tài
    liệu như các request sửa và xác nhận. `vault = None` (test, script) ⇒ không làm bước này: tài liệu dừng ở
    `review` và thành `confirmed` ở lần gọi xác nhận kế tiếp. `locks = None` ⇒ không lấy khoá (chỉ còn so version).
    """

    document_id: str
    path: Path
    store: DocumentRepository
    reader: PdfReader
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    vault: VaultRepository | None = None
    locks: KeyedLocks | None = None
    # Ngưỡng của 0D (vai trò trang) ở bước kết thúc lần đọc; app truyền `ImportRuntime.reader_config`.
    config: PdfReaderConfig = field(default_factory=PdfReaderConfig)
    # Đồng hồ cho `confirmed_at` của trang tự xác nhận (GĐ1 1B); test truyền đồng hồ cố định.
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))


class ImportQueue(Protocol):
    def submit(self, job: ReadJob) -> None:
        """Xếp `job` vào cuối hàng đợi (FIFO)."""
        ...

    def close(self, timeout_s: float = QUEUE_CLOSE_TIMEOUT_S) -> bool:
        """Ngừng nhận việc; việc đang đọc dừng trước lần ghi trang kế tiếp, trang chưa ghi giữ `pending`.

        Chờ luồng đọc tối đa `timeout_s`; `True` khi không còn luồng nào đang đọc.
        """
        ...

    def is_active(self, document_id: str) -> bool:
        """Tài liệu đang nằm trong hàng đợi hoặc đang được đọc. `False` với tài liệu `processing` mà không luồng nào
        còn giữ việc của nó (ví dụ lần đọc trước dừng vì lỗi tạm thời): tài liệu đó xoá được (§6.5 `DELETE`)."""
        ...


class KeyedLocks:
    """Khoá theo khoá chuỗi (ở đây `document_id`), cấp phát khi cần và bỏ khi không còn ai giữ hay chờ.

    Khoá vào lại được trong cùng luồng (`RLock`): với hàng đợi đồng bộ, việc đọc chạy ngay trong `accept_document`
    (đang giữ khoá của tài liệu) và bước kết thúc lần đọc lấy lại đúng khoá đó (`read_document`).
    """

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._locks: dict[str, tuple[threading.RLock, int]] = {}

    @contextlib.contextmanager
    def hold(self, key: str) -> Iterator[None]:
        with self._guard:
            lock, users = self._locks.get(key, (threading.RLock(), 0))
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
    """Cổng nhận việc của bộ nhập: `close` chặn lượt nhận mới và chờ các lượt đang chạy xong.

    Một lượt nhận bắt đầu ngay sau bước kiểm byte (§6.5 bước 1–2), TRƯỚC khi ghi file tạm và mở thử, và kết thúc
    khi đã xếp hàng đợi (hoặc đã dọn xong). Nhờ vậy `ImportRuntime.close` chỉ đóng hàng đợi và dọn kho file gốc khi
    không request nào đang ghi file tạm, mở thử, tạo bản ghi hay xếp việc.
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
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    # Khoá theo `subject_code` và chỗ giữ chương (§6.5 bước 5, GĐ1 1A; `chapter_reservation`): chọn chương dưới khoá
    # môn rồi nhả khoá TRƯỚC khi lấy khoá tài liệu (`locks`); không lấy khoá môn khi đang giữ khoá tài liệu.
    subject_locks: KeyedLocks = field(default_factory=KeyedLocks)
    chapter_reservations: ChapterReservations = field(default_factory=ChapterReservations)

    def close(self, timeout_s: float | None = None) -> bool:
        """Ngừng nhận tài liệu, dừng hàng đợi rồi dọn kho file gốc; `True` khi mọi luồng đã dừng hẳn.

        "Request" ở đây là mọi lượt nhận đã qua cổng (`AdmissionGate`), kể cả lượt đang ghi file tạm hoặc mở thử;
        request đến sau khi đóng cổng nhận `ImportUnavailableError` (router trả lỗi có `detail`). Hết `timeout_s`
        (mặc định `QUEUE_CLOSE_TIMEOUT_S`) mà luồng đọc nền hoặc một request vẫn chạy thì KHÔNG xoá thư mục file tạm
        dưới chân chúng: ghi cảnh báo, thư mục được dọn ở lần khởi động sau (`original_files.remove_stale_temp_dirs`).
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
    chapter_code: str | None = None  # tuỳ chọn từ 2026-10-31: `None` ⇒ chương tự động (§5.7)
    output_language: OutputLanguage = "vi"
    title: str | None = None
    auto_confirm: bool = True  # field form (§6.5, GĐ1 1B): bật luật `page_clean_v1` cho tài liệu này


@dataclass(frozen=True)
class ImportOutcome:
    """`created = False`: tài liệu cùng nội dung đã được nhập cho cùng môn và cùng `chapter_code` (§6.5 bước 3, trả
    200 thay vì 202). Cùng môn mà khác `chapter_code` thì không có `ImportOutcome`: `_existing_outcome` ném
    `CHAPTER_CONFLICT` (409, duyệt 2026-10-26 chiều)."""

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


def _chapter_code(document: ImportedDocument) -> str:
    if document.chapter_code is None:
        raise DocumentChapterMissingError()
    return document.chapter_code


def _view(document: ImportedDocument, pages: list[PageSummary]) -> DocumentImport:
    """`DocumentImport` kèm số đếm tóm tắt GĐ0 (lấy, bỏ, cần xem) do backend đếm từ `pages`."""
    return DocumentImport(
        meta=document.meta,
        filename=document.filename,
        size_bytes=document.size_bytes,
        output_language=document.output_language,
        chapter_code=_chapter_code(document),
        status=document.status,
        pages=pages,
        pages_kept=count_kept(pages),
        pages_dropped=count_dropped(pages),
        pages_needs_review=count_needs_review(pages),
        chapter_title=document.chapter_title,
        # Bản ghi có `chapter_code` mà thiếu nguồn là bản ghi nhập trước 2026-10-31 (field form bắt buộc) ⇒ "user"
        chapter_source=ChapterSource(code=document.chapter_code_source or "user", title=document.chapter_title_source),
        chapter_flags=list(document.chapter_flags),
        auto_confirm=document.auto_confirm,
        pages_auto_confirmed=count_auto_confirmed(pages),
        pages_kept_unconfirmed=count_kept_unconfirmed(pages),
    )


def document_view(store: DocumentRepository, document: ImportedDocument) -> DocumentImport:
    """`DocumentImport` từ tóm tắt trang của kho (không dựng lại các dòng của mọi trang ở mỗi lần hỏi trạng thái)."""
    return _view(document, store.list_page_summaries(document.meta.doc_id))


def document_summary(store: DocumentRepository, document: ImportedDocument) -> DocumentSummary:
    pages = store.list_page_summaries(document.meta.doc_id)
    return DocumentSummary(
        meta=document.meta,
        status=document.status,
        pages_pending=sum(page.status == "pending" for page in pages),
        pages_needs_review=count_needs_review(pages),
        pages_failed=sum(page.status == "failed" for page in pages),
        pages_confirmed=sum(page.confirmed for page in pages),
        pages_dropped=count_dropped(pages),
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


def _existing_outcome(store: DocumentRepository, existing: ImportedDocument, request: ImportRequest) -> ImportOutcome:
    """§6.5 bước 3, kiểm theo thứ tự: khác môn ⇒ `SUBJECT_CONFLICT`; bản ghi nhập trước khi có `chapter_code` ⇒
    `DocumentChapterMissingError` (§5.7); khác `chapter_code` ⇒ `CHAPTER_CONFLICT`; cùng môn, cùng chương ⇒ 200 với
    tài liệu hiện có. Không ghi gì xuống kho."""
    if existing.meta.subject_code != request.subject_code:
        raise DocumentImportError("SUBJECT_CONFLICT")
    existing_chapter = _chapter_code(existing)
    # Form không có `chapter_code` ⇒ trả bản ghi cũ, không tính lại chương (§5.7, GĐ1 1A)
    if request.chapter_code and existing_chapter != request.chapter_code:
        raise DocumentImportError("CHAPTER_CONFLICT")
    return ImportOutcome(created=False, document=document_view(store, existing))


def _discard_unused_file(store: DocumentRepository, files: OriginalFiles, document_id: str, sha256: str) -> None:
    """Xoá file gốc vừa lưu khi không còn tài liệu nhập nào dùng nó; không xoá được ⇒ cảnh báo, file mồ côi được
    dọn ở lần khởi động sau (`remove_orphan_files`)."""
    try:
        if store.get_imported_document(document_id) is None:
            files.delete(sha256)
    except (StorageError, OSError) as exc:
        _LOGGER.warning("chưa dọn được file gốc của tài liệu %s (%s)", document_id, type(exc).__name__)


def _undo_create(
    store: DocumentRepository, files: OriginalFiles, document_id: str, sha256: str, upload: UploadedDocument | None
) -> None:
    """Huỷ tài liệu vừa tạo nhưng không xếp được vào hàng đợi (chưa trang nào được đọc).

    Chỉ gỡ phần do lần nhập này thêm (`revert_imported_document`): dòng `documents` do upload đề §6.1 tạo trước đó
    (`upload`) được giữ nguyên cùng item trỏ tới nó.
    """
    try:
        store.revert_imported_document(document_id, upload)
    except StorageError as exc:
        _LOGGER.warning(
            "không huỷ được tài liệu %s (%s); tài liệu giữ processing, xoá được khi không còn việc đọc nào",
            document_id,
            type(exc).__name__,
        )
        return
    _discard_unused_file(store, files, document_id, sha256)


def _new_document(
    request: ImportRequest,
    *,
    filename: str,
    document_id: str,
    sha256: str,
    page_count: int,
    now: datetime,
    chapter: InitialChapter,
) -> ImportedDocument:
    """Bản ghi tài liệu mới (`processing`); dựng và validate TRƯỚC khi lưu file gốc, để dữ liệu sai không để lại file."""
    return ImportedDocument(
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
        chapter_code=chapter.chapter_code,
        status="processing",
        chapter_code_source=chapter.code_source,
        chapter_title=chapter.title,
        chapter_title_source=chapter.title_source,
        chapter_flags=list(chapter.flags),
        auto_confirm=request.auto_confirm,
    )


def accept_document(
    request: ImportRequest,
    *,
    store: DocumentRepository,
    runtime: ImportRuntime,
    reader: PdfReader,
    now: datetime,
    vault: VaultRepository | None = None,
) -> ImportOutcome:
    """Nhận một PDF theo đúng thứ tự của §6.5. Chặn tới vài chục giây: gọi ngoài event loop.

    `vault` đi cùng việc đọc (`ReadJob.vault`): router luôn truyền, để đọc xong thì tài liệu đủ điều kiện được nhập
    Vault ngay (xem `read_document`).

    Sau bước kiểm byte (§6.5 bước 1–2), lần nhận đi qua cổng `runtime.gate` cho tới khi xong, nên `runtime.close`
    chờ cả lượt đang ghi file tạm hay mở thử. Lần nhận được ghi nhận dưới khoá của môn (`reserve_chapter`, khoá môn nhả
    ngay sau đó, không bọc bước mở thử). Chương KHÔNG được giữ chỗ từ đầu: chọn chương và tạo bản ghi nằm trong cùng
    đoạn găng của môn, sau khi mở thử xong (`ChapterTurn.settled`), nên kết quả nhập đồng thời tương đương một thứ tự
    tuần tự (§5.7): file lỗi không chiếm chương, nhập lại cùng nội dung trả bản cũ mà không chọn chương.
    Phần còn lại chạy dưới khoá của `document_id` (`runtime.locks`): request thứ hai cùng nội dung chờ request đầu
    xong rồi thấy tài liệu đã có (200, hoặc 409 theo `_existing_outcome`). Tài liệu cùng mọi trang `pending` được tạo
    trong MỘT giao dịch (`create_imported_document`); nếu một tiến trình khác vẫn kịp tạo trước
    (`DocumentExistsError`) thì trả tài liệu đó như bước 3.

    Lỗi:
    - `DocumentImportError` (bước 1–4): không để lại file hay bản ghi nào.
    - `PdfWorkerError` (worker không khởi động được, lỗi môi trường chứ không phải lỗi file): ném nguyên, không để
      lại file hay bản ghi nào; router trả 500 kèm `detail`.
    - `DocumentExistsError`: tiến trình khác vừa tạo rồi xoá tài liệu cùng nội dung giữa hai bước; ném nguyên,
      router trả 500 kèm `detail` (gửi lại là đủ).
    - Lỗi kho khi tạo tài liệu (ví dụ `StorageBusyError`): không có bản ghi nào; file gốc vừa lưu được xoá nếu
      không còn tài liệu nào dùng nó (xoá lỗi thì dọn ở lần khởi động sau). Lỗi được ném nguyên.
    - `ImportUnavailableError`: bộ nhập đang tắt (cổng đã đóng; không ghi gì) hoặc hàng đợi từ chối việc (phần do
      lần nhập này thêm bị gỡ cùng file gốc, nên không kẹt `processing`).
    """
    filename = _check_upload(request, runtime.reader_config.max_bytes)
    sha256 = hashlib.sha256(request.content).hexdigest()
    document_id = f"doc_{sha256[:16]}"
    runtime.gate.enter()
    try:
        with (
            reserve_chapter(
                reservations=runtime.chapter_reservations,
                subject_locks=runtime.subject_locks,
                store=store,
                subject_code=request.subject_code,
                filename=filename,
                chapter_code=request.chapter_code,
                document_id=document_id,
            ) as turn,
            runtime.locks.hold(document_id),
        ):
            existing = store.get_imported_document(document_id)
            if existing is not None:
                return _existing_outcome(store, existing, request)
            upload = store.get_document(document_id)
            staged = runtime.files.stage(request.content)
            try:
                page_count = _probe(reader, staged)
            except BaseException:
                runtime.files.discard(staged)
                raise
            return _store_and_queue(
                request,
                build=lambda chapter: _new_document(
                    request,
                    filename=filename,
                    document_id=document_id,
                    sha256=staged.sha256,
                    page_count=page_count,
                    now=now,
                    chapter=chapter,
                ),
                turn=turn,
                upload=upload,
                staged=staged,
                store=store,
                runtime=runtime,
                reader=reader,
                vault=vault,
            )
    finally:
        runtime.gate.leave()


def _store_and_queue(
    request: ImportRequest,
    *,
    build: Callable[[InitialChapter], ImportedDocument],
    turn: ChapterTurn,
    upload: UploadedDocument | None,
    staged: StagedFile,
    store: DocumentRepository,
    runtime: ImportRuntime,
    reader: PdfReader,
    vault: VaultRepository | None,
) -> ImportOutcome:
    """§6.5 bước 5: lưu file gốc, tạo tài liệu và trang `pending` (một giao dịch), xếp hàng đợi.

    Chọn chương (`turn.settled`) và tạo bản ghi nằm trong cùng đoạn găng của môn; xếp hàng đợi nằm NGOÀI đoạn găng.
    Bản ghi được dựng và validate TRƯỚC khi lưu file gốc, để dữ liệu sai không để lại file.
    """
    document_id = turn.document_id
    with turn.settled() as chapter:
        try:
            document = build(chapter)
        except BaseException:
            runtime.files.discard(staged)
            raise
        try:
            path = runtime.files.commit(staged)
        except BaseException:
            runtime.files.discard(staged)
            raise
        pages = [pending_page(document_id, number) for number in range(1, document.meta.page_count + 1)]
        try:
            stored = store.create_imported_document(document, pages)
        except DocumentExistsError:
            existing = store.get_imported_document(document_id)
            if existing is None:
                _discard_unused_file(store, runtime.files, document_id, staged.sha256)
                raise
            return _existing_outcome(store, existing, request)
        except BaseException:
            _discard_unused_file(store, runtime.files, document_id, staged.sha256)
            raise
    # Dựng response từ dữ liệu vừa ghi, không đọc lại kho: tài liệu đã tạo thì luôn được xếp hàng đợi ngay sau đây.
    view = _view(stored, [PageSummary.from_page(page) for page in pages])
    try:
        runtime.queue.submit(
            ReadJob(
                document_id=document_id,
                path=path,
                store=store,
                reader=reader,
                retry=runtime.retry,
                vault=vault,
                locks=runtime.locks,
                config=runtime.reader_config,
            )
        )
    except Exception as exc:
        _LOGGER.warning("hàng đợi đọc từ chối tài liệu %s (%s); huỷ lần nhập", document_id, type(exc).__name__)
        _undo_create(store, runtime.files, document_id, staged.sha256, upload)
        raise ImportUnavailableError() from exc
    return ImportOutcome(created=True, document=view)


# --------------------------------------------------------------------------- #
# Đọc nền
# --------------------------------------------------------------------------- #


def _never() -> bool:
    return False


def _within_vault_lines(page: PageExtraction) -> PageExtraction:
    """Trang vừa đọc có nhiều hơn `MAX_PAGE_LINES` (giới hạn trang của Vault, `domain.vault`) dòng có văn bản thì
    thành `failed` với `TOO_MANY_LINES` (duyệt 2026-10-28, câu 18 (a)); trang khác giữ nguyên.

    Bộ đọc trả tới `MAX_PAGE_LINES` của `document_readers` (lớn hơn) dòng mỗi trang. Trang như vậy không bao giờ xác
    nhận được (`_fits_vault`), nên nếu giữ lại nó sẽ chặn cả tài liệu (§5.7: mọi trang có văn bản phải xác nhận).
    Thành `failed` thì trang không chặn, các trang còn lại xác nhận và vào Vault như thường. Dòng rỗng hoặc chỉ có
    khoảng trắng không tính, cùng định nghĩa với văn bản xác nhận (`_confirmed_lines`).
    """
    if page.status == "failed" or len(_confirmed_lines(page)) <= MAX_PAGE_LINES:
        return page
    return failed_page(page.document_id, page.page_number, "TOO_MANY_LINES", score=page.text_score).model_copy(
        update={"has_table": page.has_table, "has_formula": page.has_formula, "has_figure": page.has_figure}
    )


def _write_page(store: DocumentRepository, document_id: str, page: PageExtraction) -> None:
    """Ghi một trang vừa đọc vào chỗ trang `pending` tương ứng; `version` của trang khớp version của dòng trong kho.
    Trang vượt giới hạn dòng của Vault được ghi thành `failed` (`_within_vault_lines`)."""
    if page.document_id != document_id:
        return
    stored = store.get_page(document_id, page.page_number, PageExtraction)
    if stored is None or stored.status != "pending":
        return
    read = _within_vault_lines(page).model_copy(
        update={"version": stored.version + 1, "confirmed": False, "confirmed_at": None}
    )
    store.update_page(
        document_id, page.page_number, read, status=read.status, confirmed=False, expected_version=stored.version
    )


def _write_page_retrying(
    store: DocumentRepository,
    document_id: str,
    page: PageExtraction,
    retry: RetryPolicy,
    should_stop: Callable[[], bool],
    pause: Callable[[float], None],
) -> None:
    """`_write_page`, thử lại tối đa `retry.attempts` lần khi kho bận; vẫn bận (hoặc đang dừng) ⇒ ném lỗi."""
    for attempt in range(1, retry.attempts + 1):
        try:
            _write_page(store, document_id, page)
            return
        except StorageBusyError:
            if attempt == retry.attempts or should_stop():
                raise
            _LOGGER.warning(
                "kho bận khi ghi trang %d của tài liệu %s; thử lại (%d/%d)",
                page.page_number,
                document_id,
                attempt,
                retry.attempts,
            )
            pause(retry.delay_s * attempt)


def _fail_pending(store: DocumentRepository, document_id: str, reason: FailureReason) -> None:
    for page in store.list_pages(document_id, PageExtraction):
        if page.status == "pending":
            _write_page(store, document_id, failed_page(document_id, page.page_number, reason))


def _with_text_layer(document: ImportedDocument, pages: list[PageExtraction]) -> DocumentMeta:
    """`meta` với `has_text_layer` tính lại từ các trang: true khi ≥ 1 trang dùng tầng 1 (§5.8)."""
    return document.meta.model_copy(update={"has_text_layer": any(p.tier_used == "text_layer" for p in pages)})


def _write_final_status(store: DocumentRepository, document_id: str, status: Literal["review", "failed"]) -> bool:
    """Ghi trạng thái cuối của lần đọc và `has_text_layer` (true khi ≥ 1 trang dùng tầng 1, §5.8); `True` khi đã ghi.

    Tài liệu đã `confirmed` (một lần xác nhận vừa settle tài liệu) giữ `confirmed`. Version lệch vì lần ghi khác
    chen vào giữa lúc đọc và lúc ghi ⇒ đọc lại tài liệu và trang rồi tính lại từ đầu (không mất `has_text_layer`),
    tối đa `SETTLE_ATTEMPTS` lần; vẫn lệch ⇒ ghi cảnh báo, không ném lỗi (tài liệu còn `processing` được xếp lại
    ở lần khởi động sau, lúc đó không còn trang `pending` nên chỉ chạy lại bước này).
    """
    for attempt in range(1, SETTLE_ATTEMPTS + 1):
        document = store.get_imported_document(document_id)
        if document is None:
            return False
        pages = store.list_pages(document_id, PageExtraction)
        meta = _with_text_layer(document, pages)
        final = "confirmed" if document.status == "confirmed" else status
        try:
            store.update_imported_document(
                document.model_copy(update={"status": final, "meta": meta}), document.version
            )
        except VersionConflictError:
            if attempt == SETTLE_ATTEMPTS:
                _LOGGER.warning(
                    "đã đọc hết trang của tài liệu %s nhưng chưa ghi được trạng thái cuối: version vẫn lệch sau %d "
                    "lần đọc lại; tài liệu giữ trạng thái đang lưu",
                    document_id,
                    SETTLE_ATTEMPTS,
                )
                return False
            _LOGGER.info(
                "tài liệu %s vừa được ghi bởi yêu cầu khác khi đặt trạng thái cuối; đọc lại (%d/%d)",
                document_id,
                attempt,
                SETTLE_ATTEMPTS,
            )
            continue
        return True
    return False


# Lỗi bất ngờ của bước settle sau khi đọc mà vẫn giữ tài liệu `review` (`_settle_after_read`): lỗi dữ liệu và lỗi
# tính toán (`pydantic.ValidationError` và `QuoteMismatchError` là `ValueError`). Lỗi khác (ví dụ `AttributeError`,
# lỗi lập trình) vẫn tới `run_job`.
_SETTLE_DATA_ERRORS: tuple[type[Exception], ...] = (ValueError, TypeError, LookupError, ArithmeticError, RuntimeError)


def _settle_after_read(
    store: DocumentRepository, vault: VaultRepository, document_id: str, status: Literal["review", "failed"]
) -> None:
    """Lần đọc kết thúc (`status` vừa ghi là `review` hoặc `failed`): tài liệu đủ điều kiện (mọi trang có văn bản đã
    xác nhận từ trước) thì nhập Vault và thành `confirmed` (`settle_document`, §5.7 "Chốt trạng thái tài liệu").
    Lỗi ở bước này không làm hỏng lần đọc: tài liệu giữ `status` vừa ghi (các trang đã đọc và đã xác nhận giữ
    nguyên), lần gọi xác nhận kế tiếp settle lại.

    Mọi trang đã được ghi xong trước bước này, nên lỗi dữ liệu của settle không được thoát ra `run_job` (nơi lỗi lạ
    làm tài liệu `failed`, trái §5.7) hay ra bước khởi động (`requeue_pending`). Lỗi đã biết (kho, thiếu
    `chapter_code`, tài liệu vừa bị xoá) ghi cảnh báo. Lỗi bất ngờ thuộc `_SETTLE_DATA_ERRORS` (ví dụ
    `ValidationError` khi văn bản đã xác nhận không dựng được `ConfirmedDocumentText`) ghi lỗi chỉ kèm tên lớp ngoại
    lệ, không kèm thông điệp hay traceback vì chúng có thể chứa văn bản của tài liệu.
    """
    try:
        settle_document(store, vault, document_id)
    except (StorageError, DocumentChapterMissingError, DocumentNotFoundError) as exc:
        _LOGGER.warning(
            "lần đọc tài liệu %s đã kết thúc nhưng chưa hoàn tất xác nhận (%s); tài liệu giữ %s, gọi lại xác nhận "
            "để hoàn tất",
            document_id,
            type(exc).__name__,
            status,
        )
    except _SETTLE_DATA_ERRORS as exc:
        _LOGGER.error(
            "lần đọc tài liệu %s đã kết thúc nhưng bước nhập Vault lỗi bất ngờ (%s); tài liệu giữ %s",
            document_id,
            type(exc).__name__,
            status,
        )


def _write_role(store: DocumentRepository, page: PageExtraction, decision: RoleDecision) -> None:
    """Ghi vai trò của một trang qua `update_page` có so version; lệch version ⇒ đọc lại trang và thử lại tối đa
    `SETTLE_ATTEMPTS` lần (trang đã đổi thành không đổi được thì thôi); hết lượt ⇒ cảnh báo, trang giữ như cũ (không
    bỏ mềm là phía an toàn)."""
    current: PageExtraction | None = page
    for attempt in range(1, SETTLE_ATTEMPTS + 1):
        if current is None or not changeable(current):
            return
        updated = apply_decision(current, decision)
        if updated is None:
            return
        try:
            store.update_page(
                current.document_id,
                current.page_number,
                updated,
                status=updated.status,
                confirmed=False,
                expected_version=current.version,
            )
            return
        except VersionConflictError:
            if attempt == SETTLE_ATTEMPTS:
                break
            current = store.get_page(current.document_id, current.page_number, PageExtraction)
    _LOGGER.warning(
        "chưa ghi được vai trò trang %d của tài liệu %s sau %d lần (version lệch); trang giữ như cũ",
        page.page_number,
        page.document_id,
        SETTLE_ATTEMPTS,
    )


def _roles_allowed(store: DocumentRepository, vault: VaultRepository | None, document_id: str) -> bool:
    """0D chỉ chạy khi tài liệu còn trong kho, chưa `confirmed` và Vault chưa có chunk của nó (`vault = None`: test,
    script ⇒ chỉ kiểm trạng thái tài liệu)."""
    document = store.get_imported_document(document_id)
    if document is None or document.status == "confirmed":
        return False
    return vault is None or not vault.has_document_chunks(document_id)


def assign_page_roles(
    store: DocumentRepository, document_id: str, config: PdfReaderConfig, vault: VaultRepository | None = None
) -> None:
    """GĐ0 0D cho cả tài liệu (người gọi giữ khoá của tài liệu): vai trò trang và bỏ mềm theo `assign_roles`, so với
    trang của các tài liệu khác cùng môn còn trong kho. Trang `pending`, `failed`, đã xác nhận, đã hoàn tác không đổi."""
    if not _roles_allowed(store, vault, document_id):
        return
    document = store.get_imported_document(document_id)
    if document is None:
        return
    pages = store.list_pages(document_id, PageExtraction)
    others: list[PageExtraction] = []
    for other in store.list_imported_documents(document.meta.subject_code):
        if other.meta.doc_id != document_id:
            others.extend(store.list_pages(other.meta.doc_id, PageExtraction))
    decisions = assign_roles(pages, document.meta.page_count, others, config)
    for page in pages:
        decision = decisions.get(page.page_number)
        if decision is not None:
            _write_role(store, page, decision)


def _reassess_previous_covers(
    store: DocumentRepository,
    document_id: str,
    config: PdfReaderConfig,
    locks: KeyedLocks | None,
    vault: VaultRepository | None,
) -> None:
    """Sau khi nhả khoá của tài liệu vừa đọc: đánh giá lại trang bìa của các tài liệu khác cùng môn (`reassess_cover`),
    lấy khoá từng tài liệu một (không giữ hai khoá cùng lúc). Tài liệu đã `confirmed` hoặc đã có chunk không đổi gì."""
    document = store.get_imported_document(document_id)
    if document is None:
        return
    later = store.list_pages(document_id, PageExtraction)
    for other in store.list_imported_documents(document.meta.subject_code):
        other_id = other.meta.doc_id
        if other_id == document_id:
            continue
        with locks.hold(other_id) if locks is not None else contextlib.nullcontext():
            if not _roles_allowed(store, vault, other_id):
                continue
            earlier = store.list_pages(other_id, PageExtraction)
            decisions = reassess_cover(earlier, later, config)
            for page in earlier:
                decision = decisions.get(page.page_number)
                if decision is not None:
                    _write_role(store, page, decision)


def _after_finish(
    store: DocumentRepository,
    document_id: str,
    config: PdfReaderConfig,
    locks: KeyedLocks | None,
    vault: VaultRepository | None,
) -> None:
    """Bước sau khi nhả khoá của lần đọc: đánh giá lại trang bìa của tài liệu trước; lỗi kho chỉ ghi cảnh báo (mọi
    trang của lần đọc đã được ghi xong)."""
    try:
        _reassess_previous_covers(store, document_id, config, locks, vault)
    except StorageError as exc:
        _LOGGER.warning(
            "chưa đánh giá lại được trang bìa của tài liệu nhập trước (sau tài liệu %s): %s",
            document_id,
            type(exc).__name__,
        )


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _is_page_clean(document: ImportedDocument, page: PageExtraction) -> bool:
    """`page_clean_v1` cho trang của tài liệu này; "có chữ" và "vừa giới hạn Vault" dùng đúng định nghĩa của xác nhận
    thường (`_has_text`, `_fits_vault`)."""
    return passes_page_clean(
        page,
        auto_confirm=document.auto_confirm,
        source_kind=document.meta.source_kind,
        has_text=_has_text(page),
        fits_vault=_fits_vault(page),
    )


def _auto_confirm_page(
    store: DocumentRepository, document: ImportedDocument, page: PageExtraction, clock: Callable[[], datetime]
) -> None:
    """Tự xác nhận một trang qua `update_page` có so version; lệch version ⇒ đọc lại trang và đánh giá lại tối đa
    `SETTLE_ATTEMPTS` lần (trang đã đổi thành không đủ sạch thì thôi); hết lượt ⇒ cảnh báo, trang giữ như cũ (chưa
    xác nhận là phía an toàn). Không ghi `PageEditAudit`: không đổi dòng nào (§5.7)."""
    current: PageExtraction | None = page
    for attempt in range(1, SETTLE_ATTEMPTS + 1):
        if current is None or not _is_page_clean(document, current):
            return
        confirmed = PageExtraction.model_validate(
            {
                **current.model_dump(),
                "confirmed": True,
                "confirmed_at": clock(),
                "confirmed_by": "auto",
                "confirm_rule": AUTO_CONFIRM_RULE,
                "version": current.version + 1,
            }
        )
        try:
            store.update_page(
                current.document_id,
                current.page_number,
                confirmed,
                status=confirmed.status,
                confirmed=True,
                expected_version=current.version,
            )
            return
        except VersionConflictError:
            if attempt == SETTLE_ATTEMPTS:
                break
            current = store.get_page(current.document_id, current.page_number, PageExtraction)
    _LOGGER.warning(
        "chưa tự xác nhận được trang %d của tài liệu %s sau %d lần (version lệch); trang giữ như cũ",
        page.page_number,
        page.document_id,
        SETTLE_ATTEMPTS,
    )


def _auto_confirm_pages(
    store: DocumentRepository, vault: VaultRepository | None, document_id: str, clock: Callable[[], datetime]
) -> None:
    """GĐ1 1B cho cả tài liệu (người gọi giữ khoá của tài liệu), SAU 0D: tự xác nhận trang theo `page_clean_v1`.

    Chỉ đặt trạng thái trang, KHÔNG settle: tài liệu không đổi `status`, Vault không nhận chunk nào. Bỏ qua khi tài
    liệu tắt `auto_confirm`, thiếu `chapter_code` (nhập trước migration 0002: không hoàn tác được), đã `confirmed`
    hoặc Vault đã có chunk của nó (`_roles_allowed`)."""
    document = store.get_imported_document(document_id)
    if document is None or not document.auto_confirm or document.chapter_code is None:
        return
    if not _roles_allowed(store, vault, document_id):
        return
    for page in store.list_pages(document_id, PageExtraction):
        if _is_page_clean(document, page):
            _auto_confirm_page(store, document, page, clock)


def _finish(
    store: DocumentRepository,
    document_id: str,
    status: Literal["review", "failed"],
    vault: VaultRepository | None = None,
    config: PdfReaderConfig | None = None,
    clock: Callable[[], datetime] | None = None,
) -> None:
    """Gán vai trò trang (GĐ0 0D, `assign_page_roles`), tự xác nhận trang đủ sạch (GĐ1 1B, `_auto_confirm_pages`) rồi
    đặt trạng thái cuối của lần đọc (`_write_final_status`); có `vault` thì settle luôn tài liệu nếu đủ điều kiện
    (`_settle_after_read`), ở cả đường đọc xong (`review`) lẫn đường `failed` (§5.7 "Chốt trạng thái tài liệu"), để
    tài liệu không kẹt `review` hay `failed` khi người dùng đã xác nhận mọi trang có văn bản trong lúc các trang còn
    lại đang đọc (rồi thành `failed` hoặc trắng). Tài liệu có trang tự xác nhận KHÔNG được settle ở đây: chỉ
    `confirm_document` đóng nó (`settle_document`, `by_user`)."""
    try:
        assign_page_roles(store, document_id, PdfReaderConfig() if config is None else config, vault)
    except StorageError as exc:
        _LOGGER.warning("chưa gán được vai trò trang của tài liệu %s: %s", document_id, type(exc).__name__)
    try:
        _auto_confirm_pages(store, vault, document_id, _utc_now if clock is None else clock)
    except StorageError as exc:
        _LOGGER.warning("chưa tự xác nhận được trang của tài liệu %s: %s", document_id, type(exc).__name__)
    if _write_final_status(store, document_id, status) and vault is not None:
        _settle_after_read(store, vault, document_id, status)


def mark_document_failed(
    store: DocumentRepository,
    document_id: str,
    locks: KeyedLocks | None = None,
    vault: VaultRepository | None = None,
    config: PdfReaderConfig | None = None,
    clock: Callable[[], datetime] | None = None,
) -> None:
    """Tài liệu không đọc tiếp được: trang còn `pending` thành `failed` (`PAGE_ERROR`), tài liệu thành `failed`;
    có `vault` (thường là `ReadJob.vault`) thì sau đó settle như bước kết thúc lần đọc (`_finish`): mọi trang có văn
    bản đã xác nhận ⇒ tài liệu `confirmed` và (khác `past_exam`) được cắt chunk vào Vault (§5.7).

    Chạy dưới khoá của tài liệu (`locks`, thường là `ReadJob.locks`) như bước kết thúc lần đọc và các request sửa,
    xác nhận: lần ghi trạng thái `failed` và bước settle sau nó không chen vào giữa một lần xác nhận đang settle tài
    liệu. `locks = None` (test, script) ⇒ không lấy khoá (chỉ còn so version); `vault = None` ⇒ không settle.
    """
    settings = PdfReaderConfig() if config is None else config
    with locks.hold(document_id) if locks is not None else contextlib.nullcontext():
        _fail_pending(store, document_id, "PAGE_ERROR")
        _finish(store, document_id, "failed", vault, settings, clock)
    _after_finish(store, document_id, settings, locks, vault)


def _close_pages(pages: Iterator[PageExtraction]) -> None:
    """Đóng generator của bộ đọc để worker được dọn ngay (khi dừng giữa chừng)."""
    if isinstance(pages, Generator):
        pages.close()


def _open_pages(
    job: ReadJob, pending: list[int], should_stop: Callable[[], bool], pause: Callable[[float], None]
) -> tuple[Iterator[PageExtraction], PageExtraction | None] | None:
    """Mở bộ đọc và lấy trang đầu; `None` khi không đọc tiếp được ở lần này.

    - Lỗi của chính tài liệu (`PdfInputError`, `ValueError`) hoặc mất file gốc (`FileNotFoundError`) ⇒ tài liệu
      `failed` (§5.7).
    - Lỗi môi trường (`PdfWorkerError`, `OSError` khác) ⇒ thử lại theo `job.retry`; hết lượt (hoặc đang dừng) thì
      tài liệu giữ `processing`, trang giữ `pending` (xếp lại ở lần khởi động sau, hoặc người dùng xoá).
    """
    for attempt in range(1, job.retry.attempts + 1):
        pages: Iterator[PageExtraction] = iter(())
        try:
            pages = iter(job.reader.read_pages(job.path, job.document_id, pending))
            return pages, next(pages, None)
        except (PdfInputError, ValueError, FileNotFoundError):
            _close_pages(pages)
            _LOGGER.warning("không mở lại được tài liệu %s để đọc", job.document_id)
            mark_document_failed(job.store, job.document_id, job.locks, job.vault, job.config, job.clock)
            return None
        except (PdfWorkerError, OSError) as exc:
            _close_pages(pages)
            if attempt == job.retry.attempts or should_stop():
                _LOGGER.warning(
                    "bộ đọc lỗi môi trường ở tài liệu %s (%s); tài liệu giữ processing, trang giữ pending",
                    job.document_id,
                    type(exc).__name__,
                )
                return None
            _LOGGER.warning(
                "bộ đọc lỗi môi trường ở tài liệu %s (%s); thử lại (%d/%d)",
                job.document_id,
                type(exc).__name__,
                attempt,
                job.retry.attempts,
            )
            pause(job.retry.delay_s * attempt)
    return None


def read_document(
    job: ReadJob, should_stop: Callable[[], bool] = _never, pause: Callable[[float], None] = time.sleep
) -> None:
    """Đọc các trang còn `pending` của tài liệu, ghi từng trang khi đọc xong; xong thì tài liệu thành `review`.

    - Bộ đọc không mở lại được tài liệu vì lỗi của chính file (`PdfInputError`, `ValueError`, file gốc mất) ⇒ tài
      liệu `failed` (§5.7). Lỗi môi trường khi mở (`PdfWorkerError`, `OSError` khác) được thử lại theo `job.retry`,
      hết lượt thì tài liệu giữ `processing` (`_open_pages`).
    - `should_stop()` được kiểm trước khi mở bộ đọc, trước và sau mỗi lần ghi trang: đúng ⇒ dừng ngay, không ghi
      thêm; trang chưa ghi giữ `pending`, tài liệu giữ `processing` (được xếp lại khi khởi động, §6.5).
    - Lỗi tạm thời KHÔNG làm tài liệu `failed`: kho bận khi ghi một trang được thử lại có giới hạn
      (`_write_page_retrying`), vẫn bận thì `StorageBusyError` được ném ra cho hàng đợi (tài liệu giữ
      `processing`); bộ đọc ném `PdfInputError`, `RuntimeError` (gồm `PdfWorkerError`), `ValueError` hoặc `OSError`
      sau khi đã trả trang đầu (trái `PdfReader`, ví dụ worker chết bất thường) ⇒ dừng đọc, trang chưa đọc giữ
      `pending` cho lần khởi động sau.
    - Bộ đọc kết thúc bình thường mà còn trang `pending` (trả thiếu trang, trái `PdfReader`) ⇒ các trang đó thành
      `failed` (`PAGE_ERROR`) trước khi tài liệu thành `review`.
    - Trang có nhiều hơn `MAX_PAGE_LINES` dòng có văn bản (giới hạn trang của Vault) được ghi thành `failed`
      (`TOO_MANY_LINES`, `_within_vault_lines`); các trang còn lại xác nhận được như thường.
    - Bước kết thúc chạy dưới khoá của tài liệu (`job.locks`) như các request sửa và xác nhận: đặt `review` rồi, nếu
      mọi trang có văn bản đã được xác nhận trong lúc đọc, nhập Vault và đặt `confirmed` (`_finish`).
    """
    store, document_id = job.store, job.document_id
    pending = [p.page_number for p in store.list_pages(document_id, PageExtraction) if p.status == "pending"]
    if pending:
        if should_stop():
            return
        opened = _open_pages(job, pending, should_stop, pause)
        if opened is None:
            return
        pages, page = opened
        try:
            while page is not None:
                if should_stop():
                    return
                _write_page_retrying(store, document_id, page, job.retry, should_stop, pause)
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
        _fail_pending(store, document_id, "PAGE_ERROR")
    if should_stop():
        return
    with job.locks.hold(document_id) if job.locks is not None else contextlib.nullcontext():
        _finish(store, document_id, "review", job.vault, job.config, job.clock)
    _after_finish(store, document_id, job.config, job.locks, job.vault)


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
            mark_document_failed(job.store, job.document_id, job.locks, job.vault, job.config, job.clock)
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

    def is_active(self, document_id: str) -> bool:
        """Việc đọc chạy xong ngay trong `submit`, nên không tài liệu nào còn nằm trong hàng đợi."""
        return False


class BackgroundImportQueue:
    """Hàng đợi FIFO trong tiến trình backend; một luồng nền đọc một tài liệu mỗi lúc (§6.5)."""

    def __init__(self, run: Callable[[ReadJob, Callable[[], bool]], None] = read_document) -> None:
        self._run = run
        self._jobs: queue.Queue[ReadJob | None] = queue.Queue()
        self._stop = threading.Event()
        self._idle = threading.Condition()
        self._unfinished = 0
        self._active: Counter[str] = Counter()
        self._thread = threading.Thread(target=self._loop, name="holocron-document-import", daemon=True)
        self._thread.start()

    def submit(self, job: ReadJob) -> None:
        if self._stop.is_set():
            raise RuntimeError("hàng đợi đọc tài liệu đã đóng")
        with self._idle:
            self._unfinished += 1
            self._active[job.document_id] += 1
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
                    self._active[job.document_id] -= 1
                    if self._active[job.document_id] <= 0:
                        del self._active[job.document_id]
                    self._idle.notify_all()

    def is_active(self, document_id: str) -> bool:
        with self._idle:
            return self._active[document_id] > 0

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


def requeue_pending(
    store: DocumentRepository,
    files: OriginalFiles,
    reader: PdfReader,
    jobs: ImportQueue,
    retry: RetryPolicy | None = None,
    *,
    vault: VaultRepository | None = None,
    locks: KeyedLocks | None = None,
    config: PdfReaderConfig | None = None,
) -> list[str]:
    """Khi khởi động: xếp lại tài liệu còn `processing` theo thứ tự nhận (cũ trước), từ file gốc đã lưu (§6.5).

    File gốc không còn ⇒ tài liệu `failed`. Trả id các tài liệu đã xếp lại. `vault`, `locks` và `config` (ngưỡng 0D)
    đi cùng việc đọc (`ReadJob`).
    """
    policy = RetryPolicy() if retry is None else retry
    settings = PdfReaderConfig() if config is None else config
    queued: list[str] = []
    for document in reversed(store.list_imported_documents()):
        if document.status != "processing":
            continue
        document_id = document.meta.doc_id
        if not files.exists(document.meta.sha256):
            _LOGGER.warning("mất file gốc của tài liệu %s; đánh dấu failed", document_id)
            mark_document_failed(store, document_id, locks, vault, settings)
            continue
        path = files.path_for(document.meta.sha256)
        jobs.submit(
            ReadJob(
                document_id=document_id,
                path=path,
                store=store,
                reader=reader,
                retry=policy,
                vault=vault,
                locks=locks,
                config=settings,
            )
        )
        queued.append(document_id)
    return queued


def remove_orphan_files(store: DocumentRepository, files: OriginalFiles) -> list[str]:
    """Khi khởi động: xoá file gốc không thuộc tài liệu nhập nào (xoá file lỗi ở lần `DELETE` trước, hoặc tiến trình
    bị ngắt giữa lúc lưu file và lúc tạo bản ghi). Trả sha256 các file đã xoá."""
    return files.remove_orphans({document.meta.sha256 for document in store.list_imported_documents()})


# --------------------------------------------------------------------------- #
# Sửa dòng và xác nhận (§6.5 PATCH …/lines, POST …/confirm)
# --------------------------------------------------------------------------- #


def _has_text(page: PageExtraction) -> bool:
    """Trang có ít nhất một dòng có chữ (không tính dòng rỗng hay chỉ có khoảng trắng, và từ GĐ0 không tính dòng
    header/footer)."""
    return any(line.text.strip() and not line.is_header_footer for line in page.lines)


def _page_key(document_id: str, page_number: int) -> str:
    return f"{document_id}#{page_number}"


def _check_text_open(document: ImportedDocument, vault: VaultRepository) -> None:
    """Văn bản của tài liệu còn được đổi (sửa dòng, xác nhận thêm trang); không ⇒ `PageStateError` (409).

    Đóng khi tài liệu đã `confirmed`, hoặc khi Vault đã có chunk của tài liệu mà tài liệu chưa kịp thành
    `confirmed` (lần settle trước nhập Vault xong nhưng đặt `confirmed` lỗi). Chunk được cắt từ toàn văn bản đã xác
    nhận, nên đổi văn bản lúc đó sẽ làm lần settle sau thêm chunk mới bên cạnh chunk cũ.
    """
    if document.status == "confirmed":
        raise PageStateError("tài liệu đã xác nhận, không đổi văn bản được")
    if vault.has_document_chunks(document.meta.doc_id):
        raise PageStateError(SETTLE_PENDING_DETAIL)


def _check_editable(document: ImportedDocument, page: PageExtraction, vault: VaultRepository) -> None:
    """409 (§6.5): trang còn `pending`, `failed` (không sửa được trong MVP), đã xác nhận, hoặc văn bản tài liệu đã
    đóng (`_check_text_open`)."""
    if page.status == "pending":
        raise PageStateError("trang đang được đọc, chưa sửa được")
    if page.status == "failed":
        raise PageStateError("trang đọc lỗi không sửa được; hãy xoá tài liệu rồi nhập lại file")
    if page.confirmed:
        raise PageStateError("trang đã xác nhận, không sửa được")
    if page.dropped:
        raise PageStateError(DROPPED_EDIT_DETAIL)
    _check_text_open(document, vault)


def _check_version(page: PageExtraction, expected_version: int) -> None:
    if page.version != expected_version:
        key = _page_key(page.document_id, page.page_number)
        raise VersionConflictError("trang", key, expected_version, page.version)


def apply_line_edits(
    page: PageExtraction, patch: PageLinesPatch, *, line_conf_min: float
) -> tuple[PageExtraction, list[PageLineChange]]:
    """Áp `patch` lên trang (hàm thuần, §6.5); trả trang mới (version + 1) và danh sách dòng đổi cho audit.

    - Dòng sửa: văn bản mới (NFC), `edited_by_human = true`, bỏ `flags`, bỏ `TokenMismatch` của dòng đó; `engine` và
      `conf` gốc của engine giữ nguyên (duyệt 2026-10-26 chiều).
    - Dòng gõ thêm: cuối trang, `engine = "human"`, `bbox = null`, `conf = 1.0`, `edited_by_human = true` (duyệt
      2026-10-26 chiều).
    - `cross_check.status`: `mismatch` mà hết token lệch ⇒ `ok`; trạng thái khác giữ nguyên.
    - `review_reasons`: bỏ `TOKEN_MISMATCH` khi hết token lệch; bỏ `LOW_CONF_LINES` khi không còn dòng nào có
      `conf < line_conf_min` (dòng `edited_by_human` không tính); lý do khác giữ nguyên; không thêm lý do nào.
    - `status`: `ok` khi `review_reasons` rỗng, ngược lại `needs_review`.

    `line_index` không tồn tại, hoặc trang vượt `MAX_PAGE_LINES` dòng ⇒ `InvalidPageEditError`.
    """
    edits = {edit.line_index: edit.text for edit in patch.edits}
    missing = sorted(index for index in edits if index >= len(page.lines))
    if missing:
        raise InvalidPageEditError(f"line_index không tồn tại trong trang: {missing}")
    if len(page.lines) + len(patch.appended_lines) > MAX_PAGE_LINES:
        raise InvalidPageEditError(f"trang không được quá {MAX_PAGE_LINES} dòng")
    lines = [
        line.model_copy(update={"text": edits[line.line_index], "edited_by_human": True, "flags": []})
        if line.line_index in edits
        else line
        for line in page.lines
    ]
    lines += [
        ExtractedLine(
            line_index=len(page.lines) + offset, text=text, bbox=None, conf=1.0, engine="human", edited_by_human=True
        )
        for offset, text in enumerate(patch.appended_lines)
    ]
    mismatches = [m for m in page.cross_check.mismatches if m.line_index not in edits]
    cross_status = "ok" if page.cross_check.status == "mismatch" and not mismatches else page.cross_check.status
    reasons: list[ReviewReason] = list(page.review_reasons)
    if not mismatches and "TOKEN_MISMATCH" in reasons:
        reasons.remove("TOKEN_MISMATCH")
    if "LOW_CONF_LINES" in reasons and not any(
        line.conf < line_conf_min and not line.edited_by_human for line in lines
    ):
        reasons.remove("LOW_CONF_LINES")
    # GĐ0: lý do cùng tên với cờ dòng của 0B được bỏ khi không còn dòng nào mang cờ đó (dòng sửa đã bị xoá `flags`).
    for flag in ("LOW_TEXT_CONTRAST", "TEXT_OCCLUDED", "FONT_ENCODING_SUSPECT"):
        if flag in reasons and not any(flag in line.flags for line in lines):
            reasons.remove(flag)
    edited = PageExtraction.model_validate(
        {
            **page.model_dump(),
            "version": page.version + 1,
            "status": "needs_review" if reasons else "ok",
            "lines": [line.model_dump() for line in lines],
            "cross_check": {
                "status": cross_status,
                "secondary_engine": page.cross_check.secondary_engine,
                "mismatches": [m.model_dump() for m in mismatches],
            },
            "review_reasons": reasons,
        }
    )
    changes = [
        PageLineChange(line_index=index, old=page.lines[index].text, new=text) for index, text in sorted(edits.items())
    ]
    changes += [
        PageLineChange(line_index=len(page.lines) + offset, old=None, new=text)
        for offset, text in enumerate(patch.appended_lines)
    ]
    return edited, changes


def edit_page_lines(
    store: DocumentRepository,
    vault: VaultRepository,
    runtime: ImportRuntime,
    document_id: str,
    page_number: int,
    patch: PageLinesPatch,
    *,
    now: datetime,
) -> PageExtraction:
    """`PATCH …/lines` (§6.5): sửa dòng, tính lại cờ và lý do xem lại, ghi `PageEditAudit` cùng giao dịch với trang.

    Lỗi: không có tài liệu/trang ⇒ `DocumentNotFoundError`; trạng thái không cho sửa ⇒ `PageStateError`;
    `expected_version` lệch (kể cả do ghi chen ngang) ⇒ `VersionConflictError`; body sai so với trang ⇒
    `InvalidPageEditError`.
    """
    with runtime.locks.hold(document_id):
        page = get_page_extraction(store, document_id, page_number)
        document = store.get_imported_document(document_id)
        if document is None:
            raise DocumentNotFoundError(document_id)
        _check_editable(document, page, vault)
        _check_version(page, patch.expected_version)
        edited, changes = apply_line_edits(page, patch, line_conf_min=runtime.reader_config.line_conf_min)
        audit = PageEditAudit(
            document_id=document_id,
            page_number=page_number,
            from_version=page.version,
            to_version=edited.version,
            editor_id=patch.editor_id,
            changes=changes,
            at=now,
        )
        store.update_page(
            document_id,
            page_number,
            edited,
            status=edited.status,
            confirmed=False,
            expected_version=page.version,
            audit=audit,
        )
        return edited


def _store_confirmed(store: DocumentRepository, page: PageExtraction, now: datetime) -> PageExtraction:
    """Ghi xác nhận của NGƯỜI DÙNG (`confirmed_by = "user"`, từng trang hay theo lô, §6.5)."""
    user: ConfirmedBy = "user"
    confirmed = page.model_copy(
        update={"confirmed": True, "confirmed_at": now, "confirmed_by": user, "version": page.version + 1}
    )
    store.update_page(
        page.document_id,
        page.page_number,
        confirmed,
        status=confirmed.status,
        confirmed=True,
        expected_version=page.version,
    )
    return confirmed


def _ready_to_confirm(pages: list[PageExtraction]) -> bool:
    """§5.7: hết trang `pending`, có ít nhất một trang đã xác nhận, và mọi trang có văn bản (không `failed`) đã xác
    nhận. Trang `failed`, trang không có chữ và (GĐ0) trang bỏ mềm không xác nhận được nên không chặn."""
    if any(page.status == "pending" for page in pages):
        return False
    text_pages = [page for page in pages if page.status != "failed" and not page.dropped and _has_text(page)]
    return any(page.confirmed for page in pages) and all(page.confirmed for page in text_pages)


def _confirmed_lines(page: PageExtraction) -> list[str]:
    """Các dòng của trang đi vào văn bản xác nhận: bỏ dòng không có văn bản (rỗng hoặc chỉ có khoảng trắng, §6.5) và
    (GĐ0) dòng header/footer, cùng định nghĩa "có văn bản" với `_has_text`."""
    return [line.text for line in page.lines if line.text.strip() and not line.is_header_footer]


def _fits_vault(page: PageExtraction) -> bool:
    """Văn bản xác nhận của trang nằm trong giới hạn của `ConfirmedPage` (`domain.vault`). Bộ đọc trả tới
    `MAX_PAGE_LINES` của `document_readers` (lớn hơn) dòng mỗi trang, nên trang phải được kiểm TRƯỚC khi ghi xác
    nhận; nếu không, bước settle mới ném lỗi validate, sau khi trang đã thành `confirmed`."""
    lines = _confirmed_lines(page)
    return len(lines) <= MAX_PAGE_LINES and all(len(line) <= MAX_LINE_CHARS for line in lines)


def confirmed_text(document: ImportedDocument, pages: list[PageExtraction]) -> ConfirmedDocumentText | None:
    """Văn bản đã xác nhận của tài liệu cho bộ cắt chunk (§12.1); `None` khi không có trang nào để cắt.

    Chỉ trang `confirmed`; dòng có `text` rỗng không vào văn bản xác nhận (§6.5). Nhãn ban đầu (duyệt 2026-10-26):
    `chapter_code` của tài liệu, `category = THEORY`, `topic_keys = []` (mặc định của `ConfirmedDocumentText`).
    """
    confirmed_pages = [
        ConfirmedPage(page_number=page.page_number, lines=_confirmed_lines(page))
        for page in pages
        if page.confirmed and not page.dropped
    ]
    if not confirmed_pages:
        return None
    return ConfirmedDocumentText(
        doc_id=document.meta.doc_id,
        subject_code=document.meta.subject_code,
        chapter_code=_chapter_code(document),
        pages=confirmed_pages,
    )


def _ingest(vault: VaultRepository, document: ImportedDocument, pages: list[PageExtraction]) -> None:
    """Cắt chunk vào Vault (idempotent: chunk đã có giữ nguyên bản đang lưu). Tài liệu `past_exam` không được cắt
    chunk: §6.5 chỉ quy định cắt chunk cho tài liệu có `source_kind` khác `past_exam`."""
    if document.meta.source_kind == SourceKind.PAST_EXAM:
        return
    text = confirmed_text(document, pages)
    if text is not None:
        ingest_confirmed_text(vault, text)


def settle_document(
    store: DocumentRepository, vault: VaultRepository, document_id: str, *, by_user: bool = False
) -> ImportedDocument:
    """Khi đủ điều kiện (`_ready_to_confirm`): cắt chunk vào Vault TRƯỚC, rồi đặt tài liệu `confirmed`.

    Tài liệu có ít nhất một trang `confirmed_by = "auto"` chỉ được chốt khi `by_user = True` (người dùng bấm
    "Chấp nhận", `confirm_document`); ngược lại (cuối lần đọc, đường `failed`, sau `confirm_page`) giữ nguyên, không
    cắt chunk (§5.7 "Chốt trạng thái tài liệu"). Tài liệu đã `confirmed` luôn chạy lại bước cắt idempotent.

    Hai bước không nằm trong một giao dịch (Vault và tài liệu là hai repository). Bù lại:
    - nhập Vault là nguyên tử và idempotent (chunk đã có giữ nguyên bản đang lưu), nên gọi lại chỉ nhập lại đúng
      văn bản đó, không thêm chunk nào;
    - văn bản đóng ngay khi Vault có chunk của tài liệu (`_check_text_open`): sửa dòng hay xác nhận thêm trang bị từ
      chối cho tới khi tài liệu `confirmed`, nên lần gọi lại luôn cắt đúng văn bản đã nhập.

    Nhập Vault lỗi ⇒ ném lỗi, tài liệu chưa thành `confirmed`, Vault không có chunk nào mới. Đặt `confirmed` lệch
    version (luồng đọc nền vừa ghi tài liệu) ⇒ đọc lại và thử tối đa `SETTLE_ATTEMPTS` lần, hết lượt thì ném
    `VersionConflictError`; lỗi khác của bước này ném nguyên. Hai trường hợp sau để lại chunk trong Vault với tài
    liệu chưa `confirmed`; gọi xác nhận lại (hoặc lần đọc nền kết thúc) là hoàn tất.
    """
    for _ in range(SETTLE_ATTEMPTS - 1):
        try:
            return _settle_once(store, vault, document_id, by_user)
        except VersionConflictError:
            continue
    return _settle_once(store, vault, document_id, by_user)


def _settle_once(
    store: DocumentRepository, vault: VaultRepository, document_id: str, by_user: bool
) -> ImportedDocument:
    document = store.get_imported_document(document_id)
    if document is None:
        raise DocumentNotFoundError(document_id)
    pages = store.list_pages(document_id, PageExtraction)
    if document.status != "confirmed":
        if not _ready_to_confirm(pages):
            return document
        if not by_user and any(page.confirmed_by == "auto" for page in pages):
            return document
    _ingest(vault, document, pages)
    if document.status == "confirmed":
        return document
    # `has_text_layer` tính ngay ở đây (§5.8): tài liệu đi thẳng `processing → confirmed` (bước kết thúc lần đọc chưa
    # ghi được vì kho bận) không còn lần ghi trạng thái cuối nào sửa lại nó.
    settled = document.model_copy(update={"status": "confirmed", "meta": _with_text_layer(document, pages)})
    return store.update_imported_document(settled, document.version)


def _settle_confirmed(
    store: DocumentRepository, vault: VaultRepository, document_id: str, *, by_user: bool = False
) -> None:
    """Settle sau khi request đã ghi xác nhận trang. Hết lượt thử vì version lệch thì KHÔNG trả lỗi cho request
    (trang đã xác nhận thật, báo "version lệch" là sai), chỉ ghi cảnh báo: response mang trạng thái đang lưu, tài
    liệu được settle ở lần xác nhận sau hoặc khi lần đọc nền kết thúc. Lỗi khác (ví dụ `StorageBusyError` ⇒ 503
    theo §6.0) ném nguyên: gọi lại xác nhận là idempotent."""
    try:
        settle_document(store, vault, document_id, by_user=by_user)
    except VersionConflictError:
        _LOGGER.warning(
            "đã xác nhận trang của tài liệu %s nhưng chưa đặt được confirmed sau %d lần (version lệch); tài liệu "
            "được hoàn tất ở lần xác nhận sau",
            document_id,
            SETTLE_ATTEMPTS,
        )


def _confirmable_document(store: DocumentRepository, document_id: str) -> ImportedDocument:
    """Tài liệu để xác nhận, kiểm TRƯỚC khi ghi trang nào: không có ⇒ `DocumentNotFoundError`; thiếu `chapter_code`
    (nhập trước migration 0002: không cắt chunk hay dựng response được) ⇒ `DocumentChapterMissingError`."""
    document = store.get_imported_document(document_id)
    if document is None:
        raise DocumentNotFoundError(document_id)
    _chapter_code(document)
    return document


def confirm_page(
    store: DocumentRepository,
    vault: VaultRepository,
    runtime: ImportRuntime,
    document_id: str,
    page_number: int,
    request: PageConfirmRequest,
    *,
    now: datetime,
) -> PageExtraction:
    """`POST …/pages/{n}/confirm` (§6.5): nhận trang `ok` và `needs_review` (người dùng đã xem lại).

    Trang đã xác nhận ⇒ trả nguyên trang (200, không đổi gì). Lỗi, đều kiểm TRƯỚC khi ghi trang:
    `DocumentNotFoundError`; `DocumentChapterMissingError`; `PageStateError` khi trang `pending`, `failed`, không
    có dòng nào có chữ, hoặc văn bản tài liệu đã đóng (`_check_text_open`); `VersionConflictError` khi version của
    trang lệch; `PageTooLargeError` (422) khi trang vượt giới hạn trang của Vault (`_fits_vault`). Sau khi xác nhận, tài liệu đủ điều kiện thì văn bản vào Vault và tài liệu thành `confirmed`
    (`_settle_confirmed`: settle lỗi sau khi đã ghi trang không bị báo thành version lệch).
    """
    with runtime.locks.hold(document_id):
        page = get_page_extraction(store, document_id, page_number)
        document = _confirmable_document(store, document_id)
        if not page.confirmed:
            if page.status == "pending":
                raise PageStateError("trang đang được đọc, chưa xác nhận được")
            if page.status == "failed":
                raise PageStateError("trang đọc lỗi không xác nhận được; hãy xoá tài liệu rồi nhập lại file")
            if page.dropped:
                raise PageStateError(DROPPED_CONFIRM_DETAIL)
            _check_version(page, request.expected_version)
            if not _has_text(page):
                raise PageStateError("trang không có dòng nào có văn bản để xác nhận")
            _check_text_open(document, vault)
            if not _fits_vault(page):
                raise PageTooLargeError()
            page = _store_confirmed(store, page, now)
        _settle_confirmed(store, vault, document_id)
        return page


def confirm_document(
    store: DocumentRepository,
    vault: VaultRepository,
    runtime: ImportRuntime,
    document_id: str,
    request: DocumentConfirmRequest,
    *,
    now: datetime,
) -> DocumentImport:
    """`POST /v1/documents/{id}/confirm` (§6.5): xác nhận mọi trang `ok` chưa xác nhận có văn bản.

    Trang `needs_review` phải xác nhận từng trang; trang `failed` và trang không có chữ không xác nhận được. Trang
    vượt giới hạn trang của Vault (`_fits_vault`) bị bỏ qua kèm cảnh báo trong log (response vẫn cho thấy trang đó
    chưa xác nhận); xác nhận riêng trang đó trả 422 với `detail` nêu giới hạn. Lỗi,
    đều kiểm TRƯỚC khi ghi trang: `DocumentNotFoundError`; `DocumentChapterMissingError`; `PageStateError` khi còn
    trang `pending`, hoặc còn trang để xác nhận mà văn bản tài liệu đã đóng. Gọi lại là idempotent. Settle lỗi sau
    khi đã ghi trang được xử lý như `confirm_page` (`_settle_confirmed`); response là trạng thái đang lưu.
    """
    with runtime.locks.hold(document_id):
        document = _confirmable_document(store, document_id)
        pages = store.list_pages(document_id, PageExtraction)
        if any(page.status == "pending" for page in pages):
            raise PageStateError("tài liệu còn trang đang được đọc, chưa xác nhận được")
        to_confirm: list[PageExtraction] = []
        for page in pages:
            if page.status != "ok" or page.confirmed or page.dropped or not _has_text(page):
                continue
            if _fits_vault(page):
                to_confirm.append(page)
            else:
                _LOGGER.warning(
                    "xác nhận theo lô bỏ qua trang %d của tài liệu %s: vượt giới hạn trang của Knowledge Vault",
                    page.page_number,
                    document_id,
                )
        if to_confirm:
            _check_text_open(document, vault)
        for page in to_confirm:
            _store_confirmed(store, page, now)
        _settle_confirmed(store, vault, document_id, by_user=True)
        return get_document_view(store, document_id)


def restore_page(
    store: DocumentRepository,
    vault: VaultRepository,
    runtime: ImportRuntime,
    document_id: str,
    page_number: int,
    request: PageRestoreRequest,
) -> PageExtraction:
    """`POST …/pages/{n}/restore` (§6.5, GĐ0 0D): hoàn tác bỏ mềm một trang bằng một lời gọi.

    Trang bỏ mềm ⇒ `dropped = false`, `restored = true`, `version + 1`; `page_role`, `role_evidence`,
    `review_reasons`, `status` giữ nguyên; không ghi `PageEditAudit`. Trang không bỏ mềm (kể cả đã hoàn tác) ⇒ trả
    nguyên trang, không kiểm `expected_version`. Lỗi: `DocumentNotFoundError` (tài liệu hay trang);
    `DocumentChapterMissingError`; `VersionConflictError` khi version lệch; `PageStateError` khi văn bản tài liệu đã
    đóng (`confirmed`, hoặc Vault đã có chunk: `_check_text_open`).
    """
    with runtime.locks.hold(document_id):
        page = get_page_extraction(store, document_id, page_number)
        document = _confirmable_document(store, document_id)
        if not page.dropped:
            return page
        _check_version(page, request.expected_version)
        _check_text_open(document, vault)
        restored = PageExtraction.model_validate(
            {**page.model_dump(), "dropped": False, "restored": True, "version": page.version + 1}
        )
        store.update_page(
            document_id, page_number, restored, status=restored.status, confirmed=False, expected_version=page.version
        )
        return restored


def unconfirm_page(
    store: DocumentRepository,
    vault: VaultRepository,
    runtime: ImportRuntime,
    document_id: str,
    page_number: int,
    request: PageUnconfirmRequest,
) -> PageExtraction:
    """`POST …/pages/{n}/unconfirm` (§6.5, GĐ1 1B): hoàn tác tự xác nhận một trang bằng một lời gọi.

    Trang `confirmed_by = "auto"` ⇒ `confirmed = false`, `confirmed_at`, `confirmed_by`, `confirm_rule` về null,
    `auto_confirm_undone = true` (không bao giờ tự xác nhận lại), `version + 1`; dòng, `review_reasons`, `status` giữ
    nguyên; không ghi `PageEditAudit`. Trang chưa xác nhận ⇒ trả nguyên trang, không kiểm `expected_version`. Lỗi:
    `DocumentNotFoundError` (tài liệu hay trang); `DocumentChapterMissingError`; `PageStateError` khi trang do người
    dùng xác nhận (MVP không có bỏ xác nhận của người dùng) hoặc văn bản tài liệu đã đóng (`_check_text_open`);
    `VersionConflictError` khi version lệch.
    """
    with runtime.locks.hold(document_id):
        page = get_page_extraction(store, document_id, page_number)
        document = _confirmable_document(store, document_id)
        if not page.confirmed:
            return page
        if page.confirmed_by != "auto":
            raise PageStateError(USER_CONFIRMED_UNDO_DETAIL)
        _check_version(page, request.expected_version)
        _check_text_open(document, vault)
        reopened = PageExtraction.model_validate(
            {
                **page.model_dump(),
                "confirmed": False,
                "confirmed_at": None,
                "confirmed_by": None,
                "confirm_rule": None,
                "auto_confirm_undone": True,
                "version": page.version + 1,
            }
        )
        store.update_page(
            document_id, page_number, reopened, status=reopened.status, confirmed=False, expected_version=page.version
        )
        return reopened


# --------------------------------------------------------------------------- #
# Xoá (§6.5 DELETE /v1/documents/{id})
# --------------------------------------------------------------------------- #


def delete_imported_document(store: DocumentRepository, runtime: ImportRuntime, document_id: str) -> None:
    """Xoá tài liệu cùng trang, chunk, chỉ mục, cặp khái niệm (kho) và file gốc, dưới khoá của `document_id`.

    Không có ⇒ `DocumentNotFoundError`; còn item tham chiếu ⇒ `DocumentBusyError`. Tài liệu `processing` chỉ bị từ
    chối (`DocumentBusyError`) khi hàng đợi còn giữ việc đọc nó (`ImportQueue.is_active`); tài liệu kẹt `processing`
    mà không còn việc đọc nào (lần đọc trước dừng vì lỗi tạm thời) xoá được, để người dùng nhập lại. Không có việc
    đọc mới nào được xếp cho tài liệu này trong lúc xoá: việc chỉ được xếp khi nhận file, dưới cùng khoá. Kho đã xoá
    mà xoá file gốc lỗi (ví dụ Windows `PermissionError` khi file đang mở) ⇒ chỉ ghi cảnh báo: file mồ côi được dọn ở
    lần khởi động sau (`remove_orphan_files`).
    """
    with runtime.locks.hold(document_id):
        document = store.get_imported_document(document_id)
        if document is None:
            raise DocumentNotFoundError(document_id)
        if document.status == "processing" and runtime.queue.is_active(document_id):
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
