"""Đổi mã hoặc tên chương của tài liệu nhập (SPECIFICATION §6.5 `PATCH /v1/documents/{document_id}/chapter`, §5.7
"Chương tự động"; GĐ1 1A).

- Đổi `chapter_code` chỉ khi tài liệu chưa `confirmed` và Vault chưa có chunk của tài liệu (chunk chép mã chương lúc
  cắt, nên đổi sau đó sẽ lệch); `chapter_title` đổi lúc nào cũng được. Request có cả hai field mà mã chương đã khoá
  thì không đổi gì.
- Giá trị người dùng đặt nhận nguồn `"user"`; đặt `chapter_code` xoá mọi cờ chương. Mã được phép trùng chương của
  tài liệu khác cùng môn (như form).
- Ghi dưới khoá của tài liệu (`ImportRuntime.locks`), không lấy khoá môn; không có `expected_version`, người ghi sau
  thắng. Lần ghi bị version lệch (một luồng ghi tài liệu ngoài khoá) được đọc lại và thử lại tối đa
  `SETTLE_ATTEMPTS` lần.
"""

from __future__ import annotations

from typing import Any

from ..domain.documents import DocumentChapterPatch, DocumentImport
from ..domain.repositories import DocumentRepository, ImportedDocument, VaultRepository, VersionConflictError
from .document_import import (
    SETTLE_ATTEMPTS,
    DocumentChapterMissingError,
    DocumentNotFoundError,
    ImportRuntime,
    get_document_view,
)

CHAPTER_CODE_LOCKED_DETAIL = (
    "Tài liệu đã xác nhận hoặc văn bản đã vào Knowledge Vault nên không đổi được mã chương; tên chương vẫn đổi được"
)


class ChapterCodeLockedError(Exception):
    """Đổi `chapter_code` khi tài liệu đã `confirmed` hoặc Vault đã có chunk của tài liệu (409, §6.5)."""

    def __init__(self) -> None:
        super().__init__(CHAPTER_CODE_LOCKED_DETAIL)


def _patched(document: ImportedDocument, patch: DocumentChapterPatch) -> ImportedDocument:
    changes: dict[str, Any] = {}
    if patch.chapter_code is not None:
        changes.update(chapter_code=patch.chapter_code, chapter_code_source="user", chapter_flags=[])
    if patch.chapter_title is not None:
        changes.update(chapter_title=patch.chapter_title, chapter_title_source="user")
    return ImportedDocument.model_validate({**document.model_dump(), **changes})


def update_document_chapter(
    store: DocumentRepository,
    vault: VaultRepository,
    runtime: ImportRuntime,
    document_id: str,
    patch: DocumentChapterPatch,
) -> DocumentImport:
    """Áp `patch` rồi trả `DocumentImport` đang lưu.

    Lỗi, kiểm TRƯỚC khi ghi: `DocumentNotFoundError` (404); `DocumentChapterMissingError` (409, tài liệu nhập trước
    khi có `chapter_code`); `ChapterCodeLockedError` (409). Version vẫn lệch sau `SETTLE_ATTEMPTS` lần ⇒
    `VersionConflictError` (409).
    """
    with runtime.locks.hold(document_id):
        for attempt in range(1, SETTLE_ATTEMPTS + 1):
            document = store.get_imported_document(document_id)
            if document is None:
                raise DocumentNotFoundError(document_id)
            if document.chapter_code is None:
                raise DocumentChapterMissingError()
            if patch.chapter_code is not None and (
                document.status == "confirmed" or vault.has_document_chunks(document_id)
            ):
                raise ChapterCodeLockedError()
            try:
                store.update_imported_document(_patched(document, patch), document.version)
            except VersionConflictError:
                if attempt == SETTLE_ATTEMPTS:
                    raise
                continue
            break
        return get_document_view(store, document_id)
