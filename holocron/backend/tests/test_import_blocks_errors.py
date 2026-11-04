"""Lỗi và đường phụ của bước khối trong luồng nhập (X09; ADR-010 mục 13; SPECIFICATION §5.7, §5.15, §6.10).

- Kho bận/lệch version khi lưu khối ở `_finish`: thử lại; hết lượt ⇒ tài liệu giữ `processing`, KHÔNG tự xác nhận,
  KHÔNG chốt, đọc lại được ở lần khởi động sau (không âm thầm đi đường dòng; Q1).
- Bộ dựng/`page_numbers` ném lỗi dữ liệu ⇒ mọi trang lớp chữ `LAYOUT_FAILED`, không tự xác nhận, version/hash vẫn ghi.
- Đường `failed` và xếp lại khi khởi động mang bộ đọc hình học; `page_drop_reason` của ledger theo vai trò trang;
  dựng lại sau `PATCH …/lines` (thử lại, ảnh cắt, ledger); truy vấn cây/khối (`block_tree`) và route ảnh cắt.

Bộ đọc giả có hình học (`tests/import_blocks_support.py`); không mạng, không tài liệu thật.
"""

from __future__ import annotations

import dataclasses
import hashlib
import logging
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from holocron_core.domain.blocks import Block, CropImage, ExtractionLedger, Section
from holocron_core.domain.documents import (
    DocumentConfirmRequest,
    LineEdit,
    PageExtraction,
    PageLinesPatch,
    PageRef,
    PageRoleEvidence,
)
from holocron_core.domain.repositories import (
    AppStore,
    DocumentLayout,
    PageLayout,
    StorageBusyError,
    StorageError,
    VersionConflictError,
)
from holocron_core.infrastructure.document_readers.pdf_reader import GeometrySink
from holocron_core.infrastructure.memory_store import IngestionStore
from holocron_core.routers.api import app, get_store
from holocron_core.routers.blocks import DOCUMENT_NOT_FOUND_DETAIL
from holocron_core.services.block_pipeline import LayoutConfig, PageBlocks, PageContext, default_layout_config
from holocron_core.services.block_tree import _page_lists, _root, page_block_list, section_blocks
from holocron_core.services.document_import import (
    SETTLE_ATTEMPTS,
    ImportRuntime,
    ReadJob,
    RetryPolicy,
    SynchronousImportQueue,
    confirm_document,
    edit_page_lines,
    read_document,
    requeue_pending,
)
from holocron_core.services.document_layout import refresh_drop_reasons
from holocron_core.services.page_numbers import PageNumberStage
from tests.import_blocks_support import (
    LANG,
    NOW,
    THREE,
    TWO_PARAGRAPHS,
    EditStage,
    GeoReader,
    as_figure,
    as_table,
    document_of,
    finish_again,
    import_doc,
    layout_with,
    pages_of,
    runtime_for,
    store,
    text_page,
)

__all__ = ["store"]

FAST = RetryPolicy(attempts=3, delay_s=0.0)


# --------------------------------------------------------------------------- #
# Kho và bộ đọc giả
# --------------------------------------------------------------------------- #


class FlakyLayoutStore(IngestionStore):
    """Kho in-memory mà `save_document_layout` ném `make_error()` cho `failures` lần gọi đầu (trước đó chạy
    `on_fail(self)` nếu có, ví dụ để một trang khác đổi version như khi có lần ghi chen ngang)."""

    def __init__(self, make_error: Callable[[], StorageError], failures: int = 0) -> None:
        super().__init__()
        self.make_error = make_error
        self.failures = failures
        self.calls = 0
        self.on_fail: Callable[[FlakyLayoutStore], None] | None = None

    def save_document_layout(self, document_id: str, layout: DocumentLayout) -> None:
        self.calls += 1
        if self.failures > 0:
            self.failures -= 1
            if self.on_fail is not None:
                self.on_fail(self)
            raise self.make_error()
        super().save_document_layout(document_id, layout)


def busy() -> StorageError:
    return StorageBusyError("kho bận (test)")


def conflict() -> StorageError:
    return VersionConflictError("trang", "test", 1, 2)


