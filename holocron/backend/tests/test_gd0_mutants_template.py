"""Test biên cho template GĐ0 (`template_images`: khoá TEXT/PATH, header/footer, thứ tự và trần): giết đột biến sống
của báo cáo 857e035. Ngưỡng đọc từ `PdfReaderConfig`; dữ liệu dựng để đại lượng đúng BẰNG ngưỡng."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import pytest

from holocron_core.infrastructure.document_readers.config import PdfReaderConfig
from holocron_core.infrastructure.document_readers.protocol import (
    MAX_GRID_INDEX,
    MAX_TEMPLATE_KEYS,
    MAX_TEMPLATE_TEXT_CHARS,
    ImageKey,
    PathKey,
    TemplateScan,
    TextKey,
)
from holocron_core.infrastructure.document_readers.template_images import (
    PageImageKeys,
    TemplateFilter,
    find_template_images,
    matches_text_key,
    normalize_template_text,
    quantize_bbox,
    scan_template_images,
    text_key,
)

CONFIG = PdfReaderConfig()
DIGEST = "c" * 64


def _text(text: str = "Tieu de", bbox: tuple[int, int, int, int] = (5, 1, 30, 3)) -> TextKey:
    return TextKey(bbox=bbox, text=text)


def _path(
    bbox: tuple[int, int, int, int] = (0, 0, 100, 7),
    fill: tuple[int, int, int, int] | None = (25, 51, 127, 255),
    stroke: tuple[int, int, int, int] | None = None,
    segments: int = 5,
) -> PathKey:
    return PathKey(bbox=bbox, fill=fill, stroke=stroke, segments=segments)


def _image(bbox: tuple[int, int, int, int] = (0, 0, 100, 100), digest: str = DIGEST) -> ImageKey:
    return ImageKey(digest=digest, width_px=8, height_px=8, filters=("FlateDecode",), bbox=bbox)


def _on(pages: Iterable[int], count: int, key: Any) -> list[list[Any]]:
    wanted = set(pages)
    return [[key] if page in wanted else [] for page in range(count)]


# --------------------------------------------------------------------------- #
# Chuẩn hoá chuỗi, khoá TEXT, so khớp
# --------------------------------------------------------------------------- #


def test_normalize_template_text_rules() -> None:
    assert normalize_template_text("Café") == "Café", "NFC"
    assert normalize_template_text("Trang 12") == "Trang ##"
    assert normalize_template_text("٣٤ ३") == "## #", "chữ số Unicode `Nd` khác nhau đều thành #"
    assert normalize_template_text("x² ½") == "x² ½", "số kiểu No không phải chữ số Nd"
    assert normalize_template_text("a \t\n   b") == "a b"
    assert normalize_template_text("   ") == ""
    assert normalize_template_text("  a  ") == "a"
    assert normalize_template_text("") == ""


def test_text_key_drops_lines_without_box_text_or_with_too_long_text() -> None:
    step = CONFIG.template_image_bbox_step
    box = (0.05, 0.01, 0.30, 0.03)
    key = text_key("Trang 3", box, step)
    assert key == TextKey(bbox=quantize_bbox(box, step), text="Trang #")
    assert text_key("Trang 3", None, step) is None
    assert text_key("", box, step) is None
    assert text_key(" \t ", box, step) is None
    exact = "a" * MAX_TEMPLATE_TEXT_CHARS
    assert text_key(exact, box, step) == TextKey(bbox=quantize_bbox(box, step), text=exact)
    assert text_key(exact + "a", box, step) is None
    collapsing = "a" + " " * (MAX_TEMPLATE_TEXT_CHARS * 2) + "b"
    assert text_key(collapsing, box, step) == TextKey(bbox=quantize_bbox(box, step), text="a b"), (
        "đo độ dài SAU chuẩn hoá"
    )


def test_matches_text_key_allows_one_grid_cell_of_drift_on_every_coordinate() -> None:
    stored = _text("x", (10, 10, 20, 20))
    keys = frozenset({stored})
    for coordinate in range(4):
        for delta in (-1, 0, 1):
            box = [10, 10, 20, 20]
            box[coordinate] += delta
            assert matches_text_key(_text("x", (box[0], box[1], box[2], box[3])), keys) is True
        for delta in (-2, 2):
            box = [10, 10, 20, 20]
            box[coordinate] += delta
            assert matches_text_key(_text("x", (box[0], box[1], box[2], box[3])), keys) is False
    assert matches_text_key(_text("y", (10, 10, 20, 20)), keys) is False
    assert matches_text_key(_text("x", (11, 9, 21, 19)), keys) is True
    assert matches_text_key(_text("x", (10, 10, 20, 20)), frozenset()) is False


def test_matches_text_key_at_the_edges_of_the_grid() -> None:
    top = MAX_GRID_INDEX
    corner = frozenset({_text("x", (0, 0, top, top))})
    assert matches_text_key(_text("x", (0, 0, top, top)), corner) is True
    assert matches_text_key(_text("x", (1, 1, top - 1, top - 1)), corner) is True
    assert matches_text_key(_text("x", (2, 0, top, top)), corner) is False
    assert matches_text_key(_text("x", (0, 0, top - 2, top)), corner) is False


# --------------------------------------------------------------------------- #
# find_template_images: dòng chữ (tỉ lệ trang và độ trải), đường vẽ, ảnh
# --------------------------------------------------------------------------- #


def test_text_template_needs_page_ratio_and_spread_each_inclusive() -> None:
    count = 20
    config = PdfReaderConfig(header_footer_min_page_ratio=6 / count, header_footer_min_spread=0.5)
    key = _text()
    spread_exact = _on(range(0, 12, 2), count, key)  # 6 trang, trải (10 - 0) / 20 = 0.5
    assert find_template_images(spread_exact, count, config) == {key}
    too_narrow = _on(range(6), count, key)  # 6 trang, trải 5 / 20 = 0.25
    assert find_template_images(too_narrow, count, config) == frozenset()
    just_below = _on((0, 2, 4, 6, 9), count, key)  # 5 trang, trải 0.45: dưới cả hai
    assert find_template_images(just_below, count, config) == frozenset()
    late_and_narrow = _on(range(5, 11), count, key)  # 6 trang từ 5 đến 10: trải 5 / 20 = 0.25, không phải (10 + 5) / 20
    assert find_template_images(late_and_narrow, count, config) == frozenset()
    late_but_wide = _on(range(2, 14, 2), count, key)  # trang 2..12: trải 10 / 20 = 0.5
    assert find_template_images(late_but_wide, count, config) == {key}
    wide_but_rare = _on((0, 5, 10, 15, 19), count, key)  # 5 trang, trải 0.95: đủ trải, thiếu tỉ lệ
    assert find_template_images(wide_but_rare, count, config) == frozenset()


def test_text_template_spread_is_measured_against_the_page_count() -> None:
    count = 10
    config = PdfReaderConfig(header_footer_min_page_ratio=0.3, header_footer_min_spread=0.7)
    key = _text()
    assert find_template_images(_on((0, 3, 7), count, key), count, config) == {key}
    assert find_template_images(_on((0, 3, 6), count, key), count, config) == frozenset()


def test_template_needs_the_minimum_number_of_pages_even_when_the_ratio_is_met() -> None:
    minimum = CONFIG.template_image_min_pages
    count = minimum + 1
    config = PdfReaderConfig(
        template_min_page_ratio=(minimum - 1) / count,
        header_footer_min_page_ratio=(minimum - 1) / count,
        header_footer_min_spread=0.0,
    )
    for key in (_image(), _path(), _text()):
        assert find_template_images(_on(range(minimum - 1), count, key), count, config) == frozenset()
        assert find_template_images(_on(range(minimum), count, key), count, config) == {key}


def test_image_and_path_templates_use_the_template_page_ratio_inclusively() -> None:
    count = 10
    config = PdfReaderConfig(template_min_page_ratio=5 / count)
    for key in (_image(), _path()):
        assert find_template_images(_on(range(5), count, key), count, config) == {key}
        assert find_template_images(_on(range(4), count, key), count, config) == frozenset()


def test_template_filter_default_path_limit_matches_the_config_default() -> None:
    bare = TemplateFilter(keys=frozenset(), bbox_step=0.01, max_image_bytes=1, max_images_per_page=1)
    assert bare.max_paths_per_page == CONFIG.template_scan_max_paths_per_page
    assert bare.path_keys == frozenset() and bare.text_keys == frozenset()


def test_scanned_pages_may_not_exceed_the_document_page_count() -> None:
    keys = [[_image()]] * 3
    assert find_template_images(keys, 3, CONFIG) == {_image()}
    with pytest.raises(ValueError):
        find_template_images(keys, 2, CONFIG)
    assert find_template_images([[_image()]] * 2, 2, CONFIG) == frozenset(), "dưới số trang tối thiểu"


def test_same_place_tolerance_and_content_separate_the_groups() -> None:
    count = 6
    config = PdfReaderConfig(
        template_min_page_ratio=0.5, header_footer_min_page_ratio=0.5, header_footer_min_spread=0.0
    )
    shifts = [(0, 0, 100, 100), (1, 1, 101, 99), (0, 0, 100, 100), (1, 0, 99, 101), (0, 0, 100, 100), (0, 0, 100, 100)]
    pages = [[_image(box)] for box in shifts]
    assert _image((0, 0, 100, 100)) in find_template_images(pages, count, config)
    far = [(0, 0, 100, 100), (2, 0, 100, 100), (0, 2, 100, 100), (0, 0, 102, 100), (0, 0, 100, 102), (3, 3, 97, 97)]
    assert find_template_images([[_image(box)] for box in far], count, config) == frozenset()
    mixed = [[_path(fill=(page % 3, 2, 3, 255))] for page in range(count)]
    assert find_template_images(mixed, count, config) == frozenset(), "khác màu tô ⇒ khác nội dung"
    stroke_mixed = [[_path(stroke=(page % 3, 2, 3, 255))] for page in range(count)]
    assert find_template_images(stroke_mixed, count, config) == frozenset()
    segment_mixed = [[_path(segments=5 + page % 3)] for page in range(count)]
    assert find_template_images(segment_mixed, count, config) == frozenset()
    texts = [[_text(f"A{page % 3}")] for page in range(count)]
    assert find_template_images(texts, count, config) == frozenset()


# --------------------------------------------------------------------------- #
# TemplateFilter.from_scan
# --------------------------------------------------------------------------- #


def test_template_filter_is_built_from_any_kind_of_template_and_copies_the_limits() -> None:
    config = PdfReaderConfig(
        template_image_bbox_step=0.02,
        template_scan_max_image_bytes=1234,
        template_scan_max_images_per_page=7,
        template_scan_max_paths_per_page=9,
    )
    for scan in (
        TemplateScan(status="ok", keys=[_image()]),
        TemplateScan(status="ok", path_keys=[_path()]),
        TemplateScan(status="ok", text_keys=[_text()]),
    ):
        built = TemplateFilter.from_scan(scan, config)
        assert built is not None
        assert (built.bbox_step, built.max_image_bytes, built.max_images_per_page, built.max_paths_per_page) == (
            0.02,
            1234,
            7,
            9,
        )
        assert built.keys == frozenset(scan.keys)
        assert built.path_keys == frozenset(scan.path_keys)
        assert built.text_keys == frozenset(scan.text_keys)
    assert TemplateFilter.from_scan(TemplateScan(status="ok"), config) is None
    assert TemplateFilter.from_scan(TemplateScan(status="timeout"), config) is None


# --------------------------------------------------------------------------- #
# scan_template_images: thứ tự kết quả, trần số khoá, ba loại khoá
# --------------------------------------------------------------------------- #


class _Source:
    def __init__(self, page: PageImageKeys) -> None:
        self._page = page

    def page_image_keys(self, index: int, config: PdfReaderConfig) -> PageImageKeys | None:
        return self._page


def test_scan_returns_every_kind_in_a_stable_sorted_order() -> None:
    images = [_image((5, 0, 9, 9), "e" * 64), _image((9, 0, 9, 9), "a" * 64), _image((1, 0, 9, 9), "e" * 64)]
    paths = [
        _path((3, 0, 9, 9), fill=(1, 1, 1, 255), stroke=None, segments=2),
        _path((3, 0, 9, 9), fill=None, stroke=(1, 1, 1, 255), segments=2),
        _path((3, 0, 9, 9), fill=(1, 1, 1, 255), stroke=(0, 0, 0, 255), segments=2),
        _path((3, 0, 9, 9), fill=(1, 1, 1, 255), stroke=None, segments=1),
        _path((2, 5, 9, 9), fill=(9, 9, 9, 255), stroke=None, segments=9),
    ]
    texts = [_text("b", (4, 4, 5, 5)), _text("a", (4, 4, 5, 5)), _text("z", (1, 1, 5, 5)), _text("c", (4, 3, 5, 5))]
    page = PageImageKeys(
        keys=list(reversed(images)), hashed_bytes=1, paths=tuple(reversed(paths)), texts=tuple(reversed(texts))
    )
    config = PdfReaderConfig(header_footer_min_spread=0.0)
    scan = scan_template_images(_Source(page), 3, config)
    assert scan.status == "ok"
    assert scan.keys == [images[1], images[2], images[0]], "digest, rồi hộp"
    assert scan.path_keys == [paths[4], paths[1], paths[3], paths[0], paths[2]], "hộp, tô, viền, số đoạn"
    assert scan.text_keys == [texts[2], texts[3], texts[1], texts[0]], "hộp, rồi chuỗi"


def _many_texts(count: int) -> tuple[TextKey, ...]:
    return tuple(_text(f"dong {index}", (index % 50, index // 50, 60, 20)) for index in range(count))


def _many_paths(count: int) -> tuple[PathKey, ...]:
    return tuple(_path((index % 50, index // 50, 60, 20), segments=index % 7) for index in range(count))


def _many_images(count: int) -> list[ImageKey | None]:
    return [_image((index % 50, index // 50, 60, 20), f"{index:064x}") for index in range(count)]


def test_scan_key_cap_is_inclusive_for_each_kind() -> None:
    config = PdfReaderConfig(
        header_footer_min_spread=0.0, template_scan_max_images=50_000, template_scan_max_images_per_page=50_000
    )
    at_cap_text = PageImageKeys(keys=[], hashed_bytes=0, texts=_many_texts(MAX_TEMPLATE_KEYS))
    assert scan_template_images(_Source(at_cap_text), 3, config).status == "ok"
    at_cap_path = PageImageKeys(keys=[], hashed_bytes=0, paths=_many_paths(MAX_TEMPLATE_KEYS))
    assert scan_template_images(_Source(at_cap_path), 3, config).status == "ok"
    over_path = PageImageKeys(keys=[], hashed_bytes=0, paths=_many_paths(MAX_TEMPLATE_KEYS + 1))
    assert scan_template_images(_Source(over_path), 3, config) == TemplateScan(status="over_cap")
    over_text = PageImageKeys(keys=[], hashed_bytes=0, texts=_many_texts(MAX_TEMPLATE_KEYS + 1))
    assert scan_template_images(_Source(over_text), 3, config) == TemplateScan(status="over_cap")
    over_image = PageImageKeys(keys=_many_images(MAX_TEMPLATE_KEYS + 1), hashed_bytes=0)
    assert scan_template_images(_Source(over_image), 3, config) == TemplateScan(status="over_cap")
    at_cap_image = PageImageKeys(keys=_many_images(MAX_TEMPLATE_KEYS), hashed_bytes=0)
    assert scan_template_images(_Source(at_cap_image), 3, config).status == "ok"


def test_scan_limits_are_inclusive_on_images_and_hashed_bytes() -> None:
    page = PageImageKeys(keys=[_image()], hashed_bytes=10)
    exact = PdfReaderConfig(
        template_scan_max_images=3,
        template_scan_max_images_per_page=3,
        template_scan_max_bytes=30,
        template_scan_max_image_bytes=10,
    )
    assert scan_template_images(_Source(page), 3, exact).status == "ok"
    fewer = PdfReaderConfig(
        template_scan_max_images=2,
        template_scan_max_images_per_page=2,
        template_scan_max_bytes=30,
        template_scan_max_image_bytes=10,
    )
    assert scan_template_images(_Source(page), 3, fewer).status == "over_cap"
    smaller = PdfReaderConfig(
        template_scan_max_images=3,
        template_scan_max_images_per_page=3,
        template_scan_max_bytes=29,
        template_scan_max_image_bytes=10,
    )
    assert scan_template_images(_Source(page), 3, smaller).status == "over_cap"


def test_scan_of_a_short_document_is_ok_without_reading_any_page() -> None:
    class Refusing:
        def page_image_keys(self, index: int, config: PdfReaderConfig) -> PageImageKeys | None:
            raise AssertionError("không được đọc trang")

    assert scan_template_images(Refusing(), CONFIG.template_image_min_pages - 1, CONFIG) == TemplateScan(status="ok")
