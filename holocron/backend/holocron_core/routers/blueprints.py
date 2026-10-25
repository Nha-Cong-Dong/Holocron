"""`POST /v1/blueprints/{blueprint_id}/twins`: sinh `TwinBatch` cho mọi slot của blueprint (SPECIFICATION §6.3)."""

from __future__ import annotations

import hashlib
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException

from ..domain.twins import IdempotencyRecord, TwinBatch, TwinBatchRequest
from ..services.twin_batch import generate_twin_batch
from .deps import REGISTRY, AuthorDep, CitationDep, ClockDep, StoreDep

router = APIRouter()


@router.post("/v1/blueprints/{blueprint_id}/twins", response_model=TwinBatch, status_code=201)
def create_twins(
    store: StoreDep,
    author: AuthorDep,
    citations: CitationDep,
    clock: ClockDep,
    blueprint_id: str,
    body: TwinBatchRequest,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> TwinBatch:
    blueprint = store.get_blueprint(blueprint_id)
    if blueprint is None:
        raise HTTPException(status_code=404, detail="không tìm thấy blueprint")
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

    batch = generate_twin_batch(blueprint, body, author=author, registry=REGISTRY, citations=citations)
    # Cùng key nhưng khác body: coi là request mới và KHÔNG ghi đè bản ghi cũ (spec chưa định nghĩa trường hợp này).
    if idempotency_key is not None and existing is None:
        store.save_idempotent(
            blueprint_id, idempotency_key, IdempotencyRecord(body_digest=digest, stored_at=now, batch=batch)
        )
    return batch
