"""Tầng 1 (lớp chữ): dựng dòng từ ký tự, `text_score` và cờ trang (ADR-008 mục 6, 9).

Hàm thuần, không gọi PDFium: worker dùng `group_lines` để chuyển ký tự thô sang dòng; tiến trình cha tính điểm và
cờ trang từ dữ liệu worker trả về. Không rẽ nhánh theo môn hay topic (CLAUDE.md §1).
"""

from __future__ import annotations

import itertools
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

from .config import PdfReaderConfig
from .protocol import RawTextLine, RawWord, UnitBBox

PdfBox = tuple[float, float, float, float]  # (left, bottom, right, top), toạ độ PDF gốc dưới-trái

# Ký tự đặc trưng của PDF tiếng Việt mã TCVN3 (ABC) hoặc VNI khi lớp chữ bị đọc như Latin-1 (R8): ký hiệu
# Latin-1 mà TCVN3 dùng cho chữ có dấu (ví dụ `®` = đ, `¬` = ơ, `¹` = ạ), dấu trừ U+2212 (ư), và chữ Latin-1
# không có trong tiếng Việt chuẩn mà TCVN3/VNI dùng làm chữ hoặc dấu thanh (ví dụ `Ö` trong `ViÖt`, `ä` trong
# `Vieät`). Chữ Latin-1 có trong tiếng Việt (à á â ã è é ê ì í ò ó ô õ ù ú ý) không nằm trong tập này.
LEGACY_VIETNAMESE_MARKERS: frozenset[str] = frozenset(
    "¡¢£¤¥¦§¨©ª«¬\u00ad®µ¶·¸¹»¼½¾×÷−" + "ÄÅÆÇËÎÏÐÑÖØÛÜÞßäåæçëîïðñöøûüþÿ"
)

_MATH_RANGES: tuple[tuple[int, int], ...] = (
    (0x0391, 0x03A9),  # chữ Hy Lạp hoa
    (0x03B1, 0x03C9),  # chữ Hy Lạp thường
    (0x2070, 0x209F),  # chỉ số trên/dưới
    (0x2200, 0x22FF),  # Mathematical Operators (∑ ∫ √ ≤ ≥ …)
    (0x27C0, 0x27EF),  # Miscellaneous Mathematical Symbols-A
    (0x2980, 0x29FF),  # Miscellaneous Mathematical Symbols-B
    (0x2A00, 0x2AFF),  # Supplemental Mathematical Operators
    (0x1D400, 0x1D7FF),  # Mathematical Alphanumeric Symbols (ADR-008 mục 9)
)
_MATH_SINGLES: frozenset[str] = frozenset("±×÷ϑϕϖϵ")


@dataclass(frozen=True)
class PdfChar:
    """Một ký tự PDFium trả về; `box` là None khi PDFium không cho hộp toạ độ."""

    text: str
    box: PdfBox | None


# --------------------------------------------------------------------------- #
# Dựng dòng
# --------------------------------------------------------------------------- #


def _clamp(value: float) -> float:
    return min(1.0, max(0.0, value))


def normalize_box(box: PdfBox, page_box: PdfBox, rotation: int) -> UnitBBox:
    """Đổi hộp toạ độ PDF sang (x0, y0, x1, y1) ∈ [0, 1], gốc trên-trái của trang ĐÃ xoay theo `/Rotate`."""
    left, bottom, right, top = page_box
    width = max(right - left, 1e-9)
    height = max(top - bottom, 1e-9)
    bl, bb, br, bt = box
    x0, x1 = _clamp((bl - left) / width), _clamp((br - left) / width)
    y0, y1 = _clamp((top - bt) / height), _clamp((top - bb) / height)
    corners = [(x0, y0), (x1, y1)]
    turn = rotation % 360
    if turn == 90:
        corners = [(1.0 - y, x) for x, y in corners]
    elif turn == 180:
        corners = [(1.0 - x, 1.0 - y) for x, y in corners]
    elif turn == 270:
        corners = [(y, 1.0 - x) for x, y in corners]
    xs = [x for x, _ in corners]
    ys = [y for _, y in corners]
    return (min(xs), min(ys), max(xs), max(ys))


def _union(boxes: Sequence[UnitBBox]) -> UnitBBox | None:
    if not boxes:
        return None
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


def _build_line(chars: Sequence[tuple[str, UnitBBox | None]]) -> RawTextLine | None:
    text = "".join(c for c, _ in chars)
    if not text.strip():
        return None
    words: list[RawWord] = []
    current: list[tuple[str, UnitBBox | None]] = []
    for item in [*chars, (" ", None)]:
        if item[0].isspace():
            word_box = _union([b for _, b in current if b is not None])
            if current and word_box is not None:
                words.append(RawWord(text="".join(c for c, _ in current), bbox=word_box))
            current = []
        else:
            current.append(item)
    return RawTextLine(text=text, bbox=_union([w.bbox for w in words]), words=words)


def group_lines(chars: Sequence[PdfChar], page_box: PdfBox, rotation: int) -> list[RawTextLine]:
    """Gom ký tự theo thứ tự đọc của PDFium thành dòng; ngắt dòng tại `\\r`/`\\n` (PDFium tự chèn giữa các dòng)."""
    lines: list[RawTextLine] = []
    current: list[tuple[str, UnitBBox | None]] = []
    for char in [*chars, PdfChar(text="\n", box=None)]:
        if char.text in ("\r", "\n"):
            line = _build_line(current)
            if line is not None:
                lines.append(line)
            current = []
            continue
        box = None if char.box is None else normalize_box(char.box, page_box, rotation)
        current.append((char.text, box))
    return lines


