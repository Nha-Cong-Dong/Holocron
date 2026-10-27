"""Dựng bản zip portable Windows MVP-1 (kế hoạch đóng gói §3, §5, §7).

Cách dùng (gốc repo, chạy bằng Python của `.venv` đã qua bộ test):
  python scripts/build_portable.py --lock
      Sinh `holocron/packaging/requirements-portable.txt` (base) và `requirements-portable-ocr.txt` (phần thêm
      của RapidOCR): resolve với constraint = `pip freeze` của interpreter đang chạy, ghim `==` + SHA-256 của
      đúng wheel win_amd64 cp314/abi3/py3-none-any. Cần mạng.
  python scripts/build_portable.py [--ocr lite|full] [--app-src holocron/backend] [--web-out <thư mục out>]
      Dựng `dist/Holocron-<version>-win64.zip` và `.sha256` cạnh nó. Cần mạng (Python embeddable, wheel).

`--ocr lite` (mặc định TẠM THỜI) bỏ RapidOCR; `--ocr full` dừng ở bước license chờ người dùng quyết Q4
(GEOS LGPL-2.1). Script chỉ dùng thư viện chuẩn; chạy được trên Windows và Linux (bước biên dịch `.pyc` chỉ chạy
trên Windows vì cần chạy `python.exe` của bản embeddable).
"""

from __future__ import annotations

import argparse
import email.parser
import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
import tomllib
import urllib.request
import zipfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from email.message import Message
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = ROOT / "holocron" / "backend"
FRONTEND_DIR = ROOT / "holocron" / "frontend"
PACKAGING_DIR = ROOT / "holocron" / "packaging"
PYPROJECT = BACKEND_DIR / "pyproject.toml"
LOCK_BASE = PACKAGING_DIR / "requirements-portable.txt"
LOCK_OCR = PACKAGING_DIR / "requirements-portable-ocr.txt"
BUILD_DIR = ROOT / "build" / "portable"
DIST_DIR = ROOT / "dist"

# Python embeddable (PSF). SHA-256 đối chiếu với trường `sha256_sum` của API phát hành python.org
# (https://www.python.org/api/v2/downloads/release_file/?release=1171), độc lập với file tải về.
PYTHON_VERSION = "3.14.10"
PYTHON_EMBED_NAME = f"python-{PYTHON_VERSION}-embed-amd64.zip"
PYTHON_EMBED_URL = f"https://www.python.org/ftp/python/{PYTHON_VERSION}/{PYTHON_EMBED_NAME}"
PYTHON_EMBED_SHA256 = "90905e64a840557d8c1bc4f5b74db3d7ee85e3ffb217611cac1c7813acea4159"
PTH_FILE = "python314._pth"

# Nền tảng đích cho `pip download`/`pip install --target` (cài chéo được từ Linux).
TARGET_PIP_ARGS = (
    "--platform",
    "win_amd64",
    "--implementation",
    "cp",
    "--python-version",
    "3.14",
    "--abi",
    "cp314",
    "--only-binary=:all:",
)

# Closure của `[project].dependencies` (pyproject.toml), đo từ metadata của `.venv` trên Windows. Lock chỉ được
# chứa các gói này; gói mới xuất hiện ⇒ dừng (thêm dependency cần người dùng duyệt, CLAUDE.md §2.2).
OCR_ROOTS = frozenset({"rapidocr", "onnxruntime", "opencv-python-headless"})
BASE_PACKAGES = frozenset(
    {
        "annotated-doc",
        "annotated-types",
        "anyio",
        "click",
        "fastapi",
        "h11",
        "idna",
        "opentelemetry-api",
        "pydantic",
        "pydantic-core",
        "pypdfium2",
        "python-multipart",
        "starlette",
        "typing-extensions",
        "typing-inspection",
        "uvicorn",
    }
)
OCR_PACKAGES = frozenset(
    {
        "certifi",
        "charset-normalizer",
        "colorama",
        "colorlog",
        "flatbuffers",
        "numpy",
        "omegaconf",
        "onnxruntime",
        "opencv-python-headless",
        "packaging",
        "pillow",
        "protobuf",
        "pyclipper",
        "pyyaml",
        "rapidocr",
        "requests",
        "shapely",
        "six",
        "tqdm",
        "urllib3",
    }
)

# Giấy phép cho phép (SPDX, so khớp không phân biệt hoa thường). `LicenseRef-BSD` là classifier
# "BSD License" chung chung của PyPI; CC-BY-4.0 là giấy phép chỉ đòi ghi công (dữ liệu caniuse-lite, PDFium).
PERMISSIVE_LICENSES = frozenset(
    {
        "0BSD",
        "APACHE-2.0",
        "BLUEOAK-1.0.0",
        "BSD-2-CLAUSE",
        "BSD-3-CLAUSE",
        "CC-BY-4.0",
        "CC0-1.0",
        "HPND",
        "ISC",
        "LICENSEREF-BSD",
        "MIT",
        "MIT-0",
        "MIT-CMU",
        "PSF-2.0",
        "PYTHON-2.0",
        "UNLICENSE",
        "ZLIB",
    }
)
# LGPL/MPL chỉ được phép với đúng gói đã duyệt (kế hoạch §5 bước 7). GEOS LGPL-2.1 chờ Q4, KHÔNG có ở đây.
WEAK_COPYLEFT_ALLOWED = frozenset({("certifi", "MPL-2.0"), ("tqdm", "MPL-2.0")})
# Metadata không phải biểu thức SPDX, đã đọc tay; ghim theo phiên bản để bản mới buộc xem lại.
# pypdfium2: `License: BSD-3-Clause, Apache-2.0, dependency licenses`; license của PDFium và thư viện nó nhúng
# (freetype chọn FTL, ICU, lcms, libjpeg-turbo, openjpeg, libpng, zlib, abseil…) nằm trong dist-info/licenses.
LICENSE_OVERRIDES: Mapping[tuple[str, str], str] = {
    ("pypdfium2", "5.14.0"): "BSD-3-Clause AND Apache-2.0 AND CC-BY-4.0",
}
LICENSE_ALIASES: Mapping[str, str] = {
    "3-clause bsd license": "BSD-3-Clause",
    "apache 2.0": "Apache-2.0",
    "apache license 2.0": "Apache-2.0",
    "apache software license": "Apache-2.0",
    "bsd 3-clause license": "BSD-3-Clause",
    "mit license": "MIT",
}
CLASSIFIER_LICENSES: Mapping[str, str] = {
    "License :: OSI Approved :: Apache Software License": "Apache-2.0",
    "License :: OSI Approved :: BSD License": "LicenseRef-BSD",
    "License :: OSI Approved :: ISC License (ISCL)": "ISC",
    "License :: OSI Approved :: MIT License": "MIT",
    "License :: OSI Approved :: Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0",
    "License :: OSI Approved :: Python Software Foundation License": "PSF-2.0",
    "License :: OSI Approved :: GNU General Public License v2 (GPLv2)": "GPL-2.0-only",
    "License :: OSI Approved :: GNU General Public License v3 (GPLv3)": "GPL-3.0-only",
    "License :: OSI Approved :: GNU Lesser General Public License v2 or later (LGPLv2+)": "LGPL-2.0-or-later",
    "License :: OSI Approved :: GNU Lesser General Public License v3 (LGPLv3)": "LGPL-3.0-only",
    "License :: OSI Approved :: GNU Affero General Public License v3": "AGPL-3.0-only",
}
CLASSIFIER_IGNORED = frozenset({"License :: OSI Approved"})

