"""Launcher của bản zip portable MVP-1: `python -X utf8 -m holocron_core.launcher [--no-browser] [--data-dir D] [--port P]`.

Là composition root ở gốc package (ngoài năm tầng của CLAUDE.md §2.2), nên được import `routers`. Thứ tự:

1. Dựng biến môi trường cho backend (`HOLOCRON_DATA_DIR`, `HOLOCRON_WEB_DIR`, `HOLOCRON_TESSERACT_CMD`,
   `HOLOCRON_TESSDATA_DIR`) TRƯỚC khi import `routers.api`, vì kho và cấu hình đọc env lúc tạo. Biến đã có trong env
   không bị ghi đè, trừ `HOLOCRON_DATA_DIR` khi có `--data-dir`. Không đặt biến LLM/VLM (bản portable chạy offline)
   và không đặt token truy cập.
2. Khoá độc quyền, không chặn `<data>/instance.lock`: chỉ một phiên bản dùng một thư mục dữ liệu. Lần mở thứ hai đọc
   cổng từ `<data>/instance.json`, mở trình duyệt tới đó rồi thoát.
3. Bind + listen socket `127.0.0.1:<port>` (mặc định cổng ngẫu nhiên), ghi `instance.json` nguyên tử, rồi giao socket
   cho uvicorn chạy ở luồng chính (Ctrl+C/Ctrl+Break đi qua lifespan: dừng bộ nhập, đóng SQLite). Luồng phụ thăm
   `GET /v1/runtime` tới khi có phản hồi rồi mở trình duyệt.
4. Chỉ phiên bản đang giữ khoá mới mở log file (`launcher_logging`): in `Holocron <version> (<commit7>)` và `Log: …`
   sau bốn dòng đầu; handler file được gắn lại sau `uvicorn.Config` (bước đó xoá handler của logger `uvicorn*`).

Các bước là hàm nhận phụ thuộc qua tham số (`environ`, `which`, `exists`, `fetch`, `sleep`, `clock`, `open_browser`)
để test không cần binary thật, trình duyệt hay uvicorn.
"""

from __future__ import annotations

import argparse
import http.client
import io
import logging
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from collections.abc import Callable, Mapping, MutableMapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import FrameType

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .infrastructure.document_readers.config import ENV_TESSDATA_DIR, ENV_TESSERACT_CMD
from .infrastructure.storage import DATA_DIR_ENV
from .launcher_logging import (
    FILE_ONLY,
    LAUNCHER_LOGGER,
    LOG_FILENAME,
    default_build_info_path,
    default_log_dir,
    file_logging,
    read_build_info,
    reattach_file_handlers,
    version_line,
)

if sys.platform == "win32":
    import msvcrt

    def _try_lock(fd: int) -> bool:
        """Khoá byte đầu của file, không chặn (`LK_NBLCK`); đang bị tiến trình khác giữ ⇒ False."""
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True

    def _unlock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

    def assign_to_kill_on_close_job() -> int | None:
        """Gán CHÍNH tiến trình launcher vào một Job Object `KILL_ON_JOB_CLOSE`; trả handle Job, lỗi ⇒ None.

        Mọi tiến trình con (worker PDF, sandbox, Tesseract) kế thừa Job này, nên khi launcher chết (kể cả đóng cửa sổ
        console) handle cuối cùng đóng theo và Windows giết hết chúng. Job lồng (Windows 8+): sandbox và worker vẫn tự
        gán Job giới hạn RAM/CPU riêng bên trong (`sandbox/os_limits.py`); `tests/test_launcher.py` kiểm điều này.
        `BREAKAWAY_OK` chỉ để trình duyệt được mở NGOÀI Job (`open_browser_default`); tiến trình nào không xin
        `CREATE_BREAKAWAY_FROM_JOB` vẫn ở trong Job, và Job của sandbox/worker không cho breakaway.
        Handle không được đóng: nó sống tới khi tiến trình thoát.
        """
        import ctypes
        from ctypes import wintypes

        class BasicLimits(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]
            LimitFlags: int

        class IoCounters(ctypes.Structure):
            _fields_ = [
                (n, ctypes.c_uint64)
                for n in ("ReadOps", "WriteOps", "OtherOps", "ReadBytes", "WriteBytes", "OtherBytes")
            ]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimits),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]
            BasicLimitInformation: BasicLimits

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return None
        info = ExtendedLimits()
        # 0x2000 KILL_ON_JOB_CLOSE, 0x800 BREAKAWAY_OK; 9 = JobObjectExtendedLimitInformation
        info.BasicLimitInformation.LimitFlags = 0x2000 | 0x800
        configured = kernel32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))
        if not configured or not kernel32.AssignProcessToJobObject(job, kernel32.GetCurrentProcess()):
            kernel32.CloseHandle(job)
            return None
        return int(job)

