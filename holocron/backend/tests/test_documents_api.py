"""API bộ nhập tài liệu, phần 1 (SPECIFICATION §6.5): `POST /v1/documents`, `GET` trạng thái/trang, `DELETE`.

Chạy trên cả ba loại kho (`tests/vault_support.py`); `PdfReader` giả (`tests/document_import_support.py`), hàng đợi
đọc chạy đồng bộ, file gốc ghi vào `tmp_path`.
"""

import hashlib
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from holocron_core.domain.documents import DocumentImport, DocumentSummary, PageExtraction
from holocron_core.domain.ingestion import IngestionItem, UploadedDocument
from holocron_core.infrastructure.document_readers.config import PdfReaderConfig
from holocron_core.infrastructure.document_readers.pdf_reader import PdfInputErrorCode, PdfWorkerError
from holocron_core.routers import deps
from holocron_core.routers.api import app
from holocron_core.services.document_import import SynchronousImportQueue
from holocron_core.services.ingestion_gate import recompute_item
from tests.document_import_support import FakePdfReader, ImportEnv, install, pdf_bytes
from tests.test_api_item_state import _body
from tests.vault_support import NOW, STORE_KINDS, open_store

MAX_DOCUMENT_BYTES = PdfReaderConfig().max_bytes
DOCUMENT_PATHS = [
    ("/v1/documents", "post"),
    ("/v1/documents", "get"),
    ("/v1/documents/{document_id}", "get"),
    ("/v1/documents/{document_id}", "delete"),
    ("/v1/documents/{document_id}/pages/{page_number}", "get"),
]


@pytest.fixture(params=STORE_KINDS)
def env(request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ImportEnv]:
    for store in open_store(str(request.param), tmp_path, monkeypatch):
        yield install(store, tmp_path / "files", SynchronousImportQueue(), FakePdfReader())
        app.dependency_overrides.clear()


def _doc_id(content: bytes) -> str:
    return "doc_" + hashlib.sha256(content).hexdigest()[:16]


def _stored_files(env: ImportEnv) -> list[str]:
    return sorted(path.name for path in env.files.root.iterdir())


def test_openapi_lists_the_document_routes_and_nothing_of_part_two() -> None:
    paths = app.openapi()["paths"]
    for path, method in DOCUMENT_PATHS:
        assert method in paths.get(path, {}), (path, method)
    assert not [p for p in paths if p.startswith("/v1/documents") and ("lines" in p or "confirm" in p or "items" in p)]


def test_post_accepts_a_pdf_and_reads_it_in_the_background(env: ImportEnv) -> None:
    content = pdf_bytes("a")
    doc_id = _doc_id(content)
    response = env.post(content, output_language="en", title="Bài giảng chương 3")
    assert response.status_code == 202
    assert response.headers["location"] == f"/v1/documents/{doc_id}"
    body = DocumentImport.model_validate(response.json())
    assert body.status == "processing" and body.meta.doc_id == doc_id
    assert body.meta.sha256 == hashlib.sha256(content).hexdigest() and body.meta.page_count == 2
    assert body.meta.title == "Bài giảng chương 3" and body.meta.subject_code == "ET4010"
    assert body.output_language == "en" and body.filename == "vi_xu_ly.pdf" and body.size_bytes == len(content)
    assert [(p.page_number, p.status, p.tier_used) for p in body.pages] == [(1, "pending", None), (2, "pending", None)]
    assert env.queue.submitted == [doc_id]
    assert env.reader.reads == [(doc_id, [1, 2])]
    assert _stored_files(env) == [f"{body.meta.sha256}.pdf"]
    assert (env.files.root / f"{body.meta.sha256}.pdf").read_bytes() == content

    status = env.client.get(f"/v1/documents/{doc_id}")
    assert status.status_code == 200
    after = DocumentImport.model_validate(status.json())
    assert after.status == "review" and after.meta.has_text_layer is True
    assert [(p.status, p.tier_used, p.confirmed) for p in after.pages] == [("ok", "text_layer", False)] * 2

    page = env.client.get(f"/v1/documents/{doc_id}/pages/2")
    assert page.status_code == 200
    extraction = PageExtraction.model_validate(page.json())
    assert extraction.page_number == 2 and extraction.version == 2 and not extraction.confirmed
    assert extraction.lines[0].text == "Trang 2: nội dung mẫu tự viết"


