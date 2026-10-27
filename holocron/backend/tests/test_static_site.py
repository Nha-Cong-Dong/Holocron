"""Backend phục vụ bản export tĩnh của frontend khi đặt `HOLOCRON_WEB_DIR` (`routers/static_site.py`).

Yêu cầu chính: mọi phản hồi của API (mã, `Allow`, redirect, thân JSON) y hệt khi không gắn trang tĩnh.
"""

from __future__ import annotations

import ast
import errno
import importlib.util
import json
import os
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import IO, Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocketDisconnect

from holocron_core.infrastructure.llm_config import ConfigError
from holocron_core.routers import api, static_site
from holocron_core.routers.static_site import (
    CACHE_IMMUTABLE,
    ENV_WEB_DIR,
    _route_path,
    attach_static_site,
    inject_runtime,
    runtime_script,
    web_dir_from_env,
)

INDEX_HTML = '<!DOCTYPE html><html lang="vi"><head><meta charset="utf-8"/><title>Holocron</title></head><body>Trang chủ</body></html>'
VAULT_HTML = '<html><HEAD data-x="1">\r\n<title>Kho</title></HEAD><body><header>h</header></body></html>'
NOT_FOUND_HTML = "<html><head><title>404</title></head><body>Không tìm thấy trang</body></html>"
SECRET = "BI-MAT-NGOAI-THU-MUC-WEB"

RUNTIME_RE = re.compile(r"<script>window\.__HOLOCRON_RUNTIME__ = (\{.*?\});</script>")


