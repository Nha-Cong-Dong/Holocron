"""Settle tài liệu nhập (SPECIFICATION §5.7, §6.0, §6.5; sửa review phần 2): đọc nền xong thì tài liệu đủ điều kiện
được nhập Vault và thành `confirmed`; nhập Vault và đặt `confirmed` không nguyên tử nên văn bản đóng khi Vault đã có
chunk; lỗi settle sau khi đã ghi trang không bị báo thành "version lệch"; bước kết thúc lần đọc đọc lại khi version
lệch.

Chạy trên cả ba loại kho, bọc trong `FlakyStore` để cài lỗi và thứ tự xen kẽ một cách tất định. Hàng đợi runtime giữ
việc (`HoldingQueue`) để test tự chạy từng lần đọc bằng hàng đợi đồng bộ. Văn bản là mẫu tự viết.
"""

import dataclasses
import hashlib
import logging
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest

from holocron_core.domain.documents import CrossCheck, PageExtraction
from holocron_core.domain.repositories import (
    AppStore,
    ImportedDocument,
    StorageBusyError,
    VersionConflictError,
)
from holocron_core.domain.schemas import KnowledgeChunk
from holocron_core.routers.api import app
from holocron_core.services import document_import
from holocron_core.services.document_import import (
    CHAPTER_MISSING_DETAIL,
    SETTLE_ATTEMPTS,
    SETTLE_PENDING_DETAIL,
    ReadJob,
    SynchronousImportQueue,
    confirmed_text,
    settle_document,
)
from holocron_core.services.vault_chunking import build_chunks
from tests.document_import_support import FakePdfReader, HoldingQueue, ImportEnv, install, ok_page, pdf_bytes
from tests.vault_support import STORE_KINDS, open_store

EDITOR = "local_user"
SUBJECT = "ET4010"


class FlakyStore:
    """Kho bọc: mọi lời gọi chuyển tới `inner`, trừ lỗi và thứ tự xen kẽ do test cài.

    - `before_update`: chạy MỘT lần ngay trước lần `update_imported_document` kế tiếp (giả lập một request khác ghi
      tài liệu chen vào giữa lúc đọc và lúc ghi).
    - `update_errors`: ném lần lượt ở các lần `update_imported_document` kế tiếp; `always_conflict` ném mãi.
    - `add_chunks_error`: nhập Vault ném lỗi này (không ghi gì, như kho thật khi lỗi).
    """

    def __init__(self, inner: AppStore) -> None:
        self.inner = inner
        self.before_update: Callable[[], None] | None = None
        self.update_errors: list[Exception] = []
        self.always_conflict = False
        self.add_chunks_error: Exception | None = None
        self.updates = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    def update_imported_document(self, document: ImportedDocument, expected_version: int) -> ImportedDocument:
        self.updates += 1
        hook, self.before_update = self.before_update, None
        if hook is not None:
            hook()
        if self.update_errors:
            raise self.update_errors.pop(0)
        if self.always_conflict:
            raise VersionConflictError("tài liệu", document.meta.doc_id, expected_version, expected_version + 1)
        return self.inner.update_imported_document(document, expected_version)

    def add_chunks(self, chunks: Sequence[KnowledgeChunk]) -> list[KnowledgeChunk]:
        if self.add_chunks_error is not None:
            raise self.add_chunks_error
        return self.inner.add_chunks(chunks)


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
    """Trang đọc xong mà không có chữ: không xác nhận được nên không chặn tài liệu thành `confirmed` (§5.7)."""
    return PageExtraction(
        document_id=document_id,
        page_number=page_number,
        status="ok",
        tier_used="text_layer",
        text_score=0.99,
        cross_check=CrossCheck(status="not_applicable", secondary_engine=None),
    )


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


def _post_held(setup: Setup, tag: str) -> tuple[str, ReadJob]:
    """Nhận tài liệu 3 trang; việc đọc bị giữ lại, trả id tài liệu và việc đọc mà router đã dựng."""
    content = pdf_bytes(tag)
    assert setup.env.post(content).status_code == 202
    [job] = setup.held.release()
    return "doc_" + hashlib.sha256(content).hexdigest()[:16], job


def _read(job: ReadJob, reader: FakePdfReader) -> None:
    SynchronousImportQueue().submit(dataclasses.replace(job, reader=reader))


