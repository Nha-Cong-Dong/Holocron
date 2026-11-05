"""X09 (ADR-010 mục 13, Q1): lỗi DỮ LIỆU khi lưu khối, khi ghi lại `page_drop_reason`, khi dựng cây mục của tài liệu
đã đóng và khi xếp lại lúc khởi động. Không lỗi nào được để tài liệu kẹt `processing` mà không lý do, hay làm hỏng lần
khởi động (`requeue_pending`) của các tài liệu khác."""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

import pytest

from holocron_core.domain.documents import DocumentConfirmRequest, PageExtraction
from holocron_core.domain.repositories import AppStore, DocumentLayout, StorageBusyError
from holocron_core.infrastructure.document_readers.pdf_reader import GeometrySink
from holocron_core.infrastructure.memory_store import IngestionStore
from holocron_core.services import document_import
from holocron_core.services.block_pipeline import default_layout_config
from holocron_core.services.document_import import (
    ImportRuntime,
    RetryPolicy,
    SynchronousImportQueue,
    confirm_document,
    requeue_pending,
)
from tests.import_blocks_support import (
    LANG,
    NOW,
    THREE,
    GeoReader,
    document_of,
    finish_again,
    import_doc,
    pages_of,
    runtime_for,
    store,
)

__all__ = ["store"]

FAST = RetryPolicy(attempts=3, delay_s=0.0)
FOREIGN = "khối hoặc ledger không thuộc tài liệu (test)"


class RejectingLayoutStore(IngestionStore):
    """Kho in-memory mà `save_document_layout` ném `make_error()` cho `failures` lần gọi đầu."""

    def __init__(self, make_error: Callable[[], Exception], failures: int = 0) -> None:
        super().__init__()
        self.make_error = make_error
        self.failures = failures
        self.calls = 0

    def save_document_layout(self, document_id: str, layout: DocumentLayout) -> None:
        self.calls += 1
        if self.failures > 0:
            self.failures -= 1
            raise self.make_error()
        super().save_document_layout(document_id, layout)


def foreign() -> Exception:
    return ValueError(FOREIGN)


def busy() -> Exception:
    return StorageBusyError("kho bận (test)")


@dataclass
class HalfReader(GeoReader):
    """`GeoReader` ném `ValueError` sau `break_after` trang (worker chết giữa chừng)."""

    break_after: int | None = None

    def read_pages(
        self,
        path: Path,
        document_id: str,
        page_numbers: Sequence[int] | None = None,
        *,
        geometry_sink: GeometrySink | None = None,
    ) -> Iterator[PageExtraction]:
        for count, page in enumerate(super().read_pages(path, document_id, page_numbers, geometry_sink=geometry_sink)):
            if self.break_after is not None and count >= self.break_after:
                raise ValueError("worker chết (test)")
            yield page


def fast_runtime(tmp_path: Path) -> ImportRuntime:
    return dataclasses.replace(runtime_for(tmp_path), retry=FAST)


def half_read(store: AppStore, runtime: ImportRuntime, tag: str) -> str:
    doc_id = import_doc(store, runtime, HalfReader(THREE, break_after=1), tag=tag, auto_confirm=False)
    assert document_of(store, doc_id).status == "processing"
    assert [page.status for page in pages_of(store, doc_id)] == ["ok", "pending", "pending"]
    return doc_id


def assert_layout_failed_review(store: AppStore, doc_id: str) -> None:
    """Mọi trang lớp chữ mang `LAYOUT_FAILED` (lý do hiển thị), không tự xác nhận; tài liệu chốt `review`."""
    assert document_of(store, doc_id).status == "review"
    for page in pages_of(store, doc_id):
        assert "LAYOUT_FAILED" in page.review_reasons and page.status == "needs_review"
        assert not page.confirmed and page.confirmed_by is None


def has_error(caplog: pytest.LogCaptureFixture, text: str) -> bool:
    return any(record.levelno >= logging.ERROR and text in record.getMessage() for record in caplog.records)


# --------------------------------------------------------------------------- #
# Lưu khối lần đầu: kho từ chối kế hoạch vì lỗi dữ liệu
# --------------------------------------------------------------------------- #


