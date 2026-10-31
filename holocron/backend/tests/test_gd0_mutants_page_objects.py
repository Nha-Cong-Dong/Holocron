"""Test đơn vị cho `infrastructure/document_readers/page_objects.py` (GĐ0 0A, 0B): hàm thuần, biên ngưỡng đúng bằng
giá trị cấu hình. Giết đột biến sống của báo cáo 857e035; không PDF thật, không mạng, không eval_data."""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence

import pytest

from holocron_core.infrastructure.document_readers.config import PdfReaderConfig
from holocron_core.infrastructure.document_readers.page_objects import (
    OPAQUE_ALPHA,
    OcclusionIndex,
    PageObject,
    RgbImage,
    background_paths,
    box_area,
    compose,
    contrast_ratio,
    contrast_verdict,
    covered_fraction,
    dhash,
    hamming,
    intersect,
    is_scan_text_layer,
    median_color,
    pixel_box,
    relative_luminance,
    transform_box,
    union_box,
)

CONFIG = PdfReaderConfig()
PAGE = (0.0, 0.0, 100.0, 100.0)
Matrix = tuple[float, float, float, float, float, float]


# --------------------------------------------------------------------------- #
# Ma trận và hộp
# --------------------------------------------------------------------------- #


def _mul(first: Matrix, second: Matrix) -> Matrix:
    """Tích ma trận 3×3 theo quy ước hàng của PDF: điểm áp `first` rồi `second`."""
    a1, b1, c1, d1, e1, f1 = first
    a2, b2, c2, d2, e2, f2 = second
    return (
        a1 * a2 + b1 * c2,
        a1 * b2 + b1 * d2,
        c1 * a2 + d1 * c2,
        c1 * b2 + d1 * d2,
        e1 * a2 + f1 * c2 + e2,
        e1 * b2 + f1 * d2 + f2,
    )


def _apply(matrix: Matrix, point: tuple[float, float]) -> tuple[float, float]:
    a, b, c, d, e, f = matrix
    x, y = point
    return (a * x + c * y + e, b * x + d * y + f)


def test_compose_applies_inner_first_then_outer_on_a_point() -> None:
    inner: Matrix = (2.0, 3.0, 5.0, 7.0, 11.0, 13.0)
    outer: Matrix = (1.5, -2.0, 3.0, 4.0, 5.0, 6.0)
    point = (3.0, -2.0)
    expected = _apply(outer, _apply(inner, point))
    assert _apply(compose(outer, inner), point) == pytest.approx(expected)
    assert compose(outer, inner) != compose(inner, outer)


def test_transform_box_is_the_bounding_box_of_the_four_corners() -> None:
    rotate: Matrix = (0.0, 1.0, -1.0, 0.0, 10.0, 20.0)
    assert transform_box((0.0, 0.0, 4.0, 2.0), rotate) == (8.0, 20.0, 10.0, 24.0)
    skew: Matrix = (1.0, 0.5, 0.25, 1.0, 0.0, 0.0)
    corners = [_apply(skew, (x, y)) for x in (1.0, 5.0) for y in (2.0, 4.0)]
    expected = (
        min(c[0] for c in corners),
        min(c[1] for c in corners),
        max(c[0] for c in corners),
        max(c[1] for c in corners),
    )
    assert transform_box((1.0, 2.0, 5.0, 4.0), skew) == pytest.approx(expected)


