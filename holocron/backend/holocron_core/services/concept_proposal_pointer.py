"""Đề xuất cặp khái niệm theo giao thức con trỏ cho một lô của job nền (spec §5.8 "Giao thức con trỏ"; ADR-007 mục 3).

LLM không chép trích dẫn: nó trỏ vào các dòng có id `S1..Sn` (đánh số liên tục trong lô, chỉ dòng không rỗng của
`raw_text`, tách theo `\\n`) và trả MỘT object `{"chunks": [{chunk_id, is_exercise}], "pairs": [{term_span_id,
term_exact, def_span_ids, def_start_words, def_end_words}]}`. Python cắt trích dẫn từ `raw_text`:

1. `def_span_ids` là các id liền nhau tăng dần của CÙNG một chunk (sai ⇒ `span_cross_chunk`; id lạ ⇒ `unknown_span`);
   `term_span_id` cùng chunk đó (sai ⇒ `term_span_cross_chunk`). `W` = đoạn `raw_text` từ đầu span đầu tới cuối span
   cuối.
2. `term_exact` là chuỗi con (sau NFC) của span `term_span_id` (sai ⇒ `term_not_in_span`); `term` = `term_exact` đã
   gộp khoảng trắng.
3. Mốc: `def_start_words` xuất hiện đúng một lần trong `W`; `def_end_words` đúng một lần trong phần `W` từ chỗ bắt đầu
   (kết thúc không sớm hơn mốc đầu). So sau NFC; một dãy khoảng trắng trong mốc khớp một dãy khoảng trắng bất kỳ.
   Trích dẫn = lát cắt của `raw_text`, không ghép hay sửa chữ.
4. Mốc hỏng ⇒ MỘT lời gọi xin mốc dài hơn cho mọi phần tử hỏng của lô, nằm trong cùng `deadline_s` và cùng ngân sách
   `json_retry_max` của lô; vẫn hỏng ⇒ loại (`anchor_not_found` / `anchor_ambiguous`). Lời gọi đó lỗi hoặc quá hạn
   ⇒ lô mang lỗi tương ứng (`BatchErrorKind`), nhưng cặp hợp lệ của lượt đầu VẪN được lưu.
5. Trích dẫn đã cắt VẪN qua cổng hiện có (`screen_proposal`: kiểm nguyên văn I3, độ dài, bỏ trùng, trần mỗi chunk),
   rồi nhận `failed_gates`/`pre_selected` và được lưu `proposed`, `proposed_by = "llm"`.

Nhãn bài tập: chỉ đọc từ `chunks`; LLM phải trả nhãn true/false cho MỌI chunk của lô. Field `is_exercise` trên phần
tử của `pairs` (nếu có) bị bỏ qua, không phải phiếu nhãn. Chunk thiếu nhãn, có nhãn null, hoặc nhãn xung đột (cùng
`chunk_id` hai giá trị) ⇒ lưu `is_exercise = null`, cổng `EXERCISE` trượt. Nhãn được ghi (so version; lệch thì đọc
lại và thử lại có giới hạn) TRƯỚC khi tính cổng các cặp của chunk; ghi không được ⇒ coi như null.
LLM KHÔNG BAO GIỜ xác nhận cặp.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol

from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from ..domain.llm import ChatCompletionClient, ChatMessage, LlmUpstreamError
from ..domain.repositories import (
    MissingReferenceError,
    RecordNotFoundError,
    VaultRepository,
    VersionConflictError,
)
from ..domain.schemas import KnowledgeChunk, normalize_quote
from ..domain.vault import BatchErrorKind, ConceptPair, ProposalRejectReason, QuoteMismatchError
from ..infrastructure.llm_config import LlmTaskLimits
from .concept_proposal import (
    MAX_TERM_CHARS,
    ProposalFormatError,
    ProposalStats,
    RawProposal,
    gate_annotator,
    json_array,
    json_object,
    pair_key,
    screen_proposal,
)
from .concept_proposal_gates import PageLookup, PageSource, page_lookup
from .concept_proposal_prompt import (
    SpanPayload,
    build_anchor_format_retry_messages,
    build_anchor_retry_messages,
    build_pointer_messages,
    build_pointer_retry_messages,
)

TASK_NAME = "concept_pair_pointer_batch"
MAX_LIST_ITEMS = 64
"""Số id tối đa trong `def_span_ids` của một phần tử (dài hơn ⇒ phản hồi sai dạng)."""

AnchorFailure = Literal["anchor_not_found", "anchor_ambiguous"]

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Span và parse
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Span:
    """Một dòng không rỗng của `raw_text`: `[start, end)` là vị trí trong `raw_text` của chunk."""

    span_id: str
    chunk_id: str
    index: int
    start: int
    end: int


def build_spans(chunks: Sequence[KnowledgeChunk]) -> list[Span]:
    """Span `S1..Sn` của cả lô, đánh số liên tục theo thứ tự chunk rồi thứ tự dòng."""
    spans: list[Span] = []
    for chunk in chunks:
        position = 0
        for line in chunk.raw_text.split("\n"):
            if line.strip():
                number = len(spans) + 1
                spans.append(Span(f"S{number}", chunk.chunk_id, number, position, position + len(line)))
            position += len(line) + 1
    return spans


class PointerProposal(BaseModel):
    """Một phần tử cặp LLM trả về (field thừa bị bỏ qua)."""

    term_span_id: str
    term_exact: str
    def_span_ids: list[str] = Field(max_length=MAX_LIST_ITEMS)
    def_start_words: str
    def_end_words: str


class ChunkExerciseLabel(BaseModel):
    """Một phần tử nhãn `{chunk_id, is_exercise}`; `is_exercise` null ⇒ nhãn chưa xác định."""

    chunk_id: str
    is_exercise: bool | None


class AnchorFix(BaseModel):
    index: int = Field(ge=0)
    def_start_words: str
    def_end_words: str


class PointerResponse(BaseModel):
    """Object LLM trả: `chunks` (nhãn bài tập) và `pairs` (con trỏ); thiếu một trong hai khoá ⇒ sai dạng."""

    chunks: list[ChunkExerciseLabel]
    pairs: list[PointerProposal]


_RESPONSE = TypeAdapter(PointerResponse)
_FIXES = TypeAdapter(list[AnchorFix])


def parse_pointer_response(text: str, limits: LlmTaskLimits) -> PointerResponse:
    """Tách object JSON `{"chunks": [...], "pairs": [...]}`; một phần tử sai dạng ⇒ cả phản hồi sai
    (`ProposalFormatError`, không kèm nội dung phản hồi)."""
    try:
        return _RESPONSE.validate_python(json_object(text, limits))
    except ValidationError:
        raise ProposalFormatError("object JSON không đúng dạng {chunks, pairs}") from None


def parse_anchor_fixes(text: str, limits: LlmTaskLimits) -> list[AnchorFix]:
    try:
        return _FIXES.validate_python(json_array(text, limits))
    except ValidationError:
        raise ProposalFormatError("mảng JSON không đúng dạng {index, def_start_words, def_end_words}") from None


# --------------------------------------------------------------------------- #
# Cắt trích dẫn theo mốc
# --------------------------------------------------------------------------- #


def _nfc_with_offsets(text: str) -> tuple[str, dict[int, int]]:
    """NFC của `text` theo từng cụm (ký tự gốc kèm dấu kết hợp phía sau) và bảng vị trí đầu cụm NFC → vị trí gốc.

    Chỉ vị trí nằm ở ranh giới cụm mới có trong bảng (kể cả vị trí cuối); khớp mốc không rơi đúng ranh giới thì
    không cắt được.
    """
    parts: list[str] = []
    offsets: dict[int, int] = {0: 0}
    length = 0
    cluster_start = 0
    for index in range(1, len(text) + 1):
        if index == len(text) or unicodedata.combining(text[index]) == 0:
            normalized = unicodedata.normalize("NFC", text[cluster_start:index])
            parts.append(normalized)
            length += len(normalized)
            offsets[length] = index
            cluster_start = index
    return "".join(parts), offsets


def _anchor_pattern(anchor: str) -> re.Pattern[str] | None:
    words = unicodedata.normalize("NFC", anchor).split()
    if not words:
        return None
    return re.compile(r"(?=(" + r"\s+".join(re.escape(word) for word in words) + r"))")


def _occurrences(pattern: re.Pattern[str], text: str, offsets: dict[int, int]) -> list[tuple[int, int]]:
    """Mọi lần xuất hiện (kể cả chồng nhau) của mốc, chỉ tính lần nằm đúng ranh giới cụm."""
    found: list[tuple[int, int]] = []
    for match in pattern.finditer(text):
        start, end = match.start(1), match.end(1)
        if start in offsets and end in offsets:
            found.append((start, end))
    return found


def cut_quote(window: str, start_words: str, end_words: str) -> tuple[int, int] | AnchorFailure:
    """Vị trí `[đầu, cuối)` trong `window` của trích dẫn theo hai mốc, hoặc lý do mốc hỏng."""
    normalized, offsets = _nfc_with_offsets(window)
    start_pattern = _anchor_pattern(start_words)
    end_pattern = _anchor_pattern(end_words)
    if start_pattern is None or end_pattern is None:
        return "anchor_not_found"
    starts = _occurrences(start_pattern, normalized, offsets)
    if not starts:
        return "anchor_not_found"
    if len(starts) > 1:
        return "anchor_ambiguous"
    [(begin, begin_end)] = starts
    ends = [(s, e) for s, e in _occurrences(end_pattern, normalized, offsets) if s >= begin and e >= begin_end]
    if not ends:
        return "anchor_not_found"
    if len(ends) > 1:
        return "anchor_ambiguous"
    return offsets[begin], offsets[ends[0][1]]


@dataclass
class _Cut:
    chunk_id: str
    term: str
    quote: str


@dataclass
class _Located:
    """Phần tử đã qua kiểm span và term, chờ cắt theo mốc."""

    chunk: KnowledgeChunk
    term: str
    window_start: int
    window_end: int
    def_span_ids: list[str]


def _locate(
    proposal: PointerProposal, spans: dict[str, Span], chunks: dict[str, KnowledgeChunk]
) -> _Located | ProposalRejectReason:
    known = [spans.get(span_id) for span_id in proposal.def_span_ids]
    term_span = spans.get(proposal.term_span_id)
    def_spans = [span for span in known if span is not None]
    if term_span is None or not proposal.def_span_ids or len(def_spans) != len(known):
        return "unknown_span"
    first = def_spans[0]
    expected = list(range(first.index, first.index + len(def_spans)))
    if [span.index for span in def_spans] != expected or any(span.chunk_id != first.chunk_id for span in def_spans):
        return "span_cross_chunk"
    if term_span.chunk_id != first.chunk_id:
        return "term_span_cross_chunk"
    chunk = chunks[first.chunk_id]
    term_line = unicodedata.normalize("NFC", chunk.raw_text[term_span.start : term_span.end])
    term_exact = unicodedata.normalize("NFC", proposal.term_exact)
    if not term_exact or term_exact not in term_line:
        return "term_not_in_span"
    term = normalize_quote(proposal.term_exact)
    if not term or len(term) > MAX_TERM_CHARS:
        return "invalid_term"
    return _Located(chunk, term, first.start, def_spans[-1].end, list(proposal.def_span_ids))


def _cut(located: _Located, start_words: str, end_words: str) -> _Cut | AnchorFailure:
    window = located.chunk.raw_text[located.window_start : located.window_end]
    outcome = cut_quote(window, start_words, end_words)
    if isinstance(outcome, str):
        return outcome
    begin, end = outcome
    quote = located.chunk.raw_text[located.window_start + begin : located.window_start + end]
    return _Cut(located.chunk.chunk_id, located.term, quote)


# --------------------------------------------------------------------------- #
# Nhãn bài tập
# --------------------------------------------------------------------------- #


def resolve_exercise_labels(response: PointerResponse, chunk_ids: Sequence[str]) -> dict[str, bool | None]:
    """Nhãn của từng chunk trong lô, chỉ từ `response.chunks`: `True`/`False` khi mọi nhãn LLM trả cho chunk đó cùng
    một giá trị; `None` khi không có nhãn nào, có nhãn null, hoặc nhãn xung đột. Nhãn của chunk ngoài lô bị bỏ."""
    votes: dict[str, list[bool | None]] = {chunk_id: [] for chunk_id in chunk_ids}
    for label in response.chunks:
        if label.chunk_id in votes:
            votes[label.chunk_id].append(label.is_exercise)
    resolved: dict[str, bool | None] = {}
    for chunk_id, values in votes.items():
        distinct = set(values)
        resolved[chunk_id] = values[0] if len(distinct) == 1 and None not in distinct else None
    return resolved


class ChunkLabelWriter(Protocol):
    """Phần của `VaultRepository` mà việc ghi nhãn bài tập dùng."""

    def get_chunk(self, chunk_id: str) -> KnowledgeChunk | None: ...

    def set_chunk_exercise(self, chunk_id: str, is_exercise: bool | None, expected_version: int) -> KnowledgeChunk: ...


def write_exercise_label(vault: ChunkLabelWriter, chunk_id: str, label: bool | None, retries: int) -> bool:
    """Ghi nhãn theo version (đọc – so – ghi); lệch version thì đọc lại và thử lại tối đa `retries` lần. Không đổi
    nhãn nào khác của chunk. Trả `True` khi nhãn đang lưu bằng `label` sau cùng."""
    for _ in range(retries + 1):
        chunk = vault.get_chunk(chunk_id)
        if chunk is None:
            return False
        if chunk.is_exercise is label:
            return True
        try:
            vault.set_chunk_exercise(chunk_id, label, chunk.version)
        except VersionConflictError:
            continue
        except RecordNotFoundError:
            return False
        return True
    return False


# --------------------------------------------------------------------------- #
# Gọi LLM trong ngân sách của lô
# --------------------------------------------------------------------------- #


class BatchFailure(Exception):
    def __init__(self, kind: BatchErrorKind) -> None:
        super().__init__(kind)
        self.kind: BatchErrorKind = kind


@dataclass
class _Budget:
    started: float
    json_retries_left: int
    limits: LlmTaskLimits
    monotonic: Callable[[], float]
    stats: ProposalStats = field(default_factory=ProposalStats)

    def expired(self, *, inclusive: bool) -> bool:
        elapsed = self.monotonic() - self.started
        return elapsed >= self.limits.deadline_s if inclusive else elapsed > self.limits.deadline_s


def _ask[T](
    client: ChatCompletionClient,
    messages: list[ChatMessage],
    parse: Callable[[str], T],
    retry: Callable[[str], list[ChatMessage]],
    budget: _Budget,
) -> tuple[T, str]:
    """Một lời gọi (cộng lời gọi lại khi JSON hỏng, trong ngân sách `json_retry_max` dùng chung của lô)."""
    stats = budget.stats
    while True:
        if budget.expired(inclusive=True):
            stats.deadline_exceeded = True
            raise BatchFailure("deadline")
        try:
            result = client.complete(messages, max_tokens=budget.limits.max_tokens)
        except LlmUpstreamError as exc:
            stats.llm_calls += 1
            stats.http_attempts += exc.attempts
            raise BatchFailure("llm_upstream") from None
        stats.llm_calls += 1
        stats.http_attempts += result.attempts
        if result.usage is not None:
            stats.prompt_tokens += result.usage.prompt_tokens
            stats.completion_tokens += result.usage.completion_tokens
        if budget.expired(inclusive=False):
            stats.deadline_exceeded = True
            raise BatchFailure("deadline")
        try:
            return parse(result.content), result.content
        except ProposalFormatError:
            stats.json_errors += 1
            if budget.json_retries_left <= 0:
                raise BatchFailure("format") from None
            budget.json_retries_left -= 1
            messages = retry(result.content)


# --------------------------------------------------------------------------- #
# Một lô
# --------------------------------------------------------------------------- #


class BatchOutcome(BaseModel):
    """Kết quả một lô: cặp đã lưu, số loại theo lý do, số chunk LLM gắn là bài tập, lỗi của lô (nếu có)."""

    pairs: list[ConceptPair] = Field(default_factory=list)
    rejected_by_reason: dict[ProposalRejectReason, int] = Field(default_factory=dict)
    exercise_chunks: int = 0
    error: BatchErrorKind | None = None
    stats: ProposalStats = Field(default_factory=ProposalStats)

    @property
    def rejected_count(self) -> int:
        return sum(self.rejected_by_reason.values())


def _count(reasons: dict[ProposalRejectReason, int], reason: ProposalRejectReason) -> None:
    reasons[reason] = reasons.get(reason, 0) + 1


def _span_payload(chunks: Sequence[KnowledgeChunk], spans: Sequence[Span]) -> list[SpanPayload]:
    by_chunk: dict[str, list[tuple[str, str]]] = {chunk.chunk_id: [] for chunk in chunks}
    texts = {chunk.chunk_id: chunk.raw_text for chunk in chunks}
    for span in spans:
        by_chunk[span.chunk_id].append((span.span_id, texts[span.chunk_id][span.start : span.end]))
    return [(chunk.chunk_id, by_chunk[chunk.chunk_id]) for chunk in chunks]


@dataclass
class _Pending:
    position: int
    located: _Located
    failure: AnchorFailure


def _resolve_anchors(
    client: ChatCompletionClient,
    first: list[ChatMessage],
    raw: str,
    pending: list[_Pending],
    results: list[_Cut | ProposalRejectReason | None],
    budget: _Budget,
) -> BatchErrorKind | None:
    """Xin mốc dài hơn MỘT lần cho mọi phần tử hỏng mốc; ghi kết quả vào `results`. Lỗi của lời gọi ⇒ trả loại lỗi,
    phần tử còn hỏng giữ lý do của lượt đầu."""
    failures: list[dict[str, object]] = [
        {
            "index": number,
            "def_span_ids": item.located.def_span_ids,
            "problem": "not_found" if item.failure == "anchor_not_found" else "ambiguous",
        }
        for number, item in enumerate(pending)
    ]
    for item in pending:
        results[item.position] = item.failure
    anchor_messages = build_anchor_retry_messages(first, raw, failures)
    try:
        fixes, _ = _ask(
            client,
            anchor_messages,
            lambda text: parse_anchor_fixes(text, budget.limits),
            lambda previous: build_anchor_format_retry_messages(anchor_messages, previous),
            budget,
        )
    except BatchFailure as failure:
        return failure.kind
    by_index = {fix.index: fix for fix in fixes}
    for number, item in enumerate(pending):
        fix = by_index.get(number)
        if fix is not None:
            results[item.position] = _cut(item.located, fix.def_start_words, fix.def_end_words)
    return None


def run_pointer_batch(
    vault: VaultRepository,
    client: ChatCompletionClient,
    *,
    chunks: Sequence[KnowledgeChunk],
    now: datetime,
    limits: LlmTaskLimits,
    monotonic: Callable[[], float],
    pages: PageSource | None,
    preselect_enabled: bool,
    label_retries: int,
) -> BatchOutcome:
    """Chạy một lô theo giao thức con trỏ và lưu cặp đạt (xem docstring module). Không ném lỗi LLM: lỗi của lô nằm ở
    `BatchOutcome.error`."""
    budget = _Budget(started=monotonic(), json_retries_left=limits.json_retry_max, limits=limits, monotonic=monotonic)
    if not chunks:
        return BatchOutcome(stats=budget.stats)
    spans = build_spans(chunks)
    by_span = {span.span_id: span for span in spans}
    by_chunk = {chunk.chunk_id: chunk for chunk in chunks}
    first = build_pointer_messages(_span_payload(chunks, spans))
    try:
        response, raw = _ask(
            client,
            first,
            lambda text: parse_pointer_response(text, limits),
            lambda previous: build_pointer_retry_messages(first, previous),
            budget,
        )
    except BatchFailure as failure:
        _log(budget.stats, failure.kind, 0, 0)
        return BatchOutcome(error=failure.kind, stats=budget.stats)

    # Nhãn bài tập được ghi TRƯỚC khi tính cổng các cặp của chunk.
    labels = resolve_exercise_labels(response, list(by_chunk))
    effective: dict[str, bool | None] = {
        chunk_id: label if write_exercise_label(vault, chunk_id, label, retries=label_retries) else None
        for chunk_id, label in labels.items()
    }
    exercise_chunks = sum(1 for label in labels.values() if label is True)

    reasons: dict[ProposalRejectReason, int] = {}
    cap = limits.max_proposals_per_chunk * len(chunks)
    considered = response.pairs[:cap]
    if len(response.pairs) > cap:
        reasons["proposal_limit"] = len(response.pairs) - cap

    results: list[_Cut | ProposalRejectReason | None] = []
    pending: list[_Pending] = []
    for position, proposal in enumerate(considered):
        located = _locate(proposal, by_span, by_chunk)
        if isinstance(located, str):
            results.append(located)
            continue
        cut = _cut(located, proposal.def_start_words, proposal.def_end_words)
        results.append(cut)
        if isinstance(cut, str):
            pending.append(_Pending(position, located, cut))
    error = _resolve_anchors(client, first, raw, pending, results, budget) if pending else None

    saved = _save(vault, results, by_chunk, now, page_lookup(pages), effective, preselect_enabled, reasons)
    _log(budget.stats, error or "ok", len(saved), sum(reasons.values()))
    return BatchOutcome(
        pairs=saved, rejected_by_reason=reasons, exercise_chunks=exercise_chunks, error=error, stats=budget.stats
    )


def _save(
    vault: VaultRepository,
    results: Sequence[_Cut | ProposalRejectReason | None],
    chunks: dict[str, KnowledgeChunk],
    now: datetime,
    lookup: PageLookup,
    labels: dict[str, bool | None],
    preselect_enabled: bool,
    reasons: dict[ProposalRejectReason, int],
) -> list[ConceptPair]:
    subject_codes = {chunk.subject_code for chunk in chunks.values()}
    seen = {
        pair_key(pair.chunk_id, pair.term, pair.definition_quote)
        for subject_code in subject_codes
        for pair in vault.list_concept_pairs(subject_code)
        if pair.chunk_id in chunks
    }
    saved_per_chunk: Counter[str] = Counter()
    annotate = gate_annotator(lookup, labels, preselect_enabled)
    saved: list[ConceptPair] = []
    for result in results:
        if result is None:
            continue
        if isinstance(result, str):
            _count(reasons, result)
            continue
        raw = RawProposal(term=result.term, definition_quote=result.quote, chunk_id=result.chunk_id)
        outcome = screen_proposal(raw, chunks, seen, saved_per_chunk, now, annotate)
        if isinstance(outcome, str):
            _count(reasons, outcome)
            continue
        try:
            saved.append(vault.add_concept_pair(outcome))  # repository kiểm lại I3 trong giao dịch ghi
        except MissingReferenceError:
            _count(reasons, "unknown_chunk")  # chunk bị xoá giữa chừng
        except QuoteMismatchError:
            _count(reasons, "quote_not_verbatim")
    return saved


def _log(stats: ProposalStats, outcome: str, proposed: int, rejected: int) -> None:
    logger.info(
        "llm_task=%s outcome=%s llm_calls=%d http_attempts=%d json_errors=%d deadline_exceeded=%s prompt_tokens=%d "
        "completion_tokens=%d proposed=%d rejected=%d",
        TASK_NAME,
        outcome,
        stats.llm_calls,
        stats.http_attempts,
        stats.json_errors,
        stats.deadline_exceeded,
        stats.prompt_tokens,
        stats.completion_tokens,
        proposed,
        rejected,
    )
