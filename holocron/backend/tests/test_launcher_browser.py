"""Launcher mở Chrome/Brave ở chế độ app: tìm trình duyệt, dòng lệnh, Job Object, cờ `--browser`, nối vào `main`.

Không khởi chạy trình duyệt thật, không đọc registry thật: `read_registry`, `exists`, `popen` là giả.
"""

from __future__ import annotations

import socket
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path

import pytest

from holocron_core import launcher
from holocron_core.launcher import (
    CREATE_BREAKAWAY_FROM_JOB,
    EXIT_OK,
    InstanceInfo,
    LauncherDeps,
    app_url,
    find_browser,
    make_browser_opener,
    read_app_path,
)

HOME = Path("/home/nguoi-dung")
URL = app_url(45678)
CHROME_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"


class Env:
    """Môi trường Windows giả: `files` là tập file tồn tại, `registry` ánh xạ (hive, khoá) sang giá trị."""

    def __init__(self, tmp_path: Path) -> None:
        self.lad = tmp_path / "lad"
        self.pf = tmp_path / "pf"
        self.pf86 = tmp_path / "pf86"
        self.environ = {
            "LOCALAPPDATA": str(self.lad),
            "ProgramFiles": str(self.pf),
            "ProgramFiles(x86)": str(self.pf86),
        }
        self.files: set[Path] = set()
        self.registry: dict[tuple[str, str], str] = {}
        self.lines: list[str] = []
        self.fallback_urls: list[str] = []
        self.popen_calls: list[tuple[list[str], dict[str, object]]] = []
        self.popen_fail_flags: set[int] = set()

    def exists(self, path: Path) -> bool:
        return path in self.files

    def read_registry(self, hive: str, subkey: str) -> str | None:
        return self.registry.get((hive, subkey))

    def popen(self, args: list[str], **kwargs: object) -> object:
        flags = kwargs.get("creationflags")
        if isinstance(flags, int) and flags in self.popen_fail_flags:
            raise PermissionError("Job không cho breakaway")
        self.popen_calls.append((args, kwargs))
        return object()

    def chrome_pf(self) -> Path:
        return self.pf / "Google" / "Chrome" / "Application" / "chrome.exe"

    def brave_pf(self) -> Path:
        return self.pf / "BraveSoftware" / "Brave-Browser" / "Application" / "brave.exe"

    def opener(self, choice: str = "auto", platform: str = "win32") -> Callable[[str], object]:
        return make_browser_opener(
            choice,
            environ=self.environ,
            platform=platform,
            home=HOME,
            exists=self.exists,
            emit=self.lines.append,
            fallback=self.fallback_urls.append,
            read_registry=self.read_registry,
            popen=self.popen,
        )

    def find(self, name: str) -> str | None:
        return find_browser(name, self.environ, home=HOME, read_registry=self.read_registry, exists=self.exists)


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return Env(tmp_path)


def test_chrome_chosen_when_present(env: Env) -> None:
    env.files |= {env.chrome_pf(), env.brave_pf()}
    env.opener()(URL)
    assert env.popen_calls[0][0][0] == str(env.chrome_pf())
    assert env.fallback_urls == []


def test_brave_when_no_chrome(env: Env) -> None:
    env.files.add(env.brave_pf())
    env.opener()(URL)
    assert env.popen_calls[0][0][0] == str(env.brave_pf())


def test_hkcu_wins_over_hklm(env: Env, tmp_path: Path) -> None:
    user, machine = tmp_path / "u" / "chrome.exe", tmp_path / "m" / "chrome.exe"
    env.files |= {user, machine, env.chrome_pf()}
    env.registry[("HKCU", CHROME_KEY)] = str(user)
    env.registry[("HKLM", CHROME_KEY)] = str(machine)
    assert env.find("chrome") == str(user)


def test_registry_path_to_missing_file_is_skipped(env: Env, tmp_path: Path) -> None:
    env.registry[("HKCU", CHROME_KEY)] = str(tmp_path / "gone" / "chrome.exe")
    env.files.add(env.chrome_pf())
    assert env.find("chrome") == str(env.chrome_pf())


def test_registry_value_quotes_and_env_vars(env: Env) -> None:
    env.files.add(env.pf / "X" / "chrome.exe")
    env.registry[("HKLM", CHROME_KEY)] = '"%ProgramFiles%/X/chrome.exe"'
    assert env.find("chrome") == str(env.pf / "X" / "chrome.exe")


