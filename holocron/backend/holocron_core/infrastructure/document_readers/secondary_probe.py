"""Thăm dò lượt đọc thứ hai (RapidOCR) trong tiến trình con có giới hạn, một lần lúc khởi động app (spec §5.12).

Tiến trình cha (app) KHÔNG import `rapidocr`, `onnxruntime` hay `cv2`: nó chạy module này bằng
`python -P -s -m <module>` qua `tesseract.run_bounded` (không qua shell, môi trường tối thiểu, wall-clock timeout
`PdfReaderConfig.worker_startup_timeout_s` như lúc khởi động worker đọc PDF, trần byte đầu ra
`ocr_probe_max_bytes`, kill khi quá hạn và chờ sau kill tối đa `ocr_kill_grace_s`). Tiến trình con:

1. đọc `SecondaryProbeRequest` (một dòng JSON ở stdin);
2. TỰ áp giới hạn OS bằng `sandbox.os_limits.apply_limits` với đúng RAM của worker (`worker_mem_bytes`) và CPU của
   một trang OCR (`ocr_cpu_seconds_per_page`), TRƯỚC khi nạp gì: Unix `RLIMIT_AS`, `RLIMIT_CPU`, `RLIMIT_FSIZE` = 0,
   `RLIMIT_CORE` = 0; Windows Job Object;
3. nạp RapidOCR bằng đúng bộ nạp của worker (`RapidOcrBackend.load_outcome`), không đọc ảnh nào;
4. ghi MỘT dòng báo cáo có tiền tố `REPORT_PREFIX` ra stdout rồi thoát; lỗi lạ lúc nạp thì ghi dòng báo cáo
   "không nạp được" kèm tên lớp lỗi rồi ném lại (thoát mã khác 0). Thư viện in gì ra `sys.stdout` trong lúc nạp
   thì bị chuyển sang stderr (bị bỏ), nên không lẫn vào kênh báo cáo.

Tiến trình con chết, quá hạn, ghi quá trần byte, hay báo cáo sai dạng ⇒ coi như không nạp được. Báo cáo chỉ mang
tên loại lỗi (định danh Python, không có đường dẫn hay thông điệp), được tiến trình cha kiểm lại trước khi dùng.
"""

from __future__ import annotations

import functools
import os
import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, model_validator

from ...sandbox.os_limits import apply_limits
from .config import PdfReaderConfig
from .rapidocr_adapter import RapidOcrBackend, RapidOcrLoad
from .tesseract import Runner, run_bounded

PROBE_MODULE = "holocron_core.infrastructure.document_readers.secondary_probe"
REPORT_PREFIX = "HOLOCRON_SECONDARY_PROBE "
_PACKAGE_PARENT = Path(__file__).resolve().parents[3]
_ERROR_NAME_PATTERN = r"^[A-Za-z_][A-Za-z0-9_]{0,63}$"
_ERROR_NAME = re.compile(_ERROR_NAME_PATTERN)

# Tên lỗi do tiến trình cha đặt khi không có báo cáo hợp lệ từ tiến trình con.
CHILD_TIMEOUT = "ProbeTimeout"
CHILD_OVERFLOW = "ProbeOutputOverflow"
CHILD_FAILED = "ProbeProcessFailed"
INVALID_REPORT = "ProbeInvalidReport"
# Tên lỗi do tiến trình con đặt khi dòng yêu cầu sai dạng, hoặc lớp exception có tên không phải định danh ASCII.
INVALID_REQUEST = "ProbeInvalidRequest"
UNNAMED_ERROR = "UnnamedError"

ErrorName = Annotated[str, StringConstraints(pattern=_ERROR_NAME_PATTERN)]


class SecondaryProbeRequest(BaseModel):
    """Giới hạn OS tiến trình con tự áp trước khi nạp RapidOCR."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    mem_bytes: int = Field(ge=1)
    cpu_seconds: int = Field(ge=1)


class SecondaryProbeReport(BaseModel):
    """Kết quả thăm dò: `loaded` khi nạp được; ngược lại `error` là tên loại lỗi (null khi và chỉ khi nạp được)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    loaded: bool
    error: ErrorName | None

    @model_validator(mode="after")
    def _error_iff_not_loaded(self) -> SecondaryProbeReport:
        if (self.error is None) != self.loaded:
            raise ValueError("error là null khi và chỉ khi loaded = true")
        return self


def _failure(error: str) -> SecondaryProbeReport:
    return SecondaryProbeReport(loaded=False, error=error)


def error_name(exc: BaseException) -> str:
    """Tên lớp của `exc` nếu là định danh ASCII ngắn; ngược lại `UNNAMED_ERROR` (không bao giờ chép thông điệp)."""
    name = type(exc).__name__
    return name if _ERROR_NAME.fullmatch(name) else UNNAMED_ERROR


