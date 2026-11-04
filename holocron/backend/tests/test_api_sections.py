"""Bốn endpoint đọc của SPECIFICATION §6.10 (cây, khối của mục, khối của trang, ảnh cắt): mã lỗi, header và phạm vi
tài liệu của ảnh cắt (ADR-010 mục 19; X09).

Ảnh cắt seed bằng `save_crop_image` với PNG tự tạo; khối seed bằng luồng nhập (`accept_document` với bộ đọc giả có
hình học, `tests/import_blocks_support.py`). Không mạng.
"""

from __future__ import annotations

import hashlib
import struct
import zlib
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from holocron_core.domain.blocks import Block, CropImage
from holocron_core.domain.documents import DocumentConfirmRequest
from holocron_core.domain.repositories import AppStore
from holocron_core.domain.vault import PageBlockList, SectionBlocks, SubjectTree
from holocron_core.infrastructure.storage import SQLiteStore
from holocron_core.routers.api import app, get_store
from holocron_core.services.document_import import confirm_document
from tests.import_blocks_support import (
    NOW,
    SUBJECT,
    EditStage,
    GeoReader,
    SamplePage,
    as_figure,
    import_doc,
    layout_with,
    runtime_for,
    store,
)
from tests.test_vault_tree import pages_for

__all__ = ["store"]

CROP_HEADERS = {
    "content-type": "image/png",
    "x-content-type-options": "nosniff",
    "cache-control": "private, max-age=31536000, immutable",
}


