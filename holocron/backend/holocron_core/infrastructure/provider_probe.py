"""Kiểm thử một nhà cung cấp AI (`POST /v1/settings/providers/{id}/test`; spec §6.11; ADR-009 mục 12.8, 12.9).

- `action = "chat"`: MỘT chat completion nhỏ cho vai được chọn (vai `vlm` gửi kèm một ảnh PNG 8×8 do code tự tạo,
  không dùng tài liệu); `action = "list_models"`: MỘT `GET {base_url}/models`. Không thử lại.
- Mạng (ADR-009 mục 12.8): `guarded_transport` phân giải tên máy ở MỖI kết nối, kiểm MỌI địa chỉ, rồi nối tới đúng
  địa chỉ đã kiểm (ghim IP; SNI và `Host` vẫn là tên máy) để chống DNS rebinding. `ip_policy = "public_only"` (nhà
  cung cấp người dùng): chỉ địa chỉ công khai; loopback chỉ khi tên máy ∈ `LOOPBACK_HOSTS`. `"operator"` (env): miễn
  chặn IP riêng. Luôn không redirect, không proxy hệ thống (`UrllibTransport`).
- Kết quả chỉ gồm mã đã làm sạch, mã HTTP, độ trễ và tên model đã lọc: không thân phản hồi, không URL, không khoá.
"""

from __future__ import annotations

import base64
import http.client
import ipaddress
import json
import socket
import ssl
import struct
import time
import urllib.error
import urllib.request
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..domain.ai import (
    MAX_TEST_MODELS,
    AiRole,
    AiUsage,
    CostEstimate,
    IpPolicy,
    ProviderTestCode,
    ProviderTestRequest,
    ProviderTestResult,
    SelectedProvider,
)
from ..domain.llm import LlmUpstreamError
from .llm_client import DEFAULT_HTTP_LIMITS, USER_AGENT, Transport, UrllibTransport
from .llm_config import LOOPBACK_HOSTS, LlmHttpLimits
from .provider_store import address_is_public

PROBE_TIMEOUT_S = 30.0
PROBE_MAX_TOKENS = 8
PROBE_PROMPT = "Reply with the single word: ok"
PROBE_IMAGE_EDGE_PX = 8
MODEL_NAME_MAX_CHARS = 200
# Ước tính bảo thủ số token vào khi phản hồi thiếu `usage`: số ký tự phần chữ / 2 (ADR-009 mục 12.3).
CHARS_PER_ESTIMATED_TOKEN = 2

Address = ipaddress.IPv4Address | ipaddress.IPv6Address
Resolver = Callable[[str, int], list[str]]
Connector = Callable[[tuple[str, int], float | None], socket.socket]


class BlockedAddressError(OSError):
    """Tên máy phân giải ra địa chỉ bị chặn (hoặc không phân giải được). Thông điệp không nêu tên máy hay địa chỉ."""


def system_resolver(host: str, port: int) -> list[str]:
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


def _connect(address: tuple[str, int], timeout: float | None) -> socket.socket:
    return socket.create_connection(address, timeout)


def address_allowed(address: Address, *, host: str, policy: IpPolicy) -> bool:
    """`operator` ⇒ mọi địa chỉ; `public_only` ⇒ công khai, hoặc loopback khi tên máy trong URL ∈ `LOOPBACK_HOSTS`."""
    if policy == "operator":
        return True
    if host in LOOPBACK_HOSTS:
        mapped = address.ipv4_mapped if isinstance(address, ipaddress.IPv6Address) else None
        return (mapped or address).is_loopback
    return address_is_public(address)


@dataclass(frozen=True)
class AddressGuard:
    """Phân giải và kiểm địa chỉ của một kết nối; trả MỘT địa chỉ đã kiểm để nối (ghim IP)."""

    policy: IpPolicy
    resolver: Resolver = system_resolver
    connector: Connector = _connect

    def pick(self, host: str, port: int) -> str:
        try:
            resolved = self.resolver(host, port)
        except OSError:
            raise BlockedAddressError("không phân giải được tên máy của nhà cung cấp") from None
        addresses: list[Address] = []
        for raw in resolved:
            try:
                addresses.append(ipaddress.ip_address(raw.split("%", 1)[0]))
            except ValueError:
                raise BlockedAddressError("địa chỉ phân giải không hợp lệ") from None
        if not addresses or not all(address_allowed(item, host=host, policy=self.policy) for item in addresses):
            raise BlockedAddressError("địa chỉ của nhà cung cấp thuộc dải bị chặn")
        return str(addresses[0])

    def open(self, host: str, port: int, timeout: float | None) -> socket.socket:
        return self.connector((self.pick(host, port), port), timeout)


