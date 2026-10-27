"""Model dùng chung của luồng nhập đề và Ingestion Gate (SPECIFICATION §5.2–§5.4, §5.11).

Đặt ở `domain/` để mọi tầng (kể cả repository ở `infrastructure/`) dùng được mà không vi phạm phân tầng.
Logic kiểm bảng, sửa ô và tự giải nằm ở `services/ingestion_gate.py`; module này chỉ chứa dữ liệu.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .schemas import CPUSchedulingProblem, OptionKey, SelfSolveResult, SolverInputs

MediaType = Literal["image/png", "image/jpeg", "image/webp", "application/pdf"]


class UploadedDocument(BaseModel):
    model_config = ConfigDict(frozen=True)

    document_id: str = Field(pattern=r"^doc_[0-9a-f]{16}$")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    filename: str = Field(min_length=1, max_length=255)
    media_type: MediaType
    size_bytes: int = Field(ge=1)
    uploaded_at: datetime

    @classmethod
    def from_content(cls, content: bytes, filename: str, media_type: MediaType) -> UploadedDocument:
        """`document_id` suy ra từ sha256 nội dung: cùng file ⇒ cùng id (tải lại không nhân bản tài liệu)."""
        digest = hashlib.sha256(content).hexdigest()
        return cls(
            document_id=f"doc_{digest[:16]}",
            sha256=digest,
            filename=filename,
            media_type=media_type,
            size_bytes=len(content),
            uploaded_at=datetime.now(UTC),
        )


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    BLOCKING = "blocking"


class ExtractedCell(BaseModel):
    row: int = Field(ge=0)
    col: int = Field(ge=0)
    text: str
    confidence: float = Field(ge=0.0, le=1.0)
    verified_by_human: bool = False


class ExtractedTable(BaseModel):
    table_id: str
    page_number: int = Field(ge=1)
    version: int = Field(default=1, ge=1)
    headers: list[str]
    cells: list[ExtractedCell]

    @model_validator(mode="after")
    def _grid(self) -> ExtractedTable:
        coords = {(c.row, c.col) for c in self.cells}
        if len(coords) != len(self.cells):
            raise ValueError("trùng toạ độ ô")
        if any(c.col >= len(self.headers) for c in self.cells):
            raise ValueError("ô vượt số cột header")
        return self

    @property
    def n_rows(self) -> int:
        return 1 + max((c.row for c in self.cells), default=-1)

    def cell(self, row: int, col: int) -> ExtractedCell | None:
        for c in self.cells:
            if c.row == row and c.col == col:
                return c
        return None


class GateIssue(BaseModel):
    code: Literal[
        "DUAL_PASS_MISMATCH",
        "LOW_CONFIDENCE",
        "HEADER_MISMATCH",
        "NOT_A_NUMBER",
        "OUT_OF_RANGE",
        "DUPLICATE_LABEL",
        "MISSING_CELL",
        "NOT_IN_TEXT_LAYER",
        "SELF_SOLVE_MISMATCH",
        "LATEX_UNBALANCED",
        "CROSS_CHECK_UNAVAILABLE",  # ADR-008 mục 11; spec §5.3, §7.2, §7.4
    ]
    severity: Severity
    message_vi: str
    row: int | None = None
    col: int | None = None
    alternatives: list[str] = Field(default_factory=list)


class GateStatus(str, Enum):
    AUTO_PASS = "AUTO_PASS"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    BLOCKED = "BLOCKED"


class GateReport(BaseModel):
    table_id: str
    table_version: int
    status: GateStatus
    issues: list[GateIssue]


class PatchAudit(BaseModel):
    table_id: str
    from_version: int
    to_version: int
    editor_id: str
    changes: list[dict[str, Any]]
    at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class IngestionItemCreate(BaseModel):
    source_doc_id: str
    page_number: int = Field(ge=1)
    topic_key: str
    stem_latex: str
    options: dict[OptionKey, str]
    table_primary: ExtractedTable
    table_secondary: ExtractedTable | None = None
    page_text_layer: str | None = None
    solver_inputs: SolverInputs = Field(default_factory=SolverInputs)


class IngestionItem(IngestionItemCreate):
    item_id: str
    non_cloneable: bool = False
    non_cloneable_reason: str | None = None
    problem: CPUSchedulingProblem | None = None
    self_solve: SelfSolveResult | None = None
    report: GateReport | None = None
    audits: list[PatchAudit] = Field(default_factory=list)
    approved: bool = False
