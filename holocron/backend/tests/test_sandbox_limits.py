"""Biên của ASTGuard và hợp đồng đầu ra: nguồn quá lớn, lỗi cú pháp, quá nhiều node, kiểu đầu ra sai."""

from holocron_core.sandbox.sandbox import (
    MAX_AST_NODES,
    MAX_SOURCE_BYTES,
    ASTGuard,
    SandboxStatus,
    run_template,
)


def test_oversize_source_is_rejected_before_parsing() -> None:
    violations = ASTGuard.check("x = 1\n" + "#" * MAX_SOURCE_BYTES)
    assert violations == [f"mã nguồn vượt {MAX_SOURCE_BYTES} bytes"]


def test_syntax_error_is_reported_as_violation() -> None:
    violations = ASTGuard.check("def broken(:\n    pass\n")
    assert len(violations) == 1 and "SyntaxError" in violations[0]


def test_too_many_ast_nodes_is_rejected() -> None:
    source = "x = [" + ",".join(["1"] * (MAX_AST_NODES + 100)) + "]\n"
    assert len(source.encode("utf-8")) < MAX_SOURCE_BYTES
    assert any("AST quá lớn" in v for v in ASTGuard.check(source))


def test_run_template_returns_ast_rejected_without_spawning() -> None:
    result = run_template("import os\n" + "#" * 60, seed=1)
    assert result.status is SandboxStatus.AST_REJECTED
    assert result.violations


def test_oversize_int_literal_and_pow_are_rejected() -> None:
    assert any("hằng số quá lớn" in v for v in ASTGuard.check("x = 10 ** 3\ny = 99999999999\n"))
    assert any("lũy thừa" in v for v in ASTGuard.check("def f(a, b):\n    return a ** b\n"))


def test_forbidden_syntax_and_names_are_flagged() -> None:
    source = "class A:\n    pass\ntry:\n    pass\nexcept Exception:\n    pass\nx = eval('1')\ny = x.__class__\n"
    joined = " | ".join(ASTGuard.check(source))
    for needle in ("ClassDef", "Try", "eval", "__class__"):
        assert needle in joined


def test_seed_problem_property_only_for_assertion_failed() -> None:
    from holocron_core.sandbox.sandbox import SandboxResult

    assert SandboxResult(status=SandboxStatus.ASSERTION_FAILED, seed=1, duration_ms=0).is_seed_problem
    assert not SandboxResult(status=SandboxStatus.RUNTIME_ERROR, seed=1, duration_ms=0).is_seed_problem


def test_star_and_private_from_imports_are_rejected() -> None:
    assert any("tên import bị cấm: *" in v for v in ASTGuard.check("from math import *\n"))
    assert any("tên import bị cấm: _x" in v for v in ASTGuard.check("from math import _x\n"))


def test_overlong_string_literal_is_rejected() -> None:
    assert any("chuỗi literal quá dài" in v for v in ASTGuard.check('x = "' + "a" * 2_500 + '"\n'))
