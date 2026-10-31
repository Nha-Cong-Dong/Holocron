"""Job nền đề xuất cặp (`services/concept_proposal_job.py`, `/v1/vault/concept-pairs/jobs`; spec §5.8, §6.6; ADR-007
mục 3). LLM là stub; KHÔNG gọi mạng.

Kiểm: lô ≤ cấu hình, song song ≤ cấu hình trên toàn runtime, FIFO, huỷ (lô chưa chạy không chạy, lô đang chạy chạy
hết và cặp vẫn lưu), lỗi một lô không làm hỏng lô khác, tiến độ do backend đếm, suy trạng thái, giữ tối đa N job đã
kết thúc, và mã lỗi của endpoint.
"""

import http.client
import socket
import threading
from collections.abc import Callable, Iterator, Sequence

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from holocron_core.domain.llm import ChatCompletionClient, ChatMessage, LlmUpstreamError
from holocron_core.domain.repositories import AppStore, ImportedDocument
from holocron_core.domain.schemas import DocumentMeta, KnowledgeChunk, SourceKind
from holocron_core.domain.vault import ProposalJob
from holocron_core.infrastructure.llm_client import OpenAiChatClient
from holocron_core.infrastructure.llm_config import (
    ConceptProposalSettings,
    LlmTaskLimits,
    RuntimeConfig,
    load_runtime_config,
)
from holocron_core.infrastructure.memory_store import IngestionStore
from holocron_core.routers import vault
from holocron_core.routers.api import app as api_app
from holocron_core.routers.deps import (
    PROPOSAL_JOBS_STATE_ATTR,
    PROPOSAL_JOBS_UNAVAILABLE_DETAIL,
    get_clock,
    get_proposal_jobs,
    get_runtime_config,
    get_store,
)
from holocron_core.services.concept_proposal_job import (
    ProposalJobQueueFullError,
    ProposalJobRuntime,
    _JobState,
    derive_status,
)
from holocron_core.services.concept_proposal_pointer import BatchOutcome
from holocron_core.services.vault import ingest_confirmed_text
from tests.proposal_support import DEF_PROCESS, EMPTY_REPLY, SUBJECT, StubClient, pointer, reply, seed, span_of
from tests.vault_support import NOW, add_document, confirmed, document_id

CONFIGURED_ENV = {
    "HOLOCRON_LLM_API_KEY": "sk-test-job",
    "HOLOCRON_LLM_BASE_URL": "https://gateway.example.test/v1",
    "HOLOCRON_LLM_MODEL_EXTRACT": "model-x",
}
JOBS_URL = "/v1/vault/concept-pairs/jobs"
WAIT_S = 10.0


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("test không được gọi mạng")

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(http.client.HTTPConnection, "connect", refuse)


def _many_chunks(store: AppStore, count: int, subject_code: str = SUBJECT) -> list[KnowledgeChunk]:
    chunks: list[KnowledgeChunk] = []
    for index in range(count):
        doc = document_id(f"{index:x}" if subject_code == SUBJECT else f"{(index + 8):x}")
        add_document(store, doc)
        lines = [f"Đoạn {index}: Khái niệm số {index} là một định nghĩa mẫu để kiểm thử."]
        chunks += ingest_confirmed_text(store, confirmed(doc, [lines], subject_code=subject_code, chapter_code="ch1"))
    return chunks


def _runtime(**settings: object) -> ProposalJobRuntime:
    values: dict[str, object] = {"job_batch_chunks": 1, "job_max_parallel": 1}
    values.update(settings)
    return ProposalJobRuntime(ConceptProposalSettings.model_validate(values), clock=lambda: NOW)


def _submit(
    runtime: ProposalJobRuntime,
    store: AppStore,
    chunks: Sequence[KnowledgeChunk],
    factory: Callable[[], ChatCompletionClient],
    limits: LlmTaskLimits | None = None,
) -> ProposalJob:
    return runtime.submit(
        subject_code=SUBJECT,
        chunk_ids=[chunk.chunk_id for chunk in chunks],
        document_id=None,
        store=store,
        client_factory=factory,
        limits=limits or LlmTaskLimits(),
        now=NOW,
    )


def _finished(runtime: ProposalJobRuntime, job: ProposalJob) -> ProposalJob:
    assert runtime.wait_idle(WAIT_S)
    current = runtime.get(job.job_id)
    assert current is not None
    return current


