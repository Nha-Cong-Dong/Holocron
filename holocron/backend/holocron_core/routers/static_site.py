"""Phục vụ bản export tĩnh của frontend từ chính backend khi đặt `HOLOCRON_WEB_DIR` (chế độ portable, MVP-1).

- Không đặt biến (rỗng hoặc toàn khoảng trắng coi như không đặt) thì không gắn gì: app giữ nguyên hành vi.
- Đặt mà thư mục không tồn tại, hoặc `index.html` trong đó không phải file đọc được ⇒ `ConfigError` lúc tạo app;
  thông điệp chỉ nêu tên biến, không nêu đường dẫn. Đường dẫn tương đối được resolve theo thư mục làm việc hiện tại.
- Bộ phục vụ tĩnh được gắn làm `app.router.default`, KHÔNG phải `Mount("/")`: router chỉ gọi nó khi không có route
  nào khớp (kể cả khớp một phần, tức 405 kèm `Allow`) và sau bước redirect dấu `/` cuối. Nhờ vậy mọi phản hồi của
  API (404, 405, 307) y hệt khi không đặt biến. Đường dẫn thuộc `/v1`, `/docs`, `/redoc`, `/openapi.json` không bao
  giờ được phục vụ file: trả đúng 404 JSON mặc định (`{"detail": "Not Found"}`, SPECIFICATION §6.0).
- Ngoài các tiền tố đó: method khác GET/HEAD ⇒ 405 JSON; GET/HEAD ⇒ phục vụ file bằng `StaticFiles(html=True)`
  (chống path traversal, `/vault` redirect `/vault/` giữ query string, đường dẫn lạ trả `404.html` mã 404 nếu có).
- File `.html` được chèn `window.__HOLOCRON_RUNTIME__ = {"apiBase": "/"}` ngay sau thẻ mở `<head …>` đầu tiên để
  frontend gọi API cùng origin (`lib/config.ts`); trả `Cache-Control: no-store`, không `ETag`, không 304/206.
  Chưa chèn token (chờ quyết định về §13.1).
"""

from __future__ import annotations

import json
import os
import re
import stat
from collections.abc import Mapping
from pathlib import Path, PurePath

from fastapi import FastAPI
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers
from starlette.exceptions import HTTPException
from starlette.responses import FileResponse, Response
from starlette.staticfiles import NotModifiedResponse, StaticFiles
from starlette.types import ASGIApp, Receive, Scope, Send

from ..infrastructure.llm_config import ConfigError

ENV_WEB_DIR = "HOLOCRON_WEB_DIR"

INDEX_FILE = "index.html"

# Tiền tố của API và trang tài liệu OpenAPI: không bao giờ phục vụ file tĩnh dưới các tiền tố này.
API_PREFIXES = ("/v1", "/docs", "/redoc", "/openapi.json")

ALLOWED_METHODS = ("GET", "HEAD")

# Thư mục (tương đối thư mục web) chứa tài nguyên có hash trong tên: được cache lâu.
IMMUTABLE_DIR = ("_next", "static")
CACHE_IMMUTABLE = "public, max-age=31536000, immutable"
CACHE_NO_STORE = "no-store"
CACHE_NO_CACHE = "no-cache"

HTML_MEDIA_TYPE = "text/html; charset=utf-8"

# Thẻ mở `<head>` (không phân biệt hoa thường, cho phép thuộc tính); không khớp `<header>`.
_HEAD_OPEN_TAG = re.compile(rb"<head(?:\s[^>]*)?>", re.IGNORECASE)

# Tách đoạn đường dẫn theo cả `/` và `\` (dấu phân cách trên Windows sau `os.path.normpath`).
_PATH_SEPARATORS = re.compile(r"[\\/]")

# Ký tự Windows không cho trong tên file (ngoài `:` và ký tự điều khiển, kiểm riêng) và độ dài tối đa một đoạn.
_FORBIDDEN_CHARS = frozenset(':*?<>|"')
MAX_SEGMENT_LENGTH = 255

# Tên thiết bị Windows: `CON`, `NUL.txt`… trỏ tới thiết bị chứ không phải file trong thư mục web.
_WINDOWS_DEVICE_NAMES = frozenset(
    {"con", "prn", "aux", "nul", "conin$", "conout$"}
    | {f"com{index}" for index in range(10)}
    | {f"lpt{index}" for index in range(10)}
    | {"com¹", "com²", "com³", "lpt¹", "lpt²", "lpt³"}
)


