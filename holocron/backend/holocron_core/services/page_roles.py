"""Vai trò trang và bỏ mềm (GĐ0 0D; spec §5.7 "Khử nhiễu trang và vai trò trang", §6.5; ADR-008 mục 6).

Hàm thuần trên các `PageExtraction` đã lưu: không đọc kho, không gọi PDF, không ngẫu nhiên (I4: cùng trang và cùng cấu
hình cho cùng kết quả). Luật chỉ dùng tập token, toạ độ dòng và hash ảnh; không có chuỗi theo môn hay ngôn ngữ nào
(CLAUDE.md §1). `services/document_import` gọi các hàm ở đây ở bước kết thúc lần đọc và ghi kết quả qua kho.

- Token: chuỗi con tối đa khớp `\\w+` của văn bản đã NFC và casefold (`text_layer.tokens`, dùng chung với 0C).
  `R(p)` là tập token của các dòng không phải header/footer của trang; `T(p)` là `R(p)` trừ token có trên
  `≥ page_role_common_token_ratio × page_count` trang của tài liệu.
- Thứ tự xét: `cover_or_course_nav` (so với trang của tài liệu KHÁC cùng môn), rồi `chapter_nav` (cụm trang rải
  rác), rồi `build_step` / `parallel_visual_series` (cặp trang kề nhau).
- Luật chặn theo ảnh: trang có ảnh riêng không xuất hiện ở trang so sánh không bao giờ là nav hay build_step.
- Chỉ trang `ok`/`needs_review`, chưa xác nhận, chưa hoàn tác (`restored`) mới được gán vai trò; trang khác giữ nguyên.
"""

from __future__ import annotations

import itertools
import statistics
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from ..domain.documents import (
    DROPPABLE_ROLES,
    MAX_RELATED_PAGES,
    PageExtraction,
    PageImage,
    PageRef,
    PageRole,
    PageRoleEvidence,
    ReviewReason,
)
from ..infrastructure.document_readers.config import PdfReaderConfig
from ..infrastructure.document_readers.page_objects import hamming
from ..infrastructure.document_readers.text_layer import tokens

UNCERTAIN_REASON: ReviewReason = "PAGE_ROLE_UNCERTAIN"


@dataclass(frozen=True)
class RoleDecision:
    """Vai trò máy đề xuất cho một trang: `uncertain` ⇔ `role_evidence.rule = "build_step_uncertain"`."""

    page_role: PageRole = "content"
    evidence: PageRoleEvidence | None = None
    dropped: bool = False

    @property
    def uncertain(self) -> bool:
        return self.evidence is not None and self.evidence.rule == "build_step_uncertain"


CONTENT = RoleDecision()


def readable(page: PageExtraction) -> bool:
    """Trang đã đọc xong và có văn bản để so (không `pending`, không `failed`)."""
    return page.status in ("ok", "needs_review")


def changeable(page: PageExtraction) -> bool:
    """Trang mà 0D được đổi vai trò: đã đọc, chưa xác nhận, chưa hoàn tác (§5.7 0D "Khi nào")."""
    return readable(page) and not page.confirmed and not page.restored


def reassessable(page: PageExtraction) -> bool:
    """Trang của tài liệu nhập TRƯỚC mà `reassess_cover` được đánh giá lại: như `changeable`, và cả trang do MÁY tự xác
    nhận (`confirmed_by = "auto"`; khi bỏ mềm, tự xác nhận bị bỏ theo). Trang do người dùng xác nhận (kể cả trang cũ
    `confirmed_by = null`) không đổi (§5.7 0D "Thứ tự nhập")."""
    return readable(page) and not page.restored and (not page.confirmed or page.confirmed_by == "auto")


def page_tokens(page: PageExtraction) -> frozenset[str]:
    """`R(p)`: token của các dòng không phải header/footer (mọi engine)."""
    return frozenset(token for line in page.lines if not line.is_header_footer for token in tokens(line.text))


def jaccard(first: frozenset[str], second: frozenset[str]) -> float:
    union = first | second
    return len(first & second) / len(union) if union else 0.0


def image_appears(image: PageImage, others: Sequence[PageImage], same_max: int) -> bool:
    """Ảnh "xuất hiện" ở trang kia: cùng `sha256` (khác null) hoặc Hamming dHash ≤ `same_max`."""
    for other in others:
        if image.sha256 is not None and image.sha256 == other.sha256:
            return True
        if image.dhash is not None and other.dhash is not None and hamming(image.dhash, other.dhash) <= same_max:
            return True
    return False


