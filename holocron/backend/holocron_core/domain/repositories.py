"""Interface lưu trữ sau `Protocol` (SPECIFICATION §5.13; CLAUDE.md §2.2).

Router và service chỉ phụ thuộc các `Protocol` ở đây; bản in-memory (`infrastructure/memory_store.py`, cho test và
chế độ dev) và bản SQLite (`infrastructure/storage/`) cùng thoả chúng. Lỗi lưu trữ là exception của domain để tầng
trên đổi thành mã HTTP mà không biết kho cụ thể: `VersionConflictError` và `ConcurrentUpdateError` ⇒ 409,
`RecordNotFoundError` ⇒ 404, `MissingReferenceError` ⇒ 422, `RecordInUseError` ⇒ 409, `StorageBusyError` ⇒ 503.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from .documents import CHAPTER_CODE_PATTERN, PageEditAudit, PageSummary
from .ingestion import IngestionItem, UploadedDocument
from .schemas import BlueprintSummary, DocumentMeta, ExamBlueprint, ExamFormat, KnowledgeChunk, Subject
from .twins import IdempotencyRecord, TwinBatch
from .vault import ConceptPair, ConceptPairStatus, VaultEdit

IDEMPOTENCY_TTL = timedelta(hours=24)

DocumentStatus = Literal["processing", "review", "confirmed", "failed"]
PageStatus = Literal["pending", "ok", "needs_review", "failed"]
OutputLanguage = Literal["vi", "en"]
SearchIndex = Literal["primary", "no_diacritics"]
VaultEntity = Literal["chunk", "concept_pair"]


class StorageError(Exception):
    """Gốc của mọi lỗi lưu trữ mà tầng trên cần phân biệt."""


class RecordNotFoundError(StorageError):
    def __init__(self, entity: str, key: str) -> None:
        super().__init__(f"không tìm thấy {entity} {key}")
        self.entity = entity
        self.key = key


class VersionConflictError(StorageError):
    """Ghi có điều kiện `WHERE version = expected` không khớp dòng nào trong khi bản ghi vẫn tồn tại (BE-003)."""

    def __init__(self, entity: str, key: str, expected_version: int, current_version: int) -> None:
        super().__init__(f"version lệch: hiện tại {current_version}")
        self.entity = entity
        self.key = key
        self.expected_version = expected_version
        self.current_version = current_version


class ConcurrentUpdateError(StorageError):
    """Version khớp nhưng trạng thái đang lưu khác trạng thái người ghi đã đọc: một lần ghi khác đã chen vào.

    Sửa stem, gắn non-cloneable và approve không tăng `table_primary.version` (§5.13: `ingestion_items.version` bằng
    `table_primary.version`), nên chỉ so version thì hai lần ghi chạy chéo đều khớp và lần sau ghi đè lần trước.
    """

    def __init__(self, entity: str, key: str) -> None:
        super().__init__(f"{entity} vừa bị thay đổi bởi một yêu cầu khác, hãy tải lại rồi thử lại")
        self.entity = entity
        self.key = key


class StorageBusyError(StorageError):
    """Kho đang bị tiến trình khác giữ khoá ghi quá `busy_timeout`; thử lại sau."""


class MissingReferenceError(StorageError):
    """Bản ghi trỏ tới một bản ghi khác không tồn tại (khoá ngoại)."""


class RecordInUseError(StorageError):
    """Không xoá được vì còn bản ghi khác tham chiếu (`ON DELETE RESTRICT`)."""


class DocumentExistsError(StorageError):
    """Đã có tài liệu nhập qua §6.5 với cùng `document_id`; `create_imported_document` không ghi gì."""

    def __init__(self, document_id: str) -> None:
        super().__init__(f"tài liệu {document_id} đã được nhập")
        self.document_id = document_id


class SubjectExistsError(StorageError):
    """Đã có môn cùng `subject_code` (kể cả môn tạo ngầm); `create_subject` không ghi gì (§6.8: 409)."""

    def __init__(self, subject_code: str) -> None:
        super().__init__(f"môn {subject_code} đã có")
        self.subject_code = subject_code


class VaultSearchUnavailableError(StorageError):
    """SQLite không có FTS5; tầng gọi dùng BM25 tự cài (§5.13, ADR-006 mục 4)."""


class ImportedDocument(BaseModel):
    """Một dòng `documents` của tài liệu nhập qua bộ nhập PDF (§6.5); upload đề (§6.1) chỉ có `UploadedDocument`.

    `chapter_code` là field form bắt buộc của `POST /v1/documents` (§6.5, duyệt 2026-10-26); bộ nhập luôn đặt nó.
    `None` chỉ gặp ở bản ghi tạo trước khi có cột `documents.chapter_code` (migration 0002).
    """

    meta: DocumentMeta
    filename: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(ge=1)
    output_language: OutputLanguage = "vi"
    chapter_code: str | None = Field(default=None, pattern=CHAPTER_CODE_PATTERN)
    status: DocumentStatus
    version: int = Field(default=1, ge=1)


class IngestionRepository(Protocol):
    """Item của Ingestion Gate và tài liệu đề tải lên (§5.4, §6.1–§6.4)."""

    def get_item(self, item_id: str) -> IngestionItem | None: ...

    def save_item(self, item: IngestionItem) -> IngestionItem:
        """Ghi không điều kiện (tạo mới hoặc thay toàn bộ). `MissingReferenceError` khi kho kiểm khoá ngoại tài liệu."""
        ...

    def update_item(
        self, item: IngestionItem, expected_version: int, expected: IngestionItem | None = None
    ) -> IngestionItem:
        """So-và-ghi nguyên tử theo `table_primary.version` (§6.2) và, khi có `expected`, theo toàn bộ trạng thái.

        Bản ghi đang lưu phải có version `expected_version`; version mới là `item.table_primary.version`, bằng
        `expected_version` (sửa không đụng bảng) hoặc `expected_version + 1` (sửa bảng). Lệch ⇒
        `VersionConflictError`; không có bản ghi ⇒ `RecordNotFoundError`. `expected` là item người ghi đã đọc: trạng
        thái đang lưu (trừ `audits`) khác nó ⇒ `ConcurrentUpdateError`. Mọi lần đọc-sửa-ghi PHẢI truyền `expected`,
        vì sửa không đụng bảng giữ nguyên version.
        """
        ...

    def save_document(self, document: UploadedDocument) -> UploadedDocument:
        """Cùng nội dung ⇒ cùng `document_id`: giữ bản ghi đầu tiên (idempotent)."""
        ...

    def get_document(self, document_id: str) -> UploadedDocument | None: ...


def check_page_audit(audit: PageEditAudit, document_id: str, page_number: int, expected_version: int) -> None:
    """`audit` thuộc đúng trang `(document_id, page_number)` và bắt đầu từ `expected_version`; sai ⇒ `ValueError`."""
    if (audit.document_id, audit.page_number) != (document_id, page_number):
        raise ValueError("audit không thuộc trang đang ghi")
    if audit.from_version != expected_version:
        raise ValueError(f"audit phải có from_version = {expected_version}")


def same_item_state(stored: IngestionItem, expected: IngestionItem) -> bool:
    """Hai item có cùng trạng thái lưu (mọi field trừ `audits`, vốn chỉ đổi cùng version bảng)."""
    return stored.model_dump(exclude={"audits"}) == expected.model_dump(exclude={"audits"})


class SubjectRepository(Protocol):
    """Môn học (§5.13 bảng `subjects`, §5.14, §6.8). Bảng khác không có khoá ngoại tới môn."""

    def list_subjects(self) -> list[Subject]:
        """Mọi môn, sắp theo `subject_code`."""
        ...

    def get_subject(self, subject_code: str) -> Subject | None: ...

    def create_subject(self, subject: Subject) -> Subject:
        """Tạo môn với `version = 1`; đã có môn cùng mã ⇒ `SubjectExistsError`, không ghi gì."""
        ...

    def update_subject(
        self,
        subject_code: str,
        expected_version: int,
        *,
        display_name: str | None,
        exam_format: ExamFormat | None,
        now: datetime,
    ) -> Subject:
        """So-và-ghi nguyên tử các field khác `None`, `version` tăng 1, `updated_at = now`.

        Không có môn ⇒ `RecordNotFoundError`; version lệch ⇒ `VersionConflictError`.
        """
        ...


class BlueprintRepository(Protocol):
    def save_blueprint(
        self, blueprint: ExamBlueprint, item_ids: Sequence[str] = (), *, implicit_subject: Subject | None = None
    ) -> ExamBlueprint:
        """Lưu blueprint kèm item nguồn (item thứ i ứng với slot i + 1, §5.9) để kiểm lại I5 khi sinh twin.

        `implicit_subject`: môn tạo ngầm (§5.14) khi `blueprint.subject_code` chưa có môn; được tạo trong CÙNG giao dịch
        với blueprint, nên lưu lỗi thì không có môn nào được tạo. Môn đã có thì giữ nguyên.
        """
        ...

    def get_blueprint(self, blueprint_id: str) -> ExamBlueprint | None: ...

    def blueprint_item_ids(self, blueprint_id: str) -> tuple[str, ...]: ...

    def list_blueprint_summaries(self, subject_code: str | None = None) -> list[BlueprintSummary]:
        """Mới nhất trước (theo `created_at`, hoà thì theo `blueprint_id`); `batch_count` = số batch đã lưu (§6.7)."""
        ...


class TwinBatchRepository(Protocol):
    """Batch twin đã sinh, tra theo `Idempotency-Key` trong 24 h (§6.3)."""

    def get_idempotent(self, blueprint_id: str, key: str, now: datetime) -> IdempotencyRecord | None:
        """Bản ghi còn hiệu lực (`now - stored_at <= IDEMPOTENCY_TTL`) của `(blueprint, key)`; hết hạn ⇒ `None`."""
        ...

    def save_batch(self, batch: TwinBatch, idempotency_key: str | None, body_digest: str) -> TwinBatch:
        """API ghi batch duy nhất của luồng sinh twin (§6.3), nguyên tử; trả batch mà request nhận được.

        Mốc thời gian là `batch.created_at` (cũng là `stored_at` của bản ghi idempotency).
        - `idempotency_key` `None` ⇒ ghi batch với khoá `NULL`.
        - Khoá K chưa có bản ghi, hoặc bản ghi của K đã hết hạn TTL ⇒ dọn khoá K cũ (nếu hết hạn) rồi ghi với K.
        - K đang có bản ghi còn hạn với body khác ⇒ ghi batch mới với khoá `NULL`, KHÔNG đụng bản ghi của K.
        - K đang có bản ghi còn hạn với cùng body ⇒ không ghi gì, trả đúng batch cũ (cùng `batch_id`).
        Blueprint không tồn tại ⇒ `MissingReferenceError` (kho SQLite).
        """
        ...

    def save_idempotent(self, blueprint_id: str, key: str, record: IdempotencyRecord) -> None:
        """Ghi bản ghi mới cho `(blueprint, key)`; bản ghi cũ cùng khoá không còn tra được nữa.

        Đường ghi cấp thấp có từ trước `save_batch` (giữ cho test kho); router không dùng.
        """
        ...


class DocumentRepository(Protocol):
    """Tài liệu nhập qua §6.5 và trang của nó. Trang lưu dạng JSON của model trang do tầng gọi chọn (§5.7); bộ nhập
    dùng `PageExtraction`, và `list_page_summaries` đọc các field tóm tắt của model đó."""

    def get_document(self, document_id: str) -> UploadedDocument | None:
        """Dòng `documents` bất kể ai tạo (upload đề §6.1 hoặc bộ nhập §6.5); như `IngestionRepository`."""
        ...

    def save_imported_document(self, document: ImportedDocument) -> ImportedDocument:
        """Tạo mới, hoặc bổ sung thông tin cho bản ghi đã có cùng `document_id` (version tăng 1)."""
        ...

    def create_imported_document(
        self, document: ImportedDocument, pending_pages: Sequence[BaseModel]
    ) -> ImportedDocument:
        """Tạo tài liệu nhập và MỌI trang của nó (trạng thái `pending`, chưa xác nhận) trong MỘT giao dịch.

        Phần tử thứ i của `pending_pages` là trang số i + 1; số phần tử phải bằng `meta.page_count`, sai ⇒
        `ValueError`. Bản ghi chỉ do upload đề tạo (chưa có `subject_code`) được bổ sung thông tin như
        `save_imported_document`. Đã có tài liệu nhập cùng `document_id` ⇒ `DocumentExistsError`. Lỗi ở bất kỳ bước
        nào (kể cả `StorageBusyError` giữa chừng) ⇒ không ghi gì. Trả bản ghi đã lưu.

        Môn của tài liệu chưa có thì được tạo ngầm trong CÙNG giao dịch (§5.14, `Subject.implicit(subject_code,
        meta.uploaded_at)`); môn đã có giữ nguyên. Lỗi ⇒ không có môn nào được tạo.
        """
        ...

    def get_imported_document(self, document_id: str) -> ImportedDocument | None:
        """`None` khi không có bản ghi, hoặc bản ghi chỉ do upload đề tạo (chưa có `subject_code`)."""
        ...

    def update_imported_document(self, document: ImportedDocument, expected_version: int) -> ImportedDocument:
        """So-và-ghi nguyên tử; trả bản ghi với `version = expected_version + 1`."""
        ...

    def list_imported_documents(self, subject_code: str | None = None) -> list[ImportedDocument]:
        """Mới nhất trước (theo `uploaded_at`, hoà thì theo `document_id`)."""
        ...

    def delete_document(self, document_id: str) -> bool:
        """Xoá tài liệu cùng trang, chunk, chỉ mục và cặp khái niệm; `False` khi không có.

        Còn item tham chiếu ⇒ `RecordInUseError`.
        """
        ...

    def revert_imported_document(self, document_id: str, upload: UploadedDocument | None) -> bool:
        """Gỡ đúng phần mà một lần nhập §6.5 vừa thêm, trong MỘT giao dịch; `False` khi không có tài liệu nhập.

        `upload` là dòng `documents` do upload đề §6.1 tạo TRƯỚC lần nhập này (đọc bằng `get_document` trước khi
        gọi `create_imported_document`), hoặc `None` khi lần nhập tạo dòng mới:
        - `None` ⇒ xoá hẳn như `delete_document` (còn item tham chiếu ⇒ `RecordInUseError`, không xoá gì);
        - có giá trị ⇒ giữ dòng (item của §6.1 vẫn trỏ được tới), trả các cột về đúng `upload`, bỏ mọi thông tin
          của bộ nhập (môn, chương, tiêu đề, trạng thái…) cùng trang, lịch sử sửa trang và chunk; version tăng 1.

        Trong cùng giao dịch, môn của tài liệu bị xoá nếu nó vẫn đúng là bản ghi mà `create_imported_document` của
        lần nhập này tạo ngầm (`Subject.implicit(subject_code, meta.uploaded_at)`, `version = 1`) và không còn tài
        liệu hay blueprint nào cùng `subject_code`: request lỗi thì không để lại môn tạo ngầm (§5.14).
        """
        ...

    def add_page(
        self, document_id: str, page_number: int, page: BaseModel, *, status: PageStatus, confirmed: bool
    ) -> None:
        """Thêm trang mới với version 1; tài liệu không có ⇒ `MissingReferenceError`."""
        ...

    def update_page(
        self,
        document_id: str,
        page_number: int,
        page: BaseModel,
        *,
        status: PageStatus,
        confirmed: bool,
        expected_version: int,
        audit: PageEditAudit | None = None,
    ) -> int:
        """So-và-ghi nguyên tử; trả version mới (`expected_version + 1`).

        `audit` (lần sửa dòng, §6.5) được ghi trong cùng giao dịch với trang; nó phải thuộc đúng trang này và có
        `from_version = expected_version` (`check_page_audit`), sai ⇒ `ValueError`, không ghi gì.
        """
        ...

    def list_page_audits(self, document_id: str, page_number: int) -> list[PageEditAudit]:
        """Lịch sử sửa dòng của trang theo thứ tự ghi; trang không có ⇒ danh sách rỗng."""
        ...

    def get_page[M: BaseModel](self, document_id: str, page_number: int, model: type[M]) -> M | None: ...

    def list_pages[M: BaseModel](self, document_id: str, model: type[M]) -> list[M]:
        """Theo `page_number` tăng dần."""
        ...

    def list_page_summaries(self, document_id: str) -> list[PageSummary]:
        """Tóm tắt từng trang (`DocumentImport.pages`), theo `page_number` tăng dần, KHÔNG dựng lại các dòng.

        Dùng cho các `GET` hỏi trạng thái (client hỏi 1–2 s một lần, §6.5): chỉ đọc cột `status`, `confirmed` và các
        field `tier_used`, `review_reasons` của JSON trang, không validate danh sách dòng của mọi trang.
        """
        ...


class VaultRepository(Protocol):
    """Chunk của Knowledge Vault, chỉ mục FTS5 và cặp khái niệm (§5.8, §5.13). Xếp hạng BM25 không thuộc repository.

    Bất biến repository tự giữ trong mỗi giao dịch ghi, không phụ thuộc tầng gọi:
    - I3 của cặp khái niệm: `definition_quote` là chuỗi con (NFC + gộp khoảng trắng) của `raw_text` của chunk
      (`domain.vault.check_definition_quote`); sai ⇒ `QuoteMismatchError`, không ghi gì. Khi nạp, cặp vi phạm (ví dụ
      DB bị sửa tay) không bao giờ được trả ra.
    - `linked_chunk_ids` theo §12.1 (`domain.vault.link_practice_chunks`) được tính lại cho cả môn khi nhập theo lô và
      khi sửa nhãn. Đây là dữ liệu dẫn xuất nên không tăng `version` của chunk được tính lại.
    - `chapter_code` của cặp khái niệm chép từ chunk: sửa `chapter_code` của chunk thì cặp của nó đổi theo (version
      của cặp tăng 1).
    """

    @property
    def vault_search_available(self) -> bool:
        """`False` khi SQLite thiếu FTS5; app vẫn chạy, tầng gọi dùng BM25 tự cài."""
        ...

    def add_chunk(self, chunk: KnowledgeChunk, search_body: str) -> None:
        """Ghi một chunk, `chunk_topics` và hai chỉ mục FTS5 (nếu có) trong cùng giao dịch; giữ nguyên
        `linked_chunk_ids` của `chunk` (thao tác cấp thấp; nhập tài liệu dùng `add_chunks`).

        `search_body` là token chỉ mục chính (ADR-006 mục 5) nối bằng dấu cách; chỉ mục không dấu nhận chính chuỗi đó
        sau `domain.search_text.strip_diacritics`. Tài liệu không có ⇒ `MissingReferenceError`; trùng `chunk_id` ⇒
        `StorageError`.
        """
        ...

    def add_chunks(self, chunks: Sequence[KnowledgeChunk]) -> list[KnowledgeChunk]:
        """Nhập theo lô, nguyên tử: mọi chunk chưa có được ghi (kèm chỉ mục) trong MỘT giao dịch, rồi tính lại
        `linked_chunk_ids` của các môn liên quan; lỗi ở bất kỳ chunk nào thì không ghi gì.

        Chunk đã có (cùng `chunk_id`) giữ nguyên bản đang lưu. Trả bản đang lưu của từng chunk, đúng thứ tự vào.
        Tài liệu không có ⇒ `MissingReferenceError`.
        """
        ...

    def get_chunk(self, chunk_id: str) -> KnowledgeChunk | None: ...

    def has_document_chunks(self, document_id: str) -> bool:
        """Vault đã có ít nhất một chunk cắt từ tài liệu `document_id` (bộ nhập §6.5 dùng để biết văn bản đã vào
        Vault hay chưa, kể cả khi tài liệu chưa kịp thành `confirmed`)."""
        ...

    def list_chunks(self, subject_code: str, chapter_code: str | None = None) -> list[KnowledgeChunk]:
        """Sắp theo `(doc_id, page_number, chunk_id)` (§6.6)."""
        ...

    def match_chunk_ids(self, subject_code: str, fts_query: str, index: SearchIndex = "primary") -> list[str]:
        """`chunk_id` khớp biểu thức FTS5 `fts_query` trong môn `subject_code`, sắp theo `chunk_id` (chưa xếp hạng).

        Thiếu FTS5 ⇒ `VaultSearchUnavailableError`.
        """
        ...

    def update_chunk_labels(
        self, chunk: KnowledgeChunk, expected_version: int, edit: VaultEdit | None = None
    ) -> KnowledgeChunk:
        """So-và-ghi nguyên tử phần nhãn của chunk (§6.6): `chapter_code`, `category`, `tags`, `topic_keys`,
        `label_status`, `mapping_confidence`; cùng giao dịch: `chapter_code` của cặp khái niệm thuộc chunk,
        `linked_chunk_ids` của cả môn và người sửa (`edit`).

        Văn bản (`raw_text`, `verbatim_quote`) và các field khác không được ghi. Trả chunk đã lưu với
        `version = expected_version + 1`. Lệch version ⇒ `VersionConflictError`; không có chunk ⇒
        `RecordNotFoundError`.
        """
        ...

    def set_chunk_exercise(self, chunk_id: str, is_exercise: bool, expected_version: int) -> KnowledgeChunk:
        """So-và-ghi nguyên tử nhãn `is_exercise` của chunk (LLM gắn trong job đề xuất cặp, §5.8); trả chunk đã lưu
        với `version = expected_version + 1`.

        Chỉ ghi `is_exercise`; nhãn khác, `label_status` và văn bản giữ nguyên. Lệch version ⇒
        `VersionConflictError`; không có chunk ⇒ `RecordNotFoundError`.
        """
        ...

    def add_concept_pair(self, pair: ConceptPair, edit: VaultEdit | None = None) -> ConceptPair:
        """Ghi cặp mới. Chunk không có ⇒ `MissingReferenceError`; trích dẫn không khớp nguyên văn chunk ⇒
        `QuoteMismatchError` (I3, kiểm trong giao dịch ghi)."""
        ...

    def get_concept_pair(self, pair_id: str) -> ConceptPair | None: ...

    def list_concept_pairs(
        self, subject_code: str, chapter_code: str | None = None, status: ConceptPairStatus | None = None
    ) -> list[ConceptPair]:
        """Sắp theo `(chapter_code, term, pair_id)` (§6.6)."""
        ...

    def update_concept_pair(
        self, pair: ConceptPair, expected_version: int, edit: VaultEdit | None = None
    ) -> ConceptPair:
        """So-và-ghi nguyên tử theo version; trả cặp với `version = expected_version + 1`.

        `chunk_id`, `subject_code`, `chapter_code`, `proposed_by`, `created_at` không đổi. Lệch version ⇒
        `VersionConflictError`; không có cặp ⇒ `RecordNotFoundError`; trích dẫn không khớp nguyên văn chunk ⇒
        `QuoteMismatchError` (I3).
        """
        ...

    def get_last_edit(self, entity: VaultEntity, key: str) -> VaultEdit | None:
        """Người sửa và thời điểm của lần sửa gần nhất có `edit`; chưa sửa lần nào hoặc không có bản ghi ⇒ `None`."""
        ...


class AppStore(
    IngestionRepository,
    BlueprintRepository,
    TwinBatchRepository,
    DocumentRepository,
    VaultRepository,
    SubjectRepository,
    Protocol,
):
    """Kho mà các router dùng, cấp qua `Depends(get_store)`."""
