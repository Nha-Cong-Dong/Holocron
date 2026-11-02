"""Bổ sung test cho các đột biến còn sống của bộ nhập tài liệu, đợt 2 (SPECIFICATION §5.7, §6.5): định nghĩa "dòng
có văn bản" khi xác nhận, settle lặp lại trên tài liệu đã `confirmed`, trang `failed` lúc đọc không bị đổi lý do, trang
`failed` không chặn tài liệu (I5: chỉ văn bản đã xác nhận vào Vault), Content-Type không phân biệt hoa thường, số lần
đọc lại khi ghi trạng thái cuối và thứ tự xếp lại khi khởi động.

Chạy trên cả ba loại kho (`tests/vault_support.py`), bọc trong `FlakyStore` để cài lỗi version một cách tất định.
Hàng đợi runtime giữ việc (`HoldingQueue`) để test tự chạy từng lần đọc. Văn bản là mẫu tự viết.
"""

import dataclasses
import hashlib
import logging
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from holocron_core.domain.documents import CrossCheck, ExtractedLine, PageExtraction
from holocron_core.domain.repositories import AppStore, ImportedDocument, VersionConflictError
from holocron_core.domain.vault import MAX_PAGE_LINES
from holocron_core.infrastructure.document_readers.tiers import failed_page
from holocron_core.routers.api import app
from holocron_core.services import document_import
from holocron_core.services.document_import import (
    SETTLE_ATTEMPTS,
    ReadJob,
    SynchronousImportQueue,
    requeue_pending,
)
from tests.document_import_support import FakePdfReader, HoldingQueue, ImportEnv, install, pdf_bytes
from tests.vault_support import STORE_KINDS, open_store

EDITOR = "local_user"
SUBJECT = "ET4010"
OVERSIZE_LINES = MAX_PAGE_LINES + 1


class FlakyStore:
    """Kho bọc: mọi lời gọi chuyển tới `inner`; `update_errors` ném lần lượt ở các lần `update_imported_document`
    kế tiếp (giả lập request khác ghi tài liệu chen vào)."""

    def __init__(self, inner: AppStore) -> None:
        self.inner = inner
        self.update_errors: list[Exception] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    def update_imported_document(self, document: ImportedDocument, expected_version: int) -> ImportedDocument:
        if self.update_errors:
            raise self.update_errors.pop(0)
        return self.inner.update_imported_document(document, expected_version)


@dataclasses.dataclass
class Setup:
    env: ImportEnv
    store: FlakyStore
    held: HoldingQueue


@pytest.fixture(params=STORE_KINDS)
def setup(request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Setup]:
    for inner in open_store(str(request.param), tmp_path, monkeypatch):
        store = FlakyStore(inner)
        held = HoldingQueue()
        env = install(
            store, tmp_path / "files", SynchronousImportQueue(), FakePdfReader(page_count=3), runtime_queue=held
        )
        yield Setup(env=env, store=store, held=held)
        app.dependency_overrides.clear()


def _line(index: int, text: str) -> ExtractedLine:
    return ExtractedLine(line_index=index, text=text, bbox=None, conf=0.95, engine="text_layer")


def _text_page(document_id: str, page_number: int, texts: list[str]) -> PageExtraction:
    return PageExtraction(
        document_id=document_id,
        page_number=page_number,
        status="ok",
        tier_used="text_layer",
        text_score=0.95,
        lines=[_line(index, text) for index, text in enumerate(texts)],
        cross_check=CrossCheck(status="not_applicable", secondary_engine=None),
    )


def blank_page(document_id: str, page_number: int) -> PageExtraction:
    """Trang đọc xong mà không có dòng nào: không chặn tài liệu thành `confirmed` (§5.7)."""
    return _text_page(document_id, page_number, [])


def whitespace_page(document_id: str, page_number: int) -> PageExtraction:
    """Trang `ok` chỉ có dòng toàn khoảng trắng: không có văn bản (§5.7, §6.5: `text` sau khi bỏ khoảng trắng đầu/cuối
    rỗng), không xác nhận được.

    `StrictModel` (`str_strip_whitespace`) đã cắt khoảng trắng Unicode (`White_Space`) khi dựng dòng, nên dòng còn
    lại tới service chỉ gồm ký tự phân tách U+001C–U+001F: Python coi là khoảng trắng (`str.isspace`) nhưng pydantic
    không cắt. Đó là trường hợp `_has_text` phải tự `strip()`.
    """
    page = _text_page(document_id, page_number, ["   ", "\x1c\x1d", "\x1e \x1f"])
    assert [line.text for line in page.lines] == ["", "\x1c\x1d", "\x1e \x1f"], (
        "tiền đề: pydantic không cắt U+001C–U+001F"
    )
    return page


