"""Adapter Chat Completions kiểu OpenAI (`infrastructure/llm_client.py`, spec v0.6 §11.5). KHÔNG gọi mạng.

Mọi request đi qua transport giả hoặc handler urllib giả; fixture `_no_network` chặn mọi kết nối socket để test hỏng
ngay nếu có đường nào lọt ra mạng thật.
"""

import email.message
import http.client
import io
import json
import logging
import socket
import traceback
import urllib.error
import urllib.request
import urllib.response
from importlib import metadata

import pytest
from pydantic import SecretStr

from holocron_core.domain.llm import ChatMessage, LlmUpstreamError
from holocron_core.infrastructure import llm_client
from holocron_core.infrastructure.llm_client import (
    MAX_RESPONSE_BYTES,
    USER_AGENT,
    OpenAiChatClient,
    UrllibTransport,
    package_version,
)
from holocron_core.infrastructure.llm_config import ConfigError, LlmSettings, load_runtime_config

SECRET = "sk-test-0123456789-must-never-leak"
BASE_URL = "https://gateway.example.test/v1/"
MODEL = "model-under-test"
MESSAGES = [ChatMessage(role="system", content="Chỉ dẫn"), ChatMessage(role="user", content="Văn bản chunk")]


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("test không được gọi mạng")

    # `socket.socket.connect` không chặn được: vòng lặp asyncio của TestClient trên Windows dùng `socketpair`.
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(http.client.HTTPConnection, "connect", refuse)


def _ok_body(content: str = "[]", usage: object = None) -> bytes:
    choice = {"index": 0, "message": {"role": "assistant", "content": content}}
    body: dict[str, object] = {"id": "x", "choices": [choice]}
    if usage is not None:
        body["usage"] = usage
    return json.dumps(body).encode("utf-8")


class ScriptedTransport:
    """Trả lần lượt các kết quả định sẵn; phần tử là `(mã, body)` hoặc exception sẽ được ném."""

    def __init__(self, *outcomes: tuple[int, bytes] | BaseException) -> None:
        self.outcomes = list(outcomes)
        self.requests: list[urllib.request.Request] = []
        self.timeouts: list[float] = []

    def __call__(self, request: urllib.request.Request, timeout_s: float) -> tuple[int, bytes]:
        self.requests.append(request)
        self.timeouts.append(timeout_s)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _client(transport: ScriptedTransport, sleeps: list[float] | None = None) -> OpenAiChatClient:
    return OpenAiChatClient(
        base_url=BASE_URL,
        api_key=SecretStr(SECRET),
        model=MODEL,
        timeout_s=12.5,
        transport=transport,
        sleep=(sleeps if sleeps is not None else []).append,
    )


# --------------------------------------------------------------------------- #
# Request đã dựng (không gửi)
# --------------------------------------------------------------------------- #


def test_request_has_url_headers_and_only_the_allowed_body_fields() -> None:
    request = _client(ScriptedTransport()).build_request(MESSAGES, max_tokens=321)
    assert request.full_url == "https://gateway.example.test/v1/chat/completions"
    assert request.get_method() == "POST"
    assert request.get_header("Authorization") == f"Bearer {SECRET}"
    assert request.get_header("Content-type") == "application/json"
    user_agent = request.get_header("User-agent")
    assert user_agent == USER_AGENT and user_agent.startswith("Holocron/")
    assert "urllib" not in user_agent.lower()
    assert isinstance(request.data, bytes)
    body = json.loads(request.data.decode("utf-8"))
    assert set(body) == {"model", "messages", "max_tokens"}
    assert "temperature" not in body and "top_p" not in body and "response_format" not in body
    assert body["model"] == MODEL and body["max_tokens"] == 321
    assert body["messages"] == [{"role": "system", "content": "Chỉ dẫn"}, {"role": "user", "content": "Văn bản chunk"}]


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://host/v1", "gateway.example.test/v1", ""])
def test_only_http_and_https_base_urls_are_accepted(url: str) -> None:
    with pytest.raises(ConfigError, match="HOLOCRON_LLM_BASE_URL") as excinfo:
        OpenAiChatClient(base_url=url, api_key=SecretStr(SECRET), model=MODEL, timeout_s=1.0)
    assert SECRET not in str(excinfo.value)


