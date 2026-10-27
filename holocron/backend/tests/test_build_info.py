"""Thông tin build: `read_build_info` của launcher, `write_build_info`/`Mo-thu-muc-log.cmd` của
`scripts/build_portable.py` và bước kiểm build info/log của `scripts/smoke_portable.py` (git giả, không mạng)."""

from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import os
import subprocess
import sys
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType

import pytest

from holocron_core import launcher_logging
from holocron_core.launcher_logging import BUILD_INFO_FILENAME, BuildInfo, read_build_info

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
COMMIT = "0123456789abcdef0123456789abcdef01234567"
VALID = {"version": "1.0.0-rc.1", "commit": COMMIT, "dirty": False, "built_at": "2026-10-28T01:02:03Z"}


def _load(name: str) -> ModuleType:
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))  # smoke_portable import `build_portable` theo tên
    cached = sys.modules.get(name)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bp = _load("build_portable")
sp = _load("smoke_portable")


@pytest.fixture
def no_dist(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(name: str) -> str:
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(launcher_logging.importlib.metadata, "version", missing)


def _write(tmp_path: Path, content: str | bytes) -> Path:
    path = tmp_path / BUILD_INFO_FILENAME
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")
    return path


# --- read_build_info ----------------------------------------------------------------------------------------------


def test_read_build_info_valid(tmp_path: Path) -> None:
    info = read_build_info(_write(tmp_path, json.dumps(VALID)))
    assert info == BuildInfo(version="1.0.0-rc.1", commit=COMMIT, dirty=False, built_at="2026-10-28T01:02:03Z")
    assert launcher_logging.version_line(info) == "Holocron 1.0.0-rc.1 (0123456)"


@pytest.mark.usefixtures("no_dist")
@pytest.mark.parametrize(
    "content",
    [
        "{không phải json",
        b"\xff\xfe\x00",
        "[1, 2]",
        json.dumps({k: v for k, v in VALID.items() if k != "commit"}),
        json.dumps({k: v for k, v in VALID.items() if k != "dirty"}),
        json.dumps({**VALID, "version": 1}),
        json.dumps({**VALID, "commit": None}),
        json.dumps({**VALID, "dirty": "false"}),
        json.dumps({**VALID, "dirty": 0}),
        json.dumps({**VALID, "built_at": 20261028}),
        json.dumps({**VALID, "version": "\x00\x07"}),
    ],
    ids=[
        "json-loi",
        "khong-utf8",
        "khong-phai-object",
        "thieu-commit",
        "thieu-dirty",
        "version-so",
        "commit-null",
        "dirty-chuoi",
        "dirty-so",
        "built-at-so",
        "version-chi-ky-tu-dieu-khien",
    ],
)
def test_read_build_info_broken_falls_back(tmp_path: Path, content: str | bytes) -> None:
    assert read_build_info(_write(tmp_path, content)) == BuildInfo(version="dev", commit="unknown")


def test_read_build_info_missing_file_uses_installed_version(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(launcher_logging.importlib.metadata, "version", lambda _: "0.1.0a0\n")
    assert read_build_info(tmp_path / "khong-co.json") == BuildInfo(version="0.1.0a0", commit="unknown")


@pytest.mark.usefixtures("no_dist")
def test_read_build_info_without_dist_info_is_dev(tmp_path: Path) -> None:
    assert read_build_info(tmp_path / "khong-co.json") == BuildInfo(version="dev", commit="unknown")


@pytest.mark.usefixtures("no_dist")
def test_read_build_info_rejects_file_over_4_kb(tmp_path: Path) -> None:
    base = json.dumps(VALID)
    padded = base[:-1] + " " * (4097 - len(base)) + "}"
    assert len(padded.encode()) == 4097
    assert read_build_info(_write(tmp_path, padded)) == BuildInfo(version="dev", commit="unknown")
    exact = base[:-1] + " " * (4096 - len(base)) + "}"
    assert len(exact.encode()) == 4096
    assert read_build_info(_write(tmp_path, exact)).commit == COMMIT


def test_read_build_info_strips_control_chars_and_truncates(tmp_path: Path) -> None:
    content = {**VALID, "version": "1.0\x1b[31m\u202e\n.0", "commit": "c" * 200, "built_at": "2026\x00-10"}
    info = read_build_info(_write(tmp_path, json.dumps(content)))
    assert info.version == "1.0[31m.0"
    assert info.commit == "c" * 64
    assert info.built_at == "2026-10"


def test_read_build_info_default_path_is_next_to_package() -> None:
    import holocron_core

    assert launcher_logging.default_build_info_path() == Path(holocron_core.__file__).parents[1] / BUILD_INFO_FILENAME


def test_build_script_uses_same_file_name_as_launcher() -> None:
    assert bp.BUILD_INFO_NAME == BUILD_INFO_FILENAME


# --- build: write_build_info -------------------------------------------------------------------------------------


class FakeGit:
    def __init__(self, head: str = f"{COMMIT}\n", status: str = "", fail: Exception | None = None) -> None:
        self.head = head
        self.status = status
        self.fail = fail
        self.calls: list[tuple[list[str], Path | None]] = []

    def __call__(self, args: Sequence[str], cwd: Path | None) -> str:
        self.calls.append((list(args), cwd))
        if self.fail is not None:
            raise self.fail
        if list(args) == ["git", "rev-parse", "HEAD"]:
            return self.head
        if list(args) == ["git", "status", "--porcelain"]:
            return self.status
        raise AssertionError(f"lệnh không dự kiến: {args}")


def _now() -> datetime:
    return datetime(2026, 10, 28, 15, 4, 5, tzinfo=timezone(timedelta(hours=7)))


def test_write_build_info_writes_four_keys(tmp_path: Path) -> None:
    git = FakeGit()
    repo = tmp_path / "repo"
    app_dir = tmp_path / "stage" / "app"
    info = bp.write_build_info(app_dir, "1.0.0-rc.1", git, _now, repo=repo)
    written = json.loads((app_dir / BUILD_INFO_FILENAME).read_text(encoding="utf-8"))
    assert (
        written
        == info
        == {
            "version": "1.0.0-rc.1",
            "commit": COMMIT,
            "dirty": False,
            "built_at": "2026-10-28T08:04:05Z",
        }
    )
    assert all(cwd == repo for _, cwd in git.calls), "git chạy với cwd là repo chứa --app-src"
    assert read_build_info(app_dir / BUILD_INFO_FILENAME).commit == COMMIT, "launcher đọc được đúng file build ghi"


def test_write_build_info_dirty_when_status_not_empty(tmp_path: Path) -> None:
    info = bp.write_build_info(tmp_path, "1.0.0", FakeGit(status=" M README.md\n"), _now, repo=tmp_path)
    assert info["dirty"] is True
    info = bp.write_build_info(tmp_path, "1.0.0", FakeGit(status="\n"), _now, repo=tmp_path)
    assert info["dirty"] is False


@pytest.mark.parametrize("head", ["", "abc1234\n", COMMIT.upper(), f"{COMMIT}0", "fatal: not a git repository"])
def test_write_build_info_rejects_non_40_hex_commit(tmp_path: Path, head: str) -> None:
    with pytest.raises(bp.BuildError, match="40 hex"):
        bp.write_build_info(tmp_path, "1.0.0", FakeGit(head=head), _now, repo=tmp_path)
    assert not (tmp_path / BUILD_INFO_FILENAME).exists()


@pytest.mark.parametrize("error", [FileNotFoundError("git"), PermissionError("git")])
def test_write_build_info_git_missing_is_build_error(tmp_path: Path, error: OSError) -> None:
    with pytest.raises(bp.BuildError, match="git"):
        bp.write_build_info(tmp_path, "1.0.0", FakeGit(fail=error), _now, repo=tmp_path)


def test_write_build_info_git_failure_propagates_build_error(tmp_path: Path) -> None:
    with pytest.raises(bp.BuildError, match="mã 128"):
        bp.write_build_info(
            tmp_path, "1.0.0", FakeGit(fail=bp.BuildError("lệnh thất bại (mã 128)")), _now, repo=tmp_path
        )


def test_utc_now_is_aware_utc() -> None:
    assert bp.utc_now().tzinfo is UTC


# --- build: Mo-thu-muc-log.cmd và kiểm thành viên zip --------------------------------------------------------------


def test_open_log_cmd_is_ascii_crlf_and_opens_log_dir() -> None:
    data = bp.open_log_cmd_bytes()
    data.decode("ascii")
    assert data.endswith(b"\r\n")
    assert data.count(b"\n") == data.count(b"\r\n")
    text = data.decode("ascii")
    assert "%LOCALAPPDATA%\\Holocron\\logs" in text
    assert "mkdir" in text and "explorer" in text
    assert text.startswith("@echo off\r\n")


def test_extra_required_lists_log_cmd_and_build_info() -> None:
    assert bp.zip_extra_required(None) == ["Mo-thu-muc-log.cmd", "app/holocron_build.json"]
    geos = bp.GeosSource("3.14.1", "0" * 64)
    extra = bp.zip_extra_required(geos)
    assert extra[:2] == ["Mo-thu-muc-log.cmd", "app/holocron_build.json"]
    assert f"licenses/{geos.archive_name}" in extra and f"licenses/{bp.LGPL_NAME}" in extra


def test_zip_without_log_cmd_or_build_info_fails_check() -> None:
    root = "Holocron-1.0.0-win64"
    base = [
        "Holocron.cmd",
        "LICENSE",
        "README.txt",
        "HUONG-DAN-TESSERACT.txt",
        "THIRD_PARTY_NOTICES.txt",
        "python/python.exe",
        "python/Lib/site-packages/holocron.pth",
        "app/holocron_core/launcher.py",
        "web/index.html",
        "app/holocron_core/infrastructure/templates/rr.py",
        "app/holocron_core/infrastructure/storage/migrations/001.sql",
    ]
    names = [f"{root}/{item}" for item in base]
    with pytest.raises(bp.BuildError) as excinfo:
        bp.check_zip_members(names, root, bp.zip_extra_required(None))
    assert "thiếu Mo-thu-muc-log.cmd" in str(excinfo.value)
    assert "thiếu app/holocron_build.json" in str(excinfo.value)
    bp.check_zip_members(
        [*names, f"{root}/Mo-thu-muc-log.cmd", f"{root}/app/holocron_build.json"], root, bp.zip_extra_required(None)
    )


# --- smoke: env và kiểm build info/log -----------------------------------------------------------------------------


def test_smoke_env_redirects_state_and_passes_tesseract(tmp_path: Path) -> None:
    real = {"LOCALAPPDATA": "C:\\Users\\u\\AppData\\Local", "PATH": "x"}
    env = sp.smoke_env(real, tmp_path, "C:\\T\\tesseract.exe", platform="win32")
    assert env["LOCALAPPDATA"] == str(tmp_path)
    assert env["HOLOCRON_TESSERACT_CMD"] == "C:\\T\\tesseract.exe"
    assert env["PATH"] == "x" and "XDG_STATE_HOME" not in env
    assert real["LOCALAPPDATA"] == "C:\\Users\\u\\AppData\\Local", "không đổi env thật"
    linux = sp.smoke_env({"HOLOCRON_TESSERACT_CMD": "/opt/t"}, tmp_path, "/usr/bin/tesseract", platform="linux")
    assert linux["XDG_STATE_HOME"] == str(tmp_path)
    assert linux["HOLOCRON_TESSERACT_CMD"] == "/opt/t", "đã đặt sẵn thì giữ"
    assert "HOLOCRON_TESSERACT_CMD" not in sp.smoke_env({}, tmp_path, None, platform="win32")


def test_smoke_log_dir_matches_launcher(tmp_path: Path) -> None:
    for platform in ("win32", "linux"):
        env = sp.smoke_env({}, tmp_path, None, platform=platform)
        assert sp.smoke_log_dir(tmp_path, platform=platform) == launcher_logging.default_log_dir(
            env, platform, Path("/khong-dung")
        )


class FakeZipPython:
    """Runner giả cho `detect_tesseract`: ghi lại lệnh/env, trả kết quả dựng sẵn hoặc ném lỗi."""

    def __init__(self, code: int = 0, output: str = "", error: Exception | None = None) -> None:
        self.code = code
        self.output = output
        self.error = error
        self.calls: list[tuple[list[str], dict[str, str]]] = []

    def __call__(self, args: list[str], env: dict[str, str]) -> tuple[int, str]:
        self.calls.append((args, env))
        if self.error is not None:
            raise self.error
        return self.code, self.output


def test_detect_tesseract_runs_zip_python_with_real_env(tmp_path: Path) -> None:
    exe = "C:\\Program Files\\Tesseract-OCR\\tesseract.exe"
    result = json.dumps({"tesseract": exe})
    runner = FakeZipPython(output=f"rác trước\n{sp.TESSERACT_PREFIX}{result}\nwarning từ stderr\n")
    real = {"LOCALAPPDATA": "C:\\Users\\u\\AppData\\Local", "PATH": "x"}
    assert sp.detect_tesseract(tmp_path, real, runner) == exe
    ((args, env),) = runner.calls
    assert args[:4] == sp.zip_python(tmp_path), "dò bằng python -I của zip, không phải Python host"
    assert args[4] == "-c" and "holocron_core.launcher import find_tesseract" in args[5]
    assert env == real and env is not real


@pytest.mark.parametrize(
    "runner",
    [
        FakeZipPython(output=f'{"HOLOCRON_TESSERACT:"}{{"tesseract": null}}'),
        FakeZipPython(code=1, output="Traceback (most recent call last):\nModuleNotFoundError: holocron_core"),
        FakeZipPython(output="không có dòng kết quả"),
        FakeZipPython(output="HOLOCRON_TESSERACT:không phải json"),
        FakeZipPython(output='HOLOCRON_TESSERACT:{"tesseract": 5}'),
        FakeZipPython(error=FileNotFoundError("python.exe")),
        FakeZipPython(error=subprocess.TimeoutExpired(["python.exe"], 60)),
    ],
    ids=["khong-tim-thay", "ma-thoat-khac-0", "khong-co-dong", "json-hong", "sai-kieu", "khong-chay-duoc", "het-gio"],
)
def test_detect_tesseract_failure_is_skipped_without_traceback(
    tmp_path: Path, runner: FakeZipPython, capsys: pytest.CaptureFixture[str]
) -> None:
    assert sp.detect_tesseract(tmp_path, {}, runner) is None
    out = capsys.readouterr().out
    assert out.startswith("BỎ QUA  dò Tesseract")
    assert "Traceback" not in out


def test_detect_tesseract_code_runs_launcher_search(tmp_path: Path) -> None:
    """Đoạn mã chạy trong python của zip dùng đúng `find_tesseract` (chạy thật bằng Python của test, cwd ở backend)."""
    import holocron_core

    exe = tmp_path / "tesseract.exe"
    exe.write_bytes(b"")
    backend = Path(holocron_core.__file__).parents[1]

    def run(args: list[str], env: dict[str, str]) -> tuple[int, str]:
        assert args[4] == "-c"
        completed = subprocess.run(
            [sys.executable, "-c", args[5]],
            cwd=backend,
            env=env,
            capture_output=True,
            encoding="utf-8",
            check=False,
            timeout=120,
        )
        return completed.returncode, completed.stdout + completed.stderr

    env = {**os.environ, "HOLOCRON_TESSERACT_CMD": str(exe)}
    env.pop("PYTHONPATH", None)
    assert sp.detect_tesseract(tmp_path, env, run) == str(exe)


def _zip_root(tmp_path: Path, info: object) -> Path:
    root = tmp_path / "Holocron-1.0.0-win64"
    (root / "app").mkdir(parents=True)
    (root / "app" / BUILD_INFO_FILENAME).write_text(json.dumps(info), encoding="utf-8")
    return root


def _log(log_dir: Path, text: str) -> None:
    log_dir.mkdir(parents=True)
    (log_dir / "holocron.log").write_text(text, encoding="utf-8")


def _results(report: object) -> list[tuple[str, bool, str]]:
    assert isinstance(report, sp.Report)
    return list(report.results)


def test_smoke_build_info_passes_when_log_matches(tmp_path: Path) -> None:
    root = _zip_root(tmp_path, VALID)
    _log(tmp_path / "logs", "2026-10-28 08:00:00,000 INFO holocron_core.launcher: Holocron 1.0.0-rc.1 (0123456)\n")
    report = sp.Report()
    sp.check_build_info(report, root, tmp_path / "logs", COMMIT)
    assert report.passed and len(_results(report)) == 4


def test_smoke_build_info_wrong_expected_commit_fails(tmp_path: Path) -> None:
    root = _zip_root(tmp_path, VALID)
    _log(tmp_path / "logs", "INFO holocron_core.launcher: Holocron 1.0.0-rc.1 (0123456)\n")
    report = sp.Report()
    sp.check_build_info(report, root, tmp_path / "logs", "f" * 40)
    assert not report.passed
    failed = [name for name, ok, _ in _results(report) if not ok]
    assert failed == ["commit trong build info khớp --expect-commit"]


@pytest.mark.parametrize(
    "info",
    [
        {**VALID, "extra": 1},
        {k: v for k, v in VALID.items() if k != "built_at"},
        {**VALID, "commit": "abc1234"},
        {**VALID, "dirty": "no"},
        [1],
    ],
)
def test_smoke_build_info_invalid_json_fails(tmp_path: Path, info: object) -> None:
    report = sp.Report()
    sp.check_build_info(report, _zip_root(tmp_path, info), tmp_path / "logs")
    assert not report.passed


def test_smoke_build_info_missing_or_mismatched_log_fails(tmp_path: Path) -> None:
    root = _zip_root(tmp_path, VALID)
    report = sp.Report()
    sp.check_build_info(report, root, tmp_path / "logs")
    assert [ok for _, ok, _ in _results(report)] == [True, False]
    _log(tmp_path / "logs", "INFO holocron_core.launcher: Holocron 0.9.9 (unknown)\n")
    report = sp.Report()
    sp.check_build_info(report, root, tmp_path / "logs")
    assert [ok for _, ok, _ in _results(report)] == [True, True, False]
