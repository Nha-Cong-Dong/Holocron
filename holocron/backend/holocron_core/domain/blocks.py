"""Khối, mục, sổ bảo toàn và ảnh cắt (SPECIFICATION §5.15, ADR-010 mục 2, 3, 4, 6, 8, 15, 20).

Module chỉ chứa dữ liệu và ràng buộc. Bộ dựng khối (Deterministic Core) ở `services/block_builder.py`,
`services/reading_order.py`, `services/block_pipeline.py`, `services/page_numbers.py`; sổ bảo toàn ở
`services/extraction_ledger.py`.
"""

from __future__ import annotations

from typing import Annotated, Literal, get_args

from pydantic import ConfigDict, Field, field_validator, model_validator

from .limits import MAX_PAGE_BLOCKS
from .schemas import StrictModel

__all__ = [
    "BLOCK_FLAG_ORDER",
    "BLOCK_TYPE_ORDER",
    "LAYOUT_HASH_REGISTRY",
    "LAYOUT_VERSION",
    "MAX_BLOCK_TEXT_CHARS",
    "MAX_PAGE_BLOCKS",
    "Block",
    "BlockFlag",
    "BlockType",
    "ConfirmedLineRef",
    "CropImage",
    "ExtractionLedger",
    "LedgerBasis",
    "LedgerBasisCount",
    "LedgerDrop",
    "LedgerDropReason",
    "LedgerFlagCount",
    "LedgerTypeCount",
    "OcrWordRef",
    "Section",
    "SectionFlag",
    "SemanticLabel",
    "SourceRef",
    "TableCell",
    "UnitBBox",
    "WordRef",
]

# Tăng đúng một lần trong PR nào làm đầu ra của bộ dựng đổi với tài liệu đã lưu (ADR-010 mục 15 (g'')).
LAYOUT_VERSION = 1

# Sổ đăng ký cộng dồn: version → `layout_settings_sha256` của cấu hình mặc định (ADR-010 mục 15). PR tăng
# `LAYOUT_VERSION` THÊM đúng một mục mới, KHÔNG sửa mục cũ.
LAYOUT_HASH_REGISTRY: dict[int, str] = {
    1: "c03d333cffb3dc82342f6882a4a0573061f35f80263c2acfad564d005d83b43a",
}

# Trần độ dài `Block.text` (ADR-010 mục 7): `paragraph` dài hơn được tách tại ranh giới dòng; loại khác ⇒ LAYOUT_FAILED.
MAX_BLOCK_TEXT_CHARS = 50_000

BlockType = Literal[
    "heading",
    "paragraph",
    "list_item",
    "table",
    "figure",
    "formula",
    "code",
    "caption",
    "header_footer",
    "page_number",
]
BLOCK_TYPE_ORDER: tuple[BlockType, ...] = get_args(BlockType)

BlockFlag = Literal[
    "crop_pending",
    "crop_failed",
    "crop_capped",
    "table_unresolved",
    "table_token_mismatch",
    "boxed",
    "formula_text_unverified",
    "code_layout_uncertain",
    "layout_uncertain",
    "reading_order_uncertain",
    "figure_label_text",
    "text_occluded",
    "low_text_contrast",
    "font_encoding_suspect",
    "human_edited",
    "too_short_for_chunk",
    "ai_proposal_applied",
    "ai_proposal_rejected",
    "vlm_invalid",
    "vlm_gate_rejected",
]
BLOCK_FLAG_ORDER: tuple[BlockFlag, ...] = get_args(BlockFlag)

SemanticLabel = Literal["definition", "example", "exercise"]

UnitFloat = Annotated[float, Field(ge=0.0, le=1.0)]
UnitBBox = tuple[UnitFloat, UnitFloat, UnitFloat, UnitFloat]

BLOCK_ID_PATTERN = r"^blk_[0-9a-f]{16}$"
DOCUMENT_ID_PATTERN = r"^doc_[0-9a-f]{16}$"
CROP_REF_PATTERN = r"^[0-9a-f]{64}$"

# Loại cần ảnh cắt khi có `bbox` (ADR-010 mục 8).
CROPPED_TYPES: frozenset[BlockType] = frozenset({"table", "figure", "formula"})
# Loại không có `text` (chữ nằm ở ô bảng hay nhãn hình).
TEXTLESS_TYPES: frozenset[BlockType] = frozenset({"table", "figure"})


def _check_unit_bbox(value: UnitBBox | None) -> UnitBBox | None:
    if value is not None and (value[0] > value[2] or value[1] > value[3]):
        raise ValueError("bbox phải có x0 ≤ x1 và y0 ≤ y1")
    return value


