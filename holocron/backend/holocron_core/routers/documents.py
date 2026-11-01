"""Bộ nhập tài liệu PDF (SPECIFICATION §6.5): nhận file, hỏi trạng thái, xem trang, sửa dòng, xác nhận, hoàn tác bỏ
mềm (GĐ0 0D), hoàn tác tự xác nhận (GĐ1 1B), đổi chương, xoá.

Chưa có ở đây: tạo item từ trang (`POST …/pages/{n}/items`, cần đổi `IngestionItem`).

Phụ thuộc cấp qua `Depends`, test thay bằng `app.dependency_overrides`:
- `get_import_runtime`: kho file gốc, hàng đợi đọc nền, cấu hình bộ đọc (`PdfReaderConfig`, nguồn duy nhất của giới
  hạn dung lượng), khoá theo tài liệu và cổng nhận việc; do `lifespan` của app tạo (`create_import_runtime`) và gắn
  vào `app.state`, không có biến toàn cục nào giữ chúng.
- `get_pdf_reader`: `WorkerPdfReader` với cấu hình bộ đọc của runtime và cấu hình OCR đọc từ biến môi trường (spec
  §11.2). Cấu hình OCR sai không làm request lỗi: bộ đọc chạy không có OCR (trang cần OCR thành `failed`,
  `OCR_ENGINE_MISSING`, ADR-008 mục 8), kèm cảnh báo chỉ nêu tên biến.
- `get_document_proposals` (T1b, T1e; spec §5.7, §5.8 "Khi nào chạy", §6.5): runtime job đề xuất cặp của app
  (`get_optional_proposal_jobs`, `None` khi không qua `lifespan`), khả năng `concept_pair_llm_proposal` tính bằng CÙNG
  hàm của `GET /v1/runtime` (`compute_capabilities` với `RuntimeConfig` và kết quả kiểm cục bộ `get_local_failures`), và
  hàm dựng client của `POST /v1/vault/concept-pairs/jobs` (`get_client_factory_builder` →
  `vault.get_chat_client_factory`). Mọi route trả `DocumentImport` dùng nó để điền `proposal_job` hoặc
  `proposal_job_absent_reason`; xác nhận vừa đóng tài liệu thì tự tạo job. Đường chốt chạy nền (cuối lần đọc, đường
  `failed`) dùng hook của `ImportRuntime` do `lifespan` cấp (`background_proposal_hook`), cùng các hàm trên.

`routers/runtime.py` và `routers/vault.py` import module này (`RUNTIME_STATE_ATTR`), nên hai hàm cấp phụ thuộc lấy từ
chúng (`get_capability_local_failures`, `get_client_factory_builder`) import trễ trong thân hàm để không tạo vòng import.

Mọi handler là `def` (FastAPI chạy trong threadpool): nhận file chặn tới vài chục giây (worker mở thử, ghi file, giao
dịch của kho), không được chạy trên event loop.
"""

from __future__ import annotations

import dataclasses
import functools
import logging
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile

from ..domain.capabilities import CapabilityName, CapabilityReason
from ..domain.documents import (
    CHAPTER_CODE_PATTERN,
    DocumentChapterPatch,
    DocumentConfirmRequest,
    DocumentImport,
    DocumentSummary,
    OutputLanguage,
    PageConfirmRequest,
    PageExtraction,
    PageLinesPatch,
    PageRestoreRequest,
    PageUnconfirmRequest,
)
from ..domain.repositories import AppStore, DocumentExistsError, VersionConflictError
from ..domain.schemas import SourceKind
from ..domain.vault import ProposalJob
from ..infrastructure.document_readers.config import MIB, PdfReaderConfig, load_ocr_settings
from ..infrastructure.document_readers.pdf_reader import PdfReader, PdfWorkerError, WorkerPdfReader
from ..infrastructure.llm_config import ConfigError, RuntimeConfig
from ..infrastructure.storage.original_files import OriginalFileStore
from ..services.capabilities import compute_capabilities, unavailable_error
from ..services.concept_proposal_job import ClientFactory, DocumentProposals, ProposalJobRuntime
from ..services.document_chapter import ChapterCodeLockedError, update_document_chapter
from ..services.document_import import (
    IMPORT_UNAVAILABLE_DETAIL,
    MAX_TITLE_LENGTH,
    BackgroundImportQueue,
    DocumentBusyError,
    DocumentChapterMissingError,
    DocumentConfirmedHook,
    DocumentImportError,
    DocumentNotFoundError,
    ImportErrorCode,
    ImportRequest,
    ImportRuntime,
    ImportUnavailableError,
    InvalidPageEditError,
    PageStateError,
    PageTooLargeError,
    accept_document,
    confirm_document,
    confirm_page,
    delete_imported_document,
    edit_page_lines,
    get_document_view,
    get_page_extraction,
    list_document_summaries,
    remove_orphan_files,
    requeue_pending,
    restore_page,
    unconfirm_page,
)
from .deps import (
    ClockDep,
    OptionalProposalJobsDep,
    RuntimeConfigDep,
    StoreDep,
    VaultDep,
    find_proposal_jobs,
    get_clock,
    get_runtime_config,
)

