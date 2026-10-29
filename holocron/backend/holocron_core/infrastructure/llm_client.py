"""Adapter Chat Completions kiểu OpenAI qua gateway (spec v0.6 §11.5), chỉ dùng `urllib.request` của thư viện chuẩn.

- URL: `base_url.rstrip("/") + "/chat/completions"`; `base_url` gồm cả phần version (ví dụ `…/v1`). Chỉ nhận
  `https://`, hoặc `http://` khi máy chủ là loopback (`check_base_url`; urllib còn mở được `file:` và `ftp:`).
- Header: `Authorization: Bearer <key>`, `Content-Type: application/json`, `Accept: application/json` và
  `User-Agent` riêng (`Holocron/<version>`): gateway hiện tại chặn User-Agent mặc định của urllib bằng 403
  "error code: 1010". Khoá trống (chỉ được phép khi máy chủ là loopback, VLM chạy local, ADR-009 mục 4) ⇒ không gửi
  header `Authorization`.
- `messages[].content` là chuỗi (LLM văn bản; JSON gửi đi y như trước) hoặc mảng phần `text`/`image_url` kiểu OpenAI
  (VLM). Mỗi ảnh (data URI PNG đã kiểm dạng ở `domain/llm.py`) phải ≤ trần byte cấu hình (`HOLOCRON_VLM_MAX_IMAGE_BYTES`)
  TRƯỚC khi gửi; vượt ⇒ `ImageTooLargeError`, không request nào được gửi. Thu ảnh về cạnh dài ≤
  `HOLOCRON_VLM_MAX_EDGE_PX` là việc của bước render, không phải của adapter.
- Client VLM (`from_vlm_settings`) có cận tổng `HOLOCRON_VLM_TIMEOUT_S` cho MỖI lần gọi `complete`, tính từ lúc lần
  gọi bắt đầu.
- Body chỉ gồm `model`, `messages`, `max_tokens`. KHÔNG gửi `temperature`, `top_p` hay `response_format`: nơi gọi
  tự parse và kiểm JSON.
- **Thời gian:** mỗi lần thử có cận TỔNG là `HOLOCRON_LLM_TIMEOUT_S` (không chỉ là timeout của từng thao tác socket):
  transport đọc body theo khối và so đồng hồ đơn điệu với hạn trước mỗi khối, và tiến trình gọi chỉ chờ lần thử trong
  đúng khoảng đó (phần mở kết nối và đọc header chạy trong một luồng phụ có hạn chờ), nên server gửi nhỏ giọt không giữ
  được request quá hạn. Client dựng bằng `from_settings` còn có cận tổng `LlmTaskLimits.deadline_s` cho mọi lần thử
  của nó, tính từ lúc dựng: mỗi lần thử nhận `min(HOLOCRON_LLM_TIMEOUT_S, thời gian còn lại)`, và không thử lại khi
  thời gian còn lại không đủ cho lần chờ backoff. Hết hạn ⇒ `LlmUpstreamError` (502 ở router).
- Thử lại tối đa `LlmHttpLimits.retry_max` lần (backoff `backoff_base_s` rồi gấp đôi) khi gặp 429, 5xx hoặc lỗi mạng
  (kể cả hết thời gian của một lần thử); mã khác (kể cả 401, 403, 3xx) thất bại ngay. Không theo redirect, để header
  `Authorization` không bị gửi sang host khác. Không dùng proxy của hệ thống (`ProxyHandler({})`), để khoá không đi
  qua một proxy người dùng không chọn cho Holocron.
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
import math
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from importlib import metadata
from typing import Protocol

from pydantic import BaseModel, Field, SecretStr, ValidationError

from ..domain.llm import ChatCompletionResult, ChatMessage, ImageTooLargeError, LlmUpstreamError, TokenUsage
from .llm_config import (
    DEFAULT_VLM_MAX_IMAGE_BYTES,
    ENV_LLM_API_KEY,
    ENV_LLM_BASE_URL,
    ENV_LLM_MODEL_EXTRACT,
    ENV_VLM_API_KEY,
    ENV_VLM_BASE_URL,
    ENV_VLM_MAX_IMAGE_BYTES,
    ConfigError,
    LlmHttpLimits,
    LlmSettings,
    VlmSettings,
    check_base_url,
    is_loopback_url,
    is_valid_key,
)

DEFAULT_HTTP_LIMITS = LlmHttpLimits()
# Tên cũ, giữ cho nơi gọi (script đánh giá, test); giá trị là mặc định của `LlmHttpLimits`.
HTTP_RETRY_MAX = DEFAULT_HTTP_LIMITS.retry_max
BACKOFF_BASE_S = DEFAULT_HTTP_LIMITS.backoff_base_s
MAX_RESPONSE_BYTES = DEFAULT_HTTP_LIMITS.max_response_bytes
PACKAGE_NAME = "holocron-core"

# Lỗi dữ liệu mà urllib, http.client và json có thể ném khi dựng hoặc gửi request: giá trị header sai (`ValueError`,
# kể cả `UnicodeEncodeError`), URL sai (`ValueError`), kiểu body sai (`TypeError`), codec (`LookupError`). Thông điệp
# của chúng có thể chứa nguyên giá trị header nên adapter không bao giờ giữ lại exception gốc.
REQUEST_DATA_ERRORS: tuple[type[Exception], ...] = (ValueError, TypeError, LookupError)

# Lỗi mà luồng gửi chuyển về luồng gọi để `_send` phân loại (đúng các nhóm `_send` xử lý).
TRANSFERRED_ERRORS: tuple[type[Exception], ...] = (
    OSError,
    http.client.HTTPException,
    LlmUpstreamError,
    *REQUEST_DATA_ERRORS,
)


def package_version() -> str:
    """Version của gói đã cài; chạy từ mã nguồn chưa cài thì `"dev"`."""
    try:
        return metadata.version(PACKAGE_NAME)
    except metadata.PackageNotFoundError:
        return "dev"


USER_AGENT = f"Holocron/{package_version()}"


Transport = Callable[[urllib.request.Request, float], tuple[int, bytes]]
"""`(request, cận tổng thời gian của lần gửi, giây) → (mã HTTP, body)`."""


class ResponseBody(Protocol):
    """Phần của phản hồi urllib mà `read_within_deadline` cần: đọc tối đa `size` byte bằng nhiều nhất một lần đọc."""

    def read1(self, size: int = -1, /) -> bytes: ...


def read_within_deadline(
    response: ResponseBody,
    *,
    deadline: float,
    monotonic: Callable[[], float],
    max_bytes: int,
    chunk_bytes: int,
) -> bytes:
    """Đọc body theo khối tới hết; trước mỗi khối so `monotonic()` với `deadline`.

    Hết hạn ⇒ `TimeoutError` (lỗi mạng, được thử lại nếu còn lượt và còn thời gian); body dài quá `max_bytes` ⇒
    `LlmUpstreamError`. Mỗi lần đọc dùng `read1`, tức nhiều nhất một lần đọc socket (chờ tối đa timeout của socket),
    nên server gửi nhỏ giọt không giữ được vòng đọc quá hạn.
    """
    pieces: list[bytes] = []
    size = 0
    while True:
        if monotonic() >= deadline:
            raise TimeoutError("hết thời gian đọc phản hồi của LLM")
        piece = response.read1(min(chunk_bytes, max_bytes + 1 - size))
        if not piece:
            return b"".join(pieces)
        pieces.append(piece)
        size += len(piece)
        if size > max_bytes:
            raise LlmUpstreamError("phản hồi của LLM vượt giới hạn kích thước")


def _join(worker: threading.Thread, timeout_s: float) -> None:
    worker.join(timeout_s)


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

    `timeout_s` của mỗi lần gọi là cận TỔNG: socket dùng nó làm timeout từng thao tác, body đọc theo khối với hạn
    `monotonic() + timeout_s`, và tiến trình gọi chỉ chờ luồng gửi tối đa `timeout_s` (qua `wait`); quá hạn ⇒
    `TimeoutError`. Luồng gửi bị bỏ lại khi quá hạn là luồng daemon; nó tự dừng ở lần so hạn kế tiếp khi đang đọc
    body, hoặc khi socket hết timeout. Luồng gửi kết thúc vì một lỗi ngoài `TRANSFERRED_ERRORS` ⇒ `LlmUpstreamError`
    không kèm lỗi gốc.

    `handlers`: handler urllib bổ sung cho opener (test dùng handler giả để không gọi mạng); redirect luôn bị chặn và
    proxy của hệ thống luôn bị bỏ qua. `monotonic` và `wait` cấp cho test (đồng hồ giả, không chờ thật).
    """

    def __init__(
        self,
        *handlers: urllib.request.BaseHandler,
        limits: LlmHttpLimits = DEFAULT_HTTP_LIMITS,
        monotonic: Callable[[], float] = time.monotonic,
        wait: Callable[[threading.Thread, float], None] = _join,
    ) -> None:
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect, *handlers)
        self._limits = limits
        self._monotonic = monotonic
        self._wait = wait

    @property
    def opener(self) -> urllib.request.OpenerDirector:
        return self._opener

    def __call__(self, request: urllib.request.Request, timeout_s: float) -> tuple[int, bytes]:
        deadline = self._monotonic() + timeout_s
        outcome: list[tuple[int, bytes] | Exception] = []

        def exchange() -> None:
            try:
                outcome.append(self._exchange(request, timeout_s, deadline))
            except TRANSFERRED_ERRORS as exc:  # chuyển về luồng gọi; `_send` đổi thành thông điệp an toàn
                outcome.append(exc)

        worker = threading.Thread(target=exchange, name="holocron-llm-http", daemon=True)
        worker.start()
        self._wait(worker, timeout_s)
        if not outcome:
            if worker.is_alive():
                raise TimeoutError("LLM không phản hồi xong trong thời gian cho phép")
            raise LlmUpstreamError("lỗi không mong đợi khi gửi yêu cầu tới LLM")
        result = outcome[0]
        if isinstance(result, Exception):
            raise result
        return result

    def _exchange(self, request: urllib.request.Request, timeout_s: float, deadline: float) -> tuple[int, bytes]:
        try:
            response = self._opener.open(request, timeout=timeout_s)
        except urllib.error.HTTPError as exc:
            exc.close()
            return exc.code, b""
        try:
            status = int(response.status)
            body = read_within_deadline(
                response,
                deadline=deadline,
                monotonic=self._monotonic,
                max_bytes=self._limits.max_response_bytes,
                chunk_bytes=self._limits.read_chunk_bytes,
            )
        finally:
            response.close()
        return status, body


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