def test_from_settings_uses_the_configured_endpoint_key_and_timeout() -> None:
    config = load_runtime_config(
        {
            "HOLOCRON_LLM_API_KEY": SECRET,
            "HOLOCRON_LLM_BASE_URL": "http://127.0.0.1:9/v1",
            "HOLOCRON_LLM_TIMEOUT_S": "7",
        }
    )
    client = OpenAiChatClient.from_settings(config.llm, model="m-extract")
    request = client.build_request(MESSAGES, max_tokens=5)
    assert request.full_url == "http://127.0.0.1:9/v1/chat/completions"
    assert request.data is not None and json.loads(request.data)["model"] == "m-extract"
    assert "timeout_s=7.0" in repr(client)


def test_from_settings_without_key_names_the_variable() -> None:
    with pytest.raises(ConfigError, match="HOLOCRON_LLM_API_KEY"):
        OpenAiChatClient.from_settings(LlmSettings(), model="m")


def test_package_version_falls_back_to_dev_when_not_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(name: str) -> str:
        raise metadata.PackageNotFoundError(name)

    monkeypatch.setattr(llm_client.metadata, "version", missing)
    assert package_version() == "dev"
    monkeypatch.setattr(llm_client.metadata, "version", lambda name: "9.9.9")
    assert package_version() == "9.9.9"


# --------------------------------------------------------------------------- #
# Phản hồi và usage
# --------------------------------------------------------------------------- #


def test_returns_content_usage_and_attempts() -> None:
    transport = ScriptedTransport((200, _ok_body("[1]", {"prompt_tokens": 11, "completion_tokens": 7, "total": 18})))
    result = _client(transport).complete(MESSAGES, max_tokens=50)
    assert result.content == "[1]" and result.attempts == 1
    assert result.usage is not None and (result.usage.prompt_tokens, result.usage.completion_tokens) == (11, 7)
    assert transport.timeouts == [12.5]


@pytest.mark.parametrize("usage", [None, {"prompt_tokens": 3}, {"prompt_tokens": None, "completion_tokens": None}])
def test_missing_or_partial_usage_is_not_an_error(usage: object) -> None:
    result = _client(ScriptedTransport((200, _ok_body("x", usage)))).complete(MESSAGES, max_tokens=5)
    assert result.content == "x" and result.usage is None


@pytest.mark.parametrize(
    "body",
    [b"not json", b"{}", b'{"choices": []}', b'{"choices": [{"message": {"content": null}}]}', b"\xff\xfe"],
)
def test_malformed_envelope_is_an_upstream_error_without_the_body(body: bytes) -> None:
    with pytest.raises(LlmUpstreamError) as excinfo:
        _client(ScriptedTransport((200, body))).complete(MESSAGES, max_tokens=5)
    assert excinfo.value.attempts == 1
    assert "not json" not in str(excinfo.value) and "choices" not in str(excinfo.value)


# --------------------------------------------------------------------------- #
# Thử lại
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_retries_429_and_5xx_then_succeeds(status: int) -> None:
    sleeps: list[float] = []
    transport = ScriptedTransport((status, b""), (status, b""), (200, _ok_body("ok")))
    result = _client(transport, sleeps).complete(MESSAGES, max_tokens=5)
    assert result.content == "ok" and result.attempts == 3
    assert len(transport.requests) == 3 and sleeps == [0.5, 1.0]


@pytest.mark.parametrize(
    "error",
    [
        urllib.error.URLError("getaddrinfo failed"),
        TimeoutError("timed out"),
        ConnectionResetError(),
        http.client.RemoteDisconnected("closed"),
        http.client.IncompleteRead(b"partial"),
    ],
)
def test_network_errors_are_retried(error: BaseException) -> None:
    transport = ScriptedTransport(error, (200, _ok_body("ok")))
    assert _client(transport).complete(MESSAGES, max_tokens=5).attempts == 2


