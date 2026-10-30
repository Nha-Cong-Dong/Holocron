"""Ảnh template của tài liệu: ảnh lặp lại cùng nội dung, cùng chỗ trên đa số trang (ADR-008 mục 6; sửa đổi 2026-10-30).

Ảnh template là phần khung của slide (ảnh nền, dải tiêu đề, logo), không phải nội dung: độ phủ ảnh dùng cho mọi luật
dựa trên độ phủ (trang ảnh ít chữ, ảnh phủ gần kín, phần ảnh của cờ hình) chỉ tính ảnh KHÔNG phải template.
`find_template_images` là hàm DUY NHẤT xác định ảnh template (khung VLM dùng lại khi che template); hàm thuần, không
gọi PDFium. Khoá của một ảnh (`ImageKey`) gồm SHA-256 dữ liệu thô của luồng ảnh, kích thước điểm ảnh, số bit mỗi
điểm ảnh, bộ lọc và hộp chuẩn hoá đã làm tròn (`image_key`); worker lấy khoá bằng `pdfium_adapter` mà không render.

`scan_template_images` là lượt quét cấp tài liệu của worker, chạy một lần trước trang đầu: mọi nhánh lỗi (vượt trần
số ảnh hay số byte, quá hạn, lỗi đọc trang hay ảnh) cho kết quả không có ảnh template, tức hành vi cũ. Ảnh template
chỉ làm GIẢM độ phủ ảnh; luật lớp chữ rỗng hay rác không dùng độ phủ, nên không trang nào vì ảnh template mà thoát
OCR khi lớp chữ rỗng hoặc rác (I5).
"""

from __future__ import annotations

import math
import time
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import NamedTuple, Protocol

from .config import PdfReaderConfig
from .protocol import MAX_GRID_INDEX, MAX_TEMPLATE_KEYS, GridBox, ImageKey, TemplateScan, UnitBBox

# Lỗi khi đọc một trang hay một ảnh (cùng nhóm lỗi trang của worker, ADR-008 mục 4). Trong lượt quét: cả tài liệu
# không có ảnh template. Khi đọc trang: ảnh lỗi không có khoá, được tính là ảnh nội dung.
READ_ERRORS = (RuntimeError, ValueError, LookupError, ArithmeticError, TypeError, MemoryError, OSError)


def quantize_bbox(bbox: UnitBBox, step: float) -> GridBox:
    """Làm tròn hộp chuẩn hoá về ô lưới bước `step` (dung sai vị trí và kích thước), làm tròn nửa lên."""
    x0, y0, x1, y1 = (min(MAX_GRID_INDEX, math.floor(value / step + 0.5)) for value in bbox)
    return (x0, y0, x1, y1)


def image_key(
    digest: str,
    width_px: int,
    height_px: int,
    bits_per_pixel: int,
    filters: Sequence[str],
    bbox: UnitBBox,
    step: float,
) -> ImageKey:
    """Khoá của một lần vẽ ảnh: nội dung (`digest` cùng metadata của luồng ảnh) và vị trí đã làm tròn."""
    return ImageKey(
        digest=digest,
        width_px=width_px,
        height_px=height_px,
        bits_per_pixel=bits_per_pixel,
        filters=tuple(filters),
        bbox=quantize_bbox(bbox, step),
    )


def find_template_images(
    page_keys: Sequence[Iterable[ImageKey]], page_count: int, config: PdfReaderConfig
) -> frozenset[ImageKey]:
    """Ảnh template: khoá có mặt trên ≥ `template_image_min_pages` trang VÀ ≥ `template_image_min_page_ratio` trang.

    `page_keys[i]` là khoá các ảnh của một trang đã quét; một khoá xuất hiện nhiều lần trên cùng trang chỉ tính một
    trang. Mẫu số của tỉ lệ là `page_count`, tổng số trang của tài liệu (không phải số trang đã quét). Tài liệu ít
    hơn `template_image_min_pages` trang (1–2 trang với mặc định) không có ảnh template.
    """
    if len(page_keys) > page_count:
        raise ValueError("số trang đã quét vượt số trang của tài liệu")
    if page_count < config.template_image_min_pages:
        return frozenset()
    pages_with_key = Counter(key for keys in page_keys for key in set(keys))
    return frozenset(
        key
        for key, pages in pages_with_key.items()
        if pages >= config.template_image_min_pages and pages / page_count >= config.template_image_min_page_ratio
    )


