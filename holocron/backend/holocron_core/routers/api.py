"""Ứng dụng FastAPI của Holocron: tạo `app` và gắn các router theo đúng thứ tự route.

Route nằm ở `runtime.py`, `ingestion.py`, `blueprints.py`, `vault.py`; dependency injection ở `deps.py`. Module này
re-export các tên cũ (`MAX_UPLOAD_BYTES`, `get_*`, `IngestionStore`, `IngestionItem`, `_recompute`…) để mã và test
đang import từ `holocron_core.routers.api` không phải đổi; hàm DI re-export là đúng đối tượng các route dùng.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ..domain.ingestion import IngestionItem, IngestionItemCreate
from ..domain.repositories import StorageBusyError
from ..domain.twins import IdempotencyRecord
from ..infrastructure.memory_store import IDEMPOTENCY_TTL, IngestionStore
from ..infrastructure.storage import SQLiteStore
from . import blueprints, ingestion, runtime, vault
from .blueprints import create_twins
from .deps import (
    REGISTRY,
    AuthorDep,
    CitationDep,
    ClockDep,
    ExtractorDep,
    RuntimeConfigDep,
    StoreDep,
    _default_store,
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
    "STORE_BUSY_DETAIL",
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


STORE_BUSY_DETAIL = "Kho dữ liệu đang bận, thử lại"


@asynccontextmanager
async def _lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Mở kho ngay khi khởi động: DB SQLite mới hơn code thì app từ chối khởi động (SPECIFICATION §5.13).

    Khi tắt app, đóng kết nối SQLite của kho mặc định và bỏ nó khỏi cache, để lần khởi động sau trong cùng tiến trình
    (test, `--reload`) mở lại kết nối mới thay vì dùng kết nối đã đóng. Kho in-memory không có gì để đóng.
    """
    store = get_store()
    try:
        yield
    finally:
        if isinstance(store, SQLiteStore):
            store.close()
            _default_store.cache_clear()


async def _store_busy(_: Request, exc: Exception) -> JSONResponse:
    """Kho bị tiến trình khác giữ khoá ghi quá `busy_timeout`: 503 để client thử lại, không thành 500 trần."""
    return JSONResponse(status_code=503, content={"detail": STORE_BUSY_DETAIL})


app = FastAPI(title="Holocron Ingestion Gate", version="0.1.0a0", lifespan=_lifespan)
app.add_exception_handler(StorageBusyError, _store_busy)
app.include_router(runtime.router)
app.include_router(ingestion.router)
app.include_router(blueprints.router)
app.include_router(vault.router)
