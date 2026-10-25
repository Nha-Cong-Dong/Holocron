"""`DocumentRepository.create_imported_document` nguyên tử và các bất biến của `delete_document` (SPECIFICATION §5.13,
§6.5) trên cả ba loại kho.

Văn bản mẫu tự viết cho test (không phải tài liệu môn học có bản quyền).
"""

import sqlite3
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from pydantic import BaseModel

from holocron_core.domain.documents import PageExtraction
from holocron_core.domain.ingestion import UploadedDocument
from holocron_core.domain.repositories import (
    AppStore,
    DocumentExistsError,
    ImportedDocument,
    StorageBusyError,
    StorageError,
)
from holocron_core.domain.schemas import ChunkCategory, DocumentMeta, SourceKind
from holocron_core.infrastructure import memory_store
from holocron_core.infrastructure.memory_store import IngestionStore
from holocron_core.infrastructure.storage import SQLiteStore, sqlite_store
from holocron_core.services.document_import import pending_page
from holocron_core.services.vault import ingest_confirmed_text
from tests.vault_support import ARM_PAGES, NOW, STORE_KINDS, add_document, confirmed, document_id, open_store

SHA = "ab" * 32
DOC_ID = f"doc_{SHA[:16]}"
TOPIC = "mp.arm.cpsr.flags"


