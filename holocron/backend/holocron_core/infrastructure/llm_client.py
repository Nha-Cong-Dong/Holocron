"""Adapter Chat Completions kiểu OpenAI qua gateway (spec v0.6 §11.5), chỉ dùng `urllib.request` của thư viện chuẩn.

- URL: `base_url.rstrip("/") + "/chat/completions"`; `base_url` gồm cả phần version (ví dụ `…/v1`). Chỉ nhận
  `http`/`https` (urllib còn mở được `file:` và `ftp:`).
- Header: `Authorization: Bearer <key>`, `Content-Type: application/json`, `Accept: application/json` và
  `User-Agent` riêng (`Holocron/<version>`): gateway hiện tại chặn User-Agent mặc định của urllib bằng 403
  "error code: 1010".
- Body chỉ gồm `model`, `messages`, `max_tokens`. KHÔNG gửi `temperature`, `top_p` hay `response_format`: nơi gọi
  tự parse và kiểm JSON.
- Timeout mỗi lần thử là `HOLOCRON_LLM_TIMEOUT_S`. Thử lại tối đa `HTTP_RETRY_MAX` lần (backoff 0,5 s rồi 1 s) khi
  gặp 429, 5xx hoặc lỗi mạng; mã khác (kể cả 401, 403, 3xx) thất bại ngay. Không theo redirect, để header
  `Authorization` không bị gửi sang host khác.
- Khoá là `SecretStr`, chỉ lộ giá trị lúc dựng header; không có trong `repr`, log hay thông điệp lỗi. Thông điệp lỗi
  không chứa địa chỉ endpoint, tên model, body request hay body phản hồi.
- Khoá không hợp lệ cho header (khoảng trắng, xuống dòng, ký tự điều khiển, ngoài ASCII) bị từ chối ngay khi tạo
  client (`ConfigError` chỉ nêu tên biến). Lỗi lúc dựng hay gửi request (lỗi mạng và lỗi dữ liệu
  `REQUEST_DATA_ERRORS` mà urllib, http.client hay json ném ra, ví dụ `ValueError` "Invalid header value" của
  `putheader`) đều thành `LlmUpstreamError` chỉ nêu tên lớp lỗi; lỗi gốc (có thể mang nguyên giá trị header) KHÔNG được
  giữ trong `__cause__` hay `__context__`.
"""

from __future__ import annotations

import http.client
import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from importlib import metadata

from pydantic import BaseModel, Field, SecretStr, ValidationError

from ..domain.llm import ChatCompletionResult, ChatMessage, LlmUpstreamError, TokenUsage
from .llm_config import (
    ENV_LLM_API_KEY,
    ENV_LLM_BASE_URL,
    ENV_LLM_MODEL_EXTRACT,
    ConfigError,
    LlmSettings,
    has_control_characters,
    is_valid_key,
)

HTTP_RETRY_MAX = 2
BACKOFF_BASE_S = 0.5
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
PACKAGE_NAME = "holocron-core"

# Lỗi dữ liệu mà urllib, http.client và json có thể ném khi dựng hoặc gửi request: giá trị header sai (`ValueError`,
# kể cả `UnicodeEncodeError`), URL sai (`ValueError`), kiểu body sai (`TypeError`), codec (`LookupError`). Thông điệp
# của chúng có thể chứa nguyên giá trị header nên adapter không bao giờ giữ lại exception gốc.
REQUEST_DATA_ERRORS: tuple[type[Exception], ...] = (ValueError, TypeError, LookupError)


def package_version() -> str:
    """Version của gói đã cài; chạy từ mã nguồn chưa cài thì `"dev"`."""
    try:
        return metadata.version(PACKAGE_NAME)
    except metadata.PackageNotFoundError:
        return "dev"


USER_AGENT = f"Holocron/{package_version()}"


Transport = Callable[[urllib.request.Request, float], tuple[int, bytes]]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Không theo redirect: urllib sẽ ném `HTTPError` với mã 3xx thay vì gửi lại header sang địa chỉ mới."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> urllib.request.Request | None:
        return None


class UrllibTransport:
    """Gửi một request, trả `(mã HTTP, body)`. Lỗi mạng (`OSError`, `http.client.HTTPException`) được ném tiếp.

    `handlers`: handler urllib bổ sung cho opener (test dùng handler giả để không gọi mạng); redirect luôn bị chặn.
    """

    def __init__(self, *handlers: urllib.request.BaseHandler) -> None:
        self._opener = urllib.request.build_opener(_NoRedirect, *handlers)

    def __call__(self, request: urllib.request.Request, timeout_s: float) -> tuple[int, bytes]:
        try:
            response = self._opener.open(request, timeout=timeout_s)
        except urllib.error.HTTPError as exc:
            exc.close()
            return exc.code, b""
        try:
            body = response.read(MAX_RESPONSE_BYTES + 1)
        finally:
            response.close()
        if len(body) > MAX_RESPONSE_BYTES:
            raise LlmUpstreamError("phản hồi của LLM vượt giới hạn kích thước")
        return response.status, body


class _Message(BaseModel):
    content: str


class _Choice(BaseModel):
    message: _Message


class _Usage(BaseModel):
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)


class _Envelope(BaseModel):
    choices: list[_Choice] = Field(min_length=1)
    usage: _Usage | None = None

    def token_usage(self) -> TokenUsage | None:
        """`usage` khi nhà cung cấp trả đủ hai số; thiếu thì `None` (không coi là lỗi)."""
        if self.usage is None or self.usage.prompt_tokens is None or self.usage.completion_tokens is None:
            return None
        return TokenUsage(prompt_tokens=self.usage.prompt_tokens, completion_tokens=self.usage.completion_tokens)


