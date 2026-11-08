"""Ảnh xám của trang để OCR (ADR-008 mục 4, 8, 9): chọn tỉ lệ render, mã hoá PNG, đếm mực ngoài hộp chữ.

Hàm thuần, chỉ dùng thư viện chuẩn (không numpy/OpenCV), để chạy được cả khi lượt đọc thứ hai vắng mặt. Ảnh là
dãy byte xám 8 bit, mỗi dòng đúng `width` byte (đã bỏ phần đệm `stride` của bitmap PDFium).
"""

from __future__ import annotations

import math
import struct
import zlib
from collections.abc import Sequence
from dataclasses import dataclass

from .config import DEFAULT_RENDER_SHRINK_FACTOR

POINTS_PER_INCH = 72.0
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

PixelBox = tuple[int, int, int, int]  # (left, top, right, bottom) theo điểm ảnh, right/bottom không tính


@dataclass(frozen=True)
class GrayImage:
    """Ảnh xám đã render; `dpi` là độ phân giải thật sau khi giảm cho vừa giới hạn điểm ảnh."""

    pixels: bytes
    width: int
    height: int
    dpi: float

    def __post_init__(self) -> None:
        if self.width < 1 or self.height < 1 or len(self.pixels) != self.width * self.height:
            raise ValueError("kích thước ảnh không khớp số byte")


def render_scale(
    width_pt: float,
    height_pt: float,
    dpi: int,
    max_pixels: int,
    *,
    shrink_factor: float = DEFAULT_RENDER_SHRINK_FACTOR,
) -> float:
    """Hệ số điểm ảnh trên point: `dpi / 72`, giảm dần khi ảnh vượt `max_pixels` (ADR-008 mục 4).

    Kích thước ảnh của PDFium là `ceil(cạnh × hệ số)`, nên hệ số được nhân tiếp với `shrink_factor`
    (`PdfReaderConfig.render_shrink_factor`) cho tới khi tích hai cạnh đã làm tròn lên không vượt `max_pixels`.
    """
    if width_pt <= 0 or height_pt <= 0:
        raise ValueError("khổ trang phải dương")
    if not 0.0 < shrink_factor < 1.0:
        raise ValueError("shrink_factor phải nằm trong (0, 1)")
    scale = min(dpi / POINTS_PER_INCH, math.sqrt(max_pixels / (width_pt * height_pt)))
    while math.ceil(width_pt * scale) * math.ceil(height_pt * scale) > max_pixels:
        scale *= shrink_factor
    return scale


def pack_rows(buffer: bytes, width: int, height: int, stride: int) -> bytes:
    """Bỏ byte đệm cuối mỗi dòng của bitmap (`stride ≥ width`)."""
    if stride < width or len(buffer) < stride * height:
        raise ValueError("bitmap không đủ byte cho kích thước đã báo")
    if stride == width:
        return bytes(buffer[: width * height])
    return b"".join(buffer[row * stride : row * stride + width] for row in range(height))


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def encode_png_gray(image: GrayImage) -> bytes:
    """PNG xám 8 bit, không lọc dòng, kèm `pHYs` ghi DPI để engine OCR biết độ phân giải."""
    header = struct.pack(">IIBBBBB", image.width, image.height, 8, 0, 0, 0, 0)
    width = image.width
    raw = b"".join(b"\x00" + image.pixels[row * width : (row + 1) * width] for row in range(image.height))
    pixels_per_meter = round(image.dpi / 0.0254)
    physical = struct.pack(">IIB", pixels_per_meter, pixels_per_meter, 1)
    return (
        PNG_SIGNATURE
        + _chunk(b"IHDR", header)
        + _chunk(b"pHYs", physical)
        + _chunk(b"IDAT", zlib.compress(raw, 1))
        + _chunk(b"IEND", b"")
    )


def _ink_table(threshold: int) -> bytes:
    """Bảng `bytes.translate`: điểm ảnh tối hơn ngưỡng ⇒ 1 (mực), còn lại ⇒ 0."""
    return bytes(1 if value < threshold else 0 for value in range(256))


