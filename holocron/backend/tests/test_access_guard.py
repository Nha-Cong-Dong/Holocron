"""Kiểm `Host` luôn bật và token tuỳ chọn (SPECIFICATION §6.0, §11.2, §13.1; `routers/access.py`).

Fixture `autouse` ở `tests/conftest.py` thêm `testserver` vào danh sách host cho phép (§17 D13); test nào cần danh sách
mặc định thật thì bỏ override đó bằng `monkeypatch.delitem`.
"""

import ast
import asyncio
import json
import logging
import re
from collections.abc import MutableMapping
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from holocron_core.infrastructure.memory_store import IngestionStore
from holocron_core.routers import access
from holocron_core.routers.access import (
    DEFAULT_ALLOWED_HOSTS,
    FORWARDED_HOST_HEADER,
    HOST_FORBIDDEN_DETAIL,
    TOKEN_CONFIG_INVALID_DETAIL,
    TOKEN_ENV,
    TOKEN_FORBIDDEN_DETAIL,
    TOKEN_HEADER,
    WEBSOCKET_POLICY_VIOLATION,
    InvalidTokenConfigError,
    LocalAccessMiddleware,
    get_allowed_hosts,
    get_required_token,
    host_name,
    is_valid_token,
    read_required_token,
    token_matches,
)
from holocron_core.routers.api import app, get_store

TOKEN = "tok-0123456789abcdef-must-never-leak-ZZ"
WRONG_TOKEN = "tok-0123456789abcdef-must-never-leak-YY"
EVIL = "evil.example:8000"
DOC_PATHS = ["/openapi.json", "/docs", "/redoc", "/docs/oauth2-redirect"]
MALFORMED_JSON = b'{"broken": '