else:
    import fcntl

    def _try_lock(fd: int) -> bool:
        """`flock` độc quyền, không chặn; đang bị giữ ⇒ False."""
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        return True

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)

    def assign_to_kill_on_close_job() -> int | None:
        """Chỉ Windows có Job Object; Unix không gán gì (Ctrl+C/SIGTERM đi qua lifespan)."""
        return None


WEB_DIR_ENV = "HOLOCRON_WEB_DIR"
HOST = "127.0.0.1"
LOCK_FILENAME = "instance.lock"
INSTANCE_FILENAME = "instance.json"
WEB_INDEX = "index.html"
VIE_TRAINEDDATA = "vie.traineddata"
TESSERACT_HELP_FILE = "HUONG-DAN-TESSERACT.txt"
READY_PATH = "/v1/runtime"
READY_TIMEOUT_S = 60.0
READY_INTERVAL_S = 0.2
INSTANCE_WAIT_S = 10.0
EXIT_OK = 0
EXIT_BUSY = 1
EXIT_ERROR = 2
MAX_PORT = 65535

PathExists = Callable[[Path], bool]
Which = Callable[[str], str | None]
Emit = Callable[[str], None]


class LauncherError(Exception):
    """Lỗi người dùng sửa được (thư mục dữ liệu, cổng); `main` in thông điệp và thoát mã 2."""


class InstanceInfo(BaseModel):
    """Nội dung `<data>/instance.json`: chỉ cổng và pid, KHÔNG ghi token hay biến môi trường."""

    model_config = ConfigDict(extra="forbid")

    port: int = Field(ge=1, le=MAX_PORT)
    pid: int = Field(ge=1)


@dataclass(frozen=True)
class LaunchEnvironment:
    """Kết quả dò tìm lúc khởi động; `updates` là các biến launcher sẽ đặt vào env của tiến trình."""

    data_dir: Path
    data_dir_is_default: bool
    web_dir: Path | None
    tesseract_cmd: str | None
    tessdata_dir: Path | None
    vie_available: bool | None  # None: không đặt HOLOCRON_TESSDATA_DIR và không dò thấy, Tesseract tự dùng mặc định
    updates: Mapping[str, str]


def _env_value(environ: Mapping[str, str], name: str) -> str | None:
    """Giá trị đã cắt khoảng trắng; rỗng coi như chưa đặt (spec §11.2)."""
    value = environ.get(name, "").strip()
    return value or None


def _local_app_data(environ: Mapping[str, str], home: Path) -> Path:
    """`%LOCALAPPDATA%`; thiếu thì `~/AppData/Local`."""
    raw = _env_value(environ, "LOCALAPPDATA")
    return Path(raw) if raw else home / "AppData" / "Local"


def default_data_dir(environ: Mapping[str, str], *, platform: str, home: Path) -> Path:
    """Windows `%LOCALAPPDATA%\\Holocron\\data`; nơi khác `$XDG_DATA_HOME/holocron` hoặc `~/.local/share/holocron`."""
    if platform == "win32":
        return _local_app_data(environ, home) / "Holocron" / "data"
    xdg = _env_value(environ, "XDG_DATA_HOME")
    base = Path(xdg) if xdg else home / ".local" / "share"
    return base / "holocron"


