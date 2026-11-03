"""Cây tiêu đề và nối trang (`services/section_tree.py`, ADR-010 mục 4, 15; brief X08). Dữ liệu tổng hợp tự tạo."""

from __future__ import annotations

import hashlib
import itertools
import json
from dataclasses import dataclass

import pytest

from holocron_core.domain.blocks import Block, Section, WordRef
from holocron_core.infrastructure.lang_patterns import load_lang_patterns
from holocron_core.services.block_pipeline import PageContext
from holocron_core.services.section_tree import (
    SectionTree,
    SectionTreeSettings,
    SectionTreeStage,
    build_section_tree,
)
from tests.block_samples import BOLD, DOC_ID, REGULAR, extraction_for, geo_line, geometry

LANG = load_lang_patterns()
BODY = 11.0


@dataclass(frozen=True)
class Item:
    kind: str  # "heading" | "paragraph"
    text: str
    size: float = BODY
    bold: bool = False


def head(text: str, size: float = 20.0, bold: bool = True) -> Item:
    return Item("heading", text, size, bold)


def para(text: str = "noi dung doan van") -> Item:
    return Item("paragraph", text)


_counter = itertools.count(1)


def make_page(page_number: int, items: list[Item]) -> tuple[PageContext, list[Block]]:
    lines = [
        geo_line(it.text, 0.1, 0.05 + 0.05 * i, size=it.size, font_id=1 if it.bold else 0) for i, it in enumerate(items)
    ]
    geo = geometry(lines, fonts=[REGULAR, BOLD])
    context = PageContext(DOC_ID, page_number, geo, extraction_for(geo, page_number), LANG)
    blocks: list[Block] = []
    for i, (it, line) in enumerate(zip(items, geo.lines, strict=True)):
        data: dict[str, object] = {
            "block_id": f"blk_{next(_counter):016x}",
            "document_id": DOC_ID,
            "page_number": page_number,
            "type": it.kind,
            "bbox": line.bbox,
            "text": it.text,
            "line_refs": [WordRef(line_idx=i, word_idx=w) for w in range(len(line.words))],
            "source": "text_layer",
            "reading_order": i,
        }
        if it.kind == "heading":
            data["level"] = 1
        blocks.append(Block.model_validate(data))
    return context, blocks


def build(pages: list[list[Item]], settings: SectionTreeSettings | None = None) -> SectionTree:
    built = [make_page(n, items) for n, items in enumerate(pages, start=1)]
    return build_section_tree(DOC_ID, "ch2", "Chuong hai", built, LANG, settings or SectionTreeSettings())


def by_title(tree: SectionTree, title: str) -> Section:
    return next(s for s in tree.sections if s.title == title)


def parent_of(tree: SectionTree, section: Section) -> Section:
    return next(s for s in tree.sections if s.section_id == section.parent_id)


def test_three_numbered_levels() -> None:
    tree = build(
        [
            [
                head("1. Mot"),
                para(),
                head("1.1 Mot mot"),
                para(),
                head("1.1.1 Sau nua"),
                para(),
                head("1.2 Mot hai"),
                para(),
                head("2. Hai"),
            ]
        ]
    )
    root = tree.sections[0]
    assert (root.kind, root.level, root.parent_id, root.chapter_code, root.title) == (
        "chapter",
        0,
        None,
        "ch2",
        "Chuong hai",
    )
    levels = {s.title: s.level for s in tree.sections[1:]}
    assert levels == {"1. Mot": 1, "1.1 Mot mot": 2, "1.1.1 Sau nua": 3, "1.2 Mot hai": 2, "2. Hai": 1}
    assert parent_of(tree, by_title(tree, "1.1.1 Sau nua")).title == "1.1 Mot mot"
    assert parent_of(tree, by_title(tree, "1.2 Mot hai")).title == "1. Mot"
    assert parent_of(tree, by_title(tree, "2. Hai")).title == "Chuong hai"


