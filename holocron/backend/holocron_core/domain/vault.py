"""Model của Knowledge Vault ngoài `KnowledgeChunk` (SPECIFICATION §5.8, §6.6; ADR-006, ADR-007).

- `ConfirmedPage`/`ConfirmedDocumentText`: văn bản người dùng đã xác nhận ở bộ nhập tài liệu, đầu vào duy nhất của
  bộ cắt chunk (§12.1, ADR-006 mục 1 và 3).
- `ConceptPair`: cặp khái niệm – trích dẫn định nghĩa (ADR-007 mục 1). `definition_quote` được lưu ở dạng NFC đã gộp
  khoảng trắng. Model không thấy chunk, nên việc kiểm nó là chuỗi con của `raw_text` của chunk (I3) nằm ở
  `check_definition_quote`: service gọi trước khi ghi, và mọi repository gọi lại trong giao dịch ghi và khi nạp.
- `VaultEdit`: ai sửa và lúc nào (hàng Repudiation của §13), lưu kèm chunk hoặc cặp; không xuất hiện trong response.
- `link_practice_chunks`: `linked_chunk_ids` theo §12.1, hàm thuần mà repository gọi trong giao dịch ghi.
- `SearchHit`: một kết quả tìm kiếm BM25 (§6.6).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import datetime
from enum import Enum
from typing import Annotated, Literal, get_args
from uuid import uuid4

from pydantic import Field, field_validator, model_validator

from .limits import MAX_LINE_CHARS
from .schemas import TOPIC_KEY_PATTERN, ChunkCategory, KnowledgeChunk, StrictModel, normalize_quote, quote_in_text

SUBJECT_CODE_PATTERN = r"^[A-Z]{2,6}[0-9]{2,4}$"
CHAPTER_CODE_PATTERN = r"^ch[0-9]{1,2}(\.[0-9]{1,2})?$"
MAX_PAGE_LINES = 5000
MIN_QUOTE_CHARS = 8
MAX_DOCUMENT_PAGES = 300  # cùng giới hạn trang của bộ nhập PDF (§6.5, ADR-008 mục 4)
MAX_TOPIC_KEY_CHARS = 128
MAX_TOPIC_KEYS = 32
MAX_ID_CHARS = 128  # `chunk_id`, `pair_id`, `editor_id`

TopicKey = Annotated[str, Field(pattern=TOPIC_KEY_PATTERN, max_length=MAX_TOPIC_KEY_CHARS)]


class ConfirmedPage(StrictModel):
    """Một trang đã xác nhận: các dòng theo thứ tự đọc; dòng rỗng là ranh giới đoạn."""

    page_number: int = Field(ge=1)
    lines: list[str] = Field(max_length=MAX_PAGE_LINES)

    @field_validator("lines")
    @classmethod
    def _line_length(cls, lines: list[str]) -> list[str]:
        for line in lines:
            if len(line) > MAX_LINE_CHARS:
                raise ValueError(f"mỗi dòng tối đa {MAX_LINE_CHARS} ký tự")
        return lines


class ConfirmedDocumentText(StrictModel):
    """Văn bản đã xác nhận của một tài liệu cùng nhãn khởi đầu cho mọi chunk.

    Nhãn khởi đầu (`chapter_code`, `category`, `topic_keys`) chỉ là đề xuất (`label_status = "proposed"`); người
    dùng sửa và xác nhận từng chunk qua `PATCH /v1/vault/chunks/{chunk_id}/labels`.
    """

    doc_id: str = Field(min_length=3, max_length=128)
    subject_code: str = Field(pattern=SUBJECT_CODE_PATTERN)
    chapter_code: str = Field(pattern=CHAPTER_CODE_PATTERN)
    category: ChunkCategory = ChunkCategory.THEORY
    topic_keys: list[TopicKey] = Field(default_factory=list, max_length=MAX_TOPIC_KEYS)
    pages: list[ConfirmedPage] = Field(min_length=1, max_length=MAX_DOCUMENT_PAGES)

    @model_validator(mode="after")
    def _pages_in_order(self) -> ConfirmedDocumentText:
        numbers = [page.page_number for page in self.pages]
        if numbers != sorted(set(numbers)):
            raise ValueError("pages phải theo page_number tăng dần, không trùng")
        return self


class QuoteMismatchError(ValueError):
    """Trích dẫn không phải chuỗi con nguyên văn (NFC + gộp khoảng trắng) của văn bản chunk (I3)."""

    def __init__(self, chunk_id: str) -> None:
        super().__init__(
            f"definition_quote không phải chuỗi con nguyên văn của chunk {chunk_id} "
            "(đã chuẩn hoá NFC và khoảng trắng, I3)"
        )
        self.chunk_id = chunk_id


def check_definition_quote(chunk_id: str, raw_text: str, definition_quote: str) -> None:
    """I3: `definition_quote` là chuỗi con của `raw_text` của chunk sau NFC và gộp khoảng trắng.

    Sai ⇒ `QuoteMismatchError`; không sửa hộ trích dẫn.
    """
    if not quote_in_text(definition_quote, raw_text):
        raise QuoteMismatchError(chunk_id)


class VaultEdit(StrictModel):
    """Người sửa và thời điểm của lần sửa gần nhất (sửa nhãn chunk; tạo, xác nhận, từ chối cặp khái niệm)."""

    editor_id: str = Field(min_length=1, max_length=MAX_ID_CHARS)
    at: datetime


class ConceptPairStatus(str, Enum):
    PROPOSED = "proposed"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


def find_term_spans(term: str, quote: str) -> list[tuple[int, int]]:
    """Vị trí `[start, end)` của `term` trong `quote` (cả hai NFC), không phân biệt hoa thường, đúng ranh giới chữ.

    `re.IGNORECASE` chỉ dùng phép gập chữ đơn giản nên không đổi độ dài chuỗi; vị trí tính trên chính `quote`.
    """
    if not term:
        return []
    pattern = re.compile(rf"(?<!\w){re.escape(term)}(?!\w)", re.IGNORECASE)
    return [(match.start(), match.end()) for match in pattern.finditer(quote)]


PairGate = Literal["G1_SOURCE", "G2_GLYPH", "G3_OCCLUDED", "G4_COLUMN_MIX", "G5_NUMBERS", "EXERCISE"]
"""Cổng tích sẵn của cặp (§5.8, ADR-007 mục 3); thứ tự khai báo là thứ tự sắp của `ConceptPair.failed_gates`."""

PAIR_GATE_ORDER: tuple[PairGate, ...] = get_args(PairGate)


class ConceptPair(StrictModel):
    """Cặp khái niệm – trích dẫn định nghĩa (§5.8, ADR-007 mục 1).

    `pre_selected` (máy tích sẵn ô Chấp nhận) và `failed_gates` (cổng trượt) do code tính một lần lúc lưu cặp (ADR-007
    mục 3). Tích sẵn không phải xác nhận: `status` vẫn `proposed`. `pre_selected = true` chỉ khi `failed_gates` rỗng.
    """

    pair_id: str = Field(default_factory=lambda: f"cp_{uuid4().hex[:16]}")
    chunk_id: str = Field(min_length=1, max_length=128)
    subject_code: str = Field(pattern=SUBJECT_CODE_PATTERN)
    chapter_code: str = Field(pattern=CHAPTER_CODE_PATTERN)
    term: str = Field(min_length=1, max_length=120)
    definition_quote: str = Field(min_length=8, max_length=600)
    term_spans: list[tuple[int, int]] = Field(default_factory=list)
    status: ConceptPairStatus
    proposed_by: Literal["llm", "user"]
    mapping_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    version: int = Field(default=1, ge=1)
    created_at: datetime
    confirmed_at: datetime | None = None
    pre_selected: bool = False
    failed_gates: list[PairGate] = Field(default_factory=list)

    @field_validator("term")
    @classmethod
    def _term_nfc(cls, value: str) -> str:
        return normalize_quote(value)

    @field_validator("failed_gates")
    @classmethod
    def _gates_ordered_unique(cls, value: list[PairGate]) -> list[PairGate]:
        """Sắp theo thứ tự khai báo của `PairGate`, bỏ trùng."""
        present = set(value)
        return [gate for gate in PAIR_GATE_ORDER if gate in present]

    @field_validator("definition_quote")
    @classmethod
    def _quote_nfc(cls, value: str) -> str:
        normalized = normalize_quote(value)
        if len(normalized) < MIN_QUOTE_CHARS:
            raise ValueError(f"definition_quote phải có ít nhất {MIN_QUOTE_CHARS} ký tự sau khi gộp khoảng trắng")
        return normalized

    @model_validator(mode="after")
    def _rules(self) -> ConceptPair:
        if (self.status is ConceptPairStatus.CONFIRMED) != (self.confirmed_at is not None):
            raise ValueError("confirmed_at có giá trị khi và chỉ khi status = confirmed")
        if self.pre_selected and self.failed_gates:
            raise ValueError("pre_selected = true chỉ khi failed_gates rỗng")
        # Vị trí term luôn do code tính lại từ term và definition_quote đã chuẩn hoá.
        self.term_spans = find_term_spans(self.term, self.definition_quote)
        return self


class SearchHit(StrictModel):
    """Một kết quả tìm kiếm (§6.6). `score` là BM25, chỉ để xếp hạng."""

    chunk: KnowledgeChunk
    score: float
    index: Literal["primary", "no_diacritics"]


def _page_gap(chunk: KnowledgeChunk, other: KnowledgeChunk) -> int:
    """Khoảng cách trang giữa hai đoạn `[page_number, page_end]`; hai đoạn chồng nhau thì 0."""
    first_end = chunk.page_end or chunk.page_number
    other_end = other.page_end or other.page_number
    return max(0, other.page_number - first_end, chunk.page_number - other_end)


def _nearest_theory(chunk: KnowledgeChunk, candidates: list[KnowledgeChunk]) -> str:
    """`chunk_id` của chunk gần `chunk` nhất: cùng tài liệu trước, rồi khoảng cách trang, trang, `chunk_id`."""
    ranked = sorted(
        candidates,
        key=lambda other: (other.doc_id != chunk.doc_id, _page_gap(chunk, other), other.page_number, other.chunk_id),
    )
    return ranked[0].chunk_id


def link_practice_chunks(chunks: Sequence[KnowledgeChunk]) -> dict[str, list[str]]:
    """`linked_chunk_ids` của mọi chunk trong `chunks` (các chunk của cùng một môn) theo §12.1.

    Chunk `PRACTICE`: với mỗi `topic_key` của nó, liên kết tới chunk `THEORY` có cùng khoá đó và gần nhất về trang.
    "Gần nhất" xét lần lượt: cùng tài liệu trước tài liệu khác, khoảng cách trang nhỏ hơn, trang nhỏ hơn, `chunk_id`
    nhỏ hơn; nên kết quả tất định. Danh sách sắp theo `chunk_id`, không trùng. Chunk `THEORY` và chunk `PRACTICE` không
    có `topic_keys` thì danh sách rỗng.
    """
    theory = [chunk for chunk in chunks if chunk.category is ChunkCategory.THEORY]
    links: dict[str, list[str]] = {}
    for chunk in chunks:
        found: set[str] = set()
        if chunk.category is ChunkCategory.PRACTICE:
            for topic_key in chunk.topic_keys:
                candidates = [other for other in theory if topic_key in other.topic_keys]
                if candidates:
                    found.add(_nearest_theory(chunk, candidates))
        links[chunk.chunk_id] = sorted(found)
    return links


# --------------------------------------------------------------------------- #
# Job nền đề xuất cặp (§5.8 "Job nền đề xuất cặp", §6.6; ADR-007 mục 3)
# --------------------------------------------------------------------------- #

ProposalJobStatus = Literal["queued", "running", "succeeded", "partial", "failed", "cancelled"]
ProposalRejectReason = Literal[
    "quote_not_verbatim",
    "unknown_chunk",
    "invalid_term",
    "invalid_quote",
    "duplicate",
    "chunk_limit",
    "proposal_limit",
    "unknown_span",
    "span_cross_chunk",
    "term_span_cross_chunk",
    "term_not_in_span",
    "anchor_not_found",
    "anchor_ambiguous",
]
"""Lý do loại một đề xuất. `term_span_cross_chunk`: span của term khác chunk với span định nghĩa (thêm theo điều
phối 2026-10-31, chờ spec cập nhật)."""
BatchErrorKind = Literal["llm_upstream", "deadline", "format"]
PROPOSAL_JOB_ID_PATTERN = r"^pjob_[0-9a-f]{16}$"
MAX_PROPOSAL_JOB_BATCH_CHUNKS = 20


class ProposalJob(StrictModel):
    """Trạng thái một job nền đề xuất cặp; mọi số đếm do backend tính, frontend chỉ hiển thị (I1)."""

    job_id: str = Field(pattern=PROPOSAL_JOB_ID_PATTERN)
    subject_code: str = Field(pattern=SUBJECT_CODE_PATTERN)
    trigger: Literal["document_confirmed", "manual"]
    document_id: str | None
    status: ProposalJobStatus
    chunk_count: int = Field(ge=1)
    batch_size: int = Field(ge=1, le=MAX_PROPOSAL_JOB_BATCH_CHUNKS)
    batches_total: int = Field(ge=1)
    batches_done: int = Field(ge=0)
    batches_failed: int = Field(ge=0)
    batch_errors: dict[BatchErrorKind, int] = Field(default_factory=dict)
    proposed_count: int = Field(ge=0)
    pre_selected_count: int = Field(ge=0)
    rejected_count: int = Field(ge=0)
    rejected_by_reason: dict[ProposalRejectReason, int] = Field(default_factory=dict)
    exercise_chunks: int = Field(ge=0)
    cancel_requested: bool = False
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @model_validator(mode="after")
    def _counts(self) -> ProposalJob:
        if sum(self.rejected_by_reason.values()) != self.rejected_count:
            raise ValueError("tổng rejected_by_reason phải bằng rejected_count")
        if self.batches_done + self.batches_failed > self.batches_total:
            raise ValueError("batches_done + batches_failed không vượt batches_total")
        if self.pre_selected_count > self.proposed_count:
            raise ValueError("pre_selected_count không vượt proposed_count")
        return self
