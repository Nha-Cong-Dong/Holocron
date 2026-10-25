"""Đối chiếu chéo hai lượt đọc OCR (ADR-008 mục 10, 11; SPECIFICATION §5.7).

Hàm thuần. Chỉ so token số và mã (regex `0x[0-9A-F]+|#?-?\\d+`, khớp chính xác sau chuẩn hoá NFC); văn bản của
lượt thứ hai không bao giờ được dùng cho nội dung. Dòng của hai lượt được ghép theo hộp toạ độ: mỗi dòng của lượt
thứ hai thuộc về dòng của lượt chính chồng lên nó nhiều nhất theo chiều dọc (và có chồng theo chiều ngang).
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

from .protocol import RawSecondaryLine, UnitBBox

TOKEN_PATTERN = re.compile(r"0x[0-9A-F]+|#?-?\d+")


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
) -> list[list[RawSecondaryLine]]:
    """Với mỗi dòng của lượt chính, các dòng của lượt thứ hai ghép vào nó, sắp từ trái sang phải.

    Một dòng của lượt thứ hai chỉ ghép vào đúng một dòng của lượt chính (dòng có tỉ lệ chồng dọc lớn nhất, hoà thì
    lấy dòng đứng trước), và chỉ khi tỉ lệ đó ≥ `min_overlap`.
    """
    assigned: list[list[RawSecondaryLine]] = [[] for _ in primary_boxes]
    for line in secondary_lines:
        best_index, best_overlap = -1, 0.0
        for index, box in enumerate(primary_boxes):
            if box is None:
                continue
            overlap = _vertical_overlap(box, line.bbox)
            if overlap > best_overlap:
                best_index, best_overlap = index, overlap
        if best_index >= 0 and best_overlap >= min_overlap:
            assigned[best_index].append(line)
    for lines in assigned:
        lines.sort(key=lambda line: line.bbox[0])
    return assigned


def compare_tokens(primary: Sequence[str], secondary: Sequence[str]) -> list[tuple[str, str | None]]:
    """Cặp (token lượt chính, token lượt thứ hai) bị lệch, sau khi gióng hai dãy token.

    Token của lượt chính bị thay ⇒ cặp với token tương ứng của lượt thứ hai; không có token tương ứng ⇒ `None`.
    Token chỉ có ở lượt thứ hai không tạo cặp (`TokenMismatch.primary_value` bắt buộc có giá trị, spec §5.7).
    """
    matcher = difflib.SequenceMatcher(a=list(primary), b=list(secondary), autojunk=False)
    pairs: list[tuple[str, str | None]] = []
    for operation, p0, p1, s0, s1 in matcher.get_opcodes():
        if operation in ("equal", "insert"):
            continue
        for offset in range(p1 - p0):
            other = secondary[s0 + offset] if operation == "replace" and s0 + offset < s1 else None
            pairs.append((primary[p0 + offset], other))
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
) -> list[LineComparison]:
    """Dòng của lượt chính có token lệch so với các dòng của lượt thứ hai ghép vào nó (chỉ trả dòng có lệch)."""
    if len(primary_texts) != len(primary_boxes):
        raise ValueError("số văn bản và số hộp của lượt chính phải bằng nhau")
    assigned = assign_secondary_lines(primary_boxes, secondary_lines, min_overlap)
    comparisons: list[LineComparison] = []
    for index, (text, lines) in enumerate(zip(primary_texts, assigned, strict=True)):
        secondary_tokens = [unicodedata.normalize("NFC", token) for line in lines for token in line.tokens]
        mismatches = compare_tokens(numeric_tokens(text), secondary_tokens)
        if mismatches:
            comparisons.append(LineComparison(line=index, mismatches=tuple(mismatches)))
    return comparisons
