"""Kho SQLite (SPECIFICATION §5.13): thoả `AppStore` (gồm `DocumentRepository` và `VaultRepository`).

Một kết nối cho mỗi kho, dùng chung giữa các luồng dưới một khoá; mọi lần ghi nằm trong `BEGIN IMMEDIATE`, mọi lần
đọc nhiều câu nằm trong một giao dịch đọc để thấy cùng một bản chụp. Ghi có điều kiện dùng một câu
`UPDATE … WHERE <khoá> = ? AND version = ?`; `rowcount = 0` mà bản ghi vẫn tồn tại ⇒ `VersionConflictError` (BE-003).
Riêng `ingestion_items` so thêm toàn bộ trạng thái đã đọc (xem `update_item`). Khoá bận quá `busy_timeout` ⇒
`StorageBusyError`.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import BaseModel, TypeAdapter

from ...domain.ai import AiCacheEntry
from ...domain.block_eligibility import is_candidate_block
from ...domain.blocks import Block, CropImage, ExtractionLedger, Section
from ...domain.documents import PageEditAudit, PageGeometry, PageSummary
from ...domain.ingestion import IngestionItem, PatchAudit, UploadedDocument
from ...domain.practice import (
    PracticeAnswer,
    PracticeAttempt,
    PracticeAttemptPage,
    PracticeAttemptSummary,
    ReviewQuestion,
    ReviewQuestionPage,
    StoredTwin,
    build_attempt,
)
from ...domain.repositories import (
    AI_CACHE_MAX_ROWS,
    AI_CACHE_TTL_DAYS,
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
    StorageBusyError,
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
from ...domain.schemas import (
    BlueprintSummary,
    DocumentMeta,
    ExamBlueprint,
    ExamFormat,
    KnowledgeChunk,
    SourceKind,
    Subject,
)
from ...domain.search_text import search_body, strip_diacritics
from ...domain.twins import BatchTwin, IdempotencyRecord, ReviewTicket, TwinBatch
from ...domain.vault import (
    ConceptPair,
    ConceptPairStatus,
    QuoteMismatchError,
    VaultEdit,
    check_definition_quote,
    link_practice_chunks,
)
from .connection import connect, fts5_available, immediate_transaction, is_busy, read_transaction
from .crop_files import CropFiles
from .migrator import load_fts5_script, migrate

IMPORTED_MEDIA_TYPE = "application/pdf"  # bộ nhập §6.5 chỉ nhận PDF trong MVP
JSON1_MISSING_DETAIL = (
    "SQLite của bản Python đang chạy thiếu JSON1 (json_extract, json_each), cần cho tài liệu nhập và Knowledge Vault; "
    "hãy dùng bản Python đi kèm SQLite ≥ 3.38 (JSON1 có sẵn) hoặc SQLite biên dịch kèm JSON1"
)
_FTS_TABLES: dict[SearchIndex, str] = {"primary": "chunks_fts", "no_diacritics": "chunks_fts_nodiac"}
_LOGGER = logging.getLogger(__name__)
_BATCH_TWIN = TypeAdapter(BatchTwin)  # twin_json đọc lại theo union câu tính toán / câu lý thuyết (`TwinBatch.twins`)
_EDIT_TABLES: dict[VaultEntity, tuple[str, str]] = {
    "chunk": ("chunks", "chunk_id"),
    "concept_pair": ("concept_pairs", "pair_id"),
}


def _ts(moment: datetime) -> str:
    """ISO-8601 UTC, độ dài cố định, để so sánh chuỗi đúng thứ tự thời gian; thời điểm không múi giờ coi là UTC."""
    aware = moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
    return aware.astimezone(UTC).isoformat(timespec="microseconds")


def _now() -> str:
    return _ts(datetime.now(UTC))


def _dump(model: BaseModel) -> str:
    """JSON của model, bỏ computed field (model `extra="forbid"` không nhận lại chúng khi validate)."""
    return model.model_dump_json(exclude=set(type(model).model_computed_fields))


def _translate(exc: sqlite3.IntegrityError, on_foreign_key: StorageError) -> StorageError:
    """Vi phạm khoá ngoại thành lỗi domain `on_foreign_key`; vi phạm ràng buộc khác thành `StorageError`."""
    return on_foreign_key if "FOREIGN KEY" in str(exc) else StorageError(str(exc))


def _version_conflict(
    conn: sqlite3.Connection, entity: str, key: str, expected: int, sql: str, params: tuple[object, ...]
) -> StorageError:
    """Lỗi cho một lần ghi có điều kiện không khớp dòng nào: bản ghi còn ⇒ xung đột version, không còn ⇒ không thấy."""
    row = conn.execute(sql, params).fetchone()
    if row is None:
        return RecordNotFoundError(entity, key)
    return VersionConflictError(entity, key, expected, int(row["version"]))


class SQLiteFeatureError(RuntimeError):
    """SQLite thiếu tính năng mà kho bắt buộc phải có (JSON1); kho từ chối mở, app từ chối khởi động."""


def json1_available(conn: sqlite3.Connection) -> bool:
    """SQLite có JSON1: chạy thử đúng hai hàm kho dùng (`json_extract`, `json_each`), không đọc bảng nào."""
    try:
        conn.execute("SELECT json_extract('[1]', '$[0]'), (SELECT count(*) FROM json_each('[1]'))").fetchone()
    except sqlite3.OperationalError:
        return False
    return True


@contextmanager
def _storage_errors() -> Iterator[None]:
    """Đổi lỗi `sqlite3` mà tầng trên cần phân biệt thành lỗi domain: ràng buộc ⇒ `StorageError`, khoá bận ⇒
    `StorageBusyError` (router trả 503 thay vì 500). Lỗi `sqlite3` khác ném nguyên."""
    try:
        yield
    except sqlite3.IntegrityError as exc:
        raise StorageError(str(exc)) from exc
    except sqlite3.OperationalError as exc:
        if is_busy(exc):
            raise StorageBusyError(f"kho dữ liệu đang bận: {exc}") from exc
        raise


class SQLiteStore:
    """Kho SQLite; tạo bằng `SQLiteStore.open(path)` (`":memory:"` cho test)."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        crops_dir: Path | None = None,
        ai_cache_max_rows: int = AI_CACHE_MAX_ROWS,
        ai_cache_ttl_days: int = AI_CACHE_TTL_DAYS,
    ) -> None:
        """Nhận kết nối đã mở bằng `connection.connect`, kiểm JSON1, chạy migration và tạo chỉ mục FTS5 nếu SQLite
        có FTS5.

        SQLite thiếu JSON1 ⇒ `SQLiteFeatureError` trước khi chạy migration nào (từ chối khởi động, thông điệp nêu
        cách sửa), thay vì `sqlite3.OperationalError` trần ở request đầu tiên đọc tóm tắt trang hay nhập chunk. DB
        có version mới hơn code ⇒ `SchemaVersionError` (từ chối khởi động). Thiếu FTS5 thì vẫn chạy (BM25 tự cài).

        `crops_dir` (chỉ `open_store_from_env` truyền, `HOLOCRON_DATA_DIR/crops`): thư mục file ảnh cắt; khi mở kho,
        file/thư mục ảnh không có hàng `crop_images` tương ứng bị dọn. `None` ⇒ kho không lưu ảnh cắt, KHÔNG tạo
        thư mục và KHÔNG xoá file nào (`save_crop_image` trả `"capped"`).
        """
        if not json1_available(conn):
            raise SQLiteFeatureError(JSON1_MISSING_DETAIL)
        if ai_cache_max_rows < 1 or ai_cache_ttl_days < 1:
            raise ValueError("ai_cache_max_rows và ai_cache_ttl_days phải là số nguyên dương")
        self._conn = conn
        self._lock = threading.RLock()
        self._ai_cache_max_rows = ai_cache_max_rows
        self._ai_cache_ttl = timedelta(days=ai_cache_ttl_days)
        self._crops = None if crops_dir is None else CropFiles(crops_dir)
        with self._lock:
            migrate(conn)
            self._fts5 = fts5_available(conn)
            if self._fts5:
                conn.executescript(load_fts5_script())
            self._sweep_orphan_crops()

    @classmethod
    def open(cls, path: str | Path, *, crops_dir: Path | None = None) -> SQLiteStore:
        conn = connect(path)
        try:
            return cls(conn, crops_dir=crops_dir)
        except BaseException:
            conn.close()
            raise

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @property
    def vault_search_available(self) -> bool:
        return self._fts5

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        """Giao dịch ghi; vi phạm ràng buộc chưa được đổi thành lỗi riêng thì thành `StorageError`."""
        with _storage_errors(), self._lock, immediate_transaction(self._conn) as conn:
            yield conn

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        with _storage_errors(), self._lock, read_transaction(self._conn) as conn:
            yield conn

    # ------------------------------------------------------------------ #
    # IngestionRepository
    # ------------------------------------------------------------------ #

    def get_item(self, item_id: str) -> IngestionItem | None:
        with self._read() as conn:
            row = conn.execute("SELECT item_json FROM ingestion_items WHERE item_id = ?", (item_id,)).fetchone()
            if row is None:
                return None
            audits = conn.execute(
                "SELECT table_id, from_version, to_version, editor_id, changes_json, at FROM patch_audits "
                "WHERE item_id = ? ORDER BY audit_id",
                (item_id,),
            ).fetchall()
        item = IngestionItem.model_validate_json(str(row["item_json"]))
        item.audits = [
            PatchAudit.model_validate(
                {
                    "table_id": audit["table_id"],
                    "from_version": audit["from_version"],
                    "to_version": audit["to_version"],
                    "editor_id": audit["editor_id"],
                    "changes": json.loads(str(audit["changes_json"])),
                    "at": audit["at"],
                }
            )
            for audit in audits
        ]
        return item

    def _item_columns(self, item: IngestionItem) -> tuple[object, ...]:
        return (
            item.source_doc_id,
            item.page_number,
            item.topic_key,
            int(item.approved),
            int(item.non_cloneable),
            item.model_dump_json(exclude={"audits"}),
            _now(),
            item.table_primary.version,
        )

    def _write_audits(self, conn: sqlite3.Connection, item: IngestionItem) -> None:
        """`patch_audits` là bản sao đúng của `item.audits` (thứ tự theo `audit_id`)."""
        conn.execute("DELETE FROM patch_audits WHERE item_id = ?", (item.item_id,))
        conn.executemany(
            "INSERT INTO patch_audits (item_id, table_id, from_version, to_version, editor_id, changes_json, at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    item.item_id,
                    audit.table_id,
                    audit.from_version,
                    audit.to_version,
                    audit.editor_id,
                    json.dumps(audit.model_dump(mode="json")["changes"], ensure_ascii=False),
                    _ts(audit.at),
                )
                for audit in item.audits
            ],
        )

    def _missing_document(self, exc: sqlite3.IntegrityError, document_id: str) -> StorageError:
        return _translate(exc, MissingReferenceError(f"không tìm thấy tài liệu nguồn {document_id}"))

    def save_item(self, item: IngestionItem) -> IngestionItem:
        with self._write() as conn:
            try:
                conn.execute(
                    "INSERT INTO ingestion_items (source_doc_id, page_number, topic_key, approved, non_cloneable, "
                    "item_json, updated_at, version, item_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT (item_id) DO UPDATE SET source_doc_id = excluded.source_doc_id, "
                    "page_number = excluded.page_number, topic_key = excluded.topic_key, "
                    "approved = excluded.approved, non_cloneable = excluded.non_cloneable, "
                    "item_json = excluded.item_json, updated_at = excluded.updated_at, version = excluded.version",
                    (*self._item_columns(item), item.item_id),
                )
            except sqlite3.IntegrityError as exc:
                raise self._missing_document(exc, item.source_doc_id) from exc
            self._write_audits(conn, item)
        return item

    def update_item(
        self, item: IngestionItem, expected_version: int, expected: IngestionItem | None = None
    ) -> IngestionItem:
        """So-và-ghi trên toàn bộ trạng thái đã đọc, trong một giao dịch `BEGIN IMMEDIATE`.

        Đọc dòng đang lưu sau khi đã giữ khoá ghi, so version rồi so trạng thái với `expected`, và ghi bằng
        `UPDATE … WHERE item_id = ? AND version = ? AND item_json = ?` với đúng chuỗi vừa đọc. Không tiến trình nào
        ghi chen được giữa bước đọc và bước ghi, nên hai lần ghi chạy chéo không tăng version (sửa stem, gắn
        non-cloneable, approve) không thể cùng thành công.
        """
        new_version = item.table_primary.version
        if new_version not in (expected_version, expected_version + 1):
            raise ValueError(f"version mới {new_version} không hợp lệ so với version mong đợi {expected_version}")
        with self._write() as conn:
            row = conn.execute(
                "SELECT version, item_json FROM ingestion_items WHERE item_id = ?", (item.item_id,)
            ).fetchone()
            if row is None:
                raise RecordNotFoundError("ingestion item", item.item_id)
            if int(row["version"]) != expected_version:
                raise VersionConflictError("ingestion item", item.item_id, expected_version, int(row["version"]))
            stored_json = str(row["item_json"])
            if expected is not None and not same_item_state(IngestionItem.model_validate_json(stored_json), expected):
                raise ConcurrentUpdateError("ingestion item", item.item_id)
            try:
                cursor = conn.execute(
                    "UPDATE ingestion_items SET source_doc_id = ?, page_number = ?, topic_key = ?, approved = ?, "
                    "non_cloneable = ?, item_json = ?, updated_at = ?, version = ? "
                    "WHERE item_id = ? AND version = ? AND item_json = ?",
                    (*self._item_columns(item), item.item_id, expected_version, stored_json),
                )
            except sqlite3.IntegrityError as exc:
                raise self._missing_document(exc, item.source_doc_id) from exc
            if cursor.rowcount != 1:  # không xảy ra khi đang giữ khoá ghi; giữ để không báo thành công khi chưa ghi
                raise ConcurrentUpdateError("ingestion item", item.item_id)
            self._write_audits(conn, item)
        return item

    def save_document(self, document: UploadedDocument) -> UploadedDocument:
        with self._write() as conn:
            conn.execute(
                "INSERT INTO documents (document_id, sha256, filename, media_type, size_bytes, uploaded_at) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
                (
                    document.document_id,
                    document.sha256,
                    document.filename,
                    document.media_type,
                    document.size_bytes,
                    _ts(document.uploaded_at),
                ),
            )
            row = conn.execute(f"{self._UPLOADED_SELECT} WHERE document_id = ?", (document.document_id,)).fetchone()
        return UploadedDocument.model_validate(dict(row))

    _UPLOADED_SELECT = "SELECT document_id, sha256, filename, media_type, size_bytes, uploaded_at FROM documents"

    def get_document(self, document_id: str) -> UploadedDocument | None:
        with self._read() as conn:
            row = conn.execute(f"{self._UPLOADED_SELECT} WHERE document_id = ?", (document_id,)).fetchone()
        if row is None:
            return None
        return UploadedDocument.model_validate(dict(row))

    # ------------------------------------------------------------------ #
    # BlueprintRepository
    # ------------------------------------------------------------------ #

    def save_blueprint(
        self, blueprint: ExamBlueprint, item_ids: Sequence[str] = (), *, implicit_subject: Subject | None = None
    ) -> ExamBlueprint:
        with self._write() as conn:
            self._ensure_subject(conn, implicit_subject)
            conn.execute(
                "INSERT INTO blueprints (blueprint_id, subject_code, blueprint_json, created_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (blueprint_id) DO UPDATE SET subject_code = excluded.subject_code, "
                "blueprint_json = excluded.blueprint_json, created_at = excluded.created_at, "
                "version = blueprints.version + 1",
                (blueprint.blueprint_id, blueprint.subject_code, _dump(blueprint), _ts(blueprint.created_at)),
            )
            conn.execute("DELETE FROM blueprint_items WHERE blueprint_id = ?", (blueprint.blueprint_id,))
            try:
                conn.executemany(
                    "INSERT INTO blueprint_items (blueprint_id, slot_index, item_id) VALUES (?, ?, ?)",
                    [(blueprint.blueprint_id, index + 1, item_id) for index, item_id in enumerate(item_ids)],
                )
            except sqlite3.IntegrityError as exc:
                missing = MissingReferenceError(f"blueprint trỏ tới item không tồn tại: {list(item_ids)}")
                raise _translate(exc, missing) from exc
        return blueprint

    def get_blueprint(self, blueprint_id: str) -> ExamBlueprint | None:
        with self._read() as conn:
            row = conn.execute(
                "SELECT blueprint_json FROM blueprints WHERE blueprint_id = ?", (blueprint_id,)
            ).fetchone()
        return None if row is None else ExamBlueprint.model_validate_json(str(row["blueprint_json"]))

    def blueprint_item_ids(self, blueprint_id: str) -> tuple[str, ...]:
        with self._read() as conn:
            rows = conn.execute(
                "SELECT item_id FROM blueprint_items WHERE blueprint_id = ? ORDER BY slot_index", (blueprint_id,)
            ).fetchall()
        return tuple(str(row["item_id"]) for row in rows)

    def list_blueprint_summaries(self, subject_code: str | None = None) -> list[BlueprintSummary]:
        sql = (
            "SELECT b.blueprint_id, b.subject_code, json_extract(b.blueprint_json, '$.total_questions') AS total, "
            "b.created_at, (SELECT COUNT(*) FROM twin_batches t WHERE t.blueprint_id = b.blueprint_id) AS batches "
            "FROM blueprints b"
        )
        order = " ORDER BY b.created_at DESC, b.blueprint_id DESC"
        with self._read() as conn:
            if subject_code is None:
                rows = conn.execute(sql + order).fetchall()
            else:
                rows = conn.execute(f"{sql} WHERE b.subject_code = ?{order}", (subject_code,)).fetchall()
        return [
            BlueprintSummary(
                blueprint_id=str(row["blueprint_id"]),
                subject_code=str(row["subject_code"]),
                total_questions=int(row["total"]),
                created_at=datetime.fromisoformat(str(row["created_at"])),
                batch_count=int(row["batches"]),
            )
            for row in rows
        ]

    # ------------------------------------------------------------------ #
    # TwinBatchRepository
    # ------------------------------------------------------------------ #

    _BATCH_SELECT = (
        "SELECT batch_id, blueprint_id, base_seed, body_digest, created_at FROM twin_batches "
        "WHERE blueprint_id = ? AND idempotency_key = ?"
    )

    @staticmethod
    def _load_batch(conn: sqlite3.Connection, row: sqlite3.Row) -> TwinBatch:
        """Dựng lại batch từ dòng `twin_batches` cùng twin và ticket của nó; twin validate theo union của `TwinBatch`."""
        twins = conn.execute(
            "SELECT twin_json FROM twins WHERE batch_id = ? ORDER BY ordinal", (row["batch_id"],)
        ).fetchall()
        tickets = conn.execute(
            "SELECT ticket_json FROM review_tickets WHERE batch_id = ? ORDER BY ticket_id", (row["batch_id"],)
        ).fetchall()
        return TwinBatch(
            batch_id=str(row["batch_id"]),
            blueprint_id=str(row["blueprint_id"]),
            base_seed=int(row["base_seed"]),
            twins=[_BATCH_TWIN.validate_json(str(twin["twin_json"])) for twin in twins],
            review_tickets=[ReviewTicket.model_validate_json(str(ticket["ticket_json"])) for ticket in tickets],
            created_at=datetime.fromisoformat(str(row["created_at"])),
        )

    @staticmethod
    def _insert_batch(conn: sqlite3.Connection, batch: TwinBatch, body_digest: str, key: str | None) -> None:
        """Ghi một batch (dòng `twin_batches`, twin, ticket); `request_json` để `NULL` vì chỉ lưu `body_digest`."""
        try:
            conn.execute(
                "INSERT INTO twin_batches (batch_id, blueprint_id, base_seed, request_json, body_digest, "
                "idempotency_key, created_at) VALUES (?, ?, ?, NULL, ?, ?, ?)",
                (batch.batch_id, batch.blueprint_id, batch.base_seed, body_digest, key, _ts(batch.created_at)),
            )
        except sqlite3.IntegrityError as exc:
            raise _translate(exc, MissingReferenceError(f"không tìm thấy blueprint {batch.blueprint_id}")) from exc
        conn.executemany(
            "INSERT INTO twins (twin_id, batch_id, slot_index, ordinal, twin_json) VALUES (?, ?, ?, ?, ?)",
            [
                (twin.twin_id, batch.batch_id, twin.slot_index, ordinal, _dump(twin))
                for ordinal, twin in enumerate(batch.twins)
            ],
        )
        conn.executemany(
            "INSERT INTO review_tickets (batch_id, slot_index, ticket_json) VALUES (?, ?, ?)",
            [(batch.batch_id, ticket.slot_index, _dump(ticket)) for ticket in batch.review_tickets],
        )

    def get_idempotent(self, blueprint_id: str, key: str, now: datetime) -> IdempotencyRecord | None:
        with self._read() as conn:
            row = conn.execute(self._BATCH_SELECT, (blueprint_id, key)).fetchone()
            if row is None:
                return None
            stored_at = datetime.fromisoformat(str(row["created_at"]))
            if now - stored_at > IDEMPOTENCY_TTL:
                return None
            batch = self._load_batch(conn, row)
        return IdempotencyRecord(body_digest=str(row["body_digest"]), stored_at=stored_at, batch=batch)

    def save_batch(self, batch: TwinBatch, idempotency_key: str | None, body_digest: str) -> TwinBatch:
        """Một giao dịch `BEGIN IMMEDIATE`: đọc bản ghi của khoá, quyết định theo `TwinBatchRepository.save_batch`, ghi.

        Khoá hết hạn được đặt `NULL` trước khi ghi lại đúng khoá đó, để giữ index UNIQUE
        `(blueprint_id, idempotency_key)`; batch cũ vẫn còn trong bảng (vẫn tính vào `batch_count`).
        """
        key = idempotency_key
        with self._write() as conn:
            if key is not None:
                row = conn.execute(self._BATCH_SELECT, (batch.blueprint_id, key)).fetchone()
                if row is not None:
                    stored_at = datetime.fromisoformat(str(row["created_at"]))
                    if batch.created_at - stored_at > IDEMPOTENCY_TTL:
                        conn.execute(
                            "UPDATE twin_batches SET idempotency_key = NULL WHERE batch_id = ?", (row["batch_id"],)
                        )
                    elif str(row["body_digest"]) == body_digest:
                        return self._load_batch(conn, row)
                    else:
                        key = None  # khác body: request mới, không đụng bản ghi của khoá (§6.3)
            self._insert_batch(conn, batch, body_digest, key)
        return batch

    def save_idempotent(self, blueprint_id: str, key: str, record: IdempotencyRecord) -> None:
        """Dọn khoá rồi ghi batch mới trong cùng giao dịch, để giữ index UNIQUE `(blueprint_id, idempotency_key)`.

        Khoá hết hạn (của mọi blueprint, tính tới `record.stored_at`) và khoá cũ trùng `(blueprint, key)` được đặt
        `NULL`; batch cũ vẫn còn trong bảng nhưng không tra theo khoá được nữa (cùng hành vi ghi đè của bản in-memory).
        `batch_id` là của `record.batch`, `created_at` là `record.stored_at` (cùng giá trị, `IdempotencyRecord`).
        """
        batch = record.batch.model_copy(update={"blueprint_id": blueprint_id})
        cutoff = _ts(record.stored_at - IDEMPOTENCY_TTL)
        with self._write() as conn:
            conn.execute(
                "UPDATE twin_batches SET idempotency_key = NULL "
                "WHERE idempotency_key IS NOT NULL AND (created_at < ? OR (blueprint_id = ? AND idempotency_key = ?))",
                (cutoff, blueprint_id, key),
            )
            self._insert_batch(conn, batch, record.body_digest, key)

    def get_batch_blueprint_id(self, batch_id: str) -> str | None:
        with self._read() as conn:
            row = conn.execute("SELECT blueprint_id FROM twin_batches WHERE batch_id = ?", (batch_id,)).fetchone()
        return None if row is None else str(row["blueprint_id"])

    def get_twins(self, twin_ids: Sequence[str]) -> dict[str, StoredTwin]:
        """Một câu đọc: twin nối batch và blueprint (môn lấy từ `blueprints.subject_code`)."""
        with self._read() as conn:
            rows = conn.execute(
                "SELECT t.twin_id, t.batch_id, t.twin_json, b.blueprint_id, bp.subject_code FROM twins t "
                "JOIN twin_batches b ON b.batch_id = t.batch_id JOIN blueprints bp ON bp.blueprint_id = b.blueprint_id "
                "WHERE t.twin_id IN (SELECT value FROM json_each(?))",
                (json.dumps(list(twin_ids)),),
            ).fetchall()
        return {
            str(row["twin_id"]): StoredTwin(
                twin=_BATCH_TWIN.validate_json(str(row["twin_json"])),
                batch_id=str(row["batch_id"]),
                blueprint_id=str(row["blueprint_id"]),
                subject_code=str(row["subject_code"]),
            )
            for row in rows
        }

    # ------------------------------------------------------------------ #
    # SubjectRepository
    # ------------------------------------------------------------------ #

    _SUBJECT_SELECT = "SELECT subject_code, display_name, exam_format, version, created_at, updated_at FROM subjects"

    @staticmethod
    def _ensure_subject(conn: sqlite3.Connection, subject: Subject | None) -> None:
        """Tạo môn ngầm khi chưa có, trong giao dịch của bản ghi chính; môn đã có giữ nguyên."""
        if subject is None:
            return
        conn.execute(
            "INSERT INTO subjects (subject_code, display_name, exam_format, created_at, updated_at, version) "
            "VALUES (?, ?, ?, ?, ?, 1) ON CONFLICT (subject_code) DO NOTHING",
            (
                subject.subject_code,
                subject.display_name,
                subject.exam_format.value,
                _ts(subject.created_at),
                _ts(subject.updated_at),
            ),
        )

    @staticmethod
    def _drop_implicit_subject(conn: sqlite3.Connection, subject: Subject | None) -> None:
        """Gỡ môn tạo ngầm của một lần nhập bị huỷ, nếu nó còn nguyên và không còn tài liệu hay blueprint nào cùng môn."""
        if subject is None:
            return
        conn.execute(
            "DELETE FROM subjects WHERE subject_code = ? AND display_name = ? AND exam_format = ? AND created_at = ? "
            "AND updated_at = ? AND version = 1 "
            "AND NOT EXISTS (SELECT 1 FROM documents WHERE subject_code = ?) "
            "AND NOT EXISTS (SELECT 1 FROM blueprints WHERE subject_code = ?)",
            (
                subject.subject_code,
                subject.display_name,
                subject.exam_format.value,
                _ts(subject.created_at),
                _ts(subject.updated_at),
                subject.subject_code,
                subject.subject_code,
            ),
        )

    def list_subjects(self) -> list[Subject]:
        with self._read() as conn:
            rows = conn.execute(f"{self._SUBJECT_SELECT} ORDER BY subject_code").fetchall()
        return [Subject.model_validate(dict(row)) for row in rows]

    def get_subject(self, subject_code: str) -> Subject | None:
        with self._read() as conn:
            row = conn.execute(f"{self._SUBJECT_SELECT} WHERE subject_code = ?", (subject_code,)).fetchone()
        return None if row is None else Subject.model_validate(dict(row))

    def create_subject(self, subject: Subject) -> Subject:
        with self._write() as conn:
            cursor = conn.execute(
                "INSERT INTO subjects (subject_code, display_name, exam_format, created_at, updated_at, version) "
                "VALUES (?, ?, ?, ?, ?, 1) ON CONFLICT (subject_code) DO NOTHING",
                (
                    subject.subject_code,
                    subject.display_name,
                    subject.exam_format.value,
                    _ts(subject.created_at),
                    _ts(subject.updated_at),
                ),
            )
            if cursor.rowcount == 0:
                raise SubjectExistsError(subject.subject_code)
            row = conn.execute(f"{self._SUBJECT_SELECT} WHERE subject_code = ?", (subject.subject_code,)).fetchone()
        return Subject.model_validate(dict(row))

    def update_subject(
        self,
        subject_code: str,
        expected_version: int,
        *,
        display_name: str | None,
        exam_format: ExamFormat | None,
        now: datetime,
    ) -> Subject:
        with self._write() as conn:
            cursor = conn.execute(
                "UPDATE subjects SET display_name = COALESCE(?, display_name), "
                "exam_format = COALESCE(?, exam_format), updated_at = ?, version = version + 1 "
                "WHERE subject_code = ? AND version = ?",
                (
                    display_name,
                    None if exam_format is None else exam_format.value,
                    _ts(now),
                    subject_code,
                    expected_version,
                ),
            )
            if cursor.rowcount == 0:
                raise _version_conflict(
                    conn,
                    "môn học",
                    subject_code,
                    expected_version,
                    "SELECT version FROM subjects WHERE subject_code = ?",
                    (subject_code,),
                )
            row = conn.execute(f"{self._SUBJECT_SELECT} WHERE subject_code = ?", (subject_code,)).fetchone()
        return Subject.model_validate(dict(row))

    # ------------------------------------------------------------------ #
    # DocumentRepository
    # ------------------------------------------------------------------ #

    def _imported_columns(self, document: ImportedDocument) -> tuple[object, ...]:
        meta = document.meta
        return (
            document.filename,
            IMPORTED_MEDIA_TYPE,
            document.size_bytes,
            meta.subject_code,
            meta.title,
            meta.source_kind.value,
            document.output_language,
            meta.page_count,
            int(meta.has_text_layer),
            document.status,
            _ts(meta.uploaded_at),
            document.chapter_code,
            document.chapter_title,
            document.chapter_code_source,
            document.chapter_title_source,
            json.dumps(document.chapter_flags),
            int(document.auto_confirm),
        )

    def _upsert_imported(self, conn: sqlite3.Connection, document: ImportedDocument) -> sqlite3.Row:
        """Tạo dòng `documents`, hoặc bổ sung thông tin cho dòng cùng `document_id` (version tăng 1); trả dòng đã ghi."""
        conn.execute(
            "INSERT INTO documents (filename, media_type, size_bytes, subject_code, title, source_kind, "
            "output_language, page_count, has_text_layer, status, uploaded_at, chapter_code, chapter_title, "
            "chapter_code_source, chapter_title_source, chapter_flags_json, auto_confirm, document_id, sha256) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (document_id) DO UPDATE SET filename = excluded.filename, "
            "media_type = excluded.media_type, size_bytes = excluded.size_bytes, "
            "subject_code = excluded.subject_code, title = excluded.title, source_kind = excluded.source_kind, "
            "output_language = excluded.output_language, page_count = excluded.page_count, "
            "has_text_layer = excluded.has_text_layer, status = excluded.status, "
            "uploaded_at = excluded.uploaded_at, chapter_code = excluded.chapter_code, "
            "chapter_title = excluded.chapter_title, chapter_code_source = excluded.chapter_code_source, "
            "chapter_title_source = excluded.chapter_title_source, chapter_flags_json = excluded.chapter_flags_json, "
            "auto_confirm = excluded.auto_confirm, version = documents.version + 1",
            (*self._imported_columns(document), document.meta.doc_id, document.meta.sha256),
        )
        row: sqlite3.Row = conn.execute(
            f"{self._IMPORTED_SELECT} AND document_id = ?", (document.meta.doc_id,)
        ).fetchone()
        return row

    def save_imported_document(self, document: ImportedDocument) -> ImportedDocument:
        with self._write() as conn:
            row = self._upsert_imported(conn, document)
        return self._imported_from_row(row)

    def create_imported_document(
        self, document: ImportedDocument, pending_pages: Sequence[BaseModel]
    ) -> ImportedDocument:
        """Một giao dịch `BEGIN IMMEDIATE`: kiểm chưa có tài liệu nhập, ghi dòng `documents` rồi từng trang; lỗi ở
        bất kỳ câu nào ⇒ `ROLLBACK` (`connection._transaction`), không còn dòng nào."""
        document_id = document.meta.doc_id
        if len(pending_pages) != document.meta.page_count:
            raise ValueError("số trang pending phải bằng page_count")
        with self._write() as conn:
            existing = conn.execute(
                "SELECT 1 FROM documents WHERE document_id = ? AND subject_code IS NOT NULL", (document_id,)
            ).fetchone()
            if existing is not None:
                raise DocumentExistsError(document_id)
            self._upsert_imported(conn, document)
            updated_at = _now()
            for page_number, page in enumerate(pending_pages, start=1):
                conn.execute(
                    "INSERT INTO pages (document_id, page_number, status, extraction_json, confirmed, updated_at) "
                    "VALUES (?, ?, 'pending', ?, 0, ?)",
                    (document_id, page_number, _dump(page), updated_at),
                )
            self._ensure_subject(conn, Subject.implicit(document.meta.subject_code, document.meta.uploaded_at))
            self._next_generation(conn, document_id)
            row = conn.execute(f"{self._IMPORTED_SELECT} AND document_id = ?", (document_id,)).fetchone()
        return self._imported_from_row(row)

    @staticmethod
    def _next_generation(conn: sqlite3.Connection, document_id: str) -> None:
        """Tăng bộ đếm thế hệ nhập (tạo = 1; bảng `import_generations` không bị xoá theo tài liệu) và sao vào
        `documents.import_generation`, trong giao dịch của người gọi."""
        conn.execute(
            "INSERT INTO import_generations (document_id, last_generation) VALUES (?, 1) "
            "ON CONFLICT (document_id) DO UPDATE SET last_generation = last_generation + 1",
            (document_id,),
        )
        conn.execute(
            "UPDATE documents SET import_generation = "
            "(SELECT last_generation FROM import_generations WHERE document_id = ?) WHERE document_id = ?",
            (document_id, document_id),
        )

    _IMPORTED_SELECT = (
        "SELECT document_id, sha256, filename, size_bytes, subject_code, title, source_kind, output_language, "
        "page_count, has_text_layer, status, uploaded_at, chapter_code, chapter_title, chapter_code_source, "
        "chapter_title_source, chapter_flags_json, auto_confirm, version, layout_version, layout_settings_sha256, "
        "blocks_after_close, import_generation FROM documents WHERE subject_code IS NOT NULL"
    )

    @staticmethod
    def _imported_from_row(row: sqlite3.Row) -> ImportedDocument:
        meta = DocumentMeta(
            doc_id=row["document_id"],
            subject_code=row["subject_code"],
            title=row["title"],
            source_kind=row["source_kind"],
            sha256=row["sha256"],
            page_count=row["page_count"],
            has_text_layer=bool(row["has_text_layer"]),
            uploaded_at=row["uploaded_at"],
        )
        return ImportedDocument.model_validate(
            {
                "meta": meta,
                "filename": row["filename"],
                "size_bytes": row["size_bytes"],
                "output_language": row["output_language"],
                "chapter_code": row["chapter_code"],
                "status": row["status"],
                "version": row["version"],
                "chapter_code_source": row["chapter_code_source"],
                "chapter_title": row["chapter_title"],
                "chapter_title_source": row["chapter_title_source"],
                "chapter_flags": json.loads(row["chapter_flags_json"]),
                "auto_confirm": bool(row["auto_confirm"]),
                "layout_version": row["layout_version"],
                "layout_settings_sha256": row["layout_settings_sha256"],
                "blocks_after_close": bool(row["blocks_after_close"]),
                "import_generation": row["import_generation"],
            }
        )

    def get_imported_document(self, document_id: str) -> ImportedDocument | None:
        with self._read() as conn:
            row = conn.execute(f"{self._IMPORTED_SELECT} AND document_id = ?", (document_id,)).fetchone()
        return None if row is None else self._imported_from_row(row)

    def _update_imported_row(
        self, conn: sqlite3.Connection, document: ImportedDocument, expected_version: int
    ) -> ImportedDocument:
        """So-và-ghi cột của bộ nhập (KHÔNG ghi bốn cột khối); trả `document` với version mới và cột khối đang lưu."""
        document_id = document.meta.doc_id
        cursor = conn.execute(
            "UPDATE documents SET filename = ?, media_type = ?, size_bytes = ?, subject_code = ?, title = ?, "
            "source_kind = ?, output_language = ?, page_count = ?, has_text_layer = ?, status = ?, "
            "uploaded_at = ?, chapter_code = ?, chapter_title = ?, chapter_code_source = ?, "
            "chapter_title_source = ?, chapter_flags_json = ?, auto_confirm = ?, version = version + 1 "
            "WHERE document_id = ? AND version = ?",
            (*self._imported_columns(document), document_id, expected_version),
        )
        if cursor.rowcount == 0:
            raise _version_conflict(
                conn,
                "tài liệu",
                document_id,
                expected_version,
                "SELECT version FROM documents WHERE document_id = ?",
                (document_id,),
            )
        row = conn.execute(
            "SELECT layout_version, layout_settings_sha256, blocks_after_close, import_generation FROM documents "
            "WHERE document_id = ?",
            (document_id,),
        ).fetchone()
        return document.model_copy(
            update={
                "version": expected_version + 1,
                "layout_version": row["layout_version"],
                "layout_settings_sha256": row["layout_settings_sha256"],
                "blocks_after_close": bool(row["blocks_after_close"]),
                "import_generation": row["import_generation"],
            }
        )

    def update_imported_document(self, document: ImportedDocument, expected_version: int) -> ImportedDocument:
        with self._write() as conn:
            return self._update_imported_row(conn, document, expected_version)

    def list_imported_documents(self, subject_code: str | None = None) -> list[ImportedDocument]:
        order = " ORDER BY uploaded_at DESC, document_id DESC"
        with self._read() as conn:
            if subject_code is None:
                rows = conn.execute(self._IMPORTED_SELECT + order).fetchall()
            else:
                rows = conn.execute(f"{self._IMPORTED_SELECT} AND subject_code = ?{order}", (subject_code,)).fetchall()
        return [self._imported_from_row(row) for row in rows]

    def _delete_chunk_index(self, conn: sqlite3.Connection, document_id: str) -> set[str]:
        """Xoá dòng chỉ mục FTS5 của mọi chunk thuộc tài liệu (bảng ảo không có khoá ngoại); trả các môn có chunk."""
        if self._fts5:
            for table in _FTS_TABLES.values():
                conn.execute(
                    f"DELETE FROM {table} WHERE chunk_id IN (SELECT chunk_id FROM chunks WHERE document_id = ?)",
                    (document_id,),
                )
        return {
            str(row["subject_code"])
            for row in conn.execute("SELECT DISTINCT subject_code FROM chunks WHERE document_id = ?", (document_id,))
        }

    def _delete_document_row(self, conn: sqlite3.Connection, document_id: str) -> None:
        subjects = self._delete_chunk_index(conn, document_id)
        try:
            conn.execute("DELETE FROM documents WHERE document_id = ?", (document_id,))
        except sqlite3.IntegrityError as exc:
            in_use = RecordInUseError(f"tài liệu {document_id} còn được ingestion item tham chiếu")
            raise _translate(exc, in_use) from exc
        self._relink(conn, subjects)  # chunk còn lại không được trỏ tới chunk vừa xoá
        self._unlink_ai_cache(conn, document_id)

    def delete_document(self, document_id: str) -> bool:
        """Khối, ledger, đề xuất cấu trúc theo CASCADE của `pages`; mục, hàng ảnh cắt theo CASCADE của `documents`;
        thư mục ảnh `crops/<document_id>/` bị xoá SAU commit. `import_generations` giữ nguyên."""
        with self._lock:
            with self._write() as conn:
                if conn.execute("SELECT 1 FROM documents WHERE document_id = ?", (document_id,)).fetchone() is None:
                    return False
                self._delete_document_row(conn, document_id)
            self._delete_crop_dir(document_id)
        return True

    def revert_imported_document(self, document_id: str, upload: UploadedDocument | None) -> bool:
        with self._lock:
            reverted = self._revert_rows(document_id, upload)
            if reverted:
                self._delete_crop_dir(document_id)
        return reverted

    def _revert_rows(self, document_id: str, upload: UploadedDocument | None) -> bool:
        with self._write() as conn:
            exists = conn.execute(f"{self._IMPORTED_SELECT} AND document_id = ?", (document_id,)).fetchone()
            if exists is None:
                return False
            imported = self._imported_from_row(exists)
            implicit_subject = Subject.implicit(imported.meta.subject_code, imported.meta.uploaded_at)
            if upload is None:
                self._delete_document_row(conn, document_id)
                self._drop_implicit_subject(conn, implicit_subject)
                return True
            subjects = self._delete_chunk_index(conn, document_id)
            conn.execute("DELETE FROM chunks WHERE document_id = ?", (document_id,))  # cặp khái niệm theo CASCADE
            # lịch sử sửa trang, khối, ledger, đề xuất cấu trúc theo CASCADE của `pages`
            conn.execute("DELETE FROM pages WHERE document_id = ?", (document_id,))
            conn.execute("DELETE FROM sections WHERE document_id = ?", (document_id,))
            conn.execute("DELETE FROM crop_images WHERE document_id = ?", (document_id,))
            conn.execute(
                f"UPDATE documents SET filename = ?, media_type = ?, size_bytes = ?, uploaded_at = ?, "
                f"{self._IMPORT_ONLY_RESET}, version = version + 1 WHERE document_id = ?",
                (upload.filename, upload.media_type, upload.size_bytes, _ts(upload.uploaded_at), document_id),
            )
            self._relink(conn, subjects)
            self._drop_implicit_subject(conn, implicit_subject)
        return True

    # Cột chỉ bộ nhập §6.5 ghi; dòng chỉ do upload đề §6.1 tạo có mọi cột này là NULL (cột có mặc định của migration
    # 0004 và của migration khối trở về mặc định: `chapter_flags_json = '[]'`, `auto_confirm = 1`, cột khối null/0).
    _IMPORT_ONLY_RESET = (
        "subject_code = NULL, title = NULL, source_kind = NULL, output_language = NULL, page_count = NULL, "
        "has_text_layer = NULL, status = NULL, chapter_code = NULL, chapter_title = NULL, chapter_code_source = NULL, "
        "chapter_title_source = NULL, chapter_flags_json = '[]', auto_confirm = 1, layout_version = NULL, "
        "layout_settings_sha256 = NULL, blocks_after_close = 0, import_generation = 0"
    )

    def add_page(
        self, document_id: str, page_number: int, page: BaseModel, *, status: PageStatus, confirmed: bool
    ) -> None:
        with self._write() as conn:
            try:
                conn.execute(
                    "INSERT INTO pages (document_id, page_number, status, extraction_json, confirmed, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (document_id, page_number, status, _dump(page), int(confirmed), _now()),
                )
            except sqlite3.IntegrityError as exc:
                raise self._missing_document(exc, document_id) from exc

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
        if audit is not None:
            check_page_audit(audit, document_id, page_number, expected_version)
        geometry_json = None if geometry is None else geometry.model_dump_json()
        with self._write() as conn:
            cursor = conn.execute(
                "UPDATE pages SET status = ?, extraction_json = ?, confirmed = ?, updated_at = ?, "
                "geometry_json = COALESCE(?, geometry_json), version = version + 1 "
                "WHERE document_id = ? AND page_number = ? AND version = ?",
                (
                    status,
                    _dump(page),
                    int(confirmed),
                    _now(),
                    geometry_json,
                    document_id,
                    page_number,
                    expected_version,
                ),
            )
            if cursor.rowcount == 0:
                raise _version_conflict(
                    conn,
                    "trang",
                    f"{document_id}#{page_number}",
                    expected_version,
                    "SELECT version FROM pages WHERE document_id = ? AND page_number = ?",
                    (document_id, page_number),
                )
            if audit is not None:
                conn.execute(
                    "INSERT INTO page_audits (document_id, page_number, from_version, to_version, editor_id, "
                    "changes_json, at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        document_id,
                        page_number,
                        audit.from_version,
                        audit.to_version,
                        audit.editor_id,
                        json.dumps(audit.model_dump(mode="json")["changes"], ensure_ascii=False),
                        _ts(audit.at),
                    ),
                )
        return expected_version + 1

    def list_page_audits(self, document_id: str, page_number: int) -> list[PageEditAudit]:
        with self._read() as conn:
            rows = conn.execute(
                "SELECT document_id, page_number, from_version, to_version, editor_id, changes_json, at "
                "FROM page_audits WHERE document_id = ? AND page_number = ? ORDER BY audit_id",
                (document_id, page_number),
            ).fetchall()
        return [
            PageEditAudit.model_validate(
                {
                    "document_id": row["document_id"],
                    "page_number": row["page_number"],
                    "from_version": row["from_version"],
                    "to_version": row["to_version"],
                    "editor_id": row["editor_id"],
                    "changes": json.loads(str(row["changes_json"])),
                    "at": row["at"],
                }
            )
            for row in rows
        ]

    def get_page[M: BaseModel](self, document_id: str, page_number: int, model: type[M]) -> M | None:
        with self._read() as conn:
            row = conn.execute(
                "SELECT extraction_json FROM pages WHERE document_id = ? AND page_number = ?",
                (document_id, page_number),
            ).fetchone()
        return None if row is None else model.model_validate_json(str(row["extraction_json"]))

    def list_page_summaries(self, document_id: str) -> list[PageSummary]:
        """Đọc cột `status`, `confirmed` và các field tóm tắt của JSON trang bằng `json_extract` của SQLite (GĐ0 thêm
        `page_role`, `dropped`; JSON trang lưu trước GĐ0 không có hai field này ⇒ `content`, không bỏ mềm. GĐ1 1B thêm
        `confirmed_by`; JSON trước GĐ1 không có ⇒ null)."""
        with self._read() as conn:
            return self._page_summaries(conn, document_id)

    @staticmethod
    def _page_summaries(conn: sqlite3.Connection, document_id: str) -> list[PageSummary]:
        rows = conn.execute(
            "SELECT page_number, status, confirmed, json_extract(extraction_json, '$.tier_used') AS tier_used, "
            "json_extract(extraction_json, '$.review_reasons') AS review_reasons, "
            "json_extract(extraction_json, '$.page_role') AS page_role, "
            "json_extract(extraction_json, '$.dropped') AS dropped, "
            "json_extract(extraction_json, '$.confirmed_by') AS confirmed_by "
            "FROM pages WHERE document_id = ? ORDER BY page_number",
            (document_id,),
        ).fetchall()
        return [
            PageSummary.model_validate(
                {
                    "page_number": row["page_number"],
                    "status": row["status"],
                    "tier_used": row["tier_used"],
                    "review_reasons": json.loads(str(row["review_reasons"] or "[]")),
                    "confirmed": bool(row["confirmed"]),
                    "page_role": row["page_role"] or "content",
                    "dropped": bool(row["dropped"]),
                    "confirmed_by": row["confirmed_by"],
                }
            )
            for row in rows
        ]

    def list_pages[M: BaseModel](self, document_id: str, model: type[M]) -> list[M]:
        with self._read() as conn:
            rows = conn.execute(
                "SELECT extraction_json FROM pages WHERE document_id = ? ORDER BY page_number", (document_id,)
            ).fetchall()
        return [model.model_validate_json(str(row["extraction_json"])) for row in rows]

    # ------------------------------------------------------------------ #
    # VaultRepository
    # ------------------------------------------------------------------ #

    def _insert_chunk(self, conn: sqlite3.Connection, chunk: KnowledgeChunk, body: str) -> None:
        """Ghi một chunk, `chunk_topics` và hai chỉ mục FTS5 bằng kết nối đang ở trong giao dịch ghi.

        Chỉ mục chính nhận `body` (token đã qua ADR-006 mục 5); chỉ mục không dấu nhận `strip_diacritics(body)`, tức
        đúng token của `no_diacritics_tokens`, nên FTS5 không phải tự bỏ dấu (xem `fts5_vault.sql`).
        """
        if chunk.embedding_model is not None:
            raise ValueError("bảng chunks chưa có cột embedding_model (luôn null trong MVP, ADR-006 mục 4)")
        try:
            conn.execute(
                "INSERT INTO chunks (chunk_id, document_id, subject_code, chapter_code, category, page_number, "
                "page_end, raw_text, verbatim_quote, tags_json, linked_chunk_ids_json, token_count, label_status, "
                "mapping_confidence, version, is_exercise, section_id, block_ids_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    chunk.chunk_id,
                    chunk.doc_id,
                    chunk.subject_code,
                    chunk.chapter_code,
                    chunk.category.value,
                    chunk.page_number,
                    chunk.page_end,
                    chunk.raw_text,
                    chunk.verbatim_quote,
                    json.dumps(chunk.tags, ensure_ascii=False),
                    json.dumps(chunk.linked_chunk_ids, ensure_ascii=False),
                    chunk.token_count,
                    chunk.label_status,
                    chunk.mapping_confidence,
                    chunk.version,
                    None if chunk.is_exercise is None else int(chunk.is_exercise),
                    chunk.section_id,
                    json.dumps(chunk.block_ids),
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise self._missing_document(exc, chunk.doc_id) from exc
        conn.executemany(
            "INSERT INTO chunk_topics (chunk_id, topic_key) VALUES (?, ?)",
            [(chunk.chunk_id, topic_key) for topic_key in chunk.topic_keys],
        )
        if self._fts5:
            bodies: dict[SearchIndex, str] = {"primary": body, "no_diacritics": strip_diacritics(body)}
            for index, table in _FTS_TABLES.items():
                conn.execute(
                    f"INSERT INTO {table} (body, chunk_id, subject_code) VALUES (?, ?, ?)",
                    (bodies[index], chunk.chunk_id, chunk.subject_code),
                )

    def add_chunk(self, chunk: KnowledgeChunk, search_body: str) -> None:
        with self._write() as conn:
            self._insert_chunk(conn, chunk, search_body)

    def add_chunks(self, chunks: Sequence[KnowledgeChunk]) -> list[KnowledgeChunk]:
        """Một giao dịch `BEGIN IMMEDIATE` cho cả lô: lỗi ở chunk bất kỳ thì `ROLLBACK`, không còn dòng nào."""
        with self._write() as conn:
            subjects: set[str] = set()
            for chunk in chunks:
                if conn.execute("SELECT 1 FROM chunks WHERE chunk_id = ?", (chunk.chunk_id,)).fetchone() is not None:
                    continue
                self._insert_chunk(conn, chunk, search_body(chunk.raw_text))
                subjects.add(chunk.subject_code)
            self._relink(conn, subjects)
            stored = {
                chunk.chunk_id: chunk
                for chunk in self._load_chunks(
                    conn, "WHERE chunk_id IN (SELECT value FROM json_each(?))", (self._ids_json(chunks),)
                )
            }
        return [stored[chunk.chunk_id] for chunk in chunks]

    @staticmethod
    def _ids_json(chunks: Sequence[KnowledgeChunk]) -> str:
        return json.dumps([chunk.chunk_id for chunk in chunks])

    @staticmethod
    def _chunk_from_row(row: sqlite3.Row, topic_keys: list[str]) -> KnowledgeChunk:
        return KnowledgeChunk.model_validate(
            {
                "chunk_id": row["chunk_id"],
                "doc_id": row["document_id"],
                "subject_code": row["subject_code"],
                "chapter_code": row["chapter_code"],
                "page_number": row["page_number"],
                "page_end": row["page_end"],
                "raw_text": row["raw_text"],
                "verbatim_quote": row["verbatim_quote"],
                "category": row["category"],
                "tags": json.loads(str(row["tags_json"])),
                "topic_keys": topic_keys,
                "linked_chunk_ids": json.loads(str(row["linked_chunk_ids_json"])),
                "token_count": row["token_count"],
                "label_status": row["label_status"],
                "mapping_confidence": row["mapping_confidence"],
                "version": row["version"],
                "is_exercise": None if row["is_exercise"] is None else bool(row["is_exercise"]),
                "section_id": row["section_id"],
                "block_ids": json.loads(str(row["block_ids_json"])),
            }
        )

    _CHUNK_SELECT = (
        "SELECT chunk_id, document_id, subject_code, chapter_code, category, page_number, page_end, raw_text, "
        "verbatim_quote, tags_json, linked_chunk_ids_json, token_count, label_status, mapping_confidence, version, "
        "is_exercise, section_id, block_ids_json FROM chunks"
    )

    def _load_chunks(self, conn: sqlite3.Connection, where: str, params: Sequence[object]) -> list[KnowledgeChunk]:
        """Chunk thoả `where` kèm `topic_keys`, sắp theo `(document_id, page_number, chunk_id)`."""
        rows = conn.execute(
            f"{self._CHUNK_SELECT} {where} ORDER BY document_id, page_number, chunk_id", params
        ).fetchall()
        topic_rows = conn.execute(
            f"SELECT chunk_id, topic_key FROM chunk_topics WHERE chunk_id IN (SELECT chunk_id FROM chunks {where}) "
            "ORDER BY topic_key",
            params,
        ).fetchall()
        topics: dict[str, list[str]] = {}
        for topic in topic_rows:
            topics.setdefault(str(topic["chunk_id"]), []).append(str(topic["topic_key"]))
        return [self._chunk_from_row(row, topics.get(str(row["chunk_id"]), [])) for row in rows]

    def _relink(self, conn: sqlite3.Connection, subject_codes: set[str]) -> None:
        """Tính lại `linked_chunk_ids` (§12.1) của mọi chunk thuộc `subject_codes`, trong giao dịch ghi đang mở.

        Không tăng `version`: liên kết là dữ liệu dẫn xuất, không phải nhãn mà `expected_version` bảo vệ.
        """
        for subject_code in sorted(subject_codes):
            members = self._load_chunks(conn, "WHERE subject_code = ?", (subject_code,))
            links = link_practice_chunks(members)
            conn.executemany(
                "UPDATE chunks SET linked_chunk_ids_json = ? WHERE chunk_id = ?",
                [
                    (json.dumps(links[chunk.chunk_id], ensure_ascii=False), chunk.chunk_id)
                    for chunk in members
                    if chunk.linked_chunk_ids != links[chunk.chunk_id]
                ],
            )

    def get_chunk(self, chunk_id: str) -> KnowledgeChunk | None:
        with self._read() as conn:
            found = self._load_chunks(conn, "WHERE chunk_id = ?", (chunk_id,))
        return found[0] if found else None

    def has_document_chunks(self, document_id: str) -> bool:
        with self._read() as conn:
            row = conn.execute("SELECT 1 FROM chunks WHERE document_id = ? LIMIT 1", (document_id,)).fetchone()
        return row is not None

    def list_chunks(self, subject_code: str, chapter_code: str | None = None) -> list[KnowledgeChunk]:
        where = "WHERE subject_code = ?" + ("" if chapter_code is None else " AND chapter_code = ?")
        params: tuple[str, ...] = (subject_code,) if chapter_code is None else (subject_code, chapter_code)
        with self._read() as conn:
            return self._load_chunks(conn, where, params)

    def match_chunk_ids(self, subject_code: str, fts_query: str, index: SearchIndex = "primary") -> list[str]:
        if not self._fts5:
            raise VaultSearchUnavailableError("SQLite không có FTS5; dùng BM25 tự cài (ADR-006 mục 4)")
        table = _FTS_TABLES[index]
        with self._read() as conn:
            try:
                rows = conn.execute(
                    f"SELECT chunk_id FROM {table} WHERE {table} MATCH ? AND subject_code = ? ORDER BY chunk_id",
                    (fts_query, subject_code),
                ).fetchall()
            except sqlite3.OperationalError as exc:
                raise ValueError(f"biểu thức FTS5 không hợp lệ: {exc}") from exc
        return [str(row["chunk_id"]) for row in rows]

    @staticmethod
    def _edit_columns(edit: VaultEdit | None) -> tuple[str | None, str | None]:
        return (None, None) if edit is None else (edit.editor_id, _ts(edit.at))

    def update_chunk_labels(
        self, chunk: KnowledgeChunk, expected_version: int, edit: VaultEdit | None = None
    ) -> KnowledgeChunk:
        """Một giao dịch: nhãn của chunk, `chapter_code` của cặp thuộc chunk, `linked_chunk_ids` của môn, người sửa."""
        edited_by, edited_at = self._edit_columns(edit)
        with self._write() as conn:
            cursor = conn.execute(
                "UPDATE chunks SET chapter_code = ?, category = ?, tags_json = ?, label_status = ?, "
                "mapping_confidence = ?, edited_by = COALESCE(?, edited_by), edited_at = COALESCE(?, edited_at), "
                "version = version + 1 WHERE chunk_id = ? AND version = ?",
                (
                    chunk.chapter_code,
                    chunk.category.value,
                    json.dumps(chunk.tags, ensure_ascii=False),
                    chunk.label_status,
                    chunk.mapping_confidence,
                    edited_by,
                    edited_at,
                    chunk.chunk_id,
                    expected_version,
                ),
            )
            if cursor.rowcount == 0:
                raise _version_conflict(
                    conn,
                    "chunk",
                    chunk.chunk_id,
                    expected_version,
                    "SELECT version FROM chunks WHERE chunk_id = ?",
                    (chunk.chunk_id,),
                )
            conn.execute("DELETE FROM chunk_topics WHERE chunk_id = ?", (chunk.chunk_id,))
            conn.executemany(
                "INSERT INTO chunk_topics (chunk_id, topic_key) VALUES (?, ?)",
                [(chunk.chunk_id, topic_key) for topic_key in chunk.topic_keys],
            )
            conn.execute(
                "UPDATE concept_pairs SET chapter_code = ?, version = version + 1 "
                "WHERE chunk_id = ? AND chapter_code <> ?",
                (chunk.chapter_code, chunk.chunk_id, chunk.chapter_code),
            )
            subject = conn.execute("SELECT subject_code FROM chunks WHERE chunk_id = ?", (chunk.chunk_id,)).fetchone()
            self._relink(conn, {str(subject["subject_code"])})
            stored = self._load_chunks(conn, "WHERE chunk_id = ?", (chunk.chunk_id,))
        return stored[0]

    def set_chunk_exercise(self, chunk_id: str, is_exercise: bool | None, expected_version: int) -> KnowledgeChunk:
        """Một giao dịch: chỉ cột `is_exercise` và `version` (§5.8); nhãn khác và người sửa giữ nguyên."""
        with self._write() as conn:
            cursor = conn.execute(
                "UPDATE chunks SET is_exercise = ?, version = version + 1 WHERE chunk_id = ? AND version = ?",
                (None if is_exercise is None else int(is_exercise), chunk_id, expected_version),
            )
            if cursor.rowcount == 0:
                raise _version_conflict(
                    conn,
                    "chunk",
                    chunk_id,
                    expected_version,
                    "SELECT version FROM chunks WHERE chunk_id = ?",
                    (chunk_id,),
                )
            stored = self._load_chunks(conn, "WHERE chunk_id = ?", (chunk_id,))
        return stored[0]

    # ------------------------------------------------------------------ #
    # Cặp khái niệm (§5.8, ADR-007)
    # ------------------------------------------------------------------ #

    _PAIR_SELECT = (
        "SELECT p.pair_id, p.chunk_id, p.subject_code, p.chapter_code, p.term, p.definition_quote, "
        "p.term_spans_json, p.status, p.proposed_by, p.mapping_confidence, p.created_at, p.confirmed_at, p.version, "
        "p.pre_selected, p.failed_gates_json, c.raw_text FROM concept_pairs AS p LEFT JOIN chunks AS c ON c.chunk_id = p.chunk_id"
    )

    @staticmethod
    def _pair_model(row: sqlite3.Row) -> ConceptPair:
        return ConceptPair.model_validate(
            {
                "pair_id": row["pair_id"],
                "chunk_id": row["chunk_id"],
                "subject_code": row["subject_code"],
                "chapter_code": row["chapter_code"],
                "term": row["term"],
                "definition_quote": row["definition_quote"],
                "term_spans": json.loads(str(row["term_spans_json"])),
                "status": row["status"],
                "proposed_by": row["proposed_by"],
                "mapping_confidence": row["mapping_confidence"],
                "created_at": row["created_at"],
                "confirmed_at": row["confirmed_at"],
                "version": row["version"],
                "pre_selected": bool(row["pre_selected"]),
                "failed_gates": json.loads(str(row["failed_gates_json"])),
            }
        )

    def _pair_from_row(self, row: sqlite3.Row) -> ConceptPair | None:
        """Cặp của dòng `row`; `None` (kèm cảnh báo trong log) khi trích dẫn không khớp nguyên văn chunk (I3).

        Dòng như vậy chỉ có khi DB bị sửa ngoài app, vì mọi đường ghi đều kiểm I3. Bỏ qua thay vì ném lỗi: cặp vi
        phạm không bao giờ được trả ra, mà một dòng hỏng cũng không làm hỏng việc liệt kê cả môn. Log chỉ ghi
        `pair_id` và `chunk_id`, không ghi nội dung tài liệu.
        """
        pair = self._pair_model(row)
        raw_text = row["raw_text"]
        try:
            check_definition_quote(pair.chunk_id, "" if raw_text is None else str(raw_text), pair.definition_quote)
        except QuoteMismatchError:
            _LOGGER.warning(
                "bỏ qua cặp khái niệm %s: definition_quote không khớp nguyên văn chunk %s (I3)",
                pair.pair_id,
                pair.chunk_id,
            )
            return None
        return pair

    def _pairs_where(self, conn: sqlite3.Connection, where: str, params: Sequence[object]) -> list[ConceptPair]:
        rows = conn.execute(f"{self._PAIR_SELECT} {where}", params).fetchall()
        return [pair for pair in (self._pair_from_row(row) for row in rows) if pair is not None]

    def _check_quote(self, conn: sqlite3.Connection, chunk_id: str, definition_quote: str) -> None:
        """I3 trong giao dịch ghi đang mở; chunk không có ⇒ `MissingReferenceError`."""
        row = conn.execute("SELECT raw_text FROM chunks WHERE chunk_id = ?", (chunk_id,)).fetchone()
        if row is None:
            raise MissingReferenceError(f"không tìm thấy chunk {chunk_id}")
        check_definition_quote(chunk_id, str(row["raw_text"]), definition_quote)

    def add_concept_pair(self, pair: ConceptPair, edit: VaultEdit | None = None) -> ConceptPair:
        edited_by, edited_at = self._edit_columns(edit)
        with self._write() as conn:
            self._check_quote(conn, pair.chunk_id, pair.definition_quote)
            try:
                conn.execute(
                    "INSERT INTO concept_pairs (pair_id, chunk_id, subject_code, chapter_code, term, definition_quote, "
                    "term_spans_json, status, proposed_by, mapping_confidence, created_at, confirmed_at, version, "
                    "edited_by, edited_at, pre_selected, failed_gates_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        pair.pair_id,
                        pair.chunk_id,
                        pair.subject_code,
                        pair.chapter_code,
                        pair.term,
                        pair.definition_quote,
                        json.dumps(pair.term_spans),
                        pair.status.value,
                        pair.proposed_by,
                        pair.mapping_confidence,
                        _ts(pair.created_at),
                        None if pair.confirmed_at is None else _ts(pair.confirmed_at),
                        pair.version,
                        edited_by,
                        edited_at,
                        int(pair.pre_selected),
                        json.dumps(pair.failed_gates),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise _translate(exc, MissingReferenceError(f"không tìm thấy chunk {pair.chunk_id}")) from exc
        return pair

    def get_concept_pair(self, pair_id: str) -> ConceptPair | None:
        with self._read() as conn:
            found = self._pairs_where(conn, "WHERE p.pair_id = ?", (pair_id,))
        return found[0] if found else None

    def list_concept_pairs(
        self, subject_code: str, chapter_code: str | None = None, status: ConceptPairStatus | None = None
    ) -> list[ConceptPair]:
        clauses = ["p.subject_code = ?"]
        params: list[str] = [subject_code]
        if chapter_code is not None:
            clauses.append("p.chapter_code = ?")
            params.append(chapter_code)
        if status is not None:
            clauses.append("p.status = ?")
            params.append(status.value)
        with self._read() as conn:
            found = self._pairs_where(conn, f"WHERE {' AND '.join(clauses)}", params)
        # Sắp trong Python theo điểm mã Unicode, giống hệt bản in-memory (không phụ thuộc collation của SQLite).
        return sorted(found, key=lambda pair: (pair.chapter_code, pair.term, pair.pair_id))

    def update_concept_pair(
        self, pair: ConceptPair, expected_version: int, edit: VaultEdit | None = None
    ) -> ConceptPair:
        """So version, rồi kiểm I3 trên văn bản của chunk mà cặp ĐANG LƯU trỏ tới, rồi ghi; cùng một giao dịch."""
        edited_by, edited_at = self._edit_columns(edit)
        with self._write() as conn:
            current = conn.execute(
                "SELECT chunk_id, version FROM concept_pairs WHERE pair_id = ?", (pair.pair_id,)
            ).fetchone()
            if current is None:
                raise RecordNotFoundError("cặp khái niệm", pair.pair_id)
            if int(current["version"]) != expected_version:
                raise VersionConflictError("cặp khái niệm", pair.pair_id, expected_version, int(current["version"]))
            self._check_quote(conn, str(current["chunk_id"]), pair.definition_quote)
            cursor = conn.execute(
                "UPDATE concept_pairs SET term = ?, definition_quote = ?, term_spans_json = ?, status = ?, "
                "mapping_confidence = ?, confirmed_at = ?, edited_by = COALESCE(?, edited_by), "
                "edited_at = COALESCE(?, edited_at), version = version + 1 WHERE pair_id = ? AND version = ?",
                (
                    pair.term,
                    pair.definition_quote,
                    json.dumps(pair.term_spans),
                    pair.status.value,
                    pair.mapping_confidence,
                    None if pair.confirmed_at is None else _ts(pair.confirmed_at),
                    edited_by,
                    edited_at,
                    pair.pair_id,
                    expected_version,
                ),
            )
            if cursor.rowcount != 1:  # không xảy ra khi đang giữ khoá ghi; giữ để không báo thành công khi chưa ghi
                raise ConcurrentUpdateError("cặp khái niệm", pair.pair_id)
            # Vừa kiểm I3 trên đúng chunk này trong cùng giao dịch, nên đọc lại không cần lọc.
            row = conn.execute(f"{self._PAIR_SELECT} WHERE p.pair_id = ?", (pair.pair_id,)).fetchone()
        return self._pair_model(row)

    def get_last_edit(self, entity: VaultEntity, key: str) -> VaultEdit | None:
        table, column = _EDIT_TABLES[entity]
        with self._read() as conn:
            row = conn.execute(f"SELECT edited_by, edited_at FROM {table} WHERE {column} = ?", (key,)).fetchone()
        if row is None or row["edited_by"] is None:
            return None
        return VaultEdit(editor_id=str(row["edited_by"]), at=datetime.fromisoformat(str(row["edited_at"])))

    # ------------------------------------------------------------------ #
    # PracticeAttemptRepository
    # ------------------------------------------------------------------ #

    _ATTEMPT_SUMMARY_SELECT = (
        "SELECT a.attempt_id, a.subject_code, a.mode, a.batch_id, a.blueprint_id, a.submitted_at, "
        "COUNT(p.ordinal) AS total_count, "
        "COALESCE(SUM(p.outcome = 'correct'), 0) AS correct_count, "
        "COALESCE(SUM(p.outcome = 'incorrect'), 0) AS incorrect_count, "
        "COALESCE(SUM(p.outcome = 'skipped'), 0) AS skipped_count "
        "FROM practice_attempts a LEFT JOIN practice_answers p ON p.attempt_id = a.attempt_id "
    )
    _SUMMARY_COLUMNS = (
        "attempt_id",
        "subject_code",
        "mode",
        "batch_id",
        "blueprint_id",
        "submitted_at",
        "total_count",
        "correct_count",
        "incorrect_count",
        "skipped_count",
    )
    _ANSWER_COLUMNS = ("ordinal", "twin_id", "batch_id", "slot_index", "chosen_key", "correct_key", "outcome")
    _REVIEW_CTE = (
        "WITH ranked AS (SELECT p.twin_id, p.attempt_id, p.ordinal, p.chosen_key, p.outcome, a.seq, a.submitted_at, "
        "ROW_NUMBER() OVER (PARTITION BY p.twin_id ORDER BY a.seq DESC, p.ordinal DESC) AS rn "
        "FROM practice_answers p JOIN practice_attempts a ON a.attempt_id = p.attempt_id "
        "WHERE a.subject_code = ? AND p.outcome IN ('correct', 'incorrect')) "
    )

    def save_practice_attempt(self, attempt: PracticeAttempt) -> PracticeAttempt:
        """Một giao dịch `BEGIN IMMEDIATE`: cấp `seq` = `MAX(seq) + 1` rồi ghi lượt và mọi câu. Khoá ghi giữ suốt giao
        dịch nên hai lần ghi (kể cả từ hai tiến trình) không nhận cùng `seq`."""
        with self._write() as conn:
            row = conn.execute("SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq FROM practice_attempts").fetchone()
            missing = MissingReferenceError(f"lượt {attempt.attempt_id} trỏ tới batch hoặc twin không còn")
            try:
                conn.execute(
                    "INSERT INTO practice_attempts (attempt_id, seq, subject_code, mode, batch_id, blueprint_id, "
                    "submitted_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        attempt.attempt_id,
                        int(row["next_seq"]),
                        attempt.subject_code,
                        attempt.mode,
                        attempt.batch_id,
                        attempt.blueprint_id,
                        _ts(attempt.submitted_at),
                    ),
                )
                conn.executemany(
                    "INSERT INTO practice_answers (attempt_id, ordinal, twin_id, batch_id, slot_index, chosen_key, "
                    "correct_key, outcome) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        (
                            attempt.attempt_id,
                            answer.ordinal,
                            answer.twin_id,
                            answer.batch_id,
                            answer.slot_index,
                            None if answer.chosen_key is None else answer.chosen_key.value,
                            answer.correct_key.value,
                            answer.outcome,
                        )
                        for answer in attempt.answers
                    ],
                )
            except sqlite3.IntegrityError as exc:
                raise _translate(exc, missing) from exc
        return attempt

    @classmethod
    def _summary(cls, row: sqlite3.Row) -> PracticeAttemptSummary:
        return PracticeAttemptSummary.model_validate({column: row[column] for column in cls._SUMMARY_COLUMNS})

    def get_practice_attempt(self, attempt_id: str) -> PracticeAttempt | None:
        with self._read() as conn:
            row = conn.execute(
                "SELECT attempt_id, subject_code, mode, batch_id, blueprint_id, submitted_at FROM practice_attempts "
                "WHERE attempt_id = ?",
                (attempt_id,),
            ).fetchone()
            if row is None:
                return None
            answers = conn.execute(
                "SELECT ordinal, twin_id, batch_id, slot_index, chosen_key, correct_key, outcome FROM practice_answers "
                "WHERE attempt_id = ? ORDER BY ordinal",
                (attempt_id,),
            ).fetchall()
        return build_attempt(
            attempt_id=str(row["attempt_id"]),
            subject_code=str(row["subject_code"]),
            mode="batch" if row["mode"] == "batch" else "review",
            batch_id=None if row["batch_id"] is None else str(row["batch_id"]),
            blueprint_id=None if row["blueprint_id"] is None else str(row["blueprint_id"]),
            submitted_at=datetime.fromisoformat(str(row["submitted_at"])),
            answers=[
                PracticeAnswer.model_validate({column: answer[column] for column in self._ANSWER_COLUMNS})
                for answer in answers
            ],
        )

    def list_practice_attempts(self, subject_code: str, offset: int, limit: int) -> PracticeAttemptPage:
        with self._read() as conn:
            total = conn.execute(
                "SELECT COUNT(*) AS n FROM practice_attempts WHERE subject_code = ?", (subject_code,)
            ).fetchone()
            rows = conn.execute(
                f"{self._ATTEMPT_SUMMARY_SELECT}WHERE a.subject_code = ? GROUP BY a.attempt_id "
                "ORDER BY a.seq DESC LIMIT ? OFFSET ?",
                (subject_code, limit, offset),
            ).fetchall()
        return PracticeAttemptPage(total=int(total["n"]), attempts=[self._summary(row) for row in rows])

    def list_review_questions(self, subject_code: str, offset: int, limit: int) -> ReviewQuestionPage:
        with self._read() as conn:
            total = conn.execute(
                f"{self._REVIEW_CTE}SELECT COUNT(*) AS n FROM ranked WHERE rn = 1 AND outcome = 'incorrect'",
                (subject_code,),
            ).fetchone()
            rows = conn.execute(
                f"{self._REVIEW_CTE}SELECT r.attempt_id, r.chosen_key, r.submitted_at, t.batch_id, t.twin_json "
                "FROM ranked r JOIN twins t ON t.twin_id = r.twin_id WHERE r.rn = 1 AND r.outcome = 'incorrect' "
                "ORDER BY r.seq DESC, r.ordinal ASC LIMIT ? OFFSET ?",
                (subject_code, limit, offset),
            ).fetchall()
        questions = [
            ReviewQuestion.model_validate(
                {
                    "twin": _BATCH_TWIN.validate_json(str(row["twin_json"])),
                    "batch_id": str(row["batch_id"]),
                    "last_attempt_id": str(row["attempt_id"]),
                    "last_answered_at": datetime.fromisoformat(str(row["submitted_at"])),
                    "last_chosen_key": row["chosen_key"],
                }
            )
            for row in rows
        ]
        return ReviewQuestionPage(total=int(total["n"]), questions=questions)

    def delete_practice_attempt(self, attempt_id: str) -> bool:
        with self._write() as conn:
            cursor = conn.execute("DELETE FROM practice_attempts WHERE attempt_id = ?", (attempt_id,))
        return cursor.rowcount > 0

    def delete_practice_attempts(self, subject_code: str) -> int:
        with self._write() as conn:
            cursor = conn.execute("DELETE FROM practice_attempts WHERE subject_code = ?", (subject_code,))
        return cursor.rowcount

    # ------------------------------------------------------------------ #
    # AiCacheRepository (spec §5.16; ADR-009 mục 8)
    # ------------------------------------------------------------------ #

    @staticmethod
    def _unlink_ai_cache(conn: sqlite3.Connection, document_id: str) -> None:
        """Xoá liên kết của tài liệu rồi mọi dòng `ai_cache` không còn liên kết (gọi trong giao dịch của người gọi)."""
        conn.execute("DELETE FROM ai_cache_documents WHERE document_id = ?", (document_id,))
        conn.execute(
            "DELETE FROM ai_cache WHERE NOT EXISTS "
            "(SELECT 1 FROM ai_cache_documents AS link WHERE link.cache_key = ai_cache.cache_key)"
        )

    def get_ai_cache(self, cache_key: str, document_id: str, *, now: datetime) -> AiCacheEntry | None:
        with self._write() as conn:
            row = conn.execute(
                "SELECT cache_key, kind, input_sha256, model, prompt_version, result_json, last_used_at "
                "FROM ai_cache WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
            if row is None or row[6] < _ts(now - self._ai_cache_ttl):
                return None
            conn.execute("UPDATE ai_cache SET last_used_at = ? WHERE cache_key = ?", (_ts(now), cache_key))
            conn.execute(
                "INSERT OR IGNORE INTO ai_cache_documents (cache_key, document_id) VALUES (?, ?)",
                (cache_key, document_id),
            )
        return AiCacheEntry(
            cache_key=row[0], kind=row[1], input_sha256=row[2], model=row[3], prompt_version=row[4], result_json=row[5]
        )

    def put_ai_cache(self, entry: AiCacheEntry, document_id: str, *, now: datetime) -> None:
        stamp = _ts(now)
        with self._write() as conn:
            conn.execute(
                "INSERT INTO ai_cache (cache_key, kind, input_sha256, model, prompt_version, result_json, created_at, "
                "last_used_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (cache_key) DO UPDATE SET "
                "result_json = excluded.result_json, last_used_at = excluded.last_used_at",
                (
                    entry.cache_key,
                    entry.kind,
                    entry.input_sha256,
                    entry.model,
                    entry.prompt_version,
                    entry.result_json,
                    stamp,
                    stamp,
                ),
            )
            conn.execute(
                "INSERT OR IGNORE INTO ai_cache_documents (cache_key, document_id) VALUES (?, ?)",
                (entry.cache_key, document_id),
            )
            conn.execute("DELETE FROM ai_cache WHERE last_used_at < ?", (_ts(now - self._ai_cache_ttl),))
            conn.execute(
                "DELETE FROM ai_cache WHERE cache_key IN (SELECT cache_key FROM ai_cache "
                "ORDER BY last_used_at DESC, cache_key DESC LIMIT -1 OFFSET ?)",
                (self._ai_cache_max_rows,),
            )

    def drop_ai_cache_entry(self, cache_key: str) -> None:
        with self._write() as conn:
            conn.execute("DELETE FROM ai_cache WHERE cache_key = ?", (cache_key,))

    def clear_ai_cache(self) -> int:
        with self._write() as conn:
            deleted = conn.execute("DELETE FROM ai_cache").rowcount
            conn.execute("DELETE FROM ai_cache_documents")
        return int(deleted)

    # ------------------------------------------------------------------ #
    # BlockRepository (§5.13 migration khối; ADR-010 mục 13, 17, 19)
    # ------------------------------------------------------------------ #

    @staticmethod
    def _require_imported(conn: sqlite3.Connection, document_id: str) -> sqlite3.Row:
        row: sqlite3.Row | None = conn.execute(
            "SELECT document_id, source_kind FROM documents WHERE document_id = ? AND subject_code IS NOT NULL",
            (document_id,),
        ).fetchone()
        if row is None:
            raise RecordNotFoundError("tài liệu", document_id)
        return row

    @staticmethod
    def _require_page(conn: sqlite3.Connection, document_id: str, page_number: int) -> None:
        row = conn.execute(
            "SELECT 1 FROM pages WHERE document_id = ? AND page_number = ?", (document_id, page_number)
        ).fetchone()
        if row is None:
            raise RecordNotFoundError("trang", f"{document_id}#{page_number}")

    @staticmethod
    def _load_block(conn: sqlite3.Connection, document_id: str, block_id: str) -> Block:
        row = conn.execute(
            "SELECT block_json FROM blocks WHERE document_id = ? AND block_id = ?", (document_id, block_id)
        ).fetchone()
        if row is None:
            raise RecordNotFoundError("khối", f"{document_id}#{block_id}")
        return Block.model_validate_json(str(row["block_json"]))

    @staticmethod
    def _store_block(conn: sqlite3.Connection, block: Block) -> None:
        """Ghi lại `block_json` (và các cột lọc) của một khối đã có; cột `eligible` giữ nguyên."""
        conn.execute(
            "UPDATE blocks SET page_number = ?, reading_order = ?, type = ?, block_json = ? "
            "WHERE document_id = ? AND block_id = ?",
            (
                block.page_number,
                block.reading_order,
                block.type,
                block.model_dump_json(),
                block.document_id,
                block.block_id,
            ),
        )

    def get_page_geometry(self, document_id: str, page_number: int) -> PageGeometry | None:
        with self._read() as conn:
            row = conn.execute(
                "SELECT geometry_json FROM pages WHERE document_id = ? AND page_number = ?",
                (document_id, page_number),
            ).fetchone()
        if row is None or row["geometry_json"] is None:
            return None
        return PageGeometry.model_validate_json(str(row["geometry_json"]))

    def save_document_layout(self, document_id: str, layout: DocumentLayout) -> None:
        with self._lock:
            with self._write() as conn:
                self._require_imported(conn, document_id)
                for rewrite in layout.page_rewrites:
                    page = rewrite.page
                    if page.document_id != document_id:
                        raise ValueError("trang ghi lại không thuộc tài liệu")
                    cursor = conn.execute(
                        "UPDATE pages SET status = ?, extraction_json = ?, confirmed = ?, updated_at = ?, "
                        "version = version + 1 WHERE document_id = ? AND page_number = ? AND version = ?",
                        (
                            page.status,
                            _dump(page),
                            int(page.confirmed),
                            _now(),
                            document_id,
                            page.page_number,
                            rewrite.expected_version,
                        ),
                    )
                    if cursor.rowcount == 0:
                        raise _version_conflict(
                            conn,
                            "trang",
                            f"{document_id}#{page.page_number}",
                            rewrite.expected_version,
                            "SELECT version FROM pages WHERE document_id = ? AND page_number = ?",
                            (document_id, page.page_number),
                        )
                for page_layout in layout.pages:
                    number = page_layout.page_number
                    self._require_page(conn, document_id, number)
                    if any(block.document_id != document_id for block in page_layout.blocks) or (
                        page_layout.ledger is not None and page_layout.ledger.document_id != document_id
                    ):
                        raise ValueError("khối hoặc ledger không thuộc tài liệu")
                    conn.execute("DELETE FROM blocks WHERE document_id = ? AND page_number = ?", (document_id, number))
                    conn.executemany(
                        "INSERT INTO blocks (document_id, block_id, page_number, reading_order, type, eligible, "
                        "block_json) VALUES (?, ?, ?, ?, ?, 0, ?)",
                        [
                            (document_id, b.block_id, number, b.reading_order, b.type, b.model_dump_json())
                            for b in page_layout.blocks
                        ],
                    )
                    conn.execute(
                        "DELETE FROM extraction_ledgers WHERE document_id = ? AND page_number = ?",
                        (document_id, number),
                    )
                    if page_layout.ledger is not None:
                        conn.execute(
                            "INSERT INTO extraction_ledgers (document_id, page_number, balanced, ledger_json) "
                            "VALUES (?, ?, ?, ?)",
                            (
                                document_id,
                                number,
                                int(page_layout.ledger.balanced),
                                page_layout.ledger.model_dump_json(),
                            ),
                        )
                after_close = None if layout.blocks_after_close is None else int(layout.blocks_after_close)
                conn.execute(
                    "UPDATE documents SET layout_version = ?, layout_settings_sha256 = ?, "
                    "blocks_after_close = COALESCE(?, blocks_after_close) WHERE document_id = ?",
                    (layout.layout_version, layout.layout_settings_sha256, after_close, document_id),
                )
                stale_refs = self._drop_stale_crop_rows(conn, document_id)
            self._release_crop_files(document_id, stale_refs)

    @staticmethod
    def _drop_stale_crop_rows(conn: sqlite3.Connection, document_id: str) -> set[str]:
        """Xoá hàng ảnh cắt có `block_id` không còn trong `blocks`; trả các `crop_ref` của hàng đã xoá."""
        stale = "document_id = ? AND block_id NOT IN (SELECT block_id FROM blocks WHERE document_id = ?)"
        refs = {
            str(row["crop_ref"])
            for row in conn.execute(f"SELECT crop_ref FROM crop_images WHERE {stale}", (document_id, document_id))
        }
        conn.execute(f"DELETE FROM crop_images WHERE {stale}", (document_id, document_id))
        return refs

    def list_blocks(self, document_id: str, page_number: int | None = None) -> list[Block]:
        where: str = "document_id = ?"
        params: list[object] = [document_id]
        if page_number is not None:
            where, params = "document_id = ? AND page_number = ?", [document_id, page_number]
        return self._blocks_where(where, params)

    def _blocks_where(self, where: str, params: Sequence[object]) -> list[Block]:
        with self._read() as conn:
            rows = conn.execute(
                f"SELECT block_json FROM blocks WHERE {where} ORDER BY page_number, reading_order, block_id", params
            ).fetchall()
        return [Block.model_validate_json(str(row["block_json"])) for row in rows]

    def list_extraction_ledgers(self, document_id: str) -> list[ExtractionLedger]:
        with self._read() as conn:
            rows = conn.execute(
                "SELECT ledger_json FROM extraction_ledgers WHERE document_id = ? ORDER BY page_number",
                (document_id,),
            ).fetchall()
        return [ExtractionLedger.model_validate_json(str(row["ledger_json"])) for row in rows]

    def replace_sections(self, document_id: str, sections: Sequence[Section]) -> None:
        if any(section.document_id != document_id for section in sections):
            raise ValueError("Section không thuộc tài liệu")
        if len({section.section_id for section in sections}) != len(sections):
            raise ValueError("section_id trùng")
        with self._write() as conn:
            self._require_imported(conn, document_id)
            conn.execute("DELETE FROM sections WHERE document_id = ?", (document_id,))
            conn.executemany(
                "INSERT INTO sections (document_id, section_id, parent_id, ordinal, section_json) "
                "VALUES (?, ?, ?, ?, ?)",
                [(document_id, s.section_id, s.parent_id, s.ordinal, s.model_dump_json()) for s in sections],
            )
            for block_id, level in heading_levels(sections):
                block = self._load_block(conn, document_id, block_id)
                if block.type != "heading":
                    raise ValueError(f"khối tiêu đề {block_id} không phải heading")
                if block.level != level:
                    self._store_block(conn, block.model_copy(update={"level": level}))

    def list_sections(self, document_id: str) -> list[Section]:
        with self._read() as conn:
            rows = conn.execute(
                "SELECT section_json FROM sections WHERE document_id = ? ORDER BY ordinal, section_id", (document_id,)
            ).fetchall()
        return [Section.model_validate_json(str(row["section_json"])) for row in rows]

    def update_imported_document_and_root_section(
        self, document: ImportedDocument, expected_version: int
    ) -> ImportedDocument:
        document_id = document.meta.doc_id
        with self._write() as conn:
            updated = self._update_imported_row(conn, document, expected_version)
            row = conn.execute(
                "SELECT section_json FROM sections WHERE document_id = ? AND parent_id IS NULL", (document_id,)
            ).fetchone()
            if row is not None:
                root = with_root_chapter(
                    Section.model_validate_json(str(row["section_json"])), document.chapter_code, document.chapter_title
                )
                conn.execute(
                    "UPDATE sections SET section_json = ? WHERE document_id = ? AND section_id = ?",
                    (root.model_dump_json(), document_id, root.section_id),
                )
        return updated

    def update_block_flags(self, document_id: str, updates: Sequence[BlockFlagUpdate]) -> list[Block]:
        written: list[Block] = []
        with self._write() as conn:
            for update in updates:
                block = with_flags(self._load_block(conn, document_id, update.block_id), update)
                self._store_block(conn, block)
                written.append(block)
        return written

    def save_structure_proposal(self, record: StructureProposalRecord) -> None:
        with self._write() as conn:
            self._require_page(conn, record.document_id, record.page_number)
            conn.execute(
                "INSERT INTO structure_proposals (document_id, page_number, input_sha256, model, prompt_version, "
                "status, ops_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (document_id, page_number) DO UPDATE SET input_sha256 = excluded.input_sha256, "
                "model = excluded.model, prompt_version = excluded.prompt_version, status = excluded.status, "
                "ops_json = excluded.ops_json, created_at = excluded.created_at",
                (
                    record.document_id,
                    record.page_number,
                    record.input_sha256,
                    record.model,
                    record.prompt_version,
                    record.status,
                    record.ops_json,
                    _ts(record.created_at),
                ),
            )

    def list_structure_proposals(self, document_id: str) -> list[StructureProposalRecord]:
        with self._read() as conn:
            rows = conn.execute(
                "SELECT document_id, page_number, input_sha256, model, prompt_version, status, ops_json, created_at "
                "FROM structure_proposals WHERE document_id = ? ORDER BY page_number",
                (document_id,),
            ).fetchall()
        return [StructureProposalRecord.model_validate(dict(row)) for row in rows]

    def refresh_eligible_blocks(self, document_id: str) -> int:
        with self._write() as conn:
            source_kind = SourceKind(self._require_imported(conn, document_id)["source_kind"])
            pages = {page.page_number: page for page in self._page_summaries(conn, document_id)}
            rows = conn.execute("SELECT block_json FROM blocks WHERE document_id = ?", (document_id,)).fetchall()
            flags: list[tuple[int, str, str]] = []
            for row in rows:
                block = Block.model_validate_json(str(row["block_json"]))
                eligible = is_candidate_block(block, pages[block.page_number], source_kind)
                flags.append((int(eligible), document_id, block.block_id))
            conn.executemany("UPDATE blocks SET eligible = ? WHERE document_id = ? AND block_id = ?", flags)
        return sum(eligible for eligible, _, _ in flags)

    def list_eligible_blocks(self, document_id: str) -> list[Block]:
        return self._blocks_where("document_id = ? AND eligible = 1", [document_id])

    def save_crop_image(self, crop: CropImage, png: bytes) -> CropWriteResult:
        check_crop_bytes(crop, png)
        document_id = crop.document_id
        replaced: set[str] = set()
        with self._lock:
            with self._write() as conn:
                block = self._load_block(conn, document_id, crop.block_id)
                if block.page_number != crop.page_number:
                    raise ValueError("ảnh cắt khác trang của khối")
                if self._crops is None:
                    self._store_block(conn, capped(block))
                    return "capped"
                previous = conn.execute(
                    "SELECT crop_ref FROM crop_images WHERE document_id = ? AND block_id = ?",
                    (document_id, crop.block_id),
                ).fetchone()
                if previous is not None and previous["crop_ref"] != crop.crop_ref:
                    replaced.add(str(previous["crop_ref"]))
                self._crops.write(document_id, crop.crop_ref, png)  # file TRƯỚC commit hàng (mồ côi dọn khi mở kho)
                conn.execute(
                    "INSERT INTO crop_images (document_id, block_id, crop_ref, page_number, bbox_json, width_px, "
                    "height_px, size_bytes, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT (document_id, block_id) DO UPDATE SET crop_ref = excluded.crop_ref, "
                    "page_number = excluded.page_number, bbox_json = excluded.bbox_json, "
                    "width_px = excluded.width_px, height_px = excluded.height_px, "
                    "size_bytes = excluded.size_bytes, created_at = excluded.created_at",
                    (
                        document_id,
                        crop.block_id,
                        crop.crop_ref,
                        crop.page_number,
                        json.dumps(list(crop.bbox)),
                        crop.width_px,
                        crop.height_px,
                        crop.size_bytes,
                        _now(),
                    ),
                )
                self._store_block(conn, cropped(block, crop.crop_ref))
            self._release_crop_files(document_id, replaced)
        return "stored"

    def list_crop_images(self, document_id: str) -> list[CropImage]:
        with self._read() as conn:
            rows = conn.execute(
                "SELECT document_id, block_id, crop_ref, page_number, bbox_json, width_px, height_px, size_bytes "
                "FROM crop_images WHERE document_id = ? ORDER BY block_id",
                (document_id,),
            ).fetchall()
        return [
            CropImage.model_validate(
                {
                    "document_id": row["document_id"],
                    "block_id": row["block_id"],
                    "crop_ref": row["crop_ref"],
                    "page_number": row["page_number"],
                    "bbox": json.loads(str(row["bbox_json"])),
                    "width_px": row["width_px"],
                    "height_px": row["height_px"],
                    "size_bytes": row["size_bytes"],
                }
            )
            for row in rows
        ]

    def get_crop_bytes(self, document_id: str, crop_ref: str) -> bytes | None:
        if self._crops is None:
            return None
        with self._lock:
            with self._read() as conn:
                row = conn.execute(
                    "SELECT 1 FROM crop_images WHERE document_id = ? AND crop_ref = ?", (document_id, crop_ref)
                ).fetchone()
            return None if row is None else self._crops.read(document_id, crop_ref)

    def _release_crop_files(self, document_id: str, crop_refs: set[str]) -> None:
        """SAU commit: xoá file của các `crop_ref` không còn hàng `crop_images` nào của tài liệu (đếm bằng truy vấn,
        dưới khoá kho để không chen với một lần ghi ảnh)."""
        if self._crops is None or not crop_refs:
            return
        with self._lock:
            with self._read() as conn:
                unused = [
                    ref
                    for ref in sorted(crop_refs)
                    if conn.execute(
                        "SELECT 1 FROM crop_images WHERE document_id = ? AND crop_ref = ?", (document_id, ref)
                    ).fetchone()
                    is None
                ]
            for ref in unused:
                self._crops.delete(document_id, ref)

    def _delete_crop_dir(self, document_id: str) -> None:
        """SAU commit xoá tài liệu: xoá thư mục ảnh `crops/<document_id>/`."""
        if self._crops is not None:
            self._crops.delete_document(document_id)

    def _sweep_orphan_crops(self) -> None:
        """Lúc mở kho: dọn file/thư mục ảnh không có hàng `crop_images` tương ứng (không có thư mục ảnh ⇒ bỏ qua)."""
        if self._crops is None:
            return
        keep: dict[str, set[str]] = {}
        with self._read() as conn:
            for row in conn.execute("SELECT document_id, crop_ref FROM crop_images"):
                keep.setdefault(str(row["document_id"]), set()).add(str(row["crop_ref"]))
        self._crops.sweep_orphans(keep)
