"""Bộ đọc giả có hình học, trang tự tạo và helper nhập tài liệu dùng chung cho các test khối của luồng nhập (X09):
`tests/test_import_blocks.py`, `tests/test_import_blocks_errors.py`, `tests/test_patch_rebuild.py`,
`tests/test_vault_tree.py`, `tests/test_vault_blocks.py`, `tests/test_api_sections.py`. Không mạng, không tài liệu thật.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from holocron_core.domain.blocks import Block, BlockFlag, TableCell
from holocron_core.domain.documents import (
    PageExtraction,
    PageGeometry,
)
from holocron_core.domain.repositories import AppStore
from holocron_core.domain.schemas import SourceKind
from holocron_core.infrastructure.document_readers.pdf_reader import GeometrySink
from holocron_core.infrastructure.lang_patterns import load_lang_patterns
from holocron_core.infrastructure.memory_store import IngestionStore
from holocron_core.infrastructure.storage import SQLiteStore
from holocron_core.infrastructure.storage.original_files import OriginalFileStore
from holocron_core.services.block_pipeline import (
    PAGE_STAGES,
    LayoutConfig,
    PageContext,
    default_layout_config,
)
from holocron_core.services.document_import import (
    ImportRequest,
    ImportRuntime,
    ReadJob,
    SynchronousImportQueue,
    accept_document,
    read_document,
)
from tests.block_samples import extraction_for, geo_line, geometry
from tests.document_import_support import pdf_bytes

LANG = load_lang_patterns()
NOW = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)
SUBJECT = "IT3070"
BODY = (
    "Bo dem chuong trinh luu dia chi lenh ke tiep can thuc hien trong bo xu ly moi lan nap lenh",
    "bo dem tang len theo do dai lenh va tro toi lenh ke tiep trong bo nho chuong trinh",
)


# --------------------------------------------------------------------------- #
# Bộ đọc giả có hình học và trang tự tạo
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SamplePage:
    """Một trang giả: `geometry` dựng tay; `send_geometry = False` ⇒ bộ đọc báo geometry None (vượt trần, X04)."""

    geometry: PageGeometry
    send_geometry: bool = True


def text_page(title: str, *, footer: str | None = None, extra: Sequence[str] = ()) -> SamplePage:
    """Trang lớp chữ: tiêu đề đậm cỡ 20, đoạn hai dòng, đoạn thứ hai từ các dòng `extra` (cách xa đoạn đầu), tuỳ chọn
    một dòng chân trang (`footer`)."""
    lines = [geo_line(title, 0.1, 0.08, size=20.0, font_id=1)]
    lines += [geo_line(text, 0.1, 0.2 + 0.03 * i) for i, text in enumerate(BODY)]
    lines += [geo_line(text, 0.1, 0.5 + 0.03 * i) for i, text in enumerate(extra)]
    if footer is not None:
        lines.append(geo_line(footer, 0.5, 0.95, size=9.0))
    return SamplePage(geometry(lines))


@dataclass
class GeoReader:
    """Bộ đọc giả thoả `PdfReader` VÀ `PageGeometrySource`: trang `i` là `pages[i - 1]`."""

    pages: list[SamplePage]
    reads: int = 0

    def probe(self, path: Path) -> int:
        assert path.is_file()
        return len(self.pages)

    def read_pages(
        self,
        path: Path,
        document_id: str,
        page_numbers: Sequence[int] | None = None,
        *,
        geometry_sink: GeometrySink | None = None,
    ) -> Iterator[PageExtraction]:
        self.reads += 1
        numbers = list(range(1, len(self.pages) + 1)) if page_numbers is None else list(page_numbers)
        for number in numbers:
            sample = self.pages[number - 1]
            if geometry_sink is not None:
                geometry_sink(number, sample.geometry if sample.send_geometry else None)
            yield extraction_for(sample.geometry, number, document_id=document_id)


@dataclass(frozen=True)
class EditStage:
    """Stage tiêm qua `LayoutConfig` (sau `reading_order`, không đổi id): áp `edit` lên khối của trang trong `pages`."""

    tag: str
    edit: Callable[[Block], Block]
    pages: frozenset[int] = frozenset({1})
    name: str = "test_edit"

    def fingerprint(self) -> dict[str, Any]:
        return {"name": self.name, "version": 1, "settings": {"tag": self.tag, "pages": sorted(self.pages)}}

    def run(self, context: PageContext, blocks: list[Block]) -> list[Block]:
        if context.page_number not in self.pages:
            return blocks
        return [self.edit(block) if block.type == "paragraph" else block for block in blocks]


def flagged(flag: BlockFlag) -> Callable[[Block], Block]:
    def edit(block: Block) -> Block:
        return Block.model_validate({**block.model_dump(), "flags": [*block.flags, flag]})

    return edit


def as_table(*flags: BlockFlag) -> Callable[[Block], Block]:
    """Khối đoạn thành `table` một ô (giữ tham chiếu, `crop_pending` vì có hộp) kèm `flags`."""

    def edit(block: Block) -> Block:
        cell = TableCell(text=block.text or "", line_refs=block.line_refs, row=0, col=0)
        data = {**block.model_dump(), "type": "table", "text": None, "cells": [[cell.model_dump()]]}
        return Block.model_validate({**data, "flags": [*block.flags, "crop_pending", *flags]})

    return edit


def as_figure(block: Block) -> Block:
    """Khối đoạn có hộp thành `figure` (chữ thành nhãn) mang `crop_pending`; khối không hộp giữ nguyên."""
    if block.bbox is None:
        return block
    data = {**block.model_dump(), "type": "figure", "text": None, "line_refs": []}
    return Block.model_validate(
        {**data, "label_line_refs": block.line_refs, "labels": [block.text], "flags": ["crop_pending"]}
    )


def layout_with(*stages: EditStage) -> LayoutConfig:
    return dataclasses.replace(default_layout_config(LANG), page_stages=(*PAGE_STAGES, *stages))


@pytest.fixture(params=["memory", "sqlite"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[AppStore]:
    if request.param == "memory":
        yield IngestionStore()
        return
    opened = SQLiteStore.open(tmp_path / "kho.sqlite3", crops_dir=tmp_path / "crops")
    try:
        yield opened
    finally:
        opened.close()


def runtime_for(tmp_path: Path, layout: LayoutConfig | None = None) -> ImportRuntime:
    runtime = ImportRuntime(files=OriginalFileStore(tmp_path / "files"), queue=SynchronousImportQueue())
    return runtime if layout is None else dataclasses.replace(runtime, layout=layout)


def import_doc(
    store: AppStore,
    runtime: ImportRuntime,
    reader: GeoReader,
    *,
    tag: str = "a",
    auto_confirm: bool = True,
    geometry: bool = True,
    source_kind: SourceKind = SourceKind.LECTURE_SLIDES,
) -> str:
    request = ImportRequest(
        content=pdf_bytes(tag),
        filename=f"{tag}.pdf",
        declared_type="application/pdf",
        subject_code=SUBJECT,
        source_kind=source_kind,
        chapter_code="ch2",
        auto_confirm=auto_confirm,
    )
    outcome = accept_document(
        request,
        store=store,
        runtime=runtime,
        reader=reader,
        now=NOW,
        vault=store,
        geometry_reader=reader if geometry else None,
    )
    return outcome.document.meta.doc_id


def pages_of(store: AppStore, document_id: str) -> list[PageExtraction]:
    return store.list_pages(document_id, PageExtraction)


def document_of(store: AppStore, document_id: str) -> Any:
    document = store.get_imported_document(document_id)
    assert document is not None
    return document


def finish_again(store: AppStore, document_id: str, reader: GeoReader, layout: LayoutConfig) -> None:
    """`_finish` lần nữa qua `read_document` (không còn trang `pending` ⇒ chỉ bước kết thúc), như khi khởi động lại."""
    read_document(
        ReadJob(
            document_id=document_id,
            path=Path("khong-dung.pdf"),
            store=store,
            reader=reader,
            vault=store,
            layout=layout,
            geometry_reader=reader,
        )
    )


def without_crop_fields(blocks: Sequence[Block]) -> list[dict[str, Any]]:
    """Khối bỏ các field phụ thuộc kết quả cắt ảnh (ADR-010 mục 15): `crop_ref` và cờ ảnh cắt."""
    out: list[dict[str, Any]] = []
    for block in blocks:
        data = block.model_dump()
        data["crop_ref"] = None
        data["flags"] = [flag for flag in block.flags if flag not in ("crop_pending", "crop_failed", "crop_capped")]
        out.append(data)
    return out


THREE = [text_page("1. Gioi thieu"), text_page("2. Kien truc"), text_page("3. Bo nho")]
SECOND = ("Doan thu hai mo ta thanh ghi trang thai cua bo xu ly va cac co bao loi",)
TWO_PARAGRAPHS = [text_page("1. Gioi thieu", extra=SECOND), *THREE[1:]]
