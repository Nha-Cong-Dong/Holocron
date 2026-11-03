"""XY-cut đệ quy, thứ tự đọc và id khối (ADR-010 mục 3, 11, 13, 15).

`xy_cut` chia tập hộp theo khe trống của hình chiếu: ưu tiên khe dọc (cột) đủ rộng, rồi khe ngang lớn nhất, rồi khe
dọc hẹp; mọi khe gần bằng khe lớn nhất (trong `gap_tie_tol`) được cắt cùng lúc. Trần tất định (không đo thời gian):
`xy_cut_max_depth` (độ sâu) và `xy_cut_max_nodes` (số nút con đã tách); vượt ⇒ `XyCutLimitError` (trang `LAYOUT_FAILED`).

Stage `reading_order` (LUÔN cuối danh sách stage từng trang) xếp khối có hộp bằng XY-cut; khối `bbox = null` đứng
ngay sau khối có hộp chứa tham chiếu kề trước nó theo luồng PDFium (không có ⇒ đầu trang); rồi gán `reading_order`
liên tục 0..n-1 và `block_id` theo nội dung nguồn.
"""

from __future__ import annotations

import bisect
import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from ..domain.blocks import Block
from ..domain.documents import BBox
from .extraction_ledger import FlowKey, SourceIndex, block_refs

if TYPE_CHECKING:
    from .block_pipeline import PageContext

READING_ORDER_STAGE_VERSION = 1


