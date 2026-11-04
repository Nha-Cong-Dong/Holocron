"""Job cắt ảnh nền cho khối cần ảnh (ADR-010 mục 9, 13 "Job cắt ảnh nền", 19; SPECIFICATION §5.7, §5.15; XCROP).

Tài liệu nhập qua `accept_document` với bộ đọc giả có hình học và stage biến đoạn thành `figure` mang `crop_pending`
(`tests/import_blocks_support.py`). Bộ cắt ảnh là `RegionRenderer` giả trả PNG 1×1 tự tạo; không mạng, không worker.
"""

from __future__ import annotations

import dataclasses
import hashlib
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from holocron_core.domain.blocks import Block
from holocron_core.domain.documents import DocumentConfirmRequest, LineEdit, PageExtraction, PageLinesPatch
from holocron_core.domain.repositories import AppStore, BlockFlagUpdate
from holocron_core.infrastructure.document_readers.config import PdfReaderConfig
from holocron_core.infrastructure.document_readers.pdf_reader import CropFailure, CropOutcome, RenderedCrop
from holocron_core.infrastructure.document_readers.protocol import CropRegion
from holocron_core.infrastructure.storage import SQLiteStore
from holocron_core.routers.documents import requeue_on_startup
from holocron_core.services.crop_job import (
    BackgroundCropJobs,
    CropRunner,
    SynchronousCropJobs,
    plan_crop_job,
    schedule_pending_crops,
)
from holocron_core.services.document_import import (
    ImportRuntime,
    confirm_document,
    delete_imported_document,
    edit_page_lines,
)
from holocron_core.services.document_layout import blocks_needing_crop
from tests.block_samples import geo_line, geometry
from tests.import_blocks_support import (
    NOW,
    EditStage,
    GeoReader,
    SamplePage,
    as_figure,
    import_doc,
    layout_with,
    pages_of,
    runtime_for,
    store,
    text_page,
)
from tests.test_block_store import tiny_png

__all__ = ["store"]

FIGURES = EditStage("hinh", as_figure, pages=frozenset({1, 2, 3}))
SECOND = ("Doan thu hai mo ta thanh ghi trang thai cua bo xu ly va cac co bao loi",)
PAGES = [text_page(f"{n}. Muc", extra=SECOND) for n in (1, 2, 3)]
WAIT_S = 10.0


@dataclass
class FakeRenderer:
    """`RegionRenderer` giả: PNG 1×1 khác nhau theo thứ tự gọi; `fail` = mã lỗi cho vùng theo vị trí toàn cục;
    `on_call(n)` chạy (NGOÀI khoá của job) trước khi trả kết quả lần gọi thứ n."""

    on_call: Callable[[int], None] | None = None
    fail: dict[int, str] = field(default_factory=dict)
    calls: list[list[CropRegion]] = field(default_factory=list)
    seen: int = 0

    def render_regions(self, path: Path, requests: Sequence[CropRegion], *, max_edge_px: int) -> list[CropOutcome]:
        self.calls.append(list(requests))
        if self.on_call is not None:
            self.on_call(len(self.calls))
        out: list[CropOutcome] = []
        for region in requests:
            self.seen += 1
            code = self.fail.get(self.seen)
            if code == "capped":
                out.append(CropFailure(region.region_id, "capped"))
            elif code is not None:
                out.append(CropFailure(region.region_id, "render_error"))
            else:
                png = tiny_png(self.seen)
                out.append(RenderedCrop(region.region_id, png, 1, 1, hashlib.sha256(png).hexdigest()))
        return out


def _runner(runtime: ImportRuntime, renderer: FakeRenderer, **config: float) -> CropRunner:
    return CropRunner(renderer=renderer, locks=runtime.locks, config=PdfReaderConfig.model_validate(config))


def _with_crops(runtime: ImportRuntime, renderer: FakeRenderer, **config: float) -> ImportRuntime:
    return dataclasses.replace(runtime, crops=SynchronousCropJobs(_runner(runtime, renderer, **config), runtime.files))


def _figures(store: AppStore, document_id: str) -> list[Block]:
    return [block for block in store.list_blocks(document_id) if block.type == "figure"]


def _patch(store: AppStore, runtime: ImportRuntime, document_id: str, number: int, line: int, text: str) -> None:
    page = pages_of(store, document_id)[number - 1]
    body = PageLinesPatch(editor_id="u", expected_version=page.version, edits=[LineEdit(line_index=line, text=text)])
    edit_page_lines(store, store, runtime, document_id, number, body, now=NOW)