def test_block_save_value_error_stores_failed_layout(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Kho ném `ValueError` khi lưu kế hoạch khối ⇒ lưu `plan_failed_layout`: không khối, `layout_version` vẫn ghi, mọi
    trang lớp chữ `LAYOUT_FAILED`, tài liệu không kẹt `processing`."""
    store = RejectingLayoutStore(foreign, failures=1)
    with caplog.at_level(logging.ERROR):
        doc_id = import_doc(store, fast_runtime(tmp_path), GeoReader(THREE))
    assert store.calls == 2
    assert document_of(store, doc_id).layout_version is not None
    assert store.list_blocks(doc_id) == [] and store.list_extraction_ledgers(doc_id) == []
    assert_layout_failed_review(store, doc_id)
    assert has_error(caplog, "kho từ chối khối")


def test_block_save_value_error_every_time_flags_pages(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Kho từ chối cả kế hoạch `LAYOUT_FAILED` ⇒ lý do ghi theo từng trang (`update_page`): tài liệu chốt `review`, không
    tự xác nhận, không khối; không kẹt `processing`."""
    store = RejectingLayoutStore(foreign, failures=1000)
    with caplog.at_level(logging.ERROR):
        doc_id = import_doc(store, fast_runtime(tmp_path), GeoReader(THREE))
    assert store.calls == 2
    assert store.list_blocks(doc_id) == []
    assert_layout_failed_review(store, doc_id)
    assert has_error(caplog, "ghi lý do theo từng trang")


# --------------------------------------------------------------------------- #
# Ghi lại `page_drop_reason` (tài liệu chưa đóng, đã có khối lưu)
# --------------------------------------------------------------------------- #


def test_refresh_drop_reasons_value_error_flags_pages_keeps_blocks(
    store: AppStore, tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc_id = import_doc(store, runtime_for(tmp_path), GeoReader(THREE), auto_confirm=False)
    before = document_of(store, doc_id)
    blocks = store.list_blocks(doc_id)
    ledgers = store.list_extraction_ledgers(doc_id)
    assert blocks and before.layout_version is not None

    def broken(*_: object, **__: object) -> None:
        raise ValueError(FOREIGN)

    monkeypatch.setattr(document_import, "refresh_drop_reasons", broken)
    with caplog.at_level(logging.ERROR):
        finish_again(store, doc_id, GeoReader(THREE), default_layout_config(LANG))
    after = document_of(store, doc_id)
    assert after.layout_version == before.layout_version
    assert after.layout_settings_sha256 == before.layout_settings_sha256
    assert after.blocks_after_close == before.blocks_after_close
    assert store.list_blocks(doc_id) == blocks and store.list_extraction_ledgers(doc_id) == ledgers
    assert_layout_failed_review(store, doc_id)
    assert has_error(caplog, "ghi lại lý do bỏ trang")


# --------------------------------------------------------------------------- #
# Tài liệu đã đóng: dựng cây mục lỗi chỉ ghi lỗi (MINOR vòng 2)
# --------------------------------------------------------------------------- #


def test_closed_document_section_tree_error_only_logs(
    store: AppStore, tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = runtime_for(tmp_path)
    doc_id = import_doc(store, runtime, GeoReader(THREE), auto_confirm=False)
    confirm_document(store, store, runtime, doc_id, DocumentConfirmRequest(editor_id="u"), now=NOW)
    assert document_of(store, doc_id).status == "confirmed"
    blocks = store.list_blocks(doc_id)
    sections = store.list_sections(doc_id)
    pages = pages_of(store, doc_id)

    def broken(*_: object, **__: object) -> None:
        raise ValueError("cây mục hỏng (test)")

    monkeypatch.setattr(document_import, "_section_tree", broken)
    with caplog.at_level(logging.ERROR):
        finish_again(store, doc_id, GeoReader(THREE), default_layout_config(LANG))
    assert document_of(store, doc_id).status == "confirmed"
    assert store.list_blocks(doc_id) == blocks and store.list_sections(doc_id) == sections
    assert pages_of(store, doc_id) == pages
    assert has_error(caplog, "dựng cây mục lỗi bất ngờ")


# --------------------------------------------------------------------------- #
# Xếp lại khi khởi động
# --------------------------------------------------------------------------- #


def test_requeue_missing_file_value_error_on_save_marks_failed_with_reason(tmp_path: Path) -> None:
    """Đường mất file gốc, kho từ chối khối vì lỗi dữ liệu ⇒ `requeue_pending` không ném; tài liệu `failed`, trang lớp
    chữ mang `LAYOUT_FAILED`, trang chưa đọc `failed`."""
    store = RejectingLayoutStore(foreign)
    runtime = fast_runtime(tmp_path)
    doc_id = half_read(store, runtime, "a")
    assert runtime.files.delete(document_of(store, doc_id).meta.sha256)
    store.failures = 1000
    reader = GeoReader(THREE)
    queued = requeue_pending(
        store, runtime.files, reader, SynchronousImportQueue(), FAST, vault=store, geometry_reader=reader
    )
    assert queued == []
    assert document_of(store, doc_id).status == "failed"
    pages = pages_of(store, doc_id)
    assert "LAYOUT_FAILED" in pages[0].review_reasons and not pages[0].confirmed
    assert [page.status for page in pages[1:]] == ["failed", "failed"]


def test_requeue_unexpected_error_on_one_document_requeues_others(
    store: AppStore, tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`mark_document_failed` ném lỗi không phải lỗi kho cho tài liệu mất file gốc ⇒ lần khởi động không chết: tài liệu
    đó vẫn thành `failed` (trang chưa đọc `PAGE_ERROR`), tài liệu còn lại vẫn được xếp lại và đọc xong."""
    runtime = fast_runtime(tmp_path)
    lost = half_read(store, runtime, "a")
    kept = half_read(store, runtime, "b")
    assert runtime.files.delete(document_of(store, lost).meta.sha256)

    def broken(*_: object, **__: object) -> None:
        raise ValueError(FOREIGN)

    monkeypatch.setattr(document_import, "mark_document_failed", broken)
    reader = GeoReader(THREE)
    with caplog.at_level(logging.ERROR):
        queued = requeue_pending(
            store, runtime.files, reader, SynchronousImportQueue(), FAST, vault=store, geometry_reader=reader
        )
    assert queued == [kept]
    assert document_of(store, lost).status == "failed"
    lost_pages = pages_of(store, lost)
    assert [page.status for page in lost_pages[1:]] == ["failed", "failed"]
    assert all(page.failure_reason == "PAGE_ERROR" for page in lost_pages[1:])
    assert document_of(store, kept).status == "review"
    assert {block.page_number for block in store.list_blocks(kept)} == {1, 2, 3}
    assert has_error(caplog, "lỗi khi đánh dấu tài liệu")


def test_requeue_queue_rejecting_one_job_continues(store: AppStore, tmp_path: Path) -> None:
    """Hàng đợi từ chối việc của một tài liệu ⇒ tài liệu đó giữ `processing`, tài liệu sau vẫn được xếp lại."""
    runtime = fast_runtime(tmp_path)
    first = half_read(store, runtime, "a")
    second = half_read(store, runtime, "b")

    class PickyQueue(SynchronousImportQueue):
        def submit(self, job: document_import.ReadJob) -> None:
            if job.document_id == first:
                raise RuntimeError("hàng đợi từ chối (test)")
            super().submit(job)

    reader = GeoReader(THREE)
    queued = requeue_pending(store, runtime.files, reader, PickyQueue(), FAST, vault=store, geometry_reader=reader)
    assert queued == [second]
    assert document_of(store, first).status == "processing"
    assert document_of(store, second).status == "review"


def test_requeue_finishes_processing_document_without_pending_pages(tmp_path: Path) -> None:
    """NIT vòng 2: tài liệu `processing` không còn trang `pending` (lưu khối hết lượt vì kho bận) được `requeue_pending`
    xếp lại và chỉ chạy bước kết thúc: lưu khối, tự xác nhận, chốt `review`."""
    store = RejectingLayoutStore(busy, failures=1000)
    runtime = fast_runtime(tmp_path)
    doc_id = import_doc(store, runtime, GeoReader(THREE))
    document = document_of(store, doc_id)
    assert document.status == "processing" and document.layout_version is None
    assert all(page.status != "pending" for page in pages_of(store, doc_id))

    store.failures = 0
    reader = GeoReader(THREE)
    queued = requeue_pending(
        store, runtime.files, reader, SynchronousImportQueue(), FAST, vault=store, geometry_reader=reader
    )
    assert queued == [doc_id]
    document = document_of(store, doc_id)
    assert document.status == "review" and document.layout_version is not None
    assert {block.page_number for block in store.list_blocks(doc_id)} == {1, 2, 3}
    assert all(page.confirmed_by == "auto" for page in pages_of(store, doc_id))
