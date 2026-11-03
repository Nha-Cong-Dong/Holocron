"""Cây mục và tập khối đủ điều kiện theo "tài liệu đã đóng" lúc truy vấn (SPECIFICATION §5.15, §6.10; ADR-010 mục 13;
X09).

Tài liệu nhập qua `accept_document` với bộ đọc giả có hình học (`tests/test_import_blocks.py`), đọc qua TestClient và
method của kho; không mạng, không tài liệu thật.
"""

from __future__ import annotations

import dataclasses
import random
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from holocron_core.domain.blocks import Block
from holocron_core.domain.documents import (
    DocumentConfirmRequest,
    PageConfirmRequest,
    PageUnconfirmRequest,
)
from holocron_core.domain.repositories import AppStore, StorageBusyError, VersionConflictError
from holocron_core.domain.schemas import SourceKind
from holocron_core.domain.vault import SubjectTree
from holocron_core.infrastructure.memory_store import IngestionStore
from holocron_core.routers.api import app, get_store
from holocron_core.services.block_pipeline import PageContext
from holocron_core.services.document_import import (
    ImportRuntime,
    confirm_document,
    confirm_page,
    settle_document,
    unconfirm_page,
)
from tests.block_samples import geo_line, geometry
from tests.test_import_blocks import (
    NOW,
    SUBJECT,
    EditStage,
    GeoReader,
    SamplePage,
    as_figure,
    as_table,
    import_doc,
    layout_with,
    pages_of,
    runtime_for,
    store,
)

__all__ = ["store"]


_WORDS = [
    "thanh",
    "ghi",
    "bo",
    "nho",
    "ngat",
    "ngoai",
    "vi",
    "dia",
    "chi",
    "lenh",
    "du",
    "lieu",
    "bus",
    "xung",
    "nhip",
    "ngan",
    "xep",
    "con",
    "tro",
    "bang",
    "trang",
    "khung",
    "muc",
    "tien",
    "trinh",
    "luong",
    "khoa",
    "hang",
    "doi",
    "bo",
    "dem",
    "cache",
    "dong",
    "cot",
    "hang",
    "so",
    "nguyen",
    "thuc",
    "chuoi",
    "mang",
    "cay",
    "do",
    "thi",
    "canh",
    "dinh",
]


def _sentence(seed: str, words: int = 12) -> str:
    rng = random.Random(seed)
    return " ".join(rng.choice(_WORDS) for _ in range(words))


def distinct_page(tag: str, number: int) -> SamplePage:
    """Trang chữ riêng theo `(tag, number)`: tiêu đề, đoạn hai dòng và đoạn thứ hai (0D không coi là trang lặp)."""
    lines = [geo_line(f"{number}. {_sentence(f'{tag}-{number}-t', 3)}", 0.1, 0.08, size=20.0, font_id=1)]
    lines += [geo_line(_sentence(f"{tag}-{number}-{i}"), 0.1, 0.2 + 0.03 * i) for i in range(2)]
    lines.append(geo_line(_sentence(f"{tag}-{number}-x"), 0.1, 0.5))
    return SamplePage(geometry(lines))


def pages_for(tag: str, count: int = 2) -> list[SamplePage]:
    """Trang có chữ riêng theo `tag` (tài liệu khác nhau không giống nhau ở 0D)."""
    return [distinct_page(tag, n) for n in range(1, count + 1)]


def body_only(tag: str, count: int = 2) -> list[SamplePage]:
    """Trang chỉ một đoạn (không tiêu đề): đổi đoạn thành `table` ⇒ tài liệu không có khối chữ, 0 chunk."""
    return [
        SamplePage(geometry([geo_line(f"Bang du lieu {tag} trang {n} gom nhieu cot so lieu", 0.1, 0.3)]))
        for n in range(1, count + 1)
    ]


TABLES = EditStage("bang", as_table(), frozenset({1, 2}))


@pytest.fixture
def client(store: AppStore) -> Iterator[TestClient]:
    app.dependency_overrides[get_store] = lambda: store
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def tree(client: TestClient) -> SubjectTree:
    response = client.get("/v1/vault/tree", params={"subject_code": SUBJECT})
    assert response.status_code == 200, response.text
    return SubjectTree.model_validate(response.json())


def confirm_all(store: AppStore, runtime: ImportRuntime, document_id: str) -> None:
    confirm_document(store, store, runtime, document_id, DocumentConfirmRequest(editor_id="u"), now=NOW)


