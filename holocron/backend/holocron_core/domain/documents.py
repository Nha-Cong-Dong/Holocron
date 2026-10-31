"""Kết quả đọc PDF theo trang (SPECIFICATION §5.7, ADR-008 mục 13).

Worker đọc PDF trả dữ liệu thô theo từng trang; tiến trình cha validate rồi dựng `PageExtraction` bằng các model
ở đây. Module chỉ chứa dữ liệu và ràng buộc; cách tính điểm, cờ trang và chọn tầng nằm ở
`infrastructure/document_readers/`.
"""

from __future__ import annotations

import unicodedata
from datetime import datetime
from typing import Annotated, Literal, get_args

from pydantic import Field, StringConstraints, field_validator, model_validator

from .ingestion import ExtractedTable
from .limits import MAX_LINE_CHARS
from .schemas import DocumentMeta, StrictModel

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
FailureReason = Literal["PAGE_TIMEOUT", "PAGE_ERROR", "OCR_ENGINE_MISSING", "TOO_MANY_LINES"]
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


# --------------------------------------------------------------------------- #
# Tài liệu đã nhập (SPECIFICATION §5.7, §6.5) và lịch sử sửa trang
# --------------------------------------------------------------------------- #

DocumentStatus = Literal["processing", "review", "confirmed", "failed"]
OutputLanguage = Literal["vi", "en"]
DOCUMENT_ID_PATTERN = r"^doc_[0-9a-f]{16}$"
CHAPTER_CODE_PATTERN = r"^ch[0-9]{1,2}(\.[0-9]{1,2})?$"  # cùng mẫu với `KnowledgeChunk.chapter_code` (§5.8)

# Chương tự động (§5.7 "Chương tự động", §6.5; GĐ1 1A, duyệt 2026-10-31)

ChapterFlag = Literal[
    "CHAPTER_NUMBER_NOT_IN_FILENAME",  # tên file không có số nguyên 1–2 chữ số ⇒ dùng chương trống nhỏ nhất
    "CHAPTER_NUMBER_TAKEN",  # số trong tên file trùng chương đã có của môn ⇒ dùng chương trống nhỏ nhất
]
CHAPTER_FLAG_ORDER: tuple[ChapterFlag, ...] = get_args(ChapterFlag)
ChapterCodeSource = Literal["user", "file_name", "next_free"]
ChapterTitleSource = Literal["user", "repeated_header", "file_name", "llm"]
MAX_CHAPTER_TITLE_CHARS = 300
# Độ dài 1–300 tính trên chuỗi thô: `strip_whitespace=False` ghi đè cấu hình của `StrictModel`, nếu không khoảng trắng
# đầu/cuối bị bỏ trước khi đếm và 5000 dấu cách kèm một tên ngắn lọt qua
RawChapterTitle = Annotated[
    str, StringConstraints(strip_whitespace=False, min_length=1, max_length=MAX_CHAPTER_TITLE_CHARS)
]


def normalize_chapter_title(value: str) -> str:
    """NFC rồi gộp mọi đoạn khoảng trắng thành một dấu cách, bỏ khoảng trắng đầu/cuối (§6.5 `PATCH …/chapter`)."""
    return " ".join(unicodedata.normalize("NFC", value).split())


class ChapterSource(StrictModel):
    """Nguồn của `chapter_code` và `chapter_title` (§5.7); `title` null ⇔ `chapter_title` null."""

    code: ChapterCodeSource
    title: ChapterTitleSource | None


class PageSummary(StrictModel):
    """Tóm tắt một trang trong `DocumentImport.pages` (§5.7)."""

    page_number: int = Field(ge=1)
    status: PageStatus
    tier_used: TierUsed | None
    review_reasons: list[ReviewReason]
    confirmed: bool

    @classmethod
    def from_page(cls, page: PageExtraction) -> PageSummary:
        return cls(
            page_number=page.page_number,
            status=page.status,
            tier_used=page.tier_used,
            review_reasons=list(page.review_reasons),
            confirmed=page.confirmed,
        )


class DocumentImport(StrictModel):
    """Tài liệu nhập qua §6.5 kèm tóm tắt từng trang; dùng để client hỏi trạng thái (§5.7)."""

    meta: DocumentMeta
    filename: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(ge=1)
    output_language: OutputLanguage = "vi"
    chapter_code: str = Field(pattern=CHAPTER_CODE_PATTERN)
    status: DocumentStatus
    pages: list[PageSummary]
    # thêm 2026-10-31 (GĐ1 1A, 1B; §5.7 "Chương tự động", §6.5)
    chapter_title: str | None = Field(default=None, min_length=1, max_length=MAX_CHAPTER_TITLE_CHARS)
    chapter_source: ChapterSource
    chapter_flags: list[ChapterFlag] = Field(default_factory=list)
    auto_confirm: bool = True

    @model_validator(mode="after")
    def _chapter_title_has_a_source(self) -> DocumentImport:
        if (self.chapter_title is None) != (self.chapter_source.title is None):
            raise ValueError("chapter_source.title null khi và chỉ khi chapter_title null")
        return self

    @model_validator(mode="after")
    def _pages_cover_document(self) -> DocumentImport:
        """Đủ `page_count` phần tử, theo `page_number` tăng dần từ 1."""
        if [page.page_number for page in self.pages] != list(range(1, self.meta.page_count + 1)):
            raise ValueError("pages phải có đủ page_count phần tử, theo page_number tăng dần từ 1")
        return self