def _merge(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def ink_outside_text_ratio(image: GrayImage, boxes: Sequence[PixelBox], threshold: int) -> float:
    """Tỉ lệ điểm ảnh mực nằm ngoài mọi hộp chữ, trên tổng số điểm ảnh mực của trang (ADR-008 mục 9).

    Mẫu số là tổng điểm ảnh mực sau nhị phân hoá (đã duyệt 2026-10-24); trang không có mực cho 0.0. Các hộp chồng
    nhau chỉ được đếm một lần.
    """
    ink = image.pixels.translate(_ink_table(threshold))
    total = ink.count(1)
    if total == 0:
        return 0.0
    rows: dict[int, list[tuple[int, int]]] = {}
    for left, top, right, bottom in boxes:
        x0, x1 = max(0, left), min(image.width, right)
        if x0 >= x1:
            continue
        for row in range(max(0, top), min(image.height, bottom)):
            rows.setdefault(row, []).append((x0, x1))
    inside = 0
    for row, intervals in rows.items():
        offset = row * image.width
        inside += sum(ink.count(1, offset + start, offset + end) for start, end in _merge(intervals))
    return min(1.0, max(0.0, (total - inside) / total))


# --------------------------------------------------------------------------- #
# Ảnh cắt vùng (ADR-010 mục 9; ADR-009 mục 5)
# --------------------------------------------------------------------------- #

UnitBox = tuple[float, float, float, float]  # (x0, y0, x1, y1) ∈ [0, 1], gốc trên-trái của trang đã xoay
_IHDR_END = 8 + 8 + 13  # chữ ký + (độ dài, loại) + 13 byte dữ liệu IHDR


def expand_box(box: UnitBox, margin: float) -> UnitBox | None:
    """Hộp nới `margin` mỗi phía, kẹp trong [0, 1]; hộp suy biến (rỗng, đảo, không hữu hạn) ⇒ None."""
    x0, y0, x1, y1 = box
    if not all(math.isfinite(value) for value in box) or x1 <= x0 or y1 <= y0:
        return None
    return (max(0.0, x0 - margin), max(0.0, y0 - margin), min(1.0, x1 + margin), min(1.0, y1 + margin))


def crop_pixels(image: GrayImage, box: UnitBox) -> GrayImage | None:
    """Phần ảnh nằm trong `box` (toạ độ chuẩn hoá); vùng không phủ trọn một điểm ảnh nào ⇒ None."""
    left = math.floor(box[0] * image.width)
    top = math.floor(box[1] * image.height)
    right = min(image.width, math.ceil(box[2] * image.width))
    bottom = min(image.height, math.ceil(box[3] * image.height))
    if right <= left or bottom <= top:
        return None
    width = right - left
    pixels = b"".join(image.pixels[row * image.width + left : row * image.width + right] for row in range(top, bottom))
    return GrayImage(pixels=pixels, width=width, height=bottom - top, dpi=image.dpi)


def fit_long_edge(image: GrayImage, max_edge_px: int) -> GrayImage:
    """Thu ảnh (láng giềng gần nhất) để cạnh dài ≤ `max_edge_px`; ảnh đã vừa thì giữ nguyên."""
    long_edge = max(image.width, image.height)
    if long_edge <= max_edge_px:
        return image
    ratio = max_edge_px / long_edge
    width = max(1, min(max_edge_px, math.floor(image.width * ratio)))
    height = max(1, min(max_edge_px, math.floor(image.height * ratio)))
    columns = [min(image.width - 1, (x * image.width) // width) for x in range(width)]
    rows: list[bytes] = []
    for y in range(height):
        source = (y * image.height) // height
        line = image.pixels[source * image.width : (source + 1) * image.width]
        rows.append(bytes(line[column] for column in columns))
    return GrayImage(pixels=b"".join(rows), width=width, height=height, dpi=image.dpi * ratio)


def png_dimensions(data: bytes) -> tuple[int, int] | None:
    """`(width, height)` đọc từ IHDR của PNG; sai chữ ký hay IHDR không đứng đầu ⇒ None."""
    if len(data) < _IHDR_END or not data.startswith(PNG_SIGNATURE) or data[12:16] != b"IHDR":
        return None
    if struct.unpack(">I", data[8:12])[0] != 13:
        return None
    width, height = struct.unpack(">II", data[16:24])
    if width < 1 or height < 1:
        return None
    return int(width), int(height)
