"""Bộ nhập tài liệu (SPECIFICATION §5.7, §6.1, §6.5; ADR-008 mục 7): điều kiện duyệt câu 13.2 và 13.6, cùng hai
sửa sau review /finish-run 28/10.

- 13.2: hàng đợi từ chối lần nhập một PDF trùng nội dung với đề đã upload (§6.1) ⇒ file gốc bị xoá; item của đề
  vẫn đọc, sửa và approve được như trước, vì không đường nào của item cần file gốc.
- 13.6: lỗi môi trường lặp lại khi mở bộ đọc (`PdfWorkerError`, `OSError`) được thử lại có giới hạn rồi để tài liệu
  `processing`; lối ra là xếp lại khi khởi động (`requeue_on_startup`) hoặc `DELETE` rồi `POST` lại (202).
- Request tới sau khi `lifespan` đã gỡ bộ nhập: 500 kèm `detail`, không 500 trần.

Chạy trên ba loại kho (`tests/vault_support.py`); byte PDF và văn bản là mẫu tự tạo.
"""

import hashlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from holocron_core.domain.ingestion import IngestionItem, UploadedDocument
from holocron_core.domain.schemas import SourceKind
from holocron_core.infrastructure.document_readers.pdf_reader import PdfWorkerError
from holocron_core.infrastructure.storage import DATA_DIR_ENV, DATABASE_FILENAME, SQLiteStore
from holocron_core.infrastructure.storage.original_files import OriginalFileStore
from holocron_core.routers import deps, documents
from holocron_core.routers.api import app
from holocron_core.services.document_import import (
    IMPORT_UNAVAILABLE_DETAIL,
    BackgroundImportQueue,
    ImportRequest,
    ImportRuntime,
    RetryPolicy,
    SynchronousImportQueue,
    accept_document,
)
from holocron_core.services.ingestion_gate import recompute_item
from tests.document_import_support import FakePdfReader, ImportEnv, install, pdf_bytes
from tests.test_api_item_state import _body
from tests.test_document_import_failures import RefusingQueue
from tests.vault_support import NOW, STORE_KINDS, open_store

WAIT_S = 10.0
EDITOR = "local_user"
# Thử lại không chờ (test không sleep); số lần thử là giới hạn cần chứng minh.
FAST_RETRY = RetryPolicy(attempts=3, delay_s=0.0)
ENVIRONMENT_ERRORS = [PdfWorkerError("worker không khởi động được"), OSError("thiếu tài nguyên của máy")]


@pytest.fixture(params=STORE_KINDS)
def env(request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ImportEnv]:
    for store in open_store(str(request.param), tmp_path, monkeypatch):
        yield install(store, tmp_path / "files", SynchronousImportQueue(), FakePdfReader())
        app.dependency_overrides.clear()


@pytest.fixture
def fresh_default_store() -> Iterator[None]:
    deps._default_store.cache_clear()
    yield
    deps._default_store.cache_clear()


def _doc_id(content: bytes) -> str:
    return "doc_" + hashlib.sha256(content).hexdigest()[:16]


def _stored_files(files: OriginalFileStore) -> list[str]:
    return sorted(path.name for path in files.root.iterdir())


def _use_runtime(runtime: ImportRuntime) -> None:
    app.dependency_overrides[documents.get_import_runtime] = lambda: runtime


def _document(client: TestClient, doc_id: str) -> dict[str, Any]:
    response = client.get(f"/v1/documents/{doc_id}")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _page_statuses(client: TestClient, doc_id: str) -> list[str]:
    return [page["status"] for page in _document(client, doc_id)["pages"]]


# --------------------------------------------------------------------------- #
# 13.2: item của đề vẫn dùng được sau khi file gốc của lần nhập bị huỷ đã xoá
# --------------------------------------------------------------------------- #


def _table_patch(version: int, text: str) -> dict[str, Any]:
    return {"editor_id": "u_tuan", "expected_version": version, "patches": [{"row": 0, "col": 2, "new_text": text}]}