def png(width: int = 2, height: int = 1, shade: int = 0) -> bytes:
    """PNG xám 8 bit tự tạo (`shade` đổi byte điểm ảnh ⇒ đổi sha256)."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + bytes([shade]) * width for _ in range(height))
    header = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


@pytest.fixture
def client(store: AppStore) -> Iterator[TestClient]:
    app.dependency_overrides[get_store] = lambda: store
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _pages(tag: str) -> list[SamplePage]:
    return pages_for(tag)


def _figure_doc(store: AppStore, tmp_path: Path, tag: str) -> tuple[str, Block]:
    """Tài liệu đã xác nhận có khối `figure` (`crop_pending`) ở trang 1."""
    layout = layout_with(EditStage("hinh", lambda b: as_figure(b) if b.reading_order == 2 else b, frozenset({1})))
    runtime = runtime_for(tmp_path / tag, layout)
    doc_id = import_doc(store, runtime, GeoReader(_pages(tag)), tag=tag)
    confirm_document(store, store, runtime, doc_id, DocumentConfirmRequest(editor_id="u"), now=NOW)
    [figure] = [block for block in store.list_blocks(doc_id, 1) if block.type == "figure"]
    return doc_id, figure


def _save_crop(store: AppStore, block: Block, content: bytes) -> str:
    assert block.bbox is not None
    ref = hashlib.sha256(content).hexdigest()
    crop = CropImage(
        document_id=block.document_id,
        block_id=block.block_id,
        crop_ref=ref,
        page_number=block.page_number,
        bbox=block.bbox,
        width_px=2,
        height_px=1,
        size_bytes=len(content),
    )
    assert store.save_crop_image(crop, content) == "stored"
    return ref


def test_crop_endpoint_headers_and_scope(store: AppStore, client: TestClient, tmp_path: Path) -> None:
    """200 với đúng header và đúng byte đã lưu; ảnh của tài liệu khác (kể cả cùng nội dung file) ⇒ 404."""
    doc_a, figure_a = _figure_doc(store, tmp_path, "a")
    doc_b, _ = _figure_doc(store, tmp_path, "b")
    content = png()
    ref = _save_crop(store, figure_a, content)
    response = client.get(f"/v1/documents/{doc_a}/crops/{ref}")
    assert response.status_code == 200 and response.content == content
    for name, value in CROP_HEADERS.items():
        assert response.headers[name] == value
    assert client.get(f"/v1/documents/{doc_b}/crops/{ref}").status_code == 404
    assert client.get(f"/v1/documents/doc_ffffffffffffffff/crops/{ref}").status_code == 404
    assert client.get(f"/v1/documents/{doc_a}/crops/{hashlib.sha256(b'khac').hexdigest()}").status_code == 404
    assert client.get(f"/v1/documents/{doc_a}/crops/{ref}?token=x").status_code == 200, "token trong URL bị bỏ qua"


def test_crop_404_when_file_missing(tmp_path: Path) -> None:
    """Hàng `crop_images` còn mà file mất ⇒ 404 (kho SQLite)."""
    crops = tmp_path / "crops"
    sqlite = SQLiteStore.open(tmp_path / "kho.sqlite3", crops_dir=crops)
    app.dependency_overrides[get_store] = lambda: sqlite
    try:
        doc_id, figure = _figure_doc(sqlite, tmp_path, "a")
        ref = _save_crop(sqlite, figure, png(shade=7))
        client = TestClient(app)
        assert client.get(f"/v1/documents/{doc_id}/crops/{ref}").status_code == 200
        [file] = [path for path in crops.rglob("*.png") if path.stem == ref]
        file.unlink()
        assert client.get(f"/v1/documents/{doc_id}/crops/{ref}").status_code == 404
    finally:
        app.dependency_overrides.clear()
        sqlite.close()


def test_tree_sections_pages_crop_404_422(store: AppStore, client: TestClient, tmp_path: Path) -> None:
    doc_id, _ = _figure_doc(store, tmp_path, "a")
    missing = "doc_ffffffffffffffff"
    # cây
    assert client.get("/v1/vault/tree").status_code == 422
    assert client.get("/v1/vault/tree", params={"subject_code": "it3070"}).status_code == 422
    assert client.get("/v1/vault/tree", params={"subject_code": "ZZ9999"}).status_code == 404
    assert SubjectTree.model_validate(client.get("/v1/vault/tree", params={"subject_code": SUBJECT}).json()).sources
    # khối của mục
    [root] = [section for section in store.list_sections(doc_id) if section.kind == "chapter"]
    base = f"/v1/documents/{doc_id}/sections/{root.section_id}/blocks"
    assert client.get(f"/v1/documents/{missing}/sections/{root.section_id}/blocks").status_code == 404
    assert client.get(f"/v1/documents/{doc_id}/sections/sec_ffffffffffffffff/blocks").status_code == 404
    for params in ({"offset": -1}, {"limit": 0}, {"limit": 501}, {"offset": "x"}):
        assert client.get(base, params=params).status_code == 422
    assert SectionBlocks.model_validate(client.get(base, params={"limit": 500}).json()).section == root
    # khối của trang
    assert client.get(f"/v1/documents/{missing}/pages/1/blocks").status_code == 404
    for number in (0, 3):
        assert client.get(f"/v1/documents/{doc_id}/pages/{number}/blocks").status_code == 404
    page = PageBlockList.model_validate(client.get(f"/v1/documents/{doc_id}/pages/1/blocks").json())
    assert page.blocks and page.ledger is not None and page.layout_version is not None
    assert [block.reading_order for block in page.blocks] == sorted(block.reading_order for block in page.blocks)
    # ảnh cắt
    ref = hashlib.sha256(b"x").hexdigest()
    for bad in ("abc", ref.upper(), ref + "0"):
        assert client.get(f"/v1/documents/{doc_id}/crops/{bad}").status_code == 422
    assert client.get(f"/v1/documents/{missing}/crops/{ref}").status_code == 404


def test_section_blocks_paginated_in_page_and_reading_order(
    store: AppStore, client: TestClient, tmp_path: Path
) -> None:
    doc_id, _ = _figure_doc(store, tmp_path, "a")
    [root] = [section for section in store.list_sections(doc_id) if section.kind == "chapter"]
    sections = store.list_sections(doc_id)
    eligible = {block.block_id for block in store.list_eligible_blocks(doc_id)}
    for section in sections:
        body = SectionBlocks.model_validate(
            client.get(f"/v1/documents/{doc_id}/sections/{section.section_id}/blocks").json()
        )
        expected = [block_id for block_id in section.block_ids if block_id in eligible]
        assert body.total == len(expected)
        keys = [(block.page_number, block.reading_order) for block in body.blocks]
        assert keys == sorted(keys) and {block.block_id for block in body.blocks} == set(expected)
    busiest = max(sections, key=lambda section: len(section.block_ids))
    url = f"/v1/documents/{doc_id}/sections/{busiest.section_id}/blocks"
    full = SectionBlocks.model_validate(client.get(url).json())
    if full.total >= 2:
        second = SectionBlocks.model_validate(client.get(url, params={"offset": 1, "limit": 1}).json())
        assert second.total == full.total and second.blocks == full.blocks[1:2]
    assert root.section_id in {section.section_id for section in sections}


def test_tree_includes_confirmed_document_with_null_chapter_code(
    store: AppStore, client: TestClient, tmp_path: Path
) -> None:
    """Tài liệu đã confirmed mà `chapter_code` null (nhập trước khi có cột) vẫn được liệt kê, xếp SAU mọi tài liệu có
    chương; chương sắp theo số (ch2 < ch10); `chapter_shared_with` của nó rỗng."""
    ten, _ = _figure_doc(store, tmp_path, "muoi")
    two, _ = _figure_doc(store, tmp_path, "hai")
    legacy, _ = _figure_doc(store, tmp_path, "cu")
    for document_id, code in ((ten, "ch10"), (two, "ch2")):
        document = store.get_imported_document(document_id)
        assert document is not None
        store.update_imported_document(document.model_copy(update={"chapter_code": code}), document.version)
    document = store.get_imported_document(legacy)
    assert document is not None
    store.update_imported_document(
        document.model_copy(update={"chapter_code": None, "chapter_code_source": None}), document.version
    )
    sources = SubjectTree.model_validate(client.get("/v1/vault/tree", params={"subject_code": SUBJECT}).json()).sources
    assert [source.document_id for source in sources] == [two, ten, legacy]
    assert sources[2].chapter_code is None and sources[2].chapter_shared_with == []
