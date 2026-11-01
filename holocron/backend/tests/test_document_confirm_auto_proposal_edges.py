"""Biên của job đề xuất cặp tự tạo khi tài liệu `confirmed` và của số đếm tóm tắt (T1b, T1e; SPECIFICATION §5.7 quy
tắc `proposal_job` và thứ tự lý do vắng, §5.8 "Khi nào chạy", §6.5, §6.6).

Bổ sung cho `test_document_confirm_auto_proposal.py` và `test_document_import_summary.py`: các test đó đi qua router
(lỗi nuốt ở `notify_confirmed`/`DocumentProposals.start` không lộ ra), nên ở đây gọi thẳng service với hook ghi lại
lời gọi, và dùng runtime job ghi lại các lần `submit` (giới hạn, cấu hình, danh sách chunk, client) để kiểm giá trị
thật sự được truyền.

Chạy trên các loại kho; LLM là stub, KHÔNG gọi mạng; hàng đợi đọc giữ hoặc chạy đồng bộ; văn bản là mẫu tự viết.
"""

import dataclasses
import http.client
import socket
from collections.abc import Callable, Iterator, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from holocron_core.domain.documents import (
    DocumentConfirmRequest,
    DocumentImport,
    PageConfirmRequest,
    PageExtraction,
)
from holocron_core.domain.llm import ChatCompletionClient
from holocron_core.domain.schemas import SourceKind
from holocron_core.domain.vault import ProposalJob
from holocron_core.infrastructure.document_readers.pdf_reader import PdfInputError
from holocron_core.infrastructure.llm_config import (
    ConceptProposalSettings,
    LlmTaskLimits,
    RuntimeConfig,
    load_runtime_config,
)
from holocron_core.infrastructure.storage import DATA_DIR_ENV
from holocron_core.routers import deps, documents
from holocron_core.routers.api import app
from holocron_core.routers.deps import PROPOSAL_JOBS_STATE_ATTR, get_runtime_config
from holocron_core.routers.documents import get_import_runtime
from holocron_core.services.concept_proposal_job import (
    ClientFactory,
    DocumentProposals,
    ProposalJobRuntime,
    ProposalStore,
)
from holocron_core.services.document_import import (
    ReadJob,
    SynchronousImportQueue,
    confirm_document,
    confirm_page,
    proposal_status,
    read_document,
    requeue_pending,
)
from tests.document_import_support import FakePdfReader, pdf_bytes
from tests.proposal_support import EMPTY_REPLY, StubClient
from tests.test_document_confirm_auto_proposal import (
    CONFIGURED_ENV,
    EDITOR,
    SUBJECT,
    WAIT_S,
    FirstPageOnly,
    Setup,
    _doc_id,
    _make_setup,
)
from tests.test_document_import_summary import _confirmed_without_chunks
from tests.vault_support import NOW, open_store

LocalFailures = Any


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("test không được gọi mạng")

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(http.client.HTTPConnection, "connect", refuse)


@dataclasses.dataclass(frozen=True)
class Submission:
    subject_code: str
    chunk_ids: list[str]
    document_id: str | None
    client_factory: ClientFactory
    limits: LlmTaskLimits
    settings: ConceptProposalSettings | None
    trigger: str
    now: datetime


class RecordingJobs(ProposalJobRuntime):
    """Runtime job thật, ghi lại mọi lần `submit` (kể cả lần sau đó ném lỗi vì runtime đã đóng)."""

    def __init__(self) -> None:
        super().__init__(ConceptProposalSettings(job_batch_chunks=5, job_max_parallel=1), clock=lambda: NOW)
        self.calls: list[Submission] = []

    def submit(
        self,
        *,
        subject_code: str,
        chunk_ids: Sequence[str],
        document_id: str | None,
        store: ProposalStore,
        client_factory: ClientFactory,
        limits: LlmTaskLimits,
        now: datetime,
        settings: ConceptProposalSettings | None = None,
        trigger: Literal["document_confirmed", "manual"] = "manual",
    ) -> ProposalJob:
        self.calls.append(
            Submission(subject_code, list(chunk_ids), document_id, client_factory, limits, settings, trigger, now)
        )
        return super().submit(
            subject_code=subject_code,
            chunk_ids=chunk_ids,
            document_id=document_id,
            store=store,
            client_factory=client_factory,
            limits=limits,
            now=now,
            settings=settings,
            trigger=trigger,
        )