def _all_cropped(store: AppStore, document_id: str) -> bool:
    rows = {row.block_id: row.crop_ref for row in store.list_crop_images(document_id)}
    figures = _figures(store, document_id)
    return bool(figures) and all(
        rows.get(block.block_id) == block.crop_ref and block.crop_ref is not None and "crop_pending" not in block.flags
        for block in figures
    )


def _crop_files(tmp_path: Path) -> set[str]:
    root = tmp_path / "crops"
    return {path.stem for path in root.rglob("*.png")} if root.is_dir() else set()


def test_finish_schedules_job_that_crops_every_pending_block(store: AppStore, tmp_path: Path) -> None:
    """Bước kết thúc lần đọc commit ⇒ job cắt ảnh mọi khối cần ảnh; ghi theo lô ≤ `crop_max_regions_per_request`."""
    renderer = FakeRenderer()
    runtime = _with_crops(runtime_for(tmp_path, layout_with(FIGURES)), renderer, crop_max_regions_per_request=2)
    doc_id = import_doc(store, runtime, GeoReader(PAGES), auto_confirm=False)
    assert len(_figures(store, doc_id)) == 6
    assert [len(call) for call in renderer.calls] == [2, 2, 2]
    assert _all_cropped(store, doc_id)
    if isinstance(store, SQLiteStore):
        assert _crop_files(tmp_path) == {row.crop_ref for row in store.list_crop_images(doc_id)}


def test_patch_does_not_wait_for_crop_job(store: AppStore, tmp_path: Path) -> None:
    """Request PATCH trả về khi job còn đang render (job nền); khối giữ `crop_pending` tới khi job ghi ảnh."""
    runtime = runtime_for(tmp_path, layout_with(FIGURES))
    doc_id = import_doc(store, runtime, GeoReader(PAGES), auto_confirm=False)
    entered, release = threading.Event(), threading.Event()

    def block_render(_: int) -> None:
        entered.set()
        assert release.wait(WAIT_S)

    renderer = FakeRenderer(on_call=block_render)
    jobs = BackgroundCropJobs(_runner(runtime, renderer, crop_max_regions_per_request=32), runtime.files)
    background = dataclasses.replace(runtime, crops=jobs)
    try:
        _patch(store, background, doc_id, 1, 1, "Bo dem chuong trinh da sua boi nguoi dung")
        assert entered.wait(WAIT_S), "PATCH phải xếp job sau commit"
        assert all("crop_pending" in block.flags for block in _figures(store, doc_id))
        release.set()
        assert jobs.wait_until_idle(WAIT_S)
        assert _all_cropped(store, doc_id)
    finally:
        release.set()
        assert jobs.close(WAIT_S)


def test_crop_job_races_patch(store: AppStore, tmp_path: Path) -> None:
    """PATCH chen giữa lúc job đang render (ngoài khoá) đổi id của khối hình: job cũ bỏ qua khối đã đổi (không ghi
    hàng/file thừa), job do PATCH xếp cắt khối mới; mọi hàng ảnh trỏ tới khối còn tồn tại."""
    holder: dict[str, ImportRuntime] = {}
    doc: dict[str, str] = {}

    def patch_once(call: int) -> None:
        if call == 1:
            _patch(store, holder["rt"], doc["id"], 1, 1, "Bo dem chuong trinh da sua trong luc cat anh")

    renderer = FakeRenderer()
    runtime = runtime_for(tmp_path, layout_with(FIGURES))
    doc["id"] = import_doc(store, runtime, GeoReader(PAGES), auto_confirm=False)
    before = {block.block_id for block in _figures(store, doc["id"])}
    holder["rt"] = _with_crops(runtime, renderer, crop_max_regions_per_request=32)
    renderer.on_call = patch_once
    assert holder["rt"].crops is not None and holder["rt"].crops.schedule(store, doc["id"])
    after = {block.block_id for block in _figures(store, doc["id"])}
    assert before - after, "PATCH phải đổi id của một khối hình"
    rows = {row.block_id for row in store.list_crop_images(doc["id"])}
    assert rows == after and not (rows & (before - after))
    assert _all_cropped(store, doc["id"])
    if isinstance(store, SQLiteStore):
        assert _crop_files(tmp_path) == {row.crop_ref for row in store.list_crop_images(doc["id"])}


