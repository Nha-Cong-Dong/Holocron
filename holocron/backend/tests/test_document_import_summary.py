"""Màn kết quả nhập (T1e; SPECIFICATION §5.7 `DocumentImport`, quy tắc `proposal_job`, §6.5 "Màn kết quả nhập").

Kiểm: số đếm theo lý do xem lại và theo vai trò bỏ mềm khớp định nghĩa (trang nhiều lý do, trang bỏ mềm, trang đã
xác nhận); `pages_failed`; validator của model; mọi endpoint trả `DocumentImport` điền ĐÚNG MỘT trong `proposal_job`
và `proposal_job_absent_reason`; từng lý do vắng job và thứ tự xét; job vẫn trả khi LLM bị gỡ sau đó; job mới nhất
theo `created_at`, hoà thì job tạo sau thắng.

LLM là stub, KHÔNG gọi mạng; hàng đợi đọc chạy đồng bộ; văn bản là mẫu tự viết.
"""

import hashlib
import http.client
import socket
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from holocron_core.domain.documents import (
    DocumentImport,
    DroppedRoleCount,
    PageExtraction,
    PageSummary,
    ReviewReasonCount,
    count_dropped,
    count_failed,
    dropped_role_counts,
    review_reason_counts,
)
from holocron_core.domain.repositories import ImportedDocument
from holocron_core.domain.schemas import DocumentMeta, SourceKind
from holocron_core.domain.vault import ProposalJob
from holocron_core.infrastructure.llm_config import LlmTaskLimits
from holocron_core.routers.api import app
from holocron_core.services.concept_proposal_job import DocumentProposals, ProposalJobRuntime
from holocron_core.services.document_import import pending_page, proposal_status
from tests.document_import_support import ok_page, pdf_bytes
from tests.proposal_support import EMPTY_REPLY, StubClient
from tests.test_document_confirm_auto_proposal import EDITOR, SUBJECT, Setup, _make_setup
from tests.vault_support import NOW, open_store

WAIT_S = 10.0


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("test không được gọi mạng")

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(http.client.HTTPConnection, "connect", refuse)


@pytest.fixture(params=["memory", "sqlite"])
def setup(request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Setup]:
    for store in open_store(str(request.param), tmp_path, monkeypatch):
        made = _make_setup(store, tmp_path / "files")
        try:
            yield made
        finally:
            made.jobs.close()
            app.dependency_overrides.clear()


def _summary(number: int, **fields: Any) -> PageSummary:
    values: dict[str, Any] = {
        "page_number": number,
        "status": "ok",
        "tier_used": "text_layer",
        "review_reasons": [],
        "confirmed": False,
    }
    values.update(fields)
    return PageSummary.model_validate(values)


def _needs_review(document_id: str, number: int, *reasons: str) -> PageExtraction:
    return PageExtraction.model_validate(
        {**ok_page(document_id, number).model_dump(), "status": "needs_review", "review_reasons": list(reasons)}
    )


def _doc_id(content: bytes) -> str:
    return "doc_" + hashlib.sha256(content).hexdigest()[:16]


def _post(setup: Setup, tag: str, **form: str) -> tuple[str, dict[str, Any]]:
    content = pdf_bytes(tag)
    response = setup.env.post(content, **form)
    assert response.status_code in (200, 202), response.text
    body: dict[str, Any] = response.json()
    return _doc_id(content), body


