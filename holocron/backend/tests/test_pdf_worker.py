"""Worker đọc PDF tầng 1 (ADR-008 mục 3): tiến trình con có giới hạn, timeout, adapter pypdfium2, giao thức."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

from holocron_core.infrastructure.document_readers import _pdf_worker, pdf_reader
from holocron_core.infrastructure.document_readers.config import PdfReaderConfig
from holocron_core.infrastructure.document_readers.pdf_reader import (
    PdfInputError,
    PdfWorkerError,
    WorkerPdfReader,
    WorkerSession,
)
from holocron_core.infrastructure.document_readers.pdfium_adapter import (
    PdfiumBackend,
    PdfiumDataError,
    PdfiumDocument,
    _as_box,
    _as_float,
    _as_int,
    decode_code_units,
)
from holocron_core.infrastructure.document_readers.protocol import (
    MESSAGE_PREFIX,
    OpenedMessage,
    OpenFailedMessage,
    PageErrorMessage,
    PageMessage,
    PageRequest,
    RawPdfPage,
    ReadyMessage,
    WorkerRequest,
    decode_message,
    encode_message,
)
from tests.pdf_samples import Box, Page, Text, build_pdf, slow_page, text_lines, write_pdf

DOC_ID = "doc_00000000000000cc"
BODY = [
    "The scheduler gives each ready process the processor for one time quantum.",
    "When the quantum expires the process moves to the tail of the ready queue.",
    "Shorter quanta improve response time but add context switch overhead.",
    "The average waiting time depends on the quantum and on arrival order.",
]
# TCVN3 trong PDF WinAnsi: "ư" là byte 0xAD (U+00AD), không phải U+2212.
TCVN3 = ["HÖ ®iÒu hµnh cÊp ph¸t CPU cho c¸c tiÕn tr×nh theo l\u00adît vµ ®\u00adîc ch¹y tèi ®a mét l\u00adîng tö."] * 4


def _table_texts(top: float = 500.0) -> list[Text]:
    rows = [("Process", "Arrival", "Burst"), ("P1", "0", "5"), ("P2", "1", "3"), ("P3", "2", "8")]
    return [
        Text(x=x, y=top - 18.0 * r, value=value)
        for r, row in enumerate(rows)
        for x, value in zip((72.0, 250.0, 430.0), row, strict=True)
    ]


def _validation_error() -> ValidationError:
    try:
        decode_message(MESSAGE_PREFIX + '{"kind": "page"}')
    except ValidationError as exc:
        return exc
    raise AssertionError("cần ValidationError")


# --------------------------------------------------------------------------- #
# Tiến trình con
# --------------------------------------------------------------------------- #


def test_worker_is_killed_on_page_timeout(tmp_path: Path) -> None:
    path = write_pdf(tmp_path / "slow.pdf", [slow_page()])
    request = WorkerRequest(path=str(path), page_budget=1, config=PdfReaderConfig())
    with WorkerSession(request) as session:
        assert isinstance(session.receive(30.0), ReadyMessage)
        opened = session.receive(5.0)
        assert isinstance(opened, OpenedMessage) and opened.page_count == 1
        session.request_page(0)
        with pytest.raises(TimeoutError):
            session.receive(0.3)
        assert session.process.poll() is None, "worker vẫn đang đọc trang chậm"
        session.kill()
        assert session.process.poll() is not None, "worker phải bị kill khi quá hạn"


def test_worker_runs_in_temp_cwd_with_minimal_environment() -> None:
    env = pdf_reader._worker_env()
    assert set(env) <= {"PYTHONHASHSEED", "PYTHONIOENCODING", "PYTHONUTF8", "PYTHONPATH", "SYSTEMROOT"}
    assert env["PYTHONIOENCODING"] == "utf-8"
    assert Path(env["PYTHONPATH"], "holocron_core").is_dir()
    if sys.platform == "win32":
        assert "SYSTEMROOT" in env


def test_reader_reads_text_layer_with_flags(tmp_path: Path) -> None:
    pages = [
        Page(texts=text_lines(BODY)),
        Page(texts=text_lines(TCVN3)),
        Page(texts=text_lines(BODY) + _table_texts()),
        Page(texts=text_lines(BODY), images=[Box(100.0, 100.0, 400.0, 300.0)]),
        Page(texts=text_lines(BODY) + [Text(56.0, 600.0, "S = a + b \xe5 \xf2 \xd6 \xa3 \xb3 \xb1", font="F2")]),
        Page(texts=text_lines(BODY), rects=[Box(20.0, 20.0, 572.0, 752.0)]),
        Page(texts=text_lines(BODY), rotate=90),
    ]
    path = write_pdf(tmp_path / "mixed.pdf", pages)
    result = list(WorkerPdfReader().read_pages(path, DOC_ID))
    assert [p.page_number for p in result] == [1, 2, 3, 4, 5, 6, 7]

    plain = result[0]
    assert (plain.status, plain.tier_used, plain.review_reasons) == ("ok", "text_layer", [])
    assert [line.text for line in plain.lines] == BODY
    first = plain.lines[0]
    assert first.bbox is not None
    x0, y0, x1, y1 = first.bbox
    assert 0.08 < x0 < 0.1 and 0.05 < y0 < y1 < 0.08 and x1 > x0  # dòng đầu ở gần mép trên-trái
    assert plain.lines[1].bbox is not None and plain.lines[1].bbox[1] > y0
    assert plain.text_score is not None and plain.text_score >= 0.85

    garbled = result[1]
    assert (garbled.status, garbled.failure_reason, garbled.lines) == ("failed", "OCR_ENGINE_MISSING", [])

    table = result[2]
    assert table.has_table and table.status == "needs_review" and table.review_reasons == ["TIER3_UNAVAILABLE"]
    assert not result[0].has_table

    figure = result[3]
    # GĐ0 0C (§5.7, 2026-10-31): hình trên PDF số chỉ ghi vùng, không còn TIER3_UNAVAILABLE.
    assert figure.has_figure and figure.review_reasons == []

    formula = result[4]
    assert formula.has_formula and "∑" in " ".join(line.text for line in formula.lines)

    framed = result[5]
    assert not framed.has_figure and framed.status == "ok"  # khung viền vector một mình không phải hình

    rotated = result[6]
    assert rotated.lines[0].bbox is not None
    rx0, _, rx1, _ = rotated.lines[0].bbox
    assert rx0 > 0.85 and rx1 <= 1.0  # trang xoay 90°: dòng đầu nằm sát mép phải khi hiển thị


def test_reader_reads_selected_pages_in_order(tmp_path: Path) -> None:
    pages = [Page(texts=text_lines([f"Page {n}: " + BODY[0]] + BODY[1:])) for n in range(1, 5)]
    path = write_pdf(tmp_path / "four.pdf", pages)
    reader = WorkerPdfReader()
    result = list(reader.read_pages(path, DOC_ID, [3, 1]))
    assert [p.page_number for p in result] == [3, 1]
    assert result[0].lines[0].text.startswith("Page 3:")
    with pytest.raises(ValueError, match="trang"):
        list(reader.read_pages(path, DOC_ID, [5]))
    with pytest.raises(ValueError, match="từ 1"):
        list(reader.read_pages(path, DOC_ID, [0]))


def test_worker_that_cannot_start_raises_worker_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = write_pdf(tmp_path / "doc.pdf", [Page(texts=text_lines(BODY))])
    monkeypatch.setattr(pdf_reader, "WORKER_MODULE", "holocron_core.infrastructure.document_readers.missing")
    with pytest.raises(PdfWorkerError):
        WorkerPdfReader().probe(path)


# --------------------------------------------------------------------------- #
# Tiến trình cha xử lý thông điệp worker (session giả, không spawn)
# --------------------------------------------------------------------------- #


class FakeSession:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = outcomes
        self.killed = False
        self.requested: list[int] = []

    def request_page(self, index: int) -> None:
        self.requested.append(index)

    def receive(self, timeout_s: float) -> object:
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def kill(self) -> None:
        self.killed = True


def _raw(index: int) -> RawPdfPage:
    return RawPdfPage(page_index=index, width_pt=612, height_pt=792, lines=[], image_area_ratio=0, path_area_ratio=0)


@pytest.mark.parametrize(
    ("outcome", "status", "reason", "healthy"),
    [
        (PageMessage(page=_raw(0)), "failed", "OCR_ENGINE_MISSING", True),  # trang trống: cần OCR
        (PageErrorMessage(page_index=0, message="PdfiumError"), "failed", "PAGE_ERROR", True),
        (TimeoutError(), "failed", "PAGE_TIMEOUT", False),
        (None, "failed", "PAGE_ERROR", False),  # worker chết (EOF)
        (PageMessage(page=_raw(1)), "failed", "PAGE_ERROR", False),  # trả nhầm trang
        (OpenedMessage(page_count=1), "failed", "PAGE_ERROR", False),
    ],
)
def test_next_page_outcomes(outcome: object, status: str, reason: str, healthy: bool) -> None:
    session = FakeSession([outcome])
    page, ok = WorkerPdfReader()._next_page(session, 0, DOC_ID)
    assert (page.status, page.failure_reason, ok) == (status, reason, healthy)
    assert session.requested == [0] and session.killed == (not healthy)


def test_next_page_invalid_message_kills_worker() -> None:
    session = FakeSession([_validation_error()])
    page, ok = WorkerPdfReader()._next_page(session, 0, DOC_ID)
    assert (page.failure_reason, ok, session.killed) == ("PAGE_ERROR", False, True)


@pytest.mark.parametrize(
    ("outcomes", "error"),
    [
        ([TimeoutError()], PdfWorkerError),
        ([None], PdfWorkerError),
        ([OpenedMessage(page_count=1)], PdfWorkerError),
        ([ReadyMessage(), TimeoutError()], "OPEN_TIMEOUT"),
        ([ReadyMessage(), "invalid"], "OPEN_FAILED"),
        ([ReadyMessage(), None], "OPEN_FAILED"),
        ([ReadyMessage(), OpenedMessage(page_count=0)], "OPEN_FAILED"),
        ([ReadyMessage(), OpenFailedMessage(code="PASSWORD_REQUIRED")], "PASSWORD_REQUIRED"),
        ([ReadyMessage(), OpenedMessage(page_count=301)], "TOO_MANY_PAGES"),
    ],
)
def test_open_handshake_failures(outcomes: list[object], error: object) -> None:
    resolved = [_validation_error() if o == "invalid" else o for o in outcomes]
    session = FakeSession(resolved)
    if isinstance(error, str):
        with pytest.raises(PdfInputError) as excinfo:
            WorkerPdfReader()._open(session)
        assert excinfo.value.code == error
    else:
        with pytest.raises(PdfWorkerError):
            WorkerPdfReader()._open(session)


# --------------------------------------------------------------------------- #
# Vòng đọc của worker và adapter pypdfium2, chạy ngay trong tiến trình test (không áp giới hạn OS)
# --------------------------------------------------------------------------- #


def _run(
    path: Path, pages: list[int], *, budget: int | None = None, config: PdfReaderConfig | None = None
) -> list[BaseModel]:
    emitted: list[BaseModel] = []
    request = WorkerRequest(
        path=str(path), page_budget=len(pages) if budget is None else budget, config=config or PdfReaderConfig()
    )
    requests = [PageRequest(page_index=i) for i in pages]
    _pdf_worker.run(request, PdfiumBackend.load(), requests, emitted.append)
    return emitted


def test_worker_loop_reads_requested_pages(tmp_path: Path) -> None:
    path = write_pdf(tmp_path / "two.pdf", [Page(texts=text_lines(BODY)), Page(texts=text_lines(BODY[:2]))])
    emitted = _run(path, [1, 0, 5])
    assert isinstance(emitted[0], OpenedMessage) and emitted[0].page_count == 2
    assert isinstance(emitted[1], PageMessage) and emitted[1].page.page_index == 1
    assert [line.text for line in emitted[1].page.lines] == BODY[:2]
    assert isinstance(emitted[2], PageMessage) and emitted[2].page.page_index == 0
    assert isinstance(emitted[3], PageErrorMessage) and emitted[3].page_index == 5


def test_worker_loop_respects_page_budget_and_page_limit(tmp_path: Path) -> None:
    path = write_pdf(tmp_path / "three.pdf", [Page(texts=text_lines(BODY)) for _ in range(3)])
    assert len(_run(path, [0, 1, 2], budget=1)) == 2  # opened + 1 trang
    limited = _run(path, [0], config=PdfReaderConfig(max_pages=2))
    assert len(limited) == 1 and isinstance(limited[0], OpenedMessage) and limited[0].page_count == 3


def test_worker_loop_open_failures(tmp_path: Path) -> None:
    locked = write_pdf(tmp_path / "locked.pdf", [Page(texts=text_lines(BODY))], user_password="secret")
    assert _run(locked, [0]) == [OpenFailedMessage(code="PASSWORD_REQUIRED")]
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-1.7\nnot really a pdf")
    assert _run(broken, [0]) == [OpenFailedMessage(code="OPEN_FAILED")]
    not_pdf = tmp_path / "not.pdf"
    not_pdf.write_bytes(b"GIF89a")
    assert _run(not_pdf, [0]) == [OpenFailedMessage(code="OPEN_FAILED")]
    big = write_pdf(tmp_path / "big.pdf", [Page(texts=text_lines(BODY))])
    assert _run(big, [0], config=PdfReaderConfig(max_bytes=100)) == [OpenFailedMessage(code="OPEN_FAILED")]


def test_worker_loop_turns_page_exception_into_page_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = write_pdf(tmp_path / "one.pdf", [Page(texts=text_lines(BODY))])

    def boom(self: PdfiumDocument, index: int, *_: object) -> RawPdfPage:  # GĐ0: read_page nhận thêm cấu hình
        raise PdfiumDataError("hỏng")

    monkeypatch.setattr(PdfiumDocument, "read_page", boom)
    emitted = _run(path, [0])
    assert emitted[1] == PageErrorMessage(page_index=0, message="PdfiumDataError")


def test_adapter_reads_page_size_and_object_areas() -> None:
    data = build_pdf([Page(texts=text_lines(BODY), images=[Box(0.0, 0.0, 306.0, 792.0)])])
    backend = PdfiumBackend.load()
    document = backend.open(data)
    assert isinstance(document, PdfiumDocument)
    with document:
        assert document.page_count() == 1
        raw = document.read_page(0)
    assert raw.image_area_ratio == pytest.approx(0.5)
    assert (raw.width_pt, raw.height_pt) == (612.0, 792.0)
    assert backend.open(b"%PDF-1.4 broken") == "OPEN_FAILED"


def test_adapter_type_guards() -> None:
    assert _as_int(3) == 3 and _as_float(2) == 2.0
    assert _as_box([0, 1, 2, 3]) == (0.0, 1.0, 2.0, 3.0)
    for bad in (True, "3", 1.5):
        with pytest.raises(PdfiumDataError):
            _as_int(bad)
    for bad_float in (False, "1.0"):
        with pytest.raises(PdfiumDataError):
            _as_float(bad_float)
    for bad_box in ((1, 2, 3), "abcd", None):
        with pytest.raises(PdfiumDataError):
            _as_box(bad_box)


def test_protocol_ignores_foreign_lines_and_rejects_malformed_messages() -> None:
    assert decode_message("pdfium warning: something\n") is None
    message = OpenedMessage(page_count=4)
    assert decode_message(encode_message(message) + "\r\n") == message
    with pytest.raises(ValidationError):
        decode_message(MESSAGE_PREFIX + '{"kind": "opened", "page_count": -1}')
    with pytest.raises(ValidationError):
        decode_message(MESSAGE_PREFIX + '{"kind": "page", "page": {"page_index": 0, "width_pt": NaN}}')


def test_decode_code_units_joins_surrogate_pairs() -> None:
    math_bold_a = 0x1D400
    high = 0xD800 + ((math_bold_a - 0x10000) >> 10)
    low = 0xDC00 + ((math_bold_a - 0x10000) & 0x3FF)
    assert decode_code_units([0x41, high, low, 0x42]) == [("A", 0), (chr(math_bold_a), 1), ("B", 3)]
    assert decode_code_units([math_bold_a]) == [(chr(math_bold_a), 0)]  # Linux: mã đầy đủ
    replacement = chr(0xFFFD)
    assert decode_code_units([high, 0x41]) == [(replacement, 0), ("A", 1)]
    assert decode_code_units([low, 0x110000, -1]) == [(replacement, 0), (replacement, 1), (replacement, 2)]


def test_page_failing_domain_validation_becomes_page_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def invalid(*_: object) -> object:
        raise _validation_error()

    monkeypatch.setattr(pdf_reader, "assemble_text_layer_page", invalid)
    session = FakeSession([PageMessage(page=_raw(0))])
    page, ok = WorkerPdfReader()._next_page(session, 0, DOC_ID)
    assert (page.failure_reason, ok, session.killed) == ("PAGE_ERROR", True, False)
