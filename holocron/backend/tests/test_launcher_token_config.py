"""Launcher bản zip với `HOLOCRON_REQUIRE_TOKEN` hỏng (duyệt 2026-10-29, câu 6, phương án (3)).

Cấu hình token hỏng (kể cả toàn khoảng trắng) ⇒ launcher thoát mã khác 0 TRƯỚC khi bind socket, in URL hay mở trình
duyệt; thông điệp chỉ nêu tên biến, ra stderr và file log. uvicorn trả về mà server chưa từng khởi động xong cũng là
lỗi, không phải `EXIT_OK`.
"""

from __future__ import annotations

import socket
from collections.abc import Callable
from pathlib import Path

import pytest

from holocron_core import launcher
from holocron_core.infrastructure.storage import DATA_DIR_ENV
from holocron_core.launcher import EXIT_ERROR, EXIT_OK, INSTANCE_FILENAME, LauncherDeps
from holocron_core.launcher_logging import LOG_FILENAME, default_log_dir
from holocron_core.routers.access import TOKEN_ENV, InvalidTokenConfigError
from tests.test_launcher import Recorder, _deps, _never_serve

VALID_TOKEN = "tok-launcher-0123456789-must-never-leak"
BROKEN_TOKENS = [
    pytest.param(" ", id="space"),
    pytest.param("\t", id="tab"),
    pytest.param("\u00a0", id="nbsp"),
    pytest.param("\n", id="newline"),
    pytest.param("tök", id="non-ascii"),
]


class ErrorRecorder(Recorder):
    """`Recorder` cộng thêm các dòng launcher in ra stderr (`emit_error`)."""

    def __init__(self) -> None:
        super().__init__()
        self.errors: list[str] = []

    def emit_error(self, line: str) -> None:
        self.errors.append(line)


def _token_deps(
    tmp_path: Path, recorder: ErrorRecorder, token: str, serve: Callable[[socket.socket], bool | None]
) -> LauncherDeps:
    deps = _deps(tmp_path, recorder, _never_serve)
    deps.serve = serve
    deps.emit_error = recorder.emit_error
    deps.environ[TOKEN_ENV] = token
    return deps


def _log_text(deps: LauncherDeps) -> str:
    return (default_log_dir(deps.environ, deps.platform, deps.home) / LOG_FILENAME).read_text(encoding="utf-8")


def _assert_no_value(text: str, value: str) -> None:
    assert repr(value) not in text
    assert value.strip() == "" or value.strip() not in text
    assert "\u00a0" not in text


@pytest.mark.parametrize("value", BROKEN_TOKENS)
def test_broken_token_exits_with_error_before_binding_or_printing_the_url(tmp_path: Path, value: str) -> None:
    data_dir = tmp_path / "data"
    recorder = ErrorRecorder()
    deps = _token_deps(tmp_path, recorder, value, _never_serve)
    assert launcher.main(["--data-dir", str(data_dir)], deps) == EXIT_ERROR
    assert recorder.lines == [], "không in URL hay dòng nào ra stdout"
    assert recorder.opened == []
    assert recorder.errors == [f"Lỗi: {InvalidTokenConfigError()}"]
    assert TOKEN_ENV in recorder.errors[0]
    _assert_no_value(recorder.errors[0], value)
    log = _log_text(deps)
    assert f"ERROR holocron_core.launcher: Lỗi: {InvalidTokenConfigError()}" in log
    _assert_no_value(log, value)
    assert not (data_dir / INSTANCE_FILENAME).exists()
    lock = launcher.try_acquire_instance_lock(data_dir)
    assert lock is not None, "thoát xong phải nhả khoá"
    lock.release()


def test_broken_token_without_writable_log_still_exits_with_error(tmp_path: Path) -> None:
    recorder = ErrorRecorder()
    deps = _token_deps(tmp_path, recorder, " ", _never_serve)
    blocker = tmp_path / "lad"
    blocker.write_text("không phải thư mục", encoding="utf-8")
    assert launcher.main(["--data-dir", str(tmp_path / "data")], deps) == EXIT_ERROR
    assert recorder.errors[0] == f"Lỗi: {InvalidTokenConfigError()}"
    assert len(recorder.errors) == 2 and recorder.errors[1].startswith("Cảnh báo: không ghi được log")
    assert recorder.lines == []


