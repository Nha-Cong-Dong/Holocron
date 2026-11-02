"""Khối `code` từ font đơn cách, dựng lại thụt lề từ toạ độ x (ADR-010 mục 7, 13; spec §5.15).

Hàm thuần công khai `build_code_block` nhận một tập tham chiếu từ (`WordRef`) và trả khối `code` hoặc `None`
(ô "C" của ma trận `set_type`); `CodeBlockStage` dùng lại chính hàm đó. Tín hiệu chỉ là hình học và kiểu chữ:
- chính: đa số ký tự không trắng thuộc font có cờ `monospace` (X04);
- phụ: bước x của các từ đơn cách đo được đều nhau quanh trung vị.

Văn bản khối (mục 7): dòng nối bằng `\\n`; số dấu cách đầu dòng = round((x0 dòng − x0 khối) / bề rộng ký tự); giữa hai
từ = max(1, round(khoảng cách / bề rộng ký tự)); bề rộng ký tự = trung vị của (x1 − x0) / số code point NFC của từ trên
các từ đơn cách có hộp. Khoảng trắng tái dựng không vào ledger. Không chắc (lẫn font, từ không hộp, bước x không đều,
dòng chồng, khoảng cách bị chặn trần) ⇒ vẫn là `code` kèm cờ `code_layout_uncertain`; không đủ tín hiệu ⇒ `None`
(giữ khối cũ). Từ không hộp đứng sau từ có hộp liền trước nó theo luồng PDFium, khối mang thêm cờ
`reading_order_uncertain`. Làm tròn: `floor(v + 0.5)` (tất định, không làm tròn chẵn).
"""

from __future__ import annotations

import math
import statistics
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from ..domain.blocks import MAX_BLOCK_TEXT_CHARS, Block, BlockFlag, SourceRef, WordRef
from ..domain.documents import BBox, GeoWord
from .block_builder import LayoutBuildError
from .extraction_ledger import LedgerError, SourceIndex

if TYPE_CHECKING:
    from .block_pipeline import PageContext

CODE_BLOCKS_STAGE_VERSION = 1
_PLACEHOLDER_ID = "blk_0000000000000000"
_MERGEABLE_TYPES = frozenset({"paragraph", "list_item"})
_UNCERTAIN: BlockFlag = "code_layout_uncertain"


