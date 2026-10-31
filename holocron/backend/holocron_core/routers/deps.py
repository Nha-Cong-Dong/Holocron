"""Dependency injection cho các router: kho dữ liệu, Vault, extractor, cấu hình chạy, author, nguồn trích dẫn, đồng hồ.

Test thay phụ thuộc bằng `app.dependency_overrides[get_xxx]`; mọi router dùng đúng các hàm ở module này.
"""

from __future__ import annotations

import os
import unicodedata
from collections.abc import Callable
from datetime import UTC, datetime
from functools import cache
from typing import Annotated

from fastapi import Depends, HTTPException, Request

from ..domain.repositories import AppStore, VaultRepository
from ..infrastructure.llm_config import RuntimeConfig, load_runtime_config_lenient
from ..infrastructure.storage import open_store_from_env
from ..infrastructure.template_library import InMemoryTemplateRegistry
from ..launcher_logging import LOG_DIR_ENV, BuildInfo, read_build_info
from ..services.concept_proposal_job import ProposalJobRuntime
from ..services.extraction import DocumentExtractor, MockDualPassExtractor
from ..services.pipeline import TemplateAuthor
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
ProposalJobsDep = Annotated[ProposalJobRuntime, Depends(get_proposal_jobs)]