@pytest.fixture(params=["memory", "sqlite"])
def setup(request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Setup]:
    for store in open_store(str(request.param), tmp_path, monkeypatch):
        made = _make_setup(store, tmp_path / "files")
        try:
            yield made
        finally:
            made.jobs.close()
            app.dependency_overrides.clear()


@pytest.fixture
def recording() -> Iterator[RecordingJobs]:
    jobs = RecordingJobs()
    try:
        yield jobs
    finally:
        jobs.close()


def _stub_factory() -> ChatCompletionClient:
    return StubClient(*[EMPTY_REPLY] * 4)


def _stub_builder(config: RuntimeConfig, failures: LocalFailures) -> ClientFactory:
    return _stub_factory


def _post(setup: Setup, tag: str, **form: str) -> str:
    content = pdf_bytes(tag)
    response = setup.env.post(content, **form)
    assert response.status_code == 202, response.text
    return _doc_id(content)


def _confirm(setup: Setup, doc_id: str) -> Any:
    return setup.env.client.post(f"/v1/documents/{doc_id}/confirm", json={"editor_id": EDITOR})


def _confirmed_doc(setup: Setup, tag: str, **form: str) -> str:
    """Tài liệu `confirmed` đã cắt chunk nhưng KHÔNG có job tự tạo (LLM chưa cấu hình lúc xác nhận)."""
    setup.configure(False)
    doc_id = _post(setup, tag, **form)
    response = _confirm(setup, doc_id)
    assert response.status_code == 200 and response.json()["status"] == "confirmed"
    assert setup.jobs.list_jobs(SUBJECT) == []
    return doc_id


def _chunk_ids(setup: Setup, doc_id: str) -> list[str]:
    chunks = [chunk.chunk_id for chunk in setup.env.store.list_chunks(SUBJECT) if chunk.doc_id == doc_id]
    assert chunks
    return chunks


def _proposals(setup: Setup, jobs: ProposalJobRuntime | None, **fields: Any) -> DocumentProposals:
    values: dict[str, Any] = {
        "store": setup.env.store,
        "jobs": jobs,
        "llm_available": True,
        "make_client_factory": lambda: _stub_factory,
        "clock": lambda: NOW,
    }
    values.update(fields)
    return DocumentProposals(**values)


def _update_document(setup: Setup, doc_id: str, **changes: Any) -> None:
    document = setup.env.store.get_imported_document(doc_id)
    assert document is not None
    setup.env.store.update_imported_document(document.model_copy(update=changes), document.version)


# --------------------------------------------------------------------------- #
# Đường chốt ở bộ nhập: hook chỉ chạy khi lần gọi này VỪA đóng tài liệu (DI12b, DI15b, DI21, DI24, DI27)


def _held_job(setup: Setup, tag: str) -> tuple[str, ReadJob]:
    """Tài liệu `processing` với việc đọc mà router đã dựng (hàng đợi giữ, chưa chạy); chưa có hook."""
    runtime = dataclasses.replace(setup.env.runtime, queue=setup.held)
    app.dependency_overrides[get_import_runtime] = lambda: runtime
    doc_id = _post(setup, tag)
    [job] = setup.held.release()
    return doc_id, job


def _confirm_page_now(setup: Setup, doc_id: str, number: int, hook: Callable[[str], object] | None) -> PageExtraction:
    page = setup.env.store.get_page(doc_id, number, PageExtraction)
    assert page is not None
    return confirm_page(
        setup.env.store,
        setup.env.store,
        setup.env.runtime,
        doc_id,
        number,
        PageConfirmRequest(editor_id=EDITOR, expected_version=page.version),
        now=NOW,
        on_document_confirmed=hook,
    )


def _status(setup: Setup, doc_id: str) -> str:
    document = setup.env.store.get_imported_document(doc_id)
    assert document is not None
    return document.status


