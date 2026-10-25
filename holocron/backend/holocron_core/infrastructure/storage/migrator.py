"""Migration bằng SQL thuần (SPECIFICATION §5.13).

Script `NNNN_<tên>.sql` trong thư mục `migrations/` (package-data) được chạy lần lượt theo số, mỗi script trong một
giao dịch `BEGIN IMMEDIATE` kèm dòng `schema_version`. Không hỗ trợ hạ version: DB có version mới hơn code thì từ
chối mở. File không theo mẫu `NNNN_*.sql` (ví dụ `fts5_vault.sql`) không phải migration.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import resources
from importlib.resources.abc import Traversable

from .connection import immediate_transaction

MIGRATION_NAME = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")
FTS5_SCRIPT = "fts5_vault.sql"


class SchemaVersionError(RuntimeError):
    """DB được tạo bởi bản Holocron mới hơn, hoặc bộ migration không hợp lệ."""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str


def _migration_dir() -> Traversable:
    return resources.files(__package__ or "holocron_core.infrastructure.storage").joinpath("migrations")


def load_migrations() -> list[Migration]:
    """Đọc mọi script `NNNN_*.sql` đóng gói kèm code, sắp theo số."""
    found: list[Migration] = []
    for entry in _migration_dir().iterdir():
        match = MIGRATION_NAME.match(entry.name)
        if match is not None:
            found.append(Migration(int(match.group(1)), entry.name, entry.read_text(encoding="utf-8")))
    return sorted(found, key=lambda migration: migration.version)


def load_fts5_script() -> str:
    return _migration_dir().joinpath(FTS5_SCRIPT).read_text(encoding="utf-8")


def _check_sequence(migrations: Sequence[Migration]) -> None:
    versions = [migration.version for migration in migrations]
    if versions != list(range(1, len(versions) + 1)):
        raise SchemaVersionError(f"migration phải đánh số liên tục từ 0001, không trùng; đang có {versions}")


def current_version(conn: sqlite3.Connection) -> int:
    """Version cao nhất đã áp dụng; DB rỗng (chưa có bảng `schema_version`) là 0."""
    exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_version'").fetchone()
    if exists is None:
        return 0
    row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    return 0 if row is None or row[0] is None else int(row[0])


def migrate(conn: sqlite3.Connection, migrations: Sequence[Migration] | None = None) -> int:
    """Đưa DB lên version mới nhất của code và trả version đó.

    DB mới hơn code ⇒ `SchemaVersionError` và không đổi gì. Script lỗi ⇒ giao dịch của nó bị huỷ, version giữ nguyên.
    """
    plan = list(load_migrations() if migrations is None else migrations)
    _check_sequence(plan)
    target = len(plan)
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
    applied = current_version(conn)
    if applied > target:
        raise SchemaVersionError(
            f"Cơ sở dữ liệu ở version {applied}, mới hơn version {target} mà bản Holocron này hỗ trợ. "
            "Hãy dùng bản Holocron mới hơn; không hỗ trợ hạ version."
        )
    for migration in plan[applied:]:
        with immediate_transaction(conn):
            conn.executescript(migration.sql)
            conn.execute(
                "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                (migration.version, datetime.now(UTC).isoformat()),
            )
    return target