def _first_page_read(setup: Setup, tag: str) -> tuple[str, ReadJob]:
    """Trang 1 đã đọc (`ok`), trang 2–3 còn `pending`, tài liệu `processing`."""
    doc_id, job = _post_held(setup, tag)
    _read(job, CrashAfterFirstPage(page_count=3))
    assert [p.status for p in setup.store.list_pages(doc_id, PageExtraction)] == ["ok", "pending", "pending"]
    return doc_id, job


def _fully_read(setup: Setup, tag: str, pages: dict[int, Callable[[str], PageExtraction]] | None = None) -> str:
    doc_id, job = _post_held(setup, tag)
    _read(job, FakePdfReader(page_count=3, custom_pages=dict(pages or {})))
    assert _document(setup, doc_id)["status"] == "review"
    return doc_id


def _page(setup: Setup, doc_id: str, number: int) -> PageExtraction:
    page = setup.store.get_page(doc_id, number, PageExtraction)
    assert page is not None
    return page


def _document(setup: Setup, doc_id: str) -> dict[str, Any]:
    response = setup.env.client.get(f"/v1/documents/{doc_id}")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _confirm_page(setup: Setup, doc_id: str, number: int) -> Any:
    version = _page(setup, doc_id, number).version
    return setup.env.client.post(
        f"/v1/documents/{doc_id}/pages/{number}/confirm", json={"editor_id": EDITOR, "expected_version": version}
    )


def _confirm_document(setup: Setup, doc_id: str) -> Any:
    return setup.env.client.post(f"/v1/documents/{doc_id}/confirm", json={"editor_id": EDITOR})


def _chunk_ids(setup: Setup, doc_id: str) -> list[str]:
    return sorted(chunk.chunk_id for chunk in setup.store.inner.list_chunks(SUBJECT) if chunk.doc_id == doc_id)


def _expected_chunk_ids(setup: Setup, doc_id: str) -> list[str]:
    """Kết quả cắt văn bản đã xác nhận MỘT lần bằng bộ cắt tất định (§12.1)."""
    document = setup.store.get_imported_document(doc_id)
    assert document is not None
    text = confirmed_text(document, setup.store.list_pages(doc_id, PageExtraction))
    assert text is not None
    ids = sorted(chunk.chunk_id for chunk in build_chunks(text))
    assert ids
    return ids


# --------------------------------------------------------------------------- #
# Lỗi 1: đọc xong thì settle khi điều kiện đã thoả


@pytest.mark.parametrize(
    ("rest", "statuses"),
    [
        ({"failed_pages": frozenset({2, 3})}, ["ok", "failed", "failed"]),
        ({"custom_pages": {2: lambda d: blank_page(d, 2), 3: lambda d: blank_page(d, 3)}}, ["ok", "ok", "ok"]),
    ],
    ids=["trang-con-lai-failed", "trang-con-lai-trang"],
)
def test_reading_end_settles_when_text_pages_were_confirmed_while_reading(
    setup: Setup, rest: dict[str, Any], statuses: list[str]
) -> None:
    doc_id, job = _first_page_read(setup, "xac-nhan-khi-dang-doc")
    assert job.vault is not None and job.locks is setup.env.runtime.locks, "router cấp Vault và khoá cho việc đọc"
    assert _confirm_page(setup, doc_id, 1).status_code == 200
    assert _document(setup, doc_id)["status"] == "processing"
    assert not setup.store.has_document_chunks(doc_id), "chưa đủ điều kiện thì chưa vào Vault"

    _read(job, FakePdfReader(page_count=3, **rest))

    document = _document(setup, doc_id)
    assert document["status"] == "confirmed"
    assert [page["status"] for page in document["pages"]] == statuses
    assert document["meta"]["has_text_layer"] is True
    assert _chunk_ids(setup, doc_id) == _expected_chunk_ids(setup, doc_id)


def test_reading_end_keeps_review_while_a_text_page_is_unconfirmed(setup: Setup) -> None:
    doc_id, job = _first_page_read(setup, "con-trang-chua-xac-nhan")
    assert _confirm_page(setup, doc_id, 1).status_code == 200
    _read(job, FakePdfReader(page_count=3))
    assert _document(setup, doc_id)["status"] == "review"
    assert not setup.store.has_document_chunks(doc_id)


