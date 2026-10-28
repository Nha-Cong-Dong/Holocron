"""`services/capabilities.compute_capabilities` (spec v0.6 §5.12, §11.4). Không gọi mạng, không đọc môi trường máy chạy.

`GET /v1/runtime` trả danh sách này (`routers/runtime.py`).
"""

import pytest
from pydantic import ValidationError

from holocron_core.domain.capabilities import CAPABILITY_NAMES, Capability
from holocron_core.infrastructure.llm_config import load_runtime_config, load_runtime_config_lenient
from holocron_core.services.capabilities import (
    IMPLEMENTED_CAPABILITIES,
    REASON_DETAILS_VI,
    compute_capabilities,
    unavailable_error,
)

SECRET = "sk-test-0123456789-must-never-leak"
LLM_ENV = {
    "HOLOCRON_LLM_API_KEY": SECRET,
    "HOLOCRON_LLM_BASE_URL": "https://gateway.example.test/v1",
    "HOLOCRON_LLM_MODEL_EXTRACT": "secret-model-name",
}
VLM_ENV = {
    "HOLOCRON_VLM_BASE_URL": "https://vlm.example.test/v1",
    "HOLOCRON_VLM_API_KEY": "vlm-secret-value",
    "HOLOCRON_VLM_MODEL": "vision-model-x",
}
EXPECTED_ORDER = (
    "ocr",
    "ocr_cross_check",
    "vault_search",
    "theory_generation",
    "concept_pair_llm_proposal",
    "essay_grading_offline",
    "essay_grading_llm",
    "template_authoring_llm",
    "vlm",
    "concept_auto_proposal",
    "line_review_queue",
    "page_progress_eta",
)


def _by_name(capabilities: list[Capability]) -> dict[str, Capability]:
    return {capability.name: capability for capability in capabilities}


def test_lists_all_twelve_capabilities_in_declaration_order() -> None:
    capabilities = compute_capabilities(load_runtime_config({}))
    assert tuple(capability.name for capability in capabilities) == EXPECTED_ORDER == CAPABILITY_NAMES


def test_without_llm_only_llm_capabilities_report_llm_not_configured() -> None:
    capabilities = _by_name(compute_capabilities(load_runtime_config({})))
    proposal = capabilities["concept_pair_llm_proposal"]
    assert (proposal.available, proposal.reason) == (False, "llm_not_configured")
    assert proposal.detail_vi is not None and "HOLOCRON_LLM_API_KEY" in proposal.detail_vi
    for name in ("vault_search", "theory_generation"):
        assert capabilities[name].available and capabilities[name].reason is None
        assert capabilities[name].detail_vi is None


def test_with_llm_the_concept_pair_proposal_is_available() -> None:
    capabilities = _by_name(compute_capabilities(load_runtime_config(LLM_ENV)))
    proposal = capabilities["concept_pair_llm_proposal"]
    assert proposal.available and proposal.reason is None and proposal.detail_vi is None


@pytest.mark.parametrize("missing", list(LLM_ENV))
@pytest.mark.parametrize("blank", [None, "", "   "], ids=["unset", "empty", "whitespace"])
def test_any_missing_llm_variable_makes_llm_capabilities_unavailable(missing: str, blank: str | None) -> None:
    """Không có nhà cung cấp mặc định (spec v0.6 §11.2): thiếu một trong ba biến là chưa cấu hình LLM."""
    env = {key: value for key, value in LLM_ENV.items() if key != missing}
    if blank is not None:
        env[missing] = blank
    every = set(CAPABILITY_NAMES)
    capabilities = _by_name(compute_capabilities(load_runtime_config(env), implemented=every))
    for name in ("concept_pair_llm_proposal", "essay_grading_llm", "template_authoring_llm"):
        assert (capabilities[name].available, capabilities[name].reason) == (False, "llm_not_configured")
    error = unavailable_error(list(capabilities.values()), "concept_pair_llm_proposal")
    assert error is not None and missing in error.message and SECRET not in error.message


def test_capabilities_without_code_on_this_build_are_not_implemented_even_when_configured() -> None:
    capabilities = _by_name(compute_capabilities(load_runtime_config({**LLM_ENV, **VLM_ENV})))
    for name in EXPECTED_ORDER:
        expected = None if name in IMPLEMENTED_CAPABILITIES else "not_implemented"
        assert capabilities[name].reason == expected, name
    assert capabilities["essay_grading_llm"].reason == "not_implemented"