def blocked_by_images(page: PageExtraction, other: PageExtraction, config: PdfReaderConfig) -> bool:
    """Luật chặn theo ảnh: trang có ảnh riêng không xuất hiện ở trang so sánh."""
    return any(
        not image_appears(image, other.own_images, config.build_step_dhash_same_max) for image in page.own_images
    )


def _ref(page: PageExtraction) -> PageRef:
    return PageRef(document_id=page.document_id, page_number=page.page_number)


def _evidence(
    rule: Literal["cover_or_course_nav", "chapter_nav", "build_step", "parallel_visual_series", "build_step_uncertain"],
    related: Iterable[PageExtraction],
    *,
    jaccard_value: float | None = None,
    dhash_distance: int | None = None,
) -> PageRoleEvidence:
    refs = sorted({(page.document_id, page.page_number) for page in related})[:MAX_RELATED_PAGES]
    return PageRoleEvidence(
        rule=rule,
        related_pages=[PageRef(document_id=doc, page_number=number) for doc, number in refs],
        jaccard=None if jaccard_value is None else min(1.0, max(0.0, jaccard_value)),
        dhash_distance=dhash_distance,
    )


@dataclass(frozen=True)
class _TokenSets:
    raw: dict[int, frozenset[str]]
    content: dict[int, frozenset[str]]


def _token_sets(pages: Sequence[PageExtraction], page_count: int, config: PdfReaderConfig) -> _TokenSets:
    raw = {page.page_number: page_tokens(page) for page in pages}
    counts: Counter[str] = Counter(token for page_set in raw.values() for token in page_set)
    common = {token for token, count in counts.items() if count >= config.page_role_common_token_ratio * page_count}
    return _TokenSets(raw=raw, content={number: tokens_ - common for number, tokens_ in raw.items()})


def _cover_matches(
    page: PageExtraction,
    page_tokens_: frozenset[str],
    others: Sequence[PageExtraction],
    other_tokens: dict[tuple[str, int], frozenset[str]],
    config: PdfReaderConfig,
) -> tuple[list[PageExtraction], float]:
    matches: list[PageExtraction] = []
    best = 0.0
    if len(page_tokens_) < config.page_role_min_tokens:
        return matches, best
    for other in others:
        other_set = other_tokens[(other.document_id, other.page_number)]
        if not readable(other) or len(other_set) < config.page_role_min_tokens:
            continue
        score = jaccard(page_tokens_, other_set)
        if score >= config.cover_jaccard_min and not blocked_by_images(page, other, config):
            matches.append(other)
            best = max(best, score)
    return matches, best


def _nav_candidate(page: PageExtraction, config: PdfReaderConfig) -> bool:
    lines = [line for line in page.lines if not line.is_header_footer]
    return (
        all(len(line.text) <= config.chapter_nav_max_line_chars for line in lines)
        and not page.has_table
        and not page.own_images
    )


def _same_line_position(first: Sequence[float], second: Sequence[float], tolerance: float) -> bool:
    return all(abs(a - b) <= tolerance for a, b in zip(first, second, strict=True))


def _lines_kept(page: PageExtraction, following: PageExtraction, config: PdfReaderConfig) -> bool:
    """Điều kiện (2) của build_step: mọi dòng có chữ (không phải header/footer) của trang có ở trang sau, cùng chữ
    (bỏ khoảng trắng đầu/cuối) và lệch toạ độ ≤ `build_step_position_tol`; dòng không có hộp làm điều kiện sai."""
    later = [line for line in following.lines if not line.is_header_footer and line.bbox is not None]
    for line in page.lines:
        text = line.text.strip()
        if line.is_header_footer or not text:
            continue
        if line.bbox is None:
            return False
        box = line.bbox
        if not any(
            other.text.strip() == text
            and other.bbox is not None
            and _same_line_position(box, other.bbox, config.build_step_position_tol)
            for other in later
        ):
            return False
    return True


