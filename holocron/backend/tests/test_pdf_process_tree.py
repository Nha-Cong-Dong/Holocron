"""Kill worker là kill cả cây tiến trình; mọi lần chờ tiến trình con của worker đều có hạn (review tầng 2, lỗi 2).

- Unix: worker là trưởng nhóm tiến trình riêng, tiến trình cha kill cả nhóm (`os.killpg`).
- Windows: Job Object `KILL_ON_JOB_CLOSE` do worker tự tạo kill tiến trình con khi worker chết.
- `run_bounded`: tiến trình con đóng stdout mà không thoát thì bị kill sau `kill_grace_s`, không treo worker.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from holocron_core.infrastructure.document_readers import pdf_reader
from holocron_core.infrastructure.document_readers._pdf_worker import build_ocr
from holocron_core.infrastructure.document_readers.config import OcrSettings, PdfReaderConfig
from holocron_core.infrastructure.document_readers.pdf_reader import WorkerSession
from holocron_core.infrastructure.document_readers.protocol import ReadyMessage, WorkerRequest
from holocron_core.infrastructure.document_readers.tesseract import RunResult, _reap, run_bounded

# Tiến trình con phải chết trong hạn này sau khi worker bị kill (tiến trình con của worker giả ngủ 120 s).
EOF_DEADLINE_S = 15.0
# Trình khởi chạy `python.exe` của venv trên Windows giữ bản sao stdout của tiến trình Python thật, nên test về
# EOF của stdout gọi thẳng trình thông dịch gốc.
BASE_PYTHON = getattr(sys, "_base_executable", sys.executable)


def _orphan_session(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> WorkerSession:
    monkeypatch.setattr(pdf_reader, "WORKER_MODULE", "tests.orphan_pdf_worker")
    request = WorkerRequest(path=str(tmp_path / "orphan.pdf"), page_budget=1, config=PdfReaderConfig())
    session = WorkerSession(request)
    assert isinstance(session.receive(30.0), ReadyMessage)
    return session


def test_killing_the_worker_kills_its_child_process(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Sau `kill`, stdout của worker phải đóng hẳn: không còn tiến trình con nào giữ pipe."""
    with _orphan_session(monkeypatch, tmp_path) as session:
        session.kill()
        assert session.process.poll() is not None
        assert session.receive(EOF_DEADLINE_S) is None, "tiến trình con của worker vẫn sống sau khi worker bị kill"


@pytest.mark.skipif(sys.platform == "win32", reason="nhóm tiến trình chỉ có trên Unix")
def test_worker_leads_its_own_process_group(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    with _orphan_session(monkeypatch, tmp_path) as session:
        pid = session.process.pid
        assert os.getpgid(pid) == pid
        assert os.getpgid(pid) != os.getpgid(0)


@pytest.mark.skipif(sys.platform == "win32", reason="os.killpg chỉ có trên Unix")
def test_kill_signals_the_whole_group_before_reaping(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[tuple[int, int]] = []
    real_killpg = os.killpg

    def spy(pgid: int, signal_number: int) -> None:
        calls.append((pgid, signal_number))
        real_killpg(pgid, signal_number)

    monkeypatch.setattr(os, "killpg", spy)
    with _orphan_session(monkeypatch, tmp_path) as session:
        pid = session.process.pid
        session.kill()
        session.kill()  # lần hai: worker đã được thu hồi, không gửi tín hiệu tới một mã nhóm có thể đã bị dùng lại
    assert calls == [(pid, signal.SIGKILL)]


def test_run_bounded_does_not_wait_forever_for_a_child_that_closed_stdout() -> None:
    started = time.monotonic()
    result = run_bounded(
        [BASE_PYTHON, "-I", "-S", "-c", "import os, time; os.close(1); time.sleep(120)"],
        b"",
        60.0,
        100,
        kill_grace_s=0.5,
    )
    assert time.monotonic() - started < EOF_DEADLINE_S
    assert result.status == "failed"  # bị kill sau hạn chờ nên mã thoát khác 0


class _StuckProcess(subprocess.Popen[bytes]):
    """Tiến trình thật nhưng `wait` luôn báo quá hạn, mô phỏng tiến trình không chết được."""

    def __init__(self) -> None:
        super().__init__([BASE_PYTHON, "-I", "-S", "-c", "import time; time.sleep(120)"])
        self.kills = 0

    def wait(self, timeout: float | None = None) -> int:
        if timeout is None:
            return super().wait()
        raise subprocess.TimeoutExpired(self.args, timeout)

    def kill(self) -> None:
        self.kills += 1
        super().kill()


def test_reap_kills_and_gives_up_after_the_grace_period() -> None:
    process = _StuckProcess()
    try:
        started = time.monotonic()
        assert _reap(process, 0.1) is None
        assert process.kills == 1 and time.monotonic() - started < EOF_DEADLINE_S
    finally:
        process.kill()
        subprocess.Popen.wait(process)


def test_build_ocr_uses_the_configured_grace_and_probe_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    from holocron_core.infrastructure.document_readers import _pdf_worker

    seen: list[tuple[int, float]] = []

    def fake_run(
        args: list[str], stdin_data: bytes, timeout_s: float, max_output_bytes: int, *, kill_grace_s: float
    ) -> RunResult:
        seen.append((max_output_bytes, kill_grace_s))
        return run_bounded(["holocron-no-such-binary-xyz"], b"", 1.0, 100)

    monkeypatch.setattr(_pdf_worker, "run_bounded", fake_run)
    config = PdfReaderConfig(ocr_kill_grace_s=1.5, ocr_probe_max_bytes=4096)
    assert build_ocr(OcrSettings(tesseract_cmd="tesseract-test", secondary=None), config) is None
    assert seen == [(4096, 1.5)]
