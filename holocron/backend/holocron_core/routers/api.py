"""Ứng dụng FastAPI của Holocron: tạo `app` và gắn các router theo đúng thứ tự route.

Route nằm ở `runtime.py`, `ingestion.py`, `blueprints.py`, `vault.py`, `documents.py`; dependency injection ở `deps.py`. Module này
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
from ..infrastructure.storage import SQLiteStore, data_dir_from_env
from ..services.document_import import ImportRuntime
from . import blueprints, documents, ingestion, runtime, static_site, subjects, vault
from .access import LocalAccessMiddleware, get_allowed_hosts, get_required_token
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
    "LocalAccessMiddleware",
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
    "get_allowed_hosts",
    "get_citation_provider",
    "get_clock",
    "get_extractor",
    "get_item",
    "get_required_token",
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
async def _lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Mở kho ngay khi khởi động: DB SQLite mới hơn code thì app từ chối khởi động (SPECIFICATION §5.13).

    Sau khi mở kho, tạo bộ nhập tài liệu (kho file gốc, hàng đợi đọc nền, §6.5), gắn vào `app.state`, dọn file gốc
    mồ côi và xếp lại các tài liệu còn đang đọc dở. Khi tắt app (kể cả khi chính bước tạo bộ nhập lỗi): dừng bộ nhập
    (chờ có hạn; luồng đọc nền chưa dừng kịp thì thư mục file tạm được giữ lại cho lần khởi động sau, luồng đó tự
    thoát êm khi thấy kho đã đóng), rồi đóng kết nối SQLite của kho mặc định và bỏ nó khỏi cache, để lần khởi động
    sau trong cùng tiến trình (test, `--reload`) mở lại kết nối mới thay vì dùng kết nối đã đóng. Kho in-memory
    không có gì để đóng.

    Kiểm cục bộ của chế độ khả năng (Tesseract, gói `vie`, lượt đọc thứ hai; §5.12) chạy một lần ở đây, kết quả gắn
    vào `app.state` cho `GET /v1/runtime` và các route cần khả năng; bỏ khi tắt app để lần khởi động sau kiểm lại.
    """
    store = get_store()
    import_runtime: ImportRuntime | None = None
    try:
        runtime.run_local_capability_check(application)
        import_runtime = documents.create_import_runtime(data_dir_from_env())
        setattr(application.state, documents.RUNTIME_STATE_ATTR, import_runtime)
        documents.requeue_on_startup(store, import_runtime)
        yield
    finally:
        runtime.clear_local_capability_check(application)
        if import_runtime is not None:
            import_runtime.close()
            delattr(application.state, documents.RUNTIME_STATE_ATTR)
        if isinstance(store, SQLiteStore):
            store.close()
            _default_store.cache_clear()


async def _store_busy(_: Request, exc: Exception) -> JSONResponse:
    """Kho bị tiến trình khác giữ khoá ghi quá `busy_timeout`: 503 để client thử lại, không thành 500 trần."""
    return JSONResponse(status_code=503, content={"detail": STORE_BUSY_DETAIL})


app = FastAPI(title="Holocron Ingestion Gate", version="0.2.0a0", lifespan=_lifespan)
# Kiểm `Host` và token (nếu bật) cho MỌI request, trước routing và trước khi đọc body (SPECIFICATION §6.0, §13.1):
# gồm cả `/openapi.json`, `/docs`, `/redoc`, 404 và 405. Provider tra qua `app.dependency_overrides` để test override.
app.add_middleware(LocalAccessMiddleware, overrides=lambda: app.dependency_overrides)
app.add_exception_handler(StorageBusyError, _store_busy)
app.include_router(runtime.router)
app.include_router(ingestion.router)
app.include_router(blueprints.router)
app.include_router(vault.router)
app.include_router(documents.router)
app.include_router(subjects.router)

# Bản export tĩnh của frontend (chế độ portable): chỉ gắn khi đặt `HOLOCRON_WEB_DIR`, sau mọi `include_router`.
_web_dir = static_site.web_dir_from_env()
if _web_dir is not None:
    static_site.attach_static_site(app, _web_dir)
