"""Bộ đọc PDF sau `Protocol` `PdfReader` (CLAUDE.md §2.2; ADR-008 mục 2, 3, 4, 8).

File PDF là đầu vào KHÔNG tin cậy: tiến trình này chỉ kiểm kích thước và magic bytes; việc mở và đọc nội dung luôn
chạy trong worker (`_pdf_worker.py`) có giới hạn OS. Tiến trình cha giữ wall-clock timeout theo trang; quá hạn thì
kill worker, đánh dấu trang `failed` (`PAGE_TIMEOUT`) và dựng worker mới cho các trang còn lại. Trang cần OCR
(worker báo `ocr_started`) được chờ theo hạn của tầng 2 (`ocr_page_timeout_s`) thay cho hạn của tầng 1.

`WorkerPdfReader` không có `OcrSettings` thì không có engine OCR: trang cần OCR thành `failed`
(`OCR_ENGINE_MISSING`). Cấu hình OCR của app đọc từ biến môi trường bằng `config.load_ocr_settings`.

Tiến trình cha không có giới hạn OS, nên stdout của worker được đọc theo khối với giới hạn byte mỗi thông điệp
(`PdfReaderConfig.max_message_bytes`) và hàng đợi có kích thước cố định: worker xả dòng quá dài, byte không phải
UTF-8 hay quá nhiều dòng lạ thì bị kill, trang đang đọc thành `PAGE_ERROR`.
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
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Literal, Protocol, Self

from pydantic import ValidationError

from ...domain.documents import FailureReason, PageExtraction
from .config import OcrSettings, PdfReaderConfig
from .protocol import (
    OcrStartedMessage,
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

READ_CHUNK_BYTES = 64 * 1024
# Số dòng tối đa chờ trong hàng đợi giữa luồng đọc stdout và tiến trình cha. Hàng đợi đầy thì luồng đọc ngừng đọc,
# pipe đầy và worker bị chặn khi ghi; bộ nhớ dành cho một worker vì thế không vượt quá cỡ
# (QUEUE_LINES + 2) × max_message_bytes.
QUEUE_LINES = 4
WORKDIR_CLEANUP_S = 5.0

StreamEvent = Literal["eof", "overflow"]

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


class WorkerProtocolError(Exception):
    """Worker vi phạm giao thức: dòng vượt `max_message_bytes`, byte không phải UTF-8, hoặc quá nhiều dòng lạ."""


class PdfReader(Protocol):
    def probe(self, path: Path) -> int:
        """Kiểm giới hạn và mở thử trong worker; trả số trang hoặc ném `PdfInputError`."""
        ...

    def read_pages(
        self, path: Path, document_id: str, page_numbers: Sequence[int] | None = None
    ) -> Iterator[PageExtraction]:
        """Đọc các trang (1-based, mặc định mọi trang) theo thứ tự; trang lỗi thành `failed`, không ném lỗi.

        Lỗi của cả tài liệu (`PdfInputError`, `PdfWorkerError`, `ValueError` khi `page_numbers` sai) chỉ ném ra ở
        lần lấy phần tử đầu tiên, trước khi có trang nào được trả. Sau đó lỗi ở một trang (kể cả khi không dựng lại
        được worker, hay hết hạn tổng của tài liệu) chỉ làm trang đó và các trang chưa đọc thành `failed`
        (ADR-008 mục 4).
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


def pump_lines(
    read: Callable[[int], bytes],
    max_line_bytes: int,
    put: Callable[[bytes | StreamEvent], bool],
) -> None:
    """Tách luồng byte thành dòng (giữ `\\n`) mà không giữ quá `max_line_bytes + READ_CHUNK_BYTES` byte chưa xử lý.

    Dòng dài hơn `max_line_bytes` (hoặc phần chưa có `\\n` đã vượt mức đó) ⇒ gửi `"overflow"` rồi ngừng đọc; hết
    dữ liệu ⇒ gửi `"eof"`. `put` trả False khi phía nhận đã đóng, khi đó hàm dừng ngay.
    """
    buffer = bytearray()
    scanned = 0
    while True:
        chunk = read(READ_CHUNK_BYTES)
        if not chunk:
            put("eof")
            return
        buffer += chunk
        while (newline := buffer.find(b"\n", scanned)) >= 0:
            line = bytes(buffer[: newline + 1])
            del buffer[: newline + 1]
            scanned = 0
            if len(line) > max_line_bytes:
                put("overflow")
                return
            if not put(line):
                return
        scanned = len(buffer)
        if len(buffer) > max_line_bytes:
            put("overflow")
            return


