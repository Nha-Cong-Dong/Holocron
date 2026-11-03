"""Job đề xuất cặp tự tạo khi tài liệu nhập thành `confirmed` (T1b; SPECIFICATION §5.8 "Khi nào chạy", §6.5
`POST …/confirm`, §5.7 quy tắc `proposal_job`).

Kiểm: mỗi đường chốt (xác nhận cả tài liệu, xác nhận trang cuối, cuối lần đọc, đường `failed`) tạo ĐÚNG MỘT job
`trigger = "document_confirmed"` cho mọi chunk của tài liệu; LLM không khả dụng ⇒ không job, vẫn 200; xác nhận lại
không tạo job mới; tạo job lỗi ⇒ vẫn 200; hai lời gọi xác nhận đồng thời ⇒ đúng một job; hook nền thật
(`background_proposal_hook`) đọc runtime, cấu hình và hàm dựng client của app.

Chạy trên cả ba loại kho; LLM là stub, KHÔNG gọi mạng; hàng đợi đọc chạy đồng bộ; văn bản là mẫu tự viết.
"""

import dataclasses
import hashlib
import http.client
import socket
import threading
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from holocron_core.domain.capabilities import CapabilityName, CapabilityReason
from holocron_core.domain.documents import DocumentImport, PageExtraction
from holocron_core.domain.llm import ChatCompletionClient
from holocron_core.domain.repositories import AppStore
from holocron_core.domain.vault import ProposalJob
from holocron_core.infrastructure.document_readers.pdf_reader import PdfInputError
from holocron_core.infrastructure.llm_client import OpenAiChatClient
from holocron_core.infrastructure.llm_config import ConceptProposalSettings, RuntimeConfig, load_runtime_config
from holocron_core.infrastructure.storage import DATA_DIR_ENV
from holocron_core.routers import deps, documents, vault
from holocron_core.routers.api import app
from holocron_core.routers.deps import PROPOSAL_JOBS_STATE_ATTR, get_runtime_config
from holocron_core.routers.documents import get_import_runtime
from holocron_core.services.concept_proposal_job import ClientFactory, ProposalJobRuntime
from holocron_core.services.document_import import ReadJob, SynchronousImportQueue
from tests.document_import_support import FakePdfReader, HoldingQueue, ImportEnv, install, ok_page, pdf_bytes
from tests.proposal_support import EMPTY_REPLY, StubClient
from tests.vault_support import NOW, STORE_KINDS, open_store

CONFIGURED_ENV = {
    "HOLOCRON_LLM_API_KEY": "sk-test-auto-job",
    "HOLOCRON_LLM_BASE_URL": "https://gateway.example.test/v1",
    "HOLOCRON_LLM_MODEL_EXTRACT": "model-x",
}
SUBJECT = "ET4010"
EDITOR = "local_user"
WAIT_S = 10.0

LocalFailures = Mapping[CapabilityName, CapabilityReason]
Builder = Callable[[RuntimeConfig, LocalFailures], ClientFactory]


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("test không được gọi mạng")

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(http.client.HTTPConnection, "connect", refuse)


def _stub_builder(config: RuntimeConfig, failures: LocalFailures) -> ClientFactory:
    def make() -> ChatCompletionClient:
        return StubClient(*[EMPTY_REPLY] * 4)

    return make


@dataclasses.dataclass
class Setup:
    env: ImportEnv
    jobs: ProposalJobRuntime
    held: HoldingQueue
    config: dict[str, RuntimeConfig]
    builds: list[RuntimeConfig]

    def configure(self, configured: bool) -> None:
        self.config["current"] = load_runtime_config(CONFIGURED_ENV if configured else {})

    def doc_jobs(self, doc_id: str) -> list[ProposalJob]:
        assert self.jobs.wait_idle(WAIT_S)
        return [job for job in self.jobs.list_jobs(SUBJECT) if job.document_id == doc_id]