def _post_held(setup: Setup, tag: str) -> tuple[str, ReadJob]:
    content = pdf_bytes(tag)
    response = setup.env.post(content)
    assert response.status_code == 202, response.text
    [job] = setup.held.release()
    return "doc_" + hashlib.sha256(content).hexdigest()[:16], job


def _read(job: ReadJob, reader: FakePdfReader) -> None:
    SynchronousImportQueue().submit(dataclasses.replace(job, reader=reader))


def _fully_read(setup: Setup, tag: str, reader: FakePdfReader) -> str:
    doc_id, job = _post_held(setup, tag)
    _read(job, reader)
    assert _stored_document(setup, doc_id).status == "review"
    return doc_id


def _stored_document(setup: Setup, doc_id: str) -> ImportedDocument:
    document = setup.store.get_imported_document(doc_id)
    assert document is not None
    return document


def _page(setup: Setup, doc_id: str, number: int) -> PageExtraction:
    page = setup.store.get_page(doc_id, number, PageExtraction)
    assert page is not None
    return page


def _confirm_page(setup: Setup, doc_id: str, number: int) -> Any:
    version = _page(setup, doc_id, number).version
    return setup.env.client.post(
        f"/v1/documents/{doc_id}/pages/{number}/confirm", json={"editor_id": EDITOR, "expected_version": version}
    )


def _confirm_document(setup: Setup, doc_id: str) -> Any:
    return setup.env.client.post(f"/v1/documents/{doc_id}/confirm", json={"editor_id": EDITOR})


def _chunks(setup: Setup, doc_id: str) -> list[Any]:
    return sorted(
        (chunk for chunk in setup.store.inner.list_chunks(SUBJECT) if chunk.doc_id == doc_id),
        key=lambda chunk: chunk.chunk_id,
    )


def _pages(pages: dict[int, Callable[[str], PageExtraction]], **kwargs: Any) -> FakePdfReader:
    return FakePdfReader(page_count=3, custom_pages=pages, **kwargs)


# --------------------------------------------------------------------------- #
# 1. Dòng chỉ có khoảng trắng không phải văn bản


def test_whitespace_only_page_is_not_confirmable_and_does_not_block_the_document(setup: Setup) -> None:
    doc_id = _fully_read(
        setup,
        "trang-toan-khoang-trang",
        _pages({2: lambda d: whitespace_page(d, 2), 3: lambda d: blank_page(d, 3)}),
    )
    before = _page(setup, doc_id, 2)
    assert before.status == "ok" and before.lines

    response = _confirm_page(setup, doc_id, 2)
    assert response.status_code == 409, response.text
    assert _page(setup, doc_id, 2) == before, "không ghi xác nhận trang nào"

    response = _confirm_document(setup, doc_id)
    assert response.status_code == 200, response.text
    body = response.json()
    assert [page["confirmed"] for page in body["pages"]] == [True, False, False]
    assert body["status"] == "confirmed"
    assert _stored_document(setup, doc_id).status == "confirmed"


# --------------------------------------------------------------------------- #
# 2. Xác nhận lặp lại trên tài liệu đã `confirmed` không ghi gì


def test_repeated_confirm_on_a_confirmed_document_writes_nothing(setup: Setup) -> None:
    doc_id = _fully_read(
        setup, "xac-nhan-lap-lai", _pages({2: lambda d: blank_page(d, 2), 3: lambda d: blank_page(d, 3)})
    )
    assert _confirm_page(setup, doc_id, 1).status_code == 200
    settled = _stored_document(setup, doc_id)
    assert settled.status == "confirmed"
    chunks = _chunks(setup, doc_id)
    assert chunks

    assert _confirm_page(setup, doc_id, 1).status_code == 200
    assert _confirm_document(setup, doc_id).status_code == 200

    again = _stored_document(setup, doc_id)
    assert again.status == "confirmed"
    assert again.version == settled.version, "settle trên tài liệu đã confirmed không ghi lại tài liệu"
    assert again == settled
    assert _chunks(setup, doc_id) == chunks


