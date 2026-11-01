"""Stage `base_blocks` + `reading_order` trên geometry dựng tay (ADR-010 mục 3, 6, 7, 11; spec §5.15)."""

from __future__ import annotations

import unicodedata

import pytest
from pydantic import ValidationError

from holocron_core.domain.blocks import MAX_BLOCK_TEXT_CHARS, Block
from holocron_core.domain.documents import ExtractedLine, GeoLine, PageExtraction, PageGeometry
from holocron_core.infrastructure.lang_patterns import load_lang_patterns
from holocron_core.services.block_builder import BaseBlockSettings, BaseBlockStage
from holocron_core.services.block_pipeline import PageBlocks, build_page_blocks
from holocron_core.services.extraction_ledger import SourceIndex
from holocron_core.services.reading_order import ReadingOrderStage
from tests.block_samples import BOLD, DOC_ID, extraction_for, geo_line, geometry

LANG = load_lang_patterns()


def _build(geo: PageGeometry, extraction: PageExtraction | None = None) -> PageBlocks:
    return build_page_blocks(
        document_id=DOC_ID,
        page_number=1,
        geometry=geo,
        extraction=extraction or extraction_for(geo),
        lang=LANG,
    )


def _ordered(result: PageBlocks) -> list[Block]:
    assert not result.layout_failed, result.ledger
    return sorted(result.blocks, key=lambda b: b.reading_order)


def _texts(result: PageBlocks) -> list[str | None]:
    return [b.text for b in _ordered(result)]


def _ref_text(geo: PageGeometry, extraction: PageExtraction, block: Block) -> str:
    index = SourceIndex(geo, extraction)
    return " ".join(index.text(ref) for ref in block.line_refs)


def _column(x0: float, y0: float, texts: list[str], pitch: float = 0.018) -> list[GeoLine]:
    return [geo_line(text, x0, y0 + i * pitch) for i, text in enumerate(texts)]


# --------------------------------------------------------------------------- #
# Khối cơ sở, cột, tiêu đề, danh sách
# --------------------------------------------------------------------------- #


def test_two_columns_heading_and_paragraphs_in_reading_order() -> None:
    heading = geo_line("Big Title Here", 0.1, 0.05, height=0.02, size=18, font_id=1)
    left = _column(0.1, 0.12, ["left one aa", "left two bb", "left three"]) + _column(
        0.1, 0.2, ["left para2 a", "left para2 b"]
    )
    right = _column(0.55, 0.12, ["right one aa", "right two bb"])
    result = _build(geometry([heading, *right, *left]))
    blocks = _ordered(result)
    assert [b.type for b in blocks] == ["heading", "paragraph", "paragraph", "paragraph"]
    assert blocks[0].level == 1 and blocks[0].text == "Big Title Here"
    assert [b.text for b in blocks[1:]] == [
        "left one aa left two bb left three",
        "left para2 a left para2 b",
        "right one aa right two bb",
    ]
    assert [b.reading_order for b in blocks] == [0, 1, 2, 3]


def test_raw_line_spanning_two_columns_is_split_into_both_blocks() -> None:
    wide = [geo_line(f"l{i} aaa bbb", 0.1, 0.1 + i * 0.018) for i in range(3)]
    # Dòng thô chứa cả chữ cột trái và cột phải (khe ngang lớn giữa hai nhóm từ).
    joined: list[GeoLine] = []
    for i, line in enumerate(wide):
        right = geo_line(f"r{i} ccc ddd", 0.6, 0.1 + i * 0.018)
        y0 = 0.1 + i * 0.018
        joined.append(
            line.model_copy(update={"words": [*line.words, *right.words], "bbox": (0.1, y0, 0.7, y0 + 0.012)})
        )
    result = _build(geometry(joined))
    assert _texts(result) == ["l0 aaa bbb l1 aaa bbb l2 aaa bbb", "r0 ccc ddd r1 ccc ddd r2 ccc ddd"]


def test_bullets_make_list_items_with_continuation_and_depth() -> None:
    lines = [
        geo_line("Intro text line", 0.1, 0.1),
        geo_line("• first item text", 0.1, 0.13),
        geo_line("continues here", 0.12, 0.148),
        geo_line("– nested item", 0.16, 0.166),
        geo_line("• second item", 0.1, 0.184),
    ]
    blocks = _ordered(_build(geometry(lines)))
    assert [(b.type, b.list_depth) for b in blocks] == [
        ("paragraph", None),
        ("list_item", 0),
        ("list_item", 1),
        ("list_item", 0),
    ]
    assert blocks[1].text == "• first item text continues here"


