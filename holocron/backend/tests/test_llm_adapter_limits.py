"""Adapter LLM, sửa sau review: cận thời gian tổng thật cho request HTTP, scheme của base URL, tắt proxy hệ thống, giới
hạn HTTP là field cấu hình (`infrastructure/llm_client.py`, `infrastructure/llm_config.py`; spec v0.6 §6.0, §11.2,
§11.5).

KHÔNG gọi mạng và KHÔNG chờ thật: server "gửi nhỏ giọt" là handler urllib giả, mỗi lần đọc làm đồng hồ giả tiến thêm;
fixture `_no_network` chặn mọi kết nối socket.
"""

import email.message
import http.client
import io
import json
import socket
import threading
import urllib.request
import urllib.response
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from holocron_core.domain.llm import ChatMessage, LlmUpstreamError
from holocron_core.domain.repositories import AppStore
from holocron_core.infrastructure.llm_client import (
    BACKOFF_BASE_S,
    HTTP_RETRY_MAX,
    MAX_RESPONSE_BYTES,
    OpenAiChatClient,
    UrllibTransport,
    read_within_deadline,
)
from holocron_core.infrastructure.llm_config import (
    ConfigError,
    LlmHttpLimits,
    LlmSettings,
    LlmTaskLimits,
    check_base_url,
    load_runtime_config,
)
from holocron_core.infrastructure.memory_store import IngestionStore
from holocron_core.routers import vault
from holocron_core.routers.deps import get_clock, get_runtime_config, get_store
from holocron_core.services.vault import ingest_confirmed_text
from tests.vault_support import NOW, PHILOSOPHY_PAGES, add_document, confirmed, document_id

SECRET = "sk-test-0123456789-must-never-leak"
BASE_URL = "https://gateway.example.test/v1"
MODEL = "model-under-test"
MESSAGES = [ChatMessage(role="user", content="Văn bản chunk")]


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("test không được gọi mạng")

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(http.client.HTTPConnection, "connect", refuse)


def _ok_body(content: str = "[]") -> bytes:
    return json.dumps({"choices": [{"message": {"role": "assistant", "content": content}}]}).encode("utf-8")


