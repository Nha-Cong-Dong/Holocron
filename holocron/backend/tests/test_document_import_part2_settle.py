"""Chốt trạng thái ở đường kết thúc `failed` (SPECIFICATION §5.7 "Chốt trạng thái tài liệu", §6.5): tài liệu vừa
thành `failed` vì không đọc tiếp được vẫn thành `confirmed` khi mọi trang có văn bản đã xác nhận, và (khác
`past_exam`) được cắt chunk vào Vault với `chapter_code` của tài liệu, `raw_text` trích nguyên văn (I3).

Chạy trên cả ba loại kho. Hàng đợi runtime giữ việc (`HoldingQueue`) để test tự chạy lần đọc bằng hàng đợi đồng bộ
với bộ đọc giả lỗi. Văn bản là mẫu tự viết.
"""

import dataclasses
import hashlib
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest

from holocron_core.domain.documents import PageExtraction
from holocron_core.domain.repositories import AppStore
from holocron_core.infrastructure.document_readers.pdf_reader import PdfInputError
from holocron_core.routers.api import app
from holocron_core.services import document_import
from holocron_core.services.document_import import (
    ReadJob,
    SynchronousImportQueue,
    confirmed_text,
    mark_document_failed,
    requeue_pending,
)
from holocron_core.services.vault_chunking import build_chunks
from tests.document_import_support import FakePdfReader, HoldingQueue, ImportEnv, install, ok_page, pdf_bytes
from tests.vault_support import STORE_KINDS, open_store

EDITOR = "local_user"
SUBJECT = "ET4010"
CHAPTER = "ch4.2"


@dataclasses.dataclass
class Setup:
    env: ImportEnv
    store: AppStore
    held: HoldingQueue


@pytest.fixture(params=STORE_KINDS)
def setup(request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Setup]:
    for store in open_store(str(request.param), tmp_path, monkeypatch):
        held = HoldingQueue()
        env = install(
            store, tmp_path / "files", SynchronousImportQueue(), FakePdfReader(page_count=3), runtime_queue=held
        )
        yield Setup(env=env, store=store, held=held)
        app.dependency_overrides.clear()


@dataclasses.dataclass
class CrashAfterFirstPage(FakePdfReader):
    """Trả trang đầu được yêu cầu rồi ném `RuntimeError` (worker chết giữa chừng): trang còn lại giữ `pending`."""

    def read_pages(
        self, path: Path, document_id: str, page_numbers: Sequence[int] | None = None
    ) -> Iterator[PageExtraction]:
        numbers = list(range(1, self.page_count + 1)) if page_numbers is None else list(page_numbers)
        yield ok_page(document_id, numbers[0])
        raise RuntimeError("worker chết giữa chừng")


def _post_held(setup: Setup, tag: str, source_kind: str = "lecture_slides") -> tuple[str, ReadJob]:
    content = pdf_bytes(tag)
    response = setup.env.post(content, source_kind=source_kind, chapter_code=CHAPTER)
    assert response.status_code == 202, response.text
    [job] = setup.held.release()
    return "doc_" + hashlib.sha256(content).hexdigest()[:16], job


def _read(job: ReadJob, reader: FakePdfReader) -> None:
    SynchronousImportQueue().submit(dataclasses.replace(job, reader=reader))


def _first_page_read(setup: Setup, tag: str, source_kind: str = "lecture_slides") -> tuple[str, ReadJob]:
    """Trang 1 đã đọc (`ok`), trang 2–3 còn `pending`, tài liệu `processing`."""
    doc_id, job = _post_held(setup, tag, source_kind)
    _read(job, CrashAfterFirstPage(page_count=3))
    assert [p.status for p in setup.store.list_pages(doc_id, PageExtraction)] == ["ok", "pending", "pending"]
    return doc_id, job


def _confirm_page(setup: Setup, doc_id: str, number: int) -> None:
    page = setup.store.get_page(doc_id, number, PageExtraction)
    assert page is not None
    response = setup.env.client.post(
        f"/v1/documents/{doc_id}/pages/{number}/confirm", json={"editor_id": EDITOR, "expected_version": page.version}
    )
    assert response.status_code == 200, response.text


def _status(setup: Setup, doc_id: str) -> str:
    document = setup.store.get_imported_document(doc_id)
    assert document is not None
    return document.status


def _fail_by_open_error(setup: Setup, doc_id: str, job: ReadJob) -> None:
    """Bộ đọc không mở lại được tài liệu (`PdfInputError`) ⇒ `_open_pages` đánh dấu `failed`."""
    _read(job, FakePdfReader(page_count=3, read_error=PdfInputError("OPEN_FAILED")))


def _fail_by_unexpected_error(setup: Setup, doc_id: str, job: ReadJob) -> None:
    """Lỗi bất ngờ của bộ đọc (lỗi lập trình) ⇒ `run_job` đánh dấu `failed`."""
    _read(job, FakePdfReader(page_count=3, read_error=KeyError("lỗi lập trình của bộ đọc")))


def _fail_by_missing_file(setup: Setup, doc_id: str, job: ReadJob) -> None:
    """Khởi động lại mà file gốc đã mất ⇒ `requeue_pending` đánh dấu `failed` (Vault và khoá như router cấp)."""
    document = setup.store.get_imported_document(doc_id)
    assert document is not None
    assert setup.env.files.delete(document.meta.sha256)
    runtime = setup.env.runtime
    queue = SynchronousImportQueue()
    queued = requeue_pending(
        setup.store, runtime.files, FakePdfReader(page_count=3), queue, vault=setup.store, locks=runtime.locks
    )
    assert queued == [] and queue.submitted == []


