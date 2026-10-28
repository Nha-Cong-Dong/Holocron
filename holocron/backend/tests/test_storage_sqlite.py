"""Kho SQLite (SPECIFICATION §5.13): migration, round-trip JSON, optimistic locking, idempotency 24 h, khoá ngoại, FTS5.

Mọi DB nằm trong `:memory:` hoặc `tmp_path`; không ghi vào thư mục người dùng.
"""

import sqlite3
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from holocron_core.domain.ingestion import IngestionItem, UploadedDocument
from holocron_core.domain.repositories import (
    IDEMPOTENCY_TTL,
    AppStore,
    ImportedDocument,
    MissingReferenceError,
    RecordInUseError,
    RecordNotFoundError,
    StorageError,
    VaultSearchUnavailableError,
    VersionConflictError,
)
from holocron_core.domain.schemas import (
    BlueprintSlot,
    DocumentMeta,
    ExamBlueprint,
    KnowledgeChunk,
    SourceKind,
    TwinQuestion,
)
from holocron_core.domain.twins import AttemptLog, IdempotencyRecord, ReviewTicket, TwinBatch
from holocron_core.infrastructure.memory_store import IngestionStore
from holocron_core.infrastructure.storage import (
    DATA_DIR_ENV,
    DATABASE_FILENAME,
    SchemaVersionError,
    SQLiteStore,
    data_dir_from_env,
    open_store_from_env,
    sqlite_store,
)
from holocron_core.infrastructure.storage.connection import connect, fts5_available
from holocron_core.infrastructure.storage.migrator import Migration, current_version, load_migrations, migrate
from holocron_core.routers import deps
from holocron_core.routers.api import app
from holocron_core.services.ingestion_gate import CellPatch, apply_patches, recompute_item
from tests.test_api_item_state import _body

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
T0 = datetime(2026, 1, 1, tzinfo=UTC)
SHA = "a" * 64
DOC_ID = f"doc_{SHA[:16]}"

SPEC_TABLES = {
    "schema_version",
    "documents",
    "pages",
    "page_audits",
    "chunks",
    "chunk_topics",
    "chunks_fts",
    "chunks_fts_nodiac",
    "concept_pairs",
    "ingestion_items",
    "patch_audits",
    "blueprints",
    "blueprint_items",
    "twin_batches",
    "twins",
    "review_tickets",
    "templates",
}


class FakePage(BaseModel):
    """Model trang giả lập: repository lưu trang dạng JSON của model do tầng gọi chọn (§5.7)."""

    document_id: str
    page_number: int
    version: int = 1
    status: Literal["pending", "ok", "needs_review", "failed"]
    lines: list[str] = []


# --------------------------------------------------------------------------- #
# Fixture và dữ liệu mẫu
# --------------------------------------------------------------------------- #


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / DATABASE_FILENAME


@pytest.fixture
def store(db_path: Path) -> Iterator[SQLiteStore]:
    opened = SQLiteStore.open(db_path)
    yield opened
    opened.close()


@pytest.fixture
def raw(db_path: Path, store: SQLiteStore) -> Iterator[sqlite3.Connection]:
    """Kết nối thứ hai vào cùng file, để soi bảng và mô phỏng tiến trình khác."""
    conn = connect(db_path)
    yield conn
    conn.close()