def test_bold_line_in_regular_body_is_heading_and_header_footer_is_own_block() -> None:
    lines = [
        geo_line("Running header", 0.1, 0.02, header_footer=True),
        geo_line("Bold Heading", 0.1, 0.1, font_id=1),
        *_column(0.1, 0.13, ["body text line one here", "body text line two here", "body text three"]),
    ]
    blocks = _ordered(_build(geometry(lines)))
    assert [b.type for b in blocks] == ["header_footer", "heading", "paragraph"]


def test_first_line_indent_starts_new_paragraph() -> None:
    lines = [
        geo_line("para one line one", 0.1, 0.1),
        geo_line("para one line two", 0.1, 0.118),
        geo_line("para two indented", 0.14, 0.136),
        geo_line("para two line two", 0.1, 0.154),
    ]
    assert _texts(_build(geometry(lines))) == [
        "para one line one para one line two",
        "para two indented para two line two",
    ]


def test_font_data_dropped_geometry_still_builds() -> None:
    lines = [geo_line(t, 0.1, 0.1 + i * 0.018, font_id=None) for i, t in enumerate(["no font one", "no font two"])]
    geo = geometry(lines, fonts=[], font_data_dropped=True)
    blocks = _ordered(_build(geo))
    assert [b.type for b in blocks] == ["paragraph"] and blocks[0].text == "no font one no font two"


# --------------------------------------------------------------------------- #
# Văn bản khối và I3
# --------------------------------------------------------------------------- #


def test_block_text_is_join_of_line_refs() -> None:
    """I3: chữ khối = NFC từng từ nguồn nối bằng MỘT dấu cách, giữ gạch nối cuối dòng (`trích-` + `dẫn`)."""
    nfd = unicodedata.normalize("NFD", "trích-")
    lines = [
        geo_line(f"Trích dẫn nguyên văn {nfd}", 0.1, 0.1),
        geo_line("dẫn tiếp theo", 0.1, 0.118),
        geo_line("dòng   ba", 0.1, 0.136),
    ]
    geo = geometry(lines)
    extraction = extraction_for(geo)
    blocks = _ordered(_build(geo, extraction))
    assert len(blocks) == 1
    block = blocks[0]
    assert block.text == "Trích dẫn nguyên văn trích- dẫn tiếp theo dòng ba"
    assert block.text == _ref_text(geo, extraction, block)
    assert unicodedata.is_normalized("NFC", block.text)
    for line in extraction.lines:
        collapsed = " ".join(unicodedata.normalize("NFC", line.text).split())
        assert collapsed in block.text


def test_words_ordered_by_x_within_line() -> None:
    """Thứ tự x thắng luồng PDFium; từ không hộp đứng sau từ có hộp kề trước nó + `reading_order_uncertain`."""
    swapped = geo_line("alpha beta gamma", 0.1, 0.1, order=[2, 0, 1])
    plain = _build(geometry([swapped]))
    assert _texts(plain) == ["alpha beta gamma"]
    assert "reading_order_uncertain" not in _ordered(plain)[0].flags
    boxless = geo_line("one two three four", 0.1, 0.1, boxless={1}, order=[2, 0, 1, 3])
    # Luồng: three, one, two(không hộp), four ⇒ neo `one` kéo `two` theo sau; theo x: one two three four.
    result = _build(geometry([boxless]))
    blocks = _ordered(result)
    assert blocks[0].text == "one two three four"
    assert "reading_order_uncertain" in blocks[0].flags


def test_line_substring_is_block_substring_for_every_block() -> None:
    lines = [*_column(0.1, 0.1, ["một hai ba bốn", "năm sáu bảy"]), geo_line("• mục một", 0.1, 0.2)]
    geo = geometry(lines)
    extraction = extraction_for(geo)
    blocks = _ordered(_build(geo, extraction))
    joined = "\n\n".join(b.text or "" for b in blocks)
    for line in extraction.lines:
        assert line.text in joined


# --------------------------------------------------------------------------- #
# Từ/dòng không hộp
# --------------------------------------------------------------------------- #


def test_boxless_line_at_page_start_goes_first() -> None:
    lines = [geo_line("floating start", None, 0.0), *_column(0.1, 0.3, ["boxed one", "boxed two"])]
    blocks = _ordered(_build(geometry(lines)))
    assert [b.text for b in blocks] == ["floating start", "boxed one boxed two"]
    assert blocks[0].bbox is None and "reading_order_uncertain" in blocks[0].flags


def test_boxless_line_inside_column_follows_previous_boxed_block() -> None:
    left = [geo_line("left a", 0.1, 0.1), geo_line("left b", 0.1, 0.118)]
    right = [geo_line("right a", 0.6, 0.1), geo_line("right b", 0.6, 0.118)]
    floating = geo_line("lost words", None, 0.0)
    blocks = _ordered(_build(geometry([*left, floating, *right])))
    assert [b.text for b in blocks] == ["left a left b", "lost words", "right a right b"]