def test_title_defaults_to_the_filename_without_extension(env: ImportEnv) -> None:
    body = env.post(pdf_bytes("t"), filename="Chuong 3. Thanh ghi.pdf").json()
    assert body["meta"]["title"] == "Chuong 3. Thanh ghi"
    assert body["output_language"] == "vi"


def test_malicious_filename_never_becomes_a_path(env: ImportEnv, tmp_path: Path) -> None:
    content = pdf_bytes("evil")
    response = env.post(content, filename="../../x.pdf")
    assert response.status_code == 202
    assert response.json()["filename"] == "../../x.pdf", "tên file chỉ là dữ liệu hiển thị"
    assert _stored_files(env) == [f"{hashlib.sha256(content).hexdigest()}.pdf"]
    assert not (tmp_path / "x.pdf").exists() and not (tmp_path.parent / "x.pdf").exists()
    assert all(path.parent == env.files.root for path in env.reader.probed)


def test_oversize_wins_over_every_later_check(env: ImportEnv) -> None:
    response = env.post(b"x" * (MAX_DOCUMENT_BYTES + 1), filename="a" * 300, content_type="image/png")
    assert response.status_code == 413
    assert env.reader.probed == [] and _stored_files(env) == []


def test_exactly_the_size_limit_passes_the_size_check(env: ImportEnv) -> None:
    content = pdf_bytes("max")
    content += b"0" * (MAX_DOCUMENT_BYTES - len(content))
    assert env.post(content).status_code == 202


@pytest.mark.parametrize(
    ("content", "filename", "content_type", "status"),
    [
        (b"", "a.pdf", "application/pdf", 400),
        (b"", "a" * 300, "image/png", 400),
        (pdf_bytes("long"), "a" * 252 + ".pdf", "application/pdf", 422),
        (b"\x89PNG\r\n\x1a\n" + b"0" * 32, "a" * 300, "image/png", 422),
        (b"\x89PNG\r\n\x1a\n" + b"0" * 32, "de.png", "application/pdf", 415),
        (pdf_bytes("ct"), "a.pdf", "image/png", 415),
        (pdf_bytes("ct"), "a.pdf", "text/plain", 415),
    ],
)
def test_upload_checks_follow_the_order_of_the_spec(
    env: ImportEnv, content: bytes, filename: str, content_type: str, status: int
) -> None:
    response = env.post(content, filename=filename, content_type=content_type)
    assert response.status_code == status, response.text
    assert env.reader.probed == [] and _stored_files(env) == []
    assert env.client.get("/v1/documents").json() == []


@pytest.mark.parametrize("content_type", ["application/octet-stream", "application/pdf; charset=binary"])
def test_generic_or_parameterised_pdf_content_type_is_accepted(env: ImportEnv, content_type: str) -> None:
    assert env.post(pdf_bytes(content_type), content_type=content_type).status_code == 202


@pytest.mark.parametrize(
    ("code", "message"),
    [
        ("PASSWORD_REQUIRED", "PDF có mật khẩu"),
        ("OPEN_FAILED", "Không mở được tài liệu"),
        ("OPEN_TIMEOUT", "Không mở được tài liệu"),
        ("TOO_MANY_PAGES", "PDF vượt giới hạn số trang"),
    ],
)
def test_probe_failures_are_422_and_leave_nothing_behind(env: ImportEnv, code: PdfInputErrorCode, message: str) -> None:
    env.reader.probe_error = code
    content = pdf_bytes(code)
    response = env.post(content)
    assert response.status_code == 422
    assert response.json() == {"detail": message}
    assert len(env.reader.probed) == 1 and _stored_files(env) == []
    assert env.store.get_imported_document(_doc_id(content)) is None and env.queue.submitted == []


