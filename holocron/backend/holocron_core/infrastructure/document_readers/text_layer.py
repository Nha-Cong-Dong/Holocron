"""Tầng 1 (lớp chữ): dựng dòng từ ký tự, `text_score` và cờ trang (ADR-008 mục 6, 9).

Hàm thuần, không gọi PDFium: worker dùng `group_lines` để chuyển ký tự thô sang dòng; tiến trình cha tính điểm và
cờ trang từ dữ liệu worker trả về. Không rẽ nhánh theo môn hay topic (CLAUDE.md §1).

GĐ0 (sửa đổi 2026-10-31, spec §5.7 "Khử nhiễu trang"): ghép ký tự rời (`merge_spaced_chars`) trước khi dựng dòng,
đánh dấu header/footer (`mark_header_footer`), token (`tokens`, dùng chung 0C và 0D), cổng glyph
(`font_encoding_suspect`), luật (e) `image_needs_reading` của `ocr_reason`. Không hàm nào thêm, đổi hay sắp lại ký tự
của lớp chữ (I3): ghép ký tự rời chỉ bỏ ký tự trắng/ngắt dòng do PDFium tự sinh.
"""

from __future__ import annotations

import itertools
import math
import re
import statistics
import unicodedata
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from typing import Literal, NamedTuple

from .config import PdfReaderConfig
from .protocol import (
    FONT_NAME_MAX_CHARS,
    FONT_SIZE_MAX_PT,
    FONT_SIZE_MIN_PT,
    MAX_PAGE_FONTS,
    MAX_PAGE_WORDS,
    MAX_RAW_LINE_CHARS,
    RawFont,
    RawLineFlag,
    RawPdfPage,
    RawTextLine,
    RawWord,
    TextKey,
    UnitBBox,
)
from .template_images import matches_text_key, text_key

PdfBox = tuple[float, float, float, float]  # (left, bottom, right, top), toạ độ PDF gốc dưới-trái

# Ký tự đặc trưng của PDF tiếng Việt mã TCVN3 (ABC) hoặc VNI khi lớp chữ bị đọc như Latin-1 (R8): ký hiệu
# Latin-1 mà TCVN3 dùng cho chữ có dấu (ví dụ `®` = đ, `¬` = ơ, `¹` = ạ), dấu trừ U+2212 (ư), và chữ Latin-1
# không có trong tiếng Việt chuẩn mà TCVN3/VNI dùng làm chữ hoặc dấu thanh (ví dụ `Ö` trong `ViÖt`, `ä` trong
# `Vieät`). Chữ Latin-1 có trong tiếng Việt (à á â ã è é ê ì í ò ó ô õ ù ú ý) không nằm trong tập này.
LEGACY_VIETNAMESE_MARKERS: frozenset[str] = frozenset(
    "¡¢£¤¥¦§¨©ª«¬\u00ad®µ¶·¸¹»¼½¾×÷−" + "ÄÅÆÇËÎÏÐÑÖØÛÜÞßäåæçëîïðñöøûüþÿ"
)

# Các dấu trên cũng là ký hiệu toán hay đơn vị thông dụng (`x−1`, `a×b`, `2·3`, `a÷b`, `µs`), nên được xét theo
# vị trí trong từ (xem `_counts_as_legacy_marker`).
MATH_LIKE_MARKERS: frozenset[str] = frozenset("−×·÷µ")

# Ký hiệu nhãn hiệu hay đứng CUỐI tên riêng Latin (`ARM®`, `Intel©`, `ARM®-based`) nhưng cũng là chữ TCVN3 (`®` = đ,
# `©` = â), hay đứng ĐẦU từ (`®iÒu`, `®a`). Chỉ được miễn khi đứng cuối từ (không có chữ cái theo sau) VÀ cụm chữ ngay
# trước nó toàn chữ Latin ASCII; mọi vị trí khác vẫn là dấu TCVN3 (`Hà®`, `x®y`). `™` không phải dấu TCVN3 nên không
# cần miễn. Các chữ TCVN3 khác (`¶` = ả, `§` = Đ, `«` = ô, `»` = ằ) không được miễn ở vị trí nào (`qu¶`, `m«`).
TRAILING_SYMBOL_MARKERS: frozenset[str] = frozenset("©®")

