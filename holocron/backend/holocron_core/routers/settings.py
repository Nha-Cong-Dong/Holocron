"""Cài đặt "Kết nối AI" (spec §6.11; ADR-009 mục 8, 12.7, 12.10).

V02 chỉ có `DELETE /v1/settings/ai-cache` (xoá toàn bộ cache AI); V04 thêm phần còn lại của router.

Bảo vệ endpoint ghi (ADR-009 mục 12.7, phương án 1): CHƯA đặt `HOLOCRON_REQUIRE_TOKEN` ⇒ 403 fail-closed với
`code = "token_not_configured"`; đã đặt ⇒ middleware `LocalAccessMiddleware` đã bắt buộc `X-Holocron-Token` trước khi
request tới đây (thiếu/sai ⇒ 403). Không kiểm IP nguồn (mọi request qua proxy Next.js đến từ loopback).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..domain.repositories import AppStore
from .access import get_required_token
from .deps import get_store

TOKEN_NOT_CONFIGURED_CODE = "token_not_configured"
TOKEN_NOT_CONFIGURED_DETAIL = (
    "Chưa đặt mã truy cập: đặt HOLOCRON_REQUIRE_TOKEN rồi nhập mã ở trang Cài đặt để thay đổi kết nối AI"
)

router = APIRouter(prefix="/v1/settings", tags=["settings"])


class AiCacheCleared(BaseModel):
    """Response `DELETE /v1/settings/ai-cache`: số dòng `ai_cache` đã xoá (liên kết tài liệu xoá theo)."""

    deleted_entries: int = Field(ge=0)


def _token_not_configured() -> JSONResponse:
    return JSONResponse(
        status_code=403, content={"detail": TOKEN_NOT_CONFIGURED_DETAIL, "code": TOKEN_NOT_CONFIGURED_CODE}
    )


@router.delete(
    "/ai-cache",
    response_model=AiCacheCleared,
    responses={403: {"description": "Chưa đặt HOLOCRON_REQUIRE_TOKEN (token_not_configured) hoặc thiếu/sai token"}},
)
def clear_ai_cache(
    store: Annotated[AppStore, Depends(get_store)],
    required_token: Annotated[str | None, Depends(get_required_token)],
) -> AiCacheCleared | JSONResponse:
    if required_token is None:
        return _token_not_configured()
    return AiCacheCleared(deleted_entries=store.clear_ai_cache())