def test_crop_job_cancelled_on_document_delete(store: AppStore, tmp_path: Path) -> None:
    """Xoá tài liệu giữa hai lô ⇒ job huỷ: không render lô kế, không ghi hàng hay file ảnh nào."""
    renderer = FakeRenderer()
    base = runtime_for(tmp_path, layout_with(FIGURES))
    doc_id = import_doc(store, base, GeoReader(PAGES), auto_confirm=False)
    runner = _runner(base, renderer, crop_max_regions_per_request=1)
    job = plan_crop_job(store, doc_id, base.files)
    assert job is not None
    renderer.on_call = lambda call: delete_imported_document(store, base, doc_id) if call == 1 else None
    result = runner.run(job)
    assert result.cancelled and result.stored == 0
    assert len(renderer.calls) == 1
    assert store.get_imported_document(doc_id) is None and store.list_crop_images(doc_id) == []
    assert _crop_files(tmp_path) == set()


def test_stale_crop_job_does_not_write_new_import(store: AppStore, tmp_path: Path) -> None:
    """Job tạo cho thế hệ nhập cũ chạy sau khi tài liệu bị xoá rồi nhập lại (cùng `document_id`, thế hệ mới) ⇒ không
    ghi gì vào lần nhập mới; job mới của lần nhập mới vẫn cắt được."""
    base = runtime_for(tmp_path, layout_with(FIGURES))
    doc_id = import_doc(store, base, GeoReader(PAGES), auto_confirm=False)
    stale = plan_crop_job(store, doc_id, base.files)
    assert stale is not None
    delete_imported_document(store, base, doc_id)
    again = import_doc(store, base, GeoReader(PAGES), auto_confirm=False)
    assert again == doc_id
    renderer = FakeRenderer()
    runner = _runner(base, renderer)
    result = runner.run(stale)
    assert result.cancelled and renderer.calls == [] and store.list_crop_images(doc_id) == []
    assert all("crop_pending" in block.flags for block in _figures(store, doc_id))
    fresh = plan_crop_job(store, doc_id, base.files)
    assert fresh is not None and fresh.key != stale.key
    assert runner.run(fresh).stored == len(_figures(store, doc_id))


def test_successful_crop_clears_failed_and_capped_flags(store: AppStore, tmp_path: Path) -> None:
    """Khối mang thêm `crop_failed`/`crop_capped` trong lúc job render (vẫn `crop_pending`) ⇒ ghi ảnh thành công gỡ cả
    ba cờ ảnh cắt cùng giao dịch với hàng `crop_images`."""
    base = runtime_for(tmp_path, layout_with(FIGURES))
    doc_id = import_doc(store, base, GeoReader(PAGES[:1]), auto_confirm=False)
    figures = _figures(store, doc_id)

    def mark(_: int) -> None:
        updates = [BlockFlagUpdate(block_id=b.block_id, add=["crop_failed", "crop_capped"]) for b in figures]
        store.update_block_flags(doc_id, updates)

    job = plan_crop_job(store, doc_id, base.files)
    assert job is not None
    assert _runner(base, FakeRenderer(on_call=mark)).run(job).stored == len(figures)
    for block in _figures(store, doc_id):
        assert block.crop_ref is not None
        assert not {"crop_pending", "crop_failed", "crop_capped"} & set(block.flags)


def test_failed_or_capped_crops_not_retried(store: AppStore, tmp_path: Path) -> None:
    """Lỗi render ⇒ `crop_pending` + `crop_failed`; trần ⇒ `crop_pending` + `crop_capped`; không ném. Lần xếp sau
    (sau `_finish` lần nữa, quét khởi động) không render lại các khối đó."""
    renderer = FakeRenderer(fail={1: "render_error", 2: "capped"})
    runtime = _with_crops(runtime_for(tmp_path, layout_with(FIGURES)), renderer)
    doc_id = import_doc(store, runtime, GeoReader(PAGES[:1]), auto_confirm=False)
    flags = sorted(
        tuple(sorted(set(block.flags) & {"crop_pending", "crop_failed", "crop_capped"}))
        for block in _figures(store, doc_id)
    )
    assert flags == [("crop_capped", "crop_pending"), ("crop_failed", "crop_pending")]
    assert store.list_crop_images(doc_id) == []
    calls = len(renderer.calls)
    assert runtime.crops is not None and runtime.crops.schedule(store, doc_id) is False
    assert schedule_pending_crops(store, runtime.crops) == []
    assert len(renderer.calls) == calls


