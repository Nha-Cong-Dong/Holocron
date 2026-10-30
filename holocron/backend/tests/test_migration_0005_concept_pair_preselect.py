"""Migration `0005_concept_pair_preselect.sql` (SPECIFICATION §5.13 bảng `chunks`, `concept_pairs`; §5.8).

DB tạo ở version 4 (trước migration này) có chunk và cặp khái niệm: lên version mới thì dữ liệu cũ đọc ra đúng như
trước, `chunks.is_exercise` NULL, `concept_pairs.pre_selected = 0`, `failed_gates_json = '[]'`; CHECK chặn giá trị
lạ; giá trị mới ghi/đọc khứ hồi qua kho; script lỗi không đổi version.
"""

import sqlite3
from pathlib import Path

import pytest

from holocron_core.domain.repositories import VersionConflictError
from holocron_core.domain.vault import ConceptPair, ConceptPairStatus
from holocron_core.infrastructure.storage import SQLiteStore
from holocron_core.infrastructure.storage.connection import connect
from holocron_core.infrastructure.storage.migrator import current_version, load_migrations, migrate

STAMP = "2026-10-20T08:00:00.000000+00:00"
DOC = "doc_aaaaaaaaaaaaaaaa"
CHUNK = "chk_cu"
PAIR = "cp_cu"
RAW = "Tiến trình là một chương trình đang thực thi trong bộ nhớ."


def _version_of_0005() -> int:
    names = [migration.name for migration in load_migrations()]
    return names.index("0005_concept_pair_preselect.sql") + 1


def _database_at_version_4(path: Path) -> None:
    conn = connect(path)
    try:
        migrate(conn, load_migrations()[:4])
        assert current_version(conn) == 4
        conn.execute(
            "INSERT INTO documents (document_id, sha256, filename, media_type, size_bytes, uploaded_at) "
            "VALUES (?, ?, 'a.pdf', 'application/pdf', 10, ?)",
            (DOC, "a" * 64, STAMP),
        )
        conn.execute(
            "INSERT INTO chunks (chunk_id, document_id, subject_code, chapter_code, category, page_number, page_end, "
            "raw_text, verbatim_quote, tags_json, linked_chunk_ids_json, token_count, label_status, "
            "mapping_confidence, version) VALUES (?, ?, 'IT3070', 'ch2', 'THEORY', 1, NULL, ?, ?, '[\"#theory\"]', "
            "'[]', 12, 'confirmed', NULL, 3)",
            (CHUNK, DOC, RAW, RAW),
        )
        conn.execute(
            "INSERT INTO concept_pairs (pair_id, chunk_id, subject_code, chapter_code, term, definition_quote, "
            "term_spans_json, status, proposed_by, mapping_confidence, created_at, confirmed_at, version) "
            "VALUES (?, ?, 'IT3070', 'ch2', 'Tiến trình', ?, '[[0, 10]]', 'proposed', 'llm', 0.5, ?, NULL, 2)",
            (PAIR, CHUNK, RAW, STAMP),
        )
    finally:
        conn.close()


def test_migration_0005_is_numbered_right_after_0004() -> None:
    assert _version_of_0005() == 5


def test_old_chunks_and_pairs_read_back_unchanged_with_spec_defaults(tmp_path: Path) -> None:
    path = tmp_path / "v4.sqlite3"
    _database_at_version_4(path)
    opened = SQLiteStore.open(path)
    try:
        chunk = opened.get_chunk(CHUNK)
        assert chunk is not None
        assert (chunk.raw_text, chunk.label_status, chunk.version, chunk.is_exercise) == (RAW, "confirmed", 3, None)
        pair = opened.get_concept_pair(PAIR)
        assert pair is not None
        assert (pair.term, pair.definition_quote, pair.version, pair.mapping_confidence) == ("Tiến trình", RAW, 2, 0.5)
        assert (pair.pre_selected, pair.failed_gates, pair.status) == (False, [], ConceptPairStatus.PROPOSED)
    finally:
        opened.close()
    raw = sqlite3.connect(path)
    try:
        assert raw.execute("SELECT is_exercise FROM chunks").fetchone() == (None,)
        assert raw.execute("SELECT pre_selected, failed_gates_json FROM concept_pairs").fetchone() == (0, "[]")
        assert raw.execute("SELECT max(version) FROM schema_version").fetchone()[0] == len(load_migrations())
    finally:
        raw.close()


def test_new_values_round_trip_through_the_store(tmp_path: Path) -> None:
    path = tmp_path / "v4.sqlite3"
    _database_at_version_4(path)
    opened = SQLiteStore.open(path)
    try:
        labelled = opened.set_chunk_exercise(CHUNK, True, 3)
        assert (labelled.is_exercise, labelled.version, labelled.label_status) == (True, 4, "confirmed")
        with pytest.raises(VersionConflictError):
            opened.set_chunk_exercise(CHUNK, False, 3)
        pair = ConceptPair(
            chunk_id=CHUNK,
            subject_code="IT3070",
            chapter_code="ch2",
            term="Chương trình",
            definition_quote=RAW,
            status=ConceptPairStatus.PROPOSED,
            proposed_by="llm",
            created_at=STAMP,
            failed_gates=["G4_COLUMN_MIX", "G1_SOURCE"],
        )
        opened.add_concept_pair(pair)
        stored = opened.get_concept_pair(pair.pair_id)
        assert stored is not None and stored.failed_gates == ["G1_SOURCE", "G4_COLUMN_MIX"]
        selected = pair.model_copy(
            update={"pair_id": "cp_moi", "term": "Bộ nhớ", "failed_gates": [], "pre_selected": True}
        )
        opened.add_concept_pair(selected)
        reread = opened.get_concept_pair("cp_moi")
        assert reread is not None and reread.pre_selected and reread.failed_gates == []
        confirmed = opened.update_concept_pair(
            reread.model_copy(update={"status": ConceptPairStatus.CONFIRMED, "confirmed_at": reread.created_at}), 1
        )
        assert confirmed.pre_selected and confirmed.failed_gates == []  # tính một lần, xác nhận không đổi
    finally:
        opened.close()


@pytest.mark.parametrize(
    ("table", "column", "value"),
    [
        ("chunks", "is_exercise", 2),
        ("concept_pairs", "pre_selected", 2),
        ("concept_pairs", "pre_selected", None),
        ("concept_pairs", "failed_gates_json", None),
    ],
)
def test_check_constraints_reject_values_outside_the_spec(
    tmp_path: Path, table: str, column: str, value: object
) -> None:
    path = tmp_path / "v4.sqlite3"
    _database_at_version_4(path)
    SQLiteStore.open(path).close()
    conn = connect(path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(f"UPDATE {table} SET {column} = ?", (value,))
    finally:
        conn.close()


def test_failed_migration_0005_keeps_version_4(tmp_path: Path) -> None:
    conn = connect(tmp_path / "x.sqlite3")
    try:
        migrate(conn, load_migrations()[:4])
        conn.execute("ALTER TABLE concept_pairs ADD COLUMN failed_gates_json TEXT")  # cột đã có: script 0005 lỗi
        with pytest.raises(sqlite3.OperationalError):
            migrate(conn)
        assert current_version(conn) == 4
        columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(chunks)")}
        assert "is_exercise" not in columns, "giao dịch của script lỗi bị huỷ toàn bộ"
    finally:
        conn.close()