# Phụ âm đầu và phụ âm cuối (chữ Latin ASCII) của âm tiết tiếng Việt; dùng để phân biệt từ TCVN3/VNI (`häc`,
# `ViÖt`, `Vieät`) với từ tiếng nước ngoài có chữ Latin-1 hợp lệ (`Müller`, `Größe`, `Köln`).
_VIETNAMESE_INITIALS: frozenset[str] = frozenset(
    {"", "b", "c", "ch", "d", "g", "gh", "h", "k", "kh", "l", "m", "n", "ng", "ngh", "nh", "p", "ph", "q", "r", "s"}
    | {"t", "th", "tr", "v", "x"}
)
_VIETNAMESE_FINALS: frozenset[str] = frozenset({"", "c", "ch", "m", "n", "ng", "nh", "p", "t"})
_ASCII_VOWELS: frozenset[str] = frozenset("aeiouy")

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
    """Một ký tự PDFium trả về; `box` là None khi PDFium không cho hộp toạ độ.

    `generated`: PDFium tự sinh ký tự này (khoảng trắng, ngắt dòng; `FPDFText_IsGenerated`). `flags`: cờ dòng của
    đối tượng chữ chứa ký tự (GĐ0 0B), dòng nhận hợp các cờ của ký tự của nó.

    Font (ADR-010 mục 5; chỉ đường đọc trang lớp chữ, không qua giao thức): `font` là font của đối tượng chữ chứa ký
    tự, `font_size_pt` là cỡ hiệu dụng đã kẹp và làm tròn (`effective_font_size`), `font_id` là chỉ số vào bảng font
    của trang (`assign_font_ids`; None khi font không vào được bảng).
    """

    text: str
    box: PdfBox | None
    generated: bool = False
    flags: frozenset[RawLineFlag] = field(default_factory=frozenset)
    font: RawFont | None = None
    font_size_pt: float | None = None
    font_id: int | None = None


# Token (GĐ0 0C, 0D): chuỗi con tối đa khớp `\w+` (Unicode) của văn bản đã NFC và casefold.
_TOKEN = re.compile(r"\w+")
_LINE_BREAKS = ("\r", "\n")


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


class _LineChar(NamedTuple):
    text: str
    box: UnitBBox | None
    flags: frozenset[RawLineFlag]
    font_id: int | None
    font_size_pt: float | None


def _majority[T](values: Iterable[T | None]) -> T | None:
    """Giá trị chiếm đa số (bỏ qua None); hoà ⇒ giá trị xuất hiện trước (I4); không có giá trị nào ⇒ None."""
    counts: dict[T, int] = {}
    for value in values:
        if value is not None:
            counts[value] = counts.get(value, 0) + 1
    if not counts:
        return None
    best = max(counts.values())
    return next(value for value, count in counts.items() if count == best)


def _word(chars: Sequence[_LineChar]) -> RawWord:
    return RawWord(
        text="".join(c.text for c in chars),
        bbox=_union([c.box for c in chars if c.box is not None]),
        font_id=_majority(c.font_id for c in chars),
        font_size_pt=_majority(c.font_size_pt for c in chars),
    )


def _build_line(chars: Sequence[_LineChar]) -> RawTextLine | None:
    """Dòng từ ký tự; mỗi chuỗi ký tự không trắng tối đa là một từ, kể cả từ không có hộp (`bbox = None`).

    `font_id`, `font_size_pt` của từ và dòng: giá trị chiếm đa số ký tự không trắng (`_majority`).
    """
    text = "".join(c.text for c in chars)
    if not text.strip():
        return None
    words: list[RawWord] = []
    current: list[_LineChar] = []
    for item in chars:
        if item.text.isspace():
            if current:
                words.append(_word(current))
            current = []
        else:
            current.append(item)
    if current:
        words.append(_word(current))
    flags: set[RawLineFlag] = set()
    for char in chars:
        flags |= char.flags
    voters = [c for c in chars if not c.text.isspace()]
    return RawTextLine(
        text=text,
        bbox=_union([w.bbox for w in words if w.bbox is not None]),
        words=words,
        flags=sorted(flags),
        font_id=_majority(c.font_id for c in voters),
        font_size_pt=_majority(c.font_size_pt for c in voters),
    )


def group_lines(chars: Sequence[PdfChar], page_box: PdfBox, rotation: int) -> list[RawTextLine]:
    """Gom ký tự theo thứ tự đọc của PDFium thành dòng; ngắt dòng tại `\\r`/`\\n` (PDFium tự chèn giữa các dòng).

    Dòng dài hơn `MAX_RAW_LINE_CHARS` ký tự được ngắt thành nhiều dòng, để khớp giới hạn của `RawTextLine`. Cờ của
    dòng là hợp cờ của các ký tự trong dòng (GĐ0 0B).
    """
    lines: list[RawTextLine] = []
    current: list[_LineChar] = []
    for char in [*chars, PdfChar(text="\n", box=None)]:
        if char.text in _LINE_BREAKS or len(current) >= MAX_RAW_LINE_CHARS:
            line = _build_line(current)
            if line is not None:
                lines.append(line)
            current = []
            if char.text in _LINE_BREAKS:
                continue
        box = None if char.box is None else normalize_box(char.box, page_box, rotation)
        current.append(_LineChar(char.text, box, char.flags, char.font_id, char.font_size_pt))
    return lines


def cap_boxless_words(lines: list[RawTextLine]) -> tuple[list[RawTextLine], bool]:
    """Từ không hộp không được làm trang vượt `MAX_PAGE_WORDS` (ADR-010 mục 5).

    Trang vượt trần mà có từ không hộp ⇒ bỏ MỌI từ không hộp của trang, phần tử thứ hai là True
    (`boxless_words_dropped`: các từ không còn phủ đủ dòng nên trang không có geometry). Văn bản dòng không đổi.
    Trang vượt trần chỉ bằng từ có hộp thì không đổi gì ở đây (giới hạn của `RawPdfPage` xử lý như trước).
    """
    if sum(len(line.words) for line in lines) <= MAX_PAGE_WORDS:
        return lines, False
    if all(word.bbox is not None for line in lines for word in line.words):
        return lines, False
    kept = [line.model_copy(update={"words": [w for w in line.words if w.bbox is not None]}) for line in lines]
    return kept, True