@dataclass
class BreakingReader(GeoReader):
    """`GeoReader` ném `ValueError` khi mở (`fail_open`) hoặc sau `break_after` trang (worker chết giữa chừng)."""

    fail_open: bool = False
    break_after: int | None = None

    def read_pages(
        self,
        path: Path,
        document_id: str,
        page_numbers: Sequence[int] | None = None,
        *,
        geometry_sink: GeometrySink | None = None,
    ) -> Iterator[PageExtraction]:
        if self.fail_open:
            raise ValueError("không mở được (test)")
        for count, page in enumerate(super().read_pages(path, document_id, page_numbers, geometry_sink=geometry_sink)):
            if self.break_after is not None and count >= self.break_after:
                raise ValueError("worker chết (test)")
            yield page


class BrokenPageNumbers(PageNumberStage):
    """`page_numbers` cấp tài liệu ném lỗi dữ liệu: cả kế hoạch lưu khối hỏng (không riêng một trang)."""

    def __init__(self, error: type[Exception]) -> None:
        super().__init__(LANG)
        self.error = error

    def run(self, pages: list[PageBlocks]) -> list[PageBlocks]:
        raise self.error("page_numbers hỏng (test)")


@dataclass(frozen=True)
class ShiftFigure:
    """Stage dời hộp của khối `figure` trên trang 1 (giữ id): giả lập khối cùng id mà khác hộp."""

    name: str = "test_shift_figure"

    def fingerprint(self) -> dict[str, Any]:
        return {"name": self.name, "version": 1, "settings": {}}

    def run(self, context: PageContext, blocks: list[Block]) -> list[Block]:
        if context.page_number != 1:
            return blocks
        out: list[Block] = []
        for block in blocks:
            if block.type == "figure" and block.bbox is not None:
                x0, y0, x1, y1 = block.bbox
                block = Block.model_validate({**block.model_dump(), "bbox": (x0, y0 / 2, x1, y1)})
            out.append(block)
        return out


def fast_runtime(tmp_path: Path, layout: LayoutConfig | None = None) -> ImportRuntime:
    return dataclasses.replace(runtime_for(tmp_path, layout), retry=FAST)


def ledger_of(store: AppStore, document_id: str, number: int) -> ExtractionLedger:
    [ledger] = [item for item in store.list_extraction_ledgers(document_id) if item.page_number == number]
    return ledger


def drop_page(store: AppStore, document_id: str, number: int, related: int) -> None:
    """Bỏ mềm trang qua `update_page` (KHÔNG ghi ledger): ledger lệch như khi chết giữa hai lần ghi."""
    page = pages_of(store, document_id)[number - 1]
    evidence = PageRoleEvidence(
        rule="cover_or_course_nav", related_pages=[PageRef(document_id=document_id, page_number=related)]
    )
    dropped = page.model_copy(
        update={
            "page_role": "cover_or_course_nav",
            "role_evidence": evidence,
            "dropped": True,
            "version": page.version + 1,
        }
    )
    store.update_page(
        document_id, number, dropped, status=dropped.status, confirmed=False, expected_version=page.version
    )


def patch_line(
    store: AppStore, runtime: ImportRuntime, document_id: str, number: int, index: int, text: str
) -> PageExtraction:
    page = pages_of(store, document_id)[number - 1]
    body = PageLinesPatch(editor_id="u", expected_version=page.version, edits=[LineEdit(line_index=index, text=text)])
    return edit_page_lines(store, store, runtime, document_id, number, body, now=NOW)


# --------------------------------------------------------------------------- #
# MAJOR: lỗi kho khi lưu khối ở `_finish`
# --------------------------------------------------------------------------- #


def test_busy_store_on_block_save_retries_then_stores(tmp_path: Path) -> None:
    store = FlakyLayoutStore(busy, failures=FAST.attempts - 1)
    doc_id = import_doc(store, fast_runtime(tmp_path), GeoReader(THREE))
    assert store.calls == FAST.attempts
    document = document_of(store, doc_id)
    assert document.status == "review" and document.layout_version is not None
    assert store.list_blocks(doc_id)
    assert all(page.confirmed_by == "auto" for page in pages_of(store, doc_id))


