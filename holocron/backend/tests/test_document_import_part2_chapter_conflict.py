"""`POST /v1/documents` cùng nội dung với tài liệu đã nhập (SPECIFICATION §6.5 bước 3, §5.7): thứ tự kiểm khác môn
⇒ `SUBJECT_CONFLICT`; bản ghi `chapter_code = null` ⇒ `CHAPTER_MISSING_DETAIL`; khác chương ⇒ `CHAPTER_CONFLICT`
(409, duyệt 2026-10-26 chiều); cùng môn cùng chương ⇒ 200. Kiểm ở cả đường nhận lần đầu (tài liệu đã có khi lấy
khoá) và đường đua `DocumentExistsError` (tiến trình khác tạo trước giữa hai bước). Bản ghi chỉ do §6.1 tạo vẫn
nhập được (202).

Chạy trên cả ba loại kho. Nội dung PDF là byte tự tạo, chỉ `FakePdfReader` đọc.
"""

import hashlib
from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest

from holocron_core.domain.documents import PageExtraction
from holocron_core.domain.ingestion import UploadedDocument
from holocron_core.domain.repositories import AppStore, DocumentExistsError, ImportedDocument
from holocron_core.routers.api import app
from holocron_core.services.document_import import (
    CHAPTER_MISSING_DETAIL,
    IMPORT_ERROR_MESSAGES,
    SynchronousImportQueue,
)
from tests.document_import_support import FakePdfReader, ImportEnv, install, pdf_bytes
from tests.vault_support import STORE_KINDS, open_store

SUBJECT = "ET4010"
OTHER_SUBJECT = "IT3070"
CHAPTER = "ch3"
OTHER_CHAPTER = "ch4.1"


@pytest.fixture(params=STORE_KINDS)
def env(request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ImportEnv]:
    for store in open_store(str(request.param), tmp_path, monkeypatch):
        yield install(store, tmp_path / "files", SynchronousImportQueue(), FakePdfReader(page_count=2))
        app.dependency_overrides.clear()


def _doc_id(content: bytes) -> str:
    return "doc_" + hashlib.sha256(content).hexdigest()[:16]


def _drop_chapter(store: AppStore, doc_id: str) -> None:
    """Giả lập tài liệu nhập trước migration 0002 (chưa có `chapter_code`)."""
    document = store.get_imported_document(doc_id)
    assert document is not None
    store.update_imported_document(document.model_copy(update={"chapter_code": None}), document.version)


# (môn của bản ghi, chương của bản ghi — None là bản ghi trước migration 0002, môn gửi lên, chương gửi lên,
#  mã HTTP, detail mong đợi — None là 200 với bản ghi hiện có)
CASES: list[tuple[str, str | None, str, str, int, str | None]] = [
    (OTHER_SUBJECT, None, SUBJECT, CHAPTER, 409, IMPORT_ERROR_MESSAGES["SUBJECT_CONFLICT"]),
    (OTHER_SUBJECT, OTHER_CHAPTER, SUBJECT, CHAPTER, 409, IMPORT_ERROR_MESSAGES["SUBJECT_CONFLICT"]),
    (SUBJECT, None, SUBJECT, OTHER_CHAPTER, 409, CHAPTER_MISSING_DETAIL),
    (SUBJECT, None, SUBJECT, CHAPTER, 409, CHAPTER_MISSING_DETAIL),
    (SUBJECT, OTHER_CHAPTER, SUBJECT, CHAPTER, 409, IMPORT_ERROR_MESSAGES["CHAPTER_CONFLICT"]),
    (SUBJECT, CHAPTER, SUBJECT, CHAPTER, 200, None),
]
CASE_IDS = [
    "khac-mon-thieu-chuong",
    "khac-mon-khac-chuong",
    "thieu-chuong-gui-chuong-khac",
    "thieu-chuong-gui-chuong-bat-ky",
    "cung-mon-khac-chuong",
    "cung-mon-cung-chuong",
]


def _check(
    env: ImportEnv,
    doc_id: str,
    response_status: int,
    body: dict[str, object],
    status: int,
    detail: str | None,
    existing_chapter: str | None,
) -> None:
    assert response_status == status, body
    if detail is None:
        assert body["chapter_code"] == existing_chapter
        meta = body["meta"]
        assert isinstance(meta, dict) and meta["doc_id"] == doc_id
    else:
        assert body == {"detail": detail}
    stored = env.store.get_imported_document(doc_id)
    assert stored is not None and stored.chapter_code == existing_chapter, "không đổi chương của bản ghi"
    assert [d.meta.doc_id for d in env.store.list_imported_documents()] == [doc_id], "không tạo bản ghi nào khác"


