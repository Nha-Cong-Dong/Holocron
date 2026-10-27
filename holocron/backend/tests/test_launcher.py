"""Launcher bản zip portable: dựng env, tìm Tesseract, khoá một phiên bản, cổng, luồng thăm, điều phối `main`.

Không mở trình duyệt, không chạy uvicorn thật, không ghi `os.environ`; mọi phụ thuộc được tiêm qua tham số.
"""

from __future__ import annotations

import argparse
import io
import json
import socket
import subprocess
import sys
import threading
import webbrowser
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import ClassVar

import pytest

from holocron_core import launcher
from holocron_core.launcher import (
    EXIT_BUSY,
    EXIT_ERROR,
    EXIT_OK,
    INSTANCE_FILENAME,
    WEB_DIR_ENV,
    InstanceInfo,
    LaunchEnvironment,
    LauncherDeps,
    LauncherError,
)

DATA_ENV = "HOLOCRON_DATA_DIR"
TESS_CMD_ENV = "HOLOCRON_TESSERACT_CMD"
TESSDATA_ENV = "HOLOCRON_TESSDATA_DIR"
ACCENTED = "Thư mục ố"
HOME = Path("/home/nguoi-dung")


def _no_which(_: str) -> str | None:
    return None


def _exists_in(paths: set[Path]) -> Callable[[Path], bool]:
    return lambda path: path in paths


