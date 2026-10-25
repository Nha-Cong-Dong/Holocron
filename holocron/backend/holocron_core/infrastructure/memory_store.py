"""Kho in-memory cho item, tài liệu, blueprint và bản ghi idempotency.

Thoả `domain.repositories.AppStore`; dùng cho test và chế độ dev (không đặt `HOLOCRON_DATA_DIR`). Bản lưu lâu dài
là `infrastructure/storage/` (SQLite, SPECIFICATION §5.13).
"""

from __future__ import annotations

import threading
from collections.abc import Sequence
from datetime import datetime

from ..domain.ingestion import IngestionItem, UploadedDocument
from ..domain.repositories import (
    IDEMPOTENCY_TTL,
    ConcurrentUpdateError,
    RecordNotFoundError,
    VersionConflictError,
    same_item_state,
)
from ..domain.schemas import ExamBlueprint
from ..domain.twins import IdempotencyRecord

__all__ = ["IDEMPOTENCY_TTL", "IngestionStore"]


class IngestionStore:
    """Kho in-memory cho item và tài liệu tải lên; được cấp qua `Depends(get_store)` để test thay được."""

    def __init__(self) -> None:
        self._items: dict[str, IngestionItem] = {}
        self._documents: dict[str, UploadedDocument] = {}
        self._blueprints: dict[str, ExamBlueprint] = {}
        self._blueprint_items: dict[str, tuple[str, ...]] = {}
        self._idempotency: dict[tuple[str, str], IdempotencyRecord] = {}
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