def test_repeated_slide_title_merges_three_pages() -> None:
    tree = build(
        [
            [head("Scheduling"), para()],
            [head("Scheduling"), para()],
            [head("scheduling "), para()],
            [head("Memory"), para()],
        ]
    )
    sched = by_title(tree, "Scheduling")
    assert (sched.page_start, sched.page_end) == (1, 3)
    assert len(sched.heading_block_ids) == 3
    assert sched.flags == ["continued_across_pages"]
    assert by_title(tree, "Memory").flags == []
    assert len([s for s in tree.sections if s.kind == "section"]) == 2


@pytest.mark.parametrize("suffix", ["(tiếp)", "(cont.)"])
def test_continuation_suffix(suffix: str) -> None:
    tree = build([[head("Deadlock"), para()], [para(), head(f"Deadlock {suffix}"), para()]])
    deadlock = by_title(tree, "Deadlock")
    assert deadlock.flags == ["continued_across_pages"]
    assert len(deadlock.heading_block_ids) == 2
    assert deadlock.page_end == 2


def test_suffix_without_matching_section_is_new_section_not_guess() -> None:
    tree = build([[head("Alpha"), para()], [head("Beta (tiếp)"), para()]])
    beta = by_title(tree, "Beta")
    assert beta.flags == []
    assert len([s for s in tree.sections if s.kind == "section"]) == 2


def test_unnumbered_headings_cluster_by_size() -> None:
    tree = build(
        [
            [
                head("Lon", 28.0),
                para(),
                head("Vua", 20.0),
                para(),
                head("Nho", 15.0),
                para(),
                head("Vua hai", 20.0),
                para(),
                head("Lon hai", 28.0),
            ],
        ]
    )
    levels = {s.title: (s.level, s.flags) for s in tree.sections[1:]}
    assert levels["Lon"] == (1, [])
    assert levels["Vua"] == (2, [])
    assert levels["Nho"] == (3, [])
    assert parent_of(tree, by_title(tree, "Vua hai")).title == "Lon"
    assert parent_of(tree, by_title(tree, "Lon hai")).title == "Chuong hai"


def test_close_size_clusters_flag_level_uncertain() -> None:
    tree = build([[head("Mot", 20.0), para(), head("Hai", 18.0), para(), head("Ba", 30.0)]])
    assert "level_uncertain" in by_title(tree, "Mot").flags
    assert "level_uncertain" in by_title(tree, "Hai").flags
    assert "level_uncertain" not in by_title(tree, "Ba").flags


def test_size_unknown_and_unnumbered_is_uncertain_level_one() -> None:
    items = [head("Chi mot")]
    context, blocks = make_page(1, items)
    stripped = [b.model_copy(update={"line_refs": []}) for b in blocks]
    tree = build_section_tree(DOC_ID, None, None, [(context, stripped)], LANG)
    section = tree.sections[1]
    assert (section.level, section.flags) == (1, ["level_uncertain"])
    assert tree.sections[0].flags == ["untitled"] and tree.sections[0].title is None


def test_level_jump_creates_no_virtual_node() -> None:
    tree = build([[head("1. Mot"), para(), head("1.2.3 Nhay cap"), para(), head("1.3 Tiep")]])
    sections = tree.sections
    assert [s.title for s in sections] == ["Chuong hai", "1. Mot", "1.2.3 Nhay cap", "1.3 Tiep"]
    jump = by_title(tree, "1.2.3 Nhay cap")
    assert jump.level == 3 and parent_of(tree, jump).title == "1. Mot"
    assert parent_of(tree, by_title(tree, "1.3 Tiep")).title == "1. Mot"


def test_roman_and_letter_numbering() -> None:
    tree = build([[head("I. Mot"), para(), head("a) Y a", 14.0, False), head("b) Y b", 14.0, False), head("II. Hai")]])
    assert parent_of(tree, by_title(tree, "a) Y a")).title == "I. Mot"
    assert parent_of(tree, by_title(tree, "b) Y b")).title == "I. Mot"
    assert by_title(tree, "II. Hai").level == 1


def test_no_heading_document_is_root_only() -> None:
    tree = build([[para(), para()], [para()]])
    assert len(tree.sections) == 1
    root = tree.sections[0]
    assert (root.page_start, root.page_end) == (1, 2)
    assert len(root.block_ids) == 3 and root.heading_block_ids == []


