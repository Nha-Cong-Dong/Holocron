"""Bổ sung test qua API cho các đột biến còn sống của bộ nhập tài liệu (SPECIFICATION §6.5): `PATCH …/lines` giữ kiểm
chéo không phải `mismatch` (I5), ghi audit ở dạng NFC, không đánh dấu trang đã xác nhận; tài liệu `past_exam` đã
`confirmed` đóng văn bản; dòng dài đúng giới hạn vào Vault.

Chạy trên cả ba loại kho (`tests/vault_support.py`), `PdfReader` giả với trang dựng sẵn, hàng đợi đọc đồng bộ. Văn
bản là mẫu tự viết, không phải tài liệu môn học có bản quyền.
"""

import hashlib
import unicodedata
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from holocron_core.domain.documents import MAX_LINE_CHARS, DocumentImport, PageExtraction
from holocron_core.routers.api import app
from holocron_core.services.document_import import SynchronousImportQueue
from tests.document_import_support import FakePdfReader, ImportEnv, install, ok_page, pdf_bytes
from tests.vault_support import STORE_KINDS, open_store

EDITOR = "local_user"


@pytest.fixture(params=STORE_KINDS)
def env(request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ImportEnv]:
    for store in open_store(str(request.param), tmp_path, monkeypatch):
        yield install(store, tmp_path / "files", SynchronousImportQueue(), FakePdfReader())
        app.dependency_overrides.clear()


def _line(index: int, text: str, conf: float) -> dict[str, Any]:
    return {"line_index": index, "text": text, "bbox": None, "conf": conf, "engine": "tesseract"}


def ocr_page(document_id: str, page_number: int, cross_check_status: str) -> PageExtraction:
    """Trang OCR cần xem lại, kiểm chéo `unavailable` (engine phụ không chạy) hoặc `not_applicable`."""
    reasons = ["CROSS_CHECK_UNAVAILABLE"] if cross_check_status == "unavailable" else ["LOW_OCR_SCORE"]
    return PageExtraction.model_validate(
        {
            "document_id": document_id,
            "page_number": page_number,
            "status": "needs_review",
            "tier_used": "ocr",
            "text_score": 0.1,
            "ocr_score": 0.5,
            "lines": [_line(0, "MOV R0, #0x1E", 0.95), _line(1, "ADDS R0, R1, R2", 0.95)],
            "cross_check": {"status": cross_check_status, "secondary_engine": None},
            "review_reasons": reasons,
        }
    )


def blank_page(document_id: str, page_number: int) -> PageExtraction:
    return PageExtraction.model_validate(
        {
            "document_id": document_id,
            "page_number": page_number,
            "status": "ok",
            "tier_used": "text_layer",
            "text_score": 0.99,
            "cross_check": {"status": "not_applicable", "secondary_engine": None},
        }
    )


def long_line_page(document_id: str, page_number: int) -> PageExtraction:
    """Trang có một dòng dài đúng `MAX_LINE_CHARS` ký tự."""
    return PageExtraction.model_validate(
        {
            "document_id": document_id,
            "page_number": page_number,
            "status": "ok",
            "tier_used": "text_layer",
            "text_score": 0.95,
            "lines": [
                {"line_index": 0, "text": "a" * MAX_LINE_CHARS, "bbox": None, "conf": 0.95, "engine": "text_layer"}
            ],
            "cross_check": {"status": "not_applicable", "secondary_engine": None},
        }
    )


def _post(env: ImportEnv, tag: str, pages: dict[int, Callable[[str], PageExtraction]], **form: Any) -> str:
    env.reader.custom_pages = pages
    env.reader.page_count = max(pages)
    content = pdf_bytes(tag)
    response = env.post(content, **form)
    assert response.status_code == 202, response.text
    return "doc_" + hashlib.sha256(content).hexdigest()[:16]


def _page(env: ImportEnv, doc_id: str, number: int) -> PageExtraction:
    response = env.client.get(f"/v1/documents/{doc_id}/pages/{number}")
    assert response.status_code == 200
    return PageExtraction.model_validate(response.json())


def _document(env: ImportEnv, doc_id: str) -> DocumentImport:
    response = env.client.get(f"/v1/documents/{doc_id}")
    assert response.status_code == 200
    return DocumentImport.model_validate(response.json())


def _patch(env: ImportEnv, doc_id: str, number: int, version: int, **body: Any) -> Any:
    payload = {"editor_id": EDITOR, "expected_version": version, "edits": [], "appended_lines": [], **body}
    return env.client.patch(f"/v1/documents/{doc_id}/pages/{number}/lines", json=payload)


def _confirm_page(env: ImportEnv, doc_id: str, number: int, version: int) -> Any:
    return env.client.post(
        f"/v1/documents/{doc_id}/pages/{number}/confirm", json={"editor_id": EDITOR, "expected_version": version}
    )


