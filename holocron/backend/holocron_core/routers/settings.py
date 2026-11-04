"""Cài đặt "Kết nối AI" (spec §6.11; ADR-009 mục 8, 12.7, 12.10).

V02 có `DELETE /v1/settings/ai-cache` (xoá toàn bộ cache AI); V04 tối thiểu thêm `GET/PUT /v1/settings/ai-consent`
(công tắc đồng ý lưu ở `HOLOCRON_DATA_DIR/ai_settings.json`; `HOLOCRON_AI_CONSENT` đặt ⇒ khoá, 409
`consent_env_locked`; không có thư mục dữ liệu ⇒ 409 `settings_not_persistent`) và nhà cung cấp người dùng
(`GET/POST /v1/settings/providers`, `PATCH/DELETE /v1/settings/providers/{id}`, `POST …/{id}/test`). Khoá chỉ ở
backend; response chỉ có `key_last4`; nhà cung cấp env chỉ đọc (409 `provider_read_only`).

Bảo vệ endpoint ghi (ADR-009 mục 12.7, phương án 1): CHƯA đặt `HOLOCRON_REQUIRE_TOKEN` ⇒ 403 fail-closed với
`code = "token_not_configured"`; đã đặt ⇒ middleware `LocalAccessMiddleware` đã bắt buộc `X-Holocron-Token` trước khi
request tới đây (thiếu/sai ⇒ 403). Không kiểm IP nguồn (mọi request qua proxy Next.js đến từ loopback).

422 của router này chỉ giữ `loc`, `msg`, `type` của từng lỗi (che `input`, `ctx`) để không vọng giá trị đã gửi (§6.11).
Lỗi nghiệp vụ dạng `{"detail": {"code", "message"}}`, câu tiếng Việt cố định.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Coroutine
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field

from ..domain.ai import (
    PROVIDER_ID_PATTERN,
    AiConsentState,
    AiConsentStore,
    KeyStorageError,
    ProviderConfig,
    ProviderCreate,
    ProviderInputError,
    ProviderLimitError,
    ProviderList,
    ProviderNotFoundError,
    ProviderTestRequest,
    ProviderTestResult,
    ProviderUpdate,
    SettingsNotPersistentError,
    WriteProtection,
)
from ..domain.repositories import AppStore
from ..domain.schemas import StrictModel
from ..infrastructure.ai_runtime import ai_consent_env_locked, read_consent_state
from ..infrastructure.provider_probe import probe_estimate
from ..infrastructure.provider_store import ProviderStore
from ..services.provider_selection import ProviderSelection
from ..services.provider_settings import build_provider_list
from .access import get_required_token
from .deps import ProviderProbeDep, ProviderSelectionDep, ProviderStoreDep, get_ai_consent_store, get_store

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


# --------------------------------------------------------------------------- #
# Nhà cung cấp (spec §6.11)
# --------------------------------------------------------------------------- #

PROVIDER_NOT_FOUND_MESSAGE = "Không có nhà cung cấp này"
PROVIDER_READ_ONLY_CODE = "provider_read_only"
PROVIDER_READ_ONLY_MESSAGE = "Nhà cung cấp cấu hình bằng biến môi trường chỉ đọc; đổi biến môi trường rồi khởi động lại"
PROVIDER_LIMIT_CODE = "provider_limit"
PROVIDER_LIMIT_MESSAGE = "Đã có đủ 20 nhà cung cấp; xoá bớt trước khi thêm"
PROVIDERS_NOT_PERSISTENT_MESSAGE = (
    "Không lưu được nhà cung cấp: chưa đặt thư mục dữ liệu HOLOCRON_DATA_DIR; dùng biến môi trường HOLOCRON_LLM_*, "
    "HOLOCRON_VLM_*"
)
KEY_STORAGE_FAILED_CODE = "key_storage_failed"
KEY_STORAGE_FAILED_MESSAGE = "Không mã hoá được khoá để lưu (DPAPI lỗi); chưa lưu gì, thử lại"
_ENV_PROVIDER_ID = re.compile(r"^env_[a-z]+$")
_PROVIDER_ID = re.compile(PROVIDER_ID_PATTERN)

WRITE_RESPONSES: dict[int | str, dict[str, Any]] = {
    403: {"description": "Chưa đặt HOLOCRON_REQUIRE_TOKEN (token_not_configured) hoặc thiếu/sai token"},
}
TokenDep = Annotated[str | None, Depends(get_required_token)]
EarlyCode = Literal["key_unreadable", "no_model_for_role", "budget_exhausted", "blocked_address"]


def _write_protection(required_token: str | None) -> WriteProtection:
    return "token_not_configured" if required_token is None else "token"


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail=PROVIDER_NOT_FOUND_MESSAGE)


def _not_persistent() -> HTTPException:
    return _business_error(409, SETTINGS_NOT_PERSISTENT_CODE, PROVIDERS_NOT_PERSISTENT_MESSAGE)


def _provider_list(
    store: ProviderStore, selection: ProviderSelection, write_protection: WriteProtection
) -> ProviderList:
    return build_provider_list(
        selection.candidates(),
        store.entries(),
        selection.spent_usd,
        settings_persistent=store.persistent,
        write_protection=write_protection,
    )


def _provider_config(provider_id: str, store: ProviderStore, selection: ProviderSelection) -> ProviderConfig:
    for config in _provider_list(store, selection, "token").providers:
        if config.id == provider_id:
            return config
    raise _not_found()


def _check_writable_id(provider_id: str, store: ProviderStore) -> None:
    """Env ⇒ 409 `provider_read_only`; không lưu được ⇒ 409 `settings_not_persistent`; id sai dạng ⇒ 404."""
    if _ENV_PROVIDER_ID.fullmatch(provider_id):
        raise _business_error(409, PROVIDER_READ_ONLY_CODE, PROVIDER_READ_ONLY_MESSAGE)
    if not store.persistent:
        raise _not_persistent()
    if not _PROVIDER_ID.fullmatch(provider_id):
        raise _not_found()


def _input_error(exc: ProviderInputError) -> HTTPException:
    return _business_error(422, exc.code, exc.message)


def _key_storage_failed() -> HTTPException:
    return _business_error(500, KEY_STORAGE_FAILED_CODE, KEY_STORAGE_FAILED_MESSAGE)


@router.get("/providers", response_model=ProviderList)
def list_providers(store: ProviderStoreDep, selection: ProviderSelectionDep, required_token: TokenDep) -> ProviderList:
    return _provider_list(store, selection, _write_protection(required_token))


@router.post(
    "/providers",
    status_code=201,
    response_model=ProviderConfig,
    dependencies=[Depends(require_write_token)],
    responses={
        **WRITE_RESPONSES,
        409: {"description": "settings_not_persistent hoặc provider_limit"},
        500: {"description": "key_storage_failed"},
    },
)
def create_provider(body: ProviderCreate, store: ProviderStoreDep, selection: ProviderSelectionDep) -> ProviderConfig:
    if not store.persistent:
        raise _not_persistent()
    try:
        entry = store.create(body)
    except SettingsNotPersistentError:
        raise _not_persistent() from None
    except ProviderLimitError:
        raise _business_error(409, PROVIDER_LIMIT_CODE, PROVIDER_LIMIT_MESSAGE) from None
    except ProviderInputError as exc:
        raise _input_error(exc) from None
    except KeyStorageError:
        raise _key_storage_failed() from None
    return _provider_config(entry.id, store, selection)


@router.patch(
    "/providers/{provider_id}",
    response_model=ProviderConfig,
    dependencies=[Depends(require_write_token)],
    responses={
        **WRITE_RESPONSES,
        404: {"description": "Không có nhà cung cấp"},
        409: {"description": "provider_read_only hoặc settings_not_persistent"},
        500: {"description": "key_storage_failed"},
    },
)
def update_provider(
    provider_id: str, body: ProviderUpdate, store: ProviderStoreDep, selection: ProviderSelectionDep
) -> ProviderConfig:
    _check_writable_id(provider_id, store)
    try:
        store.update(provider_id, body)
    except SettingsNotPersistentError:
        raise _not_persistent() from None
    except ProviderNotFoundError:
        raise _not_found() from None
    except ProviderInputError as exc:
        raise _input_error(exc) from None
    except KeyStorageError:
        raise _key_storage_failed() from None
    return _provider_config(provider_id, store, selection)


@router.delete(
    "/providers/{provider_id}",
    status_code=204,
    dependencies=[Depends(require_write_token)],
    responses={
        **WRITE_RESPONSES,
        404: {"description": "Không có nhà cung cấp"},
        409: {"description": "provider_read_only hoặc settings_not_persistent"},
    },
)
def delete_provider(provider_id: str, store: ProviderStoreDep) -> Response:
    _check_writable_id(provider_id, store)
    try:
        store.delete(provider_id)
    except SettingsNotPersistentError:
        raise _not_persistent() from None
    except ProviderNotFoundError:
        raise _not_found() from None
    return Response(status_code=204)


def _early_result(code: EarlyCode) -> ProviderTestResult:
    return ProviderTestResult(ok=False, code=code, http_status=None, latency_ms=0)


@router.post(
    "/providers/{provider_id}/test",
    response_model=ProviderTestResult,
    dependencies=[Depends(require_write_token)],
    responses={**WRITE_RESPONSES, 404: {"description": "Không có nhà cung cấp"}},
)
def test_provider(
    provider_id: str,
    body: ProviderTestRequest,
    store: ProviderStoreDep,
    selection: ProviderSelectionDep,
    probe: ProviderProbeDep,
) -> ProviderTestResult:
    """Không bị công tắc đồng ý chặn (không gửi nội dung tài liệu); có tính vào bộ đếm chi phí (§6.11)."""
    candidate = next((item for item in selection.candidates() if item.provider_id == provider_id), None)
    if candidate is None:
        entry = next((item for item in store.entries() if item.id == provider_id), None)
        if entry is None:
            raise _not_found()
        # Mục người dùng không có ứng viên: khoá không đọc được, hoặc URL đã lưu không còn qua kiểm tĩnh.
        return _early_result("key_unreadable" if entry.key_unreadable else "blocked_address")
    model = "" if body.role is None else candidate.model_for(body.role)
    if model is None:
        return _early_result("no_model_for_role")
    selected = selection.reserve(provider_id, model, probe_estimate(body))
    if selected is None:
        return _early_result("budget_exhausted")
    try:
        outcome = probe.run(selected, body)
    except BaseException:
        selection.release(selected.reservation_id)
        raise
    if outcome.usage is None:
        selection.release(selected.reservation_id)
    else:
        selection.record(selected.reservation_id, outcome.usage)
    return outcome.result