def web_dir_from_env(env: Mapping[str, str] | None = None) -> Path | None:
    """Thư mục web từ `HOLOCRON_WEB_DIR` (mặc định đọc `os.environ`); rỗng hoặc toàn khoảng trắng ⇒ `None`."""
    source = os.environ if env is None else env
    value = source.get(ENV_WEB_DIR, "").strip()
    if not value:
        return None
    return Path(value)


def runtime_script() -> bytes:
    """Thẻ `<script>` khai báo `window.__HOLOCRON_RUNTIME__`; `<` được thoát để chuỗi không đóng được thẻ script."""
    payload = json.dumps({"apiBase": "/"}).replace("<", "\\u003c")
    return f"<script>window.__HOLOCRON_RUNTIME__ = {payload};</script>".encode()


def inject_runtime(html: bytes) -> bytes:
    """Chèn script runtime ngay sau thẻ mở `<head …>` đầu tiên; không có thẻ đó thì trả nguyên văn.

    Nội dung được giải mã UTF-8 tường minh (sai mã hoá ⇒ `UnicodeDecodeError`) trước khi chèn; phép chèn làm trên
    bytes để giữ nguyên từng byte còn lại của file (kể cả kiểu xuống dòng).
    """
    html.decode("utf-8")
    match = _HEAD_OPEN_TAG.search(html)
    if match is None:
        return html
    return html[: match.end()] + runtime_script() + html[match.end() :]


def _route_path(scope: Scope) -> str:
    """Đường dẫn của request sau khi bỏ `root_path` (cùng quy tắc với router của Starlette)."""
    path: str = scope["path"]
    root_path: str = scope.get("root_path", "")
    if root_path and path.startswith(root_path) and (len(path) == len(root_path) or path[len(root_path)] == "/"):
        return path[len(root_path) :]
    return path


def _is_api_path(path: str) -> bool:
    return any(path == prefix or path.startswith(prefix + "/") for prefix in API_PREFIXES)


def _is_html(path: str) -> bool:
    return path.lower().endswith(".html")


def _is_unsafe_segment(segment: str) -> bool:
    """Đoạn đường dẫn có thể trỏ tới thứ khác file thường của thư mục web (đặc biệt trên Windows).

    Từ chối trên mọi nền tảng để hành vi giống nhau: `:` (luồng dữ liệu phụ NTFS như `index.html::$DATA`), ký tự
    Windows không cho trong tên file (`* ? < > | "`, `os.stat` ném WinError 123), ký tự điều khiển, đoạn dài quá
    `MAX_SEGMENT_LENGTH`, đoạn kết thúc bằng dấu chấm hoặc khoảng trắng (Windows bỏ chúng, nên `index.html.` trỏ
    tới `index.html`) và tên thiết bị Windows (`CON`, `NUL.txt`…). `..` để `StaticFiles` chặn như thường.
    """
    if segment in ("", ".", ".."):
        return False
    if len(segment) > MAX_SEGMENT_LENGTH or not _FORBIDDEN_CHARS.isdisjoint(segment):
        return True
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in segment):
        return True
    if segment[-1] in ". ":
        return True
    stem = segment.split(".", 1)[0].rstrip(" ").lower()
    return stem in _WINDOWS_DEVICE_NAMES


def _cache_control(relative_path: PurePath, status_code: int) -> str:
    """`_next/static/*` (tên có hash) cache lâu khi 200; payload RSC `*.txt` không lưu; file khác phải hỏi lại.

    `relative_path` là đường dẫn file đã chuẩn hoá, tương đối thư mục web (không phải URL thô), để
    `/_next/static/%2e%2e/%2e%2e/index.txt` được xét như `index.txt`.
    """
    parts = relative_path.parts
    if status_code == 200 and len(parts) > len(IMMUTABLE_DIR) and parts[: len(IMMUTABLE_DIR)] == IMMUTABLE_DIR:
        return CACHE_IMMUTABLE
    if relative_path.name.lower().endswith(".txt"):
        return CACHE_NO_STORE
    return CACHE_NO_CACHE


def _read_injected(path: str) -> bytes:
    """Nội dung HTML đã chèn runtime; file không phải UTF-8 hợp lệ thì trả nguyên văn, không chèn."""
    with open(path, "rb") as handle:
        raw = handle.read()
    try:
        return inject_runtime(raw)
    except UnicodeDecodeError:
        return raw


