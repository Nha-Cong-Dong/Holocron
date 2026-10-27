"""API bộ nhập tài liệu, phần 2 (SPECIFICATION §6.5): field `chapter_code`, `PATCH …/lines`, xác nhận trang và tài liệu,
nhập văn bản đã xác nhận vào Knowledge Vault.

Chạy trên cả ba loại kho (`tests/vault_support.py`), `PdfReader` giả với trang dựng sẵn, hàng đợi đọc đồng bộ. Văn
bản là mẫu tự viết, không phải tài liệu môn học có bản quyền.
"""

import hashlib
import unicodedata
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from holocron_core.domain.documents import CrossCheck, DocumentImport, PageExtraction
from holocron_core.domain.schemas import ChunkCategory, normalize_quote
from holocron_core.routers.api import app
from holocron_core.services.document_import import IMPORT_ERROR_MESSAGES, SynchronousImportQueue
from tests.document_import_support import FakePdfReader, HoldingQueue, ImportEnv, install, ok_page, pdf_bytes
from tests.vault_support import NOW, STORE_KINDS, open_store

EDITOR = "local_user"


@pytest.fixture(params=STORE_KINDS)
def env(request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ImportEnv]:
    for store in open_store(str(request.param), tmp_path, monkeypatch):
        yield install(store, tmp_path / "files", SynchronousImportQueue(), FakePdfReader())
        app.dependency_overrides.clear()


def _doc_id(content: bytes) -> str:
    return "doc_" + hashlib.sha256(content).hexdigest()[:16]


def _line(index: int, text: str, conf: float, *, flags: list[str] | None = None) -> dict[str, Any]:
    return {
        "line_index": index,
        "text": text,
        "bbox": [0.1, 0.1 + index * 0.05, 0.9, 0.14 + index * 0.05],
        "conf": conf,
        "engine": "tesseract",
        "flags": flags or [],
    }


def ocr_review_page(document_id: str, page_number: int, *, extra_reasons: list[str] | None = None) -> PageExtraction:
    """Trang OCR cần xem kỹ: dòng 0 conf thấp, dòng 2 lệch token với RapidOCR (§5.7)."""
    return PageExtraction.model_validate(
        {
            "document_id": document_id,
            "page_number": page_number,
            "status": "needs_review",
            "tier_used": "ocr",
            "text_score": 0.1,
            "ocr_score": 0.9,
            "coverage_ratio": 1.0,
            "ink_outside_text_ratio": 0.0,
            "lines": [
                _line(0, "Thanh ghi CPSR chứa các cờ trạng thái", 0.42, flags=["LOW_LINE_CONF"]),
                _line(1, "Lệnh MOV nạp hằng số vào thanh ghi.", 0.93),
                _line(2, "MOV R0, #0x1E", 0.91, flags=["TOKEN_MISMATCH"]),
            ],
            "cross_check": {
                "status": "mismatch",
                "secondary_engine": "rapidocr",
                "mismatches": [{"line_index": 2, "primary_value": "#0x1E", "secondary_value": "#0x1F"}],
            },
            "review_reasons": ["LOW_CONF_LINES", "TOKEN_MISMATCH", *(extra_reasons or [])],
        }
    )


def blank_page(document_id: str, page_number: int) -> PageExtraction:
    return PageExtraction(
        document_id=document_id,
        page_number=page_number,
        status="ok",
        tier_used="text_layer",
        text_score=0.99,
        cross_check=CrossCheck(status="not_applicable", secondary_engine=None),
    )


def _post(
    env: ImportEnv,
    tag: str,
    pages: dict[int, Callable[[str], PageExtraction]],
    *,
    page_count: int | None = None,
    **form: Any,
) -> str:
    env.reader.custom_pages = pages
    env.reader.page_count = max([2, *pages]) if page_count is None else page_count
    content = pdf_bytes(tag)
    response = env.post(content, **form)
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


def _patch(env: ImportEnv, doc_id: str, number: int, version: int, **body: Any) -> Any:
    payload = {"editor_id": EDITOR, "expected_version": version, "edits": [], "appended_lines": [], **body}
    return env.client.patch(f"/v1/documents/{doc_id}/pages/{number}/lines", json=payload)


def _confirm_page(env: ImportEnv, doc_id: str, number: int, version: int) -> Any:
    return env.client.post(
        f"/v1/documents/{doc_id}/pages/{number}/confirm", json={"editor_id": EDITOR, "expected_version": version}
    )


def _confirm_document(env: ImportEnv, doc_id: str) -> Any:
    return env.client.post(f"/v1/documents/{doc_id}/confirm", json={"editor_id": EDITOR})