# --------------------------------------------------------------------------- #
# Font (ADR-010 mục 5)
# --------------------------------------------------------------------------- #

_SUBSET_PREFIX = re.compile(r"^[A-Z]{6}\+")
_NAME_SEPARATORS = re.compile(r"[-_, ]+")
# Token CamelCase: cụm chữ hoa đứng trước một chữ hoa + chữ thường (`PS` trong `PSBold`), chữ hoa + chữ thường, cụm
# chữ hoa, chữ số.
_CAMEL_TOKEN = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|[0-9]+")
# Cờ font của PDF (bảng 121 của PDF 1.7): bit 1 FixedPitch, bit 7 Italic.
FONT_FLAG_FIXED_PITCH = 1
FONT_FLAG_ITALIC = 1 << 6


def clean_font_name(raw: bytes) -> str | None:
    """Tên font an toàn: tới NUL đầu tiên, chỉ giữ ASCII in được (U+0020–U+007E), bỏ tiền tố subset `^[A-Z]{6}+`,
    cắt `FONT_NAME_MAX_CHARS` ký tự, bỏ khoảng trắng đầu/cuối; rỗng ⇒ None."""
    printable = bytes(byte for byte in raw.split(b"\0", 1)[0] if 0x20 <= byte <= 0x7E).decode("ascii")
    name = _SUBSET_PREFIX.sub("", printable)[:FONT_NAME_MAX_CHARS].strip()
    return name or None


def font_name_tokens(name: str | None) -> frozenset[str]:
    """Token của tên font (chữ thường): tách tại `-`, `_`, `,`, dấu cách và ranh giới CamelCase."""
    if name is None:
        return frozenset()
    return frozenset(token.lower() for part in _NAME_SEPARATORS.split(name) for token in _CAMEL_TOKEN.findall(part))


def classify_font(
    name: str | None, weight: int | None, flags: int | None, italic_angle: int | None, config: PdfReaderConfig
) -> RawFont:
    """Thuộc tính font từ dữ liệu PDFium (None: không đọc được tín hiệu đó; weight hay cờ âm cũng coi như không có).

    Đậm: weight `≥ bold_min_weight` (nguồn `weight`), ngược lại token tên (nguồn `name`). Nghiêng, đơn cách: cờ font
    (bit Italic hay góc nghiêng ≠ 0; bit FixedPitch) trước (nguồn `flag`), rồi token tên (nguồn `name`).
    """
    tokens = font_name_tokens(name)
    bold_source: Literal["weight", "name"] | None = None
    if weight is not None and weight >= config.bold_min_weight:
        bold_source = "weight"
    elif tokens & config.bold_name_tokens:
        bold_source = "name"
    font_flags = flags if flags is not None and flags >= 0 else 0
    italic_source: Literal["flag", "name"] | None = None
    if font_flags & FONT_FLAG_ITALIC or italic_angle not in (None, 0):
        italic_source = "flag"
    elif tokens & config.italic_name_tokens:
        italic_source = "name"
    monospace_source: Literal["flag", "name"] | None = None
    if font_flags & FONT_FLAG_FIXED_PITCH:
        monospace_source = "flag"
    elif tokens & config.monospace_name_tokens:
        monospace_source = "name"
    return RawFont(
        font_name=name,
        bold=bold_source is not None,
        italic=italic_source is not None,
        monospace=monospace_source is not None,
        bold_source=bold_source,
        italic_source=italic_source,
        monospace_source=monospace_source,
    )


def effective_font_size(size: float, determinants: Iterable[float]) -> float | None:
    """Cỡ chữ hiệu dụng = |cỡ PDFium| × √|det| của ma trận chữ và của từng ma trận form chứa nó (ADR-010 mục 5).

    Một det bằng 0 hay không hữu hạn, cỡ bằng 0, hoặc kết quả không hữu hạn (NaN, inf) ⇒ None; còn lại kẹp vào
    [`FONT_SIZE_MIN_PT`, `FONT_SIZE_MAX_PT`] rồi làm tròn 0.1 pt.
    """
    value = abs(size)
    for det in determinants:
        if not math.isfinite(det) or det == 0.0:
            return None
        value *= math.sqrt(abs(det))
    if not math.isfinite(value) or value == 0.0:
        return None
    return round(min(FONT_SIZE_MAX_PT, max(FONT_SIZE_MIN_PT, value)), 1)