def _check_budget(value: float | None, name: str) -> None:
    if value is not None and not (math.isfinite(value) and value > 0):
        raise ValueError(f"{name} phải là số dương hữu hạn")


class OpenAiChatClient:
    """`ChatCompletionClient` cho một endpoint tương thích OpenAI Chat Completions.

    `total_budget_s`: cận tổng thời gian cho mọi lần gọi `complete` của client này, tính từ lúc tạo (`None` = chỉ có cận
    của từng lần thử). `call_budget_s`: cận tổng của MỖI lần gọi `complete` (gồm mọi lần thử và backoff), tính từ lúc
    lần gọi bắt đầu (VLM, `HOLOCRON_VLM_TIMEOUT_S`). Có cả hai thì áp cận chặt hơn. `monotonic` là đồng hồ đơn điệu đo
    các cận đó (test dùng đồng hồ giả).

    `api_key = None` chỉ được phép khi máy chủ của `base_url` là loopback; khi đó request không có header
    `Authorization` (ADR-009 mục 4). `base_url_variable`/`key_variable` là tên biến nêu trong `ConfigError` (LLM văn
    bản hay VLM). Mỗi ảnh trong request phải ≤ `max_image_bytes` byte (sau giải mã), sai ⇒ `ImageTooLargeError`, không
    gửi gì.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: SecretStr | None,
        model: str,
        timeout_s: float,
        retry_max: int = HTTP_RETRY_MAX,
        backoff_base_s: float = BACKOFF_BASE_S,
        total_budget_s: float | None = None,
        call_budget_s: float | None = None,
        max_image_bytes: int = DEFAULT_VLM_MAX_IMAGE_BYTES,
        base_url_variable: str = ENV_LLM_BASE_URL,
        key_variable: str = ENV_LLM_API_KEY,
        transport: Transport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        check_base_url(base_url, base_url_variable)
        if api_key is None:
            if not is_loopback_url(base_url):
                raise ConfigError(f"chưa đặt biến: {key_variable}")
        elif not is_valid_key(api_key.get_secret_value()):
            raise ConfigError(
                f"{key_variable} không hợp lệ: chỉ được chứa ký tự ASCII in được, không có khoảng trắng, "
                "xuống dòng hay ký tự điều khiển"
            )
        _check_budget(total_budget_s, "total_budget_s")
        _check_budget(call_budget_s, "call_budget_s")
        if max_image_bytes < 1:
            raise ValueError("max_image_bytes phải là số nguyên dương")
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._api_key = api_key
        self._model = model
        self._timeout_s = timeout_s
        self._retry_max = retry_max
        self._backoff_base_s = backoff_base_s
        self._transport: Transport = transport or UrllibTransport()
        self._sleep = sleep
        self._monotonic = monotonic
        self._total_budget_s = total_budget_s
        self._deadline = None if total_budget_s is None else monotonic() + total_budget_s
        self._call_budget_s = call_budget_s
        self._max_image_bytes = max_image_bytes

    @classmethod
    def from_settings(cls, settings: LlmSettings, *, model: str | None) -> OpenAiChatClient:
        """Client cho `model` từ cấu hình; thiếu biến ⇒ `ConfigError` (nêu tên biến, không nêu giá trị).

        `model` là `None` khi biến model tương ứng chưa đặt; khi đó báo thiếu `HOLOCRON_LLM_MODEL_EXTRACT`. Client này
        phục vụ một việc: giới hạn HTTP lấy từ `settings.http_limits`, cận tổng thời gian là
        `settings.task_limits.deadline_s` tính từ lúc dựng.
        """
        missing = [name for name in settings.missing if name != ENV_LLM_MODEL_EXTRACT]
        if model is None:
            missing.append(ENV_LLM_MODEL_EXTRACT)
        if settings.base_url is None or settings.api_key is None or model is None:
            raise ConfigError(f"chưa đặt biến: {', '.join(missing)}")
        limits = settings.http_limits
        return cls(
            base_url=settings.base_url,
            api_key=settings.api_key,
            model=model,
            timeout_s=settings.timeout_s,
            retry_max=limits.retry_max,
            backoff_base_s=limits.backoff_base_s,
            total_budget_s=settings.task_limits.deadline_s,
            transport=UrllibTransport(limits=limits),
        )

    @classmethod
    def from_vlm_settings(
        cls,
        settings: VlmSettings,
        *,
        transport: Transport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> OpenAiChatClient:
        """Client cho bộ đọc trang VLM (ADR-009 mục 4) từ cấu hình đã nạp.

        `HOLOCRON_VLM_TIMEOUT_S` vừa là cận của từng lần thử vừa là cận tổng của MỖI lần gọi `complete` (tính từ lúc
        lần gọi bắt đầu, không từ lúc dựng client); trần byte ảnh là `HOLOCRON_VLM_MAX_IMAGE_BYTES`. Khoá trống chỉ có
        với endpoint loopback, khi đó không gửi `Authorization`. `ConfigError` nêu tên biến `HOLOCRON_VLM_*`.
        `transport`, `sleep`, `monotonic` cấp cho test (không gọi mạng, đồng hồ giả); mặc định là urllib và đồng hồ thật.
        """
        limits = settings.http_limits
        return cls(
            base_url=settings.base_url,
            api_key=settings.api_key,
            model=settings.model,
            timeout_s=settings.timeout_s,
            retry_max=limits.retry_max,
            backoff_base_s=limits.backoff_base_s,
            call_budget_s=settings.timeout_s,
            max_image_bytes=settings.max_image_bytes,
            base_url_variable=ENV_VLM_BASE_URL,
            key_variable=ENV_VLM_API_KEY,
            transport=transport or UrllibTransport(limits=limits, monotonic=monotonic),
            sleep=sleep,
            monotonic=monotonic,
        )

    def __repr__(self) -> str:
        return f"OpenAiChatClient(timeout_s={self._timeout_s}, retry_max={self._retry_max})"

    def _check_images(self, messages: Sequence[ChatMessage]) -> None:
        """Mỗi ảnh (sau giải mã) ≤ `max_image_bytes`; vượt ⇒ `ImageTooLargeError` (không nêu nội dung)."""
        for message in messages:
            for part in message.image_parts():
                if part.image_url.decoded_size > self._max_image_bytes:
                    raise ImageTooLargeError(f"ảnh gửi VLM vượt trần byte ({ENV_VLM_MAX_IMAGE_BYTES})")

    def build_request(self, messages: Sequence[ChatMessage], *, max_tokens: int) -> urllib.request.Request:
        """Request POST đã dựng đủ header và body; không gửi gì. Ảnh vượt trần ⇒ `ImageTooLargeError`."""
        self._check_images(messages)
        body = {
            "model": self._model,
            "messages": [message.model_dump() for message in messages],
            "max_tokens": max_tokens,
        }
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        }
        if self._api_key is not None:
            headers = {"Authorization": f"Bearer {self._api_key.get_secret_value()}", **headers}
        return urllib.request.Request(
            self._url,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers=headers,
        )

    def _remaining_s(self, call_deadline: float | None = None) -> float | None:
        """Thời gian còn lại của cận chặt nhất (cận tổng của client, cận của lần gọi), không vượt cận đó; `None` khi
        không có cận nào.

        Kẹp bằng `min` vì `(t0 + cận) - t1` có thể lớn hơn cận vài ulp do làm tròn số thực khi `t1` gần `t0`.
        """
        remaining: list[float] = []
        now: float | None = None
        if self._deadline is not None and self._total_budget_s is not None:
            now = self._monotonic()
            remaining.append(min(self._total_budget_s, self._deadline - now))
        if call_deadline is not None and self._call_budget_s is not None:
            now = self._monotonic() if now is None else now
            remaining.append(min(self._call_budget_s, call_deadline - now))
        return min(remaining) if remaining else None

    def complete(self, messages: Sequence[ChatMessage], *, max_tokens: int) -> ChatCompletionResult:
        # Lỗi được ghi lại thành chuỗi trong khối `except` rồi ném SAU khối đó, để exception mới không mang
        # `__context__` là lỗi gốc (lỗi của http.client có thể chứa nguyên giá trị header `Authorization`).
        call_deadline = None if self._call_budget_s is None else self._monotonic() + self._call_budget_s
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
                pause = self._backoff_base_s * 2 ** (attempt - 2)
                remaining = self._remaining_s(call_deadline)
                if remaining is not None and remaining <= pause:
                    raise LlmUpstreamError(_deadline_message(last_failure), attempts=attempt - 1)
                self._sleep(pause)
            remaining = self._remaining_s(call_deadline)
            if remaining is not None and remaining <= 0:
                raise LlmUpstreamError(_deadline_message(last_failure), attempts=attempt - 1)
            budget = self._timeout_s if remaining is None else min(self._timeout_s, remaining)
            outcome = self._send(request, budget)
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

    def _send(self, request: urllib.request.Request, timeout_s: float) -> tuple[int, bytes] | _Failure:
        """Một lần gửi. Lỗi chỉ được giữ lại dưới dạng tên lớp và thông điệp do adapter đặt, không giữ exception gốc."""
        try:
            return self._transport(request, timeout_s)
        except (OSError, http.client.HTTPException) as exc:
            return _Failure(f"lỗi mạng {type(exc).__name__}", retryable=True)
        except LlmUpstreamError as exc:
            return _Failure(str(exc), retryable=False)
        except REQUEST_DATA_ERRORS as exc:
            return _Failure(f"không gửi được yêu cầu tới LLM ({type(exc).__name__})", retryable=False)


def _deadline_message(last_failure: str) -> str:
    suffix = f" ({last_failure})" if last_failure else ""
    return f"hết hạn thời gian tổng của lần gọi LLM{suffix}"


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
