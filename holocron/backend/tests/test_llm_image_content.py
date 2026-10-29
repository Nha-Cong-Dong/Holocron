"""Nội dung nhiều phần (text + ảnh PNG) của `ChatMessage` và client VLM (ADR-009 mục 4; spec §11.5). KHÔNG gọi mạng.

Mọi request đi qua transport giả; fixture `_no_network` chặn kết nối socket để test hỏng ngay nếu có đường nào lọt ra
mạng thật.
"""

from __future__ import annotations

import base64
import http.client
import json
import logging
import socket
import traceback
import urllib.request

import pytest
from pydantic import SecretStr, ValidationError

from holocron_core.domain.llm import (
    PNG_MAGIC,
    ChatMessage,
    ImageTooLargeError,
    ImageUrl,
    ImageUrlPart,
    LlmUpstreamError,
    TextPart,
)
from holocron_core.infrastructure.llm_client import USER_AGENT, OpenAiChatClient
from holocron_core.infrastructure.llm_config import (
    ConfigError,
    VlmSettings,
    is_loopback_url,
    load_runtime_config,
    load_runtime_config_lenient,
)

SECRET = "sk-vlm-0123456789-must-never-leak"
CLOUD_URL = "https://vision.example.test/v1"
LOOPBACK_URL = "http://127.0.0.1:11434/v1"
MODEL = "vision-model-under-test"
PROMPT = "Chép lại nội dung trang."
TEXT_MESSAGES = [ChatMessage(role="system", content="Chỉ dẫn"), ChatMessage(role="user", content="Văn bản chunk")]


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("test không được gọi mạng")

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(http.client.HTTPConnection, "connect", refuse)


def _png(size: int = 64) -> bytes:
    """Byte bắt đầu bằng magic PNG, đủ `size` byte (domain chỉ kiểm magic; IHDR do bước render kiểm)."""
    return PNG_MAGIC + b"\x00" * max(0, size - len(PNG_MAGIC))


