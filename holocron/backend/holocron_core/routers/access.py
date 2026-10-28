"""Chặn request từ trình duyệt không phải của app (SPECIFICATION §6.0, §11.2, §13.1; duyệt 2026-10-25).

Kiểm trong middleware ASGI `LocalAccessMiddleware`, đăng ký ở `routers/api.py`. Middleware chạy TRƯỚC routing và trước
khi đọc body, nên áp cho MỌI request tới app: route API, route tài liệu tự sinh của FastAPI (`/openapi.json`, `/docs`,
`/redoc`, `/docs/oauth2-redirect`), path không tồn tại (404) và method sai (405). Bản trước đăng ký bằng
`FastAPI(dependencies=...)`, vốn không áp cho các route tài liệu, 404 và 405; khoảng hở đó đã được sửa. Body hỏng gửi
từ `Host` lạ cũng nhận 403, không phải 422.

Hai lớp:

- **Kiểm `Host` luôn bật**, ở mọi chế độ chạy: chỉ nhận đúng một header `Host` có tên thuộc danh sách cho phép (mặc định
  `127.0.0.1`, `localhost`, `[::1]`), có hoặc không kèm cổng. Có `X-Forwarded-Host` (proxy rewrite của Next.js dùng
  `changeOrigin` ghi đè `Host` thành địa chỉ backend và chuyển `Host` gốc vào header này) thì MỌI tên host trong đó
  (mọi header, mọi giá trị phân tách bằng dấu phẩy) cũng phải thuộc danh sách; phần tử rỗng hay sai dạng bị từ chối.
  Lớp này chặn **DNS rebinding**: trang web độc hại trỏ tên miền của nó về `127.0.0.1` vẫn gửi `Host` là tên miền đó.
  Lớp này **KHÔNG chặn CSRF**: trang web khác gửi request tới `http://127.0.0.1:<port>` thì trình duyệt vẫn gửi
  `Host: 127.0.0.1:<port>`, hợp lệ. Chỉ token (khi bật) chặn được CSRF.
- **Token tuỳ chọn**: chỉ khi đặt `HOLOCRON_REQUIRE_TOKEN` (giá trị của biến chính là token) thì mọi request phải mang
  đúng một header `X-Holocron-Token` bằng đúng giá trị đó, so bằng `hmac.compare_digest` trên bytes latin-1 (cách
  Starlette giải mã header). Biến không đặt hoặc là chuỗi rỗng `""` coi như chưa đặt (dòng `HOLOCRON_REQUIRE_TOKEN=`
  trong `.env`). Có giá trị thì token chỉ được gồm ASCII in được (U+0020–U+007E) và không có khoảng trắng đầu/cuối;
  không cắt khoảng trắng trước khi kiểm. Vi phạm, kể cả giá trị toàn khoảng trắng (dấu cách, tab, NBSP, xuống dòng hay
  khoảng trắng Unicode khác), là cấu hình hỏng: app TỪ CHỐI KHỞI ĐỘNG (`check_token_config` gọi trong `lifespan` ở
  `routers/api.py`, lỗi `InvalidTokenConfigError` chỉ nêu tên biến, không nêu giá trị). Middleware vẫn đọc lại biến ở
  mỗi request làm lớp phòng thủ thứ hai (ví dụ `os.environ` bị đổi trong tiến trình sau khi khởi động): cấu hình hỏng
  ⇒ fail-closed, mọi request bị 403 với `detail` cố định chỉ nêu tên biến.

Danh sách host và token lấy từ hai provider `get_allowed_hosts`, `get_required_token`; middleware tra
`app.dependency_overrides` trước khi gọi provider, nên test override như một dependency FastAPI (fixture `autouse` ở
`tests/conftest.py`, §17 D13). `testserver` của `TestClient` KHÔNG thuộc danh sách mặc định.

Từ chối luôn là 403 JSON `{"detail": ...}` với `detail` cố định: không lặp lại `Host`, token hay header đã gửi. Module
không ghi log; token không bao giờ được ghi log hay trả về client.
"""

from __future__ import annotations

import hmac
import os
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

