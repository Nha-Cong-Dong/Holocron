"""Worker đọc PDF giả có tiến trình con, chạy bằng `python -m tests.orphan_pdf_worker` thay worker thật.

Giống worker thật ở hai điểm liên quan tới việc kill: tự áp giới hạn OS (`apply_limits`, Job Object
`KILL_ON_JOB_CLOSE` trên Windows) rồi mới chạy tiến trình con (như Tesseract). Tiến trình con kế thừa stdout của
worker và ngủ lâu, nên tiến trình cha chỉ thấy EOF trên stdout khi CẢ worker lẫn tiến trình con đã chết.
"""

from __future__ import annotations

import subprocess
import sys

from holocron_core.infrastructure.document_readers.protocol import ReadyMessage, encode_message
from holocron_core.sandbox.os_limits import apply_limits

CHILD_SLEEP_S = 120
MEM_BYTES = 1024 * 1024 * 1024
CPU_SECONDS = 60


def main() -> None:
    sys.stdin.buffer.readline()  # WorkerRequest
    apply_limits(MEM_BYTES, CPU_SECONDS)
    subprocess.Popen(
        [sys.executable, "-I", "-S", "-c", f"import time; time.sleep({CHILD_SLEEP_S})"],
        stdin=subprocess.DEVNULL,
        stdout=sys.stdout.fileno(),
        stderr=subprocess.DEVNULL,
    )
    sys.stdout.buffer.write((encode_message(ReadyMessage()) + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()
    sys.stdin.buffer.readline()  # đứng chờ tới khi bị kill


if __name__ == "__main__":
    main()