def _make_setup(store: AppStore, files_dir: Path) -> Setup:
    held = HoldingQueue()
    env = install(store, files_dir, SynchronousImportQueue(), FakePdfReader(page_count=2))
    jobs = ProposalJobRuntime(ConceptProposalSettings(job_batch_chunks=5, job_max_parallel=1), clock=lambda: NOW)
    config = {"current": load_runtime_config(CONFIGURED_ENV)}
    builds: list[RuntimeConfig] = []

    def build(cfg: RuntimeConfig, failures: LocalFailures) -> ClientFactory:
        builds.append(cfg)
        return _stub_builder(cfg, failures)

    app.dependency_overrides[deps.get_optional_proposal_jobs] = lambda: jobs
    app.dependency_overrides[get_runtime_config] = lambda: config["current"]
    app.dependency_overrides[documents.get_client_factory_builder] = lambda: build
    return Setup(env=env, jobs=jobs, held=held, config=config, builds=builds)


@pytest.fixture(params=STORE_KINDS)
def setup(request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Setup]:
    for store in open_store(str(request.param), tmp_path, monkeypatch):
        made = _make_setup(store, tmp_path / "files")
        try:
            yield made
        finally:
            made.jobs.close()
            app.dependency_overrides.clear()


def _doc_id(content: bytes) -> str:
    return "doc_" + hashlib.sha256(content).hexdigest()[:16]


def _post(setup: Setup, tag: str, **form: str) -> str:
    content = pdf_bytes(tag)
    response = setup.env.post(content, **form)
    assert response.status_code == 202, response.text
    return _doc_id(content)


def _confirm(client: TestClient, doc_id: str) -> Any:
    return client.post(f"/v1/documents/{doc_id}/confirm", json={"editor_id": EDITOR})


def _confirm_page(setup: Setup, doc_id: str, number: int) -> Any:
    page = setup.env.store.get_page(doc_id, number, PageExtraction)
    assert page is not None
    return setup.env.client.post(
        f"/v1/documents/{doc_id}/pages/{number}/confirm",
        json={"editor_id": EDITOR, "expected_version": page.version},
    )


def _chunk_ids(setup: Setup, doc_id: str) -> list[str]:
    return [chunk.chunk_id for chunk in setup.env.store.list_chunks(SUBJECT) if chunk.doc_id == doc_id]


def _assert_one_auto_job(setup: Setup, doc_id: str) -> ProposalJob:
    [job] = setup.doc_jobs(doc_id)
    assert (job.trigger, job.document_id, job.subject_code) == ("document_confirmed", doc_id, SUBJECT)
    assert job.chunk_count == len(_chunk_ids(setup, doc_id)) >= 1
    return job


def _hold_reads(setup: Setup, hook: Callable[[str], object] | None = None) -> None:
    """Hàng đợi runtime giữ việc đọc (test tự chạy); `hook` là hook "đề xuất sau khi đóng" của runtime."""
    runtime = dataclasses.replace(setup.env.runtime, queue=setup.held, on_document_confirmed=hook)
    app.dependency_overrides[get_import_runtime] = lambda: runtime


# --------------------------------------------------------------------------- #
# Đường chốt 1: POST /v1/documents/{id}/confirm


def test_confirming_the_document_creates_exactly_one_job_for_its_chunks(setup: Setup) -> None:
    doc_id = _post(setup, "chap-nhan")
    response = _confirm(setup.env.client, doc_id)
    assert response.status_code == 200, response.text
    body = DocumentImport.model_validate(response.json())
    assert body.status == "confirmed" and body.proposal_job_absent_reason is None
    assert body.proposal_job is not None and body.proposal_job.document_id == doc_id
    assert body.proposal_job.trigger == "document_confirmed" and body.proposal_job.status in ("queued", "running")
    job = _assert_one_auto_job(setup, doc_id)
    assert job.job_id == body.proposal_job.job_id
    assert job.status == "succeeded"
    assert setup.builds == [setup.config["current"]], "client dựng từ đúng cấu hình của request, một lần"


