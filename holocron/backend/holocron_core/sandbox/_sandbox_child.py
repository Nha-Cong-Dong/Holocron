"""Tiến trình con chạy template. Được khởi chạy bởi sandbox.run_template.

Thứ tự bảo vệ: (1) pre-import whitelist, (2) giới hạn tài nguyên OS (RLIMIT_* trên Unix,
Job Object trên Windows), (3) exec với builtins rút gọn và __import__ chỉ trả về module đã
nạp sẵn. Kết quả ghi đúng một dòng có tiền tố RESULT_PREFIX ra stdout.
"""

import builtins
import json
import sys
import traceback
import types
from collections.abc import Callable

RESULT_PREFIX = "HOLOCRON_RESULT:"
ALLOWED_MODULES = ("random", "math", "fractions", "itertools", "functools", "collections", "statistics")
SAFE_BUILTIN_NAMES = (
    "abs",
    "all",
    "any",
    "bool",
    "dict",
    "divmod",
    "enumerate",
    "filter",
    "float",
    "format",
    "frozenset",
    "int",
    "isinstance",
    "len",
    "list",
    "map",
    "max",
    "min",
    "pow",
    "range",
    "reversed",
    "round",
    "set",
    "sorted",
    "str",
    "sum",
    "tuple",
    "zip",
    "None",
    "True",
    "False",
    "Exception",
    "ValueError",
    "TypeError",
    "KeyError",
    "IndexError",
    "ZeroDivisionError",
    "AssertionError",
    "ArithmeticError",
    "StopIteration",
)
ENTRYPOINTS = ("generate_params", "validate", "solve", "make_distractors", "render_stem")

Json = dict[str, object]


