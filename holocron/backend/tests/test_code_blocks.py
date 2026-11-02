"""Khối `code` đơn cách: thụt lề từ toạ độ x, cờ không chắc, hàm công khai, hash cấu hình (ADR-010 mục 7, 13, 15; X06)."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence

from holocron_core.domain.blocks import Block, ConfirmedLineRef, WordRef
from holocron_core.domain.documents import GeoFont, GeoLine, GeoWord, PageGeometry
from holocron_core.domain.schemas import quote_in_text
from holocron_core.infrastructure.lang_patterns import load_lang_patterns
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
