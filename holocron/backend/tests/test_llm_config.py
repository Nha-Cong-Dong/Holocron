"""Cấu hình LLM/VLM từ biến môi trường (spec §11.2): mặc định, ghi đè, bảo vệ khoá, chế độ demo. Không gọi mạng."""

import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from holocron_core.infrastructure.llm_config import (
    DEFAULT_TIMEOUT_S,
    ConfigError,
    RuntimeConfig,
    load_runtime_config,
)
from holocron_core.routers.api import app, get_extractor, get_runtime_config
from holocron_core.services.extraction import (
    DocumentExtractor,
    DualPassExtraction,
    MockDualPassExtractor,
    UploadedDocument,
)

SECRET = "sk-test-0123456789-must-never-leak"
VLM_SECRET = "vlm-test-9876543210-must-never-leak"
FULL_VLM_ENV = {
    "HOLOCRON_VLM_BASE_URL": "https://vlm.example.test/v1",
    "HOLOCRON_VLM_API_KEY": VLM_SECRET,
    "HOLOCRON_VLM_MODEL": "vision-model-x",
}
PACKAGE_ROOT = Path(__file__).resolve().parent.parent / "holocron_core"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test không được phụ thuộc môi trường thật của máy chạy."""
    import os

    for name in [n for n in os.environ if n.startswith(("HOLOCRON_LLM_", "HOLOCRON_VLM_"))]:
        monkeypatch.delenv(name)


def test_defaults_when_nothing_is_configured() -> None:
    config = load_runtime_config({})
    assert config.llm.base_url is None
    assert config.llm.model_extract is None and config.llm.model_author is None
    assert config.llm.timeout_s == DEFAULT_TIMEOUT_S == 60
    assert config.llm.api_key is None and not config.llm_configured
    assert config.vlm is None and config.demo_mode is True
    assert config.vlm_missing == ("HOLOCRON_VLM_BASE_URL", "HOLOCRON_VLM_API_KEY", "HOLOCRON_VLM_MODEL")


def test_every_variable_can_be_overridden() -> None:
    env = {
        "HOLOCRON_LLM_BASE_URL": "https://llm.example.test/v1",
        "HOLOCRON_LLM_API_KEY": SECRET,
        "HOLOCRON_LLM_MODEL_EXTRACT": "extract-model",
        "HOLOCRON_LLM_MODEL_AUTHOR": "author-model",
        "HOLOCRON_LLM_TIMEOUT_S": "12.5",
        **FULL_VLM_ENV,
    }
    config = load_runtime_config(env)
    assert config.llm.base_url == "https://llm.example.test/v1"
    assert (config.llm.model_extract, config.llm.model_author) == ("extract-model", "author-model")
    assert config.llm.timeout_s == 12.5
    assert config.llm.api_key is not None and config.llm.api_key.get_secret_value() == SECRET
    assert config.llm_configured
    assert config.vlm is not None and config.demo_mode is False and config.vlm_missing == ()
    assert config.vlm.model == "vision-model-x" and config.vlm.base_url == "https://vlm.example.test/v1"
    assert config.vlm.api_key.get_secret_value() == VLM_SECRET


def test_blank_values_count_as_unset_and_are_stripped() -> None:
    config = load_runtime_config(
        {"HOLOCRON_LLM_BASE_URL": "   ", "HOLOCRON_LLM_API_KEY": "", "HOLOCRON_LLM_MODEL_AUTHOR": "  coder  "}
    )
    assert config.llm.base_url is None
    assert config.llm.api_key is None
    assert config.llm.model_author == "coder"


def test_incomplete_vlm_configuration_falls_back_to_demo_and_names_what_is_missing() -> None:
    config = load_runtime_config({"HOLOCRON_VLM_BASE_URL": "https://vlm.example.test/v1", "HOLOCRON_VLM_MODEL": "m"})
    assert config.vlm is None and config.demo_mode is True
    assert config.vlm_missing == ("HOLOCRON_VLM_API_KEY",)


@pytest.mark.parametrize("raw", ["abc", "0", "-5", "nan", "inf"])
def test_invalid_timeout_is_rejected_without_echoing_secrets(raw: str) -> None:
    with pytest.raises(ConfigError, match="HOLOCRON_LLM_TIMEOUT_S") as excinfo:
        load_runtime_config({"HOLOCRON_LLM_TIMEOUT_S": raw, "HOLOCRON_LLM_API_KEY": SECRET})
    assert SECRET not in str(excinfo.value)


def test_default_source_is_the_process_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOLOCRON_LLM_MODEL_AUTHOR", "from-environ")
    assert load_runtime_config().llm.model_author == "from-environ"


def _all_text_forms(config: RuntimeConfig) -> list[str]:
    forms = [repr(config), str(config), repr(config.llm), config.model_dump_json(), str(config.model_dump())]
    if config.vlm is not None:
        forms += [repr(config.vlm), config.vlm.model_dump_json()]
    return forms


def test_api_keys_never_appear_in_repr_str_or_serialisation() -> None:
    config = load_runtime_config({"HOLOCRON_LLM_API_KEY": SECRET, **FULL_VLM_ENV})
    for text in _all_text_forms(config):
        assert SECRET not in text and VLM_SECRET not in text


def test_loading_the_configuration_does_not_log_secrets(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG):
        load_runtime_config({"HOLOCRON_LLM_API_KEY": SECRET, **FULL_VLM_ENV})
    assert SECRET not in caplog.text and VLM_SECRET not in caplog.text


def test_runtime_endpoint_reports_demo_mode_without_leaking_anything(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOLOCRON_LLM_API_KEY", SECRET)
    monkeypatch.setenv("HOLOCRON_LLM_BASE_URL", "https://llm.example.test/v1")
    monkeypatch.setenv("HOLOCRON_LLM_MODEL_EXTRACT", "secret-model-name")
    monkeypatch.setenv("HOLOCRON_LLM_MODEL_AUTHOR", "secret-model-name")
    response = TestClient(app).get("/v1/runtime")
    assert response.status_code == 200
    assert response.json() == {"demo_mode": True, "llm_configured": True, "vlm_configured": False}
    assert SECRET not in response.text and "secret-model-name" not in response.text


def test_runtime_endpoint_reflects_the_extractor_in_use_not_only_the_environment() -> None:
    """`demo_mode` đúng theo extractor đang chạy: có cấu hình VLM mà vẫn dùng Mock thì vẫn là chế độ demo."""

    class RealExtractor:
        def extract(self, document: UploadedDocument, content: bytes, topic_key: str) -> DualPassExtraction:
            raise NotImplementedError

    real: DocumentExtractor = RealExtractor()
    configured = load_runtime_config(FULL_VLM_ENV)
    app.dependency_overrides[get_runtime_config] = lambda: configured
    try:
        assert TestClient(app).get("/v1/runtime").json()["demo_mode"] is True  # vẫn là Mock
        assert isinstance(get_extractor(), MockDualPassExtractor)
        app.dependency_overrides[get_extractor] = lambda: real
        body = TestClient(app).get("/v1/runtime").json()
        assert body == {"demo_mode": False, "llm_configured": False, "vlm_configured": True}
    finally:
        app.dependency_overrides.clear()


def test_model_names_are_not_hard_coded_outside_the_config_module() -> None:
    offenders = [
        path.relative_to(PACKAGE_ROOT).as_posix()
        for layer in ("services", "routers", "domain", "sandbox")
        for path in (PACKAGE_ROOT / layer).rglob("*.py")
        if "deepseek" in path.read_text(encoding="utf-8").lower()
    ]
    assert not offenders, f"tên model hoặc nhà cung cấp bị hard-code: {offenders}"
