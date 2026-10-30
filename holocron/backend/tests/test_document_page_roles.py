"""GĐ0 0D ở service và API (spec §5.7 "Khử nhiễu trang và vai trò trang", §6.5 `POST …/restore`).

Trang dựng sẵn qua `FakePdfReader` (không PDF thật, không eval_data), hàng đợi đọc đồng bộ. Số đếm tóm tắt
(`DocumentImport`, `DocumentSummary`) kiểm trên kho SQLite: kho in-memory chưa đọc `page_role`/`dropped` vào tóm tắt
trang (ngoài phạm vi nhánh này, xem báo cáo). Trang, dòng, bỏ mềm và hoàn tác kiểm trên mọi loại kho.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from holocron_core.domain.documents import PageExtraction, PageSummary
from holocron_core.domain.repositories import AppStore, VersionConflictError
from holocron_core.infrastructure.document_readers.config import PdfReaderConfig
from holocron_core.routers.api import app
from holocron_core.services.document_import import (
    DROPPED_CONFIRM_DETAIL,
    DROPPED_EDIT_DETAIL,
    SETTLE_ATTEMPTS,
    SETTLE_PENDING_DETAIL,
    SynchronousImportQueue,
    assign_page_roles,
)
from holocron_core.services.page_roles import RoleDecision, apply_decision
from tests.document_import_support import FakePdfReader, ImportEnv, install, pdf_bytes
from tests.vault_support import STORE_KINDS, open_store

EDITOR = "local_user"
NAV = ["Muc luc chuong", "Kien truc tap lenh", "Che do dia chi", "Ngat va ngoai le", "Bo nho dem va bus"]
COVER = ["Truong dai hoc mau", "Khoa dien tu vien thong", "Mon vi xu ly", "Giang vien mau", "Hoc ky mau nam"]
NAV_PAGES = (2, 6, 11)
PAGE_COUNT = 12


def _page(document_id: str, number: int, lines: list[str], *, review: bool = False) -> PageExtraction:
    return PageExtraction.model_validate(
        {
            "document_id": document_id,
            "page_number": number,
            "status": "needs_review" if review else "ok",
            "tier_used": "text_layer",
            "text_score": 1.0,
            "lines": [
                {
                    "line_index": index,
                    "text": text,
                    "bbox": [0.1, 0.2 + 0.05 * index, 0.9, 0.24 + 0.05 * index],
                    "conf": 1.0,
                    "engine": "text_layer",
                }
                for index, text in enumerate(lines)
            ],
            "cross_check": {"status": "not_applicable", "secondary_engine": None},
            "review_reasons": ["LOW_TEXT_CONTRAST"] if review else [],
        }
    )


def _content(document_id: str, number: int) -> list[str]:
    seed = hashlib.sha256(f"{document_id}:{number}".encode()).hexdigest()
    return [" ".join(f"t{seed[k : k + 6]}{i}" for k in range(0, 18, 6)) for i in range(4)]


def _builder(
    lines: Callable[[str, int], list[str]], number: int, *, review: bool = False
) -> Callable[[str], PageExtraction]:
    return lambda document_id: _page(document_id, number, lines(document_id, number), review=review)


def _reader(*, cover: bool = False, review_nav: bool = False) -> FakePdfReader:
    pages: dict[int, Callable[[str], PageExtraction]] = {
        number: _builder(_content, number) for number in range(1, PAGE_COUNT + 1)
    }
    for number in NAV_PAGES:
        pages[number] = _builder(lambda _doc, _n: NAV, number, review=review_nav)
    if cover:
        pages[1] = _builder(lambda _doc, _n: COVER, 1)
    return FakePdfReader(page_count=PAGE_COUNT, custom_pages=pages)


def _env(kind: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reader: FakePdfReader) -> Iterator[ImportEnv]:
    for store in open_store(kind, tmp_path, monkeypatch):
        yield install(store, tmp_path / "files", SynchronousImportQueue(), reader)
        app.dependency_overrides.clear()


@pytest.fixture(params=STORE_KINDS)
def env(request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ImportEnv]:
    yield from _env(str(request.param), tmp_path, monkeypatch, _reader(review_nav=True))


@pytest.fixture
def sqlite_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ImportEnv]:
    yield from _env("sqlite", tmp_path, monkeypatch, _reader(review_nav=True))


def _doc_id(content: bytes) -> str:
    return "doc_" + hashlib.sha256(content).hexdigest()[:16]


def _import(env: ImportEnv, tag: str = "a", **form: str) -> str:
    content = pdf_bytes(tag)
    response = env.post(content, **form)
    assert response.status_code == 202, response.text
    return _doc_id(content)


def _page_json(env: ImportEnv, document_id: str, number: int) -> dict[str, Any]:
    response = env.client.get(f"/v1/documents/{document_id}/pages/{number}")
    assert response.status_code == 200
    data: dict[str, Any] = response.json()
    return data


def _restore(env: ImportEnv, document_id: str, number: int, version: int) -> Any:
    return env.client.post(
        f"/v1/documents/{document_id}/pages/{number}/restore", json={"editor_id": EDITOR, "expected_version": version}
    )


# --------------------------------------------------------------------------- #
# Bỏ mềm khi đọc xong, không vào chunk, không chặn chốt
# --------------------------------------------------------------------------- #


def test_dropped_page_never_chunked(env: ImportEnv) -> None:
    doc = _import(env)
    for number in NAV_PAGES:
        page = _page_json(env, doc, number)
        assert (page["page_role"], page["dropped"]) == ("chapter_nav", True)
        assert page["role_evidence"]["rule"] == "chapter_nav"
    confirmed = env.client.post(f"/v1/documents/{doc}/confirm", json={"editor_id": EDITOR})
    assert confirmed.status_code == 200
    # Trang nội dung `ok` được xác nhận theo lô; trang bỏ mềm (needs_review) không chặn bước chốt.
    assert confirmed.json()["status"] == "confirmed"
    for number in NAV_PAGES:
        assert not _page_json(env, doc, number)["confirmed"]
    chunks = env.store.list_chunks("ET4010")
    assert chunks
    text = " ".join(chunk.raw_text for chunk in chunks)
    assert all(line not in text for line in NAV)


def test_dropped_pages_not_counted_as_needs_review(sqlite_env: ImportEnv) -> None:
    doc = _import(sqlite_env)
    view = sqlite_env.client.get(f"/v1/documents/{doc}").json()
    assert (view["pages_dropped"], view["pages_kept"], view["pages_needs_review"]) == (3, 9, 0)
    assert [p["dropped"] for p in view["pages"] if p["page_number"] in NAV_PAGES] == [True] * 3
    summary = sqlite_env.client.get("/v1/documents").json()[0]
    assert (summary["pages_dropped"], summary["pages_needs_review"]) == (3, 0)
    restored = _restore(sqlite_env, doc, 2, _page_json(sqlite_env, doc, 2)["version"])
    assert restored.status_code == 200
    view = sqlite_env.client.get(f"/v1/documents/{doc}").json()
    assert (view["pages_dropped"], view["pages_kept"], view["pages_needs_review"]) == (2, 10, 1)


def test_sqlite_summary_reads_old_page_json_without_new_fields(sqlite_env: ImportEnv) -> None:
    doc = _import(sqlite_env)
    summaries = sqlite_env.store.list_page_summaries(doc)
    assert summaries == [PageSummary.from_page(page) for page in sqlite_env.store.list_pages(doc, PageExtraction)]


# --------------------------------------------------------------------------- #
# POST …/restore
# --------------------------------------------------------------------------- #


def test_restore_is_one_call_and_idempotent(env: ImportEnv) -> None:
    doc = _import(env)
    before = _page_json(env, doc, 6)
    response = _restore(env, doc, 6, before["version"])
    assert response.status_code == 200
    after = response.json()
    assert (after["dropped"], after["restored"], after["version"]) == (False, True, before["version"] + 1)
    assert (after["page_role"], after["role_evidence"]) == (before["page_role"], before["role_evidence"])
    assert (after["review_reasons"], after["status"]) == (before["review_reasons"], before["status"])
    assert env.store.list_page_audits(doc, 6) == []
    # Gọi lại (kể cả version cũ) trên trang không bỏ mềm: 200, không đổi gì, không kiểm version.
    again = _restore(env, doc, 6, before["version"])
    assert again.status_code == 200 and again.json() == after
    plain = _page_json(env, doc, 3)
    assert _restore(env, doc, 3, 999).json() == plain


def test_restored_page_is_never_dropped_again(env: ImportEnv) -> None:
    doc = _import(env)
    _restore(env, doc, 2, _page_json(env, doc, 2)["version"])
    assign_page_roles(env.store, doc, PdfReaderConfig(), env.store)
    page = _page_json(env, doc, 2)
    assert (page["dropped"], page["restored"]) == (False, True)
    assert all(_page_json(env, doc, number)["dropped"] for number in (6, 11))


def test_restored_page_must_be_confirmed_before_vault(env: ImportEnv) -> None:
    doc = _import(env)
    _restore(env, doc, 2, _page_json(env, doc, 2)["version"])
    batch = env.client.post(f"/v1/documents/{doc}/confirm", json={"editor_id": EDITOR}).json()
    assert batch["status"] == "review"  # trang 2 (needs_review) phải xác nhận từng trang
    version = _page_json(env, doc, 2)["version"]
    confirm = env.client.post(
        f"/v1/documents/{doc}/pages/2/confirm", json={"editor_id": EDITOR, "expected_version": version}
    )
    assert confirm.status_code == 200
    assert env.client.get(f"/v1/documents/{doc}").json()["status"] == "confirmed"
    assert any(NAV[1] in chunk.raw_text for chunk in env.store.list_chunks("ET4010"))


def test_restore_errors(env: ImportEnv) -> None:
    doc = _import(env)
    version = _page_json(env, doc, 6)["version"]
    assert _restore(env, "doc_0000000000000000", 6, 1).status_code == 404
    assert _restore(env, doc, PAGE_COUNT + 1, 1).status_code == 404
    conflict = _restore(env, doc, 6, version + 5)
    assert conflict.status_code == 409
    assert _page_json(env, doc, 6)["dropped"]
    for body in ({"editor_id": EDITOR}, {"editor_id": "", "expected_version": 1}, {"expected_version": 0}):
        assert env.client.post(f"/v1/documents/{doc}/pages/6/restore", json=body).status_code == 422


def test_restore_rejected_when_document_text_is_closed(env: ImportEnv) -> None:
    doc = _import(env)
    env.client.post(f"/v1/documents/{doc}/confirm", json={"editor_id": EDITOR})
    assert env.client.get(f"/v1/documents/{doc}").json()["status"] == "confirmed"
    response = _restore(env, doc, 6, _page_json(env, doc, 6)["version"])
    assert response.status_code == 409
    assert _page_json(env, doc, 6)["dropped"]


def test_restore_rejected_when_vault_has_chunks(env: ImportEnv, monkeypatch: pytest.MonkeyPatch) -> None:
    doc = _import(env)
    monkeypatch.setattr(type(env.store), "has_document_chunks", lambda self, document_id: True)
    response = _restore(env, doc, 6, _page_json(env, doc, 6)["version"])
    assert response.status_code == 409 and response.json()["detail"] == SETTLE_PENDING_DETAIL


def test_dropped_page_cannot_be_edited_or_confirmed(env: ImportEnv) -> None:
    doc = _import(env)
    version = _page_json(env, doc, 6)["version"]
    edit = env.client.patch(
        f"/v1/documents/{doc}/pages/6/lines",
        json={"editor_id": EDITOR, "expected_version": version, "edits": [{"line_index": 0, "text": "x"}]},
    )
    assert edit.status_code == 409 and edit.json()["detail"] == DROPPED_EDIT_DETAIL
    confirm = env.client.post(
        f"/v1/documents/{doc}/pages/6/confirm", json={"editor_id": EDITOR, "expected_version": version}
    )
    assert confirm.status_code == 409 and confirm.json()["detail"] == DROPPED_CONFIRM_DETAIL


# --------------------------------------------------------------------------- #
# 0D ở service: nhánh lỗi và nhánh không đổi
# --------------------------------------------------------------------------- #


class _ConflictingStore:
    """Bọc kho: `update_page` của trang `page_number` ném `VersionConflictError` `failures` lần đầu."""

    def __init__(self, store: AppStore, page_number: int, failures: int) -> None:
        self._store = store
        self._page_number = page_number
        self.failures = failures
        self.attempts = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._store, name)

    def update_page(self, document_id: str, page_number: int, page: Any, **kwargs: Any) -> int:
        if page_number == self._page_number:
            self.attempts += 1
            if self.attempts <= self.failures:
                raise VersionConflictError("trang", f"{document_id}#{page_number}", 1, 2)
        result: int = self._store.update_page(document_id, page_number, page, **kwargs)
        return result


def _reset_roles(store: AppStore, doc: str) -> None:
    """Đưa mọi trang về `content` (như vừa đọc xong, trước 0D)."""
    for page in store.list_pages(doc, PageExtraction):
        if page.dropped:
            store.update_page(
                doc,
                page.page_number,
                apply_decision(page, RoleDecision()) or page,
                status=page.status,
                confirmed=False,
                expected_version=page.version,
            )


@pytest.mark.parametrize("failures", [1, SETTLE_ATTEMPTS - 1])
def test_role_write_retries_on_version_conflict(env: ImportEnv, failures: int) -> None:
    doc = _import(env)
    _reset_roles(env.store, doc)
    wrapped = _ConflictingStore(env.store, 6, failures)
    assign_page_roles(wrapped, doc, PdfReaderConfig(), env.store)
    assert wrapped.attempts == failures + 1
    assert _page_json(env, doc, 6)["dropped"]


def test_role_write_gives_up_after_settle_attempts(env: ImportEnv, caplog: pytest.LogCaptureFixture) -> None:
    doc = _import(env)
    _reset_roles(env.store, doc)
    wrapped = _ConflictingStore(env.store, 6, SETTLE_ATTEMPTS + 5)
    with caplog.at_level(logging.WARNING):
        assign_page_roles(wrapped, doc, PdfReaderConfig(), env.store)
    assert wrapped.attempts == SETTLE_ATTEMPTS
    page = _page_json(env, doc, 6)
    assert (page["dropped"], page["page_role"]) == (False, "content")
    assert _page_json(env, doc, 2)["dropped"]
    assert any("vai trò trang 6" in record.getMessage() for record in caplog.records)


def test_confirmed_document_or_chunks_are_never_dropped(env: ImportEnv, monkeypatch: pytest.MonkeyPatch) -> None:
    doc = _import(env)
    _reset_roles(env.store, doc)
    env.client.post(f"/v1/documents/{doc}/confirm", json={"editor_id": EDITOR})
    document = env.store.get_imported_document(doc)
    assert document is not None and document.status != "confirmed"  # trang nav needs_review chưa xác nhận
    monkeypatch.setattr(type(env.store), "has_document_chunks", lambda self, document_id: True)
    assign_page_roles(env.store, doc, PdfReaderConfig(), env.store)
    assert not any(page.dropped for page in env.store.list_pages(doc, PageExtraction))
    monkeypatch.undo()
    stored = env.store.get_imported_document(doc)
    assert stored is not None
    env.store.update_imported_document(stored.model_copy(update={"status": "confirmed"}), stored.version)
    assign_page_roles(env.store, doc, PdfReaderConfig(), None)
    assert not any(page.dropped for page in env.store.list_pages(doc, PageExtraction))


def test_confirmed_page_keeps_its_role(env: ImportEnv) -> None:
    doc = _import(env)
    _reset_roles(env.store, doc)
    page = env.store.get_page(doc, 6, PageExtraction)
    assert page is not None
    env.store.update_page(
        doc,
        6,
        page.model_copy(update={"confirmed": True, "version": page.version + 1}),
        status=page.status,
        confirmed=True,
        expected_version=page.version,
    )
    assign_page_roles(env.store, doc, PdfReaderConfig(), env.store)
    assert not _page_json(env, doc, 6)["dropped"]
    assert _page_json(env, doc, 2)["dropped"] and _page_json(env, doc, 11)["dropped"]


# --------------------------------------------------------------------------- #
# Bìa so với tài liệu khác cùng môn và đánh giá lại tài liệu nhập trước
# --------------------------------------------------------------------------- #


@pytest.fixture(params=STORE_KINDS)
def cover_env(request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ImportEnv]:
    yield from _env(str(request.param), tmp_path, monkeypatch, _reader(cover=True))


def test_cover_of_both_documents_is_dropped(cover_env: ImportEnv) -> None:
    first = _import(cover_env, "a", chapter_code="ch2")
    assert not _page_json(cover_env, first, 1)["dropped"]  # chưa có tài liệu khác để so
    second = _import(cover_env, "b", chapter_code="ch3")
    later = _page_json(cover_env, second, 1)
    assert (later["page_role"], later["dropped"]) == ("cover_or_course_nav", True)
    assert later["role_evidence"]["related_pages"] == [{"document_id": first, "page_number": 1}]
    earlier = _page_json(cover_env, first, 1)
    assert (earlier["page_role"], earlier["dropped"]) == ("cover_or_course_nav", True)
    assert earlier["role_evidence"]["related_pages"] == [{"document_id": second, "page_number": 1}]


def test_confirmed_earlier_document_is_not_reassessed(cover_env: ImportEnv) -> None:
    first = _import(cover_env, "a", chapter_code="ch2")
    stored = cover_env.store.get_imported_document(first)
    assert stored is not None
    cover_env.store.update_imported_document(stored.model_copy(update={"status": "confirmed"}), stored.version)
    second = _import(cover_env, "b", chapter_code="ch3")
    assert _page_json(cover_env, second, 1)["dropped"]
    assert not _page_json(cover_env, first, 1)["dropped"]


def test_other_subject_is_never_compared(cover_env: ImportEnv) -> None:
    first = _import(cover_env, "a", chapter_code="ch2")
    second = _import(cover_env, "b", subject_code="ET4020", chapter_code="ch2")
    assert not _page_json(cover_env, first, 1)["dropped"] and not _page_json(cover_env, second, 1)["dropped"]


# --------------------------------------------------------------------------- #
# Hợp đồng dữ liệu: JSON cũ, validator mới
# --------------------------------------------------------------------------- #


def test_old_page_json_reads_with_defaults() -> None:
    old = {
        "document_id": "doc_00000000000000aa",
        "page_number": 1,
        "version": 3,
        "status": "ok",
        "tier_used": "text_layer",
        "text_score": 1.0,
        "lines": [{"line_index": 0, "text": "x", "bbox": None, "conf": 1.0, "engine": "text_layer"}],
        "cross_check": {"status": "not_applicable", "secondary_engine": None},
    }
    page = PageExtraction.model_validate(old)
    assert (page.page_role, page.dropped, page.restored, page.role_evidence) == ("content", False, False, None)
    assert (page.own_images, page.hidden_chars_dropped, page.scan_text_layer, page.content_token_count) == (
        [],
        0,
        False,
        None,
    )
    assert page.lines[0].is_header_footer is False
    summary = PageSummary.model_validate(
        {k: old[k] for k in ("page_number", "status", "tier_used")} | {"review_reasons": [], "confirmed": False}
    )
    assert (summary.page_role, summary.dropped) == ("content", False)


EVIDENCE = {"rule": "chapter_nav", "related_pages": [{"document_id": "doc_00000000000000aa", "page_number": 2}]}


@pytest.mark.parametrize(
    "update",
    [
        {"dropped": True},  # page_role content
        {
            "dropped": True,
            "page_role": "parallel_visual_series",
            "role_evidence": {**EVIDENCE, "rule": "parallel_visual_series"},
        },
        {"dropped": True, "page_role": "chapter_nav", "role_evidence": EVIDENCE, "confirmed": True},
        {"dropped": True, "page_role": "chapter_nav", "role_evidence": EVIDENCE, "restored": True},
        {"page_role": "chapter_nav"},  # thiếu bằng chứng
        {"page_role": "chapter_nav", "role_evidence": {**EVIDENCE, "rule": "build_step"}},
        {"role_evidence": EVIDENCE},  # bằng chứng trên trang content không cờ
        {"review_reasons": ["PAGE_ROLE_UNCERTAIN"], "status": "needs_review"},
        {
            "review_reasons": ["PAGE_ROLE_UNCERTAIN"],
            "status": "needs_review",
            "page_role": "parallel_visual_series",
            "role_evidence": {**EVIDENCE, "rule": "build_step_uncertain"},
        },
        {"own_images": [{"bbox": [0.5, 0.5, 0.2, 0.9]}]},
        {"own_images": [{"bbox": [0.1, 0.1, 0.2, 0.2], "dhash": "xyz"}]},
        {"hidden_chars_dropped": -1},
    ],
)
def test_page_extraction_rejects_inconsistent_roles(update: dict[str, Any]) -> None:
    base = _page("doc_00000000000000aa", 1, ["x"]).model_dump()
    with pytest.raises(ValidationError):
        PageExtraction.model_validate({**base, **update})


def test_page_extraction_accepts_valid_roles() -> None:
    base = _page("doc_00000000000000aa", 1, ["x"]).model_dump()
    dropped = PageExtraction.model_validate(
        {**base, "dropped": True, "page_role": "chapter_nav", "role_evidence": EVIDENCE}
    )
    assert dropped.dropped
    uncertain = PageExtraction.model_validate(
        {
            **base,
            "status": "needs_review",
            "review_reasons": ["PAGE_ROLE_UNCERTAIN"],
            "role_evidence": {**EVIDENCE, "rule": "build_step_uncertain", "dhash_distance": 6},
        }
    )
    assert uncertain.page_role == "content"


@pytest.mark.parametrize(
    "update",
    [
        {"hidden_text_drop_max": 2.0, "hidden_text_keep_min": 2.0},
        {"build_step_dhash_same_max": 9, "parallel_series_dhash_min": 9},
        {"template_min_page_ratio": 0.0},
        {"chapter_nav_min_pages": 1},
    ],
)
def test_reader_config_rejects_bad_denoise_thresholds(update: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        PdfReaderConfig.model_validate(update)
