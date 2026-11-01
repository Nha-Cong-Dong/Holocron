"""PDF và geometry mẫu cho test bộ dựng khối (ADR-010; X05). Không dùng tài liệu thật.

- `build_pdf`: PDF viết tay, font chuẩn Helvetica (`/F1`) và Helvetica-Bold (`/F2`), mã WinAnsi (cp1252); mỗi đoạn chữ
  là một đối tượng chữ riêng; chữ ẩn là chữ trắng trên nền trắng (bộ đọc loại theo tương phản, GĐ0 0B(1)).
- `seeded_page`: một trang tổng hợp theo seed: tiêu đề đậm, header lặp, số trang ở chân, một hay hai cột, mục danh sách
  nhiều dòng có bullet, gạch nối cuối dòng và một đoạn chữ ẩn.
- `geo_line`, `geometry`, `extraction_for`: dựng `PageGeometry`/`PageExtraction` bằng tay cho test hình học.
"""

from __future__ import annotations

import random
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from holocron_core.domain.documents import (
    CrossCheck,
    ExtractedLine,
    GeoFont,
    GeoLine,
    GeoWord,
    PageExtraction,
    PageGeometry,
)

DOC_ID = "doc_00000000000000b1"
READER_SHA = "0" * 64
PAGE_WIDTH = 612.0
PAGE_HEIGHT = 792.0

_VOCAB = [
    "process",
    "scheduler",
    "memory",
    "kernel",
    "thread",
    "queue",
    "buffer",
    "cache",
    "signal",
    "device",
    "driver",
    "page",
    "frame",
    "table",
    "segment",
    "register",
    "value",
    "system",
    "resource",
    "lock",
    "state",
    "event",
    "timer",
    "priority",
    "vector",
    "record",
    "field",
    "index",
    "matrix",
    "graph",
    "node",
    "edge",
    "weight",
    "path",
    "cycle",
    "tree",
    "root",
    "leaf",
    "branch",
    "level",
    "order",
    "sample",
    "result",
    "method",
]


@dataclass(frozen=True)
class Run:
    """Một đối tượng chữ: (x, y) là đường chân chữ, toạ độ PDF (gốc dưới-trái)."""

    x: float
    y: float
    text: str
    size: float = 11.0
    bold: bool = False
    hidden: bool = False


def _escape(value: str) -> bytes:
    out = bytearray()
    for byte in value.encode("cp1252"):
        if byte in b"()\\":
            out += b"\\" + bytes([byte])
        elif 32 <= byte < 127:
            out.append(byte)
        else:
            out += b"\\%03o" % byte
    return bytes(out)


def _ops(runs: list[Run]) -> bytes:
    parts = [
        b"BT /F%d %.2f Tf 0 Tr %s rg 1 0 0 1 %.2f %.2f Tm (%s) Tj ET"
        % (2 if run.bold else 1, run.size, b"1 1 1" if run.hidden else b"0 0 0", run.x, run.y, _escape(run.text))
        for run in runs
    ]
    return b"\n".join(parts) + b"\n"


def build_pdf(pages: list[list[Run]]) -> bytes:
    objects: dict[int, bytes] = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        3: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        4: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>",
    }
    kids: list[int] = []
    number = 5
    for runs in pages:
        page_number, content_number = number, number + 1
        number += 2
        kids.append(page_number)
        objects[page_number] = (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %.0f %.0f] /Contents %d 0 R "
            b"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> >>" % (PAGE_WIDTH, PAGE_HEIGHT, content_number)
        )
        data = _ops(runs)
        objects[content_number] = b"<< /Length %d >>\nstream\n%s\nendstream" % (len(data), data)
    objects[2] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (b" ".join(b"%d 0 R" % k for k in kids), len(kids))
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: dict[int, int] = {}
    for key in sorted(objects):
        offsets[key] = len(out)
        out += b"%d 0 obj\n%s\nendobj\n" % (key, objects[key])
    xref_at = len(out)
    size = max(objects) + 1
    out += b"xref\n0 %d\n0000000000 65535 f \n" % size
    for key in range(1, size):
        out += b"%010d 00000 n \n" % offsets[key]
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (size, xref_at)
    return bytes(out)


def _words(rng: random.Random, count: int) -> list[str]:
    return [rng.choice(_VOCAB) for _ in range(count)]


