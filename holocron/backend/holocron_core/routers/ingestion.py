"""Ingestion Gate: tải đề lên, tạo/xem item, sửa bảng và đề, đánh dấu non-cloneable, approve (SPECIFICATION §6.1–§6.4)."""

from __future__ import annotations

from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from ..domain.ingestion import GateStatus, IngestionItem, IngestionItemCreate, Severity, UploadedDocument
from ..domain.repositories import (
    AppStore,
    ConcurrentUpdateError,
    MissingReferenceError,
    RecordNotFoundError,
    VersionConflictError,
)
from ..domain.schemas import SolverInputs
from ..services.extraction import UnsupportedTopicError, sniff_media_type
from ..services.ingestion_gate import TABLE_SCHEMAS, TOPIC_ALGORITHMS, CellPatch, apply_patches, recompute_item
from .deps import REGISTRY, ExtractorDep, StoreDep

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_FILENAME_LENGTH = 255  # khớp `UploadedDocument.filename`

router = APIRouter()


class UploadResponse(BaseModel):
    document: UploadedDocument
    item: IngestionItem


class TablePatchRequest(BaseModel):
    editor_id: str
    expected_version: int = Field(ge=1)
    patches: list[CellPatch] = Field(min_length=1)


class StemPatchRequest(BaseModel):
    editor_id: str
    stem_latex: str = Field(min_length=10)


class NonCloneableRequest(BaseModel):
    editor_id: str
    reason: str = Field(min_length=5, max_length=300)


def _recompute(item: IngestionItem) -> IngestionItem:
    return recompute_item(item, REGISTRY)


def _get(store: AppStore, item_id: str) -> IngestionItem:
    """Item đang lưu, đúng như kho trả về; dùng làm trạng thái mong đợi khi ghi có điều kiện."""
    item = store.get_item(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="không tìm thấy ingestion item")
    return item


def _edit(store: AppStore, item_id: str) -> tuple[IngestionItem, IngestionItem]:
    """`(đã đọc, bản sao để sửa)`: router sửa trên bản sao rồi ghi có điều kiện theo bản đã đọc, không sửa thẳng kho."""
    seen = _get(store, item_id)
    return seen, seen.model_copy(deep=True)


def _create(store: AppStore, body: IngestionItemCreate) -> IngestionItem:
    item = IngestionItem(item_id=f"ing_{uuid4().hex[:12]}", **body.model_dump(exclude_unset=True))
    try:
        return store.save_item(_recompute(item))
    except MissingReferenceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _update(store: AppStore, item: IngestionItem, seen: IngestionItem) -> IngestionItem:
    """Ghi nguyên tử: chỉ thành công khi kho vẫn giữ đúng `seen` (version và mọi field, BE-003, §6.2).

    Sửa stem, gắn non-cloneable và approve không tăng version, nên chỉ so version thì hai lần ghi chạy chéo đều
    thành công và lần sau xoá thay đổi của lần trước (ví dụ mất cờ non-cloneable rồi item vẫn được approve, trái I5).
    """
    try:
        return store.update_item(item, seen.table_primary.version, expected=seen)
    except (VersionConflictError, ConcurrentUpdateError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RecordNotFoundError as exc:
        raise HTTPException(status_code=404, detail="không tìm thấy ingestion item") from exc


@router.post("/v1/ingestion/upload", response_model=UploadResponse, status_code=201)
async def upload_document(
    store: StoreDep,
    extractor: ExtractorDep,
    file: Annotated[UploadFile, File(description="Ảnh (PNG/JPEG/WebP) hoặc PDF của đề thi")],
    topic_key: Annotated[str, Form(pattern=r"^[a-z0-9_]+(\.[a-z0-9_]+){2,}$")],
    quantum: Annotated[int | None, Form(ge=1, le=20)] = None,
) -> UploadResponse:
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"file vượt {MAX_UPLOAD_BYTES} bytes")
    if not content:
        raise HTTPException(status_code=400, detail="file rỗng")
    filename = file.filename or "upload"
    if len(filename) > MAX_FILENAME_LENGTH:
        raise HTTPException(status_code=422, detail=f"tên file dài quá {MAX_FILENAME_LENGTH} ký tự")
    media_type = sniff_media_type(content)
    if media_type is None:
        raise HTTPException(status_code=415, detail="chỉ nhận PNG, JPEG, WebP hoặc PDF")
    declared = file.content_type
    if declared not in (None, "", "application/octet-stream", media_type):
        raise HTTPException(
            status_code=415, detail=f"Content-Type khai báo {declared} không khớp nội dung thực {media_type}"
        )

    document = store.save_document(UploadedDocument.from_content(content, filename=filename, media_type=media_type))
    try:
        extraction = extractor.extract(document, content, topic_key)
    except UnsupportedTopicError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    resolved_quantum = quantum if quantum is not None else extraction.quantum
    solver_inputs = SolverInputs(quantum=resolved_quantum) if resolved_quantum is not None else SolverInputs()
    item = _create(
        store,
        IngestionItemCreate(
            source_doc_id=document.document_id,
            page_number=extraction.page_number,
            topic_key=topic_key,
            stem_latex=extraction.stem_latex,
            options=extraction.options,
            table_primary=extraction.table_primary,
            table_secondary=extraction.table_secondary,
            page_text_layer=extraction.page_text_layer,
            solver_inputs=solver_inputs,
        ),
    )
    return UploadResponse(document=document, item=item)


