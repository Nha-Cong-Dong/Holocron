"""Tầng 1 (lớp chữ): dựng dòng từ ký tự, `text_score` và cờ trang (ADR-008 mục 6, 9).

Hàm thuần, không gọi PDFium: worker dùng `group_lines` để chuyển ký tự thô sang dòng; tiến trình cha tính điểm và
cờ trang từ dữ liệu worker trả về. Không rẽ nhánh theo môn hay topic (CLAUDE.md §1).
"""

from __future__ import annotations

import itertools
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from .config import PdfReaderConfig
from .protocol import MAX_RAW_LINE_CHARS, RawPdfPage, RawTextLine, RawWord, UnitBBox

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
    """Gom ký tự theo thứ tự đọc của PDFium thành dòng; ngắt dòng tại `\\r`/`\\n` (PDFium tự chèn giữa các dòng).

    Dòng dài hơn `MAX_RAW_LINE_CHARS` ký tự được ngắt thành nhiều dòng, để khớp giới hạn của `RawTextLine`.
    """
    lines: list[RawTextLine] = []
    current: list[tuple[str, UnitBBox | None]] = []
    for char in [*chars, PdfChar(text="\n", box=None)]:
        if char.text in ("\r", "\n") or len(current) >= MAX_RAW_LINE_CHARS:
            line = _build_line(current)
            if line is not None:
                lines.append(line)
            current = []
            if char.text in ("\r", "\n"):
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


OcrReason = Literal["empty_text_layer", "garbled_text_layer", "sparse_text_on_image", "image_covers_page"]


def content_image_area(raw: RawPdfPage) -> float:
    """Độ phủ ảnh dùng cho mọi luật dựa trên độ phủ: diện tích hợp của các ảnh KHÔNG phải template.

    `content_image_area_ratio` là None khi tài liệu không có ảnh template (tài liệu 1–2 trang, lượt quét không `ok`):
    khi đó là `image_area_ratio`, đúng hành vi cũ.
    """
    return raw.image_area_ratio if raw.content_image_area_ratio is None else raw.content_image_area_ratio


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
    Thứ tự xét: rỗng, rác, trang ảnh ít chữ, ảnh phủ gần kín. Ít chữ trên trang không có ảnh lớn KHÔNG phải lý do bỏ
    lớp chữ. "Ảnh" ở hai luật cuối là ảnh KHÔNG phải template (`content_image_area`, sửa đổi 2026-10-30): ảnh nền,
    dải tiêu đề lặp lại trên đa số trang không làm trang thành trang ảnh. Hai luật đầu không dùng độ phủ ảnh, nên ảnh
    template không bao giờ làm trang có lớp chữ rỗng hay rác thoát OCR.
    """
    text = page_text(raw.lines)
    non_ws = non_whitespace_count(text)
    if non_ws == 0:
        return "empty_text_layer"
    if text_score(text, config) < config.text_layer_min:
        return "garbled_text_layer"
    image = content_image_area(raw)
    if non_ws < config.text_sparse_chars and image >= config.text_sparse_image_min:
        return "sparse_text_on_image"
    if non_ws < config.text_full_image_max_chars and image >= config.text_full_image_min:
        return "image_covers_page"
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


def has_figure(image_area_ratio: float, path_area_ratio: float, config: PdfReaderConfig) -> bool:
    """PDF số: ảnh nhúng đủ lớn; diện tích đường vẽ chỉ được tính khi trang có ảnh (không dùng một mình).

    Nơi gọi truyền độ phủ ảnh KHÔNG phải template (`content_image_area`, sửa đổi 2026-10-30) cho cả ngưỡng
    `figure_image_area_min` lẫn điều kiện "có ảnh" của nhánh đường vẽ; `path_area_ratio` không trừ gì.
    """
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
