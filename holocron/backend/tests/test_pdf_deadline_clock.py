"""Hạn tổng của tài liệu không phụ thuộc độ phân giải đồng hồ (ADR-008 mục 4).

Trên Windows, lượt chờ có thể trả về `TimeoutError` khi `time.monotonic()` mới tăng ít hơn timeout một chút. Khi
lượt chờ hết hạn đã bị giới hạn bởi phần hạn tổng còn lại, `read_pages` phải coi hạn tổng là đã hết: không dựng
worker mới, mọi trang còn lại thành `failed` (`PAGE_TIMEOUT`). Timeout của riêng một trang vẫn dựng worker mới.

Đồng hồ của module `pdf_reader` được thay bằng đồng hồ giả; session giả ném `TimeoutError` ngay (không ngủ) sau
khi cho đồng hồ trôi ít hơn timeout một chút, nên các test này tất định trên mọi nền tảng.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from holocron_core.infrastructure.document_readers import pdf_reader
from holocron_core.infrastructure.document_readers.config import PdfReaderConfig
from holocron_core.infrastructure.document_readers.pdf_reader import WorkerPdfReader
from holocron_core.infrastructure.document_readers.protocol import (
    OcrStartedMessage,
    PageMessage,
    RawPdfPage,
    RawTextLine,
    RawWord,
)

DOC_ID = "doc_000000000000c10c"
# Đồng hồ trôi ít hơn timeout đúng chừng này khi lượt chờ hết hạn (mô phỏng độ phân giải thô của Windows).
CLOCK_SHORTFALL_S = 0.001
TEXT = "The scheduler gives each ready process the processor for one time quantum in turn."
BOX = (0.1, 0.1, 0.9, 0.12)


class _FakeClock:
    """Thay module `time` trong `pdf_reader`: chỉ trôi khi session giả cho trôi."""

    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class _ClockSession:
    """Session giả: `"timeout"` cho đồng hồ trôi `timeout_s - CLOCK_SHORTFALL_S` rồi ném `TimeoutError` ngay."""

    def __init__(self, clock: _FakeClock, outcomes: list[object]) -> None:
        self.clock = clock
        self.outcomes = outcomes
        self.requested: list[int] = []
        self.timeouts: list[float] = []
        self.closed = False

    def request_page(self, index: int) -> None:
        self.requested.append(index)

    def receive(self, timeout_s: float) -> object:
        self.timeouts.append(timeout_s)
        outcome = self.outcomes.pop(0)
        if outcome == "timeout":
            self.clock.now += timeout_s - CLOCK_SHORTFALL_S
            raise TimeoutError
        return outcome

    def kill(self) -> None:
        self.closed = True

    def close(self) -> None:
        self.closed = True


def _page(index: int) -> RawPdfPage:
    """Trang có lớp chữ tốt (đọc xong ở tầng 1, không cần OCR)."""
    words = [RawWord(text=token, bbox=BOX) for token in TEXT.split()]
    line = RawTextLine(text=TEXT, bbox=BOX, words=words)
    return RawPdfPage(
        page_index=index, width_pt=612.0, height_pt=792.0, lines=[line] * 4, image_area_ratio=0.0, path_area_ratio=0.0
    )


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _FakeClock:
    fake = _FakeClock()
    monkeypatch.setattr(pdf_reader, "time", fake)
    return fake


def _patch_start(monkeypatch: pytest.MonkeyPatch, sessions: list[_ClockSession]) -> list[int]:
    """`_start` trả lần lượt các session giả; trả danh sách `page_budget` của mỗi lần dựng worker."""
    budgets: list[int] = []

    def start(self: WorkerPdfReader, path: Path, page_budget: int) -> tuple[_ClockSession, int]:
        budgets.append(page_budget)
        return sessions.pop(0), 3

    monkeypatch.setattr(WorkerPdfReader, "_start", start)
    return budgets


def _pdf_stub(tmp_path: Path) -> Path:
    path = tmp_path / "stub.pdf"
    path.write_bytes(b"%PDF-1.7\n")
    return path


def _summary(reader: WorkerPdfReader, path: Path) -> list[tuple[int, str, str | None]]:
    return [(p.page_number, p.status, p.failure_reason) for p in reader.read_pages(path, DOC_ID)]


def test_text_layer_wait_capped_by_document_deadline_ends_the_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clock: _FakeClock
) -> None:
    first = _ClockSession(clock, ["timeout"])
    spare = _ClockSession(clock, [PageMessage(page=_page(1)), PageMessage(page=_page(2))])
    budgets = _patch_start(monkeypatch, [first, spare])
    config = PdfReaderConfig(document_timeout_s=0.3, text_layer_page_timeout_s=5.0)
    assert _summary(WorkerPdfReader(config), _pdf_stub(tmp_path)) == [
        (1, "failed", "PAGE_TIMEOUT"),
        (2, "failed", "PAGE_TIMEOUT"),
        (3, "failed", "PAGE_TIMEOUT"),
    ]
    assert first.timeouts == [pytest.approx(0.3)] and first.closed
    assert budgets == [config.max_pages]  # đồng hồ còn báo 1 ms nhưng không dựng worker mới
    assert spare.requested == []


def test_remaining_budget_never_exceeds_the_document_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clock: _FakeClock
) -> None:
    # Với đồng hồ lớn, (t0 + 0.3) - t0 = 0.30000000000291 trên số thực: hạn chờ không được vượt hạn tổng.
    clock.now = 100000.123
    assert (clock.now + 0.3) - clock.now > 0.3  # điều kiện tái hiện lỗi làm tròn
    first = _ClockSession(clock, ["timeout"])
    spare = _ClockSession(clock, [PageMessage(page=_page(1)), PageMessage(page=_page(2))])
    budgets = _patch_start(monkeypatch, [first, spare])
    config = PdfReaderConfig(document_timeout_s=0.3, text_layer_page_timeout_s=5.0)
    assert _summary(WorkerPdfReader(config), _pdf_stub(tmp_path)) == [
        (1, "failed", "PAGE_TIMEOUT"),
        (2, "failed", "PAGE_TIMEOUT"),
        (3, "failed", "PAGE_TIMEOUT"),
    ]
    assert first.timeouts == [config.document_timeout_s]
    assert budgets == [config.max_pages] and spare.requested == []


def test_ocr_wait_capped_by_document_deadline_ends_the_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clock: _FakeClock
) -> None:
    first = _ClockSession(clock, [OcrStartedMessage(page_index=0), "timeout"])
    spare = _ClockSession(clock, [PageMessage(page=_page(1)), PageMessage(page=_page(2))])
    budgets = _patch_start(monkeypatch, [first, spare])
    config = PdfReaderConfig(document_timeout_s=10.0, text_layer_page_timeout_s=5.0, ocr_page_timeout_s=30.0)
    assert _summary(WorkerPdfReader(config), _pdf_stub(tmp_path)) == [
        (1, "failed", "PAGE_TIMEOUT"),
        (2, "failed", "PAGE_TIMEOUT"),
        (3, "failed", "PAGE_TIMEOUT"),
    ]
    assert first.timeouts == [pytest.approx(5.0), pytest.approx(10.0)]
    assert budgets == [config.max_pages] and spare.requested == []


def test_page_timeout_below_the_document_deadline_still_restarts_the_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clock: _FakeClock
) -> None:
    first = _ClockSession(clock, ["timeout"])
    second = _ClockSession(clock, [PageMessage(page=_page(1)), PageMessage(page=_page(2))])
    budgets = _patch_start(monkeypatch, [first, second])
    config = PdfReaderConfig(document_timeout_s=600.0, text_layer_page_timeout_s=0.05)
    assert _summary(WorkerPdfReader(config), _pdf_stub(tmp_path)) == [
        (1, "failed", "PAGE_TIMEOUT"),
        (2, "ok", None),
        (3, "ok", None),
    ]
    assert budgets == [config.max_pages, 2] and second.requested == [1, 2]


def test_text_layer_timeout_restarts_even_when_only_the_ocr_wait_would_be_capped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clock: _FakeClock
) -> None:
    # Hạn tổng còn 10 s: hạn OCR (30 s) bị giới hạn, hạn lớp chữ (5 s) thì không. Lượt chờ hết hạn là lớp chữ.
    first = _ClockSession(clock, ["timeout"])
    second = _ClockSession(clock, [PageMessage(page=_page(1)), PageMessage(page=_page(2))])
    budgets = _patch_start(monkeypatch, [first, second])
    config = PdfReaderConfig(document_timeout_s=10.0, text_layer_page_timeout_s=5.0, ocr_page_timeout_s=30.0)
    assert [r for _, _, r in _summary(WorkerPdfReader(config), _pdf_stub(tmp_path))] == ["PAGE_TIMEOUT", None, None]
    assert first.timeouts == [pytest.approx(5.0)]
    assert budgets == [config.max_pages, 2] and second.requested == [1, 2]


def test_ocr_timeout_below_the_document_deadline_still_restarts_the_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clock: _FakeClock
) -> None:
    first = _ClockSession(clock, [OcrStartedMessage(page_index=0), "timeout"])
    second = _ClockSession(clock, [PageMessage(page=_page(1)), PageMessage(page=_page(2))])
    budgets = _patch_start(monkeypatch, [first, second])
    config = PdfReaderConfig(document_timeout_s=600.0, text_layer_page_timeout_s=5.0, ocr_page_timeout_s=30.0)
    assert [r for _, _, r in _summary(WorkerPdfReader(config), _pdf_stub(tmp_path))] == ["PAGE_TIMEOUT", None, None]
    assert first.timeouts == [pytest.approx(5.0), pytest.approx(30.0)]
    assert budgets == [config.max_pages, 2] and second.requested == [1, 2]
