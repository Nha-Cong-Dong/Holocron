"""Log ra file của launcher: thư mục log, handler file (gắn/gỡ/đóng), che bí mật, gắn lại sau `uvicorn.Config`, và
đường `main` (dòng version/log, phiên thứ hai không mở log, cảnh báo khi không ghi được)."""

from __future__ import annotations

import logging
import socket
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from holocron_core import launcher, launcher_logging
from holocron_core.launcher import EXIT_OK, InstanceInfo, LauncherDeps
from holocron_core.launcher_logging import (
    LOG_FILENAME,
    HolocronFileHandler,
    SecretFilter,
    default_log_dir,
    file_logging,
    reattach_file_handlers,
)

HOME = Path("/home/nguoi-dung")
API_KEY = "sk-test-0123456789abcdef"
TRACKED_LOGGERS = ("holocron_core", "holocron_core.launcher", "uvicorn", "uvicorn.error", "uvicorn.access")


@pytest.fixture(autouse=True)
def restore_logging() -> Iterator[None]:
    """Khôi phục handler, level, propagate của `holocron_core` và `uvicorn*` sau mỗi test."""
    saved = {
        name: (list(logger.handlers), logger.level, logger.propagate, logger.disabled)
        for name in TRACKED_LOGGERS
        for logger in (logging.getLogger(name),)
    }
    yield
    for name, (handlers, level, propagate, disabled) in saved.items():
        logger = logging.getLogger(name)
        for handler in list(logger.handlers):
            if handler not in handlers:
                logger.removeHandler(handler)
        for handler in handlers:
            if handler not in logger.handlers:
                logger.addHandler(handler)
        logger.setLevel(level)
        logger.propagate = propagate
        logger.disabled = disabled


def _file_handlers(name: str) -> list[logging.Handler]:
    return [h for h in logging.getLogger(name).handlers if isinstance(h, HolocronFileHandler)]


def _read_log(log_dir: Path) -> str:
    return (log_dir / LOG_FILENAME).read_text(encoding="utf-8")


# --- Thư mục log -------------------------------------------------------------------------------------------------


def test_default_log_dir_windows_uses_localappdata() -> None:
    assert default_log_dir({"LOCALAPPDATA": "/lad"}, "win32", HOME) == Path("/lad") / "Holocron" / "logs"


def test_default_log_dir_windows_without_localappdata_falls_back_to_home() -> None:
    expected = HOME / "AppData" / "Local" / "Holocron" / "logs"
    assert default_log_dir({}, "win32", HOME) == expected
    assert default_log_dir({"LOCALAPPDATA": "  "}, "win32", HOME) == expected


def test_default_log_dir_linux_uses_xdg_state_home() -> None:
    assert default_log_dir({"XDG_STATE_HOME": "/xdg"}, "linux", HOME) == Path("/xdg") / "holocron" / "logs"


def test_default_log_dir_without_xdg_uses_local_state() -> None:
    expected = HOME / ".local" / "state" / "holocron" / "logs"
    assert default_log_dir({}, "linux", HOME) == expected
    assert default_log_dir({"XDG_STATE_HOME": " ", "LOCALAPPDATA": "/lad"}, "darwin", HOME) == expected


# --- file_logging ------------------------------------------------------------------------------------------------


def test_file_logging_writes_header_first_then_detaches_and_closes(tmp_path: Path) -> None:
    log_dir = tmp_path / "Thư mục log ố" / "logs"
    app_logger = logging.getLogger("holocron_core")
    level_before = app_logger.level
    handlers_before = list(app_logger.handlers)
    with file_logging(log_dir, {}, ("Holocron 1.2.3 (abcdef0)", "Log: x")) as warning:
        assert warning is None
        assert app_logger.level == logging.INFO
        file_handlers = _file_handlers("holocron_core")
        assert len(file_handlers) == 1
        logging.getLogger("holocron_core.services.demo").info("thông tin %s", "ố")
        logging.getLogger("holocron_core.services.demo").debug("không ghi debug")
    lines = _read_log(log_dir).splitlines()
    assert lines[0].endswith("INFO holocron_core.launcher: Holocron 1.2.3 (abcdef0)")
    assert lines[1].endswith("INFO holocron_core.launcher: Log: x")
    assert lines[2].endswith("INFO holocron_core.services.demo: thông tin ố")
    assert len(lines) == 3
    assert app_logger.handlers == handlers_before
    assert app_logger.level == level_before
    handler = file_handlers[0]
    assert isinstance(handler, HolocronFileHandler)
    assert handler.stream is None, "handler phải được đóng"
    (log_dir / LOG_FILENAME).unlink()  # Windows: chỉ xoá được khi file đã đóng


