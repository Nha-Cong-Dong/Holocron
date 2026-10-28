"""Đối chiếu chéo có trần độ phức tạp ở tiến trình cha (ADR-008 mục 3, 11; review tầng 2, lỗi 1).

Tiến trình cha không có giới hạn OS, nên dữ liệu đối kháng từ một worker bị khai thác (nhiều dòng trùng hộp toạ độ,
dòng dày token) phải xong nhanh hoặc bị cắt ở trần cấu hình; trang khi đó thành `needs_review` với
`CROSS_CHECK_UNAVAILABLE`, không bao giờ được coi là khớp.
"""

from __future__ import annotations

import random
import statistics
import time

import pytest

from holocron_core.domain.documents import PageExtraction
from holocron_core.infrastructure.document_readers.config import PdfReaderConfig
from holocron_core.infrastructure.document_readers.cross_check import (
    CrossCheckLimitError,
    CrossCheckLimits,
    WorkBudget,
    assign_secondary_lines,
    compare_tokens,
    cross_check_lines,
)
from holocron_core.infrastructure.document_readers.protocol import (
    MAX_PAGE_LINES,
    RawOcrLine,
    RawOcrPage,
    RawPdfPage,
    RawSecondaryLine,
    RawSecondaryPass,
    RawTextLine,
    UnitBBox,
)
from holocron_core.infrastructure.document_readers.tiers import assemble_text_layer_page

DOC_ID = "doc_00000000000000cc"
GARBLED = "HÖ ®iÒu hµnh cÊp ph¸t CPU cho c¸c tiÕn tr×nh theo l−ît vµ ®−îc ch¹y tèi ®a mét l−îng tö."
SAME_BOX: UnitBBox = (0.1, 0.1, 0.9, 0.12)
TIME_LIMIT_S = 1.0
ABSOLUTE_LIMIT_S = 3.0
RATIO_RUNS = 5
RATIO_LIMIT = 5.0


def _page(primary: list[RawOcrLine], secondary: list[RawSecondaryLine]) -> RawPdfPage:
    ocr = RawOcrPage(
        dpi=300.0,
        lines=primary,
        ink_outside_text_ratio=0.0,
        secondary=RawSecondaryPass(engine="rapidocr", lines=secondary),
    )
    return RawPdfPage(
        page_index=0,
        width_pt=612.0,
        height_pt=792.0,
        lines=[RawTextLine(text=GARBLED, bbox=SAME_BOX, words=[])],
        image_area_ratio=1.0,
        path_area_ratio=0.0,
        ocr=ocr,
    )


def _same_box_page(primary_lines: int, secondary_lines: int) -> RawPdfPage:
    primary = [RawOcrLine(text="P1 0 5", bbox=SAME_BOX, words=[], conf=0.95) for _ in range(primary_lines)]
    secondary = [RawSecondaryLine(bbox=SAME_BOX, tokens=["1", "0", "6"]) for _ in range(secondary_lines)]
    return _page(primary, secondary)


def _timed_assemble(raw: RawPdfPage, config: PdfReaderConfig) -> tuple[PageExtraction, float]:
    started = time.perf_counter()
    page = assemble_text_layer_page(raw, DOC_ID, config)
    return page, time.perf_counter() - started


def _assert_cut(page: PageExtraction) -> None:
    assert page.cross_check.status == "unavailable" and page.cross_check.secondary_engine is None
    assert page.status == "needs_review" and "CROSS_CHECK_UNAVAILABLE" in page.review_reasons
    assert all("TOKEN_MISMATCH" not in line.flags for line in page.lines)


def _median_elapsed(lines: int, config: PdfReaderConfig, runs: int = RATIO_RUNS) -> tuple[PageExtraction, float]:
    """Trang của lần chạy đầu và trung vị thời gian đối chiếu qua `runs` lần trên trang đối kháng `lines` × `lines`."""
    raw = _same_box_page(lines, lines)
    page, first = _timed_assemble(raw, config)
    samples = [first] + [_timed_assemble(raw, config)[1] for _ in range(runs - 1)]
    return page, statistics.median(samples)


def _assert_scales_linearly(small: float, large: float, small_lines: int, large_lines: int) -> None:
    """Thời gian ở cỡ lớn không quá `RATIO_LIMIT` lần thời gian ở cỡ nhỏ."""
    ratio = large / small
    assert ratio < RATIO_LIMIT, f"tỉ lệ thời gian ×{ratio:.1f} (n {small_lines} → {large_lines}) ≥ ×{RATIO_LIMIT:.0f}"


