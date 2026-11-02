"""Đối tượng trang PDF và các phép đo khử nhiễu GĐ0 (spec §5.7 "Khử nhiễu trang"; ADR-008 mục 6, 9).

Hàm thuần, không gọi PDFium: `pdfium_adapter` duyệt đối tượng (kể cả trong form XObject) thành `PageObject` có hộp
thật trong toạ độ trang và thứ tự vẽ, rồi dùng các hàm ở đây để quyết định nền, chữ ẩn, chữ bị che, độ phủ và dHash.
Mọi ngưỡng lấy từ `PdfReaderConfig`; không có chuỗi theo môn hay ngôn ngữ nào (CLAUDE.md §1). Không hàm nào đổi chữ
của lớp chữ (I3): các hàm chỉ trả quyết định giữ, loại hay gắn cờ cho từng đối tượng chữ.
"""

from __future__ import annotations

import itertools
import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

from .config import PdfReaderConfig
from .protocol import Rgba

PdfBox = tuple[float, float, float, float]  # (left, bottom, right, top), toạ độ PDF gốc dưới-trái
Matrix = tuple[float, float, float, float, float, float]  # (a, b, c, d, e, f) của PDF
Rgb = tuple[int, int, int]
ObjectKind = Literal["text", "image", "path", "form", "other"]

IDENTITY: Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
# Chế độ vẽ chữ không vẽ gì lên trang: 3 (ẩn) và 7 (chỉ thêm vào vùng cắt). Chữ ở các chế độ này không bao giờ bị
# loại vì chữ ẩn và không xét tương phản (0B(1)); trang có đa số ký tự ở chế độ này trên ảnh phủ kín là lớp OCR.
INVISIBLE_RENDER_MODES = frozenset({3, 7})
# Chế độ vẽ chữ hợp lệ của PDF là 0–7; PDFium trả -1 khi không xác định (chữ đó không được xét chữ ẩn hay bị che).
MAX_RENDER_MODE = 7
# Chế độ chỉ viền (không tô): màu chữ là màu viền.
STROKE_ONLY_RENDER_MODES = frozenset({1, 5})
OPAQUE_ALPHA = 255
# Số điểm ảnh tối đa lấy mẫu trong hộp của một đối tượng chữ khi tìm màu nền (lấy cách đều), để trang nhiều đối tượng
# vẫn đọc xong trong hạn của tầng 1.
MAX_BACKGROUND_SAMPLES = 256
# Lưới chia trang khi tìm đối tượng che chữ (chỉ để tăng tốc; kết quả không đổi theo lưới).
OCCLUSION_GRID = 32
# Số hộp tối đa khi tính diện tích hợp phần bị che; vượt trần dùng tổng diện tích (cận trên, nghiêng về gắn cờ).
MAX_OCCLUDER_BOXES = 256
DHASH_WIDTH = 9
DHASH_HEIGHT = 8
DHASH_CELL_SAMPLES = 64


@dataclass(frozen=True)
class PageObject:
    """Một đối tượng trang sau khi duyệt (0A): `z` là thứ tự vẽ theo duyệt sâu (nội dung form ở đúng chỗ của form),
    `box` là hộp sau mọi ma trận form, trong toạ độ trang (chưa cắt theo trang).

    Đường vẽ: `fill`/`stroke` là màu RGBA khi có tô/viền (None khi không), `segments` là số đoạn. Chữ: `render_mode`
    và `color` (màu tô, hoặc màu viền khi chỉ viền; None khi không đọc được).
    """

    kind: ObjectKind
    z: int
    box: PdfBox
    fill: Rgba | None = None
    stroke: Rgba | None = None
    segments: int = 0
    render_mode: int = 0
    color: Rgba | None = None

    @property
    def solid_fill(self) -> bool:
        """Đường vẽ tô đặc: có chế độ tô và alpha của màu tô bằng 255 (0A, 0B(2))."""
        return self.kind == "path" and self.fill is not None and self.fill[3] == OPAQUE_ALPHA


