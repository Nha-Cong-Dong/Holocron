"""Tự xác nhận cấp trang gặp luật bìa đi ngược (SPECIFICATION §5.7 0D "Thứ tự nhập" và "Tự xác nhận cấp trang").

Tài liệu nhập trước (A) tự xác nhận trang bìa vì lúc đó chưa có tài liệu nào để so. Khi tài liệu sau (B) cùng môn có
bìa trùng, A còn mở thì bìa của A bị bỏ mềm và mất luôn tự xác nhận (I5: trang bỏ mềm không bao giờ vào Vault); bìa
do NGƯỜI DÙNG xác nhận, tài liệu A đã được người dùng chấp nhận hay trang đã hoàn tác bỏ mềm thì không đổi. Chạy trên
cả ba loại kho. Văn bản là mẫu tự viết.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from holocron_core.domain.documents import PageExtraction
from holocron_core.routers.api import app
from holocron_core.services import document_import
from holocron_core.services.document_import import SynchronousImportQueue
from tests.document_import_support import FakePdfReader, ImportEnv, install, pdf_bytes
from tests.test_document_auto_confirm import (
    _body,
    _confirm_document,
    _confirm_page,
    _doc_id,
    _document,
    _page,
    _text_page,
    _unconfirm,
    clean_page,
)
from tests.vault_support import STORE_KINDS, open_store

COVER = ["Truong dai hoc mau", "Khoa dien tu vien thong", "Mon vi xu ly", "Giang vien mau", "Hoc ky mau nam"]


def cover_page(number: int) -> Callable[[str], PageExtraction]:
    return lambda document_id: _text_page(document_id, number, COVER)


PAGES: dict[int, Callable[[str], PageExtraction]] = {
    1: cover_page(1),
    2: clean_page(2),
    3: clean_page(3),
    4: clean_page(4),
}


@pytest.fixture(params=STORE_KINDS)
def env(request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ImportEnv]:
    for store in open_store(str(request.param), tmp_path, monkeypatch):
        reader = FakePdfReader(page_count=len(PAGES), custom_pages=dict(PAGES))
        yield install(store, tmp_path / "files", SynchronousImportQueue(), reader)
        app.dependency_overrides.clear()


def _import(env: ImportEnv, tag: str, chapter_code: str = "ch2") -> str:
    content = pdf_bytes(tag)
    response = env.post(content, chapter_code=chapter_code, auto_confirm="true")
    assert response.status_code == 202, response.text
    return _doc_id(content)


def _chunk_text(env: ImportEnv) -> str:
    return " ".join(chunk.raw_text for chunk in env.store.list_chunks("ET4010"))


def _is_machine_free(page: PageExtraction) -> bool:
    return (page.confirmed, page.confirmed_at, page.confirmed_by, page.confirm_rule) == (False, None, None, None)


def test_first_document_auto_confirms_its_cover(env: ImportEnv) -> None:
    first = _import(env, "a")
    cover = _page(env, first, 1)
    assert (cover.dropped, cover.confirmed_by, cover.confirm_rule) == (False, "auto", "page_clean_v1")


def test_later_document_drops_the_auto_confirmed_cover_of_an_open_document(env: ImportEnv) -> None:
    first = _import(env, "a")
    before = _page(env, first, 1)
    assert before.confirmed_by == "auto"
    second = _import(env, "b", chapter_code="ch3")
    assert _page(env, second, 1).dropped
    cover = _page(env, first, 1)
    assert (cover.page_role, cover.dropped) == ("cover_or_course_nav", True)
    assert _is_machine_free(cover), "bìa bị bỏ mềm mất luôn tự xác nhận"
    assert cover.auto_confirm_undone is False, "không phải người dùng hoàn tác"
    assert cover.version == before.version + 1
    assert cover.role_evidence is not None
    assert [(ref.document_id, ref.page_number) for ref in cover.role_evidence.related_pages] == [(second, 1)]
    assert (cover.lines, cover.review_reasons, cover.status) == (before.lines, before.review_reasons, before.status)
    document = _document(env, first)
    assert (document.pages_dropped, document.pages_auto_confirmed, document.status) == (1, 3, "review")
    assert [page.confirmed_by for page in document.pages] == [None, "auto", "auto", "auto"]
    assert not env.store.has_document_chunks(first)


def test_dropped_auto_cover_never_reaches_the_vault(env: ImportEnv) -> None:
    first = _import(env, "a")
    _import(env, "b", chapter_code="ch3")
    response = _confirm_document(env, first)
    assert response.status_code == 200 and response.json()["status"] == "confirmed"
    text = _chunk_text(env)
    assert text, "các trang nội dung vẫn vào Vault"
    assert all(line not in text for line in COVER)
    assert not _page(env, first, 1).confirmed


def test_cover_confirmed_by_the_user_is_left_alone(env: ImportEnv) -> None:
    first = _import(env, "a")
    assert _unconfirm(env, first, 1, _page(env, first, 1).version).status_code == 200
    assert _confirm_page(env, first, 1).status_code == 200
    before = _page(env, first, 1)
    assert before.confirmed_by == "user"
    second = _import(env, "b", chapter_code="ch3")
    assert _page(env, second, 1).dropped, "bìa của tài liệu sau vẫn bỏ mềm"
    after = _page(env, first, 1)
    assert after == before, "trang do người dùng xác nhận không đổi"
    assert (after.dropped, after.confirmed_by, after.version) == (False, "user", before.version)


def test_cover_of_a_document_the_user_already_accepted_is_left_alone(env: ImportEnv) -> None:
    first = _import(env, "a")
    assert _confirm_document(env, first).status_code == 200
    before = _page(env, first, 1)
    _import(env, "b", chapter_code="ch3")
    assert _page(env, first, 1) == before
    assert before.confirmed_by == "auto" and not before.dropped
    assert any(line in _chunk_text(env) for line in COVER), (
        "giới hạn đã biết: bìa của tài liệu đã chấp nhận ở lại Vault"
    )


def test_cover_with_chunks_in_the_vault_is_left_alone(env: ImportEnv, monkeypatch: pytest.MonkeyPatch) -> None:
    first = _import(env, "a")
    before = _page(env, first, 1)
    monkeypatch.setattr(type(env.store), "has_document_chunks", lambda self, document_id: document_id == first)
    _import(env, "b", chapter_code="ch3")
    assert _page(env, first, 1) == before


def test_restored_cover_is_not_dropped_again_nor_auto_confirmed(env: ImportEnv) -> None:
    first = _import(env, "a")
    _import(env, "b", chapter_code="ch3")
    dropped = _page(env, first, 1)
    assert dropped.dropped
    restore = env.client.post(f"/v1/documents/{first}/pages/1/restore", json=_body(dropped.version))
    assert restore.status_code == 200
    restored = _page(env, first, 1)
    assert (restored.restored, restored.dropped, restored.confirmed) == (True, False, False)
    _import(env, "c", chapter_code="ch4")
    assert _page(env, first, 1) == restored, "trang đã hoàn tác không bị bỏ mềm lại"
    document_import._finish(env.store, first, "review", env.store)
    assert _page(env, first, 1) == restored, "trang đã hoàn tác không tự xác nhận lại (page_clean_v1)"
    assert _confirm_page(env, first, 1).status_code == 200
    assert _page(env, first, 1).confirmed_by == "user"


def test_reassessment_only_touches_pages_that_match(env: ImportEnv) -> None:
    first = _import(env, "a")
    before = {number: _page(env, first, number) for number in (2, 3, 4)}
    _import(env, "b", chapter_code="ch3")
    assert {number: _page(env, first, number) for number in (2, 3, 4)} == before


def test_soft_dropped_auto_cover_keeps_the_confirmed_column_in_step_with_the_json(env: ImportEnv) -> None:
    """`_write_role` ghi cột `confirmed` theo trang đã áp quyết định, không theo hằng: sau khi bìa tự xác nhận bị bỏ
    mềm, bản tóm tắt (đọc cột) và JSON trang (đọc `confirmed`) phải khớp, trên cả ba loại kho."""
    first = _import(env, "a")
    assert [s.confirmed for s in env.store.list_page_summaries(first)] == [True, True, True, True]
    _import(env, "b", chapter_code="ch3")
    pages = env.store.list_pages(first, PageExtraction)
    summaries = env.store.list_page_summaries(first)
    assert pages[0].dropped and not pages[0].confirmed
    assert [s.confirmed for s in summaries] == [page.confirmed for page in pages] == [False, True, True, True]