@pytest.fixture
def runtimes() -> Iterator[list[ProposalJobRuntime]]:
    made: list[ProposalJobRuntime] = []
    yield made
    for runtime in made:
        runtime.close()


class Gate:
    """Chặn lời gọi LLM cho tới khi `release`; đếm số lời gọi chạy đồng thời và thứ tự chunk được gửi."""

    def __init__(self) -> None:
        self.release_event = threading.Event()
        self.lock = threading.Lock()
        self.active = 0
        self.max_active = 0
        self.started = threading.Semaphore(0)
        self.order: list[str] = []

    def respond(self, messages: Sequence[ChatMessage]) -> str:
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.order.append(messages[1].content)
        self.started.release()
        self.release_event.wait(WAIT_S)
        with self.lock:
            self.active -= 1
        return EMPTY_REPLY

    def client(self) -> StubClient:
        return StubClient(self.respond)

    def wait_started(self, count: int) -> None:
        for _ in range(count):
            assert self.started.acquire(timeout=WAIT_S)


# --------------------------------------------------------------------------- #
# Runtime
# --------------------------------------------------------------------------- #


def test_batches_never_exceed_the_parallel_limit(runtimes: list[ProposalJobRuntime]) -> None:
    store = IngestionStore()
    chunks = _many_chunks(store, 5)
    runtime = _runtime(job_max_parallel=2)
    runtimes.append(runtime)
    gate = Gate()
    first = _submit(runtime, store, chunks[:3], gate.client)
    second = _submit(runtime, store, chunks[3:], gate.client)
    gate.wait_started(2)
    assert gate.max_active == 2 and len(gate.order) == 2  # lô thứ ba chờ dù thuộc job khác
    gate.release_event.set()
    for job in (first, second):
        done = _finished(runtime, job)
        assert done.status == "succeeded" and done.batches_done == done.batches_total
    assert gate.max_active == 2 and len(gate.order) == 5


def test_batch_size_follows_the_configuration(runtimes: list[ProposalJobRuntime]) -> None:
    store = IngestionStore()
    chunks = _many_chunks(store, 5)
    runtime = _runtime(job_batch_chunks=2)
    runtimes.append(runtime)
    sent: list[int] = []

    def respond(messages: Sequence[ChatMessage]) -> str:
        sent.append(sum(chunk.chunk_id in messages[1].content for chunk in chunks))
        return EMPTY_REPLY

    job = _submit(runtime, store, [*chunks, chunks[0]], lambda: StubClient(respond))
    assert (job.chunk_count, job.batch_size, job.batches_total, job.status) == (5, 2, 3, "queued")
    assert _finished(runtime, job).batches_done == 3
    assert sorted(sent) == [1, 2, 2]


def test_jobs_run_first_in_first_out(runtimes: list[ProposalJobRuntime]) -> None:
    store = IngestionStore()
    chunks = _many_chunks(store, 3)
    runtime = _runtime()
    runtimes.append(runtime)
    gate = Gate()
    first = _submit(runtime, store, chunks[:2], gate.client)
    second = _submit(runtime, store, chunks[2:], gate.client)
    gate.release_event.set()
    _finished(runtime, second)
    sent = [next(i for i, chunk in enumerate(chunks) if chunk.chunk_id in text) for text in gate.order]
    assert sent == [0, 1, 2]
    assert _finished(runtime, first).status == "succeeded"


def test_cancel_skips_queued_batches_and_keeps_pairs_of_the_running_one(runtimes: list[ProposalJobRuntime]) -> None:
    store = IngestionStore()
    chunks = seed(store)
    runtime = _runtime()
    runtimes.append(runtime)
    release = threading.Event()
    started = threading.Event()
    calls: list[int] = []

    def respond(messages: Sequence[ChatMessage]) -> str:
        calls.append(1)
        started.set()
        release.wait(WAIT_S)
        s = span_of(chunks[:1], DEF_PROCESS)
        return reply(pointer(s, "Tiến trình", [s], "Tiến trình là", "bộ nhớ."))

    job = _submit(runtime, store, chunks, lambda: StubClient(respond))
    assert started.wait(WAIT_S)
    cancelled = runtime.cancel(job.job_id)
    assert cancelled is not None and cancelled.cancel_requested and cancelled.status == "running"
    release.set()
    done = _finished(runtime, job)
    assert (done.status, done.batches_done, done.batches_failed, done.proposed_count) == ("cancelled", 1, 0, 1)
    assert done.finished_at == NOW and len(calls) == 1
    assert [pair.term for pair in store.list_concept_pairs(SUBJECT)] == ["Tiến trình"]
    again = runtime.cancel(job.job_id)
    assert again == done  # huỷ job đã kết thúc: không đổi gì


