"""`PATCH …/lines` dựng lại khối của tài liệu chưa đóng có khối lưu (SPECIFICATION §5.15, §6.5; ADR-010 mục 13 "PATCH
lines", "Đường dựng lại"; X09 C6).

Tài liệu nhập qua `accept_document` với bộ đọc giả có hình học (`tests/test_import_blocks.py`); không mạng.
"""

from __future__ import annotations

from pathlib import Path

from holocron_core.domain.blocks import Block, WordRef
from holocron_core.domain.documents import (
    LineEdit,
    PageConfirmRequest,
    PageExtraction,
    PageLinesPatch,
    PageUnconfirmRequest,
)
from holocron_core.domain.repositories import AppStore
from holocron_core.services.document_import import (
    ImportRuntime,
    confirm_page,
    edit_page_lines,
    unconfirm_page,
)
from tests.test_import_blocks import (
    NOW,
    THREE,
    EditStage,
    GeoReader,
    as_figure,
    flagged,
    import_doc,
    layout_with,
    pages_of,
    runtime_for,
    store,
    text_page,
    without_crop_fields,
)

__all__ = ["store"]


def _patch(
    store: AppStore,
    runtime: ImportRuntime,
    document_id: str,
    number: int,
    edits: dict[int, str],
    appended: list[str] | None = None,
) -> PageExtraction:
    page = pages_of(store, document_id)[number - 1]
    body = PageLinesPatch(
        editor_id="u",
        expected_version=page.version,
        edits=[LineEdit(line_index=index, text=text) for index, text in edits.items()],
        appended_lines=appended or [],
    )
    return edit_page_lines(store, store, runtime, document_id, number, body, now=NOW)


def _ids(store: AppStore, document_id: str, number: int) -> dict[str, Block]:
    return {block.block_id: block for block in store.list_blocks(document_id, number)}


def _paged(title: str, footer: str) -> object:
    return text_page(title, footer=footer, extra=("Doan thu hai noi ve thanh ghi va co bao loi cua bo xu ly",))


def test_patch_lines_keeps_unchanged_block_ids(store: AppStore, tmp_path: Path) -> None:
    """I4: sửa một dòng ⇒ khối chứa dòng đó đổi id, mang `human_edited`, tham chiếu `confirmed_line`; khối khác giữ
    nguyên id; ledger của trang thêm basis `confirmed_lines` và vẫn cân."""
    pages = [text_page("1. Gioi thieu", extra=("Doan thu hai noi ve thanh ghi va co bao loi",)), *THREE[1:]]
    runtime = runtime_for(tmp_path)
    doc_id = import_doc(store, runtime, GeoReader(pages), auto_confirm=False)
    before = _ids(store, doc_id, 1)
    edited = _patch(store, runtime, doc_id, 1, {3: "Doan thu hai da sua boi nguoi dung"})
    assert edited.lines[3].text == "Doan thu hai da sua boi nguoi dung"
    after = _ids(store, doc_id, 1)
    kept = set(before) & set(after)
    changed = [block for block_id, block in after.items() if block_id not in before]
    assert len(kept) == len(before) - 1 and len(changed) == 1
    [new] = changed
    assert "human_edited" in new.flags and new.text is not None and "da sua" in new.text
    assert any(ref.kind == "confirmed_line" for ref in new.line_refs)
    for block_id in kept:
        assert after[block_id].text == before[block_id].text
        assert all(isinstance(ref, WordRef) for ref in after[block_id].line_refs)
    [ledger] = [item for item in store.list_extraction_ledgers(doc_id) if item.page_number == 1]
    assert ledger.balanced and "confirmed_lines" in {basis.ledger_basis for basis in ledger.bases}
    assert _ids(store, doc_id, 2).keys() == {b.block_id for b in store.list_blocks(doc_id, 2)}
    assert len(store.list_page_audits(doc_id, 1)) == 1