class FakeClock:
    """Đồng hồ đơn điệu giả; `sleep` làm đồng hồ tiến, không chờ thật."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.advance(seconds)


class TrickleBody:
    """Body gửi nhỏ giọt: mỗi lần đọc trả `piece` và làm đồng hồ giả tiến `step` giây; không bao giờ hết dữ liệu."""

    def __init__(self, clock: FakeClock, step: float, piece: bytes = b"x") -> None:
        self.clock = clock
        self.step = step
        self.piece = piece
        self.reads: list[int] = []
        self.closed = False

    def read1(self, size: int = -1) -> bytes:
        self.reads.append(size)
        self.clock.advance(self.step)
        return self.piece

    def read(self, size: int = -1) -> bytes:
        return self.read1(size)

    def readline(self, size: int = -1) -> bytes:
        return self.read1(size)

    def close(self) -> None:
        self.closed = True


class FiniteBody(TrickleBody):
    """Như `TrickleBody` nhưng hết dữ liệu sau `total` byte."""

    def __init__(self, clock: FakeClock, step: float, data: bytes) -> None:
        super().__init__(clock, step)
        self.data = data

    def read1(self, size: int = -1) -> bytes:
        self.reads.append(size)
        self.clock.advance(self.step)
        piece, self.data = self.data[:size], self.data[size:]
        return piece


class BodyHttps(urllib.request.HTTPSHandler):
    """Handler HTTPS giả trả `status` với body cho trước; ghi lại host của request."""

    def __init__(self, body: TrickleBody, status: int = 200) -> None:
        super().__init__()
        self.body = body
        self.status = status
        self.hosts: list[str] = []

    def https_open(self, req: urllib.request.Request) -> urllib.response.addinfourl:
        self.hosts.append(req.host)
        response = urllib.response.addinfourl(self.body, email.message.Message(), req.full_url, self.status)
        response.msg = "fake"
        return response


def _request() -> urllib.request.Request:
    return urllib.request.Request(BASE_URL + "/chat/completions", data=b"{}", method="POST")


# --------------------------------------------------------------------------- #
# Đọc body theo khối với hạn tổng (đồng hồ giả)
# --------------------------------------------------------------------------- #


def test_read_within_deadline_stops_a_trickling_body() -> None:
    clock = FakeClock()
    body = TrickleBody(clock, step=1.0)
    with pytest.raises(TimeoutError):
        read_within_deadline(body, deadline=clock() + 5.0, monotonic=clock, max_bytes=1_000, chunk_bytes=64)
    # Mỗi byte tốn 1 s: đọc đúng 5 khối rồi dừng ở lần so hạn kế tiếp, không đọc vô hạn.
    assert len(body.reads) == 5 and clock.now == 1005.0


def test_read_within_deadline_reads_in_bounded_chunks_until_eof() -> None:
    clock = FakeClock()
    body = FiniteBody(clock, step=0.0, data=b"a" * 10)
    assert (
        read_within_deadline(body, deadline=clock() + 1.0, monotonic=clock, max_bytes=100, chunk_bytes=4) == b"a" * 10
    )
    assert body.reads == [4, 4, 4, 4]  # khối cuối trả rỗng = hết dữ liệu


def test_read_within_deadline_enforces_the_size_limit_without_reading_further() -> None:
    clock = FakeClock()
    body = FiniteBody(clock, step=0.0, data=b"a" * 50)
    with pytest.raises(LlmUpstreamError, match="kích thước"):
        read_within_deadline(body, deadline=clock() + 1.0, monotonic=clock, max_bytes=10, chunk_bytes=8)
    assert body.reads == [8, 3]  # không xin quá max_bytes + 1 byte


def test_urllib_transport_times_out_a_trickling_server_with_a_fake_clock() -> None:
    clock = FakeClock()
    body = TrickleBody(clock, step=1.0)
    transport = UrllibTransport(BodyHttps(body), monotonic=clock)
    with pytest.raises(TimeoutError):
        transport(_request(), 10.0)
    assert len(body.reads) == 10 and body.closed


def test_urllib_transport_reads_a_normal_response_in_chunks() -> None:
    clock = FakeClock()
    data = _ok_body("xin chào")
    body = FiniteBody(clock, step=0.01, data=data)
    limits = LlmHttpLimits(read_chunk_bytes=16)
    status, received = UrllibTransport(BodyHttps(body), limits=limits, monotonic=clock)(_request(), 10.0)
    assert (status, received) == (200, data)
    assert max(body.reads) <= 16 and len(body.reads) > 1


def test_waiting_thread_returns_when_the_header_phase_hangs() -> None:
    """Server không trả nổi dòng trạng thái/header: luồng gọi không chờ quá hạn (ở đây `wait` giả trả về ngay như khi
    hết hạn), lần gửi thành `TimeoutError`; luồng gửi bị bỏ lại là daemon và tự kết thúc khi được thả."""
    release = threading.Event()
    entered = threading.Event()

    class HangingHttps(urllib.request.HTTPSHandler):
        def https_open(self, req: urllib.request.Request) -> urllib.response.addinfourl:
            entered.set()
            release.wait(5.0)
            response = urllib.response.addinfourl(io.BytesIO(b""), email.message.Message(), req.full_url, 200)
            response.msg = "fake"
            return response

    workers: list[threading.Thread] = []

    def wait_until_started(worker: threading.Thread, timeout_s: float) -> None:
        workers.append(worker)
        assert entered.wait(5.0)
        assert timeout_s == 3.0

    transport = UrllibTransport(HangingHttps(), wait=wait_until_started)
    with pytest.raises(TimeoutError):
        transport(_request(), 3.0)
    [worker] = workers
    assert worker.daemon and worker.is_alive()
    release.set()
    worker.join(5.0)
    assert not worker.is_alive()


def test_a_worker_that_dies_of_an_unexpected_error_is_an_upstream_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Lỗi ngoài `TRANSFERRED_ERRORS` làm luồng gửi kết thúc mà không có kết quả: lỗi adapter, không mang lỗi gốc."""
    seen: list[type[BaseException] | None] = []
    monkeypatch.setattr(threading, "excepthook", lambda args: seen.append(args.exc_type))

    def join(worker: threading.Thread, timeout_s: float) -> None:
        worker.join(5.0)

    def fake_exchange(request: urllib.request.Request, timeout_s: float, deadline: float) -> tuple[int, bytes]:
        raise RuntimeError(SECRET)

    transport = UrllibTransport(wait=join)
    monkeypatch.setattr(transport, "_exchange", fake_exchange)
    with pytest.raises(LlmUpstreamError, match="không mong đợi") as excinfo:
        transport(_request(), 1.0)
    assert seen == [RuntimeError]
    assert SECRET not in str(excinfo.value) and excinfo.value.__context__ is None


