"""Smoke test bản zip portable trên Windows (opt-in, chạy tay; kế hoạch đóng gói §7 bước 1–5, chưa gồm token Q1).

Cách dùng (gốc repo, bằng Python của `.venv`; cần mạng một lần để cài pytest vào thư mục overlay tạm):
  python scripts/smoke_portable.py dist/Holocron-<version>-win64.zip [--keep]

1. Giải zip (chống zip-slip) vào thư mục tạm có dấu, chạy `Holocron.cmd --no-browser --data-dir <thư mục có dấu>`,
   đọc URL từ stdout; kiểm `python.exe -S -s -P` của zip chỉ thấy thư viện chuẩn (như tiến trình con sandbox).
2. `GET /` có `__HOLOCRON_RUNTIME__`; `/vault/` 200; `/v1/khong-co` JSON 404; `/v1/runtime` 200;
   `Host: evil.example` ⇒ 403.
3. Tải lên một PDF tự tạo có lớp chữ, chờ đọc xong, trang 1 có văn bản.
4. Chạy `tests/test_sandbox_security.py` và `tests/test_pdf_input_limits.py` bằng `python.exe` của zip (pytest ở
   overlay tạm ngoài zip), khẳng định `holocron_core` được nạp từ `app/` của zip.
5. Gửi Ctrl+Break, tiến trình thoát trong 10 s, không còn tiến trình con, DB SQLite mở lại được.
In PASS/FAIL từng mục; thoát mã 1 nếu có mục FAIL.
"""

from __future__ import annotations

import argparse
import http.client
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
from dataclasses import dataclass, field
from pathlib import Path

from build_portable import BuildError, safe_extract

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
SYS_PATH_CODE = "import json, sys; print(json.dumps(sys.path))"


class SmokeError(Exception):
    """Lỗi dừng smoke test."""


@dataclass
class Report:
    results: list[tuple[str, bool, str]] = field(default_factory=list)

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.results.append((name, ok, detail))
        print(f"{'PASS' if ok else 'FAIL'}  {name}{f' — {detail}' if detail else ''}", flush=True)
        return ok

    @property
    def passed(self) -> bool:
        return all(ok for _, ok, _ in self.results)


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


def check_sandbox_path(report: Report, root: Path) -> None:
    python = root / "python" / "python.exe"
    code, output = run_text([str(python), "-S", "-s", "-P", "-c", SYS_PATH_CODE], timeout=60)
    if not report.check("python -S -s -P chạy được", code == 0, output.strip()[-300:] if code else ""):
        return
    entries = [Path(entry) for entry in json.loads(output.strip().splitlines()[-1])]
    extra = [
        str(entry)
        for entry in entries
        if entry.name.lower() == "site-packages" or entry.resolve() == (root / "app").resolve()
    ]
    report.check("sandbox (-S) chỉ thấy thư viện chuẩn", not extra, ", ".join(extra))


def check_http(report: Report, port: int) -> None:
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
    status, _, _ = http_request(port, "GET", "/v1/runtime", headers={"Host": "evil.example"})
    report.check("Host: evil.example bị chặn 403", status == 403, f"{status}")


def check_upload(report: Report, port: int) -> None:
    body, content_type = multipart(
        {"subject_code": "SMOKE101", "source_kind": "lecture_slides", "output_language": "vi"},
        "smoke.pdf",
        minimal_pdf(PDF_LINES),
    )
    status, _, text = http_request(port, "POST", "/v1/documents", body=body, headers={"Content-Type": content_type})
    if not report.check("POST /v1/documents nhận PDF", status in (200, 202), f"{status} {text[:200]}"):
        return
    document_id = str(json.loads(text)["meta"]["doc_id"])
    deadline = time.monotonic() + 180
    document_status = "processing"
    while document_status == "processing" and time.monotonic() < deadline:
        time.sleep(1)
        _, _, text = http_request(port, "GET", f"/v1/documents/{document_id}")
        document_status = str(json.loads(text).get("status"))
    if not report.check("tài liệu đọc xong trong 180 s", document_status != "processing", document_status):
        return
    status, _, text = http_request(port, "GET", f"/v1/documents/{document_id}/pages/1")
    lines = [str(line.get("text", "")) for line in json.loads(text).get("lines", [])] if status == 200 else []
    report.check("trang 1 có văn bản", any("Holocron" in line for line in lines), f"{status}, {len(lines)} dòng")


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
    pip = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--target", str(overlay)]
    code, output = run_text([*pip, "--only-binary=:all:", pytest_requirement])
    if not report.check(f"cài {pytest_requirement} vào overlay tạm", code == 0, output.strip()[-300:] if code else ""):
        return
    python = root / "python" / "python.exe"
    args = [str(python), "-X", "utf8", "-c", PYTEST_CODE, str(run_dir), str(overlay), str(root / "app"), str(ini)]
    code, output = run_text([*args, *(f"tests/{name}" for name in TEST_FILES)], cwd=run_dir)
    tail = output.strip().splitlines()[-1] if output.strip() else ""
    report.check("pytest sandbox + giới hạn PDF bằng python.exe của zip", code == 0, tail)


def smoke(zip_path: Path, work: Path) -> Report:
    report = Report()
    base = work / ACCENTED_DIR
    data_dir = work / ACCENTED_DATA
    safe_extract(zip_path, base)
    roots = [path for path in base.iterdir() if path.is_dir()]
    if len(roots) != 1:
        raise SmokeError(f"zip phải có đúng một thư mục gốc, có {len(roots)}")
    root = roots[0]
    print(f"Giải nén vào: {root}", flush=True)
    check_sandbox_path(report, root)

    process = start_server(root, data_dir)
    reader = OutputReader(process)
    reader.ready.wait(timeout=60)
    if report.check("Holocron.cmd in URL trong 60 s", reader.port is not None, " | ".join(reader.lines[-5:])):
        assert reader.port is not None
        check_http(report, reader.port)
        check_upload(report, reader.port)
        check_shutdown(report, process, root, data_dir)
    elif process.poll() is None:
        process.kill()
        process.wait(timeout=10)

    check_pytest(report, root, work)
    return report


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Smoke test bản zip portable Holocron (Windows).")
    parser.add_argument("zip", help="đường dẫn Holocron-<version>-win64.zip")
    parser.add_argument("--keep", action="store_true", help="giữ thư mục tạm để xem lại")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if sys.platform != "win32":
        print("Smoke test chỉ chạy trên Windows.", file=sys.stderr)
        return 2
    work = Path(tempfile.mkdtemp(prefix="holocron-smoke-"))
    try:
        report = smoke(Path(args.zip).resolve(), work)
    except (BuildError, SmokeError, OSError, subprocess.SubprocessError) as exc:
        print(f"LỖI smoke: {exc}", file=sys.stderr)
        return 1
    finally:
        if args.keep:
            print(f"Giữ thư mục tạm: {work}")
        else:
            shutil.rmtree(work, ignore_errors=True)
    failed = [name for name, ok, _ in report.results if not ok]
    print(f"Kết quả: {len(report.results) - len(failed)}/{len(report.results)} PASS")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