def _get(setup: Setup, doc_id: str) -> dict[str, Any]:
    response = setup.env.client.get(f"/v1/documents/{doc_id}")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _confirm(setup: Setup, doc_id: str) -> dict[str, Any]:
    response = setup.env.client.post(f"/v1/documents/{doc_id}/confirm", json={"editor_id": EDITOR})
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _exactly_one(body: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    job, reason = body["proposal_job"], body["proposal_job_absent_reason"]
    assert (job is None) != (reason is None), "đúng một trong hai field"
    if job is not None:
        assert job["document_id"] == body["meta"]["doc_id"]
    return job, reason


# --------------------------------------------------------------------------- #
# Số đếm (định nghĩa §5.7)


def test_review_reason_counts_follow_the_definition() -> None:
    pages = [
        _summary(1, status="needs_review", review_reasons=["LOW_OCR_SCORE", "LOW_LINE_COVERAGE"]),
        _summary(2, status="needs_review", review_reasons=["LOW_LINE_COVERAGE"]),
        _summary(3, status="needs_review", review_reasons=["LOW_OCR_SCORE"], confirmed=True),  # đã xác nhận
        _summary(4, status="needs_review", review_reasons=["TOKEN_MISMATCH"], dropped=True, page_role="build_step"),
        _summary(5),
        _summary(6, status="failed", tier_used=None),
        _summary(7, status="needs_review", review_reasons=["HIGH_PAGE_CER", "LOW_OCR_SCORE"]),
    ]
    assert review_reason_counts(pages) == [
        ReviewReasonCount(reason="LOW_OCR_SCORE", count=2, pages=[1, 7]),
        ReviewReasonCount(reason="LOW_LINE_COVERAGE", count=2, pages=[1, 2]),
        ReviewReasonCount(reason="HIGH_PAGE_CER", count=1, pages=[7]),
    ], "trang nhiều lý do đếm ở mỗi lý do; trang đã xác nhận và trang bỏ mềm không đếm; theo thứ tự khai báo"
    assert count_failed(pages) == 1


def test_dropped_role_counts_follow_the_definition() -> None:
    pages = [
        _summary(1, page_role="cover_or_course_nav", dropped=True),
        _summary(2, page_role="build_step", dropped=True),
        _summary(3, page_role="chapter_nav"),  # vai trò bỏ được nhưng không bỏ
        _summary(
            4, page_role="cover_or_course_nav", dropped=True, status="needs_review", review_reasons=["TEXT_OCCLUDED"]
        ),
        _summary(5, page_role="chapter_nav", dropped=True),
        _summary(6),
    ]
    counts = dropped_role_counts(pages)
    assert counts == [
        DroppedRoleCount(reason="cover_or_course_nav", count=2, pages=[1, 4]),
        DroppedRoleCount(reason="chapter_nav", count=1, pages=[5]),
        DroppedRoleCount(reason="build_step", count=1, pages=[2]),
    ]
    assert sum(item.count for item in counts) == count_dropped(pages)
    assert dropped_role_counts([_summary(1)]) == [] and review_reason_counts([_summary(1)]) == []


@pytest.mark.parametrize(
    "bad",
    [
        {"reason": "LOW_OCR_SCORE", "count": 2, "pages": [1]},
        {"reason": "LOW_OCR_SCORE", "count": 2, "pages": [2, 1]},
        {"reason": "LOW_OCR_SCORE", "count": 2, "pages": [1, 1]},
        {"reason": "LOW_OCR_SCORE", "count": 0, "pages": []},
        {"reason": "NOT_A_REASON", "count": 1, "pages": [1]},
    ],
)
def test_review_reason_count_validator(bad: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ReviewReasonCount.model_validate(bad)


@pytest.mark.parametrize("reason", ["content", "parallel_visual_series", "exercise"])
def test_dropped_role_count_only_takes_droppable_roles(reason: str) -> None:
    with pytest.raises(ValidationError):
        DroppedRoleCount.model_validate({"reason": reason, "count": 1, "pages": [1]})
    with pytest.raises(ValidationError):
        DroppedRoleCount.model_validate({"reason": "build_step", "count": 2, "pages": [3]})


def _job(document_id: str | None, **fields: Any) -> ProposalJob:
    values: dict[str, Any] = {
        "job_id": "pjob_0123456789abcdef",
        "subject_code": SUBJECT,
        "trigger": "document_confirmed",
        "document_id": document_id,
        "status": "queued",
        "chunk_count": 1,
        "batch_size": 5,
        "batches_total": 1,
        "batches_done": 0,
        "batches_failed": 0,
        "proposed_count": 0,
        "pre_selected_count": 0,
        "rejected_count": 0,
        "exercise_chunks": 0,
        "created_at": NOW,
    }
    values.update(fields)
    return ProposalJob.model_validate(values)


def test_document_import_validator_takes_at_most_one_and_the_matching_job(setup: Setup) -> None:
    doc_id, body = _post(setup, "validator")
    base = {key: value for key, value in body.items() if not key.startswith("proposal_job")}
    job = _job(doc_id).model_dump(mode="json")
    DocumentImport.model_validate({**base, "proposal_job": job})
    DocumentImport.model_validate({**base, "proposal_job_absent_reason": "not_confirmed"})
    with pytest.raises(ValidationError, match="cùng khác null"):
        DocumentImport.model_validate({**base, "proposal_job": job, "proposal_job_absent_reason": "not_confirmed"})
    with pytest.raises(ValidationError, match="document_id"):
        DocumentImport.model_validate({**base, "proposal_job": _job("doc_ffffffffffffffff").model_dump(mode="json")})
    with pytest.raises(ValidationError):
        DocumentImport.model_validate({**base, "proposal_job_absent_reason": "unknown"})


def test_response_counts_are_computed_by_the_backend(setup: Setup) -> None:
    setup.env.reader.page_count = 4
    setup.env.reader.failed_pages = frozenset({4})
    setup.env.reader.custom_pages = {
        1: lambda d: _needs_review(d, 1, "LOW_OCR_SCORE", "LOW_LINE_COVERAGE"),
        2: lambda d: _needs_review(d, 2, "LOW_LINE_COVERAGE"),
    }
    doc_id, _ = _post(setup, "dem")
    body = _get(setup, doc_id)
    assert body["pages_failed"] == 1 and body["pages_needs_review"] == 2
    assert body["review_reason_counts"] == [
        {"reason": "LOW_OCR_SCORE", "count": 1, "pages": [1]},
        {"reason": "LOW_LINE_COVERAGE", "count": 2, "pages": [1, 2]},
    ]
    assert body["dropped_role_counts"] == [] and body["pages_dropped"] == 0
    page = setup.env.store.get_page(doc_id, 2, PageExtraction)
    assert page is not None
    confirmed = setup.env.client.post(
        f"/v1/documents/{doc_id}/pages/2/confirm", json={"editor_id": EDITOR, "expected_version": page.version}
    )
    assert confirmed.status_code == 200
    after = _get(setup, doc_id)
    assert after["review_reason_counts"] == [
        {"reason": "LOW_OCR_SCORE", "count": 1, "pages": [1]},
        {"reason": "LOW_LINE_COVERAGE", "count": 1, "pages": [1]},
    ], "trang đã xác nhận không còn được đếm"


def test_dropped_pages_are_counted_by_role_in_the_response(setup: Setup) -> None:
    doc_id, _ = _post(setup, "bo-mem")
    page = setup.env.store.get_page(doc_id, 1, PageExtraction)
    assert page is not None
    dropped = PageExtraction.model_validate(
        {
            **page.model_dump(),
            "page_role": "chapter_nav",
            "role_evidence": {"rule": "chapter_nav", "related_pages": [{"document_id": doc_id, "page_number": 2}]},
            "dropped": True,
            "version": page.version + 1,
        }
    )
    setup.env.store.update_page(
        doc_id, 1, dropped, status=dropped.status, confirmed=False, expected_version=page.version
    )
    body = _get(setup, doc_id)
    assert body["pages_dropped"] == 1
    assert body["dropped_role_counts"] == [{"reason": "chapter_nav", "count": 1, "pages": [1]}]


# --------------------------------------------------------------------------- #
# Đúng một trong hai field ở mọi endpoint trả `DocumentImport`


def test_every_document_import_endpoint_fills_exactly_one_field(setup: Setup) -> None:
    doc_id, created = _post(setup, "moi-endpoint")
    assert _exactly_one(created) == (None, "not_confirmed"), "POST /v1/documents 202"
    assert _exactly_one(_get(setup, doc_id)) == (None, "not_confirmed"), "GET"
    patched = setup.env.client.patch(
        f"/v1/documents/{doc_id}/chapter", json={"editor_id": EDITOR, "chapter_title": "Chương mới"}
    )
    assert patched.status_code == 200 and _exactly_one(patched.json()) == (None, "not_confirmed"), "PATCH …/chapter"
    confirmed = _confirm(setup, doc_id)
    job, _ = _exactly_one(confirmed)
    assert job is not None, "POST …/confirm"
    _, duplicate = _post(setup, "moi-endpoint")
    duplicate_job, _ = _exactly_one(duplicate)
    assert duplicate_job is not None and duplicate_job["job_id"] == job["job_id"], (
        "POST trùng (200, `_existing_outcome`)"
    )
    title = setup.env.client.patch(
        f"/v1/documents/{doc_id}/chapter", json={"editor_id": EDITOR, "chapter_title": "Tên khác"}
    )
    assert title.status_code == 200 and _exactly_one(title.json())[0] is not None
    assert _exactly_one(_get(setup, doc_id))[0] is not None


# --------------------------------------------------------------------------- #
# Lý do vắng job và thứ tự xét


def test_not_confirmed_comes_first_even_for_a_past_exam_without_llm(setup: Setup) -> None:
    setup.configure(False)
    doc_id, body = _post(setup, "de-chua-xac-nhan", source_kind="past_exam")
    assert _exactly_one(body) == (None, "not_confirmed")
    assert _exactly_one(_get(setup, doc_id)) == (None, "not_confirmed")


def test_past_exam_comes_before_no_chunks_and_llm(setup: Setup) -> None:
    setup.configure(False)
    doc_id, _ = _post(setup, "de-cu-thu-tu", source_kind="past_exam")
    assert _exactly_one(_confirm(setup, doc_id)) == (None, "past_exam")


def _confirmed_without_chunks(setup: Setup) -> str:
    doc_id = "doc_" + "e" * 16
    setup.env.store.create_imported_document(
        ImportedDocument(
            meta=DocumentMeta(
                doc_id=doc_id,
                subject_code=SUBJECT,
                title="Tài liệu không có chunk",
                source_kind=SourceKind.TEXTBOOK,
                sha256="e" * 64,
                page_count=1,
                has_text_layer=True,
            ),
            filename="khong_chunk.pdf",
            size_bytes=10,
            chapter_code="ch1",
            status="confirmed",
        ),
        [pending_page(doc_id, 1)],
    )
    return doc_id


def test_no_chunks_comes_before_llm_unavailable(setup: Setup) -> None:
    doc_id = _confirmed_without_chunks(setup)
    setup.configure(False)
    assert _exactly_one(_get(setup, doc_id)) == (None, "no_chunks")
    setup.configure(True)
    assert _exactly_one(_get(setup, doc_id)) == (None, "no_chunks")


def test_llm_unavailable_then_not_retained_once_llm_is_configured(setup: Setup) -> None:
    setup.configure(False)
    doc_id, _ = _post(setup, "llm-sau")
    assert _exactly_one(_confirm(setup, doc_id)) == (None, "llm_unavailable")
    assert _exactly_one(_get(setup, doc_id)) == (None, "llm_unavailable")
    setup.configure(True)
    assert _exactly_one(_get(setup, doc_id)) == (None, "not_retained")


def test_job_is_still_returned_after_llm_is_removed(setup: Setup) -> None:
    doc_id, _ = _post(setup, "go-llm")
    job, _ = _exactly_one(_confirm(setup, doc_id))
    assert job is not None
    setup.configure(False)
    assert setup.jobs.wait_idle(WAIT_S)
    after, reason = _exactly_one(_get(setup, doc_id))
    assert reason is None and after is not None and after["job_id"] == job["job_id"]
    assert after["status"] in ("succeeded", "partial", "failed"), "tiến độ do backend đếm, đọc lại mỗi lần hỏi"


def test_a_manual_job_for_the_document_is_returned_too(setup: Setup) -> None:
    setup.configure(False)
    doc_id, _ = _post(setup, "job-tay")
    _confirm(setup, doc_id)
    chunk_ids = [chunk.chunk_id for chunk in setup.env.store.list_chunks(SUBJECT) if chunk.doc_id == doc_id]
    manual = setup.jobs.submit(
        subject_code=SUBJECT,
        chunk_ids=chunk_ids,
        document_id=doc_id,
        store=setup.env.store,
        client_factory=lambda: StubClient(EMPTY_REPLY),
        limits=LlmTaskLimits(),
        now=NOW,
    )
    job, _ = _exactly_one(_get(setup, doc_id))
    assert job is not None and (job["job_id"], job["trigger"]) == (manual.job_id, "manual")


# --------------------------------------------------------------------------- #
# Job mới nhất theo tài liệu (runtime)


def test_latest_job_uses_created_at_and_later_creation_wins_ties(setup: Setup) -> None:
    doc_id, _ = _post(setup, "moi-nhat")
    setup.configure(False)
    _confirm(setup, doc_id)
    chunk_ids = [chunk.chunk_id for chunk in setup.env.store.list_chunks(SUBJECT) if chunk.doc_id == doc_id]
    runtime = ProposalJobRuntime(clock=lambda: NOW)
    try:

        def submit(offset_minutes: int) -> ProposalJob:
            return runtime.submit(
                subject_code=SUBJECT,
                chunk_ids=chunk_ids,
                document_id=doc_id,
                store=setup.env.store,
                client_factory=lambda: StubClient(EMPTY_REPLY),
                limits=LlmTaskLimits(),
                now=NOW + timedelta(minutes=offset_minutes),
            )

        newest = submit(5)
        submit(1)  # tạo sau nhưng cũ hơn
        latest = runtime.latest_for_document(doc_id)
        assert latest is not None and latest.job_id == newest.job_id
        tie = submit(5)  # hoà `created_at`: job tạo sau thắng
        latest = runtime.latest_for_document(doc_id)
        assert latest is not None and latest.job_id == tie.job_id
        assert runtime.latest_for_document("doc_0000000000000000") is None
        document = setup.env.store.get_imported_document(doc_id)
        assert document is not None
        proposals = DocumentProposals(store=setup.env.store, jobs=runtime, llm_available=False)
        job, reason = proposal_status(document, setup.env.store, proposals)
        assert reason is None and job is not None and job.job_id == tie.job_id
    finally:
        runtime.close()


def test_service_defaults_without_proposals_or_vault() -> None:
    """Gọi thẳng service (script) không có `proposals`/`vault`: không job, LLM coi như không khả dụng."""
    doc = ImportedDocument(
        meta=DocumentMeta(
            doc_id="doc_" + "a" * 16,
            subject_code=SUBJECT,
            title="Tài liệu",
            source_kind=SourceKind.TEXTBOOK,
            sha256="a" * 64,
            page_count=1,
            has_text_layer=True,
        ),
        filename="a.pdf",
        size_bytes=10,
        chapter_code="ch1",
        status="confirmed",
    )
    assert proposal_status(doc, None, None) == (None, "no_chunks")
    assert proposal_status(doc.model_copy(update={"status": "review"}), None, None) == (None, "not_confirmed")
