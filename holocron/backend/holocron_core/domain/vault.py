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
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Annotated, Literal, get_args
from uuid import uuid4

from pydantic import Field, field_validator, model_validator

from .blocks import Block, BlockType, Section
from .limits import MAX_LINE_CHARS
from .schemas import TOPIC_KEY_PATTERN, ChunkCategory, KnowledgeChunk, StrictModel, normalize_quote, quote_in_text
from .search_text import TOKEN_RE

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


class ChunkBlock(StrictModel):
    """Một khối ỨNG VIÊN có chữ đưa vào cắt chunk theo khối (ADR-010 mục 16): khối tiêu đề lặp do nối trang đã bị bỏ
    (chỉ giữ khối tiêu đề đầu của Section), `section_id` là Section sâu nhất chứa khối."""

    block_id: str = Field(pattern=r"^blk_[0-9a-f]{16}$")
    page_number: int = Field(ge=1)
    reading_order: int = Field(ge=0)
    is_heading: bool
    text: str
    section_id: str = Field(pattern=r"^sec_[0-9a-f]{16}$")


class BlockDocumentText(StrictModel):
    """Đầu vào cắt chunk của tài liệu có khối (ADR-010 mục 16): khối ứng viên có chữ theo (trang, `reading_order`) và
    văn bản đã xác nhận của các trang KHÔNG có khối (`LAYOUT_FAILED`, OCR), cùng nhãn khởi đầu như
    `ConfirmedDocumentText`. Trang của `line_pages` không có khối nào trong `blocks`."""

    doc_id: str = Field(min_length=3, max_length=128)
    subject_code: str = Field(pattern=SUBJECT_CODE_PATTERN)
    chapter_code: str = Field(pattern=CHAPTER_CODE_PATTERN)
    category: ChunkCategory = ChunkCategory.THEORY
    topic_keys: list[TopicKey] = Field(default_factory=list, max_length=MAX_TOPIC_KEYS)
    blocks: list[ChunkBlock] = Field(default_factory=list)
    line_pages: list[ConfirmedPage] = Field(default_factory=list, max_length=MAX_DOCUMENT_PAGES)

    @model_validator(mode="after")
    def _consistent(self) -> BlockDocumentText:
        numbers = [page.page_number for page in self.line_pages]
        if numbers != sorted(set(numbers)):
            raise ValueError("line_pages phải theo page_number tăng dần, không trùng")
        if set(numbers) & {block.page_number for block in self.blocks}:
            raise ValueError("trang của line_pages không được có khối")
        if len({block.block_id for block in self.blocks}) != len(self.blocks):
            raise ValueError("block_id trùng")
        return self

    def labels(self, pages: Sequence[ConfirmedPage]) -> ConfirmedDocumentText:
        """Văn bản đã xác nhận của một dãy trang không có khối, cùng nhãn khởi đầu (đầu vào của `build_chunks`)."""
        return ConfirmedDocumentText(
            doc_id=self.doc_id,
            subject_code=self.subject_code,
            chapter_code=self.chapter_code,
            category=self.category,
            topic_keys=list(self.topic_keys),
            pages=list(pages),
        )


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
"""Lý do loại một đề xuất. `term_span_cross_chunk`: span của term khác chunk với span định nghĩa (mã con trỏ thứ sáu, do
`services/concept_proposal_pointer.py` đặt khi `term_span_id` không cùng chunk với `def_span_ids`; có trong
`src/types/holocron.ts`, spec §6.6 mới liệt kê năm mã con trỏ)."""
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


# --------------------------------------------------------------------------- #
# Tra cứu khối (SPECIFICATION §6.10, §12.3; ADR-010 mục 18). Thêm 2026-11-03 (X10).
# --------------------------------------------------------------------------- #


class SectionPathItem(StrictModel):
    section_id: str
    title: str | None


class MatchSpan(StrictModel):
    """Khoảng khớp trong chữ GỐC; `start`/`end` là đơn vị UTF-16 code unit của chuỗi đích (`start < end`)."""

    target: Literal["text", "cell", "label"]
    row: int | None = None
    cell_index: int | None = None
    label_index: int | None = None
    start: int = Field(ge=0)
    end: int = Field(ge=1)

    @model_validator(mode="after")
    def _span_consistent(self) -> MatchSpan:
        if self.start >= self.end:
            raise ValueError("start phải nhỏ hơn end")
        if (self.row is not None or self.cell_index is not None) != (self.target == "cell"):
            raise ValueError("row và cell_index chỉ có khi target = 'cell'")
        if (self.label_index is not None) != (self.target == "label"):
            raise ValueError("label_index chỉ có khi target = 'label'")
        return self


class BlockSearchHit(StrictModel):
    block: Block
    document_title: str
    chapter_code: str
    section_path: list[SectionPathItem]
    match_kind: Literal["exact", "folded", "fuzzy"]
    match_spans: list[MatchSpan] = Field(min_length=1)
    score: float


class BlockSearchPage(StrictModel):
    total: int = Field(ge=0, le=1000)
    total_capped: bool
    hits: list[BlockSearchHit]
    engine: Literal["fts5", "fts5_trigram", "python"]
    layout_failed_pages: int = Field(ge=0)
    pages_without_blocks: int = Field(ge=0)
    pages_unconfirmable_with_blocks: int = Field(ge=0)


# Loại khối không tra cứu được (không bao giờ đủ điều kiện, §5.15).
BLOCK_SEARCH_EXCLUDED_TYPES: frozenset[BlockType] = frozenset({"header_footer", "page_number"})