def test_file_logging_rotates_with_expected_limits(tmp_path: Path) -> None:
    with file_logging(tmp_path, {}, ()):
        (handler,) = _file_handlers("holocron_core")
        assert isinstance(handler, HolocronFileHandler)
        assert handler.maxBytes == 5 * 1024 * 1024
        assert handler.backupCount == 5
        assert handler.encoding == "utf-8"
        assert handler.level == logging.INFO


def test_file_logging_console_still_prints_warnings(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with file_logging(tmp_path, {}, ()):
        logging.getLogger("holocron_core.services.demo").warning("cảnh báo ra console")
        logging.getLogger("holocron_core.services.demo").info("chỉ vào file")
    err = capsys.readouterr().err
    assert "cảnh báo ra console" in err
    assert "chỉ vào file" not in err
    assert "cảnh báo ra console" in _read_log(tmp_path)


def test_file_logging_unwritable_dir_returns_warning_without_attaching(tmp_path: Path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    app_logger = logging.getLogger("holocron_core")
    handlers_before = list(app_logger.handlers)
    level_before = app_logger.level
    with file_logging(blocker / "logs", {}, ("header",)) as warning:
        assert warning is not None
        assert warning.startswith("Cảnh báo: không ghi được log")
        assert "\n" not in warning
        assert app_logger.handlers == handlers_before
        assert app_logger.level == level_before


def test_file_logging_detaches_from_uvicorn_loggers_too(tmp_path: Path) -> None:
    with file_logging(tmp_path, {}, ()):
        reattach_file_handlers()
        assert _file_handlers("uvicorn") and _file_handlers("uvicorn.error")
    assert not _file_handlers("uvicorn")
    assert not _file_handlers("uvicorn.error")
    assert not _file_handlers("holocron_core")


# --- che bí mật --------------------------------------------------------------------------------------------------


def _record(message: str, *args: object) -> logging.LogRecord:
    return logging.LogRecord("holocron_core.x", logging.INFO, __file__, 1, message, args, None)


def _masked(secret_filter: SecretFilter, record: logging.LogRecord) -> logging.LogRecord:
    result = secret_filter.filter(record)
    assert isinstance(result, logging.LogRecord)
    return result


def test_secret_filter_matches_names_case_insensitively_and_masks_args() -> None:
    secret_filter = SecretFilter({"openai_api_key": API_KEY, "Github_Token": "ghp_abcdefgh123", "PATH": "/usr/bin"})
    record = _record("gọi với %s và %s, PATH=%s", API_KEY, "ghp_abcdefgh123", "/usr/bin")
    masked = _masked(secret_filter, record)
    assert masked.getMessage() == "gọi với *** và ***, PATH=/usr/bin"
    assert record.getMessage() == f"gọi với {API_KEY} và ghp_abcdefgh123, PATH=/usr/bin", "record gốc không đổi"


def test_secret_filter_ignores_empty_and_short_values() -> None:
    secret_filter = SecretFilter({"EMPTY_KEY": "", "SHORT_TOKEN": "abc1234", "BLANK_SECRET": "   x   "})
    assert secret_filter.filter(_record("abc1234 x")) is True
    assert launcher_logging.secret_values({"EMPTY_KEY": "", "SHORT_TOKEN": "abc1234"}) == ()


def test_secret_filter_masks_longer_nested_secret_first() -> None:
    inner = "abcdefgh12"
    outer = f"{inner}-ZZZZZZZZ"
    secret_filter = SecretFilter({"A_KEY": inner, "B_SECRET": outer})
    assert _masked(secret_filter, _record(f"x {outer} y {inner}")).getMessage() == "x *** y ***"


def test_secret_filter_masks_traceback_and_stack_info(tmp_path: Path) -> None:
    secrets = {"HOLOCRON_LLM_API_KEY": API_KEY}
    with file_logging(tmp_path, secrets, ()):
        logger = logging.getLogger("holocron_core.services.demo")
        try:
            raise RuntimeError(f"lỗi gọi API với khoá {API_KEY}")
        except RuntimeError:
            logger.exception("thất bại")
        logger.info("ngăn xếp", stack_info=True)
        logger.info("args %s", API_KEY)
    text = _read_log(tmp_path)
    assert API_KEY not in text
    assert "RuntimeError: lỗi gọi API với khoá ***" in text
    assert "Stack (most recent call last)" in text
    assert "args ***" in text


def test_secret_filter_masks_stack_info_value() -> None:
    record = _record("m")
    record.stack_info = f"Stack: {API_KEY}"
    assert _masked(SecretFilter({"KEY": API_KEY}), record).stack_info == "Stack: ***"


def test_secret_filter_masks_cached_exc_text() -> None:
    record = _record("m")
    record.exc_text = f"Traceback ... {API_KEY}"
    assert _masked(SecretFilter({"KEY": API_KEY}), record).exc_text == "Traceback ... ***"


def test_secret_filter_survives_bad_format_args() -> None:
    record = _record("thiếu %s %s", API_KEY)
    assert API_KEY not in _masked(SecretFilter({"KEY": API_KEY}), record).getMessage()


# --- uvicorn ------------------------------------------------------------------------------------------------------


def test_handler_survives_real_uvicorn_config(tmp_path: Path) -> None:
    import uvicorn

    with file_logging(tmp_path, {}, ()):
        uvicorn.Config("holocron_core.routers.api:app", log_level="warning", access_log=False)
        assert not _file_handlers("uvicorn.error"), "dictConfig của uvicorn xoá handler, cần gắn lại"
        reattach_file_handlers()
        reattach_file_handlers()
        assert len(_file_handlers("uvicorn.error")) == 1, "không gắn trùng"
        assert len(_file_handlers("uvicorn")) == 1
        logging.getLogger("uvicorn.error").warning("uvicorn cảnh báo")
        logging.getLogger("uvicorn.error").info("uvicorn info không ghi")
    text = _read_log(tmp_path)
    assert text.count("uvicorn cảnh báo") == 1, "uvicorn.error lan lên uvicorn nhưng chỉ ghi một lần"
    assert "uvicorn info không ghi" not in text


def test_serve_app_reattaches_after_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import uvicorn

    seen: list[int] = []

    def fake_run(self: uvicorn.Server, sockets: list[socket.socket] | None = None) -> None:
        seen.append(len(_file_handlers("uvicorn.error")))

    monkeypatch.setattr(uvicorn.Server, "run", fake_run)
    monkeypatch.setattr(launcher, "install_shutdown_signals", lambda: None)
    with file_logging(tmp_path, {}, ()), launcher.bind_listening_socket(0) as sock:
        launcher.serve_app(sock)
    assert seen == [1]


# --- main ---------------------------------------------------------------------------------------------------------


class Lines:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def emit(self, line: str) -> None:
        self.lines.append(line)


def _deps(tmp_path: Path, lines: Lines, serve: Callable[[socket.socket], None]) -> LauncherDeps:
    return LauncherDeps(
        environ={"LOCALAPPDATA": str(tmp_path / "lad"), "HOLOCRON_LLM_API_KEY": API_KEY},
        which=lambda _: None,
        exists=lambda _: False,
        zip_root=tmp_path / "zip",
        platform="win32",
        home=tmp_path,
        emit=lines.emit,
        fetch=lambda _: False,
        clock=lambda: 0.0,
        sleep=lambda _: None,
        open_browser=lambda _: None,
        serve=serve,
        assign_job=lambda: 1,
        pid=1234,
        build_info_path=tmp_path / "missing_build.json",
    )


def _log_dir(tmp_path: Path) -> Path:
    return tmp_path / "lad" / "Holocron" / "logs"


def test_main_emits_version_and_log_lines_after_first_four(tmp_path: Path) -> None:
    build = tmp_path / "build.json"
    build.write_text(
        '{"version": "0.9.0", "commit": "0123456789abcdef0123456789abcdef01234567", "dirty": false, '
        '"built_at": "2026-10-28T00:00:00Z"}',
        encoding="utf-8",
    )
    lines = Lines()

    def serve(_: socket.socket) -> None:
        logging.getLogger("holocron_core.services.demo").info("đang chạy với %s", API_KEY)

    deps = _deps(tmp_path, lines, serve)
    deps.build_info_path = build
    assert launcher.main(["--no-browser", "--data-dir", str(tmp_path / "data")], deps) == EXIT_OK
    log_path = _log_dir(tmp_path) / LOG_FILENAME
    assert lines.lines[0].startswith("Holocron: http://127.0.0.1:")
    assert lines.lines[3] == "Đóng cửa sổ này hoặc nhấn Ctrl+C để tắt Holocron."
    assert lines.lines[4] == "Holocron 0.9.0 (0123456)"
    assert lines.lines[5] == f"Log: {log_path}"
    text = log_path.read_text(encoding="utf-8")
    first, second = text.splitlines()[:2]
    assert first.endswith("Holocron 0.9.0 (0123456)")
    assert second.endswith(f"Log: {log_path}")
    assert "đang chạy với ***" in text and API_KEY not in text
    assert not _file_handlers("holocron_core"), "thoát xong phải gỡ handler"
    log_path.unlink()


def test_main_without_build_info_falls_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(launcher_logging, "installed_version", lambda: "0.1.0a0")
    lines = Lines()
    assert launcher.main(["--data-dir", str(tmp_path / "data")], _deps(tmp_path, lines, lambda _: None)) == EXIT_OK
    assert lines.lines[4] == "Holocron 0.1.0a0 (unknown)"


def test_main_unwritable_log_dir_warns_after_first_four_lines(tmp_path: Path) -> None:
    (tmp_path / "lad").write_text("không phải thư mục", encoding="utf-8")
    lines = Lines()
    served: list[bool] = []
    deps = _deps(tmp_path, lines, lambda _: served.append(True))
    assert launcher.main(["--no-browser", "--data-dir", str(tmp_path / "data")], deps) == EXIT_OK
    assert served == [True], "không ghi được log vẫn phải chạy"
    assert lines.lines[0].startswith("Holocron: http://127.0.0.1:")
    assert lines.lines[4].startswith("Holocron ")
    assert lines.lines[5].startswith("Cảnh báo: không ghi được log")


def test_main_logs_serve_exception_then_reraises(tmp_path: Path) -> None:
    def serve(_: socket.socket) -> None:
        raise RuntimeError(f"hỏng với {API_KEY}")

    deps = _deps(tmp_path, Lines(), serve)
    with pytest.raises(RuntimeError):
        launcher.main(["--no-browser", "--data-dir", str(tmp_path / "data")], deps)
    text = (_log_dir(tmp_path) / LOG_FILENAME).read_text(encoding="utf-8")
    assert "ERROR holocron_core.launcher: Holocron dừng vì lỗi không mong đợi" in text
    assert "RuntimeError: hỏng với ***" in text
    assert API_KEY not in text


def test_second_instance_does_not_open_log(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    held = launcher.try_acquire_instance_lock(data_dir)
    assert held is not None
    try:
        launcher.write_instance_info(data_dir, InstanceInfo(port=43210, pid=99))

        def never(_: socket.socket) -> None:
            raise AssertionError("không được serve")

        lines = Lines()
        assert launcher.main(["--no-browser", "--data-dir", str(data_dir)], _deps(tmp_path, lines, never)) == EXIT_OK
        assert not _log_dir(tmp_path).exists(), "phiên thứ hai chỉ chuyển hướng, không mở log"
        assert not any(line.startswith("Log: ") for line in lines.lines)
    finally:
        held.release()


def test_default_deps_read_build_info_next_to_package() -> None:
    import holocron_core

    expected = Path(holocron_core.__file__).parents[1] / "holocron_build.json"
    assert LauncherDeps().build_info_path == expected
    assert launcher_logging.default_build_info_path() == expected


def test_console_handler_writes_to_current_stderr(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with file_logging(tmp_path, {}, ()):
        consoles = [
            h
            for h in logging.getLogger("holocron_core").handlers
            if type(h) is logging.StreamHandler  # handler console, không phải handler file
        ]
        assert len(consoles) == 1
        assert consoles[0].stream is sys.stderr
        assert consoles[0].level == logging.WARNING
