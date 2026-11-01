"""Field chương của `DocumentImport`, field form `auto_confirm` và `PATCH /v1/documents/{id}/chapter`
(SPECIFICATION §5.7 "Chương tự động", §6.5; GĐ1 1A, 1B).

- 202: `chapter_title` là tên file đã làm sạch (nguồn `file_name`), `chapter_source.code` theo nguồn của mã, cờ
  chương, `auto_confirm` như form (mặc định bật). Đọc lại qua `GET` cho cùng giá trị (kho in-memory và SQLite).
- PATCH: đặt tên lúc nào cũng được; đặt mã chỉ khi tài liệu chưa `confirmed` và Vault chưa có chunk của tài liệu;
  request có cả hai field mà mã đã khoá thì không đổi gì; 404, 409 (tài liệu nhập trước khi có `chapter_code`), 422.

Chạy trên cả ba loại kho; nội dung PDF là byte tự tạo, chỉ `FakePdfReader` đọc.
"""

import hashlib
import unicodedata
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from httpx import Response
from pydantic import ValidationError

from holocron_core.domain.documents import DocumentImport
from holocron_core.domain.repositories import ImportedDocument, VersionConflictError
from holocron_core.infrastructure.memory_store import IngestionStore
from holocron_core.routers.api import app
from holocron_core.services.document_chapter import CHAPTER_CODE_LOCKED_DETAIL
from holocron_core.services.document_import import CHAPTER_MISSING_DETAIL, SETTLE_ATTEMPTS, SynchronousImportQueue
from holocron_core.services.vault import ingest_confirmed_text
from tests.document_import_support import FakePdfReader, ImportEnv, install, pdf_bytes
from tests.vault_support import ARM_PAGES, STORE_KINDS, confirmed, open_store

EDITOR = "local_user"


@pytest.fixture(params=STORE_KINDS)
def env(request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ImportEnv]:
    for store in open_store(str(request.param), tmp_path, monkeypatch):
        yield install(store, tmp_path / "files", SynchronousImportQueue(), FakePdfReader(page_count=2))
        app.dependency_overrides.clear()


def _doc_id(content: bytes) -> str:
    return "doc_" + hashlib.sha256(content).hexdigest()[:16]


def _import(env: ImportEnv, tag: str, **kwargs: Any) -> tuple[str, dict[str, Any]]:
    kwargs.setdefault("chapter_code", None)
    kwargs.setdefault("filename", "Chuong-2_Vi-xu-ly-ARM.pdf")
    kwargs.setdefault("auto_confirm", None)  # các test này kiểm mặc định của field form (bật), nên không gửi field
    response = env.post(pdf_bytes(tag), **kwargs)
    assert response.status_code == 202, response.text
    body: dict[str, Any] = response.json()
    return _doc_id(pdf_bytes(tag)), body


def _chapter_fields(body: dict[str, Any]) -> tuple[Any, ...]:
    return (
        body["chapter_code"],
        body["chapter_title"],
        body["chapter_source"],
        body["chapter_flags"],
        body["auto_confirm"],
    )


def _patch(env: ImportEnv, doc_id: str, **body: Any) -> Response:
    return env.client.patch(f"/v1/documents/{doc_id}/chapter", json={"editor_id": EDITOR, **body})


def _get(env: ImportEnv, doc_id: str) -> dict[str, Any]:
    response = env.client.get(f"/v1/documents/{doc_id}")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


# --------------------------------------------------------------------------- #
# Field của DocumentImport lúc nhận file
# --------------------------------------------------------------------------- #


def test_automatic_chapter_and_cleaned_file_name_title(env: ImportEnv) -> None:
    doc_id, body = _import(env, "tu-dong")
    expected = ("ch2", "Chuong 2 Vi xu ly ARM", {"code": "file_name", "title": "file_name"}, [], True)
    assert _chapter_fields(body) == expected
    assert _chapter_fields(_get(env, doc_id)) == expected


def test_next_free_chapter_carries_its_flag(env: ImportEnv) -> None:
    _import(env, "dau", filename="Chuong-2.pdf")
    doc_id, body = _import(env, "trung", filename="bai_2-on_tap.pdf")
    expected = ("ch2", "bai 2 on tap", {"code": "file_name", "title": "file_name"}, ["CHAPTER_NUMBER_SHARED"], True)
    assert _chapter_fields(body) == expected
    assert _chapter_fields(_get(env, doc_id)) == expected
    _, without = _import(env, "khong-so", filename="tong-hop.pdf")
    assert without["chapter_code"] == "ch1" and without["chapter_flags"] == ["CHAPTER_NUMBER_NOT_IN_FILENAME"]


