"""Nhánh lỗi và kế toán byte của kho khối, ảnh cắt và model ranh giới (SPECIFICATION §5.13, §5.15; ADR-010 mục 13,
16, 19).

Ghi sai tài liệu/trang ⇒ `ValueError`, không ghi gì (kể cả trang ghi lại trong cùng giao dịch); trần byte ảnh cắt
của kho bộ nhớ tính mỗi file một lần và nhả byte khi hàng cuối mất; thế hệ nhập luôn lấy từ kho. Dữ liệu tự tạo.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from pydantic import ValidationError

from holocron_core.domain.block_eligibility import is_candidate_block
from holocron_core.domain.documents import PageExtraction, PageSummary
from holocron_core.domain.repositories import (
    AppStore,
    BlockFlagUpdate,
    DocumentLayout,
    PageLayout,
    PageRewrite,
    check_crop_bytes,
)
from holocron_core.domain.schemas import SourceKind
from holocron_core.domain.vault import BlockDocumentText, ChunkBlock, ConfirmedPage
from holocron_core.infrastructure.memory_store import IngestionStore
from tests.block_samples import geo_line, geometry
from tests.test_block_store import (
    DOC,
    KINDS,
    LAYOUT_SHA,
    OTHER,
    close,
    crop_of,
    layout,
    ledger_for,
    make_block,
    open_kind,
    seed,
    tiny_png,
)


def _summary(number: int, *, status: str = "ok", confirmed: bool = True) -> PageSummary:
    return PageSummary.model_validate(
        {
            "page_number": number,
            "status": status,
            "confirmed": confirmed,
            "dropped": False,
            "page_role": "content",
            "tier_used": None if status == "failed" else "text_layer",
            "review_reasons": [],
        }
    )


# --------------------------------------------------------------------------- #
# Model ranh giới
# --------------------------------------------------------------------------- #


def test_is_candidate_block_rejects_summary_of_other_page() -> None:
    block = make_block(DOC, 2, 0)
    with pytest.raises(ValueError, match="trang"):
        is_candidate_block(block, _summary(1), SourceKind.LECTURE_SLIDES)
    assert is_candidate_block(block, _summary(2), SourceKind.LECTURE_SLIDES)


def test_failed_page_is_never_candidate_even_if_confirmed() -> None:
    block = make_block(DOC, 1, 0)
    assert not is_candidate_block(block, _summary(1, status="failed", confirmed=True), SourceKind.LECTURE_SLIDES)


def test_block_flag_update_rejects_same_flag_added_and_removed() -> None:
    with pytest.raises(ValidationError, match="vừa thêm vừa gỡ"):
        BlockFlagUpdate(block_id="blk_" + "1" * 16, add=["too_short_for_chunk"], remove=["too_short_for_chunk"])


def test_page_layout_requires_blocks_and_ledger_of_its_page() -> None:
    other_page = make_block(DOC, 2, 0)
    with pytest.raises(ValidationError, match="đúng trang"):
        PageLayout(page_number=1, blocks=[other_page], ledger=None)
    mine = make_block(DOC, 1, 0)
    with pytest.raises(ValidationError, match="ledger"):
        PageLayout(page_number=1, blocks=[mine], ledger=ledger_for(DOC, 2, []))
    with pytest.raises(ValidationError, match="trùng"):
        PageLayout(page_number=1, blocks=[mine, mine], ledger=None)


def test_document_layout_rejects_duplicate_pages_and_rewrites() -> None:
    page = PageLayout(page_number=1, blocks=[], ledger=None)
    with pytest.raises(ValidationError, match="PageLayout"):
        DocumentLayout(layout_version=2, layout_settings_sha256=LAYOUT_SHA, pages=[page, page])
    store = IngestionStore()
    seed(store, pages=1)
    current = store.get_page(DOC, 1, PageExtraction)
    assert current is not None
    rewrite = PageRewrite(page=current, expected_version=current.version)
    with pytest.raises(ValidationError, match="PageRewrite"):
        DocumentLayout(layout_version=2, layout_settings_sha256=LAYOUT_SHA, page_rewrites=[rewrite, rewrite])


def test_block_document_text_consistency() -> None:
    chunk_block = ChunkBlock(
        block_id="blk_" + "1" * 16,
        page_number=1,
        reading_order=0,
        is_heading=False,
        text="x",
        section_id="sec_" + "1" * 16,
    )
    base = {"doc_id": DOC, "subject_code": "IT3070", "chapter_code": "ch2"}
    two, three = ConfirmedPage(page_number=2, lines=["a"]), ConfirmedPage(page_number=3, lines=["b"])
    with pytest.raises(ValidationError, match="tăng dần"):
        BlockDocumentText.model_validate({**base, "line_pages": [three, two]})
    with pytest.raises(ValidationError, match="tăng dần"):
        BlockDocumentText.model_validate({**base, "line_pages": [two, two]})
    with pytest.raises(ValidationError, match="không được có khối"):
        BlockDocumentText.model_validate(
            {**base, "blocks": [chunk_block], "line_pages": [ConfirmedPage(page_number=1, lines=["a"])]}
        )
    with pytest.raises(ValidationError, match="block_id trùng"):
        BlockDocumentText.model_validate({**base, "blocks": [chunk_block, chunk_block]})


def test_check_crop_bytes_requires_matching_size() -> None:
    figure = make_block(DOC, 1, 0, "figure")
    png = tiny_png(40)
    crop = crop_of(figure, png)
    check_crop_bytes(crop, png)
    with pytest.raises(ValueError, match="size_bytes"):
        check_crop_bytes(crop.model_copy(update={"size_bytes": len(png) + 1}), png)


# --------------------------------------------------------------------------- #
# Ghi khối sai tài liệu/trang (hai kho)
# --------------------------------------------------------------------------- #


def _blocks_snapshot(store: AppStore) -> tuple[object, ...]:
    return (
        store.list_blocks(DOC),
        store.list_extraction_ledgers(DOC),
        [summary.confirmed for summary in store.list_page_summaries(DOC)],
        store.get_page(DOC, 2, PageExtraction),
    )


@pytest.mark.parametrize("kind", KINDS)
def test_save_layout_foreign_block_or_ledger_writes_nothing(kind: str, tmp_path: Path) -> None:
    store = open_kind(kind, tmp_path)
    try:
        seed(store)
        seed(store, OTHER)
        store.save_document_layout(DOC, layout({1: [make_block(DOC, 1, 0)]}))
        current = store.get_page(DOC, 2, PageExtraction)
        assert current is not None
        undo = current.model_copy(update={"confirmed": False, "confirmed_at": None, "confirmed_by": None})
        rewrite = [PageRewrite(page=undo, expected_version=current.version)]
        before = _blocks_snapshot(store)
        foreign_block = layout({2: [make_block(OTHER, 2, 0)]}, rewrites=rewrite)
        with pytest.raises(ValueError, match="không thuộc tài liệu"):
            store.save_document_layout(DOC, foreign_block)
        assert _blocks_snapshot(store) == before  # trang ghi lại trong cùng giao dịch cũng không đổi
        foreign_ledger = DocumentLayout(
            layout_version=3,
            layout_settings_sha256="2" * 64,
            pages=[PageLayout(page_number=2, blocks=[], ledger=ledger_for(OTHER, 2, []))],
            page_rewrites=rewrite,
        )
        with pytest.raises(ValueError, match="không thuộc tài liệu"):
            store.save_document_layout(DOC, foreign_ledger)
        assert _blocks_snapshot(store) == before
        other_page = store.get_page(OTHER, 2, PageExtraction)
        assert other_page is not None
        foreign_rewrite = layout({}, rewrites=[PageRewrite(page=other_page, expected_version=other_page.version)])
        with pytest.raises(ValueError, match="không thuộc tài liệu"):
            store.save_document_layout(DOC, foreign_rewrite)
        assert _blocks_snapshot(store) == before
        document = store.get_imported_document(DOC)
        assert document is not None and document.layout_settings_sha256 == LAYOUT_SHA
    finally:
        close(store)


@pytest.mark.parametrize("kind", KINDS)
def test_save_crop_on_other_page_writes_nothing(kind: str, tmp_path: Path) -> None:
    store = open_kind(kind, tmp_path)
    try:
        seed(store)
        figure = make_block(DOC, 1, 0, "figure")
        store.save_document_layout(DOC, layout({1: [figure]}))
        png = tiny_png(41)
        with pytest.raises(ValueError, match="khác trang"):
            store.save_crop_image(crop_of(figure, png).model_copy(update={"page_number": 2}), png)
        assert store.list_crop_images(DOC) == []
        assert store.get_crop_bytes(DOC, hashlib.sha256(png).hexdigest()) is None
        assert store.list_blocks(DOC) == [figure]
        if kind == "sqlite":
            assert not (tmp_path / "crops" / DOC).exists()
    finally:
        close(store)


@pytest.mark.parametrize("kind", KINDS)
def test_update_imported_document_keeps_stored_generation(kind: str, tmp_path: Path) -> None:
    store = open_kind(kind, tmp_path)
    try:
        stale = seed(store)
        assert stale.import_generation == 1
        updated = store.update_imported_document(
            stale.model_copy(update={"status": "confirmed", "import_generation": 7}), stale.version
        )
        assert updated.import_generation == 1
        reread = store.get_imported_document(DOC)
        assert reread is not None and reread.import_generation == 1
    finally:
        close(store)


@pytest.mark.parametrize("kind", KINDS)
def test_refresh_eligible_counts_only_its_document(kind: str, tmp_path: Path) -> None:
    store = open_kind(kind, tmp_path)
    try:
        seed(store)
        seed(store, OTHER)
        store.save_document_layout(DOC, layout({1: [make_block(DOC, 1, 0)], 2: [make_block(DOC, 2, 0)]}))
        store.save_document_layout(OTHER, layout({1: [make_block(OTHER, 1, 0)]}, doc_id=OTHER))
        assert store.refresh_eligible_blocks(OTHER) == 1
        assert store.refresh_eligible_blocks(DOC) == 2
        assert store.refresh_eligible_blocks(OTHER) == 1
    finally:
        close(store)


@pytest.mark.parametrize("kind", KINDS)
def test_rewritten_block_loses_eligible(kind: str, tmp_path: Path) -> None:
    store = open_kind(kind, tmp_path)
    try:
        seed(store)
        same = make_block(DOC, 1, 0)
        store.save_document_layout(DOC, layout({1: [same]}))
        assert store.refresh_eligible_blocks(DOC) == 1
        store.save_document_layout(DOC, layout({1: [same]}))
        assert store.list_eligible_blocks(DOC) == []  # khối ghi lại luôn eligible = 0 tới lần refresh sau
    finally:
        close(store)


@pytest.mark.parametrize("kind", KINDS)
def test_reimport_after_delete_has_no_stale_geometry(kind: str, tmp_path: Path) -> None:
    store = open_kind(kind, tmp_path)
    try:
        seed(store, pages=1)
        current = store.get_page(DOC, 1, PageExtraction)
        assert current is not None
        geo = geometry([geo_line("Mot dong", 0.1, 0.2)])
        store.update_page(
            DOC,
            1,
            current.model_copy(update={"version": current.version + 1}),
            status=current.status,
            confirmed=current.confirmed,
            expected_version=current.version,
            geometry=geo,
        )
        assert store.get_page_geometry(DOC, 1) == geo
        assert store.delete_document(DOC)
        seed(store, pages=1)
        assert store.get_page_geometry(DOC, 1) is None
    finally:
        close(store)


# --------------------------------------------------------------------------- #
# Trần byte ảnh cắt của kho bộ nhớ
# --------------------------------------------------------------------------- #


def test_memory_cap_is_inclusive() -> None:
    png = tiny_png(42)
    store = IngestionStore(memory_crop_max_bytes=len(png))
    seed(store)
    figure = make_block(DOC, 1, 0, "figure")
    store.save_document_layout(DOC, layout({1: [figure]}))
    assert store.save_crop_image(crop_of(figure, png), png) == "stored"


def test_memory_cap_counts_shared_png_once() -> None:
    png = tiny_png(43)
    store = IngestionStore(memory_crop_max_bytes=len(png) + 1)
    seed(store)
    first, second = make_block(DOC, 1, 0, "figure", key="mot"), make_block(DOC, 1, 1, "figure", key="hai")
    store.save_document_layout(DOC, layout({1: [first, second]}))
    assert store.save_crop_image(crop_of(first, png), png) == "stored"
    assert store.save_crop_image(crop_of(second, png), png) == "stored"


def test_memory_delete_document_releases_crop_bytes() -> None:
    first, second = tiny_png(44), tiny_png(45)
    store = IngestionStore(memory_crop_max_bytes=len(first) + len(second) - 1)
    seed(store)
    figure = make_block(DOC, 1, 0, "figure")
    store.save_document_layout(DOC, layout({1: [figure]}))
    assert store.save_crop_image(crop_of(figure, first), first) == "stored"
    assert store.delete_document(DOC)
    seed(store)
    store.save_document_layout(DOC, layout({1: [figure]}))
    assert store.save_crop_image(crop_of(figure, second), second) == "stored"


def test_memory_layout_rewrite_releases_crop_bytes() -> None:
    first, second = tiny_png(46), tiny_png(47)
    store = IngestionStore(memory_crop_max_bytes=len(first) + len(second) - 1)
    seed(store)
    old = make_block(DOC, 1, 0, "figure", key="cu")
    store.save_document_layout(DOC, layout({1: [old]}))
    assert store.save_crop_image(crop_of(old, first), first) == "stored"
    new = make_block(DOC, 1, 0, "figure", key="moi")
    store.save_document_layout(DOC, layout({1: [new]}))
    assert store.save_crop_image(crop_of(new, second), second) == "stored"


def test_memory_shared_png_kept_until_last_row_removed() -> None:
    png = tiny_png(48)
    store = IngestionStore()
    seed(store)
    first, second = make_block(DOC, 1, 0, "figure", key="mot"), make_block(DOC, 2, 0, "figure", key="hai")
    store.save_document_layout(DOC, layout({1: [first], 2: [second]}))
    store.save_crop_image(crop_of(first, png), png)
    store.save_crop_image(crop_of(second, png), png)
    ref = hashlib.sha256(png).hexdigest()
    store.save_document_layout(DOC, layout({1: [make_block(DOC, 1, 0, key="moi")]}))
    assert store.get_crop_bytes(DOC, ref) == png
    store.save_document_layout(DOC, layout({2: [make_block(DOC, 2, 0, key="moi")]}))
    assert store.get_crop_bytes(DOC, ref) is None


def test_memory_replacing_crop_releases_old_bytes() -> None:
    old, new, third = tiny_png(49), tiny_png(50), tiny_png(51)
    store = IngestionStore(memory_crop_max_bytes=len(new) + max(len(old), len(third)))
    seed(store)
    figure, other = make_block(DOC, 1, 0, "figure", key="mot"), make_block(DOC, 2, 0, "figure", key="hai")
    store.save_document_layout(DOC, layout({1: [figure], 2: [other]}))
    assert store.save_crop_image(crop_of(figure, old), old) == "stored"
    assert store.save_crop_image(crop_of(figure, new), new) == "stored"
    assert store.get_crop_bytes(DOC, hashlib.sha256(old).hexdigest()) is None
    assert len(old) + len(new) + len(third) > len(new) + max(len(old), len(third))
    assert store.save_crop_image(crop_of(other, third), third) == "stored"