class FakeClock:
    """Đồng hồ giả: `sleep` đẩy thời gian lên, không ngủ thật."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


# --- Thư mục dữ liệu ---------------------------------------------------------------------------------------------


def test_default_data_dir_windows_uses_localappdata() -> None:
    environ = {"LOCALAPPDATA": "/lad"}
    assert launcher.default_data_dir(environ, platform="win32", home=HOME) == Path("/lad") / "Holocron" / "data"


def test_default_data_dir_windows_without_localappdata_uses_home() -> None:
    expected = HOME / "AppData" / "Local" / "Holocron" / "data"
    assert launcher.default_data_dir({"LOCALAPPDATA": "  "}, platform="win32", home=HOME) == expected


def test_default_data_dir_unix_prefers_xdg_then_local_share() -> None:
    assert launcher.default_data_dir({"XDG_DATA_HOME": "/xdg"}, platform="linux", home=HOME) == Path("/xdg/holocron")
    assert launcher.default_data_dir({}, platform="darwin", home=HOME) == HOME / ".local" / "share" / "holocron"


def test_data_dir_priority_cli_over_env_over_default(tmp_path: Path) -> None:
    cli = tmp_path / ACCENTED
    environ = {DATA_ENV: "/from-env", "XDG_DATA_HOME": "/xdg"}
    assert launcher.resolve_data_dir(str(cli), environ, platform="linux", home=HOME) == (cli, False)
    assert launcher.resolve_data_dir(None, environ, platform="linux", home=HOME) == (Path("/from-env"), False)
    assert launcher.resolve_data_dir(None, {DATA_ENV: " "}, platform="linux", home=HOME) == (
        HOME / ".local" / "share" / "holocron",
        True,
    )


def test_relative_cli_data_dir_becomes_absolute() -> None:
    data_dir, is_default = launcher.resolve_data_dir("rel-dir", {}, platform="linux", home=HOME)
    assert data_dir.is_absolute() and data_dir.name == "rel-dir" and not is_default


def test_ensure_data_dir_creates_accented_nested_dir(tmp_path: Path) -> None:
    target = tmp_path / ACCENTED / "dữ liệu"
    launcher.ensure_data_dir(target)
    assert target.is_dir()


def test_ensure_data_dir_reports_error_when_path_is_a_file(tmp_path: Path) -> None:
    blocker = tmp_path / "là-file"
    blocker.write_text("x", encoding="utf-8")
    with pytest.raises(LauncherError, match="Không tạo được thư mục dữ liệu"):
        launcher.ensure_data_dir(blocker / "data")


# --- Web và Tesseract ---------------------------------------------------------------------------------------------


def test_zip_root_is_parent_of_python_dir(tmp_path: Path) -> None:
    executable = tmp_path / ACCENTED / "python" / "python.exe"
    assert launcher.zip_root_from_executable(str(executable)) == tmp_path / ACCENTED


def test_web_dir_requires_index_html(tmp_path: Path) -> None:
    root = tmp_path / ACCENTED
    assert launcher.find_web_dir(root, _exists_in(set())) is None
    assert launcher.find_web_dir(root, _exists_in({root / "web" / "index.html"})) == root / "web"


def test_tesseract_env_is_checked_without_falling_back() -> None:
    which_calls: list[str] = []

    def which(cmd: str) -> str | None:
        which_calls.append(cmd)
        return "/usr/bin/tesseract" if cmd == "tesseract" else None

    found = launcher.find_tesseract(
        {TESS_CMD_ENV: "/opt/t/tesseract"}, platform="linux", home=HOME, which=which, exists=_exists_in(set())
    )
    assert found is None
    assert which_calls == ["/opt/t/tesseract"]


def test_tesseract_env_found_by_which_or_exists() -> None:
    def which(cmd: str) -> str | None:
        return "/resolved/tess" if cmd == "tess" else None

    assert (
        launcher.find_tesseract({TESS_CMD_ENV: "tess"}, platform="linux", home=HOME, which=which, exists=_no_exists)
        == "/resolved/tess"
    )
    direct = Path("/opt") / ACCENTED / "tesseract"
    assert launcher.find_tesseract(
        {TESS_CMD_ENV: str(direct)}, platform="linux", home=HOME, which=_no_which, exists=_exists_in({direct})
    ) == str(direct)


def _no_exists(_: Path) -> bool:
    return False


def test_tesseract_from_path() -> None:
    def which(cmd: str) -> str | None:
        return "/usr/bin/tesseract" if cmd == "tesseract" else None

    assert launcher.find_tesseract({}, platform="linux", home=HOME, which=which, exists=_no_exists) == (
        "/usr/bin/tesseract"
    )


def test_tesseract_windows_install_dirs_in_order() -> None:
    environ = {"ProgramFiles": "/pf", "LOCALAPPDATA": "/lad"}
    program_files = Path("/pf") / "Tesseract-OCR" / "tesseract.exe"
    user_install = Path("/lad") / "Programs" / "Tesseract-OCR" / "tesseract.exe"
    both = _exists_in({program_files, user_install})
    assert launcher.find_tesseract(environ, platform="win32", home=HOME, which=_no_which, exists=both) == str(
        program_files
    )
    only_user = _exists_in({user_install})
    assert launcher.find_tesseract(environ, platform="win32", home=HOME, which=_no_which, exists=only_user) == str(
        user_install
    )
    no_pf = {"LOCALAPPDATA": "/lad"}
    assert launcher.find_tesseract(no_pf, platform="win32", home=HOME, which=_no_which, exists=only_user) == str(
        user_install
    )
    assert launcher.find_tesseract(environ, platform="win32", home=HOME, which=_no_which, exists=_no_exists) is None


def test_tesseract_unix_has_no_install_dir_fallback() -> None:
    everything = launcher.find_tesseract(
        {"ProgramFiles": "/pf"}, platform="linux", home=HOME, which=_no_which, exists=lambda _: True
    )
    assert everything is None


def test_tessdata_env_is_kept_and_checked_for_vie() -> None:
    tessdata = Path("/td") / ACCENTED
    with_vie = launcher.find_tessdata(
        {TESSDATA_ENV: str(tessdata)},
        data_dir=Path("/d/data"),
        data_dir_is_default=True,
        tesseract_cmd=None,
        exists=_exists_in({tessdata / "vie.traineddata"}),
    )
    assert with_vie == (tessdata, True)
    without_vie = launcher.find_tessdata(
        {TESSDATA_ENV: str(tessdata)},
        data_dir=Path("/d/data"),
        data_dir_is_default=True,
        tesseract_cmd=None,
        exists=_exists_in({Path("/d/tessdata/vie.traineddata")}),
    )
    assert without_vie == (tessdata, False)


def test_tessdata_beside_default_data_dir_then_beside_binary() -> None:
    data_dir = Path("/lad/Holocron/data")
    beside_data = Path("/lad/Holocron/tessdata")
    beside_binary = Path("/pf/Tesseract-OCR/tessdata")
    exists = _exists_in({beside_data / "vie.traineddata", beside_binary / "vie.traineddata"})
    kwargs = {"tesseract_cmd": "/pf/Tesseract-OCR/tesseract.exe", "exists": exists}
    assert launcher.find_tessdata({}, data_dir=data_dir, data_dir_is_default=True, **kwargs) == (beside_data, True)
    # --data-dir tuỳ ý: không dò `<data>/../tessdata` (có thể là gốc ổ đĩa)
    assert launcher.find_tessdata({}, data_dir=data_dir, data_dir_is_default=False, **kwargs) == (beside_binary, True)


def test_tessdata_missing_everywhere() -> None:
    result = launcher.find_tessdata(
        {}, data_dir=Path("/d/data"), data_dir_is_default=True, tesseract_cmd=None, exists=_no_exists
    )
    assert result == (None, None), "không dò thấy và không đặt biến: không xác định, không khẳng định thiếu"


# --- Dựng env -----------------------------------------------------------------------------------------------------


def _build(
    environ: dict[str, str], *, cli: str | None, root: Path, exists: Callable[[Path], bool]
) -> LaunchEnvironment:
    return launcher.build_launch_environment(
        environ, cli_data_dir=cli, zip_root=root, platform="win32", home=HOME, which=_no_which, exists=exists
    )


def test_build_environment_sets_everything_found(tmp_path: Path) -> None:
    root = tmp_path / ACCENTED
    lad = tmp_path / "Người dùng" / "AppData" / "Local"
    tess = lad / "Programs" / "Tesseract-OCR" / "tesseract.exe"
    tessdata = lad / "Holocron" / "tessdata"
    exists = _exists_in({root / "web" / "index.html", tess, tessdata / "vie.traineddata"})
    environ = {"LOCALAPPDATA": str(lad)}
    launch = _build(environ, cli=None, root=root, exists=exists)
    assert launch.data_dir == lad / "Holocron" / "data" and launch.data_dir_is_default
    assert dict(launch.updates) == {
        DATA_ENV: str(lad / "Holocron" / "data"),
        WEB_DIR_ENV: str(root / "web"),
        TESS_CMD_ENV: str(tess),
        TESSDATA_ENV: str(tessdata),
    }
    assert environ == {"LOCALAPPDATA": str(lad)}, "build không được đổi environ"


def test_build_environment_never_overrides_existing_values(tmp_path: Path) -> None:
    root = tmp_path / "zip"
    environ = {
        DATA_ENV: "/keep-data",
        WEB_DIR_ENV: "/keep-web",
        TESS_CMD_ENV: "/keep-tess",
        TESSDATA_ENV: "/keep-tessdata",
    }
    exists = _exists_in({root / "web" / "index.html", Path("/keep-tess")})
    launch = _build(environ, cli=None, root=root, exists=exists)
    assert dict(launch.updates) == {}
    assert launch.data_dir == Path("/keep-data") and not launch.data_dir_is_default


def test_build_environment_cli_overrides_data_dir_only(tmp_path: Path) -> None:
    cli = tmp_path / ACCENTED
    launch = _build({DATA_ENV: "/old", WEB_DIR_ENV: "/keep-web"}, cli=str(cli), root=tmp_path, exists=_no_exists)
    assert dict(launch.updates) == {DATA_ENV: str(cli)}


def test_build_environment_without_web_or_tesseract(tmp_path: Path) -> None:
    launch = _build({"LOCALAPPDATA": "/lad"}, cli=None, root=tmp_path, exists=_no_exists)
    assert set(launch.updates) == {DATA_ENV}
    assert launch.web_dir is None and launch.tesseract_cmd is None and launch.tessdata_dir is None


def test_apply_environment_writes_only_updates() -> None:
    environ = {"KEEP": "1"}
    launcher.apply_environment(environ, {DATA_ENV: "/d"})
    assert environ == {"KEEP": "1", DATA_ENV: "/d"}


def _launch(tesseract: str | None, vie: bool | None) -> LaunchEnvironment:
    return LaunchEnvironment(
        data_dir=Path("/d"),
        data_dir_is_default=True,
        web_dir=None,
        tesseract_cmd=tesseract,
        tessdata_dir=None,
        vie_available=vie,
        updates={},
    )


def test_ocr_status_line_variants() -> None:
    ok = launcher.ocr_status_line(_launch("/t/tesseract", True))
    assert ok == "OCR: có Tesseract (/t/tesseract), có vie.traineddata."
    missing_vie = launcher.ocr_status_line(_launch("/t/tesseract", False))
    assert "thiếu vie.traineddata" in missing_vie and "HUONG-DAN-TESSERACT.txt" in missing_vie
    missing_all = launcher.ocr_status_line(_launch(None, False))
    assert missing_all.startswith("OCR: thiếu Tesseract, thiếu vie.traineddata.")


def test_ocr_status_line_unknown_vie_is_not_reported_missing() -> None:
    unknown = launcher.ocr_status_line(_launch("/usr/bin/tesseract", None))
    assert unknown == (
        "OCR: có Tesseract (/usr/bin/tesseract), không xác định được vie.traineddata "
        "(Tesseract sẽ dùng thư mục mặc định của nó). Nếu trang scan không đọc được, xem HUONG-DAN-TESSERACT.txt."
    )
    assert "thiếu vie" not in unknown
    no_engine = launcher.ocr_status_line(_launch(None, None))
    assert no_engine.startswith("OCR: thiếu Tesseract, không xác định được vie.traineddata")
    assert no_engine.endswith("Trang scan sẽ không đọc được; xem HUONG-DAN-TESSERACT.txt.")


def test_build_environment_reports_unknown_vie_without_tessdata_env(tmp_path: Path) -> None:
    def which(cmd: str) -> str | None:
        return "/usr/bin/tesseract" if cmd == "tesseract" else None

    launch = launcher.build_launch_environment(
        {}, cli_data_dir=None, zip_root=tmp_path, platform="linux", home=HOME, which=which, exists=_no_exists
    )
    assert launch.vie_available is None
    assert TESSDATA_ENV not in launch.updates


# --- Khoá và instance.json ----------------------------------------------------------------------------------------


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    path = tmp_path / ACCENTED
    path.mkdir()
    return path


def test_lock_is_exclusive_and_released(data_dir: Path) -> None:
    first = launcher.try_acquire_instance_lock(data_dir)
    assert first is not None
    assert launcher.try_acquire_instance_lock(data_dir) is None
    first.release()
    first.release()  # nhả hai lần không lỗi
    again = launcher.try_acquire_instance_lock(data_dir)
    assert again is not None
    again.release()


def test_lock_on_missing_dir_is_launcher_error(tmp_path: Path) -> None:
    with pytest.raises(LauncherError, match="Không ghi được vào thư mục dữ liệu"):
        launcher.try_acquire_instance_lock(tmp_path / "không-có")


def test_instance_info_roundtrip_is_atomic(data_dir: Path) -> None:
    launcher.write_instance_info(data_dir, InstanceInfo(port=51234, pid=4321))
    assert json.loads((data_dir / INSTANCE_FILENAME).read_text(encoding="utf-8")) == {"port": 51234, "pid": 4321}
    assert sorted(p.name for p in data_dir.iterdir()) == [INSTANCE_FILENAME], "không còn file tạm"
    assert launcher.read_instance_port(data_dir) == 51234
    launcher.remove_instance_info(data_dir)
    assert not (data_dir / INSTANCE_FILENAME).exists()
    launcher.remove_instance_info(data_dir)  # thiếu file vẫn không lỗi


@pytest.mark.parametrize(
    "content",
    ["", "{", "[]", '{"pid": 1}', '{"port": 0, "pid": 1}', '{"port": 70000, "pid": 1}', '{"port": "x", "pid": 1}'],
)
def test_broken_instance_file_has_no_port(data_dir: Path, content: str) -> None:
    (data_dir / INSTANCE_FILENAME).write_text(content, encoding="utf-8")
    assert launcher.read_instance_port(data_dir) is None


def test_missing_instance_file_has_no_port(data_dir: Path) -> None:
    assert launcher.read_instance_port(data_dir) is None


def test_instance_info_rejects_token_like_extra_fields() -> None:
    with pytest.raises(ValueError):
        InstanceInfo.model_validate({"port": 1, "pid": 1, "token": "x"})


def test_write_instance_info_failure_is_launcher_error(tmp_path: Path) -> None:
    with pytest.raises(LauncherError, match="Không ghi được"):
        launcher.write_instance_info(tmp_path / "không-có", InstanceInfo(port=1, pid=1))


def test_remove_instance_info_ignores_os_error(data_dir: Path) -> None:
    (data_dir / INSTANCE_FILENAME).mkdir()  # thư mục cùng tên: unlink lỗi, vẫn không ném
    launcher.remove_instance_info(data_dir)


def test_wait_for_instance_sees_late_port_while_lock_is_held(data_dir: Path) -> None:
    held = launcher.try_acquire_instance_lock(data_dir)
    assert held is not None
    clock = FakeClock()

    def sleep(seconds: float) -> None:
        clock.sleep(seconds)
        if len(clock.sleeps) == 3:
            launcher.write_instance_info(data_dir, InstanceInfo(port=40000, pid=7))

    try:
        assert launcher.wait_for_instance(data_dir, sleep=sleep, clock=clock.clock) == 40000
        assert clock.sleeps == [0.2, 0.2, 0.2]
    finally:
        held.release()


def test_wait_for_instance_gives_up_after_timeout(data_dir: Path) -> None:
    held = launcher.try_acquire_instance_lock(data_dir)
    assert held is not None
    clock = FakeClock()
    try:
        assert launcher.wait_for_instance(data_dir, sleep=clock.sleep, clock=clock.clock) is None
        assert 10.0 <= clock.now <= 10.0 + 0.2 + 1e-6
    finally:
        held.release()


def test_wait_for_instance_takes_lock_when_old_instance_exits(data_dir: Path) -> None:
    """Phiên cũ thoát trong lúc chờ (để lại cổng chết): lấy được khoá, không trả cổng cũ."""
    held = launcher.try_acquire_instance_lock(data_dir)
    assert held is not None
    clock = FakeClock()

    def sleep(seconds: float) -> None:
        clock.sleep(seconds)
        if len(clock.sleeps) == 2:
            held.release()
            launcher.write_instance_info(data_dir, InstanceInfo(port=1, pid=42))

    outcome = launcher.wait_for_instance(data_dir, sleep=sleep, clock=clock.clock)
    assert isinstance(outcome, launcher.InstanceLock)
    outcome.release()


def test_running_instance_opens_old_port(data_dir: Path) -> None:
    opened: list[str] = []
    lines: list[str] = []
    code = launcher.handle_running_instance(
        data_dir, 45678, no_browser=False, open_browser=opened.append, emit=lines.append
    )
    assert code == EXIT_OK
    assert opened == ["http://127.0.0.1:45678/"]
    assert "http://127.0.0.1:45678/" in lines[0]


@pytest.mark.parametrize("error", [OSError("không có trình duyệt"), webbrowser.Error("không có trình duyệt")])
def test_running_instance_browser_failure_prints_url(data_dir: Path, error: Exception) -> None:
    def broken(_: str) -> None:
        raise error

    lines: list[str] = []
    code = launcher.handle_running_instance(data_dir, 45678, no_browser=False, open_browser=broken, emit=lines.append)
    assert code == EXIT_OK
    assert lines[-1] == "Không mở được trình duyệt; hãy tự mở http://127.0.0.1:45678/"
    assert not any("Đã mở trình duyệt" in line for line in lines)


def test_running_instance_no_browser(data_dir: Path) -> None:
    opened: list[str] = []
    lines: list[str] = []
    code = launcher.handle_running_instance(
        data_dir, 45678, no_browser=True, open_browser=opened.append, emit=lines.append
    )
    assert (code, opened, len(lines)) == (EXIT_OK, [], 1)


def test_running_instance_without_port_exits_busy(data_dir: Path) -> None:
    opened: list[str] = []
    lines: list[str] = []
    code = launcher.handle_running_instance(
        data_dir, None, no_browser=False, open_browser=opened.append, emit=lines.append
    )
    assert code == EXIT_BUSY
    assert opened == []
    assert "Holocron đang khởi động hoặc bị treo" in lines[0]


# --- Cổng và socket -----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("text, expected", [("0", 0), ("8000", 8000), ("65535", 65535)])
def test_port_number_accepts_range(text: str, expected: int) -> None:
    assert launcher.port_number(text) == expected


@pytest.mark.parametrize("text", ["-1", "65536", "abc", "1.5"])
def test_port_number_rejects_out_of_range(text: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        launcher.port_number(text)


def test_parse_args_defaults_and_argparse_error(capsys: pytest.CaptureFixture[str]) -> None:
    args = launcher.parse_args([])
    assert (args.no_browser, args.data_dir, args.port) == (False, None, 0)
    args = launcher.parse_args(["--no-browser", "--data-dir", ACCENTED, "--port", "8123"])
    assert (args.no_browser, args.data_dir, args.port) == (True, ACCENTED, 8123)
    with pytest.raises(SystemExit) as exc:
        launcher.parse_args(["--port", "70000"])
    assert exc.value.code == 2
    assert "0–65535" in capsys.readouterr().err


def test_bind_listening_socket_random_port() -> None:
    with launcher.bind_listening_socket(0) as sock:
        host, port = sock.getsockname()
        assert host == "127.0.0.1" and port > 0
        if sys.platform == "win32":
            assert sock.getsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE) == 1
            assert sock.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR) == 0
        else:
            assert sock.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR) != 0
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            pass  # đã listen: kết nối được trước khi giao cho uvicorn


def test_bind_listening_socket_busy_port_is_launcher_error() -> None:
    with launcher.bind_listening_socket(0) as sock:
        port = sock.getsockname()[1]
        with pytest.raises(LauncherError, match=f"Không mở được cổng 127.0.0.1:{port}"):
            launcher.bind_listening_socket(port)


# --- Luồng thăm ---------------------------------------------------------------------------------------------------


def _closed_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_probe_http_false_when_nothing_listens() -> None:
    assert launcher.probe_http(_closed_port(), timeout=0.5) is False


@pytest.fixture
def raw_http_server() -> Iterator[tuple[int, list[bytes]]]:
    """Server một lượt trên loopback trả 403 (bất kỳ phản hồi HTTP nào cũng là sẵn sàng); ghi lại request."""
    requests: list[bytes] = []
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)

    def handle() -> None:
        conn, _ = server.accept()
        with conn:
            requests.append(conn.recv(4096))
            conn.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")

    thread = threading.Thread(target=handle, daemon=True)
    thread.start()
    yield int(server.getsockname()[1]), requests
    thread.join(timeout=5)
    server.close()


def test_probe_http_true_on_any_response(raw_http_server: tuple[int, list[bytes]]) -> None:
    port, requests = raw_http_server
    assert launcher.probe_http(port, timeout=5) is True
    request = requests[0].decode("ascii")
    assert request.startswith("GET /v1/runtime HTTP/1.1\r\n")
    assert f"Host: 127.0.0.1:{port}\r\n" in request


def test_probe_http_false_on_garbage_response() -> None:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)

    def handle() -> None:
        conn, _ = server.accept()
        with conn:
            conn.recv(4096)
            conn.sendall(b"khong-phai-http\r\n\r\n")

    thread = threading.Thread(target=handle, daemon=True)
    thread.start()
    try:
        assert launcher.probe_http(int(server.getsockname()[1]), timeout=5) is False
    finally:
        thread.join(timeout=5)
        server.close()


def _announce(
    *,
    stop: threading.Event,
    fetch: Callable[[int], bool],
    no_browser: bool = False,
    open_browser: Callable[[str], object] | None = None,
) -> tuple[list[str], list[str], FakeClock]:
    opened: list[str] = []
    lines: list[str] = []
    clock = FakeClock()
    launcher.announce_when_ready(
        5000,
        stop=stop,
        no_browser=no_browser,
        fetch=fetch,
        sleep=clock.sleep,
        clock=clock.clock,
        open_browser=open_browser or opened.append,
        emit=lines.append,
    )
    return opened, lines, clock


def test_ready_after_some_polls_opens_browser() -> None:
    answers = iter([False, False, True])
    opened, lines, clock = _announce(stop=threading.Event(), fetch=lambda _: next(answers))
    assert opened == ["http://127.0.0.1:5000/"]
    assert clock.sleeps == [0.2, 0.2]
    assert "đang mở trình duyệt" in lines[0]


def test_ready_with_no_browser_only_prints() -> None:
    opened, lines, _ = _announce(stop=threading.Event(), fetch=lambda _: True, no_browser=True)
    assert opened == []
    assert lines == ["Holocron đã sẵn sàng tại http://127.0.0.1:5000/"]


def test_ready_timeout_prints_url_and_does_not_open() -> None:
    opened, lines, clock = _announce(stop=threading.Event(), fetch=lambda _: False)
    assert opened == []
    assert 60.0 <= clock.now <= 60.0 + 0.2 + 1e-6
    assert "http://127.0.0.1:5000/" in lines[0] and "60 giây" in lines[0]


def test_stopped_server_never_opens_browser() -> None:
    stop = threading.Event()

    def fetch(_: int) -> bool:
        stop.set()  # serve() kết thúc trong lúc đang thăm
        return False

    opened, lines, _ = _announce(stop=stop, fetch=fetch)
    assert (opened, lines) == ([], [])


def test_stop_set_before_polling_skips_fetch() -> None:
    stop = threading.Event()
    stop.set()
    calls: list[int] = []

    def fetch(port: int) -> bool:
        calls.append(port)
        return True

    opened, lines, _ = _announce(stop=stop, fetch=fetch)
    assert (calls, opened, lines) == ([], [], [])


def test_browser_failure_prints_url() -> None:
    def broken(_: str) -> None:
        raise OSError("không có trình duyệt")

    _, lines, _ = _announce(stop=threading.Event(), fetch=lambda _: True, open_browser=broken)
    assert lines[-1] == "Không mở được trình duyệt; hãy tự mở http://127.0.0.1:5000/"


# --- Console, tín hiệu, serve -------------------------------------------------------------------------------------


def test_use_utf8_console_reconfigures_text_wrapper() -> None:
    stream = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    launcher.use_utf8_console(stream)
    assert stream.encoding == "utf-8"
    stream.write(ACCENTED)
    stream.flush()
    launcher.use_utf8_console(io.StringIO())  # không phải TextIOWrapper: bỏ qua, không lỗi


def test_shutdown_signals_raise_keyboard_interrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    installed: dict[int, Callable[[int, object], object]] = {}

    def fake_signal(signum: int, handler: Callable[[int, object], object]) -> None:
        installed[signum] = handler

    monkeypatch.setattr(launcher.signal, "signal", fake_signal)
    launcher.install_shutdown_signals()
    import signal as signal_module

    expected = {int(signal_module.SIGTERM)}
    if sys.platform == "win32":
        expected.add(int(signal_module.SIGBREAK))
    assert set(installed) == expected
    for handler in installed.values():
        with pytest.raises(KeyboardInterrupt):
            handler(0, None)


def test_serve_app_hands_listening_socket_to_uvicorn(monkeypatch: pytest.MonkeyPatch) -> None:
    import uvicorn

    from holocron_core.routers.api import app

    seen: list[tuple[object, list[socket.socket] | None]] = []

    def fake_run(self: uvicorn.Server, sockets: list[socket.socket] | None = None) -> None:
        seen.append((self.config.app, sockets))

    monkeypatch.setattr(uvicorn.Server, "run", fake_run)
    monkeypatch.setattr(launcher, "install_shutdown_signals", lambda: None)
    with launcher.bind_listening_socket(0) as sock:
        launcher.serve_app(sock)
        assert seen == [(app, [sock])]


def test_default_deps_use_real_environment() -> None:
    import os

    deps = LauncherDeps()
    assert deps.environ is os.environ
    assert deps.serve is launcher.serve_app and deps.fetch is launcher.probe_http
    assert deps.zip_root == launcher.zip_root_from_executable(sys.executable)
    assert deps.exists(Path(__file__)) is True


class FakePopen:
    """Thay `subprocess.Popen` để không khởi chạy gì thật; `fail=True` giả lập Job ngoài không cho breakaway."""

    calls: ClassVar[list[tuple[list[str], int]]] = []
    fail: ClassVar[bool] = False

    def __init__(self, args: list[str], **kwargs: object) -> None:
        if FakePopen.fail:
            raise PermissionError("breakaway bị từ chối")
        flags = kwargs.get("creationflags", 0)
        FakePopen.calls.append((args, flags if isinstance(flags, int) else -1))


@pytest.fixture
def fake_popen(monkeypatch: pytest.MonkeyPatch) -> type[FakePopen]:
    FakePopen.calls = []
    FakePopen.fail = False
    monkeypatch.setattr(launcher.subprocess, "Popen", FakePopen)
    return FakePopen


@pytest.mark.skipif(sys.platform == "win32", reason="Windows mở trình duyệt qua tiến trình phụ tách khỏi Job")
def test_default_browser_uses_webbrowser(monkeypatch: pytest.MonkeyPatch, fake_popen: type[FakePopen]) -> None:
    opened: list[str] = []
    monkeypatch.setattr(launcher.webbrowser, "open", opened.append)
    launcher.open_browser_default("http://127.0.0.1:1/")
    assert opened == ["http://127.0.0.1:1/"]
    assert fake_popen.calls == []


@pytest.mark.skipif(sys.platform != "win32", reason="Job Object chỉ có trên Windows")
def test_default_browser_breaks_away_from_job(monkeypatch: pytest.MonkeyPatch, fake_popen: type[FakePopen]) -> None:
    opened: list[str] = []
    monkeypatch.setattr(launcher.webbrowser, "open", opened.append)
    launcher.open_browser_default("http://127.0.0.1:1/")
    assert opened == []
    [(args, flags)] = fake_popen.calls
    assert args == [sys.executable, "-I", "-c", launcher.BROWSER_HELPER, "http://127.0.0.1:1/"]
    assert flags & 0x01000000, "CREATE_BREAKAWAY_FROM_JOB"


@pytest.mark.skipif(sys.platform != "win32", reason="Job Object chỉ có trên Windows")
def test_default_browser_falls_back_when_breakaway_denied(
    monkeypatch: pytest.MonkeyPatch, fake_popen: type[FakePopen]
) -> None:
    opened: list[str] = []
    monkeypatch.setattr(launcher.webbrowser, "open", opened.append)
    fake_popen.fail = True
    launcher.open_browser_default("http://127.0.0.1:1/")
    assert opened == ["http://127.0.0.1:1/"]


@pytest.mark.skipif(sys.platform == "win32", reason="Windows gán Job thật; kiểm ở các test Windows cuối file")
def test_unix_has_no_job_object() -> None:
    assert launcher.assign_to_kill_on_close_job() is None


# --- main ---------------------------------------------------------------------------------------------------------


class Recorder:
    """Ghi lại những gì `main` làm qua phụ thuộc giả."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.opened: list[str] = []
        self.browser_opened = threading.Event()

    def emit(self, line: str) -> None:
        self.lines.append(line)

    def open_browser(self, url: str) -> None:
        self.opened.append(url)
        self.browser_opened.set()


