"""Blueprint và batch twin (SPECIFICATION §6.3, §6.7).

- `POST /v1/blueprints`: tạo blueprint chỉ gồm slot lý thuyết (duyệt 2026-10-28 câu 2 và 15); `item_ids` khác rỗng
  (câu tính toán) trả 422 "chưa hỗ trợ" trước mọi lần ghi. Blueprint tính toán vẫn nạp được qua
  `IngestionStore.save_blueprint(blueprint, item_ids)` và luồng sinh twin tính toán giữ nguyên.
- `GET /v1/blueprints?subject_code=`, `GET /v1/blueprints/{blueprint_id}`: chỉ đọc.
- `POST /v1/blueprints/{blueprint_id}/twins`: sinh `TwinBatch` cho mọi slot; mọi batch được lưu qua
  `TwinBatchRepository.save_batch` (kể cả khi không có `Idempotency-Key`).
"""

from __future__ import annotations

import hashlib
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query, Response

from ..domain.schemas import (
    BlueprintCreate,
    BlueprintSummary,
    CognitiveLevel,
    ExamBlueprint,
    ExamFormat,
    QuestionFormat,
    Subject,
    TheorySlot,
    TheorySlotSpec,
)
from ..domain.twins import TwinBatch, TwinBatchRequest
from ..domain.vault import SUBJECT_CODE_PATTERN
from ..services.capabilities import compute_capabilities, unavailable_error
from ..services.twin_batch import generate_twin_batch
from .deps import REGISTRY, AuthorDep, CitationDep, ClockDep, RuntimeConfigDep, StoreDep, VaultDep

router = APIRouter()

COMPUTATIONAL_NOT_SUPPORTED = "Blueprint có câu tính toán chưa được hỗ trợ trong bản này"
BLUEPRINT_NOT_FOUND = "không tìm thấy blueprint"


def _source_chunk_errors(vault: VaultDep, body: BlueprintCreate) -> list[str]:
    """§6.7 bước 4: `source_chunk_id` (tuỳ chọn) phải là chunk có thật, cùng môn và cùng `chapter_code` với slot."""
    errors: list[str] = []
    for index, spec in enumerate(body.theory_slots):
        if spec.source_chunk_id is None:
            continue
        chunk = vault.get_chunk(spec.source_chunk_id)
        if chunk is None:
            errors.append(f"theory_slots[{index}]: không có chunk {spec.source_chunk_id}")
        elif chunk.subject_code != body.subject_code:
            errors.append(f"theory_slots[{index}]: chunk {spec.source_chunk_id} thuộc môn khác")
        elif chunk.chapter_code != spec.chapter_code:
            errors.append(
                f"theory_slots[{index}]: chunk {spec.source_chunk_id} thuộc chương khác chapter_code của slot"
            )
    return errors


def _slot_format(spec: TheorySlotSpec, exam_format: ExamFormat) -> QuestionFormat:
    """`format` người dùng chọn; null ⇒ mặc định theo `exam_format` của môn (§5.14). Không bao giờ đổi ESSAY sang MCQ4."""
    return QuestionFormat(spec.format) if spec.format is not None else exam_format.default_theory_format()


