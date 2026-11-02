"""`DocumentRepository`, phần 2 của bộ nhập (SPECIFICATION §5.13, §6.5): `chapter_code` của tài liệu (migration 0002),
gỡ phần do một lần nhập thêm (`revert_imported_document`) và tóm tắt trang không dựng lại dòng
(`list_page_summaries`). Chạy trên kho in-memory và SQLite (có/không FTS5); văn bản là mẫu tự viết.
"""

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from holocron_core.domain.documents import CrossCheck, PageExtraction, PageSummary
from holocron_core.domain.ingestion import IngestionItem, UploadedDocument
from holocron_core.domain.repositories import AppStore, ImportedDocument, RecordInUseError
from holocron_core.domain.schemas import DocumentMeta, SourceKind
from holocron_core.infrastructure.storage import SQLiteStore
from holocron_core.infrastructure.storage.connection import connect
from holocron_core.infrastructure.storage.migrator import current_version, load_migrations, migrate
from holocron_core.routers import deps
from holocron_core.services.ingestion_gate import recompute_item
from holocron_core.services.vault import ingest_confirmed_text
from tests.test_api_item_state import _body
from tests.vault_support import ARM_PAGES, NOW, STORE_KINDS, confirmed, open_store

DOC = "doc_cccccccccccccccc"
SHA = ("cccccccccccccccc" * 4)[:64]