def test_page_without_any_box_follows_pdfium_order() -> None:
    lines = [geo_line(t, None, 0.0) for t in ["first line", "second line"]]
    lines.append(geo_line("footer line", None, 0.0, header_footer=True))
    blocks = _ordered(_build(geometry(lines)))
    assert [(b.type, b.text) for b in blocks] == [
        ("paragraph", "first line second line"),
        ("header_footer", "footer line"),
    ]
    assert all(b.bbox is None and "reading_order_uncertain" in b.flags for b in blocks)


def test_bbox_null_block_not_cropped() -> None:
    """Khối `bbox = null` không bao giờ mang `crop_pending` (không cắt được ảnh)."""
    lines = [geo_line("only boxless words", None, 0.0)]
    blocks = _ordered(_build(geometry(lines)))
    assert blocks[0].bbox is None and "crop_pending" not in blocks[0].flags
    with pytest.raises(ValidationError):
        Block.model_validate(
            {
                "block_id": "blk_0000000000000000",
                "document_id": DOC_ID,
                "page_number": 1,
                "type": "figure",
                "bbox": None,
                "flags": ["crop_pending"],
                "source": "text_layer",
                "reading_order": 0,
            }
        )


# --------------------------------------------------------------------------- #
# Chồng lấn, chữ bị che
# --------------------------------------------------------------------------- #


def test_overlapping_lines_become_uncertain_figure_with_labels() -> None:
    lines = [
        geo_line("normal text before", 0.1, 0.05),
        geo_line("label one", 0.3, 0.3),
        geo_line("label two", 0.31, 0.302),
    ]
    geo = geometry(lines)
    result = _build(geo)
    blocks = _ordered(result)
    figure = next(b for b in blocks if b.type == "figure")
    assert {"layout_uncertain", "crop_pending", "figure_label_text"} <= set(figure.flags)
    assert figure.text is None and figure.line_refs == []
    assert figure.labels == ["label one", "label two"]
    assert len(figure.label_line_refs) == 4
    assert result.ledger is not None and result.ledger.balanced
    assert {t.type: t.count for t in result.ledger.by_type}["figure"] == len("labelonelabeltwo")


def test_occluded_line_stays_in_its_block_with_flag() -> None:
    lines = [
        geo_line("visible line here", 0.1, 0.1),
        geo_line("covered line", 0.1, 0.118, flags=["TEXT_OCCLUDED"]),
        geo_line("faint line", 0.1, 0.136, flags=["LOW_TEXT_CONTRAST"]),
    ]
    result = _build(geometry(lines))
    blocks = _ordered(result)
    assert [b.type for b in blocks] == ["paragraph"]
    assert {"text_occluded", "low_text_contrast"} <= set(blocks[0].flags)
    assert result.ledger is not None
    assert {f.reason: f.count for f in result.ledger.flagged} == {"occluded": 11, "low_contrast": 9}


def test_font_encoding_suspect_comes_from_extracted_line() -> None:
    geo = geometry([geo_line("odd glyphs", 0.1, 0.1)])
    extraction = extraction_for(geo)
    flagged = extraction.lines[0].model_copy(update={"flags": ["FONT_ENCODING_SUSPECT"]})
    extraction = extraction.model_copy(update={"lines": [flagged], "status": "ok"})
    blocks = _ordered(_build(geo, extraction))
    assert "font_encoding_suspect" in blocks[0].flags


# --------------------------------------------------------------------------- #
# Độ dài khối
# --------------------------------------------------------------------------- #


def _long_line(i: int, x0: float, y0: float) -> GeoLine:
    return geo_line(" ".join(f"w{i:02d}{j:03d}" for j in range(280)), x0, y0, char_w=0.00045, height=0.008)


def test_paragraph_over_50k_splits_at_line_boundary() -> None:
    lines = [_long_line(i, 0.05, 0.05 + i * 0.011) for i in range(30)]
    geo = geometry(lines)
    extraction = extraction_for(geo)
    blocks = _ordered(_build(geo, extraction))
    assert len(blocks) >= 2 and all(b.type == "paragraph" for b in blocks)
    assert all(b.text is not None and len(b.text) <= MAX_BLOCK_TEXT_CHARS for b in blocks)
    for block in blocks:
        line_ids = {ref.line_idx for ref in block.line_refs if ref.kind == "word"}
        assert all(len([r for r in block.line_refs if r.kind == "word" and r.line_idx == i]) == 280 for i in line_ids)
    assert " ".join(b.text or "" for b in blocks) == " ".join(line.text for line in extraction.lines)


