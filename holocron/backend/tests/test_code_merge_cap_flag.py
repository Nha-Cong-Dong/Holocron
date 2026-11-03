"""Cờ `code_layout_uncertain` khi trang vượt `max_merge_candidates` (SPEC §5.15; quyết định 2026-11-03).

Cờ nghĩa thêm: "trang vượt trần ứng viên gộp, khối có thể là code chưa gộp". Hiện chưa có đường nào trong
`holocron_core` đọc cờ KHỐI để quyết định review/blocking (cờ khối chỉ dùng cho `Block`); các test dưới đây khoá hành vi
ở mức hàm công khai: cờ không làm layout hỏng, không thêm cờ khác, không đổi kiểu/văn bản/tham chiếu dòng của khối.
"""

from __future__ import annotations

import pytest

from holocron_core.domain.blocks import Block
from holocron_core.services.block_builder import BaseBlockStage
from holocron_core.services.code_blocks import CodeBlockSettings, CodeBlockStage
from holocron_core.services.reading_order import ReadingOrderStage
from tests.block_samples import REGULAR, geometry
from tests.test_code_blocks import MONO, _context, _hand_block, _lines_bbox, _row, _run

SMALL_CAP = CodeBlockSettings(max_merge_candidates=1)


def _two_blocks(kind: str) -> tuple[list[Block], list[Block]]:
    geo = geometry([_row("aa bb cc", 0.2), _row("dd ee ff", 0.5)], fonts=[REGULAR, MONO])
    blocks = [_hand_block(geo, [i], "paragraph", bbox=_lines_bbox(geo, i, i)) for i in range(2)]
    if kind == "list_item":
        blocks = [Block.model_validate({**b.model_dump(), "type": kind, "list_depth": 0}) for b in blocks]
    out = CodeBlockStage(SMALL_CAP).run(_context(geo), blocks)
    return blocks, out


@pytest.mark.parametrize("kind", ["paragraph", "list_item"])
def test_cap_flag_only_adds_uncertain_flag_on_mergeable_blocks(kind: str) -> None:
    blocks, out = _two_blocks(kind)
    assert [b.type for b in out] == [kind, kind]
    assert [b.flags for b in out] == [["code_layout_uncertain"]] * 2
    for before, after in zip(blocks, out, strict=True):
        assert after.model_dump(exclude={"flags"}) == before.model_dump(exclude={"flags"})


def test_cap_flag_does_not_fail_layout_or_add_other_flags_through_pipeline() -> None:
    geo = geometry(
        [_row("aa bb cc", 0.2), _row("dd ee ff", 0.5), _row("gg hh ii", 0.8)],
        fonts=[REGULAR, MONO],
    )
    stages = (BaseBlockStage(), CodeBlockStage(SMALL_CAP), ReadingOrderStage())
    result = _run(geo, stages)
    assert not result.layout_failed
    assert result.ledger is not None
    assert result.blocks
    assert all(b.flags == ["code_layout_uncertain"] for b in result.blocks if b.type in ("paragraph", "list_item"))
    assert {f for b in result.blocks for f in b.flags} == {"code_layout_uncertain"}