def test_reading_job_without_vault_stops_at_review(setup: Setup) -> None:
    """Giả định đã ghi: việc đọc không có Vault (test, script) không settle; lần xác nhận kế tiếp hoàn tất."""
    doc_id, job = _first_page_read(setup, "khong-vault")
    assert _confirm_page(setup, doc_id, 1).status_code == 200
    _read(dataclasses.replace(job, vault=None), FakePdfReader(page_count=3, failed_pages=frozenset({2, 3})))
    assert _document(setup, doc_id)["status"] == "review"
    assert _confirm_page(setup, doc_id, 1).status_code == 200, "trang đã xác nhận: 200 và settle lại"
    assert _document(setup, doc_id)["status"] == "confirmed"


# --------------------------------------------------------------------------- #
# Lỗi 2: nhập Vault và đặt `confirmed` không nguyên tử


def test_ingest_error_leaves_the_document_unconfirmed_and_vault_empty(setup: Setup) -> None:
    doc_id = _fully_read(setup, "nhap-vault-loi", {3: lambda d: blank_page(d, 3)})
    assert _confirm_page(setup, doc_id, 1).status_code == 200
    setup.store.add_chunks_error = StorageBusyError("kho bận")
    response = _confirm_page(setup, doc_id, 2)
    assert response.status_code == 503, "§6.0: kho bận ⇒ 503, không phải 409 version lệch"
    assert _page(setup, doc_id, 2).confirmed, "trang đã ghi xác nhận trước khi settle"
    assert _document(setup, doc_id)["status"] == "review"
    assert not setup.store.has_document_chunks(doc_id)
    setup.store.add_chunks_error = None
    assert _confirm_document(setup, doc_id).json()["status"] == "confirmed"
    assert _chunk_ids(setup, doc_id) == _expected_chunk_ids(setup, doc_id)


def test_settle_retries_after_a_version_conflict(setup: Setup) -> None:
    doc_id = _fully_read(setup, "lech-mot-lan")
    setup.store.update_errors = [VersionConflictError("tài liệu", doc_id, 1, 2)]
    updates_before = setup.store.updates
    response = _confirm_document(setup, doc_id)
    assert response.status_code == 200 and response.json()["status"] == "confirmed"
    assert setup.store.updates - updates_before == 2, "một lần lệch, một lần ghi thành công"
    assert _chunk_ids(setup, doc_id) == _expected_chunk_ids(setup, doc_id)


def test_settle_raises_after_the_last_attempt(setup: Setup) -> None:
    doc_id = _fully_read(setup, "het-luot-dich-vu")
    for number in (1, 2, 3):
        page = _page(setup, doc_id, number)
        confirmed = page.model_copy(update={"confirmed": True, "version": page.version + 1})
        setup.store.update_page(
            doc_id, number, confirmed, status=page.status, confirmed=True, expected_version=page.version
        )
    setup.store.always_conflict = True
    updates_before = setup.store.updates
    with pytest.raises(VersionConflictError):
        settle_document(setup.store, setup.store, doc_id)
    assert setup.store.updates - updates_before == SETTLE_ATTEMPTS


def test_exhausted_settle_returns_200_then_text_is_frozen_until_settled(
    setup: Setup, caplog: pytest.LogCaptureFixture
) -> None:
    doc_id = _fully_read(setup, "het-luot", {3: lambda d: blank_page(d, 3)})
    setup.store.always_conflict = True
    with caplog.at_level(logging.WARNING, logger=document_import.__name__):
        response = _confirm_document(setup, doc_id)
    assert response.status_code == 200, "lỗi settle sau khi đã ghi trang không được báo thành version lệch"
    body = response.json()
    assert body["status"] == "review"
    assert [page["confirmed"] for page in body["pages"]] == [True, True, False]
    assert any("chưa đặt được confirmed" in record.getMessage() for record in caplog.records)
    expected = _expected_chunk_ids(setup, doc_id)
    assert _chunk_ids(setup, doc_id) == expected, "Vault đã nhập xong trước bước lỗi"

    blank = _page(setup, doc_id, 3)
    patch = {"editor_id": EDITOR, "expected_version": blank.version, "edits": [], "appended_lines": ["Dòng gõ thêm"]}
    edited = setup.env.client.patch(f"/v1/documents/{doc_id}/pages/3/lines", json=patch)
    assert edited.status_code == 409 and edited.json()["detail"] == SETTLE_PENDING_DETAIL
    assert _page(setup, doc_id, 3) == blank, "văn bản đã đóng: trang trắng không đổi"

    setup.store.always_conflict = False
    again = _confirm_document(setup, doc_id)
    assert again.status_code == 200 and again.json()["status"] == "confirmed"
    assert _chunk_ids(setup, doc_id) == expected, "settle lại không thêm chunk nào"


