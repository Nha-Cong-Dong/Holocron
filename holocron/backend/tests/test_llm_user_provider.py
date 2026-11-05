"""Client LLM theo nguồn hợp nhất env + nhà cung cấp người dùng (ADR-009 mục 12.3, 12.8, 12.11; spec §5.12, §11.4)
và sổ chi tiêu fail-closed khi `ai_usage.json` không đọc được (spec §5.16 "Lưu trữ").

Khả năng `concept_pair_llm_proposal` báo ở `GET /v1/runtime` (`deps.merged_capabilities`) phải khớp việc phụ thuộc
`vault.get_chat_client` dựng được client: chỉ có nhà cung cấp người dùng vai `llm` ⇒ không 409; chỉ có env ⇒ client env
như cũ. Mọi lời gọi qua transport giả, KHÔNG gọi mạng.
"""

import http.client
import json
import logging
import socket
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from holocron_core.domain.ai import IpPolicy, ProviderCandidate, ProviderModels, ProviderPrices
from holocron_core.domain.llm import ChatMessage, LlmUpstreamError
from holocron_core.domain.repositories import AppStore
from holocron_core.domain.schemas import KnowledgeChunk
from holocron_core.infrastructure.ai_runtime import (
    AI_USAGE_FILENAME,
    MAX_USAGE_BYTES,
    FileSpendLedger,
    InMemorySpendLedger,
)
from holocron_core.infrastructure.llm_client import OpenAiChatClient
from holocron_core.infrastructure.llm_config import LlmHttpLimits, LlmSettings, load_runtime_config
from holocron_core.infrastructure.selected_llm_client import SelectedProviderChatClient, estimate_chat_cost
from holocron_core.routers import documents, vault
from holocron_core.routers.deps import (
    get_clock,
    get_llm_transport_factory,
    get_provider_selection,
    get_runtime_config,
    get_store,
    merged_capabilities,
)
from holocron_core.services.concept_proposal_job import ClientFactory
from holocron_core.services.provider_selection import ProviderSelection
from holocron_core.services.vault import ingest_confirmed_text
from tests.vault_support import NOW, PHILOSOPHY_PAGES, STORE_KINDS, add_document, confirmed, document_id, open_store

USER_KEY = "sk-user-0123456789abcdef"
USER_HOST = "llm-user.example.test"
USER_MODEL = "user-llm-model"
USER_ID = "usr_0000000000a1"
PHIL_QUOTE = "Vật chất là phạm trù triết học dùng để chỉ thực tại khách quan."
ENV_ONLY = {
    "HOLOCRON_LLM_API_KEY": "sk-test-0123456789-env-key",
    "HOLOCRON_LLM_BASE_URL": "https://gateway.example.test/v1",
    "HOLOCRON_LLM_MODEL_EXTRACT": "env-model-name",
}
PROPOSE_URL = "/v1/vault/concept-pairs/propose"


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("test không được gọi mạng")

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(http.client.HTTPConnection, "connect", refuse)


def _user_llm(*, budget: float = 1.0, enabled: bool = True) -> ProviderCandidate:
    return ProviderCandidate(
        provider_id=USER_ID,
        template="custom",
        label="LLM của tôi",
        base_url=f"https://{USER_HOST}/v1",
        api_key=SecretStr(USER_KEY),
        models=ProviderModels(llm=USER_MODEL),
        local=False,
        ip_policy="public_only",
        priority=100,
        budget_usd=budget,
        prices=ProviderPrices(input_per_1k_usd=0.01, output_per_1k_usd=0.01, per_image_usd=0.0, source="user"),
        budget_group=USER_ID,
        enabled=enabled,
    )


def _selection(*candidates: ProviderCandidate) -> ProviderSelection:
    frozen = list(candidates)
    return ProviderSelection(lambda: frozen, InMemorySpendLedger())


def _ok_body(content: str, usage: dict[str, int] | None = None) -> bytes:
    body: dict[str, object] = {
        "id": "x",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}}],
    }
    if usage is not None:
        body["usage"] = usage
    return json.dumps(body).encode("utf-8")


