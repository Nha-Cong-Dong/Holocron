"""Smoke test bản zip portable trên Windows (opt-in, chạy tay; kế hoạch đóng gói §7 bước 1–5, chưa gồm token Q1).

Cách dùng (gốc repo, bằng Python của `.venv`; cần mạng một lần để cài pytest vào thư mục overlay tạm):
  python scripts/smoke_portable.py dist/Holocron-<version>-win64.zip [--keep] [--only sandbox-probe]

Mọi lời gọi `python/python.exe` của zip đều có `-I` (như `Holocron.cmd`).
1. Giải zip (chống zip-slip) vào thư mục tạm có dấu. `python -I` của zip nạp `holocron_core` từ `app/` qua
   `holocron.pth`. Probe sandbox (BẮT BUỘC, I1): chạy `run_template` thật của `holocron_core.sandbox` với một template
   nhỏ, bắt nguyên `args`/`env`/`cwd` của `subprocess.run`, chạy lại đúng lệnh đó với script probe thay cho
   `CHILD_SCRIPT`; tiến trình probe phải có `no_site`, không nạp `site`, `hash_randomization == 0`, không import được
   `holocron_core`/`fastapi`/`pydantic`/`numpy`, và mọi phần tử `sys.path` nằm trong `python/` của zip.
2. RapidOCR thật (bản `full`): import `rapidocr`, `onnxruntime`, `cv2`, `shapely`; một lần suy luận trên ảnh có chữ tự
   tạo bằng adapter của app (model đóng sẵn, không tải mạng).
3. `Holocron.cmd --no-browser --data-dir <thư mục có dấu>`, đọc URL từ stdout. `GET /` có `__HOLOCRON_RUNTIME__`;
   `/vault/` 200; `/v1/khong-co` JSON 404; `/v1/runtime` 200; `Host: evil.example` ⇒ 403 (SKIP "chờ B4" trừ khi có
   `--expect-host-check`). Tải lên một PDF có lớp chữ (trang 1 có văn bản) và một PDF scan không có lớp chữ (bản
   `full`: `cross_check` là ok/mismatch với `secondary_engine = rapidocr`). Server và tiến trình con luôn được dừng
   (`finally`) trước khi xoá thư mục tạm.
4. Chạy `tests/test_sandbox_security.py` và `tests/test_pdf_input_limits.py` bằng `python -I` của zip (pytest ở
   overlay tạm ngoài zip), khẳng định `holocron_core` được nạp từ `app/` của zip.
5. Gửi Ctrl+Break, tiến trình thoát trong 10 s, không còn tiến trình con, DB SQLite mở lại được.
In PASS/FAIL từng mục; thoát mã 1 nếu có mục FAIL.
"""

from __future__ import annotations

import argparse
import http.client
import importlib
import importlib.metadata
import io
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import zlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from build_portable import BuildError, pip_command, pip_env, safe_extract

ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = ROOT / "holocron" / "backend" / "tests"
TEST_FILES = ("test_sandbox_security.py", "test_pdf_input_limits.py")
TEST_SUPPORT = ("__init__.py", "conftest.py", "pdf_samples.py")
ACCENTED_DIR = "Thư mục thử ố"
ACCENTED_DATA = "dữ liệu thử"
URL_LINE = re.compile(r"Holocron: http://127\.0\.0\.1:(\d+)/")
PDF_LINES = (
    "Holocron smoke test page one.",
    "The scheduler gives each ready process the processor for one time quantum.",
    "The scheduler gives each ready process the processor for one time quantum.",
    "The scheduler gives each ready process the processor for one time quantum.",
)
PROCESS_QUERY = (
    "Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and "
    "$_.ExecutablePath.StartsWith($env:HOLOCRON_SMOKE_ROOT, [System.StringComparison]::OrdinalIgnoreCase) } | "
    'ForEach-Object { "$($_.ProcessId)" }'
)
TESSERACT_QUERY = (
    "Get-CimInstance Win32_Process -Filter \"Name='tesseract.exe'\" | "
    'ForEach-Object { "$($_.ProcessId) $($_.ParentProcessId)" }'
)
PYTEST_CODE = """
import sys
from pathlib import Path
run_dir, overlay, app_dir, ini = sys.argv[1:5]
sys.path[0:0] = [overlay, run_dir]
import holocron_core
if not Path(holocron_core.__file__).resolve().is_relative_to(Path(app_dir).resolve()):
    print("holocron_core không nằm trong app/ của zip: " + str(holocron_core.__file__))
    sys.exit(3)
import pytest
sys.exit(pytest.main(["-q", "-p", "no:cacheprovider", "--rootdir", run_dir, "-c", ini, *sys.argv[5:]]))
"""
PAGE_OBJECT = (
    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>"
)
# Cờ của mọi lời gọi `python.exe` của zip (như `Holocron.cmd`).
ZIP_PYTHON_FLAGS = ("-I", "-X", "utf8")
APP_IMPORT_CODE = (
    "import json, sys, holocron_core; print(json.dumps({'file': holocron_core.__file__, 'path': sys.path}))"
)