class DocumentSummary(StrictModel):
    """Một phần tử của `GET /v1/documents` (§6.5); các bộ đếm do code đếm từ trang."""

    meta: DocumentMeta
    status: DocumentStatus
    pages_pending: int = Field(ge=0)
    pages_needs_review: int = Field(ge=0)
    pages_failed: int = Field(ge=0)
    pages_confirmed: int = Field(ge=0)


class PageLineChange(StrictModel):
    """Một dòng đổi trong `PageEditAudit.changes`; `old` là null với dòng người dùng gõ thêm."""

    line_index: int = Field(ge=0)
    old: str | None
    new: str


class PageEditAudit(StrictModel):
    """Một lần sửa dòng của trang (§6.5, đề xuất): version của trang tăng đúng 1."""

    document_id: str = Field(pattern=DOCUMENT_ID_PATTERN)
    page_number: int = Field(ge=1)
    from_version: int = Field(ge=1)
    to_version: int = Field(ge=2)
    editor_id: str = Field(min_length=1)
    changes: list[PageLineChange]
    at: datetime

    @model_validator(mode="after")
    def _one_step(self) -> PageEditAudit:
        if self.to_version != self.from_version + 1:
            raise ValueError("to_version phải bằng from_version + 1")
        return self


# --------------------------------------------------------------------------- #
# Sửa dòng và xác nhận (SPECIFICATION §6.5)
# --------------------------------------------------------------------------- #

MAX_APPENDED_LINES = 500
MAX_EDITOR_ID_CHARS = 128


def _nfc_line(value: str) -> str:
    """Chuẩn hoá NFC; dài quá `MAX_LINE_CHARS` sau chuẩn hoá ⇒ `ValueError`."""
    normalized = unicodedata.normalize("NFC", value)
    if len(normalized) > MAX_LINE_CHARS:
        raise ValueError(f"dòng dài quá {MAX_LINE_CHARS} ký tự sau chuẩn hoá NFC")
    return normalized


class LineEdit(StrictModel):
    """Một dòng sửa trong `PATCH …/lines`; `text` rỗng nghĩa là dòng không vào văn bản xác nhận (§6.5)."""

    line_index: int = Field(ge=0)
    text: str = Field(max_length=MAX_LINE_CHARS)

    @field_validator("text")
    @classmethod
    def _nfc(cls, value: str) -> str:
        return _nfc_line(value)


class PageLinesPatch(StrictModel):
    """Body của `PATCH /v1/documents/{document_id}/pages/{page_number}/lines` (§6.5)."""

    editor_id: str = Field(min_length=1, max_length=MAX_EDITOR_ID_CHARS)
    expected_version: int = Field(ge=1)
    edits: list[LineEdit] = Field(default_factory=list)
    appended_lines: list[str] = Field(default_factory=list, max_length=MAX_APPENDED_LINES)

    @field_validator("appended_lines")
    @classmethod
    def _nfc_appended(cls, value: list[str]) -> list[str]:
        return [_nfc_line(line) for line in value]

    @model_validator(mode="after")
    def _not_empty_and_unique(self) -> PageLinesPatch:
        if not self.edits and not self.appended_lines:
            raise ValueError("edits và appended_lines không được cùng rỗng")
        indexes = [edit.line_index for edit in self.edits]
        if len(indexes) != len(set(indexes)):
            raise ValueError("line_index trong edits không được trùng")
        return self


class PageConfirmRequest(StrictModel):
    """Body của `POST /v1/documents/{document_id}/pages/{page_number}/confirm` (§6.5)."""

    editor_id: str = Field(min_length=1, max_length=MAX_EDITOR_ID_CHARS)
    expected_version: int = Field(ge=1)


class DocumentConfirmRequest(StrictModel):
    """Body của `POST /v1/documents/{document_id}/confirm` (§6.5)."""

    editor_id: str = Field(min_length=1, max_length=MAX_EDITOR_ID_CHARS)


# --------------------------------------------------------------------------- #
# Đổi chương (SPECIFICATION §6.5 `PATCH …/chapter`; GĐ1 1A)
# --------------------------------------------------------------------------- #


class DocumentChapterPatch(StrictModel):
    """Body của `PATCH /v1/documents/{document_id}/chapter` (§6.5); hai field không cùng null."""

    editor_id: str = Field(min_length=1, max_length=MAX_EDITOR_ID_CHARS)
    chapter_code: str | None = Field(default=None, pattern=CHAPTER_CODE_PATTERN)
    # §6.5: 1–300 ký tự trên chuỗi thô (`RawChapterTitle`), rồi kiểm lại 1–300 sau chuẩn hoá (NFC, gộp khoảng trắng)
    chapter_title: RawChapterTitle | None = None

    @field_validator("chapter_title")
    @classmethod
    def _normalized_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = normalize_chapter_title(value)
        if not 1 <= len(normalized) <= MAX_CHAPTER_TITLE_CHARS:
            raise ValueError(f"chapter_title phải dài 1–{MAX_CHAPTER_TITLE_CHARS} ký tự sau chuẩn hoá")
        return normalized

    @model_validator(mode="after")
    def _something_to_change(self) -> DocumentChapterPatch:
        if self.chapter_code is None and self.chapter_title is None:
            raise ValueError("chapter_code và chapter_title không được cùng null")
        return self