@dataclass(frozen=True)
class RgbImage:
    """Ảnh màu đã render (3 byte mỗi điểm ảnh, thứ tự R, G, B, không đệm)."""

    pixels: bytes
    width: int
    height: int

    def __post_init__(self) -> None:
        if self.width < 1 or self.height < 1 or len(self.pixels) != 3 * self.width * self.height:
            raise ValueError("kích thước ảnh không khớp số byte")


# --------------------------------------------------------------------------- #
# Ma trận và hộp
# --------------------------------------------------------------------------- #


def compose(outer: Matrix, inner: Matrix) -> Matrix:
    """Ma trận áp `inner` trước rồi `outer` (điểm trong không gian con của form ra không gian cha)."""
    a1, b1, c1, d1, e1, f1 = inner
    a2, b2, c2, d2, e2, f2 = outer
    return (
        a1 * a2 + b1 * c2,
        a1 * b2 + b1 * d2,
        c1 * a2 + d1 * c2,
        c1 * b2 + d1 * d2,
        e1 * a2 + f1 * c2 + e2,
        e1 * b2 + f1 * d2 + f2,
    )


def transform_box(box: PdfBox, matrix: Matrix) -> PdfBox:
    """Hộp bao của bốn góc `box` sau khi áp `matrix`."""
    a, b, c, d, e, f = matrix
    left, bottom, right, top = box
    xs: list[float] = []
    ys: list[float] = []
    for x, y in ((left, bottom), (left, top), (right, bottom), (right, top)):
        xs.append(a * x + c * y + e)
        ys.append(b * x + d * y + f)
    return (min(xs), min(ys), max(xs), max(ys))


def box_area(box: PdfBox) -> float:
    return max(box[2] - box[0], 0.0) * max(box[3] - box[1], 0.0)


def intersect(first: PdfBox, second: PdfBox) -> PdfBox | None:
    box = (max(first[0], second[0]), max(first[1], second[1]), min(first[2], second[2]), min(first[3], second[3]))
    return box if box[2] > box[0] and box[3] > box[1] else None


def union_box(boxes: Iterable[PdfBox]) -> PdfBox | None:
    items = list(boxes)
    if not items:
        return None
    return (min(b[0] for b in items), min(b[1] for b in items), max(b[2] for b in items), max(b[3] for b in items))


def _covered_length(spans: list[tuple[float, float]]) -> float:
    covered = 0.0
    current_low, current_high = spans[0]
    for low, high in spans[1:]:
        if low > current_high:
            covered += current_high - current_low
            current_low, current_high = low, high
        else:
            current_high = max(current_high, high)
    return covered + current_high - current_low


def covered_fraction(target: PdfBox, boxes: Sequence[PdfBox], max_boxes: int = MAX_OCCLUDER_BOXES) -> float:
    """Tỉ lệ diện tích `target` nằm trong hợp các `boxes`, ∈ [0, 1]; quá `max_boxes` hộp ⇒ tổng diện tích (cận trên)."""
    area = box_area(target)
    if area <= 0.0:
        return 0.0
    clipped = [box for box in (intersect(target, other) for other in boxes) if box is not None]
    if not clipped:
        return 0.0
    if len(clipped) > max_boxes:
        return min(1.0, sum(box_area(box) for box in clipped) / area)
    edges = sorted({x for box in clipped for x in (box[0], box[2])})
    total = 0.0
    for x_low, x_high in itertools.pairwise(edges):
        spans = sorted((box[1], box[3]) for box in clipped if box[0] <= x_low and box[2] >= x_high)
        if spans:
            total += (x_high - x_low) * _covered_length(spans)
    return min(1.0, total / area)


# --------------------------------------------------------------------------- #
# Nền, chữ bị che (0A, 0B(2))
# --------------------------------------------------------------------------- #


