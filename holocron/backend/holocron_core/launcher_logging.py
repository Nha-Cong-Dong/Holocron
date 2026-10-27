"""Log ra file và thông tin build cho launcher của bản portable (chỉ dùng thư viện chuẩn).

- Log LUÔN nằm ở thư mục log theo người dùng (`default_log_dir`), kể cả khi có `--data-dir`/`HOLOCRON_DATA_DIR`:
  người test chỉ cần nhớ một chỗ để gửi log. Hai phiên bản khác `--data-dir` cùng ghi một `holocron.log`.
- Handler file (`HolocronFileHandler`) gắn vào logger `holocron_core`; `uvicorn.Config()` chạy `dictConfig` và xoá
  handler của logger `uvicorn*`, nên `serve_app` gọi `reattach_file_handlers()` ngay sau khi tạo `Config`.
- `SecretFilter` che giá trị của biến môi trường có tên chứa KEY/TOKEN/SECRET/PASSWORD trước khi ghi (spec §11.2).
- `read_build_info` đọc `app/holocron_build.json` do `scripts/build_portable.py` ghi; file thiếu/hỏng thì lùi về
  version của gói đã cài (hoặc `dev`) và commit `unknown`.
"""

from __future__ import annotations

import contextlib
import copy
import importlib.metadata
import json
import logging
import logging.handlers
import sys
import unicodedata
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

LOG_FILENAME = "holocron.log"
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 5
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
APP_LOGGER = "holocron_core"
LAUNCHER_LOGGER = (
    "holocron_core.launcher"  # tên cố định: khi chạy `-m holocron_core.launcher`, `__name__` là `__main__`
)
UVICORN_LOGGERS = ("uvicorn", "uvicorn.error")
SECRET_NAME_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD")
MIN_SECRET_LENGTH = 8
SECRET_MASK = "***"
_WRITTEN_BY = "_holocron_file_handler_id"

BUILD_INFO_FILENAME = "holocron_build.json"
BUILD_INFO_MAX_BYTES = 4096
BUILD_FIELD_MAX_CHARS = 64
DIST_NAME = "holocron-core"
DEV_VERSION = "dev"
UNKNOWN_COMMIT = "unknown"


def _env_value(environ: Mapping[str, str], name: str) -> str | None:
    """Giá trị đã cắt khoảng trắng; rỗng coi như chưa đặt (giống launcher)."""
    value = environ.get(name, "").strip()
    return value or None


def default_log_dir(environ: Mapping[str, str], platform: str, home: Path) -> Path:
    """Windows `%LOCALAPPDATA%\\Holocron\\logs` (thiếu thì `~/AppData/Local`); nơi khác `$XDG_STATE_HOME/holocron/logs`
    hoặc `~/.local/state/holocron/logs`."""
    if platform == "win32":
        local = _env_value(environ, "LOCALAPPDATA")
        base = Path(local) if local else home / "AppData" / "Local"
        return base / "Holocron" / "logs"
    xdg = _env_value(environ, "XDG_STATE_HOME")
    state = Path(xdg) if xdg else home / ".local" / "state"
    return state / "holocron" / "logs"


# ---------------------------------------------------------------- che bí mật


def secret_values(environ: Mapping[str, str]) -> tuple[str, ...]:
    """Giá trị của biến có tên chứa KEY/TOKEN/SECRET/PASSWORD (không phân biệt hoa thường), bỏ giá trị ngắn hơn
    `MIN_SECRET_LENGTH`; giá trị dài đứng trước để khi hai bí mật lồng nhau, bí mật dài được che trọn."""
    found: set[str] = set()
    for name, value in environ.items():
        if not any(marker in name.upper() for marker in SECRET_NAME_MARKERS):
            continue
        for candidate in (value, value.strip()):
            if len(candidate.strip()) >= MIN_SECRET_LENGTH:
                found.add(candidate)
    return tuple(sorted(found, key=lambda item: (-len(item), item)))


class SecretFilter(logging.Filter):
    """Filter gắn lên HANDLER: trả bản sao của record đã che (không đổi record gốc mà handler khác dùng).

    Danh sách giá trị lấy một lần lúc dựng; che message đã format (gồm `record.args`), `exc_text` (format exception
    trước rồi che) và `stack_info`.
    """

    def __init__(self, environ: Mapping[str, str]) -> None:
        super().__init__()
        self._secrets = secret_values(environ)
        self._exception_formatter = logging.Formatter()

    def mask(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, SECRET_MASK)
        return text

    def filter(self, record: logging.LogRecord) -> bool | logging.LogRecord:
        if not self._secrets:
            return True
        masked = copy.copy(record)
        try:
            message = record.getMessage()
        except (TypeError, ValueError):
            message = f"{record.msg!s} {record.args!r}"
        masked.msg = self.mask(message)
        masked.args = None
        exc_text = record.exc_text
        if record.exc_info and not exc_text:
            exc_text = self._exception_formatter.formatException(record.exc_info)
        if exc_text:
            masked.exc_text = self.mask(exc_text)
        if record.stack_info:
            masked.stack_info = self.mask(record.stack_info)
        return masked


# ---------------------------------------------------------------- handler file


class HolocronFileHandler(logging.handlers.RotatingFileHandler):
    """Handler file xoay vòng của Holocron; một record chỉ ghi một lần dù đi qua nhiều logger có cùng handler
    (`uvicorn.error` lan lên `uvicorn`)."""

    def handle(self, record: logging.LogRecord) -> bool:
        if getattr(record, _WRITTEN_BY, None) == id(self):
            return False
        setattr(record, _WRITTEN_BY, id(self))
        return super().handle(record)