def test_page_numbers_rerun_after_patch(store: AppStore, tmp_path: Path) -> None:
    """`page_numbers` chạy lại TOÀN tài liệu sau PATCH: sửa số ở chân trang 2 ⇒ chỉ còn 2 trang có hiệu số ổn định
    (< `page_number_min_pages` = 3) ⇒ khối số trang của trang 1 và 3 về loại gốc; sửa lại ⇒ thành `page_number`."""
    pages = [text_page(f"{n}. Muc", footer=str(n)) for n in (1, 2, 3)]
    runtime = runtime_for(tmp_path)
    doc_id = import_doc(store, runtime, GeoReader(pages), auto_confirm=False)

    def numbered(number: int) -> list[Block]:
        return [block for block in store.list_blocks(doc_id, number) if block.type == "page_number"]

    assert all(numbered(n) for n in (1, 2, 3))
    footer_line = len(pages_of(store, doc_id)[1].lines) - 1
    _patch(store, runtime, doc_id, 2, {footer_line: "Hai"})
    assert numbered(1) == [] and numbered(3) == [], "trang khác trang sửa cũng được lưu lại"
    _patch(store, runtime, doc_id, 2, {footer_line: "2"})
    assert all(numbered(n) for n in (1, 2, 3))


def test_rebuild_equals_fresh_build_except_dropped_ai_proposals(store: AppStore, tmp_path: Path) -> None:
    """Kết quả dựng lại (khối ĐÃ LƯU, trước settle) BẰNG dựng mới từ cùng geometry, cùng dòng đã xác nhận, cùng hash
    (bỏ field ảnh cắt). Chưa có V03 ⇒ không có đề xuất AI nào bị bỏ."""
    layout = layout_with(EditStage("hinh", as_figure, frozenset({2})))
    pages = [text_page(f"{n}. Muc", footer=str(n), extra=("Doan thu hai cua trang",)) for n in (1, 2, 3)]
    runtime = runtime_for(tmp_path, layout)
    doc_id = import_doc(store, runtime, GeoReader(pages), auto_confirm=False)
    assert store.list_structure_proposals(doc_id) == []
    _patch(store, runtime, doc_id, 2, {1: "Dong da sua"}, appended=["Dong go them"])
    stored_pages = pages_of(store, doc_id)
    fresh = layout.page_numbers.run(
        [
            layout.build_page_blocks(
                document_id=doc_id,
                page_number=page.page_number,
                geometry=store.get_page_geometry(doc_id, page.page_number),
                extraction=page,
            )
            for page in stored_pages
        ]
    )
    for result in fresh:
        assert without_crop_fields(store.list_blocks(doc_id, result.page_number)) == without_crop_fields(result.blocks)
    ledgers = {item.page_number: item for item in store.list_extraction_ledgers(doc_id)}
    assert ledgers[2] == fresh[1].ledger


def test_rebuild_reevaluates_auto_confirmed_pages(store: AppStore, tmp_path: Path) -> None:
    """Dựng lại xét lại trang tự xác nhận: hash đã lưu khác hash của cấu hình hiện tại ⇒ MỌI trang lớp chữ dựng lại;
    ≥ 2 trang tự xác nhận có cờ chặn về xem lại trong MỘT lần PATCH (`confirmed_by`/`confirm_rule` null,
    `auto_confirm_undone` giữ false); trang người dùng xác nhận giữ nguyên; version/hash mới được ghi."""
    pages = [*THREE, text_page("4. Ngat")]
    runtime = runtime_for(tmp_path)
    doc_id = import_doc(store, runtime, GeoReader(pages))
    assert all(page.confirmed_by == "auto" for page in pages_of(store, doc_id))
    for number in (1, 4):
        page = pages_of(store, doc_id)[number - 1]
        unconfirm_page(
            store, store, runtime, doc_id, number, PageUnconfirmRequest(editor_id="u", expected_version=page.version)
        )
    page4 = pages_of(store, doc_id)[3]
    confirm_page(
        store, store, runtime, doc_id, 4, PageConfirmRequest(editor_id="u", expected_version=page4.version), now=NOW
    )
    flag_all = layout_with(EditStage("chan", flagged("layout_uncertain"), frozenset({1, 2, 3, 4})))
    new_runtime = runtime_for(tmp_path / "moi", flag_all)
    old_hash = store.get_imported_document(doc_id)
    assert old_hash is not None and old_hash.layout_settings_sha256 != flag_all.settings_sha256()
    _patch(store, new_runtime, doc_id, 1, {1: "Dong da sua o trang mot"})
    document = store.get_imported_document(doc_id)
    assert document is not None and document.layout_settings_sha256 == flag_all.settings_sha256()
    after = pages_of(store, doc_id)
    for page in after[1:3]:
        assert not page.confirmed and page.confirmed_by is None and page.confirm_rule is None
        assert not page.auto_confirm_undone and page.confirmed_at is None
    assert after[3].confirmed and after[3].confirmed_by == "user"
    for number in (2, 3, 4):
        assert any("layout_uncertain" in block.flags for block in store.list_blocks(doc_id, number))


