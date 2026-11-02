"""Vai trò trang GĐ0 0D (hàm thuần `services.page_roles`; spec §5.7 "Khử nhiễu trang và vai trò trang").

Trang dựng bằng `PageExtraction` tự viết, không PDF, không eval_data. dHash viết tay để kiểm đúng ba vùng của luật
build_step: ≤ 4 bit (bỏ mềm), 5–8 bit (giữ + cờ), > 8 bit (chuỗi ảnh song song, giữ).
"""

from __future__ import annotations

from typing import Any

import pytest

from holocron_core.domain.documents import PageExtraction
from holocron_core.infrastructure.document_readers.config import PdfReaderConfig
from holocron_core.services.page_roles import (
    CONTENT,
    apply_decision,
    assign_roles,
    image_distance,
    jaccard,
    page_tokens,
    reassess_cover,
)

CONFIG = PdfReaderConfig()
DOC = "doc_00000000000000e1"
OTHER = "doc_00000000000000e2"
SHA_A = "a" * 64
SHA_B = "b" * 64
HASH_0 = "0000000000000000"


def _hash(bits: int) -> str:
    """dHash cách `HASH_0` đúng `bits` bit."""
    return f"{(1 << bits) - 1:016x}"


def _page(
    number: int,
    lines: list[str],
    *,
    document_id: str = DOC,
    images: list[dict[str, Any]] | None = None,
    status: str = "ok",
    top: float = 0.2,
    **extra: Any,
) -> PageExtraction:
    data: dict[str, Any] = {
        "document_id": document_id,
        "page_number": number,
        "status": status,
        "tier_used": None if status in ("pending", "failed") else "text_layer",
        "text_score": None if status == "pending" else 1.0,
        "lines": [
            {
                "line_index": index,
                "text": text,
                "bbox": [0.1, top + 0.05 * index, 0.9, top + 0.05 * index + 0.04],
                "conf": 1.0,
                "engine": "text_layer",
            }
            for index, text in enumerate(lines)
        ],
        "cross_check": {"status": "not_applicable", "secondary_engine": None},
        "own_images": images or [],
        **extra,
    }
    if status == "failed":
        data["failure_reason"] = "PAGE_ERROR"
        data["lines"] = []
    if status == "needs_review":
        data["review_reasons"] = ["LOW_OCR_SCORE"]
    return PageExtraction.model_validate(data)


