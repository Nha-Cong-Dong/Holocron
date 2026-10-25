"""LLM đề xuất cặp khái niệm (`services/concept_proposal.py`, `POST /v1/vault/concept-pairs/propose`; ADR-007 mục 1,
3; spec §6.6, v0.6 §6.0, §11.4, §11.5). LLM là stub `ChatCompletionClient`; KHÔNG gọi mạng.

Bất biến được kiểm: I3 (trích dẫn nguyên văn sau NFC và gộp khoảng trắng, sai thì chỉ được đếm), LLM không bao giờ
xác nhận cặp, chunk khác môn bị từ chối, JSON hỏng thì thử lại đúng một lần rồi bỏ cuộc.
"""

import http.client
import json
import logging
import socket
import unicodedata
from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from holocron_core.domain.llm import ChatCompletionResult, ChatMessage, LlmUpstreamError, TokenUsage
from holocron_core.domain.repositories import AppStore, RecordNotFoundError
from holocron_core.domain.schemas import KnowledgeChunk
from holocron_core.domain.vault import ConceptPairStatus
from holocron_core.infrastructure.llm_client import OpenAiChatClient
from holocron_core.infrastructure.llm_config import RuntimeConfig, load_runtime_config
from holocron_core.routers import vault
from holocron_core.routers.deps import get_clock, get_runtime_config, get_store
from holocron_core.services.concept_proposal import (
    MAX_PROPOSAL_CHUNKS,
    ChunkSubjectMismatchError,
    ConceptProposalError,
    ConceptProposalResult,
    ProposalFormatError,
    parse_proposals,
    propose_concept_pairs,
)
from holocron_core.services.concept_proposal_prompt import RETRY_NOTE, SYSTEM_PROMPT, build_messages
from holocron_core.services.vault import create_concept_pair, ingest_confirmed_text, reject_concept_pair
from tests.vault_support import (
    ARM_PAGES,
    NOW,
    PHILOSOPHY_PAGES,
    STORE_KINDS,
    add_document,
    confirmed,
    document_id,
    open_store,
)

SECRET = "sk-test-0123456789-must-never-leak"
PHIL_QUOTE = "Vật chất là phạm trù triết học dùng để chỉ thực tại khách quan."
MIND_QUOTE = "Ý thức là sự phản ánh năng động, sáng tạo thế giới khách quan vào bộ óc con người."
CONFIGURED_ENV = {
    "HOLOCRON_LLM_API_KEY": SECRET,
    "HOLOCRON_LLM_BASE_URL": "https://gateway.example.test/v1",
    "HOLOCRON_LLM_MODEL_EXTRACT": "secret-model-name",
}
PROPOSE_URL = "/v1/vault/concept-pairs/propose"


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("test không được gọi mạng")

    # `socket.socket.connect` không chặn được: vòng lặp asyncio của TestClient trên Windows dùng `socketpair`.
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(http.client.HTTPConnection, "connect", refuse)


class StubClient:
    """`ChatCompletionClient` giả: trả lần lượt các phản hồi định sẵn (chuỗi) hoặc ném lỗi định sẵn."""

    def __init__(self, *responses: str | LlmUpstreamError) -> None:
        self.responses = list(responses)
        self.calls: list[list[ChatMessage]] = []
        self.max_tokens: list[int] = []

    def complete(self, messages: Sequence[ChatMessage], *, max_tokens: int) -> ChatCompletionResult:
        self.calls.append(list(messages))
        self.max_tokens.append(max_tokens)
        response = self.responses.pop(0)
        if isinstance(response, LlmUpstreamError):
            raise response
        usage = TokenUsage(prompt_tokens=100, completion_tokens=20)
        return ChatCompletionResult(content=response, usage=usage, attempts=1)


def _json(*items: dict[str, str]) -> str:
    return json.dumps(list(items), ensure_ascii=False)


def _item(chunk: KnowledgeChunk, term: str, quote: str, **extra: str) -> dict[str, str]:
    return {"term": term, "definition_quote": quote, "chunk_id": chunk.chunk_id, **extra}