class GuardedHTTPConnection(http.client.HTTPConnection):
    """`http://` (chỉ loopback theo luật base URL) nối tới địa chỉ đã kiểm."""

    guard: AddressGuard

    def connect(self) -> None:
        self.sock = self.guard.open(self.host, self.port, self.timeout)


class GuardedHTTPSConnection(http.client.HTTPSConnection):
    """`https://` nối tới địa chỉ đã kiểm rồi bắt tay TLS với SNI = tên máy (chứng chỉ kiểm theo tên máy)."""

    guard: AddressGuard
    tls: ssl.SSLContext

    def connect(self) -> None:
        raw = self.guard.open(self.host, self.port, self.timeout)
        self.sock = self.tls.wrap_socket(raw, server_hostname=self.host)


class GuardedHTTPHandler(urllib.request.HTTPHandler):
    def __init__(self, guard: AddressGuard) -> None:
        super().__init__()
        self._guard = guard

    def http_open(self, req: urllib.request.Request) -> http.client.HTTPResponse:
        guard = self._guard

        class _Connection(GuardedHTTPConnection):
            pass

        _Connection.guard = guard
        return self.do_open(_Connection, req)


class GuardedHTTPSHandler(urllib.request.HTTPSHandler):
    """`tls` mặc định là `ssl.create_default_context()` (kiểm chứng chỉ và tên máy); test cấp context ghi lại SNI."""

    def __init__(self, guard: AddressGuard, tls: ssl.SSLContext | None = None) -> None:
        super().__init__()
        self._guard = guard
        self._tls = ssl.create_default_context() if tls is None else tls

    @property
    def tls(self) -> ssl.SSLContext:
        return self._tls

    def https_open(self, req: urllib.request.Request) -> http.client.HTTPResponse:
        guard, tls = self._guard, self._tls

        class _Connection(GuardedHTTPSConnection):
            pass

        _Connection.guard = guard
        _Connection.tls = tls
        return self.do_open(_Connection, req)


def guarded_transport(policy: IpPolicy, limits: LlmHttpLimits = DEFAULT_HTTP_LIMITS) -> Transport:
    """`UrllibTransport` (không redirect, không proxy, giới hạn HTTP `limits`) với kết nối đi qua `AddressGuard` của
    `policy`. Mọi client gửi tới nhà cung cấp đã chọn (kiểm thử, bộ đọc VLM) PHẢI dựng transport bằng hàm này."""
    guard = AddressGuard(policy)
    return UrllibTransport(GuardedHTTPHandler(guard), GuardedHTTPSHandler(guard), limits=limits)


# --------------------------------------------------------------------------- #
# Kiểm thử
# --------------------------------------------------------------------------- #


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def probe_png(edge_px: int = PROBE_IMAGE_EDGE_PX) -> bytes:
    """Ảnh PNG xám `edge_px`×`edge_px` do code tự tạo (không dùng tài liệu)."""
    header = struct.pack(">IIBBBBB", edge_px, edge_px, 8, 0, 0, 0, 0)
    rows = b"".join(b"\x00" + bytes([0x80]) * edge_px for _ in range(edge_px))
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(rows))
        + _png_chunk(b"IEND", b"")
    )


def _messages(role: AiRole) -> list[dict[str, Any]]:
    if role == "llm":
        return [{"role": "user", "content": PROBE_PROMPT}]
    image = "data:image/png;base64," + base64.b64encode(probe_png()).decode("ascii")
    return [
        {
            "role": "user",
            "content": [{"type": "text", "text": PROBE_PROMPT}, {"type": "image_url", "image_url": {"url": image}}],
        }
    ]