SUBJECT_CODE_PATTERN = r"^[A-Z]{2,6}[0-9]{2,4}$"
RUNTIME_STATE_ATTR = "document_import"
NOT_FOUND_DETAIL = "không tìm thấy tài liệu"
PAGE_NOT_FOUND_DETAIL = "không tìm thấy tài liệu hoặc trang"
WORKER_UNAVAILABLE_DETAIL = "Không khởi động được bộ đọc PDF (lỗi môi trường của máy, không phải lỗi file); hãy thử lại"
IMPORT_RACE_DETAIL = "Tài liệu cùng nội dung vừa bị thay đổi bởi một yêu cầu khác; hãy gửi lại"
# Mô tả giới hạn của field `file` lấy từ cấu hình mặc định của bộ đọc (ADR-008 mục 4), không ghi cứng con số.
_DEFAULT_LIMITS = PdfReaderConfig()
FILE_FIELD_DESCRIPTION = (
    f"PDF tài liệu môn học (≤ {_DEFAULT_LIMITS.max_bytes // MIB} MiB, ≤ {_DEFAULT_LIMITS.max_pages} trang)"
)

ERROR_STATUS: dict[ImportErrorCode, int] = {
    "TOO_LARGE": 413,
    "EMPTY": 400,
    "FILENAME_TOO_LONG": 422,
    "NOT_PDF": 415,
    "CONTENT_TYPE_MISMATCH": 415,
    "SUBJECT_CONFLICT": 409,
    "CHAPTER_CONFLICT": 409,
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
    Hook "đề xuất sau khi đóng" (T1b) do `lifespan` gắn sau (`with_proposal_hook`).
    """
    reader_config = PdfReaderConfig() if config is None else config
    files = OriginalFileStore.open(data_dir, max_bytes=reader_config.max_bytes)
    files.clean_staging()
    return ImportRuntime(files=files, queue=BackgroundImportQueue(), reader_config=reader_config)


def with_proposal_hook(runtime: ImportRuntime, hook: DocumentConfirmedHook) -> ImportRuntime:
    """Cùng bộ nhập (cùng kho file, hàng đợi, khoá) kèm hook "đề xuất sau khi đóng" cho các đường chốt chạy nền (T1b,
    `background_proposal_hook`); `lifespan` gọi trước khi gắn bộ nhập vào `app.state`."""
    return dataclasses.replace(runtime, on_document_confirmed=hook)


def requeue_on_startup(store: AppStore, runtime: ImportRuntime) -> list[str]:
    """Khi khởi động: dọn file gốc mồ côi, rồi xếp lại tài liệu đang đọc dở từ file gốc đã lưu (§6.5; chỉ có ý nghĩa
    với kho SQLite). Việc đọc mang theo Vault (cùng kho) và khoá theo tài liệu của runtime (`ReadJob`).

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
    return requeue_pending(
        store,
        runtime.files,
        reader,
        runtime.queue,
        runtime.retry,
        vault=store,
        locks=runtime.locks,
        config=runtime.reader_config,
        on_document_confirmed=runtime.on_document_confirmed,
    )


def get_import_runtime(request: Request) -> ImportRuntime:
    """Bộ nhập của app. Không có (`lifespan` chưa chạy, hoặc đã gỡ khi tắt app) ⇒ 500 kèm `detail` như các đường
    app-tắt khác (`ImportUnavailableError`), không 500 trần; mọi route dùng bộ nhập đi qua đây."""
    runtime = getattr(request.app.state, RUNTIME_STATE_ATTR, None)
    if isinstance(runtime, ImportRuntime):
        return runtime
    _LOGGER.warning("request tới bộ nhập tài liệu khi bộ nhập chưa khởi tạo hoặc đã dừng (lifespan)")
    raise HTTPException(status_code=500, detail=IMPORT_UNAVAILABLE_DETAIL)


RuntimeDep = Annotated[ImportRuntime, Depends(get_import_runtime)]


def get_pdf_reader(runtime: RuntimeDep) -> PdfReader:
    """Bộ đọc của request; cấu hình OCR sai ⇒ bộ đọc không có OCR và một cảnh báo (thông điệp chỉ nêu tên biến)."""
    try:
        return load_pdf_reader(runtime.reader_config)
    except ConfigError as exc:
        _LOGGER.warning("cấu hình OCR không hợp lệ, đọc PDF không có OCR: %s", exc)
        return WorkerPdfReader(runtime.reader_config, ocr=None)


ReaderDep = Annotated[PdfReader, Depends(get_pdf_reader)]

# --------------------------------------------------------------------------- #
# Job đề xuất cặp của tài liệu (T1b, T1e)
# --------------------------------------------------------------------------- #

LocalFailures = Mapping[CapabilityName, CapabilityReason]
ClientFactoryBuilder = Callable[[RuntimeConfig, LocalFailures], ClientFactory]
"""Dựng hàm tạo client LLM từ cấu hình và kết quả kiểm cục bộ; ném lỗi khi khả năng không khả dụng hoặc cấu hình LLM
không dựng được client (`vault.get_chat_client_factory` ném `HTTPException` 409)."""


def get_capability_local_failures(request: Request) -> LocalFailures:
    """Kết quả kiểm cục bộ của app, đúng hàm `get_local_failures` của `GET /v1/runtime` (bộ kiểm theo override của app,
    như `runtime.start_local_capability_check`). Import trễ: `routers/runtime.py` import module này."""
    from . import runtime

    provider: Callable[[], runtime.LocalCapabilityCheck] = request.app.dependency_overrides.get(
        runtime.get_local_capability_check, runtime.get_local_capability_check
    )
    return runtime.get_local_failures(request, provider())


def get_client_factory_builder() -> ClientFactoryBuilder:
    """Hàm dựng client LLM của job đề xuất cặp: CÙNG hàm của `POST /v1/vault/concept-pairs/jobs`
    (`vault.get_chat_client_factory`, kiểm khả năng và cấu hình, không gọi mạng). Import trễ: `routers/vault.py` import
    `routers/runtime.py`, module đó import module này. Test thay bằng `app.dependency_overrides`."""
    from .vault import get_chat_client_factory

    return get_chat_client_factory


def document_proposals(
    store: AppStore,
    config: RuntimeConfig,
    local_failures: LocalFailures,
    clock: Callable[[], datetime],
    jobs: ProposalJobRuntime | None,
    build_client_factory: ClientFactoryBuilder,
) -> DocumentProposals:
    """`DocumentProposals` của một request hoặc một lần hook nền chạy. Khả năng LLM lấy từ CÙNG hàm của
    `GET /v1/runtime` (`compute_capabilities` + `unavailable_error`), không có nguồn thứ hai; client chỉ dựng khi thật
    sự tạo job."""
    error = unavailable_error(compute_capabilities(config, local_failures=local_failures), "concept_pair_llm_proposal")
    return DocumentProposals(
        store=store,
        jobs=jobs,
        llm_available=error is None,
        make_client_factory=functools.partial(build_client_factory, config, local_failures),
        limits=config.llm.task_limits,
        settings=config.llm.proposal,
        clock=clock,
    )


def get_document_proposals(
    store: StoreDep,
    config: RuntimeConfigDep,
    clock: ClockDep,
    jobs: OptionalProposalJobsDep,
    local_failures: Annotated[LocalFailures, Depends(get_capability_local_failures)],
    build_client_factory: Annotated[ClientFactoryBuilder, Depends(get_client_factory_builder)],
) -> DocumentProposals:
    return document_proposals(store, config, local_failures, clock, jobs, build_client_factory)


DocumentProposalsDep = Annotated[DocumentProposals, Depends(get_document_proposals)]


def _app_local_failures(application: FastAPI) -> LocalFailures:
    """Kết quả kiểm cục bộ đã lưu của app (luồng nền không có request); chưa có ⇒ đang kiểm, như `GET /v1/runtime`."""
    from . import runtime

    stored: LocalFailures | None = getattr(application.state, runtime.LOCAL_FAILURES_STATE_ATTR, None)
    return runtime.PENDING_LOCAL_FAILURES if stored is None else stored


def background_proposal_hook(application: FastAPI, store: AppStore) -> DocumentConfirmedHook:
    """Hook "đề xuất sau khi đóng" cho đường chốt chạy nền (cuối lần đọc, đường `failed`), cấp lúc `lifespan` dựng
    `ImportRuntime` (T1b). Mỗi lần chạy đọc lại cấu hình, kết quả kiểm cục bộ và runtime job của app (theo override
    của app như `runtime.start_local_capability_check`), rồi gọi `DocumentProposals.start` như đường request."""

    def start(document_id: str) -> ProposalJob | None:
        overrides = application.dependency_overrides
        config_provider: Callable[[], RuntimeConfig] = overrides.get(get_runtime_config, get_runtime_config)
        clock_provider: Callable[[], Callable[[], datetime]] = overrides.get(get_clock, get_clock)
        builder_provider: Callable[[], ClientFactoryBuilder] = overrides.get(
            get_client_factory_builder, get_client_factory_builder
        )
        proposals = document_proposals(
            store,
            config_provider(),
            _app_local_failures(application),
            clock_provider(),
            find_proposal_jobs(application),
            builder_provider(),
        )
        return proposals.start(document_id)

    return start


@router.post(
    "/v1/documents",
    response_model=DocumentImport,
    status_code=202,
    responses={200: {"model": DocumentImport, "description": "Đã nhập cùng nội dung cho cùng môn và cùng chương"}},
)
def import_document(
    response: Response,
    store: StoreDep,
    vault: VaultDep,
    runtime: RuntimeDep,
    reader: ReaderDep,
    clock: ClockDep,
    proposals: DocumentProposalsDep,
    file: Annotated[UploadFile, File(description=FILE_FIELD_DESCRIPTION)],
    subject_code: Annotated[str, Form(pattern=SUBJECT_CODE_PATTERN)],
    source_kind: Annotated[SourceKind, Form()],
    # Tuỳ chọn từ 2026-10-31 (§6.5, GĐ1 1A): không gửi hoặc chuỗi rỗng ⇒ chương tự động (§5.7)
    chapter_code: Annotated[str | None, Form(pattern=CHAPTER_CODE_PATTERN)] = None,
    output_language: Annotated[OutputLanguage, Form()] = "vi",
    title: Annotated[str | None, Form(min_length=1, max_length=MAX_TITLE_LENGTH)] = None,
    # §6.5 (GĐ1 1B): lưu vào `DocumentImport.auto_confirm`; bật luật tự xác nhận cấp trang `page_clean_v1` (§5.7)
    auto_confirm: Annotated[bool, Form()] = True,
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
        chapter_code=chapter_code,
        output_language=output_language,
        title=title,
        auto_confirm=auto_confirm,
    )
    try:
        outcome = accept_document(
            request, store=store, runtime=runtime, reader=reader, now=clock(), vault=vault, proposals=proposals
        )
    except DocumentImportError as exc:
        raise HTTPException(status_code=ERROR_STATUS[exc.code], detail=str(exc)) from exc
    except ImportUnavailableError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except PdfWorkerError as exc:
        _LOGGER.warning("worker đọc PDF không khởi động được khi nhận tài liệu")
        raise HTTPException(status_code=500, detail=WORKER_UNAVAILABLE_DETAIL) from exc
    except DocumentExistsError as exc:
        raise HTTPException(status_code=500, detail=IMPORT_RACE_DETAIL) from exc
    except DocumentChapterMissingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
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
def get_document(store: StoreDep, proposals: DocumentProposalsDep, document_id: str) -> DocumentImport:
    try:
        return get_document_view(store, document_id, vault=store, proposals=proposals)
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=NOT_FOUND_DETAIL) from exc
    except DocumentChapterMissingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/v1/documents/{document_id}/pages/{page_number}", response_model=PageExtraction)
