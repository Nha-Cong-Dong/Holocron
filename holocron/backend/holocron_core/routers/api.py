"""Ứng dụng FastAPI của Holocron: tạo `app` và gắn các router theo đúng thứ tự route.

Route nằm ở `runtime.py`, `ingestion.py`, `blueprints.py`; dependency injection ở `deps.py`. Module này re-export
các tên cũ (`MAX_UPLOAD_BYTES`, `get_*`, `IngestionStore`, `IngestionItem`, `_recompute`…) để mã và test đang import
từ `holocron_core.routers.api` không phải đổi; hàm DI re-export là đúng đối tượng các route dùng.
"""

from __future__ import annotations

from fastapi import FastAPI

from ..domain.ingestion import IngestionItem, IngestionItemCreate
from ..domain.twins import IdempotencyRecord
from ..infrastructure.memory_store import IDEMPOTENCY_TTL, IngestionStore
from . import blueprints, ingestion, runtime
from .blueprints import create_twins
from .deps import (
    REGISTRY,
    AuthorDep,
    CitationDep,
    ClockDep,
    ExtractorDep,
    RuntimeConfigDep,
    StoreDep,
    get_citation_provider,
    get_clock,
    get_extractor,
    get_runtime_config,
    get_store,
    get_template_author,
)
from .ingestion import (
    MAX_FILENAME_LENGTH,
    MAX_UPLOAD_BYTES,
    NonCloneableRequest,
    StemPatchRequest,
    TablePatchRequest,
    UploadResponse,
    _recompute,
    approve,
    create_item,
    flag_non_cloneable,
    get_item,
    patch_stem,
    patch_table,
    upload_document,
)
from .runtime import RuntimeInfo, get_runtime

__all__ = [
    "IDEMPOTENCY_TTL",
    "MAX_FILENAME_LENGTH",
    "MAX_UPLOAD_BYTES",
    "REGISTRY",
    "AuthorDep",
    "CitationDep",
    "ClockDep",
    "ExtractorDep",
    "IdempotencyRecord",
    "IngestionItem",
    "IngestionItemCreate",
    "IngestionStore",
    "NonCloneableRequest",
    "RuntimeConfigDep",
    "RuntimeInfo",
    "StemPatchRequest",
    "StoreDep",
    "TablePatchRequest",
    "UploadResponse",
    "_recompute",
    "app",
    "approve",
    "create_item",
    "create_twins",
    "flag_non_cloneable",
    "get_citation_provider",
    "get_clock",
    "get_extractor",
    "get_item",
    "get_runtime",
    "get_runtime_config",
    "get_store",
    "get_template_author",
    "patch_stem",
    "patch_table",
    "upload_document",
]

app = FastAPI(title="Holocron Ingestion Gate", version="0.1.0a0")
app.include_router(runtime.router)
app.include_router(ingestion.router)
app.include_router(blueprints.router)
