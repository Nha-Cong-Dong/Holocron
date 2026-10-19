"""Cưỡng chế bảng phân tầng package (CLAUDE.md §2.2) bằng cách phân tích `import` qua `ast`."""

import ast
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parent.parent / "holocron_core"
LAYERS = ("domain", "sandbox", "infrastructure", "services", "routers")

# tầng -> các tên import bị cấm (tầng nội bộ hoặc thư viện ngoài)
FORBIDDEN: dict[str, frozenset[str]] = {
    "domain": frozenset({"sandbox", "infrastructure", "services", "routers", "fastapi", "starlette"}),
    "sandbox": frozenset({"services", "routers", "infrastructure", "fastapi", "starlette", "httpx", "requests"}),
    "infrastructure": frozenset({"services", "routers", "fastapi", "starlette"}),
    "services": frozenset({"routers", "fastapi", "starlette"}),
    "routers": frozenset(),
}


def _imported_roots(path: Path, layer: str) -> set[str]:
    """Tên cấp cao nhất được import: tầng nội bộ (qua import tương đối/tuyệt đối) hoặc thư viện ngoài."""
    roots: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                roots.add(parts[1] if parts[0] == "holocron_core" and len(parts) > 1 else parts[0])
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level >= 2:  # `from ..x import y` đi lên khỏi tầng hiện tại
                roots.add(module.split(".")[0] if module else "")
                if not module:
                    roots.update(alias.name for alias in node.names)
            elif node.level == 0:
                parts = module.split(".")
                roots.add(parts[1] if parts[0] == "holocron_core" and len(parts) > 1 else parts[0])
    return roots - {""}


def _python_files(layer: str) -> list[Path]:
    return sorted((PACKAGE_ROOT / layer).rglob("*.py"))


@pytest.mark.parametrize("layer", LAYERS)
def test_layer_has_files(layer: str) -> None:
    assert _python_files(layer), f"không tìm thấy mã nguồn của tầng {layer}"


@pytest.mark.parametrize("layer", LAYERS)
def test_layer_does_not_import_forbidden_modules(layer: str) -> None:
    violations = [
        f"{path.relative_to(PACKAGE_ROOT)} import {sorted(bad)}"
        for path in _python_files(layer)
        if (bad := _imported_roots(path, layer) & FORBIDDEN[layer])
    ]
    assert not violations, "vi phạm phân tầng:\n" + "\n".join(violations)


def test_domain_only_uses_stdlib_and_pydantic() -> None:
    import sys

    allowed = set(sys.stdlib_module_names) | {"pydantic", "holocron_core", "__future__"}
    violations = [
        f"{path.relative_to(PACKAGE_ROOT)}: {name}"
        for path in _python_files("domain")
        for name in _imported_roots(path, "domain")
        if name not in allowed
    ]
    assert not violations, "domain chỉ được dùng thư viện chuẩn và pydantic:\n" + "\n".join(violations)


def test_detector_catches_a_violation(tmp_path: Path) -> None:
    """Kiểm tra bản thân bộ phát hiện: một file giả lập vi phạm phải bị bắt."""
    bad = tmp_path / "bad.py"
    bad.write_text("from ..routers.api import app\nimport fastapi\n", encoding="utf-8")
    assert {"routers", "fastapi"} <= _imported_roots(bad, "services")