# --------------------------------------------------------------------------- #
# chapter_code (form bắt buộc)
# --------------------------------------------------------------------------- #


def test_chapter_code_is_required_stored_and_returned(env: ImportEnv) -> None:
    content = pdf_bytes("chuong")
    accepted = env.post(content, chapter_code="ch3.2")
    assert accepted.status_code == 202 and accepted.json()["chapter_code"] == "ch3.2"
    assert _document(env, _doc_id(content)).chapter_code == "ch3.2"
    stored = env.store.get_imported_document(_doc_id(content))
    assert stored is not None and stored.chapter_code == "ch3.2"


@pytest.mark.parametrize("chapter_code", [None, "", "3", "chuong3", "ch100", "ch3.", "CH3"])
def test_missing_or_invalid_chapter_code_is_422_and_stores_nothing(env: ImportEnv, chapter_code: str | None) -> None:
    content = pdf_bytes(f"sai-{chapter_code}")
    data = {"subject_code": "ET4010", "source_kind": "textbook"}
    if chapter_code is not None:
        data["chapter_code"] = chapter_code
    response = env.client.post("/v1/documents", data=data, files={"file": ("a.pdf", content, "application/pdf")})
    assert response.status_code == 422
    assert env.reader.probed == [] and list(env.files.root.iterdir()) == []


def test_same_content_same_subject_other_chapter_is_a_409(env: ImportEnv) -> None:
    """§6.5 bước 3 (duyệt 2026-10-26 chiều): cùng nội dung, cùng môn, khác chương ⇒ 409; không đọc lại, không đổi
    chương."""
    content = pdf_bytes("hai-chuong")
    env.post(content, chapter_code="ch3")
    again = env.post(content, chapter_code="ch4")
    assert again.status_code == 409 and again.json() == {"detail": IMPORT_ERROR_MESSAGES["CHAPTER_CONFLICT"]}
    assert len(env.reader.probed) == 1


# --------------------------------------------------------------------------- #
# PATCH …/lines
# --------------------------------------------------------------------------- #


def test_editing_the_mismatched_line_clears_token_mismatch_but_keeps_low_conf(env: ImportEnv) -> None:
    doc_id = _post(env, "sua-token", {1: lambda d: ocr_review_page(d, 1, extra_reasons=["HIGH_INK_OUTSIDE_TEXT"])})
    before = _page(env, doc_id, 1)
    response = _patch(env, doc_id, 1, before.version, edits=[{"line_index": 2, "text": "MOV R0, #0x1F"}])
    assert response.status_code == 200, response.text
    page = PageExtraction.model_validate(response.json())
    assert page.version == before.version + 1
    edited = page.lines[2]
    assert (edited.text, edited.edited_by_human, edited.flags) == ("MOV R0, #0x1F", True, [])
    assert (edited.engine, edited.conf, edited.bbox) == ("tesseract", 0.91, before.lines[2].bbox), "giữ engine và conf"
    assert page.cross_check.status == "ok" and page.cross_check.mismatches == []
    assert page.cross_check.secondary_engine == "rapidocr"
    assert page.review_reasons == ["HIGH_INK_OUTSIDE_TEXT", "LOW_CONF_LINES"]
    assert page.status == "needs_review" and not page.confirmed
    assert page.lines[0] == before.lines[0] and page.lines[1] == before.lines[1]
    assert _page(env, doc_id, 1) == page
    audits = env.store.list_page_audits(doc_id, 1)
    assert len(audits) == 1
    audit = audits[0]
    assert (audit.from_version, audit.to_version, audit.editor_id) == (before.version, page.version, EDITOR)
    assert [(c.line_index, c.old, c.new) for c in audit.changes] == [(2, "MOV R0, #0x1E", "MOV R0, #0x1F")]
    assert audit.at == NOW or audit.at > NOW


def test_editing_every_flagged_line_makes_the_page_ok(env: ImportEnv) -> None:
    doc_id = _post(env, "sua-het", {1: lambda d: ocr_review_page(d, 1)})
    first = _patch(env, doc_id, 1, _page(env, doc_id, 1).version, edits=[{"line_index": 2, "text": "MOV R0, #0x1F"}])
    page = PageExtraction.model_validate(first.json())
    assert page.review_reasons == ["LOW_CONF_LINES"]
    unchanged_text = page.lines[0].text
    second = _patch(env, doc_id, 1, page.version, edits=[{"line_index": 0, "text": unchanged_text}])
    page = PageExtraction.model_validate(second.json())
    assert page.lines[0].edited_by_human and page.lines[0].conf == 0.42, "dòng đã sửa không tính conf thấp"
    assert page.review_reasons == [] and page.status == "ok"
    summary = _document(env, doc_id).pages[0]
    assert (summary.status, summary.review_reasons) == ("ok", [])
    assert len(env.store.list_page_audits(doc_id, 1)) == 2