def test_crop_jobs_rescheduled_on_startup_scan(store: AppStore, tmp_path: Path) -> None:
    """Khởi động lại: quét khối `crop_pending` (không `crop_failed`/`crop_capped`, chưa có hàng ảnh) và xếp job cho
    tài liệu của chúng, qua `requeue_on_startup` của router; tài liệu không còn khối chờ không được xếp."""
    plain = runtime_for(tmp_path, layout_with(FIGURES))
    waiting = import_doc(store, plain, GeoReader(PAGES), tag="cho", auto_confirm=False)
    failed_renderer = FakeRenderer(fail={1: "render_error", 2: "render_error"})
    done = import_doc(store, _with_crops(plain, failed_renderer), GeoReader(PAGES[:1]), tag="loi", auto_confirm=False)
    assert all("crop_failed" in block.flags for block in _figures(store, done))
    renderer = FakeRenderer()
    restarted = _with_crops(plain, renderer)
    requeue_on_startup(store, restarted)
    assert _all_cropped(store, waiting)
    assert {region.page_index for call in renderer.calls for region in call} == {0, 1, 2}
    assert len([region for call in renderer.calls for region in call]) == len(_figures(store, waiting))
    assert store.list_crop_images(done) == []


def test_crop_job_reevaluates_auto_confirm(store: AppStore, tmp_path: Path) -> None:
    """Nghĩa mới (SPEC §5.7, người dùng 2026-11-03): job cắt ảnh KHÔNG đổi trạng thái xác nhận trang, trang tự xác nhận
    giữ nguyên, trang chưa xác nhận vẫn chưa; trên tài liệu đã đóng, job ghi ảnh nhưng không đổi `eligible` hay trạng
    thái tài liệu."""
    base = runtime_for(tmp_path, layout_with(FIGURES))
    doc_id = import_doc(store, base, GeoReader(PAGES), auto_confirm=True)
    before = pages_of(store, doc_id)
    assert any(page.confirmed_by == "auto" for page in before), "cờ crop_pending không chặn tự xác nhận"
    job = plan_crop_job(store, doc_id, base.files)
    assert job is not None and _runner(base, FakeRenderer()).run(job).stored == 6
    assert pages_of(store, doc_id) == before


def test_crop_job_on_closed_document_keeps_eligible_and_status(store: AppStore, tmp_path: Path) -> None:
    """Tài liệu đã đóng ⇒ job vẫn ghi ảnh nhưng không đổi `eligible`, trang hay trạng thái/version của tài liệu."""
    base = runtime_for(tmp_path, layout_with(FIGURES))
    closed = import_doc(store, base, GeoReader(PAGES), auto_confirm=True)
    confirm_document(store, store, base, closed, DocumentConfirmRequest(editor_id="u"), now=NOW)
    document = store.get_imported_document(closed)
    assert document is not None and document.status == "confirmed"
    eligible = [block.block_id for block in store.list_eligible_blocks(closed)]
    pages = pages_of(store, closed)
    job = plan_crop_job(store, closed, base.files)
    assert job is not None and _runner(base, FakeRenderer()).run(job).stored == 6
    assert [block.block_id for block in store.list_eligible_blocks(closed)] == eligible
    assert pages_of(store, closed) == pages
    after = store.get_imported_document(closed)
    assert after is not None and after.status == "confirmed" and after.version == document.version
    assert _all_cropped(store, closed)


def test_crop_document_budget_marks_remaining_capped(store: AppStore, tmp_path: Path) -> None:
    """Hết `crop_document_budget_s` của job ⇒ mọi vùng còn lại `crop_capped` (giữ `crop_pending`); ngân sách tính cho
    TỪNG job (job sau của tài liệu khác có ngân sách mới)."""
    clock = [0.0]

    def tick(_: int) -> None:
        clock[0] += 5.0

    base = runtime_for(tmp_path, layout_with(FIGURES))
    first = import_doc(store, base, GeoReader(PAGES), tag="mot", auto_confirm=False)
    second = import_doc(store, base, GeoReader(PAGES), tag="hai", auto_confirm=False)
    config = PdfReaderConfig(crop_max_regions_per_request=2, crop_document_budget_s=4.0)
    runner = CropRunner(
        renderer=FakeRenderer(on_call=tick), locks=base.locks, config=config, monotonic=lambda: clock[0]
    )
    for doc_id in (first, second):
        job = plan_crop_job(store, doc_id, base.files)
        assert job is not None
        result = runner.run(job)
        assert (result.stored, result.capped) == (2, 4)
        capped = [block for block in _figures(store, doc_id) if "crop_capped" in block.flags]
        assert len(capped) == 4 and all("crop_pending" in block.flags for block in capped)


