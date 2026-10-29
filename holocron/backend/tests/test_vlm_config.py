"""Cấu hình VLM của ADR-009 mục 4, 6, 7 (`infrastructure/llm_config.py`; spec §11.2). Không gọi mạng.

Khoá trống với loopback, mặc định theo loopback/cloud, `HOLOCRON_VLM_MODE` lưu riêng, nhãn bên nhận dữ liệu và các
nhánh lỗi cấu hình (thông điệp chỉ nêu tên biến; bộ nạp lenient coi VLM như chưa cấu hình).
"""

from __future__ import annotations

import logging

import pytest

from holocron_core.infrastructure.llm_config import (
    VLM_REQUIRED_VARIABLES,
    ConfigError,
    load_runtime_config,
    load_runtime_config_lenient,
)

SECRET = "sk-vlm-0123456789-must-never-leak"
CLOUD_URL = "https://vision.example.test/v1"
LOOPBACK_URLS = ["http://127.0.0.1:11434/v1", "http://localhost:1234/v1", "http://[::1]:8080/v1"]
MODEL = "vision-model-under-test"
CLOUD_ENV = {"HOLOCRON_VLM_BASE_URL": CLOUD_URL, "HOLOCRON_VLM_API_KEY": SECRET, "HOLOCRON_VLM_MODEL": MODEL}
LOOPBACK_ENV = {"HOLOCRON_VLM_BASE_URL": LOOPBACK_URLS[0], "HOLOCRON_VLM_MODEL": MODEL}


# --------------------------------------------------------------------------- #
# Đã cấu hình hay chưa: loopback được trống khoá, cloud thì không
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("url", LOOPBACK_URLS)
def test_loopback_endpoint_is_configured_without_a_key(url: str) -> None:
    config = load_runtime_config({"HOLOCRON_VLM_BASE_URL": url, "HOLOCRON_VLM_MODEL": MODEL})
    assert config.vlm is not None and config.vlm.api_key is None
    assert config.vlm_missing == ()
    assert config.vlm.local and config.vlm_local


@pytest.mark.parametrize("blank", ["", "   ", "\t"])
def test_cloud_endpoint_with_a_blank_key_is_unconfigured_not_an_error(blank: str) -> None:
    config = load_runtime_config({**CLOUD_ENV, "HOLOCRON_VLM_API_KEY": blank})
    assert config.vlm is None and not config.vlm_local
    assert config.vlm_missing == ("HOLOCRON_VLM_API_KEY",)


def test_without_a_base_url_the_key_still_counts_as_required() -> None:
    assert load_runtime_config({}).vlm_missing == VLM_REQUIRED_VARIABLES
    only_model = load_runtime_config({"HOLOCRON_VLM_MODEL": MODEL})
    assert only_model.vlm is None
    assert only_model.vlm_missing == ("HOLOCRON_VLM_BASE_URL", "HOLOCRON_VLM_API_KEY")


def test_loopback_without_a_model_reports_only_the_model() -> None:
    config = load_runtime_config({"HOLOCRON_VLM_BASE_URL": LOOPBACK_URLS[0]})
    assert config.vlm is None and config.vlm_missing == ("HOLOCRON_VLM_MODEL",)


def test_a_host_that_only_looks_like_loopback_still_needs_a_key() -> None:
    config = load_runtime_config(
        {"HOLOCRON_VLM_BASE_URL": "https://127.0.0.1.example.test/v1", "HOLOCRON_VLM_MODEL": "m"}
    )
    assert config.vlm is None and config.vlm_missing == ("HOLOCRON_VLM_API_KEY",)


# --------------------------------------------------------------------------- #
# Mặc định theo loại endpoint và giá trị đặt tay
# --------------------------------------------------------------------------- #


def test_cloud_defaults() -> None:
    config = load_runtime_config(CLOUD_ENV)
    assert config.vlm is not None
    assert (config.vlm.timeout_s, config.vlm.concurrency) == (60.0, 2)
    assert (config.vlm.max_edge_px, config.vlm.max_image_bytes) == (2048, 4 * 1024 * 1024)
    assert not config.vlm_local and config.vlm_data_recipient is None


def test_loopback_defaults() -> None:
    config = load_runtime_config(LOOPBACK_ENV)
    assert config.vlm is not None
    assert (config.vlm.timeout_s, config.vlm.concurrency) == (120.0, 1)
    assert (config.vlm.max_edge_px, config.vlm.max_image_bytes) == (2048, 4 * 1024 * 1024)


@pytest.mark.parametrize("base", [CLOUD_ENV, LOOPBACK_ENV], ids=["cloud", "loopback"])
def test_explicit_values_override_both_defaults(base: dict[str, str]) -> None:
    env = {
        **base,
        "HOLOCRON_VLM_TIMEOUT_S": " 30.5 ",
        "HOLOCRON_VLM_CONCURRENCY": "8",
        "HOLOCRON_VLM_MAX_EDGE_PX": "1024",
        "HOLOCRON_VLM_MAX_IMAGE_BYTES": "1000",
    }
    config = load_runtime_config(env)
    assert config.vlm is not None
    assert (config.vlm.timeout_s, config.vlm.concurrency) == (30.5, 8)
    assert (config.vlm.max_edge_px, config.vlm.max_image_bytes) == (1024, 1000)
    lowest = load_runtime_config({**base, "HOLOCRON_VLM_CONCURRENCY": "1"})
    assert lowest.vlm is not None and lowest.vlm.concurrency == 1


