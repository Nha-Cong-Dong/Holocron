"""XY-cut, thứ tự đọc khi thiếu hộp và id khối (ADR-010 mục 3, 11, 15; `services/reading_order.py`)."""

from __future__ import annotations

import pytest

from holocron_core.domain.blocks import Block
from holocron_core.domain.documents import BBox
from holocron_core.services.extraction_ledger import SourceIndex
from holocron_core.services.reading_order import (
    XyCutLimitError,
    XyCutSettings,
    assign_block_ids,
    block_id_for,
    order_positions,
    xy_cut,
)
from tests.block_samples import DOC_ID, extraction_for, geo_line, geometry


def _flatten(leaves: list[list[int]]) -> list[int]:
    return [i for leaf in leaves for i in leaf]


def _two_columns() -> list[BBox]:
    boxes: list[BBox] = [(0.1, 0.05, 0.9, 0.08)]  # tiêu đề vắt qua hai cột
    for row in range(4):
        y = 0.12 + row * 0.02
        boxes.append((0.1, y, 0.45, y + 0.012))
    for row in range(4):
        y = 0.12 + row * 0.02
        boxes.append((0.55, y, 0.9, y + 0.012))
    return boxes


def test_xy_cut_reads_heading_then_left_then_right_column() -> None:
    result = xy_cut(_two_columns(), XyCutSettings())
    assert _flatten(result.leaves) == list(range(9))
    assert result.nodes > 0


def test_xy_cut_depth_limit_raises() -> None:
    with pytest.raises(XyCutLimitError):
        xy_cut(_two_columns(), XyCutSettings(xy_cut_max_depth=1))


def test_xy_cut_node_limit_raises() -> None:
    with pytest.raises(XyCutLimitError):
        xy_cut(_two_columns(), XyCutSettings(xy_cut_max_nodes=3))


def test_xy_cut_overlapping_boxes_share_a_leaf_sorted_by_position() -> None:
    boxes: list[BBox] = [(0.2, 0.2, 0.5, 0.3), (0.1, 0.1, 0.4, 0.25)]
    assert xy_cut(boxes, XyCutSettings()).leaves == [[1, 0]]
    assert xy_cut([], XyCutSettings()).leaves == []


def test_xy_cut_on_50k_words_is_bounded_and_deterministic() -> None:
    """Trang 50 000 từ rời nhau: số nút ≤ `xy_cut_max_nodes` hoặc ném lỗi trần — luôn như nhau, không đo thời gian."""
    boxes: list[BBox] = []
    for row in range(250):
        for col in range(200):
            x, y = col * 0.005, row * 0.004
            boxes.append((x, y, x + 0.003, y + 0.002))
    settings = XyCutSettings()
    outcomes: list[str] = []
    for _ in range(2):
        try:
            result = xy_cut(boxes, settings)
        except XyCutLimitError:
            outcomes.append("limit")
        else:
            assert result.nodes <= settings.xy_cut_max_nodes
            outcomes.append(f"nodes={result.nodes}")
    assert outcomes[0] == outcomes[1]
    small = boxes[:5000]
    result = xy_cut(small, XyCutSettings(xy_cut_max_nodes=50_000))
    assert result.nodes <= 50_000 and sorted(_flatten(result.leaves)) == list(range(5000))


def test_order_positions_places_boxless_after_previous_boxed_in_flow() -> None:
    boxes: list[BBox | None] = [None, (0.1, 0.5, 0.9, 0.6), None, (0.1, 0.1, 0.9, 0.2), None]
    flows = [[(0, 0)], [(1, 0)], [(2, 0)], [(3, 0)], [(9, 0)]]
    # Hộp: khối 3 (trên) rồi khối 1; khối 0 đứng đầu luồng ⇒ đầu trang; khối 2 sau khối 1; khối 4 sau khối 3.
    assert order_positions(boxes, flows, XyCutSettings()) == [0, 3, 4, 1, 2]


def test_order_positions_all_boxless_follows_pdfium_order() -> None:
    boxes: list[BBox | None] = [None, None, None]
    flows = [[(2, 0)], [(0, 0)], [(1, 0)]]
    assert order_positions(boxes, flows, XyCutSettings()) == [1, 2, 0]


def _block(kind: str, bbox: BBox | None, **extra: object) -> Block:
    data: dict[str, object] = {
        "block_id": "blk_0000000000000000",
        "document_id": DOC_ID,
        "page_number": 1,
        "type": kind,
        "bbox": bbox,
        "source": "text_layer",
        "reading_order": 0,
    }
    data.update(extra)
    return Block.model_validate(data)


def test_fallback_ids_for_blocks_without_refs_are_unique_and_stable() -> None:
    geo = geometry([geo_line("alpha beta", 0.1, 0.1)])
    index = SourceIndex(geo, extraction_for(geo))
    same_box = (0.1, 0.3, 0.5, 0.5)
    blocks = [
        _block("figure", same_box, flags=["crop_pending"]),
        _block("figure", same_box, flags=["crop_pending"]),
        _block("table", same_box, cells=[], flags=["crop_pending"]),
        _block(
            "paragraph",
            (0.1, 0.1, 0.3, 0.112),
            text="alpha beta",
            line_refs=[{"kind": "word", "line_idx": 0, "word_idx": 0}, {"kind": "word", "line_idx": 0, "word_idx": 1}],
        ),
    ]
    first = assign_block_ids(DOC_ID, 1, blocks, index)
    second = assign_block_ids(DOC_ID, 1, list(reversed(blocks)), index)
    ids = [b.block_id for b in first]
    assert len(set(ids)) == 4
    assert {b.block_id for b in second} == set(ids)
    other_page = assign_block_ids(DOC_ID, 2, blocks[:1], index)
    assert other_page[0].block_id not in ids


def test_block_id_does_not_depend_on_line_ref_order() -> None:
    geo = geometry([geo_line("alpha beta gamma", 0.1, 0.1)])
    index = SourceIndex(geo, extraction_for(geo))
    refs = [{"kind": "word", "line_idx": 0, "word_idx": i} for i in range(3)]
    forward = _block("paragraph", (0.1, 0.1, 0.4, 0.112), text="alpha beta gamma", line_refs=refs)
    backward = _block("paragraph", (0.1, 0.1, 0.4, 0.112), text="alpha beta gamma", line_refs=list(reversed(refs)))
    first = block_id_for(DOC_ID, 1, forward, index)
    assert first is not None and first == block_id_for(DOC_ID, 1, backward, index)
    assert first != block_id_for(DOC_ID, 2, forward, index)