DEFAULT_ALLOWED_HOSTS: frozenset[str] = frozenset({"127.0.0.1", "localhost", "[::1]"})
TOKEN_ENV = "HOLOCRON_REQUIRE_TOKEN"
TOKEN_HEADER = "X-Holocron-Token"
FORWARDED_HOST_HEADER = "X-Forwarded-Host"
HOST_FORBIDDEN_DETAIL = "Yêu cầu bị từ chối: Host không thuộc danh sách cho phép"
TOKEN_FORBIDDEN_DETAIL = "Yêu cầu bị từ chối: thiếu hoặc sai token"
TOKEN_CONFIG_INVALID_DETAIL = f"Yêu cầu bị từ chối: biến môi trường {TOKEN_ENV} không hợp lệ"
MAX_PORT = 65535
# Mã đóng WebSocket "policy violation" (RFC 6455 §7.4.1); đóng trước khi accept thì server trả 403 cho handshake.
WEBSOCKET_POLICY_VIOLATION = 1008
_HEADER_ENCODING = "latin-1"
_HOST_KEY = b"host"
_FORWARDED_HOST_KEY = FORWARDED_HOST_HEADER.lower().encode("ascii")
_TOKEN_KEY = TOKEN_HEADER.lower().encode("ascii")
_PRINTABLE_ASCII_MIN = 0x20
_PRINTABLE_ASCII_MAX = 0x7E

ProviderOverrides = Mapping[Callable[..., Any], Callable[..., Any]]


class InvalidTokenConfigError(ValueError):
    """`HOLOCRON_REQUIRE_TOKEN` có giá trị nhưng không phải token hợp lệ; thông điệp chỉ nêu tên biến."""

    def __init__(self) -> None:
        super().__init__(
            f"{TOKEN_ENV} không hợp lệ: chỉ được gồm ký tự ASCII in được (U+0020–U+007E), không có khoảng trắng đầu "
            "hoặc cuối, không được chỉ gồm khoảng trắng; để trống hoặc bỏ biến nếu không cần xác thực"
        )


def _is_port(text: str) -> bool:
    """Cổng TCP viết bằng chữ số ASCII (không nhận chữ số Unicode như `８`), trong khoảng 0–65535."""
    return 0 < len(text) <= len(str(MAX_PORT)) and text.isascii() and text.isdecimal() and int(text) <= MAX_PORT


def host_name(host_header: str) -> str | None:
    """Tên host (chữ thường) của header `Host`, bỏ phần `:<cổng>`; `None` nếu header sai dạng.

    IPv6 phải nằm trong ngoặc vuông (`[::1]:8000`). Cổng, nếu có, phải là số hợp lệ; `localhost:` (cổng rỗng) là sai dạng.
    """
    if host_header.startswith("["):
        end = host_header.find("]")
        if end == -1:
            return None
        name, rest = host_header[: end + 1], host_header[end + 1 :]
    else:
        name, separator, port = host_header.partition(":")
        rest = separator + port
    if not name or (rest and not (rest.startswith(":") and _is_port(rest[1:]))):
        return None
    return name.lower()


def _header_values(headers: Iterable[tuple[bytes, bytes]], key: bytes) -> list[bytes]:
    """Mọi giá trị của một header (tên so không phân biệt hoa thường), giữ thứ tự xuất hiện."""
    return [value for name, value in headers if name.lower() == key]


def _name_allowed(host_header: str, allowed_hosts: frozenset[str]) -> bool:
    name = host_name(host_header)
    return name is not None and name in allowed_hosts


def host_allowed(headers: Iterable[tuple[bytes, bytes]], allowed_hosts: frozenset[str]) -> bool:
    """Đúng một `Host` có tên được phép, và mọi tên trong mọi `X-Forwarded-Host` (nếu có) cũng được phép."""
    header_list = list(headers)
    hosts = _header_values(header_list, _HOST_KEY)
    if len(hosts) != 1 or not _name_allowed(hosts[0].decode(_HEADER_ENCODING), allowed_hosts):
        return False
    for value in _header_values(header_list, _FORWARDED_HOST_KEY):
        for part in value.decode(_HEADER_ENCODING).split(","):
            if not _name_allowed(part.strip(" \t"), allowed_hosts):
                return False
    return True