# --------------------------------------------------------------------------- #
# 3. Trang đã `failed` lúc đọc giữ lý do của bộ đọc, kể cả khi mang nhiều dòng


def timeout_page_with_lines(document_id: str, page_number: int) -> PageExtraction:
    """Trang `failed` (`PAGE_TIMEOUT`) mang hơn `MAX_PAGE_LINES` dòng có chữ (`failed_page` thường không mang dòng)."""
    lines = [_line(index, f"Dòng {index} của trang {page_number}.") for index in range(OVERSIZE_LINES)]
    return failed_page(document_id, page_number, "PAGE_TIMEOUT").model_copy(update={"lines": lines})


def test_failed_page_keeps_its_reason_even_with_too_many_lines(setup: Setup) -> None:
    doc_id = _fully_read(setup, "trang-timeout-nhieu-dong", _pages({1: lambda d: timeout_page_with_lines(d, 1)}))
    page = _page(setup, doc_id, 1)
    assert page.status == "failed"
    assert page.failure_reason == "PAGE_TIMEOUT"


# --------------------------------------------------------------------------- #
# 4. Trang `failed` có dòng chữ không chặn tài liệu


def test_failed_page_with_text_does_not_block_the_document(setup: Setup) -> None:
    doc_id = _fully_read(
        setup, "trang-failed-co-chu", _pages({3: lambda d: blank_page(d, 3)}, failed_pages=frozenset({2}))
    )
    page = _page(setup, doc_id, 2)
    assert page.status == "failed"
    forced = page.model_copy(
        update={"lines": [_line(0, "Văn bản còn sót trên trang lỗi.")], "version": page.version + 1}
    )
    setup.store.inner.update_page(doc_id, 2, forced, status="failed", confirmed=False, expected_version=page.version)
    assert _page(setup, doc_id, 2).lines, "trang failed mang dòng chữ trong kho"

    response = _confirm_page(setup, doc_id, 1)
    assert response.status_code == 200, response.text

    assert _stored_document(setup, doc_id).status == "confirmed"
    assert _chunks(setup, doc_id)
    assert all("sót trên trang lỗi" not in chunk.raw_text for chunk in _chunks(setup, doc_id)), (
        "I5: chỉ văn bản đã xác nhận"
    )


# --------------------------------------------------------------------------- #
# 5. Content-Type không phân biệt hoa thường


@pytest.mark.parametrize("content_type", ["Application/PDF", "APPLICATION/PDF; charset=binary"])
def test_content_type_is_case_insensitive(setup: Setup, content_type: str) -> None:
    content = pdf_bytes(f"hoa-thuong-{content_type}")
    response = setup.env.post(content, content_type=content_type)
    assert response.status_code == 202, response.text


# --------------------------------------------------------------------------- #
# 6. Ghi trạng thái cuối: lượt cuối cùng vẫn được thử


def test_reading_end_succeeds_on_the_last_attempt(setup: Setup, caplog: pytest.LogCaptureFixture) -> None:
    doc_id, job = _post_held(setup, "luot-cuoi-thanh-cong")
    setup.store.update_errors = [VersionConflictError("tài liệu", doc_id, 1, 2) for _ in range(SETTLE_ATTEMPTS - 1)]
    caplog.clear()
    with caplog.at_level(logging.INFO, logger=document_import.__name__):
        _read(job, FakePdfReader(page_count=3))
    assert setup.store.update_errors == [], "đã dùng hết lỗi cài sẵn"
    assert _stored_document(setup, doc_id).status == "review"
    messages = [record.getMessage() for record in caplog.records]
    assert not any("chưa ghi được trạng thái cuối" in message for message in messages)
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


# --------------------------------------------------------------------------- #
# 7. Xếp lại khi khởi động: cũ trước


def test_requeue_submits_older_documents_first(setup: Setup) -> None:
    older, _ = _post_held(setup, "xep-lai-cu")
    newer, _ = _post_held(setup, "xep-lai-moi")
    assert _stored_document(setup, older).meta.uploaded_at < _stored_document(setup, newer).meta.uploaded_at
    assert _stored_document(setup, older).status == _stored_document(setup, newer).status == "processing"

    queue = HoldingQueue()
    queued = requeue_pending(setup.store.inner, setup.env.runtime.files, FakePdfReader(page_count=3), queue)

    assert queued == [older, newer]
    assert [job.document_id for job in queue.held] == [older, newer]
