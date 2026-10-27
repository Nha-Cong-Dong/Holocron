"""`DocumentRepository` (SPECIFICATION §5.13, §6.5): cùng ngữ nghĩa trên kho in-memory và SQLite (có/không FTS5).

Gồm tài liệu nhập, trang (so-và-ghi theo version), lịch sử sửa trang `PageEditAudit` và xoá tài liệu kéo theo trang,
chunk, cặp khái niệm. Văn bản là mẫu tự viết (`tests/vault_support.py`).
"""

from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest

from holocron_core.domain.documents import CrossCheck, PageEditAudit, PageExtraction, PageLineChange
from holocron_core.domain.ingestion import IngestionItem
from holocron_core.domain.repositories import (
    AppStore,
    ImportedDocument,
    MissingReferenceError,
    RecordInUseError,
    RecordNotFoundError,
    StorageError,
    VersionConflictError,
)
from holocron_core.domain.schemas import DocumentMeta, SourceKind
from holocron_core.domain.vault import ConceptPair, ConceptPairStatus
from holocron_core.routers import deps
from holocron_core.services.ingestion_gate import recompute_item
from holocron_core.services.vault import ingest_confirmed_text
from tests.test_api_item_state import _body
from tests.vault_support import ARM_PAGES, NOW, STORE_KINDS, add_document, confirmed, open_store

DOC_A = "doc_aaaaaaaaaaaaaaaa"
DOC_B = "doc_bbbbbbbbbbbbbbbb"


