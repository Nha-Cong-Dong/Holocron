"""Migration `0004_document_chapter_title.sql` và đọc/ghi các cột của nó (SPECIFICATION §5.13 bảng `documents`, §5.7 "Chương tự động").

DB tạo ở version 3 (trước migration này): lên version mới thì dữ liệu cũ đọc ra đúng như trước, các cột mới có giá
trị mặc định của spec (bản ghi có `chapter_code` nhận nguồn `user`), ràng buộc CHECK chặn giá trị lạ, và script lỗi
không đổi version. Gỡ một lần nhập trên dòng của upload đề trả các cột mới về mặc định.
"""

import sqlite3
from pathlib import Path

import pytest

from holocron_core.domain.documents import CHAPTER_FLAG_ORDER
from holocron_core.domain.ingestion import UploadedDocument
from holocron_core.domain.repositories import ImportedDocument
from holocron_core.domain.schemas import DocumentMeta, SourceKind
from holocron_core.infrastructure.storage import SQLiteStore
from holocron_core.infrastructure.storage.connection import connect
from holocron_core.infrastructure.storage.migrator import current_version, load_migrations, migrate
from holocron_core.services.document_import import pending_page
from tests.vault_support import STORE_KINDS, open_store

STAMP = "2026-10-20T08:00:00.000000+00:00"
IMPORTED = "doc_aaaaaaaaaaaaaaaa"
LEGACY = "doc_bbbbbbbbbbbbbbbb"
EXAM_ONLY = "doc_cccccccccccccccc"
NEW_COLUMNS = ("chapter_title", "chapter_code_source", "chapter_title_source", "chapter_flags_json", "auto_confirm")


def _version_of_0004() -> int:
    names = [migration.name for migration in load_migrations()]
    return names.index("0004_document_chapter_title.sql") + 1


def _database_at_version_3(path: Path) -> None:
    """Ba dòng `documents`: nhập qua §6.5 có `chapter_code`, nhập trước migration 0002 (`chapter_code` NULL), và dòng
    chỉ do upload đề §6.1 tạo."""
    conn = connect(path)
    try:
        migrate(conn, load_migrations()[:3])
        assert current_version(conn) == 3
        imported = (
            "INSERT INTO documents (document_id, sha256, filename, media_type, size_bytes, subject_code, title, "
            "source_kind, output_language, page_count, has_text_layer, status, uploaded_at, chapter_code) "
            "VALUES (?, ?, ?, 'application/pdf', 10, 'ET4010', ?, 'textbook', 'vi', 1, 1, 'review', ?, ?)"
        )
        conn.execute(imported, (IMPORTED, "a" * 64, "chuong2.pdf", "Chương 2", STAMP, "ch2"))
        conn.execute(imported, (LEGACY, "b" * 64, "cu.pdf", "Cũ", STAMP, None))
        conn.execute(
            "INSERT INTO documents (document_id, sha256, filename, media_type, size_bytes, uploaded_at) "
            "VALUES (?, ?, 'de.png', 'image/png', 10, ?)",
            (EXAM_ONLY, "c" * 64, STAMP),
        )
    finally:
        conn.close()


def _new_columns(path: Path) -> dict[str, tuple[object, ...]]:
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(f"SELECT document_id, {', '.join(NEW_COLUMNS)} FROM documents ORDER BY document_id")
        return {str(row[0]): tuple(row[1:]) for row in rows}
    finally:
        conn.close()


def test_migration_0004_is_numbered_right_after_0003() -> None:
    assert _version_of_0004() == 4


def test_old_documents_read_back_unchanged_with_spec_defaults(tmp_path: Path) -> None:
    path = tmp_path / "v3.sqlite3"
    _database_at_version_3(path)
    opened = SQLiteStore.open(path)
    try:
        imported = opened.get_imported_document(IMPORTED)
        assert imported is not None
        assert (imported.chapter_code, imported.meta.title, imported.filename, imported.status) == (
            "ch2",
            "Chương 2",
            "chuong2.pdf",
            "review",
        )
        legacy = opened.get_imported_document(LEGACY)
        assert legacy is not None and legacy.chapter_code is None and legacy.meta.title == "Cũ"
        assert opened.get_imported_document(EXAM_ONLY) is None
        exam = opened.get_document(EXAM_ONLY)
        assert exam is not None and exam.filename == "de.png"
        assert [document.meta.doc_id for document in opened.list_imported_documents("ET4010")] == [LEGACY, IMPORTED]
    finally:
        opened.close()
    assert _new_columns(path) == {
        IMPORTED: (None, "user", None, "[]", 1),
        LEGACY: (None, None, None, "[]", 1),
        EXAM_ONLY: (None, None, None, "[]", 1),
    }
    raw = connect(path)
    try:
        assert current_version(raw) == len(load_migrations()) >= 4
    finally:
        raw.close()


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("chapter_code_source", "llm"),
        ("chapter_title_source", "next_free"),
        ("auto_confirm", 2),
        ("chapter_title", ""),
        ("chapter_title", "x" * 301),
        ("chapter_flags_json", None),
        ("auto_confirm", None),
    ],
)
def test_check_constraints_reject_values_outside_the_spec(tmp_path: Path, column: str, value: object) -> None:
    path = tmp_path / "v3.sqlite3"
    _database_at_version_3(path)
    SQLiteStore.open(path).close()
    conn = connect(path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(f"UPDATE documents SET {column} = ? WHERE document_id = ?", (value, IMPORTED))
    finally:
        conn.close()


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("chapter_code_source", "next_free"),
        ("chapter_title_source", "repeated_header"),
        ("auto_confirm", 0),
        ("chapter_title", "x" * 300),
        ("chapter_flags_json", '["CHAPTER_NUMBER_TAKEN"]'),
    ],
)
def test_check_constraints_accept_values_of_the_spec(tmp_path: Path, column: str, value: object) -> None:
    path = tmp_path / "v3.sqlite3"
    _database_at_version_3(path)
    SQLiteStore.open(path).close()
    conn = connect(path)
    try:
        conn.execute(f"UPDATE documents SET {column} = ? WHERE document_id = ?", (value, IMPORTED))
        stored = conn.execute(f"SELECT {column} FROM documents WHERE document_id = ?", (IMPORTED,)).fetchone()
        assert stored[0] == value
    finally:
        conn.close()