@pytest.mark.parametrize("value", [pytest.param("", id="empty"), pytest.param(VALID_TOKEN, id="valid")])
def test_unset_or_valid_token_keeps_the_normal_path(tmp_path: Path, value: str) -> None:
    recorder = ErrorRecorder()
    served: list[int] = []

    def serve(sock: socket.socket) -> bool:
        served.append(int(sock.getsockname()[1]))
        return True

    deps = _token_deps(tmp_path, recorder, value, serve)
    assert launcher.main(["--no-browser", "--data-dir", str(tmp_path / "data")], deps) == EXIT_OK
    assert len(served) == 1
    assert recorder.lines[0] == f"Holocron: http://127.0.0.1:{served[0]}/"
    assert recorder.errors == []


def test_server_that_never_started_exits_with_error(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    recorder = ErrorRecorder()
    deps = _token_deps(tmp_path, recorder, "", lambda _: False)
    assert launcher.main(["--no-browser", "--data-dir", str(data_dir)], deps) == EXIT_ERROR
    message = f"Lỗi: {launcher.SERVER_NOT_STARTED_MESSAGE}"
    assert recorder.errors == [message], "lỗi ra stderr như lỗi token"
    assert all(not line.startswith("Lỗi:") for line in recorder.lines), "không in lỗi ra stdout"
    assert f"ERROR holocron_core.launcher: {message}" in _log_text(deps)
    assert not (data_dir / INSTANCE_FILENAME).exists()
    lock = launcher.try_acquire_instance_lock(data_dir)
    assert lock is not None
    lock.release()


def test_uvicorn_startup_exit_code_passes_through_and_cleans_up(tmp_path: Path) -> None:
    """uvicorn tự `sys.exit(3)` khi `lifespan` lỗi: mã 3 đi thẳng ra, `instance.json` bị xoá, khoá được nhả."""
    data_dir = tmp_path / "data"
    recorder = ErrorRecorder()
    seen_instance: list[bool] = []

    def serve(_: socket.socket) -> bool:
        seen_instance.append((data_dir / INSTANCE_FILENAME).exists())
        raise SystemExit(3)

    deps = _token_deps(tmp_path, recorder, "", serve)
    with pytest.raises(SystemExit) as caught:
        launcher.main(["--no-browser", "--data-dir", str(data_dir)], deps)
    assert caught.value.code == 3
    assert seen_instance == [True], "đang serve thì instance.json phải có"
    assert not (data_dir / INSTANCE_FILENAME).exists()
    lock = launcher.try_acquire_instance_lock(data_dir)
    assert lock is not None, "thoát xong phải nhả khoá"
    lock.release()


@pytest.mark.parametrize("started", [True, False])
def test_serve_app_reports_whether_uvicorn_started(monkeypatch: pytest.MonkeyPatch, started: bool) -> None:
    import uvicorn

    def fake_run(self: uvicorn.Server, sockets: list[socket.socket] | None = None) -> None:
        self.started = started

    monkeypatch.setattr(uvicorn.Server, "run", fake_run)
    monkeypatch.setattr(launcher, "install_shutdown_signals", lambda: None)
    with launcher.bind_listening_socket(0) as sock:
        assert launcher.serve_app(sock) is started


@pytest.mark.parametrize("value", [pytest.param(" ", id="space"), pytest.param("\u00a0", id="nbsp")])
def test_real_uvicorn_does_not_start_with_a_broken_token(
    monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str], value: str
) -> None:
    """uvicorn thật chạy `lifespan` thật: không khởi động; hoặc uvicorn tự thoát mã khác 0, hoặc `serve_app` trả False."""
    monkeypatch.delenv(DATA_DIR_ENV, raising=False)
    monkeypatch.setenv(TOKEN_ENV, value)
    monkeypatch.setattr(launcher, "install_shutdown_signals", lambda: None)
    with launcher.bind_listening_socket(0) as sock:
        try:
            started = launcher.serve_app(sock)
        except SystemExit as exc:
            assert exc.code not in (0, None)
        else:
            assert started is False
    output = capfd.readouterr()
    combined = output.out + output.err
    assert TOKEN_ENV in combined
    _assert_no_value(combined, value)