def test_user_chapter_has_source_user_and_no_flag(env: ImportEnv) -> None:
    doc_id, body = _import(env, "nguoi-dung", chapter_code="ch3.2", filename="Chuong-2.pdf")
    assert _chapter_fields(body) == ("ch3.2", "Chuong 2", {"code": "user", "title": "file_name"}, [], True)
    assert _chapter_fields(_get(env, doc_id)) == _chapter_fields(body)


@pytest.mark.parametrize(("form", "stored"), [("false", False), ("true", True), ("0", False), ("1", True)])
def test_auto_confirm_form_field_is_stored(env: ImportEnv, form: str, stored: bool) -> None:
    doc_id, body = _import(env, f"tu-xac-nhan-{form}", auto_confirm=form)
    assert body["auto_confirm"] is stored and _get(env, doc_id)["auto_confirm"] is stored
    document = env.store.get_imported_document(doc_id)
    assert document is not None and document.auto_confirm is stored


def test_invalid_auto_confirm_is_422_and_stores_nothing(env: ImportEnv) -> None:
    response = env.post(pdf_bytes("sai-auto"), auto_confirm="co")
    assert response.status_code == 422
    assert env.reader.probed == [] and env.store.list_imported_documents() == []


# --------------------------------------------------------------------------- #
# PATCH …/chapter
# --------------------------------------------------------------------------- #


def test_patch_title_normalizes_and_keeps_the_code(env: ImportEnv) -> None:
    doc_id, _ = _import(env, "doi-ten", filename="ch2.pdf")
    response = _patch(env, doc_id, chapter_title=unicodedata.normalize("NFD", "  Bộ   nhớ  "))
    assert response.status_code == 200, response.text
    expected = ("ch2", unicodedata.normalize("NFC", "Bộ nhớ"), {"code": "file_name", "title": "user"}, [], True)
    assert _chapter_fields(response.json()) == expected
    assert _chapter_fields(_get(env, doc_id)) == expected


def test_patch_code_sets_source_user_and_clears_flags(env: ImportEnv) -> None:
    _import(env, "a", filename="ch2.pdf")
    doc_id, body = _import(env, "b", filename="khong-so.pdf")
    assert body["chapter_flags"] == ["CHAPTER_NUMBER_NOT_IN_FILENAME"]
    response = _patch(env, doc_id, chapter_code="ch2")  # trùng chương của tài liệu khác: được phép
    assert response.status_code == 200, response.text
    expected = ("ch2", "khong so", {"code": "user", "title": "file_name"}, [], True)
    assert _chapter_fields(response.json()) == expected
    assert _chapter_fields(_get(env, doc_id)) == expected


def test_patch_both_fields_before_confirmation(env: ImportEnv) -> None:
    doc_id, _ = _import(env, "ca-hai")
    response = _patch(env, doc_id, chapter_code="ch7.1", chapter_title="Ngắt")
    assert response.status_code == 200, response.text
    assert _chapter_fields(response.json()) == ("ch7.1", "Ngắt", {"code": "user", "title": "user"}, [], True)


def test_reimport_after_patch_returns_the_patched_record(env: ImportEnv) -> None:
    doc_id, _ = _import(env, "sau-patch")
    assert _patch(env, doc_id, chapter_code="ch5").status_code == 200
    again = env.post(pdf_bytes("sau-patch"), chapter_code=None, filename="Chuong-2_Vi-xu-ly-ARM.pdf")
    assert again.status_code == 200 and again.json()["chapter_code"] == "ch5"


def _confirm(env: ImportEnv, doc_id: str) -> None:
    response = env.client.post(f"/v1/documents/{doc_id}/confirm", json={"editor_id": EDITOR})
    assert response.status_code == 200 and response.json()["status"] == "confirmed", response.text


def test_code_is_locked_after_confirmation_but_the_title_is_not(env: ImportEnv) -> None:
    doc_id, _ = _import(env, "da-xac-nhan")
    _confirm(env, doc_id)
    before = _get(env, doc_id)
    for body in ({"chapter_code": "ch4"}, {"chapter_code": "ch4", "chapter_title": "Tên mới"}):
        locked = _patch(env, doc_id, **body)
        assert locked.status_code == 409 and locked.json() == {"detail": CHAPTER_CODE_LOCKED_DETAIL}
        assert _get(env, doc_id) == before, "409 không đổi gì, kể cả tên"
    renamed = _patch(env, doc_id, chapter_title="Tên mới")
    assert renamed.status_code == 200
    assert _chapter_fields(renamed.json()) == ("ch2", "Tên mới", {"code": "file_name", "title": "user"}, [], True)
    assert [chunk.chapter_code for chunk in env.store.list_chunks("ET4010")] != []
    assert {chunk.chapter_code for chunk in env.store.list_chunks("ET4010")} == {"ch2"}


