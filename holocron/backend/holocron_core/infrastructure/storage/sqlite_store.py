"""Kho SQLite (SPECIFICATION §5.13): thoả `AppStore`, `DocumentRepository` và `VaultRepository`.

Một kết nối cho mỗi kho, dùng chung giữa các luồng dưới một khoá; mọi lần ghi nằm trong `BEGIN IMMEDIATE`, mọi lần
đọc nhiều câu nằm trong một giao dịch đọc để thấy cùng một bản chụp. Ghi có điều kiện dùng một câu
`UPDATE … WHERE <khoá> = ? AND version = ?`; `rowcount = 0` mà bản ghi vẫn tồn tại ⇒ `VersionConflictError` (BE-003).
Riêng `ingestion_items` so thêm toàn bộ trạng thái đã đọc (xem `update_item`). Khoá bận quá `busy_timeout` ⇒
`StorageBusyError`.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel

from ...domain.ingestion import IngestionItem, PatchAudit, UploadedDocument
from ...domain.repositories import (
    IDEMPOTENCY_TTL,
    ConcurrentUpdateError,
    ImportedDocument,
    MissingReferenceError,
    PageStatus,
    RecordInUseError,
    RecordNotFoundError,
    SearchIndex,
    StorageBusyError,
    StorageError,
    VaultSearchUnavailableError,
    VersionConflictError,
    same_item_state,
)
from ...domain.schemas import DocumentMeta, ExamBlueprint, KnowledgeChunk, TwinQuestion
from ...domain.twins import IdempotencyRecord, ReviewTicket, TwinBatch
from ...domain.vault import ConceptPair, ConceptPairStatus
from .connection import connect, fts5_available, immediate_transaction, is_busy, read_transaction
from .migrator import load_fts5_script, migrate

IMPORTED_MEDIA_TYPE = "application/pdf"  # bộ nhập §6.5 chỉ nhận PDF trong MVP
_FTS_TABLES: dict[SearchIndex, str] = {"primary": "chunks_fts", "no_diacritics": "chunks_fts_nodiac"}


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

    def __init__(self, conn: sqlite3.Connection) -> None:
        """Nhận kết nối đã mở bằng `connection.connect`, chạy migration và tạo chỉ mục FTS5 nếu SQLite có FTS5.

        DB có version mới hơn code ⇒ `SchemaVersionError` (từ chối khởi động).
        """
        self._conn = conn
        self._lock = threading.RLock()
        with self._lock:
            migrate(conn)
            self._fts5 = fts5_available(conn)
            if self._fts5:
                conn.executescript(load_fts5_script())

    @classmethod
    def open(cls, path: str | Path) -> SQLiteStore:
        conn = connect(path)
        try:
            return cls(conn)
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

    def save_blueprint(self, blueprint: ExamBlueprint, item_ids: Sequence[str] = ()) -> ExamBlueprint:
        with self._write() as conn:
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

    # ------------------------------------------------------------------ #
    # TwinBatchRepository
    # ------------------------------------------------------------------ #

    def get_idempotent(self, blueprint_id: str, key: str, now: datetime) -> IdempotencyRecord | None:
        with self._read() as conn:
            row = conn.execute(
                "SELECT batch_id, base_seed, body_digest, created_at FROM twin_batches "
                "WHERE blueprint_id = ? AND idempotency_key = ?",
                (blueprint_id, key),
            ).fetchone()
            if row is None:
                return None
            stored_at = datetime.fromisoformat(str(row["created_at"]))
            if now - stored_at > IDEMPOTENCY_TTL:
                return None
            twins = conn.execute(
                "SELECT twin_json FROM twins WHERE batch_id = ? ORDER BY ordinal", (row["batch_id"],)
            ).fetchall()
            tickets = conn.execute(
                "SELECT ticket_json FROM review_tickets WHERE batch_id = ? ORDER BY ticket_id", (row["batch_id"],)
            ).fetchall()
        batch = TwinBatch(
            blueprint_id=blueprint_id,
            base_seed=int(row["base_seed"]),
            twins=[TwinQuestion.model_validate_json(str(twin["twin_json"])) for twin in twins],
            review_tickets=[ReviewTicket.model_validate_json(str(ticket["ticket_json"])) for ticket in tickets],
        )
        return IdempotencyRecord(body_digest=str(row["body_digest"]), stored_at=stored_at, batch=batch)

    def save_idempotent(self, blueprint_id: str, key: str, record: IdempotencyRecord) -> None:
        """Dọn khoá rồi ghi batch mới trong cùng giao dịch, để giữ index UNIQUE `(blueprint_id, idempotency_key)`.

        Khoá hết hạn (của mọi blueprint, tính tới `record.stored_at`) và khoá cũ trùng `(blueprint, key)` được đặt
        `NULL`; batch cũ vẫn còn trong bảng nhưng không tra theo khoá được nữa (cùng hành vi ghi đè của bản in-memory).
        `request_json` để `NULL` vì bản ghi idempotency chỉ mang `body_digest`.
        """
        batch_id = f"tb_{uuid4().hex[:16]}"
        cutoff = _ts(record.stored_at - IDEMPOTENCY_TTL)
        with self._write() as conn:
            conn.execute(
                "UPDATE twin_batches SET idempotency_key = NULL "
                "WHERE idempotency_key IS NOT NULL AND (created_at < ? OR (blueprint_id = ? AND idempotency_key = ?))",
                (cutoff, blueprint_id, key),
            )
            try:
                conn.execute(
                    "INSERT INTO twin_batches (batch_id, blueprint_id, base_seed, request_json, body_digest, "
                    "idempotency_key, created_at) VALUES (?, ?, ?, NULL, ?, ?, ?)",
                    (batch_id, blueprint_id, record.batch.base_seed, record.body_digest, key, _ts(record.stored_at)),
                )
            except sqlite3.IntegrityError as exc:
                raise _translate(exc, MissingReferenceError(f"không tìm thấy blueprint {blueprint_id}")) from exc
            conn.executemany(
                "INSERT INTO twins (twin_id, batch_id, slot_index, ordinal, twin_json) VALUES (?, ?, ?, ?, ?)",
                [
                    (twin.twin_id, batch_id, twin.slot_index, ordinal, _dump(twin))
                    for ordinal, twin in enumerate(record.batch.twins)
                ],
            )
            conn.executemany(
                "INSERT INTO review_tickets (batch_id, slot_index, ticket_json) VALUES (?, ?, ?)",
                [(batch_id, ticket.slot_index, _dump(ticket)) for ticket in record.batch.review_tickets],
            )

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
        )

    def save_imported_document(self, document: ImportedDocument) -> ImportedDocument:
        with self._write() as conn:
            conn.execute(
                "INSERT INTO documents (filename, media_type, size_bytes, subject_code, title, source_kind, "
                "output_language, page_count, has_text_layer, status, uploaded_at, document_id, sha256) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (document_id) DO UPDATE SET filename = excluded.filename, "
                "media_type = excluded.media_type, size_bytes = excluded.size_bytes, "
                "subject_code = excluded.subject_code, title = excluded.title, source_kind = excluded.source_kind, "
                "output_language = excluded.output_language, page_count = excluded.page_count, "
                "has_text_layer = excluded.has_text_layer, status = excluded.status, "
                "uploaded_at = excluded.uploaded_at, version = documents.version + 1",
                (*self._imported_columns(document), document.meta.doc_id, document.meta.sha256),
            )
            row = conn.execute(f"{self._IMPORTED_SELECT} AND document_id = ?", (document.meta.doc_id,)).fetchone()
        return self._imported_from_row(row)

    _IMPORTED_SELECT = (
        "SELECT document_id, sha256, filename, size_bytes, subject_code, title, source_kind, output_language, "
        "page_count, has_text_layer, status, uploaded_at, version FROM documents WHERE subject_code IS NOT NULL"
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
                "status": row["status"],
                "version": row["version"],
            }
        )

    def get_imported_document(self, document_id: str) -> ImportedDocument | None:
        with self._read() as conn:
            row = conn.execute(f"{self._IMPORTED_SELECT} AND document_id = ?", (document_id,)).fetchone()
        return None if row is None else self._imported_from_row(row)

    def update_imported_document(self, document: ImportedDocument, expected_version: int) -> ImportedDocument:
        document_id = document.meta.doc_id
        with self._write() as conn:
            cursor = conn.execute(
                "UPDATE documents SET filename = ?, media_type = ?, size_bytes = ?, subject_code = ?, title = ?, "
                "source_kind = ?, output_language = ?, page_count = ?, has_text_layer = ?, status = ?, "
                "uploaded_at = ?, version = version + 1 WHERE document_id = ? AND version = ?",
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
        return document.model_copy(update={"version": expected_version + 1})

    def list_imported_documents(self, subject_code: str | None = None) -> list[ImportedDocument]:
        order = " ORDER BY uploaded_at DESC, document_id DESC"
        with self._read() as conn:
            if subject_code is None:
                rows = conn.execute(self._IMPORTED_SELECT + order).fetchall()
            else:
                rows = conn.execute(f"{self._IMPORTED_SELECT} AND subject_code = ?{order}", (subject_code,)).fetchall()
        return [self._imported_from_row(row) for row in rows]

    def delete_document(self, document_id: str) -> bool:
        with self._write() as conn:
            if conn.execute("SELECT 1 FROM documents WHERE document_id = ?", (document_id,)).fetchone() is None:
                return False
            if self._fts5:
                for table in _FTS_TABLES.values():
                    conn.execute(
                        f"DELETE FROM {table} WHERE chunk_id IN (SELECT chunk_id FROM chunks WHERE document_id = ?)",
                        (document_id,),
                    )
            try:
                conn.execute("DELETE FROM documents WHERE document_id = ?", (document_id,))
            except sqlite3.IntegrityError as exc:
                in_use = RecordInUseError(f"tài liệu {document_id} còn được ingestion item tham chiếu")
                raise _translate(exc, in_use) from exc
        return True

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
    ) -> int:
        with self._write() as conn:
            cursor = conn.execute(
                "UPDATE pages SET status = ?, extraction_json = ?, confirmed = ?, updated_at = ?, "
                "version = version + 1 WHERE document_id = ? AND page_number = ? AND version = ?",
                (status, _dump(page), int(confirmed), _now(), document_id, page_number, expected_version),
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
        return expected_version + 1

    def get_page[M: BaseModel](self, document_id: str, page_number: int, model: type[M]) -> M | None:
        with self._read() as conn:
            row = conn.execute(
                "SELECT extraction_json FROM pages WHERE document_id = ? AND page_number = ?",
                (document_id, page_number),
            ).fetchone()
        return None if row is None else model.model_validate_json(str(row["extraction_json"]))

    def list_pages[M: BaseModel](self, document_id: str, model: type[M]) -> list[M]:
        with self._read() as conn:
            rows = conn.execute(
                "SELECT extraction_json FROM pages WHERE document_id = ? ORDER BY page_number", (document_id,)
            ).fetchall()
        return [model.model_validate_json(str(row["extraction_json"])) for row in rows]

    # ------------------------------------------------------------------ #
    # VaultRepository
    # ------------------------------------------------------------------ #

    def add_chunk(self, chunk: KnowledgeChunk, search_body: str) -> None:
        if chunk.embedding_model is not None:
            raise ValueError("bảng chunks chưa có cột embedding_model (luôn null trong MVP, ADR-006 mục 4)")
        with self._write() as conn:
            try:
                conn.execute(
                    "INSERT INTO chunks (chunk_id, document_id, subject_code, chapter_code, category, page_number, "
                    "page_end, raw_text, verbatim_quote, tags_json, linked_chunk_ids_json, token_count, label_status, "
                    "mapping_confidence, version) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise self._missing_document(exc, chunk.doc_id) from exc
            conn.executemany(
                "INSERT INTO chunk_topics (chunk_id, topic_key) VALUES (?, ?)",
                [(chunk.chunk_id, topic_key) for topic_key in chunk.topic_keys],
            )
            if self._fts5:
                for table in _FTS_TABLES.values():
                    conn.execute(
                        f"INSERT INTO {table} (body, chunk_id, subject_code) VALUES (?, ?, ?)",
                        (search_body, chunk.chunk_id, chunk.subject_code),
                    )

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
            }
        )

    _CHUNK_SELECT = (
        "SELECT chunk_id, document_id, subject_code, chapter_code, category, page_number, page_end, raw_text, "
        "verbatim_quote, tags_json, linked_chunk_ids_json, token_count, label_status, mapping_confidence, version "
        "FROM chunks"
    )

    def get_chunk(self, chunk_id: str) -> KnowledgeChunk | None:
        with self._read() as conn:
            row = conn.execute(f"{self._CHUNK_SELECT} WHERE chunk_id = ?", (chunk_id,)).fetchone()
            topics = conn.execute(
                "SELECT topic_key FROM chunk_topics WHERE chunk_id = ? ORDER BY topic_key", (chunk_id,)
            ).fetchall()
        return None if row is None else self._chunk_from_row(row, [str(topic["topic_key"]) for topic in topics])

    def list_chunks(self, subject_code: str, chapter_code: str | None = None) -> list[KnowledgeChunk]:
        where = "WHERE subject_code = ?" + ("" if chapter_code is None else " AND chapter_code = ?")
        params: tuple[str, ...] = (subject_code,) if chapter_code is None else (subject_code, chapter_code)
        with self._read() as conn:
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

    def update_chunk_labels(self, chunk: KnowledgeChunk, expected_version: int) -> KnowledgeChunk:
        with self._write() as conn:
            cursor = conn.execute(
                "UPDATE chunks SET chapter_code = ?, category = ?, tags_json = ?, label_status = ?, "
                "mapping_confidence = ?, version = version + 1 WHERE chunk_id = ? AND version = ?",
                (
                    chunk.chapter_code,
                    chunk.category.value,
                    json.dumps(chunk.tags, ensure_ascii=False),
                    chunk.label_status,
                    chunk.mapping_confidence,
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
            row = conn.execute(f"{self._CHUNK_SELECT} WHERE chunk_id = ?", (chunk.chunk_id,)).fetchone()
        return self._chunk_from_row(row, list(chunk.topic_keys))

    # ------------------------------------------------------------------ #
    # Cặp khái niệm (§5.8, ADR-007)
    # ------------------------------------------------------------------ #

    _PAIR_SELECT = (
        "SELECT pair_id, chunk_id, subject_code, chapter_code, term, definition_quote, term_spans_json, status, "
        "proposed_by, mapping_confidence, created_at, confirmed_at, version FROM concept_pairs"
    )

    @staticmethod
    def _pair_from_row(row: sqlite3.Row) -> ConceptPair:
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
            }
        )

    def add_concept_pair(self, pair: ConceptPair) -> ConceptPair:
        with self._write() as conn:
            try:
                conn.execute(
                    "INSERT INTO concept_pairs (pair_id, chunk_id, subject_code, chapter_code, term, definition_quote, "
                    "term_spans_json, status, proposed_by, mapping_confidence, created_at, confirmed_at, version) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise _translate(exc, MissingReferenceError(f"không tìm thấy chunk {pair.chunk_id}")) from exc
        return pair

    def get_concept_pair(self, pair_id: str) -> ConceptPair | None:
        with self._read() as conn:
            row = conn.execute(f"{self._PAIR_SELECT} WHERE pair_id = ?", (pair_id,)).fetchone()
        return None if row is None else self._pair_from_row(row)

    def list_concept_pairs(
        self, subject_code: str, chapter_code: str | None = None, status: ConceptPairStatus | None = None
    ) -> list[ConceptPair]:
        clauses = ["subject_code = ?"]
        params: list[str] = [subject_code]
        if chapter_code is not None:
            clauses.append("chapter_code = ?")
            params.append(chapter_code)
        if status is not None:
            clauses.append("status = ?")
            params.append(status.value)
        with self._read() as conn:
            rows = conn.execute(f"{self._PAIR_SELECT} WHERE {' AND '.join(clauses)}", params).fetchall()
        # Sắp trong Python theo điểm mã Unicode, giống hệt bản in-memory (không phụ thuộc collation của SQLite).
        return sorted(
            (self._pair_from_row(row) for row in rows), key=lambda pair: (pair.chapter_code, pair.term, pair.pair_id)
        )

    def update_concept_pair(self, pair: ConceptPair, expected_version: int) -> ConceptPair:
        with self._write() as conn:
            cursor = conn.execute(
                "UPDATE concept_pairs SET term = ?, definition_quote = ?, term_spans_json = ?, status = ?, "
                "mapping_confidence = ?, confirmed_at = ?, version = version + 1 WHERE pair_id = ? AND version = ?",
                (
                    pair.term,
                    pair.definition_quote,
                    json.dumps(pair.term_spans),
                    pair.status.value,
                    pair.mapping_confidence,
                    None if pair.confirmed_at is None else _ts(pair.confirmed_at),
                    pair.pair_id,
                    expected_version,
                ),
            )
            if cursor.rowcount == 0:
                raise _version_conflict(
                    conn,
                    "cặp khái niệm",
                    pair.pair_id,
                    expected_version,
                    "SELECT version FROM concept_pairs WHERE pair_id = ?",
                    (pair.pair_id,),
                )
            row = conn.execute(f"{self._PAIR_SELECT} WHERE pair_id = ?", (pair.pair_id,)).fetchone()
        return self._pair_from_row(row)
