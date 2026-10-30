"""Ảnh template của tài liệu: ảnh lặp lại cùng nội dung, cùng chỗ trên đa số trang (ADR-008 mục 6; sửa đổi 2026-10-30).

Ảnh template là phần khung của slide (ảnh nền, dải tiêu đề, logo), không phải nội dung: độ phủ ảnh dùng cho mọi luật
dựa trên độ phủ (trang ảnh ít chữ, ảnh phủ gần kín, phần ảnh của cờ hình) chỉ tính ảnh KHÔNG phải template.
`find_template_images` là hàm DUY NHẤT xác định ảnh template (khung VLM dùng lại khi che template); hàm thuần, không
gọi PDFium. Khoá của một ảnh (`ImageKey`) gồm SHA-256 dữ liệu thô của luồng ảnh (chưa giải mã), kích thước điểm
ảnh khai trong từ điển của luồng ảnh, bộ lọc và hộp chuẩn hoá đã làm tròn (`image_key`); worker lấy khoá bằng
`pdfium_adapter` mà không render và không giải mã ảnh. `ImageKey.bits_per_pixel` luôn là `UNREAD_BITS_PER_PIXEL`: số
bit mỗi điểm ảnh chỉ đọc được qua metadata của PDFium, mà lời gọi đó có thể giải mã ảnh (JPX, JBIG2).

Dung sai vị trí đối xứng (`find_template_images`): hai lần vẽ cùng nội dung được coi là cùng chỗ khi mọi toạ độ của
hộp đã làm tròn lệch nhau tối đa một ô lưới, nên hai hộp lệch không quá một bước `template_image_bbox_step` luôn cùng
chỗ kể cả khi vắt qua biên ô lưới.

`scan_template_images` là lượt quét cấp tài liệu của worker, chạy một lần trước trang đầu: mọi nhánh lỗi (vượt trần
số ảnh hay số byte, quá hạn, lỗi đọc trang hay ảnh) cho kết quả không có ảnh template, tức hành vi cũ. Ảnh template
chỉ làm GIẢM độ phủ ảnh; luật lớp chữ rỗng hay rác không dùng độ phủ, nên không trang nào vì ảnh template mà thoát
OCR khi lớp chữ rỗng hoặc rác (I5).
"""

from __future__ import annotations

import itertools
import math
import time
from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass
from typing import NamedTuple, Protocol

from .config import PdfReaderConfig
from .protocol import MAX_GRID_INDEX, MAX_TEMPLATE_KEYS, GridBox, ImageKey, TemplateScan, UnitBBox

# Lỗi khi đọc một trang hay một ảnh (cùng nhóm lỗi trang của worker, ADR-008 mục 4). Trong lượt quét: cả tài liệu
# không có ảnh template. Khi đọc trang: ảnh lỗi không có khoá, được tính là ảnh nội dung.
READ_ERRORS = (RuntimeError, ValueError, LookupError, ArithmeticError, TypeError, MemoryError, OSError)

# Giá trị `ImageKey.bits_per_pixel` của mọi khoá: số bit mỗi điểm ảnh không được đọc (xem docstring của module).
UNREAD_BITS_PER_PIXEL = 0

# Độ lệch tối đa (số ô lưới) giữa hai hộp được coi là cùng chỗ, trên mỗi toạ độ.
GRID_TOLERANCE = 1
_GRID_OFFSETS = tuple(itertools.product(range(-GRID_TOLERANCE, GRID_TOLERANCE + 1), repeat=4))

# Nội dung của một ảnh: mọi trường của `ImageKey` trừ hộp.
_Content = tuple[str, int, int, tuple[str, ...]]


def quantize_bbox(bbox: UnitBBox, step: float) -> GridBox:
    """Làm tròn hộp chuẩn hoá về ô lưới bước `step`, làm tròn nửa lên (dung sai: `find_template_images`)."""
    x0, y0, x1, y1 = (min(MAX_GRID_INDEX, math.floor(value / step + 0.5)) for value in bbox)
    return (x0, y0, x1, y1)


def image_key(
    digest: str,
    width_px: int,
    height_px: int,
    filters: Sequence[str],
    bbox: UnitBBox,
    step: float,
) -> ImageKey:
    """Khoá của một lần vẽ ảnh: nội dung (`digest` của dữ liệu thô, kích thước điểm ảnh, bộ lọc) và vị trí đã làm tròn."""
    return ImageKey(
        digest=digest,
        width_px=width_px,
        height_px=height_px,
        bits_per_pixel=UNREAD_BITS_PER_PIXEL,
        filters=tuple(filters),
        bbox=quantize_bbox(bbox, step),
    )