def resolve_data_dir(
    cli_data_dir: str | None, environ: Mapping[str, str], *, platform: str, home: Path
) -> tuple[Path, bool]:
    """Thứ tự ưu tiên `--data-dir` > `HOLOCRON_DATA_DIR` có sẵn > mặc định; phần tử thứ hai cho biết là mặc định."""
    if cli_data_dir is not None and cli_data_dir.strip():
        return Path(cli_data_dir.strip()).expanduser().absolute(), False
    from_env = _env_value(environ, DATA_DIR_ENV)
    if from_env is not None:
        return Path(from_env), False
    return default_data_dir(environ, platform=platform, home=home), True


def zip_root_from_executable(executable: str) -> Path:
    """Gốc bản zip theo bố cục `<gốc>/python/python.exe` (kế hoạch đóng gói §3)."""
    return Path(executable).absolute().parent.parent


def find_web_dir(zip_root: Path, exists: PathExists) -> Path | None:
    """`<gốc>/web` chỉ khi có `index.html` (bản export của frontend)."""
    web_dir = zip_root / "web"
    return web_dir if exists(web_dir / WEB_INDEX) else None


def find_tesseract(
    environ: Mapping[str, str], *, platform: str, home: Path, which: Which, exists: PathExists
) -> str | None:
    """Tìm file chạy Tesseract: `HOLOCRON_TESSERACT_CMD` ⇒ `tesseract` trong PATH ⇒ thư mục cài mặc định trên Windows.

    Biến đã đặt thì chỉ kiểm nó (không dò tiếp): người dùng đã chọn binary, launcher không thay bằng binary khác.
    """
    configured = _env_value(environ, ENV_TESSERACT_CMD)
    if configured is not None:
        return which(configured) or (configured if exists(Path(configured)) else None)
    found = which("tesseract")
    if found:
        return found
    if platform != "win32":
        return None
    candidates: list[Path] = []
    program_files = _env_value(environ, "ProgramFiles")
    if program_files is not None:
        candidates.append(Path(program_files) / "Tesseract-OCR" / "tesseract.exe")
    candidates.append(_local_app_data(environ, home) / "Programs" / "Tesseract-OCR" / "tesseract.exe")
    return next((str(path) for path in candidates if exists(path)), None)


def find_tessdata(
    environ: Mapping[str, str],
    *,
    data_dir: Path,
    data_dir_is_default: bool,
    tesseract_cmd: str | None,
    exists: PathExists,
) -> tuple[Path | None, bool | None]:
    """Thư mục chứa `vie.traineddata` và cờ có (True) / thiếu (False) / không xác định (None) `vie`.

    Thứ tự: `HOLOCRON_TESSDATA_DIR` (đã đặt thì chỉ kiểm nó: có hoặc thiếu) ⇒ `<data>/../tessdata` (CHỈ khi thư mục
    dữ liệu là mặc định, để `--data-dir` tuỳ ý không dò lên gốc ổ đĩa) ⇒ `tessdata` cạnh file chạy Tesseract.
    Không dò thấy ở đâu thì KHÔNG khẳng định thiếu: Tesseract còn thư mục mặc định riêng (ví dụ `/usr/share/...`
    trên Linux, `TESSDATA_PREFIX`) mà launcher không biết.
    """
    configured = _env_value(environ, ENV_TESSDATA_DIR)
    if configured is not None:
        tessdata = Path(configured)
        return tessdata, exists(tessdata / VIE_TRAINEDDATA)
    candidates: list[Path] = []
    if data_dir_is_default:
        candidates.append(data_dir.parent / "tessdata")
    if tesseract_cmd is not None:
        candidates.append(Path(tesseract_cmd).parent / "tessdata")
    found = next((path for path in candidates if exists(path / VIE_TRAINEDDATA)), None)
    return found, (True if found is not None else None)


