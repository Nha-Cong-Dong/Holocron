"""Migration khối `0006_blocks_sections.sql` (SPECIFICATION §5.13 "Khối, mục, ảnh cắt, ledger"; ADR-010 mục 13, 17).

DB tạo ở version 5 (trước migration khối) có tài liệu nhập, trang và chunk: lên version mới thì dữ liệu cũ đọc ra như
trước, tài liệu cũ có `import_generation = 0`, `layout_version`/`layout_settings_sha256` null, `blocks_after_close`
false, không có khối/mục/ledger/ảnh cắt; chunk cũ `section_id` null, `block_ids` rỗng (I4: không tự nhập lại).
"""

import sqlite3
from pathlib import Path

from holocron_core.domain.documents import PageExtraction
from holocron_core.infrastructure.storage import SQLiteStore
from holocron_core.infrastructure.storage.connection import connect
from holocron_core.infrastructure.storage.migrator import current_version, load_migrations, migrate
from tests.block_samples import extraction_for, geo_line, geometry

STAMP = "2026-10-20T08:00:00.000000+00:00"
DOC = "doc_aaaaaaaaaaaaaaaa"
CHUNK = "chk_cu"
RAW = "Tiến trình là một chương trình đang thực thi trong bộ nhớ."
TABLES = ("import_generations", "blocks", "sections", "crop_images", "extraction_ledgers", "structure_proposals")


def _page_json(page_number: int) -> str:
    geo = geometry([geo_line(f"Dong {page_number} cua tai lieu cu", 0.1, 0.2)])
    return extraction_for(geo, page_number, document_id=DOC).model_dump_json()


def _database_at_version_5(path: Path) -> None:
    conn = connect(path)
    try:
        migrate(conn, load_migrations()[:5])
        assert current_version(conn) == 5
        conn.execute(
            "INSERT INTO documents (document_id, sha256, filename, media_type, size_bytes, subject_code, title, "
            "source_kind, output_language, page_count, has_text_layer, status, uploaded_at, chapter_code, "
            "chapter_title, chapter_code_source, chapter_title_source, version) VALUES (?, ?, 'cu.pdf', "
            "'application/pdf', 10, 'IT3070', 'Tài liệu cũ', 'lecture_slides', 'vi', 2, 1, 'confirmed', ?, 'ch2', "
            "'Tiến trình', 'user', 'user', 4)",
            (DOC, "a" * 64, STAMP),
        )
        for number in (1, 2):
            conn.execute(
                "INSERT INTO pages (document_id, page_number, status, extraction_json, confirmed, updated_at, version) "
                "VALUES (?, ?, 'ok', ?, 1, ?, 3)",
                (DOC, number, _page_json(number), STAMP),
            )
        conn.execute(
            "INSERT INTO chunks (chunk_id, document_id, subject_code, chapter_code, category, page_number, page_end, "
            "raw_text, verbatim_quote, tags_json, linked_chunk_ids_json, token_count, label_status, "
            "mapping_confidence, version) VALUES (?, ?, 'IT3070', 'ch2', 'THEORY', 1, 2, ?, ?, '[]', '[]', 12, "
            "'proposed', NULL, 1)",
            (CHUNK, DOC, RAW, RAW),
        )
    finally:
        conn.close()


def test_migration_blocks_is_numbered_after_0005() -> None:
    names = [migration.name for migration in load_migrations()]
    assert names.index("0006_blocks_sections.sql") == names.index("0005_concept_pair_preselect.sql") + 1


