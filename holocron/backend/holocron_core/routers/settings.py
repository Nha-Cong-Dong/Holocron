"""Cài đặt "Kết nối AI" (spec §6.11; ADR-009 mục 8, 12.7, 12.10).

V02 có `DELETE /v1/settings/ai-cache` (xoá toàn bộ cache AI); V04 tối thiểu thêm `GET/PUT /v1/settings/ai-consent`
(công tắc đồng ý lưu ở `HOLOCRON_DATA_DIR/ai_settings.json`; `HOLOCRON_AI_CONSENT` đặt ⇒ khoá, 409
`consent_env_locked`; không có thư mục dữ liệu ⇒ 409 `settings_not_persistent`). Phần nhà cung cấp làm ở V04 đầy đủ.

Bảo vệ endpoint ghi (ADR-009 mục 12.7, phương án 1): CHƯA đặt `HOLOCRON_REQUIRE_TOKEN` ⇒ 403 fail-closed với
`code = "token_not_configured"`; đã đặt ⇒ middleware `LocalAccessMiddleware` đã bắt buộc `X-Holocron-Token` trước khi
request tới đây (thiếu/sai ⇒ 403). Không kiểm IP nguồn (mọi request qua proxy Next.js đến từ loopback).

422 của router này chỉ giữ `loc`, `msg`, `type` của từng lỗi (che `input`, `ctx`) để không vọng giá trị đã gửi (§6.11).
Lỗi nghiệp vụ dạng `{"detail": {"code", "message"}}`, câu tiếng Việt cố định.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Coroutine
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field

from ..domain.ai import AiConsentState, AiConsentStore, SettingsNotPersistentError
from ..domain.repositories import AppStore
from ..domain.schemas import StrictModel
from ..infrastructure.ai_runtime import ai_consent_env_locked, read_consent_state
from .access import get_required_token
from .deps import get_ai_consent_store, get_store

TOKEN_NOT_CONFIGURED_CODE = "token_not_configured"
TOKEN_NOT_CONFIGURED_DETAIL = (
    "Chưa đặt mã truy cập: đặt HOLOCRON_REQUIRE_TOKEN rồi nhập mã ở trang Cài đặt để thay đổi kết nối AI"
)

CONSENT_ENV_LOCKED_CODE = "consent_env_locked"
CONSENT_ENV_LOCKED_MESSAGE = (
    "Công tắc đồng ý đang được đặt bằng biến môi trường HOLOCRON_AI_CONSENT nên không đổi được ở trang Cài đặt"
)
SETTINGS_NOT_PERSISTENT_CODE = "settings_not_persistent"
SETTINGS_NOT_PERSISTENT_MESSAGE = (
    "Không lưu được cài đặt: chưa đặt thư mục dữ liệu HOLOCRON_DATA_DIR; dùng biến môi trường HOLOCRON_AI_CONSENT"
)
VALIDATION_ERROR_FIELDS = ("loc", "msg", "type")


class TokenNotConfiguredError(Exception):
    """Endpoint ghi cài đặt khi chưa đặt `HOLOCRON_REQUIRE_TOKEN`; `MaskedValidationRoute` đổi thành 403."""


def require_write_token(required_token: Annotated[str | None, Depends(get_required_token)]) -> None:
    """Fail-closed cho endpoint ghi: chạy trước khi validate body, nên chưa đặt token luôn là 403 (không phải 422)."""
    if required_token is None:
        raise TokenNotConfiguredError


class MaskedValidationRoute(APIRoute):
    """422 chỉ giữ `loc`, `msg`, `type` của từng lỗi (che `input`, `ctx`), riêng cho router cài đặt (§6.11); lỗi
    `TokenNotConfiguredError` ⇒ 403 `token_not_configured`."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def masked_handler(request: Request) -> Response:
            try:
                return await handler(request)
            except TokenNotConfiguredError:
                return _token_not_configured()
            except RequestValidationError as exc:
                errors = [
                    {field: error[field] for field in VALIDATION_ERROR_FIELDS if field in error}
                    for error in exc.errors()
                ]
                return JSONResponse(status_code=422, content={"detail": errors})

        return masked_handler


router = APIRouter(prefix="/v1/settings", tags=["settings"], route_class=MaskedValidationRoute)


class AiConsentUpdate(StrictModel):
    """Body `PUT /v1/settings/ai-consent`."""

    enabled: bool


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


def _business_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


ConsentStoreDep = Annotated[AiConsentStore, Depends(get_ai_consent_store)]


@router.get("/ai-consent", response_model=AiConsentState)
def get_ai_consent_state(store: ConsentStoreDep) -> AiConsentState:
    return read_consent_state(os.environ, store)


@router.put(
    "/ai-consent",
    response_model=AiConsentState,
    dependencies=[Depends(require_write_token)],
    responses={
        403: {"description": "Chưa đặt HOLOCRON_REQUIRE_TOKEN (token_not_configured) hoặc thiếu/sai token"},
        409: {"description": "consent_env_locked hoặc settings_not_persistent"},
    },
)
def put_ai_consent(body: AiConsentUpdate, store: ConsentStoreDep) -> AiConsentState:
    if ai_consent_env_locked(os.environ):
        raise _business_error(409, CONSENT_ENV_LOCKED_CODE, CONSENT_ENV_LOCKED_MESSAGE)
    if not store.persistent:
        raise _business_error(409, SETTINGS_NOT_PERSISTENT_CODE, SETTINGS_NOT_PERSISTENT_MESSAGE)
    try:
        store.save(body.enabled)
    except SettingsNotPersistentError:
        raise _business_error(409, SETTINGS_NOT_PERSISTENT_CODE, SETTINGS_NOT_PERSISTENT_MESSAGE) from None
    return read_consent_state(os.environ, store)
