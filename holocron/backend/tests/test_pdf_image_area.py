"""Độ phủ ảnh `image_area_ratio` là diện tích HỢP của các hộp ảnh, cắt theo khổ trang (ADR-008 mục 6).

Ảnh xếp chồng (ảnh nền khung slide vẽ hai lần) chỉ được tính một lần, nên slide tiêu đề ít chữ không bị đẩy sang
OCR vì độ phủ cộng dồn vượt `text_sparse_image_min`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from holocron_core.infrastructure.document_readers.config import PdfReaderConfig
from holocron_core.infrastructure.document_readers.pdf_reader import WorkerPdfReader
from holocron_core.infrastructure.document_readers.pdfium_adapter import (
    MAX_UNION_BOXES,
    PdfiumBackend,
    union_area_ratio,
)
from holocron_core.infrastructure.document_readers.protocol import RawPdfPage
from holocron_core.infrastructure.document_readers.text_layer import PdfBox, ocr_reason
from tests.pdf_samples import Box, Page, Text, build_pdf, write_pdf

CONFIG = PdfReaderConfig()
DOC_ID = "doc_00000000000000ee"
PAGE: PdfBox = (0.0, 0.0, 100.0, 100.0)
# Dải ngang phủ 22% trang 612 x 792 (một ảnh nền khung slide).
BAND_HEIGHT = 792.0 * 0.22


def _read(data: bytes) -> RawPdfPage:
    document = PdfiumBackend.load().open(data)
    assert not isinstance(document, str)
    with document:
        return document.read_page(0)


# --------------------------------------------------------------------------- #
# Hàm thuần
# --------------------------------------------------------------------------- #


def test_overlapping_boxes_are_counted_once() -> None:
    band = (0.0, 0.0, 100.0, 22.0)
    assert union_area_ratio([band, band], PAGE) == pytest.approx(0.22)
    # Chồng một phần: 0.22 + 0.22 − 0.11 (phần giao 100 x 11).
    assert union_area_ratio([band, (0.0, 11.0, 100.0, 33.0)], PAGE) == pytest.approx(0.33)
    # Hộp nằm trong hộp khác không thêm diện tích.
    assert union_area_ratio([(10.0, 10.0, 90.0, 90.0), (20.0, 20.0, 30.0, 30.0)], PAGE) == pytest.approx(0.64)
    # Hai hộp rời nhau theo chiều dọc trong cùng một dải: cộng cả hai.
    assert union_area_ratio([(0.0, 0.0, 50.0, 10.0), (0.0, 40.0, 50.0, 60.0)], PAGE) == pytest.approx(0.15)
    # Chồng chéo kiểu chữ thập: 2 x 0.2 − 0.04.
    cross = [(40.0, 0.0, 60.0, 100.0), (0.0, 40.0, 100.0, 60.0)]
    assert union_area_ratio(cross, PAGE) == pytest.approx(0.36)


def test_boxes_are_clipped_to_the_page() -> None:
    assert union_area_ratio([(-50.0, -50.0, 50.0, 50.0)], PAGE) == pytest.approx(0.25)
    assert union_area_ratio([(-10.0, 90.0, 110.0, 130.0)], PAGE) == pytest.approx(0.10)
    assert union_area_ratio([(200.0, 0.0, 300.0, 100.0), (0.0, -40.0, 100.0, -1.0)], PAGE) == 0.0
    # Khung trang không bắt đầu ở gốc toạ độ.
    assert union_area_ratio([(0.0, 0.0, 60.0, 60.0)], (50.0, 50.0, 150.0, 150.0)) == pytest.approx(0.01)


def test_ratio_never_exceeds_one() -> None:
    assert union_area_ratio([], PAGE) == 0.0
    assert union_area_ratio([(-5.0, -5.0, 105.0, 105.0)] * 5, PAGE) == 1.0
    halves = [(0.0, 0.0, 60.0, 100.0), (40.0, 0.0, 100.0, 100.0)]
    assert union_area_ratio(halves, PAGE) == pytest.approx(1.0)
    # Ba dải phủ kín trang A4 ngang: cộng dồn số thực cho 1.0000000000000002, phải chặn về 1 (`UnitFloat`).
    width, cuts = 595.2756, (400.68805772364163, 501.07016007886193)
    strips = [(0.0, 0.0, cuts[0], 792.0), (cuts[0], 0.0, cuts[1], 792.0), (cuts[1], 0.0, width, 792.0)]
    assert union_area_ratio(strips, (0.0, 0.0, width, 792.0)) == 1.0
    # Hộp suy biến (rộng hoặc cao bằng 0) không đóng góp gì.
    assert union_area_ratio([(10.0, 10.0, 10.0, 90.0), (0.0, 5.0, 100.0, 5.0)], PAGE) == 0.0


def test_box_cap_falls_back_to_summed_upper_bound() -> None:
    tiny = (0.0, 0.0, 1.0, 1.0)  # 0.0001 trang
    assert union_area_ratio([tiny] * MAX_UNION_BOXES, PAGE) == pytest.approx(0.0001)
    over = [tiny] * (MAX_UNION_BOXES + 1)
    assert union_area_ratio(over, PAGE) == pytest.approx(0.0001 * (MAX_UNION_BOXES + 1))
    # Cận trên vẫn chặn ở 1.
    assert union_area_ratio([(0.0, 0.0, 100.0, 100.0)] * (MAX_UNION_BOXES + 1), PAGE) == 1.0
    # Hộp nằm ngoài trang không tính vào trần.
    outside = [(200.0, 200.0, 201.0, 201.0)] * MAX_UNION_BOXES
    assert union_area_ratio([*outside, tiny, tiny], PAGE) == pytest.approx(0.0001)
    # Hộp suy biến cũng vậy.
    flat = [(5.0, 5.0, 5.0, 50.0)] * MAX_UNION_BOXES
    assert union_area_ratio([*flat, tiny, tiny], PAGE) == pytest.approx(0.0001)


# --------------------------------------------------------------------------- #
# PDF tự tạo, đọc qua PDFium
# --------------------------------------------------------------------------- #


def test_stacked_frame_images_keep_title_slide_on_text_layer(tmp_path: Path) -> None:
    band = Box(0.0, 600.0, 612.0, BAND_HEIGHT)
    title = Text(x=72.0, y=300.0, value="Chuong 1", size=40.0)
    raw = _read(build_pdf([Page(texts=[title], images=[band, band])]))
    assert raw.image_area_ratio == pytest.approx(0.22, abs=1e-3)
    # Tổng hai ảnh (0.44) sẽ vượt `text_sparse_image_min`; diện tích hợp thì không.
    assert 2 * raw.image_area_ratio >= CONFIG.text_sparse_image_min > raw.image_area_ratio
    assert ocr_reason(raw, CONFIG) is None
    path = write_pdf(tmp_path / "title.pdf", [Page(texts=[title], images=[band, band])])
    [page] = list(WorkerPdfReader(CONFIG).read_pages(path, DOC_ID))
    assert page.tier_used == "text_layer"


def test_image_overflowing_the_page_is_clipped() -> None:
    raw = _read(build_pdf([Page(images=[Box(-306.0, -396.0, 612.0, 792.0)])]))
    assert raw.image_area_ratio == pytest.approx(0.25, abs=1e-3)
    raw = _read(build_pdf([Page(images=[Box(-100.0, -100.0, 812.0, 992.0), Box(0.0, 0.0, 612.0, 792.0)])]))
    assert raw.image_area_ratio == pytest.approx(1.0)