@pytest.fixture(params=STORE_KINDS)
def store(request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[AppStore]:
    yield from open_store(str(request.param), tmp_path, monkeypatch)


@pytest.fixture
def chunks(store: AppStore) -> dict[str, list[KnowledgeChunk]]:
    arm_doc, phil_doc = document_id("a"), document_id("b")
    add_document(store, arm_doc)
    add_document(store, phil_doc)
    return {
        "ET4010": ingest_confirmed_text(store, confirmed(arm_doc, ARM_PAGES)),
        "SSH1110": ingest_confirmed_text(
            store, confirmed(phil_doc, PHILOSOPHY_PAGES, subject_code="SSH1110", chapter_code="ch1")
        ),
    }


def _phil(chunks: dict[str, list[KnowledgeChunk]]) -> KnowledgeChunk:
    [chunk] = chunks["SSH1110"]
    return chunk


def _propose(
    store: AppStore, client: StubClient, chunk_ids: Sequence[str], subject: str = "SSH1110"
) -> ConceptProposalResult:
    return propose_concept_pairs(store, client, subject_code=subject, chunk_ids=chunk_ids, now=NOW)


# --------------------------------------------------------------------------- #
# Parse phản hồi
# --------------------------------------------------------------------------- #

ITEM = {"term": "Vật chất", "definition_quote": PHIL_QUOTE, "chunk_id": "chk_1"}
ITEM_JSON = json.dumps([ITEM], ensure_ascii=False)


@pytest.mark.parametrize(
    "text",
    [
        ITEM_JSON,
        "```json\n" + ITEM_JSON + "\n```",
        "```\n" + ITEM_JSON + "```",
        "Đây là kết quả [ghi chú]:\n```json\n" + ITEM_JSON + "\n```\nHết.",
        "Kết quả: " + ITEM_JSON + " (đã kiểm tra)",
        json.dumps({"pairs": [ITEM]}, ensure_ascii=False),
    ],
    ids=["plain", "fenced-json", "fenced-bare", "fenced-with-prose", "prose-around", "wrapped-object"],
)
def test_parse_tolerates_fences_and_surrounding_text(text: str) -> None:
    [proposal] = parse_proposals(text)
    assert (proposal.term, proposal.definition_quote, proposal.chunk_id) == ("Vật chất", PHIL_QUOTE, "chk_1")


def test_parse_accepts_an_empty_array_and_ignores_extra_fields() -> None:
    assert parse_proposals("[]") == []
    [proposal] = parse_proposals(json.dumps([{**ITEM, "status": "confirmed", "confidence": 0.9}]))
    assert "status" not in proposal.model_dump()


@pytest.mark.parametrize(
    "text",
    [
        "",
        "Không có định nghĩa nào.",
        '{"term": "x"}',
        '[{"term": "x", ',
        json.dumps([{"term": "x", "chunk_id": "c"}]),
        json.dumps([{"term": 1, "definition_quote": PHIL_QUOTE, "chunk_id": "c"}], ensure_ascii=False),
        json.dumps(["Vật chất"], ensure_ascii=False),
    ],
    ids=["empty", "prose", "object", "truncated", "missing-field", "wrong-type", "strings"],
)
def test_parse_rejects_malformed_responses_without_echoing_them(text: str) -> None:
    with pytest.raises(ProposalFormatError) as excinfo:
        parse_proposals(text)
    assert PHIL_QUOTE not in str(excinfo.value)


def test_prompt_is_vietnamese_subject_agnostic_and_sends_chunks_as_json(
    chunks: dict[str, list[KnowledgeChunk]],
) -> None:
    messages = build_messages(chunks["ET4010"])
    assert [message.role for message in messages] == ["system", "user"]
    assert messages[0].content == SYSTEM_PROMPT and "NGUYÊN VĂN" in SYSTEM_PROMPT
    payload = json.loads(messages[1].content)
    assert payload == [{"chunk_id": chunk.chunk_id, "text": chunk.raw_text} for chunk in chunks["ET4010"]]
    for word in ("ARM", "CPSR", "triết", "ET4010", "SSH1110"):
        assert word not in SYSTEM_PROMPT


# --------------------------------------------------------------------------- #
# Service
# --------------------------------------------------------------------------- #


def test_valid_json_creates_proposed_pairs(store: AppStore, chunks: dict[str, list[KnowledgeChunk]]) -> None:
    chunk = _phil(chunks)
    client = StubClient(_json(_item(chunk, "Vật chất", PHIL_QUOTE), _item(chunk, " Ý  thức ", MIND_QUOTE)))
    result = _propose(store, client, [chunk.chunk_id])
    assert [pair.term for pair in result.pairs] == ["Vật chất", "Ý thức"]
    assert result.rejected_count == 0
    for pair in result.pairs:
        assert pair.status is ConceptPairStatus.PROPOSED and pair.proposed_by == "llm"
        assert pair.confirmed_at is None and pair.created_at == NOW and pair.mapping_confidence is None
        assert (pair.subject_code, pair.chapter_code, pair.chunk_id) == ("SSH1110", "ch1", chunk.chunk_id)
    stored = store.list_concept_pairs("SSH1110")
    assert sorted(pair.pair_id for pair in stored) == sorted(pair.pair_id for pair in result.pairs)
    assert result.stats.llm_calls == 1 and result.stats.json_errors == 0 and result.stats.http_attempts == 1
    assert (result.stats.prompt_tokens, result.stats.completion_tokens) == (100, 20)
    assert client.max_tokens == [4096]


def test_missing_usage_counts_no_tokens(store: AppStore, chunks: dict[str, list[KnowledgeChunk]]) -> None:
    class NoUsageClient:
        def complete(self, messages: Sequence[ChatMessage], *, max_tokens: int) -> ChatCompletionResult:
            return ChatCompletionResult(content="[]", attempts=2)

    result = propose_concept_pairs(
        store, NoUsageClient(), subject_code="SSH1110", chunk_ids=[_phil(chunks).chunk_id], now=NOW
    )
    assert (result.stats.prompt_tokens, result.stats.completion_tokens, result.stats.http_attempts) == (0, 0, 2)


def test_fenced_json_is_accepted(store: AppStore, chunks: dict[str, list[KnowledgeChunk]]) -> None:
    chunk = _phil(chunks)
    client = StubClient("Kết quả:\n```json\n" + _json(_item(chunk, "Vật chất", PHIL_QUOTE)) + "\n```")
    result = _propose(store, client, [chunk.chunk_id])
    assert [pair.term for pair in result.pairs] == ["Vật chất"] and len(client.calls) == 1


def test_broken_json_is_retried_once_then_accepted(store: AppStore, chunks: dict[str, list[KnowledgeChunk]]) -> None:
    chunk = _phil(chunks)
    client = StubClient("Tôi không chắc.", _json(_item(chunk, "Vật chất", PHIL_QUOTE)))
    result = _propose(store, client, [chunk.chunk_id])
    assert len(result.pairs) == 1
    assert result.stats.llm_calls == 2 and result.stats.json_errors == 1 and result.stats.json_error_rate == 0.5
    retry = client.calls[1]
    assert retry[:2] == client.calls[0]
    assert retry[2].role == "assistant" and retry[2].content == "Tôi không chắc."
    assert retry[3].role == "user" and retry[3].content == RETRY_NOTE


def test_broken_json_twice_gives_up_without_saving(
    store: AppStore, chunks: dict[str, list[KnowledgeChunk]], caplog: pytest.LogCaptureFixture
) -> None:
    chunk = _phil(chunks)
    client = StubClient('[{"term": ', "vẫn không phải JSON " + SECRET)
    with caplog.at_level(logging.INFO), pytest.raises(ConceptProposalError, match="JSON") as excinfo:
        _propose(store, client, [chunk.chunk_id])
    assert len(client.calls) == 2
    assert excinfo.value.stats.json_errors == 2 and excinfo.value.stats.json_error_rate == 1.0
    assert store.list_concept_pairs("SSH1110") == []
    assert "outcome=failed" in caplog.text and "json_errors=2" in caplog.text
    assert SECRET not in caplog.text and SECRET not in str(excinfo.value)


def test_retry_budget_is_a_parameter(store: AppStore, chunks: dict[str, list[KnowledgeChunk]]) -> None:
    chunk = _phil(chunks)
    client = StubClient("x")
    with pytest.raises(ConceptProposalError):
        propose_concept_pairs(
            store, client, subject_code="SSH1110", chunk_ids=[chunk.chunk_id], now=NOW, json_retry_max=0
        )
    assert len(client.calls) == 1


def test_upstream_failure_is_wrapped_with_counts(store: AppStore, chunks: dict[str, list[KnowledgeChunk]]) -> None:
    chunk = _phil(chunks)
    client = StubClient(LlmUpstreamError("LLM không phản hồi được sau 3 lần thử (HTTP 503)", attempts=3))
    with pytest.raises(ConceptProposalError) as excinfo:
        _propose(store, client, [chunk.chunk_id])
    assert isinstance(excinfo.value, LlmUpstreamError)
    assert excinfo.value.stats.http_attempts == 3 and excinfo.value.attempts == 3
    assert excinfo.value.stats.llm_calls == 1
    assert store.list_concept_pairs("SSH1110") == []


def test_wrong_quotes_are_rejected_and_only_counted(store: AppStore, chunks: dict[str, list[KnowledgeChunk]]) -> None:
    """I3: trích dẫn không phải chuỗi con nguyên văn của đúng chunk được nêu thì không lưu, chỉ đếm."""
    chunk = _phil(chunks)
    arm_chunk = chunks["ET4010"][0]
    paraphrase = "Vật chất là phạm trù dùng để chỉ thực tại khách quan."
    no_diacritics = "Vat chat la pham tru triet hoc dung de chi thuc tai khach quan."
    proposals = [
        _item(chunk, "Vật chất", paraphrase),
        _item(chunk, "Vật chất", no_diacritics),
        _item(chunk, "Vật chất", PHIL_QUOTE.upper()),
        _item(chunk, "Vật chất", unicodedata.normalize("NFD", paraphrase)),
        _item(chunk, "Vật chất", unicodedata.normalize("NFKD", PHIL_QUOTE).encode("ascii", "ignore").decode()),
        {**_item(chunk, "Vật chất", PHIL_QUOTE), "chunk_id": arm_chunk.chunk_id},
        {**_item(chunk, "Vật chất", PHIL_QUOTE), "chunk_id": "chk_khong_ton_tai"},
        _item(chunk, "Ý thức", MIND_QUOTE),
    ]
    result = _propose(store, StubClient(_json(*proposals)), [chunk.chunk_id])
    assert [pair.term for pair in result.pairs] == ["Ý thức"]
    assert result.rejected_count == 7
    assert result.rejected_by_reason == {"quote_not_verbatim": 5, "unknown_chunk": 2}
    assert [pair.term for pair in store.list_concept_pairs("SSH1110")] == ["Ý thức"]


def test_quote_in_nfd_or_with_extra_whitespace_matches_the_nfc_chunk_text(
    store: AppStore, chunks: dict[str, list[KnowledgeChunk]]
) -> None:
    """I3 so khớp sau NFC và gộp khoảng trắng: khác nhau CHỈ ở dạng chuẩn hoá thì vẫn là trích dẫn nguyên văn."""
    chunk = _phil(chunks)
    nfd_quote = unicodedata.normalize("NFD", PHIL_QUOTE)
    assert nfd_quote != PHIL_QUOTE
    spaced = MIND_QUOTE.replace(" ", "  \n ", 3)
    proposals = [
        _item(chunk, unicodedata.normalize("NFD", "Vật chất"), nfd_quote),
        _item(chunk, "Ý thức", spaced),
    ]
    result = _propose(store, StubClient(_json(*proposals)), [chunk.chunk_id])
    assert result.rejected_count == 0
    assert [pair.definition_quote for pair in result.pairs] == [PHIL_QUOTE, MIND_QUOTE]
    assert result.pairs[0].term == "Vật chất" and result.pairs[0].term_spans == [(0, 8)]


@pytest.mark.parametrize(
    ("term", "quote", "reason"),
    [
        ("", PHIL_QUOTE, "invalid_term"),
        ("   ", PHIL_QUOTE, "invalid_term"),
        ("x" * 121, PHIL_QUOTE, "invalid_term"),
        ("Vật", "Vật chấ", "invalid_quote"),
        ("Vật chất", "", "invalid_quote"),
    ],
    ids=["empty-term", "blank-term", "long-term", "short-quote", "empty-quote"],
)
def test_terms_and_quotes_outside_the_limits_are_rejected(
    store: AppStore, chunks: dict[str, list[KnowledgeChunk]], term: str, quote: str, reason: str
) -> None:
    chunk = _phil(chunks)
    result = _propose(store, StubClient(_json(_item(chunk, term, quote))), [chunk.chunk_id])
    assert result.pairs == [] and result.rejected_count == 1 and result.rejected_by_reason == {reason: 1}


def test_llm_can_never_confirm_a_pair(store: AppStore, chunks: dict[str, list[KnowledgeChunk]]) -> None:
    chunk = _phil(chunks)
    item = _item(chunk, "Vật chất", PHIL_QUOTE, status="confirmed", proposed_by="user", confirmed_at=NOW.isoformat())
    result = _propose(store, StubClient(_json(item)), [chunk.chunk_id])
    [pair] = result.pairs
    assert pair.status is ConceptPairStatus.PROPOSED and pair.proposed_by == "llm" and pair.confirmed_at is None
    assert store.list_concept_pairs("SSH1110", status=ConceptPairStatus.CONFIRMED) == []
    assert all(stored.status is ConceptPairStatus.PROPOSED for stored in store.list_concept_pairs("SSH1110"))


def test_duplicates_in_the_response_or_of_existing_pairs_are_not_saved_again(
    store: AppStore, chunks: dict[str, list[KnowledgeChunk]]
) -> None:
    chunk = _phil(chunks)
    existing = create_concept_pair(store, chunk_id=chunk.chunk_id, term="Ý thức", definition_quote=MIND_QUOTE, now=NOW)
    rejected = create_concept_pair(store, chunk_id=chunk.chunk_id, term="Vật", definition_quote=PHIL_QUOTE, now=NOW)
    reject_concept_pair(store, rejected.pair_id, expected_version=rejected.version)
    proposals = [
        _item(chunk, "Vật chất", PHIL_QUOTE),
        _item(chunk, "Vật  chất", unicodedata.normalize("NFD", PHIL_QUOTE)),
        _item(chunk, "Ý thức", MIND_QUOTE),
        _item(chunk, "Vật", PHIL_QUOTE),
    ]
    result = _propose(store, StubClient(_json(*proposals)), [chunk.chunk_id])
    assert [pair.term for pair in result.pairs] == ["Vật chất"] and result.rejected_count == 3
    assert result.rejected_by_reason == {"duplicate": 3}
    stored = store.list_concept_pairs("SSH1110")
    assert existing.pair_id in {pair.pair_id for pair in stored} and len(stored) == 3


def test_chunks_of_another_subject_are_refused_before_calling_the_llm(
    store: AppStore, chunks: dict[str, list[KnowledgeChunk]]
) -> None:
    client = StubClient("[]")
    arm_chunk = chunks["ET4010"][0]
    with pytest.raises(ChunkSubjectMismatchError) as excinfo:
        _propose(store, client, [_phil(chunks).chunk_id, arm_chunk.chunk_id])
    assert excinfo.value.chunk_id == arm_chunk.chunk_id
    assert client.calls == [] and store.list_concept_pairs("SSH1110") == []


def test_unknown_chunk_is_not_found(store: AppStore, chunks: dict[str, list[KnowledgeChunk]]) -> None:
    client = StubClient("[]")
    with pytest.raises(RecordNotFoundError):
        _propose(store, client, [_phil(chunks).chunk_id, "chk_khong_ton_tai"])
    assert client.calls == []


def test_chunk_count_limits_and_duplicates(store: AppStore, chunks: dict[str, list[KnowledgeChunk]]) -> None:
    chunk = _phil(chunks)
    with pytest.raises(ValueError, match="từ 1 đến 20"):
        _propose(store, StubClient(), [])
    client = StubClient("[]")
    result = _propose(store, client, [chunk.chunk_id] * (MAX_PROPOSAL_CHUNKS + 5))
    assert result.pairs == [] and result.rejected_count == 0
    assert json.loads(client.calls[0][1].content) == [{"chunk_id": chunk.chunk_id, "text": chunk.raw_text}]


def test_counts_are_logged_without_content(
    store: AppStore, chunks: dict[str, list[KnowledgeChunk]], caplog: pytest.LogCaptureFixture
) -> None:
    chunk = _phil(chunks)
    client = StubClient(_json(_item(chunk, "Vật chất", PHIL_QUOTE), _item(chunk, "Sai", "không có trong chunk")))
    with caplog.at_level(logging.INFO):
        _propose(store, client, [chunk.chunk_id])
    assert "llm_task=concept_pair_proposal outcome=ok" in caplog.text
    assert "proposed=1 rejected=1 rejected_quote_not_verbatim=1" in caplog.text and "prompt_tokens=100" in caplog.text
    assert PHIL_QUOTE not in caplog.text and "Vật chất" not in caplog.text


# --------------------------------------------------------------------------- #
# Router
# --------------------------------------------------------------------------- #


def _app(store: AppStore, config: RuntimeConfig, client: StubClient | None) -> TestClient:
    app = FastAPI()
    app.include_router(vault.router)
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_clock] = lambda: lambda: NOW
    app.dependency_overrides[get_runtime_config] = lambda: config
    if client is not None:
        app.dependency_overrides[vault.get_chat_client] = lambda: client
    return TestClient(app)


