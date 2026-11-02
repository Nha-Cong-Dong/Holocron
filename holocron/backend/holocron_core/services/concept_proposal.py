"""LLM đề xuất cặp khái niệm – trích dẫn định nghĩa từ chunk của Vault (ADR-007 mục 1, 3; spec §5.8, §6.6, §11.5).

Thứ tự bắt buộc: LLM đề xuất → code kiểm → lưu ở trạng thái `proposed` → người dùng xác nhận sau. LLM KHÔNG BAO GIỜ
xác nhận cặp: mọi cặp lưu ở đây có `status = proposed`, `proposed_by = "llm"`, `confirmed_at = None`; field `status`
hay bất kỳ field thừa nào LLM trả về đều bị bỏ qua.

- **Đầu vào:** 1–20 chunk, mọi chunk thuộc đúng `subject_code` của request (khác môn ⇒ `ChunkSubjectMismatchError`;
  không có chunk ⇒ `RecordNotFoundError`). `chunk_id` lặp chỉ tính một lần.
- **Parse:** tự tách mảng JSON khỏi phản hồi (chịu được rào ``` và văn bản thừa), validate bằng Pydantic; không dựa
  vào `response_format` của nhà cung cấp. Phản hồi dài quá `LlmTaskLimits.max_response_chars` hoặc lồng ngoặc sâu quá
  `LlmTaskLimits.max_json_depth` (đếm ngoài chuỗi JSON, trước khi gọi bộ parse) bị coi như JSON hỏng và không được
  đưa vào bộ parse; `RecursionError` của bộ parse cũng vậy. Số vị trí `[` được thử parse bị chặn ở
  `LlmTaskLimits.max_parse_starts` để thời gian parse một phản hồi có cận. Phản hồi không parse được thì gọi lại tối đa
  `LlmTaskLimits.json_retry_max` lần, hết lượt thì `ConceptProposalError` (502 ở router).
- **Hạn thời gian tổng:** cả lần đề xuất, gồm mọi lần gọi lại, phải xong trong `LlmTaskLimits.deadline_s`, đo bằng
  đồng hồ đơn điệu truyền vào (test dùng đồng hồ giả). Hạn được kiểm trước mỗi lần gọi và ngay khi lần gọi trả về; quá
  hạn thì `ConceptProposalError` (502) và kết quả về muộn bị bỏ. Lần gọi đang chạy bị chặn ở tầng HTTP: client do
  router dựng (`OpenAiChatClient.from_settings`) nhận cùng `deadline_s` làm cận tổng, tính từ lúc dựng (trước lúc
  service bắt đầu đo), và chỉ cho mỗi lần thử HTTP phần thời gian còn lại; server gửi nhỏ giọt không giữ được request
  quá hạn.
- **Trần số đề xuất:** mảng LLM trả về chỉ được xét tối đa `LlmTaskLimits.max_proposals_per_chunk × số chunk` phần tử
  đầu tiên, trước khi chạy kiểm nguyên văn; phần thừa bị bỏ và đếm vào `rejected_count` (lý do `proposal_limit`).
- **Cổng kiểm từng đề xuất:** `term` không rỗng sau khi gộp khoảng trắng và tối đa `MAX_TERM_CHARS` ký tự (đọc từ
  ràng buộc của `ConceptPair.term`); `chunk_id` thuộc các chunk của request; `definition_quote` là chuỗi con của
  `raw_text` của đúng chunk đó sau NFC và gộp khoảng trắng (I3), dài trong giới hạn của `ConceptPair.definition_quote`
  (8–600 ký tự). Đề xuất trùng (cùng chunk, cùng `term` và trích dẫn sau chuẩn hoá) với một đề xuất trước đó
  trong cùng phản hồi hoặc với một cặp đã có của chunk (mọi trạng thái, kể cả bị từ chối) không được lưu lại. Mỗi
  chunk lưu tối đa `MAX_PAIRS_PER_CHUNK` cặp trong một lần đề xuất (đúng giới hạn nêu trong prompt); đề xuất đạt
  nhưng vượt số đó bị loại (`chunk_limit`). Đề xuất không đạt, trùng hay vượt số chỉ được đếm vào `rejected_count`;
  không sửa hộ.
- **Cổng tích sẵn (§5.8, ADR-007 mục 3; thêm 2026-10-31):** cặp lưu ở đây nhận `failed_gates` theo luật tạm
  `preselect_page_flags_v1` (`concept_proposal_gates.py`) trên các trang nguồn đọc qua `pages`, và
  `pre_selected = true` chỉ khi luật đang bật và không cổng nào trượt. Giao thức cũ không có nhãn bài tập nên cổng
  `EXERCISE` dùng `is_exercise` đang lưu của chunk (`None` ⇒ trượt). Tính một lần lúc lưu.
- **Quan sát (§11.5, §14):** đếm số lượt gọi LLM, số lần thử HTTP, số phản hồi JSON lỗi, việc hết hạn và token; ghi log
  một dòng chỉ gồm các số đếm, không ghi prompt, phản hồi, khoá hay địa chỉ endpoint.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections import Counter
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from ..domain.llm import ChatCompletionClient, ChatCompletionResult, LlmUpstreamError
from ..domain.repositories import RecordNotFoundError, VaultRepository
from ..domain.schemas import KnowledgeChunk, normalize_quote, quote_in_text
from ..domain.vault import ConceptPair, ConceptPairStatus, PairGate
from ..infrastructure.llm_config import LlmTaskLimits
from .concept_proposal_gates import PageLookup, PageSource, failed_gates, page_lookup, pre_selected
from .concept_proposal_prompt import MAX_PAIRS_PER_CHUNK, build_messages, build_retry_messages


def _field_max_length(model: type[BaseModel], field: str) -> int:
    """Giới hạn `max_length` khai báo trên một trường của model, để không chép cứng con số của `domain`."""
    for constraint in model.model_fields[field].metadata:
        limit = getattr(constraint, "max_length", None)
        if isinstance(limit, int):
            return limit
    raise LookupError(f"{model.__name__}.{field} không khai báo max_length")


MIN_PROPOSAL_CHUNKS = 1
MAX_PROPOSAL_CHUNKS = 20
TASK_NAME = "concept_pair_proposal"
DEFAULT_LIMITS = LlmTaskLimits()
DEFAULT_MAX_TOKENS = DEFAULT_LIMITS.max_tokens
"""Mặc định của `LlmTaskLimits.max_tokens`; giữ tên cho script đánh giá."""
MAX_TERM_CHARS = _field_max_length(ConceptPair, "term")
"""Độ dài tối đa của `term`, đọc từ ràng buộc của `ConceptPair`."""

RejectReason = Literal[
    "quote_not_verbatim",
    "unknown_chunk",
    "invalid_term",
    "invalid_quote",
    "duplicate",
    "chunk_limit",
    "proposal_limit",
]

_FENCE_RE = re.compile(r"```[A-Za-z0-9_-]*[ \t]*\n?(.*?)```", re.DOTALL)

logger = logging.getLogger(__name__)


class ChunkSubjectMismatchError(ValueError):
    """Một chunk của request không thuộc `subject_code` của request (422 ở router)."""

    def __init__(self, chunk_id: str, subject_code: str) -> None:
        super().__init__(f"chunk {chunk_id} không thuộc môn {subject_code}")
        self.chunk_id = chunk_id


class ProposalFormatError(ValueError):
    """Phản hồi của LLM không chứa mảng JSON đề xuất hợp lệ (kể cả khi quá dài hoặc lồng quá sâu)."""


class ProposalStats(BaseModel):
    """Số đếm của một lần đề xuất, để log và để script đánh giá ghi lại; không chứa nội dung nào."""

    llm_calls: int = 0
    http_attempts: int = 0
    json_errors: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    deadline_exceeded: bool = False

    @property
    def json_error_rate(self) -> float:
        return self.json_errors / self.llm_calls if self.llm_calls else 0.0


class ConceptProposalError(LlmUpstreamError):
    """Đề xuất thất bại: LLM gọi lỗi, hết hạn thời gian tổng, hoặc phản hồi không parse được sau mọi lần thử."""

    def __init__(self, message: str, stats: ProposalStats) -> None:
        super().__init__(message, attempts=stats.http_attempts)
        self.stats = stats


class RawProposal(BaseModel):
    """Một phần tử LLM trả về. Field thừa (kể cả `status`) bị bỏ qua; kiểu sai làm cả phản hồi không hợp lệ."""

    term: str
    definition_quote: str
    chunk_id: str


class ConceptProposalResult(BaseModel):
    pairs: list[ConceptPair]
    rejected_count: int = Field(ge=0)
    rejected_by_reason: dict[RejectReason, int]
    stats: ProposalStats


_PROPOSALS = TypeAdapter(list[RawProposal])


def json_depth_exceeds(text: str, max_depth: int) -> bool:
    """`True` khi độ lồng `[`/`{` (đếm ngoài chuỗi JSON; ngoặc đóng thừa không làm âm) vượt `max_depth`.

    Quét một lượt, không đệ quy, nên an toàn với văn bản lồng sâu tuỳ ý.
    """
    depth = 0
    in_string = False
    escaped = False
    for char in text:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char in "[{":
            depth += 1
            if depth > max_depth:
                return True
        elif char in "]}":
            depth = max(depth - 1, 0)
    return False


def _first_json(text: str, limits: LlmTaskLimits, opener: str) -> Any:
    """Giá trị JSON đầu tiên bắt đầu bằng `opener` (`[` hoặc `{`) parse được thành đúng kiểu đó: trong các khối rào
    ``` trước, rồi trong toàn bộ văn bản."""
    if len(text) > limits.max_response_chars:
        raise ProposalFormatError("phản hồi dài quá giới hạn")
    expected: tuple[type, ...] = (list,) if opener == "[" else (dict,)
    decoder = json.JSONDecoder()
    candidates = [match.group(1) for match in _FENCE_RE.finditer(text)] + [text]
    starts_tried = 0
    for candidate in candidates:
        if json_depth_exceeds(candidate, limits.max_json_depth):
            raise ProposalFormatError("phản hồi lồng ngoặc quá sâu")
        for start, char in enumerate(candidate):
            if char != opener:
                continue
            starts_tried += 1
            if starts_tried > limits.max_parse_starts:
                raise ProposalFormatError("phản hồi có quá nhiều chỗ bắt đầu JSON mà không chỗ nào hợp lệ")
            try:
                value, _ = decoder.raw_decode(candidate, start)
            except json.JSONDecodeError:
                continue
            except RecursionError:
                # Ngoặc nằm trong một chuỗi mà phép đếm ở trên bỏ qua vẫn có thể làm bộ parse đệ quy quá sâu.
                raise ProposalFormatError("phản hồi lồng ngoặc quá sâu") from None
            if isinstance(value, expected):
                return value
    raise ProposalFormatError("phản hồi không chứa JSON đúng dạng")


def json_array(text: str, limits: LlmTaskLimits) -> list[Any]:
    """Mảng JSON đầu tiên tìm được: trong các khối rào ``` trước, rồi trong toàn bộ văn bản."""
    value: list[Any] = _first_json(text, limits, "[")
    return value


def json_object(text: str, limits: LlmTaskLimits) -> dict[str, Any]:
    """Object JSON đầu tiên tìm được (cùng cách tìm và cùng giới hạn như `json_array`)."""
    value: dict[str, Any] = _first_json(text, limits, "{")
    return value


def parse_proposals(text: str, limits: LlmTaskLimits = DEFAULT_LIMITS) -> list[RawProposal]:
    """Tách và validate mảng đề xuất; sai, quá dài hay quá sâu ⇒ `ProposalFormatError` (không kèm nội dung phản hồi)."""
    array = json_array(text, limits)
    try:
        return _PROPOSALS.validate_python(array)
    except ValidationError:
        raise ProposalFormatError("mảng JSON không đúng dạng {term, definition_quote, chunk_id}") from None


def _load_chunks(vault: VaultRepository, subject_code: str, chunk_ids: Sequence[str]) -> list[KnowledgeChunk]:
    chunks: list[KnowledgeChunk] = []
    for chunk_id in dict.fromkeys(chunk_ids):
        chunk = vault.get_chunk(chunk_id)
        if chunk is None:
            raise RecordNotFoundError("chunk", chunk_id)
        if chunk.subject_code != subject_code:
            raise ChunkSubjectMismatchError(chunk_id, subject_code)
        chunks.append(chunk)
    if not MIN_PROPOSAL_CHUNKS <= len(chunks) <= MAX_PROPOSAL_CHUNKS:
        raise ValueError(f"cần từ {MIN_PROPOSAL_CHUNKS} đến {MAX_PROPOSAL_CHUNKS} chunk")
    return chunks


def pair_key(chunk_id: str, term: str, quote: str) -> tuple[str, str, str]:
    return chunk_id, normalize_quote(term), normalize_quote(quote)


GateAnnotator = Callable[[KnowledgeChunk, str], tuple[list[PairGate], bool]]
"""`(chunk, definition_quote) -> (failed_gates, pre_selected)` cho một cặp sắp lưu."""


def gate_annotator(lookup: PageLookup, exercise_labels: dict[str, bool | None], enabled: bool) -> GateAnnotator:
    """Tính cổng tích sẵn theo `preselect_page_flags_v1`; `exercise_labels` là nhãn đã xác định của từng chunk
    (thiếu khoá ⇒ chưa xác định ⇒ `EXERCISE` trượt)."""

    def annotate(chunk: KnowledgeChunk, quote: str) -> tuple[list[PairGate], bool]:
        gates = failed_gates(chunk, quote, exercise_labels.get(chunk.chunk_id), lookup)
        return gates, pre_selected(gates, enabled)

    return annotate


def screen_proposal(
    proposal: RawProposal,
    chunks: dict[str, KnowledgeChunk],
    seen: set[tuple[str, str, str]],
    saved_per_chunk: Counter[str],
    now: datetime,
    annotate: GateAnnotator,
) -> ConceptPair | RejectReason:
    """Cặp `proposed` khi đề xuất qua cổng kiểm, chưa trùng và chunk chưa đủ `MAX_PAIRS_PER_CHUNK` cặp; ngược lại lý
    do bị loại. Cặp trả về được tính ngay vào `seen` và `saved_per_chunk`, và mang `failed_gates`/`pre_selected` do
    `annotate` tính."""
    term = normalize_quote(proposal.term)
    chunk = chunks.get(proposal.chunk_id)
    if chunk is None:
        return "unknown_chunk"
    if not term or len(term) > MAX_TERM_CHARS:
        return "invalid_term"
    if not quote_in_text(proposal.definition_quote, chunk.raw_text):  # I3
        return "quote_not_verbatim"
    key = pair_key(chunk.chunk_id, term, proposal.definition_quote)
    if key in seen:
        return "duplicate"
    gates, selected = annotate(chunk, proposal.definition_quote)
    try:
        pair = ConceptPair(
            chunk_id=chunk.chunk_id,
            subject_code=chunk.subject_code,
            chapter_code=chunk.chapter_code,
            term=term,
            definition_quote=proposal.definition_quote,
            status=ConceptPairStatus.PROPOSED,
            proposed_by="llm",
            created_at=now,
            failed_gates=gates,
            pre_selected=selected,
        )
    except ValidationError:
        return "invalid_quote"
    if saved_per_chunk[chunk.chunk_id] >= MAX_PAIRS_PER_CHUNK:
        return "chunk_limit"
    seen.add(key)
    saved_per_chunk[chunk.chunk_id] += 1
    return pair


def _deadline_error(stats: ProposalStats, limits: LlmTaskLimits) -> ConceptProposalError:
    stats.deadline_exceeded = True
    return ConceptProposalError(f"hết hạn thời gian tổng {limits.deadline_s:g} s của lần đề xuất", stats)


def _ask(
    client: ChatCompletionClient,
    chunks: Sequence[KnowledgeChunk],
    stats: ProposalStats,
    max_tokens: int,
    limits: LlmTaskLimits,
    monotonic: Callable[[], float],
) -> list[RawProposal]:
    started = monotonic()
    messages = build_messages(chunks)
    for _ in range(limits.json_retry_max + 1):
        if monotonic() - started >= limits.deadline_s:
            raise _deadline_error(stats, limits)
        try:
            result: ChatCompletionResult = client.complete(messages, max_tokens=max_tokens)
        except LlmUpstreamError as exc:
            stats.llm_calls += 1
            stats.http_attempts += exc.attempts
            raise ConceptProposalError(f"gọi LLM thất bại: {exc}", stats) from None
        stats.llm_calls += 1
        stats.http_attempts += result.attempts
        if result.usage is not None:
            stats.prompt_tokens += result.usage.prompt_tokens
            stats.completion_tokens += result.usage.completion_tokens
        if monotonic() - started > limits.deadline_s:
            raise _deadline_error(stats, limits)
        try:
            return parse_proposals(result.content, limits)
        except ProposalFormatError:
            stats.json_errors += 1
            messages = build_retry_messages(build_messages(chunks), result.content)
    raise ConceptProposalError(f"LLM không trả JSON hợp lệ sau {limits.json_retry_max + 1} lần gọi", stats)


def _log(stats: ProposalStats, outcome: str, proposed: int = 0, rejected: int = 0, quote_not_verbatim: int = 0) -> None:
    logger.info(
        "llm_task=%s outcome=%s llm_calls=%d http_attempts=%d json_errors=%d deadline_exceeded=%s prompt_tokens=%d "
        "completion_tokens=%d proposed=%d rejected=%d rejected_quote_not_verbatim=%d",
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
        quote_not_verbatim,
    )


def propose_concept_pairs(
    vault: VaultRepository,
    client: ChatCompletionClient,
    *,
    subject_code: str,
    chunk_ids: Sequence[str],
    now: datetime,
    max_tokens: int | None = None,
    limits: LlmTaskLimits = DEFAULT_LIMITS,
    monotonic: Callable[[], float] = time.monotonic,
    pages: PageSource | None = None,
    preselect_enabled: bool = False,
) -> ConceptProposalResult:
    """Gọi LLM một lần (cộng tối đa `limits.json_retry_max` lần gọi lại khi JSON lỗi, trong hạn `limits.deadline_s`),
    kiểm từng đề xuất, lưu cặp đạt ở trạng thái `proposed`. Trả cặp đã lưu theo thứ tự LLM đề xuất, số đề xuất bị loại
    và số đếm. `max_tokens` mặc định là `limits.max_tokens`. `monotonic` là đồng hồ đơn điệu (giây) dùng để đo hạn;
    test truyền đồng hồ giả. `pages` đọc trang nguồn cho cổng tích sẵn (`None` ⇒ mọi cổng xét theo trang trượt);
    `preselect_enabled` là `PAIR_PRESELECT_ENABLED`."""
    chunks = _load_chunks(vault, subject_code, chunk_ids)
    stats = ProposalStats()
    try:
        answered = _ask(
            client, chunks, stats, limits.max_tokens if max_tokens is None else max_tokens, limits, monotonic
        )
    except ConceptProposalError:
        _log(stats, "failed")
        raise
    # Trần số phần tử được xét, áp dụng trước mọi phép kiểm (kể cả kiểm nguyên văn I3) để công việc có cận.
    proposals = answered[: limits.max_proposals_per_chunk * len(chunks)]
    by_id = {chunk.chunk_id: chunk for chunk in chunks}
    seen = {
        pair_key(pair.chunk_id, pair.term, pair.definition_quote)
        for pair in vault.list_concept_pairs(subject_code)
        if pair.chunk_id in by_id
    }
    saved: list[ConceptPair] = []
    saved_per_chunk: Counter[str] = Counter()
    reasons: dict[RejectReason, int] = {}
    if len(answered) > len(proposals):
        reasons["proposal_limit"] = len(answered) - len(proposals)
    annotate = gate_annotator(
        page_lookup(pages), {chunk.chunk_id: chunk.is_exercise for chunk in chunks}, preselect_enabled
    )
    for proposal in proposals:
        outcome = screen_proposal(proposal, by_id, seen, saved_per_chunk, now, annotate)
        if isinstance(outcome, ConceptPair):
            saved.append(vault.add_concept_pair(outcome))  # repository kiểm lại I3 trong giao dịch ghi
        else:
            reasons[outcome] = reasons.get(outcome, 0) + 1
    rejected = len(answered) - len(saved)
    _log(stats, "ok", len(saved), rejected, reasons.get("quote_not_verbatim", 0))
    return ConceptProposalResult(pairs=saved, rejected_count=rejected, rejected_by_reason=reasons, stats=stats)