@dataclass(frozen=True)
class TemplateFilter:
    """Tập ảnh template của lượt quét, dùng khi đọc từng trang để tính độ phủ ảnh không-template.

    Khoá của ảnh trên trang được tính lại bằng đúng cách của lượt quét (`bbox_step`, `max_image_bytes`); trang có
    nhiều hơn `max_images_per_page` ảnh không loại ảnh nào.
    """

    keys: frozenset[ImageKey]
    bbox_step: float
    max_image_bytes: int
    max_images_per_page: int

    @classmethod
    def from_scan(cls, scan: TemplateScan, config: PdfReaderConfig) -> TemplateFilter | None:
        """None khi lượt quét không `ok` hoặc không có ảnh template: độ phủ ảnh như cũ."""
        if scan.status != "ok" or not scan.keys:
            return None
        return cls(
            keys=frozenset(scan.keys),
            bbox_step=config.template_image_bbox_step,
            max_image_bytes=config.template_scan_max_image_bytes,
            max_images_per_page=config.template_scan_max_images_per_page,
        )


class PageImageKeys(NamedTuple):
    """Khoá các ảnh của một trang theo thứ tự đối tượng (None: ảnh không có khoá) và số byte đã đem băm."""

    keys: list[ImageKey | None]
    hashed_bytes: int


class ImageKeySource(Protocol):
    def page_image_keys(self, index: int, config: PdfReaderConfig) -> PageImageKeys | None:
        """Khoá các ảnh của trang `index`; None khi trang có quá `template_scan_max_images_per_page` ảnh."""
        ...


def _sort_key(key: ImageKey) -> tuple[str, GridBox, int, int, int, tuple[str, ...]]:
    return (key.digest, key.bbox, key.width_px, key.height_px, key.bits_per_pixel, key.filters)


def scan_template_images(
    source: ImageKeySource,
    page_count: int,
    config: PdfReaderConfig,
    clock: Callable[[], float] = time.monotonic,
) -> TemplateScan:
    """Lượt quét ảnh của cả tài liệu, trong hạn `template_scan_timeout_s` (tính từ lúc bắt đầu quét).

    Vượt trần số ảnh (một trang hay cả tài liệu), số byte đem băm của cả tài liệu, hay số khoá template ⇒
    `over_cap`; quá hạn ⇒ `timeout`; lỗi đọc trang hay ảnh ⇒ `error`. Mọi trạng thái đó không có khoá nào. Tiến
    trình cha giữ thêm hạn cứng của chính nó (một lời gọi PDFium treo không trả quyền về đây).
    """
    if page_count < config.template_image_min_pages:
        return TemplateScan(status="ok")
    deadline = clock() + config.template_scan_timeout_s
    pages: list[list[ImageKey]] = []
    images = 0
    hashed_bytes = 0
    for index in range(page_count):
        if clock() > deadline:
            return TemplateScan(status="timeout")
        try:
            scanned = source.page_image_keys(index, config)
        except READ_ERRORS:
            return TemplateScan(status="error")
        if scanned is None:
            return TemplateScan(status="over_cap")
        images += len(scanned.keys)
        hashed_bytes += scanned.hashed_bytes
        if images > config.template_scan_max_images or hashed_bytes > config.template_scan_max_bytes:
            return TemplateScan(status="over_cap")
        pages.append([key for key in scanned.keys if key is not None])
    if clock() > deadline:
        return TemplateScan(status="timeout")
    templates = find_template_images(pages, page_count, config)
    if len(templates) > MAX_TEMPLATE_KEYS:
        return TemplateScan(status="over_cap")
    return TemplateScan(status="ok", keys=sorted(templates, key=_sort_key))