class CodeBlockSettings(BaseModel):
    """Ngưỡng của stage `code_blocks` (toạ độ chuẩn hoá; tỉ lệ theo chiều cao từ hoặc bề rộng ký tự)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Tỉ lệ ký tự không trắng thuộc font đơn cách tối thiểu để nhận code.
    min_monospace_ratio: float = Field(default=0.6, gt=0.0, le=1.0)
    # Số từ đơn cách có hộp tối thiểu để đo bề rộng ký tự.
    min_measured_words: int = Field(default=3, ge=1)
    # Sai lệch tương đối cho phép của bước x quanh trung vị.
    step_rel_tol: float = Field(default=0.15, gt=0.0)
    # Tỉ lệ từ có bước đều: dưới `min_uniform_ratio` ⇒ không nhận; dưới `flag_uniform_ratio` ⇒ nhận kèm cờ.
    min_uniform_ratio: float = Field(default=0.5, ge=0.0, le=1.0)
    flag_uniform_ratio: float = Field(default=0.9, ge=0.0, le=1.0)
    # Từ cùng một hàng khi tâm dọc lệch ≤ hệ số này × trung vị chiều cao từ.
    row_tol_ratio: float = Field(default=0.5, gt=0.0)
    # Hai hàng liên tiếp chồng dọc quá tỉ lệ này (theo hàng thấp hơn) ⇒ dòng chồng.
    row_overlap_max: float = Field(default=0.5, ge=0.0, le=1.0)
    # Hai từ chồng ngang quá hệ số này × bề rộng ký tự ⇒ dòng chồng.
    word_overlap_max_chars: float = Field(default=0.5, ge=0.0)
    # Gom các khối đơn cách cạnh nhau (stage): hai khối chỉ gộp khi hộp nới theo dọc `merge_y_factor` × chiều cao từ
    # chạm nhau VÀ khoảng trống ngang giữa hai hộp ≤ `merge_x_factor` × chiều cao từ (hai listing đặt cạnh nhau cách
    # xa hơn thì là hai khối riêng).
    merge_y_factor: float = Field(default=1.5, ge=0.0)
    merge_x_factor: float = Field(default=6.0, ge=0.0)
    # Trần số khối ứng viên gộp trên một trang; vượt ⇒ bỏ qua gộp, giữ khối cũ kèm cờ (chặn O(n²)).
    max_merge_candidates: int = Field(default=200, ge=1)
    # Trần số dấu cách tái dựng một chỗ (vượt ⇒ cắt cụt kèm cờ).
    max_spaces: int = Field(default=120, ge=1)


DEFAULT_CODE_SETTINGS = CodeBlockSettings()


def _round_half_up(value: float) -> int:
    return math.floor(value + 0.5)


@dataclass(frozen=True)
class _W:
    ref: WordRef
    word: GeoWord
    box: BBox | None
    text: str
    mono: bool


def _resolve(index: SourceIndex, context: PageContext, refs: Sequence[SourceRef]) -> list[_W] | None:
    geometry = context.geometry
    out: list[_W] = []
    seen: set[tuple[int, int]] = set()
    for ref in refs:
        if not isinstance(ref, WordRef):
            return None
        key = (ref.line_idx, ref.word_idx)
        if key in seen:
            return None
        seen.add(key)
        try:
            index.validate(ref)
        except LedgerError:
            return None
        word = geometry.lines[ref.line_idx].words[ref.word_idx]
        fonts = geometry.fonts
        mono = word.font_id is not None and word.font_id < len(fonts) and fonts[word.font_id].monospace
        out.append(_W(ref, word, word.bbox, unicodedata.normalize("NFC", word.text), mono))
    return out or None


def _nonws(text: str) -> int:
    return sum(1 for char in text if not char.isspace())


def _mono_ratio(words: Sequence[_W]) -> float:
    total = sum(_nonws(w.text) for w in words)
    if total == 0:
        return 0.0
    return sum(_nonws(w.text) for w in words if w.mono) / total


def _yc(box: BBox) -> float:
    return (box[1] + box[3]) / 2


def _rows(words: Sequence[_W], settings: CodeBlockSettings, height: float) -> list[list[_W]]:
    """Chia từ thành hàng theo tâm dọc (từ có hộp). Từ không hộp theo luồng PDFium (ADR-010 mục 7, 11): nằm cùng hàng
    với từ có hộp liền trước nó trong dòng thô (đứng đầu dòng thô: hàng trên cùng của dòng đó); dòng thô không có từ có
    hộp nào thành hàng riêng đặt ngay sau hàng của từ có hộp liền trước nó theo luồng (không có thì đứng đầu)."""
    boxed = sorted(
        ((w, w.box) for w in words if w.box is not None),
        key=lambda item: (_yc(item[1]), item[1][0]),
    )
    tol = settings.row_tol_ratio * height
    rows: list[list[_W]] = []
    centers: list[float] = []
    row_of: dict[tuple[int, int], int] = {}
    for w, box in boxed:
        if rows and abs(_yc(box) - centers[-1]) <= tol:
            rows[-1].append(w)
            centers[-1] = statistics.fmean(_yc(item.box) for item in rows[-1] if item.box)
        else:
            rows.append([w])
            centers.append(_yc(box))
        row_of[(w.ref.line_idx, w.ref.word_idx)] = len(rows) - 1
    top_row_of_line: dict[int, int] = {}
    for (line_idx, _), position in row_of.items():
        top_row_of_line[line_idx] = min(top_row_of_line.get(line_idx, position), position)
    prev_row_in_line: dict[int, int] = {}
    last_row = -1
    boxless_lines: dict[tuple[int, int], list[_W]] = {}
    for w in sorted(words, key=lambda item: (item.ref.line_idx, item.ref.word_idx)):
        line_idx = w.ref.line_idx
        found = row_of.get((line_idx, w.ref.word_idx))
        if found is not None:
            prev_row_in_line[line_idx] = last_row = found
        elif line_idx in top_row_of_line:
            rows[prev_row_in_line.get(line_idx, top_row_of_line[line_idx])].append(w)
        else:
            boxless_lines.setdefault((last_row, line_idx), []).append(w)
    out: list[list[_W]] = [rest for (anchor, _), rest in boxless_lines.items() if anchor == -1]
    for position, row in enumerate(rows):
        out.append(row)
        out.extend(rest for (anchor, _), rest in boxless_lines.items() if anchor == position)
    return out


def _order_row(row: Sequence[_W]) -> list[_W]:
    """Từ có hộp theo x0; từ không hộp đứng ngay sau từ có hộp liền trước trong luồng dòng thô."""

    def key(w: _W) -> tuple[float, int, int]:
        if w.box is not None:
            return (w.box[0], w.ref.line_idx, w.ref.word_idx)
        anchor_x: float | None = None
        anchor_idx = -1
        for other in row:
            if other.box is None or other.ref.line_idx != w.ref.line_idx:
                continue
            if anchor_idx < other.ref.word_idx < w.ref.word_idx:
                anchor_idx, anchor_x = other.ref.word_idx, other.box[0]
        return (-math.inf if anchor_x is None else anchor_x, w.ref.line_idx, w.ref.word_idx)

    return sorted(row, key=key)


def _build(
    context: PageContext, index: SourceIndex, refs: Sequence[SourceRef], settings: CodeBlockSettings
) -> Block | None:
    words = _resolve(index, context, refs)
    if words is None or _mono_ratio(words) < settings.min_monospace_ratio:
        return None
    steps = [(w.box[2] - w.box[0]) / len(w.text) for w in words if w.mono and w.box is not None and w.text]
    steps = [s for s in steps if s > 0]
    if len(steps) < settings.min_measured_words:
        return None
    char_w = statistics.median(steps)
    uniform = sum(1 for s in steps if abs(s - char_w) <= settings.step_rel_tol * char_w) / len(steps)
    if uniform < settings.min_uniform_ratio:
        return None
    boxes: list[BBox] = [w.box for w in words if w.box is not None]
    height = statistics.median(b[3] - b[1] for b in boxes)
    if height <= 0:
        return None
    flags: set[BlockFlag] = set()
    if uniform < settings.flag_uniform_ratio or any(not w.mono for w in words) or len(boxes) < len(words):
        flags.add(_UNCERTAIN)
    if len(boxes) < len(words):
        flags.add("reading_order_uncertain")
    rows = _rows(words, settings, height)
    x_left = min(b[0] for b in boxes)
    lines: list[str] = []
    ordered: list[WordRef] = []
    previous_band: tuple[float, float] | None = None
    for row in rows:
        row_sorted = _order_row(row)
        parts: list[str] = []
        first_box = next((w.box for w in row_sorted if w.box is not None), None)
        if first_box is not None:
            lead = _round_half_up((first_box[0] - x_left) / char_w)
            if lead > settings.max_spaces:
                lead = settings.max_spaces
                flags.add(_UNCERTAIN)
            parts.append(" " * lead)
        prev: _W | None = None
        for w in row_sorted:
            if prev is not None:
                spaces = 1
                if prev.box is not None and w.box is not None:
                    gap = w.box[0] - prev.box[2]
                    if gap < -settings.word_overlap_max_chars * char_w:
                        flags.add(_UNCERTAIN)
                    spaces = max(1, _round_half_up(gap / char_w))
                    if spaces > settings.max_spaces:
                        spaces = settings.max_spaces
                        flags.add(_UNCERTAIN)
                parts.append(" " * spaces)
            parts.append(w.text)
            ordered.append(w.ref)
            prev = w
        lines.append("".join(parts))
        row_boxes = [w.box for w in row_sorted if w.box is not None]
        if row_boxes:
            band = (min(b[1] for b in row_boxes), max(b[3] for b in row_boxes))
            if previous_band is not None:
                overlap = min(previous_band[1], band[1]) - max(previous_band[0], band[0])
                if overlap > settings.row_overlap_max * min(previous_band[1] - previous_band[0], band[1] - band[0]):
                    flags.add(_UNCERTAIN)
            previous_band = band
    text = "\n".join(lines)
    if len(text) > MAX_BLOCK_TEXT_CHARS:
        raise LayoutBuildError(f"khối code dài quá {MAX_BLOCK_TEXT_CHARS} ký tự")
    bbox = (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))
    for ref in ordered:
        flags |= index.line_flags(ref)
    return Block.model_validate(
        {
            "block_id": _PLACEHOLDER_ID,
            "document_id": context.document_id,
            "page_number": context.page_number,
            "type": "code",
            "bbox": bbox,
            "text": text,
            "line_refs": ordered,
            "flags": sorted(flags),
            "source": "text_layer",
            "reading_order": 0,
        }
    )


def build_code_block(
    context: PageContext, line_refs: Sequence[SourceRef], settings: CodeBlockSettings = DEFAULT_CODE_SETTINGS
) -> Block | None:
    """Khối `code` từ tập tham chiếu từ, hoặc `None` khi không nhận (tham chiếu không phải `WordRef`, không giải được,
    đa số ký tự không phải font đơn cách, không đủ từ đo được, bước x quá lệch hoặc văn bản dài quá
    `MAX_BLOCK_TEXT_CHARS`). Ô "C" của ma trận `set_type`; stage vẫn báo `LAYOUT_FAILED` khi khối vượt trần (mục 7)."""
    try:
        return _build(context, SourceIndex(context.geometry, context.extraction), line_refs, settings)
    except LayoutBuildError:
        return None


class CodeBlockStage:
    """Stage `code_blocks` (X06): gom khối `paragraph`/`list_item` đơn cách kề nhau thành khối `code`."""

    name: str = "code_blocks"

    def __init__(self, settings: CodeBlockSettings | None = None) -> None:
        self.settings = settings or CodeBlockSettings()

    def fingerprint(self) -> dict[str, Any]:
        return {"name": self.name, "version": CODE_BLOCKS_STAGE_VERSION, "settings": self.settings.model_dump()}

    def run(self, context: PageContext, blocks: list[Block]) -> list[Block]:
        settings = self.settings
        index = SourceIndex(context.geometry, context.extraction)
        grown: dict[int, BBox] = {}
        heights: dict[int, float] = {}
        for position, block in enumerate(blocks):
            if block.type not in _MERGEABLE_TYPES or block.bbox is None:
                continue
            words = _resolve(index, context, block.line_refs)
            if words is None or _mono_ratio(words) < settings.min_monospace_ratio:
                continue
            word_heights = [w.box[3] - w.box[1] for w in words if w.box is not None]
            if not word_heights:
                continue
            height = statistics.median(word_heights)
            x0, y0, x1, y1 = block.bbox
            heights[position] = height
            grown[position] = (x0, y0 - settings.merge_y_factor * height, x1, y1 + settings.merge_y_factor * height)
        candidates = list(grown)
        if len(candidates) > settings.max_merge_candidates:
            return [_flag_uncertain(block) if p in grown else block for p, block in enumerate(blocks)]
        parent = {p: p for p in candidates}

        def find(p: int) -> int:
            while parent[p] != p:
                parent[p] = parent[parent[p]]
                p = parent[p]
            return p

        for i, a in enumerate(candidates):
            for b in candidates[i + 1 :]:
                if _adjacent(grown, heights, a, b, settings):
                    parent[find(b)] = find(a)
        clusters: dict[int, list[int]] = {}
        for p in candidates:
            clusters.setdefault(find(p), []).append(p)
        replaced: dict[int, Block] = {}
        removed: set[int] = set()
        for members in clusters.values():
            code = _build(context, index, [ref for p in members for ref in blocks[p].line_refs], settings)
            if code is None:
                continue
            replaced[min(members)] = code
            removed.update(m for m in members if m != min(members))
        return [replaced.get(p, block) for p, block in enumerate(blocks) if p not in removed]


def _flag_uncertain(block: Block) -> Block:
    """Giữ nguyên khối cũ, chỉ thêm cờ `code_layout_uncertain` (không gộp được vì quá nhiều ứng viên)."""
    return Block.model_validate({**block.model_dump(), "flags": [*block.flags, _UNCERTAIN]})


def _adjacent(grown: dict[int, BBox], heights: dict[int, float], a: int, b: int, settings: CodeBlockSettings) -> bool:
    """Hai khối ứng viên thuộc cùng một listing khi hộp nới theo dọc chạm nhau và khoảng trống ngang đủ nhỏ."""
    ga, gb = grown[a], grown[b]
    if not (ga[1] <= gb[3] and gb[1] <= ga[3]):
        return False
    gap = max(ga[0] - gb[2], gb[0] - ga[2])
    return gap <= settings.merge_x_factor * max(heights[a], heights[b])
