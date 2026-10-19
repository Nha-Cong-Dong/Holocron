"""FastAPI routes cho Ingestion Gate (Tuần 2: in-memory store; Tuần 4: thay bằng repository SQL)."""

from __future__ import annotations

from typing import Annotated
from uuid import uuid4

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from ..domain.schemas import MAX_PROCESSES, CPUSchedulingProblem, OptionKey, SelfSolveResult, SolverInputs
from ..infrastructure.template_library import InMemoryTemplateRegistry
from ..services.extraction import (
    DocumentExtractor,
    MockDualPassExtractor,
    UnsupportedTopicError,
    UploadedDocument,
    sniff_media_type,
)
from ..services.ingestion_gate import (
    TABLE_SCHEMAS,
    TOPIC_ALGORITHMS,
    CellPatch,
    ExtractedTable,
    GateIssue,
    GateReport,
    GateStatus,
    PatchAudit,
    Severity,
    apply_patches,
    check_table,
    gate_status,
    latex_issues,
    rerun_self_solve,
    table_to_problem,
)

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_FILENAME_LENGTH = 255  # khớp `UploadedDocument.filename`

app = FastAPI(title="Holocron Ingestion Gate", version="0.1.0a0")
REGISTRY = InMemoryTemplateRegistry()


class IngestionItemCreate(BaseModel):
    source_doc_id: str
    page_number: int = Field(ge=1)
    topic_key: str
    stem_latex: str
    options: dict[OptionKey, str]
    table_primary: ExtractedTable
    table_secondary: ExtractedTable | None = None
    page_text_layer: str | None = None
    solver_inputs: SolverInputs = Field(default_factory=SolverInputs)


class IngestionItem(IngestionItemCreate):
    item_id: str
    non_cloneable: bool = False
    non_cloneable_reason: str | None = None
    problem: CPUSchedulingProblem | None = None
    self_solve: SelfSolveResult | None = None
    report: GateReport | None = None
    audits: list[PatchAudit] = Field(default_factory=list)
    approved: bool = False


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


class IngestionStore:
    """Kho in-memory cho item và tài liệu tải lên; được cấp qua `Depends(get_store)` để test thay được."""

    def __init__(self) -> None:
        self._items: dict[str, IngestionItem] = {}
        self._documents: dict[str, UploadedDocument] = {}

    def get_item(self, item_id: str) -> IngestionItem | None:
        return self._items.get(item_id)

    def save_item(self, item: IngestionItem) -> IngestionItem:
        self._items[item.item_id] = item
        return item

    def save_document(self, document: UploadedDocument) -> UploadedDocument:
        """Cùng nội dung ⇒ cùng `document_id`: giữ bản ghi đầu tiên (idempotent)."""
        return self._documents.setdefault(document.document_id, document)


_STORE = IngestionStore()
_EXTRACTOR = MockDualPassExtractor()


def get_store() -> IngestionStore:
    return _STORE


def get_extractor() -> DocumentExtractor:
    return _EXTRACTOR


StoreDep = Annotated[IngestionStore, Depends(get_store)]
ExtractorDep = Annotated[DocumentExtractor, Depends(get_extractor)]


def _recompute(item: IngestionItem) -> IngestionItem:
    solver = REGISTRY.get_verified(item.topic_key)
    pre_issues: list[GateIssue] = []
    item.problem = None
    if solver is None:
        # Phòng thủ chiều sâu (I5): topic chưa có template đã kiểm chứng thì không thể tự giải ⇒ không được AUTO_PASS.
        item.self_solve = None
        pre_issues.append(
            GateIssue(
                code="SELF_SOLVE_MISMATCH",
                severity=Severity.BLOCKING,
                message_vi=f"Chưa có template đã kiểm chứng cho topic {item.topic_key}, không thể tự giải để đối chiếu.",
            )
        )
    elif item.table_primary.n_rows > MAX_PROCESSES:
        # Không tự giải được; check_table đã báo OUT_OF_RANGE nên không thêm issue (tránh gắn nhầm NOT_A_NUMBER).
        item.self_solve = SelfSolveResult(status="SKIPPED", solver_confidence=0.0)
    else:
        try:
            item.problem = table_to_problem(item.topic_key, item.table_primary, item.solver_inputs.quantum)
            item.self_solve = rerun_self_solve(solver, item.problem.to_template_params(), item.options)
            if item.self_solve.status == "SKIPPED":
                pre_issues.append(
                    GateIssue(
                        code="SELF_SOLVE_MISMATCH",
                        severity=Severity.BLOCKING,
                        message_vi="Sandbox không tính được đáp án để đối chiếu (SKIPPED: lỗi, quá thời gian hoặc sai hợp đồng).",
                    )
                )
        except ValueError as exc:
            item.problem = None
            item.self_solve = SelfSolveResult(status="SKIPPED", solver_confidence=0.0)
            pre_issues.append(
                GateIssue(code="NOT_A_NUMBER", severity=Severity.BLOCKING, message_vi=f"Chưa thể tự giải: {exc}")
            )
    issues = (
        pre_issues
        + check_table(item.topic_key, item.table_primary, item.table_secondary, item.page_text_layer, item.self_solve)
        + latex_issues(item.stem_latex)
    )
    item.report = GateReport(
        table_id=item.table_primary.table_id,
        table_version=item.table_primary.version,
        status=gate_status(issues),
        issues=issues,
    )
    return item