class XyCutSettings(BaseModel):
    """Ngưỡng của XY-cut (toạ độ chuẩn hoá [0, 1] theo khổ trang)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    xy_cut_max_depth: int = Field(default=48, ge=1)
    xy_cut_max_nodes: int = Field(default=20_000, ge=1)
    # Khe dọc tối thiểu để coi là khe cột (ưu tiên trước khe ngang).
    column_gap_min: float = Field(default=0.012, ge=0.0, le=1.0)
    # Các khe chênh với khe lớn nhất không quá mức này được cắt cùng lúc (giữ độ sâu thấp với dòng đều nhau).
    gap_tie_tol: float = Field(default=0.002, ge=0.0, le=1.0)
    # Co hộp theo chiều dọc trước khi chiếu (phần đầu/đuôi chữ của hai dòng sát nhau chạm nhau): tỉ lệ chiều cao,
    # tối đa `y_core_shrink_max`.
    y_core_shrink: float = Field(default=0.15, ge=0.0, lt=0.5)
    y_core_shrink_max: float = Field(default=0.004, ge=0.0, le=1.0)


class XyCutLimitError(ValueError):
    """XY-cut vượt `xy_cut_max_depth` hoặc `xy_cut_max_nodes`."""


@dataclass(frozen=True)
class XyCutResult:
    """Lá theo thứ tự đọc (mỗi lá là chỉ số hộp, đã sắp theo (y0, x0, chỉ số)) và số nút con đã tách."""

    leaves: list[list[int]]
    nodes: int


def _core(box: BBox, settings: XyCutSettings) -> tuple[float, float]:
    shrink = min((box[3] - box[1]) * settings.y_core_shrink, settings.y_core_shrink_max)
    return box[1] + shrink, box[3] - shrink


def _gaps(spans: Sequence[tuple[float, float, int]]) -> list[tuple[float, int]]:
    """Khe của hình chiếu: (độ rộng khe, vị trí cắt trong danh sách đã sắp theo đầu đoạn)."""
    gaps: list[tuple[float, int]] = []
    end = spans[0][1]
    for position in range(1, len(spans)):
        start, stop, _ = spans[position]
        if start > end:
            gaps.append((start - end, position))
        end = max(end, stop)
    return gaps


def _split(spans: list[tuple[float, float, int]], gaps: list[tuple[float, int]], tol: float) -> list[list[int]]:
    widest = max(width for width, _ in gaps)
    cuts = [position for width, position in gaps if width >= widest - tol]
    groups: list[list[int]] = []
    previous = 0
    for cut in [*cuts, len(spans)]:
        groups.append([index for _, _, index in spans[previous:cut]])
        previous = cut
    return groups


def xy_cut(boxes: Sequence[BBox], settings: XyCutSettings) -> XyCutResult:
    """XY-cut đệ quy trên `boxes`; ném `XyCutLimitError` khi vượt trần. Tất định: hoà ⇒ theo chỉ số."""
    leaves: list[list[int]] = []
    nodes = 0

    def visit(indices: list[int], depth: int) -> None:
        nonlocal nodes
        if len(indices) <= 1:
            leaves.append(indices)
            return
        xs = sorted((boxes[i][0], boxes[i][2], i) for i in indices)
        ys = sorted((*_core(boxes[i], settings), i) for i in indices)
        x_gaps, y_gaps = _gaps(xs), _gaps(ys)
        widest_x = max((w for w, _ in x_gaps), default=0.0)
        if x_gaps and widest_x >= settings.column_gap_min:
            groups = _split(xs, x_gaps, settings.gap_tie_tol)
        elif y_gaps:
            groups = _split(ys, y_gaps, settings.gap_tie_tol)
        elif x_gaps:
            groups = _split(xs, x_gaps, settings.gap_tie_tol)
        else:
            leaves.append(sorted(indices, key=lambda i: (boxes[i][1], boxes[i][0], i)))
            return
        if depth >= settings.xy_cut_max_depth:
            raise XyCutLimitError("XY-cut vượt xy_cut_max_depth")
        nodes += len(groups)
        if nodes > settings.xy_cut_max_nodes:
            raise XyCutLimitError("XY-cut vượt xy_cut_max_nodes")
        for group in groups:
            visit(group, depth + 1)

    if boxes:
        visit(list(range(len(boxes))), 0)
    return XyCutResult(leaves=leaves, nodes=nodes)


def order_positions(
    boxes: Sequence[BBox | None], ref_flows: Sequence[Sequence[FlowKey]], settings: XyCutSettings
) -> list[int]:
    """Thứ tự đọc của các phần tử (khối hay đoạn dòng): phần tử có hộp theo XY-cut; phần tử `bbox = null` đứng ngay
    sau phần tử có hộp chứa tham chiếu kề trước nó theo luồng PDFium (ADR-010 mục 11), không có ⇒ đầu trang; nhiều
    phần tử cùng chỗ ⇒ theo luồng PDFium."""
    boxed = [i for i, box in enumerate(boxes) if box is not None]
    boxed_boxes: list[BBox] = [box for box in boxes if box is not None]
    sequence = [boxed[j] for leaf in xy_cut(boxed_boxes, settings).leaves for j in leaf]
    anchors: list[tuple[FlowKey, int]] = sorted((flow, i) for i in boxed for flow in ref_flows[i])
    anchor_keys = [flow for flow, _ in anchors]
    floating = sorted(
        (i for i, box in enumerate(boxes) if box is None),
        key=lambda i: (min(ref_flows[i], default=(1 << 62, 0)), i),
    )
    after: dict[int, list[int]] = {}
    head: list[int] = []
    for i in floating:
        first = min(ref_flows[i], default=None)
        position = 0 if first is None else bisect.bisect_left(anchor_keys, first)
        if position == 0:
            head.append(i)
        else:
            after.setdefault(anchors[position - 1][1], []).append(i)
    result = list(head)
    for i in sequence:
        result.append(i)
        result.extend(after.get(i, []))
    return result


# --------------------------------------------------------------------------- #
# Id khối (ADR-010 mục 3 (b''))
# --------------------------------------------------------------------------- #


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _quantized(box: BBox | None) -> list[int]:
    return [round(1000 * v) for v in box] if box is not None else [0, 0, 0, 0]


def block_id_for(document_id: str, page_number: int, block: Block, index: SourceIndex) -> str | None:
    """Id theo nội dung nguồn; None khi khối không có tham chiếu nào (dùng id dự phòng)."""
    refs = block_refs(block)
    if not refs:
        return None
    keys: list[list[Any]] = []
    for ref in refs:
        if ref.kind == "confirmed_line":
            keys.append([ref.kind, ref.line_index, 0, index.line_sha256(ref)])
        else:
            keys.append([ref.kind, ref.line_idx, ref.word_idx, index.line_sha256(ref)])
    keys.sort()
    digest = hashlib.sha256(_canonical(["block", document_id, page_number, keys])).hexdigest()
    return "blk_" + digest[:16]


def assign_block_ids(document_id: str, page_number: int, blocks: Sequence[Block], index: SourceIndex) -> list[Block]:
    """Gán `block_id` cho mọi khối: theo nội dung nguồn; khối không có từ nào dùng id dự phòng
    `["block_noref", document_id, page_number, type, bbox × 1000 làm tròn, region_index]`."""
    ids: list[str | None] = [block_id_for(document_id, page_number, block, index) for block in blocks]
    norefs = sorted(
        (i for i, value in enumerate(ids) if value is None),
        key=lambda i: (
            _quantized(blocks[i].bbox)[1],
            _quantized(blocks[i].bbox)[0],
            _quantized(blocks[i].bbox)[3],
            _quantized(blocks[i].bbox)[2],
            blocks[i].type,
            i,
        ),
    )
    for region_index, i in enumerate(norefs):
        key = ["block_noref", document_id, page_number, blocks[i].type, _quantized(blocks[i].bbox), region_index]
        ids[i] = "blk_" + hashlib.sha256(_canonical(key)).hexdigest()[:16]
    return [block.model_copy(update={"block_id": value}) for block, value in zip(blocks, ids, strict=True)]


class ReadingOrderStage:
    """Stage `reading_order` (X05): LUÔN cuối danh sách stage từng trang; gán `reading_order` và `block_id`."""

    name: str = "reading_order"

    def __init__(self, settings: XyCutSettings | None = None) -> None:
        self.settings = settings or XyCutSettings()

    def fingerprint(self) -> dict[str, Any]:
        return {"name": self.name, "version": READING_ORDER_STAGE_VERSION, "settings": self.settings.model_dump()}

    def run(self, context: PageContext, blocks: list[Block]) -> list[Block]:
        index = SourceIndex(context.geometry, context.extraction)
        flows = [[index.flow_key(ref) for ref in block_refs(block)] for block in blocks]
        order = order_positions([block.bbox for block in blocks], flows, self.settings)
        ordered = [blocks[i].model_copy(update={"reading_order": position}) for position, i in enumerate(order)]
        return assign_block_ids(context.document_id, context.page_number, ordered, index)
