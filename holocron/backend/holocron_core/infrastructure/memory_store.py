"""Kho in-memory cho item, tài liệu, môn học, blueprint, batch twin (kèm khoá idempotency) và Knowledge Vault.

Thoả `domain.repositories.AppStore`; dùng cho test và chế độ dev (không đặt `HOLOCRON_DATA_DIR`). Bản lưu lâu dài
là `infrastructure/storage/` (SQLite, SPECIFICATION §5.13). Bản này không có FTS5 (`vault_search_available = False`),
nên tìm kiếm Vault luôn dùng BM25 tự cài ở tầng service (ADR-006 mục 4).
"""

from __future__ import annotations

import json
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel

from ..domain.documents import PageEditAudit, PageSummary
from ..domain.ingestion import IngestionItem, MediaType, UploadedDocument
from ..domain.repositories import (
    IDEMPOTENCY_TTL,
    ConcurrentUpdateError,
    DocumentExistsError,
    ImportedDocument,
    MissingReferenceError,
    PageStatus,
    RecordInUseError,
    RecordNotFoundError,
    SearchIndex,
    StorageError,
    SubjectExistsError,
    VaultEntity,
    VaultSearchUnavailableError,
    VersionConflictError,
    check_page_audit,
    same_item_state,
)
from ..domain.schemas import BlueprintSummary, ExamBlueprint, ExamFormat, KnowledgeChunk, Subject
from ..domain.twins import IdempotencyRecord, TwinBatch
from ..domain.vault import ConceptPair, ConceptPairStatus, VaultEdit, check_definition_quote, link_practice_chunks

__all__ = ["IDEMPOTENCY_TTL", "IngestionStore"]

IMPORTED_MEDIA_TYPE: MediaType = "application/pdf"  # bộ nhập §6.5 chỉ nhận PDF trong MVP, như bản SQLite


@dataclass(frozen=True)
class _StoredPage:
    """Một dòng `pages`: JSON của model trang (validate lại khi đọc, như bản SQLite), các cột lọc và tóm tắt trang
    (`tier_used`, `review_reasons` đọc từ JSON lúc ghi, để `list_page_summaries` không phải dựng lại các dòng)."""

    extraction_json: str
    status: PageStatus
    confirmed: bool
    version: int
    tier_used: str | None = None
    review_reasons: tuple[str, ...] = ()

    @classmethod
    def of(cls, page: BaseModel, status: PageStatus, confirmed: bool, version: int) -> _StoredPage:
        extraction_json = _dump(page)
        data = json.loads(extraction_json)
        tier_used = data.get("tier_used")
        return cls(
            extraction_json,
            status,
            confirmed,
            version,
            tier_used if isinstance(tier_used, str) else None,
            tuple(str(reason) for reason in data.get("review_reasons", ())),
        )

    def summary(self, page_number: int) -> PageSummary:
        return PageSummary.model_validate(
            {
                "page_number": page_number,
                "status": self.status,
                "tier_used": self.tier_used,
                "review_reasons": list(self.review_reasons),
                "confirmed": self.confirmed,
            }
        )


@dataclass
class _StoredBatch:
    """Một dòng `twin_batches` (kèm twin và ticket); `idempotency_key` thành `None` khi khoá được dọn."""

    batch: TwinBatch
    body_digest: str
    idempotency_key: str | None


def _dump(model: BaseModel) -> str:
    """JSON của model, bỏ computed field (như `sqlite_store._dump`)."""
    return model.model_dump_json(exclude=set(type(model).model_computed_fields))


