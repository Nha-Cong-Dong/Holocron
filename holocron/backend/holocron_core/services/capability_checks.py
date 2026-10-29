"""Kiểm cục bộ cho chế độ khả năng (SPECIFICATION §5.12, §11.4): Tesseract, gói `vie`, lượt đọc thứ hai.

Chạy MỘT LẦN lúc khởi động app (tầng router gắn kết quả vào `app.state`); không gọi mạng. Kết quả là đối số
`local_failures` của `services.capabilities.compute_capabilities`.

- `probe_tesseract`: chạy binary Tesseract qua cơ chế probe của worker (`TesseractEngine.probe_status`: `--version`
  rồi `--list-langs`, mỗi lệnh một lần, không qua shell, timeout, giới hạn byte đầu ra và thời gian chờ sau khi kill
  lấy từ `PdfReaderConfig` của app). Lý do của probe phân biệt "không chạy được binary" với "thiếu `vie`". Có `vie`
  thì đọc `vie.traineddata` (có trần byte) và kiểm unicharset đủ chữ tiếng Việt (ADR-008 mục 14).
- `secondary_ocr_loads`: nạp lượt đọc thứ hai bằng đúng bộ nạp worker dùng (`RapidOcrBackend.load`), một lần, có
  bắt lỗi; đối tượng đã nạp không được giữ lại.
- `local_capability_failures`: hàm thuần từ kết quả kiểm và `OcrSettings` sang lý do không khả dụng.
- `check_local_capabilities`: nạp cấu hình OCR, probe, rồi nạp lượt thứ hai khi cần; bộ nạp cấu hình, probe và bộ
  nạp lượt thứ hai là tham số để test thay được.
"""

from __future__ import annotations

import functools
import logging
from collections.abc import Callable
from pathlib import Path
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
from ..infrastructure.document_readers.rapidocr_adapter import BUILTIN_ERRORS
from ..infrastructure.document_readers.tesseract import (
    Runner,
    TesseractEngine,
    missing_vietnamese_letters,
    run_bounded,
)
from ..infrastructure.llm_config import ConfigError

_LOGGER = logging.getLogger(__name__)

# Thông điệp cố định khi cấu hình OCR sai: chỉ nêu tên biến, KHÔNG chép thông điệp của `ConfigError` (có thể chứa
# giá trị người dùng đặt).
INVALID_OCR_CONFIG_LOG = (
    f"không kiểm được OCR lúc khởi động: cấu hình OCR không hợp lệ (xem {ENV_PDF_OCR_PRIMARY}, {ENV_PDF_OCR_SECONDARY})"
)