# --------------------------------------------------------------------------- #
# Cận tổng của client: hạn còn lại được truyền xuống transport
# --------------------------------------------------------------------------- #


class TimedTransport:
    """Transport giả: mỗi lần gửi tiêu hết thời gian được cấp (đồng hồ giả) rồi trả mã định sẵn."""

    def __init__(self, clock: FakeClock, *outcomes: tuple[int, bytes], spend: float | None = None) -> None:
        self.clock = clock
        self.outcomes = list(outcomes)
        self.spend = spend
        self.timeouts: list[float] = []

    def __call__(self, request: urllib.request.Request, timeout_s: float) -> tuple[int, bytes]:
        self.timeouts.append(timeout_s)
        self.clock.advance(timeout_s if self.spend is None else self.spend)
        return self.outcomes.pop(0)


def _budget_client(transport: TimedTransport, clock: FakeClock, budget: float | None) -> OpenAiChatClient:
    return OpenAiChatClient(
        base_url=BASE_URL,
        api_key=SecretStr(SECRET),
        model=MODEL,
        timeout_s=12.5,
        total_budget_s=budget,
        transport=transport,
        sleep=clock.sleep,
        monotonic=clock,
    )


def test_each_attempt_gets_the_remaining_total_budget() -> None:
    clock = FakeClock()
    transport = TimedTransport(clock, (503, b""), (503, b""), (503, b""))
    with pytest.raises(LlmUpstreamError, match="3 lần thử"):
        _budget_client(transport, clock, 30.0).complete(MESSAGES, max_tokens=5)
    # 0 → 12,5 (lần 1) → chờ 0,5 → 13 → 25,5 (lần 2) → chờ 1 → 26,5: còn 3,5 s cho lần 3.
    assert transport.timeouts == [12.5, 12.5, 3.5]
    assert clock.now == 1030.0


def test_no_retry_when_the_remaining_budget_cannot_cover_the_backoff() -> None:
    clock = FakeClock()
    transport = TimedTransport(clock, (503, b""), (200, _ok_body()), spend=12.6)
    with pytest.raises(LlmUpstreamError, match="hết hạn thời gian tổng") as excinfo:
        _budget_client(transport, clock, 13.0).complete(MESSAGES, max_tokens=5)
    assert excinfo.value.attempts == 1 and len(transport.timeouts) == 1 and clock.sleeps == []
    assert "HTTP 503" in str(excinfo.value)


def test_an_exhausted_budget_sends_nothing_more() -> None:
    clock = FakeClock()
    transport = TimedTransport(clock, (200, _ok_body()))
    client = _budget_client(transport, clock, 5.0)
    clock.advance(5.0)
    with pytest.raises(LlmUpstreamError, match="hết hạn thời gian tổng") as excinfo:
        client.complete(MESSAGES, max_tokens=5)
    assert excinfo.value.attempts == 0 and transport.timeouts == []