def test_endpoint_returns_proposed_pairs_and_rejected_count(
    store: AppStore, chunks: dict[str, list[KnowledgeChunk]]
) -> None:
    chunk = _phil(chunks)
    stub = StubClient(_json(_item(chunk, "Vật chất", PHIL_QUOTE), _item(chunk, "Sai", "không có trong chunk")))
    response = _app(store, load_runtime_config(CONFIGURED_ENV), stub).post(
        PROPOSE_URL, json={"subject_code": "SSH1110", "chunk_ids": [chunk.chunk_id]}
    )
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"pairs", "rejected_count"} and body["rejected_count"] == 1
    [pair] = body["pairs"]
    assert pair["status"] == "proposed" and pair["proposed_by"] == "llm" and pair["confirmed_at"] is None
    assert pair["definition_quote"] == PHIL_QUOTE


def test_endpoint_without_llm_is_a_409_capability_error(
    store: AppStore, chunks: dict[str, list[KnowledgeChunk]]
) -> None:
    response = _app(store, load_runtime_config({}), None).post(
        PROPOSE_URL, json={"subject_code": "SSH1110", "chunk_ids": [_phil(chunks).chunk_id]}
    )
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert {key: detail[key] for key in ("code", "capability", "reason")} == {
        "code": "CAPABILITY_UNAVAILABLE",
        "capability": "concept_pair_llm_proposal",
        "reason": "llm_not_configured",
    }
    assert "HOLOCRON_LLM_API_KEY" in detail["message"]
    assert store.list_concept_pairs("SSH1110") == []


