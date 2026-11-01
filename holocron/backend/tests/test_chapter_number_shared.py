"""Số chương trùng trong tên file được GIỮ kèm cờ thông tin `CHAPTER_NUMBER_SHARED` (SPECIFICATION §5.7 "Chương tự
động"; người dùng duyệt 2026-11-01).

- Slide và giáo trình cùng số chương ⇒ cả hai cùng `chN`, nguồn `file_name`; tài liệu nhận sau mang cờ SHARED.
- Tên file không có số ⇒ chương trống nhỏ nhất (`next_free`) kèm `CHAPTER_NUMBER_NOT_IN_FILENAME`.
- Cờ chương không chặn xác nhận; PATCH mã chương xoá cả cờ SHARED.
- `CHAPTER_NUMBER_TAKEN` không còn được ghi mới nhưng bản ghi cũ (SQLite) có cờ này vẫn đọc được và giữ nguyên.

Chạy trên cả ba loại kho; nội dung PDF là byte tự tạo, chỉ `FakePdfReader` đọc.
"""

import hashlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any, get_args

import pytest

from holocron_core.domain.documents import CHAPTER_FLAG_ORDER, ChapterFlag
from holocron_core.domain.repositories import ImportedDocument
from holocron_core.domain.schemas import DocumentMeta, SourceKind
from holocron_core.infrastructure.storage import SQLiteStore
from holocron_core.infrastructure.storage.connection import connect
from holocron_core.routers.api import app
from holocron_core.services.chapter_naming import ChapterChoice, choose_chapter_code
from holocron_core.services.document_import import SynchronousImportQueue, pending_page
from tests.document_import_support import FakePdfReader, ImportEnv, install, pdf_bytes
from tests.vault_support import STORE_KINDS, open_store

EDITOR = "local_user"
STAMP = "2026-10-20T08:00:00.000000+00:00"
LEGACY_ID = "doc_dddddddddddddddd"


@pytest.fixture(params=STORE_KINDS)
def env(request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ImportEnv]:
    for store in open_store(str(request.param), tmp_path, monkeypatch):
        yield install(store, tmp_path / "files", SynchronousImportQueue(), FakePdfReader(page_count=2))
        app.dependency_overrides.clear()


def _import(env: ImportEnv, tag: str, filename: str) -> tuple[str, dict[str, Any]]:
    response = env.post(pdf_bytes(tag), chapter_code=None, filename=filename)
    assert response.status_code == 202, response.text
    body: dict[str, Any] = response.json()
    return "doc_" + hashlib.sha256(pdf_bytes(tag)).hexdigest()[:16], body


def _get(env: ImportEnv, doc_id: str) -> dict[str, Any]:
    response = env.client.get(f"/v1/documents/{doc_id}")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _chapter(body: dict[str, Any]) -> tuple[Any, ...]:
    return body["chapter_code"], body["chapter_source"]["code"], body["chapter_flags"]


# --------------------------------------------------------------------------- #
# Chọn chương
# --------------------------------------------------------------------------- #


def test_written_flags_no_longer_include_taken() -> None:
    assert get_args(ChapterFlag) == ("CHAPTER_NUMBER_NOT_IN_FILENAME", "CHAPTER_NUMBER_SHARED")
    assert CHAPTER_FLAG_ORDER == ("CHAPTER_NUMBER_NOT_IN_FILENAME", "CHAPTER_NUMBER_SHARED", "CHAPTER_NUMBER_TAKEN")


@pytest.mark.parametrize("taken", [{"ch2"}, {"ch1", "ch2", "ch3"}, {"ch2", "ch2.1"}])
def test_a_shared_number_keeps_its_chapter_from_the_file_name(taken: set[str]) -> None:
    assert choose_chapter_code("GiaoTrinh_Chuong_2.pdf", taken) == ChapterChoice(
        "ch2", "file_name", ("CHAPTER_NUMBER_SHARED",)
    )


def test_a_sub_chapter_of_the_same_number_is_not_shared() -> None:
    assert choose_chapter_code("Chuong-3.pdf", {"ch3.2"}) == ChapterChoice("ch3", "file_name", ())


def test_shared_number_works_even_when_every_chapter_is_taken() -> None:
    every = {f"ch{n}" for n in range(1, 100)}
    assert choose_chapter_code("ch07.pdf", every) == ChapterChoice("ch7", "file_name", ("CHAPTER_NUMBER_SHARED",))
    assert choose_chapter_code("on-tap.pdf", every) == ChapterChoice(
        "ch99", "next_free", ("CHAPTER_NUMBER_NOT_IN_FILENAME",)
    )


# --------------------------------------------------------------------------- #
# Qua API
# --------------------------------------------------------------------------- #


