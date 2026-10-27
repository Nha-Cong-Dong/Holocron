"""Bộ đọc PDF trước worker bị khai thác hoặc tài liệu xấu (ADR-008 mục 3, 4, 6).

- stdout của worker được đọc có giới hạn byte; dòng quá dài, rác, JSON quá cỡ, byte không phải UTF-8 ⇒ trang
  `failed` (`PAGE_ERROR`), worker bị kill và trang sau đọc bằng worker mới;
- dữ liệu thô có giới hạn số ký tự mỗi dòng, số dòng và số từ mỗi trang;
- hạn tổng cho cả tài liệu, và lỗi dựng lại worker giữa chừng không ném ra ngoài;
- worker áp giới hạn OS trước khi mở file.
"""

from __future__ import annotations

import contextlib
import io
import json
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import IO

import pytest
from pydantic import BaseModel, ValidationError

from holocron_core.domain.documents import MAX_LINE_CHARS
from holocron_core.infrastructure.document_readers import _pdf_worker, pdf_reader
from holocron_core.infrastructure.document_readers.config import PdfReaderConfig
from holocron_core.infrastructure.document_readers.pdf_reader import (
    READ_CHUNK_BYTES,
    PdfInputError,
    PdfWorkerError,
    WorkerPdfReader,
    WorkerProtocolError,
    WorkerSession,
    decode_line,
    pump_lines,
)
from holocron_core.infrastructure.document_readers.pdfium_adapter import PdfiumBackend
from holocron_core.infrastructure.document_readers.protocol import (
    MAX_PAGE_LINES,
    MAX_PAGE_WORDS,
    MAX_RAW_LINE_CHARS,
    MESSAGE_PREFIX,
    OpenedMessage,
    PageErrorMessage,
    PageMessage,
    PageRequest,
    RawPdfPage,
    RawTextLine,
    RawWord,
    ReadyMessage,
    WorkerRequest,
    decode_message,
)
from holocron_core.infrastructure.document_readers.text_layer import PdfChar, group_lines
from tests.pdf_samples import Page, text_lines, write_pdf

DOC_ID = "doc_00000000000000dd"
MIB = 1024 * 1024
CONFIG = PdfReaderConfig()
ENGLISH = "The scheduler gives each ready process the processor for one time quantum in turn."
BOX = (0.1, 0.1, 0.9, 0.12)


def _line(text: str = ENGLISH, words: int | None = None) -> RawTextLine:
    tokens = text.split() if words is None else ["w"] * words
    return RawTextLine(text=text, bbox=BOX, words=[RawWord(text=t, bbox=BOX) for t in tokens])


def _page(index: int, lines: list[RawTextLine] | None = None) -> RawPdfPage:
    return RawPdfPage(
        page_index=index,
        width_pt=612.0,
        height_pt=792.0,
        lines=[_line()] * 4 if lines is None else lines,
        image_area_ratio=0.0,
        path_area_ratio=0.0,
    )


# --------------------------------------------------------------------------- #
# Đọc stdout có giới hạn (pump_lines, decode_line)
# --------------------------------------------------------------------------- #


class _Sink:
    def __init__(self, accept: int | None = None) -> None:
        self.items: list[bytes | str] = []
        self.accept = accept

    def __call__(self, item: bytes | str) -> bool:
        if self.accept is not None and len(self.items) >= self.accept:
            return False
        self.items.append(item)
        return True


def _stream(data: bytes, piece: int = 7) -> Callable[[int], bytes]:
    position = 0

    def read(size: int) -> bytes:
        nonlocal position
        chunk = data[position : position + min(size, piece)]
        position += len(chunk)
        return chunk

    return read


def test_pump_splits_lines_across_chunks_and_reports_eof() -> None:
    sink = _Sink()
    pump_lines(_stream(b"alpha\nbe\r\ngamma\nrest"), 100, sink)
    assert sink.items == [b"alpha\n", b"be\r\n", b"gamma\n", "eof"]


def test_pump_stops_reading_an_endless_line_without_newline() -> None:
    consumed = 0

    def endless(size: int) -> bytes:
        nonlocal consumed
        consumed += size
        return b"x" * size

    sink = _Sink()
    limit = 256 * 1024
    pump_lines(endless, limit, sink)  # nguồn vô hạn: hàm chỉ dừng được nếu cắt ở giới hạn
    assert sink.items == ["overflow"]
    assert consumed <= limit + READ_CHUNK_BYTES


