"""Job cắt ảnh nền cho khối cần ảnh (ADR-010 mục 9, 13 "Job cắt ảnh nền", 19; SPECIFICATION §5.7, §5.15; XCROP).

Khối `table`/`figure`/`formula` có `bbox` mang `crop_pending` (không `crop_failed`/`crop_capped`, chưa có hàng
`crop_images`; `document_layout.blocks_needing_crop`) nhận ảnh cắt bằng job nền:

- **Tạo job** SAU commit của bước kết thúc lần đọc (`_finish`), SAU commit của đường dựng lại `PATCH …/lines`, và từ
  quét khởi động (`schedule_pending_crops`, KHÔNG dựa `requeue_pending`). Khoá thế hệ của job =
  `(document_id, import_generation, layout_settings_sha256)` đọc lúc tạo job. Người tạo job không chờ job.
- **Chạy:** mỗi lô ≤ `crop_max_regions_per_request` vùng được render NGOÀI khoá (`RegionRenderer`), rồi ghi DƯỚI KHOÁ
  TÀI LIỆU theo lô (không giữ khoá suốt job). Mỗi lô đọc lại tài liệu và khối từ kho: tài liệu mất ⇒ job huỷ; khác
  thế hệ ⇒ job dừng, không ghi gì; chỉ ghi cho khối CÒN tồn tại, CÙNG `block_id`, cùng trang, loại và hộp, CÒN
  `crop_pending`. File PNG chỉ được kho ghi trong `save_crop_image` (sau các kiểm trên), nên khối bị bỏ qua không để
  lại file thừa.
- **Kết quả:** thành công ⇒ `save_crop_image` ghi hàng và gỡ `crop_pending`/`crop_failed`/`crop_capped` trong CÙNG giao
  dịch; lỗi render ⇒ thêm `crop_failed`; vượt trần số vùng mỗi trang/tài liệu hay hết ngân sách
  `effective_crop_budget_s` của TỪNG job ⇒ thêm `crop_capped`. Không ném, không tự thử lại.
- Job KHÔNG đổi trạng thái xác nhận trang, `eligible` hay trạng thái tài liệu (cờ ảnh cắt không chặn tự xác nhận;
  người dùng 2026-11-03); tài liệu đã đóng vẫn nhận ảnh.
- Số job chạy cùng lúc trên toàn app ≤ `crop_max_parallel_jobs` (`BackgroundCropJobs`: đúng chừng đó luồng).

Hàng đợi job là thuộc tính của `ImportRuntime` (không global state; CLAUDE.md §2.2).
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

from pydantic import ValidationError

from ..domain.blocks import Block, CropImage
from ..domain.repositories import BlockFlagUpdate, DocumentRepository, ImportedDocument, StorageError
from ..infrastructure.document_readers.config import PdfReaderConfig
from ..infrastructure.document_readers.pdf_reader import CropOutcome, RegionRenderer, RenderedCrop
from ..infrastructure.document_readers.protocol import CropRegion
from .document_layout import blocks_needing_crop

__all__ = [
    "BackgroundCropJobs",
    "CropJob",
    "CropJobKey",
    "CropJobResult",
    "CropJobs",
    "CropRunner",
    "DocumentLocks",
    "OriginalPaths",
    "SynchronousCropJobs",
    "plan_crop_job",
    "schedule_pending_crops",
]

_LOGGER = logging.getLogger(__name__)

_StoreStatus = Literal["stored", "store_capped", "crop_failed", "crop_capped"]


def _never() -> bool:
    return False


# Thời gian chờ tối đa khi tắt app cho các luồng cắt ảnh (đọc lúc gọi).
CROP_CLOSE_TIMEOUT_S = 10.0


class DocumentLocks(Protocol):
    """Khoá theo `document_id` (`document_import.KeyedLocks`): CÙNG khoá với request sửa, xác nhận, xoá và bước kết
    thúc lần đọc."""

    def hold(self, key: str) -> AbstractContextManager[None]: ...


class OriginalPaths(Protocol):
    """Đường dẫn file gốc theo sha256 (`OriginalFileStore.path_for`)."""

    def path_for(self, sha256: str) -> Path: ...


@dataclass(frozen=True)
class CropJobKey:
    """Khoá thế hệ của job (ADR-010 mục 13): đọc lúc tạo job, so lại ở mỗi lô."""

    document_id: str
    import_generation: int
    layout_settings_sha256: str


@dataclass(frozen=True)
class CropJob:
    key: CropJobKey
    path: Path
    store: DocumentRepository


@dataclass
class CropJobResult:
    """Kết quả một lần chạy (dùng cho log và test): số khối ghi ảnh, thêm `crop_failed`, thêm `crop_capped`, bỏ qua
    (khối đổi/xoá), và lý do dừng sớm (`cancelled` = tài liệu mất hay khác thế hệ; `stopped` = app đang tắt)."""

    stored: int = 0
    failed: int = 0
    capped: int = 0
    skipped: int = 0
    cancelled: bool = False
    stopped: bool = False


def _key_of(document: ImportedDocument) -> CropJobKey | None:
    if document.layout_version is None or document.layout_settings_sha256 is None:
        return None
    return CropJobKey(document.meta.doc_id, document.import_generation, document.layout_settings_sha256)


def _targets(store: DocumentRepository, document_id: str) -> list[Block]:
    cropped = {row.block_id for row in store.list_crop_images(document_id)}
    return blocks_needing_crop(store.list_blocks(document_id), cropped)


def plan_crop_job(store: DocumentRepository, document_id: str, files: OriginalPaths) -> CropJob | None:
    """Job cho tài liệu khi nó có khối lưu và có khối cần ảnh; ngược lại `None` (không có việc)."""
    document = store.get_imported_document(document_id)
    if document is None:
        return None
    key = _key_of(document)
    if key is None or not _targets(store, document_id):
        return None
    return CropJob(key=key, path=files.path_for(document.meta.sha256), store=store)


def _same_generation(store: DocumentRepository, key: CropJobKey) -> bool:
    document = store.get_imported_document(key.document_id)
    return document is not None and _key_of(document) == key


def _still_waiting(planned: Block, current: Block | None) -> bool:
    """Khối lúc ghi vẫn là đúng vùng đã render và vẫn chờ ảnh (có thể đã mang thêm `crop_failed`/`crop_capped`)."""
    return (
        current is not None
        and current.page_number == planned.page_number
        and current.type == planned.type
        and current.bbox == planned.bbox
        and "crop_pending" in current.flags
    )


@dataclass(frozen=True)
class CropRunner:
    """Chạy MỘT job (đồng bộ, trong luồng của người gọi). `monotonic` đo ngân sách của TỪNG job (test tiêm đồng hồ)."""

    renderer: RegionRenderer
    locks: DocumentLocks
    config: PdfReaderConfig = field(default_factory=PdfReaderConfig)
    monotonic: Callable[[], float] = time.monotonic

    def run(self, job: CropJob, should_stop: Callable[[], bool] = _never) -> CropJobResult:
        result = CropJobResult()
        store, key = job.store, job.key
        if not _same_generation(store, key):
            result.cancelled = True
            return result
        deadline = self.monotonic() + self.config.effective_crop_budget_s
        admitted, over_cap = self._admit(_targets(store, key.document_id))
        size = self.config.crop_max_regions_per_request
        batches = [admitted[start : start + size] for start in range(0, len(admitted), size)]
        for position, batch in enumerate(batches):
            if should_stop():
                result.stopped = True
                return result
            if self.monotonic() >= deadline:
                over_cap.extend(block for rest in batches[position:] for block in rest)
                break
            if not _same_generation(store, key):
                result.cancelled = True
                return result
            regions = [
                CropRegion(region_id=index, page_index=block.page_number - 1, bbox=block.bbox)
                for index, block in enumerate(batch)
                if block.bbox is not None
            ]
            outcomes = self.renderer.render_regions(job.path, regions, max_edge_px=self.config.crop_max_edge_px)
            if not self._write(job, list(zip(batch, outcomes, strict=True)), result):
                return result
        for start in range(0, len(over_cap), size):
            capped = [(block, None) for block in over_cap[start : start + size]]
            if not self._write(job, capped, result):
                return result
        return result

    def _admit(self, targets: Sequence[Block]) -> tuple[list[Block], list[Block]]:
        """Trần số vùng mỗi trang (`crop_max_per_page`) và mỗi job (`crop_max_per_document`); vượt ⇒ `crop_capped`."""
        per_page: dict[int, int] = {}
        admitted: list[Block] = []
        over: list[Block] = []
        for block in targets:
            count = per_page.get(block.page_number, 0)
            if count >= self.config.crop_max_per_page or len(admitted) >= self.config.crop_max_per_document:
                over.append(block)
                continue
            per_page[block.page_number] = count + 1
            admitted.append(block)
        return admitted, over

    def _write(self, job: CropJob, items: Sequence[tuple[Block, CropOutcome | None]], result: CropJobResult) -> bool:
        """Ghi một lô DƯỚI KHOÁ TÀI LIỆU; `None` ở vị trí kết quả ⇒ `crop_capped`. Trả False khi tài liệu mất hoặc
        khác thế hệ (job dừng, lô không ghi gì)."""
        store, key = job.store, job.key
        with self.locks.hold(key.document_id):
            if not _same_generation(store, key):
                result.cancelled = True
                return False
            current = {block.block_id: block for block in store.list_blocks(key.document_id)}
            flags: list[BlockFlagUpdate] = []
            for planned, outcome in items:
                if not _still_waiting(planned, current.get(planned.block_id)):
                    result.skipped += 1
                    continue
                status = self._store_one(store, planned, outcome)
                if status == "stored":
                    result.stored += 1
                elif status == "store_capped":
                    result.capped += 1
                else:
                    if status == "crop_capped":
                        result.capped += 1
                    else:
                        result.failed += 1
                    flags.append(BlockFlagUpdate(block_id=planned.block_id, add=[status]))
            if flags:
                store.update_block_flags(key.document_id, flags)
        return True

    @staticmethod
    def _store_one(store: DocumentRepository, block: Block, outcome: CropOutcome | None) -> _StoreStatus:
        """Ghi ảnh của một khối. Trả `"stored"`; `"store_capped"` (kho không có chỗ hay vượt trần byte của kho
        in-memory: kho đã tự thêm `crop_capped`); hoặc cờ người gọi cần thêm (`crop_failed`/`crop_capped`)."""
        if outcome is None:
            return "crop_capped"
        if not isinstance(outcome, RenderedCrop):
            return "crop_capped" if outcome.code == "capped" else "crop_failed"
        if block.bbox is None:
            return "crop_failed"
        try:
            crop = CropImage(
                document_id=block.document_id,
                block_id=block.block_id,
                crop_ref=outcome.sha256,
                page_number=block.page_number,
                bbox=block.bbox,
                width_px=outcome.width_px,
                height_px=outcome.height_px,
                size_bytes=len(outcome.png),
            )
        except ValidationError:
            return "crop_failed"
        return "stored" if store.save_crop_image(crop, outcome.png) == "stored" else "store_capped"


class CropJobs(Protocol):
    """Nơi xếp job cắt ảnh của app (`ImportRuntime.crops`)."""

    def schedule(self, store: DocumentRepository, document_id: str) -> bool:
        """Tạo job cho tài liệu (khoá thế hệ đọc lúc gọi) và xếp chạy; không chờ job. `False` khi không có việc."""
        ...

    def close(self, timeout_s: float = CROP_CLOSE_TIMEOUT_S) -> bool:
        """Ngừng nhận job; job đang chạy dừng trước lô kế tiếp. `True` khi không còn luồng nào chạy."""
        ...


def _run_logged(runner: CropRunner, job: CropJob, should_stop: Callable[[], bool]) -> CropJobResult | None:
    """Chạy job mà không để lỗi thoát ra luồng; log chỉ ghi id và tên lớp lỗi. Lỗi kho ⇒ khối giữ `crop_pending`
    (quét khởi động sau xếp lại)."""
    try:
        result = runner.run(job, should_stop)
    except StorageError as exc:
        _LOGGER.warning("job cắt ảnh của tài liệu %s gặp lỗi kho (%s)", job.key.document_id, type(exc).__name__)
        return None
    except Exception:  # job nền không được làm chết luồng cắt ảnh của app
        if should_stop():
            _LOGGER.info("dừng job cắt ảnh của tài liệu %s vì app đang tắt", job.key.document_id)
            return None
        _LOGGER.exception("job cắt ảnh của tài liệu %s lỗi", job.key.document_id)
        return None
    _LOGGER.info(
        "job cắt ảnh của tài liệu %s: %d ảnh, %d lỗi, %d chạm trần, %d bỏ qua",
        job.key.document_id,
        result.stored,
        result.failed,
        result.capped,
        result.skipped,
    )
    return result


class SynchronousCropJobs:
    """Chạy job ngay trong `schedule` (test, script); `results` giữ kết quả theo thứ tự."""

    def __init__(self, runner: CropRunner, files: OriginalPaths) -> None:
        self._runner = runner
        self._files = files
        self.results: list[CropJobResult | None] = []

    def schedule(self, store: DocumentRepository, document_id: str) -> bool:
        job = plan_crop_job(store, document_id, self._files)
        if job is None:
            return False
        self.results.append(_run_logged(self._runner, job, _never))
        return True

    def close(self, timeout_s: float = CROP_CLOSE_TIMEOUT_S) -> bool:
        return True


class BackgroundCropJobs:
    """Hàng đợi FIFO trong tiến trình; đúng `crop_max_parallel_jobs` luồng nền nên số job chạy cùng lúc không vượt
    trần. Job đã xếp mà chưa chạy cùng khoá thế hệ chỉ giữ một bản (job đọc khối lúc chạy)."""

    def __init__(self, runner: CropRunner, files: OriginalPaths) -> None:
        self._runner = runner
        self._files = files
        self._jobs: queue.Queue[CropJob | None] = queue.Queue()
        self._stop = threading.Event()
        self._idle = threading.Condition()
        self._queued: set[CropJobKey] = set()
        self._unfinished = 0
        self._threads = [
            threading.Thread(target=self._loop, name=f"holocron-crop-{index}", daemon=True)
            for index in range(runner.config.crop_max_parallel_jobs)
        ]
        for thread in self._threads:
            thread.start()

    def schedule(self, store: DocumentRepository, document_id: str) -> bool:
        if self._stop.is_set():
            return False
        job = plan_crop_job(store, document_id, self._files)
        if job is None:
            return False
        with self._idle:
            if self._stop.is_set() or job.key in self._queued:
                return False
            self._queued.add(job.key)
            self._unfinished += 1
        self._jobs.put(job)
        return True

    def _loop(self) -> None:
        while True:
            job = self._jobs.get()
            if job is None:
                return
            with self._idle:
                self._queued.discard(job.key)
            try:
                if not self._stop.is_set():
                    _run_logged(self._runner, job, self._stop.is_set)
            finally:
                with self._idle:
                    self._unfinished -= 1
                    self._idle.notify_all()

    def wait_until_idle(self, timeout_s: float) -> bool:
        """Chờ tới khi mọi job đã nhận xong; `False` khi hết `timeout_s`."""
        with self._idle:
            return self._idle.wait_for(lambda: self._unfinished == 0, timeout=timeout_s)

    def close(self, timeout_s: float = CROP_CLOSE_TIMEOUT_S) -> bool:
        self._stop.set()
        for _ in self._threads:
            self._jobs.put(None)
        deadline = time.monotonic() + timeout_s
        for thread in self._threads:
            thread.join(timeout=max(0.0, deadline - time.monotonic()))
        return not any(thread.is_alive() for thread in self._threads)


def schedule_pending_crops(store: DocumentRepository, jobs: CropJobs) -> list[str]:
    """Quét khởi động (ADR-010 mục 13; KHÔNG dựa `requeue_pending`): xếp job cho mọi tài liệu có khối mang
    `crop_pending`, không `crop_failed`/`crop_capped`, chưa có hàng `crop_images`. Lỗi ở một tài liệu (lỗi kho hay lỗi
    dữ liệu) chỉ ghi log, không chặn tài liệu khác hay lần khởi động. Trả id các tài liệu đã xếp job."""
    scheduled: list[str] = []
    for document in reversed(store.list_imported_documents()):
        document_id = document.meta.doc_id
        try:
            if _key_of(document) is None:
                continue
            if jobs.schedule(store, document_id):
                scheduled.append(document_id)
        except StorageError as exc:
            _LOGGER.warning("chưa xếp được job cắt ảnh của tài liệu %s (%s)", document_id, type(exc).__name__)
        except Exception:  # một tài liệu lỗi không được chặn quét các tài liệu khác hay làm hỏng lần khởi động
            _LOGGER.exception("lỗi khi xếp job cắt ảnh của tài liệu %s; bỏ qua tài liệu này", document_id)
    return scheduled
