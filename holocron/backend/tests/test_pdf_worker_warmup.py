"""Làm nóng PDFium trong worker đọc PDF (ADR-008 mục 3, 4): thứ tự khởi động, lỗi khi làm nóng, không đọc file.

`_pdf_worker.start` nhận mọi phụ thuộc qua tham số; test luôn truyền `limit` giả nên không áp giới hạn OS thật lên
tiến trình pytest. Phép so sánh trang có và không có làm nóng chạy trong tiến trình con mới, vì khởi tạo của PDFium
chỉ diễn ra một lần trong mỗi tiến trình.
"""

from __future__ import annotations

import builtins
import io
import os
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import BaseModel

from holocron_core.infrastructure.document_readers import _pdf_worker, pdf_reader
from holocron_core.infrastructure.document_readers.config import PdfReaderConfig
from holocron_core.infrastructure.document_readers.pdfium_adapter import (
    WARMUP_PDF,
    PdfiumBackend,
    PdfiumDataError,
    warm_up,
)
from holocron_core.infrastructure.document_readers.protocol import (
    MESSAGE_PREFIX,
    OpenedMessage,
    PageMessage,
    PageRequest,
    RawPdfPage,
    ReadyMessage,
    WorkerRequest,
    decode_message,
    encode_message,
)
from tests.pdf_samples import Box, Page, Text, text_lines, write_pdf

BODY = [
    "The scheduler gives each ready process the processor for one time quantum.",
    "When the quantum expires the process moves to the tail of the ready queue.",
]
BACKEND_ROOT = Path(__file__).resolve().parents[1]

# Đọc các trang của một PDF trong tiến trình mới, có hoặc không làm nóng trước; in mỗi trang một dòng JSON.
_READ_SCRIPT = """
import sys
from holocron_core.infrastructure.document_readers.pdfium_adapter import PdfiumBackend, warm_up
backend = PdfiumBackend.load()
if sys.argv[2] == "warm":
    warm_up(backend)
with open(sys.argv[1], "rb") as handle:
    data = handle.read()
document = backend.open(data)
with document:
    for index in range(document.page_count()):
        sys.stdout.write(document.read_page(index).model_dump_json() + "\\n")
"""


def _request(path: Path, page_budget: int) -> WorkerRequest:
    return WorkerRequest(path=str(path), page_budget=page_budget, config=PdfReaderConfig())


def _sample_pages() -> list[Page]:
    first = Page(
        texts=[*text_lines(BODY), Text(x=72.0, y=600.0, value="abg", font="F2")],
        images=[Box(300.0, 300.0, 100.0, 80.0)],
        rects=[Box(60.0, 500.0, 200.0, 40.0)],
    )
    return [first, Page(texts=text_lines(BODY[:1]), rotate=90)]


# --------------------------------------------------------------------------- #
# Hằng PDF làm nóng và hàm làm nóng (chạy thật trong tiến trình test)
# --------------------------------------------------------------------------- #


def test_warmup_pdf_xref_matches_bytes() -> None:
    """Bảng xref của hằng khớp từng byte, để PDFium không phải dựng lại bảng (đường đọc giống file hợp lệ)."""
    xref_at = WARMUP_PDF.index(b"xref\n")
    startxref = re.search(rb"startxref\n(\d+)\n%%EOF\n$", WARMUP_PDF)
    assert startxref is not None and int(startxref.group(1)) == xref_at
    offsets = [int(m.group(1)) for m in re.finditer(rb"(\d{10}) 00000 n \n", WARMUP_PDF)]
    assert len(offsets) == 5
    for number, offset in enumerate(offsets, start=1):
        assert WARMUP_PDF[offset:].startswith(b"%d 0 obj\n" % number)
    stream = re.search(rb"/Length (\d+) >>\nstream\n(.*?)endstream", WARMUP_PDF, re.DOTALL)
    assert stream is not None and int(stream.group(1)) == len(stream.group(2))
    assert b"/BaseFont /Helvetica /Encoding /WinAnsiEncoding" in WARMUP_PDF
    assert b"/FontFile" not in WARMUP_PDF  # font không nhúng


def test_warm_up_reads_text_layer_with_real_backend() -> None:
    page = warm_up(PdfiumBackend.load())
    assert page.page_index == 0 and (page.width_pt, page.height_pt) == (200.0, 50.0)
    assert [line.text for line in page.lines] == ["Holocron 0123456789"]


