"""Template của tài liệu: ảnh, đường vẽ và dòng chữ lặp lại cùng nội dung, cùng chỗ trên nhiều trang (ADR-008 mục 6;
sửa đổi 2026-10-30; mở rộng GĐ0 2026-10-31 sang đường vẽ (PATH) và dòng chữ (TEXT, header/footer)).

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

GĐ0: CHÍNH `find_template_images` xác định cả ba loại khoá (`ImageKey`, `PathKey`, `TextKey`), cùng dung sai vị trí,
cùng trần số trang tối thiểu; ảnh và đường vẽ dùng `template_min_page_ratio`, dòng chữ dùng ngưỡng header/footer
(`header_footer_min_page_ratio` và độ trải `header_footer_min_spread`). Không có hàm thứ hai.
"""

from __future__ import annotations

import itertools
import math
import re
import time
import unicodedata
from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass
from typing import NamedTuple, Protocol

from .config import PdfReaderConfig
from .protocol import (
    MAX_GRID_INDEX,
    MAX_TEMPLATE_KEYS,
    MAX_TEMPLATE_TEXT_CHARS,
    GridBox,
    ImageKey,
    PathKey,
    Rgba,
    TemplateKey,
    TemplateScan,
    TextKey,
    UnitBBox,
)

# Lỗi khi đọc một trang hay một ảnh (cùng nhóm lỗi trang của worker, ADR-008 mục 4). Trong lượt quét: cả tài liệu
# không có ảnh template. Khi đọc trang: ảnh lỗi không có khoá, được tính là ảnh nội dung.
READ_ERRORS = (RuntimeError, ValueError, LookupError, ArithmeticError, TypeError, MemoryError, OSError)

# Giá trị `ImageKey.bits_per_pixel` của mọi khoá: số bit mỗi điểm ảnh không được đọc (xem docstring của module).
UNREAD_BITS_PER_PIXEL = 0

# Độ lệch tối đa (số ô lưới) giữa hai hộp được coi là cùng chỗ, trên mỗi toạ độ.
GRID_TOLERANCE = 1
_GRID_OFFSETS = tuple(itertools.product(range(-GRID_TOLERANCE, GRID_TOLERANCE + 1), repeat=4))

# Nội dung của một khoá: mọi trường trừ hộp, kèm loại khoá (khoá khác loại không bao giờ cùng nhóm).
_Content = tuple[str, tuple[str | int | tuple[str, ...] | Rgba | None, ...]]

_WHITESPACE_RUN = re.compile(r"\s+")


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


def path_key(bbox: UnitBBox, fill: Rgba | None, stroke: Rgba | None, segments: int, step: float) -> PathKey:
    """Khoá của một lần vẽ đường (GĐ0 0A): hộp làm tròn, màu tô, màu viền (None: không tô/không viền), số đoạn."""
    return PathKey(bbox=quantize_bbox(bbox, step), fill=fill, stroke=stroke, segments=segments)


def normalize_template_text(text: str) -> str:
    """Chuỗi chuẩn hoá của dòng (GĐ0 0A): NFC, mọi chữ số Unicode `Nd` thành `#`, gộp khoảng trắng, bỏ khoảng trắng
    đầu/cuối. Chỉ dùng làm khoá so khớp; văn bản của dòng không bao giờ bị đổi (I3)."""
    normalized = unicodedata.normalize("NFC", text)
    hashed = "".join("#" if unicodedata.category(char) == "Nd" else char for char in normalized)
    return _WHITESPACE_RUN.sub(" ", hashed).strip()


def text_key(text: str, bbox: UnitBBox | None, step: float) -> TextKey | None:
    """Khoá của một dòng; dòng không có hộp, rỗng sau chuẩn hoá hay dài hơn `MAX_TEMPLATE_TEXT_CHARS` ⇒ None."""
    normalized = normalize_template_text(text)
    if bbox is None or not normalized or len(normalized) > MAX_TEMPLATE_TEXT_CHARS:
        return None
    return TextKey(bbox=quantize_bbox(bbox, step), text=normalized)


