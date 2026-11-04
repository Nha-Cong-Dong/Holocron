"""Settle dựng Section, cắt chunk theo khối/mục và tập khối đủ điều kiện (SPECIFICATION §5.8, §5.15 "Tập khối đủ
điều kiện"; ADR-010 mục 4, 13, 16; X09).

Khối được seed bằng method của kho (`save_document_layout`) và bộ dựng công khai (`build_page_blocks`) hoặc dựng tay
từ geometry tự tạo; không có tài liệu môn học thật. Tài liệu `layout_version` null giữ đường `build_chunks`.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from holocron_core.domain.blocks import (
    BLOCK_TYPE_ORDER,
    LAYOUT_HASH_REGISTRY,
    LAYOUT_VERSION,
    Block,
    BlockFlag,
    BlockType,
    ExtractionLedger,
    LedgerBasisCount,
    LedgerTypeCount,
    Section,
    TableCell,
    WordRef,
)
from holocron_core.domain.documents import (
    GeoFont,
    GeoLine,
    GeoWord,
    PageExtraction,
    PageGeometry,
    PageRef,
    PageRoleEvidence,
)
from holocron_core.domain.repositories import (
    AppStore,
    DocumentLayout,
    ImportedDocument,
    PageLayout,
    VersionConflictError,
)
from holocron_core.domain.schemas import DocumentMeta, KnowledgeChunk, SourceKind, quote_in_text
from holocron_core.domain.vault import BlockDocumentText, ChunkBlock, ConfirmedPage
from holocron_core.infrastructure.lang_patterns import load_lang_patterns
from holocron_core.infrastructure.memory_store import IngestionStore
from holocron_core.infrastructure.storage import SQLiteStore
from holocron_core.routers.api import app
from holocron_core.services.block_pipeline import (
    PageContext,
    build_page_blocks,
    default_layout_config,
    default_layout_settings_sha256,
)
from holocron_core.services.document_import import (
    DocumentChapterMissingError,
    SynchronousImportQueue,
    pending_page,
    settle_outcome,
)
from holocron_core.services.section_tree import build_section_tree
from holocron_core.services.vault_chunking import (
    build_block_chunks,
    build_chunks,
    chunk_id_for,
    is_heading,
)
from tests.block_samples import BOLD, DOC_ID, REGULAR, extraction_for, geo_line, geometry
from tests.document_import_support import FakePdfReader, install, pdf_bytes

LANG = load_lang_patterns()
NOW = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)
MONO = GeoFont(
    font_name="Courier",
    bold=False,
    italic=False,
    monospace=True,
    bold_source=None,
    italic_source=None,
    monospace_source="name",
)
LONG = (
    "Bo dem chuong trinh luu dia chi lenh ke tiep can thuc hien trong bo xu ly. "
    "Moi lan nap lenh bo dem tang len theo do dai lenh."
)


@dataclass(frozen=True)
class Item:
    """Một dòng của trang tự tạo: `kind` là loại khối dựng tay (mỗi dòng một khối)."""

    kind: BlockType
    text: str
    size: float = 11.0
    bold: bool = False
    flags: tuple[BlockFlag, ...] = ()


def head(text: str) -> Item:
    return Item("heading", text, 20.0, True)


def para(text: str = LONG) -> Item:
    return Item("paragraph", text)


def page_with(number: int, items: Sequence[Item], *, doc_id: str = DOC_ID) -> tuple[PageGeometry, list[Block]]:
    """Geometry một dòng mỗi mục và khối dựng tay trỏ tới đúng các từ của dòng đó."""
    lines = [
        geo_line(it.text, 0.1, 0.05 + 0.05 * i, size=it.size, font_id=1 if it.bold else 0) for i, it in enumerate(items)
    ]
    geo = geometry(lines, fonts=[REGULAR, BOLD])
    blocks: list[Block] = []
    for i, (it, line) in enumerate(zip(items, geo.lines, strict=True)):
        refs = [WordRef(line_idx=i, word_idx=w) for w in range(len(line.words))]
        data: dict[str, Any] = {
            "block_id": "blk_" + hashlib.sha256(f"{doc_id}|{number}|{i}|{it.text}".encode()).hexdigest()[:16],
            "document_id": doc_id,
            "page_number": number,
            "type": it.kind,
            "bbox": line.bbox,
            "text": None if it.kind in ("table", "figure") else it.text,
            "line_refs": refs,
            "flags": list(it.flags),
            "source": "text_layer",
            "reading_order": i,
        }
        if it.kind == "heading":
            data["level"] = 1
        if it.kind == "table":
            data["cells"] = [[TableCell(text=it.text, line_refs=refs, row=0, col=0)]]
            data["flags"] = [*it.flags, "crop_pending"]
        blocks.append(Block.model_validate(data))
    return geo, blocks


def ledger_of(doc_id: str, number: int, blocks: Sequence[Block]) -> ExtractionLedger:
    counts = {kind: sum(1 for b in blocks if b.type == kind) for kind in BLOCK_TYPE_ORDER}
    total = sum(counts.values())
    return ExtractionLedger(
        document_id=doc_id,
        page_number=number,
        bases=[LedgerBasisCount(ledger_basis="pdfium", source_count=total, assigned_count=total)],
        by_type=[LedgerTypeCount(type=kind, count=count) for kind, count in counts.items() if count],
        balanced=True,
    )


def imported(pages: int, *, source_kind: SourceKind = SourceKind.LECTURE_SLIDES) -> ImportedDocument:
    return ImportedDocument(
        meta=DocumentMeta(
            doc_id=DOC_ID,
            subject_code="IT3070",
            title="Tai lieu tu tao",
            source_kind=source_kind,
            sha256=hashlib.sha256(DOC_ID.encode()).hexdigest(),
            page_count=pages,
            has_text_layer=True,
            uploaded_at=NOW,
        ),
        filename="mau.pdf",
        size_bytes=10,
        status="review",
        chapter_code="ch2",
        chapter_code_source="user",
        chapter_title="Chuong hai",
        chapter_title_source="user",
    )


@dataclass
class Page:
    """Trang để seed: `blocks = None` ⇒ trang không có khối (LAYOUT_FAILED/OCR, đi đường `build_chunks`)."""

    geometry: PageGeometry
    blocks: list[Block] | None
    confirmed: bool = True
    dropped: bool = False


def seed(
    store: AppStore,
    pages: Sequence[Page],
    *,
    layout_version: int = LAYOUT_VERSION,
    layout_sha: str | None = None,
    source_kind: SourceKind = SourceKind.LECTURE_SLIDES,
) -> ImportedDocument:
    created = store.create_imported_document(
        imported(len(pages), source_kind=source_kind), [pending_page(DOC_ID, n) for n in range(1, len(pages) + 1)]
    )
    layouts: list[PageLayout] = []
    for number, spec in enumerate(pages, start=1):
        page = extraction_for(spec.geometry, number, document_id=DOC_ID).model_copy(update={"version": 2})
        if spec.dropped:
            evidence = PageRoleEvidence(
                rule="cover_or_course_nav", related_pages=[PageRef(document_id=DOC_ID, page_number=1)]
            )
            page = page.model_copy(
                update={"page_role": "cover_or_course_nav", "role_evidence": evidence, "dropped": True}
            )
        elif spec.confirmed:
            page = page.model_copy(update={"confirmed": True, "confirmed_at": NOW, "confirmed_by": "user"})
        store.update_page(
            DOC_ID,
            number,
            page,
            status=page.status,
            confirmed=page.confirmed,
            expected_version=1,
            geometry=spec.geometry,
        )
        if spec.blocks is not None:
            layouts.append(
                PageLayout(page_number=number, blocks=spec.blocks, ledger=ledger_of(DOC_ID, number, spec.blocks))
            )
    sha = layout_sha or default_layout_settings_sha256(LANG)
    store.save_document_layout(
        DOC_ID, DocumentLayout(layout_version=layout_version, layout_settings_sha256=sha, pages=layouts)
    )
    return created


def simple(number: int, items: Sequence[Item], **options: bool) -> Page:
    geo, blocks = page_with(number, items)
    return Page(geo, blocks, **options)


def line_only(number: int, lines: Sequence[str]) -> Page:
    geo = geometry([geo_line(text, 0.1, 0.05 + 0.05 * i) for i, text in enumerate(lines)])
    return Page(geo, None)


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


def chunks_of(store: AppStore) -> list[KnowledgeChunk]:
    return store.list_chunks("IT3070")


def ordered(chunks: Sequence[KnowledgeChunk]) -> list[KnowledgeChunk]:
    """Chunk theo thứ tự ordinal: tìm lại ordinal bằng `chunk_id_for` (ordinal < số chunk)."""
    found: dict[int, KnowledgeChunk] = {}
    for chunk in chunks:
        ordinal = next(i for i in range(len(chunks)) if chunk_id_for(DOC_ID, i, chunk.raw_text) == chunk.chunk_id)
        found[ordinal] = chunk
    return [found[i] for i in sorted(found)]


def block_text(store: AppStore) -> dict[str, str | None]:
    return {block.block_id: block.text for block in store.list_blocks(DOC_ID)}


def settle(store: AppStore, *, by_user: bool = False) -> bool:
    return settle_outcome(store, store, DOC_ID, by_user=by_user).closed


# --------------------------------------------------------------------------- #
# Tập ứng viên và cắt chunk
# --------------------------------------------------------------------------- #


def test_unconfirmed_or_dropped_page_blocks_not_chunked(store: AppStore) -> None:
    cover = simple(1, [head("Bai giang mo dau"), para("Trang bia cua bai giang co ten mon hoc.")], dropped=True)
    body = simple(2, [head("1. Gioi thieu"), para()])
    seed(store, [cover, body])
    assert settle(store)
    chunked = {block_id for chunk in chunks_of(store) for block_id in chunk.block_ids}
    assert chunked and not chunked & {b.block_id for b in cover.blocks or []}
    assert {b.block_id for b in store.list_eligible_blocks(DOC_ID)} == {b.block_id for b in body.blocks or []}
    joined = "\n".join(chunk.raw_text for chunk in chunks_of(store))
    assert "Trang bia" not in joined

    # trang có chữ chưa xác nhận: tài liệu không chốt, không chunk nào được cắt
    other = IngestionStore()
    seed(other, [simple(1, [head("1. Mot"), para()]), simple(2, [para()], confirmed=False)])
    assert not settle(other, by_user=True)
    assert other.list_chunks("IT3070") == [] and other.list_eligible_blocks(DOC_ID) == []
    document = other.get_imported_document(DOC_ID)
    assert document is not None and document.status == "review"


def _code_page(number: int) -> tuple[PageGeometry, list[Block]]:
    """Trang có một đoạn chữ thường và một chương trình đơn cách (font MONO) — bộ dựng X06 sinh khối `code`."""
    program = ["start:", "    load a, 1", "        add a, b", "    store a, out", "end"]
    rows: list[GeoLine] = [geo_line("Chuong trinh mau sau day nap va cong hai so.", 0.1, 0.1)]
    for i, text in enumerate(program):
        y = 0.2 + i * 0.016
        words = [
            GeoWord(
                text=m.group(),
                bbox=(0.1 + m.start() * 0.008, y, 0.1 + m.end() * 0.008, y + 0.012),
                font_id=2,
                font_size_pt=10.0,
            )
            for m in re.finditer(r"\S+", text)
        ]
        bbox = (min(w.bbox[0] for w in words if w.bbox), y, max(w.bbox[2] for w in words if w.bbox), y + 0.012)
        rows.append(GeoLine(words=words, bbox=bbox, font_id=2, font_size_pt=10.0, extracted_line_indices=[]))
    geo = geometry(rows, fonts=[REGULAR, BOLD, MONO])
    built = build_page_blocks(
        document_id=DOC_ID, page_number=number, geometry=geo, extraction=extraction_for(geo, number), lang=LANG
    )
    assert not built.layout_failed
    return geo, built.blocks


def test_block_chunk_quote_is_substring(store: AppStore) -> None:
    geo, blocks = _code_page(1)
    assert any(block.type == "code" for block in blocks)
    seed(store, [Page(geo, blocks)])
    assert settle(store)
    stored = store.list_blocks(DOC_ID)
    assert any(block.type == "code" for block in stored)
    texts = block_text(store)
    chunks = chunks_of(store)
    assert chunks
    for chunk in chunks:
        assert quote_in_text(chunk.verbatim_quote, chunk.raw_text)
        assert chunk.raw_text == "\n\n".join(texts[block_id] or "" for block_id in chunk.block_ids)
    code_ids = {block.block_id for block in stored if block.type == "code"}
    assert code_ids <= {block_id for chunk in chunks for block_id in chunk.block_ids}


def test_too_short_block_flagged_and_not_chunked(store: AppStore) -> None:
    page = simple(1, [head("1. Ab"), head("2. Muc hai day du"), para("Ngan."), para()])
    seed(store, [page])
    assert settle(store)
    by_text = {block.text: block for block in store.list_blocks(DOC_ID)}
    short_heading = by_text["1. Ab"]
    assert short_heading.flags == ["too_short_for_chunk"]
    assert by_text["Ngan."].flags == []  # gộp với khối kề cùng Section
    chunked = {block_id for chunk in chunks_of(store) for block_id in chunk.block_ids}
    assert short_heading.block_id not in chunked
    assert by_text["Ngan."].block_id in chunked
    assert short_heading.block_id in {b.block_id for b in store.list_eligible_blocks(DOC_ID)}


def test_chunk_ordinal_order_mixed_with_build_chunks(store: AppStore) -> None:
    pages = [
        simple(1, [head("1. Mo dau"), para()]),
        line_only(2, ["Trang nay khong dung duoc khoi nen cat theo dong da xac nhan cua nguoi dung."]),
        simple(3, [head("2. Tiep theo"), para()]),
    ]
    seed(store, pages)
    assert settle(store)
    chunks = ordered(chunks_of(store))
    assert [chunk.page_number for chunk in chunks] == [1, 2, 3]
    assert [chunk.chunk_id for chunk in chunks] == [chunk_id_for(DOC_ID, i, c.raw_text) for i, c in enumerate(chunks)]
    line_chunk = chunks[1]
    assert (line_chunk.section_id, line_chunk.block_ids) == (None, [])
    assert line_chunk.raw_text.startswith("Trang nay khong dung")
    assert all(chunk.section_id is not None and chunk.block_ids for chunk in (chunks[0], chunks[2]))


def test_table_token_mismatch_keeps_table_and_not_eligible(store: AppStore) -> None:
    table = Item("table", "o bang", flags=("table_token_mismatch",))
    seed(store, [simple(1, [head("1. Bang du lieu"), table, para()])])
    assert settle(store)
    (stored_table,) = [block for block in store.list_blocks(DOC_ID) if block.type == "table"]
    assert "table_token_mismatch" in stored_table.flags
    eligible = {block.block_id for block in store.list_eligible_blocks(DOC_ID)}
    assert stored_table.block_id not in eligible and len(eligible) == 2
    assert stored_table.block_id not in {b for chunk in chunks_of(store) for b in chunk.block_ids}
    assert all(stored_table.block_id not in section.block_ids for section in store.list_sections(DOC_ID))


def test_late_dropped_cover_page_not_a_section(store: AppStore) -> None:
    seed(
        store,
        [
            simple(1, [head("Bia bai giang"), para("Trang bia co ten mon hoc va giang vien.")], dropped=True),
            simple(2, [head("1. Noi dung chinh"), para()]),
        ],
    )
    assert settle(store)
    sections = store.list_sections(DOC_ID)
    titles = [section.title for section in sections]
    assert "Bia bai giang" not in titles and "1. Noi dung chinh" in titles
    assert all(section.page_start >= 2 for section in sections)
    cover_ids = {b.block_id for b in store.list_blocks(DOC_ID, 1)}
    assert all(not cover_ids & set(section.block_ids) for section in sections)


class ConflictOnClose:
    """Kho bọc: `update_imported_document` (bước (5)) luôn lệch version; mọi lời gọi khác chuyển tới `inner`."""

    def __init__(self, inner: AppStore) -> None:
        self.inner = inner

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    def update_imported_document(self, document: ImportedDocument, expected_version: int) -> ImportedDocument:
        raise VersionConflictError("tài liệu", document.meta.doc_id, expected_version, expected_version + 1)


def test_settle_retry_reruns_steps_1_to_5_idempotent(store: AppStore) -> None:
    seed(store, [simple(1, [head("1. Mot"), para()]), simple(2, [head("2. Hai"), para(), para("Doan cuoi cung.")])])
    flaky: Any = ConflictOnClose(store)
    with pytest.raises(VersionConflictError):
        settle_outcome(flaky, flaky, DOC_ID)
    chunks = sorted(chunks_of(store), key=lambda c: c.chunk_id)
    sections = store.list_sections(DOC_ID)
    eligible = store.list_eligible_blocks(DOC_ID)
    assert chunks and sections and eligible
    document = store.get_imported_document(DOC_ID)
    assert document is not None and document.status == "review"
    assert settle(store)
    assert sorted(chunks_of(store), key=lambda c: c.chunk_id) == chunks
    assert store.list_sections(DOC_ID) == sections
    assert store.list_eligible_blocks(DOC_ID) == eligible
    closed = store.get_imported_document(DOC_ID)
    assert closed is not None and closed.status == "confirmed"


class Recorder:
    """Kho bọc ghi lại tên các method ghi khối/Vault được gọi."""

    WATCH = frozenset({"replace_sections", "refresh_eligible_blocks", "add_chunks", "update_block_flags"})

    def __init__(self, inner: AppStore) -> None:
        self.inner = inner
        self.calls: list[str] = []

    def __getattr__(self, name: str) -> Any:
        target = getattr(self.inner, name)
        if name not in self.WATCH:
            return target

        def record(*args: Any, **kwargs: Any) -> Any:
            self.calls.append(name)
            return target(*args, **kwargs)

        return record


@pytest.mark.parametrize("with_blocks", [True, False])
def test_settle_on_confirmed_document_is_noop(store: AppStore, with_blocks: bool) -> None:
    pages = [simple(1, [head("1. Mot"), para()])]
    if with_blocks:
        seed(store, pages)
    else:
        store.create_imported_document(imported(1), [pending_page(DOC_ID, 1)])
        page = extraction_for(pages[0].geometry, 1, document_id=DOC_ID).model_copy(
            update={"version": 2, "confirmed": True, "confirmed_at": NOW, "confirmed_by": "user"}
        )
        store.update_page(DOC_ID, 1, page, status="ok", confirmed=True, expected_version=1)
    assert settle(store)
    before = (sorted(c.chunk_id for c in chunks_of(store)), store.list_sections(DOC_ID), store.list_blocks(DOC_ID))
    recorder: Any = Recorder(store)
    outcome = settle_outcome(recorder, recorder, DOC_ID, by_user=True)
    assert not outcome.closed and outcome.document.status == "confirmed"
    assert recorder.calls == []
    after = (sorted(c.chunk_id for c in chunks_of(store)), store.list_sections(DOC_ID), store.list_blocks(DOC_ID))
    assert after == before


def test_layout_version_bump_does_not_rebuild_on_settle(store: AppStore) -> None:
    pages = [simple(1, [head("1. Mot"), para()]), simple(2, [para("Doan van o trang hai cua tai lieu.")])]
    seed(store, pages, layout_version=1, layout_sha=LAYOUT_HASH_REGISTRY[1])
    before = store.list_blocks(DOC_ID)
    assert settle(store)
    after = store.list_blocks(DOC_ID)
    assert [(b.block_id, b.text, b.line_refs, b.type) for b in after] == [
        (b.block_id, b.text, b.line_refs, b.type) for b in before
    ]
    document = store.get_imported_document(DOC_ID)
    assert document is not None
    assert (document.layout_version, document.layout_settings_sha256) == (1, LAYOUT_HASH_REGISTRY[1])
    assert LAYOUT_VERSION != 1


def test_settle_replaces_all_sections(store: AppStore) -> None:
    pages = [simple(1, [head("1. Mot"), para()]), simple(2, [head("2. Hai"), para()])]
    seed(store, pages)
    stale = Section(
        section_id="sec_" + "f" * 16,
        document_id=DOC_ID,
        parent_id=None,
        kind="chapter",
        level=0,
        title="Cu",
        ordinal=0,
        page_start=1,
        page_end=1,
    )
    store.replace_sections(DOC_ID, [stale])
    assert settle(store)
    contexts = [
        (PageContext(DOC_ID, n, p.geometry, extraction_for(p.geometry, n), LANG), p.blocks or [])
        for n, p in enumerate(pages, start=1)
    ]
    expected = build_section_tree(DOC_ID, "ch2", "Chuong hai", contexts, LANG).sections
    assert store.list_sections(DOC_ID) == sorted(expected, key=lambda s: (s.ordinal, s.section_id))
    assert stale.section_id not in {s.section_id for s in store.list_sections(DOC_ID)}
    headings = {b.block_id: b.level for b in store.list_blocks(DOC_ID) if b.type == "heading"}
    for section in expected:
        for block_id in section.heading_block_ids:
            if section.kind == "section":
                assert headings[block_id] == section.level


def test_chunks_never_span_two_sections_and_ids_are_deterministic(store: AppStore) -> None:
    pages = [
        simple(1, [head("1. Mot"), para(), head("1.1 Mot mot"), para("Doan ngan thu nhat."), para()]),
        simple(2, [head("2. Hai"), para()]),
    ]
    seed(store, pages)
    assert settle(store)
    section_of = {b: s.section_id for s in store.list_sections(DOC_ID) for b in s.block_ids}
    for chunk in chunks_of(store):
        assert chunk.section_id is not None
        assert {section_of[b] for b in chunk.block_ids} == {chunk.section_id}
    text = BlockDocumentText(
        doc_id=DOC_ID,
        subject_code="IT3070",
        chapter_code="ch2",
        blocks=[
            ChunkBlock(
                block_id=b.block_id,
                page_number=b.page_number,
                reading_order=b.reading_order,
                is_heading=b.type == "heading",
                text=b.text or "",
                section_id=section_of[b.block_id],
            )
            for b in store.list_blocks(DOC_ID)
        ],
        line_pages=[ConfirmedPage(page_number=3, lines=["Dong cua trang khong co khoi nao ca."])],
    )
    first, second = build_block_chunks(text), build_block_chunks(text)
    assert first == second
    assert [c.chunk_id for c in first.chunks] == [
        chunk_id_for(DOC_ID, i, c.raw_text) for i, c in enumerate(first.chunks)
    ]


def test_long_block_split_under_token_limit_and_quotes_hold() -> None:
    sentence = "Thanh ghi trang thai luu co dieu kien sau moi phep tinh so hoc. "
    long_text = (sentence * 120).strip()
    text = BlockDocumentText(
        doc_id=DOC_ID,
        subject_code="IT3070",
        chapter_code="ch2",
        blocks=[
            ChunkBlock(
                block_id="blk_" + "1" * 16,
                page_number=1,
                reading_order=0,
                is_heading=False,
                text=long_text,
                section_id="sec_" + "1" * 16,
            )
        ],
    )
    result = build_block_chunks(text)
    assert len(result.chunks) > 1
    assert all(chunk.token_count <= 450 for chunk in result.chunks)
    assert all(quote_in_text(chunk.verbatim_quote, chunk.raw_text) for chunk in result.chunks)
    assert all(chunk.block_ids == ["blk_" + "1" * 16] for chunk in result.chunks)


def test_past_exam_with_blocks_has_no_chunks_and_no_eligible_blocks(store: AppStore) -> None:
    seed(store, [simple(1, [head("Cau 1"), para()])], source_kind=SourceKind.PAST_EXAM)
    assert settle(store)
    assert chunks_of(store) == [] and store.list_eligible_blocks(DOC_ID) == []


def test_legacy_document_without_layout_keeps_build_chunks(store: AppStore) -> None:
    store.create_imported_document(imported(1), [pending_page(DOC_ID, 1)])
    geo = geometry([geo_line("Dong van ban cua tai lieu cu nhap truoc khi co khoi.", 0.1, 0.1)])
    page = extraction_for(geo, 1, document_id=DOC_ID).model_copy(
        update={"version": 2, "confirmed": True, "confirmed_at": NOW, "confirmed_by": "user"}
    )
    store.update_page(DOC_ID, 1, page, status="ok", confirmed=True, expected_version=1)
    assert settle(store)
    (chunk,) = chunks_of(store)
    assert (chunk.section_id, chunk.block_ids) == (None, [])
    assert store.list_sections(DOC_ID) == []


def test_build_chunks_heading_keywords_from_parameter() -> None:
    assert is_heading("Chương 3 Bộ nhớ")
    assert not is_heading("Kapitel 3 Speicher")
    assert is_heading("Kapitel 3 Speicher", ["kapitel"])
    assert not is_heading("Chương 3 Bộ nhớ", ["kapitel"])
    assert is_heading("3.2 Bo nho", [])
    from holocron_core.domain.vault import ConfirmedDocumentText

    text = ConfirmedDocumentText(
        doc_id=DOC_ID,
        subject_code="IT3070",
        chapter_code="ch2",
        pages=[ConfirmedPage(page_number=1, lines=["Kapitel 1", "Erster Absatz mit Text.", "Kapitel 2", "Zweiter."])],
    )
    assert len(build_chunks(text, ["kapitel"])) == 2
    assert len(build_chunks(text)) == 1


def test_layout_config_is_one_source_for_hash_and_page_numbers() -> None:
    config = default_layout_config(LANG)
    assert config.settings_sha256() == default_layout_settings_sha256(LANG) == LAYOUT_HASH_REGISTRY[LAYOUT_VERSION]
    assert config.page_number_settings() is config.page_numbers.settings
    geo, _ = _code_page(1)
    built = config.build_page_blocks(document_id=DOC_ID, page_number=1, geometry=geo, extraction=extraction_for(geo))
    assert built == build_page_blocks(
        document_id=DOC_ID, page_number=1, geometry=geo, extraction=extraction_for(geo), lang=LANG
    )


# --------------------------------------------------------------------------- #
# PATCH …/chapter (TestClient)
# --------------------------------------------------------------------------- #


def test_patch_chapter_keeps_section_id(tmp_path: Path) -> None:
    store = IngestionStore()
    env = install(store, tmp_path / "files", SynchronousImportQueue(), FakePdfReader(page_count=1))
    try:
        content = pdf_bytes("x09-section")
        response = env.post(content, chapter_code="ch2")
        assert response.status_code == 202, response.text
        doc_id = "doc_" + hashlib.sha256(content).hexdigest()[:16]
        root = Section(
            section_id="sec_" + hashlib.sha256(doc_id.encode()).hexdigest()[:16],
            document_id=doc_id,
            parent_id=None,
            kind="chapter",
            level=0,
            chapter_code="ch2",
            title="Ten cu",
            ordinal=0,
            page_start=1,
            page_end=1,
        )
        store.replace_sections(doc_id, [root])
        patched = env.client.patch(
            f"/v1/documents/{doc_id}/chapter",
            json={"editor_id": "local_user", "chapter_title": "  Ten   moi  ", "chapter_code": "ch5"},
        )
        assert patched.status_code == 200, patched.text
        (stored,) = store.list_sections(doc_id)
        assert stored.section_id == root.section_id
        assert (stored.title, stored.chapter_code) == ("Ten moi", "ch5")
        document = store.get_imported_document(doc_id)
        assert document is not None and (document.chapter_title, document.chapter_code) == ("Ten moi", "ch5")
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# Bước (1)–(3) của settle: biên của tập ứng viên và cắt chunk
# --------------------------------------------------------------------------- #


def test_past_exam_line_page_not_chunked(store: AppStore) -> None:
    pages = [
        simple(1, [head("Cau 1"), para()]),
        line_only(2, ["Trang de thi nay khong dung duoc khoi nen co dong chu da xac nhan."]),
    ]
    seed(store, pages, source_kind=SourceKind.PAST_EXAM)
    assert settle(store)
    assert chunks_of(store) == []


def _drop(store: AppStore, number: int) -> None:
    current = store.get_page(DOC_ID, number, PageExtraction)
    assert current is not None
    evidence = PageRoleEvidence(rule="cover_or_course_nav", related_pages=[PageRef(document_id=DOC_ID, page_number=1)])
    dropped = current.model_copy(
        update={
            "version": current.version + 1,
            "confirmed": False,
            "confirmed_at": None,
            "confirmed_by": None,
            "page_role": "cover_or_course_nav",
            "role_evidence": evidence,
            "dropped": True,
        }
    )
    store.update_page(DOC_ID, number, dropped, status=dropped.status, confirmed=False, expected_version=current.version)


def test_settle_retry_keeps_existing_chunks_when_candidates_change(store: AppStore) -> None:
    seed(store, [simple(1, [head("1. Mot"), para()]), simple(2, [para("Doan tiep theo o trang hai cua muc mot.")])])
    flaky: Any = ConflictOnClose(store)
    with pytest.raises(VersionConflictError):
        settle_outcome(flaky, flaky, DOC_ID)
    chunks = sorted(chunks_of(store), key=lambda c: c.chunk_id)
    assert any(chunk.page_end == 2 for chunk in chunks)
    flags = {block.block_id: block.flags for block in store.list_blocks(DOC_ID)}
    _drop(store, 2)  # tập ứng viên đổi trước lần gọi lại
    assert settle(store)
    assert sorted(chunks_of(store), key=lambda c: c.chunk_id) == chunks  # Vault đã có chunk ⇒ không cắt lại
    assert {block.block_id: block.flags for block in store.list_blocks(DOC_ID)} == flags


def test_heading_repeated_on_three_pages_chunked_once(store: AppStore) -> None:
    pages = [
        simple(1, [head("Lap lich"), para("Doan thu nhat cua muc lap lich.")]),
        simple(2, [head("Lap lich"), para("Doan thu hai cua muc lap lich.")]),
        simple(3, [head("Lap lich"), para("Doan thu ba cua muc lap lich.")]),
    ]
    seed(store, pages)
    assert settle(store)
    headings = [b.block_id for b in store.list_blocks(DOC_ID) if b.type == "heading"]
    (repeated,) = [s for s in store.list_sections(DOC_ID) if len(s.heading_block_ids) == 3]
    assert repeated.heading_block_ids == headings
    chunked = [block_id for chunk in chunks_of(store) for block_id in chunk.block_ids]
    assert [block_id for block_id in chunked if block_id in headings] == headings[:1]
    joined = "\n".join(chunk.raw_text for chunk in chunks_of(store))
    assert joined.count("Lap lich\n") == 1


def test_unconfirmed_empty_page_splits_line_page_runs(store: AppStore) -> None:
    pages = [
        line_only(1, ["Dong chu cua trang mot khong co khoi nao."]),
        Page(geometry([]), None, confirmed=False),
        line_only(3, ["Dong chu cua trang ba khong co khoi nao."]),
    ]
    seed(store, pages)
    assert settle(store)
    assert sorted((c.page_number, c.page_end) for c in chunks_of(store)) == [(1, None), (3, None)]


def test_figure_candidate_not_in_chunks(store: AppStore) -> None:
    geo, blocks = page_with(1, [head("1. Hinh minh hoa"), para()])
    figure = Block.model_validate(
        {
            "block_id": "blk_" + "f" * 16,
            "document_id": DOC_ID,
            "page_number": 1,
            "type": "figure",
            "bbox": (0.1, 0.5, 0.9, 0.9),
            "text": None,
            "line_refs": [],
            "flags": ["crop_pending"],
            "source": "text_layer",
            "reading_order": 2,
        }
    )
    seed(store, [Page(geo, [*blocks, figure])])
    assert settle(store)
    assert figure.block_id in {b.block_id for b in store.list_eligible_blocks(DOC_ID)}
    chunked = {block_id for chunk in chunks_of(store) for block_id in chunk.block_ids}
    assert chunked == {b.block_id for b in blocks}


def test_page_with_only_non_candidate_blocks_not_line_cut(store: AppStore) -> None:
    margin = Item("header_footer", "Le trang cua tai lieu mau")
    seed(store, [simple(1, [margin]), simple(2, [head("1. Mot"), para()])])
    assert settle(store)
    assert all(chunk.page_number == 2 for chunk in chunks_of(store))
    assert "Le trang" not in "\n".join(chunk.raw_text for chunk in chunks_of(store))


def test_settle_without_chapter_code_raises_chapter_missing(store: AppStore) -> None:
    created = seed(store, [simple(1, [head("1. Mot"), para()])])
    store.update_imported_document(
        created.model_copy(update={"chapter_code": None, "chapter_code_source": None}), created.version
    )
    with pytest.raises(DocumentChapterMissingError):
        settle(store)
    document = store.get_imported_document(DOC_ID)
    assert document is not None and document.status == "review"
    assert chunks_of(store) == []


# --------------------------------------------------------------------------- #
# Bảo toàn sau `PATCH …/lines` (X09, đường nhập thật của service)
# --------------------------------------------------------------------------- #


def test_ledger_patched_page_uses_confirmed_lines(store: AppStore, tmp_path: Path) -> None:
    """Trang đã sửa dòng: ledger dùng basis `confirmed_lines` với `source_count` = đơn vị của MỌI `ExtractedLine` của
    trang (đếm độc lập, không từ khối) và cân; sau khi xác nhận, chunk chứa chữ đã sửa, không còn chữ cũ."""
    from holocron_core.domain.documents import DocumentConfirmRequest, LineEdit, PageLinesPatch
    from holocron_core.services.document_import import confirm_document, edit_page_lines
    from holocron_core.services.extraction_ledger import nonws_units
    from tests.import_blocks_support import THREE, GeoReader, import_doc, pages_of, runtime_for, text_page

    runtime = runtime_for(tmp_path)
    pages = [text_page("1. Gioi thieu", extra=("Dong rieng cua trang mot se duoc sua",)), *THREE[1:]]
    doc_id = import_doc(store, runtime, GeoReader(pages), auto_confirm=False)
    page = pages_of(store, doc_id)[0]
    old = page.lines[3].text
    patch = PageLinesPatch(
        editor_id="u",
        expected_version=page.version,
        edits=[LineEdit(line_index=3, text="Bo dem chuong trinh da duoc nguoi dung sua lai")],
        appended_lines=["Dong go them cuoi trang"],
    )
    edited = edit_page_lines(store, store, runtime, doc_id, 1, patch, now=NOW)
    [ledger] = [item for item in store.list_extraction_ledgers(doc_id) if item.page_number == 1]
    bases = {basis.ledger_basis: basis for basis in ledger.bases}
    assert "confirmed_lines" in bases and ledger.balanced
    assert bases["confirmed_lines"].source_count == sum(nonws_units(line.text) for line in edited.lines)
    edited_blocks = [block for block in store.list_blocks(doc_id, 1) if "human_edited" in block.flags]
    assert edited_blocks
    confirm_document(store, store, runtime, doc_id, DocumentConfirmRequest(editor_id="u"), now=NOW)
    joined = "\n".join(chunk.raw_text for chunk in store.list_chunks("IT3070"))
    assert "da duoc nguoi dung sua lai" in joined and "Dong go them cuoi trang" in joined
    assert old not in joined
