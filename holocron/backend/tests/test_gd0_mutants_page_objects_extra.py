"""Bổ sung cho `test_gd0_mutants_page_objects.py`: chỉ số hộp, hợp khoảng, lưới tra cứu che chữ so với tính trực tiếp,
dHash và màu trung vị so với bản tham chiếu viết lại độc lập, chế độ vẽ chữ qua PDF tổng hợp. Giết đột biến sống của
báo cáo 857e035. Không mạng, không eval_data."""

from __future__ import annotations

import math
import random
import statistics

import pytest

from holocron_core.infrastructure.document_readers.config import PdfReaderConfig
from holocron_core.infrastructure.document_readers.page_objects import (
    OcclusionIndex,
    PageObject,
    RgbImage,
    covered_fraction,
    dhash,
    intersect,
    is_scan_text_layer,
    median_color,
    union_box,
)
from holocron_core.infrastructure.document_readers.pdfium_adapter import PdfiumBackend
from holocron_core.infrastructure.document_readers.protocol import RawPdfPage
from tests.denoise_samples import SyntheticPage, build, image, rect, solid, text

PdfBox = tuple[float, float, float, float]
PixelBox = tuple[int, int, int, int]
CONFIG = PdfReaderConfig()
RED = (0.8, 0.0, 0.0)


def test_intersect_checks_the_x_and_y_extent_separately() -> None:
    # x rỗng nhưng mép trên lớn: chỉ độ rộng mới quyết định.
    assert intersect((10.0, 0.0, 12.0, 100.0), (0.0, 0.0, 5.0, 100.0)) is None
    # y rỗng nhưng mép phải lớn.
    assert intersect((0.0, 10.0, 100.0, 12.0), (0.0, 0.0, 100.0, 5.0)) is None
    assert intersect((1.0, 2.0, 30.0, 40.0), (5.0, 6.0, 20.0, 50.0)) == (5.0, 6.0, 20.0, 40.0)
    assert intersect((5.0, 6.0, 20.0, 50.0), (1.0, 2.0, 30.0, 40.0)) == (5.0, 6.0, 20.0, 40.0)


def test_union_box_takes_the_extreme_of_each_coordinate() -> None:
    assert union_box([(1.0, 10.0, 20.0, 30.0), (5.0, 2.0, 8.0, 40.0)]) == (1.0, 2.0, 20.0, 40.0)
    assert union_box([(5.0, 2.0, 8.0, 40.0)]) == (5.0, 2.0, 8.0, 40.0)
    assert union_box([(0.0, 0.0, 1.0, 1.0), (9.0, 9.0, 10.0, 10.0), (4.0, -3.0, 5.0, 12.0)]) == (0.0, -3.0, 10.0, 12.0)


def test_covered_fraction_sums_disjoint_spans_with_a_non_zero_origin() -> None:
    target = (0.0, 0.0, 10.0, 10.0)
    assert covered_fraction(target, [(0.0, 1.0, 10.0, 3.0), (0.0, 5.0, 10.0, 9.0)]) == pytest.approx(0.6)
    assert covered_fraction(target, [(0.0, 5.0, 10.0, 9.0), (0.0, 1.0, 10.0, 3.0)]) == pytest.approx(0.6)
    three = [(0.0, 1.0, 10.0, 3.0), (0.0, 2.0, 10.0, 4.0), (0.0, 6.0, 10.0, 7.0)]
    assert covered_fraction(target, three) == pytest.approx(0.4)


def test_covered_fraction_of_a_tiny_target_is_not_treated_as_empty() -> None:
    tiny = (0.0, 0.0, 0.05, 0.05)
    assert covered_fraction(tiny, [(0.0, 0.0, 1.0, 1.0)]) == pytest.approx(1.0)
    assert covered_fraction(tiny, [(0.0, 0.0, 0.025, 0.05)]) == pytest.approx(0.5)


def test_covered_fraction_switches_to_the_capped_sum_above_the_default_box_limit() -> None:
    target = (0.0, 0.0, 10.0, 10.0)
    box = (0.0, 0.0, 1.0, 1.0)
    assert covered_fraction(target, [box] * 256) == pytest.approx(0.01)
    assert covered_fraction(target, [box] * 257) == 1.0


def test_page_object_defaults_and_scan_layer_edge_cases() -> None:
    plain = PageObject(kind="image", z=1, box=(0.0, 0.0, 1.0, 1.0))
    assert (plain.segments, plain.render_mode, plain.fill, plain.stroke, plain.color) == (0, 0, None, None, None)
    assert is_scan_text_layer(1, 1, 1.0, CONFIG) is True


