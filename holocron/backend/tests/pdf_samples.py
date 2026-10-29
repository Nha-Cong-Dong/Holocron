"""Bộ dựng PDF mẫu nhỏ, tự tạo lúc chạy test (ADR-008, "Hệ quả": test bằng file mẫu tự tạo).

Viết tay cú pháp PDF tối thiểu, không dùng tài liệu thật nào (tài liệu môn học có bản quyền nằm ngoài repo).
Hỗ trợ: chữ bằng font chuẩn Helvetica (WinAnsi, ký tự Latin-1) và Symbol, ảnh 1x1 kéo giãn, ảnh xám tuỳ ý (nén
Flate, dùng làm trang scan giả lập), khung chữ nhật, xoay trang, và mã hoá RC4 40-bit (Standard Security Handler R2)
để thử PDF có mật khẩu.
"""

from __future__ import annotations

import hashlib
import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path

_PAD = bytes.fromhex("28BF4E5E4E758A4164004E56FFFA01082E2E00B6D0683E802F0CA9FE6453697A")
_FILE_ID = bytes.fromhex("0123456789abcdef0123456789abcdef")
_PERMISSIONS = -44


@dataclass(frozen=True)
class Text:
    x: float
    y: float  # đường chân chữ, toạ độ PDF (gốc dưới-trái)
    value: str
    size: float = 12.0
    font: str = "F1"  # F1 = Helvetica (WinAnsi), F2 = Symbol (mã byte của font Symbol)


@dataclass(frozen=True)
class Box:
    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True)
class Raster:
    """Ảnh xám 8 bit `width` x `height` (các dòng điểm ảnh nối liền, không đệm) vẽ kéo giãn vào `box`."""

    box: Box
    width: int
    height: int
    pixels: bytes


@dataclass
class Page:
    texts: list[Text] = field(default_factory=list)
    images: list[Box] = field(default_factory=list)
    rasters: list[Raster] = field(default_factory=list)
    rects: list[Box] = field(default_factory=list)
    width: float = 612.0
    height: float = 792.0
    rotate: int = 0


def _rc4(key: bytes, data: bytes) -> bytes:
    state = list(range(256))
    j = 0
    for i in range(256):
        j = (j + state[i] + key[i % len(key)]) % 256
        state[i], state[j] = state[j], state[i]
    out = bytearray()
    i = j = 0
    for byte in data:
        i = (i + 1) % 256
        j = (j + state[i]) % 256
        state[i], state[j] = state[j], state[i]
        out.append(byte ^ state[(state[i] + state[j]) % 256])
    return bytes(out)


def _padded(password: str) -> bytes:
    return (password.encode("latin-1") + _PAD)[:32]


@dataclass(frozen=True)
class _Encryption:
    owner_entry: bytes
    user_entry: bytes
    file_key: bytes

    @classmethod
    def create(cls, user_password: str, owner_password: str) -> _Encryption:
        owner_key = hashlib.md5(_padded(owner_password)).digest()[:5]
        owner_entry = _rc4(owner_key, _padded(user_password))
        digest_input = _padded(user_password) + owner_entry + struct.pack("<i", _PERMISSIONS) + _FILE_ID
        file_key = hashlib.md5(digest_input).digest()[:5]
        return cls(owner_entry=owner_entry, user_entry=_rc4(file_key, _PAD), file_key=file_key)

    def encrypt(self, obj_number: int, data: bytes) -> bytes:
        key = hashlib.md5(self.file_key + struct.pack("<i", obj_number)[:3] + b"\x00\x00").digest()[:10]
        return _rc4(key, data)


def _escape(raw: bytes) -> bytes:
    out = bytearray()
    for byte in raw:
        if byte in b"()\\":
            out += b"\\" + bytes([byte])
        elif 32 <= byte < 127:
            out.append(byte)
        else:
            out += b"\\%03o" % byte
    return bytes(out)


def _content(page: Page) -> bytes:
    parts: list[bytes] = []
    for rect in page.rects:
        parts.append(b"0.5 w %.2f %.2f %.2f %.2f re S" % (rect.x, rect.y, rect.width, rect.height))
    for image in page.images:
        parts.append(b"q %.2f 0 0 %.2f %.2f %.2f cm /Im1 Do Q" % (image.width, image.height, image.x, image.y))
    for number, raster in enumerate(page.rasters):
        box = raster.box
        parts.append(b"q %.2f 0 0 %.2f %.2f %.2f cm /R%d Do Q" % (box.width, box.height, box.x, box.y, number))
    for text in page.texts:
        raw = text.value.encode("latin-1")
        font = text.font.encode("ascii")
        parts.append(b"BT /%s %.2f Tf %.2f %.2f Td (%s) Tj ET" % (font, text.size, text.x, text.y, _escape(raw)))
    return b"\n".join(parts) + b"\n"