# Probe sandbox: tiến trình con chạy đúng lệnh `run_template` dựng (bắt từ `subprocess.run` lúc chạy), chỉ thay
# `CHILD_SCRIPT` bằng script dưới đây. Script chỉ dùng thư viện chuẩn và in đúng một dòng có tiền tố.
SANDBOX_MODULE = "holocron_core.sandbox.sandbox"
PROBE_PREFIX = "HOLOCRON_PROBE:"
CAPTURE_PREFIX = "HOLOCRON_CAPTURE:"
FORBIDDEN_IN_SANDBOX = ("holocron_core", "fastapi", "pydantic", "numpy")
PROBE_SCRIPT = f"""
import json
import sys

result = {{
    "no_site": sys.flags.no_site,
    "site_loaded": "site" in sys.modules,
    "hash_randomization": sys.flags.hash_randomization,
    "path": list(sys.path),
    "imports": {{}},
}}
for name in {FORBIDDEN_IN_SANDBOX!r}:
    try:
        __import__(name)
        result["imports"][name] = "imported"
    except ImportError:
        result["imports"][name] = "ImportError"
    except Exception as exc:
        result["imports"][name] = type(exc).__name__
print({PROBE_PREFIX!r} + json.dumps(result))
"""
# Template hợp lệ nhỏ để `run_template` đi hết đường thật (ASTGuard ⇒ tiến trình con).
PROBE_TEMPLATE = (
    "def generate_params(seed):\n    return {'a': seed % 7 + 1}\n"
    "def validate(p):\n    assert p['a'] > 0\n"
    "def solve(p):\n    return {'value': p['a'], 'display': str(p['a']), 'explanation_vi': 'x'}\n"
    "def make_distractors(p, s):\n"
    "    return [{'misconception_code': 'A_B_C', 'value': p['a'] + i, 'display': str(p['a'] + i),"
    " 'rationale_vi': 'r'} for i in range(1, 4)]\n"
    "def render_stem(p):\n    return 'stem'\n"
)
# Chạy trong `python -I` của zip: nạp module này (thư mục scripts/ truyền qua argv) rồi gọi `capture_sandbox_probe`.
CAPTURE_CODE = f"""
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import smoke_portable
print({CAPTURE_PREFIX!r} + json.dumps(smoke_portable.capture_sandbox_probe(Path(sys.argv[2]))))
"""

# RapidOCR thật trong `python -I` của zip: vẽ chữ bằng OpenCV (không cần font), đọc bằng adapter của app (model đóng
# sẵn, đường dẫn tường minh, không tải mạng), ghi ảnh xám thô để dựng PDF scan.
SCAN_TEXT = "HOLOCRON 2026 OCR 31415"
SCAN_SIZE = (1240, 1754)  # A4 ở 150 dpi
OCR_CODE = f"""
import json, sys
from pathlib import Path
import cv2, numpy, onnxruntime, rapidocr, shapely
from holocron_core.infrastructure.document_readers.rapidocr_adapter import RapidOcrBackend
width, height = {SCAN_SIZE!r}
image = numpy.full((height, width), 255, dtype=numpy.uint8)
for row in range(3):
    cv2.putText(image, {SCAN_TEXT!r}, (90, 260 + row * 220), cv2.FONT_HERSHEY_SIMPLEX, 2.6, 0, 6, cv2.LINE_AA)
Path(sys.argv[1]).write_bytes(image.tobytes())
ok, png = cv2.imencode(".png", image)
backend = RapidOcrBackend.load()
lines = backend.read(png.tobytes(), width, height).lines if backend is not None and ok else []
print(json.dumps({{
    "modules": {{m.__name__: str(getattr(m, "__file__", "")) for m in (cv2, numpy, onnxruntime, rapidocr, shapely)}},
    "geos": shapely.geos_version_string,
    "loaded": backend is not None,
    "tokens": [token for line in lines for token in line.tokens],
}}))
"""


