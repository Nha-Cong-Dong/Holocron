"""Nối bộ dựng khối vào luồng nhập: lưu khối/ledger/hash ở `_finish`, `LAYOUT_FAILED`, tự xác nhận theo cờ khối,
`_finish` chạy sau/trên tài liệu đã đóng (SPECIFICATION §5.7, §5.15; ADR-010 mục 13, 15; X09).

PDF tự tạo (`tests/block_samples.py`) đọc bằng `WorkerPdfReader` thật cho đường nhập thật; các test còn lại dùng bộ đọc
giả thoả CẢ `PdfReader` lẫn `PageGeometrySource`, trả geometry dựng tay (không mở file). Không mạng, không tài liệu thật.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from holocron_core.domain.blocks import Block, BlockFlag
from holocron_core.domain.documents import (
    DocumentConfirmRequest,
    DocumentImport,
    LineEdit,
    PageLinesPatch,
    PageRef,
    PageRestoreRequest,
    PageRoleEvidence,
)
from holocron_core.domain.repositories import AppStore, VersionConflictError
from holocron_core.domain.schemas import quote_in_text
from holocron_core.infrastructure.memory_store import IngestionStore
from holocron_core.routers import documents as documents_router
from holocron_core.routers.api import app, get_clock, get_store
from holocron_core.routers.documents import get_import_runtime, get_pdf_reader
from holocron_core.services.block_builder import BaseBlockStage
from holocron_core.services.block_pipeline import default_layout_config
from holocron_core.services.code_blocks import CodeBlockSettings, CodeBlockStage
from holocron_core.services.document_import import (
    ImportRuntime,
    ReadJob,
    confirm_document,
    edit_page_lines,
    read_document,
    restore_page,
    settle_document,
)
from holocron_core.services.document_layout import refresh_drop_reasons
from holocron_core.services.reading_order import ReadingOrderStage
from tests.block_samples import Run, build_pdf
from tests.document_import_support import FakePdfReader, pdf_bytes
from tests.import_blocks_support import (
    BODY,
    LANG,
    NOW,
    SUBJECT,
    THREE,
    TWO_PARAGRAPHS,
    EditStage,
    GeoReader,
    as_figure,
    as_table,
    document_of,
    finish_again,
    flagged,
    import_doc,
    layout_with,
    pages_of,
    runtime_for,
    store,
    without_crop_fields,
)

__all__ = ["store"]

# --------------------------------------------------------------------------- #
# Lưu khối ở `_finish` (C1–C3)
# --------------------------------------------------------------------------- #


def test_blocks_ledger_and_hash_stored_on_import(store: AppStore, tmp_path: Path) -> None:
    layout = default_layout_config(LANG)
    doc_id = import_doc(store, runtime_for(tmp_path, layout), GeoReader(THREE))
    document = document_of(store, doc_id)
    assert document.layout_version is not None
    assert document.layout_settings_sha256 == layout.settings_sha256()
    assert not document.blocks_after_close
    assert {block.page_number for block in store.list_blocks(doc_id)} == {1, 2, 3}
    assert [ledger.page_number for ledger in store.list_extraction_ledgers(doc_id)] == [1, 2, 3]
    assert all(ledger.balanced for ledger in store.list_extraction_ledgers(doc_id))
    for number in (1, 2, 3):
        assert store.get_page_geometry(doc_id, number) == THREE[number - 1].geometry
    assert all(page.review_reasons == [] and page.confirmed_by == "auto" for page in pages_of(store, doc_id))


def test_geometry_over_cap_marks_layout_failed_on_import(store: AppStore, tmp_path: Path) -> None:
    """Bộ đọc trả geometry None cho trang lớp chữ (vượt trần, X04) ⇒ trang `LAYOUT_FAILED` (needs_review), không khối,
    không ledger; tài liệu vẫn ghi version/hash; trang khác dựng khối bình thường."""
    pages = [THREE[0], dataclasses.replace(THREE[1], send_geometry=False), THREE[2]]
    doc_id = import_doc(store, runtime_for(tmp_path), GeoReader(pages))
    page = pages_of(store, doc_id)[1]
    assert page.review_reasons == ["LAYOUT_FAILED"] and page.status == "needs_review" and not page.confirmed
    assert store.list_blocks(doc_id, 2) == []
    assert 2 not in {ledger.page_number for ledger in store.list_extraction_ledgers(doc_id)}
    assert store.list_blocks(doc_id, 1) and store.list_blocks(doc_id, 3)
    assert document_of(store, doc_id).layout_version is not None
    with_failed = dataclasses.replace(THREE[0], send_geometry=False)
    fresh = IngestionStore()
    other = import_doc(fresh, runtime_for(tmp_path / "b"), GeoReader([with_failed]), tag="b")
    assert document_of(fresh, other).layout_settings_sha256 is not None, "mọi trang hỏng vẫn ghi version/hash"
    assert pages_of(fresh, other)[0].review_reasons == ["LAYOUT_FAILED"]


def test_reader_without_geometry_keeps_legacy_path(store: AppStore, tmp_path: Path) -> None:
    """Q1 = A: bộ đọc không có hình học ⇒ không khối, `layout_version`/hash null, không `LAYOUT_FAILED`."""
    doc_id = import_doc(store, runtime_for(tmp_path), GeoReader(THREE), geometry=False)
    document = document_of(store, doc_id)
    assert document.layout_version is None and document.layout_settings_sha256 is None
    assert store.list_blocks(doc_id) == [] and store.list_extraction_ledgers(doc_id) == []
    assert all("LAYOUT_FAILED" not in page.review_reasons for page in pages_of(store, doc_id))


def test_stored_blocks_reproducible_by_settings_sha256(store: AppStore, tmp_path: Path) -> None:
    """I4 (phần X09): khối đã lưu qua luồng nhập = dựng lại từ geometry và dòng đã lưu với cấu hình cùng hash
    (so sau khi bỏ field ảnh cắt)."""
    layout = layout_with(EditStage("hinh", as_figure, frozenset({2})))
    doc_id = import_doc(store, runtime_for(tmp_path, layout), GeoReader(THREE))
    document = document_of(store, doc_id)
    fresh_layout = layout_with(EditStage("hinh", as_figure, frozenset({2})))
    assert fresh_layout.settings_sha256() == document.layout_settings_sha256
    pages = pages_of(store, doc_id)
    built = fresh_layout.page_numbers.run(
        [
            fresh_layout.build_page_blocks(
                document_id=doc_id,
                page_number=page.page_number,
                geometry=store.get_page_geometry(doc_id, page.page_number),
                extraction=page,
            )
            for page in pages
        ]
    )
    for result in built:
        stored = store.list_blocks(doc_id, result.page_number)
        assert stored, "mỗi trang có khối"
        assert without_crop_fields(stored) == without_crop_fields(result.blocks)
    assert any(block.type == "figure" for block in store.list_blocks(doc_id, 2))


# --------------------------------------------------------------------------- #
# Tự xác nhận theo cờ khối (C5; Q2 người dùng 2026-11-03)
# --------------------------------------------------------------------------- #

BLOCKING: tuple[BlockFlag, ...] = (
    "layout_uncertain",
    "table_unresolved",
    "table_token_mismatch",
    "vlm_gate_rejected",
    "ai_proposal_rejected",
)


@pytest.mark.parametrize("flag", BLOCKING)
def test_flagged_blocks_prevent_auto_confirm(store: AppStore, tmp_path: Path, flag: BlockFlag) -> None:
    """Trang có khối mang cờ dữ liệu chặn KHÔNG được tự xác nhận; trang khác cùng tài liệu vẫn tự xác nhận."""
    edit = as_table(flag) if flag == "table_token_mismatch" else flagged(flag)
    doc_id = import_doc(store, runtime_for(tmp_path, layout_with(EditStage(flag, edit))), GeoReader(THREE))
    assert any(flag in block.flags for block in store.list_blocks(doc_id, 1))
    first, *rest = pages_of(store, doc_id)
    assert not first.confirmed and first.confirmed_by is None and first.status == "ok"
    assert all(page.confirmed_by == "auto" for page in rest)


def test_crop_pending_does_not_block_auto_confirm(store: AppStore, tmp_path: Path) -> None:
    """Cờ ảnh cắt chỉ báo thiếu ảnh: trang có khối chữ sạch + khối `figure` `crop_pending` vẫn tự xác nhận (người dùng
    2026-11-03); cùng trang có `table_token_mismatch` ⇒ không."""

    def figure_and_text(block: Block) -> Block:
        return as_figure(block) if block.reading_order == 1 else block

    doc_id = import_doc(
        store, runtime_for(tmp_path, layout_with(EditStage("hinh", figure_and_text))), GeoReader(TWO_PARAGRAPHS)
    )
    blocks = store.list_blocks(doc_id, 1)
    assert any(block.type == "figure" and "crop_pending" in block.flags for block in blocks)
    assert any(block.type == "paragraph" for block in blocks)
    assert pages_of(store, doc_id)[0].confirmed_by == "auto"

    def with_mismatch(block: Block) -> Block:
        return as_table("table_token_mismatch")(block) if block.reading_order == 2 else figure_and_text(block)

    # Kho riêng: tài liệu thứ hai cùng nội dung trong cùng môn sẽ bị 0D coi là trang lặp (bỏ mềm).
    fresh = IngestionStore()
    other = import_doc(
        fresh,
        runtime_for(tmp_path / "b", layout_with(EditStage("bang", with_mismatch))),
        GeoReader(TWO_PARAGRAPHS),
        tag="b",
    )
    first = pages_of(fresh, other)[0]
    assert any("table_token_mismatch" in block.flags for block in fresh.list_blocks(other, 1))
    assert first.status == "ok" and first.page_role == "content" and not first.dropped
    assert not first.confirmed and pages_of(fresh, other)[1].confirmed_by == "auto"


@pytest.mark.parametrize("flag", ["crop_failed", "crop_capped"])
def test_crop_failure_flags_do_not_block_auto_confirm(store: AppStore, tmp_path: Path, flag: BlockFlag) -> None:
    def edit(block: Block) -> Block:
        figure = as_figure(block)
        return Block.model_validate({**figure.model_dump(), "flags": ["crop_pending", flag]})

    doc_id = import_doc(store, runtime_for(tmp_path, layout_with(EditStage(flag, edit))), GeoReader(THREE))
    assert pages_of(store, doc_id)[0].confirmed_by == "auto"


# --------------------------------------------------------------------------- #
# `_finish` trên tài liệu đã đóng / đã có khối (C4)
# --------------------------------------------------------------------------- #


class FailConfirmedStore(IngestionStore):
    """Kho in-memory mà đặt `status = "confirmed"` lệch version `failures` lần (bước (5) của settle hết lượt)."""

    def __init__(self, failures: int) -> None:
        super().__init__()
        self.failures = failures

    def update_imported_document(self, document: Any, expected_version: int) -> Any:
        if document.status == "confirmed" and self.failures > 0:
            self.failures -= 1
            raise VersionConflictError("tài liệu", document.meta.doc_id, expected_version, expected_version + 1)
        return super().update_imported_document(document, expected_version)


def _settle_then_finish(
    store: AppStore, tmp_path: Path, reader: GeoReader, before_finish: Callable[[str], None]
) -> str:
    """Đọc hết trang rồi, TRƯỚC `_finish`, chạy `before_finish(document_id)` (settle chen trước `_finish`)."""
    runtime = runtime_for(tmp_path)
    held: list[ReadJob] = []
    runtime = dataclasses.replace(runtime, queue=_Holding(held))
    doc_id = import_doc(store, runtime, reader, auto_confirm=False)
    [job] = held
    fired: list[bool] = []

    def stop_once_pages_written() -> bool:
        if not fired and all(page.status != "pending" for page in pages_of(store, doc_id)):
            fired.append(True)
            before_finish(doc_id)
        return False

    read_document(job, should_stop=stop_once_pages_written)
    return doc_id


@dataclass
class _Holding:
    held: list[ReadJob] = field(default_factory=list)

    def submit(self, job: ReadJob) -> None:
        self.held.append(job)

    def close(self, timeout_s: float = 10.0) -> bool:
        return True

    def is_active(self, document_id: str) -> bool:
        return False


def _confirm_all(store: AppStore, tmp_path: Path, document_id: str) -> None:
    confirm_document(
        store, store, runtime_for(tmp_path / "c"), document_id, DocumentConfirmRequest(editor_id="u"), now=NOW
    )


def test_settle_before_finish_closes_with_line_chunks_then_blocks_added(store: AppStore, tmp_path: Path) -> None:
    """Settle chen trước `_finish`: tài liệu đóng bằng chunk dòng; `_finish` lưu khối, đặt `blocks_after_close`, dựng
    Section và `eligible`, KHÔNG cắt thêm chunk."""
    doc_id = _settle_then_finish(store, tmp_path, GeoReader(THREE), lambda d: _confirm_all(store, tmp_path, d))
    document = document_of(store, doc_id)
    assert document.status == "confirmed" and document.layout_version is not None and document.blocks_after_close
    chunks = store.list_chunks(SUBJECT)
    assert chunks and all(chunk.block_ids == [] for chunk in chunks), "chunk là chunk dòng"
    assert store.list_sections(doc_id)
    assert store.list_eligible_blocks(doc_id)


def test_settle_fails_step_5_then_finish_sets_blocks_after_close_without_duplicate_chunks(tmp_path: Path) -> None:
    store = FailConfirmedStore(failures=3)

    def settle_fails(document_id: str) -> None:
        with pytest.raises(VersionConflictError):
            settle_document(store, store, document_id, by_user=True)

    def before(document_id: str) -> None:
        for page in pages_of(store, document_id):
            store.update_page(
                document_id,
                page.page_number,
                page.model_copy(update={"confirmed": True, "confirmed_by": "user", "version": page.version + 1}),
                status=page.status,
                confirmed=True,
                expected_version=page.version,
            )
        settle_fails(document_id)

    doc_id = _settle_then_finish(store, tmp_path, GeoReader(THREE), before)
    chunk_ids = sorted(chunk.chunk_id for chunk in store.list_chunks(SUBJECT))
    assert chunk_ids
    document = document_of(store, doc_id)
    assert document.status == "confirmed", "lần settle ở `_finish` đặt được confirmed"
    assert document.blocks_after_close and document.layout_version is not None
    assert sorted(chunk.chunk_id for chunk in store.list_chunks(SUBJECT)) == chunk_ids, "không chunk trùng"
    assert store.list_sections(doc_id) and store.list_eligible_blocks(doc_id)


def test_zero_chunk_confirmed_then_finish_builds_tree_without_flag(store: AppStore, tmp_path: Path) -> None:
    """Tài liệu đã confirmed mà Vault 0 chunk (settle đề `past_exam`… ở đây: đặt trạng thái qua kho) rồi `_finish`:
    lưu khối, dựng Section và `eligible`, không cờ `blocks_after_close`."""

    def close_without_chunks(document_id: str) -> None:
        for page in pages_of(store, document_id):
            store.update_page(
                document_id,
                page.page_number,
                page.model_copy(update={"confirmed": True, "confirmed_by": "user", "version": page.version + 1}),
                status=page.status,
                confirmed=True,
                expected_version=page.version,
            )
        document = document_of(store, document_id)
        store.update_imported_document(document.model_copy(update={"status": "confirmed"}), document.version)

    doc_id = _settle_then_finish(store, tmp_path, GeoReader(THREE), close_without_chunks)
    document = document_of(store, doc_id)
    assert document.status == "confirmed" and document.layout_version is not None
    assert not document.blocks_after_close
    assert store.list_chunks(SUBJECT) == []
    sections = store.list_sections(doc_id)
    assert sections and any(section.kind == "section" for section in sections)
    assert store.list_eligible_blocks(doc_id)


def test_second_finish_on_closed_document_keeps_blocks(store: AppStore, tmp_path: Path) -> None:
    """`_finish` lần hai trên tài liệu ĐÃ ĐÓNG có khối: khối, ledger, hash giữ nguyên (kể cả khi cấu hình khác hash);
    Section và `eligible` được bảo đảm; không chunk mới."""
    reader = GeoReader(THREE)
    doc_id = import_doc(store, runtime_for(tmp_path), reader)
    _confirm_all(store, tmp_path, doc_id)
    before = (store.list_blocks(doc_id), store.list_extraction_ledgers(doc_id), document_of(store, doc_id))
    chunks = sorted(chunk.chunk_id for chunk in store.list_chunks(SUBJECT))
    other = layout_with(EditStage("khac", flagged("layout_uncertain"), frozenset({1, 2, 3})))
    finish_again(store, doc_id, reader, other)
    after = document_of(store, doc_id)
    assert store.list_blocks(doc_id) == before[0] and store.list_extraction_ledgers(doc_id) == before[1]
    assert (after.layout_version, after.layout_settings_sha256) == (
        before[2].layout_version,
        before[2].layout_settings_sha256,
    )
    assert not after.blocks_after_close and after.status == "confirmed"
    assert sorted(chunk.chunk_id for chunk in store.list_chunks(SUBJECT)) == chunks
    assert store.list_sections(doc_id) and store.list_eligible_blocks(doc_id)


def test_second_finish_on_open_document_keeps_blocks_and_hash(store: AppStore, tmp_path: Path) -> None:
    """`_finish` lần hai trên tài liệu CHƯA đóng: không dựng lại, không ghi đè khối/ledger/hash; không Section."""
    reader = GeoReader(THREE)
    doc_id = import_doc(store, runtime_for(tmp_path), reader, auto_confirm=False)
    before = (store.list_blocks(doc_id), store.list_extraction_ledgers(doc_id), document_of(store, doc_id))
    finish_again(store, doc_id, reader, layout_with(EditStage("khac", flagged("layout_uncertain"))))
    after = document_of(store, doc_id)
    assert store.list_blocks(doc_id) == before[0] and store.list_extraction_ledgers(doc_id) == before[1]
    assert after.layout_settings_sha256 == before[2].layout_settings_sha256
    assert store.list_sections(doc_id) == [] and store.list_eligible_blocks(doc_id) == []


# --------------------------------------------------------------------------- #
# Router: bộ đọc hình học (C1), đường nhập thật (Q1), C7, I3 trên khối `code`
# --------------------------------------------------------------------------- #


@pytest.fixture
def client_env(tmp_path: Path) -> Iterator[tuple[TestClient, AppStore, list[ImportRuntime]]]:
    store = IngestionStore()
    holder: list[ImportRuntime] = [runtime_for(tmp_path)]
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_import_runtime] = lambda: holder[0]
    app.dependency_overrides[get_clock] = lambda: lambda: NOW
    try:
        yield TestClient(app), store, holder
    finally:
        app.dependency_overrides.clear()


def _post(client: TestClient, content: bytes, *, auto_confirm: str = "true") -> DocumentImport:
    response = client.post(
        "/v1/documents",
        data={
            "subject_code": SUBJECT,
            "source_kind": "lecture_slides",
            "chapter_code": "ch2",
            "auto_confirm": auto_confirm,
        },
        files={"file": ("mau.pdf", content, "application/pdf")},
    )
    assert response.status_code == 202, response.text
    return DocumentImport.model_validate(response.json())


def _body_runs(top: float = 700.0) -> list[Run]:
    return [
        Run(72, top + 20, "Gioi thieu bo xu ly", size=18, bold=True),
        Run(72, top - 20, BODY[0]),
        Run(72, top - 34, BODY[1]),
    ]


def test_real_import_path_never_null_layout_version(
    client_env: tuple[TestClient, AppStore, list[ImportRuntime]],
) -> None:
    """Đường nhập thật (`get_pdf_reader` mặc định → `WorkerPdfReader` có `PageGeometrySource`) luôn ghi
    `layout_version`/hash khác null."""
    client, store, _ = client_env
    created = _post(client, build_pdf([_body_runs(), _body_runs(600)]))
    body = DocumentImport.model_validate(client.get(f"/v1/documents/{created.meta.doc_id}").json())
    assert body.layout_version is not None and body.layout_settings_sha256 is not None
    assert store.list_blocks(created.meta.doc_id)


def test_router_reader_override_gives_no_geometry_reader(
    client_env: tuple[TestClient, AppStore, list[ImportRuntime]],
) -> None:
    """Test ghi đè `get_pdf_reader` bằng bộ đọc khác ⇒ `geometry_reader` None ⇒ đường cũ (Q1 = A), kể cả khi bộ đọc
    ghi đè có hình học."""
    client, store, _ = client_env
    reader = GeoReader(THREE)
    app.dependency_overrides[get_pdf_reader] = lambda: reader
    created = _post(client, pdf_bytes("ghi-de"))
    body = DocumentImport.model_validate(client.get(f"/v1/documents/{created.meta.doc_id}").json())
    assert body.layout_version is None and body.layout_settings_sha256 is None and not body.blocks_after_close
    assert store.list_blocks(created.meta.doc_id) == []
    assert reader.reads == 1


def _mono_runs() -> list[Run]:
    """Một đoạn chữ thường và hai đoạn chương trình đơn cách (Courier) cách nhau xa: hai ứng viên gộp `code`."""
    code = ["for i in range(10):", "    total = total + i", "    print(total)"]
    runs = _body_runs()
    runs += [Run(72, 560 - 13 * i, text, size=10, mono=True) for i, text in enumerate(code)]
    runs += [Run(72, 400 - 13 * i, text, size=10, mono=True) for i, text in enumerate(code)]
    return runs


def test_code_layout_uncertain_on_merge_cap_does_not_block(
    client_env: tuple[TestClient, AppStore, list[ImportRuntime]], tmp_path: Path
) -> None:
    """C7 (người dùng 2026-11-03; nghĩa (b) của `code_layout_uncertain`): trang vượt `max_merge_candidates` ⇒ khối
    `paragraph`/`list_item` mang cờ, trang KHÔNG `LAYOUT_FAILED`, không lý do xem lại, tự xác nhận, khối đó đủ điều kiện
    sau khi xác nhận tài liệu. Cùng PDF với cấu hình mặc định: cùng trạng thái trang, khối thành `code`."""
    client, store, holder = client_env
    content = build_pdf([_mono_runs()])
    capped = CodeBlockStage(CodeBlockSettings(max_merge_candidates=1))
    holder[0] = runtime_for(
        tmp_path / "cap",
        dataclasses.replace(default_layout_config(LANG), page_stages=(BaseBlockStage(), capped, ReadingOrderStage())),
    )
    created = _post(client, content)
    doc_id = created.meta.doc_id
    page = client.get(f"/v1/documents/{doc_id}/pages/1").json()
    assert page["review_reasons"] == [] and page["confirmed_by"] == "auto"
    body = DocumentImport.model_validate(client.get(f"/v1/documents/{doc_id}").json())
    assert body.review_reason_counts == [] and body.pages_auto_confirmed == 1
    uncertain = [b for b in store.list_blocks(doc_id) if "code_layout_uncertain" in b.flags]
    assert uncertain and all(b.type in ("paragraph", "list_item") for b in uncertain)
    assert client.post(f"/v1/documents/{doc_id}/confirm", json={"editor_id": "u"}).status_code == 200
    eligible = {b.block_id for b in store.list_eligible_blocks(doc_id)}
    assert {b.block_id for b in uncertain} <= eligible

    store.delete_document(doc_id)
    holder[0] = runtime_for(tmp_path / "default")
    again = _post(client, content)
    page = client.get(f"/v1/documents/{again.meta.doc_id}/pages/1").json()
    assert page["review_reasons"] == [] and page["confirmed_by"] == "auto"
    blocks = store.list_blocks(again.meta.doc_id)
    assert any(b.type == "code" for b in blocks)
    assert not any("code_layout_uncertain" in b.flags and b.type != "code" for b in blocks)


def test_code_block_quote_is_verbatim_end_to_end(
    client_env: tuple[TestClient, AppStore, list[ImportRuntime]],
) -> None:
    """I3 đầu-cuối: PDF có font đơn cách ⇒ ≥ 1 khối `code` đã lưu; sau khi xác nhận, trích dẫn của mọi chunk là chuỗi con
    của `raw_text` (kiểm của `KnowledgeChunk`), và chữ mỗi khối `code` nằm nguyên trong chunk chứa nó."""
    client, store, _ = client_env
    created = _post(client, build_pdf([_mono_runs()]))
    doc_id = created.meta.doc_id
    codes = [b for b in store.list_blocks(doc_id) if b.type == "code"]
    assert codes
    assert client.post(f"/v1/documents/{doc_id}/confirm", json={"editor_id": "u"}).status_code == 200
    chunks = store.list_chunks(SUBJECT)
    assert chunks
    for block in codes:
        holder = [chunk for chunk in chunks if block.block_id in chunk.block_ids]
        assert holder and block.text is not None and block.text in holder[0].raw_text
    for chunk in chunks:
        assert quote_in_text(chunk.verbatim_quote, chunk.raw_text)


def test_patch_lines_without_layout_keeps_legacy_write(store: AppStore, tmp_path: Path) -> None:
    """Tài liệu `layout_version` null: `PATCH …/lines` ghi trang như cũ, không tạo khối."""
    runtime = runtime_for(tmp_path)
    doc_id = import_doc(store, runtime, GeoReader(THREE), geometry=False, auto_confirm=False)
    page = pages_of(store, doc_id)[0]
    patch = PageLinesPatch(editor_id="u", expected_version=page.version, edits=[LineEdit(line_index=1, text="Moi")])
    edited = edit_page_lines(store, store, runtime, doc_id, 1, patch, now=NOW)
    assert edited.lines[1].text == "Moi" and store.list_blocks(doc_id) == []
    assert len(store.list_page_audits(doc_id, 1)) == 1


def test_drop_reason_follows_page_role_changes(store: AppStore, tmp_path: Path) -> None:
    """`page_drop_reason` của ledger ghi lại sau đổi vai trò (bỏ mềm) và sau `…/restore` (cùng giao dịch với trang)."""
    runtime = runtime_for(tmp_path)
    doc_id = import_doc(store, runtime, GeoReader(THREE), auto_confirm=False)
    page = pages_of(store, doc_id)[0]
    evidence = PageRoleEvidence(rule="cover_or_course_nav", related_pages=[PageRef(document_id=doc_id, page_number=2)])
    dropped = page.model_copy(
        update={
            "page_role": "cover_or_course_nav",
            "role_evidence": evidence,
            "dropped": True,
            "version": page.version + 1,
        }
    )
    store.update_page(doc_id, 1, dropped, status=dropped.status, confirmed=False, expected_version=page.version)

    def reason() -> str | None:
        [ledger] = [item for item in store.list_extraction_ledgers(doc_id) if item.page_number == 1]
        return ledger.page_drop_reason

    assert reason() is None
    refresh_drop_reasons(store, doc_id)
    assert reason() == "cover_or_course_nav"
    blocks = store.list_blocks(doc_id, 1)
    restored = restore_page(
        store, store, runtime, doc_id, 1, PageRestoreRequest(editor_id="u", expected_version=dropped.version)
    )
    assert not restored.dropped and restored.restored
    stored = pages_of(store, doc_id)[0]
    assert stored.version == restored.version and not stored.dropped
    assert reason() is None and store.list_blocks(doc_id, 1) == blocks


def test_reader_without_geometry_from_load_pdf_reader_keeps_old_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Q1 = A: `load_pdf_reader` bị thay bằng stub chỉ cài `PdfReader` ⇒ không có vai hình học (đường cũ); bộ đọc
    thật ⇒ CÙNG đối tượng ở cả hai vai."""
    runtime = runtime_for(tmp_path)
    real = documents_router.get_request_readers(runtime)
    assert real.geometry is real.reader
    stub = FakePdfReader()
    monkeypatch.setattr(documents_router, "load_pdf_reader", lambda config: stub)
    readers = documents_router.get_request_readers(runtime)
    assert readers.reader is stub and readers.geometry is None