class FakeTransports:
    """`TransportFactory` giả: ghi chính sách IP được yêu cầu và mọi request; trả lần lượt các phản hồi định sẵn."""

    def __init__(self, *outcomes: tuple[int, bytes]) -> None:
        self.outcomes = list(outcomes)
        self.policies: list[IpPolicy] = []
        self.requests: list[urllib.request.Request] = []

    def __call__(self, policy: IpPolicy, limits: LlmHttpLimits) -> "FakeTransports.Bound":
        self.policies.append(policy)
        return FakeTransports.Bound(self)

    class Bound:
        def __init__(self, owner: "FakeTransports") -> None:
            self.owner = owner

        def __call__(self, request: urllib.request.Request, timeout_s: float) -> tuple[int, bytes]:
            self.owner.requests.append(request)
            return self.owner.outcomes.pop(0)


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[AppStore]:
    yield from open_store(str(STORE_KINDS[0]), tmp_path, monkeypatch)


@pytest.fixture
def chunk(store: AppStore) -> KnowledgeChunk:
    phil_doc = document_id("b")
    add_document(store, phil_doc)
    [only] = ingest_confirmed_text(
        store, confirmed(phil_doc, PHILOSOPHY_PAGES, subject_code="SSH1110", chapter_code="ch1")
    )
    return only


def _app(store: AppStore, env: dict[str, str], selection: ProviderSelection, transports: FakeTransports) -> TestClient:
    app = FastAPI()
    app.include_router(vault.router)
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_clock] = lambda: lambda: NOW
    app.dependency_overrides[get_runtime_config] = lambda: load_runtime_config(env)
    app.dependency_overrides[get_provider_selection] = lambda: selection
    app.dependency_overrides[get_llm_transport_factory] = lambda: transports
    return TestClient(app)


def _proposal_available(env: dict[str, str], selection: ProviderSelection) -> bool:
    capabilities = merged_capabilities(load_runtime_config(env), selection.snapshot())
    return next(c for c in capabilities if c.name == "concept_pair_llm_proposal").available


# --------------------------------------------------------------------------- #
# Khả năng báo ra khớp việc dựng client
# --------------------------------------------------------------------------- #


def test_user_llm_provider_without_env_proposes_instead_of_409(store: AppStore, chunk: KnowledgeChunk) -> None:
    selection = _selection(_user_llm())
    item = {"term": "Vật chất", "definition_quote": PHIL_QUOTE, "chunk_id": chunk.chunk_id}
    reply = _ok_body(json.dumps([item], ensure_ascii=False), {"prompt_tokens": 120, "completion_tokens": 30})
    transports = FakeTransports((200, reply))
    assert _proposal_available({}, selection)  # GET /v1/runtime báo khả dụng ...
    response = _app(store, {}, selection, transports).post(
        PROPOSE_URL, json={"subject_code": "SSH1110", "chunk_ids": [chunk.chunk_id]}
    )
    assert response.status_code == 200, response.text  # ... và endpoint dựng được client (không 409)
    [pair] = response.json()["pairs"]
    assert pair["definition_quote"] == PHIL_QUOTE and pair["status"] == "proposed"
    # Đi qua bộ chọn: transport theo ip_policy của nhà cung cấp người dùng, đúng host, chi phí vào sổ.
    assert transports.policies == ["public_only"]
    [request] = transports.requests
    assert USER_HOST in request.full_url
    assert request.data is not None and json.loads(request.data)["model"] == USER_MODEL
    assert selection.spent_usd(USER_ID) > 0
    for leaked in (USER_KEY, USER_HOST, USER_MODEL):
        assert leaked not in response.text


def test_disabled_user_provider_keeps_409_consistent_with_runtime(store: AppStore, chunk: KnowledgeChunk) -> None:
    selection = _selection(_user_llm(enabled=False))
    transports = FakeTransports()
    assert not _proposal_available({}, selection)
    response = _app(store, {}, selection, transports).post(
        PROPOSE_URL, json={"subject_code": "SSH1110", "chunk_ids": [chunk.chunk_id]}
    )
    assert response.status_code == 409
    assert response.json()["detail"]["reason"] == "llm_not_configured"
    assert transports.policies == []


def test_user_provider_over_budget_is_502_without_sending(store: AppStore, chunk: KnowledgeChunk) -> None:
    selection = _selection(_user_llm(budget=0.0))
    transports = FakeTransports()
    response = _app(store, {}, selection, transports).post(
        PROPOSE_URL, json={"subject_code": "SSH1110", "chunk_ids": [chunk.chunk_id]}
    )
    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "LLM_UPSTREAM_ERROR"
    assert transports.requests == [] and transports.policies == []