def tree_block_ids(source: Any) -> set[str]:
    """Mọi `block_ids` của các Section trong cây của một nguồn."""
    out: set[str] = set()
    stack = [] if source.root is None else [source.root]
    while stack:
        node = stack.pop()
        out.update(node.section.block_ids)
        stack.extend(node.children)
    return out


def chunk_block_ids(store: AppStore) -> set[str]:
    return {block_id for chunk in store.list_chunks(SUBJECT) for block_id in chunk.block_ids}


def eligible_ids(store: AppStore, document_id: str) -> set[str]:
    return {block.block_id for block in store.list_eligible_blocks(document_id)}


def test_blocks_not_eligible_before_document_confirm(store: AppStore, client: TestClient, tmp_path: Path) -> None:
    """Trang tự xác nhận nhưng tài liệu chưa đóng: không khối đủ điều kiện, không chunk, cây không có nguồn; sau khi
    xác nhận tài liệu cây có nguồn với khối của mọi trang, và chunk dùng cùng tập."""
    runtime = runtime_for(tmp_path)
    doc_id = import_doc(store, runtime, GeoReader(pages_for("a")))
    assert all(page.confirmed_by == "auto" for page in pages_of(store, doc_id))
    assert eligible_ids(store, doc_id) == set() and store.list_chunks(SUBJECT) == []
    assert tree(client).sources == []
    confirm_all(store, runtime, doc_id)
    [source] = tree(client).sources
    assert source.document_id == doc_id and source.root is not None
    eligible = eligible_ids(store, doc_id)
    assert eligible and chunk_block_ids(store) <= eligible
    assert tree_block_ids(source) & eligible == eligible


class FailingStore(IngestionStore):
    """Kho in-memory làm hỏng bước (4) (`refresh_eligible_blocks`) hoặc bước (5) (đặt `confirmed`) của settle."""

    def __init__(self) -> None:
        super().__init__()
        self.fail_refresh = False
        self.fail_confirmed = False

    def refresh_eligible_blocks(self, document_id: str) -> int:
        if self.fail_refresh:
            raise StorageBusyError("kho bận (test)")
        return super().refresh_eligible_blocks(document_id)

    def update_imported_document(self, document: Any, expected_version: int) -> Any:
        if self.fail_confirmed and document.status == "confirmed":
            raise VersionConflictError("tài liệu", document.meta.doc_id, expected_version, expected_version + 1)
        return super().update_imported_document(document, expected_version)


@pytest.fixture
def failing(tmp_path: Path) -> Iterator[tuple[FailingStore, TestClient]]:
    failing_store = FailingStore()
    app.dependency_overrides[get_store] = lambda: failing_store
    try:
        yield failing_store, TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_eligibility_consistent_in_settle_pending_window(
    failing: tuple[FailingStore, TestClient], tmp_path: Path
) -> None:
    """Bước (5) hết lượt: Vault có chunk, tài liệu chưa `confirmed` ⇒ đã đóng (cửa sổ settle-pending): chunk, cây và
    tập đủ điều kiện dùng cùng tập khối; tài liệu chưa đóng khác không hiện."""
    store, client = failing
    runtime = runtime_for(tmp_path)
    doc_id = import_doc(store, runtime, GeoReader(pages_for("a")))
    hidden = import_doc(store, runtime_for(tmp_path / "b"), GeoReader(pages_for("b")), tag="b")
    store.fail_confirmed = True
    with pytest.raises(VersionConflictError):
        settle_document(store, store, doc_id, by_user=True)
    document = store.get_imported_document(doc_id)
    assert document is not None and document.status != "confirmed" and store.has_document_chunks(doc_id)
    [source] = tree(client).sources
    assert source.document_id == doc_id and source.document_id != hidden
    eligible = eligible_ids(store, doc_id)
    assert eligible and chunk_block_ids(store) <= eligible
    assert tree_block_ids(source) & eligible == eligible
    section = source.root
    assert section is not None
    body = client.get(f"/v1/documents/{doc_id}/sections/{section.section.section_id}/blocks").json()
    assert {block["block_id"] for block in body["blocks"]} <= eligible


