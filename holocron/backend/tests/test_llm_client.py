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