def background_paths(
    paths: Sequence[PageObject], text_boxes: Sequence[tuple[int, PdfBox]], page_box: PdfBox, config: PdfReaderConfig
) -> frozenset[int]:
    """`z` của các đường vẽ là nền (0A (i)): tô đặc, phủ `≥ background_path_min_area` diện tích trang (sau khi cắt theo
    trang) VÀ vẽ trước (z nhỏ hơn) mọi đối tượng chữ có hộp giao với nó. `text_boxes` là (z, hộp) của đối tượng chữ.
    Ảnh không bao giờ là nền ở đây: nền ảnh chỉ là ảnh template (nơi gọi xử lý)."""
    page_area = max(box_area(page_box), 1e-9)
    found: set[int] = set()
    for path in paths:
        if not path.solid_fill:
            continue
        clipped = intersect(path.box, page_box)
        if clipped is None or box_area(clipped) / page_area < config.background_path_min_area:
            continue
        if all(text_z > path.z for text_z, box in text_boxes if intersect(box, path.box) is not None):
            found.add(path.z)
    return frozenset(found)


class OcclusionIndex:
    """Tra nhanh các đối tượng (ảnh, đường vẽ tô đặc) có thể che một hộp chữ, theo lưới `OCCLUSION_GRID` × lưới."""

    def __init__(self, occluders: Sequence[PageObject], page_box: PdfBox) -> None:
        self._page_box = page_box
        self._cells: dict[tuple[int, int], list[PageObject]] = {}
        for occluder in occluders:
            for cell in self._cells_of(occluder.box):
                self._cells.setdefault(cell, []).append(occluder)

    def _cells_of(self, box: PdfBox) -> Iterable[tuple[int, int]]:
        left, bottom, right, top = self._page_box
        width = max(right - left, 1e-9)
        height = max(top - bottom, 1e-9)

        def index(value: float, start: float, size: float) -> int:
            return min(OCCLUSION_GRID - 1, max(0, int((value - start) / size * OCCLUSION_GRID)))

        x0, x1 = index(box[0], left, width), index(box[2], left, width)
        y0, y1 = index(box[1], bottom, height), index(box[3], bottom, height)
        return itertools.product(range(x0, x1 + 1), range(y0, y1 + 1))

    def covered(self, box: PdfBox, z: int) -> float:
        """Tỉ lệ hộp `box` bị các đối tượng có thứ tự vẽ sau `z` che."""
        seen: dict[int, PdfBox] = {}
        for cell in self._cells_of(box):
            for occluder in self._cells.get(cell, ()):
                if occluder.z > z:
                    seen[occluder.z] = occluder.box
        return covered_fraction(box, list(seen.values())) if seen else 0.0


# --------------------------------------------------------------------------- #
# Tương phản (0B(1))
# --------------------------------------------------------------------------- #


def _linear(channel: int) -> float:
    value = channel / 255.0
    return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4


def relative_luminance(color: Rgb) -> float:
    """Độ chói tương đối sRGB (WCAG 2.x)."""
    red, green, blue = color
    return 0.2126 * _linear(red) + 0.7152 * _linear(green) + 0.0722 * _linear(blue)


