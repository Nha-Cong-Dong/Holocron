"""`GET /v1/runtime`: trạng thái chạy cho UI (SPECIFICATION §5.12).

Kiểm cục bộ của chế độ khả năng (Tesseract, gói `vie`, lượt đọc thứ hai; `services/capability_checks.py`) chạy MỘT
LẦN lúc khởi động (`run_local_capability_check`, gọi trong `lifespan` của `api.py` sau khi tạo bộ nhập tài liệu, với
`reader_config` của bộ nhập đó) và lưu ở `app.state` dưới dạng `MappingProxyType` (request không sửa được); mọi
request đọc lại kết quả đã lưu qua `get_local_failures`, không probe lại. Bộ kiểm cấp qua `get_local_capability_check`
để test thay bằng `app.dependency_overrides` (không phụ thuộc máy chạy test có Tesseract hay RapidOCR hay không).
"""

from __future__ import annotations

import functools
from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, Request
from pydantic import BaseModel

from ..domain.capabilities import Capability, CapabilityName, CapabilityReason
from ..infrastructure.document_readers.config import PdfReaderConfig, load_ocr_settings
from ..infrastructure.document_readers.rapidocr_adapter import RapidOcrBackend
from ..services.capabilities import compute_capabilities
from ..services.capability_checks import SecondaryLoader, check_local_capabilities, probe_tesseract
from ..services.document_import import ImportRuntime
from ..services.extraction import MockDualPassExtractor
from .deps import BuildInfoDep, ExtractorDep, LogDirDep, RuntimeConfigDep
from .documents import RUNTIME_STATE_ATTR

router = APIRouter()

LOCAL_FAILURES_STATE_ATTR = "capability_local_failures"

LocalFailures = Mapping[CapabilityName, CapabilityReason]
LocalCapabilityCheck = Callable[[PdfReaderConfig], dict[CapabilityName, CapabilityReason]]
# Giá trị mặc định cho hàm dependency gọi trực tiếp ngoài FastAPI (không có app nên không có kết quả kiểm cục bộ).
NO_LOCAL_FAILURES: LocalFailures = MappingProxyType({})
# Bộ nạp lượt đọc thứ hai mà bộ kiểm thật dùng: đúng bộ nạp của worker (`_pdf_worker.build_ocr`).
load_secondary_ocr: SecondaryLoader = RapidOcrBackend.load


def get_local_capability_check() -> LocalCapabilityCheck:
    """Bộ kiểm cục bộ thật: cấu hình OCR từ biến môi trường, probe Tesseract bằng binary đã cấu hình, nạp RapidOCR."""
    return functools.partial(check_local_capabilities, load_ocr_settings, probe_tesseract, load_secondary_ocr)


def _store_local_failures(application: FastAPI, failures: Mapping[CapabilityName, CapabilityReason]) -> LocalFailures:
    stored: LocalFailures = MappingProxyType(dict(failures))
    setattr(application.state, LOCAL_FAILURES_STATE_ATTR, stored)
    return stored


def run_local_capability_check(application: FastAPI, reader_config: PdfReaderConfig) -> None:
    """Kiểm cục bộ một lần với `reader_config` của bộ nhập và lưu vào `application.state`; gọi lúc khởi động.

    Bộ kiểm lấy theo override của app.
    """
    provider: Callable[[], LocalCapabilityCheck] = application.dependency_overrides.get(
        get_local_capability_check, get_local_capability_check
    )
    _store_local_failures(application, provider()(reader_config))


def clear_local_capability_check(application: FastAPI) -> None:
    """Bỏ kết quả đã lưu khi tắt app, để lần khởi động sau trong cùng tiến trình kiểm lại."""
    if hasattr(application.state, LOCAL_FAILURES_STATE_ATTR):
        delattr(application.state, LOCAL_FAILURES_STATE_ATTR)


def get_local_failures(
    request: Request, check: Annotated[LocalCapabilityCheck, Depends(get_local_capability_check)]
) -> LocalFailures:
    """Kết quả kiểm cục bộ đã lưu lúc khởi động.

    App chạy không qua `lifespan` (ví dụ `TestClient` không dùng `with`) thì kiểm ở request đầu tiên rồi lưu lại
    như lúc khởi động; các request sau dùng kết quả đã lưu. Khi đó không có bộ nhập tài liệu nên dùng cấu hình bộ
    đọc mặc định, đúng cấu hình `create_import_runtime` tạo khi không được truyền cấu hình.
    """
    application: FastAPI = request.app
    stored: LocalFailures | None = getattr(application.state, LOCAL_FAILURES_STATE_ATTR, None)
    if stored is not None:
        return stored
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
