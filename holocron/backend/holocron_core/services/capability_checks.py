"""Kiểm cục bộ cho chế độ khả năng (SPECIFICATION §5.12, §11.4): Tesseract, gói `vie`, lượt đọc thứ hai.

Chạy MỘT LẦN lúc khởi động app (tầng router gắn kết quả vào `app.state`); không gọi mạng. Kết quả là đối số
`local_failures` của `services.capabilities.compute_capabilities`.

- `probe_tesseract`: chạy binary Tesseract qua cơ chế probe sẵn có (`TesseractEngine.probe`, `--version` rồi
  `--list-langs`, không qua shell, có timeout và giới hạn byte đầu ra theo `PdfReaderConfig`). Probe đó gộp "không
  chạy được binary" và "thiếu `vie`" thành `None`; khi đó chạy lại `--version` để phân biệt hai lý do.
- `local_capability_failures`: hàm thuần từ kết quả probe và `OcrSettings` sang lý do không khả dụng.
- `check_local_capabilities`: nạp cấu hình OCR rồi probe; probe và bộ nạp cấu hình là tham số để test thay được.
"""

from __future__ import annotations

import functools
import logging
from collections.abc import Callable
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from ..domain.capabilities import CapabilityName, CapabilityReason
from ..infrastructure.document_readers.config import OcrSettings, PdfReaderConfig
from ..infrastructure.document_readers.tesseract import Runner, TesseractEngine, run_bounded
from ..infrastructure.llm_config import ConfigError

_LOGGER = logging.getLogger(__name__)


class OcrProbeResult(BaseModel):
    """Kết quả kiểm Tesseract: `engine_runs` khi `--version` chạy được, `vie_available` khi `--list-langs` có `vie`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    engine_runs: bool
    vie_available: bool


class OcrProber(Protocol):
    """Kiểm engine OCR chính theo cấu hình; test thay bằng probe giả để không phụ thuộc máy chạy test."""

    def __call__(self, settings: OcrSettings, /) -> OcrProbeResult: ...


def probe_tesseract(
    settings: OcrSettings, config: PdfReaderConfig | None = None, run: Runner | None = None
) -> OcrProbeResult:
    """Kiểm binary Tesseract và gói `vie` bằng `TesseractEngine.probe` (cùng timeout, giới hạn byte của bộ đọc PDF).

    `run` mặc định là `run_bounded` với thời gian chờ sau khi kill lấy từ `config.ocr_kill_grace_s`.
    """
    reader_config = PdfReaderConfig() if config is None else config
    runner: Runner = (
        run if run is not None else functools.partial(run_bounded, kill_grace_s=reader_config.ocr_kill_grace_s)
    )
    timeout_s = reader_config.ocr_probe_timeout_s
    limit = reader_config.ocr_probe_max_bytes
    if TesseractEngine.probe(settings, timeout_s, runner, max_output_bytes=limit) is not None:
        return OcrProbeResult(engine_runs=True, vie_available=True)
    version = runner([settings.tesseract_cmd, "--version"], b"", timeout_s, limit)
    return OcrProbeResult(engine_runs=version.status == "ok", vie_available=False)


def local_capability_failures(
    probe_result: OcrProbeResult, settings: OcrSettings
) -> dict[CapabilityName, CapabilityReason]:
    """Lý do không khả dụng của `ocr` và `ocr_cross_check`; khả năng không có trong kết quả là khả dụng.

    OCR chính hỏng thì `ocr_cross_check` mang cùng lý do (lượt thứ hai chỉ chạy khi có lượt chính); OCR chính chạy
    được mà `settings.secondary` để trống ⇒ `secondary_ocr_disabled`.
    """
    primary: CapabilityReason | None = None
    if not probe_result.engine_runs:
        primary = "ocr_engine_missing"
    elif not probe_result.vie_available:
        primary = "ocr_language_missing"
    if primary is not None:
        return {"ocr": primary, "ocr_cross_check": primary}
    if settings.secondary is None:
        return {"ocr_cross_check": "secondary_ocr_disabled"}
    return {}


def check_local_capabilities(
    load_settings: Callable[[], OcrSettings], prober: OcrProber
) -> dict[CapabilityName, CapabilityReason]:
    """Nạp cấu hình OCR rồi probe một lần.

    Cấu hình OCR sai (`ConfigError`) ⇒ bộ đọc PDF chạy không có OCR (trang cần OCR thành `OCR_ENGINE_MISSING`),
    nên cả hai khả năng OCR báo `ocr_engine_missing`; ghi cảnh báo chỉ gồm thông điệp lỗi (tên biến, không nêu giá
    trị) và không probe.
    """
    try:
        settings = load_settings()
    except ConfigError as exc:
        _LOGGER.warning("không kiểm được OCR lúc khởi động: %s", exc)
        return {"ocr": "ocr_engine_missing", "ocr_cross_check": "ocr_engine_missing"}
    return local_capability_failures(prober(settings), settings)
