"""Dựng bộ cài NSIS của bản desktop (vỏ Tauri + sidecar Python portable + giao diện tĩnh `out/`).

Cách dùng (gốc repo, Windows, bằng Python của `.venv` như `scripts/build_portable.py`):
  python holocron/desktop/build_desktop.py [--ocr full|lite] [--web-out <thư mục out>]
      Chạy `scripts/build_portable.py` (dựng thư mục portable ở `build/portable/stage/Holocron-<version>-win64`),
      `npm ci` trong `holocron/desktop`, rồi `tauri build` với thư mục portable làm `bundle.resources`.
  python holocron/desktop/build_desktop.py --portable-dir <thư mục portable đã dựng>
      Bỏ bước dựng portable, dùng thư mục có sẵn (phải có `python/python.exe`, `app/holocron_core/`, `web/index.html`).

Version của bộ cài lấy từ `holocron/frontend/package.json` (cùng nguồn với bản zip portable). Kết quả chép ra
`dist/Holocron-<version>-desktop-x64-setup.exe` kèm `.sha256`. Bộ cài CHƯA ký số.

Script chỉ dùng thư viện chuẩn và không in giá trị biến môi trường.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path

DESKTOP_DIR = Path(__file__).resolve().parent
ROOT = DESKTOP_DIR.parent.parent
SRC_TAURI = DESKTOP_DIR / "src-tauri"
BUILD_PORTABLE = ROOT / "scripts" / "build_portable.py"
STAGE_DIR = ROOT / "build" / "portable" / "stage"
OVERRIDE_DIR = ROOT / "build" / "desktop"
DIST_DIR = ROOT / "dist"
FRONTEND_PACKAGE = ROOT / "holocron" / "frontend" / "package.json"
BUNDLE_DIR = SRC_TAURI / "target" / "release" / "bundle" / "nsis"
PORTABLE_REQUIRED = (
    Path("python") / "python.exe",
    Path("app") / "holocron_core" / "launcher.py",
    Path("web") / "index.html",
)


class DesktopBuildError(Exception):
    """Lỗi dựng bộ cài người dùng sửa được; `main` in thông điệp và thoát mã 1."""


def read_version() -> str:
    """Version SemVer của `holocron/frontend/package.json`."""
    data = json.loads(FRONTEND_PACKAGE.read_text(encoding="utf-8"))
    version = data.get("version") if isinstance(data, dict) else None
    if not isinstance(version, str) or not version:
        raise DesktopBuildError(f"{FRONTEND_PACKAGE} thiếu trường version")
    return version


def check_portable(portable: Path) -> None:
    missing = [str(rel) for rel in PORTABLE_REQUIRED if not (portable / rel).is_file()]
    if missing:
        raise DesktopBuildError(f"thư mục portable {portable} thiếu: {', '.join(missing)}")


def run(args: Sequence[str], cwd: Path) -> None:
    print(f"[run] {' '.join(args)}  (cwd {cwd})", flush=True)
    completed = subprocess.run(list(args), cwd=cwd, check=False)
    if completed.returncode != 0:
        raise DesktopBuildError(f"lệnh thoát mã {completed.returncode}: {' '.join(args)}")


def build_portable(version: str, ocr: str, web_out: str | None) -> Path:
    args = [sys.executable, str(BUILD_PORTABLE), "--ocr", ocr]
    if web_out is not None:
        args += ["--web-out", web_out]
    run(args, ROOT)
    return STAGE_DIR / f"Holocron-{version}-win64"


def write_override(version: str, portable: Path) -> Path:
    """Cấu hình gộp vào `tauri.conf.json` lúc build: version và thư mục portable làm `bundle.resources`."""
    source = Path(os.path.relpath(portable, SRC_TAURI)).as_posix().rstrip("/") + "/"
    override = {"version": version, "bundle": {"resources": {source: "portable/"}}}
    OVERRIDE_DIR.mkdir(parents=True, exist_ok=True)
    path = OVERRIDE_DIR / "tauri.override.json"
    path.write_text(json.dumps(override, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def find_npm() -> str:
    npm = shutil.which("npm")
    if npm is None:
        raise DesktopBuildError("không tìm thấy npm trong PATH")
    return npm


def find_installer(version: str, built_after: float) -> Path:
    candidates = [
        path
        for path in BUNDLE_DIR.glob(f"*_{version}_x64-setup.exe")
        if path.is_file() and path.stat().st_mtime >= built_after
    ]
    if len(candidates) != 1:
        raise DesktopBuildError(f"không tìm thấy đúng một bộ cài mới trong {BUNDLE_DIR}: {candidates}")
    return candidates[0]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build(args: argparse.Namespace) -> int:
    if sys.platform != "win32":
        raise DesktopBuildError("bộ cài desktop hiện chỉ dựng trên Windows (WebView2, NSIS)")
    started = time.monotonic()
    version = read_version()
    if args.portable_dir is not None:
        portable = Path(args.portable_dir).resolve()
    else:
        portable = build_portable(version, args.ocr, args.web_out)
    check_portable(portable)
    print(f"[portable] {portable}", flush=True)

    npm = find_npm()
    if args.skip_npm_ci and (DESKTOP_DIR / "node_modules" / "@tauri-apps" / "cli").is_dir():
        print("[npm] bỏ qua npm ci (đã có node_modules)", flush=True)
    else:
        run([npm, "ci", "--no-audit", "--no-fund"], DESKTOP_DIR)

    override = write_override(version, portable)
    built_after = time.time() - 1
    run([npm, "exec", "--", "tauri", "build", "--config", str(override)], DESKTOP_DIR)

    installer = find_installer(version, built_after)
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    target = DIST_DIR / f"Holocron-{version}-desktop-x64-setup.exe"
    shutil.copyfile(installer, target)
    digest = sha256_file(target)
    target.with_name(target.name + ".sha256").write_text(f"{digest} *{target.name}\n", encoding="ascii")
    size_mb = target.stat().st_size / (1024 * 1024)
    elapsed = time.monotonic() - started
    print(f"Xong: {target} ({size_mb:.1f} MiB, {elapsed:.0f} s), SHA-256 {digest}. Bộ cài CHƯA ký số.")
    return 0


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Dựng bộ cài NSIS bản desktop (Tauri) của Holocron.")
    parser.add_argument("--ocr", choices=("lite", "full"), default="full", help="chuyển cho build_portable.py")
    parser.add_argument("--web-out", default=None, help="chuyển cho build_portable.py (out/ đã dựng sẵn)")
    parser.add_argument("--portable-dir", default=None, help="dùng thư mục portable đã dựng, bỏ build_portable.py")
    parser.add_argument("--skip-npm-ci", action="store_true", help="không chạy npm ci khi đã có @tauri-apps/cli")
    return parser.parse_args(list(argv))


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        return build(args)
    except DesktopBuildError as exc:
        print(f"LỖI build desktop: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
