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


def render_scale(width_pt: float, height_pt: float, dpi: int, max_pixels: int) -> float:
    """Hệ số điểm ảnh trên point: `dpi / 72`, giảm dần khi ảnh vượt `max_pixels` (ADR-008 mục 4).

    Kích thước ảnh của PDFium là `ceil(cạnh × hệ số)`, nên hệ số được giảm tiếp cho tới khi tích hai cạnh đã làm
    tròn lên không vượt `max_pixels`.
    """
    if width_pt <= 0 or height_pt <= 0:
        raise ValueError("khổ trang phải dương")
    scale = min(dpi / POINTS_PER_INCH, math.sqrt(max_pixels / (width_pt * height_pt)))
    while math.ceil(width_pt * scale) * math.ceil(height_pt * scale) > max_pixels:
        scale *= 0.995
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
