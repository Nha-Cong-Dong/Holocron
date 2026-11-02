"""Bổ sung test cho các đột biến còn sống của bộ nhập tài liệu ở tầng kho và luồng đọc nền (SPECIFICATION §5.7, §6.5):
`revert_imported_document` tăng version của dòng `documents` và tính lại liên kết chunk như nhau ở mọi kho; bước settle
sau lần đọc của tài liệu cũ thiếu `chapter_code` không làm hỏng lần đọc; `requeue_pending` đưa Vault vào việc đọc.

Chạy trên cả ba loại kho (`tests/vault_support.py`). Văn bản là mẫu tự viết.
"""

import dataclasses
import hashlib
import logging
from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest

from holocron_core.domain.documents import CrossCheck, PageExtraction
from holocron_core.domain.ingestion import UploadedDocument
from holocron_core.domain.repositories import AppStore, ImportedDocument
from holocron_core.domain.schemas import DocumentMeta, SourceKind
from holocron_core.routers.api import app
from holocron_core.services.document_import import ReadJob, SynchronousImportQueue, requeue_pending
from holocron_core.services.vault import ingest_confirmed_text
from tests.document_import_support import FakePdfReader, HoldingQueue, ImportEnv, install, ok_page, pdf_bytes
from tests.vault_support import ARM_PAGES, NOW, STORE_KINDS, add_document, confirmed, document_id, open_store

EDITOR = "local_user"
SUBJECT = "ET4010"
TOPIC = "mp.arm.cpsr.flags"
DOC = "doc_eeeeeeeeeeeeeeee"
SHA = ("eeeeeeeeeeeeeeee" * 4)[:64]