def _emit(payload: Json) -> None:
    sys.stdout.write(RESULT_PREFIX + json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _preload() -> dict[str, types.SimpleNamespace]:
    """Trả về proxy chỉ chứa thuộc tính public, KHÔNG chứa module con.

    Ví dụ: `fractions.sys`, `random._os` là đường thoát sandbox kinh điển;
    proxy loại bỏ mọi giá trị là module và mọi tên bắt đầu bằng '_'.
    """
    module_type = type(sys)
    loaded: dict[str, types.SimpleNamespace] = {}
    for name in ALLOWED_MODULES:
        real = __import__(name)
        public = {k: v for k, v in vars(real).items() if not k.startswith("_") and not isinstance(v, module_type)}
        loaded[name] = types.SimpleNamespace(**public)
    return loaded


def _load_apply_limits() -> Callable[[int, int], None]:
    """Nạp `apply_limits` từ `os_limits.py` nằm cạnh file này, theo đường dẫn file.

    Child chạy bằng `python -S -s -P` với môi trường rỗng nên không import được package `holocron_core`.
    Thiếu file hoặc thiếu hàm thì ném lỗi ngay khi khởi động (fail closed): tiến trình cha nhận CRASHED.
    """
    import importlib.util
    import os.path

    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "os_limits.py")
    spec = importlib.util.spec_from_file_location("holocron_os_limits", path)
    if spec is None or spec.loader is None:
        raise ImportError("không nạp được os_limits.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    apply: Callable[[int, int], None] = module.apply_limits
    if not callable(apply):
        raise TypeError("os_limits.apply_limits không gọi được")
    return apply


_apply_limits = _load_apply_limits()


def _make_builtins(preloaded: dict[str, types.SimpleNamespace]) -> Json:
    def safe_import(
        name: str, globals: object = None, locals: object = None, fromlist: object = (), level: int = 0
    ) -> types.SimpleNamespace:
        root = name.split(".")[0]
        if level != 0 or root not in preloaded or name != root:
            raise ImportError("import bị chặn: " + name)
        return preloaded[root]

    safe: Json = {name: getattr(builtins, name) for name in SAFE_BUILTIN_NAMES}
    safe["__import__"] = safe_import
    return safe


def _is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _gantt_ok(gantt: object) -> bool:
    """`gantt` là list các dict đúng {process: str, start: int, end: int} (spec §8.1)."""
    if not isinstance(gantt, list):
        return False
    return all(
        isinstance(seg, dict)
        and set(seg) == {"process", "start", "end"}
        and isinstance(seg["process"], str)
        and _is_int(seg["start"])
        and _is_int(seg["end"])
        for seg in gantt
    )


def _check_outputs(solution: object, distractors: object) -> tuple[Json, list[Json]] | str:
    """Trả về (solution, distractors) đã kiểm hợp đồng (khoá VÀ kiểu), hoặc thông điệp lỗi."""
    if not isinstance(solution, dict) or not {"value", "display", "explanation_vi"} <= set(solution):
        return "solve() phải trả về dict có value/display/explanation_vi"
    if not _is_number(solution["value"]) or not all(
        isinstance(solution[k], str) for k in ("display", "explanation_vi")
    ):
        return "solve(): value phải là số (không phải bool); display/explanation_vi phải là str"
    if "gantt" in solution and not _gantt_ok(solution["gantt"]):
        return "solve(): gantt phải là list các dict {process: str, start: int, end: int}"
    if not isinstance(distractors, list) or len(distractors) != 3:
        return "make_distractors() phải trả về list đúng 3 phần tử"
    checked: list[Json] = []
    for d in distractors:
        if not isinstance(d, dict) or not {"misconception_code", "value", "display", "rationale_vi"} <= set(d):
            return "mỗi distractor cần misconception_code/value/display/rationale_vi"
        if not _is_number(d["value"]) or not all(
            isinstance(d[k], str) for k in ("misconception_code", "display", "rationale_vi")
        ):
            return "distractor: value phải là số (không phải bool); các trường còn lại phải là str"
        checked.append(d)
    return solution, checked


def _entry(namespace: Json, name: str) -> Callable[..., object]:
    fn = namespace.get(name)
    if callable(fn):
        return fn
    raise KeyError(name)


def main() -> None:
    request = json.loads(sys.stdin.read())
    preloaded = _preload()
    _apply_limits(int(request["mem_bytes"]), int(request["cpu_seconds"]))
    namespace: Json = {"__builtins__": _make_builtins(preloaded), "__name__": "holocron_template"}
    stage = "exec"
    try:
        code = compile(request["source"], "<template>", "exec")
        exec(code, namespace)  # noqa: S102 - chủ đích: sandbox chạy mã đã qua ASTGuard và giới hạn OS
        missing = [e for e in ENTRYPOINTS if not callable(namespace.get(e))]
        if missing:
            _emit({"status": "contract_error", "stage": stage, "message": "thiếu entrypoint: " + ", ".join(missing)})
            return
        if request.get("params") is not None:
            stage = "solve"
            params = request["params"]
            solved = _entry(namespace, "solve")(params)
            if not isinstance(solved, dict) or not {"value", "display"} <= set(solved):
                _emit({"status": "contract_error", "stage": stage, "message": "solve() sai hợp đồng"})
                return
            _emit({"status": "ok", "params": params, "solution": solved})
            return
        stage = "generate_params"
        params = _entry(namespace, "generate_params")(int(request["seed"]))
        stage = "validate"
        _entry(namespace, "validate")(params)
        stage = "solve"
        raw_solution = _entry(namespace, "solve")(params)
        stage = "make_distractors"
        raw_distractors = _entry(namespace, "make_distractors")(params, raw_solution)
        stage = "render_stem"
        stem = _entry(namespace, "render_stem")(params)
        stage = "contract"
        checked = _check_outputs(raw_solution, raw_distractors)
        if isinstance(checked, str):
            _emit({"status": "contract_error", "stage": stage, "message": checked})
            return
        solution, distractors = checked
        displays = [solution["display"]] + [d["display"] for d in distractors]
        if len(set(displays)) != 4:
            _emit({"status": "assertion_failed", "stage": stage, "message": "DISTINCT_DISPLAY (runner check)"})
            return
        _emit({"status": "ok", "params": params, "solution": solution, "distractors": distractors, "stem": stem})
    except AssertionError as exc:
        _emit({"status": "assertion_failed", "stage": stage, "message": str(exc) or "assert không có nhãn"})
    except MemoryError:
        _emit({"status": "memory_exceeded", "stage": stage, "message": "MemoryError"})
    except RecursionError:
        _emit({"status": "runtime_error", "stage": stage, "message": "RecursionError"})
    except BaseException as exc:
        tb = traceback.extract_tb(exc.__traceback__)
        frames = [f for f in tb if f.filename == "<template>"]
        line = frames[-1].lineno if frames else None
        _emit(
            {"status": "runtime_error", "stage": stage, "message": type(exc).__name__ + ": " + str(exc), "line": line}
        )
        raise  # kết quả đã phát ra; thoát mã ≠ 0 để lỗi không bị nuốt


if __name__ == "__main__":
    main()