def test_registry_value_with_missing_env_var_is_skipped_without_error(env: Env) -> None:
    env.registry[("HKCU", CHROME_KEY)] = "%KHONGCO%/chrome.exe"
    assert env.find("chrome") is None


@pytest.mark.parametrize("root_key", ["ProgramFiles(x86)", "LOCALAPPDATA"])
def test_chrome_found_under_other_roots(env: Env, root_key: str) -> None:
    exe = Path(env.environ[root_key]) / "Google" / "Chrome" / "Application" / "chrome.exe"
    env.files.add(exe)
    assert env.find("chrome") == str(exe)


def test_missing_environment_variables_do_not_raise(env: Env) -> None:
    del env.environ["ProgramFiles"], env.environ["ProgramFiles(x86)"]
    assert env.find("brave") is None


def test_none_found_falls_back_silently_in_auto(env: Env) -> None:
    env.opener()(URL)
    assert env.fallback_urls == [URL]
    assert env.lines == []
    assert env.popen_calls == []


def test_default_choice_does_not_search(env: Env) -> None:
    env.files.add(env.chrome_pf())
    env.opener("default")(URL)
    assert env.fallback_urls == [URL]
    assert env.popen_calls == []


def test_explicit_brave_with_only_chrome_falls_back_with_message(env: Env) -> None:
    env.files.add(env.chrome_pf())
    env.opener("brave")(URL)
    assert env.fallback_urls == [URL]
    assert len(env.lines) == 1
    assert "Brave" in env.lines[0]
    assert env.popen_calls == []


def test_command_line_has_app_and_profile_and_no_token(env: Env) -> None:
    env.files.add(env.chrome_pf())
    env.opener()(URL)
    args, kwargs = env.popen_calls[0]
    profile = env.lad / "Holocron" / "browser-profile"
    assert args == [str(env.chrome_pf()), f"--app={URL}", f"--user-data-dir={profile}"]
    assert profile.is_dir()
    assert "token" not in " ".join(args).lower()
    assert kwargs["shell"] is False
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert kwargs["stdout"] is subprocess.DEVNULL
    assert kwargs["stderr"] is subprocess.DEVNULL
    assert kwargs["close_fds"] is True


def test_registry_relative_path_is_ignored(env: Env) -> None:
    env.files.add(Path("chrome.exe"))
    env.registry[("HKCU", CHROME_KEY)] = "chrome.exe"
    assert env.find("chrome") is None


def test_exists_raising_oserror_counts_as_missing(env: Env) -> None:
    env.files.add(env.brave_pf())
    chrome = env.chrome_pf()

    def exists(path: Path) -> bool:
        if path == chrome:
            raise PermissionError("không có quyền")
        return path in env.files

    found = find_browser("chrome", env.environ, home=HOME, read_registry=env.read_registry, exists=exists)
    assert found is None
    brave = find_browser("brave", env.environ, home=HOME, read_registry=env.read_registry, exists=exists)
    assert brave == str(env.brave_pf())


@pytest.mark.skipif(sys.platform != "win32", reason="registry chỉ có trên Windows")
def test_read_app_path_missing_key_is_none() -> None:
    assert read_app_path("HKCU", r"SOFTWARE\Holocron-khong-ton-tai\x.exe") is None
    assert read_app_path("HKLM", r"SOFTWARE\Holocron-khong-ton-tai\x.exe") is None