def test_zero_chunk_document_still_has_tree(store: AppStore, client: TestClient, tmp_path: Path) -> None:
    """Tài liệu chỉ có khối `table` (không khối chữ): 0 chunk nhưng vẫn đóng và có cây với khối đủ điều kiện."""
    runtime = runtime_for(tmp_path, layout_with(TABLES))
    doc_id = import_doc(store, runtime, GeoReader(body_only("a")))
    assert {block.type for block in store.list_blocks(doc_id)} == {"table"}
    confirm_all(store, runtime, doc_id)
    document = store.get_imported_document(doc_id)
    assert document is not None and document.status == "confirmed"
    assert store.list_chunks(SUBJECT) == []
    [source] = tree(client).sources
    assert source.root is not None and tree_block_ids(source) == eligible_ids(store, doc_id) != set()


def test_pages_confirmed_after_finish_appear_in_tree_and_chunks(
    store: AppStore, client: TestClient, tmp_path: Path
) -> None:
    """Trang xác nhận từng trang SAU `_finish`: trang cuối đóng tài liệu; khối của mọi trang vào cây và chunk."""
    runtime = runtime_for(tmp_path)
    doc_id = import_doc(store, runtime, GeoReader(pages_for("a", 3)), auto_confirm=False)
    for page in pages_of(store, doc_id):
        confirm_page(
            store,
            store,
            runtime,
            doc_id,
            page.page_number,
            PageConfirmRequest(editor_id="u", expected_version=page.version),
            now=NOW,
        )
    [source] = tree(client).sources
    eligible = eligible_ids(store, doc_id)
    assert {block.page_number for block in store.list_eligible_blocks(doc_id)} == {1, 2, 3}
    assert tree_block_ids(source) & eligible == eligible
    assert {block.page_number for block in store.list_blocks(doc_id) if block.block_id in chunk_block_ids(store)} == {
        1,
        2,
        3,
    }


def test_settle_windows_3_4_and_4_5(failing: tuple[FailingStore, TestClient], tmp_path: Path) -> None:
    """Cửa sổ (3)→(4): chunk có, `eligible` chưa đặt ⇒ cây thấy tài liệu nhưng ÍT hơn chunk (không khối nào).
    Cửa sổ (4)→(5) với tài liệu 0 chunk: khối đã `eligible` nhưng tài liệu chưa đóng ⇒ cây không hiện tài liệu."""
    store, client = failing
    runtime = runtime_for(tmp_path)
    doc_id = import_doc(store, runtime, GeoReader(pages_for("a")))
    store.fail_refresh = True
    with pytest.raises(StorageBusyError):
        settle_document(store, store, doc_id, by_user=True)
    assert store.has_document_chunks(doc_id) and eligible_ids(store, doc_id) == set()
    [source] = tree(client).sources
    assert source.document_id == doc_id and source.root is None and tree_block_ids(source) == set()
    store.fail_refresh = False

    zero_runtime = runtime_for(tmp_path / "b", layout_with(TABLES))
    zero = import_doc(store, zero_runtime, GeoReader(body_only("b")), tag="b")
    store.fail_confirmed = True
    with pytest.raises(VersionConflictError):
        settle_document(store, store, zero, by_user=True)
    assert eligible_ids(store, zero) and not store.has_document_chunks(zero)
    assert zero not in {item.document_id for item in tree(client).sources}
    sections = store.list_sections(zero)
    assert sections
    body = client.get(f"/v1/documents/{zero}/sections/{sections[0].section_id}/blocks").json()
    assert body["total"] == 0 and body["blocks"] == []


def test_settle_version_conflict_then_unconfirm_hides_document(
    failing: tuple[FailingStore, TestClient], tmp_path: Path
) -> None:
    """Bước (5) hết lượt trên tài liệu 0 chunk rồi người dùng hoàn tác một trang: cây vẫn không hiện tài liệu; lần
    settle sau tính lại từ (1) và đóng tài liệu."""
    store, client = failing
    runtime = runtime_for(tmp_path, layout_with(TABLES))
    doc_id = import_doc(store, runtime, GeoReader(body_only("a")))
    store.fail_confirmed = True
    confirm_all(store, runtime, doc_id)
    document = store.get_imported_document(doc_id)
    assert document is not None and document.status != "confirmed" and eligible_ids(store, doc_id)
    page = pages_of(store, doc_id)[0]
    unconfirm_page(store, store, runtime, doc_id, 1, PageUnconfirmRequest(editor_id="u", expected_version=page.version))
    assert tree(client).sources == []
    store.fail_confirmed = False
    page = pages_of(store, doc_id)[0]
    confirm_page(
        store, store, runtime, doc_id, 1, PageConfirmRequest(editor_id="u", expected_version=page.version), now=NOW
    )
    confirm_all(store, runtime, doc_id)
    [source] = tree(client).sources
    assert source.document_id == doc_id and source.root is not None