def find_template_images(
    page_keys: Sequence[Iterable[ImageKey]], page_count: int, config: PdfReaderConfig
) -> frozenset[ImageKey]:
    """Ảnh template: khoá mà số trang có ảnh CÙNG NỘI DUNG ở CÙNG CHỖ đạt ≥ `template_image_min_pages` trang VÀ
    ≥ `template_image_min_page_ratio` tổng số trang.

    "Cùng chỗ" là mọi toạ độ của hộp đã làm tròn lệch tối đa `GRID_TOLERANCE` ô lưới (quan hệ đối xứng, kể cả trang
    của chính khoá đó). Mỗi khoá được xét riêng theo các trang lân cận của nó, không gom bắc cầu: ảnh trôi dần qua
    nhiều trang không thành template. Kết quả chỉ phụ thuộc tập khoá của từng trang, không phụ thuộc thứ tự trang hay
    thứ tự ảnh. `page_keys[i]` là khoá các ảnh của một trang đã quét; một khoá xuất hiện nhiều lần trên cùng trang chỉ
    tính một trang. Mẫu số của tỉ lệ là `page_count`, tổng số trang của tài liệu (không phải số trang đã quét). Tài
    liệu ít hơn `template_image_min_pages` trang (1–2 trang với mặc định) không có ảnh template.
    """
    if len(page_keys) > page_count:
        raise ValueError("số trang đã quét vượt số trang của tài liệu")
    if page_count < config.template_image_min_pages:
        return frozenset()
    pages_at: defaultdict[tuple[_Content, GridBox], set[int]] = defaultdict(set)
    keys_seen: set[ImageKey] = set()
    for page, keys in enumerate(page_keys):
        for key in keys:
            pages_at[(_content(key), key.bbox)].add(page)
            keys_seen.add(key)
    templates: set[ImageKey] = set()
    for key in keys_seen:
        content = _content(key)
        pages: set[int] = set()
        for bbox in _nearby_boxes(key.bbox):
            pages |= pages_at.get((content, bbox), set())
        if (
            len(pages) >= config.template_image_min_pages
            and len(pages) / page_count >= config.template_image_min_page_ratio
        ):
            templates.add(key)
    return frozenset(templates)


def _content(key: ImageKey) -> _Content:
    return (key.digest, key.width_px, key.height_px, key.filters)


def _nearby_boxes(bbox: GridBox) -> Iterator[GridBox]:
    """Các hộp lệch tối đa `GRID_TOLERANCE` ô lưới trên mỗi toạ độ (kể cả chính `bbox`)."""
    x0, y0, x1, y1 = bbox
    for dx0, dy0, dx1, dy1 in _GRID_OFFSETS:
        yield (x0 + dx0, y0 + dy0, x1 + dx1, y1 + dy1)


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


def _sort_key(key: ImageKey) -> tuple[str, GridBox, int, int, tuple[str, ...]]:
    return (key.digest, key.bbox, key.width_px, key.height_px, key.filters)


def scan_template_images(
    source: ImageKeySource,
    page_count: int,
    config: PdfReaderConfig,
    clock: Callable[[], float] = time.perf_counter,
) -> TemplateScan:
    """Lượt quét ảnh của cả tài liệu, trong hạn `template_scan_timeout_s` (tính từ lúc bắt đầu quét).

    Vượt trần số ảnh (một trang hay cả tài liệu), số byte đem băm của cả tài liệu, hay số khoá template ⇒
    `over_cap`; quá hạn ⇒ `timeout`; lỗi đọc trang hay ảnh ⇒ `error`. Mọi trạng thái đó không có khoá nào. Tiến
    trình cha giữ thêm hạn cứng của chính nó (một lời gọi PDFium treo không trả quyền về đây).

    Đồng hồ mặc định là `time.perf_counter` (đơn điệu, độ phân giải cao trên mọi nền tảng): `time.monotonic` của
    Python < 3.13 trên Windows chỉ nhích theo nhịp khoảng 15,6 ms, nên hạn ngắn hơn một nhịp không bao giờ bị vượt.
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
