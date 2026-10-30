"""Kiểm cục bộ cho chế độ khả năng (SPECIFICATION §5.12, §11.4): Tesseract, gói `vie`, lượt đọc thứ hai.

Chạy MỘT LẦN lúc khởi động app (tầng router gắn kết quả vào `app.state`); không gọi mạng. Kết quả là đối số
`local_failures` của `services.capabilities.compute_capabilities`.

- `probe_tesseract`: chạy binary Tesseract qua cơ chế probe của worker (`TesseractEngine.probe_status`: `--version`
  rồi `--list-langs`, mỗi lệnh một lần, không qua shell, timeout, giới hạn byte đầu ra và thời gian chờ sau khi kill
  lấy từ `PdfReaderConfig` của app). Lý do của probe phân biệt "không chạy được binary" với "thiếu `vie`". Có `vie`
  thì kiểm unicharset đủ chữ tiếng Việt (ADR-008 mục 14) bằng `tesseract.check_vie_charset`, CÙNG hàm worker gọi khi
  dựng engine; cả hai kiểm đúng thư mục tessdata mà lần gọi OCR thật dùng (`tesseract.resolve_tessdata_dir`:
  `--tessdata-dir` từ `HOLOCRON_TESSDATA_DIR` → thư mục ở dòng đầu `--list-langs` → với Tesseract 4.x, đường lùi
  là thư mục mặc định của bản cài; `TESSDATA_PREFIX` không tham gia). Thư mục đã xác định mà không có
  `vie.traineddata` là file thường ⇒ thiếu `vie`. Đường lùi không thấy file thì bỏ qua kiểm unicharset, kèm MỘT cảnh
  báo (ngoại lệ duyệt 2026-10-30).
- `secondary_ocr_loads`: đọc kết quả thăm dò lượt đọc thứ hai. Việc nạp RapidOCR chạy trong tiến trình con có giới
  hạn (`infrastructure/document_readers/secondary_probe.py`); tiến trình này không import `rapidocr`,
  `onnxruntime` hay `cv2`.
- `local_capability_failures`: hàm thuần từ kết quả kiểm và `OcrSettings` sang lý do không khả dụng.
- `check_local_capabilities`: nạp cấu hình OCR, probe, rồi thăm dò lượt thứ hai khi cần; bộ nạp cấu hình, probe và
  bộ thăm dò lượt thứ hai là tham số để test thay được.
"""

from __future__ import annotations

import functools
import logging
from collections.abc import Callable
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from ..domain.capabilities import CapabilityName, CapabilityReason
from ..infrastructure.document_readers.config import (
    ENV_PDF_OCR_PRIMARY,
    ENV_PDF_OCR_SECONDARY,
    TESSERACT_LANGUAGE,
    OcrSettings,
    PdfReaderConfig,
)
from ..infrastructure.document_readers.secondary_probe import SecondaryProbeReport
from ..infrastructure.document_readers.tesseract import (
    CharsetCheck,
    Runner,
    TesseractEngine,
    check_vie_charset,
    run_bounded,
)
from ..infrastructure.llm_config import ConfigError

_LOGGER = logging.getLogger(__name__)

# Thông điệp cố định khi cấu hình OCR sai: chỉ nêu tên biến, KHÔNG chép thông điệp của `ConfigError` (có thể chứa
# giá trị người dùng đặt).
INVALID_OCR_CONFIG_LOG = (
    f"không kiểm được OCR: cấu hình OCR không hợp lệ (xem {ENV_PDF_OCR_PRIMARY}, {ENV_PDF_OCR_SECONDARY})"
)