def test_warm_up_open_failure_raises_page_error_type(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = PdfiumBackend.load()

    def fail_open(data: bytes) -> str:
        return "OPEN_FAILED"

    monkeypatch.setattr(backend, "open", fail_open)
    with pytest.raises(PdfiumDataError):
        warm_up(backend)
    assert issubclass(PdfiumDataError, _pdf_worker.PAGE_ERRORS)


# --------------------------------------------------------------------------- #
# Thứ tự khởi động (không áp giới hạn OS thật)
# --------------------------------------------------------------------------- #


class _Recorder:
    def __init__(self, warm_error: BaseException | None = None) -> None:
        self.events: list[str] = []
        self.backend = PdfiumBackend.load()
        self.warmed: list[PdfiumBackend] = []
        self.limits: list[tuple[int, int]] = []
        self.emitted: list[BaseModel] = []
        self._warm_error = warm_error

    def load(self) -> PdfiumBackend:
        self.events.append("load")
        return self.backend

    def warm(self, backend: PdfiumBackend) -> RawPdfPage:
        self.events.append("warm")
        self.warmed.append(backend)
        if self._warm_error is not None:
            raise self._warm_error
        return warm_up(backend)

    def limit(self, mem_bytes: int, cpu_seconds: int) -> None:
        self.events.append("limit")
        self.limits.append((mem_bytes, cpu_seconds))

    def emit(self, message: BaseModel) -> None:
        self.events.append(type(message).__name__)
        self.emitted.append(message)

    def start(self, request: WorkerRequest) -> tuple[PdfiumBackend, _pdf_worker.OcrRunner | None]:
        return _pdf_worker.start(request, load=self.load, warm=self.warm, limit=self.limit, emit=self.emit)


@pytest.mark.parametrize("page_budget", [1, 2, 300])
def test_start_order_is_load_warm_limit_ready(tmp_path: Path, page_budget: int) -> None:
    recorder = _Recorder()
    backend, ocr = recorder.start(_request(tmp_path / "doc.pdf", page_budget))
    assert recorder.events == ["load", "warm", "limit", "ReadyMessage"]
    assert recorder.warmed == [recorder.backend]  # làm nóng đúng một lần, trên chính backend vừa nạp
    assert backend is recorder.backend and ocr is None
    config = PdfReaderConfig()
    assert recorder.limits == [(config.worker_mem_bytes, config.worker_cpu_seconds_per_page * (page_budget + 1))]
    assert recorder.emitted == [ReadyMessage()]


def test_start_skips_warm_up_for_probe_worker(tmp_path: Path) -> None:
    recorder = _Recorder()
    recorder.start(_request(tmp_path / "doc.pdf", 0))
    assert recorder.events == ["load", "limit", "ReadyMessage"]
    assert recorder.warmed == []
    assert recorder.limits == [(PdfReaderConfig().worker_mem_bytes, PdfReaderConfig().worker_cpu_seconds_per_page)]


@pytest.mark.parametrize("error_type", _pdf_worker.PAGE_ERRORS)
def test_warm_up_error_is_silent_and_worker_still_reads(
    tmp_path: Path, capfd: pytest.CaptureFixture[str], error_type: type[Exception]
) -> None:
    """(b) Làm nóng lỗi ⇒ vẫn báo `ready`, stdout chỉ có thông điệp giao thức, trang thật vẫn đọc được."""
    path = write_pdf(tmp_path / "doc.pdf", [Page(texts=text_lines(BODY))])
    recorder = _Recorder(warm_error=error_type("lỗi làm nóng"))
    request = _request(path, 1)
    capfd.readouterr()
    backend, ocr = _pdf_worker.start(
        request, load=recorder.load, warm=recorder.warm, limit=recorder.limit, emit=_pdf_worker._emit_stdout
    )
    captured = capfd.readouterr()
    assert captured.out == encode_message(ReadyMessage()) + "\n"
    assert captured.err == ""
    assert recorder.events == ["load", "warm", "limit"]

    emitted: list[BaseModel] = []
    _pdf_worker.run(request, backend, [PageRequest(page_index=0)], emitted.append, ocr)
    assert isinstance(emitted[0], OpenedMessage) and emitted[0].page_count == 1
    assert isinstance(emitted[1], PageMessage)
    assert [line.text for line in emitted[1].page.lines] == BODY


def test_warm_up_error_outside_page_errors_stops_startup(tmp_path: Path) -> None:
    """Lỗi không thuộc `PAGE_ERRORS` không bị nuốt: không áp giới hạn, không báo `ready` (tiến trình cha thấy EOF)."""

    class FatalError(Exception):
        pass

    recorder = _Recorder(warm_error=FatalError())
    with pytest.raises(FatalError):
        recorder.start(_request(tmp_path / "doc.pdf", 1))
    assert recorder.events == ["load", "warm"]
    assert recorder.limits == [] and recorder.emitted == []


# --------------------------------------------------------------------------- #
# Làm nóng không đọc `request.path` và không mở file
# --------------------------------------------------------------------------- #


def test_warm_up_does_not_open_any_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """(c) Theo dõi mọi đường mở file ở tầng Python; `request.path` trỏ tới file không tồn tại."""
    backend = PdfiumBackend.load()
    opened: list[object] = []

    def tracking(name: str) -> Callable[..., object]:
        def refuse(*args: object, **kwargs: object) -> object:
            opened.append((name, args[0] if args else None))
            raise AssertionError(f"làm nóng không được mở file ({name})")

        return refuse

    def refuse_read(path: str, max_bytes: int) -> bytes | None:
        opened.append(("_read_bounded", path))
        raise AssertionError("làm nóng không được đọc request.path")

    monkeypatch.setattr(builtins, "open", tracking("builtins.open"))
    monkeypatch.setattr(io, "open", tracking("io.open"))
    monkeypatch.setattr(os, "open", tracking("os.open"))
    monkeypatch.setattr(_pdf_worker, "_read_bounded", refuse_read)

    emitted: list[BaseModel] = []
    warm_results: list[RawPdfPage] = []

    def warm(target: PdfiumBackend) -> RawPdfPage:
        page = warm_up(target)
        warm_results.append(page)
        return page

    request = _request(tmp_path / "missing" / "never-created.pdf", 1)
    _pdf_worker.start(request, load=lambda: backend, warm=warm, limit=lambda mem, cpu: None, emit=emitted.append)
    assert opened == []
    assert [line.text for line in warm_results[0].lines] == ["Holocron 0123456789"]  # làm nóng chạy thật, không lỗi
    assert emitted == [ReadyMessage()]
    assert not Path(request.path).exists()


# --------------------------------------------------------------------------- #
# Trang thật giống hệt nhau khi có và không có làm nóng (tiến trình mới)
# --------------------------------------------------------------------------- #


def _read_in_fresh_process(path: Path, mode: str) -> list[RawPdfPage]:
    completed = subprocess.run(
        [sys.executable, "-P", "-s", "-c", _READ_SCRIPT, str(path), mode],
        capture_output=True,
        encoding="utf-8",
        cwd=BACKEND_ROOT,
        env=pdf_reader._worker_env(),
        timeout=120,
        check=True,
    )
    return [RawPdfPage.model_validate_json(line) for line in completed.stdout.splitlines()]


def test_pages_are_identical_with_and_without_warm_up(tmp_path: Path) -> None:
    """(a) `RawPdfPage` của cùng trang giống hệt nhau khi đọc trên PDFium nguội và đã làm nóng."""
    path = write_pdf(tmp_path / "sample.pdf", _sample_pages())
    cold = _read_in_fresh_process(path, "cold")
    warm = _read_in_fresh_process(path, "warm")
    assert len(cold) == 2
    assert cold == warm
    assert [line.text for line in cold[0].lines[:2]] == BODY
    assert cold[0].image_area_ratio > 0.0 and cold[0].path_area_ratio > 0.0


def test_real_worker_stdout_has_only_protocol_lines(tmp_path: Path) -> None:
    """Worker thật (có làm nóng, có giới hạn OS) chỉ ghi dòng giao thức ra stdout: ready, opened, page."""
    path = write_pdf(tmp_path / "doc.pdf", [Page(texts=text_lines(BODY))])
    request = WorkerRequest(path=str(path), page_budget=1, config=PdfReaderConfig())
    stdin = request.model_dump_json() + "\n" + PageRequest(page_index=0).model_dump_json() + "\n"
    completed = subprocess.run(
        [sys.executable, "-P", "-s", "-m", pdf_reader.WORKER_MODULE],
        input=stdin.encode("utf-8"),
        capture_output=True,
        cwd=tmp_path,
        env=pdf_reader._worker_env(),
        timeout=120,
        check=True,
    )
    lines = completed.stdout.decode("utf-8").splitlines()
    assert all(line.startswith(MESSAGE_PREFIX) for line in lines)
    messages = [decode_message(line) for line in lines]
    assert isinstance(messages[0], ReadyMessage)
    assert isinstance(messages[1], OpenedMessage)
    assert isinstance(messages[2], PageMessage) and [line.text for line in messages[2].page.lines] == BODY
    assert len(messages) == 3