def image_distance(page: PageExtraction, following: PageExtraction) -> int | None:
    """`D`: lớn nhất trên các ảnh riêng của trang, của khoảng cách tới ảnh gần nhất ở trang sau (0 khi cùng
    `sha256`); trang không có ảnh riêng ⇒ 0; có ảnh mà không đo được (không ảnh, `dhash` null) ⇒ None."""
    distances: list[int] = []
    for image in page.own_images:
        if image.sha256 is not None and any(image.sha256 == other.sha256 for other in following.own_images):
            distances.append(0)
            continue
        candidates = [
            hamming(image.dhash, other.dhash)
            for other in following.own_images
            if image.dhash is not None and other.dhash is not None
        ]
        if not candidates:
            return None
        distances.append(min(candidates))
    return max(distances, default=0)


def assign_roles(
    pages: Sequence[PageExtraction],
    page_count: int,
    others: Sequence[PageExtraction],
    config: PdfReaderConfig,
) -> dict[int, RoleDecision]:
    """Vai trò của các trang đổi được (`changeable`) của MỘT tài liệu, theo `page_number`.

    `others` là trang của các tài liệu KHÁC cùng môn (cho `cover_or_course_nav`). Trang không đổi được không có trong
    kết quả. Hàm thuần: cùng đầu vào cho cùng kết quả.
    """
    ordered = sorted(pages, key=lambda page: page.page_number)
    by_number = {page.page_number: page for page in ordered}
    sets = _token_sets(ordered, page_count, config)
    other_tokens = {(page.document_id, page.page_number): page_tokens(page) for page in others}
    decisions: dict[int, RoleDecision] = {}

    # 1. cover_or_course_nav: so token thô với trang của tài liệu khác cùng môn.
    for page in ordered:
        if not readable(page):
            continue
        matches, best = _cover_matches(page, sets.raw[page.page_number], others, other_tokens, config)
        if matches:
            decisions[page.page_number] = RoleDecision(
                "cover_or_course_nav", _evidence("cover_or_course_nav", matches, jaccard_value=best), dropped=True
            )

    def participates(page: PageExtraction) -> bool:
        return (
            readable(page)
            and page.page_number not in decisions
            and len(sets.content[page.page_number]) >= config.page_role_min_tokens
        )

    # 2. chapter_nav: cụm trang gần giống nhau (không bắc cầu), thành viên rải rác.
    clusters: list[list[PageExtraction]] = []
    for page in ordered:
        if not participates(page) or not _nav_candidate(page, config):
            continue
        content = sets.content[page.page_number]
        for cluster in clusters:
            if all(
                jaccard(content, sets.content[member.page_number]) >= config.chapter_nav_jaccard_min
                for member in cluster
            ):
                cluster.append(page)
                break
        else:
            clusters.append([page])
    for cluster in clusters:
        if len(cluster) < config.chapter_nav_min_pages:
            continue
        gaps = [later.page_number - earlier.page_number for earlier, later in itertools.pairwise(cluster)]
        if statistics.median(gaps) < config.chapter_nav_min_median_gap:
            continue
        for member in cluster:
            others_in = [page for page in cluster if page is not member]
            best = max(
                jaccard(sets.content[member.page_number], sets.content[other.page_number]) for other in others_in
            )
            decisions[member.page_number] = RoleDecision(
                "chapter_nav", _evidence("chapter_nav", others_in, jaccard_value=best), dropped=True
            )

    # 3. build_step / parallel_visual_series: cặp trang kề nhau.
    own: dict[int, RoleDecision] = {}
    parallel_links: dict[int, list[tuple[PageExtraction, int, float]]] = {}
    for page in ordered:
        following = by_number.get(page.page_number + 1)
        if following is None or not participates(page) or not participates(following):
            continue
        current, later = sets.content[page.page_number], sets.content[following.page_number]
        if len(current & later) / len(current) < config.build_step_subset_min:
            continue
        if not _lines_kept(page, following, config):
            continue
        distance = image_distance(page, following)
        if distance is None:
            continue
        score = jaccard(current, later)
        if distance <= config.build_step_dhash_same_max:
            own[page.page_number] = RoleDecision(
                "build_step",
                _evidence("build_step", [following], jaccard_value=score, dhash_distance=distance),
                dropped=True,
            )
        elif distance >= config.parallel_series_dhash_min:
            parallel_links.setdefault(page.page_number, []).append((following, distance, score))
            parallel_links.setdefault(following.page_number, []).append((page, distance, score))
        else:
            own[page.page_number] = RoleDecision(
                "content",
                _evidence("build_step_uncertain", [following], jaccard_value=score, dhash_distance=distance),
            )
    for number, decision in own.items():
        decisions[number] = decision
    for number, links in parallel_links.items():
        if number in decisions:
            continue
        decisions[number] = RoleDecision(
            "parallel_visual_series",
            _evidence(
                "parallel_visual_series",
                [page for page, _, _ in links],
                jaccard_value=max(score for _, _, score in links),
                dhash_distance=max(distance for _, distance, _ in links),
            ),
        )
    return {page.page_number: decisions.get(page.page_number, CONTENT) for page in ordered if changeable(page)}