def test_other_page_reasons_are_kept_after_editing(env: ImportEnv) -> None:
    kept = ["LOW_OCR_SCORE", "FIGURE_ON_SCAN", "CROSS_CHECK_UNAVAILABLE", "HIGH_PAGE_CER"]
    doc_id = _post(env, "giu-ly-do", {1: lambda d: ocr_review_page(d, 1, extra_reasons=kept)})
    page = _page(env, doc_id, 1)
    edits = [{"line_index": 0, "text": "a"}, {"line_index": 2, "text": "b"}]
    after = PageExtraction.model_validate(_patch(env, doc_id, 1, page.version, edits=edits).json())
    assert after.review_reasons == ["LOW_OCR_SCORE", "FIGURE_ON_SCAN", "CROSS_CHECK_UNAVAILABLE", "HIGH_PAGE_CER"]
    assert after.status == "needs_review"


def test_unedited_low_conf_line_keeps_low_conf_lines(env: ImportEnv) -> None:
    doc_id = _post(env, "con-conf-thap", {1: lambda d: ocr_review_page(d, 1)})
    page = _page(env, doc_id, 1)
    after = PageExtraction.model_validate(
        _patch(env, doc_id, 1, page.version, edits=[{"line_index": 1, "text": "x"}]).json()
    )
    assert after.review_reasons == ["LOW_CONF_LINES", "TOKEN_MISMATCH"], "dòng 0 và token lệch chưa được sửa"
    assert after.cross_check.status == "mismatch" and len(after.cross_check.mismatches) == 1


def test_appended_lines_go_to_the_end_as_human_lines(env: ImportEnv) -> None:
    doc_id = _post(env, "go-them", {})
    page = _page(env, doc_id, 1)
    nfd = unicodedata.normalize("NFD", "Ghi chú thêm của người học")
    after = PageExtraction.model_validate(_patch(env, doc_id, 1, page.version, appended_lines=[nfd, ""]).json())
    assert len(after.lines) == len(page.lines) + 2
    added = after.lines[len(page.lines)]
    assert added.text == unicodedata.normalize("NFC", nfd)
    assert (added.engine, added.bbox, added.conf, added.edited_by_human) == ("human", None, 1.0, True)
    assert after.lines[-1].text == "" and after.status == "ok"
    changes = env.store.list_page_audits(doc_id, 1)[0].changes
    assert [(c.line_index, c.old) for c in changes] == [(1, None), (2, None)]


def test_edit_text_is_normalized_to_nfc(env: ImportEnv) -> None:
    doc_id = _post(env, "nfc", {})
    page = _page(env, doc_id, 1)
    nfd = unicodedata.normalize("NFD", "Bộ vi xử lý")
    after = _patch(env, doc_id, 1, page.version, edits=[{"line_index": 0, "text": nfd}]).json()
    assert after["lines"][0]["text"] == unicodedata.normalize("NFC", nfd)


def test_stale_version_is_409_and_changes_nothing(env: ImportEnv) -> None:
    doc_id = _post(env, "cu", {})
    page = _page(env, doc_id, 1)
    response = _patch(env, doc_id, 1, page.version - 1, edits=[{"line_index": 0, "text": "x"}])
    assert response.status_code == 409 and isinstance(response.json()["detail"], str)
    assert _page(env, doc_id, 1) == page and env.store.list_page_audits(doc_id, 1) == []


@pytest.mark.parametrize(
    "body",
    [
        {"edits": [{"line_index": 5, "text": "x"}]},
        {"edits": [{"line_index": 0, "text": "x"}, {"line_index": 0, "text": "y"}]},
        {"edits": [], "appended_lines": []},
        {"edits": [{"line_index": 0, "text": "x" * 2001}]},
        {"appended_lines": ["x"] * 501},
        {"appended_lines": ["x" * 2001]},
        {"edits": [{"line_index": -1, "text": "x"}]},
        {"edits": [{"line_index": 0, "text": "x"}], "unknown": 1},
        {"editor_id": ""},
    ],
)
def test_invalid_bodies_are_422(env: ImportEnv, body: dict[str, Any]) -> None:
    doc_id = _post(env, "body-sai", {})
    page = _page(env, doc_id, 1)
    payload = {"edits": [{"line_index": 0, "text": "x"}], **body}
    response = _patch(env, doc_id, 1, page.version, **payload)
    assert response.status_code == 422, response.text
    assert _page(env, doc_id, 1) == page


