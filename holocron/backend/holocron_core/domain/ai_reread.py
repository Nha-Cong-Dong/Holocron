"""Đọc lại khối bằng AI theo yêu cầu người dùng (spec §6.10a; ADR-009 mục 15).

Model của response `POST …/blocks/{block_id}/ai-reread` và body của `…/accept`. Chữ trong `AiRereadBlock` là chữ VLM
ĐÃ QUA `services/vision_gate.judge_region` (I3); đề xuất `rejected` không mang chữ VLM nào. Đề xuất KHÔNG ghi kho
trang: nó chỉ nằm ở kho tạm theo instance store (`AiRereadProposalRepository`).
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .blocks import BLOCK_ID_PATTERN, DOCUMENT_ID_PATTERN, BlockFlag, BlockType
from .documents import MAX_EDITOR_ID_CHARS
from .schemas import StrictModel

AiRereadStatus = Literal["accepted_text", "rejected"]
AiRereadTokenKind = Literal["number", "code"]
# Lý do quyết định của cổng (`services/vision_gate.py`); khai báo ở domain để response dùng được (§6.10a).
GateReason = Literal[
    "ok",
    "status_invalid",
    "status_unavailable",
    "text_layer_overlap",
    "invalid_overlap",
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
AiRereadErrorCode = Literal[
    "BLOCK_NOT_ELIGIBLE",
    "AI_NOT_CONFIGURED",
    "AI_MODE_OFF",
    "AI_BUDGET_EXHAUSTED",
    "VLM_TIMEOUT",
    "VLM_RATE_LIMITED",
    "VLM_UPSTREAM_ERROR",
]

PROPOSAL_ID_PATTERN = r"^prp_[0-9a-f]{12}$"
MISMATCH_ID_PATTERN = r"^vmm_[0-9a-f]{12}$"
MAX_TOKEN_CHARS = 200
# Cờ khối cho phép đọc lại (§6.10a "Khối đủ điều kiện"; đề xuất suy ra, mở rộng là đổi contract).
AI_REREAD_FLAGS: frozenset[BlockFlag] = frozenset({"table_unresolved", "code_layout_uncertain", "low_text_contrast"})
# Trần kho tạm đề xuất của MỘT instance store (§6.10a "Lưu đề xuất"); đầy ⇒ bỏ đề xuất cũ nhất.
MAX_AI_REREAD_PROPOSALS = 50


class AiRereadMismatch(StrictModel):
    """Một chỗ lệch token số/mã của ứng viên bảng (`GateTokenMismatch` kèm `mismatch_id`)."""

    mismatch_id: str = Field(pattern=MISMATCH_ID_PATTERN)
    token_kind: AiRereadTokenKind
    row: int | None = Field(default=None, ge=0)
    col: int | None = Field(default=None, ge=0)
    vlm_value: str | None = Field(default=None, max_length=MAX_TOKEN_CHARS)
    ocr_value: str | None = Field(default=None, max_length=MAX_TOKEN_CHARS)


class AiRereadBlock(StrictModel):
    """Khối đề xuất duy nhất của vùng (như `judge_region`, ADR-009 mục 2): `text` null ⇔ `type = table` ⇔ `rows`
    khác null."""

    type: BlockType
    text: str | None
    rows: list[list[str]] | None
    flags: list[BlockFlag]

    @model_validator(mode="after")
    def _shape_matches_type(self) -> AiRereadBlock:
        table = self.type == "table"
        if (self.text is None) != table or (self.rows is not None) != table:
            raise ValueError("text null và rows khác null khi và chỉ khi type = table")
        return self


class AiRereadProposal(StrictModel):
    """Đề xuất đọc lại một khối (spec §6.10a). `blocks` có đúng một phần tử khi `status = accepted_text`, rỗng khi
    `rejected` (chữ VLM bị cổng từ chối KHÔNG ra response, I3)."""

    proposal_id: str = Field(pattern=PROPOSAL_ID_PATTERN)
    document_id: str = Field(pattern=DOCUMENT_ID_PATTERN)
    page_number: int = Field(ge=1)
    block_id: str = Field(pattern=BLOCK_ID_PATTERN)
    page_version: int = Field(ge=1)
    status: AiRereadStatus
    reason: GateReason
    blocks: list[AiRereadBlock]
    mismatches: list[AiRereadMismatch]
    ocr_text: str
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    from_cache: bool
    recipient_label: str

    @model_validator(mode="after")
    def _consistent(self) -> AiRereadProposal:
        accepted = self.status == "accepted_text"
        if accepted != (len(self.blocks) == 1) or (not accepted and self.blocks):
            raise ValueError("blocks có đúng một phần tử khi status = accepted_text, rỗng khi rejected")
        if accepted != (self.reason == "ok"):
            raise ValueError("reason = ok khi và chỉ khi status = accepted_text")
        if self.mismatches and not (accepted and self.blocks[0].type == "table"):
            raise ValueError("mismatches chỉ có ở ứng viên bảng đã qua cổng")
        return self


class AiRereadAcceptRequest(StrictModel):
    """Body của `POST …/ai-reread/{proposal_id}/accept` (như `PageConfirmRequest`, §6.5)."""

    editor_id: str = Field(min_length=1, max_length=MAX_EDITOR_ID_CHARS)
    expected_version: int = Field(ge=1)


AiRereadProposalState = Literal["pending", "discarded"]


class AiRereadProposalRecord(StrictModel):
    """Một đề xuất trong kho tạm (nội bộ, không ra response): đề xuất và trạng thái của nó. `discarded` ⇒ người dùng
    đã bấm Bỏ (giữ lại để Bỏ lần nữa vẫn 204, §6.10a "Reject")."""

    proposal: AiRereadProposal
    state: AiRereadProposalState = "pending"