def test_code_is_locked_when_the_vault_has_chunks_before_confirmation(env: ImportEnv) -> None:
    doc_id, _ = _import(env, "settle-do-dang")
    assert _get(env, doc_id)["status"] == "review", "đọc xong, chưa xác nhận"
    ingest_confirmed_text(env.store, confirmed(doc_id, ARM_PAGES, chapter_code="ch2"))
    locked = _patch(env, doc_id, chapter_code="ch4")
    assert locked.status_code == 409 and locked.json() == {"detail": CHAPTER_CODE_LOCKED_DETAIL}
    assert _get(env, doc_id)["chapter_code"] == "ch2"
    assert _patch(env, doc_id, chapter_title="Thanh ghi").status_code == 200


def test_unknown_document_is_404(env: ImportEnv) -> None:
    assert _patch(env, "doc_0000000000000000", chapter_title="x").status_code == 404


def test_document_imported_before_chapter_code_is_409(env: ImportEnv) -> None:
    doc_id, _ = _import(env, "cu")
    document = env.store.get_imported_document(doc_id)
    assert document is not None
    env.store.update_imported_document(document.model_copy(update={"chapter_code": None}), document.version)
    for body in ({"chapter_title": "x"}, {"chapter_code": "ch1"}):
        response = _patch(env, doc_id, **body)
        assert response.status_code == 409 and response.json() == {"detail": CHAPTER_MISSING_DETAIL}


@pytest.mark.parametrize(
    "body",
    [
        {"editor_id": EDITOR},
        {"editor_id": EDITOR, "chapter_code": None, "chapter_title": None},
        {"editor_id": EDITOR, "chapter_code": "chuong3"},
        {"editor_id": EDITOR, "chapter_code": "ch100"},
        {"editor_id": EDITOR, "chapter_title": ""},
        {"editor_id": EDITOR, "chapter_title": "   "},
        {"editor_id": EDITOR, "chapter_title": "x" * 301},
        {"editor_id": "", "chapter_title": "x"},
        {"chapter_title": "x"},
        {"editor_id": EDITOR, "chapter_title": "x", "expected_version": 1},
    ],
)
def test_invalid_body_is_422_and_changes_nothing(env: ImportEnv, body: dict[str, Any]) -> None:
    doc_id, _ = _import(env, "body-sai")
    before = _get(env, doc_id)
    response = env.client.patch(f"/v1/documents/{doc_id}/chapter", json=body)
    assert response.status_code == 422
    assert _get(env, doc_id) == before


def test_padded_title_over_300_raw_characters_is_422(env: ImportEnv) -> None:
    """§6.5: `chapter_title` dài 1–300 ký tự trên chuỗi thô; chuỗi thô quá dài bị chặn dù chuẩn hoá còn ngắn."""
    doc_id, _ = _import(env, "dai-tho")
    before = _get(env, doc_id)
    for raw in (" " * 5000 + "Tên", "Tên" + " " * 5000, "a " * 200):
        assert _patch(env, doc_id, chapter_title=raw).status_code == 422
    assert _get(env, doc_id) == before
    assert _patch(env, doc_id, chapter_title=" " * 297 + "Tên").status_code == 200, "đúng 300 ký tự thô thì nhận"
    assert _get(env, doc_id)["chapter_title"] == "Tên"


def test_openapi_documents_the_chapter_title_length() -> None:
    schemas: dict[str, Any] = app.openapi()["components"]["schemas"]
    options: list[dict[str, Any]] = schemas["DocumentChapterPatch"]["properties"]["chapter_title"]["anyOf"]
    text = next(option for option in options if option.get("type") == "string")
    assert (text["minLength"], text["maxLength"]) == (1, 300)


# --------------------------------------------------------------------------- #
# Nhánh còn thiếu (report mutation feat-chapter-auto 2026-10-31)
# --------------------------------------------------------------------------- #