def _random_box(rng: random.Random, area: PdfBox, overhang: float) -> PdfBox:
    left, bottom, right, top = area
    width, height = right - left, top - bottom
    x0 = left + rng.uniform(-overhang, 1 + overhang) * width
    y0 = bottom + rng.uniform(-overhang, 1 + overhang) * height
    return (x0, y0, x0 + rng.uniform(0.02, 0.6) * width, y0 + rng.uniform(0.02, 0.6) * height)


@pytest.mark.parametrize("page_box", [(0.0, 0.0, 612.0, 792.0), (10.0, 20.0, 210.0, 120.0), (-50.0, -30.0, 50.0, 70.0)])
def test_occlusion_index_matches_a_direct_computation(page_box: PdfBox) -> None:
    """Lưới chỉ để tăng tốc: kết quả phải bằng tính trực tiếp với mọi hộp, kể cả hộp tràn ra ngoài trang."""
    rng = random.Random(20261031)
    for _ in range(150):
        zs = rng.sample(range(1, 40), rng.randint(1, 8))
        occluders = [PageObject(kind="image", z=z, box=_random_box(rng, page_box, 0.3)) for z in zs]
        target = _random_box(rng, page_box, 0.0)
        z = rng.randint(0, 40)
        index = OcclusionIndex(occluders, page_box)
        expected = covered_fraction(target, [o.box for o in occluders if o.z > z])
        assert index.covered(target, z) == pytest.approx(expected), (target, z)


def test_occlusion_index_finds_occluders_in_the_first_and_last_cells() -> None:
    page = (0.0, 0.0, 320.0, 320.0)
    first_cell = OcclusionIndex([PageObject(kind="image", z=3, box=(0.0, 0.0, 9.0, 9.0))], page)
    assert first_cell.covered((0.0, 0.0, 9.0, 9.0), 0) == pytest.approx(1.0)
    last_cell = OcclusionIndex([PageObject(kind="image", z=3, box=(311.0, 311.0, 320.0, 320.0))], page)
    assert last_cell.covered((311.0, 311.0, 320.0, 320.0), 0) == pytest.approx(1.0)
    assert last_cell.covered((0.0, 0.0, 9.0, 9.0), 0) == 0.0


# --------------------------------------------------------------------------- #
# dHash và màu trung vị: so với bản tham chiếu viết lại độc lập
# --------------------------------------------------------------------------- #

PATTERN = [[(37 * (r + 1) * (c + 3) + 11 * c * c + r) % 251 for c in range(9)] for r in range(8)]


def _expected_bits(values: list[list[int]]) -> str:
    bits = 0
    for row in values:
        for column in range(8):
            bits = (bits << 1) | (1 if row[column] > row[column + 1] else 0)
    return f"{bits:016x}"


