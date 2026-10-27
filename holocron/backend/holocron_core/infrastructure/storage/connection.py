"""Kết nối SQLite với các PRAGMA bắt buộc của SPECIFICATION §5.13 và giao dịch `BEGIN IMMEDIATE`."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

MEMORY_PATH = ":memory:"
BUSY_TIMEOUT_MS = 5000


def connect(path: str | Path, busy_timeout_ms: int = BUSY_TIMEOUT_MS) -> sqlite3.Connection:
    """Mở kết nối: `foreign_keys = ON`, `journal_mode = WAL`, `busy_timeout = 5000`.

    `autocommit=True` để code tự mở giao dịch bằng `BEGIN IMMEDIATE` (không để module `sqlite3` tự mở giao dịch
    ngầm). `check_same_thread=False` vì FastAPI chạy endpoint đồng bộ trong threadpool; người gọi PHẢI tự khoá
    khi dùng chung một kết nối giữa các luồng. `:memory:` không có WAL nên SQLite giữ `journal_mode = memory`.
    `busy_timeout_ms` chỉ đổi trong test (để thử khoá bận mà không chờ 5 s); code chạy thật dùng mặc định.
    """
    conn = sqlite3.connect(str(path), autocommit=True, check_same_thread=False, timeout=busy_timeout_ms / 1000)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
    conn.execute("PRAGMA journal_mode = WAL").fetchone()
    return conn


def is_busy(exc: sqlite3.OperationalError) -> bool:
    """Lỗi do khoá bận quá `busy_timeout` (`SQLITE_BUSY`, `SQLITE_LOCKED` và các mã mở rộng của chúng)."""
    return exc.sqlite_errorcode & 0xFF in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)


@contextmanager
def _transaction(conn: sqlite3.Connection, begin: str) -> Iterator[sqlite3.Connection]:
    """Mở giao dịch bằng `begin`; thân lỗi hoặc `COMMIT` lỗi thì `ROLLBACK` rồi ném tiếp.

    `COMMIT` có thể lỗi mà giao dịch vẫn mở (khoá bận, khoá ngoại hoãn); không `ROLLBACK` thì kết nối dùng chung bị
    kẹt trong giao dịch đó và mọi `BEGIN` sau đều lỗi.
    """
    conn.execute(begin)
    try:
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


@contextmanager
def immediate_transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Giao dịch ghi: `BEGIN IMMEDIATE` giữ khoá ghi ngay từ đầu, nên đọc-so-ghi bên trong là nguyên tử."""
    with _transaction(conn, "BEGIN IMMEDIATE"):
        yield conn


@contextmanager
def read_transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Giao dịch đọc (`BEGIN` hoãn): nhiều câu đọc thấy cùng một bản chụp."""
    with _transaction(conn, "BEGIN"):
        yield conn


def fts5_available(conn: sqlite3.Connection) -> bool:
    """SQLite được biên dịch kèm FTS5 (đọc `PRAGMA compile_options`, §5.13)."""
    return any(str(row[0]) == "ENABLE_FTS5" for row in conn.execute("PRAGMA compile_options"))
