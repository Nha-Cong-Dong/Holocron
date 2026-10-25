"""Kết quả đọc PDF theo trang (SPECIFICATION §5.7, ADR-008 mục 13).

Worker đọc PDF trả dữ liệu thô theo từng trang; tiến trình cha validate rồi dựng `PageExtraction` bằng các model
ở đây. Module chỉ chứa dữ liệu và ràng buộc; cách tính điểm, cờ trang và chọn tầng nằm ở
`infrastructure/document_readers/`.
"""

from __future__ import annotations

import unicodedata
from datetime import datetime
from typing import Literal, get_args

from pydantic import Field, field_validator, model_validator

from .ingestion import ExtractedTable
from .schemas import StrictModel

MAX_LINE_CHARS = 2000

LineEngine = Literal["text_layer", "tesseract", "vlm", "human"]
TierUsed = Literal["text_layer", "ocr", "layout", "vlm"]
LineFlag = Literal["LOW_LINE_CONF", "TOKEN_MISMATCH", "HIGH_LINE_CER"]
ReviewReason = Literal[
    "LOW_OCR_SCORE",
    "LOW_LINE_COVERAGE",
    "HIGH_INK_OUTSIDE_TEXT",
    "FIGURE_ON_SCAN",
    "FORMULA_ON_SCAN",
    "TIER3_UNAVAILABLE",
    "LOW_CONF_LINES",
    "TOKEN_MISMATCH",
    "CROSS_CHECK_UNAVAILABLE",
    "HIGH_PAGE_CER",
    "OCR_ENGINE_MISSING",
]
FailureReason = Literal["PAGE_TIMEOUT", "PAGE_ERROR", "OCR_ENGINE_MISSING"]
PageStatus = Literal["pending", "ok", "needs_review", "failed"]
CrossCheckStatus = Literal["ok", "mismatch", "unavailable", "not_applicable"]

REVIEW_REASON_ORDER: tuple[ReviewReason, ...] = get_args(ReviewReason)

BBox = tuple[float, float, float, float]


class ExtractedLine(StrictModel):
    """Một dòng văn bản của trang. `bbox` = (x0, y0, x1, y1) chia theo khổ trang, ∈ [0, 1], gốc trên-trái."""

    line_index: int = Field(ge=0)
    text: str = Field(max_length=MAX_LINE_CHARS)
    bbox: BBox | None
    conf: float = Field(ge=0.0, le=1.0)
    engine: LineEngine
    flags: list[LineFlag] = Field(default_factory=list)
    edited_by_human: bool = False

    @field_validator("text")
    @classmethod
    def _nfc(cls, value: str) -> str:
        normalized = unicodedata.normalize("NFC", value)
        if len(normalized) > MAX_LINE_CHARS:
            raise ValueError(f"dòng dài quá {MAX_LINE_CHARS} ký tự sau chuẩn hoá NFC")
        return normalized

    @field_validator("bbox")
    @classmethod
    def _bbox_in_unit_square(cls, value: BBox | None) -> BBox | None:
        if value is None:
            return None
        x0, y0, x1, y1 = value
        if not all(0.0 <= v <= 1.0 for v in value):
            raise ValueError("bbox phải nằm trong [0, 1]")
        if x0 > x1 or y0 > y1:
            raise ValueError("bbox phải có x0 ≤ x1 và y0 ≤ y1")
        return value


class TokenMismatch(StrictModel):
    line_index: int = Field(ge=0)
    primary_value: str
    secondary_value: str | None


class CrossCheck(StrictModel):
    status: CrossCheckStatus
    secondary_engine: Literal["rapidocr"] | None
    mismatches: list[TokenMismatch] = Field(default_factory=list)

    @model_validator(mode="after")
    def _consistent(self) -> CrossCheck:
        if bool(self.mismatches) != (self.status == "mismatch"):
            raise ValueError("mismatches không rỗng khi và chỉ khi status = 'mismatch'")
        if self.status in ("unavailable", "not_applicable") and self.secondary_engine is not None:
            raise ValueError("secondary_engine phải là null khi status là 'unavailable' hoặc 'not_applicable'")
        return self


class PageExtraction(StrictModel):
    document_id: str = Field(pattern=r"^doc_[0-9a-f]{16}$")
    page_number: int = Field(ge=1)
    version: int = Field(default=1, ge=1)
    status: PageStatus
    tier_used: TierUsed | None
    text_score: float | None = Field(ge=0.0, le=1.0)
    ocr_score: float | None = Field(default=None, ge=0.0, le=1.0)
    coverage_ratio: float | None = Field(default=None, ge=0.0, le=1.0)
    ink_outside_text_ratio: float | None = Field(default=None, ge=0.0, le=1.0)
    has_figure: bool = False
    has_table: bool = False
    has_formula: bool = False
    lines: list[ExtractedLine] = Field(default_factory=list)
    tables: list[ExtractedTable] = Field(default_factory=list)
    cross_check: CrossCheck
    review_reasons: list[ReviewReason] = Field(default_factory=list)
    failure_reason: FailureReason | None = None
    confirmed: bool = False
    confirmed_at: datetime | None = None

    @field_validator("review_reasons")
    @classmethod
    def _ordered_unique(cls, value: list[ReviewReason]) -> list[ReviewReason]:
        """Sắp theo thứ tự khai báo của `ReviewReason`, bỏ trùng."""
        present = set(value)
        return [reason for reason in REVIEW_REASON_ORDER if reason in present]

    @model_validator(mode="after")
    def _consistent(self) -> PageExtraction:
        failed = self.status == "failed"
        if (self.failure_reason is not None) != failed:
            raise ValueError("failure_reason có giá trị khi và chỉ khi status = 'failed'")
        if not failed and (self.status == "needs_review") != bool(self.review_reasons):
            raise ValueError("status = 'needs_review' khi và chỉ khi review_reasons không rỗng (trang chưa failed)")
        if self.status in ("pending", "failed") and self.tier_used is not None:
            raise ValueError("tier_used phải là null khi trang pending hoặc failed")
        if self.status == "pending" and self.text_score is not None:
            raise ValueError("text_score phải là null khi trang pending")
        if [line.line_index for line in self.lines] != list(range(len(self.lines))):
            raise ValueError("line_index phải liên tục 0..n-1")
        flagged = {line.line_index for line in self.lines if "TOKEN_MISMATCH" in line.flags}
        if any(m.line_index not in flagged for m in self.cross_check.mismatches):
            raise ValueError("mỗi TokenMismatch phải trỏ tới một dòng có cờ TOKEN_MISMATCH")
        return self