def _retryable(status: int) -> bool:
    return status == 429 or 500 <= status <= 599


class OpenAiChatClient:
    """`ChatCompletionClient` cho một endpoint tương thích OpenAI Chat Completions."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: SecretStr,
        model: str,
        timeout_s: float,
        retry_max: int = HTTP_RETRY_MAX,
        transport: Transport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not base_url.lower().startswith(("https://", "http://")) or has_control_characters(base_url):
            raise ConfigError(f"{ENV_LLM_BASE_URL} không hợp lệ: cần bắt đầu bằng https:// hoặc http://")
        if not is_valid_key(api_key.get_secret_value()):
            raise ConfigError(
                f"{ENV_LLM_API_KEY} không hợp lệ: chỉ được chứa ký tự ASCII in được, không có khoảng trắng, "
                "xuống dòng hay ký tự điều khiển"
            )
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._api_key = api_key
        self._model = model
        self._timeout_s = timeout_s
        self._retry_max = retry_max
        self._transport: Transport = transport or UrllibTransport()
        self._sleep = sleep

    @classmethod
    def from_settings(cls, settings: LlmSettings, *, model: str | None) -> OpenAiChatClient:
        """Client cho `model` từ cấu hình; thiếu biến ⇒ `ConfigError` (nêu tên biến, không nêu giá trị).

        `model` là `None` khi biến model tương ứng chưa đặt; khi đó báo thiếu `HOLOCRON_LLM_MODEL_EXTRACT`.
        """
        missing = [name for name in settings.missing if name != ENV_LLM_MODEL_EXTRACT]
        if model is None:
            missing.append(ENV_LLM_MODEL_EXTRACT)
        if settings.base_url is None or settings.api_key is None or model is None:
            raise ConfigError(f"chưa đặt biến: {', '.join(missing)}")
        return cls(base_url=settings.base_url, api_key=settings.api_key, model=model, timeout_s=settings.timeout_s)

    def __repr__(self) -> str:
        return f"OpenAiChatClient(timeout_s={self._timeout_s}, retry_max={self._retry_max})"

    def build_request(self, messages: Sequence[ChatMessage], *, max_tokens: int) -> urllib.request.Request:
        """Request POST đã dựng đủ header và body; không gửi gì."""
        body = {
            "model": self._model,
            "messages": [message.model_dump() for message in messages],
            "max_tokens": max_tokens,
        }
        return urllib.request.Request(
            self._url,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {self._api_key.get_secret_value()}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
            },
        )

    def complete(self, messages: Sequence[ChatMessage], *, max_tokens: int) -> ChatCompletionResult:
        # Lỗi được ghi lại thành chuỗi trong khối `except` rồi ném SAU khối đó, để exception mới không mang
        # `__context__` là lỗi gốc (lỗi của http.client có thể chứa nguyên giá trị header `Authorization`).
        request: urllib.request.Request | None = None
        failure = ""
        try:
            request = self.build_request(messages, max_tokens=max_tokens)
        except REQUEST_DATA_ERRORS as exc:
            failure = f"không dựng được yêu cầu gửi LLM ({type(exc).__name__})"
        if request is None:
            raise LlmUpstreamError(failure, attempts=0)
        last_failure = ""
        for attempt in range(1, self._retry_max + 2):
            if attempt > 1:
                self._sleep(BACKOFF_BASE_S * 2 ** (attempt - 2))
            outcome = self._send(request)
            if isinstance(outcome, _Failure):
                if outcome.retryable:
                    last_failure = outcome.message
                    continue
                raise LlmUpstreamError(outcome.message, attempts=attempt)
            status, body = outcome
            if _retryable(status):
                last_failure = f"HTTP {status}"
                continue
            if not 200 <= status <= 299:
                raise LlmUpstreamError(f"LLM từ chối yêu cầu (HTTP {status})", attempts=attempt)
            return _parse(body, attempt)
        attempts = self._retry_max + 1
        raise LlmUpstreamError(f"LLM không phản hồi được sau {attempts} lần thử ({last_failure})", attempts=attempts)

    def _send(self, request: urllib.request.Request) -> tuple[int, bytes] | _Failure:
        """Một lần gửi. Lỗi chỉ được giữ lại dưới dạng tên lớp và thông điệp do adapter đặt, không giữ exception gốc."""
        try:
            return self._transport(request, self._timeout_s)
        except (OSError, http.client.HTTPException) as exc:
            return _Failure(f"lỗi mạng {type(exc).__name__}", retryable=True)
        except LlmUpstreamError as exc:
            return _Failure(str(exc), retryable=False)
        except REQUEST_DATA_ERRORS as exc:
            return _Failure(f"không gửi được yêu cầu tới LLM ({type(exc).__name__})", retryable=False)


class _Failure:
    """Kết quả thất bại của một lần gửi: thông điệp an toàn (không khoá, header, endpoint hay body)."""

    __slots__ = ("message", "retryable")

    def __init__(self, message: str, *, retryable: bool) -> None:
        self.message = message
        self.retryable = retryable


def _parse(body: bytes, attempts: int) -> ChatCompletionResult:
    """Lấy `choices[0].message.content` và `usage`; phản hồi sai dạng ⇒ `LlmUpstreamError` (không kèm nội dung)."""
    envelope: _Envelope | None = None
    try:
        envelope = _Envelope.model_validate_json(body)
    except ValidationError:
        envelope = None
    if envelope is None:
        raise LlmUpstreamError("phản hồi của LLM không đúng dạng chat completions", attempts=attempts)
    return ChatCompletionResult(
        content=envelope.choices[0].message.content, usage=envelope.token_usage(), attempts=attempts
    )