def test_exam_item_works_without_the_original_file_after_a_rejected_import(env: ImportEnv) -> None:
    """Dựng như `test_rejected_submit_keeps_the_exam_upload_record_and_its_item`, rồi dùng item qua mọi endpoint
    đọc hay sửa item. Upload đề §6.1 không lưu byte của file; lần nhập bị huỷ xoá file gốc nó vừa lưu."""
    content = pdf_bytes("de-cu-13-2")
    doc_id = _doc_id(content)
    upload = UploadedDocument.from_content(content, filename="de.pdf", media_type="application/pdf")
    env.store.save_document(upload)
    item = IngestionItem(item_id="ing_cu", **{**_body({"quantum": 4}), "source_doc_id": doc_id})
    env.store.save_item(recompute_item(item, deps.REGISTRY))
    client = env.client
    item_before = client.get("/v1/ingestion/items/ing_cu")
    assert item_before.status_code == 200
    assert item_before.json()["report"]["status"] == "AUTO_PASS", item_before.json()["report"]["issues"]
    document_before = client.get(f"/v1/documents/{doc_id}")
    pages_before = client.get(f"/v1/documents/{doc_id}/pages/1")
    list_before = client.get("/v1/documents")

    refusing = install(env.store, env.files.root, env.queue, env.reader, runtime_queue=RefusingQueue())
    response = refusing.post(content, filename="tai_lieu_khac_ten.pdf")
    assert response.status_code == 500 and response.json() == {"detail": IMPORT_UNAVAILABLE_DETAIL}
    assert _stored_files(env.files) == [], "file gốc của lần nhập bị huỷ đã xoá"
    assert env.store.get_document(doc_id) == upload

    client = refusing.client
    item_after = client.get("/v1/ingestion/items/ing_cu")
    assert item_after.status_code == 200 and item_after.json() == item_before.json()
    assert item_after.json()["source_doc_id"] == doc_id and item_after.json()["page_number"] == 2
    for path, before in (
        (f"/v1/documents/{doc_id}", document_before),
        (f"/v1/documents/{doc_id}/pages/1", pages_before),
        ("/v1/documents", list_before),
    ):
        after = client.get(path)
        assert (after.status_code, after.json()) == (before.status_code, before.json()), path

    stem = client.patch(
        "/v1/ingestion/items/ing_cu/stem", json={"editor_id": "u_tuan", "stem_latex": r"Round Robin, $q = 4$ ms."}
    )
    assert stem.status_code == 200, stem.text
    assert stem.json()["stem_latex"] == r"Round Robin, $q = 4$ ms."
    broken = client.patch("/v1/ingestion/items/ing_cu/table", json=_table_patch(1, "3"))
    assert broken.status_code == 200, broken.text
    assert broken.json()["table_primary"]["version"] == 2
    fixed = client.patch("/v1/ingestion/items/ing_cu/table", json=_table_patch(2, "2"))
    assert fixed.status_code == 200, fixed.text
    assert fixed.json()["report"]["status"] == "AUTO_PASS", fixed.json()["report"]["issues"]
    approved = client.post("/v1/ingestion/items/ing_cu/approve")
    assert approved.status_code == 200 and approved.json()["approved"] is True
    assert _stored_files(env.files) == [], "không thao tác nào của item cần (hay tạo lại) file gốc"


# --------------------------------------------------------------------------- #
# 13.6: lỗi môi trường lặp lại khi mở bộ đọc không làm tài liệu kẹt `processing`
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("error", ENVIRONMENT_ERRORS, ids=["worker", "oserror"])
def test_retries_run_out_in_one_session_then_delete_and_reimport(env: ImportEnv, error: Exception) -> None:
    """Hàng đợi nền thật: hết `attempts` lần thử thì luồng đọc dừng, tài liệu giữ `processing` nhưng không còn việc
    nào giữ nó ⇒ `DELETE` được (204) và `POST` lại cùng file được nhận như tài liệu mới (202)."""
    reader = FakePdfReader(page_count=2, read_errors=[error] * FAST_RETRY.attempts)
    jobs = BackgroundImportQueue()
    try:
        runtime = ImportRuntime(files=env.files, queue=jobs, retry=FAST_RETRY)
        _use_runtime(runtime)
        app.dependency_overrides[documents.get_pdf_reader] = lambda: reader
        content = pdf_bytes("loi-moi-truong")
        doc_id = _doc_id(content)
        assert env.post(content).status_code == 202
        assert jobs.wait_until_idle(WAIT_S)
        assert len(reader.reads) == FAST_RETRY.attempts, "thử lại có giới hạn, không lặp vô hạn"
        assert _document(env.client, doc_id)["status"] == "processing"
        assert _page_statuses(env.client, doc_id) == ["pending", "pending"]
        assert not jobs.is_active(doc_id), "không việc nào còn giữ tài liệu"

        assert env.client.delete(f"/v1/documents/{doc_id}").status_code == 204
        assert env.client.get(f"/v1/documents/{doc_id}").status_code == 404
        assert _stored_files(env.files) == []
        assert env.post(content).status_code == 202, "nhập lại được sau khi gỡ"
        assert jobs.wait_until_idle(WAIT_S)
        assert _document(env.client, doc_id)["status"] == "review"
        assert _page_statuses(env.client, doc_id) == ["ok", "ok"]
    finally:
        jobs.close(WAIT_S)