class OcrProbeResult(BaseModel):
    """Kết quả kiểm Tesseract: `engine_runs` khi `--version` chạy được; `vie_available` khi `--list-langs` có `vie`
    VÀ unicharset của `vie.traineddata` không thiếu chữ nào ngoài `ocr_charset_allowed_missing` (bỏ qua kiểm
    unicharset khi không xác định được thư mục tessdata)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    engine_runs: bool
    vie_available: bool


class OcrProber(Protocol):
    """Kiểm engine OCR chính theo cấu hình; test thay bằng probe giả để không phụ thuộc máy chạy test."""

    def __call__(self, settings: OcrSettings, config: PdfReaderConfig, /) -> OcrProbeResult: ...


class SecondaryProber(Protocol):
    """Thăm dò lượt đọc thứ hai theo cấu hình bộ đọc; bản thật nạp RapidOCR trong tiến trình con có giới hạn."""

    def __call__(self, config: PdfReaderConfig, /) -> SecondaryProbeReport: ...


def probe_tesseract(settings: OcrSettings, config: PdfReaderConfig, run: Runner | None = None) -> OcrProbeResult:
    """Kiểm binary Tesseract và gói `vie` bằng `TesseractEngine.probe_status` (cùng giới hạn với worker).

    `run` mặc định là `run_bounded` với thời gian chờ sau khi kill lấy từ `config.ocr_kill_grace_s`; timeout và giới
    hạn byte đầu ra lấy từ `config.ocr_probe_timeout_s`, `config.ocr_probe_max_bytes`.
    """
    runner: Runner = run if run is not None else functools.partial(run_bounded, kill_grace_s=config.ocr_kill_grace_s)
    outcome = TesseractEngine.probe_status(
        settings, config.ocr_probe_timeout_s, runner, max_output_bytes=config.ocr_probe_max_bytes
    )
    if outcome.status != "ok":
        return OcrProbeResult(engine_runs=outcome.status != "engine_missing", vie_available=False)
    charset = check_vie_charset(settings, outcome.listed_tessdata_dir, config)
    log_charset_check(charset)
    return OcrProbeResult(engine_runs=True, vie_available=charset.usable)


def log_charset_check(charset: CharsetCheck) -> None:
    """Một cảnh báo cho kết quả kiểm bộ chữ khác "đủ chữ"; không ghi đường dẫn (có thể chứa tên người dùng)."""
    if charset.status == "unknown_tessdata":
        _LOGGER.warning(
            "không tìm thấy %s.traineddata ở thư mục tessdata nào đã dò: bỏ qua kiểm unicharset", TESSERACT_LANGUAGE
        )
    elif charset.status == "missing_file":
        _LOGGER.warning(
            "thư mục tessdata Tesseract dùng không có %s.traineddata là file thường: coi như thiếu gói %s",
            TESSERACT_LANGUAGE,
            TESSERACT_LANGUAGE,
        )
    elif charset.status == "unreadable":
        _LOGGER.warning("không đọc được %s.traineddata (lỗi đọc hoặc quá lớn)", TESSERACT_LANGUAGE)
    elif charset.outside:
        _LOGGER.warning(
            "%s.traineddata thiếu %d chữ tiếng Việt ngoài danh sách cho phép", TESSERACT_LANGUAGE, len(charset.outside)
        )
    elif charset.missing:
        _LOGGER.warning(
            "%s.traineddata thiếu chữ được phép thiếu: %s", TESSERACT_LANGUAGE, " ".join(sorted(charset.missing))
        )


def secondary_ocr_loads(probe: SecondaryProber, config: PdfReaderConfig) -> bool:
    """Thăm dò lượt đọc thứ hai một lần; trả về nạp được hay không.

    Không nạp được thì ghi MỘT cảnh báo chỉ gồm tên loại lỗi do bộ thăm dò báo (đã kiểm là định danh Python): không
    thông điệp lỗi, không đường dẫn, không biến môi trường.
    """
    report = probe(config)
    if not report.loaded:
        _LOGGER.warning("không nạp được lượt đọc thứ hai (RapidOCR) (%s)", report.error)
    return report.loaded


def local_capability_failures(
    probe_result: OcrProbeResult, settings: OcrSettings, *, secondary_loaded: bool
) -> dict[CapabilityName, CapabilityReason]:
    """Lý do không khả dụng của `ocr` và `ocr_cross_check`; khả năng không có trong kết quả là khả dụng.

    OCR chính hỏng thì `ocr_cross_check` mang cùng lý do (lượt thứ hai chỉ chạy khi có lượt chính). OCR chính chạy
    được mà `settings.secondary` để trống (`HOLOCRON_PDF_OCR_SECONDARY` đặt rỗng) ⇒ `secondary_ocr_disabled`; bật mà
    lượt thứ hai không nạp được (`secondary_loaded` sai) ⇒ `secondary_ocr_unavailable` (duyệt 2026-10-29).
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
    if not secondary_loaded:
        return {"ocr_cross_check": "secondary_ocr_unavailable"}
    return {}


def check_local_capabilities(
    load_settings: Callable[[], OcrSettings],
    prober: OcrProber,
    probe_secondary: SecondaryProber,
    config: PdfReaderConfig,
) -> dict[CapabilityName, CapabilityReason]:
    """Nạp cấu hình OCR, probe Tesseract một lần với `config` của app, rồi thăm dò lượt thứ hai khi cần.

    Cấu hình OCR sai (`ConfigError`) ⇒ bộ đọc PDF chạy không có OCR (trang cần OCR thành `OCR_ENGINE_MISSING`),
    nên cả hai khả năng OCR báo `ocr_engine_missing`; ghi cảnh báo cố định (chỉ tên biến) và không probe. Lượt thứ
    hai chỉ được thăm dò khi OCR chính dùng được và `settings.secondary` bật (cùng điều kiện worker nạp nó).
    """
    try:
        settings = load_settings()
    except ConfigError:
        _LOGGER.warning(INVALID_OCR_CONFIG_LOG)
        return {"ocr": "ocr_engine_missing", "ocr_cross_check": "ocr_engine_missing"}
    probe_result = prober(settings, config)
    primary_ready = probe_result.engine_runs and probe_result.vie_available
    secondary_loaded = primary_ready and settings.secondary is not None and secondary_ocr_loads(probe_secondary, config)
    return local_capability_failures(probe_result, settings, secondary_loaded=secondary_loaded)