def _paragraph_lines(rng: random.Random, lines: int, words_per_line: int) -> list[str]:
    """Dòng văn xuôi; đôi khi dòng kết thúc bằng gạch nối cuối dòng (`exam-` + `ple`)."""
    out: list[str] = []
    carry = ""
    for _ in range(lines):
        words = _words(rng, words_per_line)
        if carry:
            words.insert(0, carry)
            carry = ""
        if rng.random() < 0.3:
            cut = rng.choice(_VOCAB)
            words.append(cut[: len(cut) // 2] + "-")
            carry = cut[len(cut) // 2 :]
        out.append(" ".join(words))
    if carry:
        out.append(carry + " end.")
    return out


def seeded_page(seed: int, page_number: int) -> list[Run]:
    """Trang tổng hợp theo seed (xem docstring module); header và số trang lặp trên mọi trang."""
    rng = random.Random(seed)
    runs = [
        Run(72, 760, "Course Notes Sample Header", size=9),
        Run(300, 30, str(page_number), size=9),
        Run(72, 715, f"Section {seed} " + " ".join(_words(rng, 3)).title(), size=16, bold=True),
    ]
    two_columns = seed % 2 == 1
    columns = [(72.0, 270.0), (320.0, 270.0)] if two_columns else [(72.0, 468.0)]
    words_per_line = 6 if two_columns else 11
    for left, _width in columns:
        y = 685.0
        for _block in range(rng.randint(2, 3)):
            for text in _paragraph_lines(rng, rng.randint(2, 4), words_per_line):
                runs.append(Run(left, y, text))
                y -= 14
            y -= 12
            if rng.random() < 0.6:
                for _item in range(rng.randint(1, 3)):
                    first = True
                    for text in _paragraph_lines(rng, rng.randint(1, 2), words_per_line - 2):
                        runs.append(Run(left, y, "• " + text) if first else Run(left + 12, y, text))
                        first = False
                        y -= 14
                y -= 12
        if rng.random() < 0.7:
            runs.append(Run(left, y, "hidden " + " ".join(_words(rng, 3)), hidden=True))
    return runs


def write_document(path: Path, pages: int) -> Path:
    """PDF `pages` trang, trang thứ i (1-based) dùng seed i."""
    path.write_bytes(build_pdf([seeded_page(seed, seed) for seed in range(1, pages + 1)]))
    return path


# --------------------------------------------------------------------------- #
# Geometry dựng tay
# --------------------------------------------------------------------------- #

REGULAR = GeoFont(
    font_name="Helvetica",
    bold=False,
    italic=False,
    monospace=False,
    bold_source=None,
    italic_source=None,
    monospace_source=None,
)
BOLD = GeoFont(
    font_name="Helvetica-Bold",
    bold=True,
    italic=False,
    monospace=False,
    bold_source="name",
    italic_source=None,
    monospace_source=None,
)


def geo_line(
    text: str,
    x0: float | None,
    y0: float,
    *,
    height: float = 0.012,
    char_w: float = 0.006,
    size: float = 11.0,
    font_id: int | None = 0,
    boxless: set[int] | None = None,
    flags: list[str] | None = None,
    header_footer: bool = False,
    order: list[int] | None = None,
) -> GeoLine:
    """Dòng thô có các từ cách nhau một ký tự; `x0 = None` ⇒ mọi từ không hộp; `boxless` = chỉ số từ không hộp (không
    chiếm chỗ theo x);
    `order` = thứ tự luồng PDFium của các từ (mặc định theo x)."""
    words: list[GeoWord] = []
    x = x0 or 0.0
    boxless = boxless or set()
    for i, word in enumerate(text.split()):
        width = char_w * len(word)
        box = None if x0 is None or i in boxless else (x, y0, min(x + width, 1.0), y0 + height)
        words.append(GeoWord(text=word, bbox=box, font_id=font_id, font_size_pt=size))
        if box is not None:
            x += width + char_w
    if order is not None:
        words = [words[i] for i in order]
    boxes = [w.bbox for w in words if w.bbox is not None]
    bbox = None
    if boxes:
        bbox = (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))
    return GeoLine.model_validate(
        {
            "words": words,
            "bbox": bbox,
            "font_id": font_id,
            "font_size_pt": size,
            "flags": flags or [],
            "header_footer": header_footer,
            "extracted_line_indices": [],
        }
    )


def nonws(text: str) -> int:
    return sum(1 for c in unicodedata.normalize("NFC", text) if not c.isspace())


def geometry(
    lines: list[GeoLine], *, hidden: int = 0, fonts: list[GeoFont] | None = None, **extra: object
) -> PageGeometry:
    """`PageGeometry` có `extracted_line_indices` liên tục (mỗi dòng có chữ một mảnh) và số đếm độc lập khớp."""
    indexed: list[GeoLine] = []
    position = 0
    for line in lines:
        if line.words:
            indexed.append(line.model_copy(update={"extracted_line_indices": [position]}))
            position += 1
        else:
            indexed.append(line)
    total = sum(nonws(w.text) for line in lines for w in line.words) + hidden
    data: dict[str, object] = {
        "version": 1,
        "reader_settings_sha256": READER_SHA,
        "source": "text_layer",
        "lines": indexed,
        "fonts": [REGULAR, BOLD] if fonts is None else fonts,
        "pdfium_nonws_total": total,
        "hidden_nonws_dropped": hidden,
    }
    data.update(extra)
    return PageGeometry.model_validate(data)


def extraction_for(geo: PageGeometry, page_number: int = 1, *, document_id: str = DOC_ID) -> PageExtraction:
    """`PageExtraction` khớp geometry: mỗi dòng có chữ một `ExtractedLine` (chữ = các từ nối bằng dấu cách)."""
    lines: list[ExtractedLine] = []
    for line in geo.lines:
        for _ in line.extracted_line_indices:
            flags = [] if line.header_footer else list(line.flags)
            lines.append(
                ExtractedLine(
                    line_index=len(lines),
                    text=" ".join(w.text for w in line.words),
                    bbox=line.bbox,
                    conf=1.0,
                    engine="text_layer",
                    flags=flags,
                    is_header_footer=line.header_footer,
                )
            )
    return PageExtraction(
        document_id=document_id,
        page_number=page_number,
        status="ok",
        tier_used="text_layer",
        text_score=1.0,
        lines=lines,
        cross_check=CrossCheck(status="not_applicable", secondary_engine=None),
    )