def test_non_paragraph_over_max_length_marks_layout_failed() -> None:
    first = _long_line(0, 0.05, 0.05)
    first = first.model_copy(update={"words": [first.words[0].model_copy(update={"text": "•"}), *first.words[1:]]})
    lines = [first, *[_long_line(i, 0.08, 0.05 + i * 0.011) for i in range(1, 30)]]
    short = _build(geometry(lines[:5]))
    assert [b.type for b in _ordered(short)] == ["list_item"]
    result = _build(geometry(lines))
    assert result.layout_failed and result.blocks == [] and result.ledger is None


# --------------------------------------------------------------------------- #
# Id khối
# --------------------------------------------------------------------------- #


def test_block_ids_depend_on_content_not_order() -> None:
    lines = [
        geo_line("Heading Words", 0.1, 0.05, height=0.02, size=18, font_id=1),
        *_column(0.1, 0.1, ["para one a", "para one b"]),
        *_column(0.1, 0.2, ["para two a", "para two b"]),
    ]
    geo = geometry(lines)
    first = _ordered(_build(geo))
    again = _ordered(_build(geo))
    assert [b.block_id for b in first] == [b.block_id for b in again]
    changed_line = geo_line("para two X", 0.1, 0.2)
    geo2 = geometry([*lines[:3], changed_line, lines[4]])
    second = _ordered(_build(geo2))
    assert [b.block_id == c.block_id for b, c in zip(first, second, strict=True)] == [True, True, False]
    assert len({b.block_id for b in first}) == len(first)


def test_other_document_or_page_changes_ids() -> None:
    geo = geometry([geo_line("same text", 0.1, 0.1)])
    base = _ordered(_build(geo))[0].block_id
    other = build_page_blocks(
        document_id="doc_00000000000000b2",
        page_number=1,
        geometry=geo,
        extraction=extraction_for(geo, document_id="doc_00000000000000b2"),
        lang=LANG,
    )
    page2 = build_page_blocks(
        document_id=DOC_ID, page_number=2, geometry=geo, extraction=extraction_for(geo, 2), lang=LANG
    )
    assert len({base, other.blocks[0].block_id, page2.blocks[0].block_id}) == 3


# --------------------------------------------------------------------------- #
# Dựng lại từ dòng đã xác nhận (basis confirmed_lines)
# --------------------------------------------------------------------------- #


def _patched(extraction: PageExtraction, edits: dict[int, str], appended: list[str]) -> PageExtraction:
    lines = [
        line.model_copy(update={"text": edits[line.line_index], "edited_by_human": True})
        if line.line_index in edits
        else line
        for line in extraction.lines
    ]
    for text in appended:
        lines.append(
            ExtractedLine(line_index=len(lines), text=text, bbox=None, conf=1.0, engine="human", edited_by_human=True)
        )
    return extraction.model_copy(update={"lines": lines})


def test_rebuild_from_confirmed_lines_uses_confirmed_refs_and_basis() -> None:
    lines = [*_column(0.1, 0.1, ["para one a", "para one b"]), *_column(0.1, 0.2, ["para two a", "para two b"])]
    geo = geometry(lines)
    fresh = _ordered(_build(geo))
    extraction = _patched(extraction_for(geo), {2: "para two EDITED"}, ["typed by user"])
    result = _build(geo, extraction)
    blocks = _ordered(result)
    assert result.ledger is not None and [b.ledger_basis for b in result.ledger.bases] == ["confirmed_lines"]
    assert result.ledger.balanced
    assert [b.text for b in blocks] == ["para one a para one b", "para two EDITED para two b", "typed by user"]
    assert blocks[0].block_id == fresh[0].block_id and "human_edited" not in blocks[0].flags
    assert blocks[1].block_id != fresh[1].block_id and "human_edited" in blocks[1].flags
    assert any(ref.kind == "confirmed_line" for ref in blocks[1].line_refs)
    assert blocks[2].bbox is None and {"human_edited", "reading_order_uncertain"} <= set(blocks[2].flags)


def test_custom_settings_change_grouping() -> None:
    lines = _column(0.1, 0.1, ["a b c", "d e f"], pitch=0.03)
    geo = geometry(lines)
    stages = (BaseBlockStage(BaseBlockSettings(paragraph_gap_factor=50.0)), ReadingOrderStage())
    merged = build_page_blocks(
        document_id=DOC_ID, page_number=1, geometry=geo, extraction=extraction_for(geo), lang=LANG, stages=stages
    )
    assert [b.text for b in merged.blocks] == ["a b c d e f"]


def test_bold_font_table_lookup_is_used() -> None:
    geo = geometry([geo_line("Strong", 0.1, 0.1, font_id=1), *_column(0.1, 0.13, ["plain body one", "plain two"])])
    assert geo.fonts[1] == BOLD
    assert [b.type for b in _ordered(_build(geo))] == ["heading", "paragraph"]
