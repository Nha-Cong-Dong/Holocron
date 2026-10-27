"""Giới hạn file PDF xấu (ADR-008 mục 4, 14; SPECIFICATION §6.5). File mẫu đều tự tạo lúc chạy test."""

from __future__ import annotations

from pathlib import Path

import pytest

from holocron_core.infrastructure.document_readers.config import PdfReaderConfig
from holocron_core.infrastructure.document_readers.pdf_reader import (
    PdfInputError,
    WorkerPdfReader,
    check_pdf_input,
)
from tests.pdf_samples import Page, slow_page, text_lines, write_pdf

DOC_ID = "doc_00000000000000bb"
MIB = 1024 * 1024
BODY = ["The scheduler gives each ready process the processor for one time quantum."] * 6


def _expect(code: str, reader: WorkerPdfReader, path: Path) -> None:
    with pytest.raises(PdfInputError) as excinfo:
        reader.probe(path)
    assert excinfo.value.code == code
    with pytest.raises(PdfInputError) as excinfo:
        list(reader.read_pages(path, DOC_ID))
    assert excinfo.value.code == code


def test_bad_pdf_limits(tmp_path: Path) -> None:
    """ADR-008 mục 14, test 6: magic bytes, số trang, kích thước và timeout."""
    reader = WorkerPdfReader()

    # Magic bytes: ảnh PNG đổi đuôi .pdf, hoặc văn bản thường, bị từ chối trước khi mở worker.
    png = tmp_path / "image.pdf"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    _expect("NOT_PDF", reader, png)
    text = tmp_path / "notes.pdf"
    text.write_bytes(b"hello %PDF-1.4")
    _expect("NOT_PDF", reader, text)

    # Kích thước: rỗng, và vượt 50 MiB (file thưa, không tốn đĩa).
    empty = tmp_path / "empty.pdf"
    empty.write_bytes(b"")
    _expect("EMPTY", reader, empty)
    huge = tmp_path / "huge.pdf"
    with huge.open("wb") as handle:
        handle.write(b"%PDF-1.4\n")
        handle.truncate(50 * MIB + 1)
    _expect("TOO_LARGE", reader, huge)

    # Số trang: 300 trang được nhận, 301 trang bị từ chối.
    assert reader.probe(write_pdf(tmp_path / "p300.pdf", [Page() for _ in range(300)])) == 300
    _expect("TOO_MANY_PAGES", reader, write_pdf(tmp_path / "p301.pdf", [Page() for _ in range(301)]))

    # Timeout theo trang: trang chậm thành failed PAGE_TIMEOUT, trang sau vẫn được đọc bằng worker mới
    # (việc kill worker được kiểm riêng ở test_pdf_worker.py::test_worker_is_killed_on_page_timeout).
    fast_timeout = WorkerPdfReader(PdfReaderConfig(text_layer_page_timeout_s=0.5))
    path = write_pdf(tmp_path / "slow.pdf", [slow_page(), Page(texts=text_lines(BODY))])
    pages = list(fast_timeout.read_pages(path, DOC_ID))
    assert [(p.page_number, p.status, p.failure_reason) for p in pages] == [
        (1, "failed", "PAGE_TIMEOUT"),
        (2, "ok", None),
    ]
    assert pages[0].tier_used is None and pages[0].lines == []
    assert pages[1].lines[0].text == BODY[0]


def test_default_limits_follow_adr() -> None:
    config = PdfReaderConfig()
    assert config.max_bytes == 50 * MIB
    assert config.max_pages == 300
    assert config.open_timeout_s == 5.0
    assert config.text_layer_page_timeout_s == 5.0
    assert config.worker_mem_bytes == 2 * 1024 * MIB
    assert (config.text_layer_min, config.text_score_min, config.ocr_score_min) == (0.85, 0.80, 0.80)
    assert (config.line_conf_min, config.coverage_min, config.figure_ink_min) == (0.70, 0.85, 0.10)
    assert config.cross_line_cer_warn == 0.05


def test_size_limit_boundary(tmp_path: Path) -> None:
    config = PdfReaderConfig(max_bytes=64)
    exact = tmp_path / "exact.pdf"
    exact.write_bytes(b"%PDF-" + b"x" * 59)
    assert check_pdf_input(exact, config) == 64
    over = tmp_path / "over.pdf"
    over.write_bytes(b"%PDF-" + b"x" * 60)
    with pytest.raises(PdfInputError) as excinfo:
        check_pdf_input(over, config)
    assert excinfo.value.code == "TOO_LARGE"


def test_password_protected_pdf_is_rejected_without_cracking(tmp_path: Path) -> None:
    path = write_pdf(tmp_path / "locked.pdf", [Page(texts=text_lines(BODY))], user_password="secret")
    _expect("PASSWORD_REQUIRED", WorkerPdfReader(), path)
    assert str(PdfInputError("PASSWORD_REQUIRED")) == "PDF có mật khẩu"


def test_owner_password_only_pdf_is_still_read(tmp_path: Path) -> None:
    path = write_pdf(tmp_path / "owner.pdf", [Page(texts=text_lines(BODY))], user_password="")
    reader = WorkerPdfReader()
    assert reader.probe(path) == 1
    [page] = list(reader.read_pages(path, DOC_ID))
    assert page.status == "ok" and page.lines[0].text == BODY[0]


def test_corrupted_pdf_cannot_be_opened(tmp_path: Path) -> None:
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-1.4\n" + b"\x00garbage" * 200)
    _expect("OPEN_FAILED", WorkerPdfReader(), broken)