def assign_font_ids(chars: Sequence[PdfChar]) -> tuple[list[RawFont], list[PdfChar]]:
    """Bảng font của trang và ký tự kèm `font_id` (ADR-010 mục 5).

    Chỉ ký tự không trắng có font tham gia; gọi SAU khi lọc chữ ẩn (font chỉ có ở chữ ẩn không vào bảng). Khử trùng
    theo (tên, bold, italic, monospace); `font_id` theo thứ tự xuất hiện đầu tiên. Font thứ `MAX_PAGE_FONTS + 1` trở
    đi: ký tự có `font_id = None` (cỡ chữ giữ nguyên).
    """
    fonts: list[RawFont] = []
    ids: dict[tuple[str | None, bool, bool, bool], int] = {}
    out: list[PdfChar] = []
    for char in chars:
        font = char.font
        if font is None or char.text.isspace():
            out.append(char)
            continue
        key = (font.font_name, font.bold, font.italic, font.monospace)
        font_id = ids.get(key)
        if font_id is None and len(fonts) < MAX_PAGE_FONTS:
            font_id = ids[key] = len(fonts)
            fonts.append(font)
        out.append(replace(char, font_id=font_id))
    return fonts, out


def independent_nonws_counts(
    chars: Sequence[tuple[PdfChar, int | None]], hidden: Callable[[int | None], bool]
) -> tuple[int, int]:
    """(`pdfium_nonws_total`, `hidden_nonws_dropped`): số đếm ĐỘC LẬP theo đơn vị ledger (ADR-010 mục 6).

    Đọc thẳng dòng ký tự PDFium (ký tự, chủ là đối tượng chữ chứa nó), TRƯỚC lọc chữ ẩn, ghép ký tự rời và dựng dòng;
    không gọi hàm dựng dòng/từ. Bỏ ký tự tự sinh; tách đoạn tối đa ký tự không trắng liền nhau (ngắt ở ký tự trắng,
    ký tự tự sinh, ngắt dòng và khi đổi chủ); NFC từng đoạn; cộng số code point không trắng. Đoạn có chủ mà `hidden`
    trả True (đối tượng chữ bị loại vì chữ ẩn) được cộng cả vào số thứ hai. Giới hạn đã biết: dấu kết hợp nằm ở đối
    tượng chữ khác chữ cái gốc tạo hai đoạn, NFC không ghép được như ở từ của dòng (ledger lệch và trang được báo).
    """
    total = 0
    hidden_total = 0
    segment: list[str] = []
    owner: int | None = None
    for char, char_owner in [*chars, (PdfChar(text="\n", box=None, generated=True), None)]:
        breaks = char.generated or char.text.isspace() or char.text in _LINE_BREAKS
        if segment and (breaks or char_owner != owner):
            count = sum(1 for c in unicodedata.normalize("NFC", "".join(segment)) if not c.isspace())
            total += count
            if hidden(owner):
                hidden_total += count
            segment = []
        if not breaks:
            owner = char_owner
            segment.append(char.text)
    return total, hidden_total


# --------------------------------------------------------------------------- #
# Ghép ký tự rời (GĐ0 0B(4))
# --------------------------------------------------------------------------- #


def _is_separator(char: PdfChar) -> bool:
    """Ký tự trắng hay ngắt dòng do PDFium tự sinh."""
    return char.generated and (char.text.isspace() or char.text in _LINE_BREAKS)


def _standalone_positions(chars: Sequence[PdfChar]) -> list[int]:
    """Vị trí ký tự không trắng, có hộp, mà hai bên (nếu có) là ký tự tự sinh hoặc biên của dãy: "đứng riêng"."""
    positions: list[int] = []
    for index, char in enumerate(chars):
        if char.generated or char.box is None or char.text.isspace() or not char.text:
            continue
        before_ok = index == 0 or _is_separator(chars[index - 1])
        after_ok = index == len(chars) - 1 or _is_separator(chars[index + 1])
        if before_ok and after_ok:
            positions.append(index)
    return positions


def _char_height(char: PdfChar) -> float:
    box = char.box
    return 0.0 if box is None else max(box[3] - box[1], 0.0)


def _separators_between(chars: Sequence[PdfChar], left: int, right: int) -> list[int] | None:
    """Vị trí các ký tự giữa `left` và `right` nếu tất cả là ký tự tự sinh (ít nhất một), ngược lại None."""
    between = list(range(left + 1, right))
    if not between or not all(_is_separator(chars[index]) for index in between):
        return None
    return between


def _merge_run(chars: Sequence[PdfChar], run: list[int], config: PdfReaderConfig) -> dict[int, bool] | None:
    """Quyết định cho một chuỗi ký tự đứng riêng liền nhau: {vị trí ký tự tự sinh: giữ hay bỏ}; None ⇒ không ghép.

    Cùng baseline (mép dưới lệch ≤ `char_merge_baseline_tol` × chiều cao) và khoảng cách lệch khỏi trung vị
    ≤ `char_merge_gap_tol` × chiều cao ⇒ bỏ ký tự tự sinh giữa hai ký tự (cùng từ). Khoảng cách lớn hơn trung vị cộng
    dung sai là ranh giới từ: giữ đúng một ký tự trắng tự sinh (không phải ngắt dòng); không có ký tự trắng nào thì
    chuỗi dừng ở đó (không viết thêm ký tự nào, I3).
    """
    boxes = [chars[index].box for index in run]
    if any(box is None for box in boxes):
        return None
    height = statistics.median(_char_height(chars[index]) for index in run)
    if height <= 0.0:
        return None
    bottoms = [box[1] for box in boxes if box is not None]
    if max(bottoms) - min(bottoms) > config.char_merge_baseline_tol * height:
        return None
    gaps = [right[0] - left[2] for left, right in itertools.pairwise(box for box in boxes if box is not None)]
    median_gap = statistics.median(gaps)
    tolerance = config.char_merge_gap_tol * height
    decisions: dict[int, bool] = {}
    for (left, right), gap in zip(itertools.pairwise(run), gaps, strict=True):
        between = _separators_between(chars, left, right)
        if between is None:
            return None
        if abs(gap - median_gap) <= tolerance:
            decisions.update(dict.fromkeys(between, False))
        elif gap > median_gap + tolerance:
            spaces = [index for index in between if chars[index].text not in _LINE_BREAKS]
            if not spaces:
                return None
            decisions.update({index: index == spaces[0] for index in between})
        else:
            return None
    return decisions


