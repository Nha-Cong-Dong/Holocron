"""Kho SQLite cần JSON1 (`json_extract` ở tóm tắt trang, `json_each` ở nhập chunk; SPECIFICATION §5.13): thiếu thì
kho từ chối mở ngay lúc khởi động với thông điệp nêu cách sửa, không để lộ `sqlite3.OperationalError` trần ở request."""

import sqlite3
from pathlib import Path

import pytest

from holocron_core.infrastructure.storage import SQLiteStore, sqlite_store
from holocron_core.infrastructure.storage.connection import connect


def test_json1_is_available_in_the_bundled_sqlite() -> None:
    conn = connect(":memory:")
    try:
        assert sqlite_store.json1_available(conn)
    finally:
        conn.close()


def test_json1_probe_reports_missing_functions() -> None:
    """Giả lập SQLite thiếu JSON1: kết nối mà mọi câu lệnh đều ném `OperationalError` như khi hàm không tồn tại."""

    class NoJson1:
        def execute(self, sql: str) -> sqlite3.Cursor:
            raise sqlite3.OperationalError("no such function: json_extract")

    assert sqlite_store.json1_available(NoJson1()) is False


def test_store_refuses_to_open_without_json1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sqlite_store, "json1_available", lambda conn: False)
    path = tmp_path / "khong_json1.sqlite3"
    with pytest.raises(sqlite_store.SQLiteFeatureError) as caught:
        SQLiteStore.open(path)
    assert str(caught.value) == sqlite_store.JSON1_MISSING_DETAIL and "JSON1" in str(caught.value)
    check = sqlite3.connect(path)
    try:
        tables = check.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    finally:
        check.close()
    assert tables == [], "từ chối trước khi chạy migration nào"