@pytest.fixture
def real_hosts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Dùng danh sách host mặc định của code, không có `testserver`."""
    monkeypatch.delitem(app.dependency_overrides, get_allowed_hosts)


def _client(host: str) -> TestClient:
    return TestClient(app, base_url=f"http://{host}")


class AsgiResult:
    """Kết quả gọi thẳng `app` qua ASGI: mã trạng thái, body JSON (nếu có) và việc app có đọc body request hay không."""

    def __init__(self, status: int, body: bytes, body_read: bool) -> None:
        self.status = status
        self.body = body
        self.body_read = body_read

    def json(self) -> Any:
        return json.loads(self.body)


def _asgi(
    headers: list[tuple[bytes, bytes]], method: str = "GET", path: str = "/v1/runtime", body: bytes = b""
) -> AsgiResult:
    """Gọi `app` bằng scope ASGI tự dựng, để gửi được header lặp, header thiếu hay byte latin-1 đúng như trên dây."""
    sent: list[MutableMapping[str, Any]] = []
    body_read = False

    async def receive() -> MutableMapping[str, Any]:
        nonlocal body_read
        body_read = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: MutableMapping[str, Any]) -> None:
        sent.append(message)

    scope: MutableMapping[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "root_path": "",
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 50000),
        "server": ("127.0.0.1", 8000),
    }
    asyncio.run(app(scope, receive, send))
    status = next(int(message["status"]) for message in sent if message["type"] == "http.response.start")
    content = b"".join(bytes(message.get("body", b"")) for message in sent if message["type"] == "http.response.body")
    return AsgiResult(status, content, body_read)


def _host(value: str) -> tuple[bytes, bytes]:
    return (b"host", value.encode("latin-1"))


def _forwarded(value: str) -> tuple[bytes, bytes]:
    return (FORWARDED_HOST_HEADER.lower().encode("ascii"), value.encode("latin-1"))


# --------------------------------------------------------------------------- #
# Danh sách host và phân tích header Host
# --------------------------------------------------------------------------- #


def test_default_allowlist_is_loopback_only_and_excludes_testserver() -> None:
    assert get_allowed_hosts() == DEFAULT_ALLOWED_HOSTS == frozenset({"127.0.0.1", "localhost", "[::1]"})
    assert "testserver" not in get_allowed_hosts()


def test_fixture_leaves_app_middleware_and_lets_new_app_keep_real_list() -> None:
    """Fixture ở `conftest.py` không đổi middleware của `app`; app dựng mới nhận `testserver` mặc định, nhưng override
    riêng của app đó (ở đây là danh sách thật) vẫn thắng."""
    assert [entry.cls for entry in app.user_middleware] == [LocalAccessMiddleware]

    fresh = FastAPI()
    fresh.add_middleware(access.LocalAccessMiddleware, overrides=lambda: fresh.dependency_overrides)

    @fresh.get("/ping")
    def ping() -> dict[str, str]:
        return {"status": "ok"}

    assert TestClient(fresh).get("/ping").status_code == 200
    fresh.dependency_overrides[get_allowed_hosts] = get_allowed_hosts
    denied = TestClient(fresh).get("/ping")
    assert denied.status_code == 403
    assert denied.json() == {"detail": HOST_FORBIDDEN_DETAIL}
    assert TestClient(fresh, base_url="http://127.0.0.1:8000").get("/ping").status_code == 200


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("127.0.0.1:8000", "127.0.0.1"),
        ("localhost:3000", "localhost"),
        ("LocalHost:8000", "localhost"),
        ("[::1]:8000", "[::1]"),
        ("127.0.0.1", "127.0.0.1"),
        ("[::1]", "[::1]"),
        ("localhost:65535", "localhost"),
    ],
)
def test_host_name_strips_a_valid_port(header: str, expected: str) -> None:
    assert host_name(header) == expected


@pytest.mark.parametrize(
    "header",
    [
        "",
        ":8000",
        "localhost:",
        "localhost:abc",
        "localhost:65536",
        "localhost:８０００",
        "[::1",
        "[::1]8000",
        "[::1]:",
    ],
)
def test_host_name_rejects_malformed_headers(header: str) -> None:
    assert host_name(header) is None


# --------------------------------------------------------------------------- #
# Kiểm Host qua app
# --------------------------------------------------------------------------- #


@pytest.mark.usefixtures("real_hosts")
@pytest.mark.parametrize("host", ["127.0.0.1:8000", "localhost:8000", "[::1]:8000", "localhost:3000"])
def test_allowed_host_with_port_passes(host: str) -> None:
    response = _client(host).get("/v1/runtime")
    assert response.status_code == 200


@pytest.mark.usefixtures("real_hosts")
@pytest.mark.parametrize(
    "host",
    ["testserver", "evil.example", EVIL, "127.0.0.1.evil.example:8000", "localhost.evil.example"],
)
def test_unknown_host_is_403_with_a_fixed_detail(host: str) -> None:
    response = _client(host).get("/v1/runtime")
    assert response.status_code == 403
    assert response.json() == {"detail": HOST_FORBIDDEN_DETAIL}
    assert host not in response.text


@pytest.mark.usefixtures("real_hosts")
@pytest.mark.parametrize(
    "host",
    [
        "localhost.",
        "localhost.:8000",
        "[::2]:8000",
        "[fe80::1]:8000",
        "[::ffff:127.0.0.1]:8000",
        "[0:0:0:0:0:0:0:1]:8000",
        "127.0.0.1.nip.io",
        "127.0.0.1.nip.io:8000",
        "evil@localhost",
        "evil@localhost:8000",
        "localhost@evil.example",
        "127.1:8000",
        "2130706433:8000",
        "localhost :8000",
    ],
)
def test_lookalike_hosts_are_403(host: str) -> None:
    """Tên chỉ "giống" loopback (dấu chấm cuối, IPv6 khác `::1`, IPv4-mapped, wildcard DNS, userinfo) đều bị từ chối."""
    response = TestClient(app).get("/v1/runtime", headers={"Host": host})
    assert response.status_code == 403
    assert response.json() == {"detail": HOST_FORBIDDEN_DETAIL}


@pytest.mark.usefixtures("real_hosts")
def test_unknown_host_cannot_write(monkeypatch: pytest.MonkeyPatch) -> None:
    """Request bị chặn trước khi tới route: kho không bị ghi."""
    fresh = IngestionStore()
    monkeypatch.setitem(app.dependency_overrides, get_store, lambda: fresh)
    response = _client(EVIL).post("/v1/ingestion/items", json={})
    assert response.status_code == 403
    assert response.json() == {"detail": HOST_FORBIDDEN_DETAIL}


def _api_operations() -> list[tuple[str, str]]:
    paths: dict[str, dict[str, object]] = app.openapi()["paths"]
    return [(method, path) for path, operations in paths.items() for method in operations]


@pytest.mark.usefixtures("real_hosts")
def test_every_api_route_rejects_an_unknown_host() -> None:
    """Mọi path và method của app (kể cả route ghi, body rỗng) bị chặn trước validate và trước khi chạm kho."""
    operations = _api_operations()
    assert ("get", "/v1/runtime") in operations
    client = _client(EVIL)
    for method, path in operations:
        response = client.request(method.upper(), re.sub(r"\{[^}]+\}", "x", path))
        assert response.status_code == 403, (method, path)
        assert response.json() == {"detail": HOST_FORBIDDEN_DETAIL}, (method, path)


@pytest.mark.usefixtures("real_hosts")
def test_every_api_route_rejects_a_malformed_body_from_an_unknown_host_without_reading_it() -> None:
    """Body JSON hỏng từ `Host` lạ ra 403, không phải 422: middleware từ chối trước khi đọc body."""
    for method, path in _api_operations():
        result = _asgi(
            [_host(EVIL), (b"content-type", b"application/json")],
            method=method.upper(),
            path=re.sub(r"\{[^}]+\}", "x", path),
            body=MALFORMED_JSON,
        )
        assert result.status == 403, (method, path)
        assert result.json() == {"detail": HOST_FORBIDDEN_DETAIL}, (method, path)
        assert not result.body_read, (method, path)


def test_malformed_body_from_an_allowed_host_still_reaches_validation() -> None:
    """Đối chứng: cùng body đó từ `Host` hợp lệ đi tới validate của route (422), nên 403 ở trên là do kiểm `Host`."""
    response = TestClient(app).post(
        "/v1/ingestion/items", content=MALFORMED_JSON, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 422


@pytest.mark.usefixtures("real_hosts")
@pytest.mark.parametrize("path", DOC_PATHS)
def test_documentation_routes_reject_an_unknown_host(path: str) -> None:
    """Route tài liệu tự sinh của FastAPI không đi qua dependency của app; middleware vẫn chặn chúng."""
    response = _client(EVIL).get(path)
    assert response.status_code == 403
    assert response.json() == {"detail": HOST_FORBIDDEN_DETAIL}


@pytest.mark.usefixtures("real_hosts")
@pytest.mark.parametrize("path", DOC_PATHS)
def test_documentation_routes_pass_for_an_allowed_host(path: str) -> None:
    assert _client("127.0.0.1:8000").get(path).status_code == 200


@pytest.mark.usefixtures("real_hosts")
@pytest.mark.parametrize(("host", "expected"), [(EVIL, 403), ("127.0.0.1:8000", 404)])
def test_unknown_path_is_checked_before_routing(host: str, expected: int) -> None:
    assert _client(host).get("/khong-ton-tai").status_code == expected


@pytest.mark.usefixtures("real_hosts")
@pytest.mark.parametrize(("host", "expected"), [(EVIL, 403), ("127.0.0.1:8000", 405)])
def test_wrong_method_is_checked_before_routing(host: str, expected: int) -> None:
    assert _client(host).delete("/v1/runtime").status_code == expected


@pytest.mark.usefixtures("real_hosts")
def test_websocket_from_an_unknown_host_is_closed_before_accept() -> None:
    with pytest.raises(WebSocketDisconnect) as caught, _client(EVIL).websocket_connect("/ws"):
        pass
    assert caught.value.code == WEBSOCKET_POLICY_VIOLATION


def test_missing_host_header_is_rejected() -> None:
    result = _asgi([])
    assert result.status == 403
    assert result.json() == {"detail": HOST_FORBIDDEN_DETAIL}


@pytest.mark.usefixtures("real_hosts")
@pytest.mark.parametrize(
    "hosts",
    [
        ["127.0.0.1:8000", "evil.example"],
        ["evil.example", "127.0.0.1:8000"],
        ["127.0.0.1:8000", "127.0.0.1:8000"],
    ],
)
def test_more_than_one_host_header_is_rejected(hosts: list[str]) -> None:
    """Nhiều header `Host` là mơ hồ (mỗi tầng có thể đọc một bản khác nhau): từ chối, kể cả khi đều hợp lệ."""
    result = _asgi([_host(host) for host in hosts])
    assert result.status == 403
    assert result.json() == {"detail": HOST_FORBIDDEN_DETAIL}


@pytest.mark.usefixtures("real_hosts")
def test_single_allowed_host_header_passes_over_raw_asgi() -> None:
    assert _asgi([_host("127.0.0.1:8000")]).status == 200


# --------------------------------------------------------------------------- #
# X-Forwarded-Host (proxy rewrite của Next.js với changeOrigin)
# --------------------------------------------------------------------------- #


@pytest.mark.usefixtures("real_hosts")
@pytest.mark.parametrize("forwarded", ["localhost:3000", "127.0.0.1:3000", "[::1]:3000", "localhost:3000, 127.0.0.1"])
def test_allowed_forwarded_host_passes(forwarded: str) -> None:
    response = _client("127.0.0.1:8000").get("/v1/runtime", headers={FORWARDED_HOST_HEADER: forwarded})
    assert response.status_code == 200


@pytest.mark.usefixtures("real_hosts")
@pytest.mark.parametrize(
    "forwarded",
    [
        "evil.example",
        "evil.example:3000",
        "localhost.",
        "evil@localhost",
        "127.0.0.1.nip.io:3000",
        "[::ffff:127.0.0.1]:3000",
        "localhost:3000, evil.example",
        "evil.example, localhost:3000",
        "localhost:3000,evil.example:3000",
        "",
        " ",
        ",",
        "localhost:3000,",
        ",localhost:3000",
        "localhost:3000,,127.0.0.1",
        "localhost:",
        "[::1",
    ],
)
def test_dns_rebinding_through_the_proxy_is_403(forwarded: str) -> None:
    """Proxy ghi đè `Host` thành `127.0.0.1:8000`; `Host` gốc của trình duyệt nằm trong `X-Forwarded-Host` và bị kiểm."""
    result = _asgi([_host("127.0.0.1:8000"), _forwarded(forwarded)])
    assert result.status == 403
    assert result.json() == {"detail": HOST_FORBIDDEN_DETAIL}


@pytest.mark.usefixtures("real_hosts")
def test_rebinding_through_the_proxy_via_http_client_is_403() -> None:
    response = _client("127.0.0.1:8000").get("/docs", headers={FORWARDED_HOST_HEADER: "evil.example:3000"})
    assert response.status_code == 403
    assert response.json() == {"detail": HOST_FORBIDDEN_DETAIL}


@pytest.mark.usefixtures("real_hosts")
@pytest.mark.parametrize(
    ("values", "expected"),
    [
        (["localhost:3000", "evil.example"], 403),
        (["evil.example", "localhost:3000"], 403),
        (["localhost:3000", ""], 403),
        (["localhost:3000", "127.0.0.1:3000"], 200),
    ],
)
def test_every_forwarded_host_header_is_checked(values: list[str], expected: int) -> None:
    result = _asgi([_host("127.0.0.1:8000"), *(_forwarded(value) for value in values)])
    assert result.status == expected


@pytest.mark.usefixtures("real_hosts")
def test_forwarded_host_header_name_is_case_insensitive() -> None:
    result = _asgi([_host("127.0.0.1:8000"), (b"X-Forwarded-Host", b"evil.example")])
    assert result.status == 403


# --------------------------------------------------------------------------- #
# Token tuỳ chọn
# --------------------------------------------------------------------------- #


def test_token_is_not_required_when_the_variable_is_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    assert get_required_token() is None
    assert TestClient(app).get("/v1/runtime").status_code == 200


def test_empty_token_variable_counts_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """`HOLOCRON_REQUIRE_TOKEN=` (để trống, như `.env.example`) nghĩa là không yêu cầu token."""
    assert read_required_token({TOKEN_ENV: ""}) is None
    monkeypatch.setenv(TOKEN_ENV, "")
    assert TestClient(app).get("/v1/runtime").status_code == 200


def test_token_variable_value_is_the_token() -> None:
    assert read_required_token({TOKEN_ENV: TOKEN}) == TOKEN
    assert read_required_token({}) is None


@pytest.mark.parametrize("value", [TOKEN, "a", "a b", "!~", "tok with inner spaces"])
def test_printable_ascii_tokens_are_valid(value: str) -> None:
    assert is_valid_token(value)
    assert read_required_token({TOKEN_ENV: value}) == value


INVALID_TOKENS = ["   ", "\t", " tok", "tok ", "tok\t", "\ttok", "mật-khẩu", "tok\x7f", "tok\x1b", "tök", "tok "]


@pytest.mark.parametrize("value", INVALID_TOKENS)
def test_invalid_token_variable_raises_without_echoing_the_value(value: str) -> None:
    assert not is_valid_token(value)
    with pytest.raises(InvalidTokenConfigError) as caught:
        read_required_token({TOKEN_ENV: value})
    message = str(caught.value)
    assert TOKEN_ENV in message
    assert value.strip() == "" or value.strip() not in message


@pytest.mark.parametrize("value", INVALID_TOKENS)
@pytest.mark.parametrize("path", ["/v1/runtime", "/docs", "/khong-ton-tai"])
def test_invalid_token_variable_fails_closed_over_http(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, value: str, path: str
) -> None:
    """Cấu hình token hỏng: mọi request bị 403 với `detail` cố định chỉ nêu tên biến, kể cả khi gửi đúng giá trị biến."""
    monkeypatch.setenv(TOKEN_ENV, value)
    client = TestClient(app)
    with caplog.at_level(logging.DEBUG):
        responses = [
            client.get(path),
            client.get(path, headers={TOKEN_HEADER: value.encode("utf-8")}),
            client.get(path, headers={TOKEN_HEADER: TOKEN}),
        ]
    for response in responses:
        assert response.status_code == 403
        assert response.json() == {"detail": TOKEN_CONFIG_INVALID_DETAIL}
        assert TOKEN_ENV in response.json()["detail"]
    assert value.strip() == "" or value.strip() not in caplog.text


@pytest.mark.usefixtures("real_hosts")
def test_host_is_checked_before_a_broken_token_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(TOKEN_ENV, " broken")
    response = _client(EVIL).get("/v1/runtime")
    assert response.json() == {"detail": HOST_FORBIDDEN_DETAIL}


@pytest.mark.parametrize(
    "headers",
    [{}, {TOKEN_HEADER: WRONG_TOKEN}, {TOKEN_HEADER: TOKEN[:-1]}, {TOKEN_HEADER: TOKEN + "x"}, {TOKEN_HEADER: ""}],
)
def test_missing_or_wrong_token_is_403_with_a_fixed_detail(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, headers: dict[str, str]
) -> None:
    monkeypatch.setenv(TOKEN_ENV, TOKEN)
    with caplog.at_level(logging.DEBUG):
        response = TestClient(app).get("/v1/runtime", headers=headers)
    assert response.status_code == 403
    assert response.json() == {"detail": TOKEN_FORBIDDEN_DETAIL}
    assert TOKEN not in response.text and WRONG_TOKEN not in response.text
    assert TOKEN not in caplog.text and WRONG_TOKEN not in caplog.text
    assert TOKEN not in str(dict(response.headers))


def test_correct_token_passes_without_leaking(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv(TOKEN_ENV, TOKEN)
    with caplog.at_level(logging.DEBUG):
        response = TestClient(app).get("/v1/runtime", headers={TOKEN_HEADER: TOKEN})
    assert response.status_code == 200
    assert TOKEN not in response.text
    assert TOKEN not in str(dict(response.headers))
    assert TOKEN not in caplog.text


@pytest.mark.parametrize("path", DOC_PATHS)
def test_token_guards_documentation_routes(monkeypatch: pytest.MonkeyPatch, path: str) -> None:
    monkeypatch.setenv(TOKEN_ENV, TOKEN)
    client = TestClient(app)
    assert client.get(path).json() == {"detail": TOKEN_FORBIDDEN_DETAIL}
    assert client.get(path, headers={TOKEN_HEADER: TOKEN}).status_code == 200


def test_token_with_inner_spaces_passes_over_http(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(TOKEN_ENV, "a b c")
    assert TestClient(app).get("/v1/runtime", headers={TOKEN_HEADER: "a b c"}).status_code == 200


def test_latin1_token_header_is_rejected_without_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Byte ngoài ASCII trong header được so trên bytes latin-1: không lỗi, chỉ không khớp."""
    monkeypatch.setenv(TOKEN_ENV, TOKEN)
    response = TestClient(app).get("/v1/runtime", headers={TOKEN_HEADER: TOKEN.encode("ascii") + b"\xe9"})
    assert response.status_code == 403
    assert response.json() == {"detail": TOKEN_FORBIDDEN_DETAIL}