def test_box_area_intersect_and_union() -> None:
    assert box_area((1.0, 2.0, 4.0, 6.0)) == 12.0
    assert box_area((4.0, 2.0, 1.0, 6.0)) == 0.0
    assert box_area((1.0, 6.0, 4.0, 2.0)) == 0.0
    assert intersect((0.0, 0.0, 4.0, 4.0), (2.0, 1.0, 6.0, 3.0)) == (2.0, 1.0, 4.0, 3.0)
    assert intersect((0.0, 0.0, 4.0, 4.0), (4.0, 0.0, 6.0, 4.0)) is None, "chỉ chạm cạnh"
    assert intersect((0.0, 0.0, 4.0, 4.0), (0.0, 4.0, 4.0, 6.0)) is None
    assert intersect((0.0, 0.0, 4.0, 4.0), (5.0, 5.0, 6.0, 6.0)) is None
    assert union_box([]) is None
    assert union_box([(1.0, 2.0, 3.0, 4.0), (0.0, 5.0, 2.0, 9.0)]) == (0.0, 2.0, 3.0, 9.0)


def test_covered_fraction_is_union_area_over_target_area() -> None:
    target = (0.0, 0.0, 10.0, 10.0)
    assert covered_fraction(target, []) == 0.0
    assert covered_fraction(target, [(20.0, 20.0, 30.0, 30.0)]) == 0.0
    assert covered_fraction((0.0, 0.0, 0.0, 10.0), [target]) == 0.0
    assert covered_fraction(target, [(0.0, 0.0, 4.0, 10.0), (6.0, 0.0, 10.0, 10.0)]) == pytest.approx(0.8)
    assert covered_fraction(target, [(0.0, 0.0, 6.0, 10.0), (4.0, 0.0, 10.0, 10.0)]) == pytest.approx(1.0)
    assert covered_fraction(target, [(0.0, 0.0, 10.0, 3.0), (0.0, 2.0, 10.0, 5.0)]) == pytest.approx(0.5)
    assert covered_fraction(target, [(0.0, 0.0, 10.0, 2.0), (0.0, 4.0, 10.0, 6.0)]) == pytest.approx(0.4)
    assert covered_fraction(target, [(-5.0, -5.0, 5.0, 5.0)]) == pytest.approx(0.25), "cắt theo hộp đích"
    assert covered_fraction(target, [(0.0, 0.0, 5.0, 10.0), (0.0, 0.0, 10.0, 5.0)]) == pytest.approx(0.75)


def test_covered_fraction_over_the_box_limit_uses_the_capped_sum_of_areas() -> None:
    target = (0.0, 0.0, 10.0, 10.0)
    boxes = [(0.0, 0.0, 10.0, 1.0)] * 4
    assert covered_fraction(target, boxes, max_boxes=4) == pytest.approx(0.1), "đúng bằng trần: tính theo hợp"
    assert covered_fraction(target, boxes, max_boxes=3) == pytest.approx(0.4), "vượt trần: tổng diện tích"
    assert covered_fraction(target, [(0.0, 0.0, 10.0, 10.0)] * 3, max_boxes=2) == 1.0


# --------------------------------------------------------------------------- #
# Nền và chữ bị che
# --------------------------------------------------------------------------- #


def _path(
    z: int, box: tuple[float, float, float, float], *, alpha: int = OPAQUE_ALPHA, fill: bool = True
) -> PageObject:
    return PageObject(kind="path", z=z, box=box, fill=(10, 20, 30, alpha) if fill else None, segments=4)


def test_solid_fill_needs_a_path_with_an_opaque_fill() -> None:
    assert _path(0, PAGE).solid_fill is True
    assert _path(0, PAGE, alpha=OPAQUE_ALPHA - 1).solid_fill is False
    assert _path(0, PAGE, fill=False).solid_fill is False
    assert PageObject(kind="image", z=0, box=PAGE, fill=(1, 2, 3, OPAQUE_ALPHA)).solid_fill is False


def test_background_path_area_threshold_is_inclusive_and_uses_the_clipped_box() -> None:
    share = CONFIG.background_path_min_area
    exact = _path(0, (0.0, 0.0, 100.0, 100.0 * share))
    assert background_paths([exact], [], PAGE, CONFIG) == {0}
    below = _path(0, (0.0, 0.0, 100.0, 100.0 * share - 1.0))
    assert background_paths([below], [], PAGE, CONFIG) == frozenset()
    outside = _path(0, (200.0, 200.0, 300.0, 300.0))
    assert background_paths([outside], [], PAGE, CONFIG) == frozenset()


