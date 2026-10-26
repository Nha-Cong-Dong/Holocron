"""Chọn kho khi chạy theo `HOLOCRON_DATA_DIR`.

- Có đặt (khác rỗng sau khi bỏ khoảng trắng): SQLite tại `HOLOCRON_DATA_DIR/holocron.sqlite3`.
- Không đặt: kho in-memory như trước.

Lệch có chủ đích so với SPECIFICATION §5.13 (mặc định `%LOCALAPPDATA%\\Holocron` hoặc `$XDG_DATA_HOME/holocron`):
không đặt biến thì không ghi file, để test và chế độ dev không ghi vào thư mục người dùng.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from ...domain.repositories import AppStore
from ..memory_store import IngestionStore
from .sqlite_store import SQLiteStore

DATA_DIR_ENV = "HOLOCRON_DATA_DIR"
DATABASE_FILENAME = "holocron.sqlite3"


def data_dir_from_env(env: Mapping[str, str] | None = None) -> Path | None:
    """Thư mục dữ liệu từ `env` (mặc định `os.environ`); rỗng hoặc chỉ khoảng trắng coi như chưa đặt."""
    source = os.environ if env is None else env
    raw = source.get(DATA_DIR_ENV, "").strip()
    return Path(raw) if raw else None


def open_store_from_env(env: Mapping[str, str] | None = None) -> AppStore:
    """Mở kho theo cấu hình; DB mới hơn code ⇒ `SchemaVersionError` (từ chối khởi động)."""
    data_dir = data_dir_from_env(env)
    if data_dir is None:
        return IngestionStore()
    data_dir.mkdir(parents=True, exist_ok=True)
    return SQLiteStore.open(data_dir / DATABASE_FILENAME)
