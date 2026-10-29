"""`GET /v1/runtime`: trạng thái chạy cho UI (SPECIFICATION §5.12).

Kiểm cục bộ của chế độ khả năng (Tesseract, gói `vie`, lượt đọc thứ hai; `services/capability_checks.py`) chạy MỘT
LẦN, NỀN, sau khi app mở cổng (duyệt 2026-10-30): `lifespan` của `api.py` gọi `start_local_capability_check` ngay sau
khi tạo bộ nhập tài liệu (với `reader_config` của bộ nhập đó) và KHÔNG chờ kiểm xong. Trong lúc kiểm, `ocr` và
`ocr_cross_check` báo `local_check_pending`; xong thì kết quả lưu ở `app.state` dưới dạng `MappingProxyType`
(request không sửa được) và mọi request đọc lại kết quả đó qua `get_local_failures`, không probe lại. Bộ kiểm lỗi
(ngoại lệ) ⇒ ghi log tên lỗi và coi như `ocr_engine_missing`, không treo ở trạng thái đang kiểm. Trạng thái đang kiểm
chỉ để hiển thị: route nhập tài liệu không đọc kết quả kiểm nên không bị chặn.

Khoá và trạng thái của lần kiểm nằm ở `app.state` (`LocalCheckState`), không phải biến module. Bộ kiểm cấp qua
`get_local_capability_check`, cách chạy nền qua `get_local_check_launcher`; test thay bằng `app.dependency_overrides`
(không phụ thuộc máy chạy test có Tesseract hay RapidOCR hay không). Lượt đọc thứ hai được thăm dò trong tiến trình
con có giới hạn (`secondary_probe.py`); app không import RapidOCR.
"""

from __future__ import annotations

import contextlib
import functools
import logging
import threading
from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, Request
from pydantic import BaseModel

from ..domain.capabilities import Capability, CapabilityName, CapabilityReason
from ..infrastructure.document_readers.config import PdfReaderConfig, load_ocr_settings
from ..infrastructure.document_readers.secondary_probe import error_name, probe_secondary_ocr
from ..services.capabilities import compute_capabilities
from ..services.capability_checks import check_local_capabilities, probe_tesseract
from ..services.document_import import ImportRuntime
from ..services.extraction import MockDualPassExtractor
from .deps import BuildInfoDep, ExtractorDep, LogDirDep, RuntimeConfigDep
from .documents import RUNTIME_STATE_ATTR

router = APIRouter()

_LOGGER = logging.getLogger(__name__)

LOCAL_FAILURES_STATE_ATTR = "capability_local_failures"
LOCAL_CHECK_STATE_ATTR = "capability_local_check"

LocalFailures = Mapping[CapabilityName, CapabilityReason]
LocalCapabilityCheck = Callable[[PdfReaderConfig], dict[CapabilityName, CapabilityReason]]
# Cách chạy một việc không đối số: mặc định là luồng nền daemon; test thay bằng chạy ngay hoặc giữ lại chạy sau.
LocalCheckLauncher = Callable[[Callable[[], None]], None]
# Giá trị mặc định cho hàm dependency gọi trực tiếp ngoài FastAPI (không có app nên không có kết quả kiểm cục bộ).
NO_LOCAL_FAILURES: LocalFailures = MappingProxyType({})
# Lần kiểm nền chưa xong: chỉ hai khả năng OCR mang lý do này (duyệt 2026-10-30).
PENDING_LOCAL_FAILURES: LocalFailures = MappingProxyType(
    {"ocr": "local_check_pending", "ocr_cross_check": "local_check_pending"}
)
# Bộ kiểm nền ném ngoại lệ: coi như không có engine OCR, không treo ở trạng thái đang kiểm.
CHECK_CRASHED_FAILURES: LocalFailures = MappingProxyType(
    {"ocr": "ocr_engine_missing", "ocr_cross_check": "ocr_engine_missing"}
)
LOCAL_CHECK_THREAD_NAME = "holocron-local-capability-check"
# Lỗi chuẩn bộ kiểm có thể ném (đọc file, chạy tiến trình con, validate dữ liệu): ghi một cảnh báo chỉ gồm tên lớp
# lỗi. Lỗi khác vẫn lan ra khỏi luồng nền, nhưng kết quả vẫn được lưu là `CHECK_CRASHED_FAILURES` (khối `finally`).
CHECK_ERRORS: tuple[type[Exception], ...] = (
    RuntimeError,
    ValueError,
    LookupError,
    ArithmeticError,
    TypeError,
    AttributeError,
    MemoryError,
    OSError,
)


