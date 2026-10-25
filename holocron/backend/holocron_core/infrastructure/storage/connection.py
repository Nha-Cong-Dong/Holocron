"""Kết nối SQLite với các PRAGMA bắt buộc của SPECIFICATION §5.13 và giao dịch `BEGIN IMMEDIATE`."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

MEMORY_PATH = ":memory:"
BUSY_TIMEOUT_MS = 5000


def connect(path: str | Path) -> sqlite3.Connection:
    """Mở kết nối: `foreign_keys = ON`, `journal_mode = WAL`, `busy_timeout = 5000`.

    `autocommit=True` để code tự mở giao dịch bằng `BEGIN IMMEDIATE` (không để module `sqlite3` tự mở giao dịch
    ngầm). `check_same_thread=False` vì FastAPI chạy endpoint đồng bộ trong threadpool; người gọi PHẢI tự khoá
    khi dùng chung một kết nối giữa các luồng. `:memory:` không có WAL nên SQLite giữ `journal_mode = memory`.
    """
    conn = sqlite3.connect(str(path), autocommit=True, check_same_thread=False, timeout=BUSY_TIMEOUT_MS / 1000)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA journal_mode = WAL").fetchone()
    return conn


@contextmanager
def immediate_transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Giao dịch ghi: `BEGIN IMMEDIATE` giữ khoá ghi ngay từ đầu, lỗi thì `ROLLBACK` rồi ném tiếp."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def fts5_available(conn: sqlite3.Connection) -> bool:
    """SQLite được biên dịch kèm FTS5 (đọc `PRAGMA compile_options`, §5.13)."""
    return any(str(row[0]) == "ENABLE_FTS5" for row in conn.execute("PRAGMA compile_options"))