def _first_page_confirmed_while_reading(setup: Setup, tag: str) -> tuple[str, ReadJob]:
    """Trang 1 đã đọc và xác nhận, trang 2 còn `pending` (bộ đọc chết giữa chừng); chưa gọi hook nào."""
    doc_id, job = _held_job(setup, tag)
    read_document(dataclasses.replace(job, reader=FirstPageOnly(page_count=2)))
    _confirm_page_now(setup, doc_id, 1, None)
    assert _status(setup, doc_id) == "processing"
    return doc_id, job


def test_confirm_page_hook_runs_only_when_the_last_page_closes_the_document(setup: Setup) -> None:
    calls: list[str] = []
    doc_id, job = _held_job(setup, "hook-trang")
    read_document(job)
    _confirm_page_now(setup, doc_id, 1, calls.append)
    assert calls == [] and _status(setup, doc_id) == "review", "còn trang chưa xác nhận: tài liệu chưa đóng"
    _confirm_page_now(setup, doc_id, 2, calls.append)
    assert calls == [doc_id] and _status(setup, doc_id) == "confirmed"
    _confirm_page_now(setup, doc_id, 2, calls.append)
    assert calls == [doc_id], "tài liệu đã đóng từ trước: không báo lại"


def test_read_document_does_not_notify_while_pages_remain_unconfirmed(setup: Setup) -> None:
    calls: list[str] = []
    doc_id, job = _held_job(setup, "doc-xong-chua-xac-nhan")
    read_document(dataclasses.replace(job, on_document_confirmed=calls.append))
    assert _status(setup, doc_id) == "review"
    assert calls == [], "đọc xong nhưng chưa trang nào được xác nhận: tài liệu chưa đóng"


def test_read_document_notifies_once_when_the_read_closes_the_document(setup: Setup) -> None:
    calls: list[str] = []
    doc_id, job = _first_page_confirmed_while_reading(setup, "doc-dong-khi-doc")
    reader = FakePdfReader(page_count=2, failed_pages=frozenset({2}))
    read_document(dataclasses.replace(job, reader=reader, on_document_confirmed=calls.append))
    assert _status(setup, doc_id) == "confirmed" and calls == [doc_id]


def _raising_hook(calls: list[str]) -> Callable[[str], None]:
    def hook(document_id: str) -> None:
        calls.append(document_id)
        raise RuntimeError("hook lỗi")

    return hook


def test_a_failing_hook_never_changes_confirm_page(setup: Setup) -> None:
    calls: list[str] = []
    doc_id, job = _held_job(setup, "hook-loi-trang")
    read_document(job)
    _confirm_page_now(setup, doc_id, 1, _raising_hook(calls))
    page = _confirm_page_now(setup, doc_id, 2, _raising_hook(calls))
    assert page.confirmed and calls == [doc_id] and _status(setup, doc_id) == "confirmed"


@dataclasses.dataclass(frozen=True)
class RaisingProposals(DocumentProposals):
    seen: list[str] = dataclasses.field(default_factory=list)

    def start(self, document_id: str) -> ProposalJob | None:
        self.seen.append(document_id)
        raise RuntimeError("tạo job lỗi")


def test_a_failing_proposals_start_never_changes_confirm_document(setup: Setup) -> None:
    doc_id, job = _held_job(setup, "hook-loi-tai-lieu")
    read_document(job)
    proposals = RaisingProposals(store=setup.env.store, jobs=None, llm_available=False)
    body = confirm_document(
        setup.env.store,
        setup.env.store,
        setup.env.runtime,
        doc_id,
        DocumentConfirmRequest(editor_id=EDITOR),
        now=NOW,
        proposals=proposals,
    )
    assert body.status == "confirmed" and proposals.seen == [doc_id]


def test_a_failing_hook_never_changes_read_document(setup: Setup) -> None:
    calls: list[str] = []
    doc_id, job = _first_page_confirmed_while_reading(setup, "hook-loi-doc")
    reader = FakePdfReader(page_count=2, failed_pages=frozenset({2}))
    read_document(dataclasses.replace(job, reader=reader, on_document_confirmed=_raising_hook(calls)))
    assert _status(setup, doc_id) == "confirmed" and calls == [doc_id]