def find_template_images[K: TemplateKey](
    page_keys: Sequence[Iterable[K]], page_count: int, config: PdfReaderConfig
) -> frozenset[K]:
    """Template: khoá mà số trang có đối tượng CÙNG NỘI DUNG ở CÙNG CHỖ đạt ≥ `template_image_min_pages` trang VÀ
    ≥ `template_min_page_ratio` tổng số trang (ảnh `ImageKey`, đường vẽ `PathKey`). Dòng chữ (`TextKey`, GĐ0 0B(5)):
    ≥ `template_image_min_pages` trang VÀ ≥ `header_footer_min_page_ratio` tổng số trang VÀ
    `(p_max − p_min) / page_count ≥ header_footer_min_spread`, với `p_max`, `p_min` là trang lớn nhất, nhỏ nhất có
    dòng đó ở cùng chỗ. Một hàm cho cả ba loại (GĐ0); khoá khác loại không bao giờ cùng nhóm.

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
    keys_seen: set[K] = set()
    for page, keys in enumerate(page_keys):
        for key in keys:
            pages_at[(_content(key), key.bbox)].add(page)
            keys_seen.add(key)
    templates: set[K] = set()
    for key in keys_seen:
        content = _content(key)
        pages: set[int] = set()
        for bbox in _nearby_boxes(key.bbox):
            pages |= pages_at.get((content, bbox), set())
        if _repeats_enough(key, pages, page_count, config):
            templates.add(key)
    return frozenset(templates)


def _repeats_enough(key: TemplateKey, pages: set[int], page_count: int, config: PdfReaderConfig) -> bool:
    if len(pages) < config.template_image_min_pages:
        return False
    if isinstance(key, TextKey):
        spread = (max(pages) - min(pages)) / page_count
        return (
            len(pages) / page_count >= config.header_footer_min_page_ratio and spread >= config.header_footer_min_spread
        )
    return len(pages) / page_count >= config.template_min_page_ratio


def _content(key: TemplateKey) -> _Content:
    if isinstance(key, ImageKey):
        return ("image", (key.digest, key.width_px, key.height_px, key.filters))
    if isinstance(key, PathKey):
        return ("path", (key.fill, key.stroke, key.segments))
    return ("text", (key.text,))


def matches_text_key(key: TextKey, keys: frozenset[TextKey]) -> bool:
    """Dòng khớp một khoá TEXT template: cùng chuỗi chuẩn hoá, hộp lệch tối đa `GRID_TOLERANCE` ô (0B(5))."""
    return any(TextKey(bbox=bbox, text=key.text) in keys for bbox in _nearby_boxes(key.bbox) if _in_grid(bbox))


def _in_grid(bbox: GridBox) -> bool:
    return all(0 <= value <= MAX_GRID_INDEX for value in bbox)


def _nearby_boxes(bbox: GridBox) -> Iterator[GridBox]:
    """Các hộp lệch tối đa `GRID_TOLERANCE` ô lưới trên mỗi toạ độ (kể cả chính `bbox`)."""
    x0, y0, x1, y1 = bbox
    for dx0, dy0, dx1, dy1 in _GRID_OFFSETS:
        yield (x0 + dx0, y0 + dy0, x1 + dx1, y1 + dy1)


@dataclass(frozen=True)
class TemplateFilter:
    """Template của lượt quét, dùng khi đọc từng trang: ảnh template (độ phủ ảnh không-template), đường vẽ template
    (GĐ0: diện tích đường vẽ nội dung) và dòng chữ lặp (GĐ0: header/footer).

    Khoá trên trang được tính lại bằng đúng cách của lượt quét (`bbox_step`, `max_image_bytes`); trang có nhiều hơn
    `max_images_per_page` ảnh không loại ảnh nào, trang có nhiều hơn `max_paths_per_page` đường vẽ không loại đường
    vẽ nào.
    """

    keys: frozenset[ImageKey]
    bbox_step: float
    max_image_bytes: int
    max_images_per_page: int
    path_keys: frozenset[PathKey] = frozenset()
    text_keys: frozenset[TextKey] = frozenset()
    max_paths_per_page: int = 4096

    @classmethod
    def from_scan(cls, scan: TemplateScan, config: PdfReaderConfig) -> TemplateFilter | None:
        """None khi lượt quét không `ok` hoặc không có template loại nào: độ phủ ảnh như cũ."""
        if scan.status != "ok" or not (scan.keys or scan.path_keys or scan.text_keys):
            return None
        return cls(
            keys=frozenset(scan.keys),
            bbox_step=config.template_image_bbox_step,
            max_image_bytes=config.template_scan_max_image_bytes,
            max_images_per_page=config.template_scan_max_images_per_page,
            path_keys=frozenset(scan.path_keys),
            text_keys=frozenset(scan.text_keys),
            max_paths_per_page=config.template_scan_max_paths_per_page,
        )


class PageImageKeys(NamedTuple):
    """Khoá template của một trang: ảnh theo thứ tự đối tượng (None: ảnh không có khoá), số byte đã đem băm, và
    (GĐ0) khoá đường vẽ (rỗng khi trang có quá `template_scan_max_paths_per_page` đường vẽ) và khoá dòng chữ."""

    keys: list[ImageKey | None]
    hashed_bytes: int
    paths: tuple[PathKey, ...] = ()
    texts: tuple[TextKey, ...] = ()


class ImageKeySource(Protocol):
    def page_image_keys(self, index: int, config: PdfReaderConfig) -> PageImageKeys | None:
        """Khoá template của trang `index`; None khi trang có quá `template_scan_max_images_per_page` ảnh."""
        ...


def _sort_key(key: ImageKey) -> tuple[str, GridBox, int, int, tuple[str, ...]]:
    return (key.digest, key.bbox, key.width_px, key.height_px, key.filters)


def _path_sort_key(key: PathKey) -> tuple[GridBox, tuple[int, ...], tuple[int, ...], int]:
    return (key.bbox, key.fill or (), key.stroke or (), key.segments)


def _text_sort_key(key: TextKey) -> tuple[GridBox, str]:
    return (key.bbox, key.text)


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
    path_pages: list[tuple[PathKey, ...]] = []
    text_pages: list[tuple[TextKey, ...]] = []
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
        path_pages.append(scanned.paths)
        text_pages.append(scanned.texts)
    if clock() > deadline:
        return TemplateScan(status="timeout")
    templates = find_template_images(pages, page_count, config)
    path_templates = find_template_images(path_pages, page_count, config)
    text_templates = find_template_images(text_pages, page_count, config)
    if max(len(templates), len(path_templates), len(text_templates)) > MAX_TEMPLATE_KEYS:
        return TemplateScan(status="over_cap")
    return TemplateScan(
        status="ok",
        keys=sorted(templates, key=_sort_key),
        path_keys=sorted(path_templates, key=_path_sort_key),
        text_keys=sorted(text_templates, key=_text_sort_key),
    )