def _deps(tmp_path: Path, recorder: Recorder, serve: Callable[[socket.socket], None]) -> LauncherDeps:
    clock = FakeClock()
    return LauncherDeps(
        environ={"LOCALAPPDATA": str(tmp_path / "lad")},
        which=_no_which,
        exists=_no_exists,
        zip_root=tmp_path / "zip",
        platform="win32",
        home=tmp_path,
        emit=recorder.emit,
        fetch=lambda _: True,
        clock=clock.clock,
        sleep=clock.sleep,
        open_browser=recorder.open_browser,
        serve=serve,
        assign_job=lambda: 1,
        pid=1234,
    )


def test_main_serves_then_cleans_up(tmp_path: Path) -> None:
    data_dir = tmp_path / ACCENTED / "data"
    recorder = Recorder()
    during: dict[str, object] = {}

    def serve(sock: socket.socket) -> None:
        port = int(sock.getsockname()[1])
        during["port"] = port
        during["instance"] = json.loads((data_dir / INSTANCE_FILENAME).read_text(encoding="utf-8"))
        during["second_lock"] = launcher.try_acquire_instance_lock(data_dir)
        assert recorder.browser_opened.wait(5), "luồng thăm phải mở trình duyệt khi server phản hồi"

    deps = _deps(tmp_path, recorder, serve)
    assert launcher.main(["--data-dir", str(data_dir)], deps) == EXIT_OK
    port = during["port"]
    assert during["instance"] == {"port": port, "pid": 1234}
    assert during["second_lock"] is None, "đang serve thì khoá phải bị giữ"
    assert deps.environ[DATA_ENV] == str(data_dir)
    assert recorder.opened == [f"http://127.0.0.1:{port}/"]
    assert recorder.lines[:4] == [
        f"Holocron: http://127.0.0.1:{port}/",
        f"Thư mục dữ liệu: {data_dir}",
        (
            "OCR: thiếu Tesseract, không xác định được vie.traineddata (Tesseract sẽ dùng thư mục mặc định của nó). "
            "Trang scan sẽ không đọc được; xem HUONG-DAN-TESSERACT.txt."
        ),
        "Đóng cửa sổ này hoặc nhấn Ctrl+C để tắt Holocron.",
    ]
    assert not (data_dir / INSTANCE_FILENAME).exists()
    lock = launcher.try_acquire_instance_lock(data_dir)
    assert lock is not None, "thoát xong phải nhả khoá"
    lock.release()


