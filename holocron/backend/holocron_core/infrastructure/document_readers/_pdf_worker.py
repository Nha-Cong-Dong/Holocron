"""Worker đọc PDF, chạy trong tiến trình con riêng (ADR-008 mục 3, 8).

Khởi chạy bởi `pdf_reader.WorkerSession` bằng `python -P -s -m <module này>`, cwd là thư mục tạm, môi trường
tối thiểu, stdin/stdout UTF-8. Thứ tự: đọc `WorkerRequest` (dòng đầu stdin), nạp PDFium, nếu có cấu hình OCR thì
kiểm Tesseract (`--version`, `--list-langs`) và nạp RapidOCR, TỰ áp giới hạn OS (RAM, CPU, `RLIMIT_FSIZE` = 0 trên
Unix; Job Object trên Windows), báo `ready`, mở file, báo `opened`, rồi đọc từng trang khi nhận `PageRequest`.

Trang có lớp chữ kém (`text_score < TEXT_LAYER_MIN`) được OCR khi có engine: worker báo `ocr_started` (tiến trình
cha chuyển sang hạn của tầng 2), render trang ra ảnh xám, chạy Tesseract (tiến trình con của worker, chịu cùng
giới hạn OS) rồi RapidOCR, và gửi kết quả trong `RawPdfPage.ocr`. Mỗi kết quả là một dòng stdout có tiền tố
`MESSAGE_PREFIX`; tiến trình cha validate và giữ wall-clock timeout theo trang.
"""

from __future__ import annotations

import io
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel

from ...sandbox.os_limits import apply_limits
from .config import OcrSettings, PdfReaderConfig
from .pdfium_adapter import PdfiumBackend, PdfiumDocument
from .protocol import (
    OcrStartedMessage,
    OpenedMessage,
    OpenFailedMessage,
    PageErrorMessage,
    PageMessage,
    PageRequest,
    RawOcrLine,
    RawOcrPage,
    RawPdfPage,
    RawSecondaryPass,
    ReadyMessage,
    WorkerRequest,
    encode_message,
)
from .rapidocr_adapter import RapidOcrBackend
from .raster import GrayImage, PixelBox, encode_png_gray, ink_outside_text_ratio
from .tesseract import Runner, TesseractEngine, run_bounded
from .text_layer import needs_ocr

Emit = Callable[[BaseModel], None]

PDF_MAGIC = b"%PDF-"

# Lỗi ở một trang không làm hỏng các trang khác (ADR-008 mục 4). Lỗi khác làm worker thoát; tiến trình cha thấy
# EOF, đánh dấu trang PAGE_ERROR và dựng worker mới.
PAGE_ERRORS = (RuntimeError, ValueError, LookupError, ArithmeticError, TypeError, MemoryError, OSError)


class PrimaryOcr(Protocol):
    def recognize(
        self, png: bytes, image: GrayImage, config: PdfReaderConfig
    ) -> tuple[list[RawOcrLine], list[PixelBox]]: ...


class SecondaryOcr(Protocol):
    def read(self, png: bytes, width: int, height: int) -> RawSecondaryPass: ...


@dataclass(frozen=True)
class OcrRunner:
    """Hai lượt đọc của tầng 2; `secondary` là None khi không có lượt thứ hai (`cross_check = unavailable`)."""

    primary: PrimaryOcr
    secondary: SecondaryOcr | None

    def read(self, image: GrayImage, config: PdfReaderConfig) -> RawOcrPage:
        png = encode_png_gray(image)
        lines, boxes = self.primary.recognize(png, image, config)
        secondary: RawSecondaryPass | None = None
        if self.secondary is not None:
            try:
                secondary = self.secondary.read(png, image.width, image.height)
            except PAGE_ERRORS:  # lượt thứ hai lỗi ở trang này ⇒ trang không có lượt đối chiếu (unavailable)
                secondary = None
        return RawOcrPage(
            dpi=image.dpi,
            lines=lines,
            ink_outside_text_ratio=ink_outside_text_ratio(image, boxes, config.ink_threshold),
            secondary=secondary,
        )