def test_exhausted_settle_after_page_confirm_returns_the_confirmed_page(setup: Setup) -> None:
    doc_id = _fully_read(setup, "het-luot-trang", {2: lambda d: blank_page(d, 2), 3: lambda d: blank_page(d, 3)})
    setup.store.always_conflict = True
    response = _confirm_page(setup, doc_id, 1)
    assert response.status_code == 200 and response.json()["confirmed"] is True
    assert _document(setup, doc_id)["status"] == "review"
    setup.store.always_conflict = False
    assert _confirm_page(setup, doc_id, 1).status_code == 200
    assert _document(setup, doc_id)["status"] == "confirmed"


# --------------------------------------------------------------------------- #
# Lỗi 3: kiểm `chapter_code` TRƯỚC khi ghi xác nhận trang


def _drop_chapter(setup: Setup, doc_id: str) -> None:
    """Giả lập tài liệu nhập trước migration 0002 (chưa có `chapter_code`)."""
    document = setup.store.get_imported_document(doc_id)
    assert document is not None
    setup.store.inner.update_imported_document(document.model_copy(update={"chapter_code": None}), document.version)
    stored = setup.store.get_imported_document(doc_id)
    assert stored is not None and stored.chapter_code is None


def test_missing_chapter_is_rejected_before_any_page_is_confirmed(setup: Setup) -> None:
    doc_id = _fully_read(setup, "thieu-chuong")
    _drop_chapter(setup, doc_id)
    page = _confirm_page(setup, doc_id, 1)
    assert page.status_code == 409 and page.json()["detail"] == CHAPTER_MISSING_DETAIL
    whole = _confirm_document(setup, doc_id)
    assert whole.status_code == 409 and whole.json()["detail"] == CHAPTER_MISSING_DETAIL
    assert not any(p.confirmed for p in setup.store.list_pages(doc_id, PageExtraction)), "không trang nào bị ghi"
    assert not setup.store.has_document_chunks(doc_id)


# --------------------------------------------------------------------------- #
# Lỗi 4: bước kết thúc lần đọc gặp version lệch


def test_reading_end_rereads_after_a_concurrent_settle(setup: Setup, caplog: pytest.LogCaptureFixture) -> None:
    """Thứ tự xen kẽ: trang 2–3 đã ghi; ngay trước khi luồng đọc ghi `review`, một lần xác nhận settle tài liệu
    (`confirmed`, version + 1). Luồng đọc PHẢI đọc lại, giữ `confirmed` và vẫn ghi `has_text_layer`."""
    doc_id, job = _first_page_read(setup, "xen-ke")
    assert _confirm_page(setup, doc_id, 1).status_code == 200
    setup.store.before_update = lambda: _settle_inner(setup, doc_id)
    caplog.clear()
    with caplog.at_level(logging.INFO, logger=document_import.__name__):
        _read(job, FakePdfReader(page_count=3, failed_pages=frozenset({2, 3})))
    document = setup.store.get_imported_document(doc_id)
    assert document is not None
    assert document.status == "confirmed" and document.meta.has_text_layer is True
    assert any("đọc lại (1/" in record.getMessage() for record in caplog.records)
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING], "không có cảnh báo nào sai thực tế"
    assert _chunk_ids(setup, doc_id) == _expected_chunk_ids(setup, doc_id)


def _settle_inner(setup: Setup, doc_id: str) -> None:
    settled = settle_document(setup.store.inner, setup.store.inner, doc_id)
    assert settled.status == "confirmed"


def test_reading_end_gives_up_with_an_accurate_log(setup: Setup, caplog: pytest.LogCaptureFixture) -> None:
    doc_id, job = _first_page_read(setup, "xen-ke-het-luot")
    caplog.clear()
    setup.store.always_conflict = True
    with caplog.at_level(logging.INFO, logger=document_import.__name__):
        _read(job, FakePdfReader(page_count=3))
    messages = [record.getMessage() for record in caplog.records]
    assert any("chưa ghi được trạng thái cuối" in message for message in messages)
    assert not any("trang chưa ghi giữ pending" in message for message in messages), "mọi trang đã ghi"
    assert [p.status for p in setup.store.list_pages(doc_id, PageExtraction)] == ["ok", "ok", "ok"]
    document = setup.store.get_imported_document(doc_id)
    assert document is not None and document.status == "processing"