def build_launch_environment(
    environ: Mapping[str, str],
    *,
    cli_data_dir: str | None,
    zip_root: Path,
    platform: str,
    home: Path,
    which: Which,
    exists: PathExists,
) -> LaunchEnvironment:
    """Dò thư mục dữ liệu, web, Tesseract và chọn các biến cần đặt; không đổi `environ`."""
    data_dir, is_default = resolve_data_dir(cli_data_dir, environ, platform=platform, home=home)
    web_dir = find_web_dir(zip_root, exists)
    tesseract = find_tesseract(environ, platform=platform, home=home, which=which, exists=exists)
    tessdata, vie_available = find_tessdata(
        environ, data_dir=data_dir, data_dir_is_default=is_default, tesseract_cmd=tesseract, exists=exists
    )
    updates: dict[str, str] = {}
    if cli_data_dir is not None or _env_value(environ, DATA_DIR_ENV) is None:
        updates[DATA_DIR_ENV] = str(data_dir)
    candidates = {WEB_DIR_ENV: web_dir, ENV_TESSERACT_CMD: tesseract, ENV_TESSDATA_DIR: tessdata}
    for name, value in candidates.items():
        if value is not None and _env_value(environ, name) is None:
            updates[name] = str(value)
    return LaunchEnvironment(
        data_dir=data_dir,
        data_dir_is_default=is_default,
        web_dir=web_dir,
        tesseract_cmd=tesseract,
        tessdata_dir=tessdata,
        vie_available=vie_available,
        updates=updates,
    )


def apply_environment(environ: MutableMapping[str, str], updates: Mapping[str, str]) -> None:
    """Ghi các biến đã chọn ở `build_launch_environment` vào `environ`."""
    for name, value in updates.items():
        environ[name] = value


def ocr_status_line(launch: LaunchEnvironment) -> str:
    """Một dòng trạng thái OCR cho console."""
    engine = f"có Tesseract ({launch.tesseract_cmd})" if launch.tesseract_cmd else "thiếu Tesseract"
    if launch.vie_available is None:
        language = f"không xác định được {VIE_TRAINEDDATA} (Tesseract sẽ dùng thư mục mặc định của nó)"
    else:
        language = f"có {VIE_TRAINEDDATA}" if launch.vie_available else f"thiếu {VIE_TRAINEDDATA}"
    line = f"OCR: {engine}, {language}."
    if launch.tesseract_cmd is None or launch.vie_available is False:
        line += f" Trang scan sẽ không đọc được; xem {TESSERACT_HELP_FILE}."
    elif launch.vie_available is None:
        line += f" Nếu trang scan không đọc được, xem {TESSERACT_HELP_FILE}."
    return line


def ensure_data_dir(data_dir: Path) -> None:
    """Tạo thư mục dữ liệu nếu thiếu; không tạo được ⇒ `LauncherError`."""
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise LauncherError(f"Không tạo được thư mục dữ liệu {data_dir}: {exc.strerror or exc}") from exc


@dataclass
class InstanceLock:
    """Khoá độc quyền `<data>/instance.lock` đang giữ; `release` nhả khoá và đóng file."""

    fd: int
    path: Path
    released: bool = False

    def release(self) -> None:
        if self.released:
            return
        self.released = True
        try:
            _unlock(self.fd)
        finally:
            os.close(self.fd)


def try_acquire_instance_lock(data_dir: Path) -> InstanceLock | None:
    """Khoá không chặn; phiên bản khác đang giữ ⇒ None. Không mở/ghi được file khoá ⇒ `LauncherError`."""
    path = data_dir / LOCK_FILENAME
    try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    except OSError as exc:
        raise LauncherError(f"Không ghi được vào thư mục dữ liệu {data_dir}: {exc.strerror or exc}") from exc
    if _try_lock(fd):
        return InstanceLock(fd=fd, path=path)
    os.close(fd)
    return None


def write_instance_info(data_dir: Path, info: InstanceInfo) -> None:
    """Ghi `instance.json` nguyên tử (file tạm + `os.replace`); lỗi ghi ⇒ `LauncherError`."""
    target = data_dir / INSTANCE_FILENAME
    temporary = data_dir / f"{INSTANCE_FILENAME}.{info.pid}.tmp"
    try:
        temporary.write_text(info.model_dump_json(), encoding="utf-8")
        os.replace(temporary, target)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise LauncherError(f"Không ghi được {target}: {exc.strerror or exc}") from exc