def test_cancelling_a_queued_job_finishes_it_at_once(runtimes: list[ProposalJobRuntime]) -> None:
    store = IngestionStore()
    chunks = _many_chunks(store, 2)
    runtime = _runtime()
    runtimes.append(runtime)
    gate = Gate()
    running = _submit(runtime, store, chunks[:1], gate.client)
    queued = _submit(runtime, store, chunks[1:], gate.client)
    gate.wait_started(1)
    cancelled = runtime.cancel(queued.job_id)
    assert cancelled is not None and cancelled.status == "cancelled" and cancelled.started_at is None
    gate.release_event.set()
    assert _finished(runtime, running).status == "succeeded"
    assert len(gate.order) == 1


def test_a_failing_batch_does_not_break_the_others(runtimes: list[ProposalJobRuntime]) -> None:
    store = IngestionStore()
    chunks = _many_chunks(store, 3)
    runtime = _runtime()
    runtimes.append(runtime)
    clients = iter(
        [StubClient(LlmUpstreamError("lỗi", attempts=1)), StubClient("hỏng", "vẫn hỏng"), StubClient(EMPTY_REPLY)]
    )
    done = _finished(runtime, _submit(runtime, store, chunks, lambda: next(clients), LlmTaskLimits(json_retry_max=1)))
    assert (done.batches_done, done.batches_failed, done.status) == (1, 2, "partial")
    assert done.batch_errors == {"llm_upstream": 1, "format": 1}


def test_status_is_failed_when_every_batch_fails(runtimes: list[ProposalJobRuntime]) -> None:
    store = IngestionStore()
    chunks = _many_chunks(store, 2)
    runtime = _runtime()
    runtimes.append(runtime)
    done = _finished(runtime, _submit(runtime, store, chunks, lambda: StubClient(LlmUpstreamError("x", attempts=1))))
    assert (done.batches_done, done.batches_failed, done.status) == (0, 2, "failed")


def test_status_is_partial_when_some_batches_fail(runtimes: list[ProposalJobRuntime]) -> None:
    store = IngestionStore()
    chunks = seed(store)
    runtime = _runtime()
    runtimes.append(runtime)
    s = span_of(chunks[:1], DEF_PROCESS)
    good = reply(
        pointer(s, "Tiến trình", [s], "Tiến trình là", "bộ nhớ."),
        {"chunk_id": chunks[0].chunk_id, "is_exercise": False},
    )
    clients = iter([StubClient(good), StubClient(LlmUpstreamError("lỗi", attempts=1))])
    job = _submit(runtime, store, chunks, lambda: next(clients))
    done = _finished(runtime, job)
    assert (done.status, done.batches_done, done.batches_failed) == ("partial", 1, 1)
    assert done.batch_errors == {"llm_upstream": 1}
    assert done.proposed_count == 1 and store.list_concept_pairs(SUBJECT)[0].term == "Tiến trình"


def test_unexpected_error_in_a_batch_only_fails_that_batch(runtimes: list[ProposalJobRuntime]) -> None:
    store = IngestionStore()
    chunks = _many_chunks(store, 2)
    runtime = _runtime()
    runtimes.append(runtime)
    calls: list[int] = []

    def factory() -> StubClient:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("không dựng được client")
        return StubClient(EMPTY_REPLY)

    done = _finished(runtime, _submit(runtime, store, chunks, factory))
    assert (done.status, done.batches_done, done.batches_failed, done.batch_errors) == ("partial", 1, 1, {})