def test_background_path_is_clipped_to_the_page_before_measuring() -> None:
    share = CONFIG.background_path_min_area
    # Hộp lớn nhưng chỉ lộ ra một dải `share / 2` của trang: sau khi cắt theo trang, không đủ diện tích.
    strip = _path(0, (0.0, -1000.0, 100.0, 100.0 * share / 2))
    assert background_paths([strip], [], PAGE, CONFIG) == frozenset()
    covering = _path(0, (-10.0, -10.0, 110.0, 110.0))
    assert background_paths([covering], [], PAGE, CONFIG) == {0}


def test_background_path_must_be_drawn_before_every_intersecting_text_object() -> None:
    path = _path(5, PAGE)
    over = (10.0, 10.0, 20.0, 20.0)
    assert background_paths([path], [(6, over)], PAGE, CONFIG) == {5}
    assert background_paths([path], [(4, over)], PAGE, CONFIG) == frozenset(), "chữ vẽ trước đường vẽ"
    assert background_paths([path], [(6, over), (4, over)], PAGE, CONFIG) == frozenset()
    assert background_paths([path], [(4, (200.0, 200.0, 210.0, 210.0))], PAGE, CONFIG) == {5}, "chữ không giao"
    assert background_paths([path], [(5, over)], PAGE, CONFIG) == frozenset(), "cùng thứ tự vẽ không phải sau"


def test_background_ignores_paths_without_an_opaque_fill() -> None:
    assert background_paths([_path(0, PAGE, alpha=10)], [], PAGE, CONFIG) == frozenset()
    assert background_paths([_path(0, PAGE, fill=False)], [], PAGE, CONFIG) == frozenset()
    mixed = [_path(0, PAGE, fill=False), _path(1, PAGE)]
    assert background_paths(mixed, [], PAGE, CONFIG) == {1}


def _cover(z: int, box: tuple[float, float, float, float]) -> PageObject:
    return PageObject(kind="image", z=z, box=box)


def test_occlusion_index_counts_only_objects_drawn_after_the_text() -> None:
    cover = _cover(5, (0.0, 0.0, 50.0, 100.0))
    index = OcclusionIndex([cover], PAGE)
    target = (0.0, 0.0, 100.0, 10.0)
    assert index.covered(target, 4) == pytest.approx(0.5)
    assert index.covered(target, 5) == 0.0, "cùng thứ tự vẽ không che"
    assert index.covered(target, 6) == 0.0
    assert index.covered((60.0, 0.0, 90.0, 10.0), 0) == 0.0


def test_occlusion_index_unions_overlapping_occluders_across_grid_cells() -> None:
    index = OcclusionIndex([_cover(3, (0.0, 0.0, 30.0, 100.0)), _cover(4, (20.0, 0.0, 60.0, 100.0))], PAGE)
    assert index.covered((0.0, 40.0, 100.0, 50.0), 0) == pytest.approx(0.6)
    far_corner = OcclusionIndex([_cover(3, (99.0, 99.0, 100.0, 100.0))], PAGE)
    assert far_corner.covered((99.0, 99.0, 100.0, 100.0), 0) == pytest.approx(1.0)
    outside = OcclusionIndex([_cover(3, (150.0, 150.0, 200.0, 200.0))], PAGE)
    assert outside.covered((90.0, 90.0, 100.0, 100.0), 0) == 0.0
    empty = OcclusionIndex([], PAGE)
    assert empty.covered((0.0, 0.0, 100.0, 100.0), 0) == 0.0