class OcrProbeResult(BaseModel):
    """Kết quả kiểm Tesseract: `engine_runs` khi `--version` chạy được; `vie_available` khi `--list-langs` có `vie`
    VÀ unicharset của `vie.traineddata` không thiếu chữ nào ngoài `ocr_charset_allowed_missing`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    engine_runs: bool
    vie_available: bool


class OcrProber(Protocol):
    """Kiểm engine OCR chính theo cấu hình; test thay bằng probe giả để không phụ thuộc máy chạy test."""

    def __call__(self, settings: OcrSettings, config: PdfReaderConfig, /) -> OcrProbeResult: ...


# Bộ nạp lượt đọc thứ hai: trả đối tượng engine, hoặc None khi thiếu gói/model hay khởi tạo lỗi.
SecondaryLoader = Callable[[], object | None]
# Lỗi khi nạp lượt thứ hai được tính là "nạp hỏng" thay vì làm app thôi khởi động: lỗi chuẩn mà adapter RapidOCR
# cũng bắt (gồm `OSError` khi thiếu DLL, `MemoryError`) và `ImportError` (gói nhị phân hỏng giữa chừng import).
SECONDARY_LOAD_ERRORS: tuple[type[Exception], ...] = (*BUILTIN_ERRORS, ImportError)


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
    tessdata_dir = settings.tessdata_dir if settings.tessdata_dir is not None else outcome.listed_tessdata_dir
    return OcrProbeResult(engine_runs=True, vie_available=vie_charset_ok(tessdata_dir, config))


def read_bounded(path: Path, max_bytes: int) -> bytes | None:
    """Đọc tối đa `max_bytes` byte của `path`; file lớn hơn hoặc lỗi đọc ⇒ None."""
    try:
        with path.open("rb") as handle:
            data = handle.read(max_bytes + 1)
    except OSError:
        return None
    return data if len(data) <= max_bytes else None


def vie_charset_ok(tessdata_dir: str | None, config: PdfReaderConfig) -> bool:
    """Unicharset của `<tessdata_dir>/vie.traineddata` đủ chữ tiếng Việt, trừ các chữ trong
    `config.ocr_charset_allowed_missing` (thiếu những chữ này thì vẫn đạt, kèm cảnh báo).

    Không biết thư mục, không đọc được file (vắng, lỗi đọc, vượt `config.ocr_traineddata_max_bytes`) hoặc thiếu chữ
    ngoài danh sách cho phép ⇒ False. Cảnh báo không ghi đường dẫn (có thể chứa tên người dùng).
    """
    if tessdata_dir is None:
        _LOGGER.warning("không xác định được thư mục tessdata để kiểm %s.traineddata", TESSERACT_LANGUAGE)
        return False
    data = read_bounded(Path(tessdata_dir) / f"{TESSERACT_LANGUAGE}.traineddata", config.ocr_traineddata_max_bytes)
    if data is None:
        _LOGGER.warning("không đọc được %s.traineddata (vắng, lỗi đọc hoặc quá lớn)", TESSERACT_LANGUAGE)
        return False
    missing = set(missing_vietnamese_letters(data))
    outside = missing - config.ocr_charset_allowed_missing
    if outside:
        _LOGGER.warning(
            "%s.traineddata thiếu %d chữ tiếng Việt ngoài danh sách cho phép", TESSERACT_LANGUAGE, len(outside)
        )
        return False
    if missing:
        _LOGGER.warning("%s.traineddata thiếu chữ được phép thiếu: %s", TESSERACT_LANGUAGE, " ".join(sorted(missing)))
    return True


def secondary_ocr_loads(load: SecondaryLoader) -> bool:
    """Nạp lượt đọc thứ hai một lần; trả về nạp được hay không.

    `RapidOcrBackend.load` tự trả None khi thiếu gói, thiếu model hoặc khởi tạo lỗi; lỗi thuộc
    `SECONDARY_LOAD_ERRORS` lọt ra (thiếu DLL khi import, hết bộ nhớ) cũng tính là nạp hỏng. Cảnh báo chỉ ghi tên
    lớp lỗi, không ghi thông điệp.
    """
    try:
        engine = load()
    except SECONDARY_LOAD_ERRORS as exc:
        _LOGGER.warning("không nạp được lượt đọc thứ hai lúc khởi động (%s)", type(exc).__name__)
        return False
    return engine is not None


def local_capability_failures(
    probe_result: OcrProbeResult, settings: OcrSettings, *, secondary_loaded: bool
) -> dict[CapabilityName, CapabilityReason]:
    """Lý do không khả dụng của `ocr` và `ocr_cross_check`; khả năng không có trong kết quả là khả dụng.

    OCR chính hỏng thì `ocr_cross_check` mang cùng lý do (lượt thứ hai chỉ chạy khi có lượt chính). OCR chính chạy
    được mà `settings.secondary` để trống, hoặc lượt thứ hai không nạp được (`secondary_loaded` sai) ⇒
    `secondary_ocr_disabled`: `CapabilityReason` chưa có mã riêng cho "nạp hỏng", và với worker hai trường hợp như
    nhau (trang không có lượt đối chiếu, `cross_check = unavailable`).
    """
    primary: CapabilityReason | None = None
    if not probe_result.engine_runs:
        primary = "ocr_engine_missing"
    elif not probe_result.vie_available:
        primary = "ocr_language_missing"
    if primary is not None:
        return {"ocr": primary, "ocr_cross_check": primary}
    if settings.secondary is None or not secondary_loaded:
        return {"ocr_cross_check": "secondary_ocr_disabled"}
    return {}


def check_local_capabilities(
    load_settings: Callable[[], OcrSettings],
    prober: OcrProber,
    load_secondary: SecondaryLoader,
    config: PdfReaderConfig,
) -> dict[CapabilityName, CapabilityReason]:
    """Nạp cấu hình OCR, probe Tesseract một lần với `config` của app, rồi nạp lượt thứ hai khi cần.

    Cấu hình OCR sai (`ConfigError`) ⇒ bộ đọc PDF chạy không có OCR (trang cần OCR thành `OCR_ENGINE_MISSING`),
    nên cả hai khả năng OCR báo `ocr_engine_missing`; ghi cảnh báo cố định (chỉ tên biến) và không probe. Lượt thứ
    hai chỉ được nạp khi OCR chính dùng được và `settings.secondary` bật (cùng điều kiện worker dùng nó).
    """
    try:
        settings = load_settings()
    except ConfigError:
        _LOGGER.warning(INVALID_OCR_CONFIG_LOG)
        return {"ocr": "ocr_engine_missing", "ocr_cross_check": "ocr_engine_missing"}
    probe_result = prober(settings, config)
    primary_ready = probe_result.engine_runs and probe_result.vie_available
    secondary_loaded = primary_ready and settings.secondary is not None and secondary_ocr_loads(load_secondary)
    return local_capability_failures(probe_result, settings, secondary_loaded=secondary_loaded)
