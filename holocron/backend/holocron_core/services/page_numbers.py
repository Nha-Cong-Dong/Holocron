"""Số trang cấp tài liệu (ADR-010 mục 12, 13; spec §5.15 "Số trang hai bước").

Bước (1) ở stage từng trang (`block_builder`): mỗi đoạn dòng có hộp nằm trong dải lề trên/dưới
`page_number_margin`, ≤ `page_number_max_chars` ký tự không trắng VÀ có chữ khớp một mẫu số trang của `context.lang`
(`compile_page_number_patterns`: mẫu không phụ thuộc ngôn ngữ ở settings dưới + mẫu có chữ ở `infrastructure/lang/*.toml`)
thành khối ỨNG VIÊN riêng (loại `paragraph`, hay `header_footer` khi dòng khớp khoá TEXT template); tiêu đề ngắn có chữ
số ở dải lề ("Chương 3", "1. Giới thiệu") không khớp mẫu nên vẫn là `heading`. Bước (2) ở đây, CẤP TÀI LIỆU, sau khi mọi
trang đã dựng khối: hiệu (số đọc được − `page_number`) của các ứng viên không đổi trên ≥ `page_number_min_pages` trang ⇒
`type = "page_number"`, `ledger.by_type` cập nhật; id không chứa `type` nên không đổi.

Chạy lại được (idempotent): mỗi lần chạy đặt mọi ứng viên về loại gốc (`PageBlocks.page_number_candidates`) rồi áp luật.

ĐIỀU KIỆN CHO X09 (dựng `PageBlocks` từ khối đã lưu): `page_number_candidates` không nằm trong khối đã lưu. Khối đã lưu
có `type = "page_number"` mất loại gốc (`paragraph`/`header_footer`) nên chạy lại stage này KHÔNG đưa nó về loại gốc
được. Do đó X09 PHẢI hoặc (a) lưu loại gốc của khối đã đổi sang `page_number` (hay lưu luôn `page_number_candidates`),
hoặc (b) không chạy lại stage này trên `PageBlocks` dựng từ khối đã lưu mà dựng lại khối từ `PageGeometry` (đường dựng
lại, ADR-010 mục 13) rồi chạy stage trên kết quả đó. Khối `page_number` của trang có `page_number_candidates = None`
được giữ nguyên (stage không đoán loại gốc).
"""

from __future__ import annotations

import re
import unicodedata
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..domain.blocks import Block, BlockType
from ..domain.documents import BBox
from ..infrastructure.lang_patterns import PAGE_NUMBER_GROUPS, LangPatterns
from .extraction_ledger import nonws_units, retype_by_type

if TYPE_CHECKING:
    from .block_pipeline import PageBlocks

PAGE_NUMBERS_STAGE_VERSION = 1
_ROMAN_VALUES = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}
_SPACES = re.compile(r"\s+")
_ROMAN_TABLE: tuple[tuple[int, str], ...] = (
    (1000, "m"),
    (900, "cm"),
    (500, "d"),
    (400, "cd"),
    (100, "c"),
    (90, "xc"),
    (50, "l"),
    (40, "xl"),
    (10, "x"),
    (9, "ix"),
    (5, "v"),
    (4, "iv"),
    (1, "i"),
)


class PageNumberSettings(BaseModel):
    """Ngưỡng số trang; mẫu chỉ gồm chữ số, `n/m`, số La Mã (không phụ thuộc ngôn ngữ) nằm ở đây."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    page_number_margin: float = Field(default=0.10, gt=0.0, lt=0.5)
    page_number_max_chars: int = Field(default=12, ge=1, le=64)
    page_number_min_pages: int = Field(default=3, ge=2)
    generic_patterns: tuple[str, ...] = (
        r"^(?P<n>\d{1,5})$",
        r"^(?P<n>\d{1,5})\s*/\s*\d{1,5}$",
        r"^[-–—]\s*(?P<n>\d{1,5})\s*[-–—]$",
        r"^(?P<r>[ivxlcdm]{1,8})$",
    )

    @field_validator("generic_patterns")
    @classmethod
    def _patterns_compile(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for pattern in value:
            if not set(re.compile(pattern, re.IGNORECASE).groupindex) & PAGE_NUMBER_GROUPS:
                raise ValueError("mẫu số trang phải có nhóm có tên `n` hoặc `r`")
        return value


class PageNumberCandidate(BaseModel):
    """Một khối ứng viên số trang và loại gốc của nó (do stage từng trang đặt)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    block_id: str = Field(pattern=r"^blk_[0-9a-f]{16}$")
    base_type: Literal["paragraph", "header_footer"]
    units: int = Field(ge=0)


def in_margin_band(bbox: BBox | None, settings: PageNumberSettings) -> bool:
    """Hộp nằm trọn trong dải lề trên hoặc dưới của trang."""
    if bbox is None:
        return False
    margin = settings.page_number_margin
    return bbox[3] <= margin or bbox[1] >= 1.0 - margin


def compile_page_number_patterns(settings: PageNumberSettings, lang: LangPatterns) -> tuple[re.Pattern[str], ...]:
    """Mẫu số trang đã biên dịch: mẫu không phụ thuộc ngôn ngữ của `settings` rồi mẫu của `lang` (không phân biệt hoa
    thường). MỘT nguồn dùng cho cả stage từng trang lẫn stage cấp tài liệu."""
    generic = tuple(re.compile(p, re.IGNORECASE) for p in settings.generic_patterns)
    return generic + lang.compiled_page_number_patterns()


def candidate_text(text: str, settings: PageNumberSettings, patterns: tuple[re.Pattern[str], ...]) -> bool:
    """Chữ đủ ngắn VÀ khớp một mẫu số trang (SPEC §5.15 "Số trang hai bước"): "Chương 3", "1. Giới thiệu" không khớp."""
    if nonws_units(text) > settings.page_number_max_chars:
        return False
    return page_number_value(text, patterns) is not None