def test_without_a_total_budget_each_attempt_keeps_the_per_request_timeout() -> None:
    clock = FakeClock()
    transport = TimedTransport(clock, (500, b""), (200, _ok_body("ok")))
    assert _budget_client(transport, clock, None).complete(MESSAGES, max_tokens=5).content == "ok"
    assert transport.timeouts == [12.5, 12.5]


@pytest.mark.parametrize("budget", [0.0, -1.0, float("inf"), float("nan")])
def test_total_budget_must_be_positive_and_finite(budget: float) -> None:
    with pytest.raises(ValueError, match="total_budget_s"):
        OpenAiChatClient(
            base_url=BASE_URL, api_key=SecretStr(SECRET), model=MODEL, timeout_s=1.0, total_budget_s=budget
        )


def test_remaining_budget_never_exceeds_the_total_budget_despite_float_rounding() -> None:
    # Với t0 này, (t0 + 42) - t0 = 42.00000000000364 > 42 do làm tròn; thời gian còn lại phải kẹp về đúng 42.
    t0 = 32765.479414075653
    assert (t0 + 42.0) - t0 > 42.0
    client = OpenAiChatClient(
        base_url=BASE_URL,
        api_key=SecretStr(SECRET),
        model=MODEL,
        timeout_s=12.5,
        total_budget_s=42.0,
        monotonic=lambda: t0,
    )
    assert client._remaining_s() == 42.0


def test_remaining_budget_still_counts_down_after_the_clamp() -> None:
    clock = FakeClock()
    client = _budget_client(TimedTransport(clock), clock, 30.0)
    clock.advance(12.5)
    assert client._remaining_s() == 17.5
    clock.advance(20.0)
    remaining = client._remaining_s()
    assert remaining is not None and remaining == -2.5


def test_a_trickling_server_through_the_real_client_ends_within_the_budget() -> None:
    clock = FakeClock()
    body = TrickleBody(clock, step=0.75)
    client = OpenAiChatClient(
        base_url=BASE_URL,
        api_key=SecretStr(SECRET),
        model=MODEL,
        timeout_s=10.0,
        total_budget_s=25.0,
        transport=UrllibTransport(BodyHttps(body), monotonic=clock),
        sleep=clock.sleep,
        monotonic=clock,
    )
    started = clock()
    with pytest.raises(LlmUpstreamError) as excinfo:
        client.complete(MESSAGES, max_tokens=5)
    assert SECRET not in str(excinfo.value) and "gateway.example.test" not in str(excinfo.value)
    # Hạn tổng 25 s, mỗi lần đọc 0,75 s: không vượt quá hạn quá một lần đọc.
    assert clock() - started <= 25.0 + 0.75
    assert excinfo.value.__cause__ is None and excinfo.value.__context__ is None


def test_from_settings_applies_the_task_deadline_and_the_http_limits() -> None:
    settings = LlmSettings(
        base_url=BASE_URL,
        api_key=SecretStr(SECRET),
        model_extract=MODEL,
        task_limits=LlmTaskLimits(deadline_s=42.0),
        http_limits=LlmHttpLimits(retry_max=0, backoff_base_s=0.25, max_response_bytes=10, read_chunk_bytes=4),
    )
    client = OpenAiChatClient.from_settings(settings, model=MODEL)
    assert "retry_max=0" in repr(client)
    remaining = client._remaining_s()
    assert remaining is not None and 41.0 < remaining <= 42.0
    transport = client._transport
    assert isinstance(transport, UrllibTransport)
    assert transport._limits == settings.http_limits


# --------------------------------------------------------------------------- #
# Router: request nhỏ giọt ⇒ 502 LLM_UPSTREAM_ERROR
# --------------------------------------------------------------------------- #


@pytest.fixture
def phil_store() -> Iterator[tuple[AppStore, str]]:
    store = IngestionStore()
    doc_id = document_id("c")
    add_document(store, doc_id)
    [chunk] = ingest_confirmed_text(
        store, confirmed(doc_id, PHILOSOPHY_PAGES, subject_code="SSH1110", chapter_code="ch1")
    )
    yield store, chunk.chunk_id