def test_blocks_before_first_heading_belong_to_root() -> None:
    tree = build([[para(), head("1. Mot"), para()]])
    root = tree.sections[0]
    assert len(root.block_ids) == 1
    assert len(by_title(tree, "1. Mot").block_ids) == 2


def test_section_ids_unique() -> None:
    same_title = build([[head("1. A"), para(), head("1.1 Muc"), para(), head("2. B"), para(), head("2.1 Muc"), para()]])
    ids = [s.section_id for s in same_title.sections]
    assert len(set(ids)) == len(ids)
    nested_untitled = build(
        [[head("(tiếp)", 30.0), para(), head("(tiếp)", 22.0), para(), head("(tiếp)", 15.0), para()]]
    )
    assert [s.flags for s in nested_untitled.sections[1:]] == [["untitled"]] * 3
    assert all(s.title is None for s in nested_untitled.sections[1:])
    ids = [s.section_id for s in nested_untitled.sections]
    assert len(set(ids)) == len(ids) == 4


def test_every_block_in_exactly_one_section() -> None:
    pages = [[head("1. A"), para(), head("1.1 B"), para()], [head("1.1 B"), para(), head("2. C"), para()]]
    built = [make_page(n, items) for n, items in enumerate(pages, start=1)]
    tree = build_section_tree(DOC_ID, None, "T", built, LANG)
    all_ids = [b.block_id for _, blocks in built for b in blocks]
    assert sorted(tree.block_section) == sorted(all_ids)
    listed = [bid for s in tree.sections for bid in s.block_ids]
    assert sorted(listed) == sorted(all_ids)
    section_ids = {s.section_id for s in tree.sections}
    assert set(tree.block_section.values()) <= section_ids
    for s in tree.sections:
        assert all(tree.block_section[b] == s.section_id for b in s.block_ids)


def test_section_validator_holds_for_every_node() -> None:
    tree = build(
        [[head("1. A"), para(), head("1.1 B"), para(), head("2. C")], [head("2. C"), para(), head("(tiếp)", 12.0)]]
    )
    for s in tree.sections:
        assert Section.model_validate(s.model_dump()) == s
        assert (s.parent_id is None) == (s.kind == "chapter")
        assert (s.title is None) == ("untitled" in s.flags)
    assert sum(1 for s in tree.sections if s.parent_id is None) == 1


def test_deterministic_and_input_order_independent() -> None:
    pages = [[head("1. A"), para(), head("1.1 B"), para()], [head("1.1 B"), para(), head("2. C"), para()]]
    built = [make_page(n, items) for n, items in enumerate(pages, start=1)]
    first = build_section_tree(DOC_ID, "ch1", "T", built, LANG)
    second = build_section_tree(DOC_ID, "ch1", "T", built, LANG)
    shuffled = build_section_tree(DOC_ID, "ch1", "T", list(reversed(built)), LANG)
    assert first == second == shuffled
    assert first.model_dump_json() == second.model_dump_json()
    other = build_section_tree("doc_00000000000000b2", "ch1", "T", [], LANG)
    assert other.sections[0].section_id != first.sections[0].section_id


def test_fingerprint_changes_with_threshold_pattern_and_lang() -> None:
    base = SectionTreeStage(LANG).fingerprint()
    assert set(base) == {"name", "version", "settings"}
    assert base["name"] == "section_tree"
    assert base["settings"]["lang_patterns_sha256"] == LANG.sha256()
    assert set(base["settings"]) == {*SectionTreeSettings().model_dump(), "lang_patterns_sha256"}
    assert SectionTreeStage(LANG).fingerprint() == base
    assert SectionTreeStage(LANG, SectionTreeSettings(cluster_tol=0.05)).fingerprint() != base
    assert SectionTreeStage(LANG, SectionTreeSettings(letter_pattern=r"^(?P<num>[a-z])\.\s+\S")).fingerprint() != base
    assert SectionTreeStage(LANG.model_copy(update={"continuation_suffixes": ()})).fingerprint() != base