def _runs(chars: Sequence[PdfChar], positions: list[int]) -> list[list[int]]:
    """Các chuỗi vị trí đứng riêng liền nhau (giữa hai vị trí kề nhau chỉ có ký tự tự sinh)."""
    runs: list[list[int]] = []
    for position in positions:
        if runs and _separators_between(chars, runs[-1][-1], position) is not None:
            runs[-1].append(position)
        else:
            runs.append([position])
    return runs


def merge_spaced_chars(chars: Sequence[PdfChar], config: PdfReaderConfig) -> list[PdfChar]:
    """Ghép ký tự rời TRƯỚC khi dựng dòng (GĐ0 0B(4)): chuỗi ≥ `char_merge_min_chars` ký tự đứng riêng (PDFium tách
    bằng ký tự trắng hay ngắt dòng tự sinh), cùng baseline, khoảng cách đều ⇒ bỏ ký tự tự sinh giữa chúng để thành
    một dòng; ranh giới từ giữ một ký tự trắng tự sinh. Chuỗi không thoả điều kiện giữ nguyên; dòng thường (ký tự
    không đứng riêng) không đổi. Chỉ BỎ ký tự tự sinh, không thêm hay đổi ký tự nào (I3).
    """
    drop: set[int] = set()
    for run in _runs(chars, _standalone_positions(chars)):
        if len(run) < config.char_merge_min_chars:
            continue
        decisions = _merge_run(chars, run, config)
        if decisions is None:
            continue
        drop.update(index for index, keep in decisions.items() if not keep)
    return [char for index, char in enumerate(chars) if index not in drop]


# --------------------------------------------------------------------------- #
# Header/footer, token, cổng glyph (GĐ0 0B(3), 0B(5), 0C)
# --------------------------------------------------------------------------- #


def mark_header_footer(lines: Sequence[RawTextLine], keys: frozenset[TextKey], step: float) -> list[RawTextLine]:
    """Dòng khớp một khoá TEXT template (cùng chuỗi chuẩn hoá, hộp trong ±1 ô) ⇒ `header_footer = True` (0B(5))."""
    if not keys:
        return list(lines)
    marked: list[RawTextLine] = []
    for line in lines:
        key = text_key(line.text, line.bbox, step)
        is_template = key is not None and matches_text_key(key, keys)
        marked.append(line.model_copy(update={"header_footer": True}) if is_template else line)
    return marked


def tokens(text: str) -> list[str]:
    """Token (GĐ0 0C, 0D): chuỗi con tối đa khớp `\\w+` của văn bản đã NFC và casefold."""
    return _TOKEN.findall(unicodedata.normalize("NFC", text).casefold())


def content_lines(lines: Iterable[RawTextLine]) -> list[RawTextLine]:
    """Các dòng không phải header/footer."""
    return [line for line in lines if not line.header_footer]


def content_token_count(lines: Iterable[RawTextLine]) -> int:
    """Số token của lớp chữ (đã lọc chữ ẩn ở worker), không tính dòng header/footer (0C)."""
    return sum(len(tokens(line.text)) for line in content_lines(lines))


_GLYPH_SYMBOLS = frozenset(chr(code) for code in range(0x00A1, 0x00C0)) | {"\u00d7", "\u00f7"}


def _is_letter(char: str) -> bool:
    return unicodedata.category(char).startswith("L")


def font_encoding_suspect(text: str) -> bool:
    """Cổng glyph (GĐ0 0B(3)): U+FFFD, ký tự điều khiển (`Cc`, trừ tab), vùng PUA U+E000–U+F8FF, hoặc U+00A1–U+00BF,
    `×`, `÷` có chữ cái (`L*`) liền trước hoặc liền sau. Chỉ gắn cờ, không sửa chữ."""
    for index, char in enumerate(text):
        if char == "\ufffd" or 0xE000 <= ord(char) <= 0xF8FF:
            return True
        if unicodedata.category(char) == "Cc" and char != "\t":
            return True
        if char in _GLYPH_SYMBOLS:
            before = index > 0 and _is_letter(text[index - 1])
            after = index + 1 < len(text) and _is_letter(text[index + 1])
            if before or after:
                return True
    return False