Q4_MESSAGE = "chờ Q4: GEOS LGPL-2.1 (shapely.libs/geos*.dll, kéo theo bởi rapidocr) chưa được người dùng duyệt"

HOLOCRON_CMD = (
    "@echo off",
    "setlocal",
    '"%~dp0python\\python.exe" -X utf8 -m holocron_core.launcher %*',
    "exit /b %ERRORLEVEL%",
)
HOLOCRON_PTH_LINES = ("..\\..\\..\\app", "..\\..\\..\\site-packages")

LOCK_HEADER = (
    "# Sinh bởi `python scripts/build_portable.py --lock`; KHÔNG sửa tay.",
    "# Mỗi dòng: tên==bản --hash=sha256:<wheel win_amd64 cp314/abi3/py3-none-any>; cài bằng --require-hashes.",
)
LOCK_LINE = re.compile(
    r"^(?P<name>[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)==(?P<version>[A-Za-z0-9.!+_-]+)"
    r" --hash=sha256:(?P<sha256>[0-9a-f]{64})$"
)
REQUIREMENT_NAME = re.compile(r"^\s*([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)")
FREEZE_LINE = re.compile(r"^([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)==(\S+)$")
ABI3_PYTHON = re.compile(r"^cp3(\d+)$")
LICENSE_FILE = re.compile(r"^(licen[cs]e|copying|notice|authors)", re.IGNORECASE)


class BuildError(Exception):
    """Lỗi dừng build; thông điệp in thẳng cho người chạy."""


Runner = Callable[[Sequence[str], Path | None], str]
Downloader = Callable[[str, Path], None]


