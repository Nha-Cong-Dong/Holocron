"""Kiểm tra version nhất quán giữa pyproject, package.json, FastAPI, CHANGELOG và (tuỳ chọn) tag.

Quy ước:
  * Tag và package.json dùng SemVer: `x.y.z` hoặc `x.y.z-alpha|beta|rc[.n]`.
  * pyproject.toml và `FastAPI(version=...)` dùng PEP 440 tương ứng:
    `-alpha[.n]` -> `aN`, `-beta[.n]` -> `bN`, `-rc[.n]` -> `rcN` (n mặc định 0).
  * Mục phát hành mới nhất của CHANGELOG (`## [x.y.z]`, bỏ qua `[Unreleased]`) phải khớp.

Cách dùng:
  python scripts/check_versions.py            # kiểm nhất quán trong repo
  python scripts/check_versions.py v0.1.0-alpha   # kiểm thêm tag
"""

from __future__ import annotations

import io
import json
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "holocron" / "backend" / "pyproject.toml"
PACKAGE_JSON = ROOT / "holocron" / "frontend" / "package.json"
API_PY = ROOT / "holocron" / "backend" / "holocron_core" / "routers" / "api.py"
CHANGELOG = ROOT / "CHANGELOG.md"

SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-(alpha|beta|rc)(?:\.(\d+))?)?$")
PRE_TAG = {"alpha": "a", "beta": "b", "rc": "rc"}
FASTAPI_VERSION = re.compile(r"FastAPI\([^)]*?version=\"([^\"]+)\"")
CHANGELOG_HEADING = re.compile(r"^## \[(?P<version>[^\]]+)\]", re.MULTILINE)


def semver_to_pep440(version: str) -> str:
    """Đổi SemVer sang PEP 440; ném ValueError nếu không phải SemVer hợp lệ của dự án."""
    match = SEMVER.match(version)
    if match is None:
        raise ValueError(f"không phải SemVer hợp lệ (x.y.z[-alpha|beta|rc[.n]]): {version!r}")
    major, minor, patch, pre, number = match.groups()
    base = f"{major}.{minor}.{patch}"
    return base if pre is None else f"{base}{PRE_TAG[pre]}{number or 0}"


def read_pyproject() -> str:
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def read_package_json() -> str:
    return str(json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))["version"])


def read_fastapi() -> str:
    match = FASTAPI_VERSION.search(API_PY.read_text(encoding="utf-8"))
    if match is None:
        raise ValueError(f"không tìm thấy FastAPI(version=...) trong {API_PY.relative_to(ROOT)}")
    return match.group(1)


def latest_changelog_release(text: str) -> str | None:
    for match in CHANGELOG_HEADING.finditer(text):
        if match.group("version").lower() != "unreleased":
            return match.group("version")
    return None


def check(tag: str | None) -> list[str]:
    errors: list[str] = []
    try:
        package_version = read_package_json()
        expected_py = semver_to_pep440(package_version)
    except (ValueError, KeyError, OSError) as exc:
        return [f"package.json: {exc}"]

    for name, reader in (("pyproject.toml", read_pyproject), ("FastAPI(version=...)", read_fastapi)):
        try:
            actual = reader()
        except (ValueError, KeyError, OSError) as exc:
            errors.append(f"{name}: {exc}")
            continue
        if actual != expected_py:
            errors.append(f"{name} = {actual!r}, cần {expected_py!r} (PEP 440 của package.json {package_version!r})")

    if not CHANGELOG.exists():
        errors.append("thiếu CHANGELOG.md")
    else:
        latest = latest_changelog_release(CHANGELOG.read_text(encoding="utf-8"))
        if latest is None:
            errors.append("CHANGELOG.md chưa có mục phát hành nào (## [x.y.z])")
        elif latest != package_version:
            errors.append(f"CHANGELOG mục mới nhất = {latest!r}, package.json = {package_version!r}")

    if tag is not None:
        tag_version = tag.removeprefix("v")
        if not tag.startswith("v"):
            errors.append(f"tag {tag!r} phải bắt đầu bằng 'v'")
        elif SEMVER.match(tag_version) is None:
            errors.append(f"tag {tag!r} không phải SemVer hợp lệ (vX.Y.Z[-alpha|beta|rc[.n]])")
        elif tag_version != package_version:
            errors.append(f"tag {tag!r} khác package.json {package_version!r}")
    return errors


def main(argv: list[str]) -> int:
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")  # console Windows mặc định cp1252, không in được tiếng Việt
    if isinstance(sys.stderr, io.TextIOWrapper):
        sys.stderr.reconfigure(encoding="utf-8")
    if len(argv) > 1:
        print(__doc__, file=sys.stderr)
        return 2
    errors = check(argv[0] if argv else None)
    for error in errors:
        print(f"LỖI version: {error}", file=sys.stderr)
    if not errors:
        print(f"version nhất quán: {read_package_json()}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