class IngestionStore:
    """Kho in-memory cho item, tài liệu (tải lên và nhập qua §6.5) và Vault; cấp qua `Depends(get_store)`."""

    def __init__(self) -> None:
        self._items: dict[str, IngestionItem] = {}
        self._documents: dict[str, UploadedDocument] = {}
        self._blueprints: dict[str, ExamBlueprint] = {}
        self._blueprint_items: dict[str, tuple[str, ...]] = {}
        # `twin_batches`: mọi batch đã lưu theo thứ tự ghi; `_idempotency` trỏ `(blueprint, khoá)` tới `batch_id`.
        self._batches: dict[str, _StoredBatch] = {}
        self._idempotency: dict[tuple[str, str], str] = {}
        self._subjects: dict[str, Subject] = {}
        self._chunks: dict[str, KnowledgeChunk] = {}
        self._pairs: dict[str, ConceptPair] = {}
        self._edits: dict[tuple[VaultEntity, str], VaultEdit] = {}
        # DocumentRepository: `_documents` là bảng `documents` (mọi tài liệu); `_imported` chỉ chứa tài liệu đã có
        # thông tin của bộ nhập §6.5; `_document_versions` là cột `version` của `documents`.
        self._imported: dict[str, ImportedDocument] = {}
        self._document_versions: dict[str, int] = {}
        self._pages: dict[tuple[str, int], _StoredPage] = {}
        self._page_audits: dict[tuple[str, int], list[PageEditAudit]] = {}
        self._lock = threading.RLock()

    def get_item(self, item_id: str) -> IngestionItem | None:
        return self._items.get(item_id)

    def save_item(self, item: IngestionItem) -> IngestionItem:
        self._items[item.item_id] = item
        return item

    def update_item(
        self, item: IngestionItem, expected_version: int, expected: IngestionItem | None = None
    ) -> IngestionItem:
        """So-và-ghi dưới khoá: nguyên tử giữa các luồng của cùng tiến trình (router làm việc trên bản sao).

        Như bản SQLite: so version, rồi so toàn bộ trạng thái đang lưu với `expected` (nếu có) trước khi ghi.
        """
        new_version = item.table_primary.version
        if new_version not in (expected_version, expected_version + 1):
            raise ValueError(f"version mới {new_version} không hợp lệ so với version mong đợi {expected_version}")
        with self._lock:
            current = self._items.get(item.item_id)
            if current is None:
                raise RecordNotFoundError("ingestion item", item.item_id)
            if current.table_primary.version != expected_version:
                raise VersionConflictError(
                    "ingestion item", item.item_id, expected_version, current.table_primary.version
                )
            if expected is not None and not same_item_state(current, expected):
                raise ConcurrentUpdateError("ingestion item", item.item_id)
            self._items[item.item_id] = item
        return item

    def save_document(self, document: UploadedDocument) -> UploadedDocument:
        """Cùng nội dung ⇒ cùng `document_id`: giữ bản ghi đầu tiên (idempotent)."""
        with self._lock:
            self._document_versions.setdefault(document.document_id, 1)
            return self._documents.setdefault(document.document_id, document)

    def get_document(self, document_id: str) -> UploadedDocument | None:
        return self._documents.get(document_id)

    def save_blueprint(
        self, blueprint: ExamBlueprint, item_ids: Sequence[str] = (), *, implicit_subject: Subject | None = None
    ) -> ExamBlueprint:
        """Lưu blueprint kèm danh sách item đã được duyệt mà nó được dựng từ (để kiểm lại I5 khi sinh twin); môn tạo
        ngầm (nếu có) được ghi dưới cùng khoá."""
        with self._lock:
            self._blueprints[blueprint.blueprint_id] = blueprint
            self._blueprint_items[blueprint.blueprint_id] = tuple(item_ids)
            self._ensure_subject(implicit_subject)
        return blueprint

    def get_blueprint(self, blueprint_id: str) -> ExamBlueprint | None:
        return self._blueprints.get(blueprint_id)

    def blueprint_item_ids(self, blueprint_id: str) -> tuple[str, ...]:
        return self._blueprint_items.get(blueprint_id, ())

    def list_blueprint_summaries(self, subject_code: str | None = None) -> list[BlueprintSummary]:
        with self._lock:
            counts: dict[str, int] = {}
            for stored in self._batches.values():
                counts[stored.batch.blueprint_id] = counts.get(stored.batch.blueprint_id, 0) + 1
            summaries = [
                BlueprintSummary(
                    blueprint_id=blueprint.blueprint_id,
                    subject_code=blueprint.subject_code,
                    total_questions=blueprint.total_questions,
                    created_at=blueprint.created_at,
                    batch_count=counts.get(blueprint.blueprint_id, 0),
                )
                for blueprint in self._blueprints.values()
                if subject_code is None or blueprint.subject_code == subject_code
            ]
        return sorted(summaries, key=lambda summary: (summary.created_at, summary.blueprint_id), reverse=True)

    # ------------------------------------------------------------------ #
    # TwinBatchRepository (cùng ngữ nghĩa với bản SQLite)
    # ------------------------------------------------------------------ #

    def _live_batch(self, blueprint_id: str, key: str, now: datetime) -> _StoredBatch | None:
        batch_id = self._idempotency.get((blueprint_id, key))
        if batch_id is None:
            return None
        stored = self._batches[batch_id]
        return None if now - stored.batch.created_at > IDEMPOTENCY_TTL else stored

    def _insert_batch(self, batch: TwinBatch, body_digest: str, key: str | None) -> None:
        if key is not None:
            previous = self._idempotency.get((batch.blueprint_id, key))
            if previous is not None:
                self._batches[previous].idempotency_key = None
            self._idempotency[(batch.blueprint_id, key)] = batch.batch_id
        self._batches[batch.batch_id] = _StoredBatch(batch, body_digest, key)

    def get_idempotent(self, blueprint_id: str, key: str, now: datetime) -> IdempotencyRecord | None:
        """Bản ghi còn hiệu lực (< 24 h) của `(blueprint, key)`; hết hạn thì coi như không có."""
        with self._lock:
            stored = self._live_batch(blueprint_id, key, now)
        if stored is None:
            return None
        return IdempotencyRecord(body_digest=stored.body_digest, stored_at=stored.batch.created_at, batch=stored.batch)

    def save_batch(self, batch: TwinBatch, idempotency_key: str | None, body_digest: str) -> TwinBatch:
        key = idempotency_key
        with self._lock:
            if key is not None:
                live = self._live_batch(batch.blueprint_id, key, batch.created_at)
                if live is not None and live.body_digest == body_digest:
                    return live.batch
                if live is not None:
                    key = None  # khác body: request mới, không đụng bản ghi của khoá
            self._insert_batch(batch, body_digest, key)
        return batch

    def save_idempotent(self, blueprint_id: str, key: str, record: IdempotencyRecord) -> None:
        batch = record.batch
        if batch.blueprint_id != blueprint_id:
            batch = batch.model_copy(update={"blueprint_id": blueprint_id})
        with self._lock:
            self._insert_batch(batch, record.body_digest, key)

    # ------------------------------------------------------------------ #
    # SubjectRepository
    # ------------------------------------------------------------------ #

    def _ensure_subject(self, subject: Subject | None) -> None:
        """Tạo môn ngầm khi chưa có; gọi dưới `_lock`, cùng lần ghi với bản ghi chính."""
        if subject is not None:
            self._subjects.setdefault(subject.subject_code, subject.model_copy(update={"version": 1}))

    def _drop_implicit_subject(self, subject: Subject | None) -> None:
        """Gỡ môn tạo ngầm của một lần nhập bị huỷ, nếu nó còn nguyên và không còn tài liệu hay blueprint nào cùng môn."""
        if subject is None or self._subjects.get(subject.subject_code) != subject.model_copy(update={"version": 1}):
            return
        code = subject.subject_code
        in_use = any(document.meta.subject_code == code for document in self._imported.values()) or any(
            blueprint.subject_code == code for blueprint in self._blueprints.values()
        )
        if not in_use:
            del self._subjects[code]

    def list_subjects(self) -> list[Subject]:
        with self._lock:
            return [self._subjects[code].model_copy() for code in sorted(self._subjects)]

    def get_subject(self, subject_code: str) -> Subject | None:
        with self._lock:
            subject = self._subjects.get(subject_code)
            return None if subject is None else subject.model_copy()

    def create_subject(self, subject: Subject) -> Subject:
        stored = subject.model_copy(update={"version": 1})
        with self._lock:
            if subject.subject_code in self._subjects:
                raise SubjectExistsError(subject.subject_code)
            self._subjects[subject.subject_code] = stored
        return stored.model_copy()

    def update_subject(
        self,
        subject_code: str,
        expected_version: int,
        *,
        display_name: str | None,
        exam_format: ExamFormat | None,
        now: datetime,
    ) -> Subject:
        with self._lock:
            current = self._subjects.get(subject_code)
            if current is None:
                raise RecordNotFoundError("môn học", subject_code)
            if current.version != expected_version:
                raise VersionConflictError("môn học", subject_code, expected_version, current.version)
            changes: dict[str, object] = {"version": expected_version + 1, "updated_at": now}
            if display_name is not None:
                changes["display_name"] = display_name
            if exam_format is not None:
                changes["exam_format"] = exam_format
            updated = Subject.model_validate({**current.model_dump(), **changes})
            self._subjects[subject_code] = updated
        return updated.model_copy()

    # ------------------------------------------------------------------ #
    # DocumentRepository (cùng ngữ nghĩa với bản SQLite)
    # ------------------------------------------------------------------ #

    def _put_imported(self, document: ImportedDocument, version: int) -> ImportedDocument:
        """Ghi tài liệu nhập với `version` kèm dòng `documents` tương ứng (`get_document` đọc lại được, như SQLite)."""
        stored = document.model_copy(update={"version": version}, deep=True)
        meta = stored.meta
        self._documents[meta.doc_id] = UploadedDocument(
            document_id=meta.doc_id,
            sha256=meta.sha256,
            filename=stored.filename,
            media_type=IMPORTED_MEDIA_TYPE,
            size_bytes=stored.size_bytes,
            uploaded_at=meta.uploaded_at,
        )
        self._imported[meta.doc_id] = stored
        self._document_versions[meta.doc_id] = version
        return stored.model_copy(deep=True)

    def _check_sha256_unique(self, document: ImportedDocument) -> None:
        """Cột `sha256` UNIQUE của bản SQLite: nội dung này đã thuộc tài liệu khác ⇒ `StorageError`."""
        meta = document.meta
        if any(d.sha256 == meta.sha256 and d.document_id != meta.doc_id for d in self._documents.values()):
            raise StorageError(f"UNIQUE: sha256 {meta.sha256} đã thuộc tài liệu khác")

    def save_imported_document(self, document: ImportedDocument) -> ImportedDocument:
        """Tạo mới (version 1) hoặc bổ sung thông tin cho bản ghi đã có cùng `document_id` (version tăng 1)."""
        with self._lock:
            self._check_sha256_unique(document)
            current = self._document_versions.get(document.meta.doc_id)
            return self._put_imported(document, 1 if current is None else current + 1)

    def create_imported_document(
        self, document: ImportedDocument, pending_pages: Sequence[BaseModel]
    ) -> ImportedDocument:
        """Như bản SQLite: kiểm và dựng toàn bộ dữ liệu (JSON của từng trang) trước, rồi mới ghi dưới khoá, nên lỗi
        ở bất kỳ trang nào ⇒ không ghi gì."""
        document_id = document.meta.doc_id
        if len(pending_pages) != document.meta.page_count:
            raise ValueError("số trang pending phải bằng page_count")
        with self._lock:
            if document_id in self._imported:
                raise DocumentExistsError(document_id)
            self._check_sha256_unique(document)
            rows = {
                (document_id, number): _StoredPage.of(page, "pending", False, 1)
                for number, page in enumerate(pending_pages, start=1)
            }
            current = self._document_versions.get(document_id)
            stored = self._put_imported(document, 1 if current is None else current + 1)
            self._pages.update(rows)
            self._ensure_subject(Subject.implicit(document.meta.subject_code, document.meta.uploaded_at))
            return stored

    def get_imported_document(self, document_id: str) -> ImportedDocument | None:
        with self._lock:
            document = self._imported.get(document_id)
            return None if document is None else document.model_copy(deep=True)

    def update_imported_document(self, document: ImportedDocument, expected_version: int) -> ImportedDocument:
        document_id = document.meta.doc_id
        with self._lock:
            current = self._document_versions.get(document_id)
            if current is None:
                raise RecordNotFoundError("tài liệu", document_id)
            if current != expected_version:
                raise VersionConflictError("tài liệu", document_id, expected_version, current)
            self._put_imported(document, expected_version + 1)
        return document.model_copy(update={"version": expected_version + 1})

    def list_imported_documents(self, subject_code: str | None = None) -> list[ImportedDocument]:
        with self._lock:
            found = [
                document.model_copy(deep=True)
                for document in self._imported.values()
                if subject_code is None or document.meta.subject_code == subject_code
            ]
        return sorted(found, key=lambda d: (d.meta.uploaded_at, d.meta.doc_id), reverse=True)

    def delete_document(self, document_id: str) -> bool:
        """Như bản SQLite: trang, lịch sử sửa trang, chunk, cặp khái niệm của chunk và người sửa đi cùng tài liệu;
        còn item tham chiếu thì không xoá gì. `linked_chunk_ids` của các môn liên quan được tính lại."""
        with self._lock:
            if document_id not in self._documents:
                return False
            if any(item.source_doc_id == document_id for item in self._items.values()):
                raise RecordInUseError(f"tài liệu {document_id} còn được ingestion item tham chiếu")
            self._drop_import_data(document_id)
            del self._documents[document_id]
            self._imported.pop(document_id, None)
            self._document_versions.pop(document_id, None)
        return True

    def revert_imported_document(self, document_id: str, upload: UploadedDocument | None) -> bool:
        """Như bản SQLite: không có `upload` ⇒ xoá hẳn; có ⇒ giữ dòng `documents` của upload đề, bỏ phần của bộ nhập.
        Môn tạo ngầm của lần nhập được gỡ theo `_drop_implicit_subject`."""
        with self._lock:
            imported = self._imported.get(document_id)
            if imported is None:
                return False
            if upload is None:
                self.delete_document(document_id)
            else:
                self._drop_import_data(document_id)
                self._documents[document_id] = upload.model_copy(deep=True)
                del self._imported[document_id]
                self._document_versions[document_id] = self._document_versions.get(document_id, 0) + 1
            self._drop_implicit_subject(Subject.implicit(imported.meta.subject_code, imported.meta.uploaded_at))
        return True

    def _drop_import_data(self, document_id: str) -> None:
        """Bỏ trang, lịch sử sửa trang, chunk, cặp khái niệm và người sửa của tài liệu; tính lại liên kết chunk."""
        chunk_ids = {chunk_id for chunk_id, chunk in self._chunks.items() if chunk.doc_id == document_id}
        subjects = {self._chunks[chunk_id].subject_code for chunk_id in chunk_ids}
        pair_ids = {pair_id for pair_id, pair in self._pairs.items() if pair.chunk_id in chunk_ids}
        for chunk_id in chunk_ids:
            del self._chunks[chunk_id]
            self._edits.pop(("chunk", chunk_id), None)
        for pair_id in pair_ids:
            del self._pairs[pair_id]
            self._edits.pop(("concept_pair", pair_id), None)
        for key in [key for key in self._pages if key[0] == document_id]:
            del self._pages[key]
            self._page_audits.pop(key, None)
        self._relink(subjects)

    def add_page(
        self, document_id: str, page_number: int, page: BaseModel, *, status: PageStatus, confirmed: bool
    ) -> None:
        with self._lock:
            if document_id not in self._documents:
                raise MissingReferenceError(f"không tìm thấy tài liệu nguồn {document_id}")
            if (document_id, page_number) in self._pages:
                raise StorageError(f"UNIQUE: trang {document_id}#{page_number} đã tồn tại")
            self._pages[(document_id, page_number)] = _StoredPage.of(page, status, confirmed, 1)

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
        """So-và-ghi dưới khoá; `audit` được ghi cùng lúc với trang."""
        if audit is not None:
            check_page_audit(audit, document_id, page_number, expected_version)
        key = (document_id, page_number)
        with self._lock:
            current = self._pages.get(key)
            if current is None:
                raise RecordNotFoundError("trang", f"{document_id}#{page_number}")
            if current.version != expected_version:
                raise VersionConflictError("trang", f"{document_id}#{page_number}", expected_version, current.version)
            self._pages[key] = _StoredPage.of(page, status, confirmed, expected_version + 1)
            if audit is not None:
                self._page_audits.setdefault(key, []).append(audit.model_copy(deep=True))
        return expected_version + 1

    def list_page_audits(self, document_id: str, page_number: int) -> list[PageEditAudit]:
        with self._lock:
            return [audit.model_copy(deep=True) for audit in self._page_audits.get((document_id, page_number), [])]

    def get_page[M: BaseModel](self, document_id: str, page_number: int, model: type[M]) -> M | None:
        with self._lock:
            stored = self._pages.get((document_id, page_number))
        return None if stored is None else model.model_validate_json(stored.extraction_json)

    def list_page_summaries(self, document_id: str) -> list[PageSummary]:
        with self._lock:
            rows = sorted((number, page) for (doc, number), page in self._pages.items() if doc == document_id)
        return [page.summary(number) for number, page in rows]

    def list_pages[M: BaseModel](self, document_id: str, model: type[M]) -> list[M]:
        with self._lock:
            rows = sorted(
                ((number, page.extraction_json) for (doc, number), page in self._pages.items() if doc == document_id),
            )
        return [model.model_validate_json(extraction_json) for _, extraction_json in rows]

    # ------------------------------------------------------------------ #
    # VaultRepository
    # ------------------------------------------------------------------ #

    @property
    def vault_search_available(self) -> bool:
        return False

    def _check_new_chunk(self, chunk: KnowledgeChunk, pending: dict[str, KnowledgeChunk]) -> None:
        """Như bản SQLite: không có embedding, tài liệu phải tồn tại, `chunk_id` không trùng."""
        if chunk.embedding_model is not None:
            raise ValueError("kho chưa lưu embedding_model (luôn null trong MVP, ADR-006 mục 4)")
        if chunk.doc_id not in self._documents:
            raise MissingReferenceError(f"không tìm thấy tài liệu nguồn {chunk.doc_id}")
        if chunk.chunk_id in self._chunks or chunk.chunk_id in pending:
            raise StorageError(f"chunk {chunk.chunk_id} đã tồn tại")

    def _relink(self, subject_codes: set[str]) -> None:
        """Tính lại `linked_chunk_ids` (§12.1) của mọi chunk thuộc các môn `subject_codes`; gọi khi đang giữ khoá."""
        for subject_code in sorted(subject_codes):
            members = [chunk for chunk in self._chunks.values() if chunk.subject_code == subject_code]
            for chunk_id, linked in link_practice_chunks(members).items():
                current = self._chunks[chunk_id]
                if current.linked_chunk_ids != linked:
                    self._chunks[chunk_id] = current.model_copy(update={"linked_chunk_ids": linked})

    def add_chunk(self, chunk: KnowledgeChunk, search_body: str) -> None:
        """Như bản SQLite; `search_body` không dùng (không có FTS5)."""
        with self._lock:
            self._check_new_chunk(chunk, {})
            self._chunks[chunk.chunk_id] = chunk.model_copy(deep=True)

    def add_chunks(self, chunks: Sequence[KnowledgeChunk]) -> list[KnowledgeChunk]:
        """Kiểm mọi chunk mới trước khi ghi chunk nào, nên lỗi ở chunk bất kỳ không để lại phần nào."""
        with self._lock:
            pending: dict[str, KnowledgeChunk] = {}
            for chunk in chunks:
                if chunk.chunk_id in self._chunks:
                    continue
                self._check_new_chunk(chunk, pending)
                pending[chunk.chunk_id] = chunk.model_copy(deep=True)
            self._chunks.update(pending)
            self._relink({chunk.subject_code for chunk in pending.values()})
            return [self._chunks[chunk.chunk_id].model_copy(deep=True) for chunk in chunks]

    def get_chunk(self, chunk_id: str) -> KnowledgeChunk | None:
        chunk = self._chunks.get(chunk_id)
        return None if chunk is None else chunk.model_copy(deep=True)

    def has_document_chunks(self, document_id: str) -> bool:
        with self._lock:
            return any(chunk.doc_id == document_id for chunk in self._chunks.values())

    def list_chunks(self, subject_code: str, chapter_code: str | None = None) -> list[KnowledgeChunk]:
        found = [
            chunk.model_copy(deep=True)
            for chunk in self._chunks.values()
            if chunk.subject_code == subject_code and (chapter_code is None or chunk.chapter_code == chapter_code)
        ]
        return sorted(found, key=lambda chunk: (chunk.doc_id, chunk.page_number, chunk.chunk_id))

    def match_chunk_ids(self, subject_code: str, fts_query: str, index: SearchIndex = "primary") -> list[str]:
        raise VaultSearchUnavailableError("kho in-memory không có FTS5; dùng BM25 tự cài (ADR-006 mục 4)")

    def update_chunk_labels(
        self, chunk: KnowledgeChunk, expected_version: int, edit: VaultEdit | None = None
    ) -> KnowledgeChunk:
        """Như bản SQLite: nhãn, `chapter_code` của cặp thuộc chunk, `linked_chunk_ids` của môn và người sửa đổi
        cùng lúc dưới một khoá."""
        with self._lock:
            current = self._chunks.get(chunk.chunk_id)
            if current is None:
                raise RecordNotFoundError("chunk", chunk.chunk_id)
            if current.version != expected_version:
                raise VersionConflictError("chunk", chunk.chunk_id, expected_version, current.version)
            labels = {
                "chapter_code": chunk.chapter_code,
                "category": chunk.category,
                "tags": list(chunk.tags),
                "topic_keys": list(chunk.topic_keys),
                "label_status": chunk.label_status,
                "mapping_confidence": chunk.mapping_confidence,
                "version": expected_version + 1,
            }
            stored = KnowledgeChunk.model_validate({**current.model_dump(), **labels})
            moved = {
                pair_id: pair.model_copy(update={"chapter_code": stored.chapter_code, "version": pair.version + 1})
                for pair_id, pair in self._pairs.items()
                if pair.chunk_id == stored.chunk_id and pair.chapter_code != stored.chapter_code
            }
            self._chunks[stored.chunk_id] = stored
            self._pairs.update(moved)
            self._relink({stored.subject_code})
            if edit is not None:
                self._edits[("chunk", stored.chunk_id)] = edit
            return self._chunks[stored.chunk_id].model_copy(deep=True)

    def add_concept_pair(self, pair: ConceptPair, edit: VaultEdit | None = None) -> ConceptPair:
        """Kiểm I3 dưới khoá, trên văn bản chunk đang lưu, trước khi ghi."""
        with self._lock:
            chunk = self._chunks.get(pair.chunk_id)
            if chunk is None:
                raise MissingReferenceError(f"không tìm thấy chunk {pair.chunk_id}")
            if pair.pair_id in self._pairs:
                raise StorageError(f"cặp khái niệm {pair.pair_id} đã tồn tại")
            check_definition_quote(chunk.chunk_id, chunk.raw_text, pair.definition_quote)
            self._pairs[pair.pair_id] = pair.model_copy(deep=True)
            if edit is not None:
                self._edits[("concept_pair", pair.pair_id)] = edit
        return pair

    def get_concept_pair(self, pair_id: str) -> ConceptPair | None:
        pair = self._pairs.get(pair_id)
        return None if pair is None else pair.model_copy(deep=True)

    def list_concept_pairs(
        self, subject_code: str, chapter_code: str | None = None, status: ConceptPairStatus | None = None
    ) -> list[ConceptPair]:
        found = [
            pair.model_copy(deep=True)
            for pair in self._pairs.values()
            if pair.subject_code == subject_code
            and (chapter_code is None or pair.chapter_code == chapter_code)
            and (status is None or pair.status is status)
        ]
        return sorted(found, key=lambda pair: (pair.chapter_code, pair.term, pair.pair_id))

    def update_concept_pair(
        self, pair: ConceptPair, expected_version: int, edit: VaultEdit | None = None
    ) -> ConceptPair:
        """Kiểm I3 dưới khoá, trên văn bản chunk đang lưu, trước khi ghi."""
        with self._lock:
            current = self._pairs.get(pair.pair_id)
            if current is None:
                raise RecordNotFoundError("cặp khái niệm", pair.pair_id)
            if current.version != expected_version:
                raise VersionConflictError("cặp khái niệm", pair.pair_id, expected_version, current.version)
            changes = {
                "term": pair.term,
                "definition_quote": pair.definition_quote,
                "status": pair.status,
                "mapping_confidence": pair.mapping_confidence,
                "confirmed_at": pair.confirmed_at,
                "version": expected_version + 1,
            }
            stored = ConceptPair.model_validate({**current.model_dump(), **changes})
            chunk = self._chunks.get(stored.chunk_id)
            check_definition_quote(stored.chunk_id, "" if chunk is None else chunk.raw_text, stored.definition_quote)
            self._pairs[stored.pair_id] = stored
            if edit is not None:
                self._edits[("concept_pair", stored.pair_id)] = edit
        return stored.model_copy(deep=True)

    def get_last_edit(self, entity: VaultEntity, key: str) -> VaultEdit | None:
        edit = self._edits.get((entity, key))
        return None if edit is None else edit.model_copy()