def test_block_migration_on_db_with_prior_data(tmp_path: Path) -> None:
    path = tmp_path / "v5.sqlite3"
    _database_at_version_5(path)
    store = SQLiteStore.open(path)
    try:
        document = store.get_imported_document(DOC)
        assert document is not None
        assert document.import_generation == 0
        assert (document.layout_version, document.layout_settings_sha256, document.blocks_after_close) == (
            None,
            None,
            False,
        )
        assert (document.status, document.chapter_code, document.chapter_title, document.version) == (
            "confirmed",
            "ch2",
            "Tiến trình",
            4,
        )
        assert [page.page_number for page in store.list_pages(DOC, PageExtraction)] == [1, 2]
        assert store.get_page_geometry(DOC, 1) is None
        assert store.list_blocks(DOC) == []
        assert store.list_sections(DOC) == []
        assert store.list_extraction_ledgers(DOC) == []
        assert store.list_crop_images(DOC) == []
        assert store.list_structure_proposals(DOC) == []
        assert store.get_chunk(CHUNK) is not None
    finally:
        store.close()
    raw = sqlite3.connect(path)
    try:
        assert raw.execute("SELECT max(version) FROM schema_version").fetchone()[0] == len(load_migrations())
        assert raw.execute(
            "SELECT layout_version, layout_settings_sha256, blocks_after_close, import_generation FROM documents"
        ).fetchall() == [(None, None, 0, 0)]
        assert raw.execute("SELECT count(*) FROM pages WHERE geometry_json IS NOT NULL").fetchone() == (0,)
        assert raw.execute("SELECT section_id, block_ids_json FROM chunks").fetchall() == [(None, "[]")]
        for table in TABLES:
            assert raw.execute(f"SELECT count(*) FROM {table}").fetchone() == (0,)
    finally:
        raw.close()


def test_legacy_document_keeps_raw_text_after_migration(tmp_path: Path) -> None:
    path = tmp_path / "v5.sqlite3"
    _database_at_version_5(path)
    before = [PageExtraction.model_validate_json(_page_json(number)) for number in (1, 2)]
    store = SQLiteStore.open(path)
    try:
        chunk = store.get_chunk(CHUNK)
        assert chunk is not None
        assert (chunk.raw_text, chunk.verbatim_quote, chunk.page_number, chunk.page_end) == (RAW, RAW, 1, 2)
        assert store.list_pages(DOC, PageExtraction) == before
        assert store.list_eligible_blocks(DOC) == []
        assert store.refresh_eligible_blocks(DOC) == 0
    finally:
        store.close()
    reopened = SQLiteStore.open(path)  # mở lại không chạy migration lần hai, không đổi dữ liệu
    try:
        again = reopened.get_chunk(CHUNK)
        assert again is not None and again.raw_text == RAW
        assert reopened.list_pages(DOC, PageExtraction) == before
    finally:
        reopened.close()


def test_block_tables_cascade_with_pages_and_generations_survive(tmp_path: Path) -> None:
    path = tmp_path / "v5.sqlite3"
    _database_at_version_5(path)
    SQLiteStore.open(path).close()
    raw = sqlite3.connect(path)
    try:
        raw.execute("PRAGMA foreign_keys = ON")
        raw.execute("INSERT INTO import_generations (document_id, last_generation) VALUES (?, 3)", (DOC,))
        raw.execute(
            "INSERT INTO blocks (document_id, block_id, page_number, reading_order, type, block_json) "
            "VALUES (?, 'blk_0000000000000001', 1, 0, 'paragraph', '{}')",
            (DOC,),
        )
        raw.execute(
            "INSERT INTO extraction_ledgers (document_id, page_number, balanced, ledger_json) VALUES (?, 1, 1, '{}')",
            (DOC,),
        )
        raw.execute(
            "INSERT INTO sections (document_id, section_id, parent_id, ordinal, section_json) "
            "VALUES (?, 'sec_0000000000000001', NULL, 0, '{}')",
            (DOC,),
        )
        raw.execute(
            "INSERT INTO crop_images (document_id, block_id, crop_ref, page_number, bbox_json, width_px, height_px, "
            "size_bytes, created_at) VALUES (?, 'blk_0000000000000001', ?, 1, '[0,0,1,1]', 1, 1, 1, ?)",
            (DOC, "b" * 64, STAMP),
        )
        raw.execute("DELETE FROM chunks")
        raw.execute("DELETE FROM documents")
        raw.commit()
        for table in ("blocks", "sections", "crop_images", "extraction_ledgers"):
            assert raw.execute(f"SELECT count(*) FROM {table}").fetchone() == (0,)
        assert raw.execute("SELECT last_generation FROM import_generations").fetchall() == [(3,)]
    finally:
        raw.close()
