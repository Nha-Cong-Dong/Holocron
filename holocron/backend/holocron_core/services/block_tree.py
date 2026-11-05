"""Đọc cây mục, khối của mục và khối của trang (SPECIFICATION §6.10; ADR-010 mục 4, 13; X09).

Hàm thuần trên dữ liệu kho, CHỈ đọc. Môn (`Subject`) và Nguồn (tài liệu) dựng lúc đọc, không lưu thành Section.

- Cây và khối của mục CHỈ thấy khối đủ điều kiện (`eligible = 1`) của tài liệu ĐÃ ĐÓNG, lọc LÚC TRUY VẤN: "đã đóng" =
  `status = "confirmed"` HOẶC Vault đã có chunk của tài liệu (cửa sổ settle-pending). Dừng ở cửa sổ (3)→(4) của settle
  (chunk có, `eligible` chưa đặt) ⇒ thấy ít hơn chunk; dừng ở (4)→(5) với tài liệu 0 chunk (khối `eligible` mà tài liệu
  chưa đóng) ⇒ tài liệu không hiện. Không bao giờ thấy khối của tài liệu chưa đóng.
- Nguồn bỏ tài liệu `past_exam` (ngoài phạm vi Vault); sắp theo số chương (ch2 < ch10; `chapter_code` null SAU mọi
  chương có mã), rồi `uploaded_at`, rồi `document_id`.
- Ba danh sách trang để không hụt im lặng (đếm trên trang HIỆN TẠI): `layout_failed_pages` (trang `LAYOUT_FAILED`),
  `pages_without_blocks` ("trang có nội dung" — `confirmed`, không `dropped`, không `failed`, `page_has_text` — không có
  khối, không `LAYOUT_FAILED`), `pages_unconfirmable_with_blocks` (trang có khối, không `dropped`, `page_has_text` sai).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from ..domain.blocks import Block, Section
from ..domain.documents import PageExtraction
from ..domain.repositories import DocumentRepository, ImportedDocument, SubjectRepository, VaultRepository
from ..domain.schemas import SourceKind
from ..domain.vault import PageBlockList, SectionBlocks, SectionNode, SourceNode, SubjectTree
from .document_import import page_has_text

__all__ = [
    "BlockTreeNotFoundError",
    "is_document_closed",
    "page_block_list",
    "section_blocks",
    "subject_tree",
]


class BlockTreeNotFoundError(Exception):
    """Môn, tài liệu, mục hay trang không có (router trả 404)."""


def is_document_closed(document: ImportedDocument, vault: VaultRepository) -> bool:
    """ "Tài liệu đã đóng" (ADR-010 mục 13): `status = "confirmed"` HOẶC Vault đã có chunk của tài liệu."""
    return document.status == "confirmed" or vault.has_document_chunks(document.meta.doc_id)


def _chapter_key(chapter_code: str | None) -> tuple[int, tuple[int, ...]]:
    """Khoá sắp chương: số chương theo số (ch2 < ch10, ch2 < ch2.1); `chapter_code` null xếp sau mọi chương có mã."""
    if chapter_code is None:
        return 1, ()
    return 0, tuple(int(part) for part in chapter_code.removeprefix("ch").split("."))


def _source_key(document: ImportedDocument) -> tuple[tuple[int, tuple[int, ...]], datetime, str]:
    return _chapter_key(document.chapter_code), document.meta.uploaded_at, document.meta.doc_id


def _section_node(section: Section, children: dict[str, list[Section]]) -> SectionNode:
    return SectionNode(
        section=section,
        children=[_section_node(child, children) for child in children.get(section.section_id, [])],
    )


def _root(sections: Sequence[Section]) -> SectionNode | None:
    """Cây từ Section đã lưu của tài liệu: gốc `kind = "chapter"`, con theo `ordinal`; không có gốc ⇒ None."""
    ordered = sorted(sections, key=lambda section: section.ordinal)
    children: dict[str, list[Section]] = {}
    for section in ordered:
        if section.parent_id is not None:
            children.setdefault(section.parent_id, []).append(section)
    root = next((section for section in ordered if section.kind == "chapter"), None)
    return None if root is None else _section_node(root, children)


def _page_lists(pages: Sequence[PageExtraction], blocks: Sequence[Block]) -> tuple[list[int], list[int], list[int]]:
    """`(layout_failed_pages, pages_without_blocks, pages_unconfirmable_with_blocks)`, mỗi danh sách tăng dần."""
    with_blocks = {block.page_number for block in blocks}
    failed: list[int] = []
    without: list[int] = []
    unconfirmable: list[int] = []
    for page in sorted(pages, key=lambda item: item.page_number):
        number = page.page_number
        layout_failed = "LAYOUT_FAILED" in page.review_reasons
        has_text = page_has_text(page)
        if layout_failed:
            failed.append(number)
        if number in with_blocks:
            if not page.dropped and not has_text:
                unconfirmable.append(number)
            continue
        if page.confirmed and not page.dropped and page.status != "failed" and has_text and not layout_failed:
            without.append(number)
    return failed, without, unconfirmable


def _source_node(store: DocumentRepository, document: ImportedDocument, shared: list[str]) -> SourceNode:
    document_id = document.meta.doc_id
    eligible = store.list_eligible_blocks(document_id)
    root = _root(store.list_sections(document_id)) if eligible else None
    failed, without, unconfirmable = _page_lists(
        store.list_pages(document_id, PageExtraction), store.list_blocks(document_id)
    )
    return SourceNode(
        document_id=document_id,
        title=document.meta.title,
        source_kind=document.meta.source_kind,
        chapter_code=document.chapter_code,
        chapter_title=document.chapter_title,
        layout_version=document.layout_version,
        chunks_from_lines=document.blocks_after_close,
        chapter_shared_with=shared,
        root=root,
        layout_failed_pages=failed,
        pages_without_blocks=without,
        pages_unconfirmable_with_blocks=unconfirmable,
    )


def subject_tree(
    subjects: SubjectRepository, store: DocumentRepository, vault: VaultRepository, subject_code: str
) -> SubjectTree:
    """`GET /v1/vault/tree` (§6.10). Môn không có ⇒ `BlockTreeNotFoundError`."""
    subject = subjects.get_subject(subject_code)
    if subject is None:
        raise BlockTreeNotFoundError(subject_code)
    documents = sorted(
        (
            document
            for document in store.list_imported_documents(subject_code)
            if document.meta.source_kind != SourceKind.PAST_EXAM and is_document_closed(document, vault)
        ),
        key=_source_key,
    )
    by_uploaded = sorted(documents, key=lambda document: (document.meta.uploaded_at, document.meta.doc_id))
    sources: list[SourceNode] = []
    for document in documents:
        code = document.chapter_code
        shared = (
            []
            if code is None
            else [
                other.meta.doc_id
                for other in by_uploaded
                if other.chapter_code == code and other.meta.doc_id != document.meta.doc_id
            ]
        )
        sources.append(_source_node(store, document, shared))
    return SubjectTree(subject_code=subject.subject_code, display_name=subject.display_name, sources=sources)


def _document(store: DocumentRepository, document_id: str) -> ImportedDocument:
    document = store.get_imported_document(document_id)
    if document is None:
        raise BlockTreeNotFoundError(document_id)
    return document


def section_blocks(
    store: DocumentRepository,
    vault: VaultRepository,
    document_id: str,
    section_id: str,
    *,
    offset: int,
    limit: int,
) -> SectionBlocks:
    """`GET /v1/documents/{id}/sections/{section_id}/blocks` (§6.10): khối đủ điều kiện của mục theo (trang,
    `reading_order`), phân trang `offset`/`limit`. Tài liệu chưa đóng ⇒ `total = 0`, không khối (lọc lúc truy vấn).
    Tài liệu hay mục không có ⇒ `BlockTreeNotFoundError`."""
    document = _document(store, document_id)
    section = next((item for item in store.list_sections(document_id) if item.section_id == section_id), None)
    if section is None:
        raise BlockTreeNotFoundError(section_id)
    if not is_document_closed(document, vault):
        return SectionBlocks(section=section, total=0, blocks=[])
    members = set(section.block_ids)
    blocks = sorted(
        (block for block in store.list_eligible_blocks(document_id) if block.block_id in members),
        key=lambda block: (block.page_number, block.reading_order, block.block_id),
    )
    return SectionBlocks(section=section, total=len(blocks), blocks=blocks[offset : offset + limit])


def page_block_list(store: DocumentRepository, document_id: str, page_number: int) -> PageBlockList:
    """`GET /v1/documents/{id}/pages/{n}/blocks` (§6.10): MỌI khối của trang theo `reading_order` và ledger (null khi
    chưa có). Tài liệu không có hoặc trang ngoài `1..page_count` ⇒ `BlockTreeNotFoundError`."""
    document = _document(store, document_id)
    if not 1 <= page_number <= document.meta.page_count:
        raise BlockTreeNotFoundError(f"{document_id}#{page_number}")
    blocks = sorted(
        store.list_blocks(document_id, page_number), key=lambda block: (block.reading_order, block.block_id)
    )
    ledger = next(
        (item for item in store.list_extraction_ledgers(document_id) if item.page_number == page_number), None
    )
    return PageBlockList(
        document_id=document_id,
        page_number=page_number,
        layout_version=document.layout_version,
        blocks=blocks,
        ledger=ledger,
    )