def test_propose_maps_a_trickling_gateway_to_502(phil_store: tuple[AppStore, str]) -> None:
    store, chunk_id = phil_store
    clock = FakeClock()
    body = TrickleBody(clock, step=1.0)
    config = load_runtime_config(
        {"HOLOCRON_LLM_API_KEY": SECRET, "HOLOCRON_LLM_BASE_URL": BASE_URL, "HOLOCRON_LLM_MODEL_EXTRACT": MODEL}
    )
    client = OpenAiChatClient(
        base_url=BASE_URL,
        api_key=SecretStr(SECRET),
        model=MODEL,
        timeout_s=config.llm.timeout_s,
        total_budget_s=config.llm.task_limits.deadline_s,
        transport=UrllibTransport(BodyHttps(body), monotonic=clock),
        sleep=clock.sleep,
        monotonic=clock,
    )
    app = FastAPI()
    app.include_router(vault.router)
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_clock] = lambda: lambda: NOW
    app.dependency_overrides[get_runtime_config] = lambda: config
    app.dependency_overrides[vault.get_chat_client] = lambda: client
    app.dependency_overrides[vault.get_monotonic] = lambda: clock
    response = TestClient(app).post(
        "/v1/vault/concept-pairs/propose", json={"subject_code": "SSH1110", "chunk_ids": [chunk_id]}
    )
    assert response.status_code == 502
    assert response.json() == {"detail": {"code": "LLM_UPSTREAM_ERROR", "message": vault.LLM_UPSTREAM_MESSAGE}}
    assert clock.now - 1000.0 <= config.llm.task_limits.deadline_s + 1.0
    assert store.list_concept_pairs("SSH1110") == []


# --------------------------------------------------------------------------- #
# Scheme của base URL và proxy
# --------------------------------------------------------------------------- #

ALLOWED_URLS = [
    "https://gateway.example.test/v1",
    "HTTPS://gateway.example.test/v1",
    "http://127.0.0.1:8080/v1",
    "http://localhost:11434/v1",
    "http://[::1]:8000/v1",
]
REJECTED_URLS = [
    "http://gateway.example.test/v1",
    "http://127.0.0.2/v1",
    "http://localhost.example.test/v1",
    "http://10.0.0.5/v1",
    "ftp://gateway.example.test/v1",
    "file:///etc/passwd",
    "https:///v1",
    "http://[::1/v1",
    "gateway.example.test/v1",
]


@pytest.mark.parametrize("url", ALLOWED_URLS)
@pytest.mark.parametrize("variable", ["HOLOCRON_LLM_BASE_URL", "HOLOCRON_VLM_BASE_URL"])
def test_https_or_loopback_http_base_urls_are_accepted(url: str, variable: str) -> None:
    env = {variable: url, "HOLOCRON_VLM_API_KEY": SECRET, "HOLOCRON_VLM_MODEL": MODEL}
    config = load_runtime_config(env)
    if variable == "HOLOCRON_LLM_BASE_URL":
        assert config.llm.base_url == url
    else:
        assert config.vlm is not None and config.vlm.base_url == url
    client = OpenAiChatClient(base_url=url, api_key=SecretStr(SECRET), model=MODEL, timeout_s=1.0)
    assert client.build_request(MESSAGES, max_tokens=1).full_url == url.rstrip("/") + "/chat/completions"


@pytest.mark.parametrize("url", REJECTED_URLS)
@pytest.mark.parametrize("variable", ["HOLOCRON_LLM_BASE_URL", "HOLOCRON_VLM_BASE_URL"])
def test_other_base_urls_are_config_errors_naming_only_the_variable(url: str, variable: str) -> None:
    env = {variable: url, "HOLOCRON_LLM_API_KEY": SECRET, "HOLOCRON_VLM_API_KEY": SECRET}
    with pytest.raises(ConfigError, match=variable) as excinfo:
        load_runtime_config(env)
    message = str(excinfo.value)
    assert SECRET not in message and "example.test" not in message and "passwd" not in message
    assert "10.0.0.5" not in message and "127.0.0.2" not in message
    assert excinfo.value.__cause__ is None