def test_requeue_with_missing_original_file_notifies_once(setup: Setup) -> None:
    calls: list[str] = []
    doc_id, _ = _first_page_confirmed_while_reading(setup, "mat-file-goc")
    document = setup.env.store.get_imported_document(doc_id)
    assert document is not None and setup.env.files.delete(document.meta.sha256)
    queued = requeue_pending(
        setup.env.store,
        setup.env.files,
        FakePdfReader(),
        SynchronousImportQueue(),
        vault=setup.env.store,
        locks=setup.env.runtime.locks,
        on_document_confirmed=calls.append,
    )
    assert queued == []
    assert _status(setup, doc_id) == "confirmed", "mọi trang có chữ đã xác nhận: đường failed vẫn đóng được tài liệu"
    assert calls == [doc_id]


@pytest.mark.parametrize("error", [KeyError("lỗi lạ"), PdfInputError("OPEN_FAILED"), FileNotFoundError("mất file")])
def test_reader_failure_that_closes_the_document_notifies_once(setup: Setup, error: Exception) -> None:
    calls: list[str] = []
    doc_id, job = _first_page_confirmed_while_reading(setup, f"loi-doc-{type(error).__name__}")
    reader = FakePdfReader(page_count=2, read_error=error)
    SynchronousImportQueue().submit(dataclasses.replace(job, reader=reader, on_document_confirmed=calls.append))
    assert _status(setup, doc_id) == "confirmed", "trang 2 thành failed, trang 1 đã xác nhận: tài liệu đóng"
    assert calls == [doc_id]


# --------------------------------------------------------------------------- #
# Lý do vắng job và job của runtime (DI6b, CH2, DP2)


def test_runtime_job_is_returned_even_when_the_document_is_not_confirmed(setup: Setup) -> None:
    """§5.7: có job của tài liệu trong runtime ⇒ `proposal_job`, xét TRƯỚC `not_confirmed`."""
    doc_id = _confirmed_doc(setup, "job-truoc-trang-thai")
    manual = setup.jobs.submit(
        subject_code=SUBJECT,
        chunk_ids=_chunk_ids(setup, doc_id),
        document_id=doc_id,
        store=setup.env.store,
        client_factory=_stub_factory,
        limits=LlmTaskLimits(),
        now=NOW,
    )
    _update_document(setup, doc_id, status="review")
    body = setup.env.client.get(f"/v1/documents/{doc_id}").json()
    assert body["status"] == "review"
    assert (body["proposal_job"] or {}).get("job_id") == manual.job_id
    assert body["proposal_job_absent_reason"] is None
    document = setup.env.store.get_imported_document(doc_id)
    assert document is not None
    job, reason = proposal_status(document, setup.env.store, _proposals(setup, setup.jobs))
    assert reason is None and job is not None and job.job_id == manual.job_id
    assert proposal_status(document, setup.env.store, _proposals(setup, None)) == (None, "not_confirmed")


def test_patching_the_title_keeps_the_vault_view_for_the_absent_reason(setup: Setup) -> None:
    doc_id = _confirmed_doc(setup, "doi-ten-chuong")
    assert setup.env.client.get(f"/v1/documents/{doc_id}").json()["proposal_job_absent_reason"] == "llm_unavailable"
    patched = setup.env.client.patch(
        f"/v1/documents/{doc_id}/chapter", json={"editor_id": EDITOR, "chapter_title": "Tên chương mới"}
    )
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert (body["proposal_job"], body["proposal_job_absent_reason"]) == (None, "llm_unavailable"), (
        "tài liệu có chunk: lý do không được rơi về `no_chunks`"
    )