@pytest.mark.parametrize(("make_error", "attempts"), [(busy, FAST.attempts), (conflict, SETTLE_ATTEMPTS)])
def test_block_save_failure_keeps_document_rereadable(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, make_error: Callable[[], StorageError], attempts: int
) -> None:
    """Kho bận hết lượt / version lệch hết lượt khi lưu khối: KHÔNG tự xác nhận, KHÔNG chốt (`processing`), không khối
    nào, có cảnh báo; lần khởi động sau (`_finish` lại) lưu khối, tự xác nhận và chốt `review`."""
    store = FlakyLayoutStore(make_error, failures=1000)
    reader = GeoReader(THREE)
    with caplog.at_level(logging.WARNING):
        doc_id = import_doc(store, fast_runtime(tmp_path), reader)
    assert store.calls == attempts
    document = document_of(store, doc_id)
    assert document.status == "processing" and document.layout_version is None
    assert store.list_blocks(doc_id) == []
    pages = pages_of(store, doc_id)
    assert all(page.status != "pending" and not page.confirmed for page in pages)
    assert any("tài liệu giữ processing" in record.getMessage() for record in caplog.records)

    store.failures = 0
    finish_again(store, doc_id, reader, default_layout_config(LANG))
    document = document_of(store, doc_id)
    assert document.status == "review" and document.layout_version is not None
    assert all(page.confirmed_by == "auto" for page in pages_of(store, doc_id))


def test_version_conflict_on_block_save_retries_then_stores(tmp_path: Path) -> None:
    store = FlakyLayoutStore(conflict, failures=SETTLE_ATTEMPTS - 1)
    doc_id = import_doc(store, fast_runtime(tmp_path), GeoReader(THREE))
    assert store.calls == SETTLE_ATTEMPTS
    assert document_of(store, doc_id).layout_version is not None and store.list_blocks(doc_id)


@pytest.mark.parametrize("error", [ValueError, RuntimeError])
def test_layout_data_error_marks_text_pages_layout_failed(
    store: AppStore, tmp_path: Path, caplog: pytest.LogCaptureFixture, error: type[Exception]
) -> None:
    """Q1/I5: `page_numbers` ném lỗi dữ liệu ⇒ mọi trang lớp chữ `LAYOUT_FAILED` (lý do hiển thị), không khối, không tự
    xác nhận; `layout_version`/hash vẫn ghi; ghi log lỗi; tài liệu vẫn chốt `review` để người dùng xem lại."""
    layout = dataclasses.replace(default_layout_config(LANG), page_numbers=BrokenPageNumbers(error))
    with caplog.at_level(logging.ERROR):
        doc_id = import_doc(store, runtime_for(tmp_path, layout), GeoReader(THREE))
    document = document_of(store, doc_id)
    assert document.status == "review"
    assert document.layout_version is not None and document.layout_settings_sha256 == layout.settings_sha256()
    assert store.list_blocks(doc_id) == [] and store.list_extraction_ledgers(doc_id) == []
    for page in pages_of(store, doc_id):
        assert page.review_reasons == ["LAYOUT_FAILED"] and page.status == "needs_review"
        assert not page.confirmed and page.confirmed_by is None
    assert any(record.levelno == logging.ERROR and "LAYOUT_FAILED" in record.getMessage() for record in caplog.records)


# --------------------------------------------------------------------------- #
# Đường `failed` và xếp lại khi khởi động mang bộ đọc hình học
# --------------------------------------------------------------------------- #


def _half_read(store: AppStore, tmp_path: Path) -> tuple[str, ImportRuntime]:
    """Tài liệu đọc xong trang 1 rồi bộ đọc chết: `processing`, trang 2–3 `pending`."""
    runtime = fast_runtime(tmp_path)
    doc_id = import_doc(store, runtime, BreakingReader(THREE, break_after=1), auto_confirm=False)
    assert document_of(store, doc_id).status == "processing"
    assert [page.status for page in pages_of(store, doc_id)] == ["ok", "pending", "pending"]
    return doc_id, runtime


def test_failed_path_with_geometry_reader_stores_blocks_of_read_pages(store: AppStore, tmp_path: Path) -> None:
    doc_id, _ = _half_read(store, tmp_path)
    reader = BreakingReader(THREE, fail_open=True)
    job = ReadJob(
        document_id=doc_id,
        path=tmp_path / "khong-co.pdf",
        store=store,
        reader=reader,
        vault=store,
        retry=FAST,
        geometry_reader=reader,
    )
    read_document(job, pause=lambda _: None)
    document = document_of(store, doc_id)
    assert document.status == "failed" and document.layout_version is not None
    assert {block.page_number for block in store.list_blocks(doc_id)} == {1}
    assert [page.status for page in pages_of(store, doc_id)] == ["ok", "failed", "failed"]