def test_progress_counts_pairs_rejections_exercise_chunks_and_pre_selection(
    runtimes: list[ProposalJobRuntime],
) -> None:
    store = IngestionStore()
    chunks = seed(store)
    runtime = _runtime(job_batch_chunks=2, pair_preselect_enabled=True)
    runtimes.append(runtime)
    s = span_of(chunks, DEF_PROCESS)
    response = reply(
        pointer(s, "Tiến trình", [s], "Tiến trình là", "bộ nhớ."),
        pointer(s, "Không có", [s], "Tiến trình là", "bộ nhớ."),
        pointer("S99", "x", ["S99"], "a", "b"),
        {"chunk_id": chunks[0].chunk_id, "is_exercise": False},
        {"chunk_id": chunks[1].chunk_id, "is_exercise": True},
    )
    done = _finished(runtime, _submit(runtime, store, chunks, lambda: StubClient(response)))
    assert (done.proposed_count, done.pre_selected_count, done.exercise_chunks) == (1, 1, 1)
    assert done.rejected_by_reason == {"term_not_in_span": 1, "unknown_span": 1} and done.rejected_count == 2
    assert (done.status, done.started_at, done.finished_at) == ("succeeded", NOW, NOW)
    [pair] = store.list_concept_pairs(SUBJECT)
    assert pair.pre_selected and pair.status.value == "proposed"


def test_deleted_chunks_are_skipped_without_calling_the_llm(runtimes: list[ProposalJobRuntime]) -> None:
    store = IngestionStore()
    chunks = _many_chunks(store, 1)
    runtime = _runtime()
    runtimes.append(runtime)
    store.delete_document(chunks[0].doc_id)
    client = StubClient()
    done = _finished(runtime, _submit(runtime, store, chunks, lambda: client))
    assert done.status == "succeeded" and client.calls == []


@pytest.mark.parametrize(
    ("done", "failed", "cancel", "status"),
    [(3, 0, False, "succeeded"), (2, 1, False, "partial"), (0, 3, False, "failed"), (1, 0, True, "cancelled")],
)
def test_derive_status(done: int, failed: int, cancel: bool, status: str) -> None:
    job = ProposalJob(
        job_id="pjob_0123456789abcdef",
        subject_code=SUBJECT,
        trigger="manual",
        document_id=None,
        status="running",
        chunk_count=3,
        batch_size=1,
        batches_total=3,
        batches_done=done,
        batches_failed=failed,
        proposed_count=0,
        pre_selected_count=0,
        rejected_count=0,
        exercise_chunks=0,
        cancel_requested=cancel,
        created_at=NOW,
    )
    assert derive_status(job) == status


def test_only_the_most_recent_finished_jobs_are_kept(runtimes: list[ProposalJobRuntime]) -> None:
    store = IngestionStore()
    chunks = _many_chunks(store, 1)
    runtime = _runtime(job_keep_finished=3)
    runtimes.append(runtime)
    jobs = [_finished(runtime, _submit(runtime, store, chunks, lambda: StubClient(EMPTY_REPLY))) for _ in range(5)]
    assert [runtime.get(job.job_id) is not None for job in jobs] == [False, False, True, True, True]
    assert [job.job_id for job in runtime.list_jobs(SUBJECT)] == [job.job_id for job in reversed(jobs[2:])]
    assert runtime.list_jobs("ZZ1000") == []


def test_default_settings_match_the_spec() -> None:
    settings = ConceptProposalSettings()
    assert (settings.job_batch_chunks, settings.job_max_parallel, settings.job_keep_finished) == (20, 2, 100)
    with pytest.raises(ValueError):
        ConceptProposalSettings(job_batch_chunks=21)


def test_closed_runtime_refuses_new_jobs() -> None:
    store = IngestionStore()
    chunks = _many_chunks(store, 1)
    runtime = _runtime()
    runtime.close()
    with pytest.raises(RuntimeError):
        _submit(runtime, store, chunks, lambda: StubClient(EMPTY_REPLY))
    with pytest.raises(ValueError):
        _submit(_runtime(), store, [], lambda: StubClient(EMPTY_REPLY))


# --------------------------------------------------------------------------- #
# Endpoint
# --------------------------------------------------------------------------- #


def _app(
    store: AppStore,
    runtime: ProposalJobRuntime,
    client: StubClient | None,
    configured: bool = True,
    config: RuntimeConfig | None = None,
) -> TestClient:
    app = FastAPI()
    app.include_router(vault.router)
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_clock] = lambda: lambda: NOW
    chosen = config or load_runtime_config(CONFIGURED_ENV if configured else {})
    app.dependency_overrides[get_runtime_config] = lambda: chosen
    app.dependency_overrides[get_proposal_jobs] = lambda: runtime
    if client is not None:
        app.dependency_overrides[vault.get_chat_client_factory] = lambda: lambda: client
    return TestClient(app)