def canonical_name(name: str) -> str:
    """Tên gói theo PEP 503."""
    return re.sub(r"[-_.]+", "-", name).lower()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def run_command(args: Sequence[str], cwd: Path | None = None) -> str:
    """Chạy lệnh, đọc output UTF-8 tường minh; mã thoát khác 0 ⇒ BuildError kèm đuôi output."""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    completed = subprocess.run(
        list(args),
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0:
        tail = "\n".join((completed.stdout + completed.stderr).strip().splitlines()[-20:])
        raise BuildError(f"lệnh thất bại (mã {completed.returncode}): {' '.join(args)}\n{tail}")
    return completed.stdout


def download_url(url: str, destination: Path) -> None:
    """Tải qua HTTPS vào file tạm rồi đổi tên (không để lại file dở)."""
    if not url.startswith("https://"):
        raise BuildError(f"chỉ tải qua HTTPS: {url}")
    partial = destination.with_name(destination.name + ".part")
    with urllib.request.urlopen(url, timeout=120) as response, partial.open("wb") as handle:
        shutil.copyfileobj(response, handle)
    partial.replace(destination)


# ---------------------------------------------------------------- Python embeddable


def ensure_cached_download(cache_dir: Path, name: str, url: str, sha256: str, download: Downloader) -> Path:
    """Trả file cache có đúng SHA-256 ghim. Kiểm lại MỖI lần; sai ⇒ xoá, tải lại một lần; vẫn sai ⇒ dừng."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / name
    if path.exists():
        if sha256_file(path) == sha256:
            return path
        print(f"Cache {name} sai SHA-256, xoá và tải lại.")
        path.unlink()
    print(f"Tải {url}")
    download(url, path)
    actual = sha256_file(path) if path.exists() else "<không có file>"
    if actual != sha256:
        path.unlink(missing_ok=True)
        raise BuildError(f"SHA-256 của {name} không khớp giá trị ghim: {actual} != {sha256}")
    return path


def safe_member_parts(name: str) -> tuple[str, ...]:
    """Thành phần đường dẫn an toàn của một mục zip; từ chối `..`, đường dẫn tuyệt đối, ký tự ổ đĩa."""
    normalized = name.replace("\\", "/")
    if normalized.startswith("/"):
        raise BuildError(f"mục zip có đường dẫn tuyệt đối: {name!r}")
    if ":" in normalized:
        raise BuildError(f"mục zip có ký tự ổ đĩa hoặc ':': {name!r}")
    parts = tuple(part for part in PurePosixPath(normalized).parts if part not in ("", "."))
    if ".." in parts:
        raise BuildError(f"mục zip có thành phần '..': {name!r}")
    if not parts:
        raise BuildError(f"mục zip rỗng: {name!r}")
    return parts


def safe_extract(archive: Path, destination: Path) -> list[Path]:
    """Giải nén zip không tin cậy vào `destination` (chống zip-slip); kiểm mọi mục TRƯỚC khi ghi file nào."""
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    written: list[Path] = []
    with zipfile.ZipFile(archive) as bundle:
        members = [(info, safe_member_parts(info.filename)) for info in bundle.infolist()]
        for info, parts in members:
            target = root.joinpath(*parts)
            if not target.resolve().is_relative_to(root):
                raise BuildError(f"mục zip trỏ ra ngoài thư mục đích: {info.filename!r}")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(info) as source, target.open("wb") as sink:
                shutil.copyfileobj(source, sink)
            written.append(target)
    return written


def enable_site_in_pth(text: str) -> str:
    """Bỏ `#` trước `import site` của `._pth`, giữ nguyên các dòng khác (không thêm đường dẫn nào).

    Thêm `app`/`site-packages` thẳng vào `._pth` sẽ lọt vào `sys.path` của cả tiến trình sandbox (`python -S`);
    vì vậy chúng đi qua `holocron.pth`, chỉ được xử lý khi `site` chạy (kế hoạch §3).
    """
    lines = text.splitlines()
    positions = [index for index, line in enumerate(lines) if line.strip() == "#import site"]
    if len(positions) != 1:
        raise BuildError(f"{PTH_FILE} phải có đúng một dòng '#import site' (bản embeddable khác dự kiến)")
    lines[positions[0]] = "import site"
    return "\r\n".join(lines) + "\r\n"


def holocron_pth_text() -> str:
    """Nội dung `python/Lib/site-packages/holocron.pth`: đường dẫn tương đối tới `app/` và `site-packages/`."""
    return "".join(f"{line}\r\n" for line in HOLOCRON_PTH_LINES)


def holocron_cmd_bytes() -> bytes:
    """`Holocron.cmd`: ASCII thuần, CRLF, chỉ dùng `%~dp0` (giải zip ở đâu cũng chạy)."""
    return "".join(f"{line}\r\n" for line in HOLOCRON_CMD).encode("ascii")


def install_python(stage: Path, embed_zip: Path) -> Path:
    """Giải Python embeddable vào `python/`, bật `import site`, ghi `holocron.pth`; trả thư mục `python/`."""
    python_dir = stage / "python"
    safe_extract(embed_zip, python_dir)
    pth = python_dir / PTH_FILE
    if not pth.exists():
        raise BuildError(f"bản embeddable thiếu {PTH_FILE}")
    pth.write_bytes(enable_site_in_pth(pth.read_text(encoding="utf-8")).encode("utf-8"))
    site_dir = python_dir / "Lib" / "site-packages"
    site_dir.mkdir(parents=True, exist_ok=True)
    (site_dir / "holocron.pth").write_bytes(holocron_pth_text().encode("ascii"))
    return python_dir


# ---------------------------------------------------------------- lock


@dataclass(frozen=True)
class LockEntry:
    name: str
    version: str
    sha256: str

    def line(self) -> str:
        return f"{self.name}=={self.version} --hash=sha256:{self.sha256}"


def parse_lock(text: str, source: str) -> list[LockEntry]:
    """Đọc file lock nghiêm ngặt: chỉ `tên==bản --hash=sha256:<hex>` trên một dòng; mọi dạng khác ⇒ BuildError."""
    entries: list[LockEntry] = []
    seen: set[str] = set()
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        where = f"{source}:{number}"
        if line.startswith("-"):
            raise BuildError(f"{where}: tuỳ chọn pip không được phép trong lock (index, -e, -r, -c…): {line}")
        if "://" in line or " @ " in line or line.startswith(("git+", "hg+", "svn+", "bzr+")):
            raise BuildError(f"{where}: URL/VCS không được phép trong lock: {line}")
        if "==" not in line:
            raise BuildError(f"{where}: thiếu '==' (phải ghim đúng một phiên bản): {line}")
        if "--hash=sha256:" not in line:
            raise BuildError(f"{where}: thiếu '--hash=sha256:': {line}")
        match = LOCK_LINE.match(line)
        if match is None:
            raise BuildError(f"{where}: dòng lock không hợp lệ: {line}")
        name = canonical_name(match["name"])
        if name in seen:
            raise BuildError(f"{where}: gói {name} xuất hiện hai lần")
        seen.add(name)
        entries.append(LockEntry(name, match["version"], match["sha256"]))
    return entries


def format_lock(entries: Iterable[LockEntry]) -> str:
    ordered = sorted(entries, key=lambda entry: entry.name)
    return "\n".join([*LOCK_HEADER, *(entry.line() for entry in ordered)]) + "\n"


def validate_lock_sets(base: Sequence[LockEntry], ocr: Sequence[LockEntry]) -> None:
    """`base ⊆ BASE_PACKAGES`, `ocr ⊆ OCR_PACKAGES`, `base ∩ ocr = ∅`; vi phạm ⇒ dừng (không thêm dependency)."""
    base_names = {entry.name for entry in base}
    ocr_names = {entry.name for entry in ocr}
    problems: list[str] = []
    if outside := sorted(base_names - BASE_PACKAGES):
        problems.append(f"gói ngoài closure base: {', '.join(outside)}")
    if outside := sorted(ocr_names - OCR_PACKAGES):
        problems.append(f"gói ngoài closure OCR: {', '.join(outside)}")
    if overlap := sorted(base_names & ocr_names):
        problems.append(f"gói trùng giữa base và OCR: {', '.join(overlap)}")
    if problems:
        raise BuildError("lock không hợp lệ (DỪNG, cần người dùng duyệt dependency): " + "; ".join(problems))


def read_project_dependencies(pyproject: Path) -> list[str]:
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    dependencies = data["project"]["dependencies"]
    if not isinstance(dependencies, list) or not all(isinstance(item, str) for item in dependencies):
        raise BuildError(f"{pyproject}: [project].dependencies phải là danh sách chuỗi")
    return [str(item) for item in dependencies]


def requirement_name(requirement: str) -> str:
    match = REQUIREMENT_NAME.match(requirement)
    if match is None:
        raise BuildError(f"không đọc được tên gói từ {requirement!r}")
    return canonical_name(match.group(1))


def split_roots(dependencies: Sequence[str]) -> tuple[list[str], list[str]]:
    """Chia `[project].dependencies` thành (base, OCR) theo `OCR_ROOTS`."""
    base = [item for item in dependencies if requirement_name(item) not in OCR_ROOTS]
    ocr = [item for item in dependencies if requirement_name(item) in OCR_ROOTS]
    if {requirement_name(item) for item in ocr} != OCR_ROOTS:
        raise BuildError(f"pyproject thiếu gốc OCR dự kiến: {', '.join(sorted(OCR_ROOTS))}")
    return base, ocr


def parse_freeze(text: str) -> dict[str, str]:
    """`pip freeze` ⇒ {tên chuẩn: phiên bản}; bỏ dòng editable/URL."""
    pins: dict[str, str] = {}
    for raw in text.splitlines():
        match = FREEZE_LINE.match(raw.strip())
        if match is not None:
            pins[canonical_name(match.group(1))] = match.group(2)
    return pins


def wheel_tags(filename: str) -> tuple[str, str, list[tuple[str, str, str]]]:
    """(tên chuẩn, phiên bản, các bộ tag mở rộng) của một file wheel."""
    if not filename.endswith(".whl"):
        raise BuildError(f"không phải wheel (sdist bị cấm): {filename}")
    parts = filename[: -len(".whl")].split("-")
    if len(parts) not in (5, 6):
        raise BuildError(f"tên wheel không hợp lệ: {filename}")
    name, version = parts[0], parts[1]
    pythons, abis, platforms = parts[-3], parts[-2], parts[-1]
    combos = [(py, abi, plat) for py in pythons.split(".") for abi in abis.split(".") for plat in platforms.split(".")]
    return canonical_name(name), version, combos


def wheel_is_target(filename: str) -> bool:
    """Wheel chạy được trên CPython 3.14 win_amd64: cp314-cp314, cp3x-abi3 (x ≤ 14), hoặc py3/cp314-none."""
    _, _, combos = wheel_tags(filename)
    for python, abi, platform in combos:
        if platform not in ("win_amd64", "any"):
            continue
        if abi == "cp314" and python == "cp314":
            return True
        abi3 = ABI3_PYTHON.match(python)
        if abi == "abi3" and abi3 is not None and int(abi3.group(1)) <= 14:
            return True
        if abi == "none" and python in ("py3", "py314", "cp314"):
            return True
    return False


def collect_wheels(directory: Path) -> dict[str, LockEntry]:
    """Wheel đã tải ⇒ {tên: LockEntry}; file không phải wheel đích hoặc trùng tên ⇒ BuildError."""
    found: dict[str, LockEntry] = {}
    for path in sorted(directory.iterdir()):
        name, version, _ = wheel_tags(path.name)
        if not wheel_is_target(path.name):
            raise BuildError(f"wheel không đúng nền tảng cp314/abi3/py3-none win_amd64: {path.name}")
        if name in found:
            raise BuildError(f"gói {name} có nhiều hơn một wheel trong {directory}")
        found[name] = LockEntry(name, version, sha256_file(path))
    return found


def resolve_lock(
    *, python: str, work_dir: Path, pyproject: Path, run: Runner
) -> tuple[list[LockEntry], list[LockEntry]]:
    """Resolve base và base+OCR bằng `pip download` cho win_amd64/cp314 với constraint = `pip freeze`."""
    pins = parse_freeze(run([python, "-m", "pip", "freeze", "--exclude-editable"], None))
    if not pins:
        raise BuildError("pip freeze rỗng: chạy script bằng Python của .venv đã qua bộ test")
    if work_dir.exists():
        remove_tree(work_dir)
    work_dir.mkdir(parents=True)
    constraints = work_dir / "constraints.txt"
    constraints.write_text("".join(f"{name}=={version}\n" for name, version in sorted(pins.items())), "utf-8")
    base_roots, ocr_roots = split_roots(read_project_dependencies(pyproject))
    resolved: dict[str, dict[str, LockEntry]] = {}
    for label, roots in (("base", base_roots), ("full", [*base_roots, *ocr_roots])):
        dest = work_dir / label
        dest.mkdir()
        pip = [python, "-m", "pip", "download", "--disable-pip-version-check", "--dest", str(dest)]
        run([*pip, *TARGET_PIP_ARGS, "-c", str(constraints), *roots], None)
        resolved[label] = collect_wheels(dest)
    base, full = resolved["base"], resolved["full"]
    drift = [
        f"{entry.name}=={entry.version} (venv: {pins.get(entry.name, 'không có')})"
        for entry in full.values()
        if pins.get(entry.name) != entry.version
    ]
    if drift:
        raise BuildError("bản resolve khác pip freeze của .venv (không được trôi lên bản mới): " + ", ".join(drift))
    for name, entry in base.items():
        if full.get(name) != entry:
            raise BuildError(f"gói {name} resolve khác nhau giữa base và base+OCR")
    base_entries = sorted(base.values(), key=lambda entry: entry.name)
    ocr_entries = sorted((entry for name, entry in full.items() if name not in base), key=lambda entry: entry.name)
    validate_lock_sets(base_entries, ocr_entries)
    return base_entries, ocr_entries


def read_locks(ocr_mode: str) -> list[Path]:
    """Đọc và kiểm hai file lock; trả các file cần cài theo chế độ OCR."""
    if not LOCK_BASE.exists() or not LOCK_OCR.exists():
        raise BuildError("thiếu file lock trong holocron/packaging/: chạy `python scripts/build_portable.py --lock`")
    base = parse_lock(LOCK_BASE.read_text(encoding="utf-8"), LOCK_BASE.name)
    ocr = parse_lock(LOCK_OCR.read_text(encoding="utf-8"), LOCK_OCR.name)
    validate_lock_sets(base, ocr)
    return [LOCK_BASE, LOCK_OCR] if ocr_mode == "full" else [LOCK_BASE]


def expected_packages(lock_files: Sequence[Path]) -> dict[str, str]:
    expected: dict[str, str] = {}
    for path in lock_files:
        for entry in parse_lock(path.read_text(encoding="utf-8"), path.name):
            expected[entry.name] = entry.version
    return expected


def installed_distributions(site_dir: Path) -> dict[str, tuple[str, Path]]:
    """{tên chuẩn: (phiên bản, thư mục dist-info)} của các gói đã cài vào `site_dir`."""
    found: dict[str, tuple[str, Path]] = {}
    for dist_info in sorted(site_dir.glob("*.dist-info")):
        metadata = read_metadata(dist_info)
        name, version = metadata.get("Name"), metadata.get("Version")
        if not name or not version:
            raise BuildError(f"{dist_info.name}: METADATA thiếu Name/Version")
        found[canonical_name(name)] = (version, dist_info)
    return found


def install_wheels(*, python: str, lock_files: Sequence[Path], site_dir: Path, run: Runner) -> None:
    """Cài wheel đã ghim hash vào `site-packages/` của bản zip; kiểm đúng tập gói của lock."""
    site_dir.mkdir(parents=True, exist_ok=True)
    requirement_args = [arg for path in lock_files for arg in ("-r", str(path))]
    pip = [python, "-m", "pip", "install", "--disable-pip-version-check", "--target", str(site_dir)]
    run([*pip, "--no-deps", "--require-hashes", "--no-compile", *TARGET_PIP_ARGS, *requirement_args], None)
    installed = {name: version for name, (version, _) in installed_distributions(site_dir).items()}
    expected = expected_packages(lock_files)
    if installed != expected:
        raise BuildError(f"gói đã cài khác lock: cài {sorted(installed.items())}, lock {sorted(expected.items())}")


# ---------------------------------------------------------------- bỏ khỏi zip


def prune_site_packages(site_dir: Path) -> list[str]:
    """Bỏ `opencv_videoio_ffmpeg*.dll` (LGPL, chỉ dùng cho video), đúng `onnxruntime/tools/`, `*.pyi`, đúng `bin/`.

    Thư mục chỉ xoá theo đường dẫn cố định ở cấp đã định, không xoá theo tên ở mọi cấp.
    """
    removed: list[str] = []
    for relative in ("bin", "onnxruntime/tools"):
        target = site_dir / relative
        if target.is_dir():
            remove_tree(target)
            removed.append(relative + "/")
    for path in sorted(site_dir.rglob("*")):
        if not path.is_file():
            continue
        lower = path.name.lower()
        if lower.endswith(".pyi") or (lower.startswith("opencv_videoio_ffmpeg") and lower.endswith(".dll")):
            removed.append(path.relative_to(site_dir).as_posix())
            path.unlink()
    return removed


# ---------------------------------------------------------------- license


@dataclass(frozen=True)
class LicenseVerdict:
    allowed: bool
    reason: str


SPDX_TOKEN = re.compile(r"\s*(\(|\)|[A-Za-z0-9.+:-]+)")


def tokenize_spdx(expression: str) -> list[str]:
    tokens: list[str] = []
    position = 0
    text = expression.strip()
    while position < len(text):
        match = SPDX_TOKEN.match(text, position)
        if match is None:
            raise BuildError(f"biểu thức license không hợp lệ: {expression!r}")
        tokens.append(match.group(1))
        position = match.end()
        while position < len(text) and text[position].isspace():
            position += 1
    if not tokens:
        raise BuildError("biểu thức license rỗng")
    return tokens


@dataclass(frozen=True)
class SpdxNode:
    """Lá (`op == "ID"`, `value` = mã license) hoặc nút `AND`/`OR` với `children`."""

    op: str
    value: str = ""
    children: tuple[SpdxNode, ...] = ()


def parse_spdx(expression: str) -> SpdxNode:
    """Phân tích biểu thức SPDX (`AND` chặt hơn `OR`, ngoặc, `WITH` giữ mã chính)."""
    tokens = tokenize_spdx(expression)
    position = 0

    def peek() -> str | None:
        return tokens[position] if position < len(tokens) else None

    def take() -> str:
        nonlocal position
        if position >= len(tokens):
            raise BuildError(f"biểu thức license thiếu vế: {expression!r}")
        token = tokens[position]
        position += 1
        return token

    def parse_or() -> SpdxNode:
        terms = [parse_and()]
        while (token := peek()) is not None and token.upper() == "OR":
            take()
            terms.append(parse_and())
        return terms[0] if len(terms) == 1 else SpdxNode("OR", children=tuple(terms))

    def parse_and() -> SpdxNode:
        terms = [parse_atom()]
        while (token := peek()) is not None and token.upper() == "AND":
            take()
            terms.append(parse_atom())
        return terms[0] if len(terms) == 1 else SpdxNode("AND", children=tuple(terms))

    def parse_atom() -> SpdxNode:
        token = take()
        if token == "(":
            node = parse_or()
            if take() != ")":
                raise BuildError(f"biểu thức license thiếu ')': {expression!r}")
            return node
        if token == ")" or token.upper() in ("AND", "OR", "WITH"):
            raise BuildError(f"biểu thức license sai cú pháp: {expression!r}")
        if (following := peek()) is not None and following.upper() == "WITH":
            take()
            take()
        return SpdxNode("ID", value=token.removesuffix("+"))

    node = parse_or()
    if position != len(tokens):
        raise BuildError(f"biểu thức license thừa phần cuối: {expression!r}")
    return node


def license_id_verdict(package: str, license_id: str) -> LicenseVerdict:
    upper = license_id.upper()
    if upper in PERMISSIVE_LICENSES:
        return LicenseVerdict(True, license_id)
    if upper.startswith(("LGPL", "MPL")):
        allowed = any(name == package and upper == spdx.upper() for name, spdx in WEAK_COPYLEFT_ALLOWED)
        reason = f"{license_id} (danh sách cho phép)" if allowed else f"{license_id} ngoài danh sách cho phép"
        return LicenseVerdict(allowed, reason)
    if upper.startswith(("GPL", "AGPL")):
        return LicenseVerdict(False, f"{license_id} bị cấm (GPL/AGPL)")
    return LicenseVerdict(False, f"{license_id} không nhận ra")


def evaluate_license(package: str, expression: str) -> LicenseVerdict:
    """`AND` cần mọi vế được phép, `OR` cần một vế; cú pháp sai ⇒ không được phép (fail-closed)."""
    try:
        node = parse_spdx(expression)
    except BuildError as exc:
        return LicenseVerdict(False, str(exc))

    def visit(current: SpdxNode) -> LicenseVerdict:
        if current.op == "ID":
            return license_id_verdict(package, current.value)
        verdicts = [visit(child) for child in current.children]
        reasons = "; ".join(verdict.reason for verdict in verdicts if not verdict.allowed)
        if current.op == "AND":
            return LicenseVerdict(all(v.allowed for v in verdicts), reasons or expression)
        return LicenseVerdict(any(v.allowed for v in verdicts), reasons or expression)

    return visit(node)


def is_known_spdx(text: str) -> bool:
    """Chuỗi là biểu thức SPDX mà mọi mã đều thuộc nhóm đã biết (cho phép, LGPL/MPL, GPL/AGPL)."""
    try:
        node = parse_spdx(text)
    except BuildError:
        return False

    def known(current: SpdxNode) -> bool:
        if current.op == "ID":
            upper = current.value.upper()
            return upper in PERMISSIVE_LICENSES or upper.startswith(("LGPL", "MPL", "GPL", "AGPL"))
        return all(known(child) for child in current.children)

    return known(node)


def read_metadata(dist_info: Path) -> Message:
    path = dist_info / "METADATA"
    if not path.exists():
        raise BuildError(f"{dist_info.name}: thiếu METADATA")
    return email.parser.Parser().parsestr(path.read_text(encoding="utf-8", errors="replace"), headersonly=True)


def license_expression(name: str, version: str, metadata: Message) -> str | None:
    """Biểu thức license từ metadata: `License-Expression` ⇒ bản đã đọc tay ⇒ `License` ⇒ classifier."""
    expression = (metadata.get("License-Expression") or "").strip()
    if expression:
        return expression
    override = LICENSE_OVERRIDES.get((name, version))
    if override is not None:
        return override
    field = (metadata.get("License") or "").strip()
    if field and "\n" not in field:
        alias = LICENSE_ALIASES.get(field.lower())
        if alias is not None:
            return alias
        if is_known_spdx(field):
            return field
    classifiers = [c for c in metadata.get_all("Classifier") or [] if c.startswith("License ::")]
    mapped: list[str] = []
    for classifier in classifiers:
        if classifier in CLASSIFIER_IGNORED:
            continue
        spdx = CLASSIFIER_LICENSES.get(classifier)
        if spdx is None:
            return None
        mapped.append(spdx)
    return " AND ".join(dict.fromkeys(mapped)) if mapped else None


@dataclass(frozen=True)
class PackageLicense:
    source: str
    name: str
    version: str
    expression: str
    license_files: tuple[Path, ...]


def dist_license_files(dist_info: Path) -> tuple[Path, ...]:
    files = [path for path in sorted((dist_info / "licenses").rglob("*")) if path.is_file()]
    files.extend(path for path in sorted(dist_info.iterdir()) if path.is_file() and LICENSE_FILE.match(path.name))
    return tuple(files)


def check_python_licenses(site_dir: Path) -> list[PackageLicense]:
    """Phân loại license của mọi `*.dist-info`; thiếu, không nhận ra hoặc không được phép ⇒ BuildError."""
    records: list[PackageLicense] = []
    problems: list[str] = []
    for name, (version, dist_info) in installed_distributions(site_dir).items():
        expression = license_expression(name, version, read_metadata(dist_info))
        if expression is None:
            problems.append(f"{name} {version}: thiếu hoặc không nhận ra license")
            continue
        verdict = evaluate_license(name, expression)
        if not verdict.allowed:
            problems.append(f"{name} {version}: {expression} ⇒ {verdict.reason}")
            continue
        records.append(PackageLicense("python", name, version, expression, dist_license_files(dist_info)))
    if problems:
        raise BuildError("license không đạt:\n  " + "\n  ".join(problems))
    return records


def check_bundled_dlls(site_dir: Path) -> None:
    """Kiểm DLL theo tên: `geos*.dll` ⇒ chờ Q4; `opencv_videoio_ffmpeg*` phải đã bị bỏ."""
    dlls = [path for path in site_dir.rglob("*.dll") if path.is_file()]
    ffmpeg = [path.name for path in dlls if path.name.lower().startswith("opencv_videoio_ffmpeg")]
    if ffmpeg:
        raise BuildError(f"DLL LGPL của ffmpeg còn trong bản zip: {', '.join(sorted(ffmpeg))}")
    geos = [path.name for path in dlls if path.name.lower().startswith("geos")]
    if geos:
        raise BuildError(f"{Q4_MESSAGE}: {', '.join(sorted(geos))}")


@dataclass(frozen=True)
class NpmPackage:
    name: str
    version: str
    directory: Path


def _npm_license(manifest: Mapping[str, object]) -> str | None:
    value = manifest.get("license")
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, dict) and isinstance(value.get("type"), str):
        return str(value["type"])
    legacy = manifest.get("licenses")
    if isinstance(legacy, list):
        types = [str(item["type"]) for item in legacy if isinstance(item, dict) and isinstance(item.get("type"), str)]
        if types:
            return " OR ".join(types)
    return None


def npm_production_packages(tree: Mapping[str, object], lock_packages: Mapping[str, object]) -> list[NpmPackage]:
    """Gói production thực sự cài (từ `npm ls --omit=dev --omit=optional --all --json`), bỏ mục dev/tuỳ chọn
    theo cờ trong `package-lock.json` (binary tuỳ chọn như `@img/sharp-libvips-*` không tính)."""
    found: dict[str, NpmPackage] = {}

    def locate(name: str, version: str, parents: Sequence[str]) -> str | None:
        for depth in range(len(parents), -1, -1):
            prefix = parents[depth - 1] + "/" if depth else ""
            key = f"{prefix}node_modules/{name}"
            entry = lock_packages.get(key)
            if isinstance(entry, dict) and entry.get("version") == version:
                return key
        return None

    def walk(node: Mapping[str, object], parents: list[str]) -> None:
        dependencies = node.get("dependencies")
        if not isinstance(dependencies, dict):
            return
        for name, child in sorted(dependencies.items()):
            if not isinstance(child, dict) or not isinstance(child.get("version"), str):
                continue  # mục rỗng: phụ thuộc tuỳ chọn/peer không được cài
            key = locate(name, str(child["version"]), parents)
            if key is None:
                raise BuildError(f"npm: không tìm thấy {name}@{child['version']} trong package-lock.json")
            entry = lock_packages[key]
            if isinstance(entry, dict) and any(entry.get(flag) for flag in ("dev", "optional", "devOptional")):
                continue
            if key not in found:
                found[key] = NpmPackage(name, str(child["version"]), Path(key))
                walk(child, [*parents, key])

    walk(tree, [])
    return sorted(found.values(), key=lambda package: (package.name, package.version))


def check_npm_licenses(frontend_dir: Path, npm: str, run: Runner) -> list[PackageLicense]:
    tree = json.loads(run([npm, "ls", "--omit=dev", "--omit=optional", "--all", "--json"], frontend_dir))
    lock = json.loads((frontend_dir / "package-lock.json").read_text(encoding="utf-8"))
    if not isinstance(tree, dict) or not isinstance(lock, dict) or not isinstance(lock.get("packages"), dict):
        raise BuildError("npm ls/package-lock.json không đúng dạng")
    records: list[PackageLicense] = []
    problems: list[str] = []
    for package in npm_production_packages(tree, lock["packages"]):
        directory = frontend_dir / package.directory
        manifest = json.loads((directory / "package.json").read_text(encoding="utf-8"))
        expression = _npm_license(manifest) if isinstance(manifest, dict) else None
        if expression is None:
            problems.append(f"npm {package.name} {package.version}: thiếu license")
            continue
        verdict = evaluate_license(package.name, expression)
        if not verdict.allowed:
            problems.append(f"npm {package.name} {package.version}: {expression} ⇒ {verdict.reason}")
            continue
        files = tuple(p for p in sorted(directory.iterdir()) if p.is_file() and LICENSE_FILE.match(p.name))
        records.append(PackageLicense("npm", package.name, package.version, expression, files))
    if problems:
        raise BuildError("license npm không đạt:\n  " + "\n  ".join(problems))
    return records


def license_gate(site_dir: Path, ocr_mode: str) -> list[PackageLicense]:
    """Bước license của gói Python + DLL; `--ocr full` luôn dừng ở đây chờ Q4."""
    records = check_python_licenses(site_dir)
    check_bundled_dlls(site_dir)
    if ocr_mode == "full":
        raise BuildError(Q4_MESSAGE)
    return records


def render_notices(python_license: str, records: Sequence[PackageLicense], read: Callable[[Path], str]) -> str:
    rule = "=" * 78
    parts = [
        "THIRD_PARTY_NOTICES — thành phần bên thứ ba trong bản zip portable Holocron",
        "Holocron được phát hành theo giấy phép MIT (file LICENSE). Các thành phần dưới đây giữ giấy phép riêng.",
        "",
        rule,
        f"Python {PYTHON_VERSION} embeddable (PSF-2.0; kèm OpenSSL, SQLite, libffi…)",
        rule,
        python_license.strip(),
        "",
    ]
    for record in records:
        parts += [rule, f"[{record.source}] {record.name} {record.version} — {record.expression}", rule]
        for path in record.license_files:
            parts += [f"--- {path.name}", read(path).strip(), ""]
    return "\n".join(parts) + "\n"


def read_text_lenient(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------- mã app và web


def git_app_files(app_src: Path, run: Runner) -> list[str]:
    """File của `holocron_core/` theo git (đã commit + chưa track nhưng không bị ignore), bỏ cache/build."""
    output = run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", "holocron_core"], app_src
    )
    files: list[str] = []
    for relative in sorted(set(filter(None, output.split("\0")))):
        parts = PurePosixPath(relative).parts
        if any(part == "__pycache__" or part == "build" or part.endswith(".egg-info") for part in parts):
            continue
        if relative.endswith((".pyc", ".pyo")) or not (app_src / relative).is_file():
            continue
        files.append(relative)
    return files


def check_app_tree(package_dir: Path) -> None:
    """`holocron_core/` phải có launcher (P3), template và migration SQL (package-data)."""
    missing: list[str] = []
    if not (package_dir / "launcher.py").is_file():
        missing.append("holocron_core/launcher.py (nhánh feat/packaging-launcher chưa có trong mã nguồn đóng gói)")
    if not any((package_dir / "infrastructure" / "templates").glob("*.py")):
        missing.append("holocron_core/infrastructure/templates/*.py")
    if not any((package_dir / "infrastructure" / "storage" / "migrations").glob("*.sql")):
        missing.append("holocron_core/infrastructure/storage/migrations/*.sql")
    if missing:
        raise BuildError("mã nguồn đóng gói thiếu: " + "; ".join(missing))


def copy_app(app_src: Path, app_dir: Path, run: Runner) -> int:
    files = git_app_files(app_src, run)
    for relative in files:
        target = app_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(app_src / relative, target)
    check_app_tree(app_dir / "holocron_core")
    return len(files)


def check_web_out(web_out: Path) -> None:
    if not (web_out / "index.html").is_file():
        raise BuildError(f"thiếu {web_out / 'index.html'}: bản export tĩnh của frontend chưa được dựng")


def npm_scripts(frontend_dir: Path) -> Mapping[str, object]:
    manifest = json.loads((frontend_dir / "package.json").read_text(encoding="utf-8"))
    scripts = manifest.get("scripts") if isinstance(manifest, dict) else None
    return scripts if isinstance(scripts, dict) else {}


def build_frontend(frontend_dir: Path, npm: str, run: Runner) -> Path:
    """`npm ci` rồi `npm run build:static` (script frontend tự đặt cờ export); trả thư mục `out/`."""
    if "build:static" not in npm_scripts(frontend_dir):
        raise BuildError("package.json chưa có script `build:static` (nhánh feat/frontend-static-export chưa merge?)")
    run([npm, "ci"], frontend_dir)
    run([npm, "run", "build:static"], frontend_dir)
    web_out = frontend_dir / "out"
    check_web_out(web_out)
    return web_out


# ---------------------------------------------------------------- zip


def remove_tree(path: Path) -> None:
    def make_writable(function: Callable[..., object], target: str, _exc: BaseException) -> None:
        os.chmod(target, stat.S_IWRITE)
        function(target)

    shutil.rmtree(path, onexc=make_writable)


def text_crlf_bom(text: str) -> bytes:
    """File .txt cho người dùng Windows: CRLF + UTF-8 BOM (Notepad cũ nhận đúng tiếng Việt)."""
    return ("\r\n".join(text.splitlines()) + "\r\n").encode("utf-8-sig")


def write_zip(stage: Path, zip_path: Path) -> int:
    """Nén `stage` (thư mục gốc `Holocron-<version>-win64/`) theo thứ tự cố định; trả số file."""
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    files = sorted(path for path in stage.rglob("*") if path.is_file())
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
        for path in files:
            bundle.write(path, (Path(stage.name) / path.relative_to(stage)).as_posix())
    return len(files)


def check_zip_members(names: Sequence[str], root: str) -> None:
    """Nội dung bắt buộc và nội dung cấm của bản zip cuối."""
    present = set(names)
    required = [
        "Holocron.cmd",
        "LICENSE",
        "README.txt",
        "HUONG-DAN-TESSERACT.txt",
        "THIRD_PARTY_NOTICES.txt",
        "python/python.exe",
        f"python/{PTH_FILE}",
        "python/Lib/site-packages/holocron.pth",
        "app/holocron_core/launcher.py",
        "web/index.html",
    ]
    problems = [f"thiếu {item}" for item in required if f"{root}/{item}" not in present]
    patterns = {
        "app/holocron_core/infrastructure/templates/*.py": re.compile(
            rf"^{re.escape(root)}/app/holocron_core/infrastructure/templates/[^/]+\.py$"
        ),
        "app/holocron_core/infrastructure/storage/migrations/*.sql": re.compile(
            rf"^{re.escape(root)}/app/holocron_core/infrastructure/storage/migrations/[^/]+\.sql$"
        ),
    }
    problems += [f"thiếu {label}" for label, pattern in patterns.items() if not any(map(pattern.match, names))]
    for name in names:
        if not name.startswith(f"{root}/"):
            problems.append(f"mục nằm ngoài thư mục gốc: {name}")
        lower = name.lower()
        if lower.endswith(".pyi") or "/opencv_videoio_ffmpeg" in lower:
            problems.append(f"mục bị cấm: {name}")
        if lower.startswith(f"{root.lower()}/site-packages/bin/") or "/site-packages/onnxruntime/tools/" in lower:
            problems.append(f"mục bị cấm: {name}")
    if problems:
        raise BuildError("bản zip không đạt:\n  " + "\n  ".join(problems))


# ---------------------------------------------------------------- điều phối


def read_frontend_version() -> str:
    version = json.loads((FRONTEND_DIR / "package.json").read_text(encoding="utf-8")).get("version")
    if not isinstance(version, str) or not version:
        raise BuildError("holocron/frontend/package.json thiếu version")
    return version


def compile_bytecode(stage: Path, run: Runner) -> None:
    """Biên dịch `.pyc` bằng chính `python.exe` của bản zip (`unchecked-hash`: không phụ thuộc mtime khi giải zip)."""
    python = stage / "python" / "python.exe"
    args = [str(python), "-m", "compileall", "-q", "-j", "0", "--invalidation-mode", "unchecked-hash"]
    run([*args, "app", "site-packages"], stage)


def run_lock(run: Runner) -> int:
    base, ocr = resolve_lock(python=sys.executable, work_dir=BUILD_DIR / "lock", pyproject=PYPROJECT, run=run)
    PACKAGING_DIR.mkdir(parents=True, exist_ok=True)
    LOCK_BASE.write_text(format_lock(base), encoding="utf-8", newline="\n")
    LOCK_OCR.write_text(format_lock(ocr), encoding="utf-8", newline="\n")
    print(f"Đã ghi {LOCK_BASE.relative_to(ROOT)} ({len(base)} gói), {LOCK_OCR.relative_to(ROOT)} ({len(ocr)} gói)")
    return 0


def run_build(args: argparse.Namespace, run: Runner) -> int:
    started = time.monotonic()
    run([sys.executable, str(ROOT / "scripts" / "check_versions.py")], ROOT)
    version = read_frontend_version()
    root_name = f"Holocron-{version}-win64"
    lock_files = read_locks(args.ocr)
    stage = BUILD_DIR / "stage" / root_name
    if stage.exists():
        remove_tree(stage)
    stage.mkdir(parents=True)

    app_src = Path(args.app_src).resolve()
    count = copy_app(app_src, stage / "app", run)
    print(f"[app] {count} file từ {app_src}")

    if args.web_out is not None:
        web_out = Path(args.web_out).resolve()
        check_web_out(web_out)
        frontend_dir = web_out.parent
    else:
        npm = shutil.which("npm")
        if npm is None:
            raise BuildError("không tìm thấy npm trong PATH (cần để dựng frontend hoặc dùng --web-out)")
        frontend_dir = FRONTEND_DIR
        web_out = build_frontend(frontend_dir, npm, run)
    shutil.copytree(web_out, stage / "web")
    print(f"[web] {web_out}")

    embed = ensure_cached_download(
        BUILD_DIR / "cache", PYTHON_EMBED_NAME, PYTHON_EMBED_URL, PYTHON_EMBED_SHA256, download_url
    )
    python_dir = install_python(stage, embed)
    site_dir = stage / "site-packages"
    install_wheels(python=sys.executable, lock_files=lock_files, site_dir=site_dir, run=run)
    removed = prune_site_packages(site_dir)
    print(f"[site-packages] bỏ {len(removed)} mục: {', '.join(removed[:8])}{' …' if len(removed) > 8 else ''}")

    records = license_gate(site_dir, args.ocr)
    npm_for_licenses = shutil.which("npm")
    if npm_for_licenses is None:
        raise BuildError("không tìm thấy npm trong PATH (cần `npm ls` để kiểm license frontend)")
    records += check_npm_licenses(frontend_dir, npm_for_licenses, run)
    notices = render_notices(read_text_lenient(python_dir / "LICENSE.txt"), records, read_text_lenient)
    (stage / "THIRD_PARTY_NOTICES.txt").write_bytes(text_crlf_bom(notices))

    if sys.platform == "win32":
        compile_bytecode(stage, run)
    else:
        print("[pyc] bỏ qua: chỉ biên dịch được trên Windows")

    (stage / "Holocron.cmd").write_bytes(holocron_cmd_bytes())
    shutil.copyfile(ROOT / "LICENSE", stage / "LICENSE")
    for name in ("README.txt", "HUONG-DAN-TESSERACT.txt"):
        (stage / name).write_bytes(text_crlf_bom((PACKAGING_DIR / name).read_text(encoding="utf-8")))

    zip_path = DIST_DIR / f"{root_name}.zip"
    files = write_zip(stage, zip_path)
    with zipfile.ZipFile(zip_path) as bundle:
        check_zip_members(bundle.namelist(), root_name)
    digest = sha256_file(zip_path)
    zip_path.with_name(zip_path.name + ".sha256").write_text(f"{digest} *{zip_path.name}\n", encoding="ascii")
    elapsed = time.monotonic() - started
    size_mb = zip_path.stat().st_size / (1024 * 1024)
    print(f"Xong: {zip_path} ({size_mb:.1f} MiB, {files} file, {elapsed:.0f} s), SHA-256 {digest}")
    return 0


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Dựng bản zip portable Windows của Holocron.")
    parser.add_argument("--lock", action="store_true", help="sinh lại hai file requirements-portable*.txt rồi thoát")
    parser.add_argument(
        "--ocr",
        choices=("lite", "full"),
        default="lite",
        help="lite: không RapidOCR (mặc định TẠM, chờ Q3); full: dừng ở bước license chờ Q4",
    )
    parser.add_argument("--app-src", default=str(BACKEND_DIR), help="thư mục holocron/backend chứa holocron_core/")
    parser.add_argument("--web-out", default=None, help="thư mục out/ đã dựng sẵn (bỏ bước build frontend)")
    return parser.parse_args(list(argv))


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        return run_lock(run_command) if args.lock else run_build(args, run_command)
    except BuildError as exc:
        print(f"LỖI build: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
