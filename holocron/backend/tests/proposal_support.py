"""Hỗ trợ chung cho test đề xuất cặp theo con trỏ, cổng tích sẵn và job nền (spec §5.8, §6.6; ADR-007 mục 3).

Văn bản mẫu tự viết cho test (không phải tài liệu môn học có bản quyền). LLM là stub, KHÔNG gọi mạng.
"""

import json
import threading
from collections.abc import Callable, Sequence

from pydantic import BaseModel

from holocron_core.domain.llm import ChatCompletionResult, ChatMessage, LlmUpstreamError, TokenUsage
from holocron_core.domain.repositories import AppStore
from holocron_core.domain.schemas import KnowledgeChunk
from holocron_core.services.concept_proposal_pointer import build_spans
from holocron_core.services.vault import ingest_confirmed_text
from tests.vault_support import add_document, confirmed, document_id

SUBJECT = "IT3070"
DEF_PROCESS = "Tiến trình là một chương trình đang thực thi trong bộ nhớ."
DEF_THREAD = "Luồng là đơn vị lập lịch nhỏ nhất bên trong một tiến trình."
EXERCISE_LINE = "Hãy nêu khái niệm luồng và cho một ví dụ minh hoạ cụ thể."

THEORY_LINES = ["Khái niệm cơ bản", "", DEF_PROCESS, "", DEF_THREAD]
EXERCISE_LINES = ["Bài tập", "", EXERCISE_LINE]


class GatePage(BaseModel):
    """JSON trang đủ field mà luật tạm đọc (dạng trang sau GĐ0)."""

    tier_used: str | None = "text_layer"
    scan_text_layer: bool = False
    review_reasons: list[str] = []
    has_table: bool = False
    cross_check: dict[str, str] = {"status": "not_applicable"}
    page_role: str = "content"


class LegacyPage(BaseModel):
    """JSON trang trước GĐ0: không có `scan_text_layer`, `page_role`."""

    tier_used: str | None = "text_layer"
    review_reasons: list[str] = []
    has_table: bool = False
    cross_check: dict[str, str] = {"status": "not_applicable"}


def seed(
    store: AppStore, pages: Sequence[BaseModel] | None = None, seed_chars: tuple[str, str] = ("e", "f")
) -> list[KnowledgeChunk]:
    """Hai tài liệu mẫu một trang (lý thuyết, bài tập) cùng trang nguồn (mặc định trang "sạch" `GatePage()`) và một
    chunk cho mỗi tài liệu: `[chunk lý thuyết, chunk bài tập]`."""
    chunks: list[KnowledgeChunk] = []
    page_models = list(pages) if pages is not None else [GatePage(), GatePage()]
    for char, lines, page in zip(seed_chars, (THEORY_LINES, EXERCISE_LINES), page_models, strict=True):
        doc = document_id(char)
        add_document(store, doc)
        store.add_page(doc, 1, page, status="ok", confirmed=True)
        [chunk] = ingest_confirmed_text(store, confirmed(doc, [lines], subject_code=SUBJECT, chapter_code="ch2"))
        chunks.append(chunk)
    return chunks


def span_of(chunks: Sequence[KnowledgeChunk], fragment: str) -> str:
    """Id span (giao thức con trỏ) của dòng duy nhất chứa `fragment` trong lô `chunks`."""
    texts = {chunk.chunk_id: chunk.raw_text for chunk in chunks}
    found = [span.span_id for span in build_spans(chunks) if fragment in texts[span.chunk_id][span.start : span.end]]
    [span_id] = found
    return span_id


def pointer(
    term_span: str,
    term: str,
    def_spans: Sequence[str],
    start: str,
    end: str,
    **extra: object,
) -> dict[str, object]:
    return {
        "term_span_id": term_span,
        "term_exact": term,
        "def_span_ids": list(def_spans),
        "def_start_words": start,
        "def_end_words": end,
        **extra,
    }


def label(chunk: KnowledgeChunk, value: bool | None) -> dict[str, object]:
    return {"chunk_id": chunk.chunk_id, "is_exercise": value}


def as_json(*items: object) -> str:
    return json.dumps(list(items), ensure_ascii=False)


EMPTY_REPLY = '{"chunks": [], "pairs": []}'


def reply(*items: dict[str, object], **extra: object) -> str:
    """Phản hồi con trỏ `{"chunks": [...], "pairs": [...]}`: phần tử có `term_span_id` vào `pairs`, còn lại vào
    `chunks`."""
    pairs = [item for item in items if "term_span_id" in item]
    chunks = [item for item in items if "term_span_id" not in item]
    return json.dumps({"chunks": chunks, "pairs": pairs, **extra}, ensure_ascii=False)


Response = str | LlmUpstreamError | Callable[[Sequence[ChatMessage]], str]


class StubClient:
    """`ChatCompletionClient` giả: trả lần lượt phản hồi định sẵn (chuỗi, lỗi, hoặc hàm của tin nhắn)."""

    def __init__(self, *responses: Response, on_call: Callable[[], None] | None = None) -> None:
        self.responses = list(responses)
        self.calls: list[list[ChatMessage]] = []
        self.on_call = on_call
        self._lock = threading.Lock()

    def complete(self, messages: Sequence[ChatMessage], *, max_tokens: int) -> ChatCompletionResult:
        with self._lock:
            self.calls.append(list(messages))
            response = self.responses.pop(0)
        if self.on_call is not None:
            self.on_call()
        if isinstance(response, LlmUpstreamError):
            raise response
        content = response(messages) if callable(response) else response
        return ChatCompletionResult(
            content=content, usage=TokenUsage(prompt_tokens=10, completion_tokens=5), attempts=1
        )
