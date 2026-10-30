"""Test bổ sung giết đột biến sống: `scan_flags`, biên `ImageKey`, nội dung và thứ tự khoá ảnh template."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from holocron_core.infrastructure.document_readers.config import PdfReaderConfig
from holocron_core.infrastructure.document_readers.protocol import (
    ImageKey,
    RawOcrLine,
    RawOcrPage,
    RawPdfPage,
    RawWord,
)
from holocron_core.infrastructure.document_readers.template_images import (
    PageImageKeys,
    find_template_images,
    scan_template_images,
)
from holocron_core.infrastructure.document_readers.tiers import scan_flags

CONFIG = PdfReaderConfig()
DIGEST = "a" * 64
OTHER_DIGEST = "b" * 64


def _scan_page(width_pt: float, height_pt: float) -> tuple[RawPdfPage, RawOcrPage]:
    """Cùng lưới dòng như `_table_page` của test_pdf_tier_rules, dựng bằng dòng OCR."""
    lines = []
    for row in range(3):
        top = 0.1 + row * 0.05
        words = [
            RawWord(text="aa", bbox=(0.10, top, 0.20, top + 0.02)),
            RawWord(text="bb", bbox=(0.23, top, 0.33, top + 0.02)),
        ]
        lines.append(RawOcrLine(text="aa bb", bbox=(0.10, top, 0.33, top + 0.02), words=words, conf=0.9))
    ocr = RawOcrPage(dpi=300.0, lines=lines, ink_outside_text_ratio=0.0, secondary=None)
    raw = RawPdfPage(
        page_index=0, width_pt=width_pt, height_pt=height_pt, lines=[], image_area_ratio=0.0, path_area_ratio=0.0
    )
    return raw, ocr


def test_scan_flags_table_uses_width_and_height_in_right_order() -> None:
    raw, ocr = _scan_page(720.0, 540.0)
    assert scan_flags(raw, ocr, CONFIG).has_table is True
    raw, ocr = _scan_page(540.0, 720.0)
    assert scan_flags(raw, ocr, CONFIG).has_table is False


def _key(
    *,
    digest: str = DIGEST,
    width_px: int = 8,
    height_px: int = 8,
    filters: tuple[str, ...] = ("FlateDecode",),
    bbox: tuple[int, int, int, int] = (0, 0, 100, 100),
) -> ImageKey:
    return ImageKey(digest=digest, width_px=width_px, height_px=height_px, filters=filters, bbox=bbox)


def test_image_key_bits_per_pixel_bound() -> None:
    with pytest.raises(ValidationError):
        ImageKey(digest=DIGEST, width_px=1, height_px=1, bits_per_pixel=1, filters=(), bbox=(0, 0, 1, 1))


def test_image_key_pixel_bound_per_axis() -> None:
    wide = ImageKey(digest=DIGEST, width_px=1_000_000, height_px=1, bits_per_pixel=0, filters=(), bbox=(0, 0, 1, 1))
    tall = ImageKey(digest=DIGEST, width_px=1, height_px=1_000_000, bits_per_pixel=0, filters=(), bbox=(0, 0, 1, 1))
    assert wide.width_px == 1_000_000
    assert tall.height_px == 1_000_000


_VARIANTS = {
    "width": (_key(width_px=4), _key(width_px=8)),
    "height": (_key(height_px=4), _key(height_px=8)),
    "filters": (_key(filters=("FlateDecode",)), _key(filters=("DCTDecode",))),
}


@pytest.mark.parametrize("name", sorted(_VARIANTS))
def test_find_template_images_does_not_merge_different_content(name: str) -> None:
    first, second = _VARIANTS[name]
    page_keys = [[first]] * 3 + [[second]] * 3
    assert find_template_images(page_keys, 6, CONFIG) == frozenset()


class _Source:
    def __init__(self, per_page: list[list[ImageKey]]) -> None:
        self._per_page = per_page

    def page_image_keys(self, index: int, config: PdfReaderConfig) -> PageImageKeys | None:
        return PageImageKeys(keys=list(self._per_page[index]), hashed_bytes=0)


def _variants(name: str) -> list[ImageKey]:
    """Tám khoá cùng digest, chỉ khác một trường, theo thứ tự tăng của trường đó (tám khoá: xác suất trùng thứ tự
    băm ngẫu nhiên của frozenset chỉ 1/40320)."""
    if name == "bbox":
        return [_key(bbox=(5 * i, 0, 100, 100)) for i in range(8)]
    if name == "width":
        return [_key(width_px=i + 1) for i in range(8)]
    if name == "height":
        return [_key(height_px=i + 1) for i in range(8)]
    return [_key(filters=(f"F{i}Decode",)) for i in range(8)]


@pytest.mark.parametrize("name", ["bbox", "width", "height", "filters"])
def test_scan_keys_sorted_deterministically(name: str) -> None:
    ordered = _variants(name)
    # Thứ tự ngược nhau trên mọi trang, mỗi biến thể có mặt ở cả 6 trang ⇒ đều là template.
    scan = scan_template_images(_Source([list(reversed(ordered))] * 3 + [ordered] * 3), 6, CONFIG)
    assert scan.status == "ok"
    assert scan.keys == ordered