def decode_line(raw: bytes) -> str:
    """Giải mã một dòng của worker bằng UTF-8 strict; byte sai ném `WorkerProtocolError` (không thay bằng U+FFFD)."""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WorkerProtocolError("worker ghi byte không phải UTF-8") from exc


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
        self._max_message_bytes = request.config.max_message_bytes
        self._workdir = tempfile.TemporaryDirectory(prefix="holocron_pdf_")
        # Pipe ở chế độ nhị phân: tiến trình cha tự giới hạn độ dài dòng và tự giải mã UTF-8 (tường minh, strict).
        self.process = subprocess.Popen(
            [sys.executable, "-P", "-s", "-m", WORKER_MODULE],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            cwd=self._workdir.name,
            env=_worker_env(),
            close_fds=True,
        )
        self._lines: queue.Queue[bytes | StreamEvent] = queue.Queue(maxsize=QUEUE_LINES)
        self._closing = threading.Event()
        self._pump = threading.Thread(target=self._read_stdout, daemon=True)
        self._pump.start()
        self._send(request.model_dump_json())

    def _put(self, item: bytes | StreamEvent) -> bool:
        """Đưa một dòng vào hàng đợi, chờ khi hàng đợi đầy; trả False khi session đang đóng."""
        while not self._closing.is_set():
            try:
                self._lines.put(item, timeout=0.1)
            except queue.Full:
                continue
            return True
        return False

    def _read_stdout(self) -> None:
        stdout = self.process.stdout
        if stdout is None:
            self._put("eof")
            return
        descriptor = stdout.fileno()

        def read(size: int) -> bytes:
            try:
                return os.read(descriptor, size)
            except OSError:
                return b""  # pipe đã bị đóng trong lúc `close`

        pump_lines(read, self._max_message_bytes, self._put)

    def _send(self, line: str) -> None:
        stdin = self.process.stdin
        if stdin is None:
            return
        try:
            stdin.write((line + "\n").encode("utf-8"))
            stdin.flush()
        except OSError:
            # Worker đã thoát; lần nhận tiếp theo sẽ thấy EOF.
            return

    def request_page(self, index: int) -> None:
        self._send(PageRequest(page_index=index).model_dump_json())

    def receive(self, timeout_s: float) -> WorkerMessage | None:
        """Thông điệp kế tiếp; `None` khi worker đã đóng stdout. Quá hạn ném `TimeoutError`.

        Dòng sai dạng ném `pydantic.ValidationError`. Dòng không có tiền tố bị bỏ qua, nhưng tổng số byte bị bỏ qua
        trong một lần nhận không được vượt `max_message_bytes`. Dòng quá dài, byte không phải UTF-8 hoặc quá nhiều
        dòng lạ ném `WorkerProtocolError`.
        """
        deadline = time.monotonic() + timeout_s
        foreign_bytes = 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            try:
                item = self._lines.get(timeout=remaining)
            except queue.Empty as exc:
                raise TimeoutError from exc
            if isinstance(item, str):
                if item == "eof":
                    return None
                raise WorkerProtocolError("thông điệp của worker vượt giới hạn byte")
            message = decode_message(decode_line(item))
            if message is not None:
                return message
            foreign_bytes += len(item)
            if foreign_bytes > self._max_message_bytes:
                raise WorkerProtocolError("worker ghi quá nhiều dòng không thuộc giao thức")

    def kill(self) -> None:
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait()

    def close(self) -> None:
        self._closing.set()
        stdin = self.process.stdin
        if stdin is not None and not stdin.closed:
            with contextlib.suppress(OSError):
                stdin.close()
        self.kill()
        self._pump.join(timeout=5)
        if self.process.stdout is not None:
            self.process.stdout.close()
        self._remove_workdir()

    def _remove_workdir(self) -> None:
        """Xoá thư mục tạm, thử lại trong thời gian ngắn nếu Windows báo thư mục còn bị giữ.

        Trên Windows, `python.exe` của venv là trình khởi chạy: kill nó xong thì tiến trình Python thật (cwd ở thư
        mục tạm) có thể cần thêm vài chục ms mới thoát hẳn, nhất là khi đang bị chặn lúc ghi vào pipe.
        """
        deadline = time.monotonic() + WORKDIR_CLEANUP_S
        while True:
            try:
                self._workdir.cleanup()
            except PermissionError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.02)
                continue
            return

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class WorkerPdfReader:
    """`PdfReader` tầng 1–2: mỗi tài liệu đọc qua worker có giới hạn; trang quá hạn được thay worker mới.

    `ocr` là None ⇒ không có engine OCR (chỉ tầng 1).
    """

    def __init__(self, config: PdfReaderConfig | None = None, ocr: OcrSettings | None = None) -> None:
        self.config = config or PdfReaderConfig()
        self.ocr = ocr

    def _start(self, path: Path, page_budget: int) -> tuple[WorkerSession, int]:
        request = WorkerRequest(path=str(path.resolve()), page_budget=page_budget, config=self.config, ocr=self.ocr)
        session = WorkerSession(request)
        try:
            return session, self._open(session)
        except BaseException:
            session.close()
            raise

    def _open(self, session: WorkerSession) -> int:
        try:
            ready = session.receive(self.config.worker_startup_timeout_s)
        except (TimeoutError, ValidationError, WorkerProtocolError) as exc:
            raise PdfWorkerError("worker đọc PDF không khởi động được") from exc
        if not isinstance(ready, ReadyMessage):
            raise PdfWorkerError("worker đọc PDF không khởi động được")
        try:
            opened = session.receive(self.config.open_timeout_s)
        except TimeoutError as exc:
            raise PdfInputError("OPEN_TIMEOUT") from exc
        except (ValidationError, WorkerProtocolError) as exc:
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

    def _next_page(
        self,
        session: WorkerSession,
        index: int,
        document_id: str,
        timeout_s: float | None = None,
        ocr_timeout_s: float | None = None,
    ) -> tuple[PageExtraction, bool]:
        """Đọc một trang; phần tử thứ hai là False khi worker đã bị kill và cần dựng worker mới.

        `timeout_s` mặc định là `text_layer_page_timeout_s`; sau thông điệp `ocr_started` của đúng trang này, hạn
        chờ kết quả là `ocr_timeout_s` (mặc định `ocr_page_timeout_s`). `read_pages` truyền giá trị nhỏ hơn khi hạn
        tổng của tài liệu sắp hết.
        """
        page_number = index + 1
        session.request_page(index)
        failure: FailureReason = "PAGE_ERROR"
        try:
            message = session.receive(self.config.text_layer_page_timeout_s if timeout_s is None else timeout_s)
            if isinstance(message, OcrStartedMessage) and message.page_index == index:
                message = session.receive(self.config.ocr_page_timeout_s if ocr_timeout_s is None else ocr_timeout_s)
        except TimeoutError:
            message, failure = None, "PAGE_TIMEOUT"
        except (ValidationError, WorkerProtocolError):
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
        deadline = time.monotonic() + self.config.document_timeout_s
        first, page_count = self._start(path, page_budget=budget)
        session: WorkerSession | None = first
        try:
            pending = list(range(page_count)) if requested is None else requested
            if any(index >= page_count for index in pending):
                raise ValueError(f"tài liệu chỉ có {page_count} trang")
            while pending:
                index = pending.pop(0)
                remaining = deadline - time.monotonic()
                healthy = True
                if remaining <= 0:
                    page = failed_page(document_id, index + 1, "PAGE_TIMEOUT")
                elif session is None:
                    page = failed_page(document_id, index + 1, "PAGE_ERROR")
                else:
                    page_timeout = min(self.config.text_layer_page_timeout_s, remaining)
                    ocr_timeout = min(self.config.ocr_page_timeout_s, remaining)
                    page, healthy = self._next_page(session, index, document_id, page_timeout, ocr_timeout)
                yielded_at = time.monotonic()
                yield page
                deadline += time.monotonic() - yielded_at  # thời gian nơi gọi xử lý trang không tính vào hạn
                if not healthy and session is not None:
                    session.close()
                    session = self._restart(path, len(pending), deadline) if pending else None
        finally:
            if session is not None:
                session.close()

    def _restart(self, path: Path, page_budget: int, deadline: float) -> WorkerSession | None:
        """Dựng worker mới cho các trang còn lại; hết hạn tổng hoặc không dựng được thì trả None.

        Khi đó các trang còn lại thành `failed` thay vì ném lỗi giữa chừng (ADR-008 mục 4).
        """
        if deadline - time.monotonic() <= 0:
            return None
        try:
            session, _ = self._start(path, page_budget=page_budget)
        except (PdfInputError, PdfWorkerError):
            return None
        return session
