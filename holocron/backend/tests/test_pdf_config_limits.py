"""Hằng số của tầng 2 nằm trong `PdfReaderConfig` và các ràng buộc giữa chúng (review tầng 2, lỗi 3)."""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from holocron_core.infrastructure.document_readers.config import OcrSettings, PdfReaderConfig
from holocron_core.infrastructure.document_readers.raster import render_scale
from holocron_core.infrastructure.document_readers.tesseract import RunResult, TesseractEngine


def test_tesseract_timeout_must_be_shorter_than_the_ocr_page_timeout() -> None:
    config = PdfReaderConfig()
    assert config.tesseract_timeout_s < config.ocr_page_timeout_s
    assert PdfReaderConfig(tesseract_timeout_s=29.9, ocr_page_timeout_s=30.0).tesseract_timeout_s == 29.9
    for tesseract, page in ((30.0, 30.0), (45.0, 30.0), (20.0, 10.0)):
        with pytest.raises(ValidationError, match="tesseract_timeout_s"):
            PdfReaderConfig(tesseract_timeout_s=tesseract, ocr_page_timeout_s=page)


def test_new_tier2_constants_are_configurable_and_validated() -> None:
    config = PdfReaderConfig()
    assert (config.ocr_kill_grace_s, config.ocr_probe_max_bytes, config.render_shrink_factor) == (5.0, 65536, 0.995)
    for field, bad in (
        ("ocr_kill_grace_s", 0.0),
        ("ocr_probe_max_bytes", 10),
        ("render_shrink_factor", 1.0),
        ("render_shrink_factor", 0.0),
        ("cross_check_max_lines", 0),
        ("cross_check_max_line_tokens", 0),
        ("cross_check_max_line_pairs", 0),
        ("cross_check_max_token_cells", 0),
    ):
        with pytest.raises(ValidationError):
            PdfReaderConfig(**{field: bad})


def test_render_scale_uses_the_configured_shrink_factor() -> None:
    coarse = render_scale(613.3, 791.7, 300, 1_000_000, shrink_factor=0.5)
    fine = render_scale(613.3, 791.7, 300, 1_000_000)
    for scale in (coarse, fine):
        assert math.ceil(613.3 * scale) * math.ceil(791.7 * scale) <= 1_000_000
    assert coarse < fine
    with pytest.raises(ValueError, match="shrink_factor"):
        render_scale(612.0, 792.0, 300, 1_000_000, shrink_factor=1.0)


def test_probe_reads_at_most_the_configured_bytes() -> None:
    limits: list[int] = []

    def runner(args: list[str], stdin_data: bytes, timeout_s: float, max_output_bytes: int) -> RunResult:
        limits.append(max_output_bytes)
        return RunResult(status="failed")

    settings = OcrSettings(tesseract_cmd="tesseract-test", secondary=None)
    assert TesseractEngine.probe(settings, 1.0, runner, max_output_bytes=2048) is None
    assert limits == [2048]