def _stuck_after_retries(env: ImportEnv, reader: FakePdfReader, tag: str) -> str:
    """Nhận tài liệu với `reader` lỗi môi trường ở mọi lần thử: tài liệu giữ `processing`, trang giữ `pending`."""
    _use_runtime(ImportRuntime(files=env.files, queue=SynchronousImportQueue(), retry=FAST_RETRY))
    app.dependency_overrides[documents.get_pdf_reader] = lambda: reader
    content = pdf_bytes(tag)
    assert env.post(content).status_code == 202
    assert len(reader.reads) == FAST_RETRY.attempts
    doc_id = _doc_id(content)
    assert _document(env.client, doc_id)["status"] == "processing"
    return doc_id


def _restart(env: ImportEnv, reader: FakePdfReader, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Khởi động lại: runtime mới trên cùng kho và thư mục file gốc, chạy đúng bước khởi động của `lifespan`."""
    runtime = ImportRuntime(files=OriginalFileStore(env.files.root), queue=SynchronousImportQueue(), retry=FAST_RETRY)
    _use_runtime(runtime)
    monkeypatch.setattr(documents, "load_pdf_reader", lambda config: reader)
    return documents.requeue_on_startup(env.store, runtime)


@pytest.mark.parametrize("error", ENVIRONMENT_ERRORS, ids=["worker", "oserror"])
def test_document_stuck_after_retries_is_read_again_on_restart(
    env: ImportEnv, error: Exception, monkeypatch: pytest.MonkeyPatch
) -> None:
    reader = FakePdfReader(page_count=2, read_errors=[error] * FAST_RETRY.attempts)
    doc_id = _stuck_after_retries(env, reader, "khoi-dong-lai")
    assert _restart(env, reader, monkeypatch) == [doc_id]
    assert _document(env.client, doc_id)["status"] == "review"
    assert _page_statuses(env.client, doc_id) == ["ok", "ok"]


@pytest.mark.parametrize("error", ENVIRONMENT_ERRORS, ids=["worker", "oserror"])
def test_error_that_persists_after_restart_still_allows_delete_and_reimport(
    env: ImportEnv, error: Exception, monkeypatch: pytest.MonkeyPatch
) -> None:
    reader = FakePdfReader(page_count=2, read_errors=[error] * FAST_RETRY.attempts)
    doc_id = _stuck_after_retries(env, reader, "van-loi")
    reader.read_errors = [error] * FAST_RETRY.attempts
    assert _restart(env, reader, monkeypatch) == [doc_id]
    assert len(reader.reads) == 2 * FAST_RETRY.attempts, "lần khởi động cũng chỉ thử lại có giới hạn"
    assert _document(env.client, doc_id)["status"] == "processing"
    assert env.client.delete(f"/v1/documents/{doc_id}").status_code == 204
    assert env.post(pdf_bytes("van-loi")).status_code == 202
    assert _document(env.client, doc_id)["status"] == "review"


def test_app_startup_reads_a_document_left_processing_by_exhausted_retries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fresh_default_store: None
) -> None:
    """Khởi động app thật (`lifespan`, kho SQLite trong `HOLOCRON_DATA_DIR`) sau một phiên hết lượt thử."""
    store = SQLiteStore.open(tmp_path / DATABASE_FILENAME)
    try:
        failing = FakePdfReader(page_count=2, read_errors=[PdfWorkerError("x")] * FAST_RETRY.attempts)
        runtime = ImportRuntime(
            files=OriginalFileStore.open(tmp_path), queue=SynchronousImportQueue(), retry=FAST_RETRY
        )
        outcome = accept_document(
            ImportRequest(
                content=pdf_bytes("lifespan"),
                filename="a.pdf",
                declared_type="application/pdf",
                subject_code="ET4010",
                source_kind=SourceKind.TEXTBOOK,
                chapter_code="ch3",
            ),
            store=store,
            runtime=runtime,
            reader=failing,
            now=NOW,
        )
        doc_id = outcome.document.meta.doc_id
        stuck = store.get_imported_document(doc_id)
        assert stuck is not None and stuck.status == "processing"
    finally:
        store.close()
    reader = FakePdfReader(page_count=2)
    monkeypatch.setattr(documents, "load_pdf_reader", lambda config: reader)
    monkeypatch.setenv(DATA_DIR_ENV, str(tmp_path))
    with TestClient(app) as client:
        started = getattr(app.state, documents.RUNTIME_STATE_ATTR)
        assert started.queue.wait_until_idle(WAIT_S)
        assert _document(client, doc_id)["status"] == "review"
    assert reader.reads == [(doc_id, [1, 2])]


# --------------------------------------------------------------------------- #
# Review MAJOR: request tới sau khi `lifespan` đã gỡ bộ nhập ⇒ 500 kèm `detail`
# --------------------------------------------------------------------------- #

UNAVAILABLE = {"detail": IMPORT_UNAVAILABLE_DETAIL}
_FORM = {"subject_code": "ET4010", "source_kind": "textbook", "chapter_code": "ch3"}


def _without_runtime() -> TestClient:
    """Bỏ override của bộ nhập: `app.state` không có runtime, như sau khi `lifespan` đã gỡ nó."""
    app.dependency_overrides.pop(documents.get_import_runtime, None)
    app.dependency_overrides.pop(documents.get_pdf_reader, None)
    assert not hasattr(app.state, documents.RUNTIME_STATE_ATTR)
    return TestClient(app, raise_server_exceptions=False)


def _runtime_routes(doc_id: str) -> list[tuple[str, str, dict[str, Any]]]:
    return [
        ("POST", "/v1/documents", {"data": _FORM, "files": {"file": ("b.pdf", pdf_bytes("sau"), "application/pdf")}}),
        ("DELETE", f"/v1/documents/{doc_id}", {}),
        (
            "PATCH",
            f"/v1/documents/{doc_id}/pages/1/lines",
            {"json": {"editor_id": EDITOR, "expected_version": 2, "edits": [], "appended_lines": ["thêm"]}},
        ),
        ("POST", f"/v1/documents/{doc_id}/pages/1/confirm", {"json": {"editor_id": EDITOR, "expected_version": 2}}),
        ("POST", f"/v1/documents/{doc_id}/confirm", {"json": {"editor_id": EDITOR}}),
    ]


@pytest.mark.parametrize("route", range(5), ids=["post", "delete", "patch_lines", "confirm_page", "confirm_document"])
def test_runtime_routes_after_lifespan_teardown_answer_500_with_a_detail(env: ImportEnv, route: int) -> None:
    content = pdf_bytes("truoc-khi-tat")
    doc_id = _doc_id(content)
    assert env.post(content).status_code == 202
    before = _document(env.client, doc_id)
    files_before = _stored_files(env.files)
    client = _without_runtime()
    method, path, kwargs = _runtime_routes(doc_id)[route]
    response = client.request(method, path, **kwargs)
    assert response.status_code == 500 and response.json() == UNAVAILABLE, response.text
    assert _document(client, doc_id) == before, "không ghi gì"
    assert _stored_files(env.files) == files_before
    assert len(env.reader.probed) == 1, "chỉ lần nhận trước khi tắt được mở thử; request sau khi tắt không mở file nào"


def test_read_routes_after_lifespan_teardown_still_answer(env: ImportEnv) -> None:
    """`GET` không dùng bộ nhập: vẫn trả dữ liệu đang lưu (200/404 như thường), không 500 trần."""
    content = pdf_bytes("doc-sau-khi-tat")
    doc_id = _doc_id(content)
    assert env.post(content).status_code == 202
    before = {path: env.client.get(path).json() for path in _read_paths(doc_id)}
    client = _without_runtime()
    for path, body in before.items():
        response = client.get(path)
        assert response.status_code == 200 and response.json() == body, path
    missing = client.get("/v1/documents/doc_0000000000000000")
    assert missing.status_code == 404 and missing.json() == {"detail": documents.NOT_FOUND_DETAIL}


def _read_paths(doc_id: str) -> list[str]:
    return ["/v1/documents", f"/v1/documents/{doc_id}", f"/v1/documents/{doc_id}/pages/1"]


def test_post_after_the_real_lifespan_has_ended_answers_500_with_a_detail(
    monkeypatch: pytest.MonkeyPatch, fresh_default_store: None
) -> None:
    monkeypatch.delenv(DATA_DIR_ENV, raising=False)
    client = TestClient(app, raise_server_exceptions=False)
    with client:
        assert hasattr(app.state, documents.RUNTIME_STATE_ATTR)
    assert not hasattr(app.state, documents.RUNTIME_STATE_ATTR)
    response = client.post(
        "/v1/documents", data=_FORM, files={"file": ("c.pdf", pdf_bytes("da-tat"), "application/pdf")}
    )
    assert response.status_code == 500 and response.json() == UNAVAILABLE
