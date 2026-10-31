"""Test biên cho luật vai trò trang GĐ0 0D (`services.page_roles`), giết đột biến sống của báo cáo 857e035.

Mọi ngưỡng đọc từ `PdfReaderConfig` (không chép số cứng); dữ liệu dựng để một đại lượng đúng BẰNG ngưỡng. Trang tự
viết bằng `PageExtraction`, không PDF, không eval_data.
"""

from __future__ import annotations

from typing import Any

import pytest

from holocron_core.domain.documents import PageExtraction
from holocron_core.infrastructure.document_readers.config import PdfReaderConfig
from holocron_core.services.page_roles import (
    CONTENT,
    RoleDecision,
    _lines_kept,
    _nav_candidate,
    _same_line_position,
    _token_sets,
    apply_decision,
    assign_roles,
    jaccard,
    reassess_cover,
)
from tests.test_page_roles import (
    COVER_LINES,
    DOC,
    HASH_0,
    OTHER,
    _deck,
    _hash,
    _page,
    _step_pair,
)

CONFIG = PdfReaderConfig()
THIRD = "doc_00000000000000e3"
MIN_TOKENS = CONFIG.page_role_min_tokens


def _words(prefix: str, count: int, start: int = 0) -> str:
    return " ".join(f"{prefix}{index}" for index in range(start, start + count))


def _is_cover(decisions: dict[int, RoleDecision], number: int = 1) -> bool:
    return bool(decisions[number].page_role == "cover_or_course_nav")


# --------------------------------------------------------------------------- #
# Tập token và Jaccard
# --------------------------------------------------------------------------- #


def test_jaccard_is_intersection_over_union() -> None:
    assert jaccard(frozenset({"a"}), frozenset({"a", "b"})) == 0.5
    assert jaccard(frozenset({"a", "b", "c"}), frozenset({"b", "c", "d"})) == 0.5
    assert jaccard(frozenset({"a"}), frozenset({"a"})) == 1.0
    assert jaccard(frozenset({"a"}), frozenset({"b"})) == 0.0


@pytest.mark.parametrize("on_pages", [4, 5, 6])
def test_token_is_common_from_exactly_the_configured_page_share(on_pages: int) -> None:
    config = PdfReaderConfig(page_role_common_token_ratio=0.5)
    page_count = 10
    threshold = config.page_role_common_token_ratio * page_count
    pages = [
        _page(number, [f"rieng{number} {'chung' if number <= on_pages else 'khac' + str(number)}"])
        for number in range(1, page_count + 1)
    ]
    sets = _token_sets(pages, page_count, config)
    assert ("chung" not in sets.content[1]) == (on_pages >= threshold)
    assert "chung" in sets.raw[1]
    assert "rieng1" in sets.content[1]


# --------------------------------------------------------------------------- #
# cover_or_course_nav: biên số token, biên Jaccard, trang so sánh chưa đọc
# --------------------------------------------------------------------------- #


def _cover_case(
    page_words: int, other_words: int, config: PdfReaderConfig = CONFIG, shared: int | None = None
) -> dict[int, RoleDecision]:
    shared_count = min(page_words, other_words) if shared is None else shared
    page_lines = [_words("c", page_words)]
    other_lines = [_words("c", shared_count) + " " + _words("o", other_words - shared_count)]
    pages = _deck(10, {1: _page(1, page_lines)})
    others = [_page(1, other_lines, document_id=OTHER)]
    return assign_roles(pages, 10, others, config)


def test_cover_needs_at_least_the_minimum_token_count_on_both_pages() -> None:
    config = PdfReaderConfig(cover_jaccard_min=0.5)
    assert _is_cover(_cover_case(MIN_TOKENS, MIN_TOKENS, config))
    assert not _is_cover(_cover_case(MIN_TOKENS - 1, MIN_TOKENS - 1, config))
    # Trang so sánh dưới ngưỡng token bị bỏ qua dù Jaccard đủ cao.
    assert not _is_cover(_cover_case(MIN_TOKENS, MIN_TOKENS - 1, config))
    # Trang của mình dưới ngưỡng token thì không xét, dù trang so sánh đủ token.
    assert not _is_cover(_cover_case(MIN_TOKENS - 1, MIN_TOKENS, config))