def test_occlusion_index_ignores_occluders_that_only_share_a_grid_cell() -> None:
    index = OcclusionIndex([_cover(3, (0.0, 0.0, 1.0, 1.0))], PAGE)
    assert index.covered((2.0, 2.0, 3.0, 3.0), 0) == 0.0
    left = OcclusionIndex([_cover(3, (0.0, 0.0, 10.0, 10.0))], (0.0, 0.0, 200.0, 50.0))
    assert left.covered((0.0, 0.0, 10.0, 10.0), 0) == pytest.approx(1.0)
    assert left.covered((190.0, 40.0, 200.0, 50.0), 0) == 0.0


# --------------------------------------------------------------------------- #
# Tương phản
# --------------------------------------------------------------------------- #


def _linear(value: float) -> float:
    return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4


def test_relative_luminance_follows_wcag() -> None:
    assert relative_luminance((0, 0, 0)) == 0.0
    assert relative_luminance((255, 255, 255)) == pytest.approx(1.0)
    assert relative_luminance((255, 0, 0)) == pytest.approx(0.2126)
    assert relative_luminance((0, 255, 0)) == pytest.approx(0.7152)
    assert relative_luminance((0, 0, 255)) == pytest.approx(0.0722)
    for channel in (0, 5, 10, 11, 12, 13, 100, 200):
        value = _linear(channel / 255.0)
        assert relative_luminance((channel, channel, channel)) == pytest.approx(value)
    assert relative_luminance((10, 0, 0)) == pytest.approx(0.2126 * (10 / 255) / 12.92)


def test_contrast_ratio_is_symmetric_and_in_range() -> None:
    assert contrast_ratio((255, 255, 255), (0, 0, 0)) == pytest.approx(21.0)
    assert contrast_ratio((0, 0, 0), (255, 255, 255)) == pytest.approx(21.0)
    assert contrast_ratio((10, 20, 30), (10, 20, 30)) == pytest.approx(1.0)
    assert contrast_ratio((119, 119, 119), (255, 255, 255)) == pytest.approx(4.478, abs=0.005)
    assert contrast_ratio((255, 0, 0), (0, 0, 255)) == pytest.approx((0.2126 + 0.05) / (0.0722 + 0.05))


def test_contrast_verdict_thresholds_are_inclusive_at_both_ends() -> None:
    drop_max, keep_min = CONFIG.hidden_text_drop_max, CONFIG.hidden_text_keep_min
    assert contrast_verdict(1.0, CONFIG) == "drop"
    assert contrast_verdict(drop_max, CONFIG) == "drop"
    assert contrast_verdict(math.nextafter(drop_max, math.inf), CONFIG) == "flag"
    assert contrast_verdict((drop_max + keep_min) / 2, CONFIG) == "flag"
    assert contrast_verdict(math.nextafter(keep_min, 0.0), CONFIG) == "flag"
    assert contrast_verdict(keep_min, CONFIG) == "keep"
    assert contrast_verdict(21.0, CONFIG) == "keep"


# --------------------------------------------------------------------------- #
# Lớp OCR của PDF scan
# --------------------------------------------------------------------------- #


def test_scan_text_layer_thresholds() -> None:
    mode3, image = CONFIG.scan_text_layer_mode3_min, CONFIG.scan_text_layer_image_min
    total = 1000
    over = math.floor(mode3 * total) + 1
    assert is_scan_text_layer(over, total, image, CONFIG) is True
    assert is_scan_text_layer(over, total, image - 0.001, CONFIG) is False, "ảnh dưới ngưỡng"
    assert is_scan_text_layer(over, total, 1.0, CONFIG) is True
    at = round(mode3 * total)
    assert at / total == mode3, "điều kiện nền: đúng bằng ngưỡng"
    assert is_scan_text_layer(at, total, 1.0, CONFIG) is False, "tỉ lệ ký tự ẩn phải LỚN HƠN ngưỡng"
    assert is_scan_text_layer(total, total, 1.0, CONFIG) is True
    assert is_scan_text_layer(0, total, 1.0, CONFIG) is False
    assert is_scan_text_layer(0, 0, 1.0, CONFIG) is False
    assert is_scan_text_layer(5, 0, 1.0, CONFIG) is False
    assert is_scan_text_layer(5, -1, 1.0, CONFIG) is False