def _roman(value: str) -> int | None:
    """Giá trị của số La Mã viết đúng chuẩn (`iv`, không `iiii`); sai chuẩn ⇒ None."""
    digits = [_ROMAN_VALUES[char] for char in value.lower()]
    total = 0
    for position, digit in enumerate(digits):
        following = digits[position + 1] if position + 1 < len(digits) else 0
        total += -digit if digit < following else digit
    if total <= 0 or _to_roman(total) != value.lower():
        return None
    return total


def _to_roman(number: int) -> str:
    out: list[str] = []
    for value, symbol in _ROMAN_TABLE:
        while number >= value:
            out.append(symbol)
            number -= value
    return "".join(out)


def page_number_value(text: str, patterns: tuple[re.Pattern[str], ...]) -> int | None:
    """Số trang đọc được từ chữ của khối (NFC + gộp khoảng trắng) theo mẫu đầu tiên khớp; không khớp ⇒ None."""
    normalized = _SPACES.sub(" ", unicodedata.normalize("NFC", text)).strip()
    for pattern in patterns:
        match = pattern.match(normalized)
        if match is None:
            continue
        groups = match.groupdict()
        if groups.get("n") is not None:
            return int(groups["n"])
        roman = groups.get("r")
        if roman is not None and (value := _roman(roman)) is not None:
            return value
    return None


def page_number_candidates(
    blocks: list[Block], settings: PageNumberSettings, patterns: tuple[re.Pattern[str], ...]
) -> list[PageNumberCandidate]:
    """Ứng viên của một trang vừa dựng: khối `paragraph`/`header_footer` có hộp trong dải lề và chữ khớp mẫu số trang."""
    out: list[PageNumberCandidate] = []
    for block in blocks:
        if block.type not in ("paragraph", "header_footer") or block.text is None:
            continue
        if in_margin_band(block.bbox, settings) and candidate_text(block.text, settings, patterns):
            base: Literal["paragraph", "header_footer"] = "paragraph" if block.type == "paragraph" else "header_footer"
            out.append(PageNumberCandidate(block_id=block.block_id, base_type=base, units=nonws_units(block.text)))
    return out


class PageNumberStage:
    """Stage CẤP TÀI LIỆU `page_numbers`: `run(pages) -> pages`, idempotent. Giữ mẫu ngôn ngữ người gọi đã nạp."""

    name: str = "page_numbers"

    def __init__(self, lang: LangPatterns, settings: PageNumberSettings | None = None) -> None:
        self.lang = lang
        self.settings = settings or PageNumberSettings()
        self._patterns = compile_page_number_patterns(self.settings, lang)

    def fingerprint(self) -> dict[str, Any]:
        settings = {**self.settings.model_dump(), "lang_patterns_sha256": self.lang.sha256()}
        return {"name": self.name, "version": PAGE_NUMBERS_STAGE_VERSION, "settings": settings}

    def run(self, pages: list[PageBlocks]) -> list[PageBlocks]:
        """Đặt lại mọi ứng viên về loại gốc, rồi đổi sang `page_number` những ứng viên có hiệu số ổn định."""
        reset: list[PageBlocks] = []
        for page in pages:
            if page.layout_failed:
                reset.append(page)
                continue
            candidates = page.page_number_candidates
            if candidates is None:
                candidates = page_number_candidates(page.blocks, self.settings, self._patterns)
            reset.append(self._retype(page, candidates, {c.block_id: c.base_type for c in candidates}))
        offsets: dict[int, set[int]] = {}
        values: dict[tuple[int, str], int] = {}
        for page in reset:
            if page.layout_failed or not page.page_number_candidates:
                continue
            by_id = {block.block_id: block for block in page.blocks}
            for candidate in page.page_number_candidates:
                block = by_id.get(candidate.block_id)
                if block is None or block.text is None or not candidate_text(block.text, self.settings, self._patterns):
                    continue
                value = page_number_value(block.text, self._patterns)
                if value is not None:
                    values[(page.page_number, candidate.block_id)] = value
                    offsets.setdefault(value - page.page_number, set()).add(page.page_number)
        stable = {offset for offset, seen in offsets.items() if len(seen) >= self.settings.page_number_min_pages}
        out: list[PageBlocks] = []
        for page in reset:
            if page.layout_failed or not page.page_number_candidates:
                out.append(page)
                continue
            targets: dict[str, BlockType] = {}
            for candidate in page.page_number_candidates:
                value = values.get((page.page_number, candidate.block_id))
                if value is not None and value - page.page_number in stable:
                    targets[candidate.block_id] = "page_number"
            out.append(self._retype(page, page.page_number_candidates, targets))
        return out

    @staticmethod
    def _retype(page: PageBlocks, candidates: list[PageNumberCandidate], targets: dict[str, BlockType]) -> PageBlocks:
        """Khối có id trong `targets` nhận loại mới; `ledger.by_type` chuyển đơn vị tương ứng."""
        units = {candidate.block_id: candidate.units for candidate in candidates}
        moves: list[tuple[BlockType, BlockType, int]] = []
        blocks: list[Block] = []
        for block in page.blocks:
            target = targets.get(block.block_id)
            if target is None or target == block.type:
                blocks.append(block)
                continue
            moves.append((block.type, target, units[block.block_id]))
            blocks.append(block.model_copy(update={"type": target}))
        ledger = page.ledger
        if ledger is not None and moves:
            ledger = retype_by_type(ledger, moves)
        return page.model_copy(update={"blocks": blocks, "ledger": ledger, "page_number_candidates": candidates})