def test_adversarial_overlapping_lines_scale_linearly_and_are_cut() -> None:
    """Mọi dòng của hai lượt trùng một hộp toạ độ: thời gian không tăng theo bậc hai và trang bị cắt ở cả hai cỡ.

    Đo tỉ lệ thời gian giữa n₂ = 10·n₁ dòng thay vì chỉ ngưỡng giây tuyệt đối (CI chậm làm ngưỡng chập chờn).
    Cả hai cỡ đều vượt trần số cặp (n₁² > `cross_check_max_line_pairs`) nên đối chiếu bị cắt ở ≈ trần đó: thời gian
    gần như không tăng theo n, chỉ còn phần dựng và sắp xếp dữ liệu. Bản đúng đo được ×0.9–2.4 (n 500 → 5000);
    nới trần cặp lên 5 000 000 cho ×19–20 (n=500 không còn bị cắt); đối chiếu bậc hai thuần cho ≈ ×100.
    Ngưỡng ×5 cách bản đúng ≥ 2 lần và bắt được cả ca nới trần. Lưới tuyệt đối 3 s ở n₂ (CI đo bản đúng ≈ 1.05 s)
    chặn thêm các hồi quy làm chậm đều.
    """
    config = PdfReaderConfig()
    large_lines = config.cross_check_max_lines
    small_lines = large_lines // 10
    assert small_lines**2 > config.cross_check_max_line_pairs, "cỡ nhỏ phải vượt trần số cặp để cũng bị cắt"
    small_page, small = _median_elapsed(small_lines, config)
    large_page, large = _median_elapsed(large_lines, config)
    _assert_cut(small_page)
    _assert_cut(large_page)
    assert large < ABSOLUTE_LIMIT_S, f"đối chiếu mất {large:.2f} s"
    _assert_scales_linearly(small, large, small_lines, large_lines)


def test_ratio_check_detects_quadratic_cross_check() -> None:
    """Bỏ trần số cặp (đối chiếu bậc hai cố ý) ⇒ chính phép kiểm tỉ lệ ở test trên phải đỏ."""
    config = PdfReaderConfig(cross_check_max_line_pairs=10**9)
    _, small = _median_elapsed(100, config, runs=3)
    _, large = _median_elapsed(1000, config, runs=1)
    with pytest.raises(AssertionError, match="tỉ lệ thời gian"):
        _assert_scales_linearly(small, large, 100, 1000)


def test_adversarial_overlapping_lines_at_protocol_cap_are_cut_fast() -> None:
    """Ở trần của giao thức (10 000 × 10 000) trang bị cắt ngay ở kiểm số dòng, O(1)."""
    page, elapsed = _timed_assemble(_same_box_page(MAX_PAGE_LINES, MAX_PAGE_LINES), PdfReaderConfig())
    assert elapsed < TIME_LIMIT_S, f"đối chiếu mất {elapsed:.2f} s"
    _assert_cut(page)


def test_dense_token_lines_are_cut_at_the_cell_budget() -> None:
    """Dòng dày token khác nhau ở cả hai lượt: tổng số ô gióng token vượt trần ⇒ cắt, vẫn nhanh."""
    config = PdfReaderConfig()
    width = config.cross_check_max_line_tokens
    text = " ".join(str(i) for i in range(width))
    rows = 40
    primary = [
        RawOcrLine(text=text, bbox=(0.1, 0.02 * r, 0.9, 0.02 * r + 0.015), words=[], conf=0.95) for r in range(rows)
    ]
    secondary = [
        RawSecondaryLine(bbox=(0.1, 0.02 * r, 0.9, 0.02 * r + 0.015), tokens=[str(i + 10_000) for i in range(width)])
        for r in range(rows)
    ]
    page, elapsed = _timed_assemble(_page(primary, secondary), config)
    assert elapsed < TIME_LIMIT_S, f"đối chiếu mất {elapsed:.2f} s"
    _assert_cut(page)


def test_each_limit_is_configurable_and_cuts_to_unavailable() -> None:
    lines = [
        RawOcrLine(text=f"R{r} 1 2 3", bbox=(0.1, 0.05 * r, 0.9, 0.05 * r + 0.02), words=[], conf=0.95)
        for r in range(4)
    ]
    secondary = [
        RawSecondaryLine(bbox=(0.1, 0.05 * r, 0.9, 0.05 * r + 0.02), tokens=[str(r), "1", "2", "3"]) for r in range(4)
    ]
    raw = _page(lines, secondary)
    assert assemble_text_layer_page(raw, DOC_ID, PdfReaderConfig()).cross_check.status == "ok"
    for limit in (
        {"cross_check_max_lines": 3},
        {"cross_check_max_line_tokens": 3},
        {"cross_check_max_line_pairs": 1},
    ):
        _assert_cut(assemble_text_layer_page(raw, DOC_ID, PdfReaderConfig(**limit)))
    # Dòng giống hệt nhau không tốn ô nào (bỏ phần đầu/cuối chung trước khi gióng), nên trần 1 ô vẫn đủ.
    assert assemble_text_layer_page(raw, DOC_ID, PdfReaderConfig(cross_check_max_token_cells=1)).cross_check.status == (
        "ok"
    )
    changed = _page(lines, [*secondary[:3], RawSecondaryLine(bbox=secondary[3].bbox, tokens=["3", "1", "9", "8"])])
    assert assemble_text_layer_page(changed, DOC_ID, PdfReaderConfig()).cross_check.status == "mismatch"
    _assert_cut(assemble_text_layer_page(changed, DOC_ID, PdfReaderConfig(cross_check_max_token_cells=1)))