@dataclass(frozen=True)
class FoldedText:
    """Chữ đã gấp dấu kèm vị trí trong chuỗi gốc (đơn vị code point; đổi UTF-16 ở `utf16_offsets`).

    `kept` = NFC + `casefold` (giữ dấu), `folded` = `kept` bỏ dấu kết hợp và `đ` thành `d`. Với mỗi ký tự của hai chuỗi,
    `*_starts[i]`/`*_ends[i]` là khoảng của cụm (ký tự gốc + dấu kết hợp đi kèm) sinh ra nó trong chuỗi gốc.
    """

    original: str
    kept: str
    kept_starts: tuple[int, ...]
    kept_ends: tuple[int, ...]
    folded: str
    folded_starts: tuple[int, ...]
    folded_ends: tuple[int, ...]


def _clusters(text: str) -> list[tuple[int, int]]:
    """Cụm = một ký tự cùng các dấu kết hợp theo sau; khoảng `[start, end)` theo code point."""
    bounds: list[tuple[int, int]] = []
    start = 0
    for index, char in enumerate(text):
        if index > start and not unicodedata.combining(char):
            bounds.append((start, index))
            start = index
    if text:
        bounds.append((start, len(text)))
    return bounds


def _strip_marks(text: str) -> str:
    stripped = "".join(c for c in unicodedata.normalize("NFD", text) if not unicodedata.combining(c))
    return unicodedata.normalize("NFC", stripped).replace("đ", "d")


def fold_text(text: str) -> FoldedText:
    """Gấp dấu theo ADR-010 mục 18: NFC, `casefold`, bỏ dấu kết hợp, `đ` thành `d`; kèm ánh xạ về chuỗi gốc.

    Dùng chung lúc lập chỉ mục và truy vấn (bảng không dấu, trigram, đường lùi Python). Chỉ mục CHUNK không dùng hàm này.
    """
    kept: list[str] = []
    kept_starts: list[int] = []
    kept_ends: list[int] = []
    folded: list[str] = []
    folded_starts: list[int] = []
    folded_ends: list[int] = []
    for start, end in _clusters(text):
        cluster_kept = unicodedata.normalize("NFC", unicodedata.normalize("NFC", text[start:end]).casefold())
        cluster_folded = _strip_marks(cluster_kept)
        kept.append(cluster_kept)
        kept_starts.extend([start] * len(cluster_kept))
        kept_ends.extend([end] * len(cluster_kept))
        folded.append(cluster_folded)
        folded_starts.extend([start] * len(cluster_folded))
        folded_ends.extend([end] * len(cluster_folded))
    return FoldedText(
        original=text,
        kept="".join(kept),
        kept_starts=tuple(kept_starts),
        kept_ends=tuple(kept_ends),
        folded="".join(folded),
        folded_starts=tuple(folded_starts),
        folded_ends=tuple(folded_ends),
    )


def utf16_offsets(text: str) -> list[int]:
    """`result[i]` = số UTF-16 code unit đứng trước code point thứ `i`; phần tử cuối là độ dài UTF-16 của `text`."""
    offsets = [0]
    for char in text:
        offsets.append(offsets[-1] + (2 if ord(char) > 0xFFFF else 1))
    return offsets


@dataclass(frozen=True)
class SearchField:
    """Một chuỗi đích có thể có khoảng khớp của khối (target/row/cell_index/label_index như `MatchSpan`)."""

    target: Literal["text", "cell", "label"]
    text: str
    row: int | None = None
    cell_index: int | None = None
    label_index: int | None = None


def block_search_fields(block: Block) -> list[SearchField]:
    """Chữ lập chỉ mục của khối: `text`, từng ô bảng, từng nhãn figure (ADR-010 mục 18); theo thứ tự cố định."""
    fields: list[SearchField] = []
    if block.text:
        fields.append(SearchField("text", block.text))
    for row_index, row in enumerate(block.cells or []):
        for cell_index, cell in enumerate(row):
            if cell.text:
                fields.append(SearchField("cell", cell.text, row=row_index, cell_index=cell_index))
    for label_index, label in enumerate(block.labels):
        if label:
            fields.append(SearchField("label", label, label_index=label_index))
    return fields


def block_index_body(block: Block) -> str:
    """`body` của `blocks_fts`: token đã gấp dấu của mọi chuỗi đích, nối bằng một dấu cách."""
    return " ".join(
        token for field in block_search_fields(block) for token in TOKEN_RE.findall(fold_text(field.text).folded)
    )


def block_trigram_body(block: Block) -> str:
    """`body` của `blocks_fts_trigram`: văn bản đã gấp dấu, gộp khoảng trắng; các chuỗi đích cách nhau một dấu cách."""
    return " ".join(" ".join(fold_text(field.text).folded.split()) for field in block_search_fields(block))


def section_path_of(block_id: str, sections: Sequence[Section]) -> list[SectionPathItem]:
    """Đường từ gốc (chương) tới Section chứa khối; không Section nào chứa ⇒ rỗng."""
    by_id = {section.section_id: section for section in sections}
    holder = next((section for section in sections if block_id in section.block_ids), None)
    path: list[SectionPathItem] = []
    seen: set[str] = set()
    while holder is not None and holder.section_id not in seen:
        seen.add(holder.section_id)
        path.append(SectionPathItem(section_id=holder.section_id, title=holder.title))
        holder = None if holder.parent_id is None else by_id.get(holder.parent_id)
    path.reverse()
    return path