@pytest.mark.parametrize("kind", ["memory", "sqlite"])
def test_app_lifespan_provides_the_job_runtime_to_the_import_router(
    kind: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Không override `get_optional_proposal_jobs`: xác nhận qua app thật tạo job trong runtime của `app.state`."""
    monkeypatch.delenv(DATA_DIR_ENV, raising=False)
    deps._default_store.cache_clear()
    try:
        for store in open_store(kind, tmp_path, monkeypatch):
            made = _make_setup(store, tmp_path / "files")
            app.dependency_overrides.pop(deps.get_optional_proposal_jobs)
            made.jobs.close()
            try:
                with TestClient(app) as client:
                    runtime = getattr(app.state, PROPOSAL_JOBS_STATE_ATTR)
                    assert isinstance(runtime, ProposalJobRuntime) and runtime is not made.jobs
                    content = pdf_bytes("lifespan-that")
                    posted = client.post(
                        "/v1/documents",
                        data={"subject_code": SUBJECT, "source_kind": "lecture_slides", "chapter_code": "ch3"},
                        files={"file": ("vi_xu_ly.pdf", content, "application/pdf")},
                    )
                    assert posted.status_code == 202, posted.text
                    doc_id = _doc_id(content)
                    response = client.post(f"/v1/documents/{doc_id}/confirm", json={"editor_id": EDITOR})
                    assert response.status_code == 200, response.text
                    body = DocumentImport.model_validate(response.json())
                    assert body.proposal_job is not None and body.proposal_job_absent_reason is None
                    assert runtime.wait_idle(WAIT_S)
                    latest = runtime.latest_for_document(doc_id)
                    assert latest is not None and latest.job_id == body.proposal_job.job_id
            finally:
                app.dependency_overrides.clear()
    finally:
        deps._default_store.cache_clear()


# --------------------------------------------------------------------------- #
# DocumentProposals.start (CP2b, CP2c, CP3, CP4b, CP8, CP12, CP13)


def test_start_submits_the_documents_chunks_with_its_own_limits_and_settings(
    setup: Setup, recording: RecordingJobs
) -> None:
    doc_id = _confirmed_doc(setup, "start-gia-tri")
    limits = LlmTaskLimits(max_tokens=321, deadline_s=12.5)
    settings = ConceptProposalSettings(job_batch_chunks=3, job_max_parallel=1)
    job = _proposals(setup, recording, limits=limits, settings=settings).start(doc_id)
    assert job is not None and job.trigger == "document_confirmed" and job.document_id == doc_id
    [call] = recording.calls
    assert call.limits == limits and call.limits != LlmTaskLimits(), (
        "giới hạn của DocumentProposals, không phải mặc định"
    )
    assert call.settings == settings, "cấu hình của DocumentProposals, không phải None"
    assert call.client_factory is _stub_factory and call.now == NOW
    assert (call.subject_code, call.document_id, call.trigger) == (SUBJECT, doc_id, "document_confirmed")
    assert call.chunk_ids == _chunk_ids(setup, doc_id)


def test_start_ignores_a_past_exam(setup: Setup, recording: RecordingJobs) -> None:
    doc_id = _confirmed_doc(setup, "start-de-cu")
    _chunk_ids(setup, doc_id)
    document = setup.env.store.get_imported_document(doc_id)
    assert document is not None
    meta = document.meta.model_copy(update={"source_kind": SourceKind.PAST_EXAM})
    _update_document(setup, doc_id, meta=meta)
    assert _proposals(setup, recording).start(doc_id) is None
    assert recording.calls == []


def test_start_ignores_a_document_that_is_not_confirmed(setup: Setup, recording: RecordingJobs) -> None:
    doc_id = _confirmed_doc(setup, "start-chua-xac-nhan")
    _chunk_ids(setup, doc_id)
    _update_document(setup, doc_id, status="review")
    assert _proposals(setup, recording).start(doc_id) is None
    assert recording.calls == []


def test_start_does_not_submit_a_document_without_chunks(setup: Setup, recording: RecordingJobs) -> None:
    doc_id = "doc_" + "e" * 16
    _confirmed_without_chunks(setup)
    assert setup.env.store.get_imported_document(doc_id) is not None
    assert _proposals(setup, recording).start(doc_id) is None
    assert recording.calls == [], "không có chunk: không được xếp job (kể cả job sẽ bị từ chối)"


def test_start_only_takes_chunks_of_its_own_document(setup: Setup, recording: RecordingJobs) -> None:
    first = _confirmed_doc(setup, "start-hai-tai-lieu-a", chapter_code="ch3")
    second = _confirmed_doc(setup, "start-hai-tai-lieu-b", chapter_code="ch4")
    mine, other = _chunk_ids(setup, first), _chunk_ids(setup, second)
    assert not set(mine) & set(other)
    assert _proposals(setup, recording).start(first) is not None
    [call] = recording.calls
    assert sorted(call.chunk_ids) == sorted(mine) and not set(call.chunk_ids) & set(other)


@pytest.mark.parametrize("failure", ["closed_runtime", "client_factory_error"])
def test_start_swallows_any_error(setup: Setup, recording: RecordingJobs, failure: str) -> None:
    doc_id = _confirmed_doc(setup, f"start-loi-{failure}")

    def broken() -> ClientFactory:
        raise RuntimeError("không dựng được client")

    if failure == "closed_runtime":
        recording.close()
        proposals = _proposals(setup, recording)
    else:
        proposals = _proposals(setup, recording, make_client_factory=broken)
    assert proposals.start(doc_id) is None
    assert recording.list_jobs(SUBJECT) == []


# --------------------------------------------------------------------------- #
# Router: cấu hình của request và của hook nền (RT2, RT3, RT12)


def _tuned_config() -> RuntimeConfig:
    base = load_runtime_config(CONFIGURED_ENV)
    llm = base.llm.model_copy(
        update={
            "task_limits": LlmTaskLimits(max_tokens=321, deadline_s=12.5),
            "proposal": ConceptProposalSettings(job_batch_chunks=3, job_max_parallel=1),
        }
    )
    return base.model_copy(update={"llm": llm})


def test_confirm_request_uses_the_configured_limits_and_proposal_settings(
    setup: Setup, recording: RecordingJobs
) -> None:
    config = _tuned_config()
    assert config.llm.task_limits != LlmTaskLimits() and config.llm.proposal != ConceptProposalSettings()
    setup.config["current"] = config
    app.dependency_overrides[deps.get_optional_proposal_jobs] = lambda: recording
    doc_id = _post(setup, "cau-hinh-request")
    assert _confirm(setup, doc_id).status_code == 200
    [call] = recording.calls
    assert call.limits == config.llm.task_limits, "RuntimeConfig.llm.task_limits tới jobs.submit"
    assert call.settings == config.llm.proposal, "RuntimeConfig.llm.proposal tới jobs.submit"


def _background_app(recording: RecordingJobs, config: RuntimeConfig, builder: Any = _stub_builder) -> FastAPI:
    background = FastAPI()
    setattr(background.state, PROPOSAL_JOBS_STATE_ATTR, recording)
    background.dependency_overrides[get_runtime_config] = lambda: config
    background.dependency_overrides[documents.get_client_factory_builder] = lambda: builder
    return background


def test_background_hook_uses_the_configured_limits_and_proposal_settings(
    setup: Setup, recording: RecordingJobs
) -> None:
    doc_id = _confirmed_doc(setup, "cau-hinh-nen")
    config = _tuned_config()
    hook = documents.background_proposal_hook(_background_app(recording, config), setup.env.store)
    assert hook(doc_id) is not None
    [call] = recording.calls
    assert call.limits == config.llm.task_limits and call.settings == config.llm.proposal
    assert (call.document_id, call.trigger) == (doc_id, "document_confirmed")


def test_background_hook_builds_the_client_with_the_app_override(setup: Setup, recording: RecordingJobs) -> None:
    doc_id = _confirmed_doc(setup, "nen-override-client")
    built: list[RuntimeConfig] = []

    def marker() -> ChatCompletionClient:
        return StubClient(*[EMPTY_REPLY] * 4)

    def builder(config: RuntimeConfig, failures: LocalFailures) -> ClientFactory:
        built.append(config)
        return marker

    config = load_runtime_config(CONFIGURED_ENV)
    hook = documents.background_proposal_hook(_background_app(recording, config, builder), setup.env.store)
    assert hook(doc_id) is not None
    [call] = recording.calls
    assert call.client_factory is marker, "hàm dựng client lấy theo dependency_overrides của app"
    assert built == [config]