@pytest.mark.parametrize("tokens", [[TOKEN, TOKEN], [TOKEN, WRONG_TOKEN], [WRONG_TOKEN, TOKEN]])
def test_more_than_one_token_header_is_rejected(monkeypatch: pytest.MonkeyPatch, tokens: list[str]) -> None:
    monkeypatch.setenv(TOKEN_ENV, TOKEN)
    result = _asgi([_host("testserver"), *((b"x-holocron-token", token.encode("ascii")) for token in tokens)])
    assert result.status == 403
    assert result.json() == {"detail": TOKEN_FORBIDDEN_DETAIL}


def test_token_header_name_is_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(TOKEN_ENV, TOKEN)
    response = TestClient(app).get("/v1/runtime", headers={TOKEN_HEADER.lower(): TOKEN})
    assert response.status_code == 200


@pytest.mark.usefixtures("real_hosts")
def test_host_is_checked_before_the_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Token đúng không cứu được Host lạ."""
    monkeypatch.setenv(TOKEN_ENV, TOKEN)
    response = _client(EVIL).get("/v1/runtime", headers={TOKEN_HEADER: TOKEN})
    assert response.status_code == 403
    assert response.json() == {"detail": HOST_FORBIDDEN_DETAIL}


@pytest.mark.usefixtures("real_hosts")
def test_token_applies_on_an_allowed_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(TOKEN_ENV, TOKEN)
    client = _client("127.0.0.1:8000")
    assert client.get("/v1/runtime").json() == {"detail": TOKEN_FORBIDDEN_DETAIL}
    assert client.get("/v1/runtime", headers={TOKEN_HEADER: TOKEN}).status_code == 200


def test_token_provider_can_be_overridden_like_a_dependency(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    monkeypatch.setitem(app.dependency_overrides, get_required_token, lambda: TOKEN)
    client = TestClient(app)
    assert client.get("/v1/runtime").json() == {"detail": TOKEN_FORBIDDEN_DETAIL}
    assert client.get("/v1/runtime", headers={TOKEN_HEADER: TOKEN}).status_code == 200


def test_token_is_compared_in_constant_time(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[bytes, bytes]] = []
    real = access.hmac.compare_digest

    def spy(a: bytes, b: bytes) -> bool:
        calls.append((a, b))
        return real(a, b)

    monkeypatch.setattr(access.hmac, "compare_digest", spy)
    monkeypatch.setenv(TOKEN_ENV, TOKEN)
    assert TestClient(app).get("/v1/runtime", headers={TOKEN_HEADER: WRONG_TOKEN}).status_code == 403
    assert len(calls) == 1


def test_token_matches_compares_latin1_bytes() -> None:
    assert token_matches(TOKEN.encode("latin-1"), TOKEN)
    assert not token_matches(WRONG_TOKEN.encode("latin-1"), TOKEN)
    assert not token_matches(b"\xe9", TOKEN)
    assert not token_matches(None, TOKEN)


def test_guard_module_imports_no_network_or_logging_library() -> None:
    """Module kiểm chỉ đọc header và biến môi trường: không gọi mạng, không ghi log (token không thể lọt vào log)."""
    tree = ast.parse(Path(access.__file__).read_text(encoding="utf-8"))
    roots = {
        alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names
    }
    roots |= {node.module.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
    assert roots.isdisjoint({"socket", "http", "urllib", "httpx", "requests", "logging", "ssl"}), roots


# --------------------------------------------------------------------------- #
# Ca bổ sung sau mutation testing (biên cổng, giải mã byte, strip, token rỗng)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("header", ["localhost:008000", "localhost:" + "9" * 5000])
def test_host_name_rejects_port_longer_than_five_digits(header: str) -> None:
    assert host_name(header) is None


@pytest.mark.usefixtures("real_hosts")
def test_host_with_a_very_long_port_is_403() -> None:
    assert _asgi([_host("localhost:" + "9" * 5000)]).status == 403


@pytest.mark.parametrize(("header", "expected"), [("localhost:1", "localhost"), ("127.0.0.1:0", "127.0.0.1")])
def test_host_name_accepts_single_digit_port(header: str, expected: str) -> None:
    assert host_name(header) == expected


@pytest.mark.usefixtures("real_hosts")
def test_non_ascii_host_bytes_are_403_not_500() -> None:
    assert _asgi([(b"host", b"evil\xe9.example")]).status == 403


@pytest.mark.usefixtures("real_hosts")
@pytest.mark.parametrize("forwarded", [b"localhost\xa0", b"localhost\x0b"])
def test_forwarded_host_is_not_stripped_of_unicode_or_vertical_whitespace(forwarded: bytes) -> None:
    result = _asgi([_host("127.0.0.1:8000"), (FORWARDED_HOST_HEADER.lower().encode("ascii"), forwarded)])
    assert result.status == 403


@pytest.mark.usefixtures("real_hosts")
def test_forwarded_host_list_allows_a_tab_after_the_comma() -> None:
    result = _asgi(
        [_host("127.0.0.1:8000"), (FORWARDED_HOST_HEADER.lower().encode("ascii"), b"localhost:3000,\t127.0.0.1")]
    )
    assert result.status == 200


def test_empty_string_is_not_a_valid_token() -> None:
    assert is_valid_token("") is False


def test_token_matches_compares_latin1_bytes_not_utf8() -> None:
    assert token_matches(b"\xe9", "é") is True


# --------------------------------------------------------------------------- #
# Trang tĩnh (HOLOCRON_WEB_DIR) cũng bị kiểm Host
# --------------------------------------------------------------------------- #


def _static_probe_app(web_dir: Path, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    """Nạp lại `routers/api.py` thành module riêng với `HOLOCRON_WEB_DIR`, như `tests/test_static_site.py`."""
    import importlib.util

    from holocron_core.routers import api

    monkeypatch.setenv("HOLOCRON_WEB_DIR", str(web_dir))
    spec = importlib.util.spec_from_file_location("holocron_core.routers._api_access_probe", Path(api.__file__))
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    probe: FastAPI = module.app
    return probe


@pytest.mark.parametrize("path", ["/", "/index.html"])
def test_static_pages_reject_an_unknown_host_and_serve_an_allowed_one(
    path: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<html><head></head><body>Trang chủ</body></html>", encoding="utf-8")
    probe = _static_probe_app(web, monkeypatch)
    evil = TestClient(probe, base_url=f"http://{EVIL}").get(path)
    assert evil.status_code == 403
    assert evil.json() == {"detail": HOST_FORBIDDEN_DETAIL}
    ok = TestClient(probe, base_url="http://127.0.0.1:8000").get(path)
    assert ok.status_code == 200
    assert "Trang chủ" in ok.text