def test_configuration_reasons_apply_once_the_code_exists() -> None:
    every = set(CAPABILITY_NAMES)
    unconfigured = _by_name(compute_capabilities(load_runtime_config({}), implemented=every))
    assert unconfigured["template_authoring_llm"].reason == "llm_not_configured"
    assert unconfigured["essay_grading_llm"].reason == "llm_not_configured"
    assert unconfigured["vlm"].reason == "vlm_not_configured"
    assert unconfigured["essay_grading_offline"].available
    configured = _by_name(compute_capabilities(load_runtime_config({**LLM_ENV, **VLM_ENV}), implemented=every))
    assert all(capability.available for capability in configured.values())


def test_local_checks_supply_the_remaining_reasons() -> None:
    capabilities = _by_name(
        compute_capabilities(
            load_runtime_config({}),
            implemented=set(CAPABILITY_NAMES),
            local_failures={
                "ocr": "ocr_engine_missing",
                "ocr_cross_check": "secondary_ocr_disabled",
                "concept_pair_llm_proposal": "ocr_language_missing",
            },
        )
    )
    assert capabilities["ocr"].reason == "ocr_engine_missing"
    assert capabilities["ocr"].detail_vi == REASON_DETAILS_VI["ocr_engine_missing"]
    assert capabilities["ocr_cross_check"].reason == "secondary_ocr_disabled"
    # Thiếu cấu hình được báo trước lỗi kiểm cục bộ.
    assert capabilities["concept_pair_llm_proposal"].reason == "llm_not_configured"


def test_nothing_reveals_keys_model_names_or_endpoints() -> None:
    configs = [load_runtime_config({}), load_runtime_config({**LLM_ENV, **VLM_ENV})]
    for config in configs:
        capabilities = compute_capabilities(config, implemented=set(CAPABILITY_NAMES))
        dumped = "".join(capability.model_dump_json() for capability in capabilities)
        for secret in (SECRET, "secret-model-name", "gateway.example.test", "vlm-secret-value", "vision-model-x"):
            assert secret not in dumped


def test_unavailable_error_is_the_409_detail_naming_the_variable_only() -> None:
    capabilities = compute_capabilities(load_runtime_config({}))
    error = unavailable_error(capabilities, "concept_pair_llm_proposal")
    assert error is not None
    assert error.model_dump() == {
        "code": "CAPABILITY_UNAVAILABLE",
        "capability": "concept_pair_llm_proposal",
        "reason": "llm_not_configured",
        "message": error.message,
    }
    assert "HOLOCRON_LLM_API_KEY" in error.message
    assert unavailable_error(compute_capabilities(load_runtime_config(LLM_ENV)), "concept_pair_llm_proposal") is None


@pytest.mark.parametrize(
    ("available", "reason"), [(True, "not_implemented"), (False, None)], ids=["available-with-reason", "missing"]
)
def test_capability_reason_is_null_iff_available(available: bool, reason: str | None) -> None:
    with pytest.raises(ValidationError, match="reason"):
        Capability(name="ocr", available=available, reason=reason, detail_vi=None)


@pytest.mark.parametrize(
    "broken",
    [
        {"HOLOCRON_LLM_API_KEY": "sk-test 0123"},
        {"HOLOCRON_LLM_BASE_URL": "http://gateway.example.test/v1"},
    ],
    ids=["key-with-space", "plain-http"],
)
def test_invalid_llm_or_vlm_configuration_counts_as_not_configured(broken: dict[str, str]) -> None:
    """Cách `routers/deps.get_runtime_config` nạp cấu hình: nhóm có biến sai coi như chưa cấu hình, không ném lỗi."""
    config = load_runtime_config_lenient({**LLM_ENV, **broken, **VLM_ENV, "HOLOCRON_VLM_API_KEY": "abc def"})
    capabilities = _by_name(compute_capabilities(config, implemented=CAPABILITY_NAMES))
    for name in ("concept_pair_llm_proposal", "essay_grading_llm", "template_authoring_llm"):
        assert capabilities[name].reason == "llm_not_configured"
    assert capabilities["vlm"].reason == "vlm_not_configured"
    error = unavailable_error(list(capabilities.values()), "concept_pair_llm_proposal")
    assert error is not None and "0123" not in error.message and "abc def" not in error.message
