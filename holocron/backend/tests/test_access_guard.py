"""Kiểm `Host` luôn bật và token tuỳ chọn (SPECIFICATION §6.0, §11.2, §13.1; `routers/access.py`).

Fixture `autouse` ở `tests/conftest.py` thêm `testserver` vào danh sách host cho phép (§17 D13); test nào cần danh sách
mặc định thật thì bỏ override đó bằng `monkeypatch.delitem`.
"""

import ast
import logging
import re
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from holocron_core.infrastructure.memory_store import IngestionStore
from holocron_core.routers import access
from holocron_core.routers.access import (
    DEFAULT_ALLOWED_HOSTS,
    HOST_FORBIDDEN_DETAIL,
    TOKEN_ENV,
    TOKEN_FORBIDDEN_DETAIL,
    TOKEN_HEADER,
    get_allowed_hosts,
    get_required_token,
    host_name,
    read_required_token,
    require_local_request,
    token_matches,
)
from holocron_core.routers.api import app, get_store

TOKEN = "tok-0123456789abcdef-must-never-leak-ZZ"
WRONG_TOKEN = "tok-0123456789abcdef-must-never-leak-YY"


@pytest.fixture
def real_hosts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Dùng danh sách host mặc định của code, không có `testserver`."""
    monkeypatch.delitem(app.dependency_overrides, get_allowed_hosts)


def _client(host: str) -> TestClient:
    return TestClient(app, base_url=f"http://{host}")


# --------------------------------------------------------------------------- #
# Danh sách host và phân tích header Host
# --------------------------------------------------------------------------- #


def test_default_allowlist_is_loopback_only_and_excludes_testserver() -> None:
    assert get_allowed_hosts() == DEFAULT_ALLOWED_HOSTS == frozenset({"127.0.0.1", "localhost", "[::1]"})
    assert "testserver" not in get_allowed_hosts()


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
    ["testserver", "evil.example", "evil.example:8000", "127.0.0.1.evil.example:8000", "localhost.evil.example"],
)
def test_unknown_host_is_403_with_a_fixed_detail(host: str) -> None:
    response = _client(host).get("/v1/runtime")
    assert response.status_code == 403
    assert response.json() == {"detail": HOST_FORBIDDEN_DETAIL}
    assert host not in response.text


@pytest.mark.usefixtures("real_hosts")
def test_unknown_host_cannot_write(monkeypatch: pytest.MonkeyPatch) -> None:
    """Request bị chặn trước khi tới route: kho không bị ghi (chặn CSRF/DNS rebinding cho cả endpoint ghi)."""
    fresh = IngestionStore()
    monkeypatch.setitem(app.dependency_overrides, get_store, lambda: fresh)
    response = _client("evil.example:8000").post("/v1/ingestion/items", json={})
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
    client = _client("evil.example:8000")
    for method, path in operations:
        response = client.request(method.upper(), re.sub(r"\{[^}]+\}", "x", path))
        assert response.status_code == 403, (method, path)
        assert response.json() == {"detail": HOST_FORBIDDEN_DETAIL}, (method, path)


def _request(headers: list[tuple[bytes, bytes]]) -> Request:
    return Request({"type": "http", "method": "GET", "path": "/", "headers": headers})


def test_missing_host_header_is_rejected() -> None:
    with pytest.raises(HTTPException) as caught:
        require_local_request(_request([]), DEFAULT_ALLOWED_HOSTS, None)
    assert caught.value.status_code == 403
    assert caught.value.detail == HOST_FORBIDDEN_DETAIL


# --------------------------------------------------------------------------- #
# Token tuỳ chọn
# --------------------------------------------------------------------------- #


def test_token_is_not_required_when_the_variable_is_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    assert get_required_token() is None
    assert TestClient(app).get("/v1/runtime").status_code == 200


@pytest.mark.parametrize("value", ["", "   ", "\t"])
def test_blank_token_variable_counts_as_unset(value: str) -> None:
    assert read_required_token({TOKEN_ENV: value}) is None


def test_token_variable_value_is_the_token() -> None:
    assert read_required_token({TOKEN_ENV: TOKEN}) == TOKEN
    assert read_required_token({}) is None


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


def test_token_header_name_is_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(TOKEN_ENV, TOKEN)
    response = TestClient(app).get("/v1/runtime", headers={TOKEN_HEADER.lower(): TOKEN})
    assert response.status_code == 200


@pytest.mark.usefixtures("real_hosts")
def test_host_is_checked_before_the_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Token đúng không cứu được Host lạ."""
    monkeypatch.setenv(TOKEN_ENV, TOKEN)
    response = _client("evil.example:8000").get("/v1/runtime", headers={TOKEN_HEADER: TOKEN})
    assert response.status_code == 403
    assert response.json() == {"detail": HOST_FORBIDDEN_DETAIL}


@pytest.mark.usefixtures("real_hosts")
def test_token_applies_on_an_allowed_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(TOKEN_ENV, TOKEN)
    client = _client("127.0.0.1:8000")
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


def test_non_ascii_token_compares_without_error() -> None:
    assert token_matches("mật-khẩu", "mật-khẩu")
    assert not token_matches("mật-khẩu", "mat-khau")
    assert not token_matches(None, "mật-khẩu")


def test_guard_module_imports_no_network_or_logging_library() -> None:
    """Module kiểm chỉ đọc header và biến môi trường: không gọi mạng, không ghi log (token không thể lọt vào log)."""
    tree = ast.parse(Path(access.__file__).read_text(encoding="utf-8"))
    roots = {
        alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names
    }
    roots |= {node.module.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
    assert roots.isdisjoint({"socket", "http", "urllib", "httpx", "requests", "logging", "ssl"}), roots
