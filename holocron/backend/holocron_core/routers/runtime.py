"""`GET /v1/runtime`: trạng thái chạy cho UI (SPECIFICATION §5.12)."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from ..domain.capabilities import Capability
from ..services.capabilities import compute_capabilities
from ..services.extraction import MockDualPassExtractor
from .deps import BuildInfoDep, ExtractorDep, LogDirDep, RuntimeConfigDep

router = APIRouter()


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
    config: RuntimeConfigDep, extractor: ExtractorDep, build: BuildInfoDep, log_dir: LogDirDep
) -> RuntimeInfo:
    return RuntimeInfo(
        demo_mode=isinstance(extractor, MockDualPassExtractor),
        llm_configured=config.llm_configured,
        vlm_configured=config.vlm is not None,
        app_version=build.version,
        commit=build.commit,
        log_dir=log_dir,
        capabilities=compute_capabilities(config),
    )