def test_gives_up_after_the_retry_budget() -> None:
    sleeps: list[float] = []
    transport = ScriptedTransport((503, b""), TimeoutError(), (429, b""))
    with pytest.raises(LlmUpstreamError, match="3 lần thử") as excinfo:
        _client(transport, sleeps).complete(MESSAGES, max_tokens=5)
    assert excinfo.value.attempts == 3 and len(transport.requests) == 3 and sleeps == [0.5, 1.0]
    assert "HTTP 429" in str(excinfo.value)


@pytest.mark.parametrize("status", [400, 401, 403, 404, 302, 301])
def test_other_statuses_fail_immediately_without_retry(status: int) -> None:
    transport = ScriptedTransport((status, b"error code: 1010"), (200, _ok_body("never")))
    with pytest.raises(LlmUpstreamError, match=f"HTTP {status}") as excinfo:
        _client(transport).complete(MESSAGES, max_tokens=5)
    assert excinfo.value.attempts == 1 and len(transport.requests) == 1
    assert "1010" not in str(excinfo.value)


def test_retry_budget_is_configurable() -> None:
    transport = ScriptedTransport((500, b""))
    client = OpenAiChatClient(
        base_url=BASE_URL, api_key=SecretStr(SECRET), model=MODEL, timeout_s=1.0, retry_max=0, transport=transport
    )
    with pytest.raises(LlmUpstreamError) as excinfo:
        client.complete(MESSAGES, max_tokens=5)
    assert excinfo.value.attempts == 1


# --------------------------------------------------------------------------- #
# Không lộ khoá, endpoint, model hay nội dung
# --------------------------------------------------------------------------- #


def test_key_endpoint_model_and_request_body_never_leak_into_repr_errors_or_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    outcomes: list[tuple[int, bytes] | BaseException] = [(401, b"bad key " + SECRET.encode())]
    outcomes += [(500, b""), (500, b""), (500, b"")]
    outcomes += [(200, b"garbage " + SECRET.encode())]
    transport = ScriptedTransport(*outcomes)
    client = _client(transport)
    texts = [repr(client), str(client)]
    with caplog.at_level(logging.DEBUG):
        for _ in range(3):
            with pytest.raises(LlmUpstreamError) as excinfo:
                client.complete(MESSAGES, max_tokens=5)
            texts += [str(excinfo.value), repr(excinfo.value)]
    texts.append(caplog.text)
    for text in texts:
        assert SECRET not in text
        assert "gateway.example.test" not in text and MODEL not in text
        assert "Văn bản chunk" not in text


# --------------------------------------------------------------------------- #
# Transport urllib (handler giả, không mở socket)
# --------------------------------------------------------------------------- #


class FakeHttps(urllib.request.HTTPSHandler):
    """Thay handler HTTPS mặc định: trả phản hồi định sẵn, ghi lại URL được mở."""

    def __init__(self, status: int, body: bytes = b"", location: str | None = None) -> None:
        super().__init__()
        self.status = status
        self.body = body
        self.location = location
        self.opened: list[str] = []
        self.sent_headers: list[dict[str, str]] = []

    def https_open(self, req: urllib.request.Request) -> urllib.response.addinfourl:
        self.opened.append(req.full_url)
        self.sent_headers.append(dict(req.header_items()))
        headers = email.message.Message()
        if self.location is not None:
            headers["Location"] = self.location
        response = urllib.response.addinfourl(io.BytesIO(self.body), headers, req.full_url, self.status)
        response.msg = "fake"
        return response


def _request() -> urllib.request.Request:
    return urllib.request.Request("https://gateway.example.test/v1/chat/completions", data=b"{}", method="POST")