def is_valid_token(value: str) -> bool:
    """Token hợp lệ: không rỗng, chỉ ASCII in được (U+0020–U+007E), không có khoảng trắng đầu/cuối."""
    return (
        bool(value)
        and all(_PRINTABLE_ASCII_MIN <= ord(char) <= _PRINTABLE_ASCII_MAX for char in value)
        and value[0] != " "
        and value[-1] != " "
    )


def read_required_token(env: Mapping[str, str] | None = None) -> str | None:
    """Token bắt buộc từ `env` (mặc định `os.environ`); `None` chỉ khi biến không đặt hoặc là chuỗi rỗng `""`.

    Mọi giá trị khác phải hợp lệ theo `is_valid_token`, kiểm nguyên văn (không cắt khoảng trắng); sai ⇒ ném
    `InvalidTokenConfigError`, kể cả giá trị toàn khoảng trắng (dấu cách, tab, NBSP, U+3000, xuống dòng): lúc khởi
    động app từ chối khởi động (`check_token_config`), lúc phục vụ request thì fail-closed thay vì âm thầm bỏ token.
    """
    source = os.environ if env is None else env
    value = source.get(TOKEN_ENV)
    if not value:
        return None
    if not is_valid_token(value):
        raise InvalidTokenConfigError
    return value


def token_matches(provided: bytes | None, expected: str) -> bool:
    """So token bằng phép so thời gian hằng, trên bytes latin-1 như Starlette giải mã header."""
    if provided is None:
        return False
    return hmac.compare_digest(provided, expected.encode(_HEADER_ENCODING))


def get_allowed_hosts() -> frozenset[str]:
    return DEFAULT_ALLOWED_HOSTS


def get_required_token() -> str | None:
    return read_required_token()


def required_token_provider(overrides: ProviderOverrides) -> Callable[[], str | None]:
    """Provider token hiện hành: override trong `overrides` (ở app là `app.dependency_overrides`) nếu có."""
    provider: Callable[[], str | None] = overrides.get(get_required_token, get_required_token)
    return provider


def check_token_config(overrides: ProviderOverrides) -> None:
    """Kiểm cấu hình token lúc khởi động; hỏng ⇒ `InvalidTokenConfigError` (chỉ nêu tên biến), app từ chối khởi động.

    Gọi trong `lifespan` của `routers/api.py`, trước khi mở kho, cùng đường lỗi với DB mới hơn code (§5.13).
    """
    required_token_provider(overrides)()


def access_denial(
    headers: Iterable[tuple[bytes, bytes]], allowed_hosts: frozenset[str], token_provider: Callable[[], str | None]
) -> str | None:
    """`detail` của 403 nếu request bị từ chối, `None` nếu được qua. Kiểm `Host` trước, rồi cấu hình token, rồi token."""
    header_list = list(headers)
    if not host_allowed(header_list, allowed_hosts):
        return HOST_FORBIDDEN_DETAIL
    try:
        required_token = token_provider()
    except InvalidTokenConfigError:
        return TOKEN_CONFIG_INVALID_DETAIL
    if required_token is None:
        return None
    tokens = _header_values(header_list, _TOKEN_KEY)
    if len(tokens) != 1 or not token_matches(tokens[0], required_token):
        return TOKEN_FORBIDDEN_DETAIL
    return None


class LocalAccessMiddleware:
    """Middleware ASGI kiểm `Host` và token cho mọi request HTTP/WebSocket, trước routing và trước khi đọc body.

    `overrides` trả bảng override provider hiện hành (ở app là `app.dependency_overrides`), tra lại ở mỗi request để
    test override được giữa các lần gọi.
    """

    def __init__(self, app: ASGIApp, *, overrides: Callable[[], ProviderOverrides]) -> None:
        self._app = app
        self._overrides = overrides

    def _allowed_hosts(self) -> frozenset[str]:
        provider = self._overrides().get(get_allowed_hosts, get_allowed_hosts)
        hosts: frozenset[str] = provider()
        return hosts

    def _required_token(self) -> str | None:
        return required_token_provider(self._overrides())()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self._app(scope, receive, send)
            return
        detail = access_denial(scope["headers"], self._allowed_hosts(), self._required_token)
        if detail is None:
            await self._app(scope, receive, send)
        elif scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": WEBSOCKET_POLICY_VIOLATION})
        else:
            await JSONResponse(status_code=403, content={"detail": detail})(scope, receive, send)