def test_requeue_passes_geometry_reader(store: AppStore, tmp_path: Path) -> None:
    doc_id, runtime = _half_read(store, tmp_path)
    reader = GeoReader(THREE)
    queued = requeue_pending(
        store, runtime.files, reader, SynchronousImportQueue(), FAST, vault=store, geometry_reader=reader
    )
    assert queued == [doc_id]
    document = document_of(store, doc_id)
    assert document.status == "review"
    assert document.layout_settings_sha256 == default_layout_config().settings_sha256()
    assert {block.page_number for block in store.list_blocks(doc_id)} == {1, 2, 3}


def test_requeue_missing_file_with_geometry_reader_stores_blocks(store: AppStore, tmp_path: Path) -> None:
    doc_id, runtime = _half_read(store, tmp_path)
    assert runtime.files.delete(document_of(store, doc_id).meta.sha256)
    reader = GeoReader(THREE)
    queued = requeue_pending(
        store, runtime.files, reader, SynchronousImportQueue(), FAST, vault=store, geometry_reader=reader
    )
    assert queued == []
    document = document_of(store, doc_id)
    assert document.status == "failed" and document.layout_version is not None
    assert {block.page_number for block in store.list_blocks(doc_id)} == {1}


def test_requeue_missing_file_busy_store_does_not_abort_startup(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store = FlakyLayoutStore(busy, failures=1000)
    doc_id, runtime = _half_read(store, tmp_path)
    assert runtime.files.delete(document_of(store, doc_id).meta.sha256)
    reader = GeoReader(THREE)
    with caplog.at_level(logging.WARNING):
        queued = requeue_pending(
            store, runtime.files, reader, SynchronousImportQueue(), FAST, vault=store, geometry_reader=reader
        )
    assert queued == []
    document = document_of(store, doc_id)
    assert document.status == "processing" and document.layout_version is None
    assert any("chưa đánh dấu được tài liệu" in record.getMessage() for record in caplog.records)


# --------------------------------------------------------------------------- #
# `page_drop_reason` của ledger theo vai trò trang (0D, trang bìa, `_finish` lại)
# --------------------------------------------------------------------------- #

COVER = text_page("Truong Dai hoc Bach khoa Ha Noi", extra=("Mon hoc Kien truc may tinh IT3070 hoc ky 2025",))
FIRST = [COVER, text_page("2. Kien truc"), text_page("3. Bo nho")]
LATER = [
    COVER,
    text_page("2. Thanh ghi", extra=("Thanh ghi la o nho nhanh ben trong bo xu ly trung tam",)),
    text_page("3. Ngat", extra=("Ngat cho phep thiet bi ngoai vi bao bo xu ly su kien",)),
]


def _two_covers(store: AppStore, tmp_path: Path) -> tuple[str, str, list[Block]]:
    first = import_doc(store, runtime_for(tmp_path / "a"), GeoReader(FIRST), tag="a", auto_confirm=False)
    assert ledger_of(store, first, 1).page_drop_reason is None
    blocks = store.list_blocks(first, 1)
    later = import_doc(store, runtime_for(tmp_path / "b"), GeoReader(LATER), tag="b", auto_confirm=False)
    return first, later, blocks


def test_cover_drop_reason_in_ledger_at_import_and_after_reassess(store: AppStore, tmp_path: Path) -> None:
    """0D bỏ mềm trang bìa của tài liệu sau ⇒ ledger mang `page_drop_reason` ngay sau `_finish`; đánh giá lại trang bìa
    của tài liệu trước ⇒ ledger của nó cũng mang lý do, khối giữ nguyên; trang khác không có lý do."""
    first, later, blocks = _two_covers(store, tmp_path)
    for document_id in (first, later):
        page = pages_of(store, document_id)[0]
        assert page.dropped and page.page_role == "cover_or_course_nav"
        assert ledger_of(store, document_id, 1).page_drop_reason == "cover_or_course_nav"
        assert ledger_of(store, document_id, 2).page_drop_reason is None
    assert store.list_blocks(first, 1) == blocks


def test_second_finish_repairs_stale_drop_reason(store: AppStore, tmp_path: Path) -> None:
    """Ledger lệch vai trò (chết giữa hai lần ghi) được `_finish` lần hai sửa lại; khối giữ nguyên."""
    _, later, _ = _two_covers(store, tmp_path)
    ledger = ledger_of(store, later, 1)
    blocks = store.list_blocks(later, 1)
    document = document_of(store, later)
    store.save_document_layout(
        later,
        DocumentLayout(
            layout_version=document.layout_version,
            layout_settings_sha256=document.layout_settings_sha256,
            pages=[
                PageLayout(page_number=1, blocks=blocks, ledger=ledger.model_copy(update={"page_drop_reason": None}))
            ],
        ),
    )
    finish_again(store, later, GeoReader(LATER), default_layout_config(LANG))
    assert ledger_of(store, later, 1).page_drop_reason == "cover_or_course_nav"
    assert store.list_blocks(later, 1) == blocks


def test_second_finish_undrop_clears_drop_reason(store: AppStore, tmp_path: Path) -> None:
    """Tài liệu trước bị xoá ⇒ `_finish` lần hai của tài liệu sau trả trang bìa về `content` ⇒ ledger bỏ lý do."""
    first, later, _ = _two_covers(store, tmp_path)
    store.delete_document(first)
    finish_again(store, later, GeoReader(LATER), default_layout_config(LANG))
    assert not pages_of(store, later)[0].dropped
    assert ledger_of(store, later, 1).page_drop_reason is None


def test_refresh_drop_reasons_only_listed_pages_keeps_blocks(store: AppStore, tmp_path: Path) -> None:
    doc_id = import_doc(store, runtime_for(tmp_path), GeoReader(THREE), auto_confirm=False)
    blocks = store.list_blocks(doc_id)
    drop_page(store, doc_id, 1, 2)
    drop_page(store, doc_id, 3, 2)
    refresh_drop_reasons(store, doc_id, [1])
    assert ledger_of(store, doc_id, 1).page_drop_reason == "cover_or_course_nav"
    assert ledger_of(store, doc_id, 3).page_drop_reason is None, "trang không liệt kê không ghi"
    assert store.list_blocks(doc_id) == blocks


# --------------------------------------------------------------------------- #
# Dựng lại sau `PATCH …/lines`: thử lại, ảnh cắt, ledger
# --------------------------------------------------------------------------- #


def _bump_page(document_id: str, number: int) -> Callable[[FlakyLayoutStore], None]:
    """Lần ghi chen ngang: trang `number` lên version (nội dung không đổi)."""

    def bump(target: FlakyLayoutStore) -> None:
        [page] = [item for item in target.list_pages(document_id, PageExtraction) if item.page_number == number]
        IngestionStore.update_page(
            target,
            page.document_id,
            number,
            page.model_copy(update={"version": page.version + 1}),
            status=page.status,
            confirmed=page.confirmed,
            expected_version=page.version,
        )

    return bump


def _rebuild_store(tmp_path: Path) -> tuple[FlakyLayoutStore, ImportRuntime, str]:
    store = FlakyLayoutStore(conflict)
    runtime = runtime_for(tmp_path)
    doc_id = import_doc(store, runtime, GeoReader(THREE), auto_confirm=False)
    store.calls = 0
    return store, runtime, doc_id


def test_rebuild_retries_when_other_page_changed(tmp_path: Path) -> None:
    store, runtime, doc_id = _rebuild_store(tmp_path)
    store.failures = SETTLE_ATTEMPTS - 1
    store.on_fail = _bump_page(doc_id, 2)
    edited = patch_line(store, runtime, doc_id, 1, 1, "Bo dem chuong trinh da sua")
    assert store.calls == SETTLE_ATTEMPTS
    assert pages_of(store, doc_id)[0].lines[1].text == edited.lines[1].text == "Bo dem chuong trinh da sua"
    assert len(store.list_page_audits(doc_id, 1)) == 1


@pytest.mark.parametrize("bumped", [1, 2])
def test_rebuild_gives_up_without_writing(tmp_path: Path, bumped: int) -> None:
    """Trang vừa sửa lệch ⇒ ném ngay; chỉ trang khác lệch ⇒ ném khi hết `SETTLE_ATTEMPTS` lượt; không ghi gì."""
    store, runtime, doc_id = _rebuild_store(tmp_path)
    before = pages_of(store, doc_id)[0]
    store.failures = 1000
    store.on_fail = _bump_page(doc_id, bumped)
    with pytest.raises(VersionConflictError):
        patch_line(store, runtime, doc_id, 1, 1, "Bo dem chuong trinh da sua")
    assert store.calls == (1 if bumped == 1 else SETTLE_ATTEMPTS)
    assert pages_of(store, doc_id)[0].lines == before.lines
    assert store.list_page_audits(doc_id, 1) == []


FIGURE_STAGE = EditStage("hinh", as_figure)


def _figure_with_crop(store: AppStore, tmp_path: Path) -> tuple[str, Block, str]:
    runtime = runtime_for(tmp_path, layout_with(FIGURE_STAGE))
    doc_id = import_doc(store, runtime, GeoReader(THREE), auto_confirm=False)
    [figure] = [block for block in store.list_blocks(doc_id, 1) if block.type == "figure"]
    assert figure.bbox is not None
    content = b"\x89PNG\r\n\x1a\nanh-cat-test"
    ref = hashlib.sha256(content).hexdigest()
    crop = CropImage(
        document_id=doc_id,
        block_id=figure.block_id,
        crop_ref=ref,
        page_number=1,
        bbox=figure.bbox,
        width_px=2,
        height_px=1,
        size_bytes=len(content),
    )
    assert store.save_crop_image(crop, content) == "stored"
    return doc_id, figure, ref


def test_patch_keeps_crop_of_unchanged_figure(store: AppStore, tmp_path: Path) -> None:
    doc_id, figure, ref = _figure_with_crop(store, tmp_path)
    runtime = runtime_for(tmp_path / "patch", layout_with(FIGURE_STAGE))
    patch_line(store, runtime, doc_id, 1, 0, "1. Gioi thieu moi")
    [after] = [block for block in store.list_blocks(doc_id, 1) if block.type == "figure"]
    assert after.block_id == figure.block_id and after.crop_ref == ref
    assert "crop_pending" not in after.flags
    assert store.get_crop_bytes(doc_id, ref) is not None


def test_patch_drops_crop_when_figure_box_moves(store: AppStore, tmp_path: Path) -> None:
    doc_id, figure, _ = _figure_with_crop(store, tmp_path)
    shifted = dataclasses.replace(
        layout_with(FIGURE_STAGE), page_stages=(*layout_with(FIGURE_STAGE).page_stages, ShiftFigure())
    )
    patch_line(store, fast_runtime(tmp_path / "patch", shifted), doc_id, 1, 0, "1. Gioi thieu moi")
    [after] = [block for block in store.list_blocks(doc_id, 1) if block.type == "figure"]
    assert after.block_id == figure.block_id and after.bbox != figure.bbox
    assert after.crop_ref is None and "crop_pending" in after.flags


def test_patch_saves_other_page_whose_ledger_changed(store: AppStore, tmp_path: Path) -> None:
    """Khối trang 2 không đổi nhưng ledger lệch vai trò (bỏ mềm chưa ghi ledger) ⇒ `PATCH` trang 1 lưu lại ledger 2."""
    runtime = runtime_for(tmp_path)
    doc_id = import_doc(store, runtime, GeoReader(THREE), auto_confirm=False)
    blocks = store.list_blocks(doc_id, 2)
    drop_page(store, doc_id, 2, 1)
    assert ledger_of(store, doc_id, 2).page_drop_reason is None
    patch_line(store, runtime, doc_id, 1, 1, "Bo dem chuong trinh da sua")
    assert ledger_of(store, doc_id, 2).page_drop_reason == "cover_or_course_nav"
    assert store.list_blocks(doc_id, 2) == blocks


# --------------------------------------------------------------------------- #
# `block_tree` và route ảnh cắt
# --------------------------------------------------------------------------- #


def _sample(store: AppStore, tmp_path: Path) -> tuple[list[PageExtraction], list[Block]]:
    doc_id = import_doc(store, runtime_for(tmp_path), GeoReader(THREE), auto_confirm=False)
    return pages_of(store, doc_id), store.list_blocks(doc_id)


def test_page_lists_unconfirmable_ignores_dropped(store: AppStore, tmp_path: Path) -> None:
    pages, blocks = _sample(store, tmp_path)
    blank = pages[0].model_copy(update={"lines": []})
    dropped = blank.model_copy(update={"dropped": True, "page_role": "cover_or_course_nav"})
    assert _page_lists([blank, *pages[1:]], blocks)[2] == [1]
    assert _page_lists([dropped, *pages[1:]], blocks)[2] == []


def test_page_lists_without_blocks_conditions(store: AppStore, tmp_path: Path) -> None:
    pages, blocks = _sample(store, tmp_path)
    others = [block for block in blocks if block.page_number != 1]
    confirmed = pages[0].model_copy(update={"confirmed": True, "confirmed_by": "user"})
    assert _page_lists([confirmed, *pages[1:]], others)[1] == [1]
    for variant in (
        pages[0],
        confirmed.model_copy(update={"status": "failed"}),
        confirmed.model_copy(update={"dropped": True, "page_role": "cover_or_course_nav"}),
    ):
        assert _page_lists([variant, *pages[1:]], others)[1] == []


def test_root_needs_chapter(store: AppStore, tmp_path: Path) -> None:
    runtime = runtime_for(tmp_path)
    doc_id = import_doc(store, runtime, GeoReader(THREE), auto_confirm=False)
    confirm_document(store, store, runtime, doc_id, _confirm_request(), now=NOW)
    sections = store.list_sections(doc_id)
    children = [section for section in sections if section.kind != "chapter"]
    assert children
    assert _root(children) is None
    root = _root(sections)
    assert root is not None and root.section.kind == "chapter"


def _confirm_request() -> DocumentConfirmRequest:
    return DocumentConfirmRequest(editor_id="u")


def test_section_blocks_only_eligible_in_page_then_order(store: AppStore, tmp_path: Path) -> None:
    """Mục gồm khối của trang 1–2 (kể cả khối không đủ điều kiện) ⇒ chỉ khối `eligible`, sắp theo (trang,
    `reading_order`)."""
    mismatch = EditStage("bang", lambda b: as_table("table_token_mismatch")(b) if b.reading_order == 1 else b)
    runtime = runtime_for(tmp_path, layout_with(mismatch))
    doc_id = import_doc(store, runtime, GeoReader(TWO_PARAGRAPHS), auto_confirm=False)
    confirm_document(store, store, runtime, doc_id, _confirm_request(), now=NOW)
    [root] = [section for section in store.list_sections(doc_id) if section.kind == "chapter"]
    members = [block for block in store.list_blocks(doc_id) if block.page_number in (1, 2)]
    eligible = {block.block_id for block in store.list_eligible_blocks(doc_id)}
    assert any(block.block_id not in eligible for block in members)
    wide = Section.model_validate(
        {**root.model_dump(), "block_ids": [block.block_id for block in reversed(members)], "page_end": 2}
    )
    store.replace_sections(doc_id, [wide])
    result = section_blocks(store, store, doc_id, root.section_id, offset=0, limit=500)
    expected = sorted(
        (block for block in members if block.block_id in eligible), key=lambda b: (b.page_number, b.reading_order)
    )
    assert result.total == len(expected) and [block.block_id for block in result.blocks] == [
        block.block_id for block in expected
    ]
    assert {block.page_number for block in result.blocks} == {1, 2}
    assert max(block.reading_order for block in result.blocks if block.page_number == 1) > min(
        block.reading_order for block in result.blocks if block.page_number == 2
    ), "thứ tự (reading_order, trang) sẽ khác"


def test_page_block_list_accepts_last_page(store: AppStore, tmp_path: Path) -> None:
    doc_id = import_doc(store, runtime_for(tmp_path), GeoReader(THREE), auto_confirm=False)
    page = page_block_list(store, doc_id, document_of(store, doc_id).meta.page_count)
    assert page.page_number == 3 and page.blocks


def test_crop_route_unknown_document_detail(store: AppStore) -> None:
    app.dependency_overrides[get_store] = lambda: store
    try:
        ref = hashlib.sha256(b"x").hexdigest()
        response = TestClient(app).get(f"/v1/documents/doc_ffffffffffffffff/crops/{ref}")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 404 and response.json()["detail"] == DOCUMENT_NOT_FOUND_DETAIL