# --------------------------------------------------------------------------- #
# text_score (ADR-008 mục 6)
# --------------------------------------------------------------------------- #


def _is_garbage(char: str) -> bool:
    """Ký tự rác: U+FFFD, vùng Private Use, ký tự điều khiển (ký tự trắng đã bị loại trước đó)."""
    return char == "\ufffd" or unicodedata.category(char) in ("Co", "Cc")


def _letter_run(token: str, start: int, step: int) -> int:
    """Số chữ cái liền nhau tính từ vị trí `start` theo hướng `step` (±1)."""
    count = 0
    index = start
    while 0 <= index < len(token) and token[index].isalpha():
        count += 1
        index += step
    return count


def _is_plain_latin(run: str) -> bool:
    """Cụm chữ chỉ gồm chữ cái Latin ASCII (không dấu)."""
    return run.isascii() and run.isalpha()


def is_vietnamese_syllable_shape(run: str) -> bool:
    """Cụm chữ có dạng một âm tiết tiếng Việt: phụ âm đầu ASCII + MỘT cụm nguyên âm liền nhau + phụ âm cuối ASCII.

    Chữ ngoài ASCII (chữ có dấu, chữ Latin-1 của TCVN3/VNI) được coi là nguyên âm. `Müller` (hai cụm nguyên âm, phụ
    âm cuối `r`), `Größe` (phụ âm đầu `gr`) và `Köln` (phụ âm cuối `ln`) không có dạng này; `häc`, `ViÖt`, `Vieät`,
    `ñöôïc` thì có.
    """
    lowered = run.lower()
    nucleus = [index for index, char in enumerate(lowered) if not char.isascii() or char in _ASCII_VOWELS]
    if not nucleus:
        return False
    start, end = nucleus[0], nucleus[-1] + 1
    if len(nucleus) != end - start:
        return False
    return lowered[:start] in _VIETNAMESE_INITIALS and lowered[end:] in _VIETNAMESE_FINALS


def _alpha_run(token: str, index: int) -> str:
    """Cụm chữ cái liền nhau chứa vị trí `index`."""
    return token[index - _letter_run(token, index - 1, -1) : index + _letter_run(token, index, 1)]


def _is_latin_trailing_symbol(token: str, index: int) -> bool:
    """Ký hiệu ở `index` đứng cuối một cụm chữ Latin ASCII (`ARM®`, `ARM®-based`): không có chữ cái theo sau."""
    left = _letter_run(token, index - 1, -1)
    return left > 0 and token[index - left : index].isascii() and not _precedes_letter(token, index)


def _precedes_letter(token: str, index: int) -> bool:
    """Sau `index` có chữ cái, chỉ cách bởi các dấu TCVN3/VNI không phải chữ cái (không qua dấu câu, chữ số)."""
    end = index + 1
    while end < len(token) and not token[end].isalpha():
        if token[end] not in LEGACY_VIETNAMESE_MARKERS:
            return False
        end += 1
    return end < len(token)


def _counts_as_legacy_marker(token: str, index: int) -> bool:
    """Ký tự ở `index` có được tính là dấu TCVN3/VNI hay không.

    Dấu không phải chữ cái (`¸`, `¬`, `¹`, `¶`, `«`…) luôn được tính, trừ `TRAILING_SYMBOL_MARKERS` (`®`, `©`) đứng
    cuối một cụm chữ Latin ASCII (`ARM®`, `Intel©`, `ARM®-based`); `®iÒu`, `®a`, `x®y`, `Hà®` vẫn bị tính.
    Chữ Latin-1 (`Ö`, `ä`, `ü`…) chỉ được tính khi cụm chữ chứa nó có dạng một âm tiết tiếng Việt
    (`is_vietnamese_syllable_shape`: `ViÖt`, `häc`), không phải từ nước ngoài (`Müller`).
    Ký hiệu toán trùng mã TCVN3 (`MATH_LIKE_MARKERS`) được xét theo vị trí: `µ` chỉ được tính khi đứng ngay sau một
    chữ cái (chữ `à` của TCVN3: `hµnh`, `vµ`); đứng đầu từ hay sau chữ số là tiền tố đơn vị (`µs`, `10µs`). `−`,
    `×`, `·`, `÷` chỉ được tính khi nằm giữa hai chữ cái và một bên là cụm từ hai chữ cái trở lên (`Tr−êng`,
    `tr×nh`). Toán hạng là số (`x−1`, `2·3`) hoặc biến một chữ (`a×b`) không bị tính. Riêng `·` nằm giữa hai cụm
    chữ Latin không dấu là dấu nhân của đơn vị (`kg·m`, `kW·h`), không bị tính; đổi lại, từ TCVN3 mà `·` (chữ `ã`)
    chỉ đứng cạnh chữ không dấu (`l·nh`) không được tính ở ký tự này, các dấu khác của trang vẫn kéo
    `valid_word_ratio` xuống.
    """
    char = token[index]
    if char not in LEGACY_VIETNAMESE_MARKERS:
        return False
    if char in TRAILING_SYMBOL_MARKERS:
        return not _is_latin_trailing_symbol(token, index)
    if char not in MATH_LIKE_MARKERS:
        return not char.isalpha() or is_vietnamese_syllable_shape(_alpha_run(token, index))
    if char == "µ":
        return index > 0 and token[index - 1].isalpha()
    left = _letter_run(token, index - 1, -1)
    right = _letter_run(token, index + 1, 1)
    if (
        char == "·"
        and _is_plain_latin(token[index - left : index])
        and _is_plain_latin(token[index + 1 : index + 1 + right])
    ):
        return False
    return left > 0 and right > 0 and max(left, right) >= 2