@pytest.mark.parametrize("url", REJECTED_URLS)
def test_the_client_refuses_the_same_base_urls(url: str) -> None:
    with pytest.raises(ConfigError, match="HOLOCRON_LLM_BASE_URL"):
        OpenAiChatClient(base_url=url, api_key=SecretStr(SECRET), model=MODEL, timeout_s=1.0)


def test_check_base_url_names_the_given_variable() -> None:
    with pytest.raises(ConfigError, match="^HOLOCRON_VLM_BASE_URL không hợp lệ"):
        check_base_url("http://vlm.example.test/v1", "HOLOCRON_VLM_BASE_URL")


def test_the_opener_ignores_system_proxies(monkeypatch: pytest.MonkeyPatch) -> None:
    proxy = "http://proxy.example.test:3128"
    for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        monkeypatch.setenv(name, proxy)
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)

    # Đối chứng: opener mặc định của urllib đưa request qua proxy của môi trường.
    clock = FakeClock()
    control = BodyHttps(FiniteBody(clock, 0.0, _ok_body()))
    urllib.request.build_opener(control).open(_request(), timeout=1.0).close()
    assert control.hosts == ["proxy.example.test:3128"]

    handler = BodyHttps(FiniteBody(clock, 0.0, _ok_body()))
    transport = UrllibTransport(handler)
    # `ProxyHandler({})` thay chỗ handler proxy mặc định; nó không có `*_open` nên không được đăng ký vào opener.
    assert not any(isinstance(h, urllib.request.ProxyHandler) for h in transport.opener.handlers)
    assert transport(_request(), 1.0)[0] == 200
    assert handler.hosts == ["gateway.example.test"]


# --------------------------------------------------------------------------- #
# Giới hạn HTTP là field cấu hình có mặc định
# --------------------------------------------------------------------------- #


def test_http_limits_defaults_keep_the_previous_values() -> None:
    limits = LlmHttpLimits()
    assert (limits.retry_max, limits.backoff_base_s, limits.max_response_bytes) == (2, 0.5, 4 * 1024 * 1024)
    assert (HTTP_RETRY_MAX, BACKOFF_BASE_S, MAX_RESPONSE_BYTES) == (2, 0.5, 4 * 1024 * 1024)
    assert LlmSettings().http_limits == limits
    assert load_runtime_config({}).llm.http_limits == limits


@pytest.mark.parametrize(
    "bad",
    [
        {"retry_max": -1},
        {"backoff_base_s": -0.1},
        {"backoff_base_s": float("inf")},
        {"max_response_bytes": 0},
        {"read_chunk_bytes": 0},
    ],
)
def test_http_limits_reject_invalid_values(bad: dict[str, float]) -> None:
    with pytest.raises(ValidationError):
        LlmHttpLimits.model_validate(bad)


def test_backoff_base_is_configurable() -> None:
    clock = FakeClock()
    transport = TimedTransport(clock, (500, b""), (500, b""), (200, _ok_body("ok")), spend=0.0)
    client = OpenAiChatClient(
        base_url=BASE_URL,
        api_key=SecretStr(SECRET),
        model=MODEL,
        timeout_s=1.0,
        backoff_base_s=0.25,
        transport=transport,
        sleep=clock.sleep,
        monotonic=clock,
    )
    assert client.complete(MESSAGES, max_tokens=5).attempts == 3
    assert clock.sleeps == [0.25, 0.5]


def test_max_response_bytes_is_configurable() -> None:
    clock = FakeClock()
    transport = UrllibTransport(
        BodyHttps(FiniteBody(clock, 0.0, b"x" * 11)), limits=LlmHttpLimits(max_response_bytes=10), monotonic=clock
    )
    with pytest.raises(LlmUpstreamError, match="kích thước"):
        transport(_request(), 1.0)