def test_slide_and_textbook_of_the_same_chapter_share_it(env: ImportEnv) -> None:
    slide_id, slide = _import(env, "slide", "Slide_Chuong_2.pdf")
    book_id, book = _import(env, "giao-trinh", "GiaoTrinh-ch02.pdf")
    assert _chapter(slide) == ("ch2", "file_name", [])
    assert _chapter(book) == ("ch2", "file_name", ["CHAPTER_NUMBER_SHARED"])
    assert _chapter(_get(env, slide_id)) == ("ch2", "file_name", [])
    assert _chapter(_get(env, book_id)) == ("ch2", "file_name", ["CHAPTER_NUMBER_SHARED"])
    _, untitled = _import(env, "khong-so", "tong-hop.pdf")
    assert _chapter(untitled) == ("ch1", "next_free", ["CHAPTER_NUMBER_NOT_IN_FILENAME"])


def test_shared_flag_does_not_block_confirmation(env: ImportEnv) -> None:
    _import(env, "slide", "Slide_Chuong_2.pdf")
    doc_id, _ = _import(env, "giao-trinh", "GiaoTrinh-ch02.pdf")
    body = _get(env, doc_id)
    assert body["status"] == "review" and body["chapter_flags"] == ["CHAPTER_NUMBER_SHARED"]
    response = env.client.post(f"/v1/documents/{doc_id}/confirm", json={"editor_id": EDITOR})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "confirmed"


def test_patch_code_clears_the_shared_flag(env: ImportEnv) -> None:
    _import(env, "slide", "Slide_Chuong_2.pdf")
    doc_id, body = _import(env, "giao-trinh", "GiaoTrinh-ch02.pdf")
    assert body["chapter_flags"] == ["CHAPTER_NUMBER_SHARED"]
    response = env.client.patch(f"/v1/documents/{doc_id}/chapter", json={"editor_id": EDITOR, "chapter_code": "ch2"})
    assert response.status_code == 200, response.text
    assert _chapter(response.json()) == ("ch2", "user", [])
    assert _chapter(_get(env, doc_id)) == ("ch2", "user", [])


# --------------------------------------------------------------------------- #
# Bản ghi cũ có CHAPTER_NUMBER_TAKEN
# --------------------------------------------------------------------------- #


def _legacy_document() -> ImportedDocument:
    return ImportedDocument(
        meta=DocumentMeta(
            doc_id=LEGACY_ID,
            subject_code="ET4010",
            title="Chương 1",
            source_kind=SourceKind.TEXTBOOK,
            sha256="d" * 64,
            page_count=1,
            has_text_layer=True,
            uploaded_at=STAMP,
        ),
        filename="bai-2.pdf",
        size_bytes=10,
        chapter_code="ch1",
        status="review",
        chapter_code_source="next_free",
        chapter_title="bai 2",
        chapter_title_source="file_name",
    )


def test_old_sqlite_row_with_taken_is_read_and_kept(tmp_path: Path) -> None:
    path = tmp_path / "store.sqlite3"
    store = SQLiteStore.open(path)
    try:
        store.create_imported_document(_legacy_document(), [pending_page(LEGACY_ID, 1)])
    finally:
        store.close()
    conn = connect(path)
    try:
        conn.execute(
            "UPDATE documents SET chapter_flags_json = ? WHERE document_id = ?",
            ('["CHAPTER_NUMBER_TAKEN", "CHAPTER_NUMBER_TAKEN"]', LEGACY_ID),
        )
    finally:
        conn.close()
    reopened = SQLiteStore.open(path)
    try:
        read = reopened.get_imported_document(LEGACY_ID)
        assert read is not None
        assert (read.chapter_code, read.chapter_code_source, read.chapter_flags) == (
            "ch1",
            "next_free",
            ["CHAPTER_NUMBER_TAKEN"],
        ), "cờ cũ giữ nguyên, không đổi sang SHARED, không trùng"
        listed = reopened.list_imported_documents("ET4010")
        assert [document.chapter_flags for document in listed] == [["CHAPTER_NUMBER_TAKEN"]]
    finally:
        reopened.close()


def test_api_returns_an_old_document_with_taken_unchanged(env: ImportEnv) -> None:
    """GET /v1/documents/{id} phải trả nguyên cờ cũ `CHAPTER_NUMBER_TAKEN` (response model nhận cờ kho cũ)."""
    legacy = _legacy_document().model_copy(update={"chapter_flags": ["CHAPTER_NUMBER_TAKEN"]})
    env.store.create_imported_document(legacy, [pending_page(LEGACY_ID, 1)])
    body = _get(env, LEGACY_ID)
    assert _chapter(body) == ("ch1", "next_free", ["CHAPTER_NUMBER_TAKEN"]), "cờ cũ giữ nguyên qua API"
