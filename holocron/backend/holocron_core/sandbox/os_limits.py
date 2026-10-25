"""Áp giới hạn tài nguyên OS cho CHÍNH tiến trình đang chạy (ADR-002, ADR-008 mục 3).

Dùng chung cho tiến trình con của sandbox (`_sandbox_child.py`) và worker đọc PDF. Module chỉ dùng thư viện
chuẩn và không import gì của `holocron_core`, vì child của sandbox chạy bằng `python -S` với môi trường rỗng
và nạp file này theo đường dẫn.

- Unix: `RLIMIT_AS` (RAM), `RLIMIT_CPU`, `RLIMIT_FSIZE` = 0 (không ghi được file), `RLIMIT_CORE` = 0.
- Windows: Job Object giới hạn RAM và CPU user-time, `KILL_ON_JOB_CLOSE`. Không có tương đương `RLIMIT_FSIZE`;
  việc ghi file bị chặn ở lớp khác (sandbox: không có `open`/`import os`; worker PDF: không có đường ghi).
"""

import sys

if sys.platform == "win32":

    def apply_limits(mem_bytes: int, cpu_seconds: int) -> None:
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

    def apply_limits(mem_bytes: int, cpu_seconds: int) -> None:
        resource.setrlimit(resource.RLIMIT_AS, (mem_bytes, mem_bytes))
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
        resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