def probe_estimate(request: ProviderTestRequest) -> CostEstimate:
    """Chi phí tối đa của lời gọi kiểm thử (để giữ chỗ trước khi gửi); `list_models` không tính token."""
    if request.action == "list_models" or request.role is None:
        return CostEstimate(input_tokens=0, max_output_tokens=0, images=0)
    text_chars = len(PROBE_PROMPT)
    return CostEstimate(
        input_tokens=-(-text_chars // CHARS_PER_ESTIMATED_TOKEN),
        max_output_tokens=PROBE_MAX_TOKENS,
        images=1 if request.role == "vlm" else 0,
    )


def _headers(provider: SelectedProvider, *, json_body: bool) -> dict[str, str]:
    headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    if json_body:
        headers["Content-Type"] = "application/json"
    if provider.api_key is not None:
        headers["Authorization"] = f"Bearer {provider.api_key.get_secret_value()}"
    return headers


def _build_request(provider: SelectedProvider, request: ProviderTestRequest) -> urllib.request.Request:
    base = provider.base_url.rstrip("/")
    if request.action == "list_models" or request.role is None:
        return urllib.request.Request(base + "/models", method="GET", headers=_headers(provider, json_body=False))
    body = {"model": provider.model, "messages": _messages(request.role), "max_tokens": PROBE_MAX_TOKENS}
    return urllib.request.Request(
        base + "/chat/completions",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers=_headers(provider, json_body=True),
    )


@dataclass(frozen=True)
class ProbeOutcome:
    """`usage = None` ⇔ request chưa được gửi (chặn địa chỉ trước khi nối): nơi gọi chỉ nhả chỗ."""

    result: ProviderTestResult
    usage: AiUsage | None


def _status_code(status: int, request: ProviderTestRequest) -> ProviderTestCode:
    if 300 <= status <= 399:
        return "redirect_blocked"
    if status in (401, 403):
        return "auth_failed"
    if status == 404:
        return "not_found"
    if status == 429:
        return "rate_limited"
    if status == 400 and request.role == "vlm":
        return "image_rejected"
    return "upstream_error"


def _clean_models(payload: object) -> list[str] | None:
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if not isinstance(data, list):
        return None
    names: list[str] = []
    for item in data:
        name = item.get("id") if isinstance(item, dict) else None
        if (
            isinstance(name, str)
            and 0 < len(name) <= MODEL_NAME_MAX_CHARS
            and all(0x21 <= ord(char) <= 0x7E for char in name)
        ):
            names.append(name)
        if len(names) >= MAX_TEST_MODELS:
            break
    return names


def _chat_usage(payload: object) -> tuple[int, int] | None:
    """`(prompt_tokens, completion_tokens)` khi phản hồi có đủ; `None` khi phản hồi không phải chat completion hợp lệ
    (thiếu `choices`). Thiếu `usage` ⇒ `(-1, -1)` (nơi gọi ước tính)."""
    if not isinstance(payload, dict):
        return None
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    usage = payload.get("usage")
    if isinstance(usage, dict):
        prompt, completion = usage.get("prompt_tokens"), usage.get("completion_tokens")
        if isinstance(prompt, int) and isinstance(completion, int) and prompt >= 0 and completion >= 0:
            return prompt, completion
    return -1, -1


class ProviderProbe:
    """Chạy một lần kiểm thử qua `transport_for(ip_policy)`; test cấp transport giả (không gọi mạng)."""

    def __init__(
        self,
        transport_for: Callable[[IpPolicy], Transport] = guarded_transport,
        *,
        timeout_s: float = PROBE_TIMEOUT_S,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._transport_for = transport_for
        self._timeout_s = timeout_s
        self._monotonic = monotonic

    def run(self, provider: SelectedProvider, request: ProviderTestRequest) -> ProbeOutcome:
        estimate = probe_estimate(request)
        conservative = AiUsage(
            input_tokens=estimate.input_tokens,
            output_tokens=estimate.max_output_tokens,
            images=estimate.images,
            estimated=True,
        )
        started = self._monotonic()

        def finish(
            code: ProviderTestCode, status: int | None, usage: AiUsage | None, models: list[str] | None = None
        ) -> ProbeOutcome:
            latency = max(0, round((self._monotonic() - started) * 1000))
            result = ProviderTestResult(
                ok=code == "ok", code=code, http_status=status, latency_ms=latency, models=models or []
            )
            return ProbeOutcome(result=result, usage=usage)

        try:
            status, body = self._transport_for(provider.ip_policy)(_build_request(provider, request), self._timeout_s)
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, BlockedAddressError):
                return finish("blocked_address", None, None)
            if isinstance(exc.reason, TimeoutError):
                return finish("timeout", None, conservative)
            return finish("upstream_error", None, conservative)
        except BlockedAddressError:
            return finish("blocked_address", None, None)
        except TimeoutError:
            return finish("timeout", None, conservative)
        except (OSError, http.client.HTTPException, LlmUpstreamError, ValueError, TypeError, LookupError):
            return finish("upstream_error", None, conservative)
        http_status = status if 100 <= status <= 599 else None
        if not 200 <= status <= 299:
            return finish(_status_code(status, request), http_status, conservative)
        try:
            payload: object = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return finish("invalid_response", http_status, conservative)
        if request.action == "list_models":
            models = _clean_models(payload)
            if models is None:
                return finish("invalid_response", http_status, conservative)
            return finish("ok", http_status, conservative, models)
        counts = _chat_usage(payload)
        if counts is None:
            return finish("invalid_response", http_status, conservative)
        if counts == (-1, -1):
            return finish("ok", http_status, conservative)
        usage = AiUsage(input_tokens=counts[0], output_tokens=counts[1], images=estimate.images, estimated=False)
        return finish("ok", http_status, usage)