# --------------------------------------------------------------------------- #
# text_score (ADR-008 mục 6)
# --------------------------------------------------------------------------- #


def _is_garbage(char: str) -> bool:
    """Ký tự rác: U+FFFD, vùng Private Use, ký tự điều khiển (ký tự trắng đã bị loại trước đó)."""
    return char == "\ufffd" or unicodedata.category(char) in ("Co", "Cc")


def is_legacy_encoded_word(token: str) -> bool:
    """Từ chứa ký tự đặc trưng của TCVN3/VNI lẫn với chữ cái khác ⇒ không hợp lệ."""
    has_marker = any(c in LEGACY_VIETNAMESE_MARKERS for c in token)
    has_other_letter = any(c.isalpha() and c not in LEGACY_VIETNAMESE_MARKERS for c in token)
    return has_marker and has_other_letter


def text_sufficiency(text: str, config: PdfReaderConfig) -> float:
    """(a) min(1, số ký tự không trắng / `text_full_chars`)."""
    non_ws = sum(1 for c in text if not c.isspace())
    return min(1.0, non_ws / config.text_full_chars)


def clean_char_ratio(text: str) -> float:
    """(b) 1 − tỉ lệ ký tự rác trên số ký tự không trắng; trang không có chữ cho 1.0 (đã bị (a) chặn)."""
    non_ws = [c for c in text if not c.isspace()]
    if not non_ws:
        return 1.0
    return 1.0 - sum(1 for c in non_ws if _is_garbage(c)) / len(non_ws)


def valid_word_ratio(text: str) -> float:
    """(c) tỉ lệ từ hợp lệ; "từ" là cụm không trắng có ít nhất một chữ cái."""
    words = [token for token in text.split() if any(c.isalpha() for c in token)]
    if not words:
        return 1.0
    return 1.0 - sum(1 for w in words if is_legacy_encoded_word(w)) / len(words)


def text_score(text: str, config: PdfReaderConfig) -> float:
    """Tích ba thành phần (a)·(b)·(c), ∈ [0, 1]; tính trên văn bản đã chuẩn hoá NFC."""
    normalized = unicodedata.normalize("NFC", text)
    score = text_sufficiency(normalized, config) * clean_char_ratio(normalized) * valid_word_ratio(normalized)
    return _clamp(score)


# --------------------------------------------------------------------------- #
# Cờ trang (ADR-008 mục 6, 9)
# --------------------------------------------------------------------------- #


def is_math_symbol(char: str) -> bool:
    if char in _MATH_SINGLES:
        return True
    code = ord(char)
    return any(low <= code <= high for low, high in _MATH_RANGES)


def has_formula(text: str, config: PdfReaderConfig) -> bool:
    """Mật độ ký hiệu toán trên số ký tự không trắng đạt ngưỡng, và có đủ số ký hiệu tối thiểu."""
    non_ws = [c for c in unicodedata.normalize("NFC", text) if not c.isspace()]
    if not non_ws:
        return False
    symbols = sum(1 for c in non_ws if is_math_symbol(c))
    return symbols >= config.formula_min_symbols and symbols / len(non_ws) >= config.formula_density_min


def has_figure(image_area_ratio: float, path_area_ratio: float, config: PdfReaderConfig) -> bool:
    """PDF số: ảnh nhúng đủ lớn; diện tích đường vẽ chỉ được tính khi trang có ảnh (không dùng một mình)."""
    if image_area_ratio >= config.figure_image_area_min:
        return True
    return image_area_ratio > 0.0 and path_area_ratio >= config.figure_path_area_min


def _row_cells(line: RawTextLine, width_pt: float, height_pt: float, config: PdfReaderConfig) -> list[float]:
    """Mép trái các ô của một dòng: tách khi khoảng trống ngang lớn hơn `table_gap_em` lần chiều cao dòng."""
    if line.bbox is None or not line.words:
        return []
    line_height_pt = (line.bbox[3] - line.bbox[1]) * height_pt
    min_gap = config.table_gap_em * max(line_height_pt, 1e-9)
    words = sorted(line.words, key=lambda w: w.bbox[0])
    lefts = [words[0].bbox[0]]
    for previous, word in itertools.pairwise(words):
        if (word.bbox[0] - previous.bbox[2]) * width_pt > min_gap:
            lefts.append(word.bbox[0])
    return lefts


def has_table(lines: Sequence[RawTextLine], width_pt: float, height_pt: float, config: PdfReaderConfig) -> bool:
    """Lưới toạ độ chữ: ≥ `table_min_rows` dòng liền nhau, cùng số ô ≥ `table_min_cols`, mép trái các ô thẳng hàng."""
    run = 0
    previous: list[float] = []
    for line in lines:
        lefts = _row_cells(line, width_pt, height_pt, config)
        aligned = (
            len(lefts) >= config.table_min_cols
            and len(lefts) == len(previous)
            and all(abs(a - b) <= config.table_col_tolerance for a, b in zip(lefts, previous, strict=True))
        )
        if aligned:
            run += 1
        elif len(lefts) >= config.table_min_cols:
            run = 1
        else:
            run = 0
        if run >= config.table_min_rows:
            return True
        previous = lefts
    return False