def test_cover_jaccard_threshold_is_inclusive() -> None:
    shared, own = MIN_TOKENS - 2, 2
    exact = shared / (MIN_TOKENS + own)  # 6 / 10: hai tập có MIN_TOKENS token, chung `shared`
    config = PdfReaderConfig(cover_jaccard_min=exact)
    case = _cover_case(MIN_TOKENS, MIN_TOKENS, config, shared=shared)
    assert _is_cover(case)
    evidence = case[1].evidence
    assert evidence is not None and evidence.jaccard == exact
    below = _cover_case(MIN_TOKENS, MIN_TOKENS, config, shared=shared - 1)
    assert not _is_cover(below)


def test_cover_ignores_a_pending_page_that_has_enough_tokens() -> None:
    pages = _deck(4, {1: _page(1, COVER_LINES)})
    pending = [_page(1, COVER_LINES, document_id=OTHER, status="pending")]
    assert assign_roles(pages, 4, pending, CONFIG)[1] == CONTENT
    ready = [_page(1, COVER_LINES, document_id=OTHER)]
    assert assign_roles(pages, 4, ready, CONFIG)[1].page_role == "cover_or_course_nav"


def test_cover_evidence_jaccard_is_the_best_score_among_matches() -> None:
    pages = _deck(10, {1: _page(1, COVER_LINES)})
    trimmed = _page(1, COVER_LINES[:-1], document_id=OTHER)
    exact = _page(2, COVER_LINES, document_id=THIRD)
    evidence = assign_roles(pages, 10, [trimmed, exact], CONFIG)[1].evidence
    assert evidence is not None and evidence.jaccard == 1.0
    assert [(ref.document_id, ref.page_number) for ref in evidence.related_pages] == [(OTHER, 1), (THIRD, 2)]
    only_trimmed = assign_roles(pages, 10, [trimmed], CONFIG)[1].evidence
    assert only_trimmed is not None and only_trimmed.jaccard == pytest.approx(14 / 16)


# --------------------------------------------------------------------------- #
# chapter_nav: ứng viên, cụm, biên
# --------------------------------------------------------------------------- #


def test_nav_candidate_line_length_limit_is_inclusive() -> None:
    limit = CONFIG.chapter_nav_max_line_chars
    assert _nav_candidate(_page(1, ["x" * limit]), CONFIG) is True
    assert _nav_candidate(_page(1, ["x" * (limit + 1)]), CONFIG) is False
    assert _nav_candidate(_page(1, ["ngan", "x" * limit]), CONFIG) is True
    assert _nav_candidate(_page(1, ["ngan"], has_table=True), CONFIG) is False
    assert (
        _nav_candidate(
            _page(1, ["ngan"], images=[{"bbox": [0.1, 0.1, 0.2, 0.2], "sha256": None, "dhash": None}]), CONFIG
        )
        is False
    )
    assert _nav_candidate(_page(1, []), CONFIG) is True


def _nav_pair(
    numbers: tuple[int, ...], words_each: int, config: PdfReaderConfig = CONFIG, *, second: str | None = None
) -> dict[int, RoleDecision]:
    nav = _words("nv", words_each)
    special = {
        number: _page(number, [nav if second is None or index == 0 else second]) for index, number in enumerate(numbers)
    }
    return assign_roles(_deck(12, special), 12, [], config)


def test_chapter_nav_needs_the_minimum_token_count_in_the_content_set() -> None:
    first = _nav_pair((2, 8), MIN_TOKENS)
    assert first[2].page_role == "chapter_nav" and first[8].page_role == "chapter_nav"
    assert _nav_pair((2, 8), MIN_TOKENS - 1)[2] == CONTENT


def test_chapter_nav_jaccard_threshold_is_inclusive() -> None:
    shared = MIN_TOKENS - 2
    exact = shared / (MIN_TOKENS + 2)
    config = PdfReaderConfig(chapter_nav_jaccard_min=exact)
    other = _words("nv", shared) + " " + _words("zz", MIN_TOKENS - shared)
    hit = _nav_pair((2, 8), MIN_TOKENS, config, second=other)
    assert hit[2].page_role == "chapter_nav" and hit[8].page_role == "chapter_nav"
    miss_other = _words("nv", shared - 1) + " " + _words("zz", MIN_TOKENS - shared + 1)
    assert _nav_pair((2, 8), MIN_TOKENS, config, second=miss_other)[2] == CONTENT