def test_failed_pending_and_unknown_pages_cannot_be_edited(env: ImportEnv) -> None:
    env.reader.failed_pages = frozenset({2})
    doc_id = _post(env, "trang-loi", {})
    failed = _page(env, doc_id, 2)
    response = _patch(env, doc_id, 2, failed.version, appended_lines=["gõ lại"])
    assert response.status_code == 409 and "nhập lại" in response.json()["detail"]
    assert _patch(env, doc_id, 3, 1, appended_lines=["x"]).status_code == 404
    assert _patch(env, "doc_0000000000000000", 1, 1, appended_lines=["x"]).status_code == 404

    held = HoldingQueue()
    reading = install(env.store, env.files.root, SynchronousImportQueue(), env.reader, runtime_queue=held)
    content = pdf_bytes("dang-doc")
    assert reading.post(content).status_code == 202
    pending = _page(reading, _doc_id(content), 1)
    assert pending.status == "pending"
    assert _patch(reading, _doc_id(content), 1, pending.version, appended_lines=["x"]).status_code == 409


def test_confirmed_page_cannot_be_edited(env: ImportEnv) -> None:
    doc_id = _post(env, "da-xac-nhan", {})
    page = _page(env, doc_id, 1)
    confirmed = PageExtraction.model_validate(_confirm_page(env, doc_id, 1, page.version).json())
    response = _patch(env, doc_id, 1, confirmed.version, edits=[{"line_index": 0, "text": "x"}])
    assert response.status_code == 409


# --------------------------------------------------------------------------- #
# Xác nhận trang
# --------------------------------------------------------------------------- #


def test_confirming_a_needs_review_page_marks_it_reviewed(env: ImportEnv) -> None:
    doc_id = _post(env, "xem-lai", {1: lambda d: ocr_review_page(d, 1)})
    page = _page(env, doc_id, 1)
    response = _confirm_page(env, doc_id, 1, page.version)
    assert response.status_code == 200, response.text
    confirmed = PageExtraction.model_validate(response.json())
    assert confirmed.confirmed and confirmed.confirmed_at is not None and confirmed.version == page.version + 1
    assert confirmed.status == "needs_review" and confirmed.review_reasons == page.review_reasons
    assert confirmed.lines == page.lines
    assert _page(env, doc_id, 1) == confirmed

    again = _confirm_page(env, doc_id, 1, page.version)
    assert again.status_code == 200, "gọi lại trên trang đã xác nhận: 200, không đổi gì"
    assert PageExtraction.model_validate(again.json()) == confirmed
    assert _page(env, doc_id, 1) == confirmed


def test_page_confirm_errors(env: ImportEnv) -> None:
    env.reader.failed_pages = frozenset({2})
    doc_id = _post(env, "xac-nhan-loi", {3: lambda d: blank_page(d, 3)})
    page = _page(env, doc_id, 1)
    assert _confirm_page(env, doc_id, 1, page.version + 5).status_code == 409
    assert _confirm_page(env, doc_id, 2, _page(env, doc_id, 2).version).status_code == 409, "trang failed"
    blank = _confirm_page(env, doc_id, 3, _page(env, doc_id, 3).version)
    assert blank.status_code == 409 and "văn bản" in blank.json()["detail"]
    assert _confirm_page(env, doc_id, 4, 1).status_code == 404
    assert _confirm_page(env, "doc_0000000000000000", 1, 1).status_code == 404
    assert env.client.post(f"/v1/documents/{doc_id}/pages/1/confirm", json={"editor_id": EDITOR}).status_code == 422
    assert not _page(env, doc_id, 1).confirmed


def test_pending_page_cannot_be_confirmed(env: ImportEnv) -> None:
    held = HoldingQueue()
    reading = install(env.store, env.files.root, SynchronousImportQueue(), env.reader, runtime_queue=held)
    content = pdf_bytes("cho-doc")
    reading.post(content)
    assert _confirm_page(reading, _doc_id(content), 1, 1).status_code == 409
    assert _confirm_document(reading, _doc_id(content)).status_code == 409


# --------------------------------------------------------------------------- #
# Xác nhận tài liệu và nhập Vault
# --------------------------------------------------------------------------- #