@pytest.fixture(params=["memory", "sqlite"])
def any_store(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[AppStore]:
    """Hành vi chung mà cả kho in-memory và kho SQLite PHẢI có (cùng `AppStore`)."""
    if request.param == "memory":
        yield IngestionStore()
        return
    opened = SQLiteStore.open(tmp_path / DATABASE_FILENAME)
    yield opened
    opened.close()


def _document(document_id: str = DOC_ID, sha256: str = SHA, filename: str = "de.png") -> UploadedDocument:
    return UploadedDocument(
        document_id=f"doc_{sha256[:16]}" if document_id == "" else document_id,
        sha256=sha256,
        filename=filename,
        media_type="image/png",
        size_bytes=72,
        uploaded_at=T0,
    )


def _item(item_id: str = "ing_a") -> IngestionItem:
    """Item đã qua Gate (có `problem`, `self_solve`, `report`), đủ để round-trip có ý nghĩa."""
    body = {**_body({"quantum": 4}), "source_doc_id": DOC_ID}
    return recompute_item(IngestionItem(item_id=item_id, **body), deps.REGISTRY)


def _patched(item: IngestionItem, text: str = "8") -> IngestionItem:
    """Bản sao của `item` với một lần sửa ô (version bảng tăng 1, thêm một `PatchAudit`)."""
    copy = item.model_copy(deep=True)
    new_table, audit = apply_patches(copy.table_primary, [CellPatch(row=1, col=2, new_text=text)], "u_tuan")
    copy.table_primary = new_table
    copy.audits.append(audit)
    return copy


def _blueprint(rr_slot: BlueprintSlot, blueprint_id: str = "bp_test_1", slots: int = 1) -> ExamBlueprint:
    return ExamBlueprint(
        blueprint_id=blueprint_id,
        source_exam_doc_id="doc_exam_1",
        subject_code="IT3070",
        total_questions=slots,
        slots=[rr_slot.model_copy(update={"slot_index": index + 1}) for index in range(slots)],
        created_at=T0,
    )


def _twin(twin_id: str, blueprint_id: str = "bp_test_1") -> TwinQuestion:
    twin = TwinQuestion.model_validate_json((EXAMPLES / "twin_question_rr_mcq.json").read_text(encoding="utf-8"))
    return twin.model_copy(update={"twin_id": twin_id, "blueprint_id": blueprint_id, "slot_index": 1})


def _record(
    stored_at: datetime, *twin_ids: str, digest: str = "d1", blueprint_id: str = "bp_test_1"
) -> IdempotencyRecord:
    ticket = ReviewTicket(
        blueprint_id=blueprint_id,
        slot_index=2,
        topic_key="os.cpu_sched.rr.avg_waiting_time",
        reason="thiếu nguồn trích dẫn",
        attempts=[
            AttemptLog(attempt=1, provenance="precomputed_verified", template_sha256=None, outcome="x", seeds_tried=3)
        ],
    )
    batch = TwinBatch(
        blueprint_id=blueprint_id,
        base_seed=2**63 - 1,
        twins=[_twin(twin_id, blueprint_id) for twin_id in twin_ids],
        review_tickets=[ticket],
    )
    return IdempotencyRecord(body_digest=digest, stored_at=stored_at, batch=batch)


def _meta(doc_id: str, subject_code: str = "ET4010", uploaded_at: datetime = T0, sha256: str = SHA) -> DocumentMeta:
    return DocumentMeta(
        doc_id=doc_id,
        subject_code=subject_code,
        title="Kỹ thuật vi xử lý",
        source_kind=SourceKind.LECTURE_SLIDES,
        sha256=sha256,
        page_count=3,
        has_text_layer=True,
        uploaded_at=uploaded_at,
    )


def _imported(doc_id: str = "doc_aaaaaaaaaaaaaaaa", **meta: object) -> ImportedDocument:
    return ImportedDocument(
        meta=_meta(doc_id, **meta),
        filename="vxl.pdf",
        size_bytes=1024,
        status="processing",
    )


def _chunk(chunk_id: str, doc_id: str, page_number: int = 1, chapter_code: str = "ch3") -> KnowledgeChunk:
    return KnowledgeChunk(
        chunk_id=chunk_id,
        doc_id=doc_id,
        subject_code="ET4010",
        chapter_code=chapter_code,
        page_number=page_number,
        raw_text="Thanh ghi CPSR chứa các cờ trạng thái N, Z, C, V của bộ vi xử lý ARM.",
        verbatim_quote="chứa các cờ trạng thái N, Z, C, V",
        category="THEORY",
        topic_keys=["mp.arm.cpsr.flags", "mp.arm.alu.add"],
        linked_chunk_ids=["chk_other"],
        token_count=17,
    )


def _tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {str(row[0]) for row in rows}


def _count(conn: sqlite3.Connection, sql: str, *params: object) -> int:
    return int(conn.execute(sql, params).fetchone()[0])


# --------------------------------------------------------------------------- #
# Kết nối và migration
# --------------------------------------------------------------------------- #


def test_connection_sets_the_required_pragmas(db_path: Path) -> None:
    conn = connect(db_path)
    try:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        conn.close()


def test_packaged_migrations_are_numbered_from_0001() -> None:
    migrations = load_migrations()
    assert [m.version for m in migrations] == list(range(1, len(migrations) + 1))
    assert migrations[0].name == "0001_initial.sql"


def test_migration_from_an_empty_database_creates_every_table_of_the_spec(
    store: SQLiteStore, raw: sqlite3.Connection
) -> None:
    assert SPEC_TABLES <= _tables(raw)
    rows = raw.execute("SELECT version FROM schema_version").fetchall()
    assert sorted(row[0] for row in rows) == [m.version for m in load_migrations()]
    assert current_version(raw) == len(load_migrations())
    for table in ("documents", "pages", "chunks", "concept_pairs", "ingestion_items", "blueprints", "templates"):
        columns = {str(row[1]) for row in raw.execute(f"PRAGMA table_info({table})")}
        assert "version" in columns, f"{table} thiếu cột version (optimistic locking)"


def test_reopening_an_up_to_date_database_does_not_reapply_migrations(db_path: Path) -> None:
    SQLiteStore.open(db_path).close()
    SQLiteStore.open(db_path).close()
    conn = connect(db_path)
    try:
        assert _count(conn, "SELECT COUNT(*) FROM schema_version") == len(load_migrations())
    finally:
        conn.close()


def test_database_newer_than_the_code_is_refused(db_path: Path) -> None:
    SQLiteStore.open(db_path).close()
    conn = connect(db_path)
    conn.execute("INSERT INTO schema_version (version, applied_at) VALUES (99, '2030-01-01T00:00:00+00:00')")
    conn.close()
    with pytest.raises(SchemaVersionError, match="version 99"):
        SQLiteStore.open(db_path)


def test_failed_migration_is_rolled_back_and_keeps_the_version() -> None:
    conn = connect(":memory:")
    assert current_version(conn) == 0, "DB rỗng chưa có bảng schema_version"
    good = Migration(1, "0001_a.sql", "CREATE TABLE a (x INTEGER);")
    bad = Migration(2, "0002_b.sql", "CREATE TABLE b (x INTEGER); CREATE TABLE b (x INTEGER);")
    with pytest.raises(sqlite3.OperationalError):
        migrate(conn, [good, bad])
    assert current_version(conn) == 1
    assert "a" in _tables(conn) and "b" not in _tables(conn)
    conn.close()


@pytest.mark.parametrize("versions", [[2], [1, 1], [1, 3]])
def test_migration_numbers_must_be_contiguous(versions: list[int]) -> None:
    conn = connect(":memory:")
    plan = [Migration(v, f"{v:04d}_x.sql", "SELECT 1;") for v in versions]
    with pytest.raises(SchemaVersionError, match="liên tục"):
        migrate(conn, plan)
    conn.close()


def test_memory_database_works_for_tests() -> None:
    memory = SQLiteStore.open(":memory:")
    memory.save_document(_document())
    assert memory.get_document(DOC_ID) == _document()
    memory.close()


# --------------------------------------------------------------------------- #
# Hành vi chung của AppStore (in-memory và SQLite)
# --------------------------------------------------------------------------- #


def test_document_upload_is_idempotent(any_store: AppStore) -> None:
    first = any_store.save_document(_document())
    again = any_store.save_document(_document(filename="ten_khac.png"))
    assert again == first and again.filename == "de.png"
    assert any_store.get_document(DOC_ID) == first
    assert any_store.get_document("doc_missing") is None


def test_item_round_trip_keeps_every_field(any_store: AppStore) -> None:
    any_store.save_document(_document())
    item = _patched(_item(), "8")
    assert item.problem is not None and item.self_solve is not None and item.report is not None
    any_store.save_item(item)
    loaded = any_store.get_item("ing_a")
    assert loaded == item
    assert loaded is not None and loaded.audits[0].changes == [{"row": 1, "col": 2, "old": "9", "new": "8"}]
    assert any_store.get_item("ing_missing") is None


def test_update_item_is_compare_and_swap_on_the_table_version(any_store: AppStore) -> None:
    any_store.save_document(_document())
    original = any_store.save_item(_item())
    # Hai người cùng đọc version 1 rồi cùng sửa: người ghi sau nhận xung đột, không ghi đè.
    first = _patched(original, "8")
    second = _patched(original, "7")
    any_store.update_item(first, expected_version=1)
    with pytest.raises(VersionConflictError) as conflict:
        any_store.update_item(second, expected_version=1)
    assert conflict.value.current_version == 2 and "version lệch: hiện tại 2" in str(conflict.value)
    stored = any_store.get_item("ing_a")
    assert stored is not None
    cell = stored.table_primary.cell(1, 2)
    assert cell is not None and cell.text == "8"
    assert stored.table_primary.version == 2 and len(stored.audits) == 1


def test_update_without_table_change_keeps_the_version(any_store: AppStore) -> None:
    any_store.save_document(_document())
    item = any_store.save_item(_item()).model_copy(deep=True)
    item.approved = True
    any_store.update_item(item, expected_version=1)
    stored = any_store.get_item("ing_a")
    assert stored is not None and stored.approved and stored.table_primary.version == 1


def test_update_item_rejects_unknown_items_and_version_jumps(any_store: AppStore) -> None:
    with pytest.raises(RecordNotFoundError):
        any_store.update_item(_item("ing_missing"), expected_version=1)
    with pytest.raises(ValueError, match="không hợp lệ"):
        any_store.update_item(_patched(_patched(_item())), expected_version=1)


def test_blueprint_round_trip_and_source_items(any_store: AppStore, rr_slot: BlueprintSlot) -> None:
    any_store.save_document(_document())
    for item_id in ("ing_a", "ing_b"):
        any_store.save_item(_item(item_id))
    blueprint = _blueprint(rr_slot, slots=2)
    any_store.save_blueprint(blueprint, item_ids=["ing_b", "ing_a"])
    assert any_store.get_blueprint("bp_test_1") == blueprint
    assert any_store.blueprint_item_ids("bp_test_1") == ("ing_b", "ing_a")
    any_store.save_blueprint(blueprint, item_ids=["ing_a"])
    assert any_store.blueprint_item_ids("bp_test_1") == ("ing_a",)
    assert any_store.get_blueprint("bp_missing") is None
    assert any_store.blueprint_item_ids("bp_missing") == ()


def test_idempotency_record_lives_exactly_24_hours(any_store: AppStore, rr_slot: BlueprintSlot) -> None:
    any_store.save_blueprint(_blueprint(rr_slot))
    any_store.save_blueprint(_blueprint(rr_slot, blueprint_id="bp_other"))
    record = _record(T0, "twin_a", "twin_b")
    any_store.save_idempotent("bp_test_1", "key", record)
    assert any_store.get_idempotent("bp_test_1", "key", T0 + IDEMPOTENCY_TTL) == record
    assert any_store.get_idempotent("bp_test_1", "key", T0 + IDEMPOTENCY_TTL + timedelta(seconds=1)) is None
    assert any_store.get_idempotent("bp_test_1", "other", T0) is None
    assert any_store.get_idempotent("bp_other", "key", T0) is None


def test_saving_the_same_key_again_replaces_the_record(any_store: AppStore, rr_slot: BlueprintSlot) -> None:
    any_store.save_blueprint(_blueprint(rr_slot))
    any_store.save_idempotent("bp_test_1", "key", _record(T0, "twin_a", digest="old"))
    newer = _record(T0 + timedelta(hours=1), "twin_b", digest="new")
    any_store.save_idempotent("bp_test_1", "key", newer)
    assert any_store.get_idempotent("bp_test_1", "key", T0 + timedelta(hours=2)) == newer


# --------------------------------------------------------------------------- #
# Riêng SQLite: bền qua khởi động lại, nhiều kết nối, khoá ngoại
# --------------------------------------------------------------------------- #


def test_data_survives_reopening_the_file(db_path: Path, rr_slot: BlueprintSlot) -> None:
    first = SQLiteStore.open(db_path)
    first.save_document(_document())
    item = first.save_item(_patched(_item()))
    first.save_blueprint(_blueprint(rr_slot), item_ids=["ing_a"])
    record = _record(T0, "twin_a")
    first.save_idempotent("bp_test_1", "key", record)
    first.close()

    reopened = SQLiteStore.open(db_path)
    try:
        assert reopened.get_item("ing_a") == item
        assert reopened.blueprint_item_ids("bp_test_1") == ("ing_a",)
        assert reopened.get_idempotent("bp_test_1", "key", T0) == record
    finally:
        reopened.close()


def test_concurrent_writers_on_two_connections_get_a_conflict(db_path: Path) -> None:
    """Hai kết nối (như hai tiến trình) cùng đọc version 1: so-và-ghi là một câu UPDATE nên chỉ một bên thắng."""
    writer_a = SQLiteStore.open(db_path)
    writer_b = SQLiteStore.open(db_path)
    try:
        writer_a.save_document(_document())
        writer_a.save_item(_item())
        seen_by_a = writer_a.get_item("ing_a")
        seen_by_b = writer_b.get_item("ing_a")
        assert seen_by_a is not None and seen_by_b is not None
        writer_a.update_item(_patched(seen_by_a, "8"), expected_version=1)
        with pytest.raises(VersionConflictError):
            writer_b.update_item(_patched(seen_by_b, "7"), expected_version=1)
        final = writer_b.get_item("ing_a")
        assert final is not None
        cell = final.table_primary.cell(1, 2)
        assert cell is not None and cell.text == "8" and final.table_primary.version == 2
    finally:
        writer_a.close()
        writer_b.close()


def test_item_needs_an_existing_source_document(store: SQLiteStore) -> None:
    with pytest.raises(MissingReferenceError, match=DOC_ID):
        store.save_item(_item())
    assert store.get_item("ing_a") is None
    store.save_document(_document())
    stored = store.save_item(_item())
    moved = stored.model_copy(update={"source_doc_id": "doc_ffffffffffffffff"})
    with pytest.raises(MissingReferenceError, match="doc_ffffffffffffffff"):
        store.update_item(moved, expected_version=1)
    assert store.get_item("ing_a") == stored


def test_blueprint_items_must_exist(store: SQLiteStore, rr_slot: BlueprintSlot) -> None:
    with pytest.raises(MissingReferenceError):
        store.save_blueprint(_blueprint(rr_slot), item_ids=["ing_does_not_exist"])
    assert store.get_blueprint("bp_test_1") is None, "giao dịch lỗi phải được huỷ toàn bộ"


def test_twin_batch_needs_an_existing_blueprint(store: SQLiteStore) -> None:
    with pytest.raises(MissingReferenceError):
        store.save_idempotent("bp_missing", "key", _record(T0, "twin_a"))


def test_expired_keys_are_cleared_when_a_new_record_is_written(
    store: SQLiteStore, raw: sqlite3.Connection, rr_slot: BlueprintSlot
) -> None:
    store.save_blueprint(_blueprint(rr_slot))
    store.save_idempotent("bp_test_1", "old-key", _record(T0, "twin_a"))
    store.save_idempotent("bp_test_1", "fresh-key", _record(T0 + timedelta(hours=1), "twin_b"))
    store.save_idempotent("bp_test_1", "new-key", _record(T0 + timedelta(hours=24, minutes=30), "twin_c"))
    keys = raw.execute("SELECT idempotency_key FROM twin_batches ORDER BY created_at").fetchall()
    assert [row[0] for row in keys] == [None, "fresh-key", "new-key"]
    # Khoá hết hạn đã được dọn nên ghi lại đúng khoá đó không vi phạm index UNIQUE.
    later = _record(T0 + timedelta(hours=25), "twin_d")
    store.save_idempotent("bp_test_1", "old-key", later)
    assert store.get_idempotent("bp_test_1", "old-key", T0 + timedelta(hours=25)) == later
    assert _count(raw, "SELECT COUNT(*) FROM twin_batches") == 4


def test_idempotency_key_is_unique_per_blueprint_in_the_schema(
    store: SQLiteStore, raw: sqlite3.Connection, rr_slot: BlueprintSlot
) -> None:
    store.save_blueprint(_blueprint(rr_slot))
    store.save_idempotent("bp_test_1", "key", _record(T0, "twin_a"))
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        raw.execute(
            "INSERT INTO twin_batches (batch_id, blueprint_id, base_seed, body_digest, idempotency_key, created_at) "
            "VALUES ('tb_dup', 'bp_test_1', 1, 'd', 'key', '2026-01-01T00:00:00.000000+00:00')"
        )


def test_twin_ids_are_unique_across_batches(store: SQLiteStore, rr_slot: BlueprintSlot) -> None:
    store.save_blueprint(_blueprint(rr_slot))
    store.save_idempotent("bp_test_1", "k1", _record(T0, "twin_a"))
    with pytest.raises(StorageError, match="UNIQUE"):
        store.save_idempotent("bp_test_1", "k2", _record(T0, "twin_a"))
    assert store.get_idempotent("bp_test_1", "k2", T0) is None


def test_delete_cascades_from_documents(store: SQLiteStore, raw: sqlite3.Connection) -> None:
    doc_id = "doc_aaaaaaaaaaaaaaaa"
    store.save_imported_document(_imported(doc_id))
    store.add_page(doc_id, 1, FakePage(document_id=doc_id, page_number=1, status="ok"), status="ok", confirmed=False)
    raw.execute(
        "INSERT INTO page_audits (document_id, page_number, from_version, to_version, editor_id, changes_json, at) "
        "VALUES (?, 1, 1, 2, 'u', '[]', '2026-01-01T00:00:00.000000+00:00')",
        (doc_id,),
    )
    store.add_chunk(_chunk("chk_a", doc_id), "thanh ghi cpsr")
    raw.execute(
        "INSERT INTO concept_pairs (pair_id, chunk_id, subject_code, chapter_code, term, definition_quote, "
        "term_spans_json, status, proposed_by, created_at) VALUES ('cp_a', 'chk_a', 'ET4010', 'ch3', 'CPSR', "
        "'chứa các cờ trạng thái', '[]', 'proposed', 'llm', '2026-01-01T00:00:00.000000+00:00')"
    )
    assert store.delete_document(doc_id) is True
    for table in ("documents", "pages", "page_audits", "chunks", "chunk_topics", "concept_pairs", "chunks_fts"):
        assert _count(raw, f"SELECT COUNT(*) FROM {table}") == 0, f"{table} chưa bị xoá theo tài liệu"
    assert _count(raw, "SELECT COUNT(*) FROM chunks_fts_nodiac") == 0
    assert store.delete_document(doc_id) is False


def test_delete_cascades_from_blueprints_and_items(
    store: SQLiteStore, raw: sqlite3.Connection, rr_slot: BlueprintSlot
) -> None:
    store.save_document(_document())
    store.save_item(_patched(_item()))
    store.save_blueprint(_blueprint(rr_slot), item_ids=["ing_a"])
    store.save_idempotent("bp_test_1", "key", _record(T0, "twin_a", "twin_b"))
    raw.execute("DELETE FROM blueprints WHERE blueprint_id = 'bp_test_1'")
    for table in ("blueprint_items", "twin_batches", "twins", "review_tickets"):
        assert _count(raw, f"SELECT COUNT(*) FROM {table}") == 0, f"{table} chưa bị xoá theo blueprint"
    assert _count(raw, "SELECT COUNT(*) FROM patch_audits") == 1
    raw.execute("DELETE FROM ingestion_items WHERE item_id = 'ing_a'")
    assert _count(raw, "SELECT COUNT(*) FROM patch_audits") == 0


def test_restrict_keeps_documents_and_items_that_are_still_referenced(
    store: SQLiteStore, raw: sqlite3.Connection, rr_slot: BlueprintSlot
) -> None:
    store.save_document(_document())
    store.save_item(_item())
    store.save_blueprint(_blueprint(rr_slot), item_ids=["ing_a"])
    with pytest.raises(RecordInUseError):
        store.delete_document(DOC_ID)
    assert store.get_document(DOC_ID) is not None
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        raw.execute("DELETE FROM ingestion_items WHERE item_id = 'ing_a'")
    assert store.get_item("ing_a") is not None


# --------------------------------------------------------------------------- #
# DocumentRepository
# --------------------------------------------------------------------------- #


def test_imported_document_round_trip_and_listing(store: SQLiteStore) -> None:
    newest = _imported("doc_bbbbbbbbbbbbbbbb", uploaded_at=T0 + timedelta(hours=1), sha256="b" * 64)
    other_subject = _imported("doc_cccccccccccccccc", subject_code="IT3070", sha256="c" * 64)
    oldest = _imported()
    for document in (oldest, newest, other_subject):
        assert store.save_imported_document(document) == document
    assert store.get_imported_document(oldest.meta.doc_id) == oldest
    assert [d.meta.doc_id for d in store.list_imported_documents("ET4010")] == [
        newest.meta.doc_id,
        oldest.meta.doc_id,
    ]
    assert len(store.list_imported_documents()) == 3
    assert store.get_imported_document("doc_missing") is None


def test_import_fills_in_a_document_first_uploaded_as_an_exam(store: SQLiteStore) -> None:
    upload = _document(document_id="", sha256="d" * 64)
    store.save_document(upload)
    assert store.get_imported_document(upload.document_id) is None, "bản ghi chỉ do upload đề tạo chưa là tài liệu nhập"
    filled = store.save_imported_document(_imported(upload.document_id, sha256="d" * 64))
    assert filled.version == 2 and filled.meta.subject_code == "ET4010"
    assert store.get_document(upload.document_id) is not None


def test_imported_document_update_is_compare_and_swap(store: SQLiteStore) -> None:
    document = store.save_imported_document(_imported())
    reviewed = store.update_imported_document(document.model_copy(update={"status": "review"}), expected_version=1)
    assert reviewed.version == 2
    assert store.get_imported_document(document.meta.doc_id) == reviewed
    with pytest.raises(VersionConflictError) as conflict:
        store.update_imported_document(document.model_copy(update={"status": "failed"}), expected_version=1)
    assert conflict.value.current_version == 2
    with pytest.raises(RecordNotFoundError):
        store.update_imported_document(_imported("doc_eeeeeeeeeeeeeeee"), expected_version=1)


def test_pages_round_trip_and_compare_and_swap(store: SQLiteStore) -> None:
    doc_id = "doc_aaaaaaaaaaaaaaaa"
    store.save_imported_document(_imported(doc_id))
    pending = [FakePage(document_id=doc_id, page_number=n, status="pending") for n in (2, 1)]
    for page in pending:
        store.add_page(doc_id, page.page_number, page, status="pending", confirmed=False)
    assert [p.page_number for p in store.list_pages(doc_id, FakePage)] == [1, 2]

    read = FakePage(document_id=doc_id, page_number=1, version=2, status="ok", lines=["MOV R0, #0x1F"])
    assert store.update_page(doc_id, 1, read, status="ok", confirmed=False, expected_version=1) == 2
    assert store.get_page(doc_id, 1, FakePage) == read
    with pytest.raises(VersionConflictError):
        store.update_page(doc_id, 1, read, status="ok", confirmed=True, expected_version=1)
    with pytest.raises(RecordNotFoundError):
        store.update_page(doc_id, 9, read, status="ok", confirmed=True, expected_version=1)
    assert store.get_page(doc_id, 9, FakePage) is None


def test_pages_need_an_existing_document_and_a_free_number(store: SQLiteStore) -> None:
    page = FakePage(document_id="doc_aaaaaaaaaaaaaaaa", page_number=1, status="pending")
    with pytest.raises(MissingReferenceError):
        store.add_page("doc_aaaaaaaaaaaaaaaa", 1, page, status="pending", confirmed=False)
    store.save_imported_document(_imported())
    store.add_page("doc_aaaaaaaaaaaaaaaa", 1, page, status="pending", confirmed=False)
    with pytest.raises(StorageError, match="UNIQUE"):
        store.add_page("doc_aaaaaaaaaaaaaaaa", 1, page, status="pending", confirmed=False)


# --------------------------------------------------------------------------- #
# VaultRepository và FTS5
# --------------------------------------------------------------------------- #


def test_fts5_is_present_and_both_indexes_exist(store: SQLiteStore, raw: sqlite3.Connection) -> None:
    assert fts5_available(raw), "SQLite của môi trường chạy phải có ENABLE_FTS5"
    assert store.vault_search_available is True
    assert {"chunks_fts", "chunks_fts_nodiac"} <= _tables(raw)


def test_chunk_round_trip_and_listing_order(store: SQLiteStore) -> None:
    doc_a, doc_b = "doc_aaaaaaaaaaaaaaaa", "doc_bbbbbbbbbbbbbbbb"
    store.save_imported_document(_imported(doc_a))
    store.save_imported_document(_imported(doc_b, sha256="b" * 64))
    chunks = [
        _chunk("chk_c", doc_b, page_number=1),
        _chunk("chk_b", doc_a, page_number=2, chapter_code="ch4"),
        _chunk("chk_a", doc_a, page_number=2),
        _chunk("chk_d", doc_a, page_number=1),
    ]
    for chunk in chunks:
        store.add_chunk(chunk, chunk.raw_text)
    assert store.get_chunk("chk_a") == chunks[2]
    assert store.get_chunk("chk_missing") is None
    assert [c.chunk_id for c in store.list_chunks("ET4010")] == ["chk_d", "chk_a", "chk_b", "chk_c"]
    assert [c.chunk_id for c in store.list_chunks("ET4010", chapter_code="ch4")] == ["chk_b"]
    assert all(c.topic_keys == ["mp.arm.alu.add", "mp.arm.cpsr.flags"] for c in store.list_chunks("ET4010"))
    assert store.list_chunks("IT3070") == []


def test_chunk_needs_an_existing_document_and_no_embedding(store: SQLiteStore) -> None:
    with pytest.raises(MissingReferenceError):
        store.add_chunk(_chunk("chk_a", "doc_aaaaaaaaaaaaaaaa"), "x")
    store.save_imported_document(_imported())
    embedded = _chunk("chk_a", "doc_aaaaaaaaaaaaaaaa").model_copy(update={"embedding_model": "e5"})
    with pytest.raises(ValueError, match="embedding_model"):
        store.add_chunk(embedded, "x")


def test_fts_match_uses_both_indexes_and_filters_by_subject(store: SQLiteStore) -> None:
    store.save_imported_document(_imported())
    store.add_chunk(_chunk("chk_a", "doc_aaaaaaaaaaaaaaaa"), "thanh ghi trạng thái lượng tử")
    assert store.match_chunk_ids("ET4010", "lượng") == ["chk_a"]
    assert store.match_chunk_ids("ET4010", "luong") == []
    assert store.match_chunk_ids("ET4010", "luong", index="no_diacritics") == ["chk_a"]
    assert store.match_chunk_ids("IT3070", "lượng") == []
    with pytest.raises(ValueError, match="FTS5"):
        store.match_chunk_ids("ET4010", '"không đóng')


def test_missing_fts5_reports_search_unavailable_without_crashing(
    monkeypatch: pytest.MonkeyPatch, db_path: Path
) -> None:
    monkeypatch.setattr(sqlite_store, "fts5_available", lambda conn: False)
    degraded = SQLiteStore.open(db_path)
    try:
        assert degraded.vault_search_available is False
        degraded.save_imported_document(_imported())
        degraded.add_chunk(_chunk("chk_a", "doc_aaaaaaaaaaaaaaaa"), "thanh ghi")
        assert degraded.get_chunk("chk_a") is not None
        with pytest.raises(VaultSearchUnavailableError):
            degraded.match_chunk_ids("ET4010", "thanh")
        assert degraded.delete_document("doc_aaaaaaaaaaaaaaaa") is True
    finally:
        degraded.close()
    conn = connect(db_path)
    try:
        assert "chunks_fts" not in _tables(conn)
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Chọn kho khi chạy
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("value", ["", "   "])
def test_blank_data_dir_means_in_memory(value: str) -> None:
    assert data_dir_from_env({DATA_DIR_ENV: value}) is None
    assert isinstance(open_store_from_env({DATA_DIR_ENV: value}), IngestionStore)
    assert isinstance(open_store_from_env({}), IngestionStore)


def test_data_dir_selects_sqlite_inside_that_directory(tmp_path: Path) -> None:
    data_dir = tmp_path / "nested" / "holocron"
    opened = open_store_from_env({DATA_DIR_ENV: f"  {data_dir}  "})
    try:
        assert isinstance(opened, SQLiteStore)
        assert (data_dir / DATABASE_FILENAME).is_file()
    finally:
        assert isinstance(opened, SQLiteStore)
        opened.close()


@pytest.fixture
def fresh_default_store() -> Iterator[Callable[[], None]]:
    """Bỏ kho mặc định đã mở trong tiến trình test, rồi khôi phục sau test."""
    deps._default_store.cache_clear()
    yield deps._default_store.cache_clear
    deps._default_store.cache_clear()


def test_app_refuses_to_start_on_a_newer_database(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fresh_default_store: Callable[[], None]
) -> None:
    SQLiteStore.open(tmp_path / DATABASE_FILENAME).close()
    conn = connect(tmp_path / DATABASE_FILENAME)
    conn.execute("INSERT INTO schema_version (version, applied_at) VALUES (99, '2030-01-01T00:00:00+00:00')")
    conn.close()
    monkeypatch.setenv(DATA_DIR_ENV, str(tmp_path))
    with pytest.raises(SchemaVersionError), TestClient(app):
        pass


def test_app_starts_with_the_in_memory_store_when_no_data_dir(
    monkeypatch: pytest.MonkeyPatch, fresh_default_store: Callable[[], None]
) -> None:
    monkeypatch.delenv(DATA_DIR_ENV, raising=False)
    with TestClient(app) as client:
        assert client.get("/v1/runtime").status_code == 200
    assert isinstance(deps.get_store(), IngestionStore)


def test_saving_the_same_batch_under_another_key_is_an_error_on_both_stores(
    any_store: AppStore, rr_slot: BlueprintSlot
) -> None:
    any_store.save_blueprint(_blueprint(rr_slot))
    first = _record(T0, "twin_a")
    any_store.save_idempotent("bp_test_1", "k1", first)
    with pytest.raises(StorageError, match="UNIQUE"):
        any_store.save_idempotent("bp_test_1", "k2", first)
    assert any_store.get_idempotent("bp_test_1", "k1", T0) == first
    assert any_store.get_idempotent("bp_test_1", "k2", T0) is None