def get_document_page(store: StoreDep, document_id: str, page_number: int) -> PageExtraction:
    try:
        return get_page_extraction(store, document_id, page_number)
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=PAGE_NOT_FOUND_DETAIL) from exc


def _review_error(exc: Exception) -> HTTPException:
    """Lỗi của sửa dòng và xác nhận (§6.5) thành mã HTTP; `detail` là chuỗi tiếng Việt (§6.0)."""
    if isinstance(exc, DocumentNotFoundError):
        return HTTPException(status_code=404, detail=PAGE_NOT_FOUND_DETAIL)
    if isinstance(exc, InvalidPageEditError | PageTooLargeError):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=409, detail=str(exc))


_REVIEW_ERRORS = (
    DocumentNotFoundError,
    InvalidPageEditError,
    PageTooLargeError,
    PageStateError,
    VersionConflictError,
    DocumentChapterMissingError,
)


@router.patch("/v1/documents/{document_id}/pages/{page_number}/lines", response_model=PageExtraction)
def patch_page_lines(
    store: StoreDep,
    vault: VaultDep,
    runtime: RuntimeDep,
    clock: ClockDep,
    document_id: str,
    page_number: int,
    body: PageLinesPatch,
) -> PageExtraction:
    try:
        return edit_page_lines(store, vault, runtime, document_id, page_number, body, now=clock())
    except _REVIEW_ERRORS as exc:
        raise _review_error(exc) from exc