class SmokeError(Exception):
    """Lỗi dừng smoke test."""


@dataclass
class Report:
    results: list[tuple[str, bool, str]] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.results.append((name, ok, detail))
        print(f"{'PASS' if ok else 'FAIL'}  {name}{f' — {detail}' if detail else ''}", flush=True)
        return ok

    def skip(self, name: str, reason: str) -> None:
        """Mục chưa kiểm được vì phụ thuộc việc chưa merge; KHÔNG tính PASS hay FAIL, luôn in kèm nhãn lý do."""
        self.skipped.append((name, reason))
        print(f"SKIP  {name} — {reason}", flush=True)

    @property
    def passed(self) -> bool:
        return all(ok for _, ok, _ in self.results)

    def summary(self) -> str:
        failed = sum(1 for _, ok, _ in self.results if not ok)
        text = f"Kết quả: {len(self.results) - failed}/{len(self.results)} PASS"
        return text + (f", {len(self.skipped)} SKIP" if self.skipped else "")


def parse_json(text: str) -> object | None:
    """JSON từ output tiến trình/HTTP; không phải JSON ⇒ None (bước gọi tự báo FAIL)."""
    try:
        value: object = json.loads(text)
    except json.JSONDecodeError:
        return None
    return value


def last_json_line(output: str) -> dict[str, object] | None:
    lines = output.strip().splitlines()
    value = parse_json(lines[-1]) if lines else None
    return {str(key): item for key, item in value.items()} if isinstance(value, dict) else None


# ---------------------------------------------------------------- tiện ích


def run_text(
    args: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None, timeout: float = 600
) -> tuple[int, str]:
    completed = subprocess.run(
        args,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    return completed.returncode, completed.stdout + completed.stderr


def powershell_lines(script: str, root: Path) -> list[str]:
    env = dict(os.environ)
    env["HOLOCRON_SMOKE_ROOT"] = str(root)
    code, output = run_text(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], env=env, timeout=60)
    if code != 0:
        raise SmokeError(f"powershell lỗi: {output.strip()[-400:]}")
    return [line.strip() for line in output.splitlines() if line.strip()]


def minimal_pdf(lines: tuple[str, ...]) -> bytes:
    """PDF một trang có lớp chữ (Helvetica), dựng bằng thư viện chuẩn."""

    def escape(text: str) -> str:
        return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    stream = "BT /F1 14 Tf 18 TL 56 780 Td " + " ".join(f"({escape(line)}) '" for line in lines) + " ET"
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        PAGE_OBJECT,
        f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{body}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("ascii")
    out += "".join(f"{offset:010d} 00000 n \n" for offset in offsets).encode("ascii")
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("ascii")
    return bytes(out)


def http_request(
    port: int, method: str, path: str, *, body: bytes | None = None, headers: dict[str, str] | None = None
) -> tuple[int, str, str]:
    """(status, content-type, body) của một request tới 127.0.0.1:<port>."""
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    try:
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        return response.status, response.getheader("Content-Type") or "", response.read().decode("utf-8", "replace")
    finally:
        connection.close()


def multipart(fields: dict[str, str], filename: str, content: bytes) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    parts = bytearray()
    for name, value in fields.items():
        parts += f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
    parts += (
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        "Content-Type: application/pdf\r\n\r\n"
    ).encode()
    parts += content + f"\r\n--{boundary}--\r\n".encode()
    return bytes(parts), f"multipart/form-data; boundary={boundary}"


# ---------------------------------------------------------------- tiến trình (Windows)


