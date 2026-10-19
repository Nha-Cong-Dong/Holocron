import pytest

from holocron_core.sandbox import ASTGuard, SandboxLimits, SandboxStatus, run_template

ENTRY_TAIL = (
    "\ndef validate(p):\n    assert True\n"
    "def solve(p):\n    return {'value': 1, 'display': '1', 'explanation_vi': 'x'}\n"
    "def make_distractors(p, s):\n    return [{'misconception_code': 'A_B_C', 'value': i, 'display': str(i + 2),"
    " 'rationale_vi': 'r'} for i in range(3)]\n"
    "def render_stem(p):\n    return 'stem'\n"
)


@pytest.mark.parametrize(
    "snippet, expected_fragment",
    [
        ("import os", "import bị cấm: os"),
        ("import subprocess", "import bị cấm: subprocess"),
        ("import socket", "import bị cấm: socket"),
        ("from sys import modules", "from-import bị cấm: sys"),
        ("x = open('/etc/passwd').read()", "builtin bị cấm: open"),
        ("x = eval('1+1')", "builtin bị cấm: eval"),
        ("x = ().__class__.__bases__[0].__subclasses__()", "thuộc tính bị cấm: .__class__"),
        ("import fractions\nx = fractions._sys", "thuộc tính bị cấm: ._sys"),
        ("g = (i for i in [1])\nx = g.gi_frame", "thuộc tính bị cấm: .gi_frame"),
        ("x = '{0.__class__}'.format(1)", "thuộc tính bị cấm: .format"),
        ("x = 10 ** 10 ** 10", "lũy thừa chỉ cho phép"),
        ("class A:\n    pass", "cú pháp bị cấm: ClassDef"),
        ("x = __builtins__", "tên dunder bị cấm"),
    ],
)
def test_ast_guard_blocks(snippet, expected_fragment):
    violations = ASTGuard.check(snippet + "\ndef generate_params(seed):\n    return {}\n" + ENTRY_TAIL)
    assert any(expected_fragment in v for v in violations), violations


def test_module_proxy_hides_submodules():
    src = "import fractions\ndef generate_params(seed):\n    return {'leak': str(fractions.sys)}\n" + ENTRY_TAIL
    result = run_template(src, seed=1)
    assert result.status is SandboxStatus.RUNTIME_ERROR
    assert "AttributeError" in result.message


def test_infinite_loop_times_out():
    src = "def generate_params(seed):\n    while True:\n        seed += 1\n" + ENTRY_TAIL
    result = run_template(src, seed=1, limits=SandboxLimits(wall_timeout_s=1.0, cpu_seconds=1))
    assert result.status is SandboxStatus.TIMEOUT
    assert result.duration_ms < 2_500


def test_memory_bomb_is_contained():
    src = "def generate_params(seed):\n    x = [0] * (400 * 1000 * 1000)\n    return {}\n" + ENTRY_TAIL
    result = run_template(src, seed=1, limits=SandboxLimits(mem_bytes=128 * 1024 * 1024))
    assert result.status in (SandboxStatus.MEMORY_EXCEEDED, SandboxStatus.CRASHED)


def test_assertion_failure_is_seed_problem():
    src = "def generate_params(seed):\n    return {'s': seed}\n" + ENTRY_TAIL.replace(
        "assert True", "assert p['s'] % 2 == 0, 'EVEN_ONLY'"
    )
    odd = run_template(src, seed=3)
    assert odd.status is SandboxStatus.ASSERTION_FAILED and odd.message == "EVEN_ONLY" and odd.is_seed_problem
    assert run_template(src, seed=4).status is SandboxStatus.OK
