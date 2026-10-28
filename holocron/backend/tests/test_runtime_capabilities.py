"""`GET /v1/runtime` trả `capabilities` đủ 12 khả năng theo thứ tự §5.12 (SPECIFICATION §5.12, §11.4, §15)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from holocron_core.domain.capabilities import CAPABILITY_NAMES, CapabilityName
from holocron_core.infrastructure.llm_config import load_runtime_config
from holocron_core.routers.api import app
from holocron_core.routers.deps import get_runtime_config
from holocron_core.services.capabilities import (
    IMPLEMENTED_CAPABILITIES,
    LLM_CAPABILITIES,
    VLM_CAPABILITIES,
    compute_capabilities,
)

NEW_NAMES: tuple[CapabilityName, ...] = ("concept_auto_proposal", "line_review_queue", "page_progress_eta")
FULL_ENV = {
    "HOLOCRON_LLM_BASE_URL": "https://gateway.example.test/v1",
    "HOLOCRON_LLM_API_KEY": "sk-test-0123456789-must-never-leak",
    "HOLOCRON_LLM_MODEL_EXTRACT": "secret-model-name",
    "HOLOCRON_VLM_BASE_URL": "https://vlm.example.test/v1",
    "HOLOCRON_VLM_API_KEY": "vlm-secret-value",
    "HOLOCRON_VLM_MODEL": "vision-model-x",
}


def test_the_three_new_names_are_last_and_in_no_implemented_or_provider_set() -> None:
    assert CAPABILITY_NAMES[-3:] == NEW_NAMES and len(CAPABILITY_NAMES) == 12
    for name in NEW_NAMES:
        assert name not in IMPLEMENTED_CAPABILITIES
        assert name not in LLM_CAPABILITIES
        assert name not in VLM_CAPABILITIES


def test_the_new_names_stay_not_implemented_even_with_full_llm_and_vlm_configuration() -> None:
    config = load_runtime_config(FULL_ENV)
    assert config.llm_configured and config.vlm is not None
    by_name = {capability.name: capability for capability in compute_capabilities(config)}
    for name in NEW_NAMES:
        assert (by_name[name].available, by_name[name].reason) == (False, "not_implemented")
        assert by_name[name].detail_vi


@pytest.mark.parametrize("env", [{}, FULL_ENV], ids=["offline", "llm-and-vlm"])
def test_runtime_endpoint_lists_twelve_capabilities_in_order(env: dict[str, str]) -> None:
    config = load_runtime_config(env)
    app.dependency_overrides[get_runtime_config] = lambda: config
    try:
        response = TestClient(app).get("/v1/runtime")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    capabilities = response.json()["capabilities"]
    assert [item["name"] for item in capabilities] == list(CAPABILITY_NAMES)
    assert len(capabilities) == 12
    by_name = {item["name"]: item for item in capabilities}
    for name in NEW_NAMES:
        assert (by_name[name]["available"], by_name[name]["reason"]) == (False, "not_implemented")
    for secret in ("sk-test-0123456789", "secret-model-name", "vlm-secret-value", "vision-model-x", "example.test"):
        assert secret not in response.text
