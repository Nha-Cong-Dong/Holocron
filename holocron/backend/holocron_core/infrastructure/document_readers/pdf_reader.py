"""Bộ đọc PDF sau `Protocol` `PdfReader` (CLAUDE.md §2.2; ADR-008 mục 2, 3, 4).

File PDF là đầu vào KHÔNG tin cậy: tiến trình này chỉ kiểm kích thước và magic bytes; việc mở và đọc nội dung luôn
chạy trong worker (`_pdf_worker.py`) có giới hạn OS. Tiến trình cha giữ wall-clock timeout theo trang; quá hạn thì
kill worker, đánh dấu trang `failed` (`PAGE_TIMEOUT`) và dựng worker mới cho các trang còn lại.
"""

from __future__ import annotations

import contextlib
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Literal, Protocol, Self

from pydantic import ValidationError

from ...domain.documents import FailureReason, PageExtraction
from .config import PdfReaderConfig
from .protocol import (
    OpenedMessage,
    OpenFailedMessage,
    PageErrorMessage,
    PageMessage,
    PageRequest,
    ReadyMessage,
    WorkerMessage,
    WorkerRequest,
    decode_message,
)
from .tiers import assemble_text_layer_page, failed_page

PDF_MAGIC = b"%PDF-"
WORKER_MODULE = "holocron_core.infrastructure.document_readers._pdf_worker"
_PACKAGE_PARENT = Path(__file__).resolve().parents[3]

PdfInputErrorCode = Literal[
    "EMPTY",
    "TOO_LARGE",
    "NOT_PDF",
    "PASSWORD_REQUIRED",
    "OPEN_FAILED",
    "OPEN_TIMEOUT",
    "TOO_MANY_PAGES",
]

INPUT_ERROR_MESSAGES: dict[PdfInputErrorCode, str] = {
    "EMPTY": "File PDF rỗng",
    "TOO_LARGE": "File PDF vượt giới hạn dung lượng",
    "NOT_PDF": "File không phải PDF (thiếu magic bytes %PDF-)",
    "PASSWORD_REQUIRED": "PDF có mật khẩu",
    "OPEN_FAILED": "Không mở được tài liệu",
    "OPEN_TIMEOUT": "Không mở được tài liệu (quá thời gian)",
    "TOO_MANY_PAGES": "PDF vượt giới hạn số trang",
}


class PdfInputError(Exception):
    """Tài liệu bị từ chối cả file (ADR-008 mục 4); lỗi ở một trang không dùng ngoại lệ này."""

    def __init__(self, code: PdfInputErrorCode) -> None:
        super().__init__(INPUT_ERROR_MESSAGES[code])
        self.code: PdfInputErrorCode = code


class PdfWorkerError(RuntimeError):
    """Worker không khởi động được (lỗi môi trường, không phải lỗi của file)."""


class PdfReader(Protocol):
    def probe(self, path: Path) -> int:
        """Kiểm giới hạn và mở thử trong worker; trả số trang hoặc ném `PdfInputError`."""
        ...

    def read_pages(
        self, path: Path, document_id: str, page_numbers: Sequence[int] | None = None
    ) -> Iterator[PageExtraction]:
        """Đọc các trang (1-based, mặc định mọi trang) theo thứ tự; trang lỗi thành `failed`, không ném lỗi.

        Lỗi của cả tài liệu (`PdfInputError`) ném ra ở lần lấy phần tử đầu tiên.
        """
        ...


def check_pdf_input(path: Path, config: PdfReaderConfig) -> int:
    """Kiểm kích thước và magic bytes mà không phân tích nội dung; trả kích thước (byte)."""
    size = path.stat().st_size
    if size == 0:
        raise PdfInputError("EMPTY")
    if size > config.max_bytes:
        raise PdfInputError("TOO_LARGE")
    with path.open("rb") as handle:
        if handle.read(len(PDF_MAGIC)) != PDF_MAGIC:
            raise PdfInputError("NOT_PDF")
    return size


def _worker_env() -> dict[str, str]:
    env = {
        "PYTHONHASHSEED": "0",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "PYTHONPATH": str(_PACKAGE_PARENT),
    }
    if sys.platform == "win32":
        env["SYSTEMROOT"] = os.environ.get("SYSTEMROOT", r"C:\Windows")  # Windows không khởi động được Python nếu thiếu
    return env


