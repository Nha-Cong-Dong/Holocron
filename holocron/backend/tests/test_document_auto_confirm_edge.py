"""Nhánh biên của bước tự xác nhận (SPECIFICATION §5.7 "Tự xác nhận cấp trang"): trang đã hoàn tác bỏ mềm, version
lệch, lỗi kho, thiếu `chapter_code`, Vault đã có chunk. Văn bản là mẫu tự viết."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from holocron_core.domain.documents import PageExtraction
from holocron_core.domain.repositories import AppStore, StorageError, VersionConflictError
from holocron_core.domain.schemas import SourceKind
from holocron_core.routers.api import app
from holocron_core.services import document_import
from holocron_core.services.auto_confirm import passes_page_clean
from holocron_core.services.document_import import SETTLE_ATTEMPTS, SynchronousImportQueue
from tests.document_import_support import FakePdfReader, ImportEnv, install, ok_page, pdf_bytes
from tests.test_document_auto_confirm import _doc_id, _document, _page
from tests.vault_support import NOW, STORE_KINDS, open_store

PAGES = 3
FIXED = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)


@pytest.fixture(params=STORE_KINDS)
def env(request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ImportEnv]:
    for store in open_store(str(request.param), tmp_path, monkeypatch):
        yield install(store, tmp_path / "files", SynchronousImportQueue(), FakePdfReader(page_count=PAGES))
        app.dependency_overrides.clear()


def _clock() -> datetime:
    return FIXED


def _open_document(env: ImportEnv, *, chapter_code: str | None = "ch3") -> str:
    """Tài liệu đọc xong với `auto_confirm = false` (mọi trang `ok` chưa xác nhận) rồi bật `auto_confirm` ở kho, như
    tài liệu sẵn sàng cho bước tự xác nhận chạy lần nữa."""
    content = pdf_bytes("edge")
    response = env.post(content, auto_confirm="false")
    assert response.status_code == 202, response.text
    doc = _doc_id(content)
    stored = env.store.get_imported_document(doc)
    assert stored is not None
    env.store.update_imported_document(
        stored.model_copy(update={"auto_confirm": True, "chapter_code": chapter_code}), stored.version
    )
    assert all(not page.confirmed for page in env.store.list_pages(doc, PageExtraction))
    return doc


def _auto_numbers(env: ImportEnv, doc: str) -> list[int]:
    return [p.page_number for p in env.store.list_pages(doc, PageExtraction) if p.confirmed_by == "auto"]


class _ConflictingStore:
    """Bọc kho: `update_page` của một trang ném `VersionConflictError` `failures` lần đầu (như luồng khác vừa ghi
    trang). `interfere` (nếu có) chạy một lần ngay trước lần ném đầu tiên."""

    def __init__(self, store: AppStore, page_number: int, failures: int, interfere: Any = None) -> None:
        self._store = store
        self._page_number = page_number
        self.failures = failures
        self.attempts = 0
        self._interfere = interfere

    def __getattr__(self, name: str) -> Any:
        return getattr(self._store, name)

    def update_page(self, document_id: str, page_number: int, page: Any, **kwargs: Any) -> int:
        if page_number == self._page_number:
            self.attempts += 1
            if self.attempts <= self.failures:
                if self._interfere is not None and self.attempts == 1:
                    self._interfere()
                raise VersionConflictError("trang", f"{document_id}#{page_number}", 1, 2)
        result: int = self._store.update_page(document_id, page_number, page, **kwargs)
        return result


# --------------------------------------------------------------------------- #
# page_clean_v1 và trang đã hoàn tác bỏ mềm
# --------------------------------------------------------------------------- #


def test_page_clean_never_auto_confirms_a_restored_page() -> None:
    page = ok_page("doc_" + "a" * 16, 1)
    arguments: dict[str, Any] = {
        "auto_confirm": True,
        "source_kind": SourceKind.LECTURE_SLIDES,
        "has_text": True,
        "fits_vault": True,
    }
    assert passes_page_clean(page, **arguments) is True
    restored = page.model_copy(update={"restored": True})
    assert passes_page_clean(restored, **arguments) is False


def test_auto_confirm_step_skips_a_restored_page(env: ImportEnv) -> None:
    doc = _open_document(env)
    page = env.store.get_page(doc, 2, PageExtraction)
    assert page is not None
    restored = page.model_copy(update={"restored": True, "version": page.version + 1})
    env.store.update_page(doc, 2, restored, status=page.status, confirmed=False, expected_version=page.version)
    document_import._auto_confirm_pages(env.store, env.store, doc, _clock)
    assert _auto_numbers(env, doc) == [1, 3]
    assert _page(env, doc, 2).confirmed is False


# --------------------------------------------------------------------------- #
# Version lệch
# --------------------------------------------------------------------------- #


def test_auto_confirm_retries_on_version_conflict(env: ImportEnv) -> None:
    doc = _open_document(env)
    wrapped = _ConflictingStore(env.store, 2, SETTLE_ATTEMPTS - 1)
    document_import._auto_confirm_pages(wrapped, env.store, doc, _clock)
    assert wrapped.attempts == SETTLE_ATTEMPTS
    assert _auto_numbers(env, doc) == [1, 2, 3]
    page = _page(env, doc, 2)
    assert page.confirmed_at == FIXED and page.confirm_rule == "page_clean_v1"


def test_auto_confirm_gives_up_after_settle_attempts(env: ImportEnv, caplog: pytest.LogCaptureFixture) -> None:
    doc = _open_document(env)
    wrapped = _ConflictingStore(env.store, 2, SETTLE_ATTEMPTS + 5)
    with caplog.at_level(logging.WARNING):
        document_import._auto_confirm_pages(wrapped, env.store, doc, _clock)
    assert wrapped.attempts == SETTLE_ATTEMPTS
    assert _auto_numbers(env, doc) == [1, 3], "trang lệch version giữ như cũ (chưa xác nhận là phía an toàn)"
    page = _page(env, doc, 2)
    assert (page.confirmed, page.confirmed_at, page.confirmed_by, page.confirm_rule) == (False, None, None, None)
    assert any("tự xác nhận được trang 2" in record.getMessage() for record in caplog.records)


def test_auto_confirm_stops_when_the_page_is_no_longer_clean_after_a_conflict(env: ImportEnv) -> None:
    """Giữa hai lần thử, người dùng xác nhận trang: đọc lại thấy trang đã xác nhận ⇒ thôi, không ghi đè."""
    doc = _open_document(env)

    def user_confirms() -> None:
        page = env.store.get_page(doc, 2, PageExtraction)
        assert page is not None
        mine = page.model_copy(
            update={"confirmed": True, "confirmed_at": NOW, "confirmed_by": "user", "version": page.version + 1}
        )
        env.store.update_page(doc, 2, mine, status=page.status, confirmed=True, expected_version=page.version)

    wrapped = _ConflictingStore(env.store, 2, 1, interfere=user_confirms)
    document_import._auto_confirm_pages(wrapped, env.store, doc, _clock)
    assert wrapped.attempts == 1, "không thử ghi lần hai"
    page = _page(env, doc, 2)
    assert (page.confirmed_by, page.confirm_rule, page.confirmed_at) == ("user", None, NOW)


# --------------------------------------------------------------------------- #
# _finish và các điều kiện bỏ qua
# --------------------------------------------------------------------------- #


def test_finish_survives_a_storage_error_in_the_auto_confirm_step(
    env: ImportEnv, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    doc = _open_document(env)

    def broken(*_args: Any, **_kwargs: Any) -> None:
        raise StorageError("giả lập lỗi kho")

    monkeypatch.setattr(document_import, "_auto_confirm_pages", broken)
    with caplog.at_level(logging.WARNING):
        document_import._finish(env.store, doc, "review", env.store)
    assert any("chưa tự xác nhận được trang của tài liệu" in record.getMessage() for record in caplog.records)
    assert "giả lập lỗi kho" not in caplog.text, "chỉ ghi tên lớp lỗi"
    assert _auto_numbers(env, doc) == []
    assert _document(env, doc).status == "review", "bước ghi trạng thái cuối vẫn chạy"


def test_finish_auto_confirms_when_nothing_blocks(env: ImportEnv) -> None:
    """Đối chứng của hai test dưới: cùng dữ liệu, không bị chặn thì cả ba trang được tự xác nhận."""
    doc = _open_document(env)
    document_import._finish(env.store, doc, "review", env.store, clock=_clock)
    assert _auto_numbers(env, doc) == [1, 2, 3]
    assert _document(env, doc).status == "review"


def test_auto_confirm_skips_a_document_without_chapter_code(env: ImportEnv) -> None:
    doc = _open_document(env, chapter_code=None)
    document_import._finish(env.store, doc, "review", env.store, clock=_clock)
    assert _auto_numbers(env, doc) == [], "thiếu chapter_code thì không hoàn tác được ⇒ không tự xác nhận"
    assert all(not page.confirmed for page in env.store.list_pages(doc, PageExtraction))


def test_auto_confirm_skips_when_the_vault_already_has_chunks(env: ImportEnv, monkeypatch: pytest.MonkeyPatch) -> None:
    doc = _open_document(env)
    monkeypatch.setattr(type(env.store), "has_document_chunks", lambda self, document_id: True)
    document_import._finish(env.store, doc, "review", env.store, clock=_clock)
    assert _auto_numbers(env, doc) == []


def test_auto_confirm_skips_a_confirmed_document(env: ImportEnv) -> None:
    doc = _open_document(env)
    stored = env.store.get_imported_document(doc)
    assert stored is not None
    env.store.update_imported_document(stored.model_copy(update={"status": "confirmed"}), stored.version)
    document_import._auto_confirm_pages(env.store, env.store, doc, _clock)
    assert _auto_numbers(env, doc) == []


def test_default_read_job_clock_is_the_module_clock() -> None:
    job_clock = document_import.ReadJob.__dataclass_fields__["clock"].default
    assert job_clock is document_import._utc_now
    assert job_clock().tzinfo is UTC