def reassess_cover(
    earlier: Sequence[PageExtraction], later: Sequence[PageExtraction], config: PdfReaderConfig
) -> dict[int, RoleDecision]:
    """Đánh giá lại trang bìa của tài liệu nhập TRƯỚC (`earlier`) khi tài liệu sau (`later`) đọc xong (0D "Thứ tự
    nhập"): trang `q` đổi được của tài liệu trước khớp trang `p` của tài liệu sau theo cùng luật (chiều `q` so với
    `p`) ⇒ `q` thành `cover_or_course_nav`, bỏ mềm (`q` tự xác nhận thì bỏ luôn tự
    xác nhận, xem `apply_decision`). `q` đã là cover thì thêm trang liên quan; `q` đã là chapter_nav hay build_step thì
    giữ. Chỉ trả trang có thay đổi."""
    later_tokens = {(page.document_id, page.page_number): page_tokens(page) for page in later}
    decisions: dict[int, RoleDecision] = {}
    for page in earlier:
        if not reassessable(page) or (page.dropped and page.page_role != "cover_or_course_nav"):
            continue
        matches, best = _cover_matches(page, page_tokens(page), later, later_tokens, config)
        if not matches:
            continue
        related: list[PageRef] = [_ref(match) for match in matches]
        if page.page_role == "cover_or_course_nav" and page.role_evidence is not None:
            if all(ref in page.role_evidence.related_pages for ref in related):
                continue
            related = [*page.role_evidence.related_pages, *related]
            previous = page.role_evidence.jaccard or 0.0
            best = max(best, previous)
        refs = sorted({(ref.document_id, ref.page_number) for ref in related})[:MAX_RELATED_PAGES]
        evidence = PageRoleEvidence(
            rule="cover_or_course_nav",
            related_pages=[PageRef(document_id=doc, page_number=number) for doc, number in refs],
            jaccard=min(1.0, best),
        )
        decisions[page.page_number] = RoleDecision("cover_or_course_nav", evidence, dropped=True)
    return decisions


def apply_decision(page: PageExtraction, decision: RoleDecision) -> PageExtraction | None:
    """Trang sau khi áp `decision` (version + 1), hoặc None khi không có gì đổi. `review_reasons` chỉ thêm/bỏ
    `PAGE_ROLE_UNCERTAIN`; `status` tính lại theo validator (rỗng ⇒ `ok`). Trang tự xác nhận (`confirmed_by = "auto"`)
    bị bỏ mềm thì mất luôn tự xác nhận (`confirmed`, `confirmed_at`, `confirmed_by`, `confirm_rule`); không đụng
    `auto_confirm_undone` (đó là việc của người dùng)."""
    reasons: list[ReviewReason] = [reason for reason in page.review_reasons if reason != UNCERTAIN_REASON]
    if decision.uncertain:
        reasons.append(UNCERTAIN_REASON)
    unchanged = (
        page.page_role == decision.page_role
        and page.role_evidence == decision.evidence
        and page.dropped == decision.dropped
        and (UNCERTAIN_REASON in page.review_reasons) == decision.uncertain
    )
    if unchanged:
        return None
    if decision.dropped and decision.page_role not in DROPPABLE_ROLES:
        raise ValueError("chỉ cover_or_course_nav, chapter_nav, build_step được bỏ mềm")
    cleared: dict[str, Any] = {}
    if decision.dropped and page.confirmed_by == "auto":
        cleared = {"confirmed": False, "confirmed_at": None, "confirmed_by": None, "confirm_rule": None}
    return PageExtraction.model_validate(
        {
            **page.model_dump(),
            **cleared,
            "version": page.version + 1,
            "page_role": decision.page_role,
            "role_evidence": None if decision.evidence is None else decision.evidence.model_dump(),
            "dropped": decision.dropped,
            "review_reasons": reasons,
            "status": "needs_review" if reasons else "ok",
        }
    )
