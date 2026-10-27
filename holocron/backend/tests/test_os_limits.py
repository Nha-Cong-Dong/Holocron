"""Module giới hạn OS dùng chung (`sandbox/os_limits.py`, ADR-008 mục 3).

Child của sandbox và worker đọc PDF cùng dùng module này; test ở đây kiểm nó chỉ phụ thuộc thư viện chuẩn và
giới hạn thật sự có hiệu lực trong một tiến trình con (không áp lên tiến trình pytest).
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

from holocron_core.sandbox import os_limits
from holocron_core.sandbox.sandbox import CHILD_SCRIPT

OS_LIMITS_FILE = Path(os_limits.__file__)

_PROBE = """
import importlib.util, sys
spec = importlib.util.spec_from_file_location("probe_os_limits", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.apply_limits(256 * 1024 * 1024, 5)
try:
    blob = bytearray(768 * 1024 * 1024)
    print("ALLOCATED", len(blob))
except MemoryError:
    print("MEM_BLOCKED")
"""


def test_os_limits_only_imports_stdlib() -> None:
    tree = ast.parse(OS_LIMITS_FILE.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "os_limits không được import tương đối"
            roots.add((node.module or "").split(".")[0])
    assert roots <= set(sys.stdlib_module_names)


def test_os_limits_sits_next_to_sandbox_child() -> None:
    """Child chạy `python -S -P` nạp module theo đường dẫn cạnh nó; đổi chỗ file là phá sandbox."""
    assert OS_LIMITS_FILE.parent == CHILD_SCRIPT.parent
    assert callable(os_limits.apply_limits)


def test_apply_limits_blocks_large_allocation_in_child_process(tmp_path: Path) -> None:
    proc = subprocess.run(
        [sys.executable, "-S", "-c", _PROBE, str(OS_LIMITS_FILE)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        cwd=tmp_path,
        check=False,
    )
    assert "ALLOCATED" not in proc.stdout
    assert "MEM_BLOCKED" in proc.stdout, proc.stderr[-500:]


@pytest.mark.skipif(sys.platform == "win32", reason="RLIMIT_FSIZE chỉ có trên Unix")
def test_apply_limits_blocks_file_writes_on_unix(tmp_path: Path) -> None:
    probe = (
        "import importlib.util, signal, sys\n"
        "signal.signal(signal.SIGXFSZ, signal.SIG_IGN)\n"
        "spec = importlib.util.spec_from_file_location('probe_os_limits', sys.argv[1])\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(module)\n"
        "module.apply_limits(256 * 1024 * 1024, 5)\n"
        "try:\n"
        "    with open('out.bin', 'wb') as fh:\n"
        "        fh.write(b'x' * 4096)\n"
        "        fh.flush()\n"
        "    print('WROTE')\n"
        "except OSError:\n"
        "    print('WRITE_BLOCKED')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-S", "-c", probe, str(OS_LIMITS_FILE)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        cwd=tmp_path,
        check=False,
    )
    assert "WROTE" not in proc.stdout
    assert "WRITE_BLOCKED" in proc.stdout