def test_confirming_again_returns_the_existing_job_and_creates_none(setup: Setup) -> None:
    doc_id = _post(setup, "chap-nhan-lai")
    first = _confirm(setup.env.client, doc_id).json()
    again = _confirm(setup.env.client, doc_id)
    assert again.status_code == 200
    assert again.json()["proposal_job"]["job_id"] == first["proposal_job"]["job_id"]
    assert len(setup.doc_jobs(doc_id)) == 1


def test_without_llm_no_job_is_created_and_confirm_still_succeeds(setup: Setup) -> None:
    setup.configure(False)
    doc_id = _post(setup, "khong-llm")
    response = _confirm(setup.env.client, doc_id)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "confirmed" and _chunk_ids(setup, doc_id)
    assert (body["proposal_job"], body["proposal_job_absent_reason"]) == (None, "llm_unavailable")
    assert setup.doc_jobs(doc_id) == [] and setup.builds == []
    setup.configure(True)
    again = _confirm(setup.env.client, doc_id).json()
    assert (again["proposal_job"], again["proposal_job_absent_reason"]) == (None, "not_retained")
    assert setup.doc_jobs(doc_id) == [], "tài liệu đã confirmed: cấu hình LLM sau đó không tự tạo job"


def test_past_exam_is_confirmed_without_chunks_or_job(setup: Setup) -> None:
    doc_id = _post(setup, "de-cu", source_kind="past_exam")
    body = _confirm(setup.env.client, doc_id).json()
    assert body["status"] == "confirmed"
    assert (body["proposal_job"], body["proposal_job_absent_reason"]) == (None, "past_exam")
    assert _chunk_ids(setup, doc_id) == [] and setup.doc_jobs(doc_id) == []


@pytest.mark.parametrize("failure", ["closed_runtime", "builder_error"])
def test_job_creation_errors_keep_the_confirmation(setup: Setup, failure: str) -> None:
    if failure == "closed_runtime":
        setup.jobs.close()  # `submit` ném RuntimeError
    else:

        def broken(cfg: RuntimeConfig, failures: LocalFailures) -> ClientFactory:
            raise RuntimeError("không dựng được client")

        app.dependency_overrides[documents.get_client_factory_builder] = lambda: broken
    doc_id = _post(setup, f"loi-{failure}")
    response = _confirm(setup.env.client, doc_id)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "confirmed" and _chunk_ids(setup, doc_id)
    assert (body["proposal_job"], body["proposal_job_absent_reason"]) == (None, "not_retained")
    assert setup.jobs.list_jobs(SUBJECT) == []


def test_without_a_job_runtime_confirm_still_succeeds(setup: Setup) -> None:
    app.dependency_overrides.pop(deps.get_optional_proposal_jobs)  # app không qua lifespan: không có runtime job
    doc_id = _post(setup, "khong-runtime")
    body = _confirm(setup.env.client, doc_id).json()
    assert body["status"] == "confirmed"
    assert (body["proposal_job"], body["proposal_job_absent_reason"]) == (None, "not_retained")