FAILURE_PATHS: list[Callable[[Setup, str, ReadJob], None]] = [
    _fail_by_open_error,
    _fail_by_unexpected_error,
    _fail_by_missing_file,
]
FAILURE_IDS = ["khong-mo-lai-duoc", "loi-bat-ngo-qua-run-job", "mat-file-goc-khi-khoi-dong"]


@pytest.mark.parametrize("fail", FAILURE_PATHS, ids=FAILURE_IDS)
def test_failed_end_settles_when_every_text_page_was_confirmed(
    setup: Setup, fail: Callable[[Setup, str, ReadJob], None]
) -> None:
    doc_id, job = _first_page_read(setup, f"failed-settle-{fail.__name__}")
    assert job.vault is not None, "router cấp Vault cho việc đọc"
    _confirm_page(setup, doc_id, 1)
    assert _status(setup, doc_id) == "processing"
    assert not setup.store.has_document_chunks(doc_id), "chưa đủ điều kiện thì chưa vào Vault"

    fail(setup, doc_id, job)

    pages = setup.store.list_pages(doc_id, PageExtraction)
    assert [p.status for p in pages] == ["ok", "failed", "failed"]
    assert _status(setup, doc_id) == "confirmed"
    response = setup.env.client.get(f"/v1/documents/{doc_id}")
    assert response.status_code == 200 and response.json()["status"] == "confirmed"

    chunks = [chunk for chunk in setup.store.list_chunks(SUBJECT) if chunk.doc_id == doc_id]
    assert chunks, "tài liệu khác past_exam được cắt chunk vào Vault"
    document = setup.store.get_imported_document(doc_id)
    assert document is not None
    text = confirmed_text(document, pages)
    assert text is not None
    assert sorted(chunk.chunk_id for chunk in chunks) == sorted(chunk.chunk_id for chunk in build_chunks(text))
    source = "\n".join(line.text for page in pages if page.confirmed for line in page.lines)
    for chunk in chunks:
        assert chunk.chapter_code == CHAPTER, "chunk mang chapter_code của tài liệu"
        assert chunk.page_number == 1
        assert chunk.raw_text in source, "raw_text là trích dẫn nguyên văn của trang đã xác nhận (I3)"
        assert chunk.verbatim_quote in chunk.raw_text


@pytest.mark.parametrize("fail", FAILURE_PATHS, ids=FAILURE_IDS)
def test_failed_end_confirms_a_past_exam_without_chunks(
    setup: Setup, fail: Callable[[Setup, str, ReadJob], None]
) -> None:
    doc_id, job = _first_page_read(setup, f"failed-past-exam-{fail.__name__}", source_kind="past_exam")
    _confirm_page(setup, doc_id, 1)
    fail(setup, doc_id, job)
    assert _status(setup, doc_id) == "confirmed"
    assert not setup.store.has_document_chunks(doc_id), "§6.5: tài liệu past_exam không được cắt chunk"


@pytest.mark.parametrize("fail", FAILURE_PATHS, ids=FAILURE_IDS)
def test_failed_end_with_every_page_failed_does_not_confirm(
    setup: Setup, fail: Callable[[Setup, str, ReadJob], None]
) -> None:
    doc_id, job = _post_held(setup, f"failed-moi-trang-{fail.__name__}")
    fail(setup, doc_id, job)
    assert [p.status for p in setup.store.list_pages(doc_id, PageExtraction)] == ["failed"] * 3
    assert _status(setup, doc_id) == "failed", "không có trang nào có văn bản ⇒ KHÔNG confirmed (§5.7)"
    assert not setup.store.has_document_chunks(doc_id)


@pytest.mark.parametrize("fail", FAILURE_PATHS, ids=FAILURE_IDS)
def test_failed_end_keeps_failed_while_a_text_page_is_unconfirmed(
    setup: Setup, fail: Callable[[Setup, str, ReadJob], None]
) -> None:
    doc_id, job = _first_page_read(setup, f"failed-chua-xac-nhan-{fail.__name__}")
    fail(setup, doc_id, job)
    assert _status(setup, doc_id) == "failed"
    assert not setup.store.has_document_chunks(doc_id)


def test_failed_status_with_pending_pages_puts_nothing_in_the_vault(setup: Setup) -> None:
    """Bước kết thúc `failed` gặp tài liệu còn trang `pending` (ví dụ chưa ghi xong trang `failed`) KHÔNG cắt chunk
    nào, kể cả khi mọi trang có văn bản khác đã xác nhận."""
    doc_id, _ = _first_page_read(setup, "failed-con-pending")
    _confirm_page(setup, doc_id, 1)
    document_import._finish(setup.store, doc_id, "failed", setup.store)
    assert [p.status for p in setup.store.list_pages(doc_id, PageExtraction)] == ["ok", "pending", "pending"]
    assert _status(setup, doc_id) == "failed"
    assert not setup.store.has_document_chunks(doc_id)


def test_marking_failed_without_vault_does_not_settle(setup: Setup) -> None:
    """Gọi không có Vault (test, script) chỉ ghi `failed`; lần xác nhận kế tiếp settle tài liệu."""
    doc_id, _ = _first_page_read(setup, "failed-khong-vault")
    _confirm_page(setup, doc_id, 1)
    mark_document_failed(setup.store, doc_id, setup.env.runtime.locks)
    assert _status(setup, doc_id) == "failed"
    assert not setup.store.has_document_chunks(doc_id)
    response = setup.env.client.post(f"/v1/documents/{doc_id}/confirm", json={"editor_id": EDITOR})
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    assert body["status"] == "confirmed"
    assert setup.store.has_document_chunks(doc_id)
