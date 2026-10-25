"""`GET /v1/runtime`: trạng thái chạy cho UI (SPECIFICATION §5.12)."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from ..services.extraction import MockDualPassExtractor
from .deps import ExtractorDep, RuntimeConfigDep

router = APIRouter()


class RuntimeInfo(BaseModel):
    """Trạng thái chạy cho UI: chỉ cờ boolean, KHÔNG chứa khoá API, tên model hay địa chỉ endpoint."""

    demo_mode: bool
    llm_configured: bool
    vlm_configured: bool


@router.get("/v1/runtime", response_model=RuntimeInfo)
def get_runtime(config: RuntimeConfigDep, extractor: ExtractorDep) -> RuntimeInfo:
    return RuntimeInfo(
        demo_mode=isinstance(extractor, MockDualPassExtractor),
        llm_configured=config.llm_configured,
        vlm_configured=config.vlm is not None,
    )
