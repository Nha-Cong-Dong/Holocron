"""Kho khối, mục, ledger, ảnh cắt và thế hệ nhập ở CẢ hai kho (SPECIFICATION §5.13, §5.15; ADR-010 mục 13, 17, 19).

Chỉ dùng API công khai của `BlockRepository`/`DocumentRepository`. Khối, ledger, Section và PNG đều tự tạo trong test.
Các hàm dựng dữ liệu (không tiền tố `test_`) được `tests/test_crop_store.py` dùng lại.
"""

from __future__ import annotations

import binascii
import hashlib
import struct
import unicodedata
import zlib
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from holocron_core.domain.blocks import (
    BLOCK_TYPE_ORDER,
    Block,
    BlockFlag,
    BlockType,
    CropImage,
    ExtractionLedger,
    LedgerBasisCount,
    LedgerTypeCount,
    Section,
    TableCell,
    WordRef,
)
from holocron_core.domain.documents import PageExtraction, PageRef, PageRoleEvidence
from holocron_core.domain.repositories import (
    AppStore,
    BlockFlagUpdate,
    DocumentLayout,
    ImportedDocument,
    PageLayout,
    PageRewrite,
    RecordNotFoundError,
    StructureProposalRecord,
    VersionConflictError,
)
from holocron_core.domain.schemas import DocumentMeta, SourceKind
from holocron_core.infrastructure.memory_store import IngestionStore
from holocron_core.infrastructure.storage import SQLiteStore
from holocron_core.services.document_import import pending_page
from tests.block_samples import extraction_for, geo_line, geometry

KINDS = ("memory", "sqlite")
DOC = "doc_00000000000000c1"
OTHER = "doc_00000000000000c2"
NOW = datetime(2026, 11, 3, 8, 0, tzinfo=UTC)
LAYOUT_SHA = "1" * 64


def open_kind(kind: str, tmp_path: Path, *, crops: bool = True) -> AppStore:
    if kind == "memory":
        return IngestionStore()
    return SQLiteStore.open(tmp_path / "kho.sqlite3", crops_dir=(tmp_path / "crops") if crops else None)


def close(store: AppStore) -> None:
    if isinstance(store, SQLiteStore):
        store.close()