@pytest.fixture(params=STORE_KINDS)
def store(request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[AppStore]:
    yield from open_store(str(request.param), tmp_path, monkeypatch)


def _imported(doc_id: str = DOC_A, *, subject_code: str = "ET4010", hours: int = 0) -> ImportedDocument:
    return ImportedDocument(
        meta=DocumentMeta(
            doc_id=doc_id,
            subject_code=subject_code,
            title="Kỹ thuật vi xử lý",
            source_kind=SourceKind.LECTURE_SLIDES,
            sha256=(doc_id[4:] * 4)[:64],
            page_count=2,
            has_text_layer=False,
            uploaded_at=NOW + timedelta(hours=hours),
        ),
        filename="vxl.pdf",
        size_bytes=2048,
        status="processing",
    )


def _page(doc_id: str, page_number: int, *, version: int = 1, text: str | None = None) -> PageExtraction:
    if text is None:
        return PageExtraction(
            document_id=doc_id,
            page_number=page_number,
            version=version,
            status="pending",
            tier_used=None,
            text_score=None,
            cross_check=CrossCheck(status="not_applicable", secondary_engine=None),
        )
    return PageExtraction.model_validate(
        {
            "document_id": doc_id,
            "page_number": page_number,
            "version": version,
            "status": "ok",
            "tier_used": "text_layer",
            "text_score": 0.95,
            "lines": [
                {"line_index": 0, "text": text, "bbox": [0.1, 0.1, 0.9, 0.2], "conf": 0.95, "engine": "text_layer"}
            ],
            "cross_check": {"status": "not_applicable", "secondary_engine": None},
        }
    )


def _audit(doc_id: str = DOC_A, page_number: int = 1, from_version: int = 1) -> PageEditAudit:
    return PageEditAudit(
        document_id=doc_id,
        page_number=page_number,
        from_version=from_version,
        to_version=from_version + 1,
        editor_id="local_user",
        changes=[PageLineChange(line_index=0, old="MOV R0, #0x1E", new="MOV R0, #0x1F")],
        at=NOW,
    )


def _with_pages(store: AppStore, doc_id: str = DOC_A) -> None:
    store.save_imported_document(_imported(doc_id))
    for number in (1, 2):
        store.add_page(doc_id, number, _page(doc_id, number), status="pending", confirmed=False)


def test_imported_documents_round_trip_newest_first_and_filtered(store: AppStore) -> None:
    oldest, newest = _imported(DOC_A), _imported(DOC_B, hours=1)
    other = _imported("doc_cccccccccccccccc", subject_code="IT3070", hours=2)
    for document in (oldest, newest, other):
        assert store.save_imported_document(document) == document
    assert store.get_imported_document(DOC_A) == oldest
    assert [d.meta.doc_id for d in store.list_imported_documents("ET4010")] == [DOC_B, DOC_A]
    assert [d.meta.doc_id for d in store.list_imported_documents()] == ["doc_cccccccccccccccc", DOC_B, DOC_A]
    assert store.get_imported_document("doc_dddddddddddddddd") is None
    uploaded = store.get_document(DOC_A)
    assert uploaded is not None and uploaded.media_type == "application/pdf" and uploaded.size_bytes == 2048


def test_import_fills_in_a_document_first_uploaded_as_an_exam(store: AppStore) -> None:
    add_document(store, DOC_A)
    assert store.get_imported_document(DOC_A) is None, "bản ghi chỉ do upload đề tạo chưa là tài liệu nhập"
    filled = store.save_imported_document(_imported(DOC_A))
    assert filled.version == 2 and filled.meta.subject_code == "ET4010"
    again = store.save_imported_document(_imported(DOC_A))
    assert again.version == 3


def test_imported_document_update_is_compare_and_swap(store: AppStore) -> None:
    document = store.save_imported_document(_imported())
    reviewed = store.update_imported_document(document.model_copy(update={"status": "review"}), expected_version=1)
    assert reviewed.version == 2
    assert store.get_imported_document(DOC_A) == reviewed
    with pytest.raises(VersionConflictError) as conflict:
        store.update_imported_document(document.model_copy(update={"status": "failed"}), expected_version=1)
    assert conflict.value.current_version == 2
    with pytest.raises(RecordNotFoundError):
        store.update_imported_document(_imported(DOC_B), expected_version=1)


def test_pages_round_trip_in_order_and_compare_and_swap(store: AppStore) -> None:
    store.save_imported_document(_imported())
    for number in (2, 1):
        store.add_page(DOC_A, number, _page(DOC_A, number), status="pending", confirmed=False)
    assert [p.page_number for p in store.list_pages(DOC_A, PageExtraction)] == [1, 2]
    read = _page(DOC_A, 1, version=2, text="MOV R0, #0x1F")
    assert store.update_page(DOC_A, 1, read, status="ok", confirmed=False, expected_version=1) == 2
    assert store.get_page(DOC_A, 1, PageExtraction) == read
    with pytest.raises(VersionConflictError) as conflict:
        store.update_page(DOC_A, 1, read, status="ok", confirmed=True, expected_version=1)
    assert conflict.value.current_version == 2
    with pytest.raises(RecordNotFoundError):
        store.update_page(DOC_A, 9, read, status="ok", confirmed=True, expected_version=1)
    assert store.get_page(DOC_A, 9, PageExtraction) is None
    assert store.list_pages(DOC_B, PageExtraction) == []


def test_pages_need_an_existing_document_and_a_free_number(store: AppStore) -> None:
    with pytest.raises(MissingReferenceError):
        store.add_page(DOC_A, 1, _page(DOC_A, 1), status="pending", confirmed=False)
    store.save_imported_document(_imported())
    store.add_page(DOC_A, 1, _page(DOC_A, 1), status="pending", confirmed=False)
    with pytest.raises(StorageError, match="UNIQUE"):
        store.add_page(DOC_A, 1, _page(DOC_A, 1), status="pending", confirmed=False)


def test_page_audit_is_written_with_the_page_and_read_back_in_order(store: AppStore) -> None:
    _with_pages(store)
    assert store.list_page_audits(DOC_A, 1) == []
    first = _audit(from_version=1)
    store.update_page(
        DOC_A, 1, _page(DOC_A, 1, version=2, text="a"), status="ok", confirmed=False, expected_version=1, audit=first
    )
    second = _audit(from_version=2).model_copy(update={"editor_id": "u2"})
    store.update_page(
        DOC_A, 1, _page(DOC_A, 1, version=3, text="b"), status="ok", confirmed=False, expected_version=2, audit=second
    )
    assert store.list_page_audits(DOC_A, 1) == [first, second]
    assert store.list_page_audits(DOC_A, 2) == []


def test_page_audit_is_not_written_when_the_page_write_fails(store: AppStore) -> None:
    _with_pages(store)
    with pytest.raises(VersionConflictError):
        store.update_page(
            DOC_A,
            1,
            _page(DOC_A, 1, text="x"),
            status="ok",
            confirmed=False,
            expected_version=5,
            audit=_audit(from_version=5),
        )
    assert store.list_page_audits(DOC_A, 1) == []


@pytest.mark.parametrize(
    ("audit", "message"),
    [
        (_audit(page_number=2), "không thuộc trang"),
        (_audit(doc_id=DOC_B), "không thuộc trang"),
        (_audit(from_version=2), "from_version"),
    ],
)
def test_page_audit_must_match_the_page_and_version(store: AppStore, audit: PageEditAudit, message: str) -> None:
    _with_pages(store)
    with pytest.raises(ValueError, match=message):
        store.update_page(
            DOC_A,
            1,
            _page(DOC_A, 1, version=2, text="x"),
            status="ok",
            confirmed=False,
            expected_version=1,
            audit=audit,
        )
    assert store.list_page_audits(DOC_A, 1) == []
    page = store.get_page(DOC_A, 1, PageExtraction)
    assert page is not None and page.status == "pending", "audit sai thì trang cũng không được ghi"


def test_page_audit_rejects_a_jump_of_more_than_one_version() -> None:
    with pytest.raises(ValueError, match="to_version"):
        PageEditAudit(document_id=DOC_A, page_number=1, from_version=1, to_version=3, editor_id="u", changes=[], at=NOW)


def test_delete_document_takes_pages_audits_chunks_and_concept_pairs(store: AppStore) -> None:
    _with_pages(store)
    store.update_page(
        DOC_A, 1, _page(DOC_A, 1, version=2, text="a"), status="ok", confirmed=False, expected_version=1, audit=_audit()
    )
    chunks = ingest_confirmed_text(store, confirmed(DOC_A, ARM_PAGES))
    quote = "Cờ Z được đặt khi kết quả phép toán bằng không."
    holder = next(chunk for chunk in chunks if quote in chunk.raw_text)
    pair = ConceptPair(
        chunk_id=holder.chunk_id,
        subject_code=holder.subject_code,
        chapter_code=holder.chapter_code,
        term="Cờ Z",
        definition_quote=quote,
        status=ConceptPairStatus.PROPOSED,
        proposed_by="user",
        created_at=NOW,
    )
    store.add_concept_pair(pair)
    _with_pages(store, DOC_B)
    kept = ingest_confirmed_text(store, confirmed(DOC_B, [["Lệnh SUBS trừ hai thanh ghi và cập nhật cờ."]]))

    assert store.delete_document(DOC_A) is True
    assert store.get_imported_document(DOC_A) is None and store.get_document(DOC_A) is None
    assert store.list_pages(DOC_A, PageExtraction) == []
    assert store.list_page_audits(DOC_A, 1) == []
    assert all(store.get_chunk(chunk.chunk_id) is None for chunk in chunks)
    assert store.get_concept_pair(pair.pair_id) is None
    assert store.get_last_edit("concept_pair", pair.pair_id) is None
    assert [c.chunk_id for c in store.list_chunks("ET4010")] == [c.chunk_id for c in kept]
    assert len(store.list_pages(DOC_B, PageExtraction)) == 2
    assert store.delete_document(DOC_A) is False


def test_delete_document_refuses_while_an_item_still_points_to_it(store: AppStore) -> None:
    _with_pages(store)
    body = {**_body({"quantum": 4}), "source_doc_id": DOC_A}
    store.save_item(recompute_item(IngestionItem(item_id="ing_a", **body), deps.REGISTRY))
    with pytest.raises(RecordInUseError):
        store.delete_document(DOC_A)
    assert store.get_imported_document(DOC_A) is not None
    assert len(store.list_pages(DOC_A, PageExtraction)) == 2
