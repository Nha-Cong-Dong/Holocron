"""Cấu hình LLM/VLM hỏng không làm app trả 500 (sửa sau review; spec v0.6 §6.0, §11.2, §11.4, §5.12).

Khoá có khoảng trắng hay base URL sai: `GET /v1/runtime` vẫn 200 với `llm_configured` / `vlm_configured = false`,
`POST /v1/vault/concept-pairs/propose` trả 409 `CapabilityError` (`reason = "llm_not_configured"`), không lộ giá trị.
Cấu hình đọc thật từ biến môi trường qua `get_runtime_config` (không override). KHÔNG gọi mạng.
"""

import http.client
import logging
import os
import socket
from collections.abc import Sequence
from typing import ClassVar, Self

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from holocron_core.domain.llm import ChatCompletionResult, ChatMessage
from holocron_core.domain.repositories import AppStore
from holocron_core.infrastructure.llm_config import (
    VLM_REQUIRED_VARIABLES,
    ConfigError,
    LlmSettings,
    load_runtime_config,
    load_runtime_config_lenient,
)
from holocron_core.infrastructure.memory_store import IngestionStore
from holocron_core.routers import vault
from holocron_core.routers.api import app
from holocron_core.routers.deps import get_clock, get_runtime_config, get_store
from holocron_core.services.vault import ingest_confirmed_text
from tests.vault_support import NOW, PHILOSOPHY_PAGES, add_document, confirmed, document_id

LLM_SECRET = "sk-llm-0123456789-must-never-leak"
VALID_LLM_ENV = {
    "HOLOCRON_LLM_BASE_URL": "https://gateway.example.test/v1",
    "HOLOCRON_LLM_API_KEY": LLM_SECRET,
    "HOLOCRON_LLM_MODEL_EXTRACT": "secret-model-name",
}
VLM_ENV_WITH_BAD_KEY = {
    "HOLOCRON_VLM_BASE_URL": "https://vlm.example.test/v1",
    "HOLOCRON_VLM_API_KEY": "abc def",
    "HOLOCRON_VLM_MODEL": "vision-model-x",
}
LLM_KEY_WITH_SPACE = "sk-llm-0123 456789-must-never-leak"
LEAKS = ("abc def", "must-never-leak", "secret-model-name", "gateway.example.test", "vlm.example.test", "0123")
PROPOSE_URL = "/v1/vault/concept-pairs/propose"


@pytest.fixture(autouse=True)
def _clean_env_and_no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in [n for n in os.environ if n.startswith(("HOLOCRON_LLM_", "HOLOCRON_VLM_"))]:
        monkeypatch.delenv(name)

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("test không được gọi mạng")

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(http.client.HTTPConnection, "connect", refuse)