def test_configured_dependency_builds_the_real_adapter_without_calling_it() -> None:
    client = vault.get_chat_client(load_runtime_config(CONFIGURED_ENV))
    assert isinstance(client, OpenAiChatClient)
    request = client.build_request([ChatMessage(role="user", content="x")], max_tokens=1)
    assert request.data is not None and json.loads(request.data)["model"] == "secret-model-name"
    assert SECRET not in repr(client)


@pytest.mark.parametrize(
    "responses",
    [
        (LlmUpstreamError("LLM từ chối yêu cầu (HTTP 403)", attempts=1),),
        ("không phải JSON " + SECRET, "vẫn không phải JSON"),
    ],
    ids=["upstream", "json"],
)
def test_endpoint_maps_llm_failures_to_502_without_leaking(
    store: AppStore, chunks: dict[str, list[KnowledgeChunk]], responses: tuple[str | LlmUpstreamError, ...]
) -> None:
    response = _app(store, load_runtime_config(CONFIGURED_ENV), StubClient(*responses)).post(
        PROPOSE_URL, json={"subject_code": "SSH1110", "chunk_ids": [_phil(chunks).chunk_id]}
    )
    assert response.status_code == 502
    detail = response.json()["detail"]
    assert detail["code"] == "LLM_UPSTREAM_ERROR" and set(detail) == {"code", "message"}
    for leaked in (SECRET, "secret-model-name", "gateway.example.test", "403", "JSON"):
        assert leaked not in response.text