def test_document_confirm_takes_only_ok_pages_and_text_enters_the_vault_after_the_last_page(env: ImportEnv) -> None:
    env.reader.failed_pages = frozenset({4})
    doc_id = _post(
        env,
        "ca-tai-lieu",
        {2: lambda d: ocr_review_page(d, 2), 3: lambda d: blank_page(d, 3)},
        page_count=4,
        chapter_code="ch3.2",
    )
    assert env.store.list_chunks("ET4010") == [], "chưa xác nhận thì không văn bản nào vào Vault"

    first = _confirm_document(env, doc_id)
    assert first.status_code == 200, first.text
    body = DocumentImport.model_validate(first.json())
    assert [(p.page_number, p.status, p.confirmed) for p in body.pages] == [
        (1, "ok", True),
        (2, "needs_review", False),
        (3, "ok", False),
        (4, "failed", False),
    ]
    assert body.status == "review", "trang needs_review phải xác nhận từng trang"
    assert env.store.list_chunks("ET4010") == []

    review = _page(env, doc_id, 2)
    edits = [{"line_index": 2, "text": "MOV R0, #0x1F"}, {"line_index": 1, "text": ""}]
    edited = PageExtraction.model_validate(_patch(env, doc_id, 2, review.version, edits=edits).json())
    assert _confirm_page(env, doc_id, 2, edited.version).status_code == 200
    document = _document(env, doc_id)
    assert document.status == "confirmed" and document.chapter_code == "ch3.2"

    chunks = env.store.list_chunks("ET4010")
    assert chunks, "tài liệu confirmed thì văn bản đã xác nhận vào Vault"
    assert all(
        (c.doc_id, c.chapter_code, c.category, c.topic_keys, c.label_status)
        == (doc_id, "ch3.2", ChunkCategory.THEORY, [], "proposed")
        for c in chunks
    )
    text = " ".join(c.raw_text for c in chunks)
    assert "Trang 1: nội dung mẫu tự viết" in text and "MOV R0, #0x1F" in text
    assert "#0x1E" not in text, "văn bản là bản người dùng đã sửa"
    assert "Lệnh MOV nạp hằng số" not in text, "dòng có text rỗng không vào văn bản xác nhận"
    assert all(normalize_quote(c.verbatim_quote) in normalize_quote(c.raw_text) for c in chunks), "I3"
    assert {c.page_number for c in chunks} <= {1, 2}


def test_confirming_again_is_idempotent(env: ImportEnv) -> None:
    doc_id = _post(env, "lap-lai", {})
    assert _confirm_document(env, doc_id).json()["status"] == "confirmed"
    chunks = env.store.list_chunks("ET4010")
    assert chunks
    again = _confirm_document(env, doc_id)
    assert again.status_code == 200 and again.json()["status"] == "confirmed"
    assert _confirm_page(env, doc_id, 1, 1).status_code == 200
    assert env.store.list_chunks("ET4010") == chunks


def test_past_exam_documents_are_confirmed_without_chunks(env: ImportEnv) -> None:
    doc_id = _post(env, "de-cu", {}, source_kind="past_exam")
    assert _confirm_document(env, doc_id).json()["status"] == "confirmed"
    assert env.store.list_chunks("ET4010") == []


def test_document_without_any_text_page_is_not_confirmed(env: ImportEnv) -> None:
    doc_id = _post(env, "trang-trang", {1: lambda d: blank_page(d, 1), 2: lambda d: blank_page(d, 2)})
    body = _confirm_document(env, doc_id).json()
    assert body["status"] == "review" and env.store.list_chunks("ET4010") == []


def test_confirmed_document_cannot_be_edited_further(env: ImportEnv) -> None:
    doc_id = _post(env, "khoa-sua", {2: lambda d: blank_page(d, 2)})
    assert _confirm_document(env, doc_id).json()["status"] == "confirmed"
    blank = _page(env, doc_id, 2)
    response = _patch(env, doc_id, 2, blank.version, appended_lines=["thêm chữ vào trang trắng"])
    assert response.status_code == 409


def test_document_confirm_unknown_document_is_404(env: ImportEnv) -> None:
    assert _confirm_document(env, "doc_0000000000000000").status_code == 404


def test_delete_after_confirm_removes_the_chunks(env: ImportEnv) -> None:
    doc_id = _post(env, "xoa-sau", {})
    _confirm_document(env, doc_id)
    assert env.store.list_chunks("ET4010")
    assert env.client.delete(f"/v1/documents/{doc_id}").status_code == 204
    assert env.store.list_chunks("ET4010") == []


def test_ok_page_from_a_reader_page_builder_is_confirmable(env: ImportEnv) -> None:
    doc_id = _post(env, "trang-ok", {1: lambda d: ok_page(d, 1)})
    assert _confirm_page(env, doc_id, 1, _page(env, doc_id, 1).version).status_code == 200
    assert _document(env, doc_id).status == "review", "trang 2 có văn bản, chưa xác nhận"