@router.post("/v1/ingestion/items", response_model=IngestionItem, status_code=201)
def create_item(store: StoreDep, body: IngestionItemCreate) -> IngestionItem:
    if body.topic_key not in TOPIC_ALGORITHMS or body.topic_key not in TABLE_SCHEMAS:
        raise HTTPException(status_code=422, detail=f"chưa hỗ trợ topic {body.topic_key}")
    return _create(store, body)


@router.get("/v1/ingestion/items/{item_id}", response_model=IngestionItem)
def get_item(store: StoreDep, item_id: str) -> IngestionItem:
    return _get(store, item_id).model_copy(deep=True)


@router.patch("/v1/ingestion/items/{item_id}/table", response_model=IngestionItem)
def patch_table(store: StoreDep, item_id: str, body: TablePatchRequest) -> IngestionItem:
    seen, item = _edit(store, item_id)
    if item.approved:
        raise HTTPException(status_code=409, detail="item đã approve, không sửa được")
    if body.expected_version != item.table_primary.version:
        raise HTTPException(status_code=409, detail=f"version lệch: hiện tại {item.table_primary.version}")
    try:
        new_table, audit = apply_patches(item.table_primary, body.patches, body.editor_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    item.table_primary = new_table
    item.audits.append(audit)
    return _update(store, _recompute(item), seen)


@router.patch("/v1/ingestion/items/{item_id}/stem", response_model=IngestionItem)
def patch_stem(store: StoreDep, item_id: str, body: StemPatchRequest) -> IngestionItem:
    seen, item = _edit(store, item_id)
    if item.approved:
        raise HTTPException(status_code=409, detail="item đã approve, không sửa được")
    item.stem_latex = body.stem_latex
    return _update(store, _recompute(item), seen)


@router.post("/v1/ingestion/items/{item_id}/non-cloneable", response_model=IngestionItem)
def flag_non_cloneable(store: StoreDep, item_id: str, body: NonCloneableRequest) -> IngestionItem:
    seen, item = _edit(store, item_id)
    if item.approved:
        raise HTTPException(status_code=400, detail="item đã approve, không thể đánh dấu non-cloneable")
    item.non_cloneable = True
    item.non_cloneable_reason = body.reason
    return _update(store, item, seen)


@router.post("/v1/ingestion/items/{item_id}/approve", response_model=IngestionItem)
def approve(store: StoreDep, item_id: str) -> IngestionItem:
    seen, item = _edit(store, item_id)
    if item.non_cloneable:
        raise HTTPException(status_code=400, detail="item đang bị đánh dấu non-cloneable, không thể approve")
    if item.report is None or item.report.status is GateStatus.BLOCKED:
        blocking = [
            i.message_vi for i in (item.report.issues if item.report else []) if i.severity is Severity.BLOCKING
        ]
        raise HTTPException(status_code=409, detail={"message": "còn lỗi BLOCKING", "issues": blocking})
    item.approved = True
    return _update(store, item, seen)
