"""Môn học và `exam_format` (SPECIFICATION §5.14 `Subject`, §6.8; duyệt 2026-10-28 câu 1).

Bốn route chạy offline, đúng bảng §6.8: liệt kê, đọc, tạo (`201` + `Location`), sửa có optimistic locking
(`expected_version` lệch ⇒ 409). Môn còn được tạo ngầm khi `POST /v1/documents` hay `POST /v1/blueprints` gặp
`subject_code` chưa có (`display_name = subject_code`, `exam_format = mixed`); việc đó nằm trong repository của bản
ghi chính, không ở đây. Không có `DELETE` trong MVP.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Response
from pydantic import Field, model_validator

from ..domain.documents import MAX_EDITOR_ID_CHARS
from ..domain.repositories import RecordNotFoundError, SubjectExistsError, VersionConflictError
from ..domain.schemas import (
    SUBJECT_DISPLAY_NAME_MAX_CHARS,
    ExamFormat,
    StrictModel,
    Subject,
)
from ..domain.vault import SUBJECT_CODE_PATTERN
from .deps import ClockDep, StoreDep

router = APIRouter()

SubjectCodePath = Annotated[str, Path(pattern=SUBJECT_CODE_PATTERN)]
DisplayName = Annotated[str, Field(min_length=1, max_length=SUBJECT_DISPLAY_NAME_MAX_CHARS)]
SUBJECT_NOT_FOUND = "không tìm thấy môn học"
SUBJECT_EXISTS = "môn học đã có"


class SubjectCreate(StrictModel):
    """Body của `POST /v1/subjects` (§6.8). Thiếu `display_name` ⇒ dùng `subject_code`, như khi tạo ngầm (§5.14)."""

    subject_code: str = Field(pattern=SUBJECT_CODE_PATTERN)
    display_name: DisplayName | None = None
    exam_format: ExamFormat = ExamFormat.MIXED


class SubjectPatch(StrictModel):
    """Body của `PATCH /v1/subjects/{subject_code}` (§6.8): ít nhất một trong hai field sửa."""

    editor_id: str = Field(min_length=1, max_length=MAX_EDITOR_ID_CHARS)
    expected_version: int = Field(ge=1)
    display_name: DisplayName | None = None
    exam_format: ExamFormat | None = None

    @model_validator(mode="after")
    def _has_change(self) -> SubjectPatch:
        if self.display_name is None and self.exam_format is None:
            raise ValueError("cần ít nhất một trong display_name, exam_format")
        return self


@router.get("/v1/subjects", response_model=list[Subject])
def list_subjects(store: StoreDep) -> list[Subject]:
    return store.list_subjects()


@router.get("/v1/subjects/{subject_code}", response_model=Subject)
def get_subject(store: StoreDep, subject_code: SubjectCodePath) -> Subject:
    subject = store.get_subject(subject_code)
    if subject is None:
        raise HTTPException(status_code=404, detail=SUBJECT_NOT_FOUND)
    return subject


@router.post("/v1/subjects", response_model=Subject, status_code=201)
def create_subject(store: StoreDep, clock: ClockDep, body: SubjectCreate, response: Response) -> Subject:
    now = clock()
    subject = Subject(
        subject_code=body.subject_code,
        display_name=body.display_name if body.display_name is not None else body.subject_code,
        exam_format=body.exam_format,
        created_at=now,
        updated_at=now,
    )
    try:
        stored = store.create_subject(subject)
    except SubjectExistsError as exc:
        raise HTTPException(status_code=409, detail=SUBJECT_EXISTS) from exc
    response.headers["Location"] = f"/v1/subjects/{stored.subject_code}"
    return stored


@router.patch("/v1/subjects/{subject_code}", response_model=Subject)
def update_subject(store: StoreDep, clock: ClockDep, subject_code: SubjectCodePath, body: SubjectPatch) -> Subject:
    try:
        return store.update_subject(
            subject_code,
            body.expected_version,
            display_name=body.display_name,
            exam_format=body.exam_format,
            now=clock(),
        )
    except RecordNotFoundError as exc:
        raise HTTPException(status_code=404, detail=SUBJECT_NOT_FOUND) from exc
    except VersionConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