class WordRef(StrictModel):
    """Từ thứ `word_idx` của dòng thô thứ `line_idx` trong `PageGeometry.lines`."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, frozen=True)

    kind: Literal["word"] = "word"
    line_idx: int = Field(ge=0)
    word_idx: int = Field(ge=0)


class OcrWordRef(StrictModel):
    """Từ OCR trong `PageGeometry.ocr_lines` (X11)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, frozen=True)

    kind: Literal["ocr_word"] = "ocr_word"
    line_idx: int = Field(ge=0)
    word_idx: int = Field(ge=0)


class ConfirmedLineRef(StrictModel):
    """Một `ExtractedLine` đã sửa hoặc gõ thêm (sau `PATCH …/lines`)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, frozen=True)

    kind: Literal["confirmed_line"] = "confirmed_line"
    line_index: int = Field(ge=0)


SourceRef = Annotated[WordRef | OcrWordRef | ConfirmedLineRef, Field(discriminator="kind")]


class TableCell(StrictModel):
    text: str
    line_refs: list[SourceRef]
    row: int = Field(ge=0)
    col: int = Field(ge=0)
    rowspan: int = Field(default=1, ge=1)
    colspan: int = Field(default=1, ge=1)


class Block(StrictModel):
    """Một khối của trang (ADR-010 mục 3; model phẳng có validator theo `type`, nợ LAYOUT-001)."""

    block_id: str = Field(pattern=BLOCK_ID_PATTERN)
    document_id: str = Field(pattern=DOCUMENT_ID_PATTERN)
    page_number: int = Field(ge=1)
    type: BlockType
    bbox: UnitBBox | None
    text: str | None = Field(default=None, max_length=MAX_BLOCK_TEXT_CHARS)
    line_refs: list[SourceRef] = Field(default_factory=list)
    label_line_refs: list[SourceRef] = Field(default_factory=list)
    labels: list[str] = Field(default_factory=list)
    level: int | None = Field(default=None, ge=1, le=6)
    list_depth: int | None = Field(default=None, ge=0, le=8)
    cells: list[list[TableCell]] | None = None
    caption_of: str | None = Field(default=None, pattern=BLOCK_ID_PATTERN)
    semantic_label: SemanticLabel | None = None
    flags: list[BlockFlag] = Field(default_factory=list)
    crop_ref: str | None = Field(default=None, pattern=CROP_REF_PATTERN)
    source: Literal["text_layer", "ocr", "vlm"]
    ocr_text: str | None = None
    reading_order: int = Field(ge=0)

    @field_validator("bbox")
    @classmethod
    def _bbox_ordered(cls, value: UnitBBox | None) -> UnitBBox | None:
        return _check_unit_bbox(value)

    @field_validator("flags")
    @classmethod
    def _ordered_unique(cls, value: list[BlockFlag]) -> list[BlockFlag]:
        """Sắp theo thứ tự khai báo của `BlockFlag`, bỏ trùng."""
        present = set(value)
        return [flag for flag in BLOCK_FLAG_ORDER if flag in present]

    @model_validator(mode="after")
    def _fields_match_type(self) -> Block:
        kind = self.type
        if (self.level is not None) != (kind == "heading"):
            raise ValueError("level có giá trị khi và chỉ khi type = 'heading'")
        if (self.list_depth is not None) != (kind == "list_item"):
            raise ValueError("list_depth có giá trị khi và chỉ khi type = 'list_item'")
        if (self.cells is not None) != (kind == "table"):
            raise ValueError("cells có giá trị khi và chỉ khi type = 'table'")
        if kind != "figure" and (self.label_line_refs or self.labels):
            raise ValueError("label_line_refs và labels chỉ có ở figure")
        if kind == "figure" and self.line_refs:
            raise ValueError("figure không có line_refs (chữ trong figure chỉ ở label_line_refs)")
        if self.caption_of is not None and kind != "caption":
            raise ValueError("caption_of chỉ có ở caption")
        if (self.ocr_text is not None) != (self.source == "vlm"):
            raise ValueError("ocr_text có giá trị khi và chỉ khi source = 'vlm'")
        if (self.text is None) != (kind in TEXTLESS_TYPES):
            raise ValueError("text là null khi và chỉ khi type là table hoặc figure")
        if (
            kind in CROPPED_TYPES
            and self.bbox is not None
            and self.crop_ref is None
            and "crop_pending" not in self.flags
        ):
            raise ValueError("table, figure, formula có bbox phải có crop_ref hoặc cờ crop_pending")
        if self.bbox is None and "crop_pending" in self.flags:
            raise ValueError("khối bbox null không mang crop_pending")
        if "table_token_mismatch" in self.flags and kind != "table":
            raise ValueError("table_token_mismatch chỉ có ở table")
        return self


SectionFlag = Literal["untitled", "continued_across_pages", "level_uncertain"]


class Section(StrictModel):
    """Một nút của cây theo TỪNG tài liệu (ADR-010 mục 4); chỉ dựng và ghi ở settle (X08, X09)."""

    section_id: str = Field(pattern=r"^sec_[0-9a-f]{16}$")
    document_id: str = Field(pattern=DOCUMENT_ID_PATTERN)
    parent_id: str | None
    kind: Literal["chapter", "section"]
    level: int = Field(ge=0, le=6)
    chapter_code: str | None = Field(default=None, pattern=r"^ch[0-9]{1,2}(\.[0-9]{1,2})?$")
    title: str | None = Field(default=None, min_length=1, max_length=300)
    heading_block_ids: list[str] = Field(default_factory=list)
    block_ids: list[str] = Field(default_factory=list)
    ordinal: int = Field(ge=0)
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)
    flags: list[SectionFlag] = Field(default_factory=list)

    @model_validator(mode="after")
    def _consistent(self) -> Section:
        if (self.parent_id is None) != (self.kind == "chapter"):
            raise ValueError("parent_id là null khi và chỉ khi kind = 'chapter'")
        if self.chapter_code is not None and self.kind != "chapter":
            raise ValueError("chapter_code chỉ có ở gốc (kind = 'chapter')")
        if self.page_end < self.page_start:
            raise ValueError("page_end phải ≥ page_start")
        if (self.title is None) != ("untitled" in self.flags):
            raise ValueError("title là null khi và chỉ khi có cờ untitled")
        return self


LedgerDropReason = Literal["hidden"]


class LedgerDrop(StrictModel):
    reason: LedgerDropReason
    count: int = Field(ge=0)


class LedgerTypeCount(StrictModel):
    type: BlockType
    count: int = Field(ge=0)


class LedgerFlagCount(StrictModel):
    """Thông tin: ký tự vẫn thuộc khối, dòng nguồn mang cờ."""

    reason: Literal["occluded", "low_contrast"]
    count: int = Field(ge=0)


LedgerBasis = Literal["pdfium", "confirmed_lines", "ocr"]
LEDGER_BASIS_ORDER: tuple[LedgerBasis, ...] = get_args(LedgerBasis)


class LedgerBasisCount(StrictModel):
    ledger_basis: LedgerBasis
    source_count: int = Field(ge=0)
    assigned_count: int = Field(ge=0)
    dropped: list[LedgerDrop] = Field(default_factory=list)

    @property
    def is_balanced(self) -> bool:
        return self.source_count == self.assigned_count + sum(drop.count for drop in self.dropped)


class ExtractionLedger(StrictModel):
    """Sổ bảo toàn của một trang (ADR-010 mục 6)."""

    document_id: str = Field(pattern=DOCUMENT_ID_PATTERN)
    page_number: int = Field(ge=1)
    unit: Literal["nonws_nfc_codepoint"] = "nonws_nfc_codepoint"
    bases: list[LedgerBasisCount] = Field(min_length=1)
    by_type: list[LedgerTypeCount] = Field(default_factory=list)
    flagged: list[LedgerFlagCount] = Field(default_factory=list)
    page_drop_reason: Literal["cover_or_course_nav", "chapter_nav", "build_step"] | None = None
    balanced: bool

    @model_validator(mode="after")
    def _consistent(self) -> ExtractionLedger:
        kinds = [basis.ledger_basis for basis in self.bases]
        if kinds != [kind for kind in LEDGER_BASIS_ORDER if kind in kinds]:
            raise ValueError("bases phải theo thứ tự khai báo của LedgerBasis, mỗi basis tối đa một phần tử")
        types = [item.type for item in self.by_type]
        if types != [kind for kind in BLOCK_TYPE_ORDER if kind in types]:
            raise ValueError("by_type phải theo thứ tự khai báo của BlockType, không trùng")
        reasons = [item.reason for item in self.flagged]
        if len(set(reasons)) != len(reasons):
            raise ValueError("flagged không được trùng lý do")
        if self.balanced != all(basis.is_balanced for basis in self.bases):
            raise ValueError("balanced phải khớp phương trình source = assigned + Σ dropped của mọi basis")
        if sum(item.count for item in self.by_type) != sum(basis.assigned_count for basis in self.bases):
            raise ValueError("Σ by_type.count phải bằng Σ bases.assigned_count")
        return self


class CropImage(StrictModel):
    """Ảnh cắt của VÙNG một khối (ADR-010 mục 8, 19)."""

    document_id: str = Field(pattern=DOCUMENT_ID_PATTERN)
    block_id: str = Field(pattern=BLOCK_ID_PATTERN)
    crop_ref: str = Field(pattern=CROP_REF_PATTERN)
    page_number: int = Field(ge=1)
    bbox: UnitBBox
    width_px: int = Field(ge=1, le=1024)
    height_px: int = Field(ge=1, le=1024)
    size_bytes: int = Field(ge=1, le=1_048_576)
    media_type: Literal["image/png"] = "image/png"

    @field_validator("bbox")
    @classmethod
    def _bbox_ordered(cls, value: UnitBBox) -> UnitBBox:
        _check_unit_bbox(value)
        return value
