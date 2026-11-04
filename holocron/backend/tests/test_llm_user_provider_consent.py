"""Công tắc đồng ý chặn job đề xuất cặp TỰ TẠO gửi tới nhà cung cấp NGƯỜI DÙNG (SPECIFICATION §5.8 "Khi nào chạy",
§5.16; ADR-009 mục 7).

Job tự tạo khi xác nhận tài liệu là lần gửi tự động: chỉ có nhà cung cấp người dùng vai `llm` và công tắc tắt ⇒
không tạo job (như LLM không khả dụng), transport không nhận request nào; công tắc bật ⇒ tạo job qua nhà cung cấp
người dùng. Đường env của job tự tạo giữ hành vi cũ; POST đề xuất thủ công không bị công tắc chặn.

Transport giả, KHÔNG gọi mạng; hàng đợi đọc chạy đồng bộ; văn bản là mẫu tự viết.
"""

import hashlib
import http.client
import json
import os
import socket
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from pydantic import SecretStr

from holocron_core.domain.ai import AiContentKind, IpPolicy, ProviderCandidate, ProviderModels, ProviderPrices
from holocron_core.domain.documents import DocumentImport
from holocron_core.domain.repositories import AppStore
from holocron_core.infrastructure.ai_runtime import InMemorySpendLedger
from holocron_core.infrastructure.llm_config import (
    ConceptProposalSettings,
    LlmHttpLimits,
    RuntimeConfig,
    load_runtime_config,
)
from holocron_core.routers import deps, documents, vault
from holocron_core.routers.api import app
from holocron_core.routers.deps import (
    PROPOSAL_JOBS_STATE_ATTR,
    get_llm_transport_factory,
    get_provider_selection,
    get_runtime_config,
)
from holocron_core.services.concept_proposal_job import ClientFactory, ProposalJobRuntime
from holocron_core.services.document_import import SynchronousImportQueue
from holocron_core.services.provider_selection import ProviderSelection
from tests.document_import_support import FakePdfReader, ImportEnv, install, pdf_bytes
from tests.vault_support import NOW, STORE_KINDS, open_store

USER_KEY = "sk-user-consent-0123456789"
USER_HOST = "llm-user-consent.example.test"
USER_ID = "usr_0000000000c1"
ENV_ONLY = {
    "HOLOCRON_LLM_API_KEY": "sk-test-consent-env-key",
    "HOLOCRON_LLM_BASE_URL": "https://gateway.example.test/v1",
    "HOLOCRON_LLM_MODEL_EXTRACT": "env-model-name",
}
SUBJECT = "ET4010"
EDITOR = "local_user"
WAIT_S = 10.0
EMPTY_REPLY = json.dumps(
    {"id": "x", "choices": [{"index": 0, "message": {"role": "assistant", "content": "[]"}}]}
).encode("utf-8")


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("test không được gọi mạng")

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(http.client.HTTPConnection, "connect", refuse)
    for name in [n for n in os.environ if n in ("HOLOCRON_AI_CONSENT", "HOLOCRON_VLM_MODE")]:
        monkeypatch.delenv(name)


class SwitchStore:
    """Kho công tắc đồng ý trong RAM (`AiConsentStore`), đọc lại ở mỗi lời gọi như kho file."""

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    @property
    def persistent(self) -> bool:
        return True

    def load(self) -> bool | None:
        return self.enabled

    def save(self, enabled: bool) -> None:
        self.enabled = enabled


class FakeTransports:
    """`TransportFactory` giả: ghi mọi request, luôn trả một phản hồi rỗng hợp lệ."""

    def __init__(self) -> None:
        self.policies: list[IpPolicy] = []
        self.requests: list[urllib.request.Request] = []

    def __call__(self, policy: IpPolicy, limits: LlmHttpLimits) -> "FakeTransports.Bound":
        self.policies.append(policy)
        return FakeTransports.Bound(self)

    class Bound:
        def __init__(self, owner: "FakeTransports") -> None:
            self.owner = owner

        def __call__(self, request: urllib.request.Request, timeout_s: float) -> tuple[int, bytes]:
            self.owner.requests.append(request)
            return 200, EMPTY_REPLY


def _user_llm() -> ProviderCandidate:
    return ProviderCandidate(
        provider_id=USER_ID,
        template="custom",
        label="LLM của tôi",
        base_url=f"https://{USER_HOST}/v1",
        api_key=SecretStr(USER_KEY),
        models=ProviderModels(llm="user-llm-model"),
        local=False,
        ip_policy="public_only",
        priority=100,
        budget_usd=1.0,
        prices=ProviderPrices(input_per_1k_usd=0.01, output_per_1k_usd=0.01, per_image_usd=0.0, source="user"),
        budget_group=USER_ID,
        enabled=True,
    )


def _selection(*candidates: ProviderCandidate) -> ProviderSelection:
    frozen = list(candidates)
    return ProviderSelection(lambda: frozen, InMemorySpendLedger())