def test_pump_rejects_a_complete_line_over_the_limit() -> None:
    sink = _Sink()
    pump_lines(_stream(b"ok\n" + b"y" * 50 + b"\nnever\n", piece=1000), 20, sink)
    assert sink.items == [b"ok\n", "overflow"]


def test_pump_stops_when_receiver_is_closed() -> None:
    sink = _Sink(accept=1)
    pump_lines(_stream(b"a\nb\nc\n"), 100, sink)
    assert sink.items == [b"a\n"]


def test_decode_line_rejects_bytes_that_are_not_utf8() -> None:
    assert decode_line("Tiếng Việt\n".encode()) == "Tiếng Việt\n"
    with pytest.raises(WorkerProtocolError):
        decode_line(b"HOLOCRON_PDF:\xff\xfe\n")


def test_default_message_and_document_limits() -> None:
    assert CONFIG.max_message_bytes == 8 * MIB
    assert CONFIG.document_timeout_s == 600.0
    with pytest.raises(ValidationError):
        PdfReaderConfig(max_message_bytes=10)
    with pytest.raises(ValidationError):
        PdfReaderConfig(document_timeout_s=0)


# --------------------------------------------------------------------------- #
# Worker giả cư xử xấu (tiến trình con thật, tests/fake_pdf_worker.py)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("mode", ["long_line", "huge_json", "noise", "bad_utf8"])
def test_misbehaving_worker_fails_page_with_page_error(
    mode: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pdf_reader, "WORKER_MODULE", "tests.fake_pdf_worker")
    path = tmp_path / f"{mode}.pdf"
    path.write_bytes(b"%PDF-1.7\n")  # worker giả không đọc file
    config = PdfReaderConfig(max_message_bytes=64 * 1024, text_layer_page_timeout_s=30.0)
    started = time.monotonic()
    pages = list(WorkerPdfReader(config).read_pages(path, DOC_ID))
    elapsed = time.monotonic() - started
    assert [(p.page_number, p.status, p.failure_reason) for p in pages] == [
        (1, "failed", "PAGE_ERROR"),
        (2, "ok", None),
    ]
    assert pages[1].lines[0].text == ENGLISH
    # Lỗi được phát hiện ngay khi đọc, không phải chờ tới hạn 30 s của trang (lúc đó sẽ là PAGE_TIMEOUT).
    assert elapsed < 20.0


# --------------------------------------------------------------------------- #
# Giới hạn của dữ liệu thô
# --------------------------------------------------------------------------- #


def test_raw_models_bound_line_length_lines_and_words() -> None:
    assert MAX_RAW_LINE_CHARS >= MAX_LINE_CHARS
    assert len(_line("a" * MAX_RAW_LINE_CHARS, words=0).text) == MAX_RAW_LINE_CHARS
    with pytest.raises(ValidationError):
        _line("a" * (MAX_RAW_LINE_CHARS + 1), words=0)
    with pytest.raises(ValidationError):
        RawWord(text="a" * (MAX_RAW_LINE_CHARS + 1), bbox=BOX)
    with pytest.raises(ValidationError):
        _page(0, [_line("x", words=0)] * (MAX_PAGE_LINES + 1))
    per_line = MAX_PAGE_WORDS // 10
    assert len(_page(0, [_line("w", words=per_line)] * 10).lines) == 10
    with pytest.raises(ValidationError):
        _page(0, [_line("w", words=per_line)] * 10 + [_line("w", words=1)])


def test_oversized_page_message_is_rejected_when_decoded() -> None:
    payload = PageMessage(page=_page(0, [_line("x", words=0)])).model_dump(mode="json")
    payload["page"]["lines"] = payload["page"]["lines"] * (MAX_PAGE_LINES + 1)
    with pytest.raises(ValidationError):
        decode_message(MESSAGE_PREFIX + json.dumps(payload))


def test_group_lines_breaks_lines_longer_than_raw_limit() -> None:
    page_box = (0.0, 0.0, 100.0, 100.0)
    chars = [PdfChar("a", (1.0, 1.0, 2.0, 2.0))] * (2 * MAX_RAW_LINE_CHARS + 5)
    lines = group_lines([*chars, PdfChar("\n", None), PdfChar("b", None)], page_box, 0)
    assert [len(line.text) for line in lines] == [MAX_RAW_LINE_CHARS, MAX_RAW_LINE_CHARS, 5, 1]


