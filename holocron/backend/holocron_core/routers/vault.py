"""Knowledge Vault (SPECIFICATION §6.6), phần không cần LLM: liệt kê và tìm chunk, sửa nhãn chunk, tạo, liệt kê,
xác nhận và từ chối cặp khái niệm. `POST /v1/vault/concept-pairs/propose` (cần LLM) chưa có ở đây.

Mọi truy vấn bắt buộc lọc theo `subject_code` (ADR-006 mục 4). Lỗi: 404 không có chunk/cặp; 409 lệch
`expected_version`; 422 body sai hoặc `definition_quote` không khớp nguyên văn văn bản chunk (I3).

Giới hạn đầu vào: `editor_id`, `chunk_id`, `pair_id` tối đa 128 ký tự; mỗi `topic_key` tối đa 128 ký tự và tối đa 32
khoá mỗi request. Mọi thao tác sửa (nhãn chunk; tạo, xác nhận, từ chối cặp) lưu `editor_id` và thời điểm kèm bản ghi
(hàng Repudiation của §13).
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Path, Query
from pydantic import BaseModel, Field, ValidationError

from ..domain.repositories import RecordNotFoundError, VersionConflictError
from ..domain.schemas import ChunkCategory, KnowledgeChunk, StrictModel
from ..domain.vault import (
    CHAPTER_CODE_PATTERN,
    MAX_ID_CHARS,
    MAX_TOPIC_KEYS,
    SUBJECT_CODE_PATTERN,
    ConceptPair,
    ConceptPairStatus,
    QuoteMismatchError,
    SearchHit,
    TopicKey,
    VaultEdit,
)
from ..services import vault as vault_service
from .deps import ClockDep, VaultDep

router = APIRouter(prefix="/v1/vault")

SubjectQuery = Annotated[str, Query(pattern=SUBJECT_CODE_PATTERN)]
ChapterQuery = Annotated[str | None, Query(pattern=CHAPTER_CODE_PATTERN)]
TopicKeysQuery = Annotated[list[TopicKey] | None, Query(alias="topic_key", max_length=MAX_TOPIC_KEYS)]
ChunkIdPath = Annotated[str, Path(min_length=1, max_length=MAX_ID_CHARS)]
PairIdPath = Annotated[str, Path(min_length=1, max_length=MAX_ID_CHARS)]
EditorId = Annotated[str, Field(min_length=1, max_length=MAX_ID_CHARS)]


class ChunkListResponse(BaseModel):
    total: int
    chunks: list[KnowledgeChunk]


class ChunkLabelsPatch(StrictModel):
    editor_id: EditorId
    expected_version: int = Field(ge=1)
    chapter_code: str | None = Field(default=None, pattern=CHAPTER_CODE_PATTERN)
    category: ChunkCategory | None = None
    topic_keys: list[TopicKey] | None = Field(default=None, max_length=MAX_TOPIC_KEYS)


class ConceptPairCreate(StrictModel):
    editor_id: EditorId
    chunk_id: str = Field(min_length=1, max_length=MAX_ID_CHARS)
    term: str = Field(min_length=1, max_length=120)
    definition_quote: str = Field(min_length=8, max_length=600)


class ConceptPairConfirm(StrictModel):
    editor_id: EditorId
    expected_version: int = Field(ge=1)
    term: str | None = Field(default=None, min_length=1, max_length=120)
    definition_quote: str | None = Field(default=None, min_length=8, max_length=600)


class ConceptPairReject(StrictModel):
    editor_id: EditorId
    expected_version: int = Field(ge=1)


def _not_found(exc: RecordNotFoundError) -> HTTPException:
    return HTTPException(status_code=404, detail=f"không tìm thấy {exc.entity}")


def _conflict(exc: VersionConflictError) -> HTTPException:
    return HTTPException(status_code=409, detail=str(exc))


def _invalid(field: str, message: str) -> HTTPException:
    """422 cùng dạng lỗi validate của FastAPI (`[{loc, msg, type}]`, §6.0)."""
    return HTTPException(status_code=422, detail=[{"loc": ["body", field], "msg": message, "type": "value_error"}])


def _validation(exc: ValidationError) -> HTTPException:
    errors: list[dict[str, Any]] = [
        {"loc": ["body", *error["loc"]], "msg": error["msg"], "type": error["type"]} for error in exc.errors()
    ]
    return HTTPException(status_code=422, detail=errors)


@router.get("/chunks", response_model=ChunkListResponse)
def list_chunks(
    vault: VaultDep,
    subject_code: SubjectQuery,
    chapter_code: ChapterQuery = None,
    topic_keys: TopicKeysQuery = None,
    label_status: Literal["proposed", "confirmed"] | None = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> ChunkListResponse:
    total, chunks = vault_service.list_chunks(
        vault,
        subject_code,
        chapter_code=chapter_code,
        topic_keys=topic_keys or (),
        label_status=label_status,
        offset=offset,
        limit=limit,
    )
    return ChunkListResponse(total=total, chunks=chunks)


@router.get("/search", response_model=list[SearchHit])
def search(
    vault: VaultDep,
    q: Annotated[str, Query(min_length=1, max_length=200)],
    subject_code: SubjectQuery,
    topic_keys: TopicKeysQuery = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> list[SearchHit]:
    return vault_service.search_chunks(vault, subject_code, q, topic_keys=topic_keys or (), limit=limit)


@router.patch("/chunks/{chunk_id}/labels", response_model=KnowledgeChunk)
def patch_chunk_labels(
    vault: VaultDep, clock: ClockDep, chunk_id: ChunkIdPath, body: ChunkLabelsPatch
) -> KnowledgeChunk:
    try:
        return vault_service.update_chunk_labels(
            vault,
            chunk_id,
            expected_version=body.expected_version,
            chapter_code=body.chapter_code,
            category=body.category,
            topic_keys=body.topic_keys,
            edit=VaultEdit(editor_id=body.editor_id, at=clock()),
        )
    except RecordNotFoundError as exc:
        raise _not_found(exc) from exc
    except VersionConflictError as exc:
        raise _conflict(exc) from exc


@router.post("/concept-pairs", response_model=ConceptPair, status_code=201)
def create_concept_pair(vault: VaultDep, clock: ClockDep, body: ConceptPairCreate) -> ConceptPair:
    try:
        return vault_service.create_concept_pair(
            vault,
            chunk_id=body.chunk_id,
            term=body.term,
            definition_quote=body.definition_quote,
            now=clock(),
            editor_id=body.editor_id,
        )
    except RecordNotFoundError as exc:
        raise _not_found(exc) from exc
    except QuoteMismatchError as exc:
        raise _invalid("definition_quote", str(exc)) from exc
    except ValidationError as exc:
        raise _validation(exc) from exc


@router.get("/concept-pairs", response_model=list[ConceptPair])
def list_concept_pairs(
    vault: VaultDep,
    subject_code: SubjectQuery,
    chapter_code: ChapterQuery = None,
    status: ConceptPairStatus | None = None,
) -> list[ConceptPair]:
    return vault.list_concept_pairs(subject_code, chapter_code, status)


@router.post("/concept-pairs/{pair_id}/confirm", response_model=ConceptPair)
def confirm_concept_pair(
    vault: VaultDep, clock: ClockDep, pair_id: PairIdPath, body: ConceptPairConfirm
) -> ConceptPair:
    try:
        return vault_service.confirm_concept_pair(
            vault,
            pair_id,
            expected_version=body.expected_version,
            now=clock(),
            term=body.term,
            definition_quote=body.definition_quote,
            editor_id=body.editor_id,
        )
    except RecordNotFoundError as exc:
        raise _not_found(exc) from exc
    except VersionConflictError as exc:
        raise _conflict(exc) from exc
    except QuoteMismatchError as exc:
        raise _invalid("definition_quote", str(exc)) from exc
    except ValidationError as exc:
        raise _validation(exc) from exc


@router.post("/concept-pairs/{pair_id}/reject", response_model=ConceptPair)
def reject_concept_pair(vault: VaultDep, clock: ClockDep, pair_id: PairIdPath, body: ConceptPairReject) -> ConceptPair:
    try:
        return vault_service.reject_concept_pair(
            vault,
            pair_id,
            expected_version=body.expected_version,
            edit=VaultEdit(editor_id=body.editor_id, at=clock()),
        )
    except RecordNotFoundError as exc:
        raise _not_found(exc) from exc
    except VersionConflictError as exc:
        raise _conflict(exc) from exc