def _import(store: AppStore, doc: str, subject_code: str = SUBJECT) -> None:
    store.save_imported_document(
        ImportedDocument(
            meta=DocumentMeta(
                doc_id=doc,
                subject_code=subject_code,
                title="Tài liệu",
                source_kind=SourceKind.TEXTBOOK,
                sha256=(doc[4:] * 4)[:64],
                page_count=1,
                has_text_layer=True,
            ),
            filename="tai_lieu.pdf",
            size_bytes=10,
            chapter_code="ch1",
            status="confirmed",
        )
    )


def test_create_job_returns_202_with_location_then_runs(runtimes: list[ProposalJobRuntime]) -> None:
    store = IngestionStore()
    chunks = seed(store)
    runtime = _runtime()
    runtimes.append(runtime)
    client = _app(store, runtime, StubClient(EMPTY_REPLY, EMPTY_REPLY))
    response = client.post(JOBS_URL, json={"subject_code": SUBJECT, "chunk_ids": [c.chunk_id for c in chunks]})
    assert response.status_code == 202
    body = response.json()
    assert (body["status"], body["trigger"], body["document_id"], body["chunk_count"]) == ("queued", "manual", None, 2)
    assert response.headers["location"] == f"{JOBS_URL}/{body['job_id']}"
    assert runtime.wait_idle(WAIT_S)
    polled = client.get(f"{JOBS_URL}/{body['job_id']}")
    assert polled.status_code == 200 and polled.json()["status"] == "succeeded"
    listed = client.get(JOBS_URL, params={"subject_code": SUBJECT}).json()
    assert [job["job_id"] for job in listed] == [body["job_id"]]
    cancelled = client.post(f"{JOBS_URL}/{body['job_id']}/cancel")
    assert cancelled.status_code == 200 and cancelled.json() == polled.json()


def test_create_job_for_a_document_uses_every_chunk_of_it(runtimes: list[ProposalJobRuntime]) -> None:
    store = IngestionStore()
    chunks = seed(store)
    _import(store, chunks[0].doc_id)
    runtime = _runtime()
    runtimes.append(runtime)
    response = _app(store, runtime, StubClient(EMPTY_REPLY)).post(
        JOBS_URL, json={"subject_code": SUBJECT, "document_id": chunks[0].doc_id}
    )
    assert response.status_code == 202
    assert (response.json()["document_id"], response.json()["chunk_count"]) == (chunks[0].doc_id, 1)
    assert runtime.wait_idle(WAIT_S)


def test_create_job_without_llm_is_a_409_capability_error_and_queues_nothing(
    runtimes: list[ProposalJobRuntime],
) -> None:
    store = IngestionStore()
    chunks = seed(store)
    runtime = _runtime()
    runtimes.append(runtime)
    response = _app(store, runtime, None, configured=False).post(
        JOBS_URL, json={"subject_code": SUBJECT, "chunk_ids": [chunks[0].chunk_id]}
    )
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert (detail["code"], detail["capability"]) == ("CAPABILITY_UNAVAILABLE", "concept_pair_llm_proposal")
    assert runtime.list_jobs(SUBJECT) == []


def test_create_job_errors(runtimes: list[ProposalJobRuntime]) -> None:
    store = IngestionStore()
    chunks = seed(store)
    other = _many_chunks(store, 1, subject_code="SSH1110")
    _import(store, chunks[1].doc_id)
    empty_doc = document_id("a")
    add_document(store, empty_doc)
    _import(store, empty_doc)
    foreign_doc = document_id("b")
    add_document(store, foreign_doc)
    _import(store, foreign_doc, subject_code="SSH1110")
    runtime = _runtime()
    runtimes.append(runtime)
    client = _app(store, runtime, StubClient())
    cases: list[tuple[dict[str, object], int]] = [
        ({"subject_code": SUBJECT, "chunk_ids": ["chk_khong_co"]}, 404),
        ({"subject_code": SUBJECT, "document_id": "doc_cccccccccccccccc"}, 404),
        ({"subject_code": SUBJECT, "document_id": empty_doc}, 409),
        ({"subject_code": SUBJECT, "document_id": foreign_doc}, 422),
        ({"subject_code": SUBJECT, "chunk_ids": [other[0].chunk_id]}, 422),
        ({"subject_code": SUBJECT}, 422),
        ({"subject_code": SUBJECT, "document_id": chunks[1].doc_id, "chunk_ids": [chunks[0].chunk_id]}, 422),
        ({"subject_code": SUBJECT, "chunk_ids": []}, 422),
        ({"subject_code": SUBJECT, "chunk_ids": ["chk_x"] * 2001}, 422),
        ({"subject_code": "sai", "chunk_ids": [chunks[0].chunk_id]}, 422),
    ]
    for body, status in cases:
        assert client.post(JOBS_URL, json=body).status_code == status, body
    assert runtime.list_jobs(SUBJECT) == []