def test_figure_only_page_counted_unconfirmable(store: AppStore, client: TestClient, tmp_path: Path) -> None:
    """Trang có khối (hình) mà không có dòng có chữ (chỉ dòng lề) không xác nhận được: đếm ở
    `pages_unconfirmable_with_blocks`, khối của nó không đủ điều kiện."""

    def to_figure(block: Block) -> Block:
        return as_figure(block.model_copy(update={"type": "paragraph"})) if block.type == "header_footer" else block

    figure_page = SamplePage(geometry([geo_line("Hinh minh hoa bo xu ly", 0.1, 0.3, header_footer=True)]))
    stage = dataclasses.replace(EditStage("hinh", to_figure, frozenset({2})), name="test_figure")
    runtime = runtime_for(tmp_path, layout_with(_AllBlocks(stage)))
    doc_id = import_doc(store, runtime, GeoReader([pages_for("a", 1)[0], figure_page]))
    assert [block.type for block in store.list_blocks(doc_id, 2)] == ["figure"]
    confirm_all(store, runtime, doc_id)
    [source] = tree(client).sources
    assert source.pages_unconfirmable_with_blocks == [2]
    assert {block.page_number for block in store.list_eligible_blocks(doc_id)} == {1}


@dataclasses.dataclass(frozen=True)
class _AllBlocks:
    """Bọc `EditStage` để áp lên MỌI loại khối (không chỉ `paragraph`)."""

    inner: EditStage
    name: str = "test_all_blocks"

    def fingerprint(self) -> dict[str, Any]:
        return {"name": self.name, "version": 1, "settings": self.inner.fingerprint()}

    def run(self, context: PageContext, blocks: list[Block]) -> list[Block]:
        if context.page_number not in self.inner.pages:
            return blocks
        return [self.inner.edit(block) for block in blocks]


def test_legacy_document_has_null_root_and_pages_without_blocks(
    store: AppStore, client: TestClient, tmp_path: Path
) -> None:
    """Tài liệu `layout_version` null (bộ đọc không có hình học): `root` null, mọi trang có nội dung ở
    `pages_without_blocks`."""
    runtime = runtime_for(tmp_path)
    doc_id = import_doc(store, runtime, GeoReader(pages_for("a", 3)), geometry=False)
    confirm_all(store, runtime, doc_id)
    [source] = tree(client).sources
    assert source.layout_version is None and source.root is None
    assert source.pages_without_blocks == [1, 2, 3] and source.layout_failed_pages == []


def test_layout_failed_page_listed(store: AppStore, client: TestClient, tmp_path: Path) -> None:
    pages = pages_for("a", 2)
    pages[1] = dataclasses.replace(pages[1], send_geometry=False)
    runtime = runtime_for(tmp_path)
    doc_id = import_doc(store, runtime, GeoReader(pages))
    page = pages_of(store, doc_id)[1]
    confirm_page(
        store, store, runtime, doc_id, 2, PageConfirmRequest(editor_id="u", expected_version=page.version), now=NOW
    )
    confirm_all(store, runtime, doc_id)
    [source] = tree(client).sources
    assert source.layout_failed_pages == [2] and source.pages_without_blocks == []


def test_chapter_shared_with_only_closed_non_exam_documents(
    store: AppStore, client: TestClient, tmp_path: Path
) -> None:
    """`chapter_shared_with`: tài liệu khác cùng môn, cùng chương, ĐÃ ĐÓNG, không `past_exam`; theo `uploaded_at`."""
    runtime = runtime_for(tmp_path)
    first = import_doc(store, runtime, GeoReader(pages_for("a")), tag="a")
    second = import_doc(store, runtime_for(tmp_path / "b"), GeoReader(pages_for("b")), tag="b")
    import_doc(store, runtime_for(tmp_path / "c"), GeoReader(pages_for("c")), tag="c")
    exam = import_doc(
        store, runtime_for(tmp_path / "d"), GeoReader(pages_for("d")), tag="d", source_kind=SourceKind.PAST_EXAM
    )
    for document_id in (first, second, exam):
        confirm_all(store, runtime, document_id)
    sources = {source.document_id: source for source in tree(client).sources}
    assert set(sources) == {first, second}
    assert sources[first].chapter_shared_with == [second]
    assert sources[second].chapter_shared_with == [first]