def _get(store: IngestionStore, item_id: str) -> IngestionItem:
    item = store.get_item(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="không tìm thấy ingestion item")
    return item


def _create(store: IngestionStore, body: IngestionItemCreate) -> IngestionItem:
    item = IngestionItem(item_id=f"ing_{uuid4().hex[:12]}", **body.model_dump(exclude_unset=True))
    return store.save_item(_recompute(item))


@app.post("/v1/ingestion/upload", response_model=UploadResponse, status_code=201)
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


@app.post("/v1/ingestion/items", response_model=IngestionItem, status_code=201)
def create_item(store: StoreDep, body: IngestionItemCreate) -> IngestionItem:
    if body.topic_key not in TOPIC_ALGORITHMS or body.topic_key not in TABLE_SCHEMAS:
        raise HTTPException(status_code=422, detail=f"chưa hỗ trợ topic {body.topic_key}")
    return _create(store, body)


@app.get("/v1/ingestion/items/{item_id}", response_model=IngestionItem)
def get_item(store: StoreDep, item_id: str) -> IngestionItem:
    return _get(store, item_id)


@app.patch("/v1/ingestion/items/{item_id}/table", response_model=IngestionItem)
def patch_table(store: StoreDep, item_id: str, body: TablePatchRequest) -> IngestionItem:
    item = _get(store, item_id)
    if item.approved:
        raise HTTPException(status_code=409, detail="item đã approve, không sửa được")
    # TODO (Week 4): Implement atomic optimistic locking using SQLAlchemy version_id_col when migrating to database.
    if body.expected_version != item.table_primary.version:
        raise HTTPException(status_code=409, detail=f"version lệch: hiện tại {item.table_primary.version}")
    try:
        new_table, audit = apply_patches(item.table_primary, body.patches, body.editor_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    item.table_primary = new_table
    item.audits.append(audit)
    return store.save_item(_recompute(item))


@app.patch("/v1/ingestion/items/{item_id}/stem", response_model=IngestionItem)
def patch_stem(store: StoreDep, item_id: str, body: StemPatchRequest) -> IngestionItem:
    item = _get(store, item_id)
    if item.approved:
        raise HTTPException(status_code=409, detail="item đã approve, không sửa được")
    item.stem_latex = body.stem_latex
    return store.save_item(_recompute(item))


@app.post("/v1/ingestion/items/{item_id}/non-cloneable", response_model=IngestionItem)
def flag_non_cloneable(store: StoreDep, item_id: str, body: NonCloneableRequest) -> IngestionItem:
    item = _get(store, item_id)
    if item.approved:
        raise HTTPException(status_code=400, detail="item đã approve, không thể đánh dấu non-cloneable")
    item.non_cloneable = True
    item.non_cloneable_reason = body.reason
    return store.save_item(item)


@app.post("/v1/ingestion/items/{item_id}/approve", response_model=IngestionItem)
def approve(store: StoreDep, item_id: str) -> IngestionItem:
    item = _get(store, item_id)
    if item.non_cloneable:
        raise HTTPException(status_code=400, detail="item đang bị đánh dấu non-cloneable, không thể approve")
    if item.report is None or item.report.status is GateStatus.BLOCKED:
        blocking = [
            i.message_vi for i in (item.report.issues if item.report else []) if i.severity is Severity.BLOCKING
        ]
        raise HTTPException(status_code=409, detail={"message": "còn lỗi BLOCKING", "issues": blocking})
    item.approved = True
    return store.save_item(item)
