"""Đối chiếu chéo hai lượt đọc OCR (ADR-008 mục 10, 11; SPECIFICATION §5.7).

Hàm thuần. Chỉ so token số và mã (regex `0x[0-9A-F]+|#?-?\\d+`, khớp chính xác sau chuẩn hoá NFC); văn bản của
lượt thứ hai không bao giờ được dùng cho nội dung. Dòng của hai lượt được ghép theo hộp toạ độ: mỗi dòng của lượt
thứ hai thuộc về dòng của lượt chính chồng lên nó nhiều nhất theo chiều dọc (và có chồng theo chiều ngang).

Đối chiếu chạy ở tiến trình cha (không có giới hạn OS) trên dữ liệu của worker, nên mọi bước đều có trần độ phức
tạp (`CrossCheckLimits`, lấy từ `PdfReaderConfig`): số dòng mỗi lượt, số token mỗi dòng, số cặp dòng được xét khi
ghép (chỉ các dòng chồng nhau theo trục y, tìm qua index sắp theo mép trên), và tổng số ô của bảng quy hoạch động
khi gióng token. Vượt trần ⇒ `CrossCheckLimitError`; nơi gọi coi trang như không có lượt đối chiếu.
"""

from __future__ import annotations

import bisect
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

from .config import (
    DEFAULT_CROSS_CHECK_MAX_LINE_PAIRS,
    DEFAULT_CROSS_CHECK_MAX_LINE_TOKENS,
    DEFAULT_CROSS_CHECK_MAX_LINES,
    DEFAULT_CROSS_CHECK_MAX_TOKEN_CELLS,
    PdfReaderConfig,
)
from .protocol import RawSecondaryLine, UnitBBox

TOKEN_PATTERN = re.compile(r"0x[0-9A-F]+|#?-?\d+")


class CrossCheckLimitError(Exception):
    """Đầu vào của đối chiếu chéo vượt một trần độ phức tạp; trang phải coi là không có lượt đối chiếu."""


@dataclass(frozen=True)
class CrossCheckLimits:
    """Trần độ phức tạp của một lần đối chiếu một trang (xem `PdfReaderConfig.cross_check_*`)."""

    max_lines: int = DEFAULT_CROSS_CHECK_MAX_LINES
    max_line_tokens: int = DEFAULT_CROSS_CHECK_MAX_LINE_TOKENS
    max_line_pairs: int = DEFAULT_CROSS_CHECK_MAX_LINE_PAIRS
    max_token_cells: int = DEFAULT_CROSS_CHECK_MAX_TOKEN_CELLS

    @classmethod
    def from_config(cls, config: PdfReaderConfig) -> CrossCheckLimits:
        return cls(
            max_lines=config.cross_check_max_lines,
            max_line_tokens=config.cross_check_max_line_tokens,
            max_line_pairs=config.cross_check_max_line_pairs,
            max_token_cells=config.cross_check_max_token_cells,
        )


class WorkBudget:
    """Ngân sách đếm lùi cho một lần đối chiếu; tiêu quá ngân sách ném `CrossCheckLimitError`."""

    def __init__(self, units: int, what: str) -> None:
        self._remaining = units
        self._what = what

    def spend(self, units: int) -> None:
        self._remaining -= units
        if self._remaining < 0:
            raise CrossCheckLimitError(f"đối chiếu chéo vượt trần {self._what}")


def numeric_tokens(text: str) -> list[str]:
    """Token số và mã của một dòng, theo thứ tự xuất hiện, sau chuẩn hoá NFC."""
    return TOKEN_PATTERN.findall(unicodedata.normalize("NFC", text))


def coverage_ratio(primary_lines: int, secondary_lines: int) -> float:
    """`min(n_chính, n_phụ) / max(n_chính, n_phụ)` (đã duyệt 2026-10-24); hai lượt cùng không có dòng ⇒ 1.0."""
    largest = max(primary_lines, secondary_lines)
    if largest == 0:
        return 1.0
    return min(primary_lines, secondary_lines) / largest


