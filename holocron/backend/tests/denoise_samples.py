"""PDF tổng hợp cho test khử nhiễu GĐ0 (spec §5.7): toán tử nội dung viết tay, thứ tự vẽ do test quyết định.

Không dùng tài liệu thật (eval_data chỉ để đo, không dùng trong pytest). Mỗi trang là một dãy toán tử nội dung theo
đúng thứ tự vẽ; tài nguyên của trang gồm font Helvetica (`/F1`), các ảnh xám tự tạo và các form XObject của trang
(form dùng chung tài nguyên của trang, nên form có thể gọi chính nó hay form khác để thử trần độ sâu).
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass, field

Matrix = tuple[float, float, float, float, float, float]


@dataclass(frozen=True)
class GrayImage:
    """Ảnh xám 8 bit `width` × `height` (dòng điểm ảnh nối liền)."""

    width: int
    height: int
    pixels: bytes


@dataclass(frozen=True)
class Form:
    name: str
    content: bytes
    bbox: tuple[float, float, float, float] = (0.0, 0.0, 1000.0, 1000.0)


@dataclass
class SyntheticPage:
    ops: list[bytes] = field(default_factory=list)
    images: dict[str, GrayImage] = field(default_factory=dict)
    forms: list[Form] = field(default_factory=list)
    width: float = 612.0
    height: float = 792.0


def _escape(value: str) -> bytes:
    raw = value.encode("latin-1")
    out = bytearray()
    for byte in raw:
        if byte in b"()\\":
            out += b"\\" + bytes([byte])
        elif 32 <= byte < 127:
            out.append(byte)
        else:
            out += b"\\%03o" % byte
    return bytes(out)


def text(
    x: float, y: float, value: str, *, size: float = 12.0, rgb: tuple[float, float, float] = (0, 0, 0), mode: int = 0
) -> bytes:
    """Một đối tượng chữ Helvetica; `mode` là chế độ vẽ chữ (`Tr`), luôn đặt tường minh."""
    r, g, b = rgb
    return b"BT /F1 %.2f Tf %.3f %.3f %.3f rg %.3f %.3f %.3f RG %d Tr %.2f %.2f Td (%s) Tj ET" % (
        size,
        r,
        g,
        b,
        r,
        g,
        b,
        mode,
        x,
        y,
        _escape(value),
    )


def rect(
    x: float, y: float, width: float, height: float, *, rgb: tuple[float, float, float] = (0, 0, 0), fill: bool = True
) -> bytes:
    """Hình chữ nhật tô đặc (`f`) hoặc chỉ viền (`S`)."""
    r, g, b = rgb
    paint = b"f" if fill else b"S"
    return b"q %.3f %.3f %.3f rg %.3f %.3f %.3f RG %.2f %.2f %.2f %.2f re %s Q" % (
        r,
        g,
        b,
        r,
        g,
        b,
        x,
        y,
        width,
        height,
        paint,
    )


def image(name: str, x: float, y: float, width: float, height: float) -> bytes:
    return b"q %.2f 0 0 %.2f %.2f %.2f cm /%s Do Q" % (width, height, x, y, name.encode("ascii"))


def form(name: str, matrix: Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)) -> bytes:
    return b"q %.4f %.4f %.4f %.4f %.4f %.4f cm /%s Do Q" % (*matrix, name.encode("ascii"))


def solid(value: int, width: int = 4, height: int = 4) -> GrayImage:
    return GrayImage(width, height, bytes([value]) * (width * height))


def gradient(width: int = 32, height: int = 32, *, reverse: bool = False, seed: int = 0) -> GrayImage:
    """Ảnh xám có vân (khác nhau theo `seed`, `reverse`) để dHash có ý nghĩa."""
    rows = bytearray()
    for y in range(height):
        for x in range(width):
            value = (x * 255 // max(width - 1, 1) + seed * 37 * (y % 4)) % 256
            rows.append(255 - value if reverse else value)
    return GrayImage(width, height, bytes(rows))


def build(pages: list[SyntheticPage]) -> bytes:
    objects: dict[int, bytes] = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        3: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    }
    streams: dict[int, tuple[bytes, bytes]] = {}
    next_number = 4
    kids: list[int] = []
    for page in pages:
        page_number, content_number = next_number, next_number + 1
        next_number += 2
        kids.append(page_number)
        refs: dict[str, int] = {}
        for name, picture in page.images.items():
            if len(picture.pixels) != picture.width * picture.height:
                raise ValueError("số điểm ảnh không khớp kích thước ảnh")
            refs[name] = next_number
            streams[next_number] = (
                b"/Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /DeviceGray /BitsPerComponent 8 "
                b"/Filter /FlateDecode" % (picture.width, picture.height),
                zlib.compress(picture.pixels),
            )
            next_number += 1
        for item in page.forms:
            refs[item.name] = next_number
            next_number += 1
        xobjects = b" ".join(b"/%s %d 0 R" % (name.encode("ascii"), number) for name, number in refs.items())
        resources = b"<< /Font << /F1 3 0 R >> /XObject << %s >> >>" % xobjects
        for item in page.forms:
            streams[refs[item.name]] = (
                b"/Type /XObject /Subtype /Form /BBox [%.2f %.2f %.2f %.2f] /Resources %s" % (*item.bbox, resources),
                item.content,
            )
        objects[page_number] = (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %.2f %.2f] /Contents %d 0 R /Resources %s >>"
            % (
                page.width,
                page.height,
                content_number,
                resources,
            )
        )
        streams[content_number] = (b"", b"\n".join(page.ops) + b"\n")
    objects[2] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (b" ".join(b"%d 0 R" % k for k in kids), len(kids))
    for number, (extra, data) in streams.items():
        objects[number] = b"<< %s /Length %d >>\nstream\n%s\nendstream" % (extra, len(data), data)
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: dict[int, int] = {}
    for number in sorted(objects):
        offsets[number] = len(out)
        out += b"%d 0 obj\n%s\nendobj\n" % (number, objects[number])
    xref_at = len(out)
    size = max(objects) + 1
    out += b"xref\n0 %d\n0000000000 65535 f \n" % size
    for number in range(1, size):
        out += b"%010d 00000 n \n" % offsets[number]
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (size, xref_at)
    return bytes(out)