def test_secondary_tokens_of_one_line_are_capped_after_merging() -> None:
    """Nhiều dòng của lượt thứ hai ghép vào một dòng chính: trần số token tính trên tổng sau khi ghép."""
    limits = CrossCheckLimits(max_line_tokens=4)
    pieces = [RawSecondaryLine(bbox=(0.1 + 0.2 * k, 0.1, 0.25 + 0.2 * k, 0.12), tokens=["1", "2"]) for k in range(3)]
    with pytest.raises(CrossCheckLimitError, match="token"):
        cross_check_lines(["1 2 1 2 1 2"], [SAME_BOX], pieces, 0.5, limits=limits)
    assert cross_check_lines(["1 2 1 2"], [SAME_BOX], pieces[:2], 0.5, limits=limits) == []


def test_compare_tokens_rejects_oversized_input() -> None:
    limits = CrossCheckLimits(max_line_tokens=3, max_token_cells=4)
    with pytest.raises(CrossCheckLimitError):
        compare_tokens(["1"] * 4, [], limits=limits)
    with pytest.raises(CrossCheckLimitError):
        compare_tokens(["1", "2", "3"], ["4", "5"], limits=limits)  # 3 × 2 ô > 4
    budget = WorkBudget(4, "thử")
    assert compare_tokens(["1", "2"], ["3", "4"], limits=limits, cells=budget) == [("1", "3"), ("2", "4")]
    with pytest.raises(CrossCheckLimitError):  # ngân sách dùng chung đã hết
        compare_tokens(["1"], ["2"], limits=limits, cells=budget)


def _reference_assign(
    boxes: list[UnitBBox | None], lines: list[RawSecondaryLine], min_overlap: float
) -> list[list[RawSecondaryLine]]:
    """Cách ghép cũ O(n·m), dùng làm chuẩn so sánh."""
    assigned: list[list[RawSecondaryLine]] = [[] for _ in boxes]
    for line in lines:
        best_index, best_overlap = -1, 0.0
        for index, box in enumerate(boxes):
            if box is None or min(box[2], line.bbox[2]) <= max(box[0], line.bbox[0]):
                continue
            overlap = min(box[3], line.bbox[3]) - max(box[1], line.bbox[1])
            shortest = min(box[3] - box[1], line.bbox[3] - line.bbox[1])
            if overlap <= 0.0 or shortest <= 0.0:
                continue
            ratio = min(1.0, overlap / shortest)
            if ratio > best_overlap:
                best_index, best_overlap = index, ratio
        if best_index >= 0 and best_overlap >= min_overlap:
            assigned[best_index].append(line)
    for group in assigned:
        group.sort(key=lambda item: item.bbox[0])
    return assigned


def _random_box(rng: random.Random) -> UnitBBox:
    x0, y0 = rng.choice([0.0, 0.1, 0.3, 0.5]), round(rng.random() * 0.9, 2)
    height = rng.choice([0.0, 0.01, 0.02, 0.02, 0.03, 0.08])
    return (x0, y0, min(1.0, x0 + rng.choice([0.1, 0.4, 0.9])), min(1.0, y0 + height))


@pytest.mark.parametrize("seed", range(100))
def test_y_index_matches_the_full_scan(seed: int) -> None:
    """Index theo trục y cho đúng kết quả của cách quét mọi cặp (kể cả hoà, hộp dẹt, dòng không có hộp)."""
    rng = random.Random(seed)
    boxes: list[UnitBBox | None] = [None if rng.random() < 0.1 else _random_box(rng) for _ in range(rng.randint(0, 30))]
    lines = [RawSecondaryLine(bbox=_random_box(rng), tokens=[str(i)]) for i in range(rng.randint(0, 30))]
    min_overlap = rng.choice([0.3, 0.5, 1.0])
    assert assign_secondary_lines(boxes, lines, min_overlap) == _reference_assign(boxes, lines, min_overlap)


@pytest.mark.parametrize("seed", range(100))
def test_token_alignment_pairs_every_unmatched_primary_token(seed: int) -> None:
    """Số cặp lệch = số token lượt chính trừ độ dài dãy con chung dài nhất; dãy giống nhau không lệch."""
    rng = random.Random(seed)
    primary = [rng.choice("0123") for _ in range(rng.randint(0, 12))]
    secondary = [rng.choice("0123") for _ in range(rng.randint(0, 12))]
    rows, cols = len(primary), len(secondary)
    table = [[0] * (cols + 1) for _ in range(rows + 1)]
    for i in range(rows):
        for j in range(cols):
            table[i + 1][j + 1] = (
                table[i][j] + 1 if primary[i] == secondary[j] else max(table[i][j + 1], table[i + 1][j])
            )
    pairs = compare_tokens(primary, secondary)
    assert len(pairs) == rows - table[rows][cols]
    assert all(token in primary and (other is None or other in secondary) for token, other in pairs)
    assert compare_tokens(primary, list(primary)) == []