# --------------------------------------------------------------------------- #
# Tiến trình cha
# --------------------------------------------------------------------------- #


def probe_env() -> dict[str, str]:
    """Môi trường tối thiểu của tiến trình con (như worker đọc PDF): UTF-8, `PYTHONPATH` tới `holocron_core`."""
    env = {
        "PYTHONHASHSEED": "0",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "PYTHONPATH": str(_PACKAGE_PARENT),
    }
    if sys.platform == "win32":
        env["SYSTEMROOT"] = os.environ.get("SYSTEMROOT", r"C:\Windows")
    return env


def probe_command() -> list[str]:
    return [sys.executable, "-P", "-s", "-m", PROBE_MODULE]


def parse_report(stdout: bytes) -> SecondaryProbeReport:
    """Dòng báo cáo CUỐI có tiền tố `REPORT_PREFIX`; không có, sai UTF-8 hay sai dạng ⇒ `INVALID_REPORT`."""
    try:
        text = stdout.decode("utf-8")
    except UnicodeDecodeError:
        return _failure(INVALID_REPORT)
    reports = [line[len(REPORT_PREFIX) :] for line in text.splitlines() if line.startswith(REPORT_PREFIX)]
    if not reports:
        return _failure(INVALID_REPORT)
    try:
        return SecondaryProbeReport.model_validate_json(reports[-1])
    except ValidationError:
        return _failure(INVALID_REPORT)


def probe_secondary_ocr(config: PdfReaderConfig, run: Runner | None = None) -> SecondaryProbeReport:
    """Nạp RapidOCR một lần trong tiến trình con có giới hạn (xem docstring của module) và trả kết quả.

    `run` mặc định là `run_bounded` với `kill_grace_s` của `config` và môi trường `probe_env()`.
    """
    runner: Runner = (
        run
        if run is not None
        else functools.partial(run_bounded, kill_grace_s=config.ocr_kill_grace_s, env=probe_env())
    )
    request = SecondaryProbeRequest(mem_bytes=config.worker_mem_bytes, cpu_seconds=config.ocr_cpu_seconds_per_page)
    result = runner(
        probe_command(),
        (request.model_dump_json() + "\n").encode("utf-8"),
        config.worker_startup_timeout_s,
        config.ocr_probe_max_bytes,
    )
    if result.status == "timeout":
        return _failure(CHILD_TIMEOUT)
    if result.status == "overflow":
        return _failure(CHILD_OVERFLOW)
    if result.status == "failed":
        # Mã thoát khác 0: chỉ tin dòng báo "không nạp được" mà tiến trình con kịp ghi (lỗi lạ lúc nạp model).
        reported = parse_report(result.stdout)
        return reported if not reported.loaded and reported.error != INVALID_REPORT else _failure(CHILD_FAILED)
    return parse_report(result.stdout)


# --------------------------------------------------------------------------- #
# Tiến trình con
# --------------------------------------------------------------------------- #


def run_probe(
    request_line: str,
    *,
    limit: Callable[[int, int], None],
    load: Callable[[], RapidOcrLoad],
    emit: Callable[[SecondaryProbeReport], None],
) -> None:
    """Phần việc của tiến trình con: áp giới hạn rồi nạp, báo đúng MỘT dòng kết quả qua `emit`.

    Lỗi lúc nạp mà `load` không tự bắt (lớp lỗi không thuộc `BUILTIN_ERRORS`, `SystemExit`…) được báo là "không
    nạp được" kèm tên lớp lỗi, rồi ném lại: tiến trình con thoát với mã khác 0, tiến trình cha đọc dòng báo cáo đã
    ghi trước đó.
    """
    try:
        request = SecondaryProbeRequest.model_validate_json(request_line)
    except ValidationError:
        emit(_failure(INVALID_REQUEST))
        return
    limit(request.mem_bytes, request.cpu_seconds)
    try:
        outcome = load()
    except BaseException as exc:
        emit(_failure(error_name(exc)))
        raise
    if outcome.backend is not None:
        emit(SecondaryProbeReport(loaded=True, error=None))
        return
    reported = outcome.error if outcome.error is not None and _ERROR_NAME.fullmatch(outcome.error) else UNNAMED_ERROR
    emit(_failure(reported))


def main() -> None:
    """Điểm vào của tiến trình con (`python -P -s -m <module>`); không nhận tham số dòng lệnh."""
    report_stream = sys.stdout
    sys.stdout = sys.stderr

    def emit(report: SecondaryProbeReport) -> None:
        report_stream.write(REPORT_PREFIX + report.model_dump_json() + "\n")
        report_stream.flush()

    run_probe(sys.stdin.readline(), limit=apply_limits, load=RapidOcrBackend.load_outcome, emit=emit)


if __name__ == "__main__":
    main()