def test_worker_reports_page_error_instead_of_an_oversized_message(tmp_path: Path) -> None:
    path = write_pdf(tmp_path / "one.pdf", [Page(texts=text_lines([ENGLISH] * 4))])
    emitted: list[BaseModel] = []
    request = WorkerRequest(path=str(path), page_budget=2, config=PdfReaderConfig(max_message_bytes=1024))
    requests = [PageRequest(page_index=0), PageRequest(page_index=3)]
    _pdf_worker.run(request, PdfiumBackend.load(), requests, emitted.append)
    assert emitted[1] == PageErrorMessage(page_index=0, message="MessageTooLarge")
    assert isinstance(emitted[2], PageErrorMessage) and emitted[2].page_index == 3  # lỗi gốc giữ nguyên


# --------------------------------------------------------------------------- #
# Hạn tổng của tài liệu và lỗi dựng lại worker (session giả, không spawn)
# --------------------------------------------------------------------------- #


class _ScriptedSession:
    """Session giả cho `read_pages`: `outcomes` theo thứ tự; `"sleep"` ngủ hết timeout rồi ném TimeoutError."""

    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = outcomes
        self.requested: list[int] = []
        self.timeouts: list[float] = []
        self.closed = False

    def request_page(self, index: int) -> None:
        self.requested.append(index)

    def receive(self, timeout_s: float) -> object:
        self.timeouts.append(timeout_s)
        outcome = self.outcomes.pop(0)
        if outcome == "sleep":
            time.sleep(timeout_s)
            raise TimeoutError
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def kill(self) -> None:
        self.closed = True

    def close(self) -> None:
        self.closed = True


def _patch_start(monkeypatch: pytest.MonkeyPatch, results: list[tuple[_ScriptedSession, int] | Exception]) -> list[int]:
    budgets: list[int] = []

    def start(self: WorkerPdfReader, path: Path, page_budget: int) -> tuple[_ScriptedSession, int]:
        budgets.append(page_budget)
        result = results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(WorkerPdfReader, "_start", start)
    return budgets


def _pdf_stub(tmp_path: Path) -> Path:
    path = tmp_path / "stub.pdf"
    path.write_bytes(b"%PDF-1.7\n")
    return path


def test_document_deadline_fails_remaining_pages_with_page_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _ScriptedSession(["sleep"])
    budgets = _patch_start(monkeypatch, [(session, 3)])
    config = PdfReaderConfig(document_timeout_s=0.3, text_layer_page_timeout_s=5.0)
    started = time.monotonic()
    pages = list(WorkerPdfReader(config).read_pages(_pdf_stub(tmp_path), DOC_ID))
    assert [(p.page_number, p.status, p.failure_reason) for p in pages] == [
        (1, "failed", "PAGE_TIMEOUT"),
        (2, "failed", "PAGE_TIMEOUT"),
        (3, "failed", "PAGE_TIMEOUT"),
    ]
    assert session.requested == [0] and session.timeouts[0] <= 0.3
    assert budgets == [CONFIG.max_pages]  # hết hạn tổng thì không dựng worker mới
    assert time.monotonic() - started < 3.0


def test_time_spent_by_the_caller_does_not_count_toward_the_document_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _ScriptedSession([PageMessage(page=_page(i)) for i in range(3)])
    _patch_start(monkeypatch, [(session, 3)])
    config = PdfReaderConfig(document_timeout_s=0.5)
    statuses = []
    for page in WorkerPdfReader(config).read_pages(_pdf_stub(tmp_path), DOC_ID):
        statuses.append(page.status)
        time.sleep(0.3)  # nơi gọi xử lý chậm: tổng 0.9 s > hạn 0.5 s
    assert statuses == ["ok", "ok", "ok"]


def test_worker_restart_failure_marks_remaining_pages_failed_without_raising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _ScriptedSession([None])  # worker chết ở trang đầu
    budgets = _patch_start(monkeypatch, [(session, 3), PdfWorkerError("không khởi động được")])
    pages = list(WorkerPdfReader().read_pages(_pdf_stub(tmp_path), DOC_ID))
    assert [(p.page_number, p.status, p.failure_reason) for p in pages] == [
        (1, "failed", "PAGE_ERROR"),
        (2, "failed", "PAGE_ERROR"),
        (3, "failed", "PAGE_ERROR"),
    ]
    assert budgets == [CONFIG.max_pages, 2] and session.closed