def _log_warning(log_dir: Path, exc: OSError) -> str:
    reason = exc.strerror or str(exc)
    return f"Cảnh báo: không ghi được log vào {log_dir} ({reason}); Holocron vẫn chạy nhưng không có file log."


@contextlib.contextmanager
def file_logging(log_dir: Path, secrets: Mapping[str, str], header: Sequence[str]) -> Iterator[str | None]:
    """Ghi log ra `<log_dir>/holocron.log` trong phạm vi khối `with`; giá trị yield là cảnh báo một dòng khi không
    mở được file (khi đó không gắn gì), `None` khi đã gắn.

    Logger `holocron_core` đặt mức INFO và có thêm `StreamHandler(stderr)` mức WARNING để console vẫn in WARNING như
    trước (thay `lastResort`). Không gắn vào root (tránh ghi đôi). Ra khỏi khối: gỡ handler khỏi mọi logger đã gắn
    (kể cả `uvicorn*` do `reattach_file_handlers`), đóng handler, khôi phục mức cũ.
    """
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = HolocronFileHandler(
            log_dir / LOG_FILENAME,
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8",
            delay=False,
        )
    except OSError as exc:
        yield _log_warning(log_dir, exc)
        return
    secret_filter = SecretFilter(secrets)
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
    file_handler.addFilter(secret_filter)
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(logging.WARNING)
    console_handler.addFilter(secret_filter)
    app_logger = logging.getLogger(APP_LOGGER)
    old_level = app_logger.level
    app_logger.addHandler(file_handler)
    app_logger.addHandler(console_handler)
    app_logger.setLevel(logging.INFO)
    try:
        launcher_logger = logging.getLogger(LAUNCHER_LOGGER)
        for line in header:
            launcher_logger.info("%s", line)
        yield None
    finally:
        for name in (APP_LOGGER, *UVICORN_LOGGERS):
            logger = logging.getLogger(name)
            for handler in (file_handler, console_handler):
                if handler in logger.handlers:
                    logger.removeHandler(handler)
        app_logger.setLevel(old_level)
        console_handler.close()
        file_handler.close()


def reattach_file_handlers() -> None:
    """Gắn lại mọi `HolocronFileHandler` của logger `holocron_core` vào `uvicorn`, `uvicorn.error` (không trùng).

    Gọi ngay sau `uvicorn.Config(...)`: bước đó chạy `dictConfig` và xoá handler của các logger `uvicorn*`. Không đổi
    mức của logger uvicorn: file chỉ nhận những gì uvicorn phát ở mức của nó (`log_level="warning"`).
    """
    handlers = [h for h in logging.getLogger(APP_LOGGER).handlers if isinstance(h, HolocronFileHandler)]
    for name in UVICORN_LOGGERS:
        logger = logging.getLogger(name)
        for handler in handlers:
            if handler not in logger.handlers:
                logger.addHandler(handler)


# ---------------------------------------------------------------- thông tin build


@dataclass(frozen=True)
class BuildInfo:
    """Nội dung `app/holocron_build.json`; `dirty`/`built_at` là `None` khi lùi về giá trị mặc định."""

    version: str
    commit: str
    dirty: bool | None = None
    built_at: str | None = None


def default_build_info_path() -> Path:
    """`<app>/holocron_build.json`: cạnh thư mục gói `holocron_core/` (bố cục bản zip)."""
    return Path(__file__).parents[1] / BUILD_INFO_FILENAME


def clean_field(text: str) -> str:
    """Bỏ ký tự điều khiển/định dạng (Unicode nhóm C) và cắt còn `BUILD_FIELD_MAX_CHARS` ký tự."""
    return "".join(ch for ch in text if not unicodedata.category(ch).startswith("C"))[:BUILD_FIELD_MAX_CHARS]


def installed_version() -> str:
    """Version của gói `holocron-core` đã cài; không có dist-info thì `dev`."""
    try:
        version = importlib.metadata.version(DIST_NAME)
    except importlib.metadata.PackageNotFoundError:
        return DEV_VERSION
    return clean_field(version) or DEV_VERSION


def fallback_build_info() -> BuildInfo:
    return BuildInfo(version=installed_version(), commit=UNKNOWN_COMMIT)


def _parse_build_info(raw: bytes) -> BuildInfo | None:
    if len(raw) > BUILD_INFO_MAX_BYTES:
        return None
    try:
        data: object = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    version, commit = data.get("version"), data.get("commit")
    dirty, built_at = data.get("dirty"), data.get("built_at")
    if not (isinstance(version, str) and isinstance(commit, str) and isinstance(built_at, str)):
        return None
    if not isinstance(dirty, bool):
        return None
    clean_version, clean_commit = clean_field(version), clean_field(commit)
    if not clean_version or not clean_commit:
        return None
    return BuildInfo(version=clean_version, commit=clean_commit, dirty=dirty, built_at=clean_field(built_at))


def read_build_info(path: Path | None = None) -> BuildInfo:
    """Đọc tối đa `BUILD_INFO_MAX_BYTES + 1` byte; quá 4096 byte, không phải JSON object, thiếu khoá hay sai kiểu
    (`version`/`commit`/`built_at` là chuỗi, `dirty` là bool) ⇒ `fallback_build_info()`."""
    target = default_build_info_path() if path is None else path
    try:
        with target.open("rb") as stream:
            raw = stream.read(BUILD_INFO_MAX_BYTES + 1)
    except OSError:
        return fallback_build_info()
    return _parse_build_info(raw) or fallback_build_info()


def version_line(info: BuildInfo) -> str:
    """Dòng `Holocron <version> (<commit7>)` in ra console và ghi đầu log."""
    return f"Holocron {info.version} ({info.commit[:7]})"