def test_unknown_or_lost_jobs_are_404(runtimes: list[ProposalJobRuntime]) -> None:
    store = IngestionStore()
    chunks = seed(store)
    before_restart = _runtime()
    runtimes.append(before_restart)
    created = _app(store, before_restart, StubClient(EMPTY_REPLY, EMPTY_REPLY)).post(
        JOBS_URL, json={"subject_code": SUBJECT, "chunk_ids": [chunks[0].chunk_id]}
    )
    job_id = created.json()["job_id"]
    after_restart = _runtime()
    runtimes.append(after_restart)
    client = _app(store, after_restart, StubClient())
    assert client.get(f"{JOBS_URL}/{job_id}").status_code == 404
    assert client.post(f"{JOBS_URL}/{job_id}/cancel").status_code == 404
    assert client.get(f"{JOBS_URL}/pjob_0000000000000000").status_code == 404
    assert client.get(JOBS_URL).status_code == 422
    assert client.get(JOBS_URL, params={"subject_code": SUBJECT}).json() == []


def test_factory_dependency_builds_a_fresh_real_client_per_call_without_calling_it() -> None:
    factory = vault.get_chat_client_factory(load_runtime_config(CONFIGURED_ENV))
    first, second = factory(), factory()
    assert isinstance(first, OpenAiChatClient) and isinstance(second, OpenAiChatClient) and first is not second


def test_jobs_live_with_the_app_lifespan_and_are_lost_on_restart() -> None:
    """Runtime do `lifespan` tạo (với `config.llm.proposal`) và đóng khi tắt app; khởi động lại ⇒ job cũ 404."""
    store = get_store()
    chunks = _many_chunks(store, 1)
    settings = ConceptProposalSettings(job_max_parallel=3, job_keep_finished=7)
    base = load_runtime_config(CONFIGURED_ENV)
    config = base.model_copy(update={"llm": base.llm.model_copy(update={"proposal": settings})})
    overrides: dict[Callable[..., object], Callable[..., object]] = {
        get_runtime_config: lambda: config,
        vault.get_chat_client_factory: lambda: lambda: StubClient(EMPTY_REPLY),
    }
    api_app.dependency_overrides.update(overrides)
    try:
        with TestClient(api_app) as client:
            jobs = getattr(api_app.state, PROPOSAL_JOBS_STATE_ATTR)
            assert isinstance(jobs, ProposalJobRuntime) and jobs.settings == settings
            created = client.post(JOBS_URL, json={"subject_code": SUBJECT, "chunk_ids": [chunks[0].chunk_id]})
            assert created.status_code == 202
            job_id = created.json()["job_id"]
            assert jobs.wait_idle(WAIT_S)
            assert client.get(f"{JOBS_URL}/{job_id}").json()["status"] == "succeeded"
        assert not hasattr(api_app.state, PROPOSAL_JOBS_STATE_ATTR)
        with pytest.raises(RuntimeError):  # đã đóng khi tắt app
            _submit(jobs, store, chunks, lambda: StubClient(EMPTY_REPLY))
        unavailable = TestClient(api_app).get(f"{JOBS_URL}/{job_id}")  # không qua lifespan
        assert unavailable.status_code == 500
        assert unavailable.json()["detail"] == PROPOSAL_JOBS_UNAVAILABLE_DETAIL
        with TestClient(api_app) as client:
            assert client.get(f"{JOBS_URL}/{job_id}").status_code == 404
    finally:
        for key in overrides:
            api_app.dependency_overrides.pop(key, None)


