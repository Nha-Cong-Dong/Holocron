"""`POST /v1/documents` cùng nội dung với bản ghi nhập trước khi có `chapter_code` (SPECIFICATION §5.7 "Tài liệu
nhập trước khi có `chapter_code`", §6.5 bước 3): 409 với `detail` cố định `CHAPTER_MISSING_DETAIL`, không ghi gì
xuống kho, không lưu file gốc, không mở thử và không xếp việc đọc.

Chạy trên cả ba loại kho. Nội dung PDF là byte tự tạo, chỉ `FakePdfReader` đọc.
"""

import hashlib
from collections.abc import Iterator
from pathlib import Path

import pytest

from holocron_core.domain.documents import PageExtraction
from holocron_core.domain.repositories import AppStore
from holocron_core.routers.api import app
from holocron_core.services.document_import import CHAPTER_MISSING_DETAIL, SynchronousImportQueue
from tests.document_import_support import FakePdfReader, ImportEnv, install, pdf_bytes
from tests.vault_support import STORE_KINDS, open_store

SPEC_CHAPTER_MISSING_DETAIL = "Tài liệu được nhập trước khi có chapter_code; hãy xoá tài liệu rồi nhập lại"


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
    stored = store.get_imported_document(doc_id)
    assert stored is not None and stored.chapter_code is None


def _files(env: ImportEnv) -> list[str]:
    return sorted(str(path.relative_to(env.files.root)) for path in env.files.root.rglob("*") if path.is_file())


def test_detail_is_the_spec_string() -> None:
    """DECISION 4 (2026-10-28): `detail` là chuỗi ghi ở SPECIFICATION §5.7."""
    assert CHAPTER_MISSING_DETAIL == SPEC_CHAPTER_MISSING_DETAIL


@pytest.mark.parametrize("chapter_code", ["ch3", "ch4.2"], ids=["cung-chuong-cu", "chuong-khac"])
def test_reimport_of_a_record_without_chapter_is_409_and_writes_nothing(env: ImportEnv, chapter_code: str) -> None:
    content = pdf_bytes("thieu-chuong-nhap-lai")
    assert env.post(content, chapter_code="ch3").status_code == 202
    doc_id = _doc_id(content)
    _drop_chapter(env.store, doc_id)
    document_before = env.store.get_imported_document(doc_id)
    pages_before = env.store.list_pages(doc_id, PageExtraction)
    files_before = _files(env)
    submitted_before = list(env.queue.submitted)
    probed_before = list(env.reader.probed)

    response = env.post(content, chapter_code=chapter_code)

    assert response.status_code == 409
    assert response.json() == {"detail": CHAPTER_MISSING_DETAIL}
    assert env.store.get_imported_document(doc_id) == document_before, "không ghi gì vào bản ghi tài liệu"
    assert env.store.list_pages(doc_id, PageExtraction) == pages_before, "không ghi gì vào các trang"
    assert _files(env) == files_before, "không lưu thêm file gốc hay file tạm"
    assert env.queue.submitted == submitted_before, "không xếp việc đọc"
    assert env.reader.probed == probed_before, "không mở thử file"
    assert [d.meta.doc_id for d in env.store.list_imported_documents()] == [doc_id]