class LocalCheckState:
    """Trạng thái kiểm cục bộ của MỘT lần khởi động app, đặt ở `app.state`.

    `lock` tuần tự hoá việc đọc/ghi kết quả (nhánh kiểm lười và luồng nền); `started` là đã khởi chạy kiểm nền (khi
    đó request chưa có kết quả nhận `PENDING_LOCAL_FAILURES` thay vì tự kiểm); `done` được đặt khi luồng nền xong,
    kể cả khi lỗi. Mỗi lần khởi động và mỗi lần tắt dùng một đối tượng mới, nên luồng nền của lần trước (nếu còn
    chạy) không ghi kết quả vào lần sau.
    """

    def __init__(self, *, started: bool = False) -> None:
        self.lock = threading.Lock()
        self.started = started
        self.done = threading.Event()


def get_local_capability_check() -> LocalCapabilityCheck:
    """Bộ kiểm cục bộ thật: cấu hình OCR từ biến môi trường, probe Tesseract bằng binary đã cấu hình, thăm dò
    RapidOCR trong tiến trình con có giới hạn (cùng bộ nạp với worker, `RapidOcrBackend.load_outcome`)."""
    return functools.partial(check_local_capabilities, load_ocr_settings, probe_tesseract, probe_secondary_ocr)


def launch_daemon_thread(work: Callable[[], None]) -> None:
    """Chạy `work` trong một luồng daemon: app không chờ, tắt app không bị luồng này giữ lại."""
    threading.Thread(target=work, name=LOCAL_CHECK_THREAD_NAME, daemon=True).start()


def get_local_check_launcher() -> LocalCheckLauncher:
    """Cách chạy lần kiểm lúc khởi động; mặc định `launch_daemon_thread`."""
    return launch_daemon_thread


def install_local_check_state(application: FastAPI, *, started: bool = False) -> LocalCheckState:
    """Đặt trạng thái kiểm mới (khoá mới) vào `application.state`; gọi khi tạo app, khi khởi động và khi tắt."""
    state = LocalCheckState(started=started)
    setattr(application.state, LOCAL_CHECK_STATE_ATTR, state)
    return state


def _local_check_state(application: FastAPI) -> LocalCheckState:
    """Trạng thái kiểm của app. App dựng riêng (không qua `api.py`, chưa gọi `install_local_check_state`) thì đặt mới
    ở lần gọi đầu; app của `api.py` luôn có sẵn nên khoá của nó không bao giờ bị tạo hai lần."""
    state = getattr(application.state, LOCAL_CHECK_STATE_ATTR, None)
    return state if isinstance(state, LocalCheckState) else install_local_check_state(application)


def _store_local_failures(application: FastAPI, failures: Mapping[CapabilityName, CapabilityReason]) -> LocalFailures:
    stored: LocalFailures = MappingProxyType(dict(failures))
    setattr(application.state, LOCAL_FAILURES_STATE_ATTR, stored)
    return stored


def _run_check(
    application: FastAPI, state: LocalCheckState, check: LocalCapabilityCheck, reader_config: PdfReaderConfig
) -> None:
    """Thân của lần kiểm nền: chạy bộ kiểm rồi lưu kết quả, nếu `state` vẫn là trạng thái hiện hành của app.

    Bộ kiểm ném lỗi thuộc `CHECK_ERRORS` ⇒ một cảnh báo chỉ gồm tên lớp lỗi (thông điệp có thể chứa đường dẫn). Ném
    bất kỳ lỗi nào ⇒ lưu `CHECK_CRASHED_FAILURES`, không bao giờ treo ở trạng thái đang kiểm.
    """
    failures: Mapping[CapabilityName, CapabilityReason] = CHECK_CRASHED_FAILURES
    try:
        failures = check(reader_config)
    except CHECK_ERRORS as exc:
        _LOGGER.warning("kiểm cục bộ OCR lúc khởi động bị lỗi (%s): coi như thiếu engine OCR", error_name(exc))
    finally:
        with state.lock:
            if getattr(application.state, LOCAL_CHECK_STATE_ATTR, None) is state:
                _store_local_failures(application, failures)
        state.done.set()