def test_never_uses_msedge(env: Env) -> None:
    edge = env.pf86 / "Microsoft" / "Edge" / "Application" / "msedge.exe"
    env.files.add(edge)
    env.registry[("HKLM", r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe")] = str(edge)
    env.opener()(URL)
    assert env.popen_calls == []
    assert env.fallback_urls == [URL]
    assert env.find("msedge") is None


def test_breakaway_flag_used_first(env: Env) -> None:
    env.files.add(env.chrome_pf())
    env.opener()(URL)
    assert env.popen_calls[0][1]["creationflags"] == CREATE_BREAKAWAY_FROM_JOB


def test_breakaway_refused_retries_without_flag_still_app_mode(env: Env) -> None:
    env.files.add(env.chrome_pf())
    env.popen_fail_flags = {CREATE_BREAKAWAY_FROM_JOB}
    env.opener()(URL)
    assert len(env.popen_calls) == 1
    args, kwargs = env.popen_calls[0]
    assert kwargs["creationflags"] == 0
    assert args[1] == f"--app={URL}"
    assert env.fallback_urls == []


def test_popen_failing_both_times_falls_back(env: Env) -> None:
    env.files.add(env.chrome_pf())
    env.popen_fail_flags = {CREATE_BREAKAWAY_FROM_JOB, 0}
    env.opener()(URL)
    assert env.fallback_urls == [URL]


def test_profile_dir_unwritable_falls_back(env: Env) -> None:
    env.files.add(env.chrome_pf())
    env.lad.parent.mkdir(parents=True, exist_ok=True)
    env.lad.write_text("là file, không phải thư mục", encoding="utf-8")
    env.opener()(URL)
    assert env.fallback_urls == [URL]
    assert env.popen_calls == []


@pytest.mark.parametrize("choice", ["auto", "chrome", "brave", "default"])
def test_non_windows_always_default(env: Env, choice: str) -> None:
    env.files |= {env.chrome_pf(), env.brave_pf()}
    env.opener(choice, platform="linux")(URL)
    assert env.fallback_urls == [URL]
    assert env.popen_calls == []


def test_unknown_browser_value_is_argparse_error(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        launcher.parse_args(["--browser", "edge"])
    assert exc.value.code == 2
    assert "--browser" in capsys.readouterr().err


def test_browser_flag_default_is_auto() -> None:
    assert launcher.parse_args([]).browser == "auto"
    assert launcher.parse_args(["--browser", "brave"]).browser == "brave"


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def _main_deps(tmp_path: Path, serve: Callable[[socket.socket], None]) -> LauncherDeps:
    clock = FakeClock()
    return LauncherDeps(
        environ={"LOCALAPPDATA": str(tmp_path / "lad")},
        which=lambda _: None,
        exists=lambda _: False,
        zip_root=tmp_path / "zip",
        platform="win32",
        home=tmp_path,
        emit=lambda _: None,
        fetch=lambda _: True,
        clock=clock.clock,
        sleep=clock.sleep,
        serve=serve,
        assign_job=lambda: 1,
        pid=1234,
    )


def _capture_opener(monkeypatch: pytest.MonkeyPatch, opened_event: threading.Event) -> tuple[list[str], list[str]]:
    """Thay `make_browser_opener`: ghi `choice` đã truyền và các URL được mở."""
    choices: list[str] = []
    opened: list[str] = []

    def fake_make(choice: str, **_: object) -> Callable[[str], object]:
        choices.append(choice)

        def open_url(url: str) -> None:
            opened.append(url)
            opened_event.set()

        return open_url

    monkeypatch.setattr(launcher, "make_browser_opener", fake_make)
    return choices, opened


def test_second_instance_also_uses_app_mode_opener(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    choices, opened = _capture_opener(monkeypatch, threading.Event())
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    held = launcher.try_acquire_instance_lock(data_dir)
    assert held is not None
    try:
        launcher.write_instance_info(data_dir, InstanceInfo(port=43210, pid=99))
        code = launcher.main(["--data-dir", str(data_dir), "--browser", "brave"], _main_deps(tmp_path, lambda _: None))
    finally:
        held.release()
    assert code == EXIT_OK
    assert choices == ["brave"]
    assert opened == ["http://127.0.0.1:43210/"]


def test_first_run_uses_app_mode_opener(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    opened_event = threading.Event()
    choices, opened = _capture_opener(monkeypatch, opened_event)

    def serve(_: socket.socket) -> None:
        assert opened_event.wait(timeout=5)

    code = launcher.main(["--data-dir", str(tmp_path / "data")], _main_deps(tmp_path, serve))
    assert code == EXIT_OK
    assert choices == ["auto"]
    assert len(opened) == 1
    assert opened[0].startswith("http://127.0.0.1:")


def test_injected_open_browser_is_not_replaced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    choices, _ = _capture_opener(monkeypatch, threading.Event())
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    held = launcher.try_acquire_instance_lock(data_dir)
    assert held is not None
    seen: list[str] = []
    try:
        launcher.write_instance_info(data_dir, InstanceInfo(port=43211, pid=99))
        deps = _main_deps(tmp_path, lambda _: None)
        deps.open_browser = seen.append
        launcher.main(["--data-dir", str(data_dir)], deps)
    finally:
        held.release()
    assert seen == ["http://127.0.0.1:43211/"]
    assert choices == []