class WorkerSession:
    """Một tiến trình worker. Luồng nền đọc stdout để tiến trình cha giữ được timeout trên cả Windows lẫn Unix."""

    def __init__(self, request: WorkerRequest) -> None:
        self._workdir = tempfile.TemporaryDirectory(prefix="holocron_pdf_")
        self.process = subprocess.Popen(
            [sys.executable, "-P", "-s", "-m", WORKER_MODULE],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            cwd=self._workdir.name,
            env=_worker_env(),
            close_fds=True,
        )
        self._lines: queue.Queue[str | None] = queue.Queue()
        self._pump = threading.Thread(target=self._read_stdout, daemon=True)
        self._pump.start()
        self._send(request.model_dump_json())

    def _read_stdout(self) -> None:
        stdout = self.process.stdout
        if stdout is not None:
            for line in stdout:
                self._lines.put(line)
        self._lines.put(None)

    def _send(self, line: str) -> None:
        stdin = self.process.stdin
        if stdin is None:
            return
        try:
            stdin.write(line + "\n")
            stdin.flush()
        except OSError:
            # Worker đã thoát; lần nhận tiếp theo sẽ thấy EOF.
            return

    def request_page(self, index: int) -> None:
        self._send(PageRequest(page_index=index).model_dump_json())

    def receive(self, timeout_s: float) -> WorkerMessage | None:
        """Thông điệp kế tiếp; `None` khi worker đã đóng stdout. Quá hạn ném `TimeoutError`.

        Dòng sai dạng ném `pydantic.ValidationError`; dòng không có tiền tố bị bỏ qua.
        """
        deadline = time.monotonic() + timeout_s
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            try:
                line = self._lines.get(timeout=remaining)
            except queue.Empty as exc:
                raise TimeoutError from exc
            if line is None:
                return None
            message = decode_message(line)
            if message is not None:
                return message

    def kill(self) -> None:
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait()

    def close(self) -> None:
        stdin = self.process.stdin
        if stdin is not None and not stdin.closed:
            with contextlib.suppress(OSError):
                stdin.close()
        self.kill()
        self._pump.join(timeout=5)
        if self.process.stdout is not None:
            self.process.stdout.close()
        self._workdir.cleanup()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class WorkerPdfReader:
    """`PdfReader` tầng 1: mỗi tài liệu đọc qua worker có giới hạn; trang quá hạn được thay worker mới."""

    def __init__(self, config: PdfReaderConfig | None = None) -> None:
        self.config = config or PdfReaderConfig()

    def _start(self, path: Path, page_budget: int) -> tuple[WorkerSession, int]:
        request = WorkerRequest(path=str(path.resolve()), page_budget=page_budget, config=self.config)
        session = WorkerSession(request)
        try:
            return session, self._open(session)
        except BaseException:
            session.close()
            raise

    def _open(self, session: WorkerSession) -> int:
        try:
            ready = session.receive(self.config.worker_startup_timeout_s)
        except (TimeoutError, ValidationError) as exc:
            raise PdfWorkerError("worker đọc PDF không khởi động được") from exc
        if not isinstance(ready, ReadyMessage):
            raise PdfWorkerError("worker đọc PDF không khởi động được")
        try:
            opened = session.receive(self.config.open_timeout_s)
        except TimeoutError as exc:
            raise PdfInputError("OPEN_TIMEOUT") from exc
        except ValidationError as exc:
            raise PdfInputError("OPEN_FAILED") from exc
        if isinstance(opened, OpenFailedMessage):
            raise PdfInputError(opened.code)
        if not isinstance(opened, OpenedMessage) or opened.page_count < 1:
            raise PdfInputError("OPEN_FAILED")
        if opened.page_count > self.config.max_pages:
            raise PdfInputError("TOO_MANY_PAGES")
        return opened.page_count

    def probe(self, path: Path) -> int:
        check_pdf_input(path, self.config)
        session, page_count = self._start(path, page_budget=0)
        session.close()
        return page_count

    def _next_page(self, session: WorkerSession, index: int, document_id: str) -> tuple[PageExtraction, bool]:
        """Đọc một trang; phần tử thứ hai là False khi worker đã bị kill và cần dựng worker mới."""
        page_number = index + 1
        session.request_page(index)
        failure: FailureReason = "PAGE_ERROR"
        try:
            message = session.receive(self.config.text_layer_page_timeout_s)
        except TimeoutError:
            message, failure = None, "PAGE_TIMEOUT"
        except ValidationError:
            message = None
        if isinstance(message, PageMessage) and message.page.page_index == index:
            try:
                return assemble_text_layer_page(message.page, document_id, self.config), True
            except ValidationError:
                return failed_page(document_id, page_number, "PAGE_ERROR"), True
        if isinstance(message, PageErrorMessage) and message.page_index == index:
            return failed_page(document_id, page_number, "PAGE_ERROR"), True
        session.kill()
        return failed_page(document_id, page_number, failure), False

    def read_pages(
        self, path: Path, document_id: str, page_numbers: Sequence[int] | None = None
    ) -> Iterator[PageExtraction]:
        check_pdf_input(path, self.config)
        if page_numbers is not None and any(n < 1 for n in page_numbers):
            raise ValueError("page_numbers bắt đầu từ 1")
        requested = None if page_numbers is None else [n - 1 for n in page_numbers]
        budget = self.config.max_pages if requested is None else len(requested)
        session, page_count = self._start(path, page_budget=budget)
        try:
            pending = list(range(page_count)) if requested is None else requested
            if any(index >= page_count for index in pending):
                raise ValueError(f"tài liệu chỉ có {page_count} trang")
            while pending:
                index = pending.pop(0)
                page, healthy = self._next_page(session, index, document_id)
                yield page
                if not healthy and pending:
                    session.close()
                    session, _ = self._start(path, page_budget=len(pending))
        finally:
            session.close()