class ConflictingStore(IngestionStore):
    """Kho in-memory mà `update_imported_document` báo version lệch `conflicts` lần đầu (như một luồng ghi tài liệu
    ngoài khoá); `conflicts < 0` ⇒ lệch mãi."""

    def __init__(self, conflicts: int) -> None:
        super().__init__()
        self.conflicts = conflicts
        self.updates = 0
        self.armed = False

    def update_imported_document(self, document: ImportedDocument, expected_version: int) -> ImportedDocument:
        if self.armed:
            self.updates += 1
            if self.conflicts < 0 or self.updates <= self.conflicts:
                raise VersionConflictError("tài liệu", document.meta.doc_id, expected_version, expected_version + 1)
        return super().update_imported_document(document, expected_version)


def _conflicting_env(tmp_path: Path, conflicts: int) -> tuple[ImportEnv, ConflictingStore, str]:
    store = ConflictingStore(conflicts)
    env = install(store, tmp_path / "files", SynchronousImportQueue(), FakePdfReader(page_count=2))
    doc_id, _ = _import(env, "lech-version")
    store.armed = True
    return env, store, doc_id


def test_patch_answers_409_not_500_when_the_version_always_conflicts(tmp_path: Path) -> None:
    env, store, doc_id = _conflicting_env(tmp_path, conflicts=-1)
    try:
        response = _patch(env, doc_id, chapter_title="Tên mới")
        assert response.status_code == 409, response.text
        assert store.updates == SETTLE_ATTEMPTS, "thử đủ số lần rồi mới báo lệch"
        assert _get(env, doc_id)["chapter_title"] == "Chuong 2 Vi xu ly ARM"
    finally:
        app.dependency_overrides.clear()


def test_patch_retries_after_one_conflict_and_writes_once(tmp_path: Path) -> None:
    env, store, doc_id = _conflicting_env(tmp_path, conflicts=1)
    try:
        before = store.get_imported_document(doc_id)
        assert before is not None
        response = _patch(env, doc_id, chapter_title="Tên mới")
        assert response.status_code == 200, response.text
        assert store.updates == 2
        after = store.get_imported_document(doc_id)
        assert after is not None and after.chapter_title == "Tên mới" and after.version == before.version + 1
    finally:
        app.dependency_overrides.clear()


def test_successful_patch_writes_exactly_once(env: ImportEnv) -> None:
    doc_id, _ = _import(env, "mot-lan")
    before = env.store.get_imported_document(doc_id)
    assert before is not None
    assert _patch(env, doc_id, chapter_code="ch8", chapter_title="Tám").status_code == 200
    after = env.store.get_imported_document(doc_id)
    assert after is not None and after.version == before.version + 1


def test_confirmed_document_without_chunks_locks_the_code(env: ImportEnv) -> None:
    doc_id, _ = _import(env, "confirmed-khong-chunk")
    document = env.store.get_imported_document(doc_id)
    assert document is not None
    env.store.update_imported_document(document.model_copy(update={"status": "confirmed"}), document.version)
    assert env.store.list_chunks("ET4010") == []
    locked = _patch(env, doc_id, chapter_code="ch4")
    assert locked.status_code == 409 and locked.json() == {"detail": CHAPTER_CODE_LOCKED_DETAIL}


def test_old_record_without_a_code_source_reads_as_user(env: ImportEnv) -> None:
    doc_id, _ = _import(env, "nguon-cu")
    document = env.store.get_imported_document(doc_id)
    assert document is not None
    old = document.model_copy(update={"chapter_code_source": None, "chapter_title": None, "chapter_title_source": None})
    env.store.update_imported_document(old, document.version)
    body = _get(env, doc_id)
    assert body["chapter_source"] == {"code": "user", "title": None} and body["chapter_title"] is None


def test_document_import_title_and_title_source_go_together(env: ImportEnv) -> None:
    doc_id, _ = _import(env, "mo-hinh")
    body = _get(env, doc_id)
    assert DocumentImport.model_validate(body).chapter_title == "Chuong 2 Vi xu ly ARM"
    for title, source_title in (("Tên", None), (None, "file_name")):
        bad = {**body, "chapter_title": title, "chapter_source": {"code": "user", "title": source_title}}
        with pytest.raises(ValidationError, match="chapter_source.title"):
            DocumentImport.model_validate(bad)
    for length, ok in ((300, True), (301, False)):
        sized = {**body, "chapter_title": "x" * length}
        if ok:
            assert DocumentImport.model_validate(sized).chapter_title == "x" * 300
        else:
            with pytest.raises(ValidationError):
                DocumentImport.model_validate(sized)