def test_worker_start_failure_is_a_server_error_and_leaves_nothing_behind(env: ImportEnv) -> None:
    env.reader.probe_exception = PdfWorkerError("worker đọc PDF không khởi động được")
    response = TestClient(app, raise_server_exceptions=False).post(
        "/v1/documents",
        data={"subject_code": "ET4010", "source_kind": "textbook"},
        files={"file": ("a.pdf", pdf_bytes("w"), "application/pdf")},
    )
    assert response.status_code == 500
    assert _stored_files(env) == [] and env.queue.submitted == []


@pytest.mark.parametrize(
    "form",
    [
        {"subject_code": "et4010"},
        {"subject_code": "ET4010X"},
        {"source_kind": "novel"},
        {"output_language": "fr"},
        {"title": "   "},
        {"title": "t" * 301},
    ],
)
def test_invalid_form_fields_are_422(env: ImportEnv, form: dict[str, str]) -> None:
    assert env.post(pdf_bytes("form"), **form).status_code == 422
    assert _stored_files(env) == []


def test_same_content_same_subject_returns_200_without_reading_again(env: ImportEnv) -> None:
    content = pdf_bytes("dup")
    assert env.post(content).status_code == 202
    again = env.post(content, filename="ban_sao.pdf")
    assert again.status_code == 200
    assert "location" not in again.headers
    body = DocumentImport.model_validate(again.json())
    assert body.status == "review" and body.filename == "vi_xu_ly.pdf"
    assert len(env.reader.probed) == 1 and len(env.reader.reads) == 1 and len(env.queue.submitted) == 1


def test_same_content_other_subject_is_409(env: ImportEnv) -> None:
    content = pdf_bytes("dup")
    env.post(content)
    response = env.post(content, subject_code="IT3070")
    assert response.status_code == 409
    assert len(env.reader.probed) == 1
    stored = env.store.get_imported_document(_doc_id(content))
    assert stored is not None and stored.meta.subject_code == "ET4010"


def test_document_first_uploaded_as_an_exam_is_read_as_new(env: ImportEnv) -> None:
    content = pdf_bytes("exam")
    env.store.save_document(UploadedDocument.from_content(content, filename="de.pdf", media_type="application/pdf"))
    response = env.post(content)
    assert response.status_code == 202
    assert env.client.get(f"/v1/documents/{_doc_id(content)}").json()["status"] == "review"


def test_list_documents_newest_first_with_page_counters(env: ImportEnv) -> None:
    env.reader.failed_pages = frozenset({2})
    first, second, other = pdf_bytes("1"), pdf_bytes("2"), pdf_bytes("3")
    env.post(first)
    env.post(second)
    env.post(other, subject_code="IT3070")
    listed = [DocumentSummary.model_validate(item) for item in env.client.get("/v1/documents").json()]
    assert [s.meta.doc_id for s in listed] == [_doc_id(other), _doc_id(second), _doc_id(first)]
    filtered = env.client.get("/v1/documents", params={"subject_code": "ET4010"}).json()
    assert [item["meta"]["doc_id"] for item in filtered] == [_doc_id(second), _doc_id(first)]
    summary = listed[0]
    assert (summary.status, summary.pages_pending, summary.pages_needs_review) == ("review", 0, 0)
    assert (summary.pages_failed, summary.pages_confirmed) == (1, 0)
    assert env.client.get("/v1/documents", params={"subject_code": "et4010"}).status_code == 422


def test_unknown_document_or_page_is_404(env: ImportEnv) -> None:
    content = pdf_bytes("p")
    env.post(content)
    doc_id = _doc_id(content)
    assert env.client.get("/v1/documents/doc_0000000000000000").status_code == 404
    assert env.client.get("/v1/documents/not-an-id").status_code == 404
    for page_number in (0, 3, -1):
        assert env.client.get(f"/v1/documents/{doc_id}/pages/{page_number}").status_code == 404
    assert env.client.get("/v1/documents/doc_0000000000000000/pages/1").status_code == 404
    assert env.client.get(f"/v1/documents/{doc_id}/pages/1").status_code == 200