def test_blank_tuning_variables_count_as_unset() -> None:
    blanks = {
        name: "  "
        for name in (
            "HOLOCRON_VLM_MODE",
            "HOLOCRON_VLM_TIMEOUT_S",
            "HOLOCRON_VLM_CONCURRENCY",
            "HOLOCRON_VLM_MAX_EDGE_PX",
            "HOLOCRON_VLM_MAX_IMAGE_BYTES",
            "HOLOCRON_VLM_DATA_RECIPIENT",
        )
    }
    assert load_runtime_config({**CLOUD_ENV, **blanks}) == load_runtime_config(CLOUD_ENV)


# --------------------------------------------------------------------------- #
# HOLOCRON_VLM_MODE: lưu riêng, không đổi việc VLM đã cấu hình
# --------------------------------------------------------------------------- #


def test_mode_defaults_to_off_and_does_not_affect_configuration() -> None:
    off = load_runtime_config(CLOUD_ENV)
    on = load_runtime_config({**CLOUD_ENV, "HOLOCRON_VLM_MODE": "images_only"})
    assert off.vlm_mode == "off" and on.vlm_mode == "images_only"
    assert off.vlm is not None and off.vlm == on.vlm and off.vlm_missing == on.vlm_missing == ()
    unconfigured = load_runtime_config({"HOLOCRON_VLM_MODE": "images_only"})
    assert unconfigured.vlm is None and unconfigured.vlm_mode == "images_only"
    assert load_runtime_config({"HOLOCRON_VLM_MODE": " off "}).vlm_mode == "off"


@pytest.mark.parametrize("raw", ["on", "true", "IMAGES_ONLY", "images", "images_only,text", "offline"])
def test_unknown_mode_is_a_config_error_naming_only_the_variable(raw: str) -> None:
    with pytest.raises(ConfigError, match="HOLOCRON_VLM_MODE") as excinfo:
        load_runtime_config({**CLOUD_ENV, "HOLOCRON_VLM_MODE": raw})
    # Thông điệp cố định, không phụ thuộc giá trị (giá trị có thể trùng một phần với "off"/"images_only").
    assert str(excinfo.value) == "HOLOCRON_VLM_MODE không hợp lệ: chỉ nhận off hoặc images_only"


# --------------------------------------------------------------------------- #
# Biến số sai ⇒ ConfigError chỉ nêu tên biến, không kẹp giá trị
# --------------------------------------------------------------------------- #

BAD_NUMBERS = [
    ("HOLOCRON_VLM_TIMEOUT_S", "0"),
    ("HOLOCRON_VLM_TIMEOUT_S", "-1"),
    ("HOLOCRON_VLM_TIMEOUT_S", "nan"),
    ("HOLOCRON_VLM_TIMEOUT_S", "inf"),
    ("HOLOCRON_VLM_TIMEOUT_S", "abc"),
    ("HOLOCRON_VLM_CONCURRENCY", "0"),
    ("HOLOCRON_VLM_CONCURRENCY", "9"),
    ("HOLOCRON_VLM_CONCURRENCY", "1.5"),
    ("HOLOCRON_VLM_CONCURRENCY", "abc"),
    ("HOLOCRON_VLM_CONCURRENCY", "-1"),
    ("HOLOCRON_VLM_CONCURRENCY", "+2"),
    ("HOLOCRON_VLM_CONCURRENCY", "٣"),  # chữ số Ả Rập-Ấn 3: `int()` nhận nhưng không phải chữ số ASCII
    ("HOLOCRON_VLM_MAX_EDGE_PX", "0"),
    ("HOLOCRON_VLM_MAX_EDGE_PX", "-2048"),
    ("HOLOCRON_VLM_MAX_EDGE_PX", "2048.0"),
    ("HOLOCRON_VLM_MAX_EDGE_PX", "2e3"),
    ("HOLOCRON_VLM_MAX_EDGE_PX", "abc"),
    ("HOLOCRON_VLM_MAX_IMAGE_BYTES", "0"),
    ("HOLOCRON_VLM_MAX_IMAGE_BYTES", "-1"),
    ("HOLOCRON_VLM_MAX_IMAGE_BYTES", "4_194_304"),
    ("HOLOCRON_VLM_MAX_IMAGE_BYTES", "4MiB"),
]


@pytest.mark.parametrize(("name", "raw"), BAD_NUMBERS, ids=[f"{name}={raw!r}" for name, raw in BAD_NUMBERS])
@pytest.mark.parametrize("base", [CLOUD_ENV, LOOPBACK_ENV, {}], ids=["cloud", "loopback", "unconfigured"])
def test_invalid_numbers_are_config_errors_naming_only_the_variable(name: str, raw: str, base: dict[str, str]) -> None:
    with pytest.raises(ConfigError, match=name) as excinfo:
        load_runtime_config({**base, name: raw})
    message = str(excinfo.value)
    assert SECRET not in message and "vision.example.test" not in message
    assert f"'{raw}'" not in message and f"={raw}" not in message