def is_legacy_encoded_word(token: str) -> bool:
    """Từ có dấu TCVN3/VNI (theo `_counts_as_legacy_marker`) lẫn với chữ cái khác ⇒ không hợp lệ.

    Một dấu đứng riêng (`®`), ký hiệu nhãn hiệu cuối tên Latin (`ARM®`), từ nước ngoài có chữ Latin-1 (`Müller`)
    và biểu thức toán (`x−1`, `a×b`, `2·3`, `µs`, `P(A∩B)`) vẫn hợp lệ.
    """
    has_marker = any(_counts_as_legacy_marker(token, index) for index in range(len(token)))
    has_other_letter = any(c.isalpha() and c not in LEGACY_VIETNAMESE_MARKERS for c in token)
    return has_marker and has_other_letter


def non_whitespace_count(text: str) -> int:
    """Số ký tự không trắng của văn bản đã chuẩn hoá NFC."""
    return sum(1 for c in unicodedata.normalize("NFC", text) if not c.isspace())


def clean_char_ratio(text: str) -> float:
    """(a) 1 − tỉ lệ ký tự rác trên số ký tự không trắng; trang không có chữ cho 1.0 (`text_score` tự xử lý)."""
    non_ws = [c for c in text if not c.isspace()]
    if not non_ws:
        return 1.0
    return 1.0 - sum(1 for c in non_ws if _is_garbage(c)) / len(non_ws)


def valid_word_ratio(text: str) -> float:
    """(b) tỉ lệ từ hợp lệ; "từ" là cụm không trắng có ít nhất một chữ cái."""
    words = [token for token in text.split() if any(c.isalpha() for c in token)]
    if not words:
        return 1.0
    return 1.0 - sum(1 for w in words if is_legacy_encoded_word(w)) / len(words)


def text_score(text: str, config: PdfReaderConfig) -> float:
    """Chất lượng lớp chữ: tích (a)·(b) ∈ [0, 1], tính trên văn bản đã chuẩn hoá NFC (ADR-008 mục 6).

    Chỉ đo lớp chữ có rác hay không (ký tự rác, từ mã TCVN3/VNI), không đo lớp chữ nhiều hay ít: slide ít chữ mà
    lớp chữ sạch vẫn được 1.0 (sửa đổi 2026-10-29). Trang không có ký tự không trắng nào được 0.0, nên luôn cần OCR.
    `config` giữ trong chữ ký cho các nơi gọi; công thức hiện không dùng ngưỡng nào của nó.
    """
    normalized = unicodedata.normalize("NFC", text)
    if not any(not c.isspace() for c in normalized):
        return 0.0
    return _clamp(clean_char_ratio(normalized) * valid_word_ratio(normalized))


def page_text(lines: Sequence[RawTextLine]) -> str:
    return "\n".join(line.text for line in lines)


OcrReason = Literal[
    "empty_text_layer", "garbled_text_layer", "sparse_text_on_image", "image_covers_page", "image_needs_reading"
]


def content_image_area(raw: RawPdfPage) -> float:
    """Độ phủ ảnh dùng cho mọi luật dựa trên độ phủ: diện tích hợp của các ảnh KHÔNG phải template.

    `content_image_area_ratio` là None khi tài liệu không có ảnh template (tài liệu 1–2 trang, lượt quét không `ok`):
    khi đó là `image_area_ratio`, đúng hành vi cũ.
    """
    return raw.image_area_ratio if raw.content_image_area_ratio is None else raw.content_image_area_ratio


def content_path_area(raw: RawPdfPage) -> float:
    """Diện tích đường vẽ không-template, không-nền (GĐ0 0A); None (dữ liệu trước GĐ0) ⇒ `path_area_ratio`."""
    return raw.path_area_ratio if raw.content_path_area_ratio is None else raw.content_path_area_ratio


def image_needs_reading(raw: RawPdfPage, config: PdfReaderConfig) -> bool:
    """Điều kiện (e) của 0C: lớp chữ (đã trừ header/footer) có `< figure_text_min_tokens` token VÀ ảnh nội dung phủ
    `≥ image_read_area_min` diện tích trang."""
    return (
        content_token_count(raw.lines) < config.figure_text_min_tokens
        and content_image_area(raw) >= config.image_read_area_min
    )