def read_instance_port(data_dir: Path) -> int | None:
    """Cổng của phiên bản đang chạy; thiếu file, file hỏng hoặc chưa có cổng hợp lệ ⇒ None."""
    try:
        raw = (data_dir / INSTANCE_FILENAME).read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        return InstanceInfo.model_validate_json(raw).port
    except ValidationError:
        return None


def remove_instance_info(data_dir: Path) -> None:
    """Xoá `instance.json` khi thoát bình thường; lỗi xoá không chặn việc thoát."""
    try:
        (data_dir / INSTANCE_FILENAME).unlink(missing_ok=True)
    except OSError:
        return


def wait_for_instance(
    data_dir: Path,
    *,
    sleep: Callable[[float], object],
    clock: Callable[[], float],
    timeout: float = INSTANCE_WAIT_S,
    interval: float = READY_INTERVAL_S,
) -> int | InstanceLock | None:
    """Khoá đang bị giữ: thăm tới khi có cổng (`int`), lấy lại được khoá (`InstanceLock`) hoặc hết `timeout` (None).

    Lấy lại được khoá nghĩa là phiên bản kia đã thoát trong lúc chờ: người gọi chạy như lần mở đầu tiên thay vì báo
    treo. Sau mỗi lần ngủ, thử khoá TRƯỚC khi đọc cổng, để không đọc nhầm cổng chết mà phiên vừa thoát để lại.
    """
    deadline = clock() + timeout
    while True:
        port = read_instance_port(data_dir)
        if port is not None:
            return port
        if clock() >= deadline:
            return None
        sleep(interval)
        lock = try_acquire_instance_lock(data_dir)
        if lock is not None:
            return lock


def app_url(port: int) -> str:
    return f"http://{HOST}:{port}/"


def open_browser_or_print(url: str, open_browser: Callable[[str], object], emit: Emit) -> bool:
    """Mở trình duyệt; lỗi (`OSError`, `webbrowser.Error`) ⇒ in URL để người dùng tự mở, trả False."""
    try:
        open_browser(url)
    except (OSError, webbrowser.Error):
        emit(f"Không mở được trình duyệt; hãy tự mở {url}")
        return False
    return True


def handle_running_instance(
    data_dir: Path,
    port: int | None,
    *,
    no_browser: bool,
    open_browser: Callable[[str], object],
    emit: Emit,
) -> int:
    """Lần mở thứ hai: có cổng ⇒ mở trình duyệt tới phiên bản cũ, mã 0; không có cổng sau khi chờ ⇒ mã 1."""
    if port is None:
        emit(
            f"Holocron đang khởi động hoặc bị treo (thư mục dữ liệu {data_dir} đang bị khoá nhưng chưa có cổng). "
            "Thử lại sau ít giây; nếu vẫn lỗi, đóng cửa sổ Holocron cũ rồi mở lại."
        )
        return EXIT_BUSY
    url = app_url(port)
    emit(f"Holocron đã chạy sẵn tại {url} (thư mục dữ liệu {data_dir}).")
    if not no_browser and open_browser_or_print(url, open_browser, emit):
        emit("Đã mở trình duyệt tới phiên bản đang chạy.")
    return EXIT_OK


def port_number(text: str) -> int:
    """Kiểu cho `--port`: số nguyên 0–65535 (0 = cổng ngẫu nhiên)."""
    try:
        value = int(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"cổng phải là số nguyên: {text!r}") from exc
    if not 0 <= value <= MAX_PORT:
        raise argparse.ArgumentTypeError(f"cổng phải trong khoảng 0–{MAX_PORT}: {value}")
    return value


