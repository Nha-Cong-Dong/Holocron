"""Worker đọc PDF, chạy trong tiến trình con riêng (ADR-008 mục 3).

Khởi chạy bởi `pdf_reader.WorkerSession` bằng `python -P -s -m <module này>`, cwd là thư mục tạm, môi trường
tối thiểu, stdin/stdout UTF-8. Thứ tự: đọc `WorkerRequest` (dòng đầu stdin), nạp PDFium, TỰ áp giới hạn OS (RAM,
CPU, `RLIMIT_FSIZE` = 0 trên Unix; Job Object trên Windows), báo `ready`, mở file, báo `opened`, rồi đọc từng
trang khi nhận `PageRequest`. Mỗi kết quả là một dòng stdout có tiền tố `MESSAGE_PREFIX`; tiến trình cha validate
và giữ wall-clock timeout theo trang.
"""

from __future__ import annotations

import io
import sys
from collections.abc import Callable, Iterable

from pydantic import BaseModel

from ...sandbox.os_limits import apply_limits
from .pdfium_adapter import PdfiumBackend, PdfiumDocument
from .protocol import (
    OpenedMessage,
    OpenFailedMessage,
    PageErrorMessage,
    PageMessage,
    PageRequest,
    ReadyMessage,
    WorkerRequest,
    encode_message,
)

Emit = Callable[[BaseModel], None]

PDF_MAGIC = b"%PDF-"


def _emit_stdout(message: BaseModel) -> None:
    sys.stdout.write(encode_message(message) + "\n")
    sys.stdout.flush()


def _stdin_page_requests() -> Iterable[PageRequest]:
    for line in sys.stdin:
        if line.strip():
            yield PageRequest.model_validate_json(line)


def _read_bounded(path: str, max_bytes: int) -> bytes | None:
    """Đọc file ở chế độ chỉ-đọc, tối đa `max_bytes`; vượt giới hạn hoặc sai magic bytes ⇒ None."""
    with open(path, "rb") as handle:
        data = handle.read(max_bytes + 1)
    if len(data) > max_bytes or not data.startswith(PDF_MAGIC):
        return None
    return data


def _read_one(document: PdfiumDocument, index: int, page_count: int) -> PageMessage | PageErrorMessage:
    if index >= page_count:
        return PageErrorMessage(page_index=index, message="trang ngoài phạm vi tài liệu")
    try:
        return PageMessage(page=document.read_page(index))
    except (RuntimeError, ValueError, LookupError, ArithmeticError, TypeError, MemoryError, OSError) as exc:
        # Lỗi ở một trang không làm hỏng các trang khác (ADR-008 mục 4). Lỗi khác làm worker thoát; tiến trình
        # cha thấy EOF, đánh dấu trang PAGE_ERROR và dựng worker mới.
        return PageErrorMessage(page_index=index, message=type(exc).__name__[:200])


def run(request: WorkerRequest, backend: PdfiumBackend, requests: Iterable[PageRequest], emit: Emit) -> None:
    """Mở tài liệu, báo số trang, rồi đọc tối đa `page_budget` trang theo thứ tự được yêu cầu."""
    data = _read_bounded(request.path, request.config.max_bytes)
    if data is None:
        emit(OpenFailedMessage(code="OPEN_FAILED"))
        return
    opened = backend.open(data)
    if isinstance(opened, str):
        emit(OpenFailedMessage(code=opened))
        return
    with opened:
        page_count = opened.page_count()
        emit(OpenedMessage(page_count=page_count))
        if page_count > request.config.max_pages:
            return
        for served, page_request in enumerate(requests):
            if served >= request.page_budget:
                return
            emit(_read_one(opened, page_request.page_index, page_count))


def main() -> None:
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    if isinstance(sys.stdin, io.TextIOWrapper):
        sys.stdin.reconfigure(encoding="utf-8")
    request = WorkerRequest.model_validate_json(sys.stdin.readline())
    backend = PdfiumBackend.load()  # nạp PDFium trước khi áp giới hạn, như sandbox nạp sẵn module whitelist
    config = request.config
    apply_limits(config.worker_mem_bytes, config.worker_cpu_seconds_per_page * (request.page_budget + 1))
    _emit_stdout(ReadyMessage())
    run(request, backend, _stdin_page_requests(), _emit_stdout)


if __name__ == "__main__":
    main()
