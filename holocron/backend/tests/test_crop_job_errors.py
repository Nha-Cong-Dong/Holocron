"""Nhánh lỗi của job cắt ảnh nền (ADR-010 mục 13; SPECIFICATION §5.7, §5.15; XCROP, review 2026-11-04).

- Lỗi khi xếp job (kể cả lỗi dữ liệu như `ValueError`) không đổi kết quả đã commit của người gọi: lần đọc vẫn xong,
  `PATCH …/lines` vẫn trả bình thường.
- Kho từ chối ghi ảnh của MỘT khối (`ValueError`, `OSError`) ⇒ khối đó `crop_failed`, các khối khác vẫn ghi, cờ của
  lô vẫn được ghi, không tự thử lại.
- Trần số vùng mỗi trang/tài liệu tính cả các hàng `crop_images` đã có.
"""

from __future__ import annotations

import dataclasses
import hashlib
from pathlib import Path

import pytest

from holocron_core.domain.blocks import Block, CropImage
from holocron_core.domain.repositories import AppStore, CropWriteResult
from holocron_core.services.crop_job import SynchronousCropJobs, plan_crop_job, schedule_pending_crops
from tests.import_blocks_support import GeoReader, document_of, import_doc, layout_with, pages_of, runtime_for, store
from tests.test_block_store import tiny_png
from tests.test_crop_job import FIGURES, PAGES, FakeRenderer, _figures, _patch, _runner

__all__ = ["store"]

_CROP_FLAGS = {"crop_pending", "crop_failed", "crop_capped"}


class _RaisingCropJobs:
    """Hàng job ném lỗi dữ liệu (không phải lỗi kho) mỗi lần xếp."""

    def __init__(self) -> None:
        self.attempts = 0

    def schedule(self, store: AppStore, document_id: str) -> bool:
        self.attempts += 1
        raise ValueError("du lieu khoi hong")

    def close(self, timeout_s: float = 0.0) -> bool:
        return True


def test_schedule_data_error_does_not_fail_finished_read(store: AppStore, tmp_path: Path) -> None:
    """`_finish` đã commit rồi mới xếp job: lỗi dữ liệu khi xếp không được biến tài liệu đọc xong thành `failed`."""
    jobs = _RaisingCropJobs()
    runtime = dataclasses.replace(runtime_for(tmp_path, layout_with(FIGURES)), crops=jobs)
    doc_id = import_doc(store, runtime, GeoReader(PAGES), auto_confirm=False)
    assert jobs.attempts == 1
    assert document_of(store, doc_id).status != "failed"
    assert all("crop_pending" in block.flags for block in _figures(store, doc_id))


def test_schedule_data_error_does_not_fail_committed_patch(store: AppStore, tmp_path: Path) -> None:
    """`PATCH …/lines` đã commit rồi mới xếp job: lỗi dữ liệu khi xếp không làm request ném (không thành 500)."""
    plain = runtime_for(tmp_path, layout_with(FIGURES))
    doc_id = import_doc(store, plain, GeoReader(PAGES), auto_confirm=False)
    before = pages_of(store, doc_id)[0].version
    jobs = _RaisingCropJobs()
    _patch(store, dataclasses.replace(plain, crops=jobs), doc_id, 1, 1, "Bo dem chuong trinh da sua boi nguoi dung")
    assert jobs.attempts == 1
    assert pages_of(store, doc_id)[0].version > before


@pytest.mark.parametrize("error", [ValueError("byte anh sai"), OSError("dia day")], ids=["value", "os"])
def test_store_error_on_one_block_marks_it_failed_and_keeps_batch_flags(
    store: AppStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    """Kho ném lỗi khi ghi ảnh của một khối ⇒ khối đó `crop_failed` (giữ `crop_pending`), khối khác vẫn ghi ảnh, cờ
    lỗi render đã gom trong cùng lô vẫn được ghi, job không ném; lần xếp sau không render lại."""
    base = runtime_for(tmp_path, layout_with(FIGURES))
    doc_id = import_doc(store, base, GeoReader(PAGES), auto_confirm=False)
    figures = _figures(store, doc_id)
    assert len(figures) == 6
    broken = figures[2].block_id
    original = store.save_crop_image

    def save(crop: CropImage, png: bytes) -> CropWriteResult:
        if crop.block_id == broken:
            raise error
        return original(crop, png)

    monkeypatch.setattr(store, "save_crop_image", save)
    renderer = FakeRenderer(fail={1: "render_error"})
    job = plan_crop_job(store, doc_id, base.files)
    assert job is not None
    result = _runner(base, renderer, crop_max_regions_per_request=32).run(job)
    assert (result.stored, result.failed, result.capped) == (4, 2, 0)
    by_id = {block.block_id: block for block in _figures(store, doc_id)}
    assert set(by_id[broken].flags) & _CROP_FLAGS == {"crop_pending", "crop_failed"}
    failed = [block for block in by_id.values() if "crop_failed" in block.flags]
    assert len(failed) == 2
    assert {row.block_id for row in store.list_crop_images(doc_id)} == set(by_id) - {b.block_id for b in failed}
    calls = len(renderer.calls)
    jobs = SynchronousCropJobs(_runner(base, renderer), base.files)
    assert jobs.schedule(store, doc_id) is False
    assert schedule_pending_crops(store, jobs) == []
    assert len(renderer.calls) == calls


def _store_existing(store: AppStore, block: Block) -> None:
    assert block.bbox is not None
    png = tiny_png(250)
    crop = CropImage(
        document_id=block.document_id,
        block_id=block.block_id,
        crop_ref=hashlib.sha256(png).hexdigest(),
        page_number=block.page_number,
        bbox=block.bbox,
        width_px=1,
        height_px=1,
        size_bytes=len(png),
    )
    assert store.save_crop_image(crop, png) == "stored"


@pytest.mark.parametrize(("per_page", "per_document"), [(1, 100), (3, 3)], ids=["page", "document"])
def test_caps_count_existing_crop_rows(store: AppStore, tmp_path: Path, per_page: int, per_document: int) -> None:
    """Trang/tài liệu đã có hàng `crop_images` (ví dụ ảnh của khối cũ sau PATCH đổi id khối): job chỉ nhận phần còn
    lại của trần; số hàng ảnh mỗi trang/tài liệu sau job không vượt trần."""
    base = runtime_for(tmp_path, layout_with(FIGURES))
    doc_id = import_doc(store, base, GeoReader(PAGES), auto_confirm=False)
    first_page = [block for block in _figures(store, doc_id) if block.page_number == 1]
    _store_existing(store, first_page[0])
    job = plan_crop_job(store, doc_id, base.files)
    assert job is not None
    _runner(base, FakeRenderer(), crop_max_per_page=per_page, crop_max_per_document=per_document).run(job)
    rows = store.list_crop_images(doc_id)
    assert len(rows) <= per_document
    per_page_rows: dict[int, int] = {}
    for row in rows:
        per_page_rows[row.page_number] = per_page_rows.get(row.page_number, 0) + 1
    assert max(per_page_rows.values()) <= per_page
    waiting = [block for block in _figures(store, doc_id) if block.crop_ref is None]
    assert waiting and all({"crop_pending", "crop_capped"} <= set(block.flags) for block in waiting)