def _vertical_overlap(a: UnitBBox, b: UnitBBox) -> float:
    """Phần chồng theo chiều dọc chia chiều cao hộp thấp hơn; hai hộp không chồng theo chiều ngang ⇒ 0."""
    if min(a[2], b[2]) <= max(a[0], b[0]):
        return 0.0
    overlap = min(a[3], b[3]) - max(a[1], b[1])
    shortest = min(a[3] - a[1], b[3] - b[1])
    if overlap <= 0.0 or shortest <= 0.0:
        return 0.0
    return min(1.0, overlap / shortest)


def assign_secondary_lines(
    primary_boxes: Sequence[UnitBBox | None],
    secondary_lines: Sequence[RawSecondaryLine],
    min_overlap: float,
    *,
    limits: CrossCheckLimits | None = None,
) -> list[list[RawSecondaryLine]]:
    """Với mỗi dòng của lượt chính, các dòng của lượt thứ hai ghép vào nó, sắp từ trái sang phải.

    Một dòng của lượt thứ hai chỉ ghép vào đúng một dòng của lượt chính (dòng có tỉ lệ chồng dọc lớn nhất, hoà thì
    lấy dòng đứng trước), và chỉ khi tỉ lệ đó ≥ `min_overlap`.

    Chỉ xét các dòng của lượt chính có thể chồng theo trục y: dòng sắp theo mép trên, nên dòng chồng với `[y0, y1)`
    có mép trên nằm trong `(y0 − H, y1)` với `H` là chiều cao lớn nhất của các dòng lượt chính. Số cặp được xét
    cộng dồn không vượt `limits.max_line_pairs`; số dòng mỗi lượt không vượt `limits.max_lines`.
    """
    bounds = limits or CrossCheckLimits()
    if len(primary_boxes) > bounds.max_lines or len(secondary_lines) > bounds.max_lines:
        raise CrossCheckLimitError("đối chiếu chéo vượt trần số dòng")
    indexed = sorted(
        ((box[1], index, box) for index, box in enumerate(primary_boxes) if box is not None),
        key=lambda item: (item[0], item[1]),
    )
    tops = [top for top, _, _ in indexed]
    tallest = max((box[3] - box[1] for _, _, box in indexed), default=0.0)
    pairs = WorkBudget(bounds.max_line_pairs, "số cặp dòng")

    assigned: list[list[RawSecondaryLine]] = [[] for _ in primary_boxes]
    for line in secondary_lines:
        low = bisect.bisect_right(tops, line.bbox[1] - tallest)
        high = bisect.bisect_left(tops, line.bbox[3])
        pairs.spend(max(0, high - low))
        best_index, best_overlap = -1, 0.0
        for _, index, box in indexed[low:high]:
            overlap = _vertical_overlap(box, line.bbox)
            if overlap > best_overlap or (overlap == best_overlap and 0 <= index < best_index):
                best_index, best_overlap = index, overlap
        if best_index >= 0 and best_overlap > 0.0 and best_overlap >= min_overlap:
            assigned[best_index].append(line)
    for lines in assigned:
        lines.sort(key=lambda line: line.bbox[0])
    return assigned


def _lcs_matches(primary: Sequence[str], secondary: Sequence[str]) -> list[tuple[int, int]]:
    """Cặp chỉ số (lượt chính, lượt thứ hai) của một dãy con chung dài nhất, theo thứ tự tăng dần (O(n·m))."""
    rows, cols = len(primary), len(secondary)
    table = [[0] * (cols + 1) for _ in range(rows + 1)]
    for i in range(rows - 1, -1, -1):
        current, below = table[i], table[i + 1]
        token = primary[i]
        for j in range(cols - 1, -1, -1):
            current[j] = below[j + 1] + 1 if token == secondary[j] else max(below[j], current[j + 1])
    matches: list[tuple[int, int]] = []
    i = j = 0
    while i < rows and j < cols:
        if primary[i] == secondary[j]:
            matches.append((i, j))
            i, j = i + 1, j + 1
        elif table[i + 1][j] >= table[i][j + 1]:
            i += 1
        else:
            j += 1
    return matches


