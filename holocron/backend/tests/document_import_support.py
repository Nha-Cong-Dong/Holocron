"""Hỗ trợ test bộ nhập tài liệu (SPECIFICATION §6.5): `PdfReader` giả và môi trường app với phụ thuộc đã thay.

`FakePdfReader` không mở file nào (chỉ kiểm file tạm có thật); nội dung PDF trong test là byte tự tạo.
"""

from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from httpx import Response

from holocron_core.domain.documents import PageExtraction
from holocron_core.domain.repositories import AppStore
from holocron_core.infrastructure.document_readers.pdf_reader import PdfInputError, PdfInputErrorCode
from holocron_core.infrastructure.document_readers.tiers import failed_page
from holocron_core.infrastructure.storage.original_files import OriginalFileStore
from holocron_core.routers.api import app, get_clock, get_store
from holocron_core.routers.documents import get_import_runtime, get_pdf_reader
from holocron_core.services.document_import import (
    QUEUE_CLOSE_TIMEOUT_S,
    ImportQueue,
    ImportRuntime,
    KeyedLocks,
    ReadJob,
    SynchronousImportQueue,
)
from tests.vault_support import NOW


def pdf_bytes(tag: str) -> bytes:
    """Byte có magic `%PDF-`, khác nhau theo `tag` (khác sha256); chỉ `FakePdfReader` đọc chúng."""
    return f"%PDF-1.4\n% mau tu tao {tag}\n".encode()


def ok_page(document_id: str, page_number: int) -> PageExtraction:
    return PageExtraction.model_validate(
        {
            "document_id": document_id,
            "page_number": page_number,
            "status": "ok",
            "tier_used": "text_layer",
            "text_score": 0.95,
            "lines": [
                {
                    "line_index": 0,
                    "text": f"Trang {page_number}: nội dung mẫu tự viết",
                    "bbox": [0.1, 0.1, 0.9, 0.15],
                    "conf": 0.95,
                    "engine": "text_layer",
                }
            ],
            "cross_check": {"status": "not_applicable", "secondary_engine": None},
        }
    )


@dataclass
class FakePdfReader:
    """`PdfReader` giả: `probe` trả `page_count` hoặc ném `probe_error` (hoặc `probe_exception`); `read_pages` trả trang `ok`.

    Trang trong `failed_pages` thành `failed` (`PAGE_ERROR`); trang có trong `custom_pages` lấy từ hàm dựng tương ứng
    (nhận `document_id`). `read_error` ném ở lần lấy phần tử đầu tiên, như bộ đọc thật khi không mở lại được tài
    liệu; `read_errors` (nếu có) ném lần lượt ở các lần đọc đầu, hết danh sách thì đọc bình thường. `max_pages`
    giới hạn số trang trả về (giả lập bộ đọc trả thiếu trang).
    """

    page_count: int = 2
    probe_error: PdfInputErrorCode | None = None
    probe_exception: Exception | None = None
    read_error: Exception | None = None
    failed_pages: frozenset[int] = frozenset()
    custom_pages: dict[int, Callable[[str], PageExtraction]] = field(default_factory=dict)
    read_errors: list[Exception] = field(default_factory=list)
    max_pages: int | None = None
    probed: list[Path] = field(default_factory=list)
    reads: list[tuple[str, list[int]]] = field(default_factory=list)

    def probe(self, path: Path) -> int:
        assert path.is_file(), "worker chỉ mở file đã ghi xong"
        self.probed.append(path)
        if self.probe_error is not None:
            raise PdfInputError(self.probe_error)
        if self.probe_exception is not None:
            raise self.probe_exception
        return self.page_count

    def read_pages(
        self, path: Path, document_id: str, page_numbers: Sequence[int] | None = None
    ) -> Iterator[PageExtraction]:
        numbers = list(range(1, self.page_count + 1)) if page_numbers is None else list(page_numbers)
        self.reads.append((document_id, numbers))
        if self.read_error is not None:
            raise self.read_error
        if self.read_errors:
            raise self.read_errors.pop(0)
        for number in numbers[: self.max_pages]:
            if number in self.custom_pages:
                yield self.custom_pages[number](document_id)
            elif number in self.failed_pages:
                yield failed_page(document_id, number, "PAGE_ERROR")
            else:
                yield ok_page(document_id, number)


@dataclass
class HoldingQueue:
    """Hàng đợi giữ việc mà không chạy (như việc còn chờ luồng đọc nền); `is_active` đúng với việc đang giữ."""

    held: list[ReadJob] = field(default_factory=list)

    def submit(self, job: ReadJob) -> None:
        self.held.append(job)

    def close(self, timeout_s: float = QUEUE_CLOSE_TIMEOUT_S) -> bool:
        return True

    def is_active(self, document_id: str) -> bool:
        return any(job.document_id == document_id for job in self.held)

    def release(self) -> list[ReadJob]:
        """Bỏ mọi việc đang giữ (như luồng đọc dừng giữa chừng mà không xếp lại) và trả chúng."""
        jobs, self.held = self.held, []
        return jobs


@dataclass
class Clock:
    """Đồng hồ cố định, tiến thêm `step` mỗi lần đọc (để thứ tự "mới nhất trước" tất định)."""

    now: datetime = NOW
    step: timedelta = timedelta(minutes=1)

    def __call__(self) -> datetime:
        current = self.now
        self.now += self.step
        return current


@dataclass
class ImportEnv:
    client: TestClient
    store: AppStore
    files: OriginalFileStore
    queue: SynchronousImportQueue
    reader: FakePdfReader
    runtime: ImportRuntime

    def post(
        self,
        content: bytes,
        *,
        subject_code: str = "ET4010",
        source_kind: str = "lecture_slides",
        chapter_code: str = "ch3",
        filename: str = "vi_xu_ly.pdf",
        content_type: str = "application/pdf",
        **form: str,
    ) -> Response:
        return self.client.post(
            "/v1/documents",
            data={"subject_code": subject_code, "source_kind": source_kind, "chapter_code": chapter_code, **form},
            files={"file": (filename, content, content_type)},
        )


def install(
    store: AppStore,
    files_dir: Path,
    queue: SynchronousImportQueue,
    reader: FakePdfReader,
    *,
    locks: KeyedLocks | None = None,
    runtime_queue: ImportQueue | None = None,
) -> ImportEnv:
    """Thay kho, bộ nhập và đồng hồ của `app`; người gọi PHẢI `app.dependency_overrides.clear()` sau test.

    `runtime_queue` (mặc định `queue`) là hàng đợi runtime thật sự dùng, để test thay bằng hàng đợi lỗi.
    """
    files = OriginalFileStore(files_dir)
    runtime = ImportRuntime(
        files=files,
        queue=queue if runtime_queue is None else runtime_queue,
        locks=KeyedLocks() if locks is None else locks,
    )
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_import_runtime] = lambda: runtime
    app.dependency_overrides[get_pdf_reader] = lambda: reader
    clock = Clock()
    app.dependency_overrides[get_clock] = lambda: clock
    return ImportEnv(client=TestClient(app), store=store, files=files, queue=queue, reader=reader, runtime=runtime)