def test_env_only_keeps_the_env_client() -> None:
    selection = _selection()
    assert _proposal_available(ENV_ONLY, selection)
    client = vault.get_chat_client(load_runtime_config(ENV_ONLY), providers=selection)
    assert isinstance(client, OpenAiChatClient)
    request = client.build_request([ChatMessage(role="user", content="x")], max_tokens=1)
    assert request.data is not None and json.loads(request.data)["model"] == "env-model-name"
    assert "gateway.example.test" in request.full_url


def test_env_wins_over_user_provider() -> None:
    client = vault.get_chat_client(load_runtime_config(ENV_ONLY), providers=_selection(_user_llm()))
    assert isinstance(client, OpenAiChatClient)


def test_nothing_configured_is_still_409(store: AppStore, chunk: KnowledgeChunk) -> None:
    response = _app(store, {}, _selection(), FakeTransports()).post(
        PROPOSE_URL, json={"subject_code": "SSH1110", "chunk_ids": [chunk.chunk_id]}
    )
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["reason"] == "llm_not_configured" and "HOLOCRON_LLM_API_KEY" in detail["message"]


def test_job_factory_and_auto_job_use_the_user_provider(store: AppStore) -> None:
    selection = _selection(_user_llm())
    config = load_runtime_config({})
    factory = vault.get_chat_client_factory(config, providers=selection, transport_for=FakeTransports())
    assert isinstance(factory(), SelectedProviderChatClient)

    def build(_config: object, _failures: object) -> ClientFactory:
        return factory

    proposals = documents.document_proposals(store, config, {}, lambda: NOW, None, build, selection)
    assert proposals.llm_available
    assert not documents.document_proposals(store, config, {}, lambda: NOW, None, build, _selection()).llm_available


# --------------------------------------------------------------------------- #
# SelectedProviderChatClient: ghi chi phí
# --------------------------------------------------------------------------- #


def test_upstream_failure_after_sending_records_worst_case_cost() -> None:
    selection = _selection(_user_llm())
    settings = LlmSettings(http_limits=LlmHttpLimits(retry_max=0))
    transports = FakeTransports((400, b"{}"))
    client = SelectedProviderChatClient(
        selector=selection, recorder=selection, settings=settings, transport_for=transports, sleep=lambda _s: None
    )
    messages = [ChatMessage(role="user", content="x" * 200)]
    with pytest.raises(LlmUpstreamError):
        client.complete(messages, max_tokens=50)
    estimate = estimate_chat_cost(messages, 50)
    expected = (estimate.input_tokens + estimate.max_output_tokens) * 0.01 / 1000
    assert selection.spent_usd(USER_ID) == pytest.approx(expected)


# --------------------------------------------------------------------------- #
# FileSpendLedger fail-closed
# --------------------------------------------------------------------------- #


def test_unreadable_usage_file_refuses_reservations_and_is_never_overwritten(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    usage = tmp_path / AI_USAGE_FILENAME
    original = b" " * (MAX_USAGE_BYTES + 1)  # quá trần đọc ⇒ Unreadable
    usage.write_bytes(original)
    with caplog.at_level(logging.WARNING):
        ledger = FileSpendLedger(tmp_path)
        assert ledger.unreadable
        assert not ledger.try_reserve(USER_ID, 0.01, 1000.0)  # coi như hết trần
        ledger.settle(USER_ID, 0.0, 0.5)
    assert usage.read_bytes() == original  # không ghi đè số đã tiêu đã lưu
    assert "không đọc được" in caplog.text and str(tmp_path) not in caplog.text
    selection = ProviderSelection(lambda: [_user_llm(budget=1000.0)], ledger)
    assert selection.select("llm", estimate_chat_cost([ChatMessage(role="user", content="x")], 10)) is None


def test_usage_file_readable_again_restores_spending(tmp_path: Path) -> None:
    usage = tmp_path / AI_USAGE_FILENAME
    usage.write_bytes(b" " * (MAX_USAGE_BYTES + 1))
    ledger = FileSpendLedger(tmp_path)
    assert not ledger.try_reserve(USER_ID, 0.1, 1.0)
    usage.write_text(json.dumps({"version": 1, "spent_usd": {USER_ID: 0.95}}), encoding="utf-8")
    assert not ledger.try_reserve(USER_ID, 0.1, 1.0)  # đọc lại: 0.95 + 0.1 > 1.0
    assert not ledger.unreadable and ledger.spent_usd(USER_ID) == pytest.approx(0.95)
    assert ledger.try_reserve(USER_ID, 0.04, 1.0)