def _make_web_dir(root: Path, *, with_404: bool = True) -> Path:
    """Thư mục web giả nhỏ, kèm một file bí mật nằm NGOÀI thư mục web để thử path traversal."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "secret.txt").write_text(SECRET, encoding="utf-8")
    web = root / "web"
    (web / "vault").mkdir(parents=True)
    (web / "_next" / "static" / "chunks").mkdir(parents=True)
    (web / "index.html").write_text(INDEX_HTML, encoding="utf-8")
    (web / "index.txt").write_text("payload RSC", encoding="utf-8")
    (web / "vault" / "index.html").write_bytes(VAULT_HTML.encode("utf-8"))
    (web / "vault" / "index.txt").write_text("payload RSC kho", encoding="utf-8")
    (web / "_next" / "static" / "chunks" / "app-abc123.js").write_text("console.log(1)", encoding="utf-8")
    (web / "favicon.ico").write_bytes(b"\x00\x00\x01\x00")
    if with_404:
        (web / "404.html").write_text(NOT_FOUND_HTML, encoding="utf-8")
    return web


def _sample_app() -> FastAPI:
    """App mẫu có route GET-only, POST-only và route có dấu `/` cuối."""
    app = FastAPI()

    @app.get("/v1/get-only")
    def get_only() -> dict[str, str]:
        return {"route": "get-only"}

    @app.post("/v1/post-only")
    def post_only() -> dict[str, str]:
        return {"route": "post-only"}

    @app.get("/v1/x/")
    def trailing() -> dict[str, str]:
        return {"route": "x"}

    return app


@pytest.fixture
def web_dir(tmp_path: Path) -> Path:
    return _make_web_dir(tmp_path)


@pytest.fixture
def site(web_dir: Path) -> TestClient:
    app = _sample_app()
    attach_static_site(app, web_dir)
    return TestClient(app)


def _runtime_of(body: str) -> object:
    match = RUNTIME_RE.search(body)
    assert match is not None, body
    return json.loads(match.group(1))


# --- Hành vi API y hệt khi không gắn trang tĩnh ---

API_REQUESTS = [
    ("GET", "/v1/get-only"),
    ("HEAD", "/v1/get-only"),
    ("POST", "/v1/get-only"),
    ("DELETE", "/v1/get-only"),
    ("GET", "/v1/get-only/"),
    ("GET", "/v1/post-only"),
    ("PUT", "/v1/post-only"),
    ("POST", "/v1/post-only"),
    ("GET", "/v1/x"),
    ("GET", "/v1/x?a=1"),
    ("POST", "/v1/x"),
    ("GET", "/v1/x/"),
    ("POST", "/v1/khong-co"),
    ("GET", "/v1"),
    ("POST", "/v1"),
    ("GET", "/v1/"),
    ("GET", "/v1/khong-co"),
    ("HEAD", "/v1/khong-co"),
    ("DELETE", "/v1/khong-co/sau"),
    ("GET", "/docs"),
    ("GET", "/docs/khong-co"),
    ("POST", "/docs"),
    ("GET", "/redoc"),
    ("GET", "/openapi.json"),
    ("POST", "/openapi.json"),
    ("GET", "/openapi.json/khong-co"),
]


def _snapshot(client: TestClient, method: str, url: str) -> tuple[int, str | None, str | None, str | None, bytes]:
    response = client.request(method, url, follow_redirects=False)
    headers = response.headers
    return (
        response.status_code,
        headers.get("allow"),
        headers.get("location"),
        headers.get("content-type"),
        response.content,
    )


@pytest.mark.parametrize(("method", "url"), API_REQUESTS)
def test_api_responses_identical_with_and_without_static_site(web_dir: Path, method: str, url: str) -> None:
    plain = TestClient(_sample_app())
    with_site_app = _sample_app()
    attach_static_site(with_site_app, web_dir)
    with_site = TestClient(with_site_app)
    assert _snapshot(with_site, method, url) == _snapshot(plain, method, url)


def test_api_reference_responses_are_the_current_defaults(site: TestClient) -> None:
    """Chốt luôn giá trị tuyệt đối, phòng trường hợp cả hai app cùng lệch."""
    assert site.post("/v1/khong-co").json() == {"detail": "Not Found"}
    assert site.post("/v1/khong-co").status_code == 404
    assert site.get("/v1").status_code == 404
    assert site.get("/v1").json() == {"detail": "Not Found"}
    assert site.get("/v1/khong-co").json() == {"detail": "Not Found"}
    not_allowed = site.get("/v1/post-only")
    assert (not_allowed.status_code, not_allowed.headers["allow"]) == (405, "POST")
    redirect = site.get("/v1/x?a=1", follow_redirects=False)
    assert redirect.status_code == 307
    assert redirect.headers["location"].endswith("/v1/x/?a=1")


def test_websocket_outside_api_is_closed_like_before(site: TestClient) -> None:
    with pytest.raises(WebSocketDisconnect), site.websocket_connect("/vault/"):
        pass


# --- Phục vụ trang tĩnh ---


def test_root_serves_index_with_runtime(site: TestClient) -> None:
    response = site.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/html; charset=utf-8"
    assert response.headers["cache-control"] == "no-store"
    assert "etag" not in response.headers
    assert "last-modified" not in response.headers
    assert _runtime_of(response.text) == {"apiBase": "/"}
    assert response.text.startswith('<!DOCTYPE html><html lang="vi"><head><script>window.__HOLOCRON_RUNTIME__')
    assert response.text.endswith(
        '</script><meta charset="utf-8"/><title>Holocron</title></head><body>Trang chủ</body></html>'
    )
    assert "token" not in response.text


def test_nested_page_injects_after_head_with_attributes_and_keeps_bytes(site: TestClient) -> None:
    response = site.get("/vault/?subject=IT3070")
    assert response.status_code == 200
    expected = VAULT_HTML.replace('<HEAD data-x="1">', '<HEAD data-x="1">' + runtime_script().decode(), 1)
    assert response.content == expected.encode("utf-8")
    assert response.headers["content-length"] == str(len(response.content))


def test_directory_without_slash_redirects_keeping_query(site: TestClient) -> None:
    response = site.get("/vault?subject=IT3070&doc=d1", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"].endswith("/vault/?subject=IT3070&doc=d1")


def test_unknown_path_serves_404_html_with_runtime(site: TestClient) -> None:
    response = site.get("/khong-co-trang-nay")
    assert response.status_code == 404
    assert "Không tìm thấy trang" in response.text
    assert _runtime_of(response.text) == {"apiBase": "/"}
    assert response.headers["cache-control"] == "no-store"


def test_unknown_path_without_404_html_is_json_404(tmp_path: Path) -> None:
    app = _sample_app()
    attach_static_site(app, _make_web_dir(tmp_path, with_404=False))
    response = TestClient(app).get("/khong-co")
    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
def test_non_get_outside_api_is_405_json(site: TestClient, method: str) -> None:
    for url in ("/", "/vault/", "/khong-co"):
        response = site.request(method, url)
        assert response.status_code == 405
        assert response.json() == {"detail": "Method Not Allowed"}
        assert response.headers["allow"] == "GET, HEAD"


def test_head_html_has_same_headers_and_length_as_get(site: TestClient) -> None:
    for url, status in (("/", 200), ("/vault/", 200), ("/khong-co", 404)):
        got = site.get(url)
        head = site.head(url)
        assert head.status_code == got.status_code == status
        assert head.content == b""
        assert head.headers["content-length"] == got.headers["content-length"] == str(len(got.content))
        assert head.headers["cache-control"] == "no-store"
        assert "etag" not in head.headers


def test_html_never_304_or_206(site: TestClient) -> None:
    conditional = site.get("/", headers={"If-None-Match": "*", "If-Modified-Since": "Fri, 01 Jan 2100 00:00:00 GMT"})
    assert conditional.status_code == 200
    assert _runtime_of(conditional.text) == {"apiBase": "/"}
    ranged = site.get("/", headers={"Range": "bytes=0-3"})
    assert ranged.status_code == 200
    assert "content-range" not in ranged.headers
    assert ranged.content == site.get("/").content


# --- Cache ---


def test_next_static_200_is_immutable(site: TestClient) -> None:
    response = site.get("/_next/static/chunks/app-abc123.js")
    assert response.status_code == 200
    assert response.text == "console.log(1)"
    assert response.headers["cache-control"] == CACHE_IMMUTABLE


def test_next_static_304_keeps_immutable(site: TestClient) -> None:
    first = site.get("/_next/static/chunks/app-abc123.js")
    again = site.get("/_next/static/chunks/app-abc123.js", headers={"If-None-Match": first.headers["etag"]})
    assert again.status_code == 304
    assert again.headers["cache-control"] == CACHE_IMMUTABLE


def test_next_static_404_is_not_immutable(site: TestClient, tmp_path: Path) -> None:
    missing = site.get("/_next/static/chunks/khong-co.js")
    assert missing.status_code == 404
    assert "immutable" not in missing.headers.get("cache-control", "")
    app = _sample_app()
    attach_static_site(app, _make_web_dir(tmp_path / "khong-404", with_404=False))
    bare = TestClient(app).get("/_next/static/chunks/khong-co.js")
    assert bare.status_code == 404
    assert "immutable" not in bare.headers.get("cache-control", "")


def test_rsc_payload_txt_is_no_store(site: TestClient) -> None:
    for url in ("/index.txt", "/vault/index.txt"):
        response = site.get(url)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"


def test_other_files_are_no_cache(site: TestClient) -> None:
    response = site.get("/favicon.ico")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"
    head = site.head("/favicon.ico")
    assert head.headers["cache-control"] == "no-cache"


# --- Path traversal ---

TRAVERSAL_URLS = [
    "/%2e%2e/secret.txt",
    "/vault/%2e%2e/%2e%2e/secret.txt",
    "/..%2fsecret.txt",
    "/%2e%2e%2fsecret.txt",
    "/vault%2f..%2f..%2fsecret.txt",
    "/..\\secret.txt",
    "/..%5csecret.txt",
    "/%00",
    "/index.html%00.txt",
    "/index.html.",
    "/index.html%20",
]


@pytest.mark.parametrize("url", TRAVERSAL_URLS)
def test_traversal_is_blocked(site: TestClient, url: str) -> None:
    response = site.get(url)
    assert response.status_code == 404
    assert SECRET not in response.text
    assert "Trang chủ" not in response.text


@pytest.mark.skipif(sys.platform != "win32", reason="đường dẫn đặc thù Windows")
@pytest.mark.parametrize(
    "url",
    [
        "/%5c..%5csecret.txt",
        "/vault%5c..%5c..%5csecret.txt",
        "/index.html::$DATA",
        "/index.html:$DATA",
        "/CON",
        "/con",
        "/NUL.txt",
        "/vault/AUX",
        "/COM1.js",
    ],
)
def test_windows_specific_paths_are_blocked(site: TestClient, url: str) -> None:
    response = site.get(url)
    assert response.status_code == 404
    assert SECRET not in response.text
    assert "Trang chủ" not in response.text


# --- Hàm thuần ---


def test_inject_runtime_only_after_first_head_open_tag() -> None:
    script = runtime_script()
    assert inject_runtime(b"<html><body>no head</body></html>") == b"<html><body>no head</body></html>"
    assert inject_runtime(b"<header>x</header>") == b"<header>x</header>"
    assert inject_runtime(b"<head><head>") == b"<head>" + script + b"<head>"
    assert inject_runtime(b"<header></header><HeAd\nclass='a'>") == b"<header></header><HeAd\nclass='a'>" + script
    with pytest.raises(UnicodeDecodeError):
        inject_runtime(b"<head>\xff")


def test_runtime_script_is_json_without_raw_less_than() -> None:
    script = runtime_script().decode()
    inner = script.removeprefix("<script>").removesuffix("</script>")
    assert "<" not in inner
    assert _runtime_of(script) == {"apiBase": "/"}


@pytest.mark.parametrize("value", ["", "   ", "\t\n"])
def test_blank_env_means_unset(value: str) -> None:
    assert web_dir_from_env({ENV_WEB_DIR: value}) is None
    assert web_dir_from_env({}) is None


def test_env_value_is_stripped(tmp_path: Path) -> None:
    assert web_dir_from_env({ENV_WEB_DIR: f"  {tmp_path}  "}) == tmp_path


def test_route_path_strips_root_path() -> None:
    assert _route_path({"path": "/base/vault/", "root_path": "/base"}) == "/vault/"
    assert _route_path({"path": "/base", "root_path": "/base"}) == ""
    assert _route_path({"path": "/basement/", "root_path": "/base"}) == "/basement/"
    assert _route_path({"path": "/vault/", "root_path": ""}) == "/vault/"


def test_relative_web_dir_resolves_against_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _make_web_dir(tmp_path)
    monkeypatch.chdir(tmp_path)
    app = _sample_app()
    attach_static_site(app, Path("web"))
    monkeypatch.chdir(tmp_path / "web" / "vault")
    assert "Trang chủ" in TestClient(app).get("/").text


# --- Lỗi cấu hình ---


def _bad_dirs(tmp_path: Path) -> list[Path]:
    no_index = tmp_path / "no-index"
    no_index.mkdir()
    index_is_dir = tmp_path / "index-is-dir"
    (index_is_dir / "index.html").mkdir(parents=True)
    a_file = tmp_path / "a-file.html"
    a_file.write_text("<head>", encoding="utf-8")
    return [tmp_path / "khong-ton-tai", no_index, index_is_dir, a_file, Path("\x00bad")]


def test_invalid_web_dir_raises_config_error_without_path(tmp_path: Path) -> None:
    for bad in _bad_dirs(tmp_path):
        app = _sample_app()
        routes_before = len(app.routes)
        with pytest.raises(ConfigError) as caught:
            attach_static_site(app, bad)
        message = str(caught.value)
        assert ENV_WEB_DIR in message
        assert str(tmp_path) not in message
        assert tmp_path.name not in message
        assert caught.value.__cause__ is None
        assert len(app.routes) == routes_before
        assert TestClient(app).get("/").json() == {"detail": "Not Found"}


# --- `routers/api.py` nạp lại dưới tên module phụ ---

API_FILE = Path(api.__file__)


def _load_api_probe() -> ModuleType:
    spec = importlib.util.spec_from_file_location("holocron_core.routers._api_static_probe", API_FILE)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_api_module_attaches_static_site_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original_app = api.app
    original_id = id(api.app)
    original_routes = len(api.app.routes)
    original_default = api.app.router.default
    expected_runtime = TestClient(api.app).get("/v1/runtime")

    monkeypatch.setenv(ENV_WEB_DIR, str(_make_web_dir(tmp_path)))
    probe = _load_api_probe()
    client = TestClient(probe.app)
    runtime = client.get("/v1/runtime")
    assert runtime.status_code == expected_runtime.status_code == 200
    assert runtime.json() == expected_runtime.json()
    home = client.get("/")
    assert home.status_code == 200
    assert _runtime_of(home.text) == {"apiBase": "/"}
    assert client.get("/v1/khong-co").json() == {"detail": "Not Found"}
    assert client.get("/v1/ingestion/upload").status_code == 405
    assert len(probe.app.routes) == original_routes

    assert api.app is original_app
    assert id(api.app) == original_id
    assert len(api.app.routes) == original_routes
    assert api.app.router.default == original_default
    assert "holocron_core.routers._api_static_probe" not in sys.modules
    assert TestClient(api.app).get("/").json() == {"detail": "Not Found"}


def test_api_module_without_env_attaches_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_WEB_DIR, "   ")
    probe = _load_api_probe()
    response = TestClient(probe.app).get("/")
    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}


def test_api_module_rejects_bad_web_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    routes = len(api.app.routes)
    for bad in _bad_dirs(tmp_path)[:4]:
        monkeypatch.setenv(ENV_WEB_DIR, str(bad))
        with pytest.raises(ConfigError) as caught:
            _load_api_probe()
        assert ENV_WEB_DIR in str(caught.value)
        assert str(tmp_path) not in str(caught.value)
    assert len(api.app.routes) == routes


def test_unreadable_index_raises_config_error_without_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    web = _make_web_dir(tmp_path)
    real_open = Path.open

    def deny_index(self: Path, mode: str = "r", *args: Any, **kwargs: Any) -> IO[Any]:
        if self.name == "index.html":
            raise PermissionError(13, "Permission denied", str(self))
        return real_open(self, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", deny_index)
    with pytest.raises(ConfigError) as caught:
        attach_static_site(_sample_app(), web)
    assert ENV_WEB_DIR in str(caught.value)
    assert str(tmp_path) not in str(caught.value)
    assert caught.value.__cause__ is None


def test_unresolvable_web_dir_raises_config_error_without_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    web = _make_web_dir(tmp_path)

    def fail_resolve(self: Path, strict: bool = False) -> Path:
        raise OSError(5, "I/O error", str(self))

    monkeypatch.setattr(Path, "resolve", fail_resolve)
    with pytest.raises(ConfigError) as caught:
        attach_static_site(_sample_app(), web)
    assert ENV_WEB_DIR in str(caught.value)
    assert str(tmp_path) not in str(caught.value)
    assert caught.value.__cause__ is None


# --- Ký tự Windows không nhận và đoạn quá dài: 404 như đường dẫn lạ khác, không 500 ---

ODD_NAME_URLS = ["/a%2Ab", "/a%3Fb", "/a%3Cb", "/a%3Eb", "/a%7Cb", "/a%22b", "/vault/a%2Ab.txt", "/" + "a" * 256]


@pytest.mark.parametrize("url", ODD_NAME_URLS)
def test_odd_names_are_404_html_not_500(site: TestClient, url: str) -> None:
    response = site.get(url)
    assert response.status_code == 404
    assert "Không tìm thấy trang" in response.text


@pytest.mark.parametrize("url", ODD_NAME_URLS)
def test_odd_names_without_404_html_are_json_404(tmp_path: Path, url: str) -> None:
    app = _sample_app()
    attach_static_site(app, _make_web_dir(tmp_path, with_404=False))
    response = TestClient(app).get(url)
    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}


def test_segment_of_255_chars_is_still_looked_up(site: TestClient) -> None:
    response = site.get("/" + "a" * 255)
    assert response.status_code == 404
    assert "Không tìm thấy trang" in response.text


def test_os_error_while_looking_up_file_is_404(site: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken_lookup(self: StaticFiles, path: str) -> tuple[str, os.stat_result | None]:
        raise OSError(errno.EINVAL, "Invalid argument", path)

    monkeypatch.setattr(StaticFiles, "lookup_path", broken_lookup)
    response = site.get("/vault/")
    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}


# --- HTML không phải UTF-8: trả nguyên văn, không chèn runtime, vẫn no-store ---


def test_non_utf8_html_is_served_unchanged(web_dir: Path) -> None:
    utf16_html = "<html><head><title>Tiếng Việt</title></head></html>".encode("utf-16")
    (web_dir / "vault" / "index.html").write_bytes(utf16_html)
    broken_404 = b"<html><head>\xff\xfe kh\xf4ng th\xe2y</head></html>"
    (web_dir / "404.html").write_bytes(broken_404)
    app = _sample_app()
    attach_static_site(app, web_dir)
    client = TestClient(app)
    for url, status, body in (("/vault/", 200, utf16_html), ("/khong-co", 404, broken_404)):
        response = client.get(url)
        assert response.status_code == status
        assert response.content == body
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["content-length"] == str(len(body))
        assert "etag" not in response.headers
        head = client.head(url)
        assert head.status_code == status
        assert head.headers["content-length"] == str(len(body))


# --- Cache quyết theo đường dẫn file đã chuẩn hoá, không theo URL thô ---


@pytest.mark.parametrize(
    ("url", "cache"),
    [
        ("/_next/static/%2e%2e/%2e%2e/index.txt", "no-store"),
        ("/_next/static/chunks/%2e%2e/%2e%2e/%2e%2e/favicon.ico", "no-cache"),
        ("/_next/static/./chunks/app-abc123.js", CACHE_IMMUTABLE),
        ("/vault/%2e%2e/_next/static/chunks/app-abc123.js", CACHE_IMMUTABLE),
        ("/_next/static%2fchunks%2fapp-abc123.js", CACHE_IMMUTABLE),
    ],
)
def test_cache_uses_normalized_file_path(site: TestClient, url: str, cache: str) -> None:
    response = site.get(url)
    assert response.status_code == 200
    assert response.headers["cache-control"] == cache


def test_file_directly_in_next_static_dir_name_is_not_special(web_dir: Path) -> None:
    (web_dir / "_next" / "static.txt").write_text("x", encoding="utf-8")
    (web_dir / "_next" / "staticx").mkdir()
    (web_dir / "_next" / "staticx" / "a.js").write_text("x", encoding="utf-8")
    app = _sample_app()
    attach_static_site(app, web_dir)
    client = TestClient(app)
    assert client.get("/_next/static.txt").headers["cache-control"] == "no-store"
    assert client.get("/_next/staticx/a.js").headers["cache-control"] == "no-cache"


def test_static_site_does_not_import_anyio_directly() -> None:
    """`anyio` chỉ là phụ thuộc bắc cầu của starlette, không khai trong `pyproject.toml`: không import trực tiếp."""
    source = Path(static_site.__file__).read_text(encoding="utf-8")
    roots: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None and node.level == 0:
            roots.add(node.module.split(".")[0])
    assert "anyio" not in roots
    assert "starlette" in roots
