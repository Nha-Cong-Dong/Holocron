"""Stage `base_blocks`: dựng khối cơ sở cho trang có lớp chữ, chỉ từ hình học và kiểu chữ (ADR-010 mục 3, 6, 7, 11).

Các bước (hàm thuần, tất định):
1. **Đoạn dòng.** Mỗi dòng thô của `PageGeometry` tách thành các đoạn tại khe ngang lớn (> `segment_gap_ratio` × chiều
   cao dòng), để một dòng PDFium vắt qua hai cột thành hai đoạn. Từ trong đoạn theo x0 của hộp; từ không hộp giữ vị trí
   theo luồng PDFium (sau từ có hộp kề trước nó) và đoạn mang `reading_order_uncertain`. Dòng không có từ nào có hộp là
   đoạn trôi (`bbox = null`). Đoạn chỉ gồm ký hiệu đầu dòng (bullet) ghép với đoạn kế tiếp của cùng dòng. Dòng thô có
   mảnh `ExtractedLine` đã sửa và dòng gõ thêm dùng tham chiếu `confirmed_line` (cờ `human_edited`).
2. **XY-cut** trên các đoạn có hộp (`reading_order.xy_cut`) cho thứ tự đọc; đoạn của cùng một lá có hộp chồng lấn
   (diện tích giao ≥ `overlap_min_ratio` × diện tích hộp nhỏ hơn) gộp thành khối `figure` + `layout_uncertain` +
   `crop_pending`, chữ vào `label_line_refs`/`labels` (không bao giờ là `paragraph` rác).
3. **Gom khối** theo thứ tự đọc: đoạn nối tiếp khối hiện tại khi cùng kiểu chữ (cỡ, đậm), khoảng cách dọc ≤
   `paragraph_gap_factor` × trung vị khoảng cách dòng của trang, chồng ngang đủ, không thụt lề dòng đầu, không bắt đầu
   bằng ký hiệu đầu dòng hay số thứ tự. Đoạn có cỡ chữ ≥ `heading_size_ratio` × cỡ chữ thân trang (hoặc đậm trong khi
   thân không đậm, đủ ngắn) là `heading` (`level = 1` sơ bộ). Dòng header/footer (khoá TEXT template) là khối
   `header_footer`. Ứng viên số trang là khối riêng (`page_numbers.py`).
4. **Văn bản khối** (mục 7): NFC từng từ, nối bằng MỘT dấu cách, giữ gạch nối cuối dòng; `paragraph` dài quá
   `MAX_BLOCK_TEXT_CHARS` tách tại ranh giới đoạn dòng; loại khác vượt ⇒ `LayoutBuildError` (trang `LAYOUT_FAILED`).
"""

from __future__ import annotations

import bisect
import re
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from itertools import accumulate, pairwise
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..domain.blocks import (
    MAX_BLOCK_TEXT_CHARS,
    MAX_PAGE_BLOCKS,
    Block,
    BlockFlag,
    BlockType,
    ConfirmedLineRef,
    SourceRef,
    WordRef,
)
from ..domain.documents import BBox, GeoWord, PageGeometry
from .extraction_ledger import FlowKey, SourceIndex
from .page_numbers import PageNumberSettings, candidate_text, in_margin_band
from .reading_order import XyCutSettings, assign_block_ids, xy_cut

if TYPE_CHECKING:
    from .block_pipeline import PageContext

BASE_BLOCKS_STAGE_VERSION = 1
_PLACEHOLDER_ID = "blk_0000000000000000"

GroupKind = Literal["heading", "paragraph", "list_item", "header_footer", "figure", "candidate"]


class LayoutBuildError(ValueError):
    """Bộ dựng không dựng được trang (vượt trần khối, khối không phải `paragraph` quá dài, dữ liệu lạ)."""