def ocr_reason(raw: RawPdfPage, config: PdfReaderConfig) -> OcrReason | None:
    """Lý do trang phải OCR cả trang, hoặc None khi dùng lớp chữ (ADR-008 mục 6; sửa đổi 2026-10-29).

    Hàm duy nhất quyết định trang có đi tầng 2 hay không, dùng chung cho worker (có OCR trang hay không) và tiến
    trình cha (dựng `PageExtraction`), để hai nơi không thể lệch nhau.
    - `empty_text_layer`: lớp chữ không có ký tự không trắng nào (`text_score = 0.0`).
    - `garbled_text_layer`: lớp chữ rác (`text_score < TEXT_LAYER_MIN`: ký tự rác, mã TCVN3/VNI).
    - `sparse_text_on_image`: lớp chữ sạch nhưng chỉ có dưới `text_sparse_chars` ký tự không trắng trong khi ảnh
      nhúng phủ ít nhất `text_sparse_image_min` diện tích trang: nội dung nằm trong ảnh, lớp chữ chỉ là phần khung.
    - `image_covers_page` (sửa đổi 2026-10-30): lớp chữ sạch, có dưới `text_full_image_max_chars` ký tự không trắng,
      ảnh nhúng phủ ít nhất `text_full_image_min` diện tích trang: nội dung nằm trong ảnh, chữ còn lại chỉ là tiêu
      đề, header, số trang dưới ảnh. Lớp chữ đầy đủ trên ảnh phủ kín (PDF scan đã có lớp chữ) vẫn giữ lớp chữ.
    - `image_needs_reading` (GĐ0 0C, luật (e)): lớp chữ có `< figure_text_min_tokens` token (trừ header/footer) VÀ
      ảnh nội dung phủ `≥ image_read_area_min`.
    Thứ tự xét: rỗng, rác, trang ảnh ít chữ, ảnh phủ gần kín, (e). Ít chữ trên trang không có ảnh lớn KHÔNG phải lý do
    bỏ lớp chữ. "Ảnh" ở ba luật cuối là ảnh KHÔNG phải template (`content_image_area`, sửa đổi 2026-10-30): ảnh nền,
    dải tiêu đề lặp lại trên đa số trang không làm trang thành trang ảnh; ảnh không phải template thì không bao giờ là
    nền. Hai luật đầu không dùng độ phủ ảnh, nên ảnh template không bao giờ làm trang có lớp chữ rỗng hay rác thoát
    OCR. `non_ws` của luật (c) không tính dòng header/footer (GĐ0, trả nợ BE-009); luật (d) tính mọi dòng.
    """
    text = page_text(raw.lines)
    non_ws = non_whitespace_count(text)
    if non_ws == 0:
        return "empty_text_layer"
    if text_score(text, config) < config.text_layer_min:
        return "garbled_text_layer"
    image = content_image_area(raw)
    content_non_ws = non_whitespace_count(page_text(content_lines(raw.lines)))
    if content_non_ws < config.text_sparse_chars and image >= config.text_sparse_image_min:
        return "sparse_text_on_image"
    if non_ws < config.text_full_image_max_chars and image >= config.text_full_image_min:
        return "image_covers_page"
    if image_needs_reading(raw, config):
        return "image_needs_reading"
    return None


def needs_ocr_page(raw: RawPdfPage, config: PdfReaderConfig) -> bool:
    """Trang cần OCR cả trang (tầng 2) theo `ocr_reason`."""
    return ocr_reason(raw, config) is not None


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


def has_figure(
    image_area_ratio: float,
    path_area_ratio: float,
    config: PdfReaderConfig,
    *,
    content_image_area_ratio: float | None = None,
) -> bool:
    """PDF số: ảnh nhúng đủ lớn; diện tích đường vẽ chỉ được tính khi trang có ảnh (không dùng một mình).

    Nhánh ảnh (`figure_image_area_min`) dùng `content_image_area_ratio` (None ⇒ `image_area_ratio`). Nhánh đường vẽ:
    trang có ảnh (`image_area_ratio > 0`) VÀ `path_area_ratio ≥ figure_path_area_min`. GĐ0 (sửa đổi 2026-10-31, trả
    BE-007): nơi gọi (`tiers.page_flags`) truyền độ phủ ảnh NỘI DUNG (không-template) làm cả `image_area_ratio` lẫn
    `content_image_area_ratio`, và diện tích đường vẽ không-template, không-nền làm `path_area_ratio`, nên chỉ đối
    tượng nội dung bật cờ hình.
    """
    content = image_area_ratio if content_image_area_ratio is None else content_image_area_ratio
    if content >= config.figure_image_area_min:
        return True
    return image_area_ratio > 0.0 and path_area_ratio >= config.figure_path_area_min


def _row_cells(line: RawTextLine, width_pt: float, height_pt: float, config: PdfReaderConfig) -> list[float]:
    """Mép trái các ô của một dòng: tách khi khoảng trống ngang lớn hơn `table_gap_em` lần chiều cao dòng."""
    boxes = sorted((word.bbox for word in line.words if word.bbox is not None), key=lambda box: box[0])
    if line.bbox is None or not boxes:
        return []
    line_height_pt = (line.bbox[3] - line.bbox[1]) * height_pt
    min_gap = config.table_gap_em * max(line_height_pt, 1e-9)
    lefts = [boxes[0][0]]
    for previous, box in itertools.pairwise(boxes):
        if (box[0] - previous[2]) * width_pt > min_gap:
            lefts.append(box[0])
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
