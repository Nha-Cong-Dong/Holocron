"""Ghi chạy chéo, khoá bận và vòng đời kết nối của kho (SPECIFICATION §5.13, §6.2–§6.4; BE-003).

Sửa stem, gắn non-cloneable và approve không tăng `table_primary.version`, nên ghi có điều kiện PHẢI so toàn bộ
trạng thái đã đọc: request đọc trước mà ghi sau nhận 409, không xoá thay đổi của request kia (I5: item đã bị gắn
non-cloneable không được approve). Mọi test chạy trên cả kho in-memory lẫn SQLite khi hành vi là chung.
"""

import sqlite3
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from holocron_core.domain.ingestion import IngestionItem
from holocron_core.domain.repositories import (
    AppStore,
    ConcurrentUpdateError,
    StorageBusyError,
    VersionConflictError,
)
from holocron_core.infrastructure.memory_store import IngestionStore
from holocron_core.infrastructure.storage import DATA_DIR_ENV, DATABASE_FILENAME, SQLiteStore, migrator
from holocron_core.infrastructure.storage.connection import connect, immediate_transaction
from holocron_core.routers import deps
from holocron_core.routers.api import STORE_BUSY_DETAIL, app, get_store
from tests.test_api_sqlite import _patch, _upload
from tests.test_storage_sqlite import _document, _item

STEM = r"Round Robin với $q = 2$ ms. Tính $\overline{WT}$ của các tiến trình."
CONFLICT = "ingestion item vừa bị thay đổi bởi một yêu cầu khác, hãy tải lại rồi thử lại"


