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


if sys.platform == "win32":

    def _apply_limits(mem_bytes: int, cpu_seconds: int) -> None:
        """Windows không có `resource`: dùng Job Object giới hạn RAM + CPU user-time cho chính tiến trình này.

        Không có RLIMIT_FSIZE/CORE tương đương; ghi file vẫn bị chặn ở L1/L2 (không có open/import os).
        Nếu tạo hoặc gán Job thất bại (ví dụ đã nằm trong job khác không cho lồng) thì bỏ qua:
        wall-clock timeout của tiến trình cha (sandbox.run_template) là lớp chặn cuối cùng.
        """
        import ctypes
        from ctypes import wintypes

        class BasicLimits(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]
            PerProcessUserTimeLimit: int
            LimitFlags: int

        class IoCounters(ctypes.Structure):
            _fields_ = [
                (n, ctypes.c_uint64)
                for n in ("ReadOps", "WriteOps", "OtherOps", "ReadBytes", "WriteBytes", "OtherBytes")
            ]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimits),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]
            BasicLimitInformation: BasicLimits
            ProcessMemoryLimit: int

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]

        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return
        info = ExtendedLimits()
        # 0x100 PROCESS_MEMORY, 0x2 PROCESS_TIME (đơn vị 100ns), 0x2000 KILL_ON_JOB_CLOSE
        info.BasicLimitInformation.LimitFlags = 0x100 | 0x2 | 0x2000
        info.BasicLimitInformation.PerProcessUserTimeLimit = cpu_seconds * 10_000_000
        info.ProcessMemoryLimit = mem_bytes
        if kernel32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
            kernel32.AssignProcessToJobObject(job, kernel32.GetCurrentProcess())

else:
    import resource

    def _apply_limits(mem_bytes: int, cpu_seconds: int) -> None:
        resource.setrlimit(resource.RLIMIT_AS, (mem_bytes, mem_bytes))
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
        resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


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


def _check_outputs(solution: object, distractors: object) -> tuple[Json, list[Json]] | str:
    """Trả về (solution, distractors) đã kiểm hợp đồng, hoặc thông điệp lỗi."""
    if not isinstance(solution, dict) or not {"value", "display", "explanation_vi"} <= set(solution):
        return "solve() phải trả về dict có value/display/explanation_vi"
    if not isinstance(distractors, list) or len(distractors) != 3:
        return "make_distractors() phải trả về list đúng 3 phần tử"
    checked: list[Json] = []
    for d in distractors:
        if not isinstance(d, dict) or not {"misconception_code", "value", "display", "rationale_vi"} <= set(d):
            return "mỗi distractor cần misconception_code/value/display/rationale_vi"
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
