"""LLM đề xuất cặp khái niệm – trích dẫn định nghĩa từ chunk của Vault (ADR-007 mục 1, 3; spec §5.8, §6.6, §11.5).

Thứ tự bắt buộc: LLM đề xuất → code kiểm → lưu ở trạng thái `proposed` → người dùng xác nhận sau. LLM KHÔNG BAO GIỜ
xác nhận cặp: mọi cặp lưu ở đây có `status = proposed`, `proposed_by = "llm"`, `confirmed_at = None`; field `status`
hay bất kỳ field thừa nào LLM trả về đều bị bỏ qua.

- **Đầu vào:** 1–20 chunk, mọi chunk thuộc đúng `subject_code` của request (khác môn ⇒ `ChunkSubjectMismatchError`;
  không có chunk ⇒ `RecordNotFoundError`). `chunk_id` lặp chỉ tính một lần.
- **Parse:** tự tách mảng JSON khỏi phản hồi (chịu được rào ``` và văn bản thừa), validate bằng Pydantic; không dựa
  vào `response_format` của nhà cung cấp. Phản hồi không parse được thì thử lại tối đa `JSON_RETRY_MAX` lần, hết lượt
  thì `ConceptProposalError` (502 ở router).
- **Cổng kiểm từng đề xuất:** `term` không rỗng sau khi gộp khoảng trắng và tối đa 120 ký tự; `chunk_id` thuộc các
  chunk của request; `definition_quote` là chuỗi con của `raw_text` của đúng chunk đó sau NFC và gộp khoảng trắng
  (I3), dài 8–600 ký tự. Đề xuất trùng (cùng chunk, cùng `term` và trích dẫn sau chuẩn hoá) với một đề xuất trước đó
  trong cùng phản hồi hoặc với một cặp đã có của chunk (mọi trạng thái, kể cả bị từ chối) không được lưu lại. Đề xuất
  không đạt hoặc trùng chỉ được đếm vào `rejected_count`; không sửa hộ.
- **Quan sát (§11.5, §14):** đếm số lượt gọi LLM, số lần thử HTTP, số phản hồi JSON lỗi và token; ghi log một dòng
  chỉ gồm các số đếm, không ghi prompt, phản hồi, khoá hay địa chỉ endpoint.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Sequence
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from ..domain.llm import ChatCompletionClient, ChatCompletionResult, LlmUpstreamError
from ..domain.repositories import RecordNotFoundError, VaultRepository
from ..domain.schemas import KnowledgeChunk, normalize_quote, quote_in_text
from ..domain.vault import ConceptPair, ConceptPairStatus
from .concept_proposal_prompt import build_messages, build_retry_messages

MIN_PROPOSAL_CHUNKS = 1
MAX_PROPOSAL_CHUNKS = 20
JSON_RETRY_MAX = 1
DEFAULT_MAX_TOKENS = 4096
MAX_TERM_CHARS = 120
TASK_NAME = "concept_pair_proposal"

RejectReason = Literal["quote_not_verbatim", "unknown_chunk", "invalid_term", "invalid_quote", "duplicate"]

_FENCE_RE = re.compile(r"```[A-Za-z0-9_-]*[ \t]*\n?(.*?)```", re.DOTALL)

logger = logging.getLogger(__name__)


class ChunkSubjectMismatchError(ValueError):
    """Một chunk của request không thuộc `subject_code` của request (422 ở router)."""

    def __init__(self, chunk_id: str, subject_code: str) -> None:
        super().__init__(f"chunk {chunk_id} không thuộc môn {subject_code}")
        self.chunk_id = chunk_id


class ProposalFormatError(ValueError):
    """Phản hồi của LLM không chứa mảng JSON đề xuất hợp lệ."""


class ProposalStats(BaseModel):
    """Số đếm của một lần đề xuất, để log và để script đánh giá ghi lại; không chứa nội dung nào."""

    llm_calls: int = 0
    http_attempts: int = 0
    json_errors: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def json_error_rate(self) -> float:
        return self.json_errors / self.llm_calls if self.llm_calls else 0.0


class ConceptProposalError(LlmUpstreamError):
    """Đề xuất thất bại: LLM gọi lỗi, hoặc phản hồi không parse được sau mọi lần thử. Kèm số đếm đã dùng."""

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


def _json_arrays(text: str) -> list[Any]:
    """Mảng JSON đầu tiên tìm được: trong các khối rào ``` trước, rồi trong toàn bộ văn bản."""
    decoder = json.JSONDecoder()
    candidates = [match.group(1) for match in _FENCE_RE.finditer(text)] + [text]
    for candidate in candidates:
        for start, char in enumerate(candidate):
            if char != "[":
                continue
            try:
                value, _ = decoder.raw_decode(candidate, start)
            except json.JSONDecodeError:
                continue
            if isinstance(value, list):
                return value
    raise ProposalFormatError("phản hồi không chứa mảng JSON")


def parse_proposals(text: str) -> list[RawProposal]:
    """Tách và validate mảng đề xuất; sai ⇒ `ProposalFormatError` (thông điệp không chứa nội dung phản hồi)."""
    array = _json_arrays(text)
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


def _pair_key(chunk_id: str, term: str, quote: str) -> tuple[str, str, str]:
    return chunk_id, normalize_quote(term), normalize_quote(quote)


def _gate(
    proposal: RawProposal, chunks: dict[str, KnowledgeChunk], seen: set[tuple[str, str, str]], now: datetime
) -> ConceptPair | RejectReason:
    """Cặp `proposed` khi đề xuất qua cổng kiểm và chưa trùng; ngược lại lý do bị loại."""
    term = normalize_quote(proposal.term)
    chunk = chunks.get(proposal.chunk_id)
    if chunk is None:
        return "unknown_chunk"
    if not term or len(term) > MAX_TERM_CHARS:
        return "invalid_term"
    if not quote_in_text(proposal.definition_quote, chunk.raw_text):  # I3
        return "quote_not_verbatim"
    key = _pair_key(chunk.chunk_id, term, proposal.definition_quote)
    if key in seen:
        return "duplicate"
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
        )
    except ValidationError:
        return "invalid_quote"
    seen.add(key)
    return pair


def _ask(
    client: ChatCompletionClient, chunks: Sequence[KnowledgeChunk], stats: ProposalStats, max_tokens: int, retries: int
) -> list[RawProposal]:
    messages = build_messages(chunks)
    for _ in range(retries + 1):
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
        try:
            return parse_proposals(result.content)
        except ProposalFormatError:
            stats.json_errors += 1
            messages = build_retry_messages(build_messages(chunks), result.content)
    raise ConceptProposalError(f"LLM không trả JSON hợp lệ sau {retries + 1} lần gọi", stats)


def _log(stats: ProposalStats, outcome: str, proposed: int = 0, rejected: int = 0, quote_not_verbatim: int = 0) -> None:
    logger.info(
        "llm_task=%s outcome=%s llm_calls=%d http_attempts=%d json_errors=%d prompt_tokens=%d "
        "completion_tokens=%d proposed=%d rejected=%d rejected_quote_not_verbatim=%d",
        TASK_NAME,
        outcome,
        stats.llm_calls,
        stats.http_attempts,
        stats.json_errors,
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
    max_tokens: int = DEFAULT_MAX_TOKENS,
    json_retry_max: int = JSON_RETRY_MAX,
) -> ConceptProposalResult:
    """Gọi LLM một lần (cộng tối đa `json_retry_max` lần thử lại khi JSON lỗi), kiểm từng đề xuất, lưu cặp đạt ở
    trạng thái `proposed`. Trả cặp đã lưu theo thứ tự LLM đề xuất, số đề xuất bị loại và số đếm."""
    chunks = _load_chunks(vault, subject_code, chunk_ids)
    stats = ProposalStats()
    try:
        proposals = _ask(client, chunks, stats, max_tokens, json_retry_max)
    except ConceptProposalError:
        _log(stats, "failed")
        raise
    by_id = {chunk.chunk_id: chunk for chunk in chunks}
    seen = {
        _pair_key(pair.chunk_id, pair.term, pair.definition_quote)
        for pair in vault.list_concept_pairs(subject_code)
        if pair.chunk_id in by_id
    }
    saved: list[ConceptPair] = []
    reasons: dict[RejectReason, int] = {}
    for proposal in proposals:
        outcome = _gate(proposal, by_id, seen, now)
        if isinstance(outcome, ConceptPair):
            saved.append(vault.add_concept_pair(outcome))  # repository kiểm lại I3 trong giao dịch ghi
        else:
            reasons[outcome] = reasons.get(outcome, 0) + 1
    rejected = len(proposals) - len(saved)
    _log(stats, "ok", len(saved), rejected, reasons.get("quote_not_verbatim", 0))
    return ConceptProposalResult(pairs=saved, rejected_count=rejected, rejected_by_reason=reasons, stats=stats)
