"""Font, cỡ chữ và số đếm độc lập của lớp chữ PDF (ADR-010 mục 5, 6; SPECIFICATION §5.7 "Dữ liệu cho bộ dựng khối").

PDF tự tạo bằng bộ dựng viết tay trong file (Base-14 Helvetica-Bold và Courier, toán tử `Tm`, `cm`, form XObject, font
giả có `/FontDescriptor` với `/StemV 80` ⇒ PDFium trả weight 400); không mạng, không eval_data.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import unicodedata
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pypdfium2.raw as pdfium_raw
import pytest
from pydantic import ValidationError

from holocron_core.domain.documents import PageExtraction
from holocron_core.infrastructure.document_readers import _pdf_worker
from holocron_core.infrastructure.document_readers.config import PdfReaderConfig
from holocron_core.infrastructure.document_readers.pdfium_adapter import (
    FONT_NAME_BUFFER_BYTES,
    PdfiumBackend,
    PdfiumDocument,
)
from holocron_core.infrastructure.document_readers.protocol import (
    FONT_NAME_MAX_CHARS,
    MAX_PAGE_FONTS,
    MAX_PAGE_WORDS,
    PageErrorMessage,
    PageMessage,
    RawFont,
    RawPdfPage,
    RawTextLine,
    RawWord,
    decode_message,
    encode_message,
)
from holocron_core.infrastructure.document_readers.text_layer import (
    PdfChar,
    assign_font_ids,
    cap_boxless_words,
    classify_font,
    clean_font_name,
    effective_font_size,
    font_name_tokens,
    group_lines,
    has_table,
    independent_nonws_counts,
)
from holocron_core.infrastructure.document_readers.tiers import assemble_text_layer_page
from holocron_core.routers.api import app
from holocron_core.services.document_import import SynchronousImportQueue
from tests.document_import_support import FakePdfReader, ImportEnv, install, pdf_bytes
from tests.vault_support import open_store

CONFIG = PdfReaderConfig()
DOC_ID = "doc_00000000000000f4"
PAGE_BOX = (0.0, 0.0, 600.0, 800.0)
Matrix = tuple[float, float, float, float, float, float]

# --------------------------------------------------------------------------- #
# Bộ dựng PDF viết tay
# --------------------------------------------------------------------------- #


def type1(base_font: str) -> bytes:
    """Font Type1 không nhúng (Base-14, hay tên lạ mà PDFium thay bằng font hệ thống)."""
    return b"<< /Type /Font /Subtype /Type1 /BaseFont /%s /Encoding /WinAnsiEncoding >>" % base_font.encode("latin-1")


@dataclass(frozen=True)
class DescribedFont:
    """Font TrueType giả có `/FontDescriptor` (không kèm dữ liệu font); `/StemV 80` ⇒ PDFium trả weight 400."""

    base_font: str
    stem_v: int = 80
    flags: int = 32


@dataclass
class PdfDoc:
    """Một trang 600 × 800: `content` là toán tử nội dung; `fonts` tên tài nguyên ⇒ font; `forms` tên ⇒ (`/Matrix`,
    nội dung) dùng chung font của trang."""

    content: bytes
    fonts: dict[str, bytes | DescribedFont] = field(default_factory=lambda: {"F1": type1("Helvetica")})
    forms: dict[str, tuple[Matrix, bytes]] = field(default_factory=dict)

    def build(self) -> bytes:
        objects: list[bytes] = [b"", b"", b""]  # 1 catalog, 2 pages, 3 page

        def add(body: bytes) -> int:
            objects.append(body)
            return len(objects)

        font_refs: dict[str, int] = {}
        for name, font in self.fonts.items():
            if isinstance(font, DescribedFont):
                descriptor = add(
                    b"<< /Type /FontDescriptor /FontName /%s /Flags %d /FontBBox [0 0 1000 1000] /ItalicAngle 0"
                    b" /Ascent 800 /Descent -200 /CapHeight 700 /StemV %d >>"
                    % (font.base_font.encode(), font.flags, font.stem_v)
                )
                widths = b" ".join(b"%d" % (400 + 7 * (i % 13)) for i in range(95))
                font_refs[name] = add(
                    b"<< /Type /Font /Subtype /TrueType /BaseFont /%s /FirstChar 32 /LastChar 126 /Widths [%s]"
                    b" /FontDescriptor %d 0 R /Encoding /WinAnsiEncoding >>"
                    % (font.base_font.encode(), widths, descriptor)
                )
            else:
                font_refs[name] = add(font)
        font_dict = b"<< " + b" ".join(b"/%s %d 0 R" % (n.encode(), r) for n, r in font_refs.items()) + b" >>"
        form_refs: dict[str, int] = {}
        for name, (matrix, data) in self.forms.items():
            matrix_text = b" ".join(b"%g" % value for value in matrix)
            form_refs[name] = add(
                b"<< /Type /XObject /Subtype /Form /BBox [0 0 600 800] /Matrix [%s] /Resources << /Font %s >>"
                b" /Length %d >>\nstream\n%s\nendstream" % (matrix_text, font_dict, len(data), data)
            )
        content = add(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(self.content), self.content))
        xobjects = b" ".join(b"/%s %d 0 R" % (n.encode(), r) for n, r in form_refs.items())
        objects[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
        objects[1] = b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>"
        objects[2] = (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 800] /Contents %d 0 R"
            b" /Resources << /Font %s /XObject << %s >> >> >>" % (content, font_dict, xobjects)
        )
        out = bytearray(b"%PDF-1.4\n")
        offsets: list[int] = []
        for number, body in enumerate(objects, start=1):
            offsets.append(len(out))
            out += b"%d 0 obj\n%s\nendobj\n" % (number, body)
        xref = len(out)
        out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
        out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
        out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
        return bytes(out)


def bt(
    font: str, size: float, x: float, y: float, value: str, *, tm: Matrix | None = None, rgb: str = "0 0 0"
) -> bytes:
    """Một đối tượng chữ; `tm` đặt ma trận chữ bằng `Tm` (khi đó `x`, `y` bị bỏ qua)."""
    position = b"%g %g Td" % (x, y) if tm is None else b"%g %g %g %g %g %g Tm" % tm
    return b"BT /%s %g Tf %s rg %s (%s) Tj ET\n" % (
        font.encode(),
        size,
        rgb.encode(),
        position,
        value.encode("latin-1"),
    )


def _open(doc: PdfDoc, backend: PdfiumBackend | None = None) -> PdfiumDocument:
    opened = (backend or PdfiumBackend.load()).open(doc.build())
    assert not isinstance(opened, str)
    return opened


def read(doc: PdfDoc, config: PdfReaderConfig = CONFIG, backend: PdfiumBackend | None = None) -> RawPdfPage:
    with _open(doc, backend) as document:
        return document.read_page(0, None, config)


def line_of(raw: RawPdfPage, text: str) -> RawTextLine:
    matches = [line for line in raw.lines if line.text.strip() == text]
    assert len(matches) == 1, [line.text for line in raw.lines]
    return matches[0]


def font_of(raw: RawPdfPage, text: str) -> RawFont:
    font_id = line_of(raw, text).font_id
    assert font_id is not None
    return raw.fonts[font_id]


def nonws(text: str) -> int:
    return sum(1 for c in unicodedata.normalize("NFC", text) if not c.isspace())


# --------------------------------------------------------------------------- #
# Cỡ chữ hiệu dụng
# --------------------------------------------------------------------------- #


def test_font_size_follows_tm_cm_form_and_rotation() -> None:
    doc = PdfDoc(
        content=bt("F1", 12, 50, 700, "Plain")
        + bt("F1", 10, 0, 0, "Scaled", tm=(2, 0, 0, 2, 50, 600))
        + b"q 2 0 0 2 0 0 cm\n"
        + bt("F1", 10, 25, 250, "Ctm")
        + b"Q\nq 1 0 0 1 0 100 cm /X1 Do Q\n"
        + bt("F1", 1, 0, 0, "Turned", tm=(0, 10, -10, 0, 400, 200)),
        forms={"X1": ((3, 0, 0, 3, 0, 0), bt("F1", 10, 20, 30, "InForm"))},
    )
    raw = read(doc)
    sizes = {line.text.strip(): line.font_size_pt for line in raw.lines}
    assert sizes["Plain"] == 12.0
    assert sizes["Scaled"] == 20.0
    assert sizes["Ctm"] == 20.0
    assert sizes["InForm"] == 30.0
    turned = [line for line in raw.lines if set(line.text.strip()) <= set("Turned")]
    assert turned and all(line.font_size_pt == 10.0 for line in turned)
    assert all(word.font_size_pt == line.font_size_pt for line in raw.lines for word in line.words)


def test_singular_text_matrix_has_no_font_size_but_keeps_words() -> None:
    raw = read(PdfDoc(content=bt("F1", 10, 0, 0, "Flat", tm=(1, 0, 2, 0, 50, 600)) + bt("F1", 10, 50, 500, "Ok")))
    flat = line_of(raw, "Flat")
    assert flat.font_size_pt is None and [word.text for word in flat.words] == ["Flat"]
    assert flat.font_id is not None
    assert line_of(raw, "Ok").font_size_pt == 10.0


def test_effective_font_size_rejects_singular_and_non_finite_and_clamps() -> None:
    assert effective_font_size(10.0, [4.0, 1.0]) == 20.0
    assert effective_font_size(-10.0, [1.0]) == 10.0
    assert effective_font_size(10.0, [0.0]) is None
    assert effective_font_size(10.0, [float("nan")]) is None
    assert effective_font_size(10.0, [float("inf")]) is None
    assert effective_font_size(float("inf"), [1.0]) is None
    assert effective_font_size(float("nan"), [1.0]) is None
    assert effective_font_size(0.0, [1.0]) is None
    assert effective_font_size(1e300, [1e300]) is None  # tràn ⇒ inf
    assert effective_font_size(0.2, [1.0]) == 1.0
    assert effective_font_size(5000.0, [1.0]) == 1000.0
    assert effective_font_size(12.04, [1.0]) == 12.0


def test_font_size_clamped_in_pdf() -> None:
    raw = read(
        PdfDoc(
            content=bt("F1", 0.3, 50, 700, "Tiny") + bt("F1", 10, 0, 0, "Huge", tm=(200, 0, 0, 200, 10, 10)),
        )
    )
    words = {word.text: word.font_size_pt for line in raw.lines for word in line.words}
    assert words == {"Tiny": 1.0, "Huge": 1000.0}


# --------------------------------------------------------------------------- #
# Thuộc tính font
# --------------------------------------------------------------------------- #


def test_base14_bold_and_courier_and_weight_400_font() -> None:
    doc = PdfDoc(
        content=bt("F1", 12, 50, 700, "Heavy") + bt("F2", 12, 50, 650, "Fixed") + bt("F3", 12, 50, 600, "Fake"),
        fonts={"F1": type1("Helvetica-Bold"), "F2": type1("Courier"), "F3": DescribedFont("ABCDEF+FakeSans")},
    )
    raw = read(doc)
    bold = font_of(raw, "Heavy")
    assert (bold.font_name, bold.bold, bold.bold_source) == ("Helvetica-Bold", True, "name")
    assert not bold.monospace and not bold.italic
    courier = font_of(raw, "Fixed")
    assert (courier.font_name, courier.monospace, courier.monospace_source) == ("Courier", True, "name")
    assert not courier.bold
    fake = font_of(raw, "Fake")
    assert fake.font_name == "FakeSans" and fake.bold is False and fake.bold_source is None
    # weight 400 vượt ngưỡng hạ xuống 400 ⇒ đậm theo weight
    lowered = font_of(read(doc, PdfReaderConfig(bold_min_weight=400)), "Fake")
    assert (lowered.bold, lowered.bold_source) == (True, "weight")


def test_fixed_pitch_flag_wins_over_name() -> None:
    raw = read(PdfDoc(content=bt("F1", 12, 50, 700, "Code"), fonts={"F1": DescribedFont("PlainSans", flags=33)}))
    font = font_of(raw, "Code")
    assert (font.monospace, font.monospace_source) == (True, "flag")


def test_classify_font_sources_and_tokens() -> None:
    assert classify_font("Arial", 700, 0, 0, CONFIG).bold_source == "weight"
    assert classify_font("Arial-Bold", 400, 0, 0, CONFIG).bold_source == "name"
    assert classify_font("Arial-Bold", None, None, None, CONFIG).bold_source == "name"
    assert classify_font("Arial", -1, -1, None, CONFIG) == RawFont(font_name="Arial")
    assert classify_font("Arial", None, 64, None, CONFIG).italic_source == "flag"
    assert classify_font("Arial", None, 0, -12, CONFIG).italic_source == "flag"
    assert classify_font("Arial-BoldItalic", None, 0, 0, CONFIG).italic_source == "name"
    assert classify_font("Times,Oblique", None, 0, 0, CONFIG).italic_source == "name"
    assert classify_font("DejaVuSansMono", None, 0, 0, CONFIG).monospace_source == "name"
    assert classify_font("Monotype", None, 0, 0, CONFIG).monospace is False
    assert classify_font("Blackadder", None, 0, 0, CONFIG).bold is False
    assert classify_font("MyriadPro-SemiBold", None, 0, 0, CONFIG).bold is True
    assert classify_font("Inter Semibold", None, 0, 0, CONFIG).bold is True
    assert font_name_tokens("TimesNewRomanPS-BoldMT") >= {"times", "new", "roman", "ps", "bold", "mt"}
    assert font_name_tokens(None) == frozenset()


def test_malicious_font_names_are_cleaned() -> None:
    assert clean_font_name(b"ABCDEF+Arial\x00junk") == "Arial"
    assert clean_font_name(b"\x01ABC\x07DEF+Foo\x1bBar") == "FooBar"
    assert clean_font_name("Đẹp-Font".encode()) == "p-Font"
    assert clean_font_name(b"Z" * 300) == "Z" * FONT_NAME_MAX_CHARS
    assert clean_font_name(b"") is None and clean_font_name(b"\x01\x02  ") is None
    assert clean_font_name(b"ABCDEF+") is None
    raw = read(
        PdfDoc(
            content=bt("F1", 12, 50, 700, "Long") + bt("F2", 12, 50, 650, "Ctl"),
            fonts={"F1": type1("X" * 300), "F2": type1("ABCDEF+Foo#01Bar")},
        )
    )
    assert font_of(raw, "Long").font_name == "X" * FONT_NAME_MAX_CHARS
    assert font_of(raw, "Ctl").font_name == "FooBar"


def test_font_name_buffer_is_fixed_and_oversized_names_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    sizes: list[int] = []

    def fake_name(font: object, buffer: ctypes.Array[ctypes.c_char], length: int) -> int:
        sizes.append(ctypes.sizeof(buffer))
        assert length == FONT_NAME_BUFFER_BYTES
        return 10_000_000  # tên khai báo rất dài: PDFium không ghi gì vào bộ đệm

    monkeypatch.setattr(pdfium_raw, "FPDFFont_GetBaseFontName", fake_name)
    raw = read(PdfDoc(content=bt("F1", 12, 50, 700, "Body"), fonts={"F1": type1("Helvetica-Bold")}))
    assert sizes and set(sizes) == {FONT_NAME_BUFFER_BYTES}
    font = font_of(raw, "Body")
    assert font.font_name is None and font.bold is False


def test_bad_weight_or_font_errors_never_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    doc = PdfDoc(content=bt("F1", 12, 50, 700, "Body"), fonts={"F1": type1("Helvetica-Bold")})
    monkeypatch.setattr(pdfium_raw, "FPDFFont_GetWeight", lambda font: -1)
    monkeypatch.setattr(pdfium_raw, "FPDFFont_GetFlags", lambda font: -1)
    raw = read(doc)
    assert font_of(raw, "Body").bold_source == "name"

    def boom(*_: object) -> object:
        raise OSError("pdfium lỗi")

    for name in ("FPDFFont_GetWeight", "FPDFFont_GetFlags", "FPDFFont_GetItalicAngle", "FPDFFont_GetBaseFontName"):
        monkeypatch.setattr(pdfium_raw, name, boom)
    raw = read(doc)
    assert font_of(raw, "Body") == RawFont() and line_of(raw, "Body").font_size_pt == 12.0
    monkeypatch.setattr(pdfium_raw, "FPDFTextObj_GetFont", boom)
    monkeypatch.setattr(pdfium_raw, "FPDFText_GetFontSize", boom)
    raw = read(doc)
    body = line_of(raw, "Body")
    assert (raw.fonts, body.font_id, body.font_size_pt, body.text) == ([], None, None, "Body")
    monkeypatch.setattr(pdfium_raw, "FPDFTextObj_GetFont", lambda obj: None)
    assert read(doc).fonts == []


# --------------------------------------------------------------------------- #
# Bảng font
# --------------------------------------------------------------------------- #


def test_font_table_deduplicates_and_orders_by_first_use() -> None:
    doc = PdfDoc(
        content=bt("F2", 12, 50, 700, "Mono") + bt("F1", 12, 50, 650, "First") + bt("F3", 12, 50, 600, "Again"),
        fonts={"F1": type1("Helvetica"), "F2": type1("Courier"), "F3": type1("Helvetica")},
    )
    raw = read(doc)
    assert [font.font_name for font in raw.fonts] == ["Courier", "Helvetica"]
    assert line_of(raw, "First").font_id == line_of(raw, "Again").font_id == 1
    assert line_of(raw, "Mono").font_id == 0


def test_font_only_on_hidden_text_not_in_table() -> None:
    red = "0.8 0 0"
    doc = PdfDoc(
        content=b"q 0.8 0 0 rg 0 0 600 800 re f Q\n"
        + bt("F1", 12, 50, 700, "Visible words", rgb="1 1 1")
        + bt("F2", 12, 50, 600, "HiddenWords", rgb=red),
        fonts={"F1": type1("Helvetica"), "F2": type1("Courier")},
    )
    raw = read(doc)
    assert [line.text for line in raw.lines] == ["Visible words"]
    assert [font.font_name for font in raw.fonts] == ["Helvetica"]
    assert raw.hidden_chars_dropped == len("HiddenWords") == raw.hidden_nonws_dropped
    assert raw.pdfium_nonws_total == nonws("Visible words") + nonws("HiddenWords")


def _font(index: int) -> RawFont:
    return RawFont(font_name=f"Font{index:03d}")


def test_font_257_gets_null_font_id_but_keeps_size() -> None:
    chars = [PdfChar(text="a", box=None, font=_font(i), font_size_pt=10.0) for i in range(MAX_PAGE_FONTS + 2)]
    chars.insert(1, PdfChar(text=" ", box=None, font=_font(999), font_size_pt=10.0))
    fonts, assigned = assign_font_ids(chars)
    assert len(fonts) == MAX_PAGE_FONTS and fonts[0] == _font(0)
    assert [c.font_id for c in assigned if not c.text.isspace()][:MAX_PAGE_FONTS] == list(range(MAX_PAGE_FONTS))
    assert [c.font_id for c in assigned[-2:]] == [None, None]
    assert all(c.font_size_pt == 10.0 for c in assigned)
    assert _font(999) not in fonts  # ký tự trắng không đưa font vào bảng
    # font đã có trong bảng vẫn nhận id sau khi bảng đầy
    _, again = assign_font_ids([*chars, PdfChar(text="b", box=None, font=_font(5))])
    assert again[-1].font_id == 5
    page = RawPdfPage(
        page_index=0,
        width_pt=600,
        height_pt=800,
        lines=[],
        image_area_ratio=0.0,
        path_area_ratio=0.0,
        fonts=fonts,
    )
    with pytest.raises(ValueError, match="256"):
        RawPdfPage.model_validate({**page.model_dump(), "fonts": [*fonts, _font(300)]})


def test_font_257_in_pdf() -> None:
    fonts: dict[str, bytes | DescribedFont] = {f"F{i}": type1(f"Fake{i:03d}") for i in range(MAX_PAGE_FONTS + 1)}
    content = b"".join(bt(f"F{i}", 8, 20 + 40 * (i % 14), 780 - 40 * (i // 14), f"w{i}") for i in range(len(fonts)))
    raw = read(PdfDoc(content=content, fonts=fonts))
    assert len(raw.fonts) == MAX_PAGE_FONTS
    words = {word.text: word for line in raw.lines for word in line.words}
    assert words["w0"].font_id == 0 and words[f"w{MAX_PAGE_FONTS - 1}"].font_id == MAX_PAGE_FONTS - 1
    last = words[f"w{MAX_PAGE_FONTS}"]
    assert last.font_id is None and last.font_size_pt == 8.0


def _chars(spec: list[tuple[str, int | None, float | None]]) -> list[PdfChar]:
    return [
        PdfChar(text=t, box=(10.0 * i, 0.0, 10.0 * i + 8.0, 10.0), font_id=f, font_size_pt=s)
        for i, (t, f, s) in enumerate(spec)
    ]


def test_majority_vote_and_deterministic_tie() -> None:
    line = group_lines(_chars([("a", 0, 10.0), ("b", 1, 12.0), (" ", None, None), ("c", 1, 12.0)]), PAGE_BOX, 0)[0]
    assert [(w.text, w.font_id, w.font_size_pt) for w in line.words] == [("ab", 0, 10.0), ("c", 1, 12.0)]
    assert (line.font_id, line.font_size_pt) == (1, 12.0)
    swapped = group_lines(_chars([("b", 1, 12.0), ("a", 0, 10.0)]), PAGE_BOX, 0)[0]
    assert (swapped.font_id, swapped.font_size_pt) == (1, 12.0)
    # font_id null không bỏ phiếu font nhưng vẫn bỏ phiếu cỡ; toàn null ⇒ null
    mixed = group_lines(_chars([("x", None, 9.0), ("y", None, 9.0), ("z", 3, 11.0)]), PAGE_BOX, 0)[0]
    assert (mixed.font_id, mixed.font_size_pt) == (3, 9.0)
    empty = group_lines(_chars([("x", None, None)]), PAGE_BOX, 0)[0]
    assert (empty.font_id, empty.font_size_pt) == (None, None)


def test_boxless_words_kept_and_has_table_unchanged() -> None:
    def row(y: float, boxless: bool) -> list[PdfChar]:
        cells = [PdfChar(text=t, box=(x, y, x + 8.0, y + 10.0)) for t, x in (("A", 50.0), ("B", 300.0))]
        tail = [PdfChar(text=" ", box=None), PdfChar(text="Z", box=None)] if boxless else []
        return [*cells[:1], PdfChar(text=" ", box=None), *cells[1:], *tail, PdfChar(text="\n", box=None)]

    with_boxless = group_lines([c for y in (700.0, 680.0, 660.0) for c in row(y, True)], PAGE_BOX, 0)
    without = group_lines([c for y in (700.0, 680.0, 660.0) for c in row(y, False)], PAGE_BOX, 0)
    assert [w.text for w in with_boxless[0].words] == ["A", "B", "Z"]
    assert with_boxless[0].words[2].bbox is None
    assert [line.bbox for line in with_boxless] == [line.bbox for line in without]
    assert has_table(with_boxless, 600, 800, CONFIG) == has_table(without, 600, 800, CONFIG) is True
    only_boxless = group_lines([PdfChar(text="Q", box=None)], PAGE_BOX, 0)[0]
    assert only_boxless.bbox is None and only_boxless.words == [RawWord(text="Q", bbox=None)]


# --------------------------------------------------------------------------- #
# Số đếm độc lập (ADR-010 mục 6)
# --------------------------------------------------------------------------- #


def test_pdfium_nonws_total_counts_spaced_and_normal_text() -> None:
    letters = b"".join(bt("F1", 14, 100 + 30 * i, 700, ch) for i, ch in enumerate("CHAPTER"))
    raw = read(PdfDoc(content=letters + bt("F1", 12, 50, 600, "Mot dong  noi dung")))
    assert "CHAPTER" in [line.text for line in raw.lines]
    assert raw.pdfium_nonws_total == len("CHAPTER") + nonws("Mot dong noi dung")
    assert raw.pdfium_nonws_total == sum(nonws(w.text) for line in raw.lines for w in line.words)
    assert raw.hidden_nonws_dropped == 0


def test_independent_count_segments_and_known_nfd_limitation() -> None:
    def char(text: str, generated: bool = False) -> PdfChar:
        return PdfChar(text=text, box=None, generated=generated)

    stream = [
        (char("a"), 1),
        (char("b"), 1),
        (char(" ", generated=True), None),
        (char("c"), 2),
        (char("\r", generated=True), None),
        (char("\n", generated=True), None),
        (char("d"), 3),
        (char("x", generated=True), None),
    ]
    assert independent_nonws_counts(stream, lambda owner: owner == 3) == (4, 1)
    # NFD trong cùng đối tượng chữ: NFC ghép thành một code point
    assert independent_nonws_counts([(char("e"), 1), (char("́"), 1)], lambda _: False) == (1, 0)
    # Giới hạn đã biết: dấu kết hợp ở đối tượng chữ khác ⇒ hai đoạn ⇒ 2 đơn vị, còn từ của dòng chỉ có 1 (ledger lệch
    # và trang được báo, ADR-010 mục 6)
    assert independent_nonws_counts([(char("e"), 1), (char("́"), 2)], lambda _: False) == (2, 0)
    assert nonws("é") == 1


# --------------------------------------------------------------------------- #
# Thông điệp worker
# --------------------------------------------------------------------------- #


def _many_words_doc() -> PdfDoc:
    body = b"".join(bt("F1", 10, 40, 780 - 12 * row, " ".join(f"w{row}x{i}" for i in range(8))) for row in range(40))
    return PdfDoc(content=body)


def test_font_data_dropped_when_message_over_cap() -> None:
    raw = read(_many_words_doc())
    assert raw.fonts and all(w.font_id == 0 for line in raw.lines for w in line.words)
    full = len(encode_message(PageMessage(page=raw)).encode()) + 2
    stripped = _pdf_worker.without_font_data(raw)
    lean = len(encode_message(PageMessage(page=stripped)).encode()) + 2
    assert lean < full
    fitted = _pdf_worker._fit_message(PageMessage(page=raw), lean)
    assert isinstance(fitted, PageMessage)
    page = fitted.page
    assert page.font_data_dropped and page.fonts == []
    assert all(line.font_id is None and line.font_size_pt is None for line in page.lines)
    assert all(w.font_id is None and w.font_size_pt is None for line in page.lines for w in line.words)
    assert [line.text for line in page.lines] == [line.text for line in raw.lines]
    assert [[w.bbox for w in line.words] for line in page.lines] == [[w.bbox for w in line.words] for line in raw.lines]
    assert _pdf_worker._fit_message(PageMessage(page=raw), full) == PageMessage(page=raw)
    too_small = _pdf_worker._fit_message(PageMessage(page=raw), lean - 1)
    assert too_small == PageErrorMessage(page_index=0, message="MessageTooLarge")


def test_message_without_font_data_has_no_new_keys_and_old_json_parses() -> None:
    page = RawPdfPage(
        page_index=0,
        width_pt=600,
        height_pt=800,
        lines=[
            RawTextLine(text="hi", bbox=(0.1, 0.1, 0.2, 0.2), words=[RawWord(text="hi", bbox=(0.1, 0.1, 0.2, 0.2))])
        ],
        image_area_ratio=0.0,
        path_area_ratio=0.0,
    )
    payload = json.loads(encode_message(PageMessage(page=page)).split(":", 1)[1])
    new_keys = {"fonts", "font_data_dropped", "boxless_words_dropped", "pdfium_nonws_total", "hidden_nonws_dropped"}
    assert not new_keys & set(payload["page"])
    assert (
        "font_id" not in payload["page"]["lines"][0] and "font_size_pt" not in payload["page"]["lines"][0]["words"][0]
    )
    decoded = decode_message(encode_message(PageMessage(page=page)))
    assert decoded == PageMessage(page=page)
    with_fonts = page.model_copy(update={"fonts": [RawFont(font_name="A")], "pdfium_nonws_total": 2})
    assert decode_message(encode_message(PageMessage(page=with_fonts))) == PageMessage(page=with_fonts)


# --------------------------------------------------------------------------- #
# Hiệu năng: cache theo đối tượng chữ
# --------------------------------------------------------------------------- #


def test_font_read_at_most_once_per_text_object(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = PdfiumBackend.load()
    calls: dict[str, int] = {}

    def counting(name: str) -> Callable[..., object]:
        original = getattr(backend, name)

        def wrapper(*args: object) -> object:
            calls[name] = calls.get(name, 0) + 1
            result: object = original(*args)
            return result

        return wrapper

    for name in ("text_font", "font_size_at", "font_name", "font_weight", "font_flags", "font_italic_angle"):
        monkeypatch.setattr(backend, name, counting(name))
    long_text = " ".join(["abcdefghij"] * 30)
    doc = PdfDoc(
        content=bt("F1", 10, 30, 700, long_text) + bt("F1", 10, 30, 600, long_text) + bt("F1", 10, 30, 500, "x")
    )
    raw = read(doc, backend=backend)
    assert sum(len(line.text) for line in raw.lines) > 600
    assert calls["text_font"] == 3 and calls["font_size_at"] == 3
    assert calls["font_name"] == calls["font_weight"] == calls["font_flags"] == calls["font_italic_angle"] == 1


def test_template_scan_path_reads_no_font(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = PdfiumBackend.load()
    called: list[object] = []
    monkeypatch.setattr(backend, "text_font", lambda obj: called.append(obj))
    with _open(PdfDoc(content=bt("F1", 10, 30, 700, "Header")), backend) as document:
        keys = document.page_image_keys(0, CONFIG)
    assert keys is not None and keys.texts and called == []


# --------------------------------------------------------------------------- #
# Không khoá mới trong JSON lưu và response API
# --------------------------------------------------------------------------- #


def _font_rich_page(document_id: str) -> PageExtraction:
    return assemble_text_layer_page(read(_many_words_doc()), document_id, CONFIG)


@pytest.fixture
def sqlite_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[ImportEnv, Path]]:
    for store in open_store("sqlite", tmp_path, monkeypatch):
        reader = FakePdfReader(page_count=1, custom_pages={1: _font_rich_page})
        yield install(store, tmp_path / "files", SynchronousImportQueue(), reader), tmp_path / "sqlite.sqlite3"
        app.dependency_overrides.clear()


def test_extraction_json_and_page_response_have_no_new_keys(sqlite_env: tuple[ImportEnv, Path]) -> None:
    env, db_path = sqlite_env
    content = pdf_bytes("font-keys")
    assert env.post(content).status_code == 202
    doc_id = "doc_" + hashlib.sha256(content).hexdigest()[:16]
    body = env.client.get(f"/v1/documents/{doc_id}/pages/1").json()
    assert set(body) == set(PageExtraction.model_fields)
    with sqlite3.connect(db_path) as conn:
        stored = conn.execute("SELECT extraction_json FROM pages WHERE document_id = ?", (doc_id,)).fetchone()[0]
    assert set(json.loads(stored)) <= set(PageExtraction.model_fields)
    forbidden = {"geometry", "fonts", "font_id", "font_size_pt", "pdfium_nonws_total", "hidden_nonws_dropped"}
    assert not forbidden & _all_keys(body) and not forbidden & _all_keys(json.loads(stored))


def _all_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {key for item in value.values() for key in _all_keys(item)}
    if isinstance(value, list):
        return {key for item in value for key in _all_keys(item)}
    return set()


# --------------------------------------------------------------------------- #
# Vòng sửa 2: ca biên cho các đột biến sống của vòng review (X04)
# --------------------------------------------------------------------------- #


def test_whitespace_does_not_vote_for_line_font_size() -> None:
    spec: list[tuple[str, int | None, float | None]] = [
        ("a", 0, 10.0),
        (" ", None, 20.0),
        ("b", 0, 10.0),
        (" ", None, 20.0),
        ("c", 0, 10.0),
        (" ", None, 20.0),
        (" ", None, 20.0),
    ]
    line = group_lines(_chars(spec), PAGE_BOX, 0)[0]
    assert line.font_size_pt == 10.0 and line.font_id == 0
    assert [w.font_size_pt for w in line.words] == [10.0, 10.0, 10.0]


def test_cap_boxless_words_keeps_page_exactly_at_cap() -> None:
    box = (0.1, 0.1, 0.5, 0.12)
    per_line = 2500
    boxed = [
        RawTextLine(text=" ".join(["a"] * per_line), bbox=box, words=[RawWord(text="a", bbox=box)] * per_line)
        for _ in range(19)
    ]
    short = RawTextLine(text="b", bbox=box, words=[RawWord(text="b", bbox=box)] * (per_line - 1))
    boxless = RawTextLine(text="z", bbox=None, words=[RawWord(text="z", bbox=None)])
    lines = [*boxed, short, boxless]
    assert sum(len(line.words) for line in lines) == MAX_PAGE_WORDS
    assert cap_boxless_words(lines) == (lines, False)


def test_clean_font_name_keeps_only_printable_ascii_up_to_tilde() -> None:
    assert clean_font_name(b"Ari\x7fal") == "Arial"
    assert clean_font_name(b"Ari~al") == "Ari~al"
    assert clean_font_name(b"Ari\x1fal") == "Arial"


def test_clean_font_name_strips_only_six_letter_subset_prefix() -> None:
    assert clean_font_name(b"ABCDEF+Foo") == "Foo"
    assert clean_font_name(b"ABCDE+Foo") == "ABCDE+Foo"
    assert clean_font_name(b"ABCDEFG+Foo") == "ABCDEFG+Foo"


def test_effective_font_size_uses_absolute_determinant_for_flipped_matrix() -> None:
    assert effective_font_size(12.0, [-1.0]) == 12.0
    assert effective_font_size(10.0, [-4.0]) == 20.0


def test_font_table_keeps_fonts_differing_only_in_style_apart() -> None:
    plain = RawFont(font_name="Same")
    bold = RawFont(font_name="Same", bold=True, bold_source="name")
    italic = RawFont(font_name="Same", italic=True, italic_source="flag")
    mono = RawFont(font_name="Same", monospace=True, monospace_source="flag")
    chars = [PdfChar(text="a", box=None, font=f) for f in (plain, bold, italic, mono, bold, plain)]
    fonts, assigned = assign_font_ids(chars)
    assert fonts == [plain, bold, italic, mono]
    assert [c.font_id for c in assigned] == [0, 1, 2, 3, 1, 0]


def test_raw_font_source_iff_value() -> None:
    with pytest.raises(ValidationError, match="nguồn"):
        RawFont(font_name="A", bold=True, bold_source=None)
    with pytest.raises(ValidationError, match="nguồn"):
        RawFont(font_name="A", bold=False, bold_source="name")
    with pytest.raises(ValidationError, match="nguồn"):
        RawFont(font_name="A", italic=True)
    with pytest.raises(ValidationError, match="nguồn"):
        RawFont(font_name="A", monospace=False, monospace_source="flag")
    assert RawFont(font_name="A", bold=True, bold_source="weight").bold_source == "weight"


def test_font_id_upper_bound_is_exclusive() -> None:
    assert RawWord(text="a", bbox=None, font_id=MAX_PAGE_FONTS - 1).font_id == MAX_PAGE_FONTS - 1
    with pytest.raises(ValidationError):
        RawWord(text="a", bbox=None, font_id=MAX_PAGE_FONTS)
    with pytest.raises(ValidationError):
        RawTextLine(text="a", bbox=None, words=[], font_id=MAX_PAGE_FONTS)


def test_raw_font_name_is_never_empty() -> None:
    # SPEC §5.7 chỉ ghi max_length; `min_length=1` là chặt hơn có chủ ý (tên rỗng phải là null)
    with pytest.raises(ValidationError):
        RawFont(font_name="")
    assert RawFont(font_name=None).font_name is None
    assert RawFont(font_name="x" * FONT_NAME_MAX_CHARS).font_name is not None


def test_raw_page_hidden_count_consistency() -> None:
    base: dict[str, object] = {
        "page_index": 0,
        "width_pt": 600,
        "height_pt": 800,
        "lines": [],
        "image_area_ratio": 0.0,
        "path_area_ratio": 0.0,
    }
    ok = RawPdfPage.model_validate({**base, "pdfium_nonws_total": 5, "hidden_nonws_dropped": 5})
    assert ok.hidden_nonws_dropped == 5
    with pytest.raises(ValidationError, match="hidden_nonws_dropped"):
        RawPdfPage.model_validate({**base, "pdfium_nonws_total": 5, "hidden_nonws_dropped": 10**9})
    with pytest.raises(ValidationError, match="hidden_nonws_dropped"):
        RawPdfPage.model_validate({**base, "hidden_nonws_dropped": 1})
    assert RawPdfPage.model_validate(base).hidden_nonws_dropped == 0


def test_form_matrix_scales_font_size() -> None:
    # `cm` đặt trước `Do` là ma trận của đối tượng form (det 4); `/Matrix` của form là phần còn lại (det 9)
    scaled_by_cm = PdfDoc(
        content=b"q 2 0 0 2 0 0 cm /X1 Do Q\n",
        forms={"X1": ((1, 0, 0, 1, 0, 0), bt("F1", 10, 20, 30, "InForm"))},
    )
    assert line_of(read(scaled_by_cm), "InForm").font_size_pt == 20.0
    both = PdfDoc(
        content=b"q 2 0 0 2 0 0 cm /X1 Do Q\n",
        forms={"X1": ((3, 0, 0, 3, 0, 0), bt("F1", 10, 20, 30, "InForm"))},
    )
    assert line_of(read(both), "InForm").font_size_pt == 60.0


def test_boxless_words_over_cap_in_pdf_are_dropped_not_page_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def many_chars(self: PdfiumDocument, page: object, fonts: object = None) -> list[tuple[PdfChar, int | None]]:
        out: list[tuple[PdfChar, int | None]] = []
        for _ in range(MAX_PAGE_WORDS // 10):
            for _ in range(10):
                out += [(PdfChar(text="a", box=(10.0, 10.0, 18.0, 20.0)), None), (PdfChar(text=" ", box=None), None)]
            out.append((PdfChar(text="\n", box=None), None))
        out += [(PdfChar(text="z", box=None), None), (PdfChar(text="\n", box=None), None)]
        return out

    monkeypatch.setattr(PdfiumDocument, "_read_chars", many_chars)
    raw = read(PdfDoc(content=bt("F1", 12, 50, 700, "x")))
    assert raw.boxless_words_dropped is True
    assert sum(len(line.words) for line in raw.lines) == MAX_PAGE_WORDS
    assert raw.lines[-1].text == "z" and raw.lines[-1].words == []


def test_reader_settings_hash_is_stable_across_hash_seeds() -> None:
    code = (
        "from holocron_core.infrastructure.document_readers.config import PdfReaderConfig;"
        "from holocron_core.infrastructure.document_readers.tiers import reader_settings_sha256;"
        "print(reader_settings_sha256(PdfReaderConfig()))"
    )
    backend_dir = Path(__file__).resolve().parents[1]
    digests = set()
    for seed in ("1", "2", "3", "4", "5", "6"):
        env = {**os.environ, "PYTHONHASHSEED": seed, "PYTHONUTF8": "1"}
        out = subprocess.run(
            [sys.executable, "-c", code], cwd=backend_dir, env=env, capture_output=True, text=True, check=True
        )
        digests.add(out.stdout.strip())
    assert len(digests) == 1


def test_raw_page_hidden_count_one_over_total_is_rejected() -> None:
    """Biên chặt: dropped = total + 1 bị từ chối, dropped = total hợp lệ."""
    base: dict[str, object] = {
        "page_index": 0,
        "width_pt": 600,
        "height_pt": 800,
        "lines": [],
        "image_area_ratio": 0.0,
        "path_area_ratio": 0.0,
    }
    assert (
        RawPdfPage.model_validate({**base, "pdfium_nonws_total": 5, "hidden_nonws_dropped": 5}).hidden_nonws_dropped
        == 5
    )
    with pytest.raises(ValidationError, match="hidden_nonws_dropped"):
        RawPdfPage.model_validate({**base, "pdfium_nonws_total": 5, "hidden_nonws_dropped": 6})
