"""Tiến trình con chạy template. Được khởi chạy bởi sandbox.run_template.

Thứ tự bảo vệ: (1) pre-import whitelist, (2) setrlimit, (3) exec với builtins
rút gọn và __import__ chỉ trả về module đã nạp sẵn. Kết quả ghi đúng một dòng
có tiền tố RESULT_PREFIX ra stdout.
"""

import builtins
import json
import resource
import sys
import traceback
import types

RESULT_PREFIX = "HOLOCRON_RESULT:"
ALLOWED_MODULES = ("random", "math", "fractions", "itertools", "functools", "collections", "statistics")
SAFE_BUILTIN_NAMES = (
    "abs", "all", "any", "bool", "dict", "divmod", "enumerate", "filter", "float", "format",
    "frozenset", "int", "isinstance", "len", "list", "map", "max", "min", "pow", "range",
    "reversed", "round", "set", "sorted", "str", "sum", "tuple", "zip", "None", "True", "False",
    "Exception", "ValueError", "TypeError", "KeyError", "IndexError", "ZeroDivisionError",
    "AssertionError", "ArithmeticError", "StopIteration",
)
ENTRYPOINTS = ("generate_params", "validate", "solve", "make_distractors", "render_stem")


def _emit(payload):
    sys.stdout.write(RESULT_PREFIX + json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _preload():
    """Trả về proxy chỉ chứa thuộc tính public, KHÔNG chứa module con.

    Ví dụ: `fractions.sys`, `random._os` là đường thoát sandbox kinh điển;
    proxy loại bỏ mọi giá trị là module và mọi tên bắt đầu bằng '_'.
    """
    module_type = type(sys)
    loaded = {}
    for name in ALLOWED_MODULES:
        real = __import__(name)
        public = {
            k: v for k, v in vars(real).items()
            if not k.startswith("_") and not isinstance(v, module_type)
        }
        loaded[name] = types.SimpleNamespace(**public)
    return loaded


def _apply_limits(mem_bytes, cpu_seconds):
    resource.setrlimit(resource.RLIMIT_AS, (mem_bytes, mem_bytes))
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def _make_builtins(preloaded):
    def safe_import(name, globals=None, locals=None, fromlist=(), level=0):
        root = name.split(".")[0]
        if level != 0 or root not in preloaded or name != root:
            raise ImportError("import bị chặn: " + name)
        return preloaded[root]

    safe = {name: getattr(builtins, name) for name in SAFE_BUILTIN_NAMES}
    safe["__import__"] = safe_import
    return safe


def _check_outputs(solution, distractors):
    if not isinstance(solution, dict) or not {"value", "display", "explanation_vi"} <= set(solution):
        return "solve() phải trả về dict có value/display/explanation_vi"
    if not isinstance(distractors, list) or len(distractors) != 3:
        return "make_distractors() phải trả về list đúng 3 phần tử"
    for d in distractors:
        if not isinstance(d, dict) or not {"misconception_code", "value", "display", "rationale_vi"} <= set(d):
            return "mỗi distractor cần misconception_code/value/display/rationale_vi"
    return None


def main():
    request = json.loads(sys.stdin.read())
    preloaded = _preload()
    _apply_limits(int(request["mem_bytes"]), int(request["cpu_seconds"]))
    namespace = {"__builtins__": _make_builtins(preloaded), "__name__": "holocron_template"}
    stage = "exec"
    try:
        code = compile(request["source"], "<template>", "exec")
        exec(code, namespace)
        missing = [e for e in ENTRYPOINTS if not callable(namespace.get(e))]
        if missing:
            _emit({"status": "contract_error", "stage": stage, "message": "thiếu entrypoint: " + ", ".join(missing)})
            return
        if request.get("params") is not None:
            stage = "solve"
            params = request["params"]
            solution = namespace["solve"](params)
            if not isinstance(solution, dict) or not {"value", "display"} <= set(solution):
                _emit({"status": "contract_error", "stage": stage, "message": "solve() sai hợp đồng"})
                return
            _emit({"status": "ok", "params": params, "solution": solution})
            return
        stage = "generate_params"
        params = namespace["generate_params"](int(request["seed"]))
        stage = "validate"
        namespace["validate"](params)
        stage = "solve"
        solution = namespace["solve"](params)
        stage = "make_distractors"
        distractors = namespace["make_distractors"](params, solution)
        stage = "render_stem"
        stem = namespace["render_stem"](params)
        stage = "contract"
        problem = _check_outputs(solution, distractors)
        if problem:
            _emit({"status": "contract_error", "stage": stage, "message": problem})
            return
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
        _emit({"status": "runtime_error", "stage": stage, "message": type(exc).__name__ + ": " + str(exc), "line": line})


if __name__ == "__main__":
    main()