def build_pdf(pages: list[Page], *, user_password: str | None = None, owner_password: str = "owner") -> bytes:
    """Dựng PDF; `user_password` khác None ⇒ mã hoá RC4 40-bit (chuỗi rỗng = chỉ có mật khẩu chủ)."""
    encryption = None if user_password is None else _Encryption.create(user_password, owner_password)
    font_helvetica, font_symbol, image = 3, 4, 5
    first_page = 6
    kids = [first_page + 2 * i for i in range(len(pages))]
    next_number = first_page + 2 * len(pages)
    objects: dict[int, bytes] = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Kids [%s] /Count %d >>" % (b" ".join(b"%d 0 R" % k for k in kids), len(pages)),
        font_helvetica: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        font_symbol: b"<< /Type /Font /Subtype /Type1 /BaseFont /Symbol >>",
    }
    streams: dict[int, tuple[bytes, bytes]] = {
        image: (
            b"/Type /XObject /Subtype /Image /Width 1 /Height 1 /ColorSpace /DeviceGray /BitsPerComponent 8",
            b"\x80",
        )
    }
    for index, page in enumerate(pages):
        page_number = first_page + 2 * index
        content_number = page_number + 1
        raster_refs = b""
        for raster_index, raster in enumerate(page.rasters):
            if len(raster.pixels) != raster.width * raster.height:
                raise ValueError("số điểm ảnh không khớp kích thước ảnh")
            streams[next_number] = (
                b"/Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /DeviceGray /BitsPerComponent 8 "
                b"/Filter /FlateDecode" % (raster.width, raster.height),
                zlib.compress(raster.pixels),
            )
            raster_refs += b" /R%d %d 0 R" % (raster_index, next_number)
            next_number += 1
        objects[page_number] = (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %.2f %.2f] /Rotate %d /Contents %d 0 R "
            b"/Resources << /Font << /F1 %d 0 R /F2 %d 0 R >> /XObject << /Im1 %d 0 R%s >> >> >>"
            % (page.width, page.height, page.rotate, content_number, font_helvetica, font_symbol, image, raster_refs)
        )
        streams[content_number] = (b"", _content(page))
    for number, (extra, data) in streams.items():
        payload = encryption.encrypt(number, data) if encryption else data
        objects[number] = b"<< %s /Length %d >>\nstream\n%s\nendstream" % (extra, len(payload), payload)

    trailer_extra = b""
    if encryption is not None:
        encrypt_number = max(objects) + 1
        objects[encrypt_number] = b"<< /Filter /Standard /V 1 /R 2 /O <%s> /U <%s> /P %d >>" % (
            encryption.owner_entry.hex().encode(),
            encryption.user_entry.hex().encode(),
            _PERMISSIONS,
        )
        trailer_extra = b" /Encrypt %d 0 R" % encrypt_number

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
    file_id = _FILE_ID.hex().encode()
    out += b"trailer\n<< /Size %d /Root 1 0 R /ID [<%s> <%s>]%s >>\n" % (size, file_id, file_id, trailer_extra)
    out += b"startxref\n%d\n%%%%EOF\n" % xref_at
    return bytes(out)


def text_lines(lines: list[str], *, top: float = 740.0, step: float = 16.0, x: float = 56.0) -> list[Text]:
    """Các dòng chữ Helvetica cỡ 12, xếp từ trên xuống."""
    return [Text(x=x, y=top - step * i, value=line) for i, line in enumerate(lines)]


def slow_page() -> Page:
    """Trang có khoảng 300 000 ký tự nhỏ: đọc lớp chữ mất vài giây, đủ vượt timeout rút ngắn trong test."""
    return Page(texts=[Text(10.0, 780.0 - (k % 390) * 2.0, "abcdefghij" * 10, size=2.0) for k in range(3000)])


def write_pdf(path: Path, pages: list[Page], *, user_password: str | None = None) -> Path:
    path.write_bytes(build_pdf(pages, user_password=user_password))
    return path