@pytest.mark.parametrize(
    ("existing_subject", "existing_chapter", "subject", "chapter", "status", "detail"), CASES, ids=CASE_IDS
)
def test_existing_record_found_when_taking_the_lock(
    env: ImportEnv,
    existing_subject: str,
    existing_chapter: str | None,
    subject: str,
    chapter: str,
    status: int,
    detail: str | None,
) -> None:
    content = pdf_bytes(f"lan-dau-{existing_subject}-{existing_chapter}-{subject}-{chapter}")
    doc_id = _doc_id(content)
    first = env.post(content, subject_code=existing_subject, chapter_code=existing_chapter or CHAPTER)
    assert first.status_code == 202, first.text
    if existing_chapter is None:
        _drop_chapter(env.store, doc_id)
    pages_before = env.store.list_pages(doc_id, PageExtraction)
    probed = len(env.reader.probed)

    response = env.post(content, subject_code=subject, chapter_code=chapter)

    _check(env, doc_id, response.status_code, response.json(), status, detail, existing_chapter)
    assert len(env.reader.probed) == probed, "đường nhận lần đầu kiểm bản ghi trước khi mở thử"
    assert env.store.list_pages(doc_id, PageExtraction) == pages_before
    assert env.queue.submitted == [doc_id], "không xếp lại việc đọc"


@pytest.mark.parametrize(
    ("existing_subject", "existing_chapter", "subject", "chapter", "status", "detail"), CASES, ids=CASE_IDS
)
def test_existing_record_created_by_a_racing_process(
    env: ImportEnv,
    monkeypatch: pytest.MonkeyPatch,
    existing_subject: str,
    existing_chapter: str | None,
    subject: str,
    chapter: str,
    status: int,
    detail: str | None,
) -> None:
    """Tiến trình khác tạo tài liệu cùng nội dung (môn, chương của nó) ngay trước lần tạo của request này."""
    real_create = env.store.create_imported_document

    def racing_create(document: ImportedDocument, pending_pages: Sequence[PageExtraction]) -> ImportedDocument:
        other = document.model_copy(
            update={
                "meta": document.meta.model_copy(update={"subject_code": existing_subject}),
                "chapter_code": existing_chapter,
            }
        )
        real_create(other, pending_pages)
        raise DocumentExistsError(document.meta.doc_id)

    monkeypatch.setattr(env.store, "create_imported_document", racing_create)
    content = pdf_bytes(f"dua-{existing_subject}-{existing_chapter}-{subject}-{chapter}")
    doc_id = _doc_id(content)

    response = env.post(content, subject_code=subject, chapter_code=chapter)

    _check(env, doc_id, response.status_code, response.json(), status, detail, existing_chapter)
    assert len(env.reader.probed) == 1, "đường đua: đã mở thử trước khi tạo"
    assert env.queue.submitted == [], "request thua cuộc đua không xếp việc đọc"
    stored = env.store.get_imported_document(doc_id)
    assert stored is not None and env.files.exists(stored.meta.sha256), "file gốc của bản ghi thắng cuộc đua còn"


def test_record_created_only_by_exam_upload_is_imported(env: ImportEnv) -> None:
    """Hồi quy: bản ghi chỉ do §6.1 tạo (chưa có `subject_code`, `chapter_code`) được bổ sung và đọc như tài liệu
    mới (202), không bị coi là thiếu chương hay khác chương."""
    content = pdf_bytes("chi-tu-upload-de")
    doc_id = _doc_id(content)
    env.store.save_document(UploadedDocument.from_content(content, filename="de.pdf", media_type="application/pdf"))
    assert env.store.get_imported_document(doc_id) is None

    response = env.post(content, chapter_code=OTHER_CHAPTER)

    assert response.status_code == 202, response.text
    assert response.json()["chapter_code"] == OTHER_CHAPTER
    stored = env.store.get_imported_document(doc_id)
    assert stored is not None
    assert (stored.meta.subject_code, stored.chapter_code) == (SUBJECT, OTHER_CHAPTER)
    assert env.queue.submitted == [doc_id]