@pytest.fixture(params=["memory", "sqlite"])
def api_store(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[AppStore]:
    """Kho cấp cho app qua `dependency_overrides`, lần lượt in-memory và SQLite."""
    store: AppStore
    if request.param == "memory":
        store = IngestionStore()
    else:
        store = SQLiteStore.open(tmp_path / DATABASE_FILENAME)
    app.dependency_overrides[get_store] = lambda: store
    yield store
    app.dependency_overrides.clear()
    if isinstance(store, SQLiteStore):
        store.close()


def _approvable(client: TestClient) -> str:
    """Item hết lỗi BLOCKING, chưa approve, ở version 2."""
    item_id: str = _upload(client)["item_id"]
    fixed = _patch(client, item_id, expected_version=1)
    assert fixed.status_code == 200 and fixed.json()["report"]["status"] == "AUTO_PASS"
    return item_id


def _read_now(store: AppStore, item_id: str) -> IngestionItem:
    """Bản item mà request chạy chậm (T2) đọc được lúc này."""
    seen = store.get_item(item_id)
    assert seen is not None
    return seen.model_copy(deep=True)


def _write_later(store: AppStore, monkeypatch: pytest.MonkeyPatch, seen: IngestionItem) -> None:
    """T2 tới bước ghi sau khi request kia đã ghi xong: lần `get_item` kế tiếp trả đúng bản T2 đã đọc trước đó."""
    real_get_item = store.get_item

    def stale_once(requested: str) -> IngestionItem | None:
        monkeypatch.setattr(store, "get_item", real_get_item)
        return seen if requested == seen.item_id else real_get_item(requested)

    monkeypatch.setattr(store, "get_item", stale_once)


def _non_cloneable(client: TestClient, item_id: str) -> Any:
    return client.post(
        f"/v1/ingestion/items/{item_id}/non-cloneable", json={"editor_id": "u_tuan", "reason": "đề cần hình vẽ"}
    )


def _stem(client: TestClient, item_id: str) -> Any:
    return client.patch(f"/v1/ingestion/items/{item_id}/stem", json={"editor_id": "u_tuan", "stem_latex": STEM})


def _stored(store: AppStore, item_id: str) -> IngestionItem:
    item = store.get_item(item_id)
    assert item is not None
    return item


# --------------------------------------------------------------------------- #
# Ghi chạy chéo qua API (cả hai kho)
# --------------------------------------------------------------------------- #


def test_approve_racing_non_cloneable_is_409_and_keeps_the_flag_I5(
    api_store: AppStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T2 approve đọc v2 (chưa gắn cờ), T1 gắn non-cloneable xong, T2 ghi: T2 nhận 409, cờ còn, item chưa duyệt."""
    client = TestClient(app)
    item_id = _approvable(client)
    seen = _read_now(api_store, item_id)  # T2 đọc
    assert _non_cloneable(client, item_id).status_code == 200  # T1 ghi trước
    _write_later(api_store, monkeypatch, seen)
    approved = client.post(f"/v1/ingestion/items/{item_id}/approve")  # T2 ghi sau
    assert approved.status_code == 409
    assert approved.json()["detail"] == CONFLICT
    stored = _stored(api_store, item_id)
    assert stored.non_cloneable and stored.non_cloneable_reason == "đề cần hình vẽ"
    assert not stored.approved and stored.table_primary.version == 2


def test_non_cloneable_racing_approve_is_409_and_keeps_the_approval(
    api_store: AppStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = TestClient(app)
    item_id = _approvable(client)
    seen = _read_now(api_store, item_id)
    assert client.post(f"/v1/ingestion/items/{item_id}/approve").status_code == 200
    _write_later(api_store, monkeypatch, seen)
    flagged = _non_cloneable(client, item_id)
    assert flagged.status_code == 409 and flagged.json()["detail"] == CONFLICT
    stored = _stored(api_store, item_id)
    assert stored.approved and not stored.non_cloneable


def test_approve_racing_a_stem_edit_is_409_and_keeps_the_new_stem(
    api_store: AppStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sửa stem không tăng version: approve đã đọc stem cũ không được duyệt (và ghi đè) stem mà người duyệt chưa xem."""
    client = TestClient(app)
    item_id = _approvable(client)
    seen = _read_now(api_store, item_id)
    assert _stem(client, item_id).status_code == 200
    _write_later(api_store, monkeypatch, seen)
    approved = client.post(f"/v1/ingestion/items/{item_id}/approve")
    assert approved.status_code == 409 and approved.json()["detail"] == CONFLICT
    stored = _stored(api_store, item_id)
    assert stored.stem_latex == STEM and not stored.approved and stored.table_primary.version == 2


def test_stem_edit_racing_non_cloneable_is_409_and_keeps_the_flag(
    api_store: AppStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = TestClient(app)
    item_id = _approvable(client)
    before = _stored(api_store, item_id).stem_latex
    seen = _read_now(api_store, item_id)
    assert _non_cloneable(client, item_id).status_code == 200
    _write_later(api_store, monkeypatch, seen)
    edited = _stem(client, item_id)
    assert edited.status_code == 409 and edited.json()["detail"] == CONFLICT
    stored = _stored(api_store, item_id)
    assert stored.non_cloneable and stored.stem_latex == before


def test_table_patch_racing_a_stem_edit_is_409_and_keeps_the_stem(
    api_store: AppStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = TestClient(app)
    item_id = _upload(client)["item_id"]
    seen = _read_now(api_store, item_id)
    assert _stem(client, item_id).status_code == 200
    _write_later(api_store, monkeypatch, seen)
    patched = _patch(client, item_id, expected_version=1)
    assert patched.status_code == 409 and patched.json()["detail"] == CONFLICT
    stored = _stored(api_store, item_id)
    assert stored.stem_latex == STEM and stored.table_primary.version == 1 and stored.audits == []


def test_sequential_writes_without_a_race_still_succeed(api_store: AppStore) -> None:
    client = TestClient(app)
    item_id = _approvable(client)
    assert _stem(client, item_id).status_code == 200
    approved = client.post(f"/v1/ingestion/items/{item_id}/approve")
    assert approved.status_code == 200 and approved.json()["approved"] is True
    assert approved.json()["stem_latex"] == STEM


# --------------------------------------------------------------------------- #
# Ghi chạy chéo ở tầng kho
# --------------------------------------------------------------------------- #


def test_update_item_compares_the_whole_state_it_read(tmp_path: Path) -> None:
    for store in (IngestionStore(), SQLiteStore.open(tmp_path / DATABASE_FILENAME)):
        store.save_document(_document())
        seen = store.save_item(_item()).model_copy(deep=True)
        flagged = seen.model_copy(update={"non_cloneable": True, "non_cloneable_reason": "đề cần hình"})
        approved = seen.model_copy(update={"approved": True})
        store.update_item(flagged, expected_version=1, expected=seen)
        with pytest.raises(ConcurrentUpdateError, match="tải lại"):
            store.update_item(approved, expected_version=1, expected=seen)
        stored = store.get_item("ing_a")
        assert stored is not None and stored.non_cloneable and not stored.approved
        if isinstance(store, SQLiteStore):
            store.close()


def test_two_connections_cannot_both_write_without_a_version_bump(tmp_path: Path) -> None:
    """Hai kết nối (như hai tiến trình) cùng đọc v1: gắn non-cloneable thắng, approve sau đó nhận xung đột."""
    db_path = tmp_path / DATABASE_FILENAME
    writer_a = SQLiteStore.open(db_path)
    writer_b = SQLiteStore.open(db_path)
    try:
        writer_a.save_document(_document())
        writer_a.save_item(_item())
        seen_by_a = writer_a.get_item("ing_a")
        seen_by_b = writer_b.get_item("ing_a")
        assert seen_by_a is not None and seen_by_b is not None
        writer_a.update_item(
            seen_by_a.model_copy(update={"non_cloneable": True, "non_cloneable_reason": "đề cần hình"}),
            expected_version=1,
            expected=seen_by_a,
        )
        with pytest.raises(ConcurrentUpdateError):
            writer_b.update_item(seen_by_b.model_copy(update={"approved": True}), 1, expected=seen_by_b)
        final = writer_b.get_item("ing_a")
        assert final is not None and final.non_cloneable and not final.approved
    finally:
        writer_a.close()
        writer_b.close()


def test_version_is_checked_before_the_state(tmp_path: Path) -> None:
    """Bảng đã được sửa (version tăng) thì lỗi vẫn là `VersionConflictError` với version hiện tại, như §6.2."""
    store = SQLiteStore.open(tmp_path / DATABASE_FILENAME)
    try:
        store.save_document(_document())
        seen = store.save_item(_item())
        bumped = seen.model_copy(deep=True)
        bumped.table_primary = bumped.table_primary.model_copy(update={"version": 2})
        store.update_item(bumped, expected_version=1, expected=seen)
        with pytest.raises(VersionConflictError, match="hiện tại 2"):
            store.update_item(seen.model_copy(update={"approved": True}), 1, expected=seen)
    finally:
        store.close()


# --------------------------------------------------------------------------- #
# Giao dịch: COMMIT lỗi, khoá bận
# --------------------------------------------------------------------------- #


def _deferred_fk_connection() -> sqlite3.Connection:
    conn = connect(":memory:")
    conn.executescript(
        "CREATE TABLE parent (id INTEGER PRIMARY KEY);"
        "CREATE TABLE child (parent_id INTEGER REFERENCES parent (id) DEFERRABLE INITIALLY DEFERRED);"
    )
    return conn


def test_failed_commit_is_rolled_back_and_the_connection_stays_usable() -> None:
    """Khoá ngoại hoãn làm `COMMIT` lỗi mà giao dịch vẫn mở: phải `ROLLBACK`, nếu không mọi `BEGIN` sau đều lỗi."""
    conn = _deferred_fk_connection()
    try:
        with pytest.raises(sqlite3.IntegrityError), immediate_transaction(conn):
            conn.execute("INSERT INTO child (parent_id) VALUES (1)")
        assert not conn.in_transaction
        assert conn.execute("SELECT COUNT(*) FROM child").fetchone()[0] == 0
        with immediate_transaction(conn):
            conn.execute("INSERT INTO parent (id) VALUES (1)")
            conn.execute("INSERT INTO child (parent_id) VALUES (1)")
        assert conn.execute("SELECT COUNT(*) FROM child").fetchone()[0] == 1
    finally:
        conn.close()


def test_error_after_the_body_already_ended_the_transaction_is_not_masked() -> None:
    conn = _deferred_fk_connection()
    try:
        with pytest.raises(RuntimeError, match="thân lỗi"), immediate_transaction(conn):
            conn.execute("ROLLBACK")
            raise RuntimeError("thân lỗi")
        assert not conn.in_transaction
    finally:
        conn.close()


@pytest.fixture
def locked_db(tmp_path: Path) -> Iterator[tuple[SQLiteStore, sqlite3.Connection]]:
    """Kho có `busy_timeout` ngắn và một kết nối khác (như tiến trình khác) đang giữ khoá ghi."""
    db_path = tmp_path / DATABASE_FILENAME
    SQLiteStore.open(db_path).close()
    store = SQLiteStore(connect(db_path, busy_timeout_ms=50))
    holder = connect(db_path)
    holder.execute("BEGIN IMMEDIATE")
    yield store, holder
    if holder.in_transaction:
        holder.execute("ROLLBACK")
    holder.close()
    store.close()


def test_busy_store_raises_a_storage_error_and_recovers(locked_db: tuple[SQLiteStore, sqlite3.Connection]) -> None:
    store, holder = locked_db
    with pytest.raises(StorageBusyError, match="bận"):
        store.save_document(_document())
    assert store.get_document(_document().document_id) is None, "WAL: đọc vẫn chạy khi bên khác giữ khoá ghi"
    holder.execute("ROLLBACK")
    assert store.save_document(_document()) == _document()


def test_busy_store_is_503_with_a_vietnamese_detail(locked_db: tuple[SQLiteStore, sqlite3.Connection]) -> None:
    store, holder = locked_db
    holder.execute("ROLLBACK")
    store.save_document(_document())
    store.save_item(_item())
    holder.execute("BEGIN IMMEDIATE")
    app.dependency_overrides[get_store] = lambda: store
    try:
        client = TestClient(app)
        response = _non_cloneable(client, "ing_a")
        assert response.status_code == 503
        assert response.json() == {"detail": STORE_BUSY_DETAIL}
        assert client.get("/v1/ingestion/items/ing_a").status_code == 200
    finally:
        app.dependency_overrides.clear()
    stored = store.get_item("ing_a")
    assert stored is not None and not stored.non_cloneable


def test_other_sqlite_errors_are_not_reported_as_busy(tmp_path: Path) -> None:
    db_path = tmp_path / DATABASE_FILENAME
    store = SQLiteStore.open(db_path)
    raw = connect(db_path)
    try:
        raw.execute("DROP TABLE blueprint_items")
        with pytest.raises(sqlite3.OperationalError, match="no such table"):
            store.blueprint_item_ids("bp_x")
    finally:
        raw.close()
        store.close()


# --------------------------------------------------------------------------- #
# Migration chạy song song, vòng đời kết nối
# --------------------------------------------------------------------------- #


def test_migration_applied_by_another_connection_is_not_run_twice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B đọc version 0 trước khi A migrate xong (hai tiến trình cùng mở DB mới): B đọc lại sau khi có khoá và bỏ qua."""
    db_path = tmp_path / DATABASE_FILENAME
    conn_a = connect(db_path)
    conn_b = connect(db_path)
    try:
        target = migrator.migrate(conn_a)
        real_current_version = migrator.current_version
        calls: list[int] = []

        def stale_first_read(conn: sqlite3.Connection) -> int:
            calls.append(1)
            return 0 if len(calls) == 1 else real_current_version(conn)

        monkeypatch.setattr(migrator, "current_version", stale_first_read)
        assert migrator.migrate(conn_b) == target
        assert len(calls) > 1, "version phải được đọc lại trong giao dịch giữ khoá ghi"
        rows = conn_b.execute("SELECT version FROM schema_version ORDER BY version").fetchall()
        assert [row[0] for row in rows] == list(range(1, target + 1))
    finally:
        conn_a.close()
        conn_b.close()


@pytest.fixture
def fresh_default_store() -> Iterator[Callable[[], None]]:
    deps._default_store.cache_clear()
    yield deps._default_store.cache_clear
    deps._default_store.cache_clear()


def test_shutdown_closes_the_sqlite_connection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fresh_default_store: Callable[[], None]
) -> None:
    monkeypatch.setenv(DATA_DIR_ENV, str(tmp_path))
    with TestClient(app) as client:
        opened = deps.get_store()
        assert isinstance(opened, SQLiteStore)
        assert client.get("/v1/ingestion/items/ing_missing").status_code == 404
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        opened.get_item("ing_missing")
    with TestClient(app) as client:
        reopened = deps.get_store()
        assert reopened is not opened, "lần khởi động sau phải mở kết nối mới"
        assert client.get("/v1/ingestion/items/ing_missing").status_code == 404
