"""Kết nối AI tối thiểu của V02: công tắc đồng ý, nhà cung cấp env, chọn nhà cung cấp và sổ chi tiêu (spec §5.16)."""

from __future__ import annotations

import logging
import threading

import pytest
from pydantic import SecretStr

from holocron_core.domain.ai import (
    TEMPLATE_DEFAULT_PRICES,
    AiUsage,
    CostEstimate,
    ProviderCandidate,
    ProviderModels,
    ProviderPrices,
    ai_cache_key,
    estimate_cost_usd,
)
from holocron_core.infrastructure.ai_runtime import (
    EnvAiConsent,
    EnvProviderSource,
    InMemorySpendLedger,
    load_env_vlm_provider,
    read_ai_consent,
)
from holocron_core.infrastructure.llm_config import ConfigError
from holocron_core.services.provider_selection import ProviderSelection

SECRET = "sk-vision-secret-123456"
VLM_ENV = {
    "HOLOCRON_VLM_BASE_URL": "https://vision.example.test/v1",
    "HOLOCRON_VLM_API_KEY": SECRET,
    "HOLOCRON_VLM_MODEL": "vision-model-x",
}
ESTIMATE = CostEstimate(input_tokens=1000, max_output_tokens=1000, images=1)


# --------------------------------------------------------------------------- #
# Công tắc đồng ý
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("env", "image", "text"),
    [
        ({}, False, False),
        ({"HOLOCRON_AI_CONSENT": "0"}, False, False),
        ({"HOLOCRON_AI_CONSENT": "1"}, True, True),
        ({"HOLOCRON_AI_CONSENT": "1", "HOLOCRON_VLM_MODE": "off"}, False, False),
        ({"HOLOCRON_AI_CONSENT": "1", "HOLOCRON_VLM_MODE": "images_only"}, True, False),
        ({"HOLOCRON_AI_CONSENT": "yes"}, False, False),
        ({"HOLOCRON_AI_CONSENT": "1", "HOLOCRON_VLM_MODE": "bogus"}, False, False),
    ],
)
def test_consent_off_blocks_all_auto_sends(env: dict[str, str], image: bool, text: bool) -> None:
    consent = EnvAiConsent(lambda: env)
    assert consent.auto_send_allowed("image") is image
    assert consent.auto_send_allowed("text") is text


def test_consent_is_read_on_every_call() -> None:
    env: dict[str, str] = {}
    consent = EnvAiConsent(lambda: env)
    assert not consent.auto_send_allowed("image")
    env["HOLOCRON_AI_CONSENT"] = "1"
    assert consent.auto_send_allowed("image")
    env["HOLOCRON_AI_CONSENT"] = "0"
    assert not consent.auto_send_allowed("image")


@pytest.mark.parametrize("raw", ["true", "on", "2", "1 0"])
def test_invalid_consent_is_a_config_error_naming_only_the_variable(raw: str) -> None:
    with pytest.raises(ConfigError, match="HOLOCRON_AI_CONSENT") as excinfo:
        read_ai_consent({"HOLOCRON_AI_CONSENT": raw})
    assert raw not in str(excinfo.value).replace("HOLOCRON_AI_CONSENT", "").replace("1 hoặc 0", "")


def test_blank_consent_counts_as_unset() -> None:
    assert read_ai_consent({"HOLOCRON_AI_CONSENT": "  "}) is None


# --------------------------------------------------------------------------- #
# Nhà cung cấp env
# --------------------------------------------------------------------------- #


def test_env_vlm_provider_defaults_to_custom_template_prices_and_budget() -> None:
    provider = load_env_vlm_provider(VLM_ENV)
    assert provider is not None
    assert (provider.provider_id, provider.template, provider.priority) == ("env_vlm", "custom", 0)
    assert provider.prices == TEMPLATE_DEFAULT_PRICES["custom"] and provider.budget_usd == 1.0
    assert provider.models.vlm == "vision-model-x" and provider.models.llm is None
    assert provider.ip_policy == "operator" and not provider.local
    assert provider.label == "nhà cung cấp AI đã cấu hình"


def test_env_vlm_provider_reads_template_budget_and_prices() -> None:
    env = {
        **VLM_ENV,
        "HOLOCRON_VLM_TEMPLATE": "gemini",
        "HOLOCRON_VLM_BUDGET_USD": "2.5",
        "HOLOCRON_VLM_PRICE_PER_IMAGE": "0.001",
        "HOLOCRON_VLM_DATA_RECIPIENT": "Google",
    }
    provider = load_env_vlm_provider(env)
    assert provider is not None
    assert provider.template == "gemini" and provider.budget_usd == 2.5 and provider.label == "Google"
    gemini = TEMPLATE_DEFAULT_PRICES["gemini"]
    assert provider.prices.source == "env" and provider.prices.per_image_usd == 0.001
    assert provider.prices.input_per_1k_usd == gemini.input_per_1k_usd


def test_gemini_template_default_budget_is_five_usd() -> None:
    provider = load_env_vlm_provider({**VLM_ENV, "HOLOCRON_VLM_TEMPLATE": "gemini"})
    assert provider is not None and provider.budget_usd == 5.0