def test_restart_that_cannot_open_the_file_again_also_fails_pages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = _ScriptedSession(["sleep"])
    budgets = _patch_start(monkeypatch, [(first, 3), PdfInputError("OPEN_TIMEOUT")])
    config = PdfReaderConfig(text_layer_page_timeout_s=0.05)
    pages = list(WorkerPdfReader(config).read_pages(_pdf_stub(tmp_path), DOC_ID, [1, 2, 3]))
    assert [p.failure_reason for p in pages] == ["PAGE_TIMEOUT", "PAGE_ERROR", "PAGE_ERROR"]
    assert budgets == [3, 2] and first.requested == [0]


def test_protocol_errors_during_handshake() -> None:
    error = WorkerProtocolError("dòng quá dài")
    with pytest.raises(PdfWorkerError):
        WorkerPdfReader()._open(_ScriptedSession([error]))
    with pytest.raises(PdfInputError) as excinfo:
        WorkerPdfReader()._open(_ScriptedSession([ReadyMessage(), error]))
    assert excinfo.value.code == "OPEN_FAILED"


def test_protocol_error_on_a_page_kills_the_worker() -> None:
    session = _ScriptedSession([WorkerProtocolError("byte không phải UTF-8")])
    page, healthy = WorkerPdfReader()._next_page(session, 0, DOC_ID)
    assert (page.failure_reason, healthy, session.closed) == ("PAGE_ERROR", False, True)


# --------------------------------------------------------------------------- #
# Worker áp giới hạn OS trước khi mở file (ADR-008 mục 3)
# --------------------------------------------------------------------------- #


def test_worker_main_applies_limits_before_opening_the_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = write_pdf(tmp_path / "one.pdf", [Page(texts=text_lines([ENGLISH] * 4))])
    request = WorkerRequest(path=str(path), page_budget=0, config=CONFIG)
    calls: list[str] = []
    real_parse = PdfiumBackend.open

    def apply_limits(mem_bytes: int, cpu_seconds: int) -> None:
        calls.append("apply_limits")  # không áp giới hạn thật lên tiến trình test

    @contextlib.contextmanager
    def recording_open(file: str, mode: str = "r") -> Iterator[IO[bytes]]:
        calls.append(f"open:{Path(file).name}")
        with Path(file).open("rb") as handle:
            yield handle

    def recording_parse(self: PdfiumBackend, data: bytes) -> object:
        calls.append("parse")
        return real_parse(self, data)

    stdout = io.StringIO()
    monkeypatch.setattr(_pdf_worker, "apply_limits", apply_limits)
    monkeypatch.setattr(_pdf_worker, "open", recording_open, raising=False)
    monkeypatch.setattr(PdfiumBackend, "open", recording_parse)
    monkeypatch.setattr("sys.stdin", io.StringIO(request.model_dump_json() + "\n"))
    monkeypatch.setattr("sys.stdout", stdout)
    _pdf_worker.main()
    assert calls == ["apply_limits", "open:one.pdf", "parse"]
    messages = [decode_message(line) for line in stdout.getvalue().splitlines()]
    assert messages == [ReadyMessage(), OpenedMessage(page_count=1)]


# --------------------------------------------------------------------------- #
# Dọn thư mục tạm khi tiến trình con chưa thoát hẳn (Windows)
# --------------------------------------------------------------------------- #


class _StickyDir:
    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.calls = 0

    def cleanup(self) -> None:
        self.calls += 1
        if self.calls <= self.failures:
            raise PermissionError("thư mục còn bị giữ")


def _session_with(workdir: _StickyDir) -> WorkerSession:
    """Session không spawn worker, chỉ để kiểm `_remove_workdir`."""
    session = WorkerSession.__new__(WorkerSession)
    vars(session)["_workdir"] = workdir
    return session


def test_workdir_removal_retries_while_the_directory_is_still_held() -> None:
    sticky = _StickyDir(failures=3)
    _session_with(sticky)._remove_workdir()
    assert sticky.calls == 4


def test_workdir_removal_gives_up_after_the_grace_period(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pdf_reader, "WORKDIR_CLEANUP_S", 0.05)
    sticky = _StickyDir(failures=10_000)
    with pytest.raises(PermissionError):
        _session_with(sticky)._remove_workdir()
    assert sticky.calls >= 2