@pytest.fixture(params=STORE_KINDS)
def store(request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[AppStore]:
    yield from open_store(str(request.param), tmp_path, monkeypatch)


# --------------------------------------------------------------------------- #
# revert_imported_document (có upload đề trước đó)
# --------------------------------------------------------------------------- #


def _upload() -> UploadedDocument:
    return UploadedDocument(
        document_id=DOC, sha256=SHA, filename="de.pdf", media_type="application/pdf", size_bytes=2048, uploaded_at=NOW
    )


def _pending(page_number: int) -> PageExtraction:
    return PageExtraction(
        document_id=DOC,
        page_number=page_number,
        status="pending",
        tier_used=None,
        text_score=None,
        cross_check=CrossCheck(status="not_applicable", secondary_engine=None),
    )


def _create(store: AppStore) -> ImportedDocument:
    imported = ImportedDocument(
        meta=DocumentMeta(
            doc_id=DOC,
            subject_code=SUBJECT,
            title="Kỹ thuật vi xử lý",
            source_kind=SourceKind.LECTURE_SLIDES,
            sha256=SHA,
            page_count=1,
            has_text_layer=False,
            uploaded_at=NOW,
        ),
        filename="vxl.pdf",
        size_bytes=2048,
        chapter_code="ch3",
        status="processing",
    )
    return store.create_imported_document(imported, [_pending(1)])


def test_revert_bumps_the_document_version_on_every_store(store: AppStore) -> None:
    """Gỡ phần nhập là một lần ghi dòng `documents` (SQLite: `version = version + 1`): lần nhập lại sau đó có
    version lớn hơn đúng 2 so với lần nhập trước, như nhau ở kho in-memory và SQLite."""
    upload = _upload()
    store.save_document(upload)
    created = _create(store)
    assert store.revert_imported_document(DOC, upload) is True
    recreated = _create(store)
    assert recreated.version == created.version + 2
    stored = store.get_imported_document(DOC)
    assert stored is not None and stored.version == recreated.version


def test_revert_recomputes_links_of_the_remaining_chunks(store: AppStore) -> None:
    upload = _upload()
    store.save_document(upload)
    _create(store)
    theory = ingest_confirmed_text(store, confirmed(DOC, ARM_PAGES, topic_keys=[TOPIC]))
    practice_doc = document_id("f")
    add_document(store, practice_doc)
    practice = ingest_confirmed_text(
        store,
        confirmed(
            practice_doc,
            [["Bài 1", "Cho R1 = 1 và R2 = 2, tính cờ sau lệnh ADDS."]],
            category="PRACTICE",
            topic_keys=[TOPIC],
        ),
    )
    assert practice[0].linked_chunk_ids == [theory[0].chunk_id]

    assert store.revert_imported_document(DOC, upload) is True

    remaining = store.get_chunk(practice[0].chunk_id)
    assert remaining is not None and remaining.linked_chunk_ids == []
    assert [chunk.chunk_id for chunk in store.list_chunks(SUBJECT)] == [practice[0].chunk_id]


# --------------------------------------------------------------------------- #
# Lần đọc nền: settle sau khi đọc và xếp lại khi khởi động
# --------------------------------------------------------------------------- #


@dataclasses.dataclass
class Setup:
    env: ImportEnv
    store: AppStore
    held: HoldingQueue


@pytest.fixture(params=STORE_KINDS)
def setup(request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Setup]:
    for opened in open_store(str(request.param), tmp_path, monkeypatch):
        held = HoldingQueue()
        env = install(
            opened, tmp_path / "files", SynchronousImportQueue(), FakePdfReader(page_count=3), runtime_queue=held
        )
        yield Setup(env=env, store=opened, held=held)
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


def blank_page(document_id: str, page_number: int) -> PageExtraction:
    return PageExtraction(
        document_id=document_id,
        page_number=page_number,
        status="ok",
        tier_used="text_layer",
        text_score=0.99,
        cross_check=CrossCheck(status="not_applicable", secondary_engine=None),
    )


def _blank_rest_reader() -> FakePdfReader:
    """Bộ đọc trả trang 2–3 không có chữ (không chặn xác nhận tài liệu)."""
    return FakePdfReader(page_count=3, custom_pages={2: lambda d: blank_page(d, 2), 3: lambda d: blank_page(d, 3)})


def _first_page_confirmed(setup: Setup, tag: str) -> tuple[str, ReadJob]:
    """Trang 1 đã đọc và đã xác nhận, trang 2–3 còn `pending`, tài liệu `processing`."""
    content = pdf_bytes(tag)
    response = setup.env.post(content, chapter_code="ch3")
    assert response.status_code == 202, response.text
    [job] = setup.held.release()
    doc_id = "doc_" + hashlib.sha256(content).hexdigest()[:16]
    SynchronousImportQueue().submit(dataclasses.replace(job, reader=CrashAfterFirstPage(page_count=3)))
    pages = setup.store.list_pages(doc_id, PageExtraction)
    assert [p.status for p in pages] == ["ok", "pending", "pending"]
    confirm = setup.env.client.post(
        f"/v1/documents/{doc_id}/pages/1/confirm", json={"editor_id": EDITOR, "expected_version": pages[0].version}
    )
    assert confirm.status_code == 200, confirm.text
    return doc_id, job


def _status(setup: Setup, doc_id: str) -> str:
    document = setup.store.get_imported_document(doc_id)
    assert document is not None
    return document.status


def test_reading_end_of_a_document_without_chapter_keeps_review(setup: Setup, caplog: pytest.LogCaptureFixture) -> None:
    """Tài liệu nhập trước khi có `chapter_code` (§5.7): settle sau lần đọc không nhập được Vault, ghi cảnh báo và
    giữ `review`; lỗi không thoát ra `run_job` (nơi lỗi lạ làm tài liệu `failed`)."""
    doc_id, job = _first_page_confirmed(setup, "cu-thieu-chuong")
    document = setup.store.get_imported_document(doc_id)
    assert document is not None
    setup.store.update_imported_document(document.model_copy(update={"chapter_code": None}), document.version)

    with caplog.at_level(logging.WARNING):
        SynchronousImportQueue().submit(dataclasses.replace(job, reader=_blank_rest_reader()))

    assert _status(setup, doc_id) == "review"
    assert [p.status for p in setup.store.list_pages(doc_id, PageExtraction)] == ["ok", "ok", "ok"]
    assert setup.store.list_chunks(SUBJECT) == []
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING and doc_id in r.getMessage()]
    assert any("DocumentChapterMissingError" in r.getMessage() for r in warnings)
    assert not any(r.levelno >= logging.ERROR for r in caplog.records)


def test_requeued_reading_settles_into_the_vault(setup: Setup) -> None:
    """Khởi động lại khi tài liệu còn `processing`: việc đọc xếp lại mang Vault, nên đọc xong mà mọi trang có văn
    bản đã xác nhận thì tài liệu thành `confirmed` và có chunk trong Vault (§5.7)."""
    doc_id, _ = _first_page_confirmed(setup, "xep-lai-khi-khoi-dong")
    assert _status(setup, doc_id) == "processing"
    runtime = setup.env.runtime
    queue = SynchronousImportQueue()

    queued = requeue_pending(
        setup.store, runtime.files, _blank_rest_reader(), queue, vault=setup.store, locks=runtime.locks
    )

    assert queued == [doc_id] and queue.submitted == [doc_id]
    assert _status(setup, doc_id) == "confirmed"
    chunks = setup.store.list_chunks(SUBJECT)
    assert chunks and all(chunk.doc_id == doc_id for chunk in chunks)
