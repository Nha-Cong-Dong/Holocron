"""Tiến trình cha chỉ nhận lượt quét ảnh template đúng lúc (ADR-008 mục 4, 6; sửa đổi 2026-10-30, review ảnh template).

Lượt quét chỉ được nhận khi worker được yêu cầu quét (chưa có kết quả quét nào được giữ) và trong lượt chờ trang đầu
của worker đó. Ngoài trường hợp đó, `template_scan_started` là vi phạm giao thức: kill worker, trang đang chờ thành
`failed` (`PAGE_ERROR`), không chờ thêm, không cộng hạn tổng và không đổi kết quả quét đã giữ. Thời gian chờ quét được
cộng vào hạn tổng tối đa một lần cho cả tài liệu.

Đồng hồ của module `pdf_reader` được thay bằng đồng hồ giả và worker bằng session giả, nên các test tất định trên mọi
nền tảng và không ngủ.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import pytest

from holocron_core.infrastructure.document_readers import pdf_reader
from holocron_core.infrastructure.document_readers.config import PdfReaderConfig
from holocron_core.infrastructure.document_readers.pdf_reader import PdfWorkerError, WorkerPdfReader
from holocron_core.infrastructure.document_readers.protocol import (
    ImageKey,
    PageMessage,
    RawPdfPage,
    RawTextLine,
    RawWord,
    TemplateScan,
    TemplateScanMessage,
    TemplateScanStartedMessage,
)

DOC_ID = "doc_000000000000beef"
CLOCK_SHORTFALL_S = 0.001
TEXT = "The scheduler gives each ready process the processor for one time quantum in turn."
BOX = (0.1, 0.1, 0.9, 0.12)
OK_SCAN = TemplateScan(
    status="ok",
    keys=[ImageKey(digest="a" * 64, width_px=4, height_px=4, bits_per_pixel=0, filters=(), bbox=(0, 0, 100, 100))],
)
OTHER_SCAN = TemplateScan(
    status="ok",
    keys=[ImageKey(digest="b" * 64, width_px=4, height_px=4, bits_per_pixel=0, filters=(), bbox=(0, 0, 100, 100))],
)


class _FakeClock:
    def __init__(self) -> None:
        self.start = 1000.0
        self.now = self.start

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


@dataclass(frozen=True)
class _After:
    """Thông điệp đến sau `seconds` giây trên đồng hồ giả."""

    seconds: float
    message: object


SILENT = "silent"  # worker im lặng: đồng hồ trôi gần hết hạn chờ rồi `TimeoutError`


class _Session:
    def __init__(self, clock: _FakeClock, outcomes: list[object]) -> None:
        self.clock = clock
        self.outcomes = outcomes
        self.requested: list[int] = []
        self.timeouts: list[float] = []
        self.killed = False
        self.closed = False

    def request_page(self, index: int) -> None:
        self.requested.append(index)

    def receive(self, timeout_s: float) -> object:
        self.timeouts.append(timeout_s)
        outcome = self.outcomes.pop(0)
        if outcome == SILENT:
            self.clock.now += timeout_s - CLOCK_SHORTFALL_S
            raise TimeoutError
        if isinstance(outcome, _After):
            self.clock.now += outcome.seconds
            return outcome.message
        return outcome

    def kill(self) -> None:
        self.killed = True

    def close(self) -> None:
        self.closed = True


def _page(index: int) -> RawPdfPage:
    words = [RawWord(text=token, bbox=BOX) for token in TEXT.split()]
    line = RawTextLine(text=TEXT, bbox=BOX, words=words)
    return RawPdfPage(
        page_index=index, width_pt=612.0, height_pt=792.0, lines=[line] * 4, image_area_ratio=0.0, path_area_ratio=0.0
    )


def _ok(index: int) -> PageMessage:
    return PageMessage(page=_page(index))


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _FakeClock:
    fake = _FakeClock()
    monkeypatch.setattr(pdf_reader, "time", fake)
    return fake


def _patch_start(
    monkeypatch: pytest.MonkeyPatch, sessions: list[_Session], page_count: int
) -> list[TemplateScan | None]:
    """`_start` trả lần lượt các session giả (hết thì worker không khởi động được); ghi kết quả quét truyền vào.

    `None` trong danh sách trả về nghĩa là worker đó được yêu cầu tự quét (như `WorkerPdfReader._start`).
    """
    held: list[TemplateScan | None] = []

    def start(
        self: WorkerPdfReader, path: Path, page_budget: int, template_scan: TemplateScan | None = None
    ) -> tuple[_Session, int]:
        held.append(template_scan)
        if not sessions:
            raise PdfWorkerError("hết worker giả")
        return sessions.pop(0), page_count

    monkeypatch.setattr(WorkerPdfReader, "_start", start)
    return held


def _stub(tmp_path: Path) -> Path:
    path = tmp_path / "stub.pdf"
    path.write_bytes(b"%PDF-1.7\n")
    return path


def _summary(reader: WorkerPdfReader, path: Path) -> list[tuple[str, str | None]]:
    return [(p.status, p.failure_reason) for p in reader.read_pages(path, DOC_ID)]


def _config(**update: object) -> PdfReaderConfig:
    return PdfReaderConfig.model_validate({**PdfReaderConfig().model_dump(), **update})


def test_scan_start_without_permission_fails_the_page_without_waiting(clock: _FakeClock) -> None:
    session = _Session(clock, [TemplateScanStartedMessage(), _After(4.0, TemplateScanMessage(scan=OK_SCAN)), _ok(0)])
    reader = WorkerPdfReader(_config(template_scan_timeout_s=5.0))
    outcome = reader._read_page(session, 0, DOC_ID, 1.0, 2.0)  # mặc định không nhận lượt quét
    assert (outcome.page.status, outcome.page.failure_reason) == ("failed", "PAGE_ERROR")
    assert (outcome.healthy, outcome.rerun, outcome.template_scan, outcome.scan_wait_s) == (False, False, None, 0.0)
    assert outcome.expired_wait is None
    assert session.killed and session.timeouts == [1.0]  # không chờ lượt quét
    assert clock.now == clock.start
    # `_next_page` (không phải lượt chờ trang đầu của worker được yêu cầu quét) cũng từ chối.
    again = _Session(clock, [TemplateScanStartedMessage()])
    page, healthy = reader._next_page(again, 0, DOC_ID)
    assert (page.status, page.failure_reason, healthy, again.killed) == ("failed", "PAGE_ERROR", False, True)


def test_restarted_worker_that_starts_a_scan_fails_the_page_and_keeps_the_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clock: _FakeClock
) -> None:
    # Hạn của trang lớn hơn hạn tổng: hạn chờ mỗi trang bằng đúng phần hạn tổng còn lại, nên đo được hạn tổng.
    config = _config(document_timeout_s=10.0, text_layer_page_timeout_s=100.0, template_scan_timeout_s=5.0)
    first = _Session(
        clock, [TemplateScanStartedMessage(), _After(3.0, TemplateScanMessage(scan=OK_SCAN)), _ok(0), None]
    )
    # Worker dựng lại (đã nhận kết quả quét) lại báo bắt đầu quét: bị từ chối, kết quả quét thứ hai không được đọc.
    second = _Session(
        clock, [_After(1.0, TemplateScanStartedMessage()), _After(4.0, TemplateScanMessage(scan=OTHER_SCAN))]
    )
    third = _Session(clock, [_ok(3)])
    held = _patch_start(monkeypatch, [first, second, third], page_count=4)
    assert _summary(WorkerPdfReader(config), _stub(tmp_path)) == [
        ("ok", None),
        ("failed", "PAGE_ERROR"),  # EOF
        ("failed", "PAGE_ERROR"),  # quét khi không được yêu cầu
        ("ok", None),
    ]
    assert held == [None, OK_SCAN, OK_SCAN]  # một tập ảnh template duy nhất cho mọi worker
    assert second.killed and second.outcomes == [_After(4.0, TemplateScanMessage(scan=OTHER_SCAN))]
    assert first.timeouts == [pytest.approx(10.0), 5.0, pytest.approx(10.0), pytest.approx(10.0)]
    # Hạn tổng = 10 s + đúng một lượt chờ quét (3 s); lượt quét bị từ chối không cộng gì: còn 1013 - 1004 = 9 s.
    assert second.timeouts == [pytest.approx(10.0)]
    assert third.timeouts == [pytest.approx(9.0)]


@pytest.mark.parametrize(
    "mid_document",
    [
        [TemplateScanStartedMessage(), TemplateScanMessage(scan=OTHER_SCAN)],
        [TemplateScanMessage(scan=OTHER_SCAN)],
    ],
)
def test_second_scan_from_the_same_worker_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clock: _FakeClock, mid_document: list[object]
) -> None:
    config = _config(document_timeout_s=10.0, text_layer_page_timeout_s=100.0, template_scan_timeout_s=5.0)
    first = _Session(clock, [TemplateScanStartedMessage(), TemplateScanMessage(scan=OK_SCAN), _ok(0), *mid_document])
    second = _Session(clock, [_ok(2)])
    held = _patch_start(monkeypatch, [first, second], page_count=3)
    assert _summary(WorkerPdfReader(config), _stub(tmp_path)) == [("ok", None), ("failed", "PAGE_ERROR"), ("ok", None)]
    assert first.killed and first.outcomes == mid_document[1:]
    assert held == [None, OK_SCAN]  # kết quả giữa chừng không ghi đè kết quả đã giữ
    assert first.timeouts == [pytest.approx(10.0), 5.0, pytest.approx(10.0), pytest.approx(10.0)]
    assert second.timeouts == [pytest.approx(10.0)]


def test_scan_is_accepted_only_in_the_first_wait_of_a_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clock: _FakeClock
) -> None:
    config = _config(document_timeout_s=10.0, text_layer_page_timeout_s=100.0, template_scan_timeout_s=5.0)
    # Worker được yêu cầu quét nhưng trả trang đầu ngay; báo quét ở trang thứ hai thì bị từ chối.
    first = _Session(clock, [_ok(0), TemplateScanStartedMessage(), TemplateScanMessage(scan=OTHER_SCAN), _ok(1)])
    # Chưa giữ kết quả quét nào ⇒ worker dựng lại được yêu cầu quét, và lượt quét ở lượt chờ đầu của nó được nhận.
    second = _Session(
        clock,
        [
            TemplateScanStartedMessage(),
            _After(2.0, TemplateScanMessage(scan=OK_SCAN)),
            _ok(2),
            TemplateScanStartedMessage(),
        ],
    )
    third = _Session(clock, [_ok(4)])
    held = _patch_start(monkeypatch, [first, second, third], page_count=5)
    pages = _summary(WorkerPdfReader(config), _stub(tmp_path))
    assert pages[:3] == [("ok", None), ("failed", "PAGE_ERROR"), ("ok", None)]
    assert first.killed and first.outcomes == [TemplateScanMessage(scan=OTHER_SCAN), _ok(1)]
    assert second.timeouts == [pytest.approx(10.0), 5.0, pytest.approx(10.0), pytest.approx(10.0)]
    assert held[:2] == [None, None]
    # Lượt quét thứ hai cũng bị từ chối với worker này (đã qua lượt chờ đầu), rồi worker sau nhận kết quả đã giữ.
    assert pages[3:] == [("failed", "PAGE_ERROR"), ("ok", None)]
    assert held[2:] == [OK_SCAN]
    assert third.timeouts == [pytest.approx(10.0)]  # hạn tổng 1012 (lượt quét được nhận cộng 2 s), đồng hồ 1002


def test_workers_that_go_silent_after_scan_start_end_within_one_scan_wait(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clock: _FakeClock
) -> None:
    config = _config(document_timeout_s=10.0, text_layer_page_timeout_s=2.0, template_scan_timeout_s=4.0)
    page_count = 6
    # Mọi worker báo bắt đầu quét rồi im lặng. Dư worker để một bản lỗi (nhận lại lượt quét) không dừng sớm.
    sessions = [_Session(clock, [TemplateScanStartedMessage(), SILENT, SILENT, SILENT]) for _ in range(30)]
    first = sessions[0]
    held = _patch_start(monkeypatch, list(sessions), page_count=page_count)
    pages = _summary(WorkerPdfReader(config), _stub(tmp_path))
    assert pages == [("failed", "PAGE_ERROR")] * page_count
    elapsed = clock.now - clock.start
    assert elapsed <= config.document_timeout_s + config.template_scan_timeout_s
    # Đúng một lượt chờ quét (của worker đầu), các worker dựng lại bị từ chối ngay khi báo bắt đầu quét.
    assert elapsed == pytest.approx(config.template_scan_timeout_s - CLOCK_SHORTFALL_S)
    assert first.timeouts == [pytest.approx(config.text_layer_page_timeout_s), config.template_scan_timeout_s]
    assert held[0] is None and held[1:] == [TemplateScan(status="timeout")] * page_count
    assert all(s.killed and s.timeouts == [pytest.approx(2.0)] for s in sessions[1 : page_count + 1])


LOGGER = "holocron_core.infrastructure.document_readers.pdf_reader"


@pytest.mark.parametrize(("status", "logged"), [("skipped", 0), ("ok", 0), ("error", 1)])
def test_only_unfinished_scans_are_logged_and_the_held_scan_is_reused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    clock: _FakeClock,
    caplog: pytest.LogCaptureFixture,
    status: str,
    logged: int,
) -> None:
    scan = OK_SCAN if status == "ok" else TemplateScan.model_validate({"status": status})
    first = _Session(clock, [TemplateScanStartedMessage(), TemplateScanMessage(scan=scan), _ok(0), None])
    second = _Session(clock, [_ok(2)])
    held = _patch_start(monkeypatch, [first, second], page_count=3)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        pages = _summary(WorkerPdfReader(_config()), _stub(tmp_path))
    assert pages == [("ok", None), ("failed", "PAGE_ERROR"), ("ok", None)]
    records = [r for r in caplog.records if r.name == LOGGER]
    assert len(records) == logged
    assert all(status in r.getMessage() for r in records)
    # Worker dựng lại nhận đúng kết quả quét lần đầu, không quét lại.
    assert held == [None, scan]