def _filler(number: int, count: int = 12) -> list[str]:
    """Dòng nội dung riêng của trang `number` (token không lặp giữa các trang)."""
    return [" ".join(f"w{number}x{i}y{k}" for k in range(3)) for i in range(count // 3)]


def _image(sha: str | None, dhash: str | None) -> dict[str, Any]:
    return {"bbox": [0.5, 0.5, 0.9, 0.9], "sha256": sha, "dhash": dhash}


NAV_LINES = ["Muc luc chuong", "Kien truc tap lenh", "Che do dia chi", "Ngat va ngoai le", "Bo nho dem va bus"]
COVER_LINES = ["Truong dai hoc mau", "Khoa dien tu vien thong", "Mon vi xu ly", "Giang vien mau", "Hoc ky mau nam"]


def _deck(count: int, special: dict[int, PageExtraction]) -> list[PageExtraction]:
    return [special.get(n, _page(n, _filler(n))) for n in range(1, count + 1)]


# --------------------------------------------------------------------------- #
# cover_or_course_nav
# --------------------------------------------------------------------------- #


def test_cover_matches_a_page_of_another_document_of_the_same_subject() -> None:
    pages = _deck(10, {1: _page(1, COVER_LINES)})
    others = [_page(1, COVER_LINES, document_id=OTHER), _page(2, _filler(99), document_id=OTHER)]
    decisions = assign_roles(pages, 10, others, CONFIG)
    cover = decisions[1]
    assert cover.page_role == "cover_or_course_nav" and cover.dropped
    assert cover.evidence is not None and [ref.page_number for ref in cover.evidence.related_pages] == [1]
    assert cover.evidence.related_pages[0].document_id == OTHER and cover.evidence.jaccard == 1.0
    assert all(decisions[n] == CONTENT for n in range(2, 11))
    # Không có tài liệu khác ⇒ không có bìa.
    assert assign_roles(pages, 10, [], CONFIG)[1] == CONTENT


def test_cover_ignores_pending_failed_and_short_pages() -> None:
    pages = _deck(4, {1: _page(1, COVER_LINES), 2: _page(2, ["Mot hai"])})
    others = [
        _page(1, [], document_id=OTHER, status="pending"),
        _page(2, ["Mot hai"], document_id=OTHER),
        _page(3, COVER_LINES, document_id=OTHER, status="failed"),
    ]
    decisions = assign_roles(pages, 4, others, CONFIG)
    assert decisions[1] == CONTENT and decisions[2] == CONTENT


# --------------------------------------------------------------------------- #
# chapter_nav
# --------------------------------------------------------------------------- #


def test_scattered_navigation_pages_are_chapter_nav() -> None:
    pages = _deck(12, {2: _page(2, NAV_LINES), 6: _page(6, NAV_LINES), 11: _page(11, NAV_LINES)})
    decisions = assign_roles(pages, 12, [], CONFIG)
    for number in (2, 6, 11):
        decision = decisions[number]
        assert decision.page_role == "chapter_nav" and decision.dropped
        assert decision.evidence is not None
        assert [ref.page_number for ref in decision.evidence.related_pages] == sorted({2, 6, 11} - {number})
    assert sum(d.dropped for d in decisions.values()) == 3


def test_consecutive_repeated_pages_are_not_chapter_nav() -> None:
    pages = _deck(10, {4: _page(4, NAV_LINES), 5: _page(5, NAV_LINES), 6: _page(6, NAV_LINES)})
    decisions = assign_roles(pages, 10, [], CONFIG)
    assert all(decisions[n].page_role != "chapter_nav" for n in (4, 5, 6))


def test_nav_candidate_needs_short_lines_no_table_no_own_image() -> None:
    long_line = NAV_LINES + ["x" * (CONFIG.chapter_nav_max_line_chars + 1)]
    for special in (
        {2: _page(2, long_line), 8: _page(8, long_line)},
        {2: _page(2, NAV_LINES, has_table=True), 8: _page(8, NAV_LINES, has_table=True)},
    ):
        assert assign_roles(_deck(12, special), 12, [], CONFIG)[2] == CONTENT


# --------------------------------------------------------------------------- #
# Luật chặn theo ảnh
# --------------------------------------------------------------------------- #


def test_own_image_page_never_dropped() -> None:
    """Trang có ảnh riêng không xuất hiện ở trang so sánh không bao giờ là nav hay build_step."""
    with_image = [_image(SHA_A, HASH_0)]
    nav = _deck(12, {2: _page(2, NAV_LINES, images=with_image), 8: _page(8, NAV_LINES, images=with_image)})
    assert assign_roles(nav, 12, [], CONFIG)[2] == CONTENT
    cover = _deck(4, {1: _page(1, COVER_LINES, images=[_image(SHA_A, HASH_0)])})
    other = [_page(1, COVER_LINES, document_id=OTHER, images=[_image(SHA_B, _hash(20))])]
    assert assign_roles(cover, 4, other, CONFIG)[1] == CONTENT
    # Cùng ảnh (cùng sha256) ở trang kia ⇒ không bị chặn.
    same = [_page(1, COVER_LINES, document_id=OTHER, images=[_image(SHA_A, _hash(20))])]
    assert assign_roles(cover, 4, same, CONFIG)[1].page_role == "cover_or_course_nav"
    step = _deck(6, {3: _page(3, _filler(3), images=[_image(SHA_A, HASH_0)]), 4: _page(4, _filler(3) + ["them"])})
    assert assign_roles(step, 6, [], CONFIG)[3] == CONTENT  # trang 4 không có ảnh ⇒ D không xác định


# --------------------------------------------------------------------------- #
# build_step / parallel_visual_series / vùng giữa
# --------------------------------------------------------------------------- #


def _step_pair(first_hash: str | None, second_hash: str | None, *, sha: tuple[str | None, str | None] = (None, None)):
    first = _page(3, _filler(3), images=[_image(sha[0], first_hash)])
    second = _page(4, _filler(3) + ["buoc tiep theo them dong moi"], images=[_image(sha[1], second_hash)])
    return _deck(6, {3: first, 4: second})


def test_build_step_with_same_image_is_dropped() -> None:
    decisions = assign_roles(_step_pair(HASH_0, _hash(4)), 6, [], CONFIG)
    assert decisions[3].page_role == "build_step" and decisions[3].dropped
    assert decisions[3].evidence is not None and decisions[3].evidence.dhash_distance == 4
    assert [ref.page_number for ref in decisions[3].evidence.related_pages] == [4]
    assert decisions[4] == CONTENT
    by_sha = assign_roles(_step_pair(HASH_0, _hash(30), sha=(SHA_A, SHA_A)), 6, [], CONFIG)[3]
    assert by_sha.page_role == "build_step" and by_sha.evidence is not None and by_sha.evidence.dhash_distance == 0


def test_build_step_chain_keeps_the_last_step() -> None:
    lines = _filler(3)
    pages = _deck(
        6,
        {
            2: _page(2, lines),
            3: _page(3, [*lines, "buoc hai"]),
            4: _page(4, [*lines, "buoc hai", "buoc ba"]),
        },
    )
    decisions = assign_roles(pages, 6, [], CONFIG)
    assert [decisions[n].page_role for n in (2, 3, 4)] == ["build_step", "build_step", "content"]


def test_parallel_visual_series_keeps_every_page() -> None:
    lines = _filler(70)
    pages = _deck(
        8,
        {n: _page(n, lines, images=[_image(None, _hash(10 * (n - 3)) if n > 3 else HASH_0)]) for n in (3, 4, 5, 6)},
    )
    decisions = assign_roles(pages, 8, [], CONFIG)
    assert [decisions[n].page_role for n in (3, 4, 5, 6)] == ["parallel_visual_series"] * 4
    assert not any(decisions[n].dropped for n in (3, 4, 5, 6))
    middle = decisions[4].evidence
    assert middle is not None and [ref.page_number for ref in middle.related_pages] == [3, 5]


def test_dhash_mid_zone_flags_not_drops() -> None:
    for bits in range(CONFIG.build_step_dhash_same_max + 1, CONFIG.parallel_series_dhash_min):
        decision = assign_roles(_step_pair(HASH_0, _hash(bits)), 6, [], CONFIG)[3]
        assert decision.page_role == "content" and not decision.dropped and decision.uncertain
        assert decision.evidence is not None and decision.evidence.dhash_distance == bits
    edge = assign_roles(_step_pair(HASH_0, _hash(CONFIG.parallel_series_dhash_min)), 6, [], CONFIG)[3]
    assert edge.page_role == "parallel_visual_series"


def test_build_step_requires_lines_in_place_and_token_subset() -> None:
    lines = _filler(3)
    moved = _deck(6, {3: _page(3, lines), 4: _page(4, [*lines, "them"], top=0.5)})
    assert assign_roles(moved, 6, [], CONFIG)[3] == CONTENT
    different = _deck(6, {3: _page(3, lines), 4: _page(4, _filler(40))})
    assert assign_roles(different, 6, [], CONFIG)[3] == CONTENT


def test_image_distance_rules() -> None:
    plain = _page(1, ["a"])
    assert image_distance(plain, _page(2, ["a"])) == 0
    pictured = _page(1, ["a"], images=[_image(None, None)])
    assert image_distance(pictured, _page(2, ["a"], images=[_image(None, HASH_0)])) is None


# --------------------------------------------------------------------------- #
# Trang không đổi được; tập token; áp quyết định
# --------------------------------------------------------------------------- #


def test_only_changeable_pages_get_decisions() -> None:
    nav = NAV_LINES
    special = {
        1: _page(1, nav),
        2: _page(2, [], status="pending"),
        3: _page(3, [], status="failed"),
        4: _page(4, nav, confirmed=True),
        7: _page(7, nav, restored=True),
        10: _page(10, nav),
    }
    decisions = assign_roles(_deck(10, special), 10, [], CONFIG)
    assert set(decisions) == {1, 5, 6, 8, 9, 10}
    assert decisions[1].page_role == "chapter_nav" and decisions[10].page_role == "chapter_nav"


def test_tokens_skip_header_footer_and_common_tokens() -> None:
    page = _page(1, ["Header lap lai", "Noi dung rieng"])
    header_page = PageExtraction.model_validate(
        {
            **page.model_dump(),
            "lines": [{**page.lines[0].model_dump(), "is_header_footer": True}, page.lines[1].model_dump()],
        }
    )
    assert page_tokens(header_page) == {"noi", "dung", "rieng"}
    assert jaccard(frozenset(), frozenset()) == 0.0


def test_apply_decision_is_idempotent_and_keeps_other_reasons() -> None:
    page = _page(3, _filler(3), status="needs_review")
    decisions = assign_roles(_deck(6, {3: page, 4: _page(4, [*_filler(3), "them"])}), 6, [], CONFIG)
    updated = apply_decision(page, decisions[3])
    assert updated is not None and updated.version == page.version + 1 and updated.dropped
    assert updated.review_reasons == ["LOW_OCR_SCORE"] and updated.status == "needs_review"
    assert apply_decision(updated, decisions[3]) is None
    back = apply_decision(updated, CONTENT)
    assert back is not None and not back.dropped and back.page_role == "content" and back.role_evidence is None


def test_uncertain_decision_adds_and_removes_review_reason() -> None:
    decision = assign_roles(_step_pair(HASH_0, _hash(6)), 6, [], CONFIG)[3]
    page = _step_pair(HASH_0, _hash(6))[2]
    flagged = apply_decision(page, decision)
    assert (
        flagged is not None and flagged.review_reasons == ["PAGE_ROLE_UNCERTAIN"] and flagged.status == "needs_review"
    )
    cleared = apply_decision(flagged, CONTENT)
    assert cleared is not None and cleared.review_reasons == [] and cleared.status == "ok"


def test_role_assignment_is_deterministic() -> None:
    pages = _deck(12, {2: _page(2, NAV_LINES), 7: _page(7, NAV_LINES)})
    assert assign_roles(pages, 12, [], CONFIG) == assign_roles(list(reversed(pages)), 12, [], CONFIG)


# --------------------------------------------------------------------------- #
# Đánh giá lại trang bìa của tài liệu nhập trước
# --------------------------------------------------------------------------- #


def test_reassess_cover_of_earlier_document() -> None:
    earlier = [_page(1, COVER_LINES, document_id=OTHER), _page(2, _filler(2), document_id=OTHER)]
    later = [_page(1, COVER_LINES), _page(2, _filler(5))]
    decisions = reassess_cover(earlier, later, CONFIG)
    assert set(decisions) == {1}
    evidence = decisions[1].evidence
    assert evidence is not None and [(r.document_id, r.page_number) for r in evidence.related_pages] == [(DOC, 1)]
    dropped = apply_decision(earlier[0], decisions[1])
    assert dropped is not None
    # Gọi lại với cùng tài liệu sau: không đổi gì nữa.
    assert reassess_cover([dropped, earlier[1]], later, CONFIG) == {}


@pytest.mark.parametrize("extra", [{"confirmed": True}, {"restored": True}])
def test_reassess_cover_skips_confirmed_and_restored(extra: dict[str, Any]) -> None:
    earlier = [_page(1, COVER_LINES, document_id=OTHER, **extra)]
    assert reassess_cover(earlier, [_page(1, COVER_LINES)], CONFIG) == {}