def test_settings_validation() -> None:
    with pytest.raises(ValueError):
        SectionTreeSettings(arabic_pattern=r"^\d+")
    with pytest.raises(ValueError):
        SectionTreeSettings(cluster_tol=0.2, close_cluster_tol=0.1)


def expected_id(document_id: str, path: list[list[object]]) -> str:
    """Công thức ADR-010 mục 4 tính độc lập với code (hashlib + json)."""
    text = json.dumps(["section", document_id, path], sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sec_" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def test_section_id_pinned_to_adr_formula() -> None:
    tree = build(
        [[head("1. Mot"), para(), head("1.1 Muc  CON"), para(), head("2. Hai"), para(), head("2.1 Muc  con"), para()]]
    )
    ids = {s.title: s.section_id for s in tree.sections}
    assert ids["Chuong hai"] == expected_id(DOC_ID, [])
    assert ids["1. Mot"] == expected_id(DOC_ID, [["1. mot", 0]])
    assert ids["1.1 Muc CON"] == expected_id(DOC_ID, [["1. mot", 0], ["1.1 muc con", 0]])
    assert ids["2. Hai"] == expected_id(DOC_ID, [["2. hai", 1]])
    assert ids["2.1 Muc con"] == expected_id(DOC_ID, [["2. hai", 1], ["2.1 muc con", 0]])


def test_same_title_under_different_parents_pinned() -> None:
    tree = build([[head("1. A"), para(), head("Vi du", 15.0), para(), head("2. B"), para(), head("Vi du", 15.0)]])
    examples = [s for s in tree.sections if s.title == "Vi du"]
    assert [s.section_id for s in examples] == [
        expected_id(DOC_ID, [["1. a", 0], ["vi du", 0]]),
        expected_id(DOC_ID, [["2. b", 1], ["vi du", 0]]),
    ]


def test_untitled_section_id_uses_empty_title() -> None:
    tree = build([[head("(tiếp)", 30.0), para(), head("(tiếp)", 22.0), para()]])
    outer, inner = tree.sections[1], tree.sections[2]
    assert outer.section_id == expected_id(DOC_ID, [["", 0]])
    assert inner.section_id == expected_id(DOC_ID, [["", 0], ["", 0]])


def test_root_id_ignores_chapter_title() -> None:
    built = [make_page(1, [head("1. A"), para()])]
    one = build_section_tree(DOC_ID, "ch1", "Ten mot", built, LANG)
    two = build_section_tree(DOC_ID, "ch1", "Ten hai", built, LANG)
    assert [s.section_id for s in one.sections] == [s.section_id for s in two.sections]


def test_numbered_headings_not_flagged_even_with_close_sizes() -> None:
    tree = build([[head("1. A", 20.0), para(), head("1.1 B", 19.0), para(), head("a) C", 19.0)]])
    assert [s.flags for s in tree.sections[1:]] == [[], [], []]
    assert by_title(tree, "1.1 B").level == 2


def test_heading_keywords_decide_level() -> None:
    tree = build([[head("Chương 2", 20.0), para(), head("Section 2.1", 19.0), para(), head("Phần III", 19.0)]])
    assert [(s.title, s.level, s.flags) for s in tree.sections[1:]] == [
        ("Chương 2", 1, []),
        ("Section 2.1", 2, []),
        ("Phần III", 1, []),
    ]
    assert parent_of(tree, by_title(tree, "Section 2.1")).title == "Chương 2"


def test_keyword_without_number_is_not_numbering() -> None:
    tree = build([[head("Chương trình học", 20.0), para(), head("Bài in", 20.0)]])
    assert all(s.level == 1 for s in tree.sections[1:])


def test_keywords_come_from_lang_config_only() -> None:
    bare = LANG.model_copy(update={"heading_keywords": ()})
    built = [make_page(1, [head("Chương 2", 20.0), para(), head("Section 2.1", 19.0)])]
    tree = build_section_tree(DOC_ID, None, "T", built, bare)
    assert all("level_uncertain" in s.flags for s in tree.sections[1:])


def test_equal_headings_on_same_page_not_merged() -> None:
    tree = build([[head("Lap"), para(), head("Lap"), para()]])
    laps = [s for s in tree.sections if s.title == "Lap"]
    assert len(laps) == 2
    assert all(s.flags == [] for s in laps)
    assert laps[0].section_id != laps[1].section_id


def test_non_adjacent_repeat_without_suffix_not_merged() -> None:
    tree = build([[head("Lap"), para()], [head("Khac"), para()], [head("Lap"), para()]])
    assert len([s for s in tree.sections if s.title == "Lap"]) == 2


@pytest.mark.parametrize("text", [None, "", "   "])
def test_blank_heading_text_is_untitled(text: str | None) -> None:
    context, blocks = make_page(1, [head("x")])
    blank = [b.model_copy(update={"text": text}) for b in blocks]
    tree = build_section_tree(DOC_ID, None, "T", [(context, blank)], LANG)
    section = tree.sections[1]
    assert section.title is None and "untitled" in section.flags


def test_suffix_stripped_by_position_on_normalized_text() -> None:
    tree = build([[head("Straße"), para()], [head("STRASSE (TIẾP)"), para()]])
    assert [(s.title, s.flags) for s in tree.sections[1:]] == [("Straße", ["continued_across_pages"])]
    tree = build([[head("Alpha"), para()], [head("ALPHA   (Tiếp)"), para()]])
    assert by_title(tree, "Alpha").flags == ["continued_across_pages"]


def test_keyword_numbering_decides_level_even_when_sizes_close() -> None:
    tree = build([[head("Chương 2", 20.0), para(), head("Section 2.1", 18.5), para(), head("Phần III", 18.5)]])
    assert [(s.title, s.level, s.flags) for s in tree.sections[1:]] == [
        ("Chương 2", 1, []),
        ("Section 2.1", 2, []),
        ("Phần III", 1, []),
    ]
    assert parent_of(tree, by_title(tree, "Section 2.1")).title == "Chương 2"


def test_keyword_numbering_without_size_is_not_uncertain() -> None:
    context, blocks = make_page(1, [head("Chương 2"), para(), head("Section 2.1"), para()])
    stripped = [b.model_copy(update={"line_refs": []}) for b in blocks]
    tree = build_section_tree(DOC_ID, None, None, [(context, stripped)], LANG)
    assert [(s.level, s.flags) for s in tree.sections[1:]] == [(1, []), (2, [])]
    assert parent_of(tree, by_title(tree, "Section 2.1")).title == "Chương 2"


def test_unnumbered_heading_in_close_cluster_is_uncertain_beside_numbered() -> None:
    tree = build([[head("Chương 2", 20.0), para(), para(), head("Chi tiet", 18.5), para()]])
    assert by_title(tree, "Chương 2").flags == []
    assert "level_uncertain" in by_title(tree, "Chi tiet").flags


def test_keyword_dotted_number_keeps_depth_level() -> None:
    tree = build([[head("Section 1.2", 20.0), para()]])
    assert by_title(tree, "Section 1.2").level == 2


def test_roman_pattern_is_standard_roman_only() -> None:
    from holocron_core.services.section_tree import _Numbering

    numbering = _Numbering(SectionTreeSettings(), LANG)
    assert numbering.classify("IV. Bon") == ("roman", 1)
    assert numbering.classify("C. Ket luan")[0] is None
    assert numbering.classify("Chapter IV") == ("keyword", 1)
    assert numbering.classify("Part C")[0] is None


def test_strip_suffix_branches() -> None:
    from holocron_core.services.section_tree import _strip_suffix

    assert _strip_suffix("Lap (tiep)", ["(tiep)"]) == ("Lap", True)
    assert _strip_suffix("Lap", ["(tiep)"]) == ("Lap", False)
    # "ß" casefold thành "ss": hậu tố "s" chỉ khớp một phần ký tự ⇒ không cắt; "ss" khớp trọn ký tự ⇒ cắt
    assert _strip_suffix("Maß", ["s"]) == ("Maß", False)
    assert _strip_suffix("Maß", ["ss"]) == ("Ma", True)
    assert _strip_suffix("Maß", ["ß"]) == ("Ma", True)
    assert _strip_suffix("Ma (TIẾP)", ["(tiếp)"]) == ("Ma", True)