# --------------------------------------------------------------------------- #
# HOLOCRON_VLM_DATA_RECIPIENT
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "label", ["OpenAI", "Máy chủ AI của trường", "Nhà cung cấp A (Gemini)", "Ollama.", "x" * 80, "Bản 1. Thử"]
)
def test_plain_text_recipient_labels_are_kept(label: str) -> None:
    config = load_runtime_config({**CLOUD_ENV, "HOLOCRON_VLM_DATA_RECIPIENT": f"  {label}  "})
    assert config.vlm is not None and config.vlm.data_recipient == label
    assert config.vlm_data_recipient == label


BAD_RECIPIENTS = {
    "too-long": "x" * 81,
    "https-url": "https://api.example.test",
    "scheme-in-text": "Gửi tới ftp://files",
    "www": "www.example",
    "www-uppercase": "WWW.Example",
    "domain": "api.openai.com",
    "domain-with-path": "vision.example.test/v1",
    "ip-address": "127.0.0.1",
    "version-without-space": "v1.5",
    "newline": "OpenAI\nX-Injected: 1",
    "contains-the-key": f"Nhà cung cấp {SECRET}",
}


@pytest.mark.parametrize("label", list(BAD_RECIPIENTS.values()), ids=list(BAD_RECIPIENTS))
def test_invalid_recipient_labels_are_config_errors_naming_only_the_variable(label: str) -> None:
    with pytest.raises(ConfigError, match="HOLOCRON_VLM_DATA_RECIPIENT") as excinfo:
        load_runtime_config({**CLOUD_ENV, "HOLOCRON_VLM_DATA_RECIPIENT": label})
    message = str(excinfo.value)
    assert label not in message and SECRET not in message


def test_recipient_is_only_reported_when_vlm_is_configured() -> None:
    config = load_runtime_config({"HOLOCRON_VLM_DATA_RECIPIENT": "OpenAI"})
    assert config.vlm is None and config.vlm_data_recipient is None and not config.vlm_local


# --------------------------------------------------------------------------- #
# Bộ nạp lenient: biến VLM mới sai ⇒ VLM chưa cấu hình, không ném
# --------------------------------------------------------------------------- #

LENIENT_CASES = [
    ("HOLOCRON_VLM_MODE", "on"),
    ("HOLOCRON_VLM_TIMEOUT_S", "nan"),
    ("HOLOCRON_VLM_CONCURRENCY", "9"),
    ("HOLOCRON_VLM_MAX_EDGE_PX", "0"),
    ("HOLOCRON_VLM_MAX_IMAGE_BYTES", "abc"),
    ("HOLOCRON_VLM_DATA_RECIPIENT", "api.example.test"),
]


@pytest.mark.parametrize(("name", "raw"), LENIENT_CASES, ids=[name for name, _ in LENIENT_CASES])
def test_lenient_loader_treats_a_broken_new_variable_as_vlm_unconfigured(
    name: str, raw: str, caplog: pytest.LogCaptureFixture
) -> None:
    llm_env = {
        "HOLOCRON_LLM_BASE_URL": "https://llm.example.test/v1",
        "HOLOCRON_LLM_API_KEY": "sk-llm-abc",
        "HOLOCRON_LLM_MODEL_EXTRACT": "m",
    }
    env = {**llm_env, **CLOUD_ENV, "HOLOCRON_VLM_MODE": "images_only", name: raw}
    with caplog.at_level(logging.WARNING):
        config = load_runtime_config_lenient(env)
    assert config.vlm is None and config.vlm_missing == VLM_REQUIRED_VARIABLES
    assert config.vlm_mode == "off" and not config.vlm_local and config.vlm_data_recipient is None
    assert config.llm_configured
    assert name in caplog.text
    assert SECRET not in caplog.text and "vision.example.test" not in caplog.text and MODEL not in caplog.text


def test_lenient_loader_matches_the_strict_one_on_valid_vlm_configuration() -> None:
    env = {**LOOPBACK_ENV, "HOLOCRON_VLM_MODE": "images_only", "HOLOCRON_VLM_DATA_RECIPIENT": "Máy này"}
    assert load_runtime_config_lenient(env) == load_runtime_config(env)


# --------------------------------------------------------------------------- #
# Không lộ khoá hay endpoint qua repr
# --------------------------------------------------------------------------- #


def test_repr_of_vlm_settings_hides_key_endpoint_and_model() -> None:
    config = load_runtime_config({**CLOUD_ENV, "HOLOCRON_VLM_DATA_RECIPIENT": "OpenAI"})
    assert config.vlm is not None
    for text in (repr(config.vlm), str(config.vlm), repr(config)):
        assert SECRET not in text and "vision.example.test" not in text and MODEL not in text