def test_main_keyboard_interrupt_is_clean_exit(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    recorder = Recorder()

    def serve(_: socket.socket) -> None:
        raise KeyboardInterrupt

    assert launcher.main(["--no-browser", "--data-dir", str(data_dir)], _deps(tmp_path, recorder, serve)) == EXIT_OK
    assert not (data_dir / INSTANCE_FILENAME).exists()
    assert recorder.opened == []


def test_main_serve_crash_still_cleans_up(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"

    def serve(_: socket.socket) -> None:
        raise RuntimeError("lifespan lỗi")

    with pytest.raises(RuntimeError):
        launcher.main(["--data-dir", str(data_dir)], _deps(tmp_path, Recorder(), serve))
    assert not (data_dir / INSTANCE_FILENAME).exists()
    lock = launcher.try_acquire_instance_lock(data_dir)
    assert lock is not None
    lock.release()


def _never_serve(_: socket.socket) -> None:
    raise AssertionError("không được serve")


def test_main_second_instance_opens_existing_port(tmp_path: Path) -> None:
    data_dir = tmp_path / ACCENTED
    data_dir.mkdir()
    held = launcher.try_acquire_instance_lock(data_dir)
    assert held is not None
    try:
        launcher.write_instance_info(data_dir, InstanceInfo(port=43210, pid=99))
        recorder = Recorder()
        assert launcher.main(["--data-dir", str(data_dir)], _deps(tmp_path, recorder, _never_serve)) == EXIT_OK
        assert recorder.opened == ["http://127.0.0.1:43210/"]
        assert "43210" in recorder.lines[0]
        assert launcher.read_instance_port(data_dir) == 43210, "lần hai không được xoá instance.json của lần một"
    finally:
        held.release()


def test_main_second_instance_without_port_exits_busy(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    held = launcher.try_acquire_instance_lock(data_dir)
    assert held is not None
    try:
        recorder = Recorder()
        assert launcher.main(["--data-dir", str(data_dir)], _deps(tmp_path, recorder, _never_serve)) == EXIT_BUSY
        assert recorder.opened == []
    finally:
        held.release()


def test_main_bad_data_dir_exits_2(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    recorder = Recorder()
    code = launcher.main(["--data-dir", str(blocker / "data")], _deps(tmp_path, recorder, _never_serve))
    assert code == EXIT_ERROR
    assert recorder.lines[0].startswith("Lỗi: Không tạo được thư mục dữ liệu")


def test_main_busy_port_exits_2_and_releases_lock(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    recorder = Recorder()
    with launcher.bind_listening_socket(0) as taken:
        port = taken.getsockname()[1]
        deps = _deps(tmp_path, recorder, _never_serve)
        assert launcher.main(["--data-dir", str(data_dir), "--port", str(port)], deps) == EXIT_ERROR
    assert recorder.lines[0].startswith(f"Lỗi: Không mở được cổng 127.0.0.1:{port}")
    assert not (data_dir / INSTANCE_FILENAME).exists()
    lock = launcher.try_acquire_instance_lock(data_dir)
    assert lock is not None
    lock.release()


def test_main_warns_when_job_assignment_fails(tmp_path: Path) -> None:
    recorder = Recorder()
    deps = _deps(tmp_path, recorder, lambda _: None)
    deps.assign_job = lambda: None
    assert launcher.main(["--no-browser", "--data-dir", str(tmp_path / "data")], deps) == EXIT_OK
    assert recorder.lines[0] == launcher.JOB_WARNING
    assert recorder.lines[1].startswith("Holocron: http://127.0.0.1:")


def test_main_no_job_warning_outside_windows(tmp_path: Path) -> None:
    recorder = Recorder()
    deps = _deps(tmp_path, recorder, lambda _: None)
    deps.assign_job = lambda: None
    deps.platform = "linux"
    assert launcher.main(["--no-browser", "--data-dir", str(tmp_path / "data")], deps) == EXIT_OK
    assert launcher.JOB_WARNING not in recorder.lines


# --- Job Object thật (Windows) ------------------------------------------------------------------------------------
# Chạy trong tiến trình con đã gán Job như launcher; KHÔNG đưa chính tiến trình pytest vào Job KILL_ON_JOB_CLOSE.

BACKEND_DIR = Path(__file__).resolve().parent.parent
windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Job Object chỉ có trên Windows")

IN_JOB_PRELUDE = (
    "import sys\n"
    "from holocron_core.launcher import assign_to_kill_on_close_job\n"
    "JOB = assign_to_kill_on_close_job()\n"
    "if JOB is None:\n"
    "    sys.exit('khong gan duoc Job')\n"
)


def _child_env() -> dict[str, str]:
    import os

    return dict(os.environ, PYTHONPATH=str(BACKEND_DIR), PYTHONIOENCODING="utf-8")


def _run_in_launcher_job(body: str, *args: str, timeout: float = 300) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", IN_JOB_PRELUDE + body, *args],
        cwd=BACKEND_DIR,
        env=_child_env(),
        capture_output=True,
        encoding="utf-8",
        timeout=timeout,
        check=False,
    )


@windows_only
def test_sandbox_test_suites_pass_inside_launcher_job() -> None:
    """test_sandbox_limits.py và test_sandbox_security.py (bom RAM, vòng lặp vô hạn…) vẫn xanh dưới Job của launcher."""
    body = (
        "import pytest\n"
        "sys.exit(pytest.main(['-q', '-p', 'no:cacheprovider', "
        "'tests/test_sandbox_limits.py', 'tests/test_sandbox_security.py']))\n"
    )
    result = _run_in_launcher_job(body)
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
    assert " passed" in result.stdout


SANDBOX_MEMORY_PROBE = """
from holocron_core.sandbox.sandbox import SandboxLimits, run_template
TAIL = (
    "\\ndef validate(p):\\n    assert True\\n"
    "def solve(p):\\n    return {'value': 1, 'display': '1', 'explanation_vi': 'x'}\\n"
    "def make_distractors(p, s):\\n    return [{'misconception_code': 'A_B_C', 'value': i, 'display': str(i + 2),"
    " 'rationale_vi': 'r'} for i in range(3)]\\n"
    "def render_stem(p):\\n    return 'stem'\\n"
)
# ~200 MB: vượt 128 MB nhưng thừa chỗ dưới 512 MB, nên chỉ giới hạn RAM của Job lồng mới làm nó hỏng
src = "def generate_params(seed):\\n    x = [0] * (25 * 1000 * 1000)\\n    return {}\\n" + TAIL
small = run_template(src, seed=1, limits=SandboxLimits(mem_bytes=128 * 1024 * 1024))
large = run_template(src, seed=1, limits=SandboxLimits(mem_bytes=512 * 1024 * 1024))
print(small.status.value, large.status.value)
"""


@windows_only
def test_sandbox_memory_limit_still_discriminates_inside_launcher_job() -> None:
    result = _run_in_launcher_job(SANDBOX_MEMORY_PROBE)
    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.split() == ["memory_exceeded", "ok"]


NESTED_LIMITS_CHILD = r"""
import ctypes, importlib.util, sys, time
from ctypes import wintypes
spec = importlib.util.spec_from_file_location("os_limits", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.apply_limits(200 * 1024 * 1024, 1)

class Basic(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]
class Io(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in ("a", "b", "c", "d", "e", "f")]
class Extended(ctypes.Structure):
    _fields_ = [("Basic", Basic), ("Io", Io), ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcess", ctypes.c_size_t), ("PeakJob", ctypes.c_size_t)]
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.QueryInformationJobObject.argtypes = [
    wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]
info = Extended()
# handle NULL = Job gần nhất của chính tiến trình (Job lồng của os_limits, không phải Job của launcher)
kernel32.QueryInformationJobObject(None, 9, ctypes.byref(info), ctypes.sizeof(info), None)
print(info.Basic.LimitFlags, info.ProcessMemoryLimit, info.Basic.PerProcessUserTimeLimit, flush=True)
start = time.monotonic()
while time.monotonic() - start < 30:
    pass
print("survived", flush=True)
"""

NESTED_LIMITS_PARENT = """
import subprocess
result = subprocess.run([sys.executable, "-c", sys.argv[1], sys.argv[2]], capture_output=True, text=True, timeout=120)
print(result.returncode & 0xFFFFFFFF)
print(result.stdout.strip())
"""


@windows_only
def test_os_limits_nested_job_keeps_memory_and_cpu_limits() -> None:
    """Tiến trình con của launcher tự gán Job lồng qua `os_limits.apply_limits`: giới hạn đúng và CPU vẫn bị cắt."""
    os_limits = BACKEND_DIR / "holocron_core" / "sandbox" / "os_limits.py"
    result = _run_in_launcher_job(NESTED_LIMITS_PARENT, NESTED_LIMITS_CHILD, str(os_limits))
    assert result.returncode == 0, result.stderr[-2000:]
    exit_code, limits, *rest = result.stdout.strip().split("\n")
    flags, memory, cpu = (int(value) for value in limits.split())
    assert flags & 0x100 and flags & 0x2 and flags & 0x2000, "PROCESS_MEMORY | PROCESS_TIME | KILL_ON_JOB_CLOSE"
    assert memory == 200 * 1024 * 1024
    assert cpu == 10_000_000
    assert int(exit_code) == 0xC0000044, "bị giết vì vượt CPU (STATUS_QUOTA_EXCEEDED)"
    assert "survived" not in rest


KILL_ON_CLOSE_BODY = """
import subprocess, time
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
print(child.pid, flush=True)
time.sleep(120)
"""


@windows_only
def test_closing_launcher_kills_child_processes() -> None:
    """Launcher chết (như đóng cửa sổ console) ⇒ Job đóng ⇒ tiến trình con bị giết."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    synchronize_and_terminate = 0x00100000 | 0x0001

    launcher_like = subprocess.Popen(
        [sys.executable, "-c", IN_JOB_PRELUDE + KILL_ON_CLOSE_BODY],
        cwd=BACKEND_DIR,
        env=_child_env(),
        stdout=subprocess.PIPE,
        encoding="utf-8",
    )
    handle = None
    try:
        assert launcher_like.stdout is not None
        child_pid = int(launcher_like.stdout.readline())
        handle = kernel32.OpenProcess(synchronize_and_terminate, False, child_pid)
        assert handle, "không mở được tiến trình con"
        assert kernel32.WaitForSingleObject(handle, 0) != 0, "tiến trình con phải còn sống trước khi tắt launcher"
        launcher_like.kill()
        launcher_like.wait(timeout=10)
        assert kernel32.WaitForSingleObject(handle, 10_000) == 0, "Job KILL_ON_JOB_CLOSE phải giết tiến trình con"
    finally:
        if launcher_like.poll() is None:
            launcher_like.kill()
            launcher_like.wait(timeout=10)
        if handle:
            kernel32.TerminateProcess(handle, 1)
            kernel32.CloseHandle(handle)
        if launcher_like.stdout is not None:
            launcher_like.stdout.close()


BREAKAWAY_BODY = """
import ctypes, subprocess
from ctypes import wintypes
from holocron_core import launcher
captured = []
class Capture(subprocess.Popen):
    def __init__(self, args, **kwargs):
        super().__init__([sys.executable, "-c", "import time; time.sleep(5)"], **kwargs)
        captured.append(self)
subprocess.Popen = Capture
launcher.open_browser_default("http://127.0.0.1:1/")
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
inside = wintypes.BOOL()
kernel32.IsProcessInJob(wintypes.HANDLE(int(captured[0]._handle)), wintypes.HANDLE(JOB), ctypes.byref(inside))
captured[0].kill()
captured[0].wait()
print(int(inside.value))
"""


@windows_only
def test_browser_helper_runs_outside_launcher_job() -> None:
    """Trình duyệt mở qua tiến trình phụ tách khỏi Job, nên tắt Holocron không giết trình duyệt."""
    result = _run_in_launcher_job(BREAKAWAY_BODY)
    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.strip() == "0"


# --- instance.json cũ còn sót ------------------------------------------------------------------------------------


def test_stale_instance_file_is_removed_before_serving(tmp_path: Path) -> None:
    """Phiên trước bị Job giết (không chạy finally) để lại cổng chết: lần sau lấy khoá thì xoá rồi ghi cổng mới."""
    data_dir = tmp_path / ACCENTED
    data_dir.mkdir()
    launcher.write_instance_info(data_dir, InstanceInfo(port=1, pid=42))
    seen: list[dict[str, int]] = []

    def serve(sock: socket.socket) -> None:
        seen.append(json.loads((data_dir / INSTANCE_FILENAME).read_text(encoding="utf-8")))

    deps = _deps(tmp_path, Recorder(), serve)
    assert launcher.main(["--no-browser", "--data-dir", str(data_dir)], deps) == EXIT_OK
    assert seen[0]["pid"] == 1234 and seen[0]["port"] != 1


def test_stale_instance_file_is_removed_even_when_bind_fails(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    launcher.write_instance_info(data_dir, InstanceInfo(port=1, pid=42))
    with launcher.bind_listening_socket(0) as taken:
        port = taken.getsockname()[1]
        deps = _deps(tmp_path, Recorder(), _never_serve)
        assert launcher.main(["--data-dir", str(data_dir), "--port", str(port)], deps) == EXIT_ERROR
    assert not (data_dir / INSTANCE_FILENAME).exists(), "không để lại cổng chết cho lần mở sau"


def test_second_launch_runs_normally_when_first_exits_while_waiting(tmp_path: Path) -> None:
    """Lần mở thứ hai thấy khoá bị giữ; phiên đầu thoát trong lúc chờ ⇒ chạy như lần đầu, không báo treo."""
    data_dir = tmp_path / ACCENTED
    data_dir.mkdir()
    held = launcher.try_acquire_instance_lock(data_dir)
    assert held is not None
    recorder = Recorder()
    served: list[int] = []
    deps = _deps(tmp_path, recorder, lambda sock: served.append(int(sock.getsockname()[1])))
    clock = FakeClock()

    def sleep(seconds: float) -> None:
        clock.sleep(seconds)
        held.release()

    deps.sleep = sleep
    deps.clock = clock.clock
    assert launcher.main(["--no-browser", "--data-dir", str(data_dir)], deps) == EXIT_OK
    assert len(served) == 1
    assert not any("bị treo" in line for line in recorder.lines)
    assert recorder.lines[0] == f"Holocron: http://127.0.0.1:{served[0]}/"
