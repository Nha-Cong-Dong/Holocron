"""Dependency injection cho các router: kho dữ liệu, Vault, extractor, cấu hình chạy, author, nguồn trích dẫn, đồng hồ.

Test thay phụ thuộc bằng `app.dependency_overrides[get_xxx]`; mọi router dùng đúng các hàm ở module này.
"""

from __future__ import annotations

import os
import unicodedata
from collections.abc import Callable
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request

from ..domain.ai import AiConsent, AiConsentStore
from ..domain.repositories import AppStore, VaultRepository
from ..infrastructure.ai_consent_store import AiSettingsFile, FileAiConsentStore, UnpersistedAiConsentStore
from ..infrastructure.ai_runtime import EnvAiConsent, EnvProviderSource, FileSpendLedger, InMemorySpendLedger
from ..infrastructure.llm_config import RuntimeConfig, load_runtime_config_lenient
from ..infrastructure.provider_probe import ProviderProbe
from ..infrastructure.provider_store import (
    CombinedProviderSource,
    FileProviderStore,
    ProviderStore,
    UnpersistedProviderStore,
)
from ..infrastructure.storage import data_dir_from_env, open_store_from_env
from ..infrastructure.template_library import InMemoryTemplateRegistry
from ..launcher_logging import LOG_DIR_ENV, BuildInfo, read_build_info
from ..services.concept_proposal_job import ProposalJobRuntime
from ..services.extraction import DocumentExtractor, MockDualPassExtractor
from ..services.pipeline import TemplateAuthor
from ..services.provider_selection import ProviderSelection
from ..services.twin_batch import CitationProvider, NoCitationProvider, UnconfiguredTemplateAuthor

REGISTRY = InMemoryTemplateRegistry()

_EXTRACTOR = MockDualPassExtractor()


@cache
def _default_store() -> AppStore:
    """Kho mặc định, mở một lần cho cả tiến trình: SQLite khi đặt `HOLOCRON_DATA_DIR`, ngược lại in-memory."""
    return open_store_from_env()


def get_store() -> AppStore:
    return _default_store()


PROPOSAL_JOBS_STATE_ATTR = "proposal_jobs"
PROPOSAL_JOBS_UNAVAILABLE_DETAIL = "Job đề xuất cặp chưa sẵn sàng: ứng dụng đang khởi động hoặc đang tắt."


def create_proposal_job_runtime(config: RuntimeConfig) -> ProposalJobRuntime:
    """Runtime job đề xuất cặp của một app (§5.8): `lifespan` tạo khi khởi động, gắn vào `app.state`, đóng khi tắt
    (trước khi đóng kho). Cấu hình lấy từ `config.llm.proposal`, cùng nguồn với từng request."""
    return ProposalJobRuntime(config.llm.proposal)


def get_proposal_jobs(request: Request) -> ProposalJobRuntime:
    """Runtime job của app. Không có (`lifespan` chưa chạy hoặc đã gỡ khi tắt app) ⇒ 500 kèm `detail`, như bộ nhập."""
    jobs = getattr(request.app.state, PROPOSAL_JOBS_STATE_ATTR, None)
    if isinstance(jobs, ProposalJobRuntime):
        return jobs
    raise HTTPException(status_code=500, detail=PROPOSAL_JOBS_UNAVAILABLE_DETAIL)


def find_proposal_jobs(application: FastAPI) -> ProposalJobRuntime | None:
    """Runtime job của app nếu `lifespan` đã tạo (và chưa gỡ); không có ⇒ `None` (không lỗi)."""
    jobs = getattr(application.state, PROPOSAL_JOBS_STATE_ATTR, None)
    return jobs if isinstance(jobs, ProposalJobRuntime) else None


def get_optional_proposal_jobs(request: Request) -> ProposalJobRuntime | None:
    """Runtime job cho bộ nhập tài liệu (T1b, T1e): khác `get_proposal_jobs`, app chạy không qua `lifespan` nhận `None`
    thay vì 500, vì xác nhận và xem tài liệu không được hỏng khi chưa có runtime job (khi đó không tạo job, và
    `DocumentImport` báo lý do vắng job)."""
    return find_proposal_jobs(request.app)


def get_extractor() -> DocumentExtractor:
    # Adapter VLM thật thuộc T2-N5. Cho tới lúc đó mọi cấu hình đều dùng Mock; `RuntimeInfo.demo_mode` suy ra từ
    # extractor đang chạy nên vẫn đúng khi đã đặt biến VLM mà chưa có adapter.
    return _EXTRACTOR


def get_runtime_config() -> RuntimeConfig:
    """Cấu hình chạy từ biến môi trường. Nhóm LLM hay VLM có biến sai (ví dụ khoá có khoảng trắng, base URL không phải
    https) thì coi như nhóm đó chưa cấu hình, không ném lỗi: `GET /v1/runtime` vẫn trả 200 với `llm_configured` /
    `vlm_configured = false`, endpoint cần LLM trả 409 `CapabilityError` (`reason = "llm_not_configured"`). Không giá
    trị biến nào được lộ ra response hay log."""
    return load_runtime_config_lenient()


@cache
def _default_build_info() -> BuildInfo:
    return read_build_info()


def get_build_info() -> BuildInfo:
    """Version và commit của bản đang chạy (đọc `holocron_build.json` một lần); thiếu/hỏng thì giá trị lùi."""
    return _default_build_info()