def test_each_job_uses_the_request_configuration(runtimes: list[ProposalJobRuntime]) -> None:
    store = IngestionStore()
    chunks = seed(store)
    runtime = _runtime(job_batch_chunks=20)
    runtimes.append(runtime)
    s = span_of(chunks, DEF_PROCESS)
    labels = [{"chunk_id": chunk.chunk_id, "is_exercise": False} for chunk in chunks]
    good = reply(pointer(s, "Tiến trình", [s], "Tiến trình là", "bộ nhớ."), *labels)
    base = load_runtime_config(CONFIGURED_ENV)
    settings = ConceptProposalSettings(job_batch_chunks=1, pair_preselect_enabled=True)
    config = base.model_copy(update={"llm": base.llm.model_copy(update={"proposal": settings})})
    client = _app(store, runtime, StubClient(good, EMPTY_REPLY), config=config)
    response = client.post(JOBS_URL, json={"subject_code": SUBJECT, "chunk_ids": [c.chunk_id for c in chunks]})
    assert (response.json()["batch_size"], response.json()["batches_total"]) == (1, 2)
    assert runtime.wait_idle(WAIT_S)
    done = client.get(f"{JOBS_URL}/{response.json()['job_id']}").json()
    assert (done["proposed_count"], done["pre_selected_count"]) == (1, 1)


def test_too_many_active_jobs_are_refused(runtimes: list[ProposalJobRuntime]) -> None:
    store = IngestionStore()
    chunks = _many_chunks(store, 2)
    runtime = _runtime(job_max_active=1)
    runtimes.append(runtime)
    gate = Gate()
    first = _submit(runtime, store, chunks[:1], gate.client)
    with pytest.raises(ProposalJobQueueFullError):
        _submit(runtime, store, chunks[1:], gate.client)
    refused = _app(store, runtime, StubClient()).post(
        JOBS_URL, json={"subject_code": SUBJECT, "chunk_ids": [chunks[1].chunk_id]}
    )
    assert refused.status_code == 409 and "job" in refused.json()["detail"]
    gate.release_event.set()
    assert _finished(runtime, first).status == "succeeded"
    again = _submit(runtime, store, chunks[1:], lambda: StubClient(EMPTY_REPLY))
    assert _finished(runtime, again).status == "succeeded"


def test_failure_while_recording_a_batch_fails_the_job_and_keeps_the_worker(
    runtimes: list[ProposalJobRuntime], monkeypatch: pytest.MonkeyPatch
) -> None:
    store = IngestionStore()
    chunks = _many_chunks(store, 3)
    runtime = _runtime()
    runtimes.append(runtime)
    real = runtime._record
    calls: list[int] = []

    def flaky(state: _JobState, outcome: BatchOutcome | None) -> None:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("hỏng khi cộng")
        real(state, outcome)

    monkeypatch.setattr(runtime, "_record", flaky)
    broken = _finished(runtime, _submit(runtime, store, chunks[:2], lambda: StubClient(EMPTY_REPLY)))
    assert broken.status == "failed" and broken.finished_at == NOW
    healthy = _finished(runtime, _submit(runtime, store, chunks[2:], lambda: StubClient(EMPTY_REPLY)))
    assert healthy.status == "succeeded"
    assert len(calls) == 2  # lô thứ hai của job hỏng không chạy


def _job(**changes: object) -> dict[str, object]:
    base: dict[str, object] = {
        "job_id": "pjob_0123456789abcdef",
        "subject_code": SUBJECT,
        "trigger": "manual",
        "document_id": None,
        "status": "running",
        "chunk_count": 3,
        "batch_size": 20,
        "batches_total": 3,
        "batches_done": 1,
        "batches_failed": 1,
        "proposed_count": 2,
        "pre_selected_count": 2,
        "rejected_count": 1,
        "rejected_by_reason": {"duplicate": 1},
        "exercise_chunks": 0,
        "created_at": NOW,
    }
    return {**base, **changes}


def test_proposal_job_model_accepts_boundaries() -> None:
    ProposalJob.model_validate(_job())
    ProposalJob.model_validate(_job(batch_size=1, chunk_count=1, batches_done=2, batches_failed=1))


@pytest.mark.parametrize(
    "changes",
    [
        {"rejected_count": 2},
        {"rejected_by_reason": {}},
        {"batches_done": 2, "batches_failed": 2},
        {"pre_selected_count": 3},
        {"batch_size": 21},
        {"batch_size": 0},
        {"chunk_count": 0},
        {"job_id": "pjob_0123456789abcdefx"},
        {"job_id": "xpjob_0123456789abcdef"},
        {"job_id": "pjob_0123456789ABCDEF"},
    ],
)
def test_proposal_job_model_rejects_inconsistent_counts_and_ids(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ProposalJob.model_validate(_job(**changes))