@pytest.fixture(params=KINDS)
def store(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[AppStore]:
    opened = open_kind(str(request.param), tmp_path)
    try:
        yield opened
    finally:
        close(opened)


def imported(
    doc_id: str = DOC, *, pages: int = 2, source_kind: SourceKind = SourceKind.LECTURE_SLIDES
) -> ImportedDocument:
    return ImportedDocument(
        meta=DocumentMeta(
            doc_id=doc_id,
            subject_code="IT3070",
            title="Tài liệu tự tạo",
            source_kind=source_kind,
            sha256=hashlib.sha256(doc_id.encode()).hexdigest(),
            page_count=pages,
            has_text_layer=True,
            uploaded_at=NOW,
        ),
        filename="mau.pdf",
        size_bytes=10,
        status="review",
        chapter_code="ch2",
        chapter_code_source="user",
        chapter_title="Chương hai",
        chapter_title_source="user",
    )


def page(
    doc_id: str, number: int, *, confirmed: bool = True, status: str = "ok", dropped: bool = False
) -> PageExtraction:
    """Trang lớp chữ một dòng; `dropped` ⇒ trang bìa bỏ mềm (chưa xác nhận); `failed` ⇒ trang lỗi."""
    base = extraction_for(geometry([geo_line(f"Dong trang {number}", 0.1, 0.2)]), number, document_id=doc_id)
    if status == "failed":
        return PageExtraction.model_validate(
            {
                "document_id": doc_id,
                "page_number": number,
                "status": "failed",
                "tier_used": None,
                "text_score": None,
                "failure_reason": "TOO_MANY_LINES",
                "cross_check": {"status": "not_applicable", "secondary_engine": None},
            }
        )
    if dropped:
        evidence = PageRoleEvidence(
            rule="cover_or_course_nav", related_pages=[PageRef(document_id=doc_id, page_number=1)]
        )
        return base.model_copy(update={"page_role": "cover_or_course_nav", "role_evidence": evidence, "dropped": True})
    if confirmed:
        return base.model_copy(update={"confirmed": True, "confirmed_at": NOW, "confirmed_by": "user"})
    return base


def seed(
    store: AppStore,
    doc_id: str = DOC,
    *,
    pages: int = 2,
    source_kind: SourceKind = SourceKind.LECTURE_SLIDES,
    page_state: dict[int, dict[str, object]] | None = None,
) -> ImportedDocument:
    """Tạo tài liệu nhập rồi ghi từng trang (mặc định `ok`, đã xác nhận); `page_state[n]` = tham số của `page`."""
    created = store.create_imported_document(
        imported(doc_id, pages=pages, source_kind=source_kind), [pending_page(doc_id, n) for n in range(1, pages + 1)]
    )
    for number in range(1, pages + 1):
        options = (page_state or {}).get(number, {})
        written = page(
            doc_id,
            number,
            confirmed=bool(options.get("confirmed", True)),
            status=str(options.get("status", "ok")),
            dropped=bool(options.get("dropped", False)),
        ).model_copy(update={"version": 2})
        store.update_page(
            doc_id, number, written, status=written.status, confirmed=written.confirmed, expected_version=1
        )
    return created


def block_id(*parts: object) -> str:
    return "blk_" + hashlib.sha256(repr(parts).encode()).hexdigest()[:16]


def make_block(
    doc_id: str,
    page_number: int,
    order: int,
    kind: BlockType = "paragraph",
    *,
    text: str = "Noi dung khoi tu tao",
    flags: list[BlockFlag] | None = None,
    bbox: tuple[float, float, float, float] | None = (0.1, 0.1, 0.9, 0.2),
    key: str = "",
) -> Block:
    data: dict[str, object] = {
        "block_id": block_id(doc_id, page_number, order, kind, key),
        "document_id": doc_id,
        "page_number": page_number,
        "type": kind,
        "bbox": bbox,
        "text": None if kind in ("table", "figure") else text,
        "line_refs": [] if kind == "figure" else [WordRef(line_idx=order, word_idx=0)],
        "flags": list(flags or []),
        "source": "text_layer",
        "reading_order": order,
    }
    if kind == "heading":
        data["level"] = 1
    if kind == "list_item":
        data["list_depth"] = 0
    if kind == "table":
        data["cells"] = [[TableCell(text="o", line_refs=[WordRef(line_idx=order, word_idx=0)], row=0, col=0)]]
    if kind in ("table", "figure", "formula") and bbox is not None and "crop_pending" not in (flags or []):
        data["flags"] = [*list(flags or []), "crop_pending"]
    return Block.model_validate(data)


def ledger_for(doc_id: str, page_number: int, blocks: list[Block]) -> ExtractionLedger:
    counts: dict[BlockType, int] = {}
    for block in blocks:
        counts[block.type] = counts.get(block.type, 0) + 1
    total = sum(counts.values())
    order = [kind for kind in BLOCK_TYPE_ORDER if kind in counts]
    return ExtractionLedger(
        document_id=doc_id,
        page_number=page_number,
        bases=[LedgerBasisCount(ledger_basis="pdfium", source_count=total, assigned_count=total)],
        by_type=[LedgerTypeCount(type=kind, count=counts[kind]) for kind in order],
        balanced=True,
    )


def layout(
    pages: dict[int, list[Block]],
    *,
    doc_id: str = DOC,
    after_close: bool | None = None,
    rewrites: list[PageRewrite] | None = None,
) -> DocumentLayout:
    return DocumentLayout(
        layout_version=2,
        layout_settings_sha256=LAYOUT_SHA,
        blocks_after_close=after_close,
        pages=[PageLayout(page_number=n, blocks=b, ledger=ledger_for(doc_id, n, b)) for n, b in sorted(pages.items())],
        page_rewrites=rewrites or [],
    )


def tiny_png(seed: int) -> bytes:
    """PNG 1×1 hợp lệ tự tạo; `seed` đổi màu điểm ảnh (đổi byte, đổi sha256)."""

    def chunk(tag: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", binascii.crc32(tag + body) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    pixel = bytes([0, seed % 256, (seed * 7) % 256, (seed * 13) % 256])
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(pixel)) + chunk(b"IEND", b"")


def crop_of(block: Block, png: bytes) -> CropImage:
    assert block.bbox is not None
    return CropImage(
        document_id=block.document_id,
        block_id=block.block_id,
        crop_ref=hashlib.sha256(png).hexdigest(),
        page_number=block.page_number,
        bbox=block.bbox,
        width_px=1,
        height_px=1,
        size_bytes=len(png),
    )


def section(
    doc_id: str,
    ordinal: int,
    *,
    parent: str | None,
    level: int,
    title: str | None,
    heading: list[str] | None = None,
    blocks: list[str] | None = None,
    chapter_code: str | None = None,
) -> Section:
    return Section(
        section_id="sec_" + hashlib.sha256(f"{doc_id}{ordinal}".encode()).hexdigest()[:16],
        document_id=doc_id,
        parent_id=parent,
        kind="chapter" if parent is None else "section",
        level=level,
        chapter_code=chapter_code,
        title=title,
        heading_block_ids=heading or [],
        block_ids=blocks or [],
        ordinal=ordinal,
        page_start=1,
        page_end=2,
        flags=[] if title else ["untitled"],
    )


# --------------------------------------------------------------------------- #
# Thế hệ nhập
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("kind", KINDS)
def test_delete_then_reimport_increments_generation(kind: str, tmp_path: Path) -> None:
    store = open_kind(kind, tmp_path)
    try:
        assert seed(store).import_generation == 1
        stored = store.get_imported_document(DOC)
        assert stored is not None and stored.import_generation == 1
        assert seed(store, OTHER).import_generation == 1  # bộ đếm theo từng document_id
        assert store.delete_document(DOC)
        assert seed(store).import_generation == 2
        assert store.revert_imported_document(DOC, None)
        again = seed(store)
        assert again.import_generation == 3
        reread = store.get_imported_document(DOC)
        assert reread is not None and reread.import_generation == 3
        assert sorted(d.import_generation for d in store.list_imported_documents()) == [1, 3]
    finally:
        close(store)


def test_sqlite_generation_survives_reopen(tmp_path: Path) -> None:
    first = open_kind("sqlite", tmp_path)
    try:
        seed(first)
        first.delete_document(DOC)
    finally:
        close(first)
    second = open_kind("sqlite", tmp_path)
    try:
        assert seed(second).import_generation == 2
    finally:
        close(second)


# --------------------------------------------------------------------------- #
# Khối, ledger, hình học
# --------------------------------------------------------------------------- #


def test_blocks_and_ledgers_round_trip(store: AppStore) -> None:
    seed(store)
    page1 = [make_block(DOC, 1, 1, text="Doan hai"), make_block(DOC, 1, 0, "heading", text="Tieu de")]
    page2 = [make_block(DOC, 2, 0), make_block(DOC, 2, 1, "figure")]
    store.save_document_layout(DOC, layout({1: page1, 2: page2}))
    assert store.list_blocks(DOC) == [page1[1], page1[0], *page2]
    assert store.list_blocks(DOC, 2) == page2
    assert store.list_extraction_ledgers(DOC) == [ledger_for(DOC, 1, page1), ledger_for(DOC, 2, page2)]
    document = store.get_imported_document(DOC)
    assert document is not None
    assert (document.layout_version, document.layout_settings_sha256, document.blocks_after_close) == (
        2,
        LAYOUT_SHA,
        False,
    )
    assert document.version == 1  # lưu khối không tăng version của tài liệu
    assert store.list_eligible_blocks(DOC) == []  # khối mới luôn eligible = 0

    replaced = [make_block(DOC, 1, 0, key="moi")]
    store.save_document_layout(DOC, layout({1: replaced}, after_close=True))
    assert store.list_blocks(DOC) == [*replaced, *page2]
    flagged = store.get_imported_document(DOC)
    assert flagged is not None and flagged.blocks_after_close
    store.save_document_layout(DOC, layout({1: replaced}))  # None ⇒ giữ cờ đang lưu
    kept = store.get_imported_document(DOC)
    assert kept is not None and kept.blocks_after_close


def test_save_layout_page_version_conflict_writes_nothing(store: AppStore) -> None:
    seed(store)
    before = [make_block(DOC, 1, 0)]
    store.save_document_layout(DOC, layout({1: before}))
    page2 = store.get_page(DOC, 2, PageExtraction)
    assert page2 is not None and page2.version == 2
    stale = PageRewrite(page=page2.model_copy(update={"confirmed": False, "confirmed_by": None}), expected_version=1)
    attempt = layout({1: [make_block(DOC, 1, 0, key="khac")], 2: [make_block(DOC, 2, 0)]}, rewrites=[stale])
    attempt = attempt.model_copy(update={"layout_version": 3, "layout_settings_sha256": "2" * 64})
    with pytest.raises(VersionConflictError):
        store.save_document_layout(DOC, attempt)
    assert store.list_blocks(DOC) == before
    assert [ledger.page_number for ledger in store.list_extraction_ledgers(DOC)] == [1]
    document = store.get_imported_document(DOC)
    assert document is not None and (document.layout_version, document.layout_settings_sha256) == (2, LAYOUT_SHA)
    unchanged = store.get_page(DOC, 2, PageExtraction)
    assert unchanged is not None and unchanged.confirmed and unchanged.version == 2
    assert [summary.confirmed for summary in store.list_page_summaries(DOC)] == [True, True]


def test_save_layout_rewrites_pages_in_same_transaction(store: AppStore) -> None:
    seed(store)
    page2 = store.get_page(DOC, 2, PageExtraction)
    assert page2 is not None
    undone = page2.model_copy(update={"confirmed": False, "confirmed_at": None, "confirmed_by": None, "version": 3})
    store.save_document_layout(
        DOC, layout({2: [make_block(DOC, 2, 0)]}, rewrites=[PageRewrite(page=undone, expected_version=2)])
    )
    reread = store.get_page(DOC, 2, PageExtraction)
    assert reread == undone
    assert [summary.confirmed for summary in store.list_page_summaries(DOC)] == [True, False]
    with pytest.raises(VersionConflictError):
        store.save_document_layout(DOC, layout({}, rewrites=[PageRewrite(page=undone, expected_version=2)]))


def test_save_layout_missing_document_or_page(store: AppStore) -> None:
    with pytest.raises(RecordNotFoundError):
        store.save_document_layout(DOC, layout({1: [make_block(DOC, 1, 0)]}))
    seed(store)
    with pytest.raises(RecordNotFoundError):
        store.save_document_layout(DOC, layout({5: [make_block(DOC, 5, 0)]}))
    assert store.list_blocks(DOC) == []


def test_page_geometry_written_with_page_and_kept_when_none(store: AppStore) -> None:
    store.create_imported_document(imported(pages=1), [pending_page(DOC, 1)])
    geo = geometry([geo_line("Mot dong", 0.1, 0.2)])
    first = page(DOC, 1, confirmed=False).model_copy(update={"version": 2})
    store.update_page(DOC, 1, first, status="ok", confirmed=False, expected_version=1, geometry=geo)
    assert store.get_page_geometry(DOC, 1) == geo
    second = first.model_copy(update={"version": 3})
    store.update_page(DOC, 1, second, status="ok", confirmed=False, expected_version=2)
    assert store.get_page_geometry(DOC, 1) == geo
    with pytest.raises(VersionConflictError):
        store.update_page(DOC, 1, second, status="ok", confirmed=False, expected_version=2, geometry=None)
    assert store.get_page_geometry(DOC, 9) is None


def test_update_imported_document_keeps_stored_layout(store: AppStore) -> None:
    stale = seed(store)
    store.save_document_layout(DOC, layout({1: [make_block(DOC, 1, 0)]}, after_close=True))
    updated = store.update_imported_document(stale.model_copy(update={"status": "confirmed"}), stale.version)
    assert (updated.layout_version, updated.blocks_after_close, updated.import_generation) == (2, True, 1)
    reread = store.get_imported_document(DOC)
    assert reread is not None
    assert (reread.status, reread.layout_version, reread.layout_settings_sha256) == ("confirmed", 2, LAYOUT_SHA)


def test_update_block_flags_is_atomic(store: AppStore) -> None:
    seed(store)
    short = make_block(DOC, 1, 0, text="ngan")
    other = make_block(DOC, 1, 1)
    store.save_document_layout(DOC, layout({1: [short, other]}))
    written = store.update_block_flags(DOC, [BlockFlagUpdate(block_id=short.block_id, add=["too_short_for_chunk"])])
    assert written[0].flags == ["too_short_for_chunk"]
    with pytest.raises(RecordNotFoundError):
        store.update_block_flags(
            DOC,
            [
                BlockFlagUpdate(block_id=other.block_id, add=["human_edited"]),
                BlockFlagUpdate(block_id=block_id("khong co"), add=["human_edited"]),
            ],
        )
    assert [b.flags for b in store.list_blocks(DOC)] == [["too_short_for_chunk"], []]
    store.update_block_flags(DOC, [BlockFlagUpdate(block_id=short.block_id, remove=["too_short_for_chunk"])])
    assert store.list_blocks(DOC)[0].flags == []


def test_structure_proposals_round_trip(store: AppStore) -> None:
    seed(store)
    record = StructureProposalRecord(
        document_id=DOC,
        page_number=2,
        input_sha256="3" * 64,
        model="mo-hinh-cuc-bo",
        prompt_version="v1",
        status="applied",
        ops_json='[{"op": "set_level"}]',
        created_at=NOW,
    )
    store.save_structure_proposal(record)
    store.save_structure_proposal(record.model_copy(update={"page_number": 1, "status": "rejected"}))
    assert [r.page_number for r in store.list_structure_proposals(DOC)] == [1, 2]
    assert store.list_structure_proposals(DOC)[1] == record
    with pytest.raises(RecordNotFoundError):
        store.save_structure_proposal(record.model_copy(update={"page_number": 9}))


# --------------------------------------------------------------------------- #
# Section
# --------------------------------------------------------------------------- #


def test_replace_sections_replaces_all_and_syncs_heading_level(store: AppStore) -> None:
    seed(store)
    head = make_block(DOC, 1, 0, "heading", text="Muc mot")
    body = make_block(DOC, 1, 1)
    store.save_document_layout(DOC, layout({1: [head, body]}))
    root = section(DOC, 0, parent=None, level=0, title="Chương hai", chapter_code="ch2")
    old_child = section(DOC, 1, parent=root.section_id, level=3, title="Muc mot", heading=[head.block_id])
    store.replace_sections(DOC, [root, old_child])
    assert store.list_blocks(DOC)[0].level == 3
    new_child = section(DOC, 2, parent=root.section_id, level=2, title="Muc mot", heading=[head.block_id])
    store.replace_sections(DOC, [new_child, root])
    assert store.list_sections(DOC) == [root, new_child]
    assert store.list_blocks(DOC)[0].level == 2
    with pytest.raises(ValueError, match="heading"):
        store.replace_sections(
            DOC, [root, section(DOC, 3, parent=root.section_id, level=1, title="X", heading=[body.block_id])]
        )
    assert store.list_sections(DOC) == [root, new_child]
    with pytest.raises(ValueError):
        store.replace_sections(DOC, [section(OTHER, 0, parent=None, level=0, title="T")])


def test_root_section_title_update_keeps_section_id(store: AppStore) -> None:
    document = seed(store)
    root = section(DOC, 0, parent=None, level=0, title="Chương hai", chapter_code="ch2")
    store.replace_sections(DOC, [root])
    patched = document.model_copy(update={"chapter_title": "  Tên   mớí ", "chapter_code": "ch3"})
    updated = store.update_imported_document_and_root_section(patched, document.version)
    assert updated.version == document.version + 1
    (stored,) = store.list_sections(DOC)
    assert stored.section_id == root.section_id
    assert (stored.title, stored.chapter_code, stored.flags) == (unicodedata.normalize("NFC", "Tên mớí"), "ch3", [])
    untitled = updated.model_copy(update={"chapter_title": None, "chapter_title_source": None})
    store.update_imported_document_and_root_section(untitled, updated.version)
    (cleared,) = store.list_sections(DOC)
    assert (cleared.section_id, cleared.title, cleared.flags) == (root.section_id, None, ["untitled"])
    with pytest.raises(VersionConflictError):
        store.update_imported_document_and_root_section(patched, document.version)
    (unchanged,) = store.list_sections(DOC)
    assert unchanged.title is None


def test_root_section_update_without_sections_only_updates_document(store: AppStore) -> None:
    document = seed(store)
    updated = store.update_imported_document_and_root_section(
        document.model_copy(update={"chapter_code": "ch4"}), document.version
    )
    assert updated.chapter_code == "ch4" and store.list_sections(DOC) == []


# --------------------------------------------------------------------------- #
# Tập đủ điều kiện (C4)
# --------------------------------------------------------------------------- #

CASES: dict[str, tuple[dict[str, object], BlockType, list[BlockFlag], SourceKind, bool]] = {
    "eligible": ({}, "paragraph", [], SourceKind.LECTURE_SLIDES, True),
    "past_exam": ({}, "paragraph", [], SourceKind.PAST_EXAM, False),
    "unconfirmed_page": ({"confirmed": False}, "paragraph", [], SourceKind.LECTURE_SLIDES, False),
    "dropped_page": ({"dropped": True}, "paragraph", [], SourceKind.LECTURE_SLIDES, False),
    "failed_page": ({"status": "failed", "confirmed": False}, "paragraph", [], SourceKind.LECTURE_SLIDES, False),
    "header_footer": ({}, "header_footer", [], SourceKind.LECTURE_SLIDES, False),
    "page_number": ({}, "page_number", [], SourceKind.LECTURE_SLIDES, False),
    "table_token_mismatch": ({}, "table", ["table_token_mismatch"], SourceKind.LECTURE_SLIDES, False),
    "table_clean": ({}, "table", [], SourceKind.TEXTBOOK, True),
    "crop_flags_do_not_block": ({}, "figure", ["crop_failed", "crop_capped"], SourceKind.LECTURE_SLIDES, True),
}


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("case", sorted(CASES))
def test_refresh_eligible_blocks_conditions(kind: str, case: str, tmp_path: Path) -> None:
    state, block_type, flags, source_kind, expected = CASES[case]
    store = open_kind(kind, tmp_path)
    try:
        seed(store, source_kind=source_kind, page_state={1: state})
        target = make_block(DOC, 1, 0, block_type, flags=flags)
        control = make_block(DOC, 2, 0)
        store.save_document_layout(DOC, layout({1: [target], 2: [control]}))
        expected_blocks = [target, control] if expected else [control]
        if source_kind == SourceKind.PAST_EXAM:
            expected_blocks = []
        assert store.refresh_eligible_blocks(DOC) == len(expected_blocks)
        assert store.list_eligible_blocks(DOC) == expected_blocks
    finally:
        close(store)


def test_refresh_eligible_blocks_follows_current_page_state(store: AppStore) -> None:
    seed(store)
    blocks = [make_block(DOC, 1, 0), make_block(DOC, 2, 0)]
    store.save_document_layout(DOC, layout({1: blocks[:1], 2: blocks[1:]}))
    assert store.refresh_eligible_blocks(DOC) == 2
    current = store.get_page(DOC, 2, PageExtraction)
    assert current is not None
    unconfirmed = current.model_copy(update={"confirmed": False, "confirmed_at": None, "confirmed_by": None})
    store.update_page(DOC, 2, unconfirmed, status="ok", confirmed=False, expected_version=current.version)
    assert store.refresh_eligible_blocks(DOC) == 1
    assert store.list_eligible_blocks(DOC) == blocks[:1]
    with pytest.raises(RecordNotFoundError):
        store.refresh_eligible_blocks(OTHER)


# --------------------------------------------------------------------------- #
# Ảnh cắt (hai kho)
# --------------------------------------------------------------------------- #


def test_crop_round_trip_clears_crop_flags(store: AppStore) -> None:
    seed(store)
    figure = make_block(DOC, 1, 0, "figure", flags=["crop_failed", "crop_capped"])
    store.save_document_layout(DOC, layout({1: [figure]}))
    png = tiny_png(1)
    crop = crop_of(figure, png)
    assert store.save_crop_image(crop, png) == "stored"
    (stored,) = store.list_blocks(DOC)
    assert (stored.crop_ref, stored.flags) == (crop.crop_ref, [])
    assert store.list_crop_images(DOC) == [crop]
    assert store.get_crop_bytes(DOC, crop.crop_ref) == png
    assert store.get_crop_bytes(OTHER, crop.crop_ref) is None
    with pytest.raises(ValueError, match="sha256"):
        store.save_crop_image(crop, tiny_png(2))
    with pytest.raises(RecordNotFoundError):
        store.save_crop_image(crop.model_copy(update={"block_id": block_id("vang")}), png)


def test_memory_crop_cap_marks_crop_capped() -> None:
    small, large = tiny_png(3), tiny_png(4) + b"\x00" * 64
    store = IngestionStore(memory_crop_max_bytes=len(small) + 10)
    seed(store)
    big = make_block(DOC, 1, 0, "figure", key="lon")
    fits = make_block(DOC, 1, 1, "figure", key="vua")
    store.save_document_layout(DOC, layout({1: [big, fits]}))
    assert store.save_crop_image(crop_of(big, large), large) == "capped"
    capped_block = store.list_blocks(DOC)[0]
    assert capped_block.flags == ["crop_pending", "crop_capped"] and capped_block.crop_ref is None
    assert store.list_crop_images(DOC) == []
    assert store.get_crop_bytes(DOC, hashlib.sha256(large).hexdigest()) is None
    assert store.save_crop_image(crop_of(fits, small), small) == "stored"
    assert store.list_blocks(DOC)[1].flags == []
    assert store.save_crop_image(crop_of(big, tiny_png(5)), tiny_png(5)) == "capped"  # tổng vượt trần
    assert store.list_blocks(DOC)[0].flags == ["crop_pending", "crop_capped"]


def test_layout_drops_crop_rows_of_removed_blocks(store: AppStore) -> None:
    seed(store)
    kept, gone = make_block(DOC, 1, 0, "figure", key="giu"), make_block(DOC, 2, 0, "figure", key="bo")
    store.save_document_layout(DOC, layout({1: [kept], 2: [gone]}))
    png_kept, png_gone = tiny_png(6), tiny_png(7)
    store.save_crop_image(crop_of(kept, png_kept), png_kept)
    store.save_crop_image(crop_of(gone, png_gone), png_gone)
    store.save_document_layout(DOC, layout({2: [make_block(DOC, 2, 0, key="moi")]}))
    assert [row.block_id for row in store.list_crop_images(DOC)] == [kept.block_id]
    assert store.get_crop_bytes(DOC, hashlib.sha256(png_gone).hexdigest()) is None
    assert store.get_crop_bytes(DOC, hashlib.sha256(png_kept).hexdigest()) == png_kept


@pytest.mark.parametrize("kind", KINDS)
def test_delete_document_removes_crops_and_block_rows(kind: str, tmp_path: Path) -> None:
    store = open_kind(kind, tmp_path)
    try:
        seed(store)
        seed(store, OTHER)
        figure, other = make_block(DOC, 1, 0, "figure"), make_block(OTHER, 1, 0, "figure")
        store.save_document_layout(DOC, layout({1: [figure]}))
        store.save_document_layout(OTHER, layout({1: [other]}, doc_id=OTHER))
        store.replace_sections(DOC, [section(DOC, 0, parent=None, level=0, title="T")])
        png, png_other = tiny_png(8), tiny_png(9)
        store.save_crop_image(crop_of(figure, png), png)
        store.save_crop_image(crop_of(other, png_other), png_other)
        ref = hashlib.sha256(png).hexdigest()
        if kind == "sqlite":
            assert (tmp_path / "crops" / DOC / f"{ref}.png").is_file()
        assert store.delete_document(DOC)
        assert store.get_crop_bytes(DOC, ref) is None
        assert (store.list_blocks(DOC), store.list_sections(DOC), store.list_crop_images(DOC)) == ([], [], [])
        assert store.list_extraction_ledgers(DOC) == []
        if kind == "sqlite":
            assert not (tmp_path / "crops" / DOC).exists()
            assert (tmp_path / "crops" / OTHER).is_dir()
        assert store.get_crop_bytes(OTHER, hashlib.sha256(png_other).hexdigest()) == png_other
    finally:
        close(store)


@pytest.mark.parametrize("kind", KINDS)
def test_revert_with_upload_drops_block_data(kind: str, tmp_path: Path) -> None:
    from holocron_core.domain.ingestion import UploadedDocument

    store = open_kind(kind, tmp_path)
    try:
        upload = UploadedDocument(
            document_id=DOC,
            sha256=hashlib.sha256(DOC.encode()).hexdigest(),
            filename="de.pdf",
            media_type="application/pdf",
            size_bytes=10,
            uploaded_at=NOW,
        )
        store.save_document(upload)
        seed(store)
        figure = make_block(DOC, 1, 0, "figure")
        store.save_document_layout(DOC, layout({1: [figure]}))
        store.replace_sections(DOC, [section(DOC, 0, parent=None, level=0, title="T")])
        png = tiny_png(10)
        store.save_crop_image(crop_of(figure, png), png)
        assert store.revert_imported_document(DOC, upload)
        assert store.get_imported_document(DOC) is None
        assert (store.list_blocks(DOC), store.list_sections(DOC), store.list_crop_images(DOC)) == ([], [], [])
        assert store.get_crop_bytes(DOC, hashlib.sha256(png).hexdigest()) is None
        if kind == "sqlite":
            assert not (tmp_path / "crops" / DOC).exists()
        assert seed(store).import_generation == 2
    finally:
        close(store)
