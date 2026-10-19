"""Sandbox thực thi Executable Template.

Ba lớp phòng thủ (defense in depth):
  L1. ASTGuard   — phân tích tĩnh, từ chối trước khi spawn tiến trình.
  L2. Runtime    — tiến trình con riêng, builtins rút gọn, module proxy không lộ module con.
  L3. OS limits  — RLIMIT_AS (RAM), RLIMIT_CPU, RLIMIT_FSIZE=0, wall-clock timeout, env rỗng.

Production nên bọc thêm L4 (nsjail / gVisor / seccomp-bpf, --network=none).
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tempfile
import time
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

CHILD_SCRIPT = Path(__file__).with_name("_sandbox_child.py")
RESULT_PREFIX = "HOLOCRON_RESULT:"

ALLOWED_IMPORTS: frozenset[str] = frozenset(
    {"random", "math", "fractions", "itertools", "functools", "collections", "statistics"}
)
FORBIDDEN_CALL_NAMES: frozenset[str] = frozenset(
    {
        "eval",
        "exec",
        "compile",
        "open",
        "__import__",
        "globals",
        "locals",
        "vars",
        "dir",
        "getattr",
        "setattr",
        "delattr",
        "hasattr",
        "input",
        "breakpoint",
        "help",
        "exit",
        "quit",
        "memoryview",
        "bytearray",
        "type",
        "object",
        "super",
        "classmethod",
        "staticmethod",
        "property",
        "id",
        "hash",
        "print",
    }
)
FORBIDDEN_ATTR_NAMES: frozenset[str] = frozenset({"format", "format_map", "mro"})
FORBIDDEN_ATTR_PREFIXES: tuple[str, ...] = ("_", "gi_", "cr_", "ag_", "f_", "tb_", "co_")
FORBIDDEN_NODE_TYPES: tuple[type[ast.AST], ...] = (
    ast.ClassDef,
    ast.AsyncFunctionDef,
    ast.AsyncFor,
    ast.AsyncWith,
    ast.Await,
    ast.Global,
    ast.With,
    ast.Try,
    ast.TryStar,
    ast.Raise,
    ast.Delete,
)
MAX_SOURCE_BYTES = 20_000
MAX_AST_NODES = 6_000
MAX_INT_LITERAL = 10**9


class SandboxLimits(BaseModel):
    model_config = ConfigDict(frozen=True)
    wall_timeout_s: float = Field(default=2.0, gt=0, le=2.0)
    cpu_seconds: int = Field(default=2, ge=1, le=2)
    mem_bytes: int = Field(default=256 * 1024 * 1024, ge=64 * 1024 * 1024, le=512 * 1024 * 1024)


class SandboxStatus(str, Enum):
    OK = "ok"
    AST_REJECTED = "ast_rejected"
    ASSERTION_FAILED = "assertion_failed"
    RUNTIME_ERROR = "runtime_error"
    CONTRACT_ERROR = "contract_error"
    TIMEOUT = "timeout"
    MEMORY_EXCEEDED = "memory_exceeded"
    CRASHED = "crashed"


class SandboxResult(BaseModel):
    status: SandboxStatus
    seed: int
    duration_ms: int
    stage: str | None = None
    message: str | None = None
    line: int | None = None
    violations: list[str] = Field(default_factory=list)
    params: dict[str, Any] | None = None
    solution: dict[str, Any] | None = None
    distractors: list[dict[str, Any]] | None = None
    stem: str | None = None

    @property
    def is_seed_problem(self) -> bool:
        """Lỗi do bộ số ngẫu nhiên (re-roll seed là đủ), không phải lỗi code."""
        return self.status is SandboxStatus.ASSERTION_FAILED


class ASTGuard(ast.NodeVisitor):
    """Whitelist-first: chỉ cho phép tập con Python thuần tính toán."""

    def __init__(self) -> None:
        self.violations: list[str] = []
        self.node_count = 0

    def _flag(self, node: ast.AST, msg: str) -> None:
        self.violations.append(f"dòng {getattr(node, 'lineno', '?')}: {msg}")

    def generic_visit(self, node: ast.AST) -> None:
        self.node_count += 1
        if isinstance(node, FORBIDDEN_NODE_TYPES):
            self._flag(node, f"cú pháp bị cấm: {type(node).__name__}")
        super().generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name not in ALLOWED_IMPORTS:
                self._flag(node, f"import bị cấm: {alias.name}")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level != 0 or node.module not in ALLOWED_IMPORTS:
            self._flag(node, f"from-import bị cấm: {node.module}")
        for alias in node.names:
            if alias.name == "*" or alias.name.startswith("_"):
                self._flag(node, f"tên import bị cấm: {alias.name}")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id.startswith("__"):
            self._flag(node, f"tên dunder bị cấm: {node.id}")
        if node.id in FORBIDDEN_CALL_NAMES:
            self._flag(node, f"builtin bị cấm: {node.id}")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr.startswith(FORBIDDEN_ATTR_PREFIXES) or node.attr in FORBIDDEN_ATTR_NAMES:
            self._flag(node, f"thuộc tính bị cấm: .{node.attr}")
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, int) and not isinstance(node.value, bool) and abs(node.value) > MAX_INT_LITERAL:
            self._flag(node, f"hằng số quá lớn: {node.value}")
        if isinstance(node.value, (bytes, str)) and len(node.value) > 2_000:
            self._flag(node, "chuỗi literal quá dài")
        self.generic_visit(node)

    def visit_BinOp(self, node: ast.BinOp) -> None:
        if isinstance(node.op, ast.Pow) and not (
            isinstance(node.right, ast.Constant) and isinstance(node.right.value, int) and 0 <= node.right.value <= 64
        ):
            self._flag(node, "lũy thừa chỉ cho phép số mũ hằng 0..64")
        self.generic_visit(node)

    @classmethod
    def check(cls, source: str) -> list[str]:
        if len(source.encode("utf-8")) > MAX_SOURCE_BYTES:
            return [f"mã nguồn vượt {MAX_SOURCE_BYTES} bytes"]
        try:
            tree = ast.parse(source, mode="exec")
        except SyntaxError as exc:
            return [f"dòng {exc.lineno}: SyntaxError: {exc.msg}"]
        guard = cls()
        guard.visit(tree)
        if guard.node_count > MAX_AST_NODES:
            guard.violations.append(f"AST quá lớn: {guard.node_count} node")
        return guard.violations


def _child_env() -> dict[str, str]:
    env = {"PYTHONHASHSEED": "0", "PYTHONIOENCODING": "utf-8"}
    if sys.platform == "win32":
        env["SYSTEMROOT"] = os.environ.get("SYSTEMROOT", r"C:\Windows")  # Windows không khởi động được Python nếu thiếu
    return env


def run_template(
    source: str,
    seed: int,
    limits: SandboxLimits | None = None,
    params_override: dict[str, Any] | None = None,
) -> SandboxResult:
    """Chạy đủ hợp đồng (generate→validate→solve→distractors→stem).

    params_override != None: chế độ self-solve — bỏ qua generate_params/validate,
    chỉ gọi solve(params) trên dữ liệu đề thật đã qua Ingestion Gate.
    """
    limits = limits or SandboxLimits()
    started = time.perf_counter()

    def elapsed() -> int:
        return int((time.perf_counter() - started) * 1000)

    violations = ASTGuard.check(source)
    if violations:
        return SandboxResult(status=SandboxStatus.AST_REJECTED, seed=seed, duration_ms=elapsed(), violations=violations)

    request = json.dumps(
        {
            "source": source,
            "seed": seed,
            "params": params_override,
            "mem_bytes": limits.mem_bytes,
            "cpu_seconds": limits.cpu_seconds,
        }
    )
    with tempfile.TemporaryDirectory(prefix="holocron_sbx_") as workdir:
        try:
            proc = subprocess.run(
                [sys.executable, "-S", "-s", "-P", str(CHILD_SCRIPT)],
                input=request,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
                timeout=limits.wall_timeout_s,
                cwd=workdir,
                env=_child_env(),
                close_fds=True,
            )
        except subprocess.TimeoutExpired:
            return SandboxResult(
                status=SandboxStatus.TIMEOUT,
                seed=seed,
                duration_ms=elapsed(),
                message=f"vượt {limits.wall_timeout_s}s wall-clock",
            )

    for raw in reversed(proc.stdout.splitlines()):
        if raw.startswith(RESULT_PREFIX):
            payload = json.loads(raw[len(RESULT_PREFIX) :])
            status = SandboxStatus(payload.pop("status"))
            return SandboxResult(status=status, seed=seed, duration_ms=elapsed(), **payload)

    killed_by_cpu = proc.returncode in (-24, -9)
    return SandboxResult(
        status=SandboxStatus.TIMEOUT if killed_by_cpu else SandboxStatus.CRASHED,
        seed=seed,
        duration_ms=elapsed(),
        message=f"returncode={proc.returncode}; stderr={proc.stderr[-500:]}",
    )
