"""Worker đọc PDF giả, cư xử xấu theo yêu cầu; chạy bằng `python -m tests.fake_pdf_worker` thay worker thật.

Cách cư xử lấy từ tên file (phần trước đuôi) trong `WorkerRequest.path`. Worker giả làm đúng giao thức khi khởi
động (`ready`, `opened` với 2 trang) và khi đọc trang 1; chỉ trang 0 bị đọc sai:

- `long_line`: xả tới 256 MiB không có `\\n`.
- `huge_json`: một thông điệp có tiền tố, JSON hợp lệ về cú pháp nhưng dài tới 256 MiB.
- `noise`: xả vô hạn các dòng ngắn không thuộc giao thức.
- `bad_utf8`: một dòng có byte không phải UTF-8, rồi đứng chờ (không thoát, không ghi thêm).

Sau khi cư xử xấu, worker đứng chờ stdin để tiến trình cha phải tự phát hiện lỗi và kill nó.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from holocron_core.infrastructure.document_readers.protocol import (
    OpenedMessage,
    PageMessage,
    RawPdfPage,
    RawTextLine,
    RawWord,
    ReadyMessage,
    encode_message,
)

CHUNK = 1024 * 1024
TOTAL = 256 * CHUNK
GOOD_TEXT = "The scheduler gives each ready process the processor for one time quantum in turn."


def _write(data: bytes) -> None:
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


def _emit(message: OpenedMessage | ReadyMessage | PageMessage) -> None:
    _write((encode_message(message) + "\n").encode("utf-8"))


def _good_page(index: int) -> PageMessage:
    words = [RawWord(text=word, bbox=(0.1, 0.1, 0.2, 0.12)) for word in GOOD_TEXT.split()]
    lines = [RawTextLine(text=GOOD_TEXT, bbox=(0.1, 0.1, 0.9, 0.12), words=words)] * 4
    page = RawPdfPage(
        page_index=index, width_pt=612.0, height_pt=792.0, lines=lines, image_area_ratio=0.0, path_area_ratio=0.0
    )
    return PageMessage(page=page)


def _misbehave(mode: str) -> None:
    if mode == "long_line":
        for _ in range(TOTAL // CHUNK):
            _write(b"x" * CHUNK)
    elif mode == "huge_json":
        _write(b'HOLOCRON_PDF:{"kind": "page_error", "page_index": 0, "message": "')
        for _ in range(TOTAL // CHUNK):
            _write(b"a" * CHUNK)
        _write(b'"}\n')
    elif mode == "noise":
        while True:
            _write(b"pdfium warning: noise\n" * 1000)
    elif mode == "bad_utf8":
        _write(b"HOLOCRON_PDF:\xff\xfe\xfd\n")


def main() -> None:
    request = json.loads(sys.stdin.buffer.readline())
    mode = Path(request["path"]).stem
    _emit(ReadyMessage())
    _emit(OpenedMessage(page_count=2))
    for raw in sys.stdin.buffer:
        index = json.loads(raw)["page_index"]
        if index == 0:
            _misbehave(mode)
            sys.stdin.buffer.readline()  # đứng chờ tới khi bị kill hoặc stdin đóng
            return
        _emit(_good_page(index))


if __name__ == "__main__":
    main()