def _scaled(values: list[list[int]], scale: int, *, offset: tuple[int, int] = (0, 0), pad: int = 0) -> RgbImage:
    width, height = 9 * scale + offset[0] + pad, 8 * scale + offset[1] + pad
    data = bytearray()
    for y in range(height):
        for x in range(width):
            cx, cy = x - offset[0], y - offset[1]
            inside = 0 <= cx < 9 * scale and 0 <= cy < 8 * scale
            gray = values[cy // scale][cx // scale] if inside else (x * 53 + y * 29) % 256
            data.extend((gray, gray, gray))
    return RgbImage(bytes(data), width, height)


@pytest.mark.parametrize("scale", [3, 4, 7, 8, 12, 25])
def test_dhash_is_resolution_independent_for_flat_cells(scale: int) -> None:
    picture = _scaled(PATTERN, scale)
    assert dhash(picture, (0, 0, 9 * scale, 8 * scale)) == _expected_bits(PATTERN)


@pytest.mark.parametrize("scale", [2, 9, 16])
def test_dhash_of_a_region_inside_a_larger_image(scale: int) -> None:
    offset = (7, 5)
    picture = _scaled(PATTERN, scale, offset=offset, pad=11)
    region = (offset[0], offset[1], offset[0] + 9 * scale, offset[1] + 8 * scale)
    assert dhash(picture, region) == _expected_bits(PATTERN)


@pytest.mark.parametrize("size", [(13, 11), (10, 10), (17, 9), (9, 8), (40, 33)])
def test_dhash_of_a_flat_image_is_zero_whatever_the_cell_sizes(size: tuple[int, int]) -> None:
    flat = RgbImage(bytes([90, 140, 40]) * (size[0] * size[1]), *size)
    assert dhash(flat, (0, 0, *size)) == "0" * 16


def test_dhash_of_a_single_pixel_box_has_all_cells_equal() -> None:
    gradient = RgbImage(bytes(250 - 40 * x for _ in range(4) for x in range(5) for _ in range(3)), 5, 4)
    assert dhash(gradient, (2, 1, 3, 2)) == "0" * 16
    full = dhash(gradient, (0, 0, 5, 4))
    assert full is not None and len(full) == 16


def _gray(picture: RgbImage, x: int, y: int) -> int:
    offset = 3 * (y * picture.width + x)
    return 299 * picture.pixels[offset] + 587 * picture.pixels[offset + 1] + 114 * picture.pixels[offset + 2]


def _reference_mean(picture: RgbImage, box: PixelBox) -> float:
    left, top, right, bottom = box
    right, bottom = max(right, left + 1), max(bottom, top + 1)
    step = max(1, int(math.sqrt((right - left) * (bottom - top) / 64)))
    values = [_gray(picture, x, y) for y in range(top, bottom, step) for x in range(left, right, step)]
    return sum(values) / (1000 * len(values))


def _reference_dhash(picture: RgbImage, box: PixelBox) -> str:
    left, top, right, bottom = box
    bits = 0
    for row in range(8):
        y0 = top + (bottom - top) * row // 8
        y1 = max(y0 + 1, top + (bottom - top) * (row + 1) // 8)
        cells = []
        for column in range(9):
            x0 = left + (right - left) * column // 9
            x1 = max(x0 + 1, left + (right - left) * (column + 1) // 9)
            cells.append(_reference_mean(picture, (x0, y0, min(x1, right), min(y1, bottom))))
        for column in range(8):
            bits = (bits << 1) | (1 if cells[column] > cells[column + 1] else 0)
    return f"{bits:016x}"


def _random_image(rng: random.Random, width: int, height: int) -> RgbImage:
    return RgbImage(bytes(rng.randrange(256) for _ in range(3 * width * height)), width, height)


def _random_pixel_box(rng: random.Random, width: int, height: int) -> PixelBox:
    left, top = rng.randint(0, width - 2), rng.randint(0, height - 2)
    return (left, top, rng.randint(left + 1, width), rng.randint(top + 1, height))


@pytest.mark.parametrize("seed", range(6))
def test_dhash_matches_an_independent_reference_on_random_images(seed: int) -> None:
    rng = random.Random(seed)
    for _ in range(12):
        width, height = rng.randint(3, 110), rng.randint(3, 100)
        picture = _random_image(rng, width, height)
        box = _random_pixel_box(rng, width, height)
        assert dhash(picture, box) == _reference_dhash(picture, box), (width, height, box)


@pytest.mark.parametrize("max_samples", [4, 9, 64, 256, 1000])
def test_median_color_samples_like_the_reference(max_samples: int) -> None:
    rng = random.Random(max_samples)
    for _ in range(10):
        width, height = rng.randint(3, 70), rng.randint(3, 70)
        picture = _random_image(rng, width, height)
        box = _random_pixel_box(rng, width, height)
        step = max(1, int(math.sqrt((box[2] - box[0]) * (box[3] - box[1]) / max_samples)))
        points = [(x, y) for y in range(box[1], box[3], step) for x in range(box[0], box[2], step)]
        expected = tuple(
            int(statistics.median(picture.pixels[3 * (y * width + x) + channel] for x, y in points))
            for channel in range(3)
        )
        assert median_color(picture, box, max_samples=max_samples) == expected, (width, height, box)


def test_median_color_default_sample_budget_is_256() -> None:
    def stripes(width: int, height: int) -> RgbImage:
        return RgbImage(
            bytes((255 if x % 2 else 0, 0, 0)[c] for _ in range(height) for x in range(width) for c in range(3)),
            width,
            height,
        )

    # Hộp 32×32 = 4 × 256 điểm: bước 2 (chỉ cột chẵn) với ngân sách 256; bước 1 nếu ngân sách là 257.
    assert median_color(stripes(32, 32), (0, 0, 32, 32)) == (0, 0, 0)
    # Hộp 30×34 = 4 × 255 điểm: bước 1 với ngân sách 256; bước 2 nếu ngân sách là 255.
    assert median_color(stripes(30, 34), (0, 0, 30, 34)) == (127, 0, 0)


def test_dhash_default_cell_sample_budget_is_64() -> None:
    rng = random.Random(64)
    # Ô 16×16 = 4 × 64 điểm (bước 2); ô 18×14 = 252 = 4 × 63 điểm (bước 1): bản tham chiếu dùng ngân sách 64.
    for width, height in ((9 * 16, 8 * 16), (9 * 18, 8 * 14), (9 * 14, 8 * 18)):
        picture = _random_image(rng, width, height)
        assert dhash(picture, (0, 0, width, height)) == _reference_dhash(picture, (0, 0, width, height))


# Cặp màu (trái, phải) mà thứ tự độ xám 299/587/114 đổi khi MỘT hệ số lệch 1 đơn vị (tìm sẵn, cố định).
NEAR_TIES = [
    ((173, 82, 213), (11, 184, 113)),
    ((123, 183, 129), (12, 239, 131)),
    ((136, 241, 83), (91, 240, 207)),
    ((24, 98, 53), (172, 25, 41)),
]


@pytest.mark.parametrize(("left", "right"), NEAR_TIES)
def test_gray_weights_are_exactly_299_587_114(left: tuple[int, int, int], right: tuple[int, int, int]) -> None:
    flat = bytes(value for _ in range(8) for column in range(9) for value in (left if column % 2 == 0 else right))
    weights = (299, 587, 114)
    base = sum(w * (a - b) for w, a, b in zip(weights, left, right, strict=True))
    assert base != 0
    assert dhash(RgbImage(flat, 9, 8), (0, 0, 9, 8)) == ("aa" * 8 if base > 0 else "55" * 8)


# --------------------------------------------------------------------------- #
# Chế độ vẽ chữ (`Tr`) qua PDF tổng hợp
# --------------------------------------------------------------------------- #


def _read(page: SyntheticPage) -> RawPdfPage:
    opened = PdfiumBackend.load().open(build([page]))
    assert not isinstance(opened, str)
    with opened as document:
        return document.read_page(0, None, CONFIG)


def _two_colour_text(mode: int, fill: tuple[float, float, float], stroke: tuple[float, float, float]) -> bytes:
    return b"BT /F1 12 Tf %.2f %.2f %.2f rg %.2f %.2f %.2f RG %d Tr 50 600 Td (HiddenWords) Tj ET" % (
        *fill,
        *stroke,
        mode,
    )


@pytest.mark.parametrize("mode", [0, 1, 2, 4, 5, 6])
def test_stroke_only_modes_use_the_stroke_colour_for_the_hidden_text_test(mode: int) -> None:
    """Chữ tô trùng màu nền bị loại; chế độ chỉ viền (1, 5) lấy màu viền (trắng, nhìn thấy), nên giữ."""
    page = SyntheticPage(
        ops=[
            rect(0, 0, 612, 792, rgb=RED),
            text(50, 700, "Visible words"),
            _two_colour_text(mode, RED, (1.0, 1.0, 1.0)),
        ]
    )
    raw = _read(page)
    texts = [line.text for line in raw.lines]
    if mode in (1, 5):
        assert texts == ["Visible words", "HiddenWords"] and raw.hidden_chars_dropped == 0
    else:
        assert texts == ["Visible words"] and raw.hidden_chars_dropped == len("HiddenWords")


@pytest.mark.parametrize("mode", [3, 7])
def test_invisible_modes_are_never_dropped_as_hidden_text(mode: int) -> None:
    page = SyntheticPage(
        ops=[rect(0, 0, 612, 792, rgb=RED), text(50, 700, "Visible words"), _two_colour_text(mode, RED, RED)]
    )
    raw = _read(page)
    assert [line.text for line in raw.lines] == ["Visible words", "HiddenWords"] and raw.hidden_chars_dropped == 0


@pytest.mark.parametrize("mode", range(8))
def test_only_modes_3_and_7_make_a_full_page_image_with_text_a_scan_layer(mode: int) -> None:
    ops = [image("S", 0, 0, 612, 792), *(text(50, 700 - 30 * i, f"Scanned line {i}", mode=mode) for i in range(3))]
    raw = _read(SyntheticPage(ops=ops, images={"S": solid(255, 8, 8)}))
    assert raw.scan_text_layer is (mode in (3, 7))