def test_urllib_transport_returns_status_and_body() -> None:
    handler = FakeHttps(200, _ok_body("ok"))
    status, body = UrllibTransport(handler)(_request(), 3.0)
    assert status == 200 and json.loads(body)["choices"][0]["message"]["content"] == "ok"


def test_urllib_transport_turns_http_errors_into_status_codes() -> None:
    status, body = UrllibTransport(FakeHttps(403, b"error code: 1010"))(_request(), 3.0)
    assert (status, body) == (403, b"")


def test_urllib_transport_never_follows_redirects() -> None:
    """Redirect có thể chuyển header `Authorization` sang host khác: bị chặn, trả mã 3xx để client thất bại."""
    handler = FakeHttps(302, location="https://elsewhere.example.test/steal")
    status, _ = UrllibTransport(handler)(_request(), 3.0)
    assert status == 302
    assert handler.opened == ["https://gateway.example.test/v1/chat/completions"]


def test_urllib_transport_rejects_oversized_responses() -> None:
    transport = UrllibTransport(FakeHttps(200, b"x" * (MAX_RESPONSE_BYTES + 1)))
    client = OpenAiChatClient(
        base_url=BASE_URL, api_key=SecretStr(SECRET), model=MODEL, timeout_s=1.0, transport=transport
    )
    with pytest.raises(LlmUpstreamError, match="kích thước") as excinfo:
        client.complete(MESSAGES, max_tokens=5)
    assert excinfo.value.attempts == 1


def test_headers_on_the_wire_keep_the_custom_user_agent() -> None:
    """urllib chỉ thêm User-Agent mặc định (`Python-urllib/…`, bị gateway chặn) khi request chưa có header này."""
    handler = FakeHttps(200, _ok_body("xin chào"))
    client = OpenAiChatClient(
        base_url=BASE_URL, api_key=SecretStr(SECRET), model=MODEL, timeout_s=1.0, transport=UrllibTransport(handler)
    )
    assert client.complete(MESSAGES, max_tokens=5).content == "xin chào"
    [headers] = handler.sent_headers
    assert headers["User-agent"] == USER_AGENT and "Python-urllib" not in headers["User-agent"]
    assert headers["Authorization"] == f"Bearer {SECRET}" and headers["Content-type"] == "application/json"


# --------------------------------------------------------------------------- #
# Khoá không hợp lệ cho header: không được lộ qua exception
# --------------------------------------------------------------------------- #

UNSAFE_KEYS = [
    "sk-test-0123\n456789-must-never-leak",
    "sk-test-0123\r456789-must-never-leak",
    "sk-test-0123\x00456789-must-never-leak",
    "sk-test-0123\r\nX-Injected: 1",
    "sk-test-0123 456789-must-never-leak",
    "sk-test-0123\t456789-must-never-leak",
    "sk-test-0123\x7f456789-must-never-leak",
    "sk-test-0123ạ456789-must-never-leak",
]
UNSAFE_IDS = ["lf", "cr", "nul", "crlf-injection", "space", "tab", "del", "non-ascii"]


def _chain_text(exc: BaseException) -> str:
    """Toàn bộ chuỗi exception (thông điệp, `__cause__`, `__context__`) như khi in traceback."""
    return "".join(traceback.format_exception(exc))


@pytest.mark.parametrize("key", UNSAFE_KEYS, ids=UNSAFE_IDS)
@pytest.mark.parametrize("variable", ["HOLOCRON_LLM_API_KEY", "HOLOCRON_VLM_API_KEY"])
def test_unsafe_key_is_a_config_error_naming_only_the_variable(key: str, variable: str) -> None:
    env = {
        "HOLOCRON_LLM_BASE_URL": BASE_URL,
        "HOLOCRON_LLM_MODEL_EXTRACT": MODEL,
        "HOLOCRON_VLM_BASE_URL": BASE_URL,
        "HOLOCRON_VLM_MODEL": MODEL,
        variable: key,
    }
    with pytest.raises(ConfigError, match=variable) as excinfo:
        load_runtime_config(env)
    assert excinfo.value.__cause__ is None and excinfo.value.__context__ is None
    for fragment in (key, key.strip(), "456789-must-never-leak", "X-Injected"):
        assert fragment not in _chain_text(excinfo.value)


