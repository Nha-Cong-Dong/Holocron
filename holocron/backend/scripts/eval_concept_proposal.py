"""Đánh giá opt-in: LLM THẬT đề xuất cặp khái niệm cho vài chunk mẫu (CLAUDE.md §2.2; spec v0.6 §11.5).

KHÔNG chạy trong pytest, CI hay pre-commit. Đọc cấu hình LLM từ biến môi trường `HOLOCRON_LLM_*` (nạp vào shell
trước; script không đọc file `.env` và không in giá trị biến nào). Kết quả và cache ghi vào `--output-dir`, thư mục
này PHẢI nằm ngoài repo.

Chạy trong `holocron/backend`:

    python -m scripts.eval_concept_proposal scripts/fixtures/concept_chunks_sample.json \\
        --output-dir <thư mục ngoài repo> --price-input-per-mtok <USD> --price-output-per-mtok <USD> \\
        [--max-calls 2] [--max-tokens 4096] [--dry-run]

- In CẬN TRÊN chi phí TRƯỚC khi gọi: số lượt gọi mới tối đa × (cận trên token vào của lượt đắt nhất × giá đầu vào
  + `--max-tokens` × giá đầu ra). Cận trên token vào = số byte UTF-8 của mọi tin nhắn (bộ tách từ BPE theo byte cho
  mỗi token ít nhất 1 byte) cộng `MESSAGE_OVERHEAD_TOKENS` mỗi tin nhắn; lượt đắt nhất là lượt gọi lại, gồm thêm phản
  hồi trước (tối đa `MAX_ECHOED_RESPONSE_CHARS` ký tự, mỗi ký tự tối đa 4 byte) và lời nhắc `RETRY_NOTE`. Số lượt gọi
  mới tối đa = `min(--max-calls, 1 + json_retry_max)`, bớt 1 khi lượt đầu đã có trong cache; mỗi lượt gọi có thể gửi
  tới `1 + HTTP_RETRY_MAX` request HTTP (thử lại sau timeout hay 5xx), và request bị timeout phía client vẫn có thể bị
  tính tiền, nên số request tính tiền tối đa = số lượt × `(1 + HTTP_RETRY_MAX)`. Làm tròn lên tới 1e-6 USD.
  `--dry-run` chỉ in ước tính rồi dừng, không cần khoá.
- Cache theo sha256 của `(model, messages, max_tokens)` trong `<output-dir>/cache/`: cùng đầu vào không gọi lại.
- `--max-calls`: số lượt gọi LLM mới (không tính lượt lấy từ cache) tối đa trong một lần chạy.
- File JSON chunk: như `ConfirmedDocumentText` nhưng không có `doc_id` (script tự suy từ sha256 của file); khoá bắt
  đầu bằng `_` bị bỏ qua.
- Kết quả: số đề xuất được lưu, số bị loại (tổng và theo lý do, gồm `quote_not_verbatim` của I3), tỉ lệ JSON lỗi,
  `usage` và chi phí theo `usage`. Không ghi khoá, địa chỉ endpoint hay prompt.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from holocron_core.domain.ingestion import UploadedDocument
from holocron_core.domain.llm import ChatCompletionClient, ChatCompletionResult, ChatMessage, LlmUpstreamError
from holocron_core.domain.schemas import KnowledgeChunk
from holocron_core.domain.vault import ConfirmedDocumentText
from holocron_core.infrastructure.llm_client import HTTP_RETRY_MAX, OpenAiChatClient
from holocron_core.infrastructure.llm_config import LlmTaskLimits, RuntimeConfig, load_runtime_config
from holocron_core.infrastructure.memory_store import IngestionStore
from holocron_core.services.concept_proposal import (
    DEFAULT_MAX_TOKENS,
    ConceptProposalError,
    ConceptProposalResult,
    ProposalStats,
    propose_concept_pairs,
)
from holocron_core.services.concept_proposal_prompt import (
    MAX_ECHOED_RESPONSE_CHARS,
    PROMPT_VERSION,
    RETRY_NOTE,
    build_messages,
)
from holocron_core.services.vault import ingest_confirmed_text

REPO_ROOT = Path(__file__).resolve().parents[3]
MESSAGE_OVERHEAD_TOKENS = 16
MAX_UTF8_BYTES_PER_CHAR = 4
EXIT_USAGE = 2


def input_tokens_upper_bound(messages: Sequence[ChatMessage]) -> int:
    """Cận trên token đầu vào: số byte UTF-8 của nội dung (mỗi token BPE theo byte phủ ít nhất 1 byte) cộng phần
    đầu của mỗi tin nhắn."""
    return sum(len(message.content.encode("utf-8")) + MESSAGE_OVERHEAD_TOKENS for message in messages)


def worst_call_input_tokens(messages: Sequence[ChatMessage]) -> int:
    """Cận trên token đầu vào của lượt đắt nhất: lượt gọi lại, gồm tin nhắn của lượt đầu, phản hồi trước (đã cắt còn
    tối đa `MAX_ECHOED_RESPONSE_CHARS` ký tự) và `RETRY_NOTE`."""
    echoed = MAX_ECHOED_RESPONSE_CHARS * MAX_UTF8_BYTES_PER_CHAR + MESSAGE_OVERHEAD_TOKENS
    note = len(RETRY_NOTE.encode("utf-8")) + MESSAGE_OVERHEAD_TOKENS
    return input_tokens_upper_bound(messages) + echoed + note


def max_new_calls(max_calls: int, json_retry_max: int, *, first_call_cached: bool) -> int:
    """Số lượt gọi LLM mới tối đa của một lần đề xuất: lượt đầu cộng `json_retry_max` lượt gọi lại, trừ lượt đầu khi
    đã có trong cache, không quá `--max-calls`."""
    return max(0, min(max_calls, 1 + json_retry_max - (1 if first_call_cached else 0)))


def cost_upper_bound_usd(
    *, calls: int, input_tokens: int, max_tokens: int, price_in_per_mtok: float, price_out_per_mtok: float
) -> float:
    """`calls × (input_tokens × giá vào + max_tokens × giá ra)`, làm tròn LÊN tới 1e-6 USD."""
    exact = calls * (input_tokens * price_in_per_mtok + max_tokens * price_out_per_mtok) / 1_000_000
    return math.ceil(exact * 1_000_000) / 1_000_000


def request_key(model: str, messages: Sequence[ChatMessage], max_tokens: int) -> str:
    payload = {"model": model, "messages": [m.model_dump() for m in messages], "max_tokens": max_tokens}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass
class CachingClient:
    """`ChatCompletionClient` có cache theo hash đầu vào và giới hạn số lượt gọi mới; client thật tạo khi cần."""

    connect: Callable[[], ChatCompletionClient]
    cache_dir: Path
    model: str
    max_calls: int
    new_calls: int = 0
    cache_hits: int = 0
    _inner: ChatCompletionClient | None = field(default=None, repr=False)

    def cached(self, key: str) -> Path:
        return self.cache_dir / f"{key}.json"

    def complete(self, messages: Sequence[ChatMessage], *, max_tokens: int) -> ChatCompletionResult:
        path = self.cached(request_key(self.model, messages, max_tokens))
        if path.exists():
            self.cache_hits += 1
            return ChatCompletionResult.model_validate_json(path.read_text(encoding="utf-8"))
        if self.new_calls >= self.max_calls:
            raise LlmUpstreamError("đã dùng hết --max-calls; tăng giới hạn nếu muốn gọi thêm", attempts=0)
        if self._inner is None:
            self._inner = self.connect()
        self.new_calls += 1
        result = self._inner.complete(messages, max_tokens=max_tokens)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(result.model_dump_json(indent=1), encoding="utf-8")
        return result


def load_fixture(path: Path) -> tuple[IngestionStore, str, list[KnowledgeChunk]]:
    """Kho in-memory chứa tài liệu mẫu đã cắt chunk bằng đúng bộ cắt của Vault."""
    raw = path.read_bytes()
    data: dict[str, Any] = {key: value for key, value in json.loads(raw).items() if not key.startswith("_")}
    document = UploadedDocument.from_content(raw, path.name, "application/pdf")
    store = IngestionStore()
    store.save_document(document)
    confirmed = ConfirmedDocumentText.model_validate({**data, "doc_id": document.document_id})
    return store, confirmed.subject_code, ingest_confirmed_text(store, confirmed)


def _inside_repo(path: Path) -> bool:
    return path.resolve().is_relative_to(REPO_ROOT)


def _cost(prompt_tokens: int, completion_tokens: int, price_in: float, price_out: float) -> float:
    return round(prompt_tokens * price_in / 1_000_000 + completion_tokens * price_out / 1_000_000, 6)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Đánh giá opt-in đề xuất cặp khái niệm bằng LLM thật.")
    parser.add_argument("chunks_json", type=Path, help="file JSON chunk mẫu (xem docstring)")
    parser.add_argument("--output-dir", type=Path, required=True, help="thư mục kết quả và cache, ngoài repo")
    parser.add_argument("--price-input-per-mtok", type=float, required=True, help="USD cho 1 triệu token đầu vào")
    parser.add_argument("--price-output-per-mtok", type=float, required=True, help="USD cho 1 triệu token đầu ra")
    parser.add_argument(
        "--max-calls", type=int, default=1 + LlmTaskLimits().json_retry_max, help="số lượt gọi LLM mới tối đa"
    )
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS, help="max_tokens mỗi lượt gọi")
    parser.add_argument("--dry-run", action="store_true", help="chỉ in ước tính chi phí, không gọi LLM")
    return parser


def _summary(
    *,
    args: argparse.Namespace,
    model: str,
    input_sha256: str,
    chunk_count: int,
    client: CachingClient,
    result: ConceptProposalResult | None,
    stats: ProposalStats,
    estimate_usd: float,
) -> dict[str, Any]:
    return {
        "prompt_version": PROMPT_VERSION,
        "model": model,
        "input_sha256": input_sha256,
        "chunks": chunk_count,
        "outcome": "ok" if result is not None else "failed",
        "proposed": 0 if result is None else len(result.pairs),
        "rejected": 0 if result is None else result.rejected_count,
        "rejected_by_reason": {} if result is None else result.rejected_by_reason,
        "rejected_quote_not_verbatim_i3": 0
        if result is None
        else result.rejected_by_reason.get("quote_not_verbatim", 0),
        "llm_calls": stats.llm_calls,
        "json_errors": stats.json_errors,
        "json_error_rate": stats.json_error_rate,
        "http_attempts": stats.http_attempts,
        "new_calls": client.new_calls,
        "cache_hits": client.cache_hits,
        "usage": {"prompt_tokens": stats.prompt_tokens, "completion_tokens": stats.completion_tokens},
        "estimated_cost_usd_upper_bound": estimate_usd,
        "cost_usd_from_usage": _cost(
            stats.prompt_tokens, stats.completion_tokens, args.price_input_per_mtok, args.price_output_per_mtok
        ),
        "pairs": []
        if result is None
        else [
            {"term": pair.term, "definition_quote": pair.definition_quote, "chunk_id": pair.chunk_id}
            for pair in result.pairs
        ],
    }


def run(
    args: argparse.Namespace,
    config: RuntimeConfig,
    connect: Callable[[], ChatCompletionClient] | None = None,
) -> int:
    """Chạy một lần đánh giá; `connect` mặc định tạo `OpenAiChatClient` từ `config` (chạy thử dùng stub)."""
    if _inside_repo(args.output_dir):
        print("--output-dir phải nằm ngoài repo (không ghi kết quả đánh giá vào repo).", file=sys.stderr)
        return EXIT_USAGE
    if args.max_calls < 0 or args.max_tokens < 1:
        print("--max-calls phải ≥ 0 và --max-tokens phải ≥ 1.", file=sys.stderr)
        return EXIT_USAGE
    model = config.llm.model_extract or ""
    limits = config.llm.task_limits
    store, subject_code, chunks = load_fixture(args.chunks_json)
    messages = build_messages(chunks)
    input_sha256 = request_key(model, messages, args.max_tokens)
    client = CachingClient(
        connect=connect or (lambda: OpenAiChatClient.from_settings(config.llm, model=model)),
        cache_dir=args.output_dir / "cache",
        model=model,
        max_calls=args.max_calls,
    )

    worst_calls = max_new_calls(
        args.max_calls, limits.json_retry_max, first_call_cached=client.cached(input_sha256).exists()
    )
    worst_requests = worst_calls * (1 + HTTP_RETRY_MAX)
    input_tokens = worst_call_input_tokens(messages)
    estimate_usd = cost_upper_bound_usd(
        calls=worst_requests,
        input_tokens=input_tokens,
        max_tokens=args.max_tokens,
        price_in_per_mtok=args.price_input_per_mtok,
        price_out_per_mtok=args.price_output_per_mtok,
    )
    print(f"Môn {subject_code}: {len(chunks)} chunk; model {model or '(chưa đặt)'}; prompt {PROMPT_VERSION}.")
    print(
        f"Cận trên TRƯỚC khi gọi: tối đa {worst_calls} lượt gọi mới, mỗi lượt tối đa {1 + HTTP_RETRY_MAX} request "
        f"HTTP ⇒ {worst_requests} request × (≤ {input_tokens} token vào, ≤ {args.max_tokens} token ra) "
        f"≤ {estimate_usd:.6f} USD."
    )
    if args.dry_run:
        print("--dry-run: không gọi LLM.")
        return 0
    if worst_calls > 0 and not config.llm_configured:
        print(f"Chưa cấu hình LLM: đặt {', '.join(config.llm.missing)}.", file=sys.stderr)
        return EXIT_USAGE

    result: ConceptProposalResult | None = None
    try:
        result = propose_concept_pairs(
            store,
            client,
            subject_code=subject_code,
            chunk_ids=[chunk.chunk_id for chunk in chunks],
            now=datetime.now(UTC),
            max_tokens=args.max_tokens,
            limits=limits,
        )
        stats = result.stats
    except ConceptProposalError as exc:
        print(f"Đề xuất thất bại: {exc}", file=sys.stderr)
        stats = exc.stats

    summary = _summary(
        args=args,
        model=model,
        input_sha256=input_sha256,
        chunk_count=len(chunks),
        client=client,
        result=result,
        stats=stats,
        estimate_usd=estimate_usd,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    out = args.output_dir / f"concept_proposal-{input_sha256[:12]}-{stamp}.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(
        f"Kết quả: {summary['proposed']} cặp đề xuất, {summary['rejected']} bị loại "
        f"({summary['rejected_quote_not_verbatim_i3']} vì I3), tỉ lệ JSON lỗi {stats.json_error_rate:.2f}, "
        f"usage {stats.prompt_tokens}/{stats.completion_tokens} token, {client.new_calls} lượt gọi mới, "
        f"{client.cache_hits} lượt lấy từ cache."
    )
    print(f"Đã ghi {out}")
    return 0 if result is not None else 1


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")  # console Windows mặc định cp1252, không in được tiếng Việt
    args = _parser().parse_args(argv)
    return run(args, load_runtime_config())


if __name__ == "__main__":
    raise SystemExit(main())