def get_log_dir() -> str | None:
    """Thư mục log do launcher đặt qua `HOLOCRON_LOG_DIR`; không chạy qua launcher thì `None`.

    Biến này do launcher quản lý (đặt khi mở được log, xoá khi không); đặt tay khi chạy uvicorn trực tiếp không được đảm bảo.
    """
    # Bỏ ký tự điều khiển/định dạng như `clean_field` nhưng không cắt độ dài: đường dẫn log có thể dài hơn 64 ký tự.
    value = "".join(
        ch for ch in os.environ.get(LOG_DIR_ENV, "") if not unicodedata.category(ch).startswith("C")
    ).strip()
    return value or None


@cache
def _settings_file(data_dir: Path) -> AiSettingsFile:
    """Một file cài đặt AI (một khoá) cho mỗi thư mục dữ liệu, dùng chung cho công tắc và nhà cung cấp người dùng."""
    return AiSettingsFile(data_dir)


@cache
def _file_consent_store(data_dir: Path) -> FileAiConsentStore:
    """Một kho cho mỗi thư mục dữ liệu, để khoá ghi của file cài đặt dùng chung cho mọi request."""
    return FileAiConsentStore(data_dir, _settings_file(data_dir))


def get_ai_consent_store() -> AiConsentStore:
    """Kho công tắc đồng ý: `HOLOCRON_DATA_DIR/ai_settings.json` khi đặt thư mục dữ liệu (đọc env ở mỗi request), không
    thì kho không lưu được (`PUT /v1/settings/ai-consent` ⇒ 409 `settings_not_persistent`)."""
    data_dir = data_dir_from_env()
    return UnpersistedAiConsentStore() if data_dir is None else _file_consent_store(data_dir)


AiConsentStoreDep = Annotated[AiConsentStore, Depends(get_ai_consent_store)]


def get_ai_consent(store: AiConsentStoreDep) -> AiConsent:
    """`AiConsent` đọc `HOLOCRON_AI_CONSENT`, kho cài đặt và `HOLOCRON_VLM_MODE` ở MỖI lời gọi (env thắng file)."""
    return EnvAiConsent(store=store)


@cache
def _file_provider_store(data_dir: Path) -> FileProviderStore:
    return FileProviderStore(_settings_file(data_dir))


def get_provider_store() -> ProviderStore:
    """Kho nhà cung cấp người dùng (spec §5.16): file cài đặt khi đặt `HOLOCRON_DATA_DIR`, không thì chỉ nhà cung cấp
    env và mọi endpoint ghi trả 409 `settings_not_persistent`. Test thay bằng `dependency_overrides`."""
    data_dir = data_dir_from_env()
    return UnpersistedProviderStore() if data_dir is None else _file_provider_store(data_dir)


@cache
def _provider_selection_for(data_dir: Path | None) -> ProviderSelection:
    """Một bộ chọn cho mỗi thư mục dữ liệu: nguồn env và kho người dùng đọc lại mỗi lần gọi; sổ chi tiêu lưu bền ở
    `ai_usage.json` khi có thư mục dữ liệu, không thì trong RAM (reset khi khởi động; spec §5.16 "Lưu trữ")."""
    ledger = InMemorySpendLedger() if data_dir is None else FileSpendLedger(data_dir)
    return ProviderSelection(CombinedProviderSource(EnvProviderSource(), get_provider_store), ledger)


def get_provider_selection() -> ProviderSelection:
    """Bộ chọn nhà cung cấp (cài DUY NHẤT ở `services/provider_selection.py`); test thay bằng `dependency_overrides`."""
    return _provider_selection_for(data_dir_from_env())


def get_provider_probe() -> ProviderProbe:
    """Kiểm thử nhà cung cấp qua mạng có chặn địa chỉ (ADR-009 mục 12.8); test thay bằng transport giả."""
    return ProviderProbe()


def get_template_author() -> TemplateAuthor:
    return UnconfiguredTemplateAuthor()


def get_citation_provider() -> CitationProvider:
    return NoCitationProvider()


def get_clock() -> Callable[[], datetime]:
    return lambda: datetime.now(UTC)


StoreDep = Annotated[AppStore, Depends(get_store)]


def get_vault(store: StoreDep) -> VaultRepository:
    """Knowledge Vault nằm trong cùng kho với item và blueprint; test thay `get_store` là thay luôn Vault."""
    return store


VaultDep = Annotated[VaultRepository, Depends(get_vault)]
ExtractorDep = Annotated[DocumentExtractor, Depends(get_extractor)]
RuntimeConfigDep = Annotated[RuntimeConfig, Depends(get_runtime_config)]
BuildInfoDep = Annotated[BuildInfo, Depends(get_build_info)]
LogDirDep = Annotated[str | None, Depends(get_log_dir)]
AuthorDep = Annotated[TemplateAuthor, Depends(get_template_author)]
CitationDep = Annotated[CitationProvider, Depends(get_citation_provider)]
ClockDep = Annotated[Callable[[], datetime], Depends(get_clock)]
AiConsentDep = Annotated[AiConsent, Depends(get_ai_consent)]
ProviderSelectionDep = Annotated[ProviderSelection, Depends(get_provider_selection)]
ProviderStoreDep = Annotated[ProviderStore, Depends(get_provider_store)]
ProviderProbeDep = Annotated[ProviderProbe, Depends(get_provider_probe)]
ProposalJobsDep = Annotated[ProposalJobRuntime, Depends(get_proposal_jobs)]
OptionalProposalJobsDep = Annotated[ProposalJobRuntime | None, Depends(get_optional_proposal_jobs)]