def bind_listening_socket(port: int, host: str = HOST) -> socket.socket:
    """Bind + listen trước khi giao cho uvicorn (không có khe hở giữa chọn cổng và nghe).

    Windows đặt `SO_EXCLUSIVEADDRUSE` và KHÔNG đặt `SO_REUSEADDR` (cờ này trên Windows cho tiến trình khác bind
    chung cổng). Unix đặt `SO_REUSEADDR` như uvicorn (chỉ bỏ qua TIME_WAIT, không cho hai socket cùng nghe).
    Cổng bị chiếm ⇒ `LauncherError`.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if sys.platform == "win32":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
        sock.listen(128)
    except OSError as exc:
        sock.close()
        raise LauncherError(f"Không mở được cổng {host}:{port}: {exc.strerror or exc}") from exc
    return sock


def probe_http(port: int, timeout: float = 1.0) -> bool:
    """Gửi `GET /v1/runtime` tới 127.0.0.1 bằng `http.client` (không qua proxy trong env); có phản hồi HTTP ⇒ True."""
    connection = http.client.HTTPConnection(HOST, port, timeout=timeout)
    try:
        connection.request("GET", READY_PATH, headers={"Host": f"{HOST}:{port}"})
        connection.getresponse().read()
    except (OSError, http.client.HTTPException):
        return False
    finally:
        connection.close()
    return True


def wait_until_ready(
    port: int,
    *,
    stop: threading.Event,
    fetch: Callable[[int], bool],
    sleep: Callable[[float], object],
    clock: Callable[[], float],
    timeout: float = READY_TIMEOUT_S,
    interval: float = READY_INTERVAL_S,
) -> bool:
    """Thăm tới khi có phản hồi (True), hết `timeout` hoặc `stop` được đặt (False)."""
    deadline = clock() + timeout
    while not stop.is_set():
        if fetch(port):
            return True
        if clock() >= deadline:
            return False
        sleep(interval)
    return False


def announce_when_ready(
    port: int,
    *,
    stop: threading.Event,
    no_browser: bool,
    fetch: Callable[[int], bool],
    sleep: Callable[[float], object],
    clock: Callable[[], float],
    open_browser: Callable[[str], object],
    emit: Emit,
    timeout: float = READY_TIMEOUT_S,
) -> None:
    """Thân luồng thăm: sẵn sàng ⇒ mở trình duyệt (trừ `--no-browser`); quá hạn ⇒ in URL và hướng dẫn.

    Server đã dừng (`stop`) thì im lặng thoát, không mở trình duyệt vào URL chết.
    """
    ready = wait_until_ready(port, stop=stop, fetch=fetch, sleep=sleep, clock=clock, timeout=timeout)
    if stop.is_set():
        return
    url = app_url(port)
    if not ready:
        emit(
            f"Holocron chưa phản hồi sau {timeout:.0f} giây. Thử mở {url} trong trình duyệt; "
            "nếu không được, đóng cửa sổ này rồi chạy lại Holocron."
        )
        return
    if no_browser:
        emit(f"Holocron đã sẵn sàng tại {url}")
        return
    emit(f"Holocron đã sẵn sàng; đang mở trình duyệt tới {url}")
    open_browser_or_print(url, open_browser, emit)


BROWSER_HELPER = "import sys, webbrowser; webbrowser.open(sys.argv[1])"
JOB_WARNING = (
    "Cảnh báo: không gán được Job Object cho Holocron; nếu đóng cửa sổ thay vì nhấn Ctrl+C, "
    "tiến trình con có thể còn chạy."
)


def open_browser_default(url: str) -> None:
    """Mở trình duyệt; trên Windows qua một Python phụ tách khỏi Job (`CREATE_BREAKAWAY_FROM_JOB`).

    Trình duyệt chưa chạy sẽ là tiến trình con của tiến trình mở nó; nếu nằm trong Job `KILL_ON_JOB_CLOSE` của
    launcher thì tắt Holocron sẽ giết luôn trình duyệt. Không tách được (Job ngoài không cho breakaway) ⇒ mở trực tiếp.

    Tiến trình phụ CỐ Ý kế thừa env của người dùng (không truyền `env=`), khác sandbox và worker vốn dùng env tối
    thiểu: trình duyệt nó mở ra là ứng dụng của người dùng, cần `PATH`, `SYSTEMROOT`, `USERPROFILE`, `APPDATA`,
    `BROWSER`… để tìm và chạy trình duyệt mặc định đúng hồ sơ người dùng. Env này không có bí mật do launcher thêm
    (launcher không đặt token; các biến `HOLOCRON_*` chỉ là đường dẫn), và cũng chính là env trình duyệt nhận khi
    người dùng tự mở. Tiến trình phụ chỉ chạy thư viện chuẩn với `-I` (bỏ `PYTHON*` và site của người dùng).
    """
    if sys.platform == "win32":
        try:
            subprocess.Popen(
                [sys.executable, "-I", "-c", BROWSER_HELPER, url],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                creationflags=subprocess.CREATE_BREAKAWAY_FROM_JOB | subprocess.CREATE_NO_WINDOW,
            )
        except OSError:
            webbrowser.open(url)
        return
    webbrowser.open(url)


def _raise_keyboard_interrupt(signum: int, frame: FrameType | None) -> None:
    raise KeyboardInterrupt


def install_shutdown_signals() -> None:
    """SIGTERM (và SIGBREAK trên Windows) thành `KeyboardInterrupt` như Ctrl+C.

    uvicorn bắt các tín hiệu này để tắt êm rồi phát lại tín hiệu cho handler cũ; handler mặc định của SIGBREAK/SIGTERM
    giết tiến trình ngay, bỏ qua bước dọn `instance.json` và nhả khoá.
    """
    signal.signal(signal.SIGTERM, _raise_keyboard_interrupt)
    if sys.platform == "win32":
        signal.signal(signal.SIGBREAK, _raise_keyboard_interrupt)


def serve_app(sock: socket.socket) -> None:
    """Import app SAU khi env đã đặt, rồi chạy uvicorn ở luồng chính trên socket đã listen."""
    import uvicorn

    from .routers.api import app

    install_shutdown_signals()
    config = uvicorn.Config(app, log_level="warning", access_log=False)
    reattach_file_handlers()
    uvicorn.Server(config).run(sockets=[sock])


def use_utf8_console(stream: object) -> None:
    """Console Windows mặc định không phải UTF-8 (CLAUDE.md §4.4); chỉ đổi khi là `TextIOWrapper` thật."""
    if isinstance(stream, io.TextIOWrapper):
        stream.reconfigure(encoding="utf-8")


def _print_line(message: str) -> None:
    print(message, flush=True)


def _path_exists(path: Path) -> bool:
    return path.exists()


@dataclass
class LauncherDeps:
    """Phụ thuộc của `main`; test thay từng phần (env giả, `serve` giả) để không chạy uvicorn hay mở trình duyệt."""

    environ: MutableMapping[str, str] = field(default_factory=lambda: os.environ)
    which: Which = shutil.which
    exists: PathExists = _path_exists
    zip_root: Path = field(default_factory=lambda: zip_root_from_executable(sys.executable))
    platform: str = sys.platform
    home: Path = field(default_factory=Path.home)
    emit: Emit = _print_line
    fetch: Callable[[int], bool] = probe_http
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], object] = time.sleep
    open_browser: Callable[[str], object] = open_browser_default
    serve: Callable[[socket.socket], None] = serve_app
    assign_job: Callable[[], int | None] = assign_to_kill_on_close_job
    pid: int = field(default_factory=os.getpid)
    build_info_path: Path = field(default_factory=default_build_info_path)


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="holocron", description="Khởi động Holocron trên máy này.")
    parser.add_argument("--no-browser", action="store_true", help="không tự mở trình duyệt")
    parser.add_argument("--data-dir", default=None, help="thư mục dữ liệu (mặc định trong thư mục người dùng)")
    parser.add_argument("--port", type=port_number, default=0, help="cổng nghe ở 127.0.0.1 (0 = ngẫu nhiên)")
    return parser.parse_args(argv)


def _serve_with_lock(args: argparse.Namespace, deps: LauncherDeps, launch: LaunchEnvironment) -> int:
    """Đang giữ khoá: xoá `instance.json` cũ → Job Object → bind → ghi `instance.json` → serve; luôn xoá khi thoát.

    `instance.json` cũ còn sót khi phiên trước chết không qua `finally` (đóng cửa sổ console, Job giết tiến trình):
    xoá ngay khi vừa lấy khoá để lần mở sau không đọc cổng chết, kể cả khi bước bind phía dưới thất bại.
    `job` là handle Job Object (Windows); giữ tới khi tiến trình thoát, Windows đóng nó và giết tiến trình con còn lại.
    """
    remove_instance_info(launch.data_dir)
    apply_environment(deps.environ, launch.updates)
    job = deps.assign_job()
    if job is None and deps.platform == "win32":
        deps.emit(JOB_WARNING)
    with bind_listening_socket(args.port) as sock:
        port = int(sock.getsockname()[1])
        write_instance_info(launch.data_dir, InstanceInfo(port=port, pid=deps.pid))
        try:
            deps.emit(f"Holocron: {app_url(port)}")
            deps.emit(f"Thư mục dữ liệu: {launch.data_dir}")
            deps.emit(ocr_status_line(launch))
            deps.emit("Đóng cửa sổ này hoặc nhấn Ctrl+C để tắt Holocron.")
            _run_logged(args, deps, sock, port)
        finally:
            remove_instance_info(launch.data_dir)
    return EXIT_OK


def _run_logged(args: argparse.Namespace, deps: LauncherDeps, sock: socket.socket, port: int) -> None:
    """Mở log file (sau khi đã giữ khoá) → in dòng version và vị trí log → luồng thăm → serve.

    Thư mục log suy từ `deps.environ`/platform/home; bí mật cần che lấy từ env đã đặt cho backend. Không mở được file
    log thì in cảnh báo thay cho dòng `Log:` và chạy tiếp. Ngoại lệ từ `deps.serve` ghi vào log rồi ném lại.
    """
    log_dir = default_log_dir(deps.environ, deps.platform, deps.home)
    header = (version_line(read_build_info(deps.build_info_path)), f"Log: {log_dir / LOG_FILENAME}")
    with file_logging(log_dir, deps.environ, header) as warning:
        deps.emit(header[0])
        deps.emit(warning or header[1])
        stop = threading.Event()
        poller = threading.Thread(
            target=announce_when_ready,
            name="holocron-ready",
            daemon=True,
            kwargs={
                "port": port,
                "stop": stop,
                "no_browser": args.no_browser,
                "fetch": deps.fetch,
                "sleep": stop.wait,
                "clock": deps.clock,
                "open_browser": deps.open_browser,
                "emit": deps.emit,
            },
        )
        poller.start()
        try:
            deps.serve(sock)
        except Exception:
            # Chỉ ghi file: ngoại lệ ném lại, Python tự in traceback ra console một lần.
            logging.getLogger(LAUNCHER_LOGGER).exception("Holocron dừng vì lỗi không mong đợi", extra=FILE_ONLY)
            raise
        finally:
            stop.set()


def main(argv: Sequence[str] | None = None, deps: LauncherDeps | None = None) -> int:
    """Điều phối mỏng: dựng env → tạo thư mục dữ liệu → khoá (bị giữ thì chờ cổng hoặc khoá) → serve."""
    deps = deps or LauncherDeps()
    args = parse_args(argv)
    try:
        launch = build_launch_environment(
            deps.environ,
            cli_data_dir=args.data_dir,
            zip_root=deps.zip_root,
            platform=deps.platform,
            home=deps.home,
            which=deps.which,
            exists=deps.exists,
        )
        ensure_data_dir(launch.data_dir)
        lock = try_acquire_instance_lock(launch.data_dir)
        if lock is None:
            outcome = wait_for_instance(launch.data_dir, sleep=deps.sleep, clock=deps.clock)
            if not isinstance(outcome, InstanceLock):
                return handle_running_instance(
                    launch.data_dir, outcome, no_browser=args.no_browser, open_browser=deps.open_browser, emit=deps.emit
                )
            lock = outcome
        try:
            return _serve_with_lock(args, deps, launch)
        finally:
            lock.release()
    except LauncherError as exc:
        deps.emit(f"Lỗi: {exc}")
        return EXIT_ERROR
    except KeyboardInterrupt:
        return EXIT_OK


if __name__ == "__main__":
    use_utf8_console(sys.stdout)
    use_utf8_console(sys.stderr)
    raise SystemExit(main())
