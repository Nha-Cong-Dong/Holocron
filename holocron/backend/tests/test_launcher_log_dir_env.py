"""Launcher luôn đặt hoặc xoá `HOLOCRON_LOG_DIR` cho server: cờ không bao giờ nói sai về việc ghi log."""

from __future__ import annotations

import socket
from collections.abc import Callable
from pathlib import Path

from holocron_core import launcher
from holocron_core.launcher import EXIT_OK, LauncherDeps
from holocron_core.launcher_logging import LOG_DIR_ENV


def _deps(tmp_path: Path, serve: Callable[[socket.socket], None], extra_env: dict[str, str]) -> LauncherDeps:
    return LauncherDeps(
        environ={"LOCALAPPDATA": str(tmp_path / "lad"), **extra_env},
        which=lambda _: None,
        exists=lambda _: False,
        zip_root=tmp_path / "zip",
        platform="win32",
        home=tmp_path,
        emit=lambda _: None,
        fetch=lambda _: False,
        clock=lambda: 0.0,
        sleep=lambda _: None,
        open_browser=lambda _: None,
        serve=serve,
        assign_job=lambda: 1,
        pid=1234,
        build_info_path=tmp_path / "missing_build.json",
    )


def _run(tmp_path: Path, extra_env: dict[str, str]) -> str | None:
    seen: list[str | None] = []
    holder: list[LauncherDeps] = []

    def serve(_: socket.socket) -> None:
        seen.append(holder[0].environ.get(LOG_DIR_ENV))

    deps = _deps(tmp_path, serve, extra_env)
    holder.append(deps)
    assert launcher.main(["--no-browser", "--data-dir", str(tmp_path / "data")], deps) == EXIT_OK
    assert len(seen) == 1
    return seen[0]


def test_log_opened_sets_env_to_log_dir(tmp_path: Path) -> None:
    assert _run(tmp_path, {}) == str(tmp_path / "lad" / "Holocron" / "logs")


def test_log_opened_overrides_stale_shell_value(tmp_path: Path) -> None:
    assert _run(tmp_path, {LOG_DIR_ENV: "/tu/shell"}) == str(tmp_path / "lad" / "Holocron" / "logs")


def test_log_warning_removes_preexisting_env(tmp_path: Path) -> None:
    (tmp_path / "lad").mkdir()
    (tmp_path / "lad" / "Holocron").write_text("chặn", encoding="utf-8")  # tệp chặn thư mục log
    assert _run(tmp_path, {LOG_DIR_ENV: "/tu/shell"}) is None


def test_log_warning_without_preexisting_env_leaves_it_unset(tmp_path: Path) -> None:
    (tmp_path / "lad").mkdir()
    (tmp_path / "lad" / "Holocron").write_text("chặn", encoding="utf-8")
    assert _run(tmp_path, {}) is None