def test_rebuild_keeps_auto_confirmed_pages_without_blocking_flags(store: AppStore, tmp_path: Path) -> None:
    """Cùng hash, PATCH một trang: trang tự xác nhận khác không có cờ chặn giữ xác nhận; trang khác không bị lưu lại."""
    runtime = runtime_for(tmp_path)
    doc_id = import_doc(store, runtime, GeoReader(THREE))
    page = pages_of(store, doc_id)[0]
    unconfirm_page(store, store, runtime, doc_id, 1, PageUnconfirmRequest(editor_id="u", expected_version=page.version))
    versions = [page.version for page in pages_of(store, doc_id)]
    _patch(store, runtime, doc_id, 1, {1: "Dong da sua"})
    after = pages_of(store, doc_id)
    assert [page.confirmed_by for page in after[1:]] == ["auto", "auto"]
    assert [page.version for page in after[1:]] == versions[1:]


def test_patch_rebuild_recomputes_layout_failed(store: AppStore, tmp_path: Path) -> None:
    """`LAYOUT_FAILED` tính lại khi dựng lại: cấu hình mới làm bộ dựng lỗi ⇒ trang được thêm `LAYOUT_FAILED`, khối của
    trang bị xoá; cấu hình mặc định ⇒ bỏ lý do."""

    def explode(block: Block) -> Block:
        raise ValueError("bộ dựng lỗi")

    runtime = runtime_for(tmp_path)
    doc_id = import_doc(store, runtime, GeoReader(THREE), auto_confirm=False)
    broken = runtime_for(tmp_path / "loi", layout_with(EditStage("loi", explode, frozenset({1}))))
    edited = _patch(store, broken, doc_id, 1, {1: "Dong da sua"})
    assert edited.review_reasons == ["LAYOUT_FAILED"] and edited.status == "needs_review"
    assert store.list_blocks(doc_id, 1) == []
    fixed = _patch(store, runtime, doc_id, 1, {1: "Dong da sua lan hai"})
    assert fixed.review_reasons == [] and fixed.status == "ok" and store.list_blocks(doc_id, 1)


def test_rebuild_reopens_auto_confirmed_page_that_becomes_layout_failed(store: AppStore, tmp_path: Path) -> None:
    """Hash khác và bộ dựng mới lỗi ở trang tự xác nhận khác ⇒ trang đó về xem lại VÀ mang `LAYOUT_FAILED`
    (`needs_review`, không khối) trong cùng lần PATCH."""

    def explode(block: Block) -> Block:
        raise ValueError("bộ dựng lỗi")

    runtime = runtime_for(tmp_path)
    doc_id = import_doc(store, runtime, GeoReader(THREE))
    page = pages_of(store, doc_id)[0]
    unconfirm_page(store, store, runtime, doc_id, 1, PageUnconfirmRequest(editor_id="u", expected_version=page.version))
    broken = runtime_for(tmp_path / "loi", layout_with(EditStage("loi", explode, frozenset({2}))))
    _patch(store, broken, doc_id, 1, {1: "Dong da sua"})
    second = pages_of(store, doc_id)[1]
    assert second.review_reasons == ["LAYOUT_FAILED"] and second.status == "needs_review"
    assert not second.confirmed and second.confirmed_by is None and not second.auto_confirm_undone
    assert store.list_blocks(doc_id, 2) == []
    assert pages_of(store, doc_id)[2].confirmed_by == "auto"