def _set_env(monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> None:
    for name, value in env.items():
        monkeypatch.setenv(name, value)


def _assert_no_leak(text: str) -> None:
    for leaked in LEAKS:
        assert leaked not in text, leaked


class RecordingAdapter:
    """Thay `OpenAiChatClient` trong router: ghi lại lần dựng client, trả mảng đề xuất rỗng, không gọi mạng."""

    built: ClassVar[list[str | None]] = []

    @classmethod
    def from_settings(cls, settings: LlmSettings, *, model: str | None) -> Self:
        cls.built.append(model)
        return cls()

    def complete(self, messages: Sequence[ChatMessage], *, max_tokens: int) -> ChatCompletionResult:
        return ChatCompletionResult(content="[]", attempts=1)


@pytest.fixture
def recording_adapter(monkeypatch: pytest.MonkeyPatch) -> type[RecordingAdapter]:
    RecordingAdapter.built = []
    monkeypatch.setattr(vault, "OpenAiChatClient", RecordingAdapter)
    return RecordingAdapter


@pytest.fixture
def vault_app() -> tuple[TestClient, AppStore, str]:
    """App chỉ gồm router Vault (router này chưa gắn vào `app` chính); `get_runtime_config` KHÔNG bị override."""
    store = IngestionStore()
    doc_id = document_id("b")
    add_document(store, doc_id)
    [chunk] = ingest_confirmed_text(
        store, confirmed(doc_id, PHILOSOPHY_PAGES, subject_code="SSH1110", chapter_code="ch1")
    )
    vault_only = FastAPI()
    vault_only.include_router(vault.router)
    vault_only.dependency_overrides[get_store] = lambda: store
    vault_only.dependency_overrides[get_clock] = lambda: lambda: NOW
    return TestClient(vault_only), store, chunk.chunk_id


def _runtime() -> TestClient:
    assert get_runtime_config not in app.dependency_overrides
    return TestClient(app)


# --------------------------------------------------------------------------- #
# GET /v1/runtime
# --------------------------------------------------------------------------- #


def test_runtime_with_a_vlm_key_containing_a_space_is_200_and_vlm_not_configured(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _set_env(monkeypatch, VLM_ENV_WITH_BAD_KEY)
    with caplog.at_level(logging.WARNING):
        response = _runtime().get("/v1/runtime")
    assert response.status_code == 200
    assert response.json() == {"demo_mode": True, "llm_configured": False, "vlm_configured": False}
    _assert_no_leak(response.text)
    assert "HOLOCRON_VLM_API_KEY" in caplog.text
    _assert_no_leak(caplog.text)


def test_runtime_with_an_llm_key_containing_whitespace_is_200_and_llm_not_configured(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _set_env(monkeypatch, {**VALID_LLM_ENV, "HOLOCRON_LLM_API_KEY": LLM_KEY_WITH_SPACE})
    with caplog.at_level(logging.WARNING):
        response = _runtime().get("/v1/runtime")
    assert response.status_code == 200
    assert response.json()["llm_configured"] is False
    _assert_no_leak(response.text)
    assert "HOLOCRON_LLM_API_KEY" in caplog.text
    _assert_no_leak(caplog.text)


@pytest.mark.parametrize(
    "bad",
    [
        {"HOLOCRON_LLM_BASE_URL": "http://gateway.example.test/v1"},
        {"HOLOCRON_LLM_TIMEOUT_S": "abc"},
        {"HOLOCRON_LLM_MODEL_EXTRACT": "secret-model-name\n0123"},
    ],
    ids=["plain-http", "timeout", "control-char"],
)
def test_runtime_with_other_invalid_llm_variables_is_200(monkeypatch: pytest.MonkeyPatch, bad: dict[str, str]) -> None:
    _set_env(monkeypatch, {**VALID_LLM_ENV, **bad})
    response = _runtime().get("/v1/runtime")
    assert response.status_code == 200 and response.json()["llm_configured"] is False
    _assert_no_leak(response.text)


def test_runtime_keeps_a_valid_llm_when_only_the_vlm_is_broken(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env(monkeypatch, {**VALID_LLM_ENV, **VLM_ENV_WITH_BAD_KEY})
    response = _runtime().get("/v1/runtime")
    assert response.status_code == 200
    assert response.json() == {"demo_mode": True, "llm_configured": True, "vlm_configured": False}
    _assert_no_leak(response.text)


# --------------------------------------------------------------------------- #
# POST /v1/vault/concept-pairs/propose
# --------------------------------------------------------------------------- #


def _assert_capability_409(response_text: str, status: int, detail: dict[str, str]) -> None:
    assert status == 409
    assert (detail["code"], detail["capability"], detail["reason"]) == (
        "CAPABILITY_UNAVAILABLE",
        "concept_pair_llm_proposal",
        "llm_not_configured",
    )
    _assert_no_leak(response_text)


def test_propose_with_an_llm_key_containing_whitespace_is_409(
    monkeypatch: pytest.MonkeyPatch,
    vault_app: tuple[TestClient, AppStore, str],
    recording_adapter: type[RecordingAdapter],
) -> None:
    client, store, chunk_id = vault_app
    _set_env(monkeypatch, {**VALID_LLM_ENV, "HOLOCRON_LLM_API_KEY": LLM_KEY_WITH_SPACE})
    response = client.post(PROPOSE_URL, json={"subject_code": "SSH1110", "chunk_ids": [chunk_id]})
    _assert_capability_409(response.text, response.status_code, response.json()["detail"])
    assert recording_adapter.built == [] and store.list_concept_pairs("SSH1110") == []


def test_propose_with_only_a_broken_vlm_key_is_409_not_500(
    monkeypatch: pytest.MonkeyPatch,
    vault_app: tuple[TestClient, AppStore, str],
    recording_adapter: type[RecordingAdapter],
) -> None:
    client, _, chunk_id = vault_app
    _set_env(monkeypatch, VLM_ENV_WITH_BAD_KEY)
    response = client.post(PROPOSE_URL, json={"subject_code": "SSH1110", "chunk_ids": [chunk_id]})
    _assert_capability_409(response.text, response.status_code, response.json()["detail"])
    assert recording_adapter.built == []


def test_propose_still_works_with_a_valid_llm_and_a_broken_vlm_key(
    monkeypatch: pytest.MonkeyPatch,
    vault_app: tuple[TestClient, AppStore, str],
    recording_adapter: type[RecordingAdapter],
) -> None:
    client, _, chunk_id = vault_app
    _set_env(monkeypatch, {**VALID_LLM_ENV, **VLM_ENV_WITH_BAD_KEY})
    response = client.post(PROPOSE_URL, json={"subject_code": "SSH1110", "chunk_ids": [chunk_id]})
    assert response.status_code == 200 and response.json() == {"pairs": [], "rejected_count": 0}
    assert recording_adapter.built == ["secret-model-name"]


# --------------------------------------------------------------------------- #
# Bộ nạp cấu hình
# --------------------------------------------------------------------------- #


def test_the_strict_loader_still_raises_for_scripts_and_tests() -> None:
    with pytest.raises(ConfigError, match="HOLOCRON_VLM_API_KEY"):
        load_runtime_config(VLM_ENV_WITH_BAD_KEY)
    with pytest.raises(ConfigError, match="HOLOCRON_LLM_API_KEY"):
        load_runtime_config({**VALID_LLM_ENV, "HOLOCRON_LLM_API_KEY": LLM_KEY_WITH_SPACE})


def test_the_lenient_loader_treats_each_broken_group_as_unconfigured() -> None:
    broken_vlm = load_runtime_config_lenient({**VALID_LLM_ENV, **VLM_ENV_WITH_BAD_KEY})
    assert broken_vlm.llm_configured and broken_vlm.vlm is None and broken_vlm.vlm_missing == VLM_REQUIRED_VARIABLES
    broken_llm = load_runtime_config_lenient({**VALID_LLM_ENV, "HOLOCRON_LLM_API_KEY": LLM_KEY_WITH_SPACE})
    assert not broken_llm.llm_configured and broken_llm.llm == LlmSettings()
    assert load_runtime_config_lenient(VALID_LLM_ENV) == load_runtime_config(VALID_LLM_ENV)


def test_the_lenient_loader_defaults_to_the_process_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env(monkeypatch, VALID_LLM_ENV)
    assert load_runtime_config_lenient().llm_configured
