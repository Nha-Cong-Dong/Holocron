"""Khối `code` đơn cách: thụt lề từ toạ độ x, cờ không chắc, hàm công khai, hash cấu hình (ADR-010 mục 7, 13, 15; X06)."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence

import pytest

from holocron_core.domain.blocks import Block, ConfirmedLineRef, WordRef
from holocron_core.domain.documents import GeoFont, GeoLine, GeoWord, PageGeometry
from holocron_core.domain.schemas import quote_in_text
from holocron_core.infrastructure.lang_patterns import load_lang_patterns
from holocron_core.services import code_blocks
from holocron_core.services.block_builder import BaseBlockStage
from holocron_core.services.block_pipeline import (
    PAGE_STAGES,
    PageBlocks,
    PageContext,
    PageStage,
    build_page_blocks,
    default_layout_settings_sha256,
    layout_settings_sha256,
)
from holocron_core.services.code_blocks import CodeBlockSettings, CodeBlockStage, build_code_block
from holocron_core.services.extraction_ledger import SourceIndex
from holocron_core.services.page_numbers import PageNumberSettings, PageNumberStage
from holocron_core.services.reading_order import ReadingOrderStage
from tests.block_samples import DOC_ID, REGULAR, extraction_for, geometry

LANG = load_lang_patterns()
MONO = GeoFont(
    font_name="Courier",
    bold=False,
    italic=False,
    monospace=True,
    bold_source=None,
    italic_source=None,
    monospace_source="name",
)
CHAR_W = 0.008
HEIGHT = 0.012
PITCH = 0.016
X0 = 0.1
STAGES: tuple[PageStage, ...] = (BaseBlockStage(), CodeBlockStage(), ReadingOrderStage())

PROGRAM = [
    "start:",
    "    load a, 1",
    "        add a, b",
    "    store a, out",
    "                      ; ghi chu",
    "end",
]


def _row(text: str, y: float, *, font_id: int = 1, x0: float = X0, boxless: frozenset[int] = frozenset()) -> GeoLine:
    """Dòng thô: mỗi từ đặt ở đúng cột ký tự của nó trong `text` (bước x = CHAR_W)."""
    words: list[GeoWord] = []
    for i, match in enumerate(re.finditer(r"\S+", text)):
        x = x0 + match.start() * CHAR_W
        box = None if i in boxless else (x, y, x + len(match.group()) * CHAR_W, y + HEIGHT)
        words.append(GeoWord(text=match.group(), bbox=box, font_id=font_id, font_size_pt=10.0))
    boxes = [w.bbox for w in words if w.bbox is not None]
    bbox = (min(b[0] for b in boxes), y, max(b[2] for b in boxes), y + HEIGHT)
    return GeoLine(words=words, bbox=bbox, font_id=font_id, font_size_pt=10.0, extracted_line_indices=[])


def _geo(rows: list[str], *, font_id: int = 1, boxless: frozenset[int] = frozenset()) -> PageGeometry:
    lines = [_row(text, 0.2 + i * PITCH, font_id=font_id, boxless=boxless) for i, text in enumerate(rows)]
    return geometry(lines, fonts=[REGULAR, MONO])


def _run(geo: PageGeometry, stages: Sequence[PageStage] = STAGES) -> PageBlocks:
    return build_page_blocks(
        document_id=DOC_ID,
        page_number=1,
        geometry=geo,
        extraction=extraction_for(geo),
        lang=LANG,
        stages=stages,
    )


def _context(geo: PageGeometry) -> PageContext:
    return PageContext(DOC_ID, 1, geo, extraction_for(geo), LANG)


def _all_refs(geo: PageGeometry, lines: slice = slice(None)) -> list[WordRef]:
    return [
        WordRef(line_idx=i, word_idx=j) for i, line in list(enumerate(geo.lines))[lines] for j in range(len(line.words))
    ]


def _expected(rows: list[str]) -> str:
    indent = min(len(r) - len(r.lstrip()) for r in rows)
    return "\n".join(r[indent:].rstrip() for r in rows)


def test_indent_0_4_8_and_right_column_comment_reconstructed() -> None:
    result = _run(_geo(PROGRAM))
    assert not result.layout_failed and result.ledger is not None and result.ledger.balanced
    assert [b.type for b in result.blocks] == ["code"]
    block = result.blocks[0]
    assert block.text == _expected(PROGRAM)
    assert block.flags == []
    assert block.text.splitlines()[2].startswith(" " * 8 + "add")
    assert block.text.splitlines()[4] == " " * 22 + "; ghi chu"


def test_code_text_without_whitespace_equals_joined_words() -> None:
    geo = _geo(PROGRAM)
    block = _run(geo).blocks[0]
    index = SourceIndex(geo, extraction_for(geo))
    joined = "".join(unicodedata.normalize("NFC", index.text(ref)) for ref in block.line_refs)
    assert block.text is not None
    assert "".join(block.text.split()) == "".join(joined.split())
    assert len(block.line_refs) == sum(len(line.words) for line in geo.lines)


def test_code_block_quote_after_whitespace_collapse() -> None:
    """I3: trích dẫn lấy từ nguồn (một khoảng trắng) là chuỗi con của khối code sau NFC + gộp khoảng trắng."""
    block = _run(_geo(PROGRAM)).blocks[0]
    assert block.text is not None
    assert "        add a, b" in block.text and "add a, b\n    store a" in block.text
    assert quote_in_text("add a, b store a, out", block.text)
    assert quote_in_text("start: load a, 1", block.text)
    assert not quote_in_text("add a, c", block.text)


def test_public_builder_matches_stage_and_rejects_non_code() -> None:
    geo = _geo(PROGRAM)
    block = build_code_block(_context(geo), _all_refs(geo))
    assert block is not None and block.type == "code" and block.text == _expected(PROGRAM)
    assert block.bbox == _run(geo).blocks[0].bbox
    plain = _geo(PROGRAM, font_id=0)
    assert build_code_block(_context(plain), _all_refs(plain)) is None
    assert build_code_block(_context(geo), [ConfirmedLineRef(line_index=0)]) is None
    assert build_code_block(_context(geo), []) is None
    assert build_code_block(_context(geo), [WordRef(line_idx=99, word_idx=0)]) is None
    assert build_code_block(_context(geo), [WordRef(line_idx=0, word_idx=0)] * 2) is None


def test_too_few_measurable_words_keeps_old_block() -> None:
    geo = _geo(["ab cd"])
    assert build_code_block(_context(geo), _all_refs(geo)) is None
    kinds = [b.type for b in _run(geo).blocks]
    assert kinds == ["paragraph"]


def test_non_uniform_steps_not_code_or_flagged() -> None:
    rows = ["alpha beta gamma delta", "epsilon zeta eta theta"]
    geo = _geo(rows)
    skewed = geo.model_copy(
        update={
            "lines": [
                line.model_copy(
                    update={
                        "words": [
                            w.model_copy(
                                update={
                                    "bbox": (w.bbox[0], w.bbox[1], w.bbox[0] + (w.bbox[2] - w.bbox[0]) * f, w.bbox[3])
                                }
                            )
                            for w, f in zip(line.words, (1.0, 2.0, 0.5, 1.0), strict=True)
                            if w.bbox
                        ]
                    }
                )
                for line in geo.lines
            ]
        }
    )
    flagged = build_code_block(_context(skewed), _all_refs(skewed))
    assert flagged is not None and "code_layout_uncertain" in flagged.flags
    strict = CodeBlockSettings(min_uniform_ratio=0.8)
    assert build_code_block(_context(skewed), _all_refs(skewed), strict) is None


def test_mixed_font_and_boxless_word_flagged_uncertain() -> None:
    geo = _geo(PROGRAM)
    first = geo.lines[0]
    mixed_word = first.words[0].model_copy(update={"font_id": 0})
    mixed = geo.model_copy(update={"lines": [first.model_copy(update={"words": [mixed_word]}), *geo.lines[1:]]})
    block = build_code_block(_context(mixed), _all_refs(mixed))
    assert block is not None and "code_layout_uncertain" in block.flags
    boxless = _geo(PROGRAM[:4], boxless=frozenset({1}))
    block2 = build_code_block(_context(boxless), _all_refs(boxless))
    assert block2 is not None and "code_layout_uncertain" in block2.flags
    assert block2.text is not None
    assert "".join(block2.text.split()) == "".join("".join(PROGRAM[:4]).split())
    result = _run(boxless)
    assert not result.layout_failed and result.ledger is not None and result.ledger.balanced


def test_overlapping_rows_flagged() -> None:
    rows = ["one two three four", "five six seven eight"]
    lines = [_row(rows[0], 0.2), _row(rows[1], 0.2 + 0.005)]
    geo = geometry(lines, fonts=[REGULAR, MONO])
    block = build_code_block(_context(geo), _all_refs(geo), CodeBlockSettings(row_tol_ratio=0.2))
    assert block is not None and "code_layout_uncertain" in block.flags


def test_spaces_capped_and_flagged() -> None:
    gapped = geometry([_row("a bb      cc dd", 0.2)], fonts=[REGULAR, MONO])
    exact = build_code_block(_context(gapped), _all_refs(gapped))
    assert exact is not None and exact.text == "a bb      cc dd" and exact.flags == []
    capped = build_code_block(_context(gapped), _all_refs(gapped), CodeBlockSettings(max_spaces=3))
    assert capped is not None and capped.text == "a bb   cc dd" and "code_layout_uncertain" in capped.flags


def test_default_pipeline_does_not_register_code_stage() -> None:
    assert [stage.name for stage in PAGE_STAGES] == ["base_blocks", "reading_order"]
    result = _run(_geo(PROGRAM), PAGE_STAGES)
    assert "code" not in {b.type for b in result.blocks}


def test_two_separate_listings_stay_two_blocks() -> None:
    top = [_row(t, 0.1 + i * PITCH) for i, t in enumerate(["aa bb cc", "  dd ee ff"])]
    bottom = [_row(t, 0.7 + i * PITCH) for i, t in enumerate(["gg hh ii", "  jj kk ll"])]
    result = _run(geometry([*top, *bottom], fonts=[REGULAR, MONO]))
    assert [b.type for b in result.blocks] == ["code", "code"]
    assert [b.text for b in result.blocks] == ["aa bb cc\n  dd ee ff", "gg hh ii\n  jj kk ll"]


def test_prose_block_untouched() -> None:
    geo = geometry([_row("plain words here now", 0.2, font_id=0)], fonts=[REGULAR, MONO])
    assert [b.type for b in _run(geo).blocks] == ["paragraph"]


# --------------------------------------------------------------------------- #
# Hash cấu hình (I4)
# --------------------------------------------------------------------------- #


def test_code_stage_fingerprint_and_hash_change() -> None:
    stage = CodeBlockStage()
    assert set(stage.fingerprint()) == {"name", "version", "settings"} and stage.fingerprint()["name"] == "code_blocks"
    numbers = PageNumberStage(LANG, PageNumberSettings())
    default = layout_settings_sha256(numbers)
    assert default == default_layout_settings_sha256(LANG)
    with_code = layout_settings_sha256(numbers, STAGES)
    assert with_code != default
    tweaked = (BaseBlockStage(), CodeBlockStage(CodeBlockSettings(min_monospace_ratio=0.7)), ReadingOrderStage())
    assert layout_settings_sha256(numbers, tweaked) != with_code
    assert layout_settings_sha256(numbers, STAGES) == with_code


def test_block_model_accepts_result() -> None:
    block = _run(_geo(PROGRAM)).blocks[0]
    assert Block.model_validate(block.model_dump()) == block


# --------------------------------------------------------------------------- #
# Vòng sửa 1: từ không hộp theo luồng, hai listing cạnh nhau, trần ứng viên, đột biến
# --------------------------------------------------------------------------- #


def _boxless_row(text: str) -> GeoLine:
    words = [GeoWord(text=t, bbox=None, font_id=1, font_size_pt=10.0) for t in text.split()]
    return GeoLine(words=words, bbox=None, font_id=1, font_size_pt=10.0, extracted_line_indices=[])


def _hand_block(
    geo: PageGeometry, lines: Sequence[int], kind: str = "paragraph", *, bbox: tuple[float, float, float, float] | None
) -> Block:
    data: dict[str, object] = {
        "block_id": "blk_0000000000000000",
        "document_id": DOC_ID,
        "page_number": 1,
        "type": kind,
        "bbox": bbox,
        "text": "x",
        "line_refs": _all_refs(geo, slice(lines[0], lines[-1] + 1)),
        "source": "text_layer",
        "reading_order": 0,
    }
    if kind == "heading":
        data["level"] = 1
    return Block.model_validate(data)


def _lines_bbox(geo: PageGeometry, first: int, last: int) -> tuple[float, float, float, float]:
    boxes = [b for line in geo.lines[first : last + 1] for b in [line.bbox] if b is not None]
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


def test_boxless_raw_line_keeps_pdfium_flow_position_and_flags_order() -> None:
    rows = ["aa bb cc", "dd ee ff", "gg hh ii", "jj kk ll"]
    lines = [_row(t, 0.2 + i * PITCH) for i, t in enumerate(rows)]
    lines[1] = _boxless_row(rows[1])
    geo = geometry(lines, fonts=[REGULAR, MONO])
    block = build_code_block(_context(geo), _all_refs(geo))
    assert block is not None and block.text == "\n".join(rows)
    assert [(r.line_idx, r.word_idx) for r in block.line_refs if isinstance(r, WordRef)][:6] == [
        (0, 0),
        (0, 1),
        (0, 2),
        (1, 0),
        (1, 1),
        (1, 2),
    ]
    assert "reading_order_uncertain" in block.flags and "code_layout_uncertain" in block.flags


def test_leading_and_consecutive_boxless_lines_keep_flow_position() -> None:
    rows = ["aa bb cc", "dd ee ff", "gg hh ii", "jj kk ll", "mm nn oo"]
    lines = [_row(t, 0.2 + i * PITCH) for i, t in enumerate(rows)]
    lines[0] = _boxless_row(rows[0])
    lines[2] = _boxless_row(rows[2])
    lines[3] = _boxless_row(rows[3])
    geo = geometry(lines, fonts=[REGULAR, MONO])
    block = build_code_block(_context(geo), _all_refs(geo))
    assert block is not None and block.text == "\n".join(rows)


def test_boxless_word_after_its_boxed_predecessor_even_when_x_order_differs() -> None:
    geo = _geo(["aa bb cc dd", "ee ff gg hh"])
    first = geo.lines[0]
    aa, bb, cc, dd = first.words
    assert aa.bbox is not None
    # luồng: aa bb(không hộp) cc dd; aa bị đẩy sang phải nên thứ tự x là cc dd aa; bb phải đứng ngay sau aa
    moved = (aa.bbox[0] + 0.5, aa.bbox[1], aa.bbox[2] + 0.5, aa.bbox[3])
    words = [aa.model_copy(update={"bbox": moved}), bb.model_copy(update={"bbox": None}), cc, dd]
    geo2 = geo.model_copy(update={"lines": [first.model_copy(update={"words": words}), *geo.lines[1:]]})
    block = build_code_block(_context(geo2), _all_refs(geo2))
    assert block is not None and block.text is not None
    assert block.text.splitlines()[0].split() == ["cc", "dd", "aa", "bb"]


def test_leading_boxless_word_of_line_spanning_two_rows_joins_top_row() -> None:
    top = _row("pp qq rr", 0.2)
    low = _row("ss tt uu", 0.2 + 3 * PITCH)
    # dòng thô: X(không hộp) a(hàng dưới) b(hàng trên) => X vào hàng đầu (trên cùng)
    x = GeoWord(text="xx", bbox=None, font_id=1, font_size_pt=10.0)
    a = low.words[0]
    b = top.words[0]
    mixed = GeoLine(
        words=[x, a, b],
        bbox=None,
        font_id=1,
        font_size_pt=10.0,
        extracted_line_indices=[],
    )
    rest_top = top.model_copy(update={"words": top.words[1:]})
    rest_low = low.model_copy(update={"words": low.words[1:]})
    geo = geometry([mixed, rest_top, rest_low], fonts=[REGULAR, MONO])
    block = build_code_block(_context(geo), _all_refs(geo))
    assert block is not None and block.text is not None
    assert [ln.split()[0] for ln in block.text.splitlines()] == ["xx", "ss"]
    assert block.text.splitlines()[0].split() == ["xx", "pp", "qq", "rr"]


def test_nfd_word_is_normalised_and_char_width_uses_nfc_code_points() -> None:
    rows = ["hàm số nguyên", "    cộng trừ nhân"]
    base = _geo(rows)
    nfd_lines = [
        line.model_copy(
            update={"words": [w.model_copy(update={"text": unicodedata.normalize("NFD", w.text)}) for w in line.words]}
        )
        for line in base.lines
    ]
    geo = base.model_copy(update={"lines": nfd_lines})
    assert any(w.text != unicodedata.normalize("NFC", w.text) for ln in geo.lines for w in ln.words)
    block = build_code_block(_context(geo), _all_refs(geo))
    assert block is not None and block.text is not None
    assert unicodedata.is_normalized("NFC", block.text)
    assert block.text == unicodedata.normalize("NFC", _expected(rows))


def test_two_listings_side_by_side_stay_two_blocks() -> None:
    wide = "aaaaaaaaaa bbbbbbbbbb ccc"
    left_rows = [wide, "  " + wide[2:], wide]
    right_rows = [wide.upper(), "  " + wide[2:].upper(), wide.upper()]
    left = [_row(t, 0.2 + i * PITCH, x0=0.1) for i, t in enumerate(left_rows)]
    right = [_row(t, 0.2 + i * PITCH, x0=0.5) for i, t in enumerate(right_rows)]
    geo = geometry([*left, *right], fonts=[REGULAR, MONO])
    blocks = [_hand_block(geo, [i], bbox=_lines_bbox(geo, i, i)) for i in range(6)]
    merged = CodeBlockStage().run(_context(geo), blocks)
    assert [b.type for b in merged] == ["code", "code"]
    assert [b.text for b in merged] == [chr(10).join(left_rows), chr(10).join(right_rows)]
    assert all(b.flags == [] for b in merged)


def test_listing_with_nearby_right_column_still_merges() -> None:
    left = _row("aa bb cc", 0.2, x0=0.1)
    near = _row("dd ee ff", 0.2, x0=0.1 + 8 * CHAR_W + 2 * HEIGHT)
    geo = geometry([left, near], fonts=[REGULAR, MONO])
    blocks = [_hand_block(geo, [i], bbox=_lines_bbox(geo, i, i)) for i in range(2)]
    merged = CodeBlockStage().run(_context(geo), blocks)
    assert [b.type for b in merged] == ["code"]


def test_leading_indent_over_max_spaces_is_cut_and_flagged() -> None:
    far = _row("cc dd ee", 0.2 + PITCH, x0=X0 + 25 * CHAR_W)
    geo = geometry([_row("aa bb cc", 0.2), far], fonts=[REGULAR, MONO])
    capped = build_code_block(_context(geo), _all_refs(geo), CodeBlockSettings(max_spaces=10))
    assert capped is not None and capped.text is not None
    assert capped.text.splitlines()[1] == " " * 10 + "cc dd ee" and "code_layout_uncertain" in capped.flags
    exact = build_code_block(_context(geo), _all_refs(geo))
    assert exact is not None and exact.text is not None
    assert exact.text.splitlines()[1] == " " * 25 + "cc dd ee" and exact.flags == []


def test_horizontally_overlapping_words_flagged_only_beyond_limit() -> None:
    line = _row("aa bb cc dd", 0.2)
    first, second, *rest = line.words
    assert first.bbox is not None and second.bbox is not None
    width = second.bbox[2] - second.bbox[0]

    def with_overlap(chars: float) -> PageGeometry:
        x0 = first.bbox[2] - chars * CHAR_W if first.bbox else 0.0
        moved = second.model_copy(update={"bbox": (x0, second.bbox[1], x0 + width, second.bbox[3])})
        return geometry([line.model_copy(update={"words": [first, moved, *rest]})], fonts=[REGULAR, MONO])

    heavy = with_overlap(1.0)
    light = with_overlap(0.25)
    flagged = build_code_block(_context(heavy), _all_refs(heavy))
    ok = build_code_block(_context(light), _all_refs(light))
    assert flagged is not None and "code_layout_uncertain" in flagged.flags
    assert ok is not None and "code_layout_uncertain" not in ok.flags
    relaxed = build_code_block(_context(heavy), _all_refs(heavy), CodeBlockSettings(word_overlap_max_chars=2.0))
    assert relaxed is not None and "code_layout_uncertain" not in relaxed.flags


def test_too_long_code_text_builder_returns_none_stage_fails_layout(monkeypatch: pytest.MonkeyPatch) -> None:
    geo = _geo(PROGRAM)
    monkeypatch.setattr(code_blocks, "MAX_BLOCK_TEXT_CHARS", 20)
    assert build_code_block(_context(geo), _all_refs(geo)) is None
    assert _run(geo).layout_failed


def test_over_candidate_cap_skips_merge_and_flags_old_blocks() -> None:
    rows = ["aa bb cc", "dd ee ff", "gg hh ii", "jj kk ll"]
    geo = geometry([_row(t, 0.2 + i * PITCH) for i, t in enumerate(rows)], fonts=[REGULAR, MONO])
    blocks = [_hand_block(geo, [i], bbox=_lines_bbox(geo, i, i)) for i in range(4)]
    out = CodeBlockStage(CodeBlockSettings(max_merge_candidates=3)).run(_context(geo), blocks)
    assert [b.type for b in out] == ["paragraph"] * 4
    assert all(b.flags == ["code_layout_uncertain"] for b in out)
    assert [b.line_refs for b in out] == [b.line_refs for b in blocks]
    within = CodeBlockStage(CodeBlockSettings(max_merge_candidates=4)).run(_context(geo), blocks)
    assert [b.type for b in within] == ["code"]
    plain_geo = _geo(PROGRAM, font_id=0)
    plain = _hand_block(plain_geo, [0], bbox=(0.1, 0.2, 0.2, 0.3))
    assert CodeBlockStage(CodeBlockSettings(max_merge_candidates=1)).run(_context(plain_geo), [plain, plain]) == [
        plain,
        plain,
    ]


# --------------------------------------------------------------------------- #
# Đột biến sống (vòng 1): NFC, tham chiếu, cờ, gộp
# --------------------------------------------------------------------------- #


def test_refs_with_duplicate_or_invalid_member_reject_whole_block() -> None:
    geo = _geo(PROGRAM)
    refs = _all_refs(geo)
    assert build_code_block(_context(geo), refs) is not None
    assert build_code_block(_context(geo), [*refs, refs[3]]) is None
    assert build_code_block(_context(geo), [*refs, WordRef(line_idx=99, word_idx=0)]) is None
    assert build_code_block(_context(geo), [*refs, WordRef(line_idx=0, word_idx=99)]) is None
    assert build_code_block(_context(geo), [*refs, ConfirmedLineRef(line_index=0)]) is None


def test_source_line_flags_propagate_to_code_block() -> None:
    geo = _geo(PROGRAM)
    first = geo.lines[0].model_copy(update={"flags": ["TEXT_OCCLUDED", "LOW_TEXT_CONTRAST"]})
    flagged = geo.model_copy(update={"lines": [first, *geo.lines[1:]]})
    block = build_code_block(_context(flagged), _all_refs(flagged))
    assert block is not None and {"text_occluded", "low_text_contrast"} <= set(block.flags)
    assert "code_layout_uncertain" not in block.flags


def test_heading_and_caption_next_to_code_are_not_merged() -> None:
    geo = _geo(["aa bb cc", "dd ee ff", "gg hh ii", "jj kk ll"])
    code = _hand_block(geo, [0, 1], bbox=_lines_bbox(geo, 0, 1))
    for kind in ("heading", "caption"):
        near = _hand_block(geo, [2, 3], kind, bbox=_lines_bbox(geo, 2, 3))
        out = CodeBlockStage().run(_context(geo), [code, near])
        assert [b.type for b in out] == ["code", kind]
        assert out[1] == near and out[0].text == "aa bb cc\ndd ee ff"
    para = _hand_block(geo, [2, 3], bbox=_lines_bbox(geo, 2, 3))
    assert [b.type for b in CodeBlockStage().run(_context(geo), [code, para])] == ["code"]


def test_all_boxless_paragraph_candidate_is_kept_without_error() -> None:
    geo = geometry([_boxless_row("aa bb cc dd")], fonts=[REGULAR, MONO])
    block = _hand_block(geo, [0], bbox=(0.1, 0.2, 0.3, 0.3))
    assert CodeBlockStage().run(_context(geo), [block]) == [block]


def test_listings_separated_by_four_line_heights_stay_apart() -> None:
    def pair(gap_heights: float) -> list[Block]:
        top = [_row("aa bb cc", 0.2), _row("dd ee ff", 0.2 + PITCH)]
        y = 0.2 + PITCH + HEIGHT + gap_heights * HEIGHT
        bottom = [_row("gg hh ii", y), _row("jj kk ll", y + PITCH)]
        geo = geometry([*top, *bottom], fonts=[REGULAR, MONO])
        blocks = [
            _hand_block(geo, [0, 1], bbox=_lines_bbox(geo, 0, 1)),
            _hand_block(geo, [2, 3], bbox=_lines_bbox(geo, 2, 3)),
        ]
        return CodeBlockStage().run(_context(geo), blocks)

    assert [b.type for b in pair(2.5)] == ["code"]
    assert [b.type for b in pair(4.0)] == ["code", "code"]


def test_chain_of_three_blocks_joining_two_clusters_becomes_one_block() -> None:
    rows = ["aa bb cc", "dd ee ff", "gg hh ii"]
    # vị trí trong danh sách khối: trên, dưới, giữa; trên và dưới không chạm nhau, cả hai chạm khối giữa
    ys = [0.2, 0.2 + 2 * PITCH, 0.2 + PITCH]
    geo = geometry([_row(t, y) for t, y in zip(rows, ys, strict=True)], fonts=[REGULAR, MONO])
    blocks = [_hand_block(geo, [i], bbox=_lines_bbox(geo, i, i)) for i in range(3)]
    out = CodeBlockStage(CodeBlockSettings(merge_y_factor=0.4)).run(_context(geo), blocks)
    assert [b.type for b in out] == ["code"]
    assert out[0].text == chr(10).join(["aa bb cc", "gg hh ii", "dd ee ff"])


def test_uneven_steps_without_overlap_get_uncertain_flag() -> None:
    geo = _geo(["alpha beta gamma delta", "epsilon zeta eta theta"])
    lines = []
    for line in geo.lines:
        words = []
        for w, f in zip(line.words, (1.0, 1.0, 1.0, 0.7), strict=True):
            assert w.bbox is not None
            right = w.bbox[0] + (w.bbox[2] - w.bbox[0]) * f
            words.append(w.model_copy(update={"bbox": (w.bbox[0], w.bbox[1], right, w.bbox[3])}))
        lines.append(line.model_copy(update={"words": words}))
    skewed = geo.model_copy(update={"lines": lines})
    block = build_code_block(_context(skewed), _all_refs(skewed))
    assert block is not None and "code_layout_uncertain" in block.flags
    relaxed = build_code_block(_context(skewed), _all_refs(skewed), CodeBlockSettings(flag_uniform_ratio=0.5))
    assert relaxed is not None and "code_layout_uncertain" not in relaxed.flags
