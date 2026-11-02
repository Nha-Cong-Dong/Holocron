"""Tự xác nhận cấp trang qua API (SPECIFICATION §5.7 "Tự xác nhận cấp trang", §6.5; GĐ1 1B).

Chạy trên cả ba loại kho (`tests/vault_support.py`), `PdfReader` giả với trang dựng sẵn, hàng đợi đọc đồng bộ. Các test
`test_auto_confirm_never_confirms_flagged_page`, `test_auto_confirm_never_closes_document`,
`test_unconfirm_restores_review_and_blocks_reauto` chưa nằm trong bảng CLAUDE.md §9. Văn bản là mẫu tự viết.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from holocron_core.domain.documents import DocumentImport, PageExtraction
from holocron_core.routers.api import app
from holocron_core.services import document_import
from holocron_core.services.document_import import (
    CHAPTER_MISSING_DETAIL,
    SETTLE_PENDING_DETAIL,
    USER_CONFIRMED_UNDO_DETAIL,
    SynchronousImportQueue,
    mark_document_failed,
)
from tests.document_import_support import FakePdfReader, ImportEnv, install, ok_page, pdf_bytes
from tests.vault_support import STORE_KINDS, open_store

EDITOR = "local_user"
NAV = ["Muc luc chuong", "Kien truc tap lenh", "Che do dia chi", "Ngat va ngoai le", "Bo nho dem va bus"]
NAV_PAGES = (2, 6, 11)


def _line(index: int, text: str, engine: str = "text_layer") -> dict[str, Any]:
    return {
        "line_index": index,
        "text": text,
        "bbox": [0.1, 0.2 + 0.05 * index, 0.9, 0.24 + 0.05 * index],
        "conf": 0.95,
        "engine": engine,
    }


def _text_page(document_id: str, number: int, lines: list[str], **extra: Any) -> PageExtraction:
    data: dict[str, Any] = {
        "document_id": document_id,
        "page_number": number,
        "status": "ok",
        "tier_used": "text_layer",
        "text_score": 0.95,
        "lines": [_line(index, text) for index, text in enumerate(lines)],
        "cross_check": {"status": "not_applicable", "secondary_engine": None},
    }
    data.update(extra)
    return PageExtraction.model_validate(data)


def _content(document_id: str, number: int) -> list[str]:
    seed = hashlib.sha256(f"{document_id}:{number}".encode()).hexdigest()
    return [" ".join(f"t{seed[k : k + 6]}{i}" for k in range(0, 18, 6)) for i in range(4)]


def clean_page(number: int) -> Callable[[str], PageExtraction]:
    return lambda document_id: _text_page(document_id, number, _content(document_id, number))


def review_page(number: int) -> Callable[[str], PageExtraction]:
    return lambda document_id: _text_page(
        document_id,
        number,
        _content(document_id, number),
        status="needs_review",
        review_reasons=["LOW_TEXT_CONTRAST"],
    )


def scan_layer_page(number: int) -> Callable[[str], PageExtraction]:
    return lambda document_id: _text_page(document_id, number, _content(document_id, number), scan_text_layer=True)


def vlm_page(number: int) -> Callable[[str], PageExtraction]:
    return lambda document_id: _text_page(document_id, number, _content(document_id, number), tier_used="vlm")


def ocr_page(number: int, cross_status: str) -> Callable[[str], PageExtraction]:
    secondary = "rapidocr" if cross_status == "ok" else None
    return lambda document_id: PageExtraction.model_validate(
        {
            "document_id": document_id,
            "page_number": number,
            "status": "ok",
            "tier_used": "ocr",
            "text_score": 0.1,
            "ocr_score": 0.95,
            "lines": [_line(index, text, "tesseract") for index, text in enumerate(_content(document_id, number))],
            "cross_check": {"status": cross_status, "secondary_engine": secondary},
        }
    )


def blank_page(number: int) -> Callable[[str], PageExtraction]:
    return lambda document_id: _text_page(document_id, number, [])


@pytest.fixture(params=STORE_KINDS)
def env(request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ImportEnv]:
    for store in open_store(str(request.param), tmp_path, monkeypatch):
        yield install(store, tmp_path / "files", SynchronousImportQueue(), FakePdfReader())
        app.dependency_overrides.clear()


def _doc_id(content: bytes) -> str:
    return "doc_" + hashlib.sha256(content).hexdigest()[:16]


def _import(env: ImportEnv, pages: dict[int, Callable[[str], PageExtraction]], tag: str = "a", **form: str) -> str:
    env.reader.custom_pages = pages
    env.reader.page_count = max(pages)
    content = pdf_bytes(tag)
    response = env.post(content, **{"auto_confirm": "true", **form})
    assert response.status_code == 202, response.text
    return _doc_id(content)


def _page(env: ImportEnv, doc_id: str, number: int) -> PageExtraction:
    response = env.client.get(f"/v1/documents/{doc_id}/pages/{number}")
    assert response.status_code == 200
    return PageExtraction.model_validate(response.json())


def _document(env: ImportEnv, doc_id: str) -> DocumentImport:
    response = env.client.get(f"/v1/documents/{doc_id}")
    assert response.status_code == 200
    return DocumentImport.model_validate(response.json())


def _body(version: int) -> dict[str, Any]:
    return {"editor_id": EDITOR, "expected_version": version}


def _unconfirm(env: ImportEnv, doc_id: str, number: int, version: int) -> Any:
    return env.client.post(f"/v1/documents/{doc_id}/pages/{number}/unconfirm", json=_body(version))


def _confirm_page(env: ImportEnv, doc_id: str, number: int) -> Any:
    return env.client.post(
        f"/v1/documents/{doc_id}/pages/{number}/confirm", json=_body(_page(env, doc_id, number).version)
    )


def _confirm_document(env: ImportEnv, doc_id: str) -> Any:
    return env.client.post(f"/v1/documents/{doc_id}/confirm", json={"editor_id": EDITOR})


def _has_chunks(env: ImportEnv, doc_id: str) -> bool:
    return env.store.has_document_chunks(doc_id)


# --------------------------------------------------------------------------- #
# Tự xác nhận ở cuối lần đọc
# --------------------------------------------------------------------------- #


def test_auto_confirm_marks_clean_pages_with_audit(env: ImportEnv) -> None:
    doc = _import(env, {1: clean_page(1), 2: clean_page(2)})
    for number in (1, 2):
        page = _page(env, doc, number)
        assert (page.confirmed, page.confirmed_by, page.confirm_rule) == (True, "auto", "page_clean_v1")
        assert page.confirmed_at is not None and page.auto_confirm_undone is False
        assert page.version == 3, "pending v1, đọc xong v2, tự xác nhận v3 (version + 1 như xác nhận thường)"
        assert env.store.list_page_audits(doc, number) == [], "tự xác nhận không đổi dòng nào: không ghi PageEditAudit"
    document = _document(env, doc)
    assert (document.pages_auto_confirmed, document.pages_kept_unconfirmed, document.pages_needs_review) == (2, 0, 0)
    assert [page.confirmed_by for page in document.pages] == ["auto", "auto"]
    assert document.auto_confirm is True


def test_auto_confirm_never_closes_document(env: ImportEnv) -> None:
    doc = _import(env, {1: clean_page(1), 2: clean_page(2)})
    assert _document(env, doc).status == "review"
    assert not _has_chunks(env, doc), "tự xác nhận không cắt chunk"
    # bước chốt sau xác nhận từng trang: trang 3 cần xem, người dùng xác nhận nó, mọi trang đã xác nhận mà vẫn chưa đóng
    doc3 = _import(env, {1: clean_page(1), 2: review_page(2)}, tag="b")
    assert _document(env, doc3).status == "review"
    assert _confirm_page(env, doc3, 2).status_code == 200
    assert all(page.confirmed for page in _document(env, doc3).pages)
    assert _document(env, doc3).status == "review"
    assert not _has_chunks(env, doc3)
    # đường failed: trang tự xác nhận nhưng tài liệu bị đánh dấu failed
    mark_document_failed(env.store, doc, env.runtime.locks, env.store)
    assert _document(env, doc).status == "failed"
    assert not _has_chunks(env, doc)
    assert _confirm_document(env, doc).status_code == 200, "người dùng bấm Chấp nhận vẫn đóng được tài liệu failed"
    assert _document(env, doc).status == "confirmed" and _has_chunks(env, doc)


def test_user_confirm_closes_document_and_keeps_auto_audit(env: ImportEnv) -> None:
    doc = _import(env, {1: clean_page(1), 2: review_page(2)})
    assert _confirm_page(env, doc, 2).status_code == 200
    response = _confirm_document(env, doc)
    assert response.status_code == 200
    document = DocumentImport.model_validate(response.json())
    assert document.status == "confirmed" and _has_chunks(env, doc)
    assert [page.confirmed_by for page in document.pages] == ["auto", "user"]
    assert _page(env, doc, 1).confirm_rule == "page_clean_v1", "audit cho biết máy xác nhận trang"
    assert _page(env, doc, 2).confirm_rule is None
    again = _confirm_document(env, doc)
    assert again.status_code == 200 and DocumentImport.model_validate(again.json()).status == "confirmed"


def test_user_confirm_leaves_review_pages_to_be_confirmed_one_by_one(env: ImportEnv) -> None:
    doc = _import(env, {1: clean_page(1), 2: review_page(2)})
    response = _confirm_document(env, doc)
    assert response.status_code == 200
    document = DocumentImport.model_validate(response.json())
    assert document.status == "review", "còn trang needs_review chưa xác nhận"
    assert (document.pages_auto_confirmed, document.pages_needs_review) == (1, 1)
    assert not _has_chunks(env, doc)


def test_auto_confirm_never_confirms_flagged_page(env: ImportEnv) -> None:
    pages = {
        1: clean_page(1),
        2: review_page(2),
        3: scan_layer_page(3),
        4: vlm_page(4),
        5: ocr_page(5, "unavailable"),
        6: ocr_page(6, "not_applicable"),
        7: blank_page(7),
        8: ocr_page(8, "ok"),
    }
    doc = _import(env, pages)
    confirmed = {page.page_number: page.confirmed_by for page in _document(env, doc).pages}
    assert confirmed == {1: "auto", 2: None, 3: None, 4: None, 5: None, 6: None, 7: None, 8: "auto"}
    for number in (2, 3, 4, 5, 6, 7):
        page = _page(env, doc, number)
        assert (page.confirmed, page.confirmed_at, page.confirm_rule) == (False, None, None)
    document = _document(env, doc)
    assert document.pages_auto_confirmed == 2
    assert document.pages_needs_review == 1
    assert document.pages_kept_unconfirmed == 5, "ok nhưng không qua source_trust_v1 hoặc không có chữ"


def test_auto_confirm_never_confirms_soft_dropped_page(env: ImportEnv) -> None:
    pages: dict[int, Callable[[str], PageExtraction]] = {
        number: (lambda doc, n=number: _text_page(doc, n, _content(doc, n))) for number in range(1, 13)
    }
    for number in NAV_PAGES:
        pages[number] = lambda doc, n=number: _text_page(doc, n, NAV)
    doc = _import(env, pages)
    document = _document(env, doc)
    assert document.pages_dropped == 3
    for page in document.pages:
        if page.dropped:
            assert (page.confirmed, page.confirmed_by) == (False, None)
        else:
            assert page.confirmed_by == "auto"
    assert document.pages_auto_confirmed == 9
    # trang bị bỏ mềm được hoàn tác: trở lại như trang chưa xác nhận, không tự xác nhận
    restore = env.client.post(f"/v1/documents/{doc}/pages/6/restore", json=_body(_page(env, doc, 6).version))
    assert restore.status_code == 200
    restored = _document(env, doc)
    assert restored.pages_kept_unconfirmed == 1 and restored.pages_auto_confirmed == 9


def test_auto_confirm_skips_past_exam_documents(env: ImportEnv) -> None:
    doc = _import(env, {1: clean_page(1), 2: clean_page(2)}, source_kind="past_exam")
    document = _document(env, doc)
    assert document.pages_auto_confirmed == 0 and document.pages_kept_unconfirmed == 2
    assert not any(page.confirmed for page in document.pages)


def test_auto_confirm_form_field_switches_the_rule(env: ImportEnv) -> None:
    off = _import(env, {1: clean_page(1)}, tag="off", auto_confirm="false")
    document = _document(env, off)
    assert document.auto_confirm is False and document.pages_auto_confirmed == 0
    assert _page(env, off, 1).confirmed is False
    env.reader.custom_pages = {1: clean_page(1)}
    env.reader.page_count = 1
    content = pdf_bytes("default")
    response = env.client.post(
        "/v1/documents",
        data={"subject_code": "ET4010", "source_kind": "lecture_slides"},
        files={"file": ("mac_dinh.pdf", content, "application/pdf")},
    )
    assert response.status_code == 202
    by_default = _document(env, _doc_id(content))
    assert by_default.auto_confirm is True and by_default.pages_auto_confirmed == 1


def test_documents_without_auto_pages_keep_the_old_settle_rule(env: ImportEnv) -> None:
    doc = _import(env, {1: clean_page(1), 2: clean_page(2)}, auto_confirm="false")
    assert _confirm_page(env, doc, 1).status_code == 200
    assert _document(env, doc).status == "review"
    assert _confirm_page(env, doc, 2).status_code == 200
    assert _document(env, doc).status == "confirmed" and _has_chunks(env, doc)
    assert [page.confirmed_by for page in _document(env, doc).pages] == ["user", "user"]


def test_batch_confirm_records_the_user(env: ImportEnv) -> None:
    doc = _import(env, {1: clean_page(1), 2: scan_layer_page(2)})
    assert _document(env, doc).pages_kept_unconfirmed == 1
    response = _confirm_document(env, doc)
    assert response.status_code == 200
    document = DocumentImport.model_validate(response.json())
    assert [page.confirmed_by for page in document.pages] == ["auto", "user"]
    assert document.status == "confirmed"


def test_auto_confirm_leaves_the_text_open_for_chapter_and_edits(env: ImportEnv) -> None:
    doc = _import(env, {1: clean_page(1), 2: review_page(2)})
    patch = env.client.patch(f"/v1/documents/{doc}/chapter", json={"editor_id": EDITOR, "chapter_code": "ch7"})
    assert patch.status_code == 200 and DocumentImport.model_validate(patch.json()).chapter_code == "ch7"
    version = _page(env, doc, 1).version
    edit = {"editor_id": EDITOR, "expected_version": version, "edits": [{"line_index": 0, "text": "x"}]}
    assert env.client.patch(f"/v1/documents/{doc}/pages/1/lines", json=edit).status_code == 409
    unconfirmed = _unconfirm(env, doc, 1, version)
    assert unconfirmed.status_code == 200
    edit["expected_version"] = unconfirmed.json()["version"]
    assert env.client.patch(f"/v1/documents/{doc}/pages/1/lines", json=edit).status_code == 200


# --------------------------------------------------------------------------- #
# POST …/unconfirm
# --------------------------------------------------------------------------- #


def test_unconfirm_restores_review_and_blocks_reauto(env: ImportEnv) -> None:
    doc = _import(env, {1: clean_page(1), 2: clean_page(2)})
    before = _page(env, doc, 1)
    response = _unconfirm(env, doc, 1, before.version)
    assert response.status_code == 200
    page = PageExtraction.model_validate(response.json())
    assert (page.confirmed, page.confirmed_at, page.confirmed_by, page.confirm_rule) == (False, None, None, None)
    assert page.auto_confirm_undone is True and page.version == before.version + 1
    assert (page.lines, page.review_reasons, page.status) == (before.lines, before.review_reasons, before.status)
    assert page == _page(env, doc, 1)
    document = _document(env, doc)
    assert (document.pages_auto_confirmed, document.pages_kept_unconfirmed, document.status) == (1, 1, "review")
    assert env.store.list_page_audits(doc, 1) == []
    # đọc lại / bước kết thúc chạy lần nữa: trang không bao giờ tự xác nhận lại, trang khác không đổi
    document_import._finish(env.store, doc, "review", env.store)
    assert _page(env, doc, 1).confirmed is False
    assert _page(env, doc, 1).version == page.version
    assert _document(env, doc).pages_auto_confirmed == 1
    # người dùng vẫn xác nhận được trang đã hoàn tác
    assert _confirm_page(env, doc, 1).status_code == 200
    assert _page(env, doc, 1).confirmed_by == "user"


def test_unconfirm_on_an_unconfirmed_page_is_a_noop(env: ImportEnv) -> None:
    doc = _import(env, {1: clean_page(1), 2: review_page(2)})
    before = _page(env, doc, 2)
    response = _unconfirm(env, doc, 2, 999)  # không kiểm expected_version
    assert response.status_code == 200
    assert PageExtraction.model_validate(response.json()) == before == _page(env, doc, 2)
    assert before.auto_confirm_undone is False


def test_unconfirm_unknown_document_or_page_is_404(env: ImportEnv) -> None:
    doc = _import(env, {1: clean_page(1)})
    assert _unconfirm(env, "doc_" + "0" * 16, 1, 1).status_code == 404
    assert _unconfirm(env, doc, 9, 1).status_code == 404


def test_unconfirm_page_confirmed_by_user_is_409(env: ImportEnv) -> None:
    doc = _import(env, {1: clean_page(1), 2: review_page(2)})
    assert _confirm_page(env, doc, 2).status_code == 200
    response = _unconfirm(env, doc, 2, _page(env, doc, 2).version)
    assert response.status_code == 409 and response.json()["detail"] == USER_CONFIRMED_UNDO_DETAIL
    assert _page(env, doc, 2).confirmed_by == "user"


def test_unconfirm_legacy_confirmed_page_is_409(env: ImportEnv) -> None:
    """Trang xác nhận trước GĐ1 (`confirmed = true`, `confirmed_by = null`) coi như của người dùng."""
    doc = _import(env, {1: clean_page(1), 2: review_page(2)}, auto_confirm="false")
    page = _page(env, doc, 1)
    legacy = page.model_copy(update={"confirmed": True, "version": page.version + 1})
    env.store.update_page(doc, 1, legacy, status="ok", confirmed=True, expected_version=page.version)
    response = _unconfirm(env, doc, 1, legacy.version)
    assert response.status_code == 409 and response.json()["detail"] == USER_CONFIRMED_UNDO_DETAIL


def test_unconfirm_stale_version_is_409(env: ImportEnv) -> None:
    doc = _import(env, {1: clean_page(1)})
    page = _page(env, doc, 1)
    response = _unconfirm(env, doc, 1, page.version + 5)
    assert response.status_code == 409
    assert _page(env, doc, 1).confirmed_by == "auto"


def test_unconfirm_after_the_user_closed_the_document_is_409(env: ImportEnv) -> None:
    doc = _import(env, {1: clean_page(1), 2: clean_page(2)})
    assert _confirm_document(env, doc).status_code == 200
    response = _unconfirm(env, doc, 1, _page(env, doc, 1).version)
    assert response.status_code == 409
    assert _page(env, doc, 1).confirmed_by == "auto" and _document(env, doc).status == "confirmed"


def test_unconfirm_when_the_vault_already_has_chunks_is_409(env: ImportEnv, monkeypatch: pytest.MonkeyPatch) -> None:
    doc = _import(env, {1: clean_page(1)})
    monkeypatch.setattr(type(env.store), "has_document_chunks", lambda self, document_id: True)
    response = _unconfirm(env, doc, 1, _page(env, doc, 1).version)
    assert response.status_code == 409 and response.json()["detail"] == SETTLE_PENDING_DETAIL
    assert _page(env, doc, 1).confirmed_by == "auto"


def test_unconfirm_document_without_chapter_code_is_409(env: ImportEnv) -> None:
    doc = _import(env, {1: clean_page(1)})
    stored = env.store.get_imported_document(doc)
    assert stored is not None
    env.store.update_imported_document(stored.model_copy(update={"chapter_code": None}), stored.version)
    response = _unconfirm(env, doc, 1, _page(env, doc, 1).version)
    assert response.status_code == 409 and response.json()["detail"] == CHAPTER_MISSING_DETAIL


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"editor_id": EDITOR},
        {"expected_version": 1},
        {"editor_id": "", "expected_version": 1},
        {"editor_id": EDITOR, "expected_version": 0},
        {"editor_id": EDITOR, "expected_version": 1, "extra": True},
    ],
)
def test_unconfirm_bad_body_is_422(env: ImportEnv, body: dict[str, Any]) -> None:
    doc = _import(env, {1: clean_page(1)})
    response = env.client.post(f"/v1/documents/{doc}/pages/1/unconfirm", json=body)
    assert response.status_code == 422
    assert _page(env, doc, 1).confirmed_by == "auto"


def test_unconfirm_does_not_touch_other_pages(env: ImportEnv) -> None:
    doc = _import(env, {1: clean_page(1), 2: clean_page(2)})
    other = _page(env, doc, 2)
    assert _unconfirm(env, doc, 1, _page(env, doc, 1).version).status_code == 200
    assert _page(env, doc, 2) == other


def test_ok_page_helper_is_a_trusted_clean_page() -> None:
    """Chốt giả định của các test dùng `ok_page`: trang mặc định của `FakePdfReader` là trang sạch lớp chữ."""
    page = ok_page("doc_" + "a" * 16, 1)
    assert (page.tier_used, page.scan_text_layer, page.status) == ("text_layer", False, "ok")