def test_failed_migration_0004_keeps_version_3(tmp_path: Path) -> None:
    conn = connect(tmp_path / "x.sqlite3")
    try:
        migrate(conn, load_migrations()[:3])
        conn.execute("ALTER TABLE documents ADD COLUMN auto_confirm INTEGER")  # cột đã có: script 0004 lỗi
        with pytest.raises(sqlite3.OperationalError):
            migrate(conn)
        assert current_version(conn) == 3
        columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(documents)")}
        assert "chapter_title" not in columns, "giao dịch của script lỗi bị huỷ toàn bộ"
    finally:
        conn.close()


def test_revert_over_an_exam_upload_resets_the_new_columns(tmp_path: Path) -> None:
    """Dòng do upload đề tạo rồi được bộ nhập bổ sung: gỡ lần nhập trả mọi cột của bộ nhập, kể cả cột của 0004, về
    trạng thái của dòng upload đề."""
    path = tmp_path / "v3.sqlite3"
    _database_at_version_3(path)
    opened = SQLiteStore.open(path)
    try:
        upload = UploadedDocument(
            document_id=IMPORTED,
            sha256="a" * 64,
            filename="chuong2.pdf",
            media_type="application/pdf",
            size_bytes=10,
            uploaded_at=STAMP,
        )
        conn = connect(path)
        try:
            conn.execute(
                "UPDATE documents SET chapter_title = 'Bộ nhớ', chapter_code_source = 'file_name', "
                "chapter_title_source = 'file_name', chapter_flags_json = '[\"CHAPTER_NUMBER_TAKEN\"]', "
                "auto_confirm = 0 WHERE document_id = ?",
                (IMPORTED,),
            )
        finally:
            conn.close()
        assert opened.revert_imported_document(IMPORTED, upload) is True
        assert opened.get_imported_document(IMPORTED) is None
        assert opened.get_document(IMPORTED) == upload
    finally:
        opened.close()
    assert _new_columns(path)[IMPORTED] == (None, None, None, "[]", 1)


def test_store_reads_the_new_columns_of_old_rows(tmp_path: Path) -> None:
    path = tmp_path / "v3.sqlite3"
    _database_at_version_3(path)
    opened = SQLiteStore.open(path)
    try:
        imported = opened.get_imported_document(IMPORTED)
        assert imported is not None
        assert (
            imported.chapter_code_source,
            imported.chapter_title,
            imported.chapter_title_source,
            imported.chapter_flags,
            imported.auto_confirm,
        ) == ("user", None, None, [], True)
        legacy = opened.get_imported_document(LEGACY)
        assert legacy is not None and legacy.chapter_code_source is None and legacy.chapter_title is None
    finally:
        opened.close()


def _document(chapter: dict[str, object]) -> ImportedDocument:
    return ImportedDocument.model_validate(
        {
            "meta": DocumentMeta(
                doc_id=IMPORTED,
                subject_code="ET4010",
                title="Chương 2",
                source_kind=SourceKind.TEXTBOOK,
                sha256="a" * 64,
                page_count=1,
                has_text_layer=True,
                uploaded_at=STAMP,
            ),
            "filename": "chuong2.pdf",
            "size_bytes": 10,
            "chapter_code": "ch1",
            "status": "processing",
            **chapter,
        }
    )


@pytest.mark.parametrize("kind", STORE_KINDS)
def test_chapter_columns_round_trip_through_create_get_list_and_update(
    kind: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chapter: dict[str, object] = {
        "chapter_code_source": "next_free",
        "chapter_title": "Chương 2 Bộ nhớ",
        "chapter_title_source": "file_name",
        "chapter_flags": [
            "CHAPTER_NUMBER_TAKEN",
            "CHAPTER_NUMBER_SHARED",
            "CHAPTER_NUMBER_NOT_IN_FILENAME",
            "CHAPTER_NUMBER_TAKEN",
        ],
        "auto_confirm": False,
    }
    for store in open_store(kind, tmp_path, monkeypatch):
        created = store.create_imported_document(_document(chapter), [pending_page(IMPORTED, 1)])
        expected = ("next_free", "Chương 2 Bộ nhớ", "file_name", list(CHAPTER_FLAG_ORDER), False)

        def fields(document: ImportedDocument | None) -> tuple[object, ...]:
            assert document is not None
            return (
                document.chapter_code_source,
                document.chapter_title,
                document.chapter_title_source,
                document.chapter_flags,
                document.auto_confirm,
            )

        assert fields(created) == expected
        assert fields(store.get_imported_document(IMPORTED)) == expected
        assert [fields(document) for document in store.list_imported_documents("ET4010")] == [expected]
        changed = created.model_copy(
            update={"chapter_title": "Ngắt", "chapter_title_source": "user", "chapter_flags": [], "auto_confirm": True}
        )
        store.update_imported_document(changed, created.version)
        assert fields(store.get_imported_document(IMPORTED)) == ("next_free", "Ngắt", "user", [], True)


def test_title_and_title_source_go_together() -> None:
    for bad in ({"chapter_title": "x"}, {"chapter_title_source": "user"}):
        with pytest.raises(ValueError, match="chapter_title"):
            _document(bad)
