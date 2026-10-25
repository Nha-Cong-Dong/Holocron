"""Bộ nhập tài liệu PDF, phần 1 (SPECIFICATION §6.5): nhận file, hỏi trạng thái, xem trang, xoá.

Chưa có ở đây (chờ quyết định): sửa dòng (`PATCH …/lines`), xác nhận trang/tài liệu, tạo item từ trang.

Phụ thuộc cấp qua `Depends`, test thay bằng `app.dependency_overrides`:
- `get_import_runtime`: kho file gốc, hàng đợi đọc nền, cấu hình bộ đọc (`PdfReaderConfig`, nguồn duy nhất của giới
  hạn dung lượng), khoá theo tài liệu và cổng nhận việc; do `lifespan` của app tạo (`create_import_runtime`) và gắn
  vào `app.state`, không có biến toàn cục nào giữ chúng.
- `get_pdf_reader`: `WorkerPdfReader` với cấu hình bộ đọc của runtime và cấu hình OCR đọc từ biến môi trường (spec
  §11.2). Cấu hình OCR sai không làm request lỗi: bộ đọc chạy không có OCR (trang cần OCR thành `failed`,
  `OCR_ENGINE_MISSING`, ADR-008 mục 8), kèm cảnh báo chỉ nêu tên biến.

Mọi handler là `def` (FastAPI chạy trong threadpool): nhận file chặn tới vài chục giây (worker mở thử, ghi file, giao
dịch của kho), không được chạy trên event loop.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile

from ..domain.documents import DocumentImport, DocumentSummary, OutputLanguage, PageExtraction
from ..domain.repositories import DocumentRepository
from ..domain.schemas import SourceKind
from ..infrastructure.document_readers.config import PdfReaderConfig, load_ocr_settings
from ..infrastructure.document_readers.pdf_reader import PdfReader, WorkerPdfReader
from ..infrastructure.llm_config import ConfigError
from ..infrastructure.storage.original_files import OriginalFileStore
from ..services.document_import import (
    BackgroundImportQueue,
    DocumentBusyError,
    DocumentImportError,
    DocumentNotFoundError,
    ImportErrorCode,
    ImportRequest,
    ImportRuntime,
    ImportUnavailableError,
    accept_document,
    delete_imported_document,
    get_document_view,
    get_page_extraction,
    list_document_summaries,
    remove_orphan_files,
    requeue_pending,
)
from .deps import ClockDep, StoreDep

SUBJECT_CODE_PATTERN = r"^[A-Z]{2,6}[0-9]{2,4}$"
RUNTIME_STATE_ATTR = "document_import"
NOT_FOUND_DETAIL = "không tìm thấy tài liệu"
PAGE_NOT_FOUND_DETAIL = "không tìm thấy tài liệu hoặc trang"

ERROR_STATUS: dict[ImportErrorCode, int] = {
    "TOO_LARGE": 413,
    "EMPTY": 400,
    "FILENAME_TOO_LONG": 422,
    "NOT_PDF": 415,
    "CONTENT_TYPE_MISMATCH": 415,
    "SUBJECT_CONFLICT": 409,
    "PASSWORD_REQUIRED": 422,
    "OPEN_FAILED": 422,
    "TOO_MANY_PAGES": 422,
}

router = APIRouter()
_LOGGER = logging.getLogger(__name__)


def load_pdf_reader(config: PdfReaderConfig) -> PdfReader:
    """Bộ đọc tầng 1–2 với cấu hình OCR từ biến môi trường; cấu hình OCR sai ⇒ `ConfigError` (chỉ nêu tên biến)."""
    return WorkerPdfReader(config, load_ocr_settings())


def create_import_runtime(data_dir: Path | None, config: PdfReaderConfig | None = None) -> ImportRuntime:
    """Kho file gốc (`HOLOCRON_DATA_DIR/files` hoặc thư mục tạm) và hàng đợi đọc nền; gọi trong `lifespan`.

    Kho file gốc nhận đúng `max_bytes` của cấu hình bộ đọc mà runtime giữ (một đối tượng cấu hình cho cả app).
    """
    reader_config = PdfReaderConfig() if config is None else config
    files = OriginalFileStore.open(data_dir, max_bytes=reader_config.max_bytes)
    files.clean_staging()
    return ImportRuntime(files=files, queue=BackgroundImportQueue(), reader_config=reader_config)


def requeue_on_startup(store: DocumentRepository, runtime: ImportRuntime) -> list[str]:
    """Khi khởi động: dọn file gốc mồ côi, rồi xếp lại tài liệu đang đọc dở từ file gốc đã lưu (§6.5; chỉ có ý nghĩa
    với kho SQLite).

    Cấu hình OCR sai thì không xếp lại (khác với `POST`, nơi bộ đọc chạy không có OCR): đọc lại khi cấu hình đang sai
    sẽ làm trang cần OCR thành `failed` vĩnh viễn. Ghi cảnh báo (chỉ nêu tên biến, không nêu giá trị) và để app vẫn
    khởi động; tài liệu giữ `processing` tới lần khởi động sau với cấu hình đúng.
    """
    remove_orphan_files(store, runtime.files)
    try:
        reader = load_pdf_reader(runtime.reader_config)
    except ConfigError as exc:
        _LOGGER.warning("không xếp lại tài liệu đang đọc dở: %s", exc)
        return []
    return requeue_pending(store, runtime.files, reader, runtime.queue)


def get_import_runtime(request: Request) -> ImportRuntime:
    runtime = getattr(request.app.state, RUNTIME_STATE_ATTR, None)
    if isinstance(runtime, ImportRuntime):
        return runtime
    raise RuntimeError("bộ nhập tài liệu chưa được khởi tạo (lifespan của app chưa chạy)")


RuntimeDep = Annotated[ImportRuntime, Depends(get_import_runtime)]


def get_pdf_reader(runtime: RuntimeDep) -> PdfReader:
    """Bộ đọc của request; cấu hình OCR sai ⇒ bộ đọc không có OCR và một cảnh báo (thông điệp chỉ nêu tên biến)."""
    try:
        return load_pdf_reader(runtime.reader_config)
    except ConfigError as exc:
        _LOGGER.warning("cấu hình OCR không hợp lệ, đọc PDF không có OCR: %s", exc)
        return WorkerPdfReader(runtime.reader_config, ocr=None)


ReaderDep = Annotated[PdfReader, Depends(get_pdf_reader)]


@router.post(
    "/v1/documents",
    response_model=DocumentImport,
    status_code=202,
    responses={200: {"model": DocumentImport, "description": "Đã nhập cùng nội dung cho cùng môn"}},
)
def import_document(
    response: Response,
    store: StoreDep,
    runtime: RuntimeDep,
    reader: ReaderDep,
    clock: ClockDep,
    file: Annotated[UploadFile, File(description="PDF tài liệu môn học (≤ 50 MiB, ≤ 300 trang)")],
    subject_code: Annotated[str, Form(pattern=SUBJECT_CODE_PATTERN)],
    source_kind: Annotated[SourceKind, Form()],
    output_language: Annotated[OutputLanguage, Form()] = "vi",
    title: Annotated[str | None, Form(min_length=1, max_length=300)] = None,
) -> DocumentImport:
    if title is not None and not title.strip():
        raise HTTPException(status_code=422, detail="title không được chỉ gồm khoảng trắng")
    content = file.file.read(runtime.reader_config.max_bytes + 1)
    request = ImportRequest(
        content=content,
        filename=file.filename,
        declared_type=file.content_type,
        subject_code=subject_code,
        source_kind=source_kind,
        output_language=output_language,
        title=title,
    )
    try:
        outcome = accept_document(request, store=store, runtime=runtime, reader=reader, now=clock())
    except DocumentImportError as exc:
        raise HTTPException(status_code=ERROR_STATUS[exc.code], detail=str(exc)) from exc
    except ImportUnavailableError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if outcome.created:
        response.headers["Location"] = f"/v1/documents/{outcome.document.meta.doc_id}"
    else:
        response.status_code = 200
    return outcome.document


@router.get("/v1/documents", response_model=list[DocumentSummary])
def list_documents(
    store: StoreDep, subject_code: Annotated[str | None, Query(pattern=SUBJECT_CODE_PATTERN)] = None
) -> list[DocumentSummary]:
    return list_document_summaries(store, subject_code)


@router.get("/v1/documents/{document_id}", response_model=DocumentImport)
def get_document(store: StoreDep, document_id: str) -> DocumentImport:
    try:
        return get_document_view(store, document_id)
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=NOT_FOUND_DETAIL) from exc


@router.get("/v1/documents/{document_id}/pages/{page_number}", response_model=PageExtraction)
def get_document_page(store: StoreDep, document_id: str, page_number: int) -> PageExtraction:
    try:
        return get_page_extraction(store, document_id, page_number)
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=PAGE_NOT_FOUND_DETAIL) from exc


@router.delete("/v1/documents/{document_id}", status_code=204, response_class=Response)
def delete_document(store: StoreDep, runtime: RuntimeDep, document_id: str) -> Response:
    try:
        delete_imported_document(store, runtime, document_id)
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=NOT_FOUND_DETAIL) from exc
    except DocumentBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return Response(status_code=204)
