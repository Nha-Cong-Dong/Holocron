"""Knowledge Vault (SPECIFICATION §6.6): liệt kê và tìm chunk, sửa nhãn chunk, tạo, liệt kê, xác nhận và từ chối cặp
khái niệm, và `POST /v1/vault/concept-pairs/propose` (LLM đề xuất cặp, cần khả năng `concept_pair_llm_proposal`).

Mọi truy vấn bắt buộc lọc theo `subject_code` (ADR-006 mục 4). Lỗi: 404 không có chunk/cặp; 409 lệch
`expected_version`; 422 body sai hoặc `definition_quote` không khớp nguyên văn văn bản chunk (I3).
`propose` (spec v0.6 §6.0, §6.6, §11.4): thiếu một trong ba biến LLM bắt buộc hoặc cấu hình LLM không hợp lệ (biến
sai khi nạp được `get_runtime_config` coi như chưa đặt; `ConfigError` khi dựng client) ⇒ 409 `CapabilityError`
(`reason = "llm_not_configured"`, thông điệp chỉ nêu tên biến), không gọi mạng; chunk khác môn ⇒ 422; gọi LLM lỗi,
phản hồi không parse được sau mọi lần thử hoặc hết hạn thời gian tổng (`LlmTaskLimits.deadline_s`, áp dụng cả cho
từng request HTTP của client) ⇒ 502 `{"code": "LLM_UPSTREAM_ERROR", "message"}`, không lộ endpoint, tên model hay nội
dung phản hồi. Client LLM cấp qua `Depends(get_chat_client)`, đồng hồ đơn điệu đo hạn qua `Depends(get_monotonic)`;
test thay bằng stub.

Giới hạn đầu vào: `editor_id`, `chunk_id`, `pair_id` tối đa 128 ký tự; mỗi `topic_key` tối đa 128 ký tự và tối đa 32
khoá mỗi request. Mọi thao tác sửa (nhãn chunk; tạo, xác nhận, từ chối cặp) lưu `editor_id` và thời điểm kèm bản ghi
(hàng Repudiation của §13).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response
from pydantic import BaseModel, Field, ValidationError, model_validator

from ..domain.capabilities import CapabilityError
from ..domain.llm import ChatCompletionClient, LlmUpstreamError, LlmUpstreamErrorDetail
from ..domain.repositories import AppStore, RecordNotFoundError, VersionConflictError
from ..domain.schemas import ChunkCategory, KnowledgeChunk, StrictModel
from ..domain.vault import (
    CHAPTER_CODE_PATTERN,
    MAX_ID_CHARS,
    MAX_TOPIC_KEYS,
    SUBJECT_CODE_PATTERN,
    ConceptPair,
    ConceptPairStatus,
    ProposalJob,
    QuoteMismatchError,
    SearchHit,
    TopicKey,
    VaultEdit,
)
from ..infrastructure.llm_client import OpenAiChatClient
from ..infrastructure.llm_config import ConfigError
from ..services import vault as vault_service
from ..services.capabilities import compute_capabilities, unavailable_error
from ..services.concept_proposal import MAX_PROPOSAL_CHUNKS, ChunkSubjectMismatchError, propose_concept_pairs
from ..services.concept_proposal_job import ClientFactory
from .deps import ClockDep, ProposalJobsDep, RuntimeConfigDep, StoreDep, VaultDep
from .runtime import NO_LOCAL_FAILURES, LocalFailuresDep

router = APIRouter(prefix="/v1/vault")

SubjectQuery = Annotated[str, Query(pattern=SUBJECT_CODE_PATTERN)]
ChapterQuery = Annotated[str | None, Query(pattern=CHAPTER_CODE_PATTERN)]
TopicKeysQuery = Annotated[list[TopicKey] | None, Query(alias="topic_key", max_length=MAX_TOPIC_KEYS)]
ChunkIdPath = Annotated[str, Path(min_length=1, max_length=MAX_ID_CHARS)]
PairIdPath = Annotated[str, Path(min_length=1, max_length=MAX_ID_CHARS)]
EditorId = Annotated[str, Field(min_length=1, max_length=MAX_ID_CHARS)]
ChunkIdField = Annotated[str, Field(min_length=1, max_length=MAX_ID_CHARS)]

LLM_UPSTREAM_MESSAGE = (
    "Gọi LLM không thành công. Hãy thử lại sau, hoặc tự tạo cặp khái niệm bằng cách bôi đoạn trong chunk."
)


def get_chat_client(
    config: RuntimeConfigDep, local_failures: LocalFailuresDep = NO_LOCAL_FAILURES
) -> ChatCompletionClient:
    """Client LLM cho đề xuất cặp khái niệm, dùng model `HOLOCRON_LLM_MODEL_EXTRACT` (§11.2).

    Khả năng `concept_pair_llm_proposal` không khả dụng, hoặc cấu hình LLM không dựng được client (`ConfigError`) ⇒
    409 `CapabilityError` (spec v0.6 §11.4). Không gửi gì ra mạng ở đây. Client dựng mỗi request nên cận tổng
    `LlmTaskLimits.deadline_s` của nó (tính từ lúc dựng) trùng với hạn của lần đề xuất.
    """
    error = unavailable_error(compute_capabilities(config, local_failures=local_failures), "concept_pair_llm_proposal")
    if error is not None:
        raise HTTPException(status_code=409, detail=error.model_dump(mode="json"))
    failure: str | None = None
    try:
        return OpenAiChatClient.from_settings(config.llm, model=config.llm.model_extract)
    except ConfigError as exc:
        failure = str(exc)  # ConfigError chỉ nêu tên biến, không bao giờ nêu giá trị
    invalid = CapabilityError(
        capability="concept_pair_llm_proposal",
        reason="llm_not_configured",
        message=f"Khả năng concept_pair_llm_proposal không khả dụng: cấu hình LLM không hợp lệ ({failure}).",
    )
    raise HTTPException(status_code=409, detail=invalid.model_dump(mode="json"))


def get_chat_client_factory(
    config: RuntimeConfigDep, local_failures: LocalFailuresDep = NO_LOCAL_FAILURES
) -> ClientFactory:
    """Hàm dựng client LLM cho job nền: mỗi lô một client, để cận tổng `deadline_s` của client tính từ lúc lô bắt đầu.

    Kiểm khả năng và cấu hình ngay khi tạo job như `get_chat_client` (409 `CapabilityError`); không gọi mạng.
    """
    get_chat_client(config, local_failures)
    settings = config.llm
    return lambda: OpenAiChatClient.from_settings(settings, model=settings.model_extract)


def get_monotonic() -> Callable[[], float]:
    """Đồng hồ đơn điệu (giây) đo hạn thời gian tổng của một lần đề xuất; test thay bằng đồng hồ giả."""
    return time.monotonic


ChatClientDep = Annotated[ChatCompletionClient, Depends(get_chat_client)]
ChatClientFactoryDep = Annotated[ClientFactory, Depends(get_chat_client_factory)]
MonotonicDep = Annotated[Callable[[], float], Depends(get_monotonic)]


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


class ConceptPairProposeRequest(StrictModel):
    subject_code: str = Field(pattern=SUBJECT_CODE_PATTERN)
    chunk_ids: list[ChunkIdField] = Field(min_length=1, max_length=MAX_PROPOSAL_CHUNKS)


class ConceptPairProposeResponse(BaseModel):
    pairs: list[ConceptPair]
    rejected_count: int


MAX_JOB_CHUNKS = 2000
JobIdPath = Annotated[str, Path(min_length=1, max_length=MAX_ID_CHARS)]


class ProposalJobCreate(StrictModel):
    """Đúng một trong `document_id` (mọi chunk của tài liệu) và `chunk_ids` (1–2000 chunk, cùng môn) (§6.6)."""

    subject_code: str = Field(pattern=SUBJECT_CODE_PATTERN)
    document_id: str | None = Field(default=None, min_length=1, max_length=MAX_ID_CHARS)
    chunk_ids: list[ChunkIdField] | None = Field(default=None, min_length=1, max_length=MAX_JOB_CHUNKS)

    @model_validator(mode="after")
    def _exactly_one_source(self) -> ProposalJobCreate:
        if (self.document_id is None) == (self.chunk_ids is None):
            raise ValueError("cần đúng một trong document_id và chunk_ids")
        return self


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


@router.post("/concept-pairs/propose", response_model=ConceptPairProposeResponse)
def propose_pairs(
    vault: StoreDep,
    clock: ClockDep,
    config: RuntimeConfigDep,
    client: ChatClientDep,
    monotonic: MonotonicDep,
    body: ConceptPairProposeRequest,
) -> ConceptPairProposeResponse:
    try:
        result = propose_concept_pairs(
            vault,
            client,
            subject_code=body.subject_code,
            chunk_ids=body.chunk_ids,
            now=clock(),
            limits=config.llm.task_limits,
            monotonic=monotonic,
            pages=vault,
            preselect_enabled=config.llm.proposal.pair_preselect_enabled,
        )
    except RecordNotFoundError as exc:
        raise _not_found(exc) from exc
    except ChunkSubjectMismatchError as exc:
        raise _invalid("chunk_ids", str(exc)) from exc
    except LlmUpstreamError:
        detail = LlmUpstreamErrorDetail(message=LLM_UPSTREAM_MESSAGE).model_dump(mode="json")
        raise HTTPException(status_code=502, detail=detail) from None
    return ConceptPairProposeResponse(pairs=result.pairs, rejected_count=result.rejected_count)


# --------------------------------------------------------------------------- #
# Job nền đề xuất cặp (§5.8, §6.6; ADR-007 mục 3)
# --------------------------------------------------------------------------- #


def _job_chunk_ids(store: AppStore, body: ProposalJobCreate) -> list[str]:
    """`chunk_id` của job theo thứ tự, bỏ trùng; 404 tài liệu/chunk, 422 khác môn, 409 tài liệu chưa có chunk."""
    if body.document_id is not None:
        document = store.get_imported_document(body.document_id)
        if document is None:
            raise HTTPException(status_code=404, detail="không tìm thấy tài liệu")
        if document.meta.subject_code != body.subject_code:
            raise _invalid("document_id", f"tài liệu không thuộc môn {body.subject_code}")
        ids = [chunk.chunk_id for chunk in store.list_chunks(body.subject_code) if chunk.doc_id == body.document_id]
        if not ids:
            raise HTTPException(status_code=409, detail="tài liệu chưa có chunk nào trong Vault")
        return ids
    ids = list(dict.fromkeys(body.chunk_ids or ()))
    for chunk_id in ids:
        chunk = store.get_chunk(chunk_id)
        if chunk is None:
            raise HTTPException(status_code=404, detail="không tìm thấy chunk")
        if chunk.subject_code != body.subject_code:
            raise _invalid("chunk_ids", str(ChunkSubjectMismatchError(chunk_id, body.subject_code)))
    return ids


@router.post("/concept-pairs/jobs", response_model=ProposalJob, status_code=202)
def create_proposal_job(
    store: StoreDep,
    clock: ClockDep,
    config: RuntimeConfigDep,
    jobs: ProposalJobsDep,
    client_factory: ChatClientFactoryDep,
    body: ProposalJobCreate,
    response: Response,
) -> ProposalJob:
    chunk_ids = _job_chunk_ids(store, body)
    job = jobs.submit(
        subject_code=body.subject_code,
        chunk_ids=chunk_ids,
        document_id=body.document_id,
        store=store,
        client_factory=client_factory,
        limits=config.llm.task_limits,
        now=clock(),
    )
    response.headers["Location"] = f"/v1/vault/concept-pairs/jobs/{job.job_id}"
    return job


@router.get("/concept-pairs/jobs", response_model=list[ProposalJob])
def list_proposal_jobs(jobs: ProposalJobsDep, subject_code: SubjectQuery) -> list[ProposalJob]:
    return jobs.list_jobs(subject_code)


@router.get("/concept-pairs/jobs/{job_id}", response_model=ProposalJob)
def get_proposal_job(jobs: ProposalJobsDep, job_id: JobIdPath) -> ProposalJob:
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="không tìm thấy job")
    return job


@router.post("/concept-pairs/jobs/{job_id}/cancel", response_model=ProposalJob)
def cancel_proposal_job(jobs: ProposalJobsDep, job_id: JobIdPath) -> ProposalJob:
    job = jobs.cancel(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="không tìm thấy job")
    return job