@router.post("/v1/documents/{document_id}/pages/{page_number}/confirm", response_model=PageExtraction)
def confirm_document_page(
    store: StoreDep,
    vault: VaultDep,
    runtime: RuntimeDep,
    clock: ClockDep,
    proposals: DocumentProposalsDep,
    document_id: str,
    page_number: int,
    body: PageConfirmRequest,
) -> PageExtraction:
    try:
        return confirm_page(
            store, vault, runtime, document_id, page_number, body, now=clock(), on_document_confirmed=proposals.start
        )
    except _REVIEW_ERRORS as exc:
        raise _review_error(exc) from exc


@router.post("/v1/documents/{document_id}/pages/{page_number}/restore", response_model=PageExtraction)
def restore_document_page(
    store: StoreDep,
    vault: VaultDep,
    runtime: RuntimeDep,
    document_id: str,
    page_number: int,
    body: PageRestoreRequest,
) -> PageExtraction:
    """Hoàn tác bỏ mềm một trang (§6.5, GĐ0 0D; tên và đường dẫn là đề xuất suy ra, chờ duyệt)."""
    try:
        return restore_page(store, vault, runtime, document_id, page_number, body)
    except _REVIEW_ERRORS as exc:
        raise _review_error(exc) from exc


@router.post("/v1/documents/{document_id}/pages/{page_number}/unconfirm", response_model=PageExtraction)
def unconfirm_document_page(
    store: StoreDep,
    vault: VaultDep,
    runtime: RuntimeDep,
    document_id: str,
    page_number: int,
    body: PageUnconfirmRequest,
) -> PageExtraction:
    """Hoàn tác tự xác nhận một trang (§6.5, GĐ1 1B; tên và đường dẫn là đề xuất suy ra, chờ duyệt)."""
    try:
        return unconfirm_page(store, vault, runtime, document_id, page_number, body)
    except _REVIEW_ERRORS as exc:
        raise _review_error(exc) from exc