# --------------------------------------------------------------------------- #
# I5: PATCH không biến kiểm chéo `unavailable`/`not_applicable` thành `ok`
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("cross_check_status", ["unavailable", "not_applicable"])
def test_patch_keeps_a_cross_check_that_is_not_mismatch(env: ImportEnv, cross_check_status: str) -> None:
    doc_id = _post(env, f"kiem-cheo-{cross_check_status}", {1: lambda d: ocr_page(d, 1, cross_check_status)})
    before = _page(env, doc_id, 1)

    response = _patch(env, doc_id, 1, before.version, edits=[{"line_index": 0, "text": "MOV R0, #0x1F"}])

    assert response.status_code == 200, response.text
    returned = PageExtraction.model_validate(response.json())
    stored = _page(env, doc_id, 1)
    for page in (returned, stored):
        assert page.cross_check.status == cross_check_status
        assert page.cross_check.secondary_engine is None
        assert page.review_reasons == before.review_reasons
        assert page.status == "needs_review"
    summary = _document(env, doc_id).pages[0]
    assert summary.status == "needs_review" and summary.review_reasons == before.review_reasons


# --------------------------------------------------------------------------- #
# PATCH: audit ở dạng NFC; trang sửa xong chưa xác nhận
# --------------------------------------------------------------------------- #


def test_patch_audit_records_nfc_text(env: ImportEnv) -> None:
    doc_id = _post(env, "audit-nfc", {1: lambda d: ocr_page(d, 1, "unavailable")})
    nfd = unicodedata.normalize("NFD", "Thanh ghi trạng thái")
    nfc = unicodedata.normalize("NFC", nfd)
    assert nfd != nfc

    response = _patch(
        env, doc_id, 1, _page(env, doc_id, 1).version, edits=[{"line_index": 0, "text": nfd}], appended_lines=[nfd]
    )

    assert response.status_code == 200, response.text
    [audit] = env.store.list_page_audits(doc_id, 1)
    assert [(c.line_index, c.old, c.new) for c in audit.changes] == [(0, "MOV R0, #0x1E", nfc), (2, None, nfc)]
    assert [line.text for line in _page(env, doc_id, 1).lines][0::2] == [nfc, nfc]


def test_patch_leaves_the_page_unconfirmed_in_the_store_summary(env: ImportEnv) -> None:
    doc_id = _post(env, "sua-chua-xac-nhan", {1: lambda d: ocr_page(d, 1, "unavailable"), 2: lambda d: ok_page(d, 2)})

    response = _patch(env, doc_id, 1, _page(env, doc_id, 1).version, appended_lines=["Dòng gõ thêm"])

    assert response.status_code == 200, response.text
    assert [summary.confirmed for summary in env.store.list_page_summaries(doc_id)] == [False, False]
    assert [summary.confirmed for summary in _document(env, doc_id).pages] == [False, False]
    listed = env.client.get("/v1/documents")
    assert listed.status_code == 200
    [row] = [item for item in listed.json() if item["meta"]["doc_id"] == doc_id]
    assert row["pages_confirmed"] == 0


# --------------------------------------------------------------------------- #
# Tài liệu đã `confirmed` (kể cả `past_exam`, không có chunk trong Vault) đóng văn bản
# --------------------------------------------------------------------------- #


def test_confirmed_past_exam_rejects_editing_its_blank_page(env: ImportEnv) -> None:
    doc_id = _post(
        env, "de-cu-trang-trang", {1: lambda d: ok_page(d, 1), 2: lambda d: blank_page(d, 2)}, source_kind="past_exam"
    )
    assert _confirm_page(env, doc_id, 1, _page(env, doc_id, 1).version).status_code == 200
    assert _document(env, doc_id).status == "confirmed"
    assert env.store.list_chunks("ET4010") == [], "past_exam không được cắt chunk"
    blank = _page(env, doc_id, 2)
    assert not blank.confirmed and blank.lines == []

    response = _patch(env, doc_id, 2, blank.version, appended_lines=["Câu 1: tính cờ sau lệnh ADDS."])

    assert response.status_code == 409, response.text
    assert _page(env, doc_id, 2) == blank
    assert env.store.list_page_audits(doc_id, 2) == []


# --------------------------------------------------------------------------- #
# Dòng dài đúng giới hạn vào Vault
# --------------------------------------------------------------------------- #


def test_line_of_exactly_max_chars_is_confirmed_into_the_vault(env: ImportEnv) -> None:
    doc_id = _post(env, "dong-dung-gioi-han", {1: lambda d: long_line_page(d, 1)})

    response = _confirm_page(env, doc_id, 1, _page(env, doc_id, 1).version)

    assert response.status_code == 200, response.text
    assert _document(env, doc_id).status == "confirmed"
    chunks = [chunk for chunk in env.store.list_chunks("ET4010") if chunk.doc_id == doc_id]
    assert chunks, "văn bản đã xác nhận được cắt chunk"
    assert "a" * MAX_LINE_CHARS in "".join(chunk.raw_text for chunk in chunks).replace("\n", "")


def test_patch_with_line_index_equal_to_the_line_count_is_422(env: ImportEnv) -> None:
    doc_id = _post(env, "dong-khong-ton-tai", {1: lambda d: ocr_page(d, 1, "unavailable")})

    response = _patch(env, doc_id, 1, _page(env, doc_id, 1).version, edits=[{"line_index": 2, "text": "x"}])

    assert response.status_code == 422, response.text
    assert env.store.list_page_audits(doc_id, 1) == []