@pytest.mark.parametrize("variable", ["HOLOCRON_LLM_BASE_URL", "HOLOCRON_LLM_MODEL_EXTRACT", "HOLOCRON_VLM_MODEL"])
@pytest.mark.parametrize("bad", ["a\nb", "a\rb", "a\x00b"], ids=["lf", "cr", "nul"])
def test_control_characters_in_other_variables_are_config_errors(variable: str, bad: str) -> None:
    with pytest.raises(ConfigError, match=variable) as excinfo:
        load_runtime_config({variable: f"https://host.example.test/{bad}"})
    assert bad not in str(excinfo.value) and "host.example.test" not in str(excinfo.value)


@pytest.mark.parametrize("key", UNSAFE_KEYS, ids=UNSAFE_IDS)
def test_client_refuses_an_unsafe_key_without_echoing_it(key: str) -> None:
    with pytest.raises(ConfigError, match="HOLOCRON_LLM_API_KEY") as excinfo:
        OpenAiChatClient(base_url=BASE_URL, api_key=SecretStr(key), model=MODEL, timeout_s=1.0)
    assert "456789-must-never-leak" not in _chain_text(excinfo.value)


def test_control_characters_in_the_base_url_are_refused() -> None:
    with pytest.raises(ConfigError, match="HOLOCRON_LLM_BASE_URL") as excinfo:
        OpenAiChatClient(base_url="https://a.test/v1\r\nX: y", api_key=SecretStr(SECRET), model=MODEL, timeout_s=1.0)
    assert SECRET not in _chain_text(excinfo.value) and "a.test" not in str(excinfo.value)


def _assert_no_secret_anywhere(exc: BaseException, secret: str) -> None:
    assert exc.__cause__ is None and exc.__context__ is None
    for text in (str(exc), repr(exc), _chain_text(exc)):
        assert secret not in text and "Authorization" not in text and "Bearer" not in text


def test_error_while_building_the_request_does_not_carry_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(ScriptedTransport())

    def explode(messages: object, *, max_tokens: int) -> urllib.request.Request:
        raise ValueError(f"Invalid header value b'Bearer {SECRET}'")

    monkeypatch.setattr(client, "build_request", explode)
    with pytest.raises(LlmUpstreamError, match="ValueError") as excinfo:
        client.complete(MESSAGES, max_tokens=5)
    assert excinfo.value.attempts == 0
    _assert_no_secret_anywhere(excinfo.value, SECRET)


@pytest.mark.parametrize(
    "error",
    [
        ValueError(f"Invalid header value b'Bearer {SECRET}'"),
        UnicodeEncodeError("latin-1", f"Bearer {SECRET}", 7, 8, "ordinal not in range(256)"),
        TypeError(SECRET),
        LookupError(SECRET),
    ],
    ids=["value-error", "unicode-encode-error", "type-error", "lookup-error"],
)
def test_unexpected_error_while_sending_does_not_carry_the_key(error: BaseException) -> None:
    transport = ScriptedTransport(error, (200, _ok_body("never")))
    with pytest.raises(LlmUpstreamError) as excinfo:
        _client(transport).complete(MESSAGES, max_tokens=5)
    assert excinfo.value.attempts == 1 and len(transport.requests) == 1  # không thử lại
    assert type(error).__name__ in str(excinfo.value)
    _assert_no_secret_anywhere(excinfo.value, SECRET)


