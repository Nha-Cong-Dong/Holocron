"""Kho in-memory cho item, tài liệu, blueprint, bản ghi idempotency và Knowledge Vault.

Thoả `domain.repositories.AppStore`; dùng cho test và chế độ dev (không đặt `HOLOCRON_DATA_DIR`). Bản lưu lâu dài
là `infrastructure/storage/` (SQLite, SPECIFICATION §5.13). Bản này không có FTS5 (`vault_search_available = False`),
nên tìm kiếm Vault luôn dùng BM25 tự cài ở tầng service (ADR-006 mục 4).
"""

from __future__ import annotations

import threading
from collections.abc import Sequence
from datetime import datetime

from ..domain.ingestion import IngestionItem, UploadedDocument
from ..domain.repositories import (
    IDEMPOTENCY_TTL,
    ConcurrentUpdateError,
    MissingReferenceError,
    RecordNotFoundError,
    SearchIndex,
    StorageError,
    VaultSearchUnavailableError,
    VersionConflictError,
    same_item_state,
)
from ..domain.schemas import ExamBlueprint, KnowledgeChunk
from ..domain.twins import IdempotencyRecord
from ..domain.vault import ConceptPair, ConceptPairStatus

__all__ = ["IDEMPOTENCY_TTL", "IngestionStore"]


class IngestionStore:
    """Kho in-memory cho item và tài liệu tải lên; được cấp qua `Depends(get_store)` để test thay được."""

    def __init__(self) -> None:
        self._items: dict[str, IngestionItem] = {}
        self._documents: dict[str, UploadedDocument] = {}
        self._blueprints: dict[str, ExamBlueprint] = {}
        self._blueprint_items: dict[str, tuple[str, ...]] = {}
        self._idempotency: dict[tuple[str, str], IdempotencyRecord] = {}
        self._chunks: dict[str, KnowledgeChunk] = {}
        self._pairs: dict[str, ConceptPair] = {}
        self._lock = threading.Lock()

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
        return self._documents.setdefault(document.document_id, document)

    def get_document(self, document_id: str) -> UploadedDocument | None:
        return self._documents.get(document_id)

    def save_blueprint(self, blueprint: ExamBlueprint, item_ids: Sequence[str] = ()) -> ExamBlueprint:
        """Lưu blueprint kèm danh sách item đã được duyệt mà nó được dựng từ (để kiểm lại I5 khi sinh twin)."""
        self._blueprints[blueprint.blueprint_id] = blueprint
        self._blueprint_items[blueprint.blueprint_id] = tuple(item_ids)
        return blueprint

    def get_blueprint(self, blueprint_id: str) -> ExamBlueprint | None:
        return self._blueprints.get(blueprint_id)

    def blueprint_item_ids(self, blueprint_id: str) -> tuple[str, ...]:
        return self._blueprint_items.get(blueprint_id, ())

    def get_idempotent(self, blueprint_id: str, key: str, now: datetime) -> IdempotencyRecord | None:
        """Bản ghi còn hiệu lực (< 24 h) của `(blueprint, key)`; hết hạn thì coi như không có."""
        record = self._idempotency.get((blueprint_id, key))
        if record is None or now - record.stored_at > IDEMPOTENCY_TTL:
            return None
        return record

    def save_idempotent(self, blueprint_id: str, key: str, record: IdempotencyRecord) -> None:
        self._idempotency[(blueprint_id, key)] = record

    # ------------------------------------------------------------------ #
    # VaultRepository
    # ------------------------------------------------------------------ #

    @property
    def vault_search_available(self) -> bool:
        return False

    def add_chunk(self, chunk: KnowledgeChunk, search_body: str) -> None:
        """Như bản SQLite: tài liệu phải tồn tại, `chunk_id` không trùng; `search_body` không dùng (không có FTS5)."""
        if chunk.embedding_model is not None:
            raise ValueError("kho chưa lưu embedding_model (luôn null trong MVP, ADR-006 mục 4)")
        with self._lock:
            if chunk.doc_id not in self._documents:
                raise MissingReferenceError(f"không tìm thấy tài liệu nguồn {chunk.doc_id}")
            if chunk.chunk_id in self._chunks:
                raise StorageError(f"chunk {chunk.chunk_id} đã tồn tại")
            self._chunks[chunk.chunk_id] = chunk.model_copy(deep=True)

    def get_chunk(self, chunk_id: str) -> KnowledgeChunk | None:
        chunk = self._chunks.get(chunk_id)
        return None if chunk is None else chunk.model_copy(deep=True)

    def list_chunks(self, subject_code: str, chapter_code: str | None = None) -> list[KnowledgeChunk]:
        found = [
            chunk.model_copy(deep=True)
            for chunk in self._chunks.values()
            if chunk.subject_code == subject_code and (chapter_code is None or chunk.chapter_code == chapter_code)
        ]
        return sorted(found, key=lambda chunk: (chunk.doc_id, chunk.page_number, chunk.chunk_id))

    def match_chunk_ids(self, subject_code: str, fts_query: str, index: SearchIndex = "primary") -> list[str]:
        raise VaultSearchUnavailableError("kho in-memory không có FTS5; dùng BM25 tự cài (ADR-006 mục 4)")

    def update_chunk_labels(self, chunk: KnowledgeChunk, expected_version: int) -> KnowledgeChunk:
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
            self._chunks[chunk.chunk_id] = stored
        return stored.model_copy(deep=True)

    def add_concept_pair(self, pair: ConceptPair) -> ConceptPair:
        with self._lock:
            if pair.chunk_id not in self._chunks:
                raise MissingReferenceError(f"không tìm thấy chunk {pair.chunk_id}")
            if pair.pair_id in self._pairs:
                raise StorageError(f"cặp khái niệm {pair.pair_id} đã tồn tại")
            self._pairs[pair.pair_id] = pair.model_copy(deep=True)
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

    def update_concept_pair(self, pair: ConceptPair, expected_version: int) -> ConceptPair:
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
            self._pairs[pair.pair_id] = stored
        return stored.model_copy(deep=True)
