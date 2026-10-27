"""Script dựng bản zip portable (`scripts/build_portable.py`): hàm thuần và nhánh lỗi, không gọi mạng."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import sys
import zipfile
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "build_portable.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("build_portable", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bp = _load()
BuildError = bp.BuildError
HASH_A = "a" * 64
HASH_B = "b" * 64
EMBED_PTH = "python314.zip\r\n.\r\n\r\n# Uncomment to run site.main() automatically\r\n#import site\r\n"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------- ._pth, holocron.pth, Holocron.cmd


def test_holocron_pth_points_relative_to_app_and_site_packages() -> None:
    lines = bp.holocron_pth_text().split("\r\n")
    assert lines == ["..\\..\\..\\app", "..\\..\\..\\site-packages", ""]


def test_install_python_removes_pth_and_writes_holocron_pth(tmp_path: Path) -> None:
    embed = tmp_path / "embed.zip"
    with zipfile.ZipFile(embed, "w") as bundle:
        bundle.writestr("python314._pth", EMBED_PTH)
        bundle.writestr("python.exe", b"MZ")
    python_dir = bp.install_python(tmp_path / "stage", embed)
    assert not list(python_dir.glob("*._pth"))
    assert (python_dir / "python.exe").exists()
    assert (python_dir / "Lib" / "site-packages" / "holocron.pth").read_bytes() == bp.holocron_pth_text().encode()


def test_install_python_removes_every_pth_whatever_the_version(tmp_path: Path) -> None:
    embed = tmp_path / "embed.zip"
    with zipfile.ZipFile(embed, "w") as bundle:
        bundle.writestr("python315._pth", EMBED_PTH)
        bundle.writestr("python._PTH", EMBED_PTH)
        bundle.writestr("python.exe", b"MZ")
    python_dir = bp.install_python(tmp_path / "stage", embed)
    assert not [path for path in python_dir.iterdir() if path.name.lower().endswith("._pth")]


def test_install_python_without_pth_stops(tmp_path: Path) -> None:
    embed = tmp_path / "embed.zip"
    with zipfile.ZipFile(embed, "w") as bundle:
        bundle.writestr("python.exe", b"MZ")
    with pytest.raises(BuildError, match=r"\._pth"):
        bp.install_python(tmp_path / "stage", embed)


def test_holocron_cmd_is_ascii_crlf_and_location_independent() -> None:
    data = bp.holocron_cmd_bytes()
    text = data.decode("ascii")
    assert data.count(b"\n") == data.count(b"\r\n") == data.count(b"\r") > 0
    assert text.endswith("\r\n")
    assert "%~dp0" in text and "%CD%" not in text.upper()
    assert re.search(r"[A-Za-z]:[\\/]", text) is None
    assert '"%~dp0python\\python.exe" -I -X utf8 -m holocron_core.launcher %*' in text


# ---------------------------------------------------------------- Python embeddable: SHA-256 và cache


def _downloader(payload: bytes, calls: list[str]) -> object:
    def download(url: str, destination: Path) -> None:
        calls.append(url)
        destination.write_bytes(payload)

    return download


def test_download_with_wrong_sha_stops_and_leaves_no_file(tmp_path: Path) -> None:
    calls: list[str] = []
    with pytest.raises(BuildError, match="SHA-256"):
        bp.ensure_cached_download(tmp_path, "py.zip", "https://x/py.zip", _sha(b"good"), _downloader(b"evil", calls))
    assert calls == ["https://x/py.zip"]
    assert not (tmp_path / "py.zip").exists()


def test_corrupted_cache_is_deleted_and_downloaded_again_once(tmp_path: Path) -> None:
    (tmp_path / "py.zip").write_bytes(b"corrupted")
    calls: list[str] = []
    path = bp.ensure_cached_download(tmp_path, "py.zip", "https://x/py.zip", _sha(b"good"), _downloader(b"good", calls))
    assert path.read_bytes() == b"good"
    assert len(calls) == 1


def test_corrupted_cache_and_bad_download_stop(tmp_path: Path) -> None:
    (tmp_path / "py.zip").write_bytes(b"corrupted")
    calls: list[str] = []
    with pytest.raises(BuildError, match="SHA-256"):
        bp.ensure_cached_download(tmp_path, "py.zip", "https://x/py.zip", _sha(b"good"), _downloader(b"bad", calls))
    assert len(calls) == 1
    assert not (tmp_path / "py.zip").exists()


def test_valid_cache_is_rechecked_without_download(tmp_path: Path) -> None:
    (tmp_path / "py.zip").write_bytes(b"good")
    calls: list[str] = []
    bp.ensure_cached_download(tmp_path, "py.zip", "https://x/py.zip", _sha(b"good"), _downloader(b"good", calls))
    assert calls == []


def test_pinned_embeddable_is_python_314_amd64() -> None:
    assert re.fullmatch(r"3\.14\.\d+", bp.PYTHON_VERSION)
    assert bp.PYTHON_EMBED_URL.startswith("https://www.python.org/ftp/python/")
    assert bp.PYTHON_EMBED_URL.endswith("-embed-amd64.zip")
    assert re.fullmatch(r"[0-9a-f]{64}", bp.PYTHON_EMBED_SHA256)


# ---------------------------------------------------------------- giải nén an toàn


@pytest.mark.parametrize(
    "name",
    ["../evil.txt", "a/../../evil.txt", "/abs/evil.txt", "\\abs\\evil.txt", "C:/evil.txt", "c:evil.txt", "..\\evil"],
)
def test_safe_extract_rejects_zip_slip_before_writing(tmp_path: Path, name: str) -> None:
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("ok.txt", "ok")
        bundle.writestr(name, "evil")
    dest = tmp_path / "out"
    with pytest.raises(BuildError, match="mục zip"):
        bp.safe_extract(archive, dest)
    assert not (dest / "ok.txt").exists()
    assert not (tmp_path / "evil.txt").exists()


def test_safe_extract_writes_nested_members(tmp_path: Path) -> None:
    archive = tmp_path / "good.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("dir/", "")
        bundle.writestr("dir/sub/file.txt", "nội dung")
    written = bp.safe_extract(archive, tmp_path / "Thư mục ố")
    assert [path.name for path in written] == ["file.txt"]
    assert (tmp_path / "Thư mục ố" / "dir" / "sub" / "file.txt").read_text(encoding="utf-8") == "nội dung"


# ---------------------------------------------------------------- lock


def test_parse_lock_reads_pinned_hashed_lines() -> None:
    text = f"# chú thích\n\nFastAPI==0.162.0 --hash=sha256:{HASH_A}\ntyping_extensions==4.16.0 --hash=sha256:{HASH_B}\n"
    entries = bp.parse_lock(text, "lock.txt")
    assert [(e.name, e.version, e.sha256) for e in entries] == [
        ("fastapi", "0.162.0", HASH_A),
        ("typing-extensions", "4.16.0", HASH_B),
    ]


@pytest.mark.parametrize(
    ("line", "message"),
    [
        (f"fastapi>=0.1 --hash=sha256:{HASH_A}", "thiếu '=='"),
        ("fastapi==0.162.0", "thiếu '--hash"),
        ("fastapi==0.162.0 --hash=md5:abc", "thiếu '--hash"),
        (f"fastapi==0.162.0 --hash=sha256:{HASH_A[:10]}", "không hợp lệ"),
        (f"fastapi @ https://example.com/fastapi.whl --hash=sha256:{HASH_A}", "URL/VCS"),
        (f"git+https://github.com/x/y.git#egg=y --hash=sha256:{HASH_A}", "URL/VCS"),
        ("--index-url https://evil.example/simple", "tuỳ chọn pip"),
        ("--extra-index-url https://evil.example/simple", "tuỳ chọn pip"),
        ("-e .", "tuỳ chọn pip"),
        (f"fastapi==0.162.0 --hash=sha256:{HASH_A} \\", "không hợp lệ"),
    ],
)
def test_parse_lock_rejects_unpinned_or_remote_lines(line: str, message: str) -> None:
    with pytest.raises(BuildError, match=message):
        bp.parse_lock(f"# đầu\n{line}\n", "lock.txt")


def test_parse_lock_rejects_duplicate_package() -> None:
    text = f"fastapi==0.162.0 --hash=sha256:{HASH_A}\nFastAPI==0.162.0 --hash=sha256:{HASH_B}\n"
    with pytest.raises(BuildError, match="hai lần"):
        bp.parse_lock(text, "lock.txt")


def _entry(name: str) -> object:
    return bp.LockEntry(name, "1.0", HASH_A)


def test_lock_sets_reject_package_in_both_base_and_ocr() -> None:
    with pytest.raises(BuildError, match="trùng giữa base và OCR: idna"):
        bp.validate_lock_sets([_entry("idna")], [_entry("idna"), _entry("numpy")])


@pytest.mark.parametrize(
    ("base", "ocr", "message"),
    [
        (["fastapi", "evil-pkg"], [], "ngoài closure base: evil-pkg"),
        (["fastapi"], ["numpy", "httpx"], "ngoài closure OCR: httpx"),
        (["numpy"], [], "ngoài closure base: numpy"),
    ],
)
def test_lock_sets_reject_package_outside_closure(base: list[str], ocr: list[str], message: str) -> None:
    with pytest.raises(BuildError, match=message):
        bp.validate_lock_sets([_entry(n) for n in base], [_entry(n) for n in ocr])


def test_committed_lock_files_are_valid_and_cover_the_closure() -> None:
    base = bp.parse_lock(bp.LOCK_BASE.read_text(encoding="utf-8"), "base")
    ocr = bp.parse_lock(bp.LOCK_OCR.read_text(encoding="utf-8"), "ocr")
    bp.validate_lock_sets(base, ocr)
    assert {e.name for e in base} == bp.BASE_PACKAGES
    assert {e.name for e in ocr} == bp.OCR_PACKAGES
    assert bp.OCR_ROOTS <= bp.OCR_PACKAGES


def test_format_lock_round_trips() -> None:
    entries = [bp.LockEntry("uvicorn", "0.54.0", HASH_B), bp.LockEntry("anyio", "4.15.1", HASH_A)]
    assert bp.parse_lock(bp.format_lock(entries), "x") == sorted(entries, key=lambda e: e.name)


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("pydantic_core-2.50.1-cp314-cp314-win_amd64.whl", True),
        ("fastapi-0.162.0-py3-none-any.whl", True),
        ("pypdfium2-5.14.0-py3-none-win_amd64.whl", True),
        ("thing-1.0-cp39-abi3-win_amd64.whl", True),
        ("thing-1.0-1-py2.py3-none-any.whl", True),
        ("thing-1.0-cp313-cp313-win_amd64.whl", False),
        ("thing-1.0-cp314-cp314-manylinux_2_17_x86_64.whl", False),
        ("thing-1.0-cp314-cp314t-win_amd64.whl", False),
        ("thing-1.0-cp314-cp314-win32.whl", False),
    ],
)
def test_wheel_tag_filter(filename: str, expected: bool) -> None:
    assert bp.wheel_is_target(filename) is expected


def test_collect_wheels_rejects_sdist(tmp_path: Path) -> None:
    (tmp_path / "thing-1.0.tar.gz").write_bytes(b"x")
    with pytest.raises(BuildError, match="sdist"):
        bp.collect_wheels(tmp_path)


def test_collect_wheels_rejects_foreign_platform(tmp_path: Path) -> None:
    (tmp_path / "thing-1.0-cp314-cp314-manylinux_2_17_x86_64.whl").write_bytes(b"x")
    with pytest.raises(BuildError, match="không đúng nền tảng"):
        bp.collect_wheels(tmp_path)


class FakePip:
    """Giả `pip freeze`/`pip download`: ghi wheel rỗng theo nhãn thư mục `--dest` (base/full)."""

    def __init__(self, freeze: dict[str, str], wheels: dict[str, list[str]]) -> None:
        self.freeze = freeze
        self.wheels = wheels
        self.calls: list[list[str]] = []

    def __call__(self, args: Sequence[str], cwd: Path | None) -> str:
        self.calls.append(list(args))
        if "freeze" in args:
            return "".join(f"{name}=={version}\n" for name, version in self.freeze.items()) + "-e git+https://x#egg=y\n"
        dest = Path(args[list(args).index("--dest") + 1])
        for filename in self.wheels[dest.name]:
            (dest / filename).write_bytes(filename.encode())
        return ""


def _pyproject(tmp_path: Path) -> Path:
    path = tmp_path / "pyproject.toml"
    deps = ["fastapi>=0.161.1", "rapidocr==3.10.0", "onnxruntime==1.31.0", "opencv-python-headless==5.0.0.93"]
    path.write_text(f"[project]\nname = 'x'\ndependencies = {json.dumps(deps)}\n", encoding="utf-8")
    return path


BASE_WHEELS = ["fastapi-0.162.0-py3-none-any.whl", "idna-3.20-py3-none-any.whl"]
OCR_WHEELS = [
    "rapidocr-3.10.0-py3-none-any.whl",
    "onnxruntime-1.31.0-cp314-cp314-win_amd64.whl",
    "opencv_python_headless-5.0.0.93-cp37-abi3-win_amd64.whl",
]
FREEZE = {"fastapi": "0.162.0", "idna": "3.20", "rapidocr": "3.10.0", "onnxruntime": "1.31.0"}
FREEZE["opencv-python-headless"] = "5.0.0.93"


def test_resolve_lock_splits_base_and_ocr_with_constraints(tmp_path: Path) -> None:
    pip = FakePip(FREEZE, {"base": BASE_WHEELS, "full": BASE_WHEELS + OCR_WHEELS})
    base, ocr = bp.resolve_lock(python="py", work_dir=tmp_path / "w", pyproject=_pyproject(tmp_path), run=pip)
    assert [e.name for e in base] == ["fastapi", "idna"]
    assert [e.name for e in ocr] == ["onnxruntime", "opencv-python-headless", "rapidocr"]
    downloads = [call for call in pip.calls if "download" in call]
    assert all("--only-binary=:all:" in call and "-c" in call and "win_amd64" in call for call in downloads)
    assert "rapidocr==3.10.0" not in downloads[0]
    assert (tmp_path / "w" / "constraints.txt").read_text(encoding="utf-8").count("==") == len(FREEZE)


def test_resolve_lock_rejects_package_outside_closure(tmp_path: Path) -> None:
    freeze = {**FREEZE, "evil-pkg": "1.0"}
    wheels = [*BASE_WHEELS, "evil_pkg-1.0-py3-none-any.whl"]
    pip = FakePip(freeze, {"base": wheels, "full": wheels + OCR_WHEELS})
    with pytest.raises(BuildError, match="ngoài closure base: evil-pkg"):
        bp.resolve_lock(python="py", work_dir=tmp_path / "w", pyproject=_pyproject(tmp_path), run=pip)


def test_resolve_lock_rejects_version_drift_from_venv(tmp_path: Path) -> None:
    pip = FakePip({**FREEZE, "fastapi": "0.161.9"}, {"base": BASE_WHEELS, "full": BASE_WHEELS + OCR_WHEELS})
    with pytest.raises(BuildError, match="khác pip freeze"):
        bp.resolve_lock(python="py", work_dir=tmp_path / "w", pyproject=_pyproject(tmp_path), run=pip)


# ---------------------------------------------------------------- bỏ khỏi zip


def test_prune_removes_only_listed_paths(tmp_path: Path) -> None:
    files = [
        "bin/uvicorn.exe",
        "onnxruntime/tools/convert.py",
        "onnxruntime/capi/ok.py",
        "pkg/bin/keep.txt",
        "pkg/tools/keep.py",
        "cv2/opencv_videoio_ffmpeg500_64.dll",
        "cv2/opencv_world.dll",
        "pkg/types.pyi",
        "pkg/mod.py",
    ]
    for name in files:
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text("x", encoding="utf-8")
    removed = bp.prune_site_packages(tmp_path)
    assert set(removed) == {"bin/", "onnxruntime/tools/", "cv2/opencv_videoio_ffmpeg500_64.dll", "pkg/types.pyi"}
    remaining = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file())
    assert remaining == [
        "cv2/opencv_world.dll",
        "onnxruntime/capi/ok.py",
        "pkg/bin/keep.txt",
        "pkg/mod.py",
        "pkg/tools/keep.py",
    ]


# ---------------------------------------------------------------- license


def test_spdx_and_binds_tighter_than_or() -> None:
    node = bp.parse_spdx("MIT OR Apache-2.0 AND BSD-3-Clause")
    assert node.op == "OR"
    assert [child.op for child in node.children] == ["ID", "AND"]


@pytest.mark.parametrize(
    ("package", "expression", "allowed"),
    [
        ("x", "MIT", True),
        ("x", "BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0", True),
        ("x", "GPL-3.0-only", False),
        ("x", "AGPL-3.0-or-later", False),
        ("x", "LGPL-2.1-only", False),
        ("shapely", "LGPL-2.1-or-later", False),
        ("x", "MPL-2.0", False),
        ("certifi", "MPL-2.0", True),
        ("tqdm", "MPL-2.0 AND MIT", True),
        ("x", "MIT AND GPL-2.0-only", False),
        ("x", "MIT OR GPL-2.0-only", True),
        ("x", "(MIT OR GPL-2.0-only) AND LGPL-3.0-only", False),
        ("x", "GPL-2.0-only OR LGPL-3.0-only", False),
        ("x", "Apache-2.0 WITH LLVM-exception", True),
        ("x", "LGPL-2.1+", False),
        ("x", "Foo-1.0", False),
        ("x", "MIT AND", False),
        ("x", "(MIT", False),
        ("x", "MIT, BSD", False),
    ],
)
def test_evaluate_license(package: str, expression: str, allowed: bool) -> None:
    assert bp.evaluate_license(package, expression).allowed is allowed


def _metadata(headers: str) -> object:
    import email.parser

    return email.parser.Parser().parsestr(headers, headersonly=True)


@pytest.mark.parametrize(
    ("name", "version", "headers", "expected"),
    [
        ("x", "1", "License-Expression: MIT\nLicense: GPL\n", "MIT"),
        ("x", "1", "License: MIT License\n", "MIT"),
        ("x", "1", "License: MPL-2.0 AND MIT\n", "MPL-2.0 AND MIT"),
        ("x", "1", "Classifier: License :: OSI Approved :: BSD License\n", "LicenseRef-BSD"),
        (
            "x",
            "1",
            "License: \nClassifier: License :: OSI Approved\nClassifier: License :: OSI Approved :: MIT License\n",
            "MIT",
        ),
        ("x", "1", "Name: x\n", None),
        ("x", "1", "License: Copyright (c) someone\n", None),
        ("x", "1", "Classifier: License :: Other/Proprietary License\n", None),
        (
            "pypdfium2",
            "5.14.0",
            "License: BSD-3-Clause, Apache-2.0, dependency licenses\n",
            "BSD-3-Clause AND Apache-2.0 AND CC-BY-4.0",
        ),
        ("pypdfium2", "9.9.9", "License: BSD-3-Clause, Apache-2.0, dependency licenses\n", None),
    ],
)
def test_license_expression_from_metadata(name: str, version: str, headers: str, expected: str | None) -> None:
    assert bp.license_expression(name, version, _metadata(headers)) == expected


def _dist(site: Path, name: str, version: str, headers: str) -> Path:
    dist_info = site / f"{name.replace('-', '_')}-{version}.dist-info"
    (dist_info / "licenses").mkdir(parents=True)
    (dist_info / "METADATA").write_text(f"Metadata-Version: 2.4\nName: {name}\nVersion: {version}\n{headers}", "utf-8")
    (dist_info / "licenses" / "LICENSE").write_text(f"license text of {name}", encoding="utf-8")
    return dist_info


def test_python_licenses_accept_permissive_and_collect_files(tmp_path: Path) -> None:
    _dist(tmp_path, "good", "1.0", "License-Expression: MIT\n")
    _dist(tmp_path, "certifi", "2026.10.23", "License: MPL-2.0\n")
    records = bp.check_python_licenses(tmp_path)
    assert [(r.name, r.expression) for r in records] == [("certifi", "MPL-2.0"), ("good", "MIT")]
    assert [p.name for p in records[1].license_files] == ["LICENSE"]


@pytest.mark.parametrize(
    ("headers", "message"),
    [
        ("License-Expression: GPL-3.0-only\n", "GPL/AGPL"),
        ("License-Expression: LGPL-2.1-only\n", "ngoài danh sách cho phép"),
        ("", "thiếu hoặc không nhận ra license"),
        ("License-Expression: MIT AND GPL-2.0-only\n", "GPL/AGPL"),
    ],
)
def test_python_licenses_fail_closed(tmp_path: Path, headers: str, message: str) -> None:
    _dist(tmp_path, "good", "1.0", "License-Expression: MIT\n")
    _dist(tmp_path, "bad", "1.0", headers)
    with pytest.raises(BuildError, match=message):
        bp.check_python_licenses(tmp_path)


# ---------------------------------------------------------------- cổng DLL/PYD, GEOS, THIRD_PARTY_NOTICES

FULL_BINARIES = [
    "python/python314.dll",
    "python/_ssl.pyd",
    "site-packages/pydantic_core/_pydantic_core.cp314-win_amd64.pyd",
    "site-packages/pypdfium2_raw/pdfium.dll",
    "site-packages/numpy/_core/_multiarray_umath.cp314-win_amd64.pyd",
    "site-packages/numpy.libs/libscipy_openblas64_-ed4f.dll",
    "site-packages/numpy.libs/msvcp140-a4c2.dll",
    "site-packages/shapely/lib.cp314-win_amd64.pyd",
    "site-packages/shapely.libs/geos-bf06.dll",
    "site-packages/shapely.libs/geos_c-6dd9.dll",
    "site-packages/shapely.libs/msvcp140-0fa7.dll",
    "site-packages/onnxruntime/capi/onnxruntime.dll",
    "site-packages/onnxruntime/capi/onnxruntime_pybind11_state.pyd",
    "site-packages/cv2/cv2.pyd",
    "site-packages/PIL/_imaging.cp314-win_amd64.pyd",
    "site-packages/google/_upb/_message.pyd",
    "app/holocron_core/launcher.py",
    "site-packages/cv2/__init__.py",
]


def test_binary_gate_accepts_known_layout_and_reports_licenses() -> None:
    records = bp.classify_binaries(FULL_BINARIES)
    by_path = {record.path: record.license for record in records}
    assert len(records) == len(FULL_BINARIES) - 2  # file .py không thuộc cổng DLL/PYD
    assert by_path["site-packages/shapely.libs/geos_c-6dd9.dll"].startswith("LGPL-2.1")
    assert by_path["site-packages/numpy.libs/msvcp140-a4c2.dll"] == bp.VC_REDIST


@pytest.mark.parametrize(
    "path, message",
    [
        ("site-packages/numpy.libs/geos_c-1.dll", "GEOS ngoài site-packages/shapely.libs/"),
        ("site-packages/geos.dll", "GEOS ngoài site-packages/shapely.libs/"),
        ("site-packages/shapely.libs/sub/geos-1.dll", "GEOS ngoài site-packages/shapely.libs/"),
        ("site-packages/evil/payload.dll", "không có trong danh sách cho phép"),
        ("site-packages/evil.pyd", "không có trong danh sách cho phép"),
        ("python/Lib/site-packages/x.pyd", "không có trong danh sách cho phép"),
        ("app/holocron_core/x.DLL", "không có trong danh sách cho phép"),
        ("site-packages/cv2/opencv_videoio_ffmpeg500_64.dll", "ffmpeg"),
    ],
)
def test_binary_gate_rejects_unknown_or_misplaced_dll(path: str, message: str) -> None:
    with pytest.raises(BuildError, match=message):
        bp.classify_binaries([*FULL_BINARIES, path])


def test_binary_gate_scans_the_whole_stage(tmp_path: Path) -> None:
    (tmp_path / "python").mkdir()
    (tmp_path / "python" / "python314.dll").write_bytes(b"MZ")
    assert [r.path for r in bp.check_stage_binaries(tmp_path)] == ["python/python314.dll"]
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "x.dll").write_bytes(b"MZ")
    with pytest.raises(BuildError, match="web/x.dll"):
        bp.check_stage_binaries(tmp_path)


@pytest.mark.parametrize(
    "pattern, path, expected",
    [
        ("python/*.dll", "python/python314.dll", True),
        ("python/*.dll", "python/sub/x.dll", False),
        ("site-packages/numpy/**/*.pyd", "site-packages/numpy/x.pyd", True),
        ("site-packages/numpy/**/*.pyd", "site-packages/numpy/a/b/x.pyd", True),
        ("site-packages/numpy/**/*.pyd", "site-packages/numpyx/x.pyd", False),
        ("site-packages/cv2/*.pyd", "SITE-PACKAGES/CV2/CV2.PYD", True),
    ],
)
def test_glob_regex(pattern: str, path: str, expected: bool) -> None:
    assert bool(bp.glob_regex(pattern).match(path)) is expected


def _ocr_entry(name: str, version: str) -> object:
    return bp.LockEntry(name, version, HASH_A)


def test_geos_mapping_follows_shapely_in_lock() -> None:
    source = bp.geos_source_for([_ocr_entry("rapidocr", "3.10.0"), _ocr_entry("shapely", "2.2.0")])
    assert source.version == "3.14.1"
    assert source.url == "https://download.osgeo.org/geos/geos-3.14.1.tar.bz2"
    assert re.fullmatch(r"[0-9a-f]{64}", source.sha256)


def test_committed_ocr_lock_has_geos_mapping() -> None:
    entries = bp.parse_lock(bp.LOCK_OCR.read_text(encoding="utf-8"), bp.LOCK_OCR.name)
    assert bp.geos_source_for(entries).version


def test_shapely_without_geos_mapping_stops() -> None:
    with pytest.raises(BuildError, match="chưa có ánh xạ"):
        bp.geos_source_for([_ocr_entry("shapely", "9.9.9")])


def test_lock_without_shapely_stops_full_build() -> None:
    with pytest.raises(BuildError, match="không có shapely"):
        bp.geos_source_for([_ocr_entry("rapidocr", "3.10.0")])


def test_geos_version_is_checked_against_the_real_dll(tmp_path: Path) -> None:
    source = bp.GEOS_BY_SHAPELY["2.2.0"]
    libs = tmp_path / "shapely.libs"
    libs.mkdir()
    (libs / "geos_c-1.dll").write_bytes(b"MZ..3.13.0-CAPI-1.19.0..")
    with pytest.raises(BuildError, match="không phải 3.14.1"):
        bp.check_geos_version(tmp_path, source)
    (libs / "geos_c-1.dll").write_bytes(b"MZ..3.14.1-CAPI-1.20.5..")
    bp.check_geos_version(tmp_path, source)


def test_geos_version_check_needs_the_dll(tmp_path: Path) -> None:
    with pytest.raises(BuildError, match="geos_c"):
        bp.check_geos_version(tmp_path, bp.GEOS_BY_SHAPELY["2.2.0"])


def test_committed_lgpl_text_matches_pinned_sha() -> None:
    text = bp.read_lgpl_text(bp.PACKAGING_DIR / "licenses" / bp.LGPL_NAME)
    assert "GNU LESSER GENERAL PUBLIC LICENSE" in text and "Version 2.1, February 1999" in text


def test_lgpl_text_with_wrong_sha_or_missing_stops(tmp_path: Path) -> None:
    path = tmp_path / "LGPL-2.1.txt"
    with pytest.raises(BuildError, match="thiếu toàn văn"):
        bp.read_lgpl_text(path)
    path.write_text("GNU LESSER GENERAL PUBLIC LICENSE (sửa)", encoding="utf-8")
    with pytest.raises(BuildError, match="khác bản ghim"):
        bp.read_lgpl_text(path)


LGPL_SAMPLE = "GNU LESSER GENERAL PUBLIC LICENSE\nVersion 2.1, February 1999\n...toàn văn..."


def _geos_notice() -> object:
    return bp.GeosNotice(bp.GEOS_BY_SHAPELY["2.2.0"], "2.2.0", LGPL_SAMPLE)


def test_notices_with_geos_carry_lgpl_text_and_source() -> None:
    binaries = [bp.BinaryRecord("site-packages/shapely.libs/geos_c-1.dll", "LGPL-2.1 (GEOS C API, xem licenses/)")]
    notice = _geos_notice()
    text = bp.render_notices("PSF text", [], bp.read_text_lenient, binaries=binaries, geos=notice)
    bp.check_notices(text, notice)
    assert "delvewheel" in text and "licenses/geos-3.14.1.tar.bz2" in text
    assert "site-packages/shapely.libs/geos_c-1.dll — LGPL-2.1" in text


@pytest.mark.parametrize(
    "drop, message",
    [
        (LGPL_SAMPLE.strip(), "thiếu toàn văn LGPL-2.1"),
        ("https://download.osgeo.org/geos/geos-3.14.1.tar.bz2", "thiếu nguồn GEOS"),
        ("licenses/geos-3.14.1.tar.bz2", "thiếu nguồn GEOS"),
        ("GEOS 3.14.1", "thiếu nguồn GEOS"),
    ],
)
def test_notices_missing_lgpl_or_geos_source_fail(drop: str, message: str) -> None:
    notice = _geos_notice()
    text = bp.render_notices("PSF text", [], bp.read_text_lenient, geos=notice).replace(drop, "")
    with pytest.raises(BuildError, match=message):
        bp.check_notices(text, notice)


def test_notices_without_geos_need_no_lgpl() -> None:
    bp.check_notices(bp.render_notices("PSF text", [], bp.read_text_lenient), None)


def test_ffmpeg_dll_left_in_site_packages_fails(tmp_path: Path) -> None:
    (tmp_path / "site-packages" / "cv2").mkdir(parents=True)
    (tmp_path / "site-packages" / "cv2" / "opencv_videoio_ffmpeg500_64.dll").write_bytes(b"MZ")
    with pytest.raises(BuildError, match="ffmpeg"):
        bp.check_stage_binaries(tmp_path)


def test_default_ocr_mode_is_full() -> None:
    assert bp.parse_args([]).ocr == "full"
    assert bp.parse_args(["--ocr", "lite"]).ocr == "lite"


def test_npm_production_packages_skip_optional_dev_and_missing() -> None:
    tree = {
        "dependencies": {
            "next": {
                "version": "16.4.3",
                "dependencies": {
                    "@img/sharp-libvips-linux-x64": {"version": "1.3.4"},
                    "@next/swc-win32-x64-msvc": {"version": "16.4.3"},
                    "@playwright/test": {},
                    "postcss": {"version": "8.5.23", "dependencies": {"nanoid": {"version": "3.3.20"}}},
                },
            },
            "react": {"version": "19.1.10"},
            "vitest": {"version": "3.2.4"},
        }
    }
    lock = {
        "": {},
        "node_modules/next": {"version": "16.4.3"},
        "node_modules/@img/sharp-libvips-linux-x64": {"version": "1.3.4", "optional": True},
        "node_modules/@next/swc-win32-x64-msvc": {"version": "16.4.3", "optional": True},
        "node_modules/postcss": {"version": "8.5.23"},
        "node_modules/postcss/node_modules/nanoid": {"version": "3.3.20"},
        "node_modules/nanoid": {"version": "9.0.0"},
        "node_modules/react": {"version": "19.1.10"},
        "node_modules/vitest": {"version": "3.2.4", "dev": True},
    }
    packages = bp.npm_production_packages(tree, lock)
    assert [(p.name, p.directory.as_posix()) for p in packages] == [
        ("nanoid", "node_modules/postcss/node_modules/nanoid"),
        ("next", "node_modules/next"),
        ("postcss", "node_modules/postcss"),
        ("react", "node_modules/react"),
    ]


def test_npm_package_missing_from_lock_fails() -> None:
    tree = {"dependencies": {"ghost": {"version": "1.0.0"}}}
    with pytest.raises(BuildError, match="ghost"):
        bp.npm_production_packages(tree, {})


def test_npm_licenses_fail_closed(tmp_path: Path) -> None:
    for name, manifest in (("ok", {"license": "MIT"}), ("gpl", {"license": "GPL-3.0"}), ("none", {})):
        (tmp_path / "node_modules" / name).mkdir(parents=True)
        (tmp_path / "node_modules" / name / "package.json").write_text(json.dumps(manifest), encoding="utf-8")
    lock = {"packages": {f"node_modules/{n}": {"version": "1.0.0"} for n in ("ok", "gpl", "none")}}
    (tmp_path / "package-lock.json").write_text(json.dumps(lock), encoding="utf-8")
    tree = {"dependencies": {n: {"version": "1.0.0"} for n in ("ok", "gpl", "none")}}

    def run(args: Sequence[str], cwd: Path | None) -> str:
        assert "--omit=dev" in args and "--omit=optional" in args and cwd == tmp_path
        return json.dumps(tree)

    with pytest.raises(BuildError) as excinfo:
        bp.check_npm_licenses(tmp_path, "npm", run)
    assert "gpl 1.0.0: GPL-3.0" in str(excinfo.value)
    assert "none 1.0.0: thiếu license" in str(excinfo.value)
    assert "npm ok" not in str(excinfo.value)


def test_render_notices_includes_python_and_package_texts(tmp_path: Path) -> None:
    license_file = tmp_path / "LICENSE"
    license_file.write_text("MIT text", encoding="utf-8")
    record = bp.PackageLicense("python", "good", "1.0", "MIT", (license_file,))
    notices = bp.render_notices("PSF text", [record], bp.read_text_lenient)
    assert "PSF text" in notices and "good 1.0 — MIT" in notices and "MIT text" in notices


# ---------------------------------------------------------------- mã app và web


def _app_tree(root: Path, *, launcher: bool = True) -> Path:
    package = root / "holocron_core"
    (package / "infrastructure" / "templates").mkdir(parents=True)
    (package / "infrastructure" / "storage" / "migrations").mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "infrastructure" / "templates" / "rr.py").write_text("", encoding="utf-8")
    (package / "infrastructure" / "storage" / "migrations" / "0001.sql").write_text("", encoding="utf-8")
    if launcher:
        (package / "launcher.py").write_text("", encoding="utf-8")
    return package


def test_missing_launcher_stops_with_clear_message(tmp_path: Path) -> None:
    package = _app_tree(tmp_path, launcher=False)
    with pytest.raises(BuildError, match="launcher.py"):
        bp.check_app_tree(package)


def test_missing_package_data_stops(tmp_path: Path) -> None:
    package = _app_tree(tmp_path)
    (package / "infrastructure" / "storage" / "migrations" / "0001.sql").unlink()
    with pytest.raises(BuildError, match=r"migrations/\*\.sql"):
        bp.check_app_tree(package)


def test_copy_app_uses_git_listing_and_skips_caches(tmp_path: Path) -> None:
    src = tmp_path / "src"
    _app_tree(src)
    (src / "holocron_core" / "__pycache__").mkdir()
    (src / "holocron_core" / "__pycache__" / "x.cpython-314.pyc").write_bytes(b"")
    listed = [
        "holocron_core/__init__.py",
        "holocron_core/launcher.py",
        "holocron_core/infrastructure/templates/rr.py",
        "holocron_core/infrastructure/storage/migrations/0001.sql",
        "holocron_core/__pycache__/x.cpython-314.pyc",
        "holocron_core/deleted.py",
    ]

    def run(args: Sequence[str], cwd: Path | None) -> str:
        assert args[:2] == ["git", "ls-files"] and cwd == src
        return "\0".join(listed) + "\0"

    count = bp.copy_app(src, tmp_path / "app", run)
    assert count == 4
    assert not (tmp_path / "app" / "holocron_core" / "__pycache__").exists()


def test_missing_web_index_stops(tmp_path: Path) -> None:
    with pytest.raises(BuildError, match="index.html"):
        bp.check_web_out(tmp_path)


def test_build_frontend_requires_build_static_script(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"build": "next build"}}), encoding="utf-8")

    def run(args: Sequence[str], cwd: Path | None) -> str:
        raise AssertionError("không được chạy npm khi thiếu build:static")

    with pytest.raises(BuildError, match="build:static"):
        bp.build_frontend(tmp_path, "npm", run)


def test_build_frontend_stops_when_out_index_missing(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"build:static": "x"}}), encoding="utf-8")
    calls: list[list[str]] = []

    def run(args: Sequence[str], cwd: Path | None) -> str:
        calls.append(list(args))
        return ""

    with pytest.raises(BuildError, match="index.html"):
        bp.build_frontend(tmp_path, "npm", run)
    assert calls == [["npm", "ci"], ["npm", "run", "build:static"]]


# ---------------------------------------------------------------- zip


REQUIRED = [
    "Holocron.cmd",
    "LICENSE",
    "README.txt",
    "HUONG-DAN-TESSERACT.txt",
    "THIRD_PARTY_NOTICES.txt",
    "python/python.exe",
    "python/Lib/site-packages/holocron.pth",
    "app/holocron_core/launcher.py",
    "app/holocron_core/infrastructure/templates/rr.py",
    "app/holocron_core/infrastructure/storage/migrations/0001.sql",
    "web/index.html",
]


def test_zip_members_complete_pass() -> None:
    bp.check_zip_members([f"R/{name}" for name in REQUIRED], "R")


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ("-app/holocron_core/launcher.py", "launcher.py"),
        ("-app/holocron_core/infrastructure/templates/rr.py", r"templates/\*\.py"),
        ("+site-packages/cv2/opencv_videoio_ffmpeg500_64.dll", "bị cấm"),
        ("+site-packages/bin/uvicorn.exe", "bị cấm"),
        ("+site-packages/onnxruntime/tools/x.py", "bị cấm"),
        ("+site-packages/pkg/x.pyi", "bị cấm"),
        ("+python/python314._pth", r"\._pth"),
        ("+python/python315._pth", r"\._pth"),
        ("+python/PYTHON._PTH", r"\._pth"),
        ("+site-packages/x._pth", r"\._pth"),
    ],
)
def test_zip_members_reject_missing_or_forbidden(change: str, message: str) -> None:
    names = [f"R/{name}" for name in REQUIRED]
    if change.startswith("-"):
        names.remove(f"R/{change[1:]}")
    else:
        names.append(f"R/{change[1:]}")
    with pytest.raises(BuildError, match=message):
        bp.check_zip_members(names, "R")


def test_zip_members_full_build_requires_geos_source_and_lgpl() -> None:
    names = [f"R/{name}" for name in REQUIRED]
    extra = ["licenses/geos-3.14.1.tar.bz2", "licenses/LGPL-2.1.txt"]
    with pytest.raises(BuildError, match="licenses/geos-3.14.1.tar.bz2"):
        bp.check_zip_members(names, "R", extra)
    bp.check_zip_members([*names, *(f"R/{name}" for name in extra)], "R", extra)


def test_write_zip_puts_everything_under_root_folder(tmp_path: Path) -> None:
    stage = tmp_path / "Holocron-0.1.0-win64"
    (stage / "web").mkdir(parents=True)
    (stage / "web" / "index.html").write_text("<html>", encoding="utf-8")
    (stage / "Holocron.cmd").write_bytes(bp.holocron_cmd_bytes())
    zip_path = tmp_path / "dist" / "out.zip"
    assert bp.write_zip(stage, zip_path) == 2
    with zipfile.ZipFile(zip_path) as bundle:
        assert sorted(bundle.namelist()) == ["Holocron-0.1.0-win64/Holocron.cmd", "Holocron-0.1.0-win64/web/index.html"]


def test_user_text_files_are_crlf_with_bom_and_short() -> None:
    for name in ("README.txt", "HUONG-DAN-TESSERACT.txt"):
        text = (bp.PACKAGING_DIR / name).read_text(encoding="utf-8")
        assert len(text.splitlines()) <= 15
        data = bp.text_crlf_bom(text)
        assert data.startswith(b"\xef\xbb\xbf")
        assert data.count(b"\n") == data.count(b"\r\n")


# ---------------------------------------------------------------- smoke: probe sandbox (I1), không mạng

SMOKE_SCRIPT = SCRIPT.with_name("smoke_portable.py")


def _load_smoke() -> ModuleType:
    spec = importlib.util.spec_from_file_location("smoke_portable", SMOKE_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


sp = _load_smoke()


def test_zip_python_always_isolated(tmp_path: Path) -> None:
    command = sp.zip_python(tmp_path)
    assert command[0] == str(tmp_path / "python" / "python.exe")
    assert command[1:] == ["-I", "-X", "utf8"]


def test_swap_child_script_replaces_exactly_the_child(tmp_path: Path) -> None:
    child = tmp_path / "sandbox" / "_sandbox_child.py"
    args = ["python.exe", "-S", "-s", "-P", str(child)]
    assert sp.swap_child_script(args, str(child), "probe.py") == ["python.exe", "-S", "-s", "-P", "probe.py"]


@pytest.mark.parametrize("count", [0, 2])
def test_swap_child_script_needs_exactly_one_child(tmp_path: Path, count: int) -> None:
    child = str(tmp_path / "_sandbox_child.py")
    with pytest.raises(sp.SmokeError, match="đúng một lần CHILD_SCRIPT"):
        sp.swap_child_script(["python.exe", "-S", *([child] * count)], child, "probe.py")


def test_parse_prefixed_takes_last_marked_line() -> None:
    output = 'noise\nHOLOCRON_PROBE:{"a": 1}\nmore\nHOLOCRON_PROBE:{"a": 2}\n'
    assert sp.parse_prefixed(output, sp.PROBE_PREFIX) == {"a": 2}
    assert sp.parse_prefixed("nothing", sp.PROBE_PREFIX) is None
    with pytest.raises(sp.SmokeError):
        sp.parse_prefixed("HOLOCRON_PROBE:[1]", sp.PROBE_PREFIX)


def _capture(python_dir: Path, **probe_changes: object) -> dict[str, object]:
    probe: dict[str, object] = {
        "no_site": 1,
        "site_loaded": False,
        "hash_randomization": 0,
        "path": [str(python_dir / "python314.zip"), str(python_dir), str(python_dir / "Lib")],
        "imports": dict.fromkeys(sp.FORBIDDEN_IN_SANDBOX, "ImportError"),
    }
    probe.update(probe_changes)
    return {"template_status": "ok", "probe": probe, "probe_returncode": 0, "probe_stderr": ""}


def test_evaluate_probe_accepts_stdlib_only_child(tmp_path: Path) -> None:
    assert sp.evaluate_probe(_capture(tmp_path / "python"), tmp_path / "python") == []


def test_evaluate_probe_flags_old_pth_layout(tmp_path: Path) -> None:
    """Bố cục cũ (`._pth` có `import site`): site chạy dù có `-S`, `app/` và `site-packages/` lọt vào sandbox."""
    python_dir = tmp_path / "python"
    capture = _capture(
        python_dir,
        no_site=0,
        site_loaded=True,
        path=[str(python_dir / "python314.zip"), str(tmp_path / "app"), str(tmp_path / "site-packages")],
        imports={"holocron_core": "imported", "fastapi": "imported", "pydantic": "imported", "numpy": "ImportError"},
    )
    problems = " | ".join(sp.evaluate_probe(capture, python_dir))
    for fragment in ("no_site", "'site'", "import holocron_core", "import fastapi", "import pydantic", "app"):
        assert fragment in problems
    assert "import numpy" not in problems


@pytest.mark.parametrize(
    "change, fragment",
    [
        ({"hash_randomization": 1}, "hash_randomization"),
        ({"imports": {}}, "import holocron_core"),
        ({"path": []}, "không đọc được sys.path"),
        ({"path": [""]}, "ngoài"),
        ({"path": ["..\\x"]}, "ngoài"),
    ],
)
def test_evaluate_probe_rejects_each_violation(tmp_path: Path, change: dict[str, object], fragment: str) -> None:
    python_dir = tmp_path / "python"
    python_dir.mkdir()
    problems = sp.evaluate_probe(_capture(python_dir, **change), python_dir)
    assert any(fragment in problem for problem in problems), problems


def test_evaluate_probe_requires_probe_output_and_real_template_run(tmp_path: Path) -> None:
    capture: dict[str, object] = {"template_status": "crashed", "probe": None, "probe_returncode": 1}
    problems = sp.evaluate_probe(capture, tmp_path)
    assert any("run_template" in problem for problem in problems)
    assert any("không trả kết quả" in problem for problem in problems)


def test_capture_runs_the_real_sandbox_command_with_probe(tmp_path: Path) -> None:
    """`run_template` thật của `holocron_core.sandbox`: lệnh, env, cwd được bắt lúc chạy (không chép tay cờ)."""
    from holocron_core.sandbox import sandbox

    probe_script = tmp_path / "sandbox_probe.py"
    probe_script.write_text(sp.PROBE_SCRIPT, encoding="utf-8")
    capture = sp.capture_sandbox_probe(probe_script)
    args, probe_args, env = capture["args"], capture["probe_args"], capture["env"]
    assert isinstance(args, list) and isinstance(probe_args, list) and isinstance(env, dict)
    assert str(sandbox.CHILD_SCRIPT) in args and str(probe_script) in probe_args
    assert [arg for arg in args if arg != str(sandbox.CHILD_SCRIPT)] == [
        a for a in probe_args if a != str(probe_script)
    ]
    assert env.get("PYTHONHASHSEED") == "0"
    assert sandbox.subprocess.run.__name__ == "run"  # bọc đã được gỡ
    assert sp.evaluate_probe(capture, Path(sys.base_prefix)) == []