def _data_uri(raw: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(raw).decode("ascii")


def _image_message(raw: bytes | None = None) -> ChatMessage:
    url = _data_uri(_png() if raw is None else raw)
    return ChatMessage(
        role="user",
        content=[TextPart(text=PROMPT), ImageUrlPart(image_url=ImageUrl(url=url))],
    )


def _ok_body(content: str = "[]") -> bytes:
    return json.dumps({"choices": [{"index": 0, "message": {"role": "assistant", "content": content}}]}).encode()


class FakeClock:
    """Đồng hồ đơn điệu giả; `sleep` làm đồng hồ tiến, không chờ thật."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class RecordingTransport:
    """Ghi lại request và timeout; mỗi lần gửi làm đồng hồ tiến `spend` giây rồi trả kết quả định sẵn."""

    def __init__(self, clock: FakeClock, *outcomes: tuple[int, bytes], spend: float = 0.0) -> None:
        self.clock = clock
        self.outcomes = list(outcomes)
        self.spend = spend
        self.requests: list[urllib.request.Request] = []
        self.timeouts: list[float] = []

    def __call__(self, request: urllib.request.Request, timeout_s: float) -> tuple[int, bytes]:
        self.requests.append(request)
        self.timeouts.append(timeout_s)
        self.clock.advance(self.spend)
        return self.outcomes.pop(0)


def _vlm_settings(**overrides: object) -> VlmSettings:
    values: dict[str, object] = {"base_url": CLOUD_URL, "api_key": SecretStr(SECRET), "model": MODEL}
    values.update(overrides)
    return VlmSettings.model_validate(values)


def _body(request: urllib.request.Request) -> dict[str, object]:
    assert isinstance(request.data, bytes)
    decoded = json.loads(request.data.decode("utf-8"))
    assert isinstance(decoded, dict)
    return decoded


# --------------------------------------------------------------------------- #
# Payload chỉ có văn bản: giữ y hệt từng byte
# --------------------------------------------------------------------------- #


def test_text_only_payload_is_byte_identical_to_the_previous_format() -> None:
    client = OpenAiChatClient(base_url=CLOUD_URL, api_key=SecretStr(SECRET), model=MODEL, timeout_s=1.0)
    request = client.build_request(TEXT_MESSAGES, max_tokens=321)
    expected = {
        "model": MODEL,
        "messages": [{"role": "system", "content": "Chỉ dẫn"}, {"role": "user", "content": "Văn bản chunk"}],
        "max_tokens": 321,
    }
    assert request.data == json.dumps(expected, ensure_ascii=False).encode("utf-8")
    assert request.get_header("Authorization") == f"Bearer {SECRET}"
    assert request.get_header("User-agent") == USER_AGENT


def test_string_content_still_validates_and_dumps_as_a_string() -> None:
    message = ChatMessage.model_validate({"role": "user", "content": "xin chào"})
    assert message.content == "xin chào" and message.image_parts() == []
    assert message.model_dump() == {"role": "user", "content": "xin chào"}


# --------------------------------------------------------------------------- #
# Payload có ảnh: đúng schema OpenAI Chat Completions
# --------------------------------------------------------------------------- #


def test_image_payload_follows_the_openai_content_parts_schema() -> None:
    raw = _png(100)
    client = OpenAiChatClient.from_vlm_settings(_vlm_settings())
    body = _body(client.build_request([_image_message(raw)], max_tokens=4096))
    assert set(body) == {"model", "messages", "max_tokens"}
    assert "temperature" not in body and "top_p" not in body and "response_format" not in body
    assert body["messages"] == [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": PROMPT},
                {"type": "image_url", "image_url": {"url": _data_uri(raw)}},
            ],
        }
    ]


def test_content_parts_are_parsed_by_their_type_discriminator() -> None:
    url = _data_uri(_png())
    message = ChatMessage.model_validate(
        {
            "role": "user",
            "content": [{"type": "text", "text": "a"}, {"type": "image_url", "image_url": {"url": url}}],
        }
    )
    assert isinstance(message.content, list)
    assert isinstance(message.content[0], TextPart) and isinstance(message.content[1], ImageUrlPart)
    assert [part.image_url.url for part in message.image_parts()] == [url]
    with pytest.raises(ValidationError):
        ChatMessage.model_validate({"role": "user", "content": [{"type": "audio", "text": "a"}]})
    with pytest.raises(ValidationError):
        ChatMessage.model_validate({"role": "user", "content": [{"type": "image_url", "text": "a"}]})


# --------------------------------------------------------------------------- #
# Ảnh sai dạng ⇒ ValidationError trước khi gửi
# --------------------------------------------------------------------------- #

_VALID_B64 = base64.b64encode(_png()).decode("ascii")
_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 60

BAD_IMAGE_URLS = {
    "jpeg-prefix": "data:image/jpeg;base64," + _VALID_B64,
    "uppercase-prefix": "DATA:IMAGE/PNG;BASE64," + _VALID_B64,
    "no-base64-marker": "data:image/png," + _VALID_B64,
    "http-url": "https://images.example.test/page.png",
    "empty-payload": "data:image/png;base64,",
    "bad-alphabet": "data:image/png;base64," + _VALID_B64[:-4] + "@@@@",
    "missing-padding": "data:image/png;base64," + base64.b64encode(_png(65)).decode("ascii").rstrip("="),
    "whitespace-inside": "data:image/png;base64," + _VALID_B64[:8] + "\n" + _VALID_B64[8:],
    "non-ascii": "data:image/png;base64," + _VALID_B64[:-4] + "ạạạạ",
    "url-safe-alphabet": "data:image/png;base64," + base64.urlsafe_b64encode(PNG_MAGIC + b"\xfb\xff" * 8).decode(),
    "not-png": _data_uri(_JPEG),
    "truncated-magic": _data_uri(PNG_MAGIC[:6]),
}


def _non_canonical(raw: bytes) -> str:
    """Base64 của `raw` với bit thừa của ký tự cuối khác 0 (giải mã vẫn ra `raw` nhưng không phải dạng chuẩn)."""
    encoded = base64.b64encode(raw).decode("ascii")
    assert encoded.endswith("=")
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
    stripped = encoded.rstrip("=")
    tweaked = alphabet[alphabet.index(stripped[-1]) + 1]
    return stripped[:-1] + tweaked + encoded[len(stripped) :]


@pytest.mark.parametrize("url", list(BAD_IMAGE_URLS.values()), ids=list(BAD_IMAGE_URLS))
def test_malformed_images_are_rejected_before_any_request(url: str) -> None:
    with pytest.raises(ValidationError):
        ImageUrl(url=url)
    with pytest.raises(ValidationError):
        ChatMessage.model_validate({"role": "user", "content": [{"type": "image_url", "image_url": {"url": url}}]})


def test_non_canonical_base64_is_rejected() -> None:
    raw = _png(65)
    tweaked = _non_canonical(raw)
    assert base64.b64decode(tweaked) == raw  # bộ giải mã lỏng vẫn nhận
    with pytest.raises(ValidationError):
        ImageUrl(url="data:image/png;base64," + tweaked)


def test_validation_errors_do_not_echo_the_whole_image() -> None:
    raw = PNG_MAGIC[:4] + b"\x00" * 4096
    with pytest.raises(ValidationError) as excinfo:
        ImageUrl(url=_data_uri(raw))
    assert base64.b64encode(raw).decode("ascii") not in str(excinfo.value)


@pytest.mark.parametrize("size", range(len(PNG_MAGIC), len(PNG_MAGIC) + 40))
def test_decoded_size_matches_the_real_byte_count(size: int) -> None:
    raw = PNG_MAGIC + b"\x01" * (size - len(PNG_MAGIC))
    assert ImageUrl(url=_data_uri(raw)).decoded_size == len(raw) == size


# --------------------------------------------------------------------------- #
# Trần byte ảnh: kiểm ở client từ cấu hình, trước khi gửi
# --------------------------------------------------------------------------- #


def test_image_over_the_configured_byte_limit_is_refused_without_sending() -> None:
    clock = FakeClock()
    transport = RecordingTransport(clock, (200, _ok_body()))
    client = OpenAiChatClient.from_vlm_settings(
        _vlm_settings(max_image_bytes=100), transport=transport, monotonic=clock
    )
    with pytest.raises(ImageTooLargeError, match="HOLOCRON_VLM_MAX_IMAGE_BYTES"):
        client.complete([_image_message(_png(101))], max_tokens=10)
    with pytest.raises(ImageTooLargeError):
        client.build_request([_image_message(_png(101))], max_tokens=10)
    assert transport.requests == []
    assert client.complete([_image_message(_png(100))], max_tokens=10).content == "[]"
    assert len(transport.requests) == 1


def test_image_byte_limit_defaults_to_four_mebibytes_from_the_environment() -> None:
    config = load_runtime_config({"HOLOCRON_VLM_BASE_URL": LOOPBACK_URL, "HOLOCRON_VLM_MODEL": MODEL})
    assert config.vlm is not None and config.vlm.max_image_bytes == 4 * 1024 * 1024
    smaller = load_runtime_config(
        {"HOLOCRON_VLM_BASE_URL": LOOPBACK_URL, "HOLOCRON_VLM_MODEL": MODEL, "HOLOCRON_VLM_MAX_IMAGE_BYTES": "50"}
    )
    assert smaller.vlm is not None
    client = OpenAiChatClient.from_vlm_settings(smaller.vlm)
    with pytest.raises(ImageTooLargeError):
        client.build_request([_image_message(_png(51))], max_tokens=1)


def test_text_clients_also_refuse_oversized_images() -> None:
    client = OpenAiChatClient(
        base_url=CLOUD_URL, api_key=SecretStr(SECRET), model=MODEL, timeout_s=1.0, max_image_bytes=10
    )
    with pytest.raises(ImageTooLargeError):
        client.build_request([_image_message(_png(11))], max_tokens=1)


# --------------------------------------------------------------------------- #
# Khoá: loopback được trống (không gửi Authorization), cloud bắt buộc
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("url", [LOOPBACK_URL, "http://localhost:1234/v1", "http://[::1]:8080/v1"])
def test_loopback_without_key_sends_no_authorization_header(url: str) -> None:
    config = load_runtime_config({"HOLOCRON_VLM_BASE_URL": url, "HOLOCRON_VLM_MODEL": MODEL})
    assert config.vlm is not None and config.vlm.api_key is None
    clock = FakeClock()
    transport = RecordingTransport(clock, (200, _ok_body("[1]")))
    client = OpenAiChatClient.from_vlm_settings(config.vlm, transport=transport, monotonic=clock)
    assert client.complete([_image_message()], max_tokens=10).content == "[1]"
    [request] = transport.requests
    assert request.get_header("Authorization") is None
    assert all(name.lower() != "authorization" for name, _ in request.header_items())
    assert request.get_header("Content-type") == "application/json"


def test_loopback_with_a_key_still_sends_it() -> None:
    settings = _vlm_settings(base_url=LOOPBACK_URL)
    request = OpenAiChatClient.from_vlm_settings(settings).build_request(TEXT_MESSAGES, max_tokens=1)
    assert request.get_header("Authorization") == f"Bearer {SECRET}"


def test_cloud_without_key_is_a_config_error_naming_the_vlm_variable() -> None:
    with pytest.raises(ConfigError, match="HOLOCRON_VLM_API_KEY") as excinfo:
        OpenAiChatClient.from_vlm_settings(_vlm_settings(api_key=None))
    assert "HOLOCRON_LLM_" not in str(excinfo.value)
    with pytest.raises(ConfigError, match="HOLOCRON_LLM_API_KEY"):
        OpenAiChatClient(base_url=CLOUD_URL, api_key=None, model=MODEL, timeout_s=1.0)


def test_vlm_client_errors_name_vlm_variables_not_llm_ones() -> None:
    with pytest.raises(ConfigError, match="HOLOCRON_VLM_BASE_URL") as excinfo:
        OpenAiChatClient.from_vlm_settings(_vlm_settings(base_url="ftp://vision.example.test/v1"))
    assert "HOLOCRON_LLM_" not in str(excinfo.value) and "vision.example.test" not in str(excinfo.value)
    with pytest.raises(ConfigError, match="HOLOCRON_VLM_API_KEY") as key_excinfo:
        OpenAiChatClient.from_vlm_settings(_vlm_settings(api_key=SecretStr("sk bad key")))
    assert "HOLOCRON_LLM_" not in str(key_excinfo.value) and "sk bad key" not in str(key_excinfo.value)


# --------------------------------------------------------------------------- #
# Timeout VLM: cận tổng cho MỖI lần gọi, tính từ lúc lần gọi bắt đầu
# --------------------------------------------------------------------------- #


def test_vlm_timeout_is_used_for_each_attempt() -> None:
    clock = FakeClock()
    transport = RecordingTransport(clock, (200, _ok_body()))
    client = OpenAiChatClient.from_vlm_settings(_vlm_settings(timeout_s=42.0), transport=transport, monotonic=clock)
    client.complete([_image_message()], max_tokens=10)
    assert transport.timeouts == [42.0]
    assert "timeout_s=42.0" in repr(client)


def test_vlm_timeout_counts_from_the_start_of_each_call_not_from_construction() -> None:
    clock = FakeClock()
    transport = RecordingTransport(clock, (200, _ok_body("[1]")), (200, _ok_body("[2]")), spend=5.0)
    client = OpenAiChatClient.from_vlm_settings(_vlm_settings(timeout_s=10.0), transport=transport, monotonic=clock)
    clock.advance(25.0)  # lâu hơn timeout kể từ lúc dựng client
    assert client.complete([_image_message()], max_tokens=10).content == "[1]"
    clock.advance(30.0)  # hai lần gọi cách nhau lâu hơn timeout
    assert client.complete([_image_message()], max_tokens=10).content == "[2]"
    assert transport.timeouts == [10.0, 10.0]


def test_vlm_timeout_bounds_all_retries_of_one_call() -> None:
    clock = FakeClock()
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock.advance(seconds)

    transport = RecordingTransport(clock, (503, b""), (503, b""), (200, _ok_body()), spend=6.0)
    client = OpenAiChatClient.from_vlm_settings(
        _vlm_settings(timeout_s=10.0), transport=transport, sleep=sleep, monotonic=clock
    )
    with pytest.raises(LlmUpstreamError, match="hết hạn") as excinfo:
        client.complete([_image_message()], max_tokens=10)
    # Lần 1 dùng 6 s; lần 2 chỉ còn 10 - 6 - 0,5 = 3,5 s; sau lần 2 không còn đủ cho backoff 1 s.
    assert transport.timeouts == [10.0, 3.5]
    assert sleeps == [0.5] and excinfo.value.attempts == 2


# --------------------------------------------------------------------------- #
# Không lộ khoá, endpoint hay tên model
# --------------------------------------------------------------------------- #


def test_failures_and_logs_never_contain_key_endpoint_or_model(caplog: pytest.LogCaptureFixture) -> None:
    clock = FakeClock()
    transport = RecordingTransport(clock, (500, b""), (500, b""), (500, b""))
    settings = _vlm_settings(timeout_s=100.0)
    client = OpenAiChatClient.from_vlm_settings(settings, transport=transport, sleep=clock.advance, monotonic=clock)
    with caplog.at_level(logging.DEBUG), pytest.raises(LlmUpstreamError) as excinfo:
        client.complete([_image_message()], max_tokens=10)
    chain = "".join(traceback.format_exception(excinfo.value))
    for text in (chain, caplog.text, repr(client), repr(settings), str(settings)):
        assert SECRET not in text and "vision.example.test" not in text and MODEL not in text


def test_loading_vlm_configuration_logs_neither_key_nor_endpoint(caplog: pytest.LogCaptureFixture) -> None:
    env = {
        "HOLOCRON_VLM_BASE_URL": CLOUD_URL,
        "HOLOCRON_VLM_API_KEY": SECRET,
        "HOLOCRON_VLM_MODEL": MODEL,
        "HOLOCRON_VLM_CONCURRENCY": "99",
    }
    with caplog.at_level(logging.DEBUG):
        config = load_runtime_config_lenient(env)
    assert config.vlm is None
    assert "HOLOCRON_VLM_CONCURRENCY" in caplog.text
    assert SECRET not in caplog.text and "vision.example.test" not in caplog.text and MODEL not in caplog.text


@pytest.mark.parametrize("budget", [0.0, -1.0, float("nan"), float("inf")])
def test_client_refuses_a_non_positive_call_budget(budget: float) -> None:
    with pytest.raises(ValueError, match="call_budget_s"):
        OpenAiChatClient(
            base_url=CLOUD_URL, api_key=SecretStr(SECRET), model=MODEL, timeout_s=1.0, call_budget_s=budget
        )


def test_client_refuses_a_non_positive_image_limit() -> None:
    with pytest.raises(ValueError, match="max_image_bytes"):
        OpenAiChatClient(base_url=CLOUD_URL, api_key=SecretStr(SECRET), model=MODEL, timeout_s=1.0, max_image_bytes=0)


@pytest.mark.parametrize("url", ["http://[::1/v1", "https://vision.example.test/v1", "not a url"])
def test_unparsable_or_remote_urls_are_not_loopback(url: str) -> None:
    assert not is_loopback_url(url)