def test_exam_only_upload_is_not_an_imported_document(env: ImportEnv) -> None:
    content = pdf_bytes("exam-only")
    env.store.save_document(UploadedDocument.from_content(content, filename="de.pdf", media_type="application/pdf"))
    assert env.client.get(f"/v1/documents/{_doc_id(content)}").status_code == 404
    assert env.client.delete(f"/v1/documents/{_doc_id(content)}").status_code == 404


def test_reader_that_cannot_reopen_the_document_marks_it_failed(env: ImportEnv) -> None:
    env.reader.read_error = PdfWorkerError("worker đọc PDF không khởi động được")
    content = pdf_bytes("reopen")
    assert env.post(content).status_code == 202
    body = DocumentImport.model_validate(env.client.get(f"/v1/documents/{_doc_id(content)}").json())
    assert body.status == "failed"
    assert [p.status for p in body.pages] == ["failed", "failed"]
    page = PageExtraction.model_validate(env.client.get(f"/v1/documents/{_doc_id(content)}/pages/1").json())
    assert page.failure_reason == "PAGE_ERROR"


def test_delete_removes_the_document_its_pages_and_the_original_file(env: ImportEnv) -> None:
    content, kept = pdf_bytes("del"), pdf_bytes("keep")
    env.post(content)
    env.post(kept)
    response = env.client.delete(f"/v1/documents/{_doc_id(content)}")
    assert response.status_code == 204 and response.content == b""
    assert env.client.get(f"/v1/documents/{_doc_id(content)}").status_code == 404
    assert env.store.list_pages(_doc_id(content), PageExtraction) == []
    assert _stored_files(env) == [f"{hashlib.sha256(kept).hexdigest()}.pdf"]
    assert env.client.delete(f"/v1/documents/{_doc_id(content)}").status_code == 404
    assert env.post(content).status_code == 202, "xoá xong thì nhập lại được như tài liệu mới"


def test_delete_while_reading_is_409(env: ImportEnv) -> None:
    idle = SynchronousImportQueue(run=lambda job: None)  # việc đọc chưa chạy: tài liệu giữ `processing`
    busy = install(env.store, env.files.root, idle, env.reader)
    content = pdf_bytes("busy")
    assert busy.post(content).status_code == 202
    response = busy.client.delete(f"/v1/documents/{_doc_id(content)}")
    assert response.status_code == 409
    assert busy.client.get(f"/v1/documents/{_doc_id(content)}").json()["status"] == "processing"
    assert _stored_files(busy) == [f"{hashlib.sha256(content).hexdigest()}.pdf"]


def test_delete_while_an_item_points_to_the_document_is_409(env: ImportEnv) -> None:
    content = pdf_bytes("item")
    env.post(content)
    body = {**_body({"quantum": 4}), "source_doc_id": _doc_id(content)}
    env.store.save_item(recompute_item(IngestionItem(item_id="ing_doc", **body), deps.REGISTRY))
    assert env.client.delete(f"/v1/documents/{_doc_id(content)}").status_code == 409
    assert env.client.get(f"/v1/documents/{_doc_id(content)}").status_code == 200
    assert len(_stored_files(env)) == 1


def test_uploaded_at_comes_from_the_injected_clock(env: ImportEnv) -> None:
    body = env.post(pdf_bytes("clock")).json()
    assert body["meta"]["uploaded_at"].startswith(NOW.date().isoformat())


def test_document_import_must_list_every_page_in_order(env: ImportEnv) -> None:
    body = DocumentImport.model_validate(env.post(pdf_bytes("model")).json())
    for pages in (body.pages[:1], body.pages[::-1], [*body.pages, body.pages[0]]):
        with pytest.raises(ValueError, match="page_count"):
            DocumentImport.model_validate({**body.model_dump(), "pages": [p.model_dump() for p in pages]})