def test_http_client_header_error_with_the_real_transport_does_not_carry_the_key() -> None:
    """Khoá có xuống dòng lọt qua (ví dụ do code khác dựng client): `http.client.putheader` ném `ValueError` chứa
    nguyên giá trị header TRƯỚC khi mở kết nối; adapter đổi thành `LlmUpstreamError` không mang lỗi gốc."""
    leaked = "sk-test-0123456789\nmust-never-leak"
    client = OpenAiChatClient(
        base_url=BASE_URL, api_key=SecretStr(SECRET), model=MODEL, timeout_s=1.0, transport=UrllibTransport()
    )
    client._api_key = SecretStr(leaked)  # giả lập khoá hỏng đã qua được bước kiểm lúc tạo client
    with pytest.raises(LlmUpstreamError, match="ValueError") as excinfo:
        client.complete(MESSAGES, max_tokens=5)
    _assert_no_secret_anywhere(excinfo.value, "must-never-leak")


def test_network_errors_after_retries_do_not_chain_the_original_exception() -> None:
    transport = ScriptedTransport(OSError(SECRET), OSError(SECRET), OSError(SECRET))
    with pytest.raises(LlmUpstreamError, match="OSError") as excinfo:
        _client(transport).complete(MESSAGES, max_tokens=5)
    _assert_no_secret_anywhere(excinfo.value, SECRET)


def test_malformed_envelope_error_does_not_chain_the_validation_error() -> None:
    body = b'{"choices": "' + SECRET.encode() + b'"}'
    with pytest.raises(LlmUpstreamError) as excinfo:
        _client(ScriptedTransport((200, body))).complete(MESSAGES, max_tokens=5)
    _assert_no_secret_anywhere(excinfo.value, SECRET)


# --------------------------------------------------------------------------- #
# Không có nhà cung cấp mặc định (spec v0.6 §11.2)
# --------------------------------------------------------------------------- #


def test_from_settings_names_every_missing_variable_and_never_defaults_to_a_provider() -> None:
    with pytest.raises(ConfigError) as excinfo:
        OpenAiChatClient.from_settings(LlmSettings(api_key=SecretStr(SECRET)), model=None)
    message = str(excinfo.value)
    assert "HOLOCRON_LLM_BASE_URL" in message and "HOLOCRON_LLM_MODEL_EXTRACT" in message
    assert "HOLOCRON_LLM_API_KEY" not in message and SECRET not in message
    settings = load_runtime_config({"HOLOCRON_LLM_API_KEY": SECRET}).llm
    assert settings.base_url is None and settings.model_extract is None and settings.model_author is None
    with pytest.raises(ConfigError, match="HOLOCRON_LLM_BASE_URL"):
        OpenAiChatClient.from_settings(settings, model=settings.model_extract)


def test_model_author_defaults_to_model_extract() -> None:
    llm = load_runtime_config({"HOLOCRON_LLM_MODEL_EXTRACT": "m-x"}).llm
    assert (llm.model_extract, llm.model_author) == ("m-x", "m-x")
    llm = load_runtime_config({"HOLOCRON_LLM_MODEL_EXTRACT": "m-x", "HOLOCRON_LLM_MODEL_AUTHOR": "m-a"}).llm
    assert (llm.model_extract, llm.model_author) == ("m-x", "m-a")


@pytest.mark.parametrize("missing", ["HOLOCRON_LLM_BASE_URL", "HOLOCRON_LLM_API_KEY", "HOLOCRON_LLM_MODEL_EXTRACT"])
@pytest.mark.parametrize("blank", [None, "", "   \t "], ids=["unset", "empty", "whitespace"])
def test_llm_is_configured_only_with_all_three_variables(missing: str, blank: str | None) -> None:
    env = {
        "HOLOCRON_LLM_BASE_URL": BASE_URL,
        "HOLOCRON_LLM_API_KEY": SECRET,
        "HOLOCRON_LLM_MODEL_EXTRACT": MODEL,
        "HOLOCRON_LLM_MODEL_AUTHOR": "author-model",
    }
    assert load_runtime_config(env).llm_configured
    if blank is None:
        del env[missing]
    else:
        env[missing] = blank
    config = load_runtime_config(env)
    assert not config.llm_configured and config.llm.missing == (missing,)
