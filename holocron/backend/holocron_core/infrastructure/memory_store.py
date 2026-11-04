"""Kho in-memory cho item, tài liệu, môn học, blueprint, batch twin (kèm khoá idempotency) và Knowledge Vault.

Thoả `domain.repositories.AppStore`; dùng cho test và chế độ dev (không đặt `HOLOCRON_DATA_DIR`). Bản lưu lâu dài
là `infrastructure/storage/` (SQLite, SPECIFICATION §5.13). Bản này không có FTS5 (`vault_search_available = False`),
nên tìm kiếm Vault luôn dùng BM25 tự cài ở tầng service (ADR-006 mục 4).
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel

from ..domain.block_eligibility import is_candidate_block
from ..domain.blocks import Block, CropImage, ExtractionLedger, Section
from ..domain.documents import PageEditAudit, PageGeometry, PageSummary
from ..domain.ingestion import IngestionItem, MediaType, UploadedDocument
from ..domain.practice import (
    PracticeAnswer,
    PracticeAttempt,
    PracticeAttemptPage,
    PracticeAttemptSummary,
    ReviewQuestion,
    ReviewQuestionPage,
    StoredTwin,
)
from ..domain.repositories import (
    IDEMPOTENCY_TTL,
    BlockFlagUpdate,
    ConcurrentUpdateError,
    CropWriteResult,
    DocumentExistsError,
    DocumentLayout,
    ImportedDocument,
    MissingReferenceError,
    PageStatus,
    RecordInUseError,
    RecordNotFoundError,
    SearchIndex,
    StorageError,
    StructureProposalRecord,
    SubjectExistsError,
    VaultEntity,
    VaultSearchUnavailableError,
    VersionConflictError,
    capped,
    check_crop_bytes,
    check_page_audit,
    cropped,
    heading_levels,
    same_item_state,
    with_flags,
    with_root_chapter,
)
from ..domain.schemas import BlueprintSummary, ExamBlueprint, ExamFormat, KnowledgeChunk, Subject
from ..domain.twins import IdempotencyRecord, TwinBatch
from ..domain.vault import ConceptPair, ConceptPairStatus, VaultEdit, check_definition_quote, link_practice_chunks

__all__ = ["IDEMPOTENCY_TTL", "IngestionStore"]

IMPORTED_MEDIA_TYPE: MediaType = "application/pdf"  # bộ nhập §6.5 chỉ nhận PDF trong MVP, như bản SQLite
MEMORY_CROP_MAX_BYTES = 256 * 1024 * 1024  # trần tổng byte ảnh cắt giữ trong một instance (§5.13 "Ảnh cắt")
# Bốn field khối của `ImportedDocument`: chỉ `create_imported_document` (thế hệ) và `save_document_layout` ghi.
_LAYOUT_FIELDS = ("layout_version", "layout_settings_sha256", "blocks_after_close", "import_generation")


@dataclass(frozen=True)
class _StoredPage:
    """Một dòng `pages`: JSON của model trang (validate lại khi đọc, như bản SQLite), các cột lọc và tóm tắt trang
    (`tier_used`, `review_reasons` và từ GĐ0 `page_role`, `dropped`, từ GĐ1 `confirmed_by` đọc từ JSON lúc ghi, để
    `list_page_summaries` không phải dựng lại các dòng; JSON thiếu hai field GĐ0 ⇒ `content`, không bỏ mềm, thiếu
    `confirmed_by` ⇒ null, như bản SQLite)."""

    extraction_json: str
    status: PageStatus
    confirmed: bool
    version: int
    tier_used: str | None = None
    review_reasons: tuple[str, ...] = ()
    page_role: str = "content"
    dropped: bool = False
    confirmed_by: str | None = None

    @classmethod
    def of(cls, page: BaseModel, status: PageStatus, confirmed: bool, version: int) -> _StoredPage:
        extraction_json = _dump(page)
        data = json.loads(extraction_json)
        tier_used = data.get("tier_used")
        page_role = data.get("page_role")
        confirmed_by = data.get("confirmed_by")
        return cls(
            extraction_json,
            status,
            confirmed,
            version,
            tier_used if isinstance(tier_used, str) else None,
            tuple(str(reason) for reason in data.get("review_reasons", ())),
            page_role if isinstance(page_role, str) else "content",
            bool(data.get("dropped", False)),
            confirmed_by if isinstance(confirmed_by, str) else None,
        )

    def summary(self, page_number: int) -> PageSummary:
        return PageSummary.model_validate(
            {
                "page_number": page_number,
                "status": self.status,
                "tier_used": self.tier_used,
                "review_reasons": list(self.review_reasons),
                "confirmed": self.confirmed,
                "page_role": self.page_role,
                "dropped": self.dropped,
                "confirmed_by": self.confirmed_by,
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

    def __init__(self, memory_crop_max_bytes: int = MEMORY_CROP_MAX_BYTES) -> None:
        """`memory_crop_max_bytes`: trần tổng byte ảnh cắt (không có biến môi trường); vượt ⇒ khối `crop_capped`."""
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
        # `practice_attempts` + `practice_answers` (§5.13): lượt theo thứ tự ghi (thứ tự chèn của dict là `seq`).
        self._attempts: dict[str, PracticeAttempt] = {}
        # BlockRepository (bảng của migration khối). `_last_generation` là `import_generations`: KHÔNG bị xoá theo tài
        # liệu, mất khi tắt app. `_eligible` là các `(document_id, block_id)` có `eligible = 1`.
        self._last_generation: dict[str, int] = {}
        self._geometries: dict[tuple[str, int], str] = {}
        self._blocks: dict[tuple[str, str], Block] = {}
        self._eligible: set[tuple[str, str]] = set()
        self._ledgers: dict[tuple[str, int], ExtractionLedger] = {}
        self._sections: dict[str, list[Section]] = {}
        self._proposals: dict[tuple[str, int], StructureProposalRecord] = {}
        self._crop_rows: dict[tuple[str, str], CropImage] = {}
        self._crop_bytes: dict[tuple[str, str], bytes] = {}
        self._memory_crop_max_bytes = memory_crop_max_bytes
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
            self._ensure_subject(implicit_subject)  # trước: lỗi ở đây thì chưa ghi gì (nguyên tử)
            self._blueprints[blueprint.blueprint_id] = blueprint
            self._blueprint_items[blueprint.blueprint_id] = tuple(item_ids)
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
        if batch.batch_id in self._batches:  # cùng ngữ nghĩa khoá chính `twin_batches.batch_id` của SQLite
            raise StorageError(f"UNIQUE: batch_id {batch.batch_id} đã được lưu")
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

    def get_batch_blueprint_id(self, batch_id: str) -> str | None:
        with self._lock:
            stored = self._batches.get(batch_id)
            return None if stored is None else stored.batch.blueprint_id

    def get_twins(self, twin_ids: Sequence[str]) -> dict[str, StoredTwin]:
        """Twin theo id kèm batch, blueprint và môn; twin có blueprint không còn thì coi như không có (như phép nối
        của bản SQLite)."""
        wanted = set(twin_ids)
        found: dict[str, StoredTwin] = {}
        with self._lock:
            for stored in self._batches.values():
                blueprint = self._blueprints.get(stored.batch.blueprint_id)
                if blueprint is None:
                    continue
                for twin in stored.batch.twins:
                    if twin.twin_id in wanted:
                        found[twin.twin_id] = StoredTwin(
                            twin=twin.model_copy(deep=True),
                            batch_id=stored.batch.batch_id,
                            blueprint_id=blueprint.blueprint_id,
                            subject_code=blueprint.subject_code,
                        )
        return found

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
        """Ghi tài liệu nhập với `version` kèm dòng `documents` tương ứng (`get_document` đọc lại được, như SQLite).

        Bốn field khối giữ giá trị đang lưu (chưa có bản ghi ⇒ mặc định), như bản SQLite."""
        current = self._imported.get(document.meta.doc_id)
        layout = {name: getattr(current, name) for name in _LAYOUT_FIELDS} if current is not None else {}
        defaults = {name: ImportedDocument.model_fields[name].default for name in _LAYOUT_FIELDS}
        stored = document.model_copy(update={"version": version, **defaults, **layout}, deep=True)
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
            generation = self._last_generation.get(document_id, 0) + 1
            self._last_generation[document_id] = generation
            stored = stored.model_copy(update={"import_generation": generation})
            self._imported[document_id] = stored.model_copy(deep=True)
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
            stored = self._put_imported(document, expected_version + 1)
        layout = {name: getattr(stored, name) for name in _LAYOUT_FIELDS}
        return document.model_copy(update={"version": expected_version + 1, **layout})

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
            # `_last_generation` giữ nguyên: thế hệ tăng đơn điệu qua xoá rồi nhập lại.
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
            self._geometries.pop(key, None)
            self._ledgers.pop(key, None)
            self._proposals.pop(key, None)
        for block_key in [key for key in self._blocks if key[0] == document_id]:
            del self._blocks[block_key]
            self._eligible.discard(block_key)
        self._sections.pop(document_id, None)
        for crop_key in [key for key in self._crop_rows if key[0] == document_id]:
            del self._crop_rows[crop_key]
        for bytes_key in [key for key in self._crop_bytes if key[0] == document_id]:
            del self._crop_bytes[bytes_key]
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
        geometry: PageGeometry | None = None,
    ) -> int:
        """So-và-ghi dưới khoá; `audit` và `geometry` (None ⇒ giữ hình học đã lưu) được ghi cùng lúc với trang."""
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
            if geometry is not None:
                self._geometries[key] = geometry.model_dump_json()
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

    def set_chunk_exercise(self, chunk_id: str, is_exercise: bool | None, expected_version: int) -> KnowledgeChunk:
        """Như bản SQLite: chỉ đổi `is_exercise` và `version`, dưới khoá."""
        with self._lock:
            current = self._chunks.get(chunk_id)
            if current is None:
                raise RecordNotFoundError("chunk", chunk_id)
            if current.version != expected_version:
                raise VersionConflictError("chunk", chunk_id, expected_version, current.version)
            stored = current.model_copy(update={"is_exercise": is_exercise, "version": expected_version + 1})
            self._chunks[chunk_id] = stored
            return stored.model_copy(deep=True)

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

    # ------------------------------------------------------------------ #
    # PracticeAttemptRepository
    # ------------------------------------------------------------------ #

    def save_practice_attempt(self, attempt: PracticeAttempt) -> PracticeAttempt:
        """Ghi dưới `_lock`; twin không còn ⇒ `MissingReferenceError` và không ghi gì (như khoá ngoại của SQLite)."""
        with self._lock:
            twins = self.get_twins([answer.twin_id for answer in attempt.answers])
            if len(twins) != len(attempt.answers) or (
                attempt.batch_id is not None and attempt.batch_id not in self._batches
            ):
                raise MissingReferenceError(f"lượt {attempt.attempt_id} trỏ tới batch hoặc twin không còn")
            if attempt.attempt_id in self._attempts:
                raise StorageError(f"lượt {attempt.attempt_id} đã có")
            self._attempts[attempt.attempt_id] = attempt.model_copy(deep=True)
        return attempt

    def get_practice_attempt(self, attempt_id: str) -> PracticeAttempt | None:
        with self._lock:
            attempt = self._attempts.get(attempt_id)
            return None if attempt is None else attempt.model_copy(deep=True)

    def list_practice_attempts(self, subject_code: str, offset: int, limit: int) -> PracticeAttemptPage:
        with self._lock:
            attempts = [attempt for attempt in self._attempts.values() if attempt.subject_code == subject_code]
        newest_first = list(reversed(attempts))
        page = newest_first[offset : offset + limit]
        return PracticeAttemptPage(
            total=len(attempts),
            attempts=[build_attempt_summary(attempt) for attempt in page],
        )

    def list_review_questions(self, subject_code: str, offset: int, limit: int) -> ReviewQuestionPage:
        """Duyệt lượt theo thứ tự ghi; câu `correct`/`incorrect` sau ghi đè câu trước của cùng twin (§5.17)."""
        latest: dict[str, tuple[int, PracticeAttempt, PracticeAnswer]] = {}
        with self._lock:
            for seq, attempt in enumerate(self._attempts.values()):
                if attempt.subject_code != subject_code:
                    continue
                for answer in attempt.answers:  # đã sắp theo `ordinal`
                    if answer.outcome != "skipped":
                        latest[answer.twin_id] = (seq, attempt, answer)
            wrong = [entry for entry in latest.values() if entry[2].outcome == "incorrect"]
            wrong.sort(key=lambda entry: (-entry[0], entry[2].ordinal))
            twins = self.get_twins([entry[2].twin_id for entry in wrong])
        # Bản in-memory không xoá batch nên mọi twin còn; phép lọc giữ cùng ngữ nghĩa phép nối `twins` của SQLite.
        questions = [
            ReviewQuestion.model_validate(
                {
                    "twin": twins[answer.twin_id].twin,
                    "batch_id": twins[answer.twin_id].batch_id,
                    "last_attempt_id": attempt.attempt_id,
                    "last_answered_at": attempt.submitted_at,
                    "last_chosen_key": answer.chosen_key,
                }
            )
            for _seq, attempt, answer in wrong
            if answer.twin_id in twins
        ]
        return ReviewQuestionPage(total=len(questions), questions=questions[offset : offset + limit])

    def delete_practice_attempt(self, attempt_id: str) -> bool:
        with self._lock:
            return self._attempts.pop(attempt_id, None) is not None

    def delete_practice_attempts(self, subject_code: str) -> int:
        with self._lock:
            doomed = [key for key, attempt in self._attempts.items() if attempt.subject_code == subject_code]
            for key in doomed:
                del self._attempts[key]
        return len(doomed)

    # ------------------------------------------------------------------ #
    # BlockRepository (cùng ngữ nghĩa với bản SQLite; ảnh cắt là byte trong instance, có trần tổng)
    # ------------------------------------------------------------------ #

    def _require_imported(self, document_id: str) -> ImportedDocument:
        document = self._imported.get(document_id)
        if document is None:
            raise RecordNotFoundError("tài liệu", document_id)
        return document

    def _require_page(self, document_id: str, page_number: int) -> _StoredPage:
        page = self._pages.get((document_id, page_number))
        if page is None:
            raise RecordNotFoundError("trang", f"{document_id}#{page_number}")
        return page

    def _require_block(self, document_id: str, block_id: str) -> Block:
        block = self._blocks.get((document_id, block_id))
        if block is None:
            raise RecordNotFoundError("khối", f"{document_id}#{block_id}")
        return block

    def get_page_geometry(self, document_id: str, page_number: int) -> PageGeometry | None:
        with self._lock:
            raw = self._geometries.get((document_id, page_number))
        return None if raw is None else PageGeometry.model_validate_json(raw)

    def save_document_layout(self, document_id: str, layout: DocumentLayout) -> None:
        """Như bản SQLite: kiểm mọi điều kiện trước, rồi mới ghi dưới khoá (lỗi ⇒ không ghi gì)."""
        with self._lock:
            document = self._require_imported(document_id)
            for rewrite in layout.page_rewrites:
                page = rewrite.page
                if page.document_id != document_id:
                    raise ValueError("trang ghi lại không thuộc tài liệu")
                stored = self._require_page(document_id, page.page_number)
                if stored.version != rewrite.expected_version:
                    raise VersionConflictError(
                        "trang", f"{document_id}#{page.page_number}", rewrite.expected_version, stored.version
                    )
            for page_layout in layout.pages:
                self._require_page(document_id, page_layout.page_number)
                if any(block.document_id != document_id for block in page_layout.blocks) or (
                    page_layout.ledger is not None and page_layout.ledger.document_id != document_id
                ):
                    raise ValueError("khối hoặc ledger không thuộc tài liệu")
            for rewrite in layout.page_rewrites:
                page = rewrite.page
                self._pages[(document_id, page.page_number)] = _StoredPage.of(
                    page, page.status, page.confirmed, rewrite.expected_version + 1
                )
            for page_layout in layout.pages:
                number = page_layout.page_number
                for key in [k for k, b in self._blocks.items() if k[0] == document_id and b.page_number == number]:
                    del self._blocks[key]
                    self._eligible.discard(key)
                for block in page_layout.blocks:
                    self._blocks[(document_id, block.block_id)] = block.model_copy(deep=True)
                if page_layout.ledger is None:
                    self._ledgers.pop((document_id, number), None)
                else:
                    self._ledgers[(document_id, number)] = page_layout.ledger.model_copy(deep=True)
            changes: dict[str, object] = {
                "layout_version": layout.layout_version,
                "layout_settings_sha256": layout.layout_settings_sha256,
            }
            if layout.blocks_after_close is not None:
                changes["blocks_after_close"] = layout.blocks_after_close
            self._imported[document_id] = document.model_copy(update=changes, deep=True)
            stale = [key for key in self._crop_rows if key[0] == document_id and key not in self._blocks]
            refs = {self._crop_rows[key].crop_ref for key in stale}
            for key in stale:
                del self._crop_rows[key]
            self._release_crop_bytes(document_id, refs)

    def _release_crop_bytes(self, document_id: str, crop_refs: set[str]) -> None:
        """Bỏ byte của các `crop_ref` không còn hàng ảnh cắt nào của tài liệu; gọi khi đang giữ khoá."""
        used = {row.crop_ref for key, row in self._crop_rows.items() if key[0] == document_id}
        for ref in crop_refs - used:
            self._crop_bytes.pop((document_id, ref), None)

    def _sorted_blocks(self, document_id: str, keep: Callable[[tuple[str, str], Block], bool]) -> list[Block]:
        with self._lock:
            found = [b.model_copy(deep=True) for k, b in self._blocks.items() if k[0] == document_id and keep(k, b)]
        return sorted(found, key=lambda b: (b.page_number, b.reading_order, b.block_id))

    def list_blocks(self, document_id: str, page_number: int | None = None) -> list[Block]:
        return self._sorted_blocks(document_id, lambda _, b: page_number is None or b.page_number == page_number)

    def list_extraction_ledgers(self, document_id: str) -> list[ExtractionLedger]:
        with self._lock:
            found = [(k[1], ledger.model_copy(deep=True)) for k, ledger in self._ledgers.items() if k[0] == document_id]
        return [ledger for _, ledger in sorted(found, key=lambda item: item[0])]

    def replace_sections(self, document_id: str, sections: Sequence[Section]) -> None:
        if any(section.document_id != document_id for section in sections):
            raise ValueError("Section không thuộc tài liệu")
        if len({section.section_id for section in sections}) != len(sections):
            raise ValueError("section_id trùng")
        with self._lock:
            self._require_imported(document_id)
            levels = heading_levels(sections)
            updated: list[Block] = []
            for block_id, level in levels:
                block = self._require_block(document_id, block_id)
                if block.type != "heading":
                    raise ValueError(f"khối tiêu đề {block_id} không phải heading")
                updated.append(block.model_copy(update={"level": level}))
            self._sections[document_id] = [section.model_copy(deep=True) for section in sections]
            for block in updated:
                self._blocks[(document_id, block.block_id)] = block

    def list_sections(self, document_id: str) -> list[Section]:
        with self._lock:
            found = [section.model_copy(deep=True) for section in self._sections.get(document_id, [])]
        return sorted(found, key=lambda section: (section.ordinal, section.section_id))

    def update_imported_document_and_root_section(
        self, document: ImportedDocument, expected_version: int
    ) -> ImportedDocument:
        document_id = document.meta.doc_id
        with self._lock:
            sections = self._sections.get(document_id, [])
            roots = [index for index, section in enumerate(sections) if section.parent_id is None]
            root = None
            if roots:
                root = with_root_chapter(sections[roots[0]], document.chapter_code, document.chapter_title)
            updated = self.update_imported_document(document, expected_version)
            if root is not None:
                sections[roots[0]] = root
        return updated

    def update_block_flags(self, document_id: str, updates: Sequence[BlockFlagUpdate]) -> list[Block]:
        with self._lock:
            written = [with_flags(self._require_block(document_id, update.block_id), update) for update in updates]
            for block in written:
                self._blocks[(document_id, block.block_id)] = block
        return [block.model_copy(deep=True) for block in written]

    def save_structure_proposal(self, record: StructureProposalRecord) -> None:
        with self._lock:
            self._require_page(record.document_id, record.page_number)
            self._proposals[(record.document_id, record.page_number)] = record.model_copy(deep=True)

    def list_structure_proposals(self, document_id: str) -> list[StructureProposalRecord]:
        with self._lock:
            found = [(k[1], r.model_copy(deep=True)) for k, r in self._proposals.items() if k[0] == document_id]
        return [record for _, record in sorted(found, key=lambda item: item[0])]

    def refresh_eligible_blocks(self, document_id: str) -> int:
        with self._lock:
            source_kind = self._require_imported(document_id).meta.source_kind
            pages = {page.page_number: page for page in self.list_page_summaries(document_id)}
            for key, block in self._blocks.items():
                if key[0] != document_id:
                    continue
                if is_candidate_block(block, pages[block.page_number], source_kind):
                    self._eligible.add(key)
                else:
                    self._eligible.discard(key)
            return sum(1 for key in self._eligible if key[0] == document_id)

    def list_eligible_blocks(self, document_id: str) -> list[Block]:
        return self._sorted_blocks(document_id, lambda key, _: key in self._eligible)

    def save_crop_image(self, crop: CropImage, png: bytes) -> CropWriteResult:
        """Byte giữ trong instance; tổng byte (mỗi `(document_id, crop_ref)` tính một lần) vượt
        `memory_crop_max_bytes` ⇒ không ghi, khối giữ `crop_pending` + thêm `crop_capped`."""
        check_crop_bytes(crop, png)
        document_id = crop.document_id
        key = (document_id, crop.block_id)
        with self._lock:
            block = self._require_block(document_id, crop.block_id)
            if block.page_number != crop.page_number:
                raise ValueError("ảnh cắt khác trang của khối")
            file_key = (document_id, crop.crop_ref)
            extra = 0 if file_key in self._crop_bytes else len(png)
            if sum(len(data) for data in self._crop_bytes.values()) + extra > self._memory_crop_max_bytes:
                self._blocks[key] = capped(block)
                return "capped"
            stored = cropped(block, crop.crop_ref)
            previous = self._crop_rows.get(key)
            self._crop_bytes.setdefault(file_key, bytes(png))
            self._crop_rows[key] = crop.model_copy(deep=True)
            self._blocks[key] = stored
            if previous is not None and previous.crop_ref != crop.crop_ref:
                self._release_crop_bytes(document_id, {previous.crop_ref})
        return "stored"

    def list_crop_images(self, document_id: str) -> list[CropImage]:
        with self._lock:
            found = [row.model_copy(deep=True) for key, row in self._crop_rows.items() if key[0] == document_id]
        return sorted(found, key=lambda row: row.block_id)

    def get_crop_bytes(self, document_id: str, crop_ref: str) -> bytes | None:
        with self._lock:
            if not any(k[0] == document_id and row.crop_ref == crop_ref for k, row in self._crop_rows.items()):
                return None
            return self._crop_bytes.get((document_id, crop_ref))


def build_attempt_summary(attempt: PracticeAttempt) -> PracticeAttemptSummary:
    """Phần tóm tắt của lượt (bỏ `answers`); số đếm đã do `build_attempt` đếm trên các câu."""
    return PracticeAttemptSummary.model_validate(attempt.model_dump(exclude={"answers"}))