@pytest.fixture(params=STORE_KINDS)
def store(request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[AppStore]:
    yield from open_store(str(request.param), tmp_path, monkeypatch)


def _document(page_count: int = 3, *, doc_id: str = DOC_ID, sha256: str = SHA) -> ImportedDocument:
    return ImportedDocument(
        meta=DocumentMeta(
            doc_id=doc_id,
            subject_code="ET4010",
            title="Bài giảng mẫu",
            source_kind=SourceKind.LECTURE_SLIDES,
            sha256=sha256,
            page_count=page_count,
            has_text_layer=False,
            uploaded_at=NOW,
        ),
        filename="bai_giang.pdf",
        size_bytes=100,
        status="processing",
    )


def _pages(page_count: int = 3, doc_id: str = DOC_ID) -> list[PageExtraction]:
    return [pending_page(doc_id, number) for number in range(1, page_count + 1)]


def _statuses(store: AppStore, doc_id: str = DOC_ID) -> list[str]:
    return [page.status for page in store.list_pages(doc_id, PageExtraction)]


def test_create_writes_the_document_and_every_pending_page(store: AppStore) -> None:
    stored = store.create_imported_document(_document(), _pages())
    assert stored.meta.doc_id == DOC_ID and stored.status == "processing" and stored.version == 1
    assert store.get_imported_document(DOC_ID) == stored
    assert _statuses(store) == ["pending"] * 3
    page = store.get_page(DOC_ID, 2, PageExtraction)
    assert page is not None and page.version == 1 and not page.confirmed


def test_create_refuses_an_existing_imported_document_without_writing(store: AppStore) -> None:
    store.create_imported_document(_document(), _pages())
    with pytest.raises(DocumentExistsError) as raised:
        store.create_imported_document(_document(2), _pages(2))
    assert raised.value.document_id == DOC_ID
    stored = store.get_imported_document(DOC_ID)
    assert stored is not None and stored.meta.page_count == 3 and _statuses(store) == ["pending"] * 3


def test_create_supplements_a_record_made_by_the_exam_upload(store: AppStore) -> None:
    store.save_document(
        UploadedDocument(
            document_id=DOC_ID,
            sha256=SHA,
            filename="de.pdf",
            media_type="application/pdf",
            size_bytes=100,
            uploaded_at=NOW,
        )
    )
    assert store.get_imported_document(DOC_ID) is None
    stored = store.create_imported_document(_document(2), _pages(2))
    assert stored.meta.subject_code == "ET4010" and stored.filename == "bai_giang.pdf"
    assert _statuses(store) == ["pending", "pending"]


def test_create_requires_one_page_per_page_count(store: AppStore) -> None:
    with pytest.raises(ValueError, match="page_count"):
        store.create_imported_document(_document(3), _pages(2))
    assert store.get_imported_document(DOC_ID) is None and store.get_document(DOC_ID) is None


def _failing_dump(real: Callable[[BaseModel], str], page_number: int) -> Callable[[BaseModel], str]:
    def dump(model: BaseModel) -> str:
        if isinstance(model, PageExtraction) and model.page_number == page_number:
            raise StorageBusyError("kho bận giả lập")
        return real(model)

    return dump


@pytest.mark.parametrize("failing_page", [1, 2, 3])
def test_storage_busy_at_page_k_leaves_no_record(
    store: AppStore, monkeypatch: pytest.MonkeyPatch, failing_page: int
) -> None:
    module = memory_store if isinstance(store, IngestionStore) else sqlite_store
    monkeypatch.setattr(module, "_dump", _failing_dump(module._dump, failing_page))
    with pytest.raises(StorageBusyError):
        store.create_imported_document(_document(), _pages())
    assert store.get_imported_document(DOC_ID) is None and store.get_document(DOC_ID) is None
    assert store.list_pages(DOC_ID, PageExtraction) == [] and store.list_imported_documents() == []
    monkeypatch.undo()
    assert store.create_imported_document(_document(), _pages()).version == 1, "kho không kẹt giao dịch dở"


def test_sqlite_failure_at_a_later_page_rolls_back_the_whole_transaction(tmp_path: Path) -> None:
    """Lỗi ràng buộc thật của SQLite ở trang 3 (trigger tạm), không phải lỗi giả lập ở Python."""
    store = SQLiteStore.open(tmp_path / "trigger.sqlite3")
    try:
        conn = sqlite3.connect(tmp_path / "trigger.sqlite3")
        conn.execute(
            "CREATE TRIGGER fail_page_3 BEFORE INSERT ON pages WHEN NEW.page_number = 3 "
            "BEGIN SELECT RAISE(ABORT, 'giả lập lỗi trang 3'); END"
        )
        conn.commit()
        conn.close()
        with pytest.raises(StorageError):
            store.create_imported_document(_document(), _pages())
        assert store.get_document(DOC_ID) is None and store.list_pages(DOC_ID, PageExtraction) == []
    finally:
        store.close()


@pytest.mark.parametrize("method", ["save", "create"])
def test_memory_store_refuses_a_sha256_owned_by_another_document(method: str) -> None:
    store = IngestionStore()
    other_id = "doc_" + "cd" * 8
    store.create_imported_document(_document(1), _pages(1))
    clash = _document(1, doc_id=other_id)
    with pytest.raises(StorageError, match="sha256"):
        if method == "save":
            store.save_imported_document(clash)
        else:
            store.create_imported_document(clash, _pages(1, other_id))
    assert store.get_imported_document(other_id) is None and store.list_pages(other_id, PageExtraction) == []


# --------------------------------------------------------------------------- #
# Xoá tài liệu: liên kết của chunk còn lại và chỉ mục FTS5
# --------------------------------------------------------------------------- #


def test_delete_recomputes_links_of_the_remaining_chunks(store: AppStore) -> None:
    theory_doc, practice_doc = document_id("a"), document_id("b")
    add_document(store, theory_doc)
    add_document(store, practice_doc)
    theory = ingest_confirmed_text(store, confirmed(theory_doc, ARM_PAGES, topic_keys=[TOPIC]))
    practice = ingest_confirmed_text(
        store,
        confirmed(
            practice_doc,
            [["Bài 1", "Cho R1 = 1 và R2 = 2, tính cờ sau lệnh ADDS."]],
            category="PRACTICE",
            topic_keys=[TOPIC],
        ),
    )
    assert practice[0].linked_chunk_ids == [theory[0].chunk_id]
    assert store.delete_document(theory_doc) is True
    remaining = store.get_chunk(practice[0].chunk_id)
    assert remaining is not None and remaining.linked_chunk_ids == []
    assert remaining.category is ChunkCategory.PRACTICE and remaining.version == practice[0].version
    assert all(store.get_chunk(chunk.chunk_id) is None for chunk in theory)


def test_sqlite_delete_leaves_no_fts_rows_of_the_deleted_document(tmp_path: Path) -> None:
    path = tmp_path / "fts.sqlite3"
    store = SQLiteStore.open(path)
    try:
        if not store.vault_search_available:
            pytest.skip("SQLite của máy này không có FTS5")
        kept_doc, deleted_doc = document_id("c"), document_id("d")
        add_document(store, kept_doc)
        add_document(store, deleted_doc)
        deleted = ingest_confirmed_text(store, confirmed(deleted_doc, ARM_PAGES))
        kept = ingest_confirmed_text(store, confirmed(kept_doc, [["Thanh ghi CPSR giữ cờ của bộ vi xử lý."]]))
        assert set(store.match_chunk_ids("ET4010", "cpsr")) == {c.chunk_id for c in [*deleted, *kept]}
        assert store.delete_document(deleted_doc) is True
        assert store.match_chunk_ids("ET4010", "cpsr") == [kept[0].chunk_id]
        assert store.match_chunk_ids("ET4010", "cpsr", index="no_diacritics") == [kept[0].chunk_id]
        conn = sqlite3.connect(path)
        try:
            for table in ("chunks_fts", "chunks_fts_nodiac"):
                rows = conn.execute(f"SELECT chunk_id FROM {table}").fetchall()
                assert [row[0] for row in rows] == [kept[0].chunk_id], table
        finally:
            conn.close()
    finally:
        store.close()