@router.post("/v1/documents/{document_id}/confirm", response_model=DocumentImport)
def confirm_whole_document(
    store: StoreDep,
    vault: VaultDep,
    runtime: RuntimeDep,
    clock: ClockDep,
    proposals: DocumentProposalsDep,
    document_id: str,
    body: DocumentConfirmRequest,
) -> DocumentImport:
    try:
        return confirm_document(store, vault, runtime, document_id, body, now=clock(), proposals=proposals)
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=NOT_FOUND_DETAIL) from exc
    except _REVIEW_ERRORS as exc:
        raise _review_error(exc) from exc


@router.patch("/v1/documents/{document_id}/chapter", response_model=DocumentImport)
def patch_document_chapter(
    store: StoreDep,
    vault: VaultDep,
    runtime: RuntimeDep,
    proposals: DocumentProposalsDep,
    document_id: str,
    body: DocumentChapterPatch,
) -> DocumentImport:
    """Đổi mã hoặc tên chương (§6.5, §5.7 "Chương tự động")."""
    try:
        return update_document_chapter(store, vault, runtime, document_id, body, proposals=proposals)
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=NOT_FOUND_DETAIL) from exc
    except (DocumentChapterMissingError, ChapterCodeLockedError, VersionConflictError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.delete("/v1/documents/{document_id}", status_code=204, response_class=Response)
def delete_document(store: StoreDep, runtime: RuntimeDep, document_id: str) -> Response:
    try:
        delete_imported_document(store, runtime, document_id)
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=NOT_FOUND_DETAIL) from exc
    except DocumentBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return Response(status_code=204)