@pytest.fixture(params=STORE_KINDS)
def store(request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[AppStore]:
    yield from open_store(str(request.param), tmp_path, monkeypatch)


def _imported(chapter_code: str | None = "ch3.2", page_count: int = 2) -> ImportedDocument:
    return ImportedDocument(
        meta=DocumentMeta(
            doc_id=DOC,
            subject_code="ET4010",
            title="Kỹ thuật vi xử lý",
            source_kind=SourceKind.LECTURE_SLIDES,
            sha256=SHA,
            page_count=page_count,
            has_text_layer=False,
            uploaded_at=NOW,
        ),
        filename="vxl.pdf",
        size_bytes=2048,
        chapter_code=chapter_code,
        status="processing",
    )


def _pending(page_number: int) -> PageExtraction:
    return PageExtraction(
        document_id=DOC,
        page_number=page_number,
        status="pending",
        tier_used=None,
        text_score=None,
        cross_check=CrossCheck(status="not_applicable", secondary_engine=None),
    )


def _review_page(page_number: int, version: int) -> PageExtraction:
    return PageExtraction.model_validate(
        {
            "document_id": DOC,
            "page_number": page_number,
            "version": version,
            "status": "needs_review",
            "tier_used": "ocr",
            "text_score": 0.2,
            "ocr_score": 0.9,
            "lines": [
                {
                    "line_index": 0,
                    "text": "MOV R0, #0x1F",
                    "bbox": [0.1, 0.1, 0.9, 0.2],
                    "conf": 0.4,
                    "engine": "tesseract",
                }
            ],
            "cross_check": {"status": "unavailable", "secondary_engine": None},
            "review_reasons": ["CROSS_CHECK_UNAVAILABLE", "LOW_CONF_LINES"],
        }
    )


def _upload(filename: str = "de.pdf") -> UploadedDocument:
    return UploadedDocument(
        document_id=DOC, sha256=SHA, filename=filename, media_type="application/pdf", size_bytes=2048, uploaded_at=NOW
    )


def _create(store: AppStore, chapter_code: str | None = "ch3.2") -> ImportedDocument:
    return store.create_imported_document(_imported(chapter_code), [_pending(1), _pending(2)])


# --------------------------------------------------------------------------- #
# chapter_code
# --------------------------------------------------------------------------- #


def test_chapter_code_round_trips_through_create_get_list_and_update(store: AppStore) -> None:
    created = _create(store)
    assert created.chapter_code == "ch3.2"
    stored = store.get_imported_document(DOC)
    assert stored is not None and stored.chapter_code == "ch3.2"
    assert [d.chapter_code for d in store.list_imported_documents()] == ["ch3.2"]
    updated = store.update_imported_document(stored.model_copy(update={"status": "review"}), stored.version)
    again = store.get_imported_document(DOC)
    assert again is not None and again.chapter_code == "ch3.2" and again.version == updated.version


def test_chapter_code_must_match_the_chunk_pattern() -> None:
    for bad in ("3", "chapter3", "ch123", "ch3.", "CH3"):
        with pytest.raises(ValueError, match="chapter_code"):
            _imported(bad)


def test_sqlite_migration_0002_adds_a_nullable_chapter_code_to_existing_documents(tmp_path: Path) -> None:
    """DB tạo ở version 1 (trước cột): lên version mới thì tài liệu cũ có `chapter_code = None`, không mất dòng nào."""
    path = tmp_path / "v1.sqlite3"
    conn = connect(path)
    migrate(conn, load_migrations()[:1])
    conn.execute(
        "INSERT INTO documents (document_id, sha256, filename, media_type, size_bytes, subject_code, title, "
        "source_kind, output_language, page_count, has_text_layer, status, uploaded_at) "
        "VALUES (?, ?, 'cu.pdf', 'application/pdf', 10, 'ET4010', 'Cũ', 'textbook', 'vi', 1, 0, 'review', ?)",
        (DOC, SHA, NOW.isoformat()),
    )
    conn.close()
    opened = SQLiteStore.open(path)
    try:
        legacy = opened.get_imported_document(DOC)
        assert legacy is not None and legacy.chapter_code is None and legacy.meta.title == "Cũ"
    finally:
        opened.close()
    raw = connect(path)
    try:
        assert current_version(raw) == len(load_migrations()) >= 2
        columns = {str(row[1]) for row in raw.execute("PRAGMA table_info(documents)")}
        assert "chapter_code" in columns
    finally:
        raw.close()


def test_sqlite_migration_0002_failure_keeps_version_1(tmp_path: Path) -> None:
    conn = connect(tmp_path / "x.sqlite3")
    migrate(conn, load_migrations()[:1])
    conn.execute("ALTER TABLE documents ADD COLUMN chapter_code TEXT")  # cột đã có: script 0002 lỗi
    with pytest.raises(sqlite3.OperationalError):
        migrate(conn)
    assert current_version(conn) == 1
    conn.close()


# --------------------------------------------------------------------------- #
# revert_imported_document
# --------------------------------------------------------------------------- #


def test_revert_without_a_prior_upload_removes_everything(store: AppStore) -> None:
    _create(store)
    assert store.revert_imported_document(DOC, None) is True
    assert store.get_imported_document(DOC) is None and store.get_document(DOC) is None
    assert store.list_pages(DOC, PageExtraction) == [] and store.list_page_summaries(DOC) == []
    assert store.revert_imported_document(DOC, None) is False


def test_revert_keeps_the_exam_upload_row_its_items_and_drops_import_data(store: AppStore) -> None:
    upload = _upload()
    store.save_document(upload)
    item = IngestionItem(item_id="ing_giu", **{**_body({"quantum": 4}), "source_doc_id": DOC})
    store.save_item(recompute_item(item, deps.REGISTRY))
    created = _create(store)
    assert store.get_document(DOC) != upload, "bộ nhập bổ sung thông tin vào cùng dòng"
    ingest_confirmed_text(store, confirmed(DOC, ARM_PAGES))
    assert store.list_chunks("ET4010")
    page = store.get_page(DOC, 1, PageExtraction)
    assert page is not None
    store.update_page(
        DOC, 1, _review_page(1, page.version + 1), status="needs_review", confirmed=False, expected_version=page.version
    )

    assert store.revert_imported_document(DOC, upload) is True
    assert store.get_imported_document(DOC) is None
    assert store.get_document(DOC) == upload
    assert store.get_item("ing_giu") is not None
    assert store.list_pages(DOC, PageExtraction) == [] and store.list_page_audits(DOC, 1) == []
    assert store.list_chunks("ET4010") == []
    assert store.list_imported_documents() == []
    recreated = _create(store, chapter_code="ch4")
    assert recreated.chapter_code == "ch4" and recreated.version > created.version


def test_revert_without_upload_refuses_when_an_item_points_to_the_document(store: AppStore) -> None:
    _create(store)
    item = IngestionItem(item_id="ing_tro", **{**_body({"quantum": 4}), "source_doc_id": DOC})
    store.save_item(recompute_item(item, deps.REGISTRY))
    with pytest.raises(RecordInUseError):
        store.revert_imported_document(DOC, None)
    assert store.get_imported_document(DOC) is not None
    assert len(store.list_pages(DOC, PageExtraction)) == 2


def test_revert_of_an_exam_only_record_does_nothing(store: AppStore) -> None:
    upload = _upload()
    store.save_document(upload)
    assert store.revert_imported_document(DOC, upload) is False
    assert store.get_document(DOC) == upload


# --------------------------------------------------------------------------- #
# list_page_summaries
# --------------------------------------------------------------------------- #


def test_page_summaries_match_the_pages_and_follow_writes(store: AppStore) -> None:
    _create(store)
    assert store.list_page_summaries(DOC) == [PageSummary.from_page(p) for p in store.list_pages(DOC, PageExtraction)]
    page = store.get_page(DOC, 2, PageExtraction)
    assert page is not None
    review = _review_page(2, page.version + 1)
    store.update_page(DOC, 2, review, status="needs_review", confirmed=False, expected_version=page.version)
    confirmed_page = review.model_copy(update={"confirmed": True, "confirmed_at": NOW, "version": review.version + 1})
    store.update_page(DOC, 2, confirmed_page, status="needs_review", confirmed=True, expected_version=review.version)
    summaries = store.list_page_summaries(DOC)
    assert summaries == [PageSummary.from_page(p) for p in store.list_pages(DOC, PageExtraction)]
    assert summaries[1] == PageSummary(
        page_number=2,
        status="needs_review",
        tier_used="ocr",
        review_reasons=["LOW_CONF_LINES", "CROSS_CHECK_UNAVAILABLE"],
        confirmed=True,
    )
    assert store.list_page_summaries("doc_0000000000000000") == []


def test_page_summaries_do_not_rebuild_the_lines(store: AppStore, monkeypatch: pytest.MonkeyPatch) -> None:
    """Hỏi trạng thái 1–2 s một lần (§6.5): tóm tắt trang không validate lại danh sách dòng của trang nào."""
    _create(store)
    page = store.get_page(DOC, 1, PageExtraction)
    assert page is not None
    store.update_page(
        DOC, 1, _review_page(1, page.version + 1), status="needs_review", confirmed=False, expected_version=page.version
    )

    def no_parse(*args: object, **kwargs: object) -> PageExtraction:
        raise AssertionError("list_page_summaries không được dựng lại PageExtraction")

    monkeypatch.setattr(PageExtraction, "model_validate_json", no_parse)
    assert [s.status for s in store.list_page_summaries(DOC)] == ["needs_review", "pending"]