@router.post("/v1/blueprints", response_model=ExamBlueprint, status_code=201)
def create_blueprint(
    store: StoreDep,
    vault: VaultDep,
    clock: ClockDep,
    config: RuntimeConfigDep,
    body: BlueprintCreate,
    response: Response,
) -> ExamBlueprint:
    """Theo đúng thứ tự §6.7: body sai (422, FastAPI) → câu tính toán chưa hỗ trợ (422) → `source_chunk_id` (422) →
    `format` mặc định theo môn → slot ESSAY khi khả năng `essay_grading_offline` không khả dụng (409
    `CapabilityError`) → lưu (môn chưa có thì tạo ngầm cùng giao dịch) → `201` + `Location`."""
    if body.item_ids:
        raise HTTPException(status_code=422, detail=COMPUTATIONAL_NOT_SUPPORTED)
    errors = _source_chunk_errors(vault, body)
    if errors:
        raise HTTPException(status_code=422, detail="; ".join(errors))
    subject = store.get_subject(body.subject_code)
    exam_format = subject.exam_format if subject is not None else ExamFormat.MIXED
    formats = [_slot_format(spec, exam_format) for spec in body.theory_slots]
    if QuestionFormat.ESSAY in formats:
        error = unavailable_error(compute_capabilities(config), "essay_grading_offline")
        if error is not None:
            raise HTTPException(status_code=409, detail=error.model_dump(mode="json"))
    now = clock()
    blueprint = ExamBlueprint(
        source_exam_doc_id=None,
        subject_code=body.subject_code,
        total_questions=len(body.theory_slots),
        duration_minutes=body.duration_minutes,
        slots=[
            TheorySlot(
                slot_index=index,
                chapter_code=spec.chapter_code,
                source_chunk_id=spec.source_chunk_id,
                format=fmt,
                cognitive_level=CognitiveLevel(spec.cognitive_level),
            )
            for index, (spec, fmt) in enumerate(zip(body.theory_slots, formats, strict=True), start=1)
        ],
        created_at=now,
    )
    implicit = None if subject is not None else Subject.implicit(body.subject_code, now)
    stored = store.save_blueprint(blueprint, (), implicit_subject=implicit)
    response.headers["Location"] = f"/v1/blueprints/{stored.blueprint_id}"
    return stored


@router.get("/v1/blueprints", response_model=list[BlueprintSummary])
def list_blueprints(
    store: StoreDep, subject_code: Annotated[str | None, Query(pattern=SUBJECT_CODE_PATTERN)] = None
) -> list[BlueprintSummary]:
    return store.list_blueprint_summaries(subject_code)


@router.get("/v1/blueprints/{blueprint_id}", response_model=ExamBlueprint)
def get_blueprint(store: StoreDep, blueprint_id: str) -> ExamBlueprint:
    blueprint = store.get_blueprint(blueprint_id)
    if blueprint is None:
        raise HTTPException(status_code=404, detail=BLUEPRINT_NOT_FOUND)
    return blueprint


@router.post("/v1/blueprints/{blueprint_id}/twins", response_model=TwinBatch, status_code=201)
def create_twins(
    store: StoreDep,
    vault: VaultDep,
    author: AuthorDep,
    citations: CitationDep,
    clock: ClockDep,
    blueprint_id: str,
    body: TwinBatchRequest,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> TwinBatch:
    blueprint = store.get_blueprint(blueprint_id)
    if blueprint is None:
        raise HTTPException(status_code=404, detail=BLUEPRINT_NOT_FOUND)
    # I5 kiểm lại ở cửa sinh twin: mọi item làm nguồn của blueprint PHẢI còn tồn tại, đã duyệt và không non-cloneable.
    invalid = [
        item_id
        for item_id in store.blueprint_item_ids(blueprint_id)
        if (item := store.get_item(item_id)) is None or not item.approved or item.non_cloneable
    ]
    if invalid:
        raise HTTPException(
            status_code=409, detail=f"blueprint chứa item chưa được duyệt hoặc không còn hợp lệ: {', '.join(invalid)}"
        )

    now = clock()
    digest = hashlib.sha256(body.model_dump_json().encode("utf-8")).hexdigest()
    existing = store.get_idempotent(blueprint_id, idempotency_key, now) if idempotency_key is not None else None
    if existing is not None and existing.body_digest == digest:
        return existing.batch  # cùng key + cùng body trong 24 h: trả lại đúng batch cũ, không chạy lại sandbox

    batch = generate_twin_batch(
        blueprint, body, author=author, registry=REGISTRY, citations=citations, vault=vault, created_at=now
    )
    # Cùng key nhưng khác body: batch mới được lưu với khoá NULL, KHÔNG ghi đè bản ghi của khoá (§6.3, `save_batch`).
    return store.save_batch(batch, idempotency_key, digest)