class BaseBlockSettings(BaseModel):
    """Ngưỡng của stage `base_blocks` (toạ độ chuẩn hoá [0, 1]; tỉ lệ theo chiều cao dòng hoặc cỡ chữ)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    segment_gap_ratio: float = Field(default=1.6, gt=0.0)
    paragraph_gap_factor: float = Field(default=1.5, gt=0.0)
    gap_abs_tol: float = Field(default=0.003, ge=0.0)
    default_gap_ratio: float = Field(default=0.35, ge=0.0)
    x_overlap_min: float = Field(default=0.5, ge=0.0, le=1.0)
    indent_tol: float = Field(default=0.012, ge=0.0)
    font_size_tol: float = Field(default=0.12, ge=0.0)
    heading_size_ratio: float = Field(default=1.15, gt=1.0)
    heading_bold_max_words: int = Field(default=20, ge=1)
    heading_max_chars: int = Field(default=400, ge=1)
    list_indent_step: float = Field(default=0.02, gt=0.0)
    overlap_min_ratio: float = Field(default=0.3, gt=0.0, le=1.0)
    overlap_leaf_cap: int = Field(default=2000, ge=2)
    # Ký hiệu đầu dòng (không phụ thuộc ngôn ngữ); `U+F0xx` là glyph Symbol/Wingdings ánh xạ vào vùng riêng.
    bullet_glyphs: tuple[str, ...] = (
        "\u2022",
        "\u25e6",
        "\u25aa",
        "\u25ab",
        "\u25cf",
        "\u25cb",
        "\u25a0",
        "\u25a1",
        "\u25fc",
        "\u25fb",
        "\u25c6",
        "\u25c7",
        "\u25ba",
        "\u25b6",
        "\u25b8",
        "\u25b9",
        "\u27a2",
        "\u27a4",
        "\u2794",
        "\u2192",
        "\u2023",
        "\u2043",
        "\u2219",
        "\u2756",
        "\u2726",
        "\u2713",
        "\u2714",
        "\u2013",
        "\u2014",
        "\u00b7",
        "\uf0a7",
        "\uf0b7",
        "\uf0d8",
        "\uf076",
        "\uf0fc",
        "-",
        "*",
    )
    enumerator_pattern: str = r"^\(?(\d{1,3}|[a-zA-Z]|[ivxIVX]{1,5})[.)]$"
    page_number: PageNumberSettings = PageNumberSettings()
    xy_cut: XyCutSettings = XyCutSettings()


@dataclass
class _Seg:
    """Một đoạn dòng: các tham chiếu theo thứ tự chữ, hộp hợp, kiểu chữ, cờ."""

    refs: list[SourceRef]
    texts: list[str]
    bbox: BBox | None
    flow: FlowKey
    header_footer: bool
    flags: set[BlockFlag]
    size: float | None
    bold: bool | None
    height: float

    @property
    def units(self) -> int:
        return sum(1 for text in self.texts for char in text if not char.isspace())

    @property
    def text(self) -> str:
        return " ".join(self.texts)


@dataclass
class _Group:
    kind: GroupKind
    segs: list[_Seg] = field(default_factory=list)
    chars: int = 0

    def add(self, seg: _Seg) -> None:
        self.chars += len(seg.text) + (1 if self.segs else 0)
        self.segs.append(seg)


def _union(boxes: Sequence[BBox]) -> BBox | None:
    if not boxes:
        return None
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


def _majority[T](pairs: Sequence[tuple[T | None, int]]) -> T | None:
    """Giá trị có tổng trọng số lớn nhất (bỏ None); hoà ⇒ giá trị gặp trước."""
    weights: dict[T, int] = {}
    for value, weight in pairs:
        if value is not None:
            weights[value] = weights.get(value, 0) + max(weight, 1)
    if not weights:
        return None
    best = max(weights.values())
    return next(value for value, weight in weights.items() if weight == best)


def _weighted_median(pairs: Sequence[tuple[float | None, int]]) -> float | None:
    """Trung vị có trọng số (giá trị thấp hơn khi rơi đúng giữa); bỏ None; không có giá trị ⇒ None."""
    items = sorted((value, max(weight, 1)) for value, weight in pairs if value is not None)
    if not items:
        return None
    cumulative = list(accumulate(weight for _, weight in items))
    return items[bisect.bisect_left(cumulative, cumulative[-1] / 2)][0]


class _PageBuilder:
    def __init__(self, context: PageContext, settings: BaseBlockSettings) -> None:
        self.context = context
        self.settings = settings
        self.geometry: PageGeometry = context.geometry
        self.index = SourceIndex(context.geometry, context.extraction)
        self.enumerator = re.compile(settings.enumerator_pattern)
        self.bullets = frozenset(settings.bullet_glyphs)
        self.body_size: float | None = None
        self.body_bold: bool | None = None
        self.max_gap = 0.0

    # ------------------------------------------------------------------ kiểu chữ

    def _bold(self, font_id: int | None) -> bool | None:
        fonts = self.geometry.fonts
        if font_id is None or font_id >= len(fonts):
            return None
        return fonts[font_id].bold

    def _style(
        self, words: Sequence[GeoWord], line_font: int | None, line_size: float | None
    ) -> tuple[float | None, bool | None]:
        weights = [(w, sum(1 for c in w.text if not c.isspace())) for w in words]
        size = _majority([(None if w.font_size_pt is None else round(w.font_size_pt, 1), n) for w, n in weights])
        bold = _majority([(self._bold(w.font_id), n) for w, n in weights])
        return (size if size is not None else line_size), (bold if bold is not None else self._bold(line_font))

    # ------------------------------------------------------------------ đoạn dòng

    def _flags(self, refs: Sequence[SourceRef]) -> set[BlockFlag]:
        flags: set[BlockFlag] = set()
        for ref in refs:
            flags |= self.index.line_flags(ref)
        return flags

    def segments(self) -> list[_Seg]:
        segs: list[_Seg] = []
        lines = self.context.extraction.lines
        for geo_idx, line in enumerate(self.geometry.lines):
            if geo_idx in self.index.edited_geo_lines:
                # Dòng thô có mảnh đã sửa luôn có ít nhất một mảnh (`SourceIndex.edited_geo_lines`).
                refs: list[SourceRef] = [ConfirmedLineRef(line_index=i) for i in line.extracted_line_indices]
                boxes = [b for i in line.extracted_line_indices if (b := lines[i].bbox) is not None]
                bbox = _union(boxes)
                size, bold = self._style(line.words, line.font_id, line.font_size_pt)
                flags = self._flags(refs)
                if bbox is None:
                    flags.add("reading_order_uncertain")
                segs.append(
                    _Seg(
                        refs,
                        [self.index.text(r) for r in refs],
                        bbox,
                        (geo_idx, 0),
                        line.header_footer,
                        flags,
                        size,
                        bold,
                        0.0 if bbox is None else bbox[3] - bbox[1],
                    )
                )
                continue
            segs.extend(self._split_line(geo_idx))
        for line_index in self.index.typed_lines:
            ref = ConfirmedLineRef(line_index=line_index)
            segs.append(
                _Seg(
                    [ref],
                    [self.index.text(ref)],
                    None,
                    self.index.flow_key(ref),
                    False,
                    self._flags([ref]) | {"reading_order_uncertain"},
                    None,
                    None,
                    0.0,
                )
            )
        return [seg for seg in segs if seg.texts]

    def _split_line(self, geo_idx: int) -> list[_Seg]:
        line = self.geometry.lines[geo_idx]
        words = line.words
        if not words:
            return []
        if all(word.bbox is None for word in words):
            refs: list[SourceRef] = [WordRef(line_idx=geo_idx, word_idx=i) for i in range(len(words))]
            size, bold = self._style(words, line.font_id, line.font_size_pt)
            return [
                _Seg(
                    refs,
                    [self.index.text(r) for r in refs],
                    None,
                    (geo_idx, 0),
                    line.header_footer,
                    self._flags(refs) | {"reading_order_uncertain"},
                    size,
                    bold,
                    0.0,
                )
            ]
        # Neo: từ có hộp kèm các từ không hộp theo sau nó trong luồng; từ không hộp đứng đầu dòng đứng trước mọi neo.
        leading: list[int] = []
        anchored: list[tuple[BBox, list[int]]] = []
        for i, word in enumerate(words):
            if word.bbox is not None:
                anchored.append((word.bbox, [i]))
            elif anchored:
                anchored[-1][1].append(i)
            else:
                leading.append(i)
        anchored.sort(key=lambda item: (item[0][0], item[1][0]))
        anchors = [group for _, group in anchored]
        boxed = [box for box, _ in anchored]
        height = max(b[3] for b in boxed) - min(b[1] for b in boxed)
        threshold = self.settings.segment_gap_ratio * height
        parts: list[list[list[int]]] = [[anchors[0]]]
        right = boxed[0][2]
        for group, box in zip(anchors[1:], boxed[1:], strict=True):
            if box[0] - right > threshold:
                parts.append([group])
            else:
                parts[-1].append(group)
            right = max(right, box[2])
        merged: list[list[list[int]]] = []
        for part in parts:
            if merged and all(words[i].text in self.bullets for group in merged[-1] for i in group):
                merged[-1].extend(part)
            else:
                merged.append(part)
        segs: list[_Seg] = []
        for position, part in enumerate(merged):
            order = (leading if position == 0 else []) + [i for group in part for i in group]
            refs = [WordRef(line_idx=geo_idx, word_idx=i) for i in order]
            seg_words = [words[i] for i in order]
            bbox = _union([b for w in seg_words if (b := w.bbox) is not None])
            size, bold = self._style(seg_words, line.font_id, line.font_size_pt)
            flags = self._flags(refs)
            if any(w.bbox is None for w in seg_words):
                flags.add("reading_order_uncertain")
            segs.append(
                _Seg(
                    refs,
                    [self.index.text(r) for r in refs],
                    bbox,
                    (geo_idx, min(order)),
                    line.header_footer,
                    flags,
                    size,
                    bold,
                    height,
                )
            )
        return segs

    # ------------------------------------------------------------------ phân loại

    def _starts_list(self, seg: _Seg) -> bool:
        first = seg.texts[0] if seg.texts else ""
        return first in self.bullets or bool(self.enumerator.match(first))

    def _candidate(self, seg: _Seg) -> bool:
        return in_margin_band(seg.bbox, self.settings.page_number) and candidate_text(
            seg.text, self.settings.page_number
        )

    # ------------------------------------------------------------------ dựng

    def build(self) -> list[Block]:
        segs = self.segments()
        boxed = [seg for seg in segs if seg.bbox is not None]
        floating = [seg for seg in segs if seg.bbox is None]
        boxes: list[BBox] = [seg.bbox for seg in boxed if seg.bbox is not None]
        items: list[_Seg | list[_Seg]] = []
        for leaf in xy_cut(boxes, self.settings.xy_cut).leaves:
            items.extend(self._resolve_leaf([boxed[i] for i in leaf]))
        body = [s for s in boxed if not s.header_footer] or boxed
        self.body_size = _weighted_median([(s.size, s.units) for s in body])
        self.body_bold = _majority([(s.bold, s.units) for s in body])
        self.max_gap = self._max_gap([item for item in items if isinstance(item, _Seg)])
        groups = self._group(items)
        groups.extend(self._group_floating(floating))
        if len(groups) > MAX_PAGE_BLOCKS:
            raise LayoutBuildError(f"trang có quá {MAX_PAGE_BLOCKS} khối")
        depths = self._list_depths(groups)
        blocks = [self._block(group, position, depths.get(id(group))) for position, group in enumerate(groups)]
        return assign_block_ids(self.context.document_id, self.context.page_number, blocks, self.index)

    def _resolve_leaf(self, leaf: list[_Seg]) -> list[_Seg | list[_Seg]]:
        """Đoạn của một lá theo (y0, x0); các đoạn chồng lấn nhau gộp thành một nhóm (sẽ là figure)."""
        if len(leaf) == 1:
            return [leaf[0]]
        if len(leaf) > self.settings.overlap_leaf_cap:
            return [leaf]
        parent = list(range(len(leaf)))

        def find(i: int) -> int:
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        for a in range(len(leaf)):
            for b in range(a + 1, len(leaf)):
                if self._overlaps(leaf[a], leaf[b]):
                    parent[find(a)] = find(b)
        components: dict[int, list[_Seg]] = {}
        for i, seg in enumerate(leaf):
            components.setdefault(find(i), []).append(seg)
        out: list[_Seg | list[_Seg]] = []
        for members in components.values():
            out.append(members[0] if len(members) == 1 else members)

        def key(item: _Seg | list[_Seg]) -> tuple[float, float, FlowKey]:
            seg = item if isinstance(item, _Seg) else item[0]
            box = seg.bbox or (0.0, 0.0, 0.0, 0.0)
            return (box[1], box[0], seg.flow)

        return sorted(out, key=key)

    def _overlaps(self, a: _Seg, b: _Seg) -> bool:
        if a.bbox is None or b.bbox is None:
            return False
        ax0, ay0, ax1, ay1 = a.bbox
        bx0, by0, bx1, by1 = b.bbox
        width = min(ax1, bx1) - max(ax0, bx0)
        height = min(ay1, by1) - max(ay0, by0)
        if width <= 0 or height <= 0:
            return False
        smaller = min((ax1 - ax0) * (ay1 - ay0), (bx1 - bx0) * (by1 - by0))
        return smaller > 0 and width * height >= self.settings.overlap_min_ratio * smaller

    def _max_gap(self, ordered: list[_Seg]) -> float:
        gaps: list[float] = []
        heights = [s.height for s in ordered if s.height > 0]
        for prev, seg in pairwise(ordered):
            if prev.bbox is None or seg.bbox is None or prev.header_footer or seg.header_footer:
                continue
            gap = seg.bbox[1] - prev.bbox[3]
            if 0 < gap < 3 * max(prev.height, seg.height) and self._x_overlap(prev.bbox, seg.bbox):
                gaps.append(gap)
        if gaps:
            median = statistics.median(gaps)
        else:
            median = self.settings.default_gap_ratio * (statistics.median(heights) if heights else 0.0)
        return self.settings.paragraph_gap_factor * median + self.settings.gap_abs_tol

    def _x_overlap(self, a: BBox, b: BBox) -> bool:
        overlap = min(a[2], b[2]) - max(a[0], b[0])
        narrower = min(a[2] - a[0], b[2] - b[0])
        return overlap > 0 and overlap >= self.settings.x_overlap_min * narrower

    def _is_heading(self, seg: _Seg) -> bool:
        if seg.header_footer or seg.units > self.settings.heading_max_chars:
            return False
        body = self.body_size
        if (
            seg.size is not None
            and body is not None
            and body > 0
            and seg.size >= body * self.settings.heading_size_ratio
        ):
            return True
        return bool(
            seg.bold is True and self.body_bold is False and len(seg.texts) <= self.settings.heading_bold_max_words
        )

    def _kind(self, seg: _Seg) -> GroupKind:
        if self._candidate(seg):
            return "candidate"
        if seg.header_footer:
            return "header_footer"
        if self._is_heading(seg):
            return "heading"
        if self._starts_list(seg):
            return "list_item"
        return "paragraph"

    def _same_style(self, a: _Seg, b: _Seg) -> bool:
        if (
            a.size is not None
            and b.size is not None
            and abs(a.size - b.size) > self.settings.font_size_tol * max(a.size, b.size)
        ):
            return False
        return a.bold is None or b.bold is None or a.bold == b.bold

    def _continues(self, group: _Group, seg: _Seg, kind: GroupKind) -> bool:
        if group.kind in ("figure", "candidate") or kind in ("candidate", "list_item"):
            return False
        if kind != group.kind and not (group.kind == "list_item" and kind == "paragraph"):
            return False
        prev = group.segs[-1]
        if prev.bbox is None or seg.bbox is None or not self._same_style(prev, seg):
            return False
        gap = seg.bbox[1] - prev.bbox[3]
        if gap > self.max_gap or gap < -0.5 * max(min(prev.height, seg.height), 0.0):
            return False
        extent = _union([s.bbox for s in group.segs if s.bbox is not None])
        if extent is None or not self._x_overlap(extent, seg.bbox):
            return False
        tol = self.settings.indent_tol
        first = group.segs[0].bbox
        if first is None:
            return False
        if group.kind == "list_item":
            # Dòng nối của mục danh sách thụt vào so với ký hiệu đầu dòng (thụt treo).
            return seg.bbox[0] >= first[0] + tol
        if group.kind == "paragraph":
            if seg.bbox[0] > prev.bbox[0] + tol and seg.bbox[0] > extent[0] + tol:
                return False
            if len(group.segs) >= 2 and seg.bbox[0] < extent[0] - tol:
                return False
            if group.chars + 1 + len(seg.text) > MAX_BLOCK_TEXT_CHARS:
                return False
        return True

    def _group(self, items: list[_Seg | list[_Seg]]) -> list[_Group]:
        groups: list[_Group] = []
        current: _Group | None = None
        for item in items:
            if isinstance(item, list):
                figure = _Group("figure")
                for seg in item:
                    figure.add(seg)
                groups.append(figure)
                current = None
                continue
            kind = self._kind(item)
            if current is not None and self._continues(current, item, kind):
                current.add(item)
                continue
            current = _Group(kind)
            current.add(item)
            groups.append(current)
        return groups

    def _group_floating(self, floating: list[_Seg]) -> list[_Group]:
        """Đoạn trôi (`bbox = null`) liền nhau theo luồng PDFium gộp thành một khối (`paragraph`/`header_footer`)."""
        groups: list[_Group] = []
        previous_line = -2
        for seg in sorted(floating, key=lambda s: s.flow):
            kind: GroupKind = "header_footer" if seg.header_footer else "paragraph"
            current = groups[-1] if groups else None
            adjacent = seg.flow[0] == previous_line + 1
            fits = current is not None and current.chars + 1 + len(seg.text) <= MAX_BLOCK_TEXT_CHARS
            if current is not None and current.kind == kind and adjacent and (fits or kind != "paragraph"):
                current.add(seg)
            else:
                group = _Group(kind)
                group.add(seg)
                groups.append(group)
            previous_line = seg.flow[0]
        return groups

    def _list_depths(self, groups: list[_Group]) -> dict[int, int]:
        lefts = sorted({round(b[0], 4) for g in groups if g.kind == "list_item" and (b := g.segs[0].bbox) is not None})
        levels: list[float] = []
        for left in lefts:
            if not levels or left - levels[-1] > self.settings.list_indent_step:
                levels.append(left)
        depths: dict[int, int] = {}
        for group in groups:
            if group.kind != "list_item":
                continue
            box = group.segs[0].bbox
            if box is None:
                depths[id(group)] = 0
                continue
            depth = sum(1 for level in levels if level <= round(box[0], 4) + 1e-9) - 1
            depths[id(group)] = min(max(depth, 0), 8)
        return depths

    def _block(self, group: _Group, position: int, depth: int | None) -> Block:
        refs = [ref for seg in group.segs for ref in seg.refs]
        bbox = _union([s.bbox for s in group.segs if s.bbox is not None])
        flags: set[BlockFlag] = set()
        for seg in group.segs:
            flags |= seg.flags
        if bbox is None:
            flags.discard("crop_pending")
        kind: BlockType
        if group.kind == "candidate":
            kind = "header_footer" if group.segs[0].header_footer else "paragraph"
        elif group.kind == "figure":
            kind = "figure"
        else:
            kind = group.kind
        data: dict[str, Any] = {
            "block_id": _PLACEHOLDER_ID,
            "document_id": self.context.document_id,
            "page_number": self.context.page_number,
            "type": kind,
            "bbox": bbox,
            "source": "text_layer",
            "reading_order": position,
        }
        if kind == "figure":
            flags |= {"layout_uncertain", "figure_label_text"}
            if bbox is not None:
                flags.add("crop_pending")
            data |= {"label_line_refs": refs, "labels": [seg.text for seg in group.segs]}
        else:
            text = " ".join(seg.text for seg in group.segs)
            if len(text) > MAX_BLOCK_TEXT_CHARS:
                raise LayoutBuildError(f"khối {kind} dài quá {MAX_BLOCK_TEXT_CHARS} ký tự")
            data |= {"line_refs": refs, "text": text}
        if kind == "heading":
            data["level"] = 1
        if kind == "list_item":
            data["list_depth"] = depth if depth is not None else 0
        data["flags"] = sorted(flags)
        return Block.model_validate(data)


class BaseBlockStage:
    """Stage `base_blocks` (X05): khối cơ sở, `heading` nhận `level = 1` sơ bộ."""

    name: str = "base_blocks"

    def __init__(self, settings: BaseBlockSettings | None = None) -> None:
        self.settings = settings or BaseBlockSettings()

    def fingerprint(self) -> dict[str, Any]:
        return {"name": self.name, "version": BASE_BLOCKS_STAGE_VERSION, "settings": self.settings.model_dump()}

    def run(self, context: PageContext, blocks: list[Block]) -> list[Block]:
        """Dựng khối cơ sở từ geometry (bỏ qua `blocks` đầu vào: stage này luôn đứng đầu)."""
        return _PageBuilder(context, self.settings).build()