def start_server(root: Path, data_dir: Path) -> subprocess.Popen[bytes]:
    if sys.platform == "win32":
        return subprocess.Popen(
            [str(root / "Holocron.cmd"), "--no-browser", "--data-dir", str(data_dir)],
            cwd=root.parent,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
    raise SmokeError("smoke test chỉ chạy trên Windows")


def send_ctrl_break(process: subprocess.Popen[bytes]) -> None:
    if sys.platform == "win32":
        import signal

        os.kill(process.pid, signal.CTRL_BREAK_EVENT)
        return
    raise SmokeError("smoke test chỉ chạy trên Windows")


class OutputReader:
    """Đọc stdout của launcher ở luồng phụ; báo cổng khi gặp dòng `Holocron: http://127.0.0.1:<cổng>/`."""

    def __init__(self, process: subprocess.Popen[bytes]) -> None:
        self.lines: list[str] = []
        self.port: int | None = None
        self.ready = threading.Event()
        self._thread = threading.Thread(target=self._read, args=(process,), daemon=True)
        self._thread.start()

    def _read(self, process: subprocess.Popen[bytes]) -> None:
        assert process.stdout is not None
        for raw in process.stdout:
            line = raw.decode("utf-8", "replace").rstrip()
            self.lines.append(line)
            match = URL_LINE.search(line)
            if match is not None and self.port is None:
                self.port = int(match.group(1))
                self.ready.set()
        self.ready.set()


# ---------------------------------------------------------------- các bước


def zip_python(root: Path) -> list[str]:
    """Lệnh gọi `python.exe` của zip, luôn kèm `-I` như `Holocron.cmd`."""
    return [str(root / "python" / "python.exe"), *ZIP_PYTHON_FLAGS]


def normalized(path: str | Path) -> Path:
    """Đường dẫn tuyệt đối đã resolve và normcase (so sánh không phân biệt hoa thường trên Windows)."""
    return Path(os.path.normcase(str(Path(path).resolve())))


def is_inside(path: str | Path, root: str | Path) -> bool:
    return normalized(path).is_relative_to(normalized(root))


def swap_child_script(args: Sequence[str], child_script: str, probe: str) -> list[str]:
    """Lệnh của sandbox với đúng một phần tử `CHILD_SCRIPT` thay bằng script probe; không thấy hoặc thấy nhiều ⇒ lỗi."""
    target = normalized(child_script)
    positions = [index for index, arg in enumerate(args) if not arg.startswith("-") and normalized(arg) == target]
    if len(positions) != 1:
        raise SmokeError(f"lệnh sandbox phải chứa đúng một lần CHILD_SCRIPT ({child_script}): {list(args)}")
    swapped = list(args)
    swapped[positions[0]] = probe
    return swapped


def parse_prefixed(output: str, prefix: str) -> dict[str, object] | None:
    """Dòng cuối có tiền tố `prefix` ⇒ dict JSON; không có ⇒ None."""
    for line in reversed(output.splitlines()):
        if line.startswith(prefix):
            try:
                value = json.loads(line[len(prefix) :])
            except json.JSONDecodeError as exc:
                raise SmokeError(f"dữ liệu sau {prefix} không phải JSON: {exc}") from exc
            if not isinstance(value, dict):
                raise SmokeError(f"dữ liệu sau {prefix} không phải object JSON")
            return {str(key): item for key, item in value.items()}
    return None


def capture_sandbox_probe(probe_script: Path) -> dict[str, object]:
    """Chạy `run_template` thật; bọc `subprocess.run` của module sandbox để bắt nguyên `args`/`env`/`cwd` rồi chạy lại
    đúng lệnh đó (cùng kwargs, cùng `cwd` còn tồn tại) với `probe_script` thay cho `CHILD_SCRIPT`.

    Chạy bên trong `python -I` của zip (qua `CAPTURE_CODE`); không tự dựng lệnh hay môi trường nào.
    """
    module = importlib.import_module(SANDBOX_MODULE)
    child_script = str(module.CHILD_SCRIPT)
    process_module = module.subprocess
    real_run = process_module.run
    captured: dict[str, object] = {}

    def wrapper(args: Sequence[str], **kwargs: object) -> object:
        result = real_run(args, **kwargs)
        if "args" not in captured:
            probe_args = swap_child_script(args, child_script, str(probe_script))
            probe = real_run(probe_args, **{**kwargs, "input": ""})
            env = kwargs.get("env")
            captured.update(
                args=list(args),
                env=dict(env) if isinstance(env, dict) else None,
                cwd=str(kwargs.get("cwd")),
                probe_args=probe_args,
                probe_returncode=probe.returncode,
                probe=parse_prefixed(str(probe.stdout), PROBE_PREFIX),
                probe_stderr=str(probe.stderr)[-2000:],
            )
        return result

    process_module.run = wrapper
    try:
        outcome = module.run_template(PROBE_TEMPLATE, seed=7)
    finally:
        process_module.run = real_run
    captured["template_status"] = str(getattr(outcome.status, "value", outcome.status))
    captured["executable"] = sys.executable
    return captured


def evaluate_probe(capture: Mapping[str, object], python_dir: Path) -> list[str]:
    """Các vi phạm I1 trong kết quả probe (rỗng = đạt)."""
    problems: list[str] = []
    if capture.get("template_status") != "ok":
        problems.append(f"run_template không chạy hết đường thật: status={capture.get('template_status')}")
    probe = capture.get("probe")
    if not isinstance(probe, dict):
        tail = str(capture.get("probe_stderr", ""))[-300:]
        return [*problems, f"tiến trình probe không trả kết quả (mã {capture.get('probe_returncode')}): {tail}"]
    if probe.get("no_site") != 1:
        problems.append(f"sys.flags.no_site = {probe.get('no_site')} (cần 1)")
    if probe.get("site_loaded") is not False:
        problems.append("module 'site' đã được nạp trong tiến trình sandbox")
    if probe.get("hash_randomization") != 0:
        problems.append(f"sys.flags.hash_randomization = {probe.get('hash_randomization')} (cần 0)")
    imports = probe.get("imports")
    imports = imports if isinstance(imports, dict) else {}
    for name in FORBIDDEN_IN_SANDBOX:
        if imports.get(name) != "ImportError":
            problems.append(f"import {name} trong sandbox: {imports.get(name)} (cần ImportError)")
    paths = probe.get("path")
    if not isinstance(paths, list) or not paths:
        problems.append("không đọc được sys.path của tiến trình probe")
    else:
        outside = [str(entry) for entry in paths if not is_inside(str(entry), python_dir)]
        if outside:
            problems.append(f"sys.path có phần tử ngoài {python_dir}: {', '.join(outside)}")
    return problems


def run_sandbox_probe(root: Path, work: Path) -> tuple[dict[str, object] | None, str]:
    """Chạy `CAPTURE_CODE` bằng `python -I` của zip; trả (kết quả bắt được, output thô)."""
    probe_dir = work / "probe"
    probe_dir.mkdir(parents=True, exist_ok=True)
    probe_script = probe_dir / "sandbox_probe.py"
    probe_script.write_text(PROBE_SCRIPT, encoding="utf-8")
    args = [*zip_python(root), "-c", CAPTURE_CODE, str(Path(__file__).resolve().parent), str(probe_script)]
    code, output = run_text(args, cwd=probe_dir, timeout=120)
    capture = parse_prefixed(output, CAPTURE_PREFIX) if code == 0 else None
    return capture, output


def check_sandbox_probe(report: Report, root: Path, work: Path) -> None:
    capture, output = run_sandbox_probe(root, work)
    if capture is None:
        report.check("probe sandbox (I1) chạy được", False, output.strip()[-400:])
        return
    probe = capture.get("probe")
    print(f"  lệnh sandbox: {capture.get('args')}", flush=True)
    print(f"  env sandbox: {capture.get('env')}", flush=True)
    if isinstance(probe, dict):
        print(f"  sys.path trong sandbox: {probe.get('path')}", flush=True)
    problems = evaluate_probe(capture, root / "python")
    report.check("probe sandbox (I1): chỉ thư viện chuẩn trong python/, không site", not problems, "; ".join(problems))


def check_app_import(report: Report, root: Path) -> None:
    code, output = run_text([*zip_python(root), "-c", APP_IMPORT_CODE], timeout=120)
    if not report.check("python -I nạp được holocron_core", code == 0, output.strip()[-300:] if code else ""):
        return
    data = last_json_line(output)
    if not report.check("python -I in JSON hợp lệ", data is not None, output.strip()[-300:]):
        return
    assert data is not None
    print(f"  sys.path của python -I: {data.get('path')}", flush=True)
    location = str(data.get("file"))
    report.check("holocron_core nạp từ app/ của zip", is_inside(location, root / "app"), location)


def check_rapidocr(report: Report, root: Path, work: Path) -> Path | None:
    """RapidOCR thật; trả file ảnh xám thô của trang scan tự tạo (None nếu bước lỗi)."""
    gray = work / "scan.gray"
    code, output = run_text([*zip_python(root), "-c", OCR_CODE, str(gray)], cwd=work, timeout=300)
    if not report.check("import rapidocr/onnxruntime/cv2/shapely", code == 0, output.strip()[-400:] if code else ""):
        return None
    data = last_json_line(output)
    if not report.check("RapidOCR in JSON hợp lệ", data is not None, output.strip()[-300:]):
        return None
    assert data is not None
    modules = data.get("modules")
    modules = modules if isinstance(modules, dict) else {}
    outside = [str(name) for name, path in modules.items() if not is_inside(str(path), root)]
    report.check("module OCR nạp từ zip", bool(modules) and not outside, ", ".join(outside))
    print(f"  GEOS của shapely: {data.get('geos')}", flush=True)
    raw_tokens = data.get("tokens")
    tokens = [str(token) for token in raw_tokens] if isinstance(raw_tokens, list) else []
    report.check(
        "RapidOCR suy luận ảnh tự tạo (model đóng sẵn)",
        bool(data.get("loaded")) and any(token in tokens for token in ("2026", "31415")),
        f"loaded={data.get('loaded')}, token={tokens[:8]}",
    )
    return gray


HOST_CHECK_PENDING = "chờ B4: kiểm Host chưa có ở develop (bật bằng --expect-host-check khi B4 đã merge)"


def check_http(report: Report, port: int, *, expect_host_check: bool = False) -> None:
    status, kind, body = http_request(port, "GET", "/")
    report.check(
        "GET / trả HTML có __HOLOCRON_RUNTIME__", status == 200 and "__HOLOCRON_RUNTIME__" in body, f"{status}"
    )
    status, _, _ = http_request(port, "GET", "/vault/")
    report.check("GET /vault/ 200", status == 200, f"{status}")
    status, kind, body = http_request(port, "GET", "/v1/khong-co")
    report.check(
        "GET /v1/khong-co JSON 404", status == 404 and "json" in kind and '"detail"' in body, f"{status} {kind}"
    )
    status, kind, _ = http_request(port, "GET", "/v1/runtime")
    report.check("GET /v1/runtime 200", status == 200 and "json" in kind, f"{status}")
    if not expect_host_check:
        report.skip("Host: evil.example bị chặn 403", HOST_CHECK_PENDING)
        return
    status, _, _ = http_request(port, "GET", "/v1/runtime", headers={"Host": "evil.example"})
    report.check("Host: evil.example bị chặn 403", status == 403, f"{status}")


def json_object(text: str) -> dict[str, object] | None:
    value = parse_json(text)
    return {str(key): item for key, item in value.items()} if isinstance(value, dict) else None


def document_id_of(created: Mapping[str, object] | None) -> str | None:
    meta = created.get("meta") if created is not None else None
    doc_id = meta.get("doc_id") if isinstance(meta, dict) else None
    return doc_id if isinstance(doc_id, str) else None


def upload_and_read_page(report: Report, port: int, label: str, filename: str, content: bytes) -> dict[str, object]:
    """Tải PDF lên, chờ đọc xong (≤ 300 s), trả JSON trang 1 (rỗng nếu bước nào FAIL; JSON hỏng ⇒ FAIL)."""
    body, content_type = multipart(
        {"subject_code": "SMOKE101", "source_kind": "lecture_slides", "output_language": "vi"}, filename, content
    )
    status, _, text = http_request(port, "POST", "/v1/documents", body=body, headers={"Content-Type": content_type})
    document_id = document_id_of(json_object(text)) if status in (200, 202) else None
    if not report.check(f"POST /v1/documents nhận {label}", document_id is not None, f"{status} {text[:200]}"):
        return {}
    deadline = time.monotonic() + 300
    document_status = "processing"
    while document_status == "processing" and time.monotonic() < deadline:
        time.sleep(1)
        _, _, text = http_request(port, "GET", f"/v1/documents/{document_id}")
        document = json_object(text)
        document_status = str(document.get("status")) if document is not None else "JSON hỏng: " + text[:100]
    if not report.check(f"{label}: đọc xong trong 300 s", document_status != "processing", document_status):
        return {}
    status, _, text = http_request(port, "GET", f"/v1/documents/{document_id}/pages/1")
    page = json_object(text) if status == 200 else None
    if not report.check(f"{label}: GET trang 1", page is not None, f"{status} {text[:100]}"):
        return {}
    assert page is not None
    return page


def page_lines(page: Mapping[str, object]) -> list[str]:
    lines = page.get("lines")
    return [str(line.get("text", "")) for line in lines if isinstance(line, dict)] if isinstance(lines, list) else []


def check_upload(report: Report, port: int) -> None:
    page = upload_and_read_page(report, port, "PDF có lớp chữ", "smoke.pdf", minimal_pdf(PDF_LINES))
    if page:
        lines = page_lines(page)
        report.check("trang 1 có văn bản", any("Holocron" in line for line in lines), f"{len(lines)} dòng")


def image_pdf(gray: bytes, width: int, height: int) -> bytes:
    """PDF một trang A4 chỉ có một ảnh xám (FlateDecode), KHÔNG có lớp chữ (PDF scan), dựng bằng thư viện chuẩn."""
    if len(gray) != width * height:
        raise SmokeError(f"ảnh xám có {len(gray)} byte, cần {width * height}")
    data = zlib.compress(gray, 9)
    content = b"q 595 0 0 842 0 0 cm /Im1 Do Q"
    page = (
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R"
        b" /Resources << /XObject << /Im1 5 0 R >> >> >>"
    )
    image_header = (
        b"<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /DeviceGray /BitsPerComponent 8"
        b" /Filter /FlateDecode /Length %d >>\nstream\n" % (width, height, len(data))
    )
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        page,
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
        image_header + data + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("ascii")
    out += "".join(f"{offset:010d} 00000 n \n" for offset in offsets).encode("ascii")
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("ascii")
    return bytes(out)


def check_scan_upload(report: Report, port: int, gray: Path) -> None:
    width, height = SCAN_SIZE
    page = upload_and_read_page(report, port, "PDF scan", "scan.pdf", image_pdf(gray.read_bytes(), width, height))
    if not page:
        return
    ok, detail = scan_cross_check_verdict(page)
    report.check("PDF scan: cross_check ok/mismatch với lượt RapidOCR", ok, detail)


SCAN_CROSS_CHECK_STATUSES = ("ok", "mismatch")


def scan_cross_check_verdict(page: Mapping[str, object]) -> tuple[bool, str]:
    """Trang scan đạt khi `cross_check.status` ∈ {ok, mismatch} VÀ `secondary_engine == "rapidocr"`.

    `not_applicable` (trang không qua OCR, ví dụ đã failed), `unavailable` hay thiếu trường đều FAIL.
    """
    cross_check = page.get("cross_check")
    status = cross_check.get("status") if isinstance(cross_check, dict) else None
    engine = cross_check.get("secondary_engine") if isinstance(cross_check, dict) else None
    detail = f"tier={page.get('tier_used')}, cross_check={status}, engine={engine}, review={page.get('review_reasons')}"
    detail += f", lỗi={page.get('failure_reason')}" if page.get("failure_reason") else ""
    return status in SCAN_CROSS_CHECK_STATUSES and engine == "rapidocr", detail


def check_shutdown(report: Report, process: subprocess.Popen[bytes], root: Path, data_dir: Path) -> None:
    before = set(powershell_lines(PROCESS_QUERY, root))
    send_ctrl_break(process)
    try:
        process.wait(timeout=10)
        exited = True
    except subprocess.TimeoutExpired:
        exited = False
        process.kill()
        process.wait(timeout=10)
    report.check("Ctrl+Break: thoát trong 10 s", exited)
    time.sleep(1)
    leftover = powershell_lines(PROCESS_QUERY, root)
    report.check("không còn tiến trình chạy từ thư mục zip", not leftover, ", ".join(leftover))
    orphans = [line for line in powershell_lines(TESSERACT_QUERY, root) if line.split()[-1] in before]
    report.check("không còn tesseract.exe con", not orphans, ", ".join(orphans))
    databases = [p for p in data_dir.rglob("*") if p.is_file() and p.read_bytes()[:16] == b"SQLite format 3\x00"]
    healthy = []
    for database in databases:
        with sqlite3.connect(database) as connection:
            healthy.append(connection.execute("PRAGMA integrity_check").fetchone() == ("ok",))
    report.check("DB SQLite mở lại được", bool(databases) and all(healthy), f"{len(databases)} file")


def check_pytest(report: Report, root: Path, work: Path) -> None:
    overlay, run_dir = work / "overlay", work / "pytest-run"
    (run_dir / "tests").mkdir(parents=True)
    for name in (*TEST_SUPPORT, *TEST_FILES):
        shutil.copyfile(TESTS_DIR / name, run_dir / "tests" / name)
    ini = run_dir / "empty.ini"
    ini.write_text("[pytest]\n", encoding="utf-8")
    try:
        pytest_requirement = f"pytest=={importlib.metadata.version('pytest')}"
    except importlib.metadata.PackageNotFoundError:
        pytest_requirement = "pytest"
    pip = pip_command(sys.executable, "install", "--target", str(overlay), "--only-binary=:all:")
    code, output = run_text([*pip, pytest_requirement], env=pip_env(os.environ))
    if not report.check(f"cài {pytest_requirement} vào overlay tạm", code == 0, output.strip()[-300:] if code else ""):
        return
    args = [*zip_python(root), "-c", PYTEST_CODE, str(run_dir), str(overlay), str(root / "app"), str(ini)]
    code, output = run_text([*args, *(f"tests/{name}" for name in TEST_FILES)], cwd=run_dir)
    tail = output.strip().splitlines()[-1] if output.strip() else ""
    report.check("pytest sandbox + giới hạn PDF bằng python -I của zip", code == 0, tail)


STEPS = ("all", "sandbox-probe")


def extract_root(zip_path: Path, work: Path) -> Path:
    base = work / ACCENTED_DIR
    safe_extract(zip_path, base)
    roots = [path for path in base.iterdir() if path.is_dir()]
    if len(roots) != 1:
        raise SmokeError(f"zip phải có đúng một thư mục gốc, có {len(roots)}")
    print(f"Giải nén vào: {roots[0]}", flush=True)
    return roots[0]


def kill_tree(pid: int) -> None:
    """Dừng cưỡng bức tiến trình và mọi tiến trình con của nó (Windows: `taskkill /T /F`)."""
    if sys.platform == "win32":
        run_text(["taskkill", "/PID", str(pid), "/T", "/F"], timeout=30)
        return
    raise SmokeError("smoke test chỉ chạy trên Windows")


def stop_server(process: subprocess.Popen[bytes], root: Path) -> None:
    """Dọn trước khi xoá thư mục tạm: dừng cây tiến trình của launcher (nếu còn) và mọi tiến trình chạy từ thư mục zip.

    Gọi trong `finally`, nên không được ném lỗi che lỗi gốc; lỗi dọn dẹp chỉ in cảnh báo.
    """
    try:
        if process.poll() is None:
            kill_tree(process.pid)
            process.wait(timeout=10)
        for line in powershell_lines(PROCESS_QUERY, root):
            if line.isdigit():
                kill_tree(int(line))
    except (OSError, SmokeError, subprocess.SubprocessError) as exc:
        print(f"CẢNH BÁO  dọn tiến trình của smoke chưa xong: {exc}", file=sys.stderr, flush=True)


def smoke(zip_path: Path, work: Path, only: str = "all", *, expect_host_check: bool = False) -> Report:
    report = Report()
    root = extract_root(zip_path, work)
    check_sandbox_probe(report, root, work)
    if only == "sandbox-probe":
        return report
    check_app_import(report, root)
    gray: Path | None = None
    if (root / "site-packages" / "rapidocr").is_dir():
        gray = check_rapidocr(report, root, work)
    else:
        print("BỎ QUA  RapidOCR — bản lite (không có site-packages/rapidocr)", flush=True)

    data_dir = work / ACCENTED_DATA
    process = start_server(root, data_dir)
    try:
        reader = OutputReader(process)
        reader.ready.wait(timeout=60)
        if report.check("Holocron.cmd in URL trong 60 s", reader.port is not None, " | ".join(reader.lines[-5:])):
            assert reader.port is not None
            for line in reader.lines:
                print(f"  {line}", flush=True)
            check_http(report, reader.port, expect_host_check=expect_host_check)
            check_upload(report, reader.port)
            if gray is not None:
                check_scan_upload(report, reader.port, gray)
            check_shutdown(report, process, root, data_dir)
    finally:
        stop_server(process, root)

    check_pytest(report, root, work)
    return report


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Smoke test bản zip portable Holocron (Windows).")
    parser.add_argument("zip", help="đường dẫn Holocron-<version>-win64.zip")
    parser.add_argument("--keep", action="store_true", help="giữ thư mục tạm để xem lại")
    parser.add_argument("--only", choices=STEPS, default="all", help="sandbox-probe: chỉ chạy probe sandbox (I1)")
    parser.add_argument(
        "--expect-host-check",
        action="store_true",
        help="kiểm Host: evil.example ⇒ 403 (bật khi B4 đã merge; mặc định SKIP có nhãn 'chờ B4')",
    )
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if sys.platform != "win32":
        print("Smoke test chỉ chạy trên Windows.", file=sys.stderr)
        return 2
    work = Path(tempfile.mkdtemp(prefix="holocron-smoke-"))
    try:
        report = smoke(Path(args.zip).resolve(), work, args.only, expect_host_check=args.expect_host_check)
    except (BuildError, SmokeError, OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        print(f"LỖI smoke: {exc}", file=sys.stderr)
        return 1
    finally:
        if args.keep:
            print(f"Giữ thư mục tạm: {work}")
        else:
            shutil.rmtree(work, ignore_errors=True)
    print(report.summary())
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