def test_crop_caps_per_page_and_document_mark_capped(store: AppStore, tmp_path: Path) -> None:
    """Trần số vùng mỗi trang/mỗi job (`crop_max_per_page`, `crop_max_per_document`) ⇒ vùng vượt `crop_capped`."""
    base = runtime_for(tmp_path, layout_with(FIGURES))
    doc_id = import_doc(store, base, GeoReader(PAGES), auto_confirm=False)
    job = plan_crop_job(store, doc_id, base.files)
    assert job is not None
    renderer = FakeRenderer()
    result = _runner(base, renderer, crop_max_per_page=1, crop_max_per_document=2).run(job)
    assert (result.stored, result.capped) == (2, 4)
    assert sum(len(call) for call in renderer.calls) == 2


def test_bbox_null_block_not_cropped(store: AppStore, tmp_path: Path) -> None:
    """Khối `bbox = null` không bao giờ vào job: chỉ khối có hộp được gửi đi cắt; trang chỉ có dòng không hộp không
    tạo job."""
    boxless = SamplePage(geometry([geo_line("Dong khong co hop toa do nao ca", None, 0.0)]))
    base = runtime_for(tmp_path, layout_with(FIGURES))
    renderer = FakeRenderer()
    runtime = _with_crops(base, renderer)
    doc_id = import_doc(store, runtime, GeoReader([PAGES[0], boxless]), auto_confirm=False)
    blocks = store.list_blocks(doc_id)
    assert any(block.bbox is None for block in blocks)
    assert all(block.bbox is not None for block in blocks_needing_crop(blocks))
    sent = [region for call in renderer.calls for region in call]
    assert sent and all(region.page_index == 0 for region in sent)
    only = import_doc(store, base, GeoReader([boxless]), tag="khong", auto_confirm=False)
    assert plan_crop_job(store, only, base.files) is None


def test_background_jobs_respect_max_parallel_jobs(store: AppStore, tmp_path: Path) -> None:
    """`crop_max_parallel_jobs = 1`: hai tài liệu xếp job cùng lúc vẫn chạy lần lượt."""
    base = runtime_for(tmp_path, layout_with(FIGURES))
    docs = [import_doc(store, base, GeoReader(PAGES), tag=tag, auto_confirm=False) for tag in ("x", "y")]
    guard = threading.Lock()
    running = [0, 0]

    def track(_: int) -> None:
        with guard:
            running[0] += 1
            running[1] = max(running[1], running[0])
        threading.Event().wait(0.05)
        with guard:
            running[0] -= 1

    jobs = BackgroundCropJobs(_runner(base, FakeRenderer(on_call=track), crop_max_parallel_jobs=1), base.files)
    try:
        assert all(jobs.schedule(store, doc_id) for doc_id in docs)
        assert jobs.wait_until_idle(WAIT_S)
    finally:
        assert jobs.close(WAIT_S)
    assert running[1] == 1
    assert all(_all_cropped(store, doc_id) for doc_id in docs)


def test_scheduling_without_layout_is_noop(store: AppStore, tmp_path: Path) -> None:
    """Tài liệu đi đường cũ (không hình học, `layout_version` null) hay không còn tồn tại ⇒ không tạo job."""
    base = runtime_for(tmp_path)
    doc_id = import_doc(store, base, GeoReader(PAGES[:1]), geometry=False, auto_confirm=False)
    assert plan_crop_job(store, doc_id, base.files) is None
    assert plan_crop_job(store, "doc_ffffffffffffffff", base.files) is None


def test_job_never_touches_pages(store: AppStore, tmp_path: Path) -> None:
    """Job chỉ ghi ảnh và cờ ảnh cắt: version của mọi trang giữ nguyên (kể cả khi có vùng lỗi)."""
    base = runtime_for(tmp_path, layout_with(FIGURES))
    doc_id = import_doc(store, base, GeoReader(PAGES), auto_confirm=False)
    versions = [page.version for page in store.list_pages(doc_id, PageExtraction)]
    job = plan_crop_job(store, doc_id, base.files)
    assert job is not None
    _runner(base, FakeRenderer(fail={1: "render_error"})).run(job)
    assert [page.version for page in store.list_pages(doc_id, PageExtraction)] == versions
