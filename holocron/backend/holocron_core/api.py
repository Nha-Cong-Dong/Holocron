"""FastAPI routes cho Ingestion Gate (Tuần 2: in-memory store; Tuần 4: thay bằng repository SQL)."""

from __future__ import annotations

from uuid import uuid4

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .ingestion_gate import (
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
    table_to_params,
)
from .schemas import OptionKey, SelfSolveResult, SolverInputs
from .template_library import InMemoryTemplateRegistry

app = FastAPI(title="Holocron Ingestion Gate", version="0.2.0")
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
    self_solve: SelfSolveResult | None = None
    report: GateReport | None = None
    audits: list[PatchAudit] = Field(default_factory=list)
    approved: bool = False


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


STORE: dict[str, IngestionItem] = {}


def _recompute(item: IngestionItem) -> IngestionItem:
    solver = REGISTRY.get_verified(item.topic_key)
    pre_issues: list[GateIssue] = []
    if solver is not None:
        try:
            params = table_to_params(
                item.topic_key, item.table_primary, item.solver_inputs.model_dump(exclude_none=True)
            )
            item.self_solve = rerun_self_solve(solver, params, item.options)
        except (ValueError, KeyError) as exc:
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


def _get(item_id: str) -> IngestionItem:
    item = STORE.get(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="không tìm thấy ingestion item")
    return item


@app.post("/v1/ingestion/items", response_model=IngestionItem, status_code=201)
def create_item(body: IngestionItemCreate) -> IngestionItem:
    item = IngestionItem(item_id=f"ing_{uuid4().hex[:12]}", **body.model_dump(exclude_unset=True))
    STORE[item.item_id] = _recompute(item)
    return STORE[item.item_id]


@app.get("/v1/ingestion/items/{item_id}", response_model=IngestionItem)
def get_item(item_id: str) -> IngestionItem:
    return _get(item_id)


@app.patch("/v1/ingestion/items/{item_id}/table", response_model=IngestionItem)
def patch_table(item_id: str, body: TablePatchRequest) -> IngestionItem:
    item = _get(item_id)
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
    STORE[item_id] = _recompute(item)
    return STORE[item_id]


@app.patch("/v1/ingestion/items/{item_id}/stem", response_model=IngestionItem)
def patch_stem(item_id: str, body: StemPatchRequest) -> IngestionItem:
    item = _get(item_id)
    if item.approved:
        raise HTTPException(status_code=409, detail="item đã approve, không sửa được")
    item.stem_latex = body.stem_latex
    STORE[item_id] = _recompute(item)
    return STORE[item_id]


@app.post("/v1/ingestion/items/{item_id}/non-cloneable", response_model=IngestionItem)
def flag_non_cloneable(item_id: str, body: NonCloneableRequest) -> IngestionItem:
    item = _get(item_id)
    if item.approved:
        raise HTTPException(status_code=400, detail="item đã approve, không thể đánh dấu non-cloneable")
    item.non_cloneable = True
    item.non_cloneable_reason = body.reason
    return item


@app.post("/v1/ingestion/items/{item_id}/approve", response_model=IngestionItem)
def approve(item_id: str) -> IngestionItem:
    item = _get(item_id)
    if item.non_cloneable:
        raise HTTPException(status_code=400, detail="item đang bị đánh dấu non-cloneable, không thể approve")
    if item.report is None or item.report.status is GateStatus.BLOCKED:
        blocking = [
            i.message_vi for i in (item.report.issues if item.report else []) if i.severity is Severity.BLOCKING
        ]
        raise HTTPException(status_code=409, detail={"message": "còn lỗi BLOCKING", "issues": blocking})
    item.approved = True
    return item