@pytest.mark.parametrize(
    ("name", "raw"),
    [
        ("HOLOCRON_VLM_TEMPLATE", "openai"),
        ("HOLOCRON_VLM_BUDGET_USD", "-1"),
        ("HOLOCRON_VLM_BUDGET_USD", "1001"),
        ("HOLOCRON_VLM_BUDGET_USD", "nan"),
        ("HOLOCRON_VLM_PRICE_IN_PER_1K", "2"),
        ("HOLOCRON_VLM_PRICE_OUT_PER_1K", "inf"),
        ("HOLOCRON_VLM_PRICE_PER_IMAGE", "abc"),
    ],
)
def test_invalid_provider_variable_is_config_error_and_lenient_source_drops_it(
    name: str, raw: str, caplog: pytest.LogCaptureFixture
) -> None:
    env = {**VLM_ENV, name: raw}
    with pytest.raises(ConfigError, match=name):
        load_env_vlm_provider(env)
    with caplog.at_level(logging.WARNING):
        assert EnvProviderSource(lambda: env)() == []
    assert name in caplog.text and SECRET not in caplog.text


def test_unconfigured_vlm_has_no_provider() -> None:
    assert EnvProviderSource(dict)() == []


def test_loopback_without_key_is_a_provider() -> None:
    env = {"HOLOCRON_VLM_BASE_URL": "http://127.0.0.1:8080/v1", "HOLOCRON_VLM_MODEL": "local"}
    provider = load_env_vlm_provider(env)
    assert provider is not None and provider.local and provider.api_key is None


def test_provider_repr_hides_key_endpoint_and_model() -> None:
    provider = load_env_vlm_provider(VLM_ENV)
    assert provider is not None
    for text in (repr(provider), str(provider)):
        assert SECRET not in text and "vision.example.test" not in text and "vision-model-x" not in text


# --------------------------------------------------------------------------- #
# Chọn nhà cung cấp và sổ chi tiêu
# --------------------------------------------------------------------------- #


def _candidate(provider_id: str, *, priority: int, budget: float, vlm: str | None = "m") -> ProviderCandidate:
    return ProviderCandidate(
        provider_id=provider_id,
        template="custom",
        label="x",
        base_url="https://p.example.test/v1",
        api_key=None if provider_id == "keyless" else SecretStr("k" * 16),
        models=ProviderModels(vlm=vlm),
        local=False,
        ip_policy="public_only",
        priority=priority,
        budget_usd=budget,
        prices=ProviderPrices(input_per_1k_usd=0.0, output_per_1k_usd=0.0, per_image_usd=0.1, source="user"),
        budget_group=provider_id,
    )


def test_selection_follows_priority_then_id_and_skips_unusable() -> None:
    providers = [
        _candidate("usr_b", priority=5, budget=10),
        _candidate("usr_a", priority=5, budget=10),
        _candidate("usr_c", priority=1, budget=10, vlm=None),
        _candidate("keyless", priority=0, budget=10),
    ]
    selection = ProviderSelection(lambda: providers, InMemorySpendLedger())
    chosen = selection.select("vlm", ESTIMATE)
    assert chosen is not None and chosen.provider_id == "usr_a"
    assert selection.select("llm", ESTIMATE) is None


def test_budget_exhausted_falls_to_next_provider_then_offline() -> None:
    providers = [_candidate("usr_a", priority=1, budget=0.1), _candidate("usr_b", priority=2, budget=0.1)]
    ledger = InMemorySpendLedger()
    selection = ProviderSelection(lambda: providers, ledger)
    first = selection.select("vlm", ESTIMATE)
    assert first is not None and first.provider_id == "usr_a"
    selection.record("usr_a", AiUsage(input_tokens=0, output_tokens=0, images=1, estimated=False), reserved=ESTIMATE)
    assert ledger.spent_usd("usr_a") == pytest.approx(0.1) and ledger.reserved_usd("usr_a") == 0.0
    second = selection.select("vlm", ESTIMATE)
    assert second is not None and second.provider_id == "usr_b"
    assert selection.select("vlm", ESTIMATE) is None  # usr_b đang giữ chỗ hết trần
    assert not selection.budget_exhausted("vlm")
    selection.record("usr_b", AiUsage(input_tokens=0, output_tokens=0, images=1, estimated=False), reserved=ESTIMATE)
    assert selection.budget_exhausted("vlm")


def test_parallel_selection_never_exceeds_budget() -> None:
    budget = 1.0
    providers = [_candidate("usr_a", priority=1, budget=budget)]
    ledger = InMemorySpendLedger()
    selection = ProviderSelection(lambda: providers, ledger)
    granted: list[int] = []
    barrier = threading.Barrier(32)
    lock = threading.Lock()

    def call() -> None:
        barrier.wait()
        chosen = selection.select("vlm", ESTIMATE)
        if chosen is None:
            return
        with lock:
            granted.append(1)
        selection.record(
            chosen.provider_id, AiUsage(input_tokens=0, output_tokens=0, images=1, estimated=False), reserved=ESTIMATE
        )

    threads = [threading.Thread(target=call) for _ in range(32)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    per_call = estimate_cost_usd(providers[0].prices, ESTIMATE)
    assert len(granted) == int(budget / per_call + 1e-9)
    assert ledger.spent_usd("usr_a") <= budget + 1e-9


def test_ai_cache_key_changes_with_every_component() -> None:
    sha = "a" * 64
    base = ai_cache_key("vlm_region", sha, "m", "v1")
    assert len(base) == 64
    assert base != ai_cache_key("vlm_region", sha, "m2", "v1")
    assert base != ai_cache_key("vlm_region", sha, "m", "v2")
    assert base != ai_cache_key("structure_proposal", sha, "m", "v1")
    assert base != ai_cache_key("vlm_region", "b" * 64, "m", "v1")
