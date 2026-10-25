"""Dependency injection cho các router: kho dữ liệu, extractor, cấu hình chạy, author, nguồn trích dẫn, đồng hồ.

Test thay phụ thuộc bằng `app.dependency_overrides[get_xxx]`; mọi router dùng đúng các hàm ở module này.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from functools import cache
from typing import Annotated

from fastapi import Depends

from ..domain.repositories import AppStore
from ..infrastructure.llm_config import RuntimeConfig, load_runtime_config
from ..infrastructure.storage import open_store_from_env
from ..infrastructure.template_library import InMemoryTemplateRegistry
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


def get_extractor() -> DocumentExtractor:
    # Adapter VLM thật thuộc T2-N5. Cho tới lúc đó mọi cấu hình đều dùng Mock; `RuntimeInfo.demo_mode` suy ra từ
    # extractor đang chạy nên vẫn đúng khi đã đặt biến VLM mà chưa có adapter.
    return _EXTRACTOR


def get_runtime_config() -> RuntimeConfig:
    return load_runtime_config()


def get_template_author() -> TemplateAuthor:
    return UnconfiguredTemplateAuthor()


def get_citation_provider() -> CitationProvider:
    return NoCitationProvider()


def get_clock() -> Callable[[], datetime]:
    return lambda: datetime.now(UTC)


StoreDep = Annotated[AppStore, Depends(get_store)]
ExtractorDep = Annotated[DocumentExtractor, Depends(get_extractor)]
RuntimeConfigDep = Annotated[RuntimeConfig, Depends(get_runtime_config)]
AuthorDep = Annotated[TemplateAuthor, Depends(get_template_author)]
CitationDep = Annotated[CitationProvider, Depends(get_citation_provider)]
ClockDep = Annotated[Callable[[], datetime], Depends(get_clock)]
