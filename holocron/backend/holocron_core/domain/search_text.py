"""Tách từ cho tìm kiếm BM25 của Knowledge Vault (ADR-006 mục 5): hàm thuần, chỉ dùng `unicodedata` và `re`.

Dùng chung cho văn bản chunk lúc lập chỉ mục và cho truy vấn:

1. Chuẩn hoá Unicode NFC (PDF hay trả chữ có dấu ở dạng tổ hợp NFD).
2. `casefold()`; `đ` vẫn là chữ riêng, không đổi thành `d`.
3. Chuẩn hoá vị trí dấu thanh của vần `oa`, `oe`, `uy`: không có phụ âm cuối thì dấu đặt trên nguyên âm đầu
   (`hòa`, `khỏe`, `thủy`), có phụ âm cuối thì đặt trên nguyên âm sau (`hoàng`, `huỳnh`). Vì vậy `hoà`/`hòa` và
   `thuỷ`/`thủy` cho cùng token. `qu` là phụ âm đầu nên `quý` giữ nguyên.
4. Token là một âm tiết: chuỗi chữ hoặc số liền nhau (`\\w` của Unicode trừ `_`), bỏ dấu câu. Không ghép từ.

Chỉ mục chính giữ dấu (`ma`, `mà`, `má`, `mã` là bốn token khác nhau). Chỉ mục phụ không dấu dùng
`no_diacritics_tokens`, bỏ mọi dấu kết hợp như tokenizer `unicode61 remove_diacritics 2` của SQLite FTS5 (`đ` vẫn
giữ, vì FTS5 cũng giữ).
"""

from __future__ import annotations

import re
import unicodedata

TOKEN_RE = re.compile(r"[^\W_]+")

# Dấu thanh tiếng Việt ở dạng kết hợp (NFD): huyền, sắc, hỏi, ngã, nặng.
TONE_MARKS = frozenset({"̀", "́", "̉", "̃", "̣"})
VOWELS = frozenset("aăâeêioôơuưy")
# Vần có hai cách đặt dấu thanh mà ADR-006 mục 5 chuẩn hoá.
TONE_SHIFT_RIMES = frozenset({"oa", "oe", "uy"})
# Phụ âm cuối hợp lệ ("" là âm tiết mở); âm tiết có đuôi khác (số, chữ ngoại) giữ nguyên.
CODAS = frozenset({"", "c", "ch", "m", "n", "ng", "nh", "p", "t"})


def _split_tone(syllable: str) -> tuple[str, str | None, int]:
    """`(âm tiết bỏ dấu thanh ở dạng NFC, dấu thanh, số dấu thanh)` của một âm tiết đã `casefold`."""
    tones: list[str] = []
    base: list[str] = []
    for char in unicodedata.normalize("NFD", syllable):
        if char in TONE_MARKS:
            tones.append(char)
        else:
            base.append(char)
    return unicodedata.normalize("NFC", "".join(base)), (tones[0] if tones else None), len(tones)


def _onset_length(base: str) -> int:
    """Độ dài phụ âm đầu; `qu` là phụ âm đầu (chữ `u` của nó không thuộc vần)."""
    if base.startswith("qu"):
        return 2
    index = 0
    while index < len(base) and base[index] not in VOWELS:
        index += 1
    return index


def normalize_tone_placement(syllable: str) -> str:
    """Đặt lại dấu thanh của một âm tiết (chữ thường, NFC) theo quy ước ở bước 3; âm tiết khác giữ nguyên."""
    base, tone, tone_count = _split_tone(syllable)
    if tone is None or tone_count != 1:
        return syllable
    onset = _onset_length(base)
    nucleus_end = onset
    while nucleus_end < len(base) and base[nucleus_end] in VOWELS:
        nucleus_end += 1
    rime = base[onset:nucleus_end]
    coda = base[nucleus_end:]
    onset_is_letters = onset == 0 or base[:onset].isalpha()
    if rime not in TONE_SHIFT_RIMES or coda not in CODAS or not onset_is_letters:
        return syllable
    target = onset + (1 if coda else 0)
    placed = base[: target + 1] + tone + base[target + 1 :]
    return unicodedata.normalize("NFC", placed)


def _normalize_word(match: re.Match[str]) -> str:
    return normalize_tone_placement(match.group(0))


def normalize_search_text(text: str) -> str:
    """Bước 1–3: NFC, `casefold`, chuẩn hoá vị trí dấu thanh; giữ nguyên dấu câu và khoảng trắng."""
    folded = unicodedata.normalize("NFC", unicodedata.normalize("NFC", text).casefold())
    return TOKEN_RE.sub(_normalize_word, folded)


def search_tokens(text: str) -> list[str]:
    """Token âm tiết của chỉ mục chính (bước 1–4), đúng thứ tự xuất hiện."""
    return TOKEN_RE.findall(normalize_search_text(text))


def strip_diacritics(token: str) -> str:
    """Bỏ mọi dấu kết hợp (dấu thanh và dấu mũ, móc, trăng); `đ` không phải dấu kết hợp nên giữ nguyên."""
    decomposed = unicodedata.normalize("NFD", token)
    return unicodedata.normalize("NFC", "".join(char for char in decomposed if not unicodedata.combining(char)))


def no_diacritics_tokens(text: str) -> list[str]:
    """Token của chỉ mục phụ không dấu: token chính bỏ dấu kết hợp."""
    return [strip_diacritics(token) for token in search_tokens(text)]


def search_body(text: str) -> str:
    """Văn bản ghi vào chỉ mục FTS5: các token chính nối bằng một dấu cách."""
    return " ".join(search_tokens(text))