class _StaticSite(StaticFiles):
    """`StaticFiles(html=True)` có chèn runtime vào HTML và đặt `Cache-Control` theo loại file."""

    def __init__(self, directory: Path) -> None:
        super().__init__(directory=directory, html=True, check_dir=False)
        # Gốc đã resolve, cùng cách `StaticFiles.lookup_path` resolve file (`os.path.realpath`).
        self.root = os.path.realpath(directory)

    def lookup_path(self, path: str) -> tuple[str, os.stat_result | None]:
        """Như `StaticFiles.lookup_path`, nhưng đoạn đường dẫn lạ hoặc lỗi hệ điều hành khi tra file ⇒ không thấy file.

        Bản gốc chỉ đổi `ENAMETOOLONG` thành 404 và ném lại mọi `OSError` khác (thành 500), ví dụ WinError 123 với
        tên chứa ký tự Windows không nhận. Ở đây mọi `OSError` khi tra file đều coi như không có file (404 hoặc
        `404.html`).
        """
        if any(_is_unsafe_segment(segment) for segment in _PATH_SEPARATORS.split(path)):
            return "", None
        try:
            return super().lookup_path(path)
        except OSError:
            return "", None

    def file_response(
        self,
        full_path: str | os.PathLike[str],
        stat_result: os.stat_result,
        scope: Scope,
        status_code: int = 200,
    ) -> Response:
        response = FileResponse(full_path, status_code=status_code, stat_result=stat_result)
        if _is_html(os.fspath(full_path)):
            # HTML được thay bằng bản đã chèn runtime ở `get_response`: không 304, không ETag, không Range.
            return response
        relative_path = PurePath(os.path.relpath(full_path, self.root))
        response.headers["Cache-Control"] = _cache_control(relative_path, status_code)
        if self.is_not_modified(response.headers, Headers(scope=scope)):
            return NotModifiedResponse(response.headers)
        return response

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        if not isinstance(response, FileResponse):
            return response
        file_path = os.fspath(response.path)
        if not _is_html(file_path):
            return response
        body = await run_in_threadpool(_read_injected, file_path)
        return Response(
            content=body,
            status_code=response.status_code,
            media_type=HTML_MEDIA_TYPE,
            headers={"Cache-Control": CACHE_NO_STORE},
        )


def _validated_web_dir(web_dir: Path) -> Path:
    """Thư mục web tuyệt đối có `index.html` đọc được; sai ⇒ `ConfigError` chỉ nêu tên biến, không nêu đường dẫn."""
    try:
        resolved = web_dir.resolve()
        is_dir = resolved.is_dir()
    except (OSError, ValueError):
        raise ConfigError(f"{ENV_WEB_DIR} không hợp lệ: không đọc được thư mục") from None
    if not is_dir:
        raise ConfigError(f"{ENV_WEB_DIR} không hợp lệ: thư mục không tồn tại")
    index = resolved / INDEX_FILE
    try:
        is_regular = stat.S_ISREG(index.stat().st_mode)
        if is_regular:
            with index.open("rb"):
                pass
    except (OSError, ValueError):
        raise ConfigError(f"{ENV_WEB_DIR} không hợp lệ: không đọc được file {INDEX_FILE}") from None
    if not is_regular:
        raise ConfigError(f"{ENV_WEB_DIR} không hợp lệ: {INDEX_FILE} không phải file")
    return resolved


def attach_static_site(app: FastAPI, web_dir: Path) -> None:
    """Gắn bộ phục vụ bản export tĩnh làm `app.router.default` (chỉ chạy khi không route nào khớp).

    Gọi SAU mọi `include_router`. `web_dir` sai ⇒ `ConfigError` (xem `_validated_web_dir`). Đường dẫn API không khớp
    route nào được chuyển cho bộ xử lý mặc định cũ của router, nên phản hồi y hệt khi không gắn trang tĩnh.
    """
    directory = _validated_web_dir(web_dir)
    site = _StaticSite(directory)
    not_found: ASGIApp = app.router.default

    async def serve(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or _is_api_path(_route_path(scope)):
            await not_found(scope, receive, send)
            return
        if scope["method"] not in ALLOWED_METHODS:
            raise HTTPException(status_code=405, headers={"Allow": ", ".join(ALLOWED_METHODS)})
        await site(scope, receive, send)

    app.router.default = serve
