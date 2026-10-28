"""Trang vượt giới hạn dòng của Knowledge Vault lúc đọc thành `failed` với `TOO_MANY_LINES` (SPECIFICATION §5.7,
§6.5; câu 18 (a) duyệt 2026-10-28): trang đó không chặn tài liệu, các trang còn lại xác nhận được và vào Vault.

Bộ đọc trả tới `MAX_PAGE_LINES` của `document_readers` (10 000) dòng mỗi trang, Vault chỉ nhận `MAX_PAGE_LINES` của
`domain.vault` (5000) dòng có văn bản. Chạy trên cả ba loại kho, `PdfReader` giả với trang dựng sẵn, hàng đợi đọc
đồng bộ. Văn bản là mẫu tự viết, không phải tài liệu môn học có bản quyền.
"""

import hashlib
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from holocron_core.domain.documents import CrossCheck, DocumentImport, ExtractedLine, PageExtraction
from holocron_core.domain.vault import MAX_PAGE_LINES
from holocron_core.infrastructure.document_readers.protocol import MAX_PAGE_LINES as READER_MAX_PAGE_LINES
from holocron_core.routers.api import app
from holocron_core.services.document_import import SynchronousImportQueue
from tests.document_import_support import FakePdfReader, ImportEnv, install, ok_page, pdf_bytes
from tests.vault_support import STORE_KINDS, open_store

EDITOR = "local_user"
SUBJECT = "ET4010"


@pytest.fixture(params=STORE_KINDS)
def env(request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ImportEnv]:
    for store in open_store(str(request.param), tmp_path, monkeypatch):
        yield install(store, tmp_path / "files", SynchronousImportQueue(), FakePdfReader())
        app.dependency_overrides.clear()


def many_lines_page(
    document_id: str, page_number: int, text_lines: int, blank_lines: int = 0, *, has_table: bool = False
) -> PageExtraction:
    """Trang `ok` đọc từ lớp chữ: `text_lines` dòng có chữ rồi `blank_lines` dòng chỉ có khoảng trắng."""
    texts = [f"Dòng {index} của trang {page_number}." for index in range(text_lines)] + ["   "] * blank_lines
    return PageExtraction(
        document_id=document_id,
        page_number=page_number,
        status="ok",
        tier_used="text_layer",
        text_score=0.95,
        has_table=has_table,
        lines=[
            ExtractedLine(line_index=index, text=text, bbox=None, conf=0.95, engine="text_layer")
            for index, text in enumerate(texts)
        ],
        cross_check=CrossCheck(status="not_applicable", secondary_engine=None),
    )


def _post(env: ImportEnv, tag: str, pages: dict[int, Callable[[str], PageExtraction]]) -> str:
    env.reader.custom_pages = pages
    env.reader.page_count = max(pages)
    content = pdf_bytes(tag)
    response = env.post(content)
    assert response.status_code == 202, response.text
    return "doc_" + hashlib.sha256(content).hexdigest()[:16]


def _page(env: ImportEnv, doc_id: str, number: int) -> PageExtraction:
    response = env.client.get(f"/v1/documents/{doc_id}/pages/{number}")
    assert response.status_code == 200, response.text
    return PageExtraction.model_validate(response.json())


def _document(env: ImportEnv, doc_id: str) -> DocumentImport:
    response = env.client.get(f"/v1/documents/{doc_id}")
    assert response.status_code == 200, response.text
    return DocumentImport.model_validate(response.json())


def _confirm_document(env: ImportEnv, doc_id: str) -> Any:
    return env.client.post(f"/v1/documents/{doc_id}/confirm", json={"editor_id": EDITOR})


def _vault_text(env: ImportEnv, doc_id: str) -> str:
    return "\n".join(chunk.raw_text for chunk in env.store.list_chunks(SUBJECT) if chunk.doc_id == doc_id)


def test_premise_reader_allows_more_lines_than_the_vault() -> None:
    assert MAX_PAGE_LINES + 1 <= READER_MAX_PAGE_LINES


def test_page_over_the_vault_line_limit_is_failed_too_many_lines(env: ImportEnv) -> None:
    doc_id = _post(env, "qua-nhieu-dong", {1: lambda d: many_lines_page(d, 1, MAX_PAGE_LINES + 1, has_table=True)})

    page = _page(env, doc_id, 1)

    assert (page.status, page.failure_reason, page.tier_used, page.lines) == ("failed", "TOO_MANY_LINES", None, [])
    assert page.review_reasons == []
    assert page.confirmed is False
    assert page.text_score == 0.95 and page.has_table, "điểm và cờ của lần đọc được giữ để hiển thị"
    assert _document(env, doc_id).status == "review"


def test_page_at_exactly_the_vault_line_limit_is_not_failed(env: ImportEnv) -> None:
    """Đúng `MAX_PAGE_LINES` dòng có chữ, cộng dòng chỉ có khoảng trắng (không vào văn bản xác nhận): không failed."""
    doc_id = _post(env, "dung-gioi-han-dong", {1: lambda d: many_lines_page(d, 1, MAX_PAGE_LINES, blank_lines=5)})

    page = _page(env, doc_id, 1)

    assert (page.status, page.failure_reason) == ("ok", None)
    assert len(page.lines) == MAX_PAGE_LINES + 5


def test_oversize_page_does_not_block_the_rest_of_the_document(env: ImportEnv) -> None:
    """1 trang vượt + 1 trang thường: xác nhận cả tài liệu được, Vault nhận trang thường, không nhận trang vượt."""
    pages: dict[int, Callable[[str], PageExtraction]] = {
        1: lambda d: many_lines_page(d, 1, MAX_PAGE_LINES + 1),
        2: lambda d: ok_page(d, 2),
    }
    doc_id = _post(env, "mot-trang-vuot-mot-trang-thuong", pages)
    assert [p.status for p in _document(env, doc_id).pages] == ["failed", "ok"]

    response = _confirm_document(env, doc_id)

    assert response.status_code == 200, response.text
    body = DocumentImport.model_validate(response.json())
    assert body.status == "confirmed"
    assert [(p.status, p.confirmed) for p in body.pages] == [("failed", False), ("ok", True)]
    text = _vault_text(env, doc_id)
    assert "Trang 2: nội dung mẫu tự viết" in text
    assert "của trang 1." not in text