def start_local_capability_check(application: FastAPI, reader_config: PdfReaderConfig) -> LocalCheckState:
    """Khởi chạy kiểm cục bộ một lần với `reader_config` của bộ nhập rồi trả về ngay (không chờ); gọi lúc khởi động.

    Bộ kiểm và cách chạy nền lấy theo override của app. Từ lúc này tới khi kiểm xong, `get_local_failures` trả
    `PENDING_LOCAL_FAILURES`.
    """
    provider: Callable[[], LocalCapabilityCheck] = application.dependency_overrides.get(
        get_local_capability_check, get_local_capability_check
    )
    launcher_provider: Callable[[], LocalCheckLauncher] = application.dependency_overrides.get(
        get_local_check_launcher, get_local_check_launcher
    )
    check = provider()
    clear_local_capability_check(application)
    state = install_local_check_state(application, started=True)
    launcher_provider()(functools.partial(_run_check, application, state, check, reader_config))
    return state


def clear_local_capability_check(application: FastAPI) -> None:
    """Bỏ kết quả đã lưu và đặt trạng thái kiểm mới (chưa khởi chạy); gọi khi tắt app để lần khởi động sau kiểm lại.

    Luồng nền của lần khởi động trước (nếu chưa xong) sẽ không ghi kết quả vào app nữa.
    """
    previous = getattr(application.state, LOCAL_CHECK_STATE_ATTR, None)
    lock = previous.lock if isinstance(previous, LocalCheckState) else contextlib.nullcontext()
    with lock:
        if hasattr(application.state, LOCAL_FAILURES_STATE_ATTR):
            delattr(application.state, LOCAL_FAILURES_STATE_ATTR)
        install_local_check_state(application)


def _stored_failures(application: FastAPI) -> LocalFailures | None:
    stored: LocalFailures | None = getattr(application.state, LOCAL_FAILURES_STATE_ATTR, None)
    return stored


def get_local_failures(
    request: Request, check: Annotated[LocalCapabilityCheck, Depends(get_local_capability_check)]
) -> LocalFailures:
    """Kết quả kiểm cục bộ đã lưu; kiểm nền đã khởi chạy mà chưa xong ⇒ `PENDING_LOCAL_FAILURES`.

    App chạy không qua `lifespan` (ví dụ `TestClient` không dùng `with`) thì kiểm ở request đầu tiên rồi lưu lại
    như lúc khởi động; các request sau dùng kết quả đã lưu. Khi đó cấu hình bộ đọc lấy từ bộ nhập tài liệu đã gắn
    vào app nếu có, không thì cấu hình mặc định (đúng cấu hình `create_import_runtime` tạo khi không được truyền cấu
    hình). Nhánh kiểm lười chạy dưới khoá ở `app.state` và đọc lại kết quả sau khi lấy khoá, nên các request đến
    cùng lúc chỉ kiểm một lần.
    """
    application: FastAPI = request.app
    stored = _stored_failures(application)
    if stored is not None:
        return stored
    state = _local_check_state(application)
    with state.lock:
        stored = _stored_failures(application)
        if stored is not None:
            return stored
        if state.started:
            return PENDING_LOCAL_FAILURES
        import_runtime = getattr(application.state, RUNTIME_STATE_ATTR, None)
        config = import_runtime.reader_config if isinstance(import_runtime, ImportRuntime) else PdfReaderConfig()
        return _store_local_failures(application, check(config))


LocalFailuresDep = Annotated[LocalFailures, Depends(get_local_failures)]


class RuntimeInfo(BaseModel):
    """Trạng thái chạy cho UI: chỉ cờ boolean, KHÔNG chứa khoá API, tên model hay địa chỉ endpoint."""

    demo_mode: bool
    llm_configured: bool
    vlm_configured: bool
    app_version: str
    commit: str
    log_dir: str | None
    capabilities: list[Capability]


@router.get("/v1/runtime", response_model=RuntimeInfo)
def get_runtime(
    config: RuntimeConfigDep,
    extractor: ExtractorDep,
    build: BuildInfoDep,
    log_dir: LogDirDep,
    local_failures: LocalFailuresDep,
) -> RuntimeInfo:
    return RuntimeInfo(
        demo_mode=isinstance(extractor, MockDualPassExtractor),
        llm_configured=config.llm_configured,
        vlm_configured=config.vlm is not None,
        app_version=build.version,
        commit=build.commit,
        log_dir=log_dir,
        capabilities=compute_capabilities(config, local_failures=local_failures),
    )
