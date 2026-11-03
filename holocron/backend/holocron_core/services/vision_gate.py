"""Cổng nhận chữ VLM của một vùng (ADR-009 mục 2, 3; Deterministic Core, CLAUDE.md §2.1).

Hàm THUẦN: nhận kết quả VLM của vùng, chữ OCR ĐỘC LẬP của cùng vùng (tham số; cổng không chạy OCR), độ giao của vùng
với lớp chữ và settings; trả quyết định cho vùng. Không gọi mạng, không đọc file. Chạy lại mỗi lần (kể cả khi kết quả
VLM lấy từ cache).

Nguyên tắc: không chắc ⇒ từ chối (khối thành hình + ảnh cắt + cờ `vlm_gate_rejected`), không bao giờ nhận chữ rác.
Chữ VLM chỉ được nhận khi khớp OCR độc lập theo ngưỡng (CER, Jaccard) VÀ mọi token số (`cross_check.TOKEN_PATTERN`,
dùng nguyên, không sửa) và token mã (mẫu riêng ở settings) trùng tuyệt đối theo tập đếm. Bảng: ô có số/mã lệch ⇒ cờ
BLOCKING cấp bảng `table_token_mismatch` (tầng nối giữ 409, ADR-009 mục 3.4). Mô tả `figure` do VLM viết không bao
giờ vào khối (đã bỏ khi parse ở `vision_reader`). Ngưỡng là field có mặc định, không phải biến môi trường; giá trị khởi
đầu là đề xuất suy ra, chỉ hiệu chỉnh trên bộ dev.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import Counter
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..domain.blocks import BlockFlag, BlockType
from ..infrastructure.document_readers.cross_check import TOKEN_PATTERN
from ..infrastructure.vision_reader import UNREADABLE_MARKER, VlmRegionBlock, VlmRegionResult

DEFAULT_CODE_TOKEN_PATTERN = r"(?<![0-9A-Za-z_])(?:[A-Z][A-Z0-9_]+|[A-Za-z_]+[0-9][0-9A-Za-z_]*)(?![0-9A-Za-z_])"
WHITESPACE = re.compile(r"\s+")
OcrEngine = Literal["tesseract", "rapidocr"]

GateOutcome = Literal["accepted_text", "accepted_table", "rejected", "keep_offline", "figure_confirmed"]
GateReason = Literal[
    "ok",
    "status_invalid",
    "status_unavailable",
    "text_layer_overlap",
    "ocr_missing",
    "unreadable_marker",
    "empty",
    "mixed_table",
    "too_long",
    "cer",
    "jaccard",
    "number_tokens",
    "code_tokens",
    "figure_only",
]


class VisionGateSettings(BaseModel):
    """Ngưỡng của cổng (ADR-009 mục 3; đề xuất suy ra, chờ xác nhận)."""

    model_config = ConfigDict(frozen=True)

    max_text_layer_overlap: float = Field(default=0.20, ge=0.0, le=1.0)
    max_cer: float = Field(default=0.15, ge=0.0, le=1.0)
    min_word_jaccard: float = Field(default=0.70, ge=0.0, le=1.0)
    code_token_pattern: str = DEFAULT_CODE_TOKEN_PATTERN
    max_region_chars: int = Field(default=20_000, ge=1)


class GateTokenMismatch(BaseModel):
    """Một chỗ lệch token số/mã của bảng (ADR-009 mục 3.4). VLM thừa: `row`, `col`, `vlm_value`; VLM bỏ sót (token
    OCR không có bên VLM): `row = col = vlm_value = None`, `ocr_value`."""

    model_config = ConfigDict(frozen=True)

    row: int | None = Field(default=None, ge=0)
    col: int | None = Field(default=None, ge=0)
    vlm_value: str | None = None
    ocr_value: str | None = None


class VisionGateDecision(BaseModel):
    """Quyết định cho một vùng.

    - `accepted_text`: khối chữ `source = "vlm"`, loại `block_type`, văn bản `text` (chữ VLM đã chuẩn hoá NFC).
    - `accepted_table`: khối `table` `source = "vlm"`, ô `rows`; `mismatches` khác rỗng ⇒ cờ `table_token_mismatch`.
    - `rejected`: khối thành `figure` + ảnh cắt + `vlm_gate_rejected`; không có chữ VLM nào.
    - `keep_offline`: khối offline giữ nguyên (lớp chữ phủ vùng, hoặc lỗi gọi VLM); `vlm_invalid` khi phản hồi hỏng.
    - `figure_confirmed`: VLM chỉ thấy hình; khối offline giữ nguyên.
    """

    model_config = ConfigDict(frozen=True)

    outcome: GateOutcome
    reason: GateReason
    block_type: BlockType | None = None
    text: str | None = None
    rows: list[list[str]] | None = None
    flags: list[BlockFlag] = []
    mismatches: list[GateTokenMismatch] = []


def normalize(text: str) -> str:
    """NFC, gộp mọi khoảng trắng thành một dấu cách, bỏ khoảng trắng đầu/cuối (ADR-009 mục 3.1)."""
    return WHITESPACE.sub(" ", unicodedata.normalize("NFC", text)).strip()


def strip_tone_marks(text: str) -> str:
    """Bỏ dấu (giữ chữ cơ sở) ở cả hai phía khi OCR độc lập là RapidOCR (từ điển thiếu chữ có dấu thanh)."""
    decomposed = unicodedata.normalize("NFD", text)
    return unicodedata.normalize("NFC", "".join(char for char in decomposed if not unicodedata.combining(char)))


def levenshtein(left: str, right: str) -> int:
    if len(left) < len(right):
        left, right = right, left
    previous = list(range(len(right) + 1))
    for i, char_left in enumerate(left, start=1):
        current = [i]
        for j, char_right in enumerate(right, start=1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (char_left != char_right)))
        previous = current
    return previous[-1]


def cer(vlm: str, ocr: str) -> float:
    """Khoảng cách Levenshtein ký tự chia số ký tự chuỗi OCR (hai chuỗi đã chuẩn hoá)."""
    return levenshtein(vlm, ocr) / max(1, len(ocr))


def _common_run(left: str, right: str, i: int, j: int) -> int:
    """Độ dài đoạn trùng dài nhất bắt đầu ở `left[i]`, `right[j]` (so lát cắt theo bước nhân đôi rồi chia đôi)."""
    limit = min(len(left) - i, len(right) - j)
    low, step = 0, 1
    while low < limit:
        size = min(step, limit - low)
        if left[i + low : i + low + size] != right[j + low : j + low + size]:
            break
        low += size
        step *= 2
    else:
        return low
    high = min(low + step, limit)
    while high - low > 1:
        middle = (low + high) // 2
        if left[i + low : i + middle] == right[j + low : j + middle]:
            low = middle
        else:
            high = middle
    return low if left[i + low : i + low + 1] != right[j + low : j + low + 1] else low + 1


def within_edit_distance(left: str, right: str, limit: int) -> bool:
    """`levenshtein(left, right) ≤ limit` theo Landau–Vishkin: chi phí O((n + m)·d) với d là khoảng cách thật (đoạn
    trùng được trượt bằng so lát cắt), nên vùng dài gần giống nhau được kiểm nhanh, khác nhiều thì dừng ở `limit`."""
    n, m = len(left), len(right)
    if abs(n - m) > limit:
        return False
    target = m - n
    unset = -(10**9)
    furthest: dict[int, int] = {0: _common_run(left, right, 0, 0)}
    if target == 0 and furthest[0] >= n:
        return True
    for edits in range(1, limit + 1):
        current: dict[int, int] = {}
        for diagonal in range(-edits, edits + 1):
            row = max(
                furthest.get(diagonal, unset) + 1,
                furthest.get(diagonal - 1, unset),
                furthest.get(diagonal + 1, unset) + 1,
            )
            row = min(row, n, m - diagonal)
            if row < 0 or row + diagonal < 0:
                continue
            row += _common_run(left, right, row, row + diagonal)
            current[diagonal] = row
            if diagonal == target and row >= n:
                return True
        furthest = current
    return False


def _words(text: str) -> Counter[str]:
    words = (word.casefold().strip(".,;:!?()[]{}\"'«»“”‘’…") for word in text.split())
    return Counter(word for word in words if word)


def word_jaccard(vlm: str, ocr: str) -> float:
    """Jaccard trên tập đếm từ (`casefold`, bỏ dấu câu đầu/cuối)."""
    left, right = _words(vlm), _words(ocr)
    union = sum((left | right).values())
    return 1.0 if union == 0 else sum((left & right).values()) / union


def number_tokens(text: str) -> Counter[str]:
    return Counter(TOKEN_PATTERN.findall(text))


def code_tokens(text: str, pattern: re.Pattern[str]) -> Counter[str]:
    return Counter(pattern.findall(text))


def _decision(outcome: GateOutcome, reason: GateReason, **fields: object) -> VisionGateDecision:
    return VisionGateDecision.model_validate({"outcome": outcome, "reason": reason, **fields})


def _rejected(reason: GateReason) -> VisionGateDecision:
    return _decision("rejected", reason, flags=["vlm_gate_rejected"])


def _comparable(text: str, engine: OcrEngine) -> str:
    return strip_tone_marks(text) if engine == "rapidocr" else text


def _check_text(vlm_text: str, ocr_text: str, settings: VisionGateSettings, engine: OcrEngine) -> GateReason | None:
    """Đủ cả bốn điều kiện của ADR-009 mục 3.3 mới nhận; xét các điều kiện rẻ trước, CER (đắt nhất) sau cùng."""
    vlm, ocr = _comparable(vlm_text, engine), _comparable(ocr_text, engine)
    if number_tokens(vlm) != number_tokens(ocr):
        return "number_tokens"
    pattern = re.compile(settings.code_token_pattern)
    if code_tokens(vlm, pattern) != code_tokens(ocr, pattern):
        return "code_tokens"
    if word_jaccard(vlm, ocr) < settings.min_word_jaccard:
        return "jaccard"
    if not within_edit_distance(vlm, ocr, int(settings.max_cer * max(1, len(ocr)))):
        return "cer"
    return None


def _table_mismatches(rows: list[list[str]], ocr: str, pattern: re.Pattern[str]) -> list[GateTokenMismatch]:
    remaining = number_tokens(ocr) + code_tokens(ocr, pattern)
    mismatches: list[GateTokenMismatch] = []
    for row_index, row in enumerate(rows):
        for col_index, cell in enumerate(row):
            for token in [*TOKEN_PATTERN.findall(cell), *pattern.findall(cell)]:
                if remaining[token] > 0:
                    remaining[token] -= 1
                else:
                    mismatches.append(GateTokenMismatch(row=row_index, col=col_index, vlm_value=token))
    for token, count in sorted(remaining.items()):
        mismatches.extend(GateTokenMismatch(ocr_value=token) for _ in range(count))
    return mismatches


def _text_candidate(blocks: Sequence[VlmRegionBlock]) -> tuple[BlockType, str, bool]:
    """(loại, văn bản nối bằng `\\n` theo `order`, có cần `layout_uncertain`) của các khối chữ."""
    ordered = sorted(blocks, key=lambda block: block.order)
    text = "\n".join(block.text or "" for block in ordered)
    if len(ordered) == 1:
        return ordered[0].type, text, False
    return "paragraph", text, True


def judge_region(
    result: VlmRegionResult,
    ocr_text: str | None,
    text_layer_overlap: float,
    settings: VisionGateSettings | None = None,
    *,
    ocr_engine: OcrEngine = "tesseract",
) -> VisionGateDecision:
    """Quyết định cho một vùng (ADR-009 mục 3.2–3.4). `ocr_text` là chữ OCR độc lập của CÙNG vùng; `None`/rỗng ⇒ từ
    chối. `text_layer_overlap` ≥ ngưỡng ⇒ vùng dùng lớp chữ, chữ VLM bị bỏ."""
    gate = settings or VisionGateSettings()
    if result.status == "vlm_invalid":
        return _decision("keep_offline", "status_invalid", flags=["vlm_invalid"])
    if result.status != "ok":
        return _decision("keep_offline", "status_unavailable")
    if text_layer_overlap >= gate.max_text_layer_overlap:
        return _decision("keep_offline", "text_layer_overlap")
    ocr = normalize(ocr_text or "")
    if not ocr:
        return _rejected("ocr_missing")
    if not result.blocks:
        return _rejected("empty")
    tables = [block for block in result.blocks if block.type == "table"]
    texts = [block for block in result.blocks if block.type not in ("table", "figure")]
    if tables and (len(tables) > 1 or len(tables) != len(result.blocks)):
        return _rejected("mixed_table")
    if not tables and not texts:
        return _decision("figure_confirmed", "figure_only")
    pieces = [cell for row in tables[0].rows or [] for cell in row] if tables else [block.text or "" for block in texts]
    if any(UNREADABLE_MARKER in piece for piece in pieces):
        return _rejected("unreadable_marker")
    if len(ocr) > gate.max_region_chars or sum(len(piece) for piece in pieces) > gate.max_region_chars:
        return _rejected("too_long")
    if tables:
        rows = [[normalize(cell) for cell in row] for row in tables[0].rows or []]
        joined = normalize(" ".join(" ".join(row) for row in rows))
        if word_jaccard(_comparable(joined, ocr_engine), _comparable(ocr, ocr_engine)) < gate.min_word_jaccard:
            return _rejected("jaccard")
        mismatches = _table_mismatches(rows, ocr, re.compile(gate.code_token_pattern))
        flags: list[BlockFlag] = ["table_token_mismatch"] if mismatches else []
        return _decision("accepted_table", "ok", block_type="table", rows=rows, flags=flags, mismatches=mismatches)
    block_type, raw_text, uncertain = _text_candidate(texts)
    failure = _check_text(normalize(raw_text), ocr, gate, ocr_engine)
    if failure is not None:
        return _rejected(failure)
    text = unicodedata.normalize("NFC", raw_text)
    return _decision(
        "accepted_text", "ok", block_type=block_type, text=text, flags=["layout_uncertain"] if uncertain else []
    )


def mismatch_ids(block_id: str, mismatches: Sequence[GateTokenMismatch]) -> list[str]:
    """`mismatch_id = "vmm_" + sha256(JSON chuẩn hoá [block_id, row, col, vlm_value, ocr_value, k])[:12]`, `k` là số
    thứ tự của chỗ lệch trùng các giá trị trước trong khối (ADR-009 mục 3.4)."""
    seen: Counter[tuple[int | None, int | None, str | None, str | None]] = Counter()
    ids: list[str] = []
    for mismatch in mismatches:
        key = (mismatch.row, mismatch.col, mismatch.vlm_value, mismatch.ocr_value)
        payload = json.dumps([block_id, *key, seen[key]], ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        seen[key] += 1
        ids.append("vmm_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12])
    return ids
