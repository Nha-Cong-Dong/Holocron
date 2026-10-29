"""`GET /v1/runtime`: trạng thái chạy cho UI (SPECIFICATION §5.12).

Kiểm cục bộ của chế độ khả năng (Tesseract, gói `vie`, lượt đọc thứ hai; `services/capability_checks.py`) chạy MỘT
LẦN lúc khởi động (`run_local_capability_check`, gọi trong `lifespan` của `api.py`) và lưu ở `app.state`; mọi
request đọc lại kết quả đã lưu qua `get_local_failures`, không probe lại. Bộ kiểm cấp qua `get_local_capability_check`
để test thay bằng `app.dependency_overrides` (không phụ thuộc máy chạy test có Tesseract hay không).
"""

from __future__ import annotations

import functools
from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, Request
from pydantic import BaseModel

from ..domain.capabilities import Capability, CapabilityName, CapabilityReason
from ..infrastructure.document_readers.config import load_ocr_settings
from ..services.capabilities import compute_capabilities
from ..services.capability_checks import check_local_capabilities, probe_tesseract
from ..services.extraction import MockDualPassExtractor
from .deps import BuildInfoDep, ExtractorDep, LogDirDep, RuntimeConfigDep

router = APIRouter()

LOCAL_FAILURES_STATE_ATTR = "capability_local_failures"

LocalFailures = Mapping[CapabilityName, CapabilityReason]
LocalCapabilityCheck = Callable[[], dict[CapabilityName, CapabilityReason]]
# Giá trị mặc định cho hàm dependency gọi trực tiếp ngoài FastAPI (không có app nên không có kết quả kiểm cục bộ).
NO_LOCAL_FAILURES: LocalFailures = MappingProxyType({})


def get_local_capability_check() -> LocalCapabilityCheck:
    """Bộ kiểm cục bộ thật: cấu hình OCR từ biến môi trường, probe Tesseract bằng binary đã cấu hình."""
    return functools.partial(check_local_capabilities, load_ocr_settings, probe_tesseract)


def run_local_capability_check(application: FastAPI) -> None:
    """Kiểm cục bộ một lần và lưu vào `application.state`; gọi lúc khởi động. Bộ kiểm lấy theo override của app."""
    provider: Callable[[], LocalCapabilityCheck] = application.dependency_overrides.get(
        get_local_capability_check, get_local_capability_check
    )
    setattr(application.state, LOCAL_FAILURES_STATE_ATTR, provider()())


def clear_local_capability_check(application: FastAPI) -> None:
    """Bỏ kết quả đã lưu khi tắt app, để lần khởi động sau trong cùng tiến trình kiểm lại."""
    if hasattr(application.state, LOCAL_FAILURES_STATE_ATTR):
        delattr(application.state, LOCAL_FAILURES_STATE_ATTR)


def get_local_failures(
    request: Request, check: Annotated[LocalCapabilityCheck, Depends(get_local_capability_check)]
) -> LocalFailures:
    """Kết quả kiểm cục bộ đã lưu lúc khởi động.

    App chạy không qua `lifespan` (ví dụ `TestClient` không dùng `with`) thì kiểm ở request đầu tiên rồi lưu lại
    như lúc khởi động; các request sau dùng kết quả đã lưu.
    """
    state = request.app.state
    stored: dict[CapabilityName, CapabilityReason] | None = getattr(state, LOCAL_FAILURES_STATE_ATTR, None)
    if stored is None:
        stored = check()
        setattr(state, LOCAL_FAILURES_STATE_ATTR, stored)
    return stored


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