def test_endpoint_errors_for_chunks(store: AppStore, chunks: dict[str, list[KnowledgeChunk]]) -> None:
    client = _app(store, load_runtime_config(CONFIGURED_ENV), StubClient())
    phil, arm = _phil(chunks).chunk_id, chunks["ET4010"][0].chunk_id
    missing = client.post(PROPOSE_URL, json={"subject_code": "SSH1110", "chunk_ids": ["chk_khong_ton_tai"]})
    assert missing.status_code == 404
    other_subject = client.post(PROPOSE_URL, json={"subject_code": "SSH1110", "chunk_ids": [phil, arm]})
    assert other_subject.status_code == 422
    assert other_subject.json()["detail"][0]["loc"] == ["body", "chunk_ids"]
    for body in (
        {"subject_code": "SSH1110", "chunk_ids": []},
        {"subject_code": "SSH1110", "chunk_ids": [phil] * (MAX_PROPOSAL_CHUNKS + 1)},
        {"subject_code": "ssh", "chunk_ids": [phil]},
        {"subject_code": "SSH1110", "chunk_ids": ["x" * 129]},
        {"subject_code": "SSH1110", "chunk_ids": [phil], "status": "confirmed"},
    ):
        assert client.post(PROPOSE_URL, json=body).status_code == 422, body