def contrast_ratio(first: Rgb, second: Rgb) -> float:
    """`(L_sáng + 0.05) / (L_tối + 0.05)` ∈ [1, 21] (WCAG 2.x)."""
    light, dark = sorted((relative_luminance(first), relative_luminance(second)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


PixelBox = tuple[int, int, int, int]  # (left, top, right, bottom) theo điểm ảnh, right/bottom không tính


def pixel_box(unit_box: tuple[float, float, float, float], width: int, height: int) -> PixelBox | None:
    """Hộp chuẩn hoá [0, 1] (gốc trên-trái) thành hộp điểm ảnh, ít nhất một điểm ảnh; hộp rỗng ⇒ None."""
    x0, y0, x1, y1 = unit_box
    left = min(width - 1, max(0, int(x0 * width)))
    top = min(height - 1, max(0, int(y0 * height)))
    right = min(width, max(left + 1, int(x1 * width + 0.999999)))
    bottom = min(height, max(top + 1, int(y1 * height + 0.999999)))
    if x1 < x0 or y1 < y0:
        return None
    return (left, top, right, bottom)


def median_color(image: RgbImage, box: PixelBox, max_samples: int = MAX_BACKGROUND_SAMPLES) -> Rgb:
    """Màu trung vị (từng kênh) của các điểm ảnh trong `box`, lấy mẫu cách đều tối đa `max_samples` điểm."""
    left, top, right, bottom = box
    count = (right - left) * (bottom - top)
    step = max(1, int((count / max_samples) ** 0.5))
    reds: list[int] = []
    greens: list[int] = []
    blues: list[int] = []
    pixels, width = image.pixels, image.width
    for y in range(top, bottom, step):
        row = y * width
        for x in range(left, right, step):
            offset = 3 * (row + x)
            reds.append(pixels[offset])
            greens.append(pixels[offset + 1])
            blues.append(pixels[offset + 2])
    return (int(statistics.median(reds)), int(statistics.median(greens)), int(statistics.median(blues)))


ContrastVerdict = Literal["drop", "flag", "keep"]


def contrast_verdict(contrast: float, config: PdfReaderConfig) -> ContrastVerdict:
    """`≤ hidden_text_drop_max` ⇒ loại; `≥ hidden_text_keep_min` ⇒ giữ; ở giữa ⇒ giữ + cờ (0B(1))."""
    if contrast <= config.hidden_text_drop_max:
        return "drop"
    if contrast >= config.hidden_text_keep_min:
        return "keep"
    return "flag"


def is_scan_text_layer(
    invisible_chars: int, total_chars: int, image_area_ratio: float, config: PdfReaderConfig
) -> bool:
    """Lớp OCR của PDF scan (0B(1)): tỉ lệ ký tự chế độ ẩn `> scan_text_layer_mode3_min` VÀ mọi ảnh phủ
    `≥ scan_text_layer_image_min`."""
    if total_chars <= 0:
        return False
    return (
        invisible_chars / total_chars > config.scan_text_layer_mode3_min
        and image_area_ratio >= config.scan_text_layer_image_min
    )


# --------------------------------------------------------------------------- #
# dHash (0D)
# --------------------------------------------------------------------------- #


def dhash(image: RgbImage, box: PixelBox) -> str | None:
    """dHash 64 bit (hex) của vùng `box`: ảnh xám thu về 9×8 bằng trung bình theo ô, bit = ô trái sáng hơn ô phải."""
    left, top, right, bottom = box
    if right <= left or bottom <= top:
        return None
    cells: list[list[float]] = []
    for row in range(DHASH_HEIGHT):
        y0 = top + (bottom - top) * row // DHASH_HEIGHT
        y1 = max(y0 + 1, top + (bottom - top) * (row + 1) // DHASH_HEIGHT)
        values: list[float] = []
        for column in range(DHASH_WIDTH):
            x0 = left + (right - left) * column // DHASH_WIDTH
            x1 = max(x0 + 1, left + (right - left) * (column + 1) // DHASH_WIDTH)
            values.append(_mean_gray(image, (x0, y0, min(x1, right), min(y1, bottom))))
        cells.append(values)
    bits = 0
    for values in cells:
        for column in range(DHASH_WIDTH - 1):
            bits = (bits << 1) | (1 if values[column] > values[column + 1] else 0)
    return f"{bits:016x}"


def _mean_gray(image: RgbImage, box: PixelBox) -> float:
    """Độ xám trung bình của ô, lấy mẫu cách đều tối đa khoảng `DHASH_CELL_SAMPLES` điểm ảnh."""
    left, top, right, bottom = box
    right, bottom = max(right, left + 1), max(bottom, top + 1)
    step = max(1, int(((right - left) * (bottom - top) / DHASH_CELL_SAMPLES) ** 0.5))
    total = 0
    count = 0
    pixels, width = image.pixels, image.width
    for y in range(top, bottom, step):
        row = y * width
        for x in range(left, right, step):
            offset = 3 * (row + x)
            total += 299 * pixels[offset] + 587 * pixels[offset + 1] + 114 * pixels[offset + 2]
            count += 1
    return total / (1000 * max(count, 1))


def hamming(first: str, second: str) -> int:
    """Khoảng cách Hamming giữa hai dHash hex."""
    return (int(first, 16) ^ int(second, 16)).bit_count()