def build_ocr(
    settings: OcrSettings,
    config: PdfReaderConfig,
    run: Runner = run_bounded,
    load_secondary: Callable[[], SecondaryOcr | None] = RapidOcrBackend.load,
) -> OcrRunner | None:
    """Kiểm Tesseract và nạp lượt thứ hai; thiếu binary hoặc thiếu `vie` ⇒ None (coi như không có engine OCR)."""
    primary = TesseractEngine.probe(settings, config.ocr_probe_timeout_s, run)
    if primary is None:
        return None
    secondary = load_secondary() if settings.secondary == "rapidocr" else None
    return OcrRunner(primary=primary, secondary=secondary)


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


def _with_ocr(raw: RawPdfPage, ocr: RawOcrPage) -> RawPdfPage:
    return RawPdfPage(
        page_index=raw.page_index,
        width_pt=raw.width_pt,
        height_pt=raw.height_pt,
        lines=raw.lines,
        image_area_ratio=raw.image_area_ratio,
        path_area_ratio=raw.path_area_ratio,
        ocr=ocr,
    )


def _read_one(
    document: PdfiumDocument,
    index: int,
    page_count: int,
    config: PdfReaderConfig,
    ocr: OcrRunner | None,
    emit: Emit,
) -> PageMessage | PageErrorMessage:
    if index >= page_count:
        return PageErrorMessage(page_index=index, message="trang ngoài phạm vi tài liệu")
    try:
        raw = document.read_page(index)
        if ocr is not None and needs_ocr(raw.lines, config):
            emit(OcrStartedMessage(page_index=index))
            image = document.render_gray(index, config.ocr_dpi, config.ocr_max_pixels)
            raw = _with_ocr(raw, ocr.read(image, config))
        return PageMessage(page=raw)
    except PAGE_ERRORS as exc:
        return PageErrorMessage(page_index=index, message=type(exc).__name__[:200])


def _fit_message(message: PageMessage | PageErrorMessage, max_message_bytes: int) -> PageMessage | PageErrorMessage:
    """Trang mà thông điệp vượt `max_message_bytes` được báo lỗi thay vì gửi (tiến trình cha sẽ không nhận dòng đó).

    Tính cả `\\r\\n`, vì stdout ở chế độ văn bản trên Windows đổi `\\n` thành `\\r\\n`.
    """
    if not isinstance(message, PageMessage):
        return message
    if len(encode_message(message).encode("utf-8")) + 2 <= max_message_bytes:
        return message
    return PageErrorMessage(page_index=message.page.page_index, message="MessageTooLarge")


def run(
    request: WorkerRequest,
    backend: PdfiumBackend,
    requests: Iterable[PageRequest],
    emit: Emit,
    ocr: OcrRunner | None = None,
) -> None:
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
            page = _read_one(opened, page_request.page_index, page_count, request.config, ocr, emit)
            emit(_fit_message(page, request.config.max_message_bytes))


def main() -> None:
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    if isinstance(sys.stdin, io.TextIOWrapper):
        sys.stdin.reconfigure(encoding="utf-8")
    request = WorkerRequest.model_validate_json(sys.stdin.readline())
    config = request.config
    # Nạp PDFium, kiểm Tesseract và nạp RapidOCR trước khi áp giới hạn, như sandbox nạp sẵn module whitelist.
    backend = PdfiumBackend.load()
    ocr = build_ocr(request.ocr, config) if request.ocr is not None else None
    cpu_per_page = config.ocr_cpu_seconds_per_page if ocr is not None else config.worker_cpu_seconds_per_page
    apply_limits(config.worker_mem_bytes, cpu_per_page * (request.page_budget + 1))
    _emit_stdout(ReadyMessage())
    run(request, backend, _stdin_page_requests(), _emit_stdout, ocr)


if __name__ == "__main__":
    main()