# --------------------------------------------------------------------------- #
# pixel_box, median_color
# --------------------------------------------------------------------------- #


def test_pixel_box_rounds_outwards_and_clamps() -> None:
    assert pixel_box((0.1, 0.2, 0.25, 0.5), 100, 200) == (10, 40, 25, 100)
    assert pixel_box((0.1, 0.2, 0.251, 0.5), 100, 200) == (10, 40, 26, 100)
    assert pixel_box((0.0, 0.0, 1.0, 1.0), 100, 200) == (0, 0, 100, 200)
    assert pixel_box((0.5, 0.5, 0.5, 0.5), 100, 100) == (50, 50, 51, 51), "hộp rỗng vẫn lấy một điểm ảnh"
    assert pixel_box((0.999, 0.999, 1.0, 1.0), 100, 100) == (99, 99, 100, 100)
    assert pixel_box((-0.5, -0.5, 0.011, 0.011), 100, 100) == (0, 0, 2, 2)
    assert pixel_box((-0.5, -0.5, 0.01, 0.01), 100, 100) == (0, 0, 1, 1)
    assert pixel_box((0.6, 0.1, 0.5, 0.4), 100, 100) is None
    assert pixel_box((0.1, 0.6, 0.4, 0.5), 100, 100) is None
    assert pixel_box((2.0, 2.0, 3.0, 3.0), 100, 100) == (99, 99, 100, 100)


def _image(width: int, height: int, pixel: Callable[[int, int], Sequence[int]]) -> RgbImage:
    data = bytearray()
    for y in range(height):
        for x in range(width):
            data.extend(pixel(x, y))
    return RgbImage(bytes(data), width, height)


def test_rgb_image_checks_the_byte_count() -> None:
    with pytest.raises(ValueError):
        RgbImage(b"\x00" * 5, 1, 1)
    with pytest.raises(ValueError):
        RgbImage(b"", 0, 1)
    with pytest.raises(ValueError):
        RgbImage(b"\x00" * 3, 1, 0)
    assert RgbImage(b"\x00" * 6, 2, 1).width == 2


def test_median_color_is_per_channel_over_the_box() -> None:
    colors = [(10, 200, 7), (20, 100, 70), (30, 50, 100), (40, 0, 9)]
    image = _image(4, 1, lambda x, y: colors[x])
    assert median_color(image, (0, 0, 4, 1)) == (25, 75, 39)
    assert median_color(image, (1, 0, 3, 1)) == (25, 75, 85)
    assert median_color(image, (2, 0, 3, 1)) == (30, 50, 100)
    assert median_color(_image(2, 2, lambda x, y: (x * 100, y * 100, 50)), (0, 0, 2, 2)) == (50, 50, 50)
    rows = _image(1, 3, lambda x, y: (y * 10, 0, 0))
    assert median_color(rows, (0, 1, 1, 3)) == (15, 0, 0)


def test_median_color_samples_evenly_when_the_box_is_larger_than_the_budget() -> None:
    image = _image(32, 32, lambda x, y: (x if x < 16 else 255, 0, 0))
    full = median_color(image, (0, 0, 32, 32), max_samples=32 * 32)
    sampled = median_color(image, (0, 0, 32, 32), max_samples=4)
    assert full == (135, 0, 0)
    # Lấy mẫu cách đều bước 16: chỉ thấy cột 0 và 16 ⇒ trung vị của {0, 255}.
    assert sampled == (127, 0, 0)
    assert median_color(image, (0, 0, 8, 8), max_samples=4) == (2, 0, 0), "bước 4: cột 0 và 4"


# --------------------------------------------------------------------------- #
# dHash, Hamming
# --------------------------------------------------------------------------- #