class Setup:
    def __init__(self, store: AppStore, files_dir: Path) -> None:
        self.env: ImportEnv = install(store, files_dir, SynchronousImportQueue(), FakePdfReader(page_count=2))
        self.jobs = ProposalJobRuntime(
            ConceptProposalSettings(job_batch_chunks=5, job_max_parallel=1), clock=lambda: NOW
        )
        self.switch = SwitchStore(enabled=False)
        self.transports = FakeTransports()
        self.selection = _selection(_user_llm())
        self.config: RuntimeConfig = load_runtime_config({})
        app.dependency_overrides[deps.get_optional_proposal_jobs] = lambda: self.jobs
        app.dependency_overrides[get_runtime_config] = lambda: self.config
        app.dependency_overrides[get_provider_selection] = lambda: self.selection
        app.dependency_overrides[get_llm_transport_factory] = lambda: self.transports
        app.dependency_overrides[deps.get_ai_consent_store] = lambda: self.switch

    def confirm_new_document(self, tag: str) -> tuple[str, DocumentImport]:
        content = pdf_bytes(tag)
        response = self.env.post(content)
        assert response.status_code == 202, response.text
        doc_id = "doc_" + hashlib.sha256(content).hexdigest()[:16]
        confirmed = self.env.client.post(f"/v1/documents/{doc_id}/confirm", json={"editor_id": EDITOR})
        assert confirmed.status_code == 200, confirmed.text
        return doc_id, DocumentImport.model_validate(confirmed.json())

    def doc_jobs(self, doc_id: str) -> list[str]:
        assert self.jobs.wait_idle(WAIT_S)
        return [job.job_id for job in self.jobs.list_jobs(SUBJECT) if job.document_id == doc_id]


@pytest.fixture(params=STORE_KINDS[:1])
def setup(request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Setup]:
    for store in open_store(str(request.param), tmp_path, monkeypatch):
        made = Setup(store, tmp_path / "files")
        try:
            yield made
        finally:
            made.jobs.close()
            app.dependency_overrides.clear()


def test_consent_off_with_only_user_provider_creates_no_job_and_sends_nothing(setup: Setup) -> None:
    doc_id, body = setup.confirm_new_document("dong-y-tat")
    assert body.status == "confirmed"
    assert (body.proposal_job, body.proposal_job_absent_reason) == (None, "llm_unavailable")
    assert setup.doc_jobs(doc_id) == []
    assert setup.transports.requests == [] and setup.transports.policies == []
    assert setup.selection.spent_usd(USER_ID) == 0


def test_consent_on_creates_the_job_through_the_user_provider(setup: Setup) -> None:
    setup.switch.enabled = True
    doc_id, body = setup.confirm_new_document("dong-y-bat")
    assert body.proposal_job is not None and body.proposal_job.trigger == "document_confirmed"
    assert setup.doc_jobs(doc_id) == [body.proposal_job.job_id]
    assert setup.transports.requests, "job gửi qua transport của nhà cung cấp người dùng (override của app)"
    assert all(USER_HOST in request.full_url for request in setup.transports.requests)


def test_background_hook_respects_the_consent_switch(setup: Setup) -> None:
    """Hook nền (cuối lần đọc) đọc công tắc, bộ chọn và transport theo override của app như đường request."""
    doc_id, _ = setup.confirm_new_document("hook-nen")
    background = FastAPI()
    setattr(background.state, PROPOSAL_JOBS_STATE_ATTR, setup.jobs)
    for dependency in (
        get_runtime_config,
        get_provider_selection,
        get_llm_transport_factory,
        deps.get_ai_consent_store,
    ):
        background.dependency_overrides[dependency] = app.dependency_overrides[dependency]
    hook = documents.background_proposal_hook(background, setup.env.store)
    assert hook(doc_id) is None
    assert setup.transports.requests == []
    setup.switch.enabled = True
    job = hook(doc_id)
    assert job is not None and job.trigger == "document_confirmed"
    assert setup.jobs.wait_idle(WAIT_S)
    assert setup.transports.requests


class Off:
    def auto_send_allowed(self, content: AiContentKind) -> bool:
        return False


def _never(config: RuntimeConfig, failures: documents.LocalFailures) -> ClientFactory:
    raise AssertionError("không dựng client khi chỉ tính khả năng")


def test_consent_off_keeps_the_env_path_and_direct_calls_unchanged(setup: Setup) -> None:
    """Đường env của job tự tạo không đổi khi công tắc tắt; lời gọi trực tiếp không truyền công tắc giữ hành vi cũ."""
    store = setup.env.store
    user_only = _selection(_user_llm())
    env_config = load_runtime_config(ENV_ONLY)
    empty = load_runtime_config({})
    assert documents.document_proposals(
        store, env_config, {}, lambda: NOW, None, _never, user_only, Off()
    ).llm_available
    assert not documents.document_proposals(store, empty, {}, lambda: NOW, None, _never, user_only, Off()).llm_available
    assert documents.document_proposals(store, empty, {}, lambda: NOW, None, _never, user_only).llm_available


def test_manual_job_factory_ignores_the_consent_switch(setup: Setup) -> None:
    """POST đề xuất thủ công (`vault.get_chat_client_factory`) không bị công tắc chặn; đường tự tạo thì bị chặn."""
    user_only = _selection(_user_llm())
    empty = load_runtime_config({})
    assert vault.get_chat_client_factory(empty, providers=user_only, transport_for=setup.transports)() is not None
    with pytest.raises(HTTPException) as caught:
        vault.auto_chat_client_factory(empty, {}, user_only, setup.transports, Off())
    assert caught.value.status_code == 409
    assert setup.transports.requests == []
