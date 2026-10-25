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

from .ingestion import IngestionItem, UploadedDocument
from .schemas import DocumentMeta, ExamBlueprint, KnowledgeChunk
from .twins import IdempotencyRecord
from .vault import ConceptPair, ConceptPairStatus

IDEMPOTENCY_TTL = timedelta(hours=24)

DocumentStatus = Literal["processing", "review", "confirmed", "failed"]
PageStatus = Literal["pending", "ok", "needs_review", "failed"]
OutputLanguage = Literal["vi", "en"]
SearchIndex = Literal["primary", "no_diacritics"]


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


class VaultSearchUnavailableError(StorageError):
    """SQLite không có FTS5; tầng gọi dùng BM25 tự cài (§5.13, ADR-006 mục 4)."""


class ImportedDocument(BaseModel):
    """Một dòng `documents` của tài liệu nhập qua bộ nhập PDF (§6.5); upload đề (§6.1) chỉ có `UploadedDocument`."""

    meta: DocumentMeta
    filename: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(ge=1)
    output_language: OutputLanguage = "vi"
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


def same_item_state(stored: IngestionItem, expected: IngestionItem) -> bool:
    """Hai item có cùng trạng thái lưu (mọi field trừ `audits`, vốn chỉ đổi cùng version bảng)."""
    return stored.model_dump(exclude={"audits"}) == expected.model_dump(exclude={"audits"})


class BlueprintRepository(Protocol):
    def save_blueprint(self, blueprint: ExamBlueprint, item_ids: Sequence[str] = ()) -> ExamBlueprint:
        """Lưu blueprint kèm item nguồn (item thứ i ứng với slot i + 1, §5.9) để kiểm lại I5 khi sinh twin."""
        ...

    def get_blueprint(self, blueprint_id: str) -> ExamBlueprint | None: ...

    def blueprint_item_ids(self, blueprint_id: str) -> tuple[str, ...]: ...


class TwinBatchRepository(Protocol):
    """Batch twin đã sinh, tra theo `Idempotency-Key` trong 24 h (§6.3)."""

    def get_idempotent(self, blueprint_id: str, key: str, now: datetime) -> IdempotencyRecord | None:
        """Bản ghi còn hiệu lực (`now - stored_at <= IDEMPOTENCY_TTL`) của `(blueprint, key)`; hết hạn ⇒ `None`."""
        ...

    def save_idempotent(self, blueprint_id: str, key: str, record: IdempotencyRecord) -> None:
        """Ghi bản ghi mới cho `(blueprint, key)`; bản ghi cũ cùng khoá không còn tra được nữa."""
        ...


class DocumentRepository(Protocol):
    """Tài liệu nhập qua §6.5 và trang của nó. Trang lưu dạng JSON của model trang do tầng gọi chọn (§5.7)."""

    def save_imported_document(self, document: ImportedDocument) -> ImportedDocument:
        """Tạo mới, hoặc bổ sung thông tin cho bản ghi đã có cùng `document_id` (version tăng 1)."""
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
    ) -> int:
        """So-và-ghi nguyên tử; trả version mới (`expected_version + 1`)."""
        ...

    def get_page[M: BaseModel](self, document_id: str, page_number: int, model: type[M]) -> M | None: ...

    def list_pages[M: BaseModel](self, document_id: str, model: type[M]) -> list[M]:
        """Theo `page_number` tăng dần."""
        ...


class VaultRepository(Protocol):
    """Chunk của Knowledge Vault và chỉ mục FTS5 (§5.8, §5.13). Xếp hạng BM25 không thuộc repository."""

    @property
    def vault_search_available(self) -> bool:
        """`False` khi SQLite thiếu FTS5; app vẫn chạy, tầng gọi dùng BM25 tự cài."""
        ...

    def add_chunk(self, chunk: KnowledgeChunk, search_body: str) -> None:
        """Ghi chunk, `chunk_topics` và hai chỉ mục FTS5 (nếu có) trong cùng giao dịch.

        `search_body` là văn bản đã qua bước 1–3 của ADR-006 mục 5. Tài liệu không có ⇒ `MissingReferenceError`.
        """
        ...

    def get_chunk(self, chunk_id: str) -> KnowledgeChunk | None: ...

    def list_chunks(self, subject_code: str, chapter_code: str | None = None) -> list[KnowledgeChunk]:
        """Sắp theo `(doc_id, page_number, chunk_id)` (§6.6)."""
        ...

    def match_chunk_ids(self, subject_code: str, fts_query: str, index: SearchIndex = "primary") -> list[str]:
        """`chunk_id` khớp biểu thức FTS5 `fts_query` trong môn `subject_code`, sắp theo `chunk_id` (chưa xếp hạng).

        Thiếu FTS5 ⇒ `VaultSearchUnavailableError`.
        """
        ...

    def update_chunk_labels(self, chunk: KnowledgeChunk, expected_version: int) -> KnowledgeChunk:
        """So-và-ghi nguyên tử phần nhãn của chunk (§6.6): `chapter_code`, `category`, `tags`, `topic_keys`,
        `label_status`, `mapping_confidence`.

        Văn bản (`raw_text`, `verbatim_quote`) và các field khác không được ghi. Trả chunk đã lưu với
        `version = expected_version + 1`. Lệch version ⇒ `VersionConflictError`; không có chunk ⇒
        `RecordNotFoundError`.
        """
        ...

    def add_concept_pair(self, pair: ConceptPair) -> ConceptPair:
        """Ghi cặp mới; chunk không có ⇒ `MissingReferenceError`. Kiểm nguyên văn (I3) là việc của tầng gọi."""
        ...

    def get_concept_pair(self, pair_id: str) -> ConceptPair | None: ...

    def list_concept_pairs(
        self, subject_code: str, chapter_code: str | None = None, status: ConceptPairStatus | None = None
    ) -> list[ConceptPair]:
        """Sắp theo `(chapter_code, term, pair_id)` (§6.6)."""
        ...

    def update_concept_pair(self, pair: ConceptPair, expected_version: int) -> ConceptPair:
        """So-và-ghi nguyên tử theo version; trả cặp với `version = expected_version + 1`.

        `chunk_id`, `subject_code`, `chapter_code`, `proposed_by`, `created_at` không đổi. Lệch version ⇒
        `VersionConflictError`; không có cặp ⇒ `RecordNotFoundError`.
        """
        ...


class AppStore(IngestionRepository, BlueprintRepository, TwinBatchRepository, VaultRepository, Protocol):
    """Kho mà các router dùng, cấp qua `Depends(get_store)`."""