def _cells(values: list[list[int]], scale: int = 1) -> RgbImage:
    """Ảnh xám 9×8 ô, mỗi ô `scale`×`scale` điểm ảnh."""
    return _image(9 * scale, 8 * scale, lambda x, y: (values[y // scale][x // scale],) * 3)


def _expected_bits(values: list[list[int]]) -> str:
    bits = 0
    for row in values:
        for column in range(8):
            bits = (bits << 1) | (1 if row[column] > row[column + 1] else 0)
    return f"{bits:016x}"


PATTERN = [[(37 * (r + 1) * (c + 3) + 11 * c * c + r) % 251 for c in range(9)] for r in range(8)]


@pytest.mark.parametrize("scale", [1, 2, 5])
def test_dhash_compares_each_cell_with_the_cell_on_its_right(scale: int) -> None:
    image = _cells(PATTERN, scale)
    assert dhash(image, (0, 0, 9 * scale, 8 * scale)) == _expected_bits(PATTERN)


def test_dhash_of_a_subregion_and_of_flat_or_ordered_images() -> None:
    decreasing = [[250 - 20 * c for c in range(9)] for _ in range(8)]
    increasing = [[20 * c for c in range(9)] for _ in range(8)]
    assert dhash(_cells(decreasing), (0, 0, 9, 8)) == "f" * 16
    assert dhash(_cells(increasing), (0, 0, 9, 8)) == "0" * 16
    assert dhash(_cells([[100] * 9] * 8), (0, 0, 9, 8)) == "0" * 16, "ô bằng nhau không phải sáng hơn"
    scaled = _cells(PATTERN, 3)
    big = _image(18 * 3, 16 * 3, lambda x, y: (200 if x >= 27 else 10,) * 3)
    assert dhash(scaled, (0, 0, 27, 24)) == _expected_bits(PATTERN)
    left, top, right, bottom = 27, 24, 54, 48
    assert dhash(big, (left, top, right, bottom)) == "0" * 16
    assert dhash(big, (0, 0, 54, 48)) is not None


def test_dhash_rejects_empty_boxes_and_returns_sixteen_hex_digits() -> None:
    image = _cells(PATTERN)
    assert dhash(image, (3, 3, 3, 8)) is None
    assert dhash(image, (3, 3, 8, 3)) is None
    assert dhash(image, (5, 3, 3, 8)) is None
    value = dhash(image, (0, 0, 9, 8))
    assert value is not None and len(value) == 16 and int(value, 16) >= 0


def test_gray_weights_order_red_green_blue() -> None:
    def pair(left: tuple[int, int, int], right: tuple[int, int, int]) -> str | None:
        values = _image(9, 8, lambda x, y: left if x % 2 == 0 else right)
        return dhash(values, (0, 0, 9, 8))

    # Cột chẵn là `left`, cột lẻ là `right`: bit 1 ở vị trí chẵn khi `left` sáng hơn `right`.
    assert pair((255, 0, 0), (0, 255, 0)) == "55" * 8, "xanh lá sáng hơn đỏ"
    assert pair((0, 255, 0), (255, 0, 0)) == "aa" * 8
    assert pair((255, 0, 0), (0, 0, 255)) == "aa" * 8, "đỏ sáng hơn xanh dương"
    assert pair((0, 0, 255), (255, 0, 0)) == "55" * 8
    assert pair((0, 255, 0), (0, 0, 255)) == "aa" * 8, "xanh lá sáng hơn xanh dương"
    assert pair((0, 0, 255), (0, 255, 0)) == "55" * 8


def test_hamming_counts_differing_bits() -> None:
    assert hamming("0" * 16, "0" * 16) == 0
    assert hamming("0" * 16, "f" * 16) == 64
    assert hamming("00000000000000ff", "0000000000000f0f") == 8
    assert hamming("8000000000000001", "0000000000000000") == 2
