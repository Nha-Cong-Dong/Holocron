"""Chặn request từ trình duyệt không phải của app (SPECIFICATION §6.0, §11.2, §13.1; duyệt 2026-10-25).

Hai lớp, đăng ký một lần cho mọi route qua `FastAPI(dependencies=...)` ở `routers/api.py`:

- **Kiểm `Host` luôn bật**, ở mọi chế độ chạy: chỉ nhận tên host thuộc danh sách cho phép (mặc định `127.0.0.1`,
  `localhost`, `[::1]`), có hoặc không kèm cổng. Chặn DNS rebinding: trang web độc hại trỏ tên miền của nó về
  `127.0.0.1` vẫn gửi `Host` là tên miền đó. Danh sách cấp qua `Depends(get_allowed_hosts)`; test override bằng fixture
  `autouse` trong `tests/conftest.py` (§17 D13). `testserver` của `TestClient` KHÔNG thuộc danh sách mặc định.
- **Token tuỳ chọn**: chỉ khi đặt `HOLOCRON_REQUIRE_TOKEN` (giá trị của biến chính là token) thì mọi request phải mang
  header `X-Holocron-Token` bằng đúng giá trị đó, so bằng `hmac.compare_digest`. Biến rỗng hoặc toàn khoảng trắng coi như
  chưa đặt (§11.2).

Từ chối luôn là 403 với `detail` cố định: không lặp lại `Host`, token hay header đã gửi. Module không ghi log; token
không bao giờ được ghi log hay trả về client.
"""

from __future__ import annotations

import hmac
import os
from collections.abc import Mapping
from typing import Annotated

from fastapi import Depends, HTTPException, Request

DEFAULT_ALLOWED_HOSTS: frozenset[str] = frozenset({"127.0.0.1", "localhost", "[::1]"})
TOKEN_ENV = "HOLOCRON_REQUIRE_TOKEN"
TOKEN_HEADER = "X-Holocron-Token"
HOST_FORBIDDEN_DETAIL = "Yêu cầu bị từ chối: Host không thuộc danh sách cho phép"
TOKEN_FORBIDDEN_DETAIL = "Yêu cầu bị từ chối: thiếu hoặc sai token"
MAX_PORT = 65535


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


def read_required_token(env: Mapping[str, str] | None = None) -> str | None:
    """Token bắt buộc từ `env` (mặc định `os.environ`); `None` khi không đặt hoặc chỉ có khoảng trắng."""
    source = os.environ if env is None else env
    value = source.get(TOKEN_ENV)
    if value is None or not value.strip():
        return None
    return value


def token_matches(provided: str | None, expected: str) -> bool:
    """So token bằng phép so thời gian hằng; so trên bytes UTF-8 để chuỗi không phải ASCII không làm lỗi."""
    if provided is None:
        return False
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


def get_allowed_hosts() -> frozenset[str]:
    return DEFAULT_ALLOWED_HOSTS


def get_required_token() -> str | None:
    return read_required_token()


AllowedHostsDep = Annotated[frozenset[str], Depends(get_allowed_hosts)]
RequiredTokenDep = Annotated[str | None, Depends(get_required_token)]


def require_local_request(request: Request, allowed_hosts: AllowedHostsDep, required_token: RequiredTokenDep) -> None:
    """Kiểm `Host` trước, rồi token nếu đã bật; sai thì 403 với `detail` cố định."""
    raw_host = request.headers.get("host")
    name = None if raw_host is None else host_name(raw_host)
    if name is None or name not in allowed_hosts:
        raise HTTPException(status_code=403, detail=HOST_FORBIDDEN_DETAIL)
    if required_token is not None and not token_matches(request.headers.get(TOKEN_HEADER), required_token):
        raise HTTPException(status_code=403, detail=TOKEN_FORBIDDEN_DETAIL)