def compare_tokens(
    primary: Sequence[str],
    secondary: Sequence[str],
    *,
    limits: CrossCheckLimits | None = None,
    cells: WorkBudget | None = None,
) -> list[tuple[str, str | None]]:
    """Cặp (token lượt chính, token lượt thứ hai) bị lệch, sau khi gióng hai dãy token theo dãy con chung dài nhất.

    Giữa hai token khớp liền nhau, token của lượt chính bị thay ⇒ cặp với token cùng vị trí của lượt thứ hai;
    không có token tương ứng ⇒ `None`. Token chỉ có ở lượt thứ hai không tạo cặp (`TokenMismatch.primary_value`
    bắt buộc có giá trị, spec §5.7).

    Phần đầu và phần cuối giống nhau được bỏ trước (tuyến tính); phần giữa gióng bằng quy hoạch động, tốn
    `n·m` ô, trừ vào `cells` (mặc định một ngân sách mới `limits.max_token_cells`). Dãy dài hơn
    `limits.max_line_tokens` hoặc hết ngân sách ⇒ `CrossCheckLimitError`.
    """
    bounds = limits or CrossCheckLimits()
    if len(primary) > bounds.max_line_tokens or len(secondary) > bounds.max_line_tokens:
        raise CrossCheckLimitError("đối chiếu chéo vượt trần số token mỗi dòng")
    budget = cells or WorkBudget(bounds.max_token_cells, "số ô gióng token")
    start = 0
    while start < len(primary) and start < len(secondary) and primary[start] == secondary[start]:
        start += 1
    p_end, s_end = len(primary), len(secondary)
    while p_end > start and s_end > start and primary[p_end - 1] == secondary[s_end - 1]:
        p_end, s_end = p_end - 1, s_end - 1
    core_primary, core_secondary = primary[start:p_end], secondary[start:s_end]
    budget.spend(len(core_primary) * len(core_secondary))

    pairs: list[tuple[str, str | None]] = []
    p_prev = s_prev = 0
    for p_match, s_match in [*_lcs_matches(core_primary, core_secondary), (len(core_primary), len(core_secondary))]:
        for offset in range(p_match - p_prev):
            other = core_secondary[s_prev + offset] if s_prev + offset < s_match else None
            pairs.append((core_primary[p_prev + offset], other))
        p_prev, s_prev = p_match + 1, s_match + 1
    return pairs


@dataclass(frozen=True)
class LineComparison:
    """Kết quả đối chiếu của một dòng lượt chính (chỉ số theo thứ tự dòng thô của lượt chính)."""

    line: int
    mismatches: tuple[tuple[str, str | None], ...]


def cross_check_lines(
    primary_texts: Sequence[str],
    primary_boxes: Sequence[UnitBBox | None],
    secondary_lines: Sequence[RawSecondaryLine],
    min_overlap: float,
    *,
    limits: CrossCheckLimits | None = None,
) -> list[LineComparison]:
    """Dòng của lượt chính có token lệch so với các dòng của lượt thứ hai ghép vào nó (chỉ trả dòng có lệch).

    Vượt một trần của `limits` ⇒ `CrossCheckLimitError` (không trả kết quả một phần).
    """
    if len(primary_texts) != len(primary_boxes):
        raise ValueError("số văn bản và số hộp của lượt chính phải bằng nhau")
    bounds = limits or CrossCheckLimits()
    assigned = assign_secondary_lines(primary_boxes, secondary_lines, min_overlap, limits=bounds)
    cells = WorkBudget(bounds.max_token_cells, "số ô gióng token")
    comparisons: list[LineComparison] = []
    for index, (text, lines) in enumerate(zip(primary_texts, assigned, strict=True)):
        secondary_count = sum(len(line.tokens) for line in lines)
        if secondary_count > bounds.max_line_tokens:
            raise CrossCheckLimitError("đối chiếu chéo vượt trần số token mỗi dòng")
        secondary_tokens = [unicodedata.normalize("NFC", token) for line in lines for token in line.tokens]
        mismatches = compare_tokens(numeric_tokens(text), secondary_tokens, limits=bounds, cells=cells)
        if mismatches:
            comparisons.append(LineComparison(line=index, mismatches=tuple(mismatches)))
    return comparisons
