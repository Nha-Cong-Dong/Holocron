"""Số trang cấp tài liệu (ADR-010 mục 12, 13; `services/page_numbers.py`)."""

from __future__ import annotations

import re

import pytest
from pydantic import ValidationError

from holocron_core.domain.documents import ExtractedLine
from holocron_core.infrastructure.lang_patterns import load_lang_patterns
from holocron_core.services.block_pipeline import PageBlocks, build_page_blocks
from holocron_core.services.page_numbers import (
    PageNumberSettings,
    PageNumberStage,
    candidate_text,
    in_margin_band,
    page_number_value,
)
from tests.block_samples import DOC_ID, extraction_for, geo_line, geometry

LANG = load_lang_patterns()


def _page(page_number: int, footer: str, *, header_footer: bool = False, edited: str | None = None) -> PageBlocks:
    lines = [
        geo_line("body text of the page", 0.1, 0.3),
        geo_line("more body text here", 0.1, 0.318),
        geo_line(footer, 0.45, 0.95, header_footer=header_footer),
    ]
    geo = geometry(lines)
    extraction = extraction_for(geo, page_number)
    if edited is not None:
        changed = extraction.lines[2].model_copy(update={"text": edited, "edited_by_human": True})
        extraction = extraction.model_copy(update={"lines": [*extraction.lines[:2], changed]})
    built = build_page_blocks(
        document_id=DOC_ID, page_number=page_number, geometry=geo, extraction=extraction, lang=LANG
    )
    assert not built.layout_failed
    return built


def _types(page: PageBlocks) -> list[str]:
    return [block.type for block in sorted(page.blocks, key=lambda b: b.reading_order)]


def _by_type(page: PageBlocks) -> dict[str, int]:
    assert page.ledger is not None and page.ledger.balanced
    return {item.type: item.count for item in page.ledger.by_type}


def test_page_number_candidates_retyped_and_ledger_updated() -> None:
    """≥ 3 trang cùng hiệu (số − trang) ⇒ `page_number`, `by_type` chuyển đơn vị; chỉ 2 trang ⇒ giữ loại gốc."""
    stage = PageNumberStage(LANG)
    three = stage.run([_page(n, f"Trang {n + 4}") for n in (1, 2, 3)])
    for page in three:
        assert _types(page) == ["paragraph", "page_number"]
        counts = _by_type(page)
        assert counts["page_number"] == len(f"Trang{page.page_number + 4}")
        assert counts["paragraph"] == len("bodytextofthepagemorebodytexthere")
        assert page.page_number_candidates is not None and len(page.page_number_candidates) == 1
    ids = [b.block_id for b in _page(1, "Trang 5").blocks]
    assert [b.block_id for b in three[0].blocks] == ids  # id không chứa type
    two = stage.run([_page(n, f"Trang {n + 4}") for n in (1, 2)])
    assert all(_types(page) == ["paragraph", "paragraph"] for page in two)
    assert all("page_number" not in _by_type(page) for page in two)


def test_page_numbers_rerun_is_idempotent_and_resets_candidates() -> None:
    stage = PageNumberStage(LANG)
    pages = [_page(n, str(n), header_footer=True) for n in (1, 2, 3, 4)]
    once = stage.run(pages)
    twice = stage.run(once)
    assert once == twice
    assert all(_types(page) == ["paragraph", "page_number"] for page in once)
    # Chạy lại trên tài liệu chỉ còn 2 trang của bộ cũ: ứng viên về loại gốc `header_footer`.
    reduced = stage.run(once[:2])
    assert all(_types(page) == ["paragraph", "header_footer"] for page in reduced)
    assert all("page_number" not in _by_type(page) and "header_footer" in _by_type(page) for page in reduced)


def test_inconsistent_offsets_and_non_matching_text_stay() -> None:
    stage = PageNumberStage(LANG)
    shuffled = stage.run([_page(1, "7"), _page(2, "3"), _page(3, "12")])
    assert all("page_number" not in _types(page) for page in shuffled)
    words = stage.run([_page(n, f"Step {n}") for n in (1, 2, 3)])
    assert all("page_number" not in _types(page) for page in words)


def test_roman_page_of_and_english_patterns() -> None:
    stage = PageNumberStage(LANG)
    roman = stage.run([_page(n, ["i", "ii", "iii"][n - 1]) for n in (1, 2, 3)])
    assert all(_types(page)[-1] == "page_number" for page in roman)
    page_of = stage.run([_page(n, f"Page {n} of 9") for n in (1, 2, 3)])
    assert all(_types(page)[-1] == "page_number" for page in page_of)
    slash = stage.run([_page(n, f"{n}/3") for n in (1, 2, 3)])
    assert all(_types(page)[-1] == "page_number" for page in slash)


def test_confirmed_lines_page_and_failed_pages() -> None:
    stage = PageNumberStage(LANG)
    edited = _page(3, "Trang 3", edited="Trang 3")
    assert edited.ledger is not None and edited.ledger.bases[0].ledger_basis == "confirmed_lines"
    failed = PageBlocks(document_id=DOC_ID, page_number=4, layout_failed=True)
    out = stage.run([_page(1, "Trang 1"), _page(2, "Trang 2"), edited, failed])
    assert _types(out[2])[-1] == "page_number" and out[3] == failed
    assert _by_type(out[2])["page_number"] == len("Trang3")


def test_value_parsing_and_filters() -> None:
    patterns = (*PageNumberStage(LANG)._patterns,)
    assert page_number_value("Trang  12", patterns) == 12
    assert page_number_value("- 4 -", patterns) == 4
    assert page_number_value("xiv", patterns) == 14
    assert page_number_value("iiii", patterns) is None
    assert page_number_value("chapter", patterns) is None
    settings = PageNumberSettings()
    assert candidate_text("Trang 12", settings) and candidate_text("iv", settings)
    assert not candidate_text("Course notes", settings) and not candidate_text("1234567890123", settings)
    assert in_margin_band((0.4, 0.95, 0.6, 0.97), settings) and in_margin_band((0.4, 0.01, 0.6, 0.05), settings)
    assert not in_margin_band((0.4, 0.5, 0.6, 0.52), settings) and not in_margin_band(None, settings)
    with pytest.raises(ValidationError):
        PageNumberSettings(generic_patterns=(r"^\d+$",))
    assert isinstance(patterns[0], re.Pattern)


def test_typed_line_is_never_a_candidate() -> None:
    page = _page(1, "Trang 1")
    lines = page.blocks
    assert lines
    geo = geometry([geo_line("body", 0.1, 0.3)])
    extraction = extraction_for(geo)
    typed = ExtractedLine(line_index=1, text="5", bbox=None, conf=1.0, engine="human", edited_by_human=True)
    extraction = extraction.model_copy(update={"lines": [*extraction.lines, typed]})
    built = build_page_blocks(document_id=DOC_ID, page_number=1, geometry=geo, extraction=extraction, lang=LANG)
    out = PageNumberStage(LANG, PageNumberSettings(page_number_min_pages=2)).run([built])
    assert "page_number" not in _types(out[0]) and out[0].page_number_candidates == []
