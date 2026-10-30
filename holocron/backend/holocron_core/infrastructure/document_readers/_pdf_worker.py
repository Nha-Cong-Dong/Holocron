"""Worker đọc PDF, chạy trong tiến trình con riêng (ADR-008 mục 3, 8).

Khởi chạy bởi `pdf_reader.WorkerSession` bằng `python -P -s -m <module này>`, cwd là thư mục tạm, môi trường
tối thiểu, stdin/stdout UTF-8. Thứ tự: đọc `WorkerRequest` (dòng đầu stdin), nạp PDFium, làm nóng PDFium bằng PDF
viết sẵn trong bộ nhớ (`pdfium_adapter.WARMUP_PDF`, bỏ qua khi `page_budget == 0`), nếu có cấu hình OCR thì kiểm
Tesseract (`--version`, `--list-langs`) và nạp RapidOCR, TỰ áp giới hạn OS (RAM, CPU, `RLIMIT_FSIZE` = 0 trên Unix;
Job Object trên Windows), báo `ready`, mở file, báo `opened`, quét ảnh template của cả tài liệu khi được yêu cầu
(`WorkerRequest.scan_templates`, báo `template_scan_started` rồi `template_scan`; worker dựng lại thì nhận sẵn kết
quả quét trong `WorkerRequest.template_scan` và không quét lại), rồi đọc từng trang khi nhận `PageRequest`.

Trang cần OCR theo `text_layer.needs_ocr_page` (lớp chữ rỗng, lớp chữ rác `text_score < TEXT_LAYER_MIN`, trang ảnh
chỉ có ít chữ (luật (c)), hoặc ảnh phủ gần kín với ít chữ (luật (d)); độ phủ ảnh của hai luật sau không tính ảnh
template; cùng hàm tiến trình cha dùng để dựng trang) được OCR khi có engine: worker báo `ocr_started` (tiến
trình cha chuyển sang hạn của tầng 2), render trang ra ảnh xám, chạy Tesseract (tiến trình con của worker, chịu cùng
giới hạn OS) rồi RapidOCR, và gửi kết quả trong `RawPdfPage.ocr`. Mỗi kết quả là một dòng stdout có tiền tố
`MESSAGE_PREFIX`; tiến trình cha validate và giữ wall-clock timeout theo trang.
"""

from __future__ import annotations

import contextlib
import functools
import io
import math
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel

from ...sandbox.os_limits import apply_limits
from .config import OcrSettings, PdfReaderConfig
from .pdfium_adapter import PdfiumBackend, PdfiumDocument, warm_up
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
    TemplateScan,
    TemplateScanMessage,
    TemplateScanStartedMessage,
    WorkerRequest,
    encode_message,
)
from .rapidocr_adapter import RapidOcrBackend
from .raster import GrayImage, PixelBox, encode_png_gray, ink_outside_text_ratio
from .template_images import READ_ERRORS, TemplateFilter, scan_template_images
from .tesseract import Runner, TesseractEngine, run_bounded
from .text_layer import needs_ocr_page

Emit = Callable[[BaseModel], None]

PDF_MAGIC = b"%PDF-"

# Lỗi ở một trang không làm hỏng các trang khác (ADR-008 mục 4). Lỗi khác làm worker thoát; tiến trình cha thấy
# EOF, đánh dấu trang PAGE_ERROR và dựng worker mới.
PAGE_ERRORS = READ_ERRORS


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
    run: Runner | None = None,
    load_secondary: Callable[[], SecondaryOcr | None] = RapidOcrBackend.load,
) -> OcrRunner | None:
    """Kiểm Tesseract và nạp lượt thứ hai; thiếu binary hoặc thiếu `vie` ⇒ None (coi như không có engine OCR).

    `run` mặc định là `run_bounded` với thời gian chờ sau khi kill lấy từ `config.ocr_kill_grace_s`.
    """
    runner = run if run is not None else functools.partial(run_bounded, kill_grace_s=config.ocr_kill_grace_s)
    primary = TesseractEngine.probe(
        settings, config.ocr_probe_timeout_s, runner, max_output_bytes=config.ocr_probe_max_bytes
    )
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
    """Trang kèm kết quả OCR; giữ nguyên mọi field khác (kể cả `content_image_area_ratio`)."""
    return raw.model_copy(update={"ocr": ocr})