def test_chapter_nav_cluster_size_threshold_is_inclusive() -> None:
    config = PdfReaderConfig(chapter_nav_min_pages=3)
    assert _nav_pair((2, 8), MIN_TOKENS, config)[2] == CONTENT
    three = _nav_pair((2, 6, 10), MIN_TOKENS, config)
    assert [three[n].page_role for n in (2, 6, 10)] == ["chapter_nav"] * 3
    two = _nav_pair((2, 8), MIN_TOKENS)
    assert [two[n].page_role for n in (2, 8)] == ["chapter_nav"] * 2


def test_chapter_nav_median_gap_threshold_is_inclusive() -> None:
    gap = int(CONFIG.chapter_nav_min_median_gap)
    assert _nav_pair((2, 2 + gap), MIN_TOKENS)[2].page_role == "chapter_nav"
    assert _nav_pair((2, 2 + gap - 1), MIN_TOKENS)[2] == CONTENT


# --------------------------------------------------------------------------- #
# build_step: vị trí dòng, header/footer, ngưỡng tập con
# --------------------------------------------------------------------------- #


def test_same_line_position_tolerance_is_inclusive() -> None:
    assert _same_line_position((0.0, 0.0, 1.0, 1.0), (0.5, 0.0, 1.0, 1.0), 0.5) is True
    assert _same_line_position((0.0, 0.0, 1.0, 1.0), (0.5, 0.0, 1.0, 1.0), 0.25) is False
    assert _same_line_position((0.0, 0.0, 1.0, 1.0), (0.0, 0.0, 1.0, 0.5), 0.25) is False
    assert _same_line_position((0.0, 0.0, 1.0, 1.0), (0.0, 0.0, 1.0, 1.0), 0.0) is True


def _with_lines(page: PageExtraction, lines: list[dict[str, Any]]) -> PageExtraction:
    return PageExtraction.model_validate({**page.model_dump(), "lines": lines})


def _line(index: int, text: str, bbox: list[float] | None, *, header: bool = False) -> dict[str, Any]:
    return {
        "line_index": index,
        "text": text,
        "bbox": bbox,
        "conf": 1.0,
        "engine": "text_layer",
        "is_header_footer": header,
    }


BOX = [0.0, 0.0, 1.0, 0.25]


def test_lines_kept_position_tolerance_boundary_via_config() -> None:
    page = _with_lines(_page(3, ["x"]), [_line(0, "Dong mot", BOX)])
    moved = _with_lines(_page(4, ["x"]), [_line(0, "Dong mot", [0.25, 0.0, 1.0, 0.25])])
    assert _lines_kept(page, moved, PdfReaderConfig(build_step_position_tol=0.25)) is True
    assert _lines_kept(page, moved, PdfReaderConfig(build_step_position_tol=0.2)) is False


def test_lines_kept_only_counts_content_lines_of_the_following_page() -> None:
    page = _with_lines(_page(3, ["x"]), [_line(0, "Dong mot", BOX)])
    only_header = _with_lines(_page(4, ["x"]), [_line(0, "Dong mot", BOX, header=True)])
    assert _lines_kept(page, only_header, CONFIG) is False
    no_box = _with_lines(_page(4, ["x"]), [_line(0, "Dong mot", None)])
    assert _lines_kept(page, no_box, CONFIG) is False
    present = _with_lines(_page(4, ["x"]), [_line(0, "Dong khac", BOX), _line(1, "Dong mot", BOX)])
    assert _lines_kept(page, present, CONFIG) is True


