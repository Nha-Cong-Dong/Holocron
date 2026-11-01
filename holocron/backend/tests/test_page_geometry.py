"""`PageGeometry`, `PageRead` và `geometry_sink` (ADR-010 mục 5, 20; SPECIFICATION §5.7 "Dữ liệu cho bộ dựng khối").

Geometry chỉ đi qua đường nội bộ (`tiers.assemble_text_layer_page_read`, `WorkerPdfReader.read_pages(geometry_sink=…)`);
`PageExtraction` không đổi. Mọi lỗi dựng geometry hay vượt trần chỉ làm `geometry = None`, trang không bao giờ `failed`
vì thế. PDF mẫu tự tạo (`tests/pdf_samples.py`), không mạng.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import ValidationError

from holocron_core.domain.documents import (
    GEOMETRY_VERSION,
    GeoFont,
    GeoLine,
    GeoWord,
    PageExtraction,
    PageGeometry,
    PageRead,
)
from holocron_core.domain.limits import MAX_GEOMETRY_JSON_BYTES
from holocron_core.infrastructure.document_readers import tiers
from holocron_core.infrastructure.document_readers.config import PdfReaderConfig
from holocron_core.infrastructure.document_readers.pdf_reader import (
    GeometrySink,
    PageGeometrySource,
    PdfReader,
    WorkerPdfReader,
)
from holocron_core.infrastructure.document_readers.pdfium_adapter import PdfiumBackend
from holocron_core.infrastructure.document_readers.protocol import (
    MAX_PAGE_FONTS,
    MAX_PAGE_GEOMETRY_LINES,
    MAX_PAGE_LINES,
    MAX_PAGE_WORDS,
    RawFont,
    RawOcrLine,
    RawOcrPage,
    RawPdfPage,
    RawTextLine,
    RawWord,
)
from holocron_core.infrastructure.document_readers.text_layer import cap_boxless_words
from holocron_core.infrastructure.document_readers.tiers import (
    assemble_text_layer_page,
    assemble_text_layer_page_read,
    reader_settings_sha256,
)
from tests.pdf_samples import Page, build_pdf, text_lines, write_pdf

CONFIG = PdfReaderConfig()
DOC_ID = "doc_00000000000000e5"
BOX = (0.1, 0.1, 0.5, 0.12)
BODY = ["Dong thu nhat cua trang co du chu de giu lop chu", "Dong thu hai cua trang cung co du chu"]


def _line(text: str, *, words: list[RawWord] | None = None, font_id: int | None = None) -> RawTextLine:
    if words is None:
        words = [RawWord(text=w, bbox=BOX, font_id=font_id, font_size_pt=11.0) for w in text.split()]
    return RawTextLine(text=text, bbox=BOX, words=words, font_id=font_id, font_size_pt=11.0)


def _page(lines: list[RawTextLine], **extra: object) -> RawPdfPage:
    return RawPdfPage.model_validate(
        {
            "page_index": 0,
            "width_pt": 612.0,
            "height_pt": 792.0,
            "lines": lines,
            "image_area_ratio": 0.0,
            "path_area_ratio": 0.0,
            "fonts": [RawFont(font_name="Helvetica")],
            "pdfium_nonws_total": sum(len(t.text.replace(" ", "")) for t in lines),
            **extra,
        }
    )


def _nonws(text: str) -> int:
    return sum(1 for c in unicodedata.normalize("NFC", text) if not c.isspace())


# --------------------------------------------------------------------------- #
# Model
# --------------------------------------------------------------------------- #


def _geometry(**extra: object) -> PageGeometry:
    return PageGeometry.model_validate(
        {"version": GEOMETRY_VERSION, "reader_settings_sha256": "0" * 64, "source": "text_layer", **extra}
    )


def test_page_geometry_model_defaults_and_validators() -> None:
    empty = _geometry()
    assert GEOMETRY_VERSION == 1
    assert (empty.lines, empty.fonts, empty.rules, empty.ocr_lines) == ([], [], [], [])
    assert (empty.pdfium_nonws_total, empty.hidden_nonws_dropped, empty.ocr_nonws_total) == (None, 0, None)
    assert not empty.font_data_dropped and not empty.rules_over_cap
    word = GeoWord(text="a", bbox=None, font_id=0)
    font = GeoFont(
        font_name="A",
        bold=False,
        italic=False,
        monospace=False,
        bold_source=None,
        italic_source=None,
        monospace_source=None,
    )
    line = GeoLine(words=[word], bbox=None, extracted_line_indices=[0])
    assert _geometry(lines=[line], fonts=[font]).lines[0].words[0].font_id == 0
    with pytest.raises(ValidationError, match="font_id"):
        _geometry(lines=[line])
    with pytest.raises(ValidationError, match="font_id"):
        _geometry(lines=[GeoLine(words=[], bbox=None, font_id=1, extracted_line_indices=[])], fonts=[font])
    with pytest.raises(ValidationError):
        GeoWord(text="", bbox=None)
    with pytest.raises(ValidationError):
        _geometry(reader_settings_sha256="xyz")
    many = GeoLine(
        words=[GeoWord(text="a", bbox=None)] * (MAX_PAGE_WORDS // 2 + 1), bbox=None, extracted_line_indices=[]
    )
    with pytest.raises(ValidationError, match=str(MAX_PAGE_WORDS)):
        _geometry(lines=[many], ocr_lines=[many])
    assert MAX_PAGE_LINES == MAX_PAGE_GEOMETRY_LINES == 10_000


def test_page_read_holds_extraction_and_optional_geometry() -> None:
    raw = _page([_line("Mot dong")])
    read = assemble_text_layer_page_read(raw, DOC_ID, CONFIG)
    assert isinstance(read, PageRead)
    assert read.extraction == assemble_text_layer_page(raw, DOC_ID, CONFIG)
    assert read.geometry is not None and read.geometry.source == "text_layer"
    assert PageRead(extraction=read.extraction).geometry is None
    assert "geometry" not in PageExtraction.model_fields


# --------------------------------------------------------------------------- #
# Dựng geometry từ dòng thô
# --------------------------------------------------------------------------- #


def test_geometry_from_pdf_mirrors_raw_lines_and_counts() -> None:
    opened = PdfiumBackend.load().open(build_pdf([Page(texts=text_lines(BODY))]))
    assert not isinstance(opened, str)
    with opened:
        raw = opened.read_page(0, None, CONFIG)
    read = assemble_text_layer_page_read(raw, DOC_ID, CONFIG)
    geometry = read.geometry
    assert geometry is not None
    assert geometry.version == GEOMETRY_VERSION and geometry.reader_settings_sha256 == reader_settings_sha256(CONFIG)
    assert [[w.text for w in line.words] for line in geometry.lines] == [line.text.split() for line in raw.lines]
    assert [font.font_name for font in geometry.fonts] == ["Helvetica"]
    assert all(line.font_id == 0 and line.font_size_pt == 12.0 for line in geometry.lines)
    assert geometry.pdfium_nonws_total == sum(_nonws(w.text) for line in geometry.lines for w in line.words)
    assert geometry.pdfium_nonws_total == sum(_nonws(text) for text in BODY)
    assert [line.extracted_line_indices for line in geometry.lines] == [[0], [1]]
    assert read.extraction == assemble_text_layer_page(raw, DOC_ID, CONFIG)


def test_extracted_line_indices_for_long_padded_and_blank_lines() -> None:
    long_text = "x" * (2 * 2000 + 500)
    blank = RawTextLine(text="    ", bbox=None, words=[])
    lines = [_line("  padded line  "), _line(long_text), blank, _line("last")]
    raw = _page(lines)
    read = assemble_text_layer_page_read(raw, DOC_ID, CONFIG)
    geometry = read.geometry
    assert geometry is not None
    assert [line.extracted_line_indices for line in geometry.lines] == [[0], [1, 2, 3], [], [4]]
    assert geometry.lines[2].words == []
    texts = [line.text for line in read.extraction.lines]
    assert texts[0] == "padded line" and "".join(texts[1:4]) == long_text and texts[4] == "last"
    for geo_line, raw_line in zip(geometry.lines, lines, strict=True):
        joined = "".join(read.extraction.lines[i].text for i in geo_line.extracted_line_indices)
        assert joined == unicodedata.normalize("NFC", raw_line.text).strip()


def test_geometry_keeps_line_flags_header_footer_and_boxless_words() -> None:
    boxless = RawTextLine(
        text="Trang 3",
        bbox=None,
        words=[RawWord(text="Trang", bbox=None), RawWord(text="3", bbox=None)],
        flags=["TEXT_OCCLUDED"],
        header_footer=True,
    )
    raw = _page([_line("Noi dung chinh cua trang nay", font_id=0), boxless], font_data_dropped=True)
    geometry = assemble_text_layer_page_read(raw, DOC_ID, CONFIG).geometry
    assert geometry is not None and geometry.font_data_dropped
    last = geometry.lines[1]
    assert [w.text for w in last.words] == ["Trang", "3"] and all(w.bbox is None for w in last.words)
    assert last.flags == ["TEXT_OCCLUDED"] and last.header_footer and last.bbox is None


def test_ocr_and_failed_pages_have_no_geometry() -> None:
    box = (0.1, 0.1, 0.9, 0.12)
    ocr_line = RawOcrLine(text="Chu OCR", bbox=box, words=[RawWord(text="Chu", bbox=box)], conf=0.95)
    ocr = RawOcrPage(dpi=300.0, lines=[ocr_line], ink_outside_text_ratio=0.0, secondary=None)
    scanned = _page([], image_area_ratio=1.0, ocr=ocr)
    read = assemble_text_layer_page_read(scanned, DOC_ID, CONFIG)
    assert read.extraction.tier_used == "ocr" and read.geometry is None
    failed = assemble_text_layer_page_read(_page([], image_area_ratio=1.0), DOC_ID, CONFIG)
    assert failed.extraction.status == "failed" and failed.geometry is None


# --------------------------------------------------------------------------- #
# Trần (ADR-010 mục 5)
# --------------------------------------------------------------------------- #


def _over_lines() -> RawPdfPage:
    line = _line("a", font_id=0)
    return RawPdfPage.model_construct(**{**_page([line]).__dict__, "lines": [line] * (MAX_PAGE_GEOMETRY_LINES + 1)})


def _over_words() -> RawPdfPage:
    words = [RawWord(text="a", bbox=BOX)] * 3500
    line = RawTextLine(text=" ".join(["a"] * 3500), bbox=BOX, words=words)
    return RawPdfPage.model_construct(**{**_page([line]).__dict__, "lines": [line] * 15})


def _over_fonts() -> RawPdfPage:
    fonts = [RawFont(font_name=f"F{i}") for i in range(MAX_PAGE_FONTS + 1)]
    return RawPdfPage.model_construct(**{**_page([_line("a", font_id=0)]).__dict__, "fonts": fonts})


def _bad_font_id() -> RawPdfPage:
    return _page([_line("a b", font_id=3)])


@pytest.mark.parametrize("make", [_over_lines, _over_words, _over_fonts, _bad_font_id])
def test_geometry_over_cap_marks_layout_failed(make: Callable[[], RawPdfPage]) -> None:
    """Phần X04: geometry vượt trần (dòng, từ, font, byte JSON) hay không hợp lệ ⇒ `geometry = None`; trang vẫn đọc
    bình thường bằng dòng (`PageExtraction` không đổi, không `failed`); X09 gắn `LAYOUT_FAILED`."""
    raw = make()
    read = assemble_text_layer_page_read(raw, DOC_ID, CONFIG)
    assert read.geometry is None
    assert read.extraction == assemble_text_layer_page(raw, DOC_ID, CONFIG)
    assert read.extraction.status != "failed" and read.extraction.tier_used == "text_layer"
    assert read.extraction.lines


def test_geometry_over_byte_cap_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """Trần byte JSON (`MAX_GEOMETRY_JSON_BYTES` = 8 MiB): trang thật vượt 8 MiB cần hàng triệu ký tự và dựng dòng
    chậm, nên test hạ trần của `tiers` xuống đúng kích thước geometry của một trang nhỏ."""
    assert MAX_GEOMETRY_JSON_BYTES == 8 * 1024 * 1024
    raw = _page([_line("Mot dong binh thuong", font_id=0)])
    geometry = assemble_text_layer_page_read(raw, DOC_ID, CONFIG).geometry
    assert geometry is not None
    size = len(geometry.model_dump_json().encode())
    monkeypatch.setattr(tiers, "MAX_GEOMETRY_JSON_BYTES", size)
    assert assemble_text_layer_page_read(raw, DOC_ID, CONFIG).geometry == geometry
    monkeypatch.setattr(tiers, "MAX_GEOMETRY_JSON_BYTES", size - 1)
    read = assemble_text_layer_page_read(raw, DOC_ID, CONFIG)
    assert read.geometry is None and read.extraction == assemble_text_layer_page(raw, DOC_ID, CONFIG)


def test_boxless_words_over_cap_drop_geometry_not_page() -> None:
    per_line = 2500
    boxed = [
        RawTextLine(text=" ".join(["a"] * per_line), bbox=BOX, words=[RawWord(text="a", bbox=BOX)] * per_line)
        for _ in range(MAX_PAGE_WORDS // per_line)
    ]
    boxless = RawTextLine(text="z", bbox=None, words=[RawWord(text="z", bbox=None)])
    lines, dropped = cap_boxless_words([*boxed, boxless])
    assert dropped and lines[-1].words == [] and lines[-1].text == "z"
    assert sum(len(line.words) for line in lines) == MAX_PAGE_WORDS
    assert cap_boxless_words([*boxed[:1], boxless]) == ([*boxed[:1], boxless], False)
    over_boxed = [*boxed, RawTextLine(text="b", bbox=BOX, words=[RawWord(text="b", bbox=BOX)])]
    assert cap_boxless_words(over_boxed) == (over_boxed, False)
    raw = _page(lines, boxless_words_dropped=True, fonts=[])
    read = assemble_text_layer_page_read(raw, DOC_ID, CONFIG)
    assert read.geometry is None
    assert read.extraction == assemble_text_layer_page(raw, DOC_ID, CONFIG)
    assert read.extraction.status != "failed" and read.extraction.lines[-1].text == "z"


# --------------------------------------------------------------------------- #
# reader_settings_sha256
# --------------------------------------------------------------------------- #


def test_reader_settings_sha256_tracks_every_config_field() -> None:
    base = reader_settings_sha256(CONFIG)
    assert base == reader_settings_sha256(PdfReaderConfig())
    assert len(base) == 64
    assert reader_settings_sha256(PdfReaderConfig(bold_min_weight=700)) != base
    assert reader_settings_sha256(PdfReaderConfig(table_gap_em=2.0)) != base
    assert reader_settings_sha256(PdfReaderConfig(ocr_charset_allowed_missing=frozenset({"ỵ"}))) != base
    reordered = PdfReaderConfig(ocr_charset_allowed_missing=frozenset(["ỵ", "Ỵ"]))
    assert reader_settings_sha256(reordered) == base


# --------------------------------------------------------------------------- #
# geometry_sink của WorkerPdfReader
# --------------------------------------------------------------------------- #


def _two_page_pdf(tmp_path: Path) -> Path:
    return write_pdf(tmp_path / "two.pdf", [Page(texts=text_lines(BODY)), Page()])


def test_geometry_sink_called_before_each_yield(tmp_path: Path) -> None:
    events: list[tuple[str, int, bool]] = []

    def sink(page_number: int, geometry: PageGeometry | None) -> None:
        events.append(("sink", page_number, geometry is not None))

    reader = WorkerPdfReader()
    source: PageGeometrySource = reader
    for page in source.read_pages(_two_page_pdf(tmp_path), DOC_ID, geometry_sink=sink):
        events.append(("page", page.page_number, page.status == "failed"))
    assert events == [("sink", 1, True), ("page", 1, False), ("sink", 2, False), ("page", 2, True)]


def test_read_pages_without_sink_is_unchanged(tmp_path: Path) -> None:
    reader: PdfReader = WorkerPdfReader()
    pages = list(reader.read_pages(_two_page_pdf(tmp_path), DOC_ID))
    assert [(p.page_number, p.status, p.failure_reason) for p in pages] == [
        (1, "ok", None),
        (2, "failed", "OCR_ENGINE_MISSING"),
    ]


def test_geometry_sink_error_propagates(tmp_path: Path) -> None:
    yielded: list[int] = []

    def sink(page_number: int, geometry: PageGeometry | None) -> None:
        raise RuntimeError(f"sink hỏng ở trang {page_number}")

    typed: GeometrySink = sink
    with pytest.raises(RuntimeError, match="trang 1"):
        for page in WorkerPdfReader().read_pages(_two_page_pdf(tmp_path), DOC_ID, geometry_sink=typed):
            yielded.append(page.page_number)
    assert yielded == []