def _read_one(
    document: PdfiumDocument,
    index: int,
    page_count: int,
    config: PdfReaderConfig,
    ocr: OcrRunner | None,
    emit: Emit,
    templates: TemplateFilter | None = None,
) -> PageMessage | PageErrorMessage:
    if index >= page_count:
        return PageErrorMessage(page_index=index, message="trang ngoài phạm vi tài liệu")
    try:
        # Không có ảnh template thì đọc trang đúng như trước (`content_image_area_ratio` là None).
        raw = document.read_page(index) if templates is None else document.read_page(index, templates)
        if ocr is not None and needs_ocr_page(raw, config):
            emit(OcrStartedMessage(page_index=index))
            image = document.render_gray(
                index, config.ocr_dpi, config.ocr_max_pixels, shrink_factor=config.render_shrink_factor
            )
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


def template_scan_for(request: WorkerRequest, document: PdfiumDocument, page_count: int, emit: Emit) -> TemplateScan:
    """Kết quả quét ảnh template mà worker dùng cho mọi trang của tài liệu.

    Có sẵn trong yêu cầu (worker dựng lại) ⇒ dùng đúng kết quả đó, không quét lại. Được yêu cầu quét và có trang để
    đọc (`page_budget ≥ 1`; worker của `probe` không quét) ⇒ báo `template_scan_started`, quét, rồi báo
    `template_scan`. Còn lại ⇒ `skipped`.
    """
    if request.template_scan is not None:
        return request.template_scan
    if not request.scan_templates or request.page_budget < 1:
        return TemplateScan(status="skipped")
    emit(TemplateScanStartedMessage())
    scan = scan_template_images(document, page_count, request.config)
    emit(TemplateScanMessage(scan=scan))
    return scan


def scan_cpu_seconds(request: WorkerRequest) -> int:
    """Giây CPU cộng thêm vào giới hạn của worker có quét ảnh template (`ceil(template_scan_timeout_s)`)."""
    if request.scan_templates and request.page_budget >= 1:
        return math.ceil(request.config.template_scan_timeout_s)
    return 0


def run(
    request: WorkerRequest,
    backend: PdfiumBackend,
    requests: Iterable[PageRequest],
    emit: Emit,
    ocr: OcrRunner | None = None,
) -> None:
    """Mở tài liệu, báo số trang, quét ảnh template (khi được yêu cầu), rồi đọc tối đa `page_budget` trang."""
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
        templates = TemplateFilter.from_scan(template_scan_for(request, opened, page_count, emit), request.config)
        for served, page_request in enumerate(requests):
            if served >= request.page_budget:
                return
            page = _read_one(opened, page_request.page_index, page_count, request.config, ocr, emit, templates)
            emit(_fit_message(page, request.config.max_message_bytes))


def start(
    request: WorkerRequest,
    *,
    load: Callable[[], PdfiumBackend],
    warm: Callable[[PdfiumBackend], object],
    limit: Callable[[int, int], None],
    emit: Emit,
) -> tuple[PdfiumBackend, OcrRunner | None]:
    """Khởi động worker: nạp PDFium, làm nóng, nạp OCR, áp giới hạn OS rồi báo `ready`.

    Mọi phụ thuộc là tham số bắt buộc (không có mặc định) để test gọi được mà không áp giới hạn OS thật lên tiến
    trình pytest. Nạp và làm nóng chạy trước khi áp giới hạn, như sandbox nạp sẵn module whitelist.
    """
    config = request.config
    backend = load()
    if request.page_budget >= 1:  # worker của `probe` (page_budget = 0) không đọc trang nào ⇒ không làm nóng
        # Làm nóng chỉ để giảm chi phí ở trang đầu: lỗi thì bỏ qua, im lặng (stdout là kênh giao thức) và đi tiếp.
        with contextlib.suppress(*PAGE_ERRORS):
            warm(backend)
    ocr = build_ocr(request.ocr, config) if request.ocr is not None else None
    cpu_per_page = config.ocr_cpu_seconds_per_page if ocr is not None else config.worker_cpu_seconds_per_page
    limit(config.worker_mem_bytes, cpu_per_page * (request.page_budget + 1) + scan_cpu_seconds(request))
    emit(ReadyMessage())
    return backend, ocr


def main() -> None:
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    if isinstance(sys.stdin, io.TextIOWrapper):
        sys.stdin.reconfigure(encoding="utf-8")
    request = WorkerRequest.model_validate_json(sys.stdin.readline())
    backend, ocr = start(request, load=PdfiumBackend.load, warm=warm_up, limit=apply_limits, emit=_emit_stdout)
    run(request, backend, _stdin_page_requests(), _emit_stdout, ocr)


if __name__ == "__main__":
    main()