def test_two_concurrent_confirms_create_exactly_one_job(setup: Setup) -> None:
    doc_id = _post(setup, "song-song")
    barrier = threading.Barrier(2)
    results: list[int] = []

    def call() -> None:
        client = TestClient(app)
        barrier.wait(WAIT_S)
        results.append(_confirm(client, doc_id).status_code)

    threads = [threading.Thread(target=call) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(WAIT_S * 3)
    assert results == [200, 200]
    _assert_one_auto_job(setup, doc_id)


# --------------------------------------------------------------------------- #
# Đường chốt 2: xác nhận trang cuối


def test_confirming_the_last_page_creates_exactly_one_job(setup: Setup) -> None:
    doc_id = _post(setup, "trang-cuoi")
    assert _confirm_page(setup, doc_id, 1).status_code == 200
    assert setup.doc_jobs(doc_id) == [], "còn trang chưa xác nhận: tài liệu chưa đóng"
    response = _confirm_page(setup, doc_id, 2)
    assert response.status_code == 200
    _assert_one_auto_job(setup, doc_id)
    assert _confirm_page(setup, doc_id, 2).status_code == 200, "trang đã xác nhận: 200, không job mới"
    assert len(setup.doc_jobs(doc_id)) == 1
    body = setup.env.client.get(f"/v1/documents/{doc_id}").json()
    assert body["status"] == "confirmed" and body["proposal_job"]["trigger"] == "document_confirmed"


def test_confirming_the_last_page_without_llm_creates_no_job(setup: Setup) -> None:
    setup.configure(False)
    doc_id = _post(setup, "trang-cuoi-khong-llm")
    for number in (1, 2):
        assert _confirm_page(setup, doc_id, number).status_code == 200
    assert setup.doc_jobs(doc_id) == []


# --------------------------------------------------------------------------- #
# Đường chốt 3–4: cuối lần đọc và đường `failed` (luồng nền, hook của ImportRuntime)


@dataclasses.dataclass
class FirstPageOnly(FakePdfReader):
    """Trả trang đầu được yêu cầu rồi ném lỗi: trang còn lại giữ `pending` (như worker chết giữa chừng)."""

    def read_pages(self, path: Path, document_id: str, page_numbers: Any = None) -> Iterator[PageExtraction]:
        numbers = list(range(1, self.page_count + 1)) if page_numbers is None else list(page_numbers)
        yield ok_page(document_id, numbers[0])
        raise RuntimeError("worker chết giữa chừng")


def _first_page_confirmed_while_reading(setup: Setup, tag: str) -> tuple[str, ReadJob]:
    """Trang 1 đã đọc và đã xác nhận trong lúc trang 2 còn `pending`; trả việc đọc mà router đã dựng."""
    doc_id = _post(setup, tag)
    [job] = setup.held.release()
    SynchronousImportQueue().submit(dataclasses.replace(job, reader=FirstPageOnly(page_count=2)))
    assert _confirm_page(setup, doc_id, 1).status_code == 200
    assert setup.env.client.get(f"/v1/documents/{doc_id}").json()["status"] == "processing"
    return doc_id, job


def _background_app(setup: Setup, *, configured: bool = True) -> FastAPI:
    """App giả cho hook nền thật: runtime job trên `app.state`, cấu hình và hàm dựng client qua override của app."""
    background = FastAPI()
    setattr(background.state, PROPOSAL_JOBS_STATE_ATTR, setup.jobs)
    chosen = load_runtime_config(CONFIGURED_ENV if configured else {})
    background.dependency_overrides[get_runtime_config] = lambda: chosen
    background.dependency_overrides[documents.get_client_factory_builder] = lambda: _stub_builder
    return background


@pytest.mark.parametrize("ending", ["reading_ends", "reading_fails"])
def test_background_settle_creates_exactly_one_job_through_the_runtime_hook(setup: Setup, ending: str) -> None:
    hook = documents.background_proposal_hook(_background_app(setup), setup.env.store)
    _hold_reads(setup, hook)
    doc_id, job = _first_page_confirmed_while_reading(setup, f"nen-{ending}")
    assert job.on_document_confirmed is hook, "việc đọc mang hook của runtime"
    reader = (
        FakePdfReader(page_count=2, failed_pages=frozenset({2}))
        if ending == "reading_ends"
        else FakePdfReader(page_count=2, read_error=PdfInputError("OPEN_FAILED"))
    )
    SynchronousImportQueue().submit(dataclasses.replace(job, reader=reader))
    body = setup.env.client.get(f"/v1/documents/{doc_id}").json()
    assert body["status"] == "confirmed"
    _assert_one_auto_job(setup, doc_id)
    assert body["proposal_job"]["document_id"] == doc_id


def test_background_hook_without_llm_or_runtime_creates_no_job(setup: Setup) -> None:
    unconfigured = documents.background_proposal_hook(_background_app(setup, configured=False), setup.env.store)
    _hold_reads(setup, unconfigured)
    doc_id, job = _first_page_confirmed_while_reading(setup, "nen-khong-llm")
    SynchronousImportQueue().submit(dataclasses.replace(job, reader=FakePdfReader(failed_pages=frozenset({2}))))
    assert setup.env.client.get(f"/v1/documents/{doc_id}").json()["status"] == "confirmed"
    assert setup.doc_jobs(doc_id) == []
    no_runtime = documents.background_proposal_hook(FastAPI(), setup.env.store)
    assert no_runtime(doc_id) is None, "app không có runtime job (đang tắt): không tạo job, không lỗi"


def test_background_hook_error_does_not_break_the_read(setup: Setup) -> None:
    calls: list[str] = []

    def broken(document_id: str) -> None:
        calls.append(document_id)
        raise RuntimeError("hook lỗi")

    _hold_reads(setup, broken)
    doc_id, job = _first_page_confirmed_while_reading(setup, "nen-hook-loi")
    SynchronousImportQueue().submit(dataclasses.replace(job, reader=FakePdfReader(failed_pages=frozenset({2}))))
    assert calls == [doc_id]
    assert setup.env.client.get(f"/v1/documents/{doc_id}").json()["status"] == "confirmed"


def test_hook_runs_after_the_document_lock_is_released(setup: Setup) -> None:
    """Hook chạy SAU khi nhả khoá của tài liệu: một luồng khác lấy được khoá ngay trong hook (không giữ hai khoá)."""
    acquired: list[bool] = []

    def probe(document_id: str) -> None:
        def take() -> None:
            with setup.env.runtime.locks.hold(document_id):
                acquired.append(True)

        other = threading.Thread(target=take)
        other.start()
        other.join(WAIT_S)
        acquired.append(not other.is_alive())

    _hold_reads(setup, probe)
    _, job = _first_page_confirmed_while_reading(setup, "nen-nha-khoa")
    SynchronousImportQueue().submit(dataclasses.replace(job, reader=FakePdfReader(failed_pages=frozenset({2}))))
    assert acquired == [True, True]


def test_startup_requeue_carries_the_runtime_hook(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    """`requeue_on_startup` đưa hook của runtime vào việc đọc xếp lại (đường chốt cuối lần đọc sau khi khởi động)."""
    calls: list[str] = []
    runtime = dataclasses.replace(setup.env.runtime, queue=setup.held, on_document_confirmed=calls.append)
    app.dependency_overrides[get_import_runtime] = lambda: runtime
    doc_id = _post(setup, "xep-lai")
    setup.held.release()
    monkeypatch.setattr(documents, "load_pdf_reader", lambda config: FakePdfReader())
    store: AppStore = setup.env.store
    assert documents.requeue_on_startup(store, runtime) == [doc_id]
    [requeued] = setup.held.release()
    assert requeued.on_document_confirmed is runtime.on_document_confirmed


def test_default_client_factory_builder_is_the_vault_job_builder() -> None:
    """Hàm dựng client mặc định là CÙNG hàm của `POST /v1/vault/concept-pairs/jobs`: kiểm khả năng, không gọi mạng."""
    builder = documents.get_client_factory_builder()
    assert builder is vault.get_chat_client_factory
    factory = builder(load_runtime_config(CONFIGURED_ENV), {})
    assert isinstance(factory(), OpenAiChatClient)
    with pytest.raises(HTTPException) as raised:
        builder(load_runtime_config({}), {})
    assert raised.value.status_code == 409


def test_lifespan_gives_the_import_runtime_the_background_hook(monkeypatch: pytest.MonkeyPatch) -> None:
    """`lifespan` gắn hook nền thật vào bộ nhập; hook không lỗi với tài liệu không có (không tạo job)."""
    monkeypatch.delenv(DATA_DIR_ENV, raising=False)
    deps._default_store.cache_clear()
    try:
        with TestClient(app):
            runtime = getattr(app.state, documents.RUNTIME_STATE_ATTR)
            assert runtime.on_document_confirmed is not None
            assert runtime.on_document_confirmed("doc_0000000000000000") is None
            jobs = getattr(app.state, PROPOSAL_JOBS_STATE_ATTR)
            assert jobs.list_jobs(SUBJECT) == []
    finally:
        deps._default_store.cache_clear()