def test_lines_kept_ignores_header_footer_and_empty_lines_of_the_page() -> None:
    following = _with_lines(_page(4, ["x"]), [_line(0, "Dong mot", BOX)])
    header = _with_lines(_page(3, ["x"]), [_line(0, "Tieu de lap lai", BOX, header=True), _line(1, "Dong mot", BOX)])
    assert _lines_kept(header, following, CONFIG) is True
    empty = _with_lines(_page(3, ["x"]), [_line(0, "", BOX), _line(1, "Dong mot", BOX)])
    assert _lines_kept(empty, following, CONFIG) is True
    no_box = _with_lines(_page(3, ["x"]), [_line(0, "Dong mot", BOX), _line(1, "Dong hai", None)])
    assert _lines_kept(no_box, following, CONFIG) is False
    assert _lines_kept(_with_lines(_page(3, ["x"]), []), following, CONFIG) is True


def test_build_step_works_when_the_subset_threshold_is_one() -> None:
    config = PdfReaderConfig(build_step_subset_min=1.0)
    decision = assign_roles(_step_pair(HASH_0, _hash(CONFIG.build_step_dhash_same_max)), 6, [], config)[3]
    assert decision.page_role == "build_step" and decision.dropped


# --------------------------------------------------------------------------- #
# reassess_cover
# --------------------------------------------------------------------------- #


def _dropped_cover(related: list[tuple[str, int]], jaccard_value: float | None) -> PageExtraction:
    return _page(
        1,
        COVER_LINES,
        document_id=OTHER,
        page_role="cover_or_course_nav",
        dropped=True,
        role_evidence={
            "rule": "cover_or_course_nav",
            "related_pages": [{"document_id": doc, "page_number": number} for doc, number in related],
            "jaccard": jaccard_value,
        },
    )


def test_reassess_cover_extends_the_evidence_of_a_dropped_cover_keeping_the_best_jaccard() -> None:
    earlier = [_dropped_cover([(DOC, 1)], 1.0)]
    later = [_page(1, COVER_LINES[:-1], document_id=THIRD)]
    decision = reassess_cover(earlier, later, CONFIG)[1]
    evidence = decision.evidence
    assert decision.page_role == "cover_or_course_nav" and decision.dropped
    assert evidence is not None and evidence.jaccard == 1.0, "jaccard cũ cao hơn thì giữ"
    assert [(ref.document_id, ref.page_number) for ref in evidence.related_pages] == [(DOC, 1), (THIRD, 1)]
    lower = reassess_cover([_dropped_cover([(DOC, 1)], 0.5)], later, CONFIG)[1].evidence
    assert lower is not None and lower.jaccard == pytest.approx(14 / 16), "jaccard mới cao hơn thì lấy mới"
    unknown = reassess_cover([_dropped_cover([(DOC, 1)], None)], later, CONFIG)[1].evidence
    assert unknown is not None and unknown.jaccard == pytest.approx(14 / 16)


def test_reassess_cover_keeps_dropped_pages_of_other_roles() -> None:
    nav = _page(
        1,
        COVER_LINES,
        document_id=OTHER,
        page_role="chapter_nav",
        dropped=True,
        role_evidence={"rule": "chapter_nav", "related_pages": [{"document_id": OTHER, "page_number": 5}]},
    )
    assert reassess_cover([nav], [_page(1, COVER_LINES)], CONFIG) == {}


def test_reassess_cover_does_not_merge_uncertain_evidence_into_a_new_cover() -> None:
    base = _page(1, COVER_LINES, document_id=OTHER)
    uncertain = PageExtraction.model_validate(
        {
            **base.model_dump(),
            "status": "needs_review",
            "review_reasons": ["PAGE_ROLE_UNCERTAIN"],
            "role_evidence": {
                "rule": "build_step_uncertain",
                "related_pages": [{"document_id": OTHER, "page_number": 2}],
                "jaccard": 0.99,
                "dhash_distance": 6,
            },
        }
    )
    decision = reassess_cover([uncertain], [_page(1, COVER_LINES[:-1])], CONFIG)[1]
    evidence = decision.evidence
    assert evidence is not None
    assert [(ref.document_id, ref.page_number) for ref in evidence.related_pages] == [(DOC, 1)]
    assert evidence.jaccard == pytest.approx(14 / 16)
    updated = apply_decision(uncertain, decision)
    assert updated is not None and updated.page_role == "cover_or_course_nav" and updated.review_reasons == []
