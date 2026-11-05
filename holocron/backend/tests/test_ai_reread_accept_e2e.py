"""Đọc lại bằng AI rồi Nhận trên tài liệu nhập qua LUỒNG THẬT có khối (SPECIFICATION §6.10a; ADR-009 mục 2, 3.4–3.6).

Tài liệu đi qua `accept_document` với bộ đọc giả có hình học (`tests/import_blocks_support.py`); một stage tiêm vào
`LayoutConfig` biến đoạn thứ hai của trang 1 thành hình (`figure`, `table_unresolved`) có vùng rộng chứa một dòng OCR
độc lập (`PageGeometry.ocr_lines`), như bảng ảnh. Mọi lời gọi VLM qua transport giả; bộ cắt ảnh là bản giả.

I3: chữ VLM chỉ vào kho sau cổng và sau khi người dùng Nhận; I5: trang có khối `vlm` không tự xác nhận (kể cả khi
chạy lại bước kết thúc), không vào xác nhận theo lô, còn ô bảng lệch thì `POST …/confirm` 409.
"""

from __future__ import annotations

import dataclasses
import http.client
import socket
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from holocron_core.domain.blocks import Block, OcrWordRef
from holocron_core.domain.documents import PageExtraction
from holocron_core.domain.repositories import AppStore
from holocron_core.infrastructure.ai_runtime import InMemorySpendLedger
from holocron_core.infrastructure.llm_config import load_runtime_config
from holocron_core.routers import documents
from holocron_core.routers.deps import (
    get_ai_consent,
    get_clock,
    get_llm_transport_factory,
    get_provider_selection,
    get_runtime_config,
    get_store,
)
from holocron_core.services.auto_confirm import is_trusted_source
from holocron_core.services.block_pipeline import PAGE_STAGES, PageContext, default_layout_config
from holocron_core.services.document_import import ImportRuntime
from holocron_core.services.provider_selection import ProviderSelection
from tests.block_samples import geo_line, geometry
from tests.import_blocks_support import (
    BODY,
    LANG,
    SECOND,
    GeoReader,
    SamplePage,
    finish_again,
    import_doc,
    pages_of,
    runtime_for,
    text_page,
)
from tests.test_ai_reread import FakeRenderer, FakeTransports, chat_body
from tests.test_block_store import close, open_kind
from tests.vision_support import NOW, Switch, candidate

OCR_TEXT = "Thanh ghi R13 chua gia tri 42"
FIGURE_BOX = (0.05, 0.45, 0.95, 0.9)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("test không được gọi mạng")

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(http.client.HTTPConnection, "connect", refuse)


@dataclass(frozen=True)
class ScannedTableFigure:
    """Stage tiêm (sau mọi stage, không đổi id): đoạn bắt đầu bằng `SECOND` ở trang 1 thành `figure` vùng
    `FIGURE_BOX` mang `table_unresolved`; nhãn gồm từ lớp chữ của đoạn và mọi từ OCR độc lập của trang (ledger cân
    cả basis `ocr`)."""

    name: str = "test_scanned_table_figure"

    def fingerprint(self) -> dict[str, Any]:
        return {"name": self.name, "version": 1, "settings": {}}

    def run(self, context: PageContext, blocks: list[Block]) -> list[Block]:
        if context.page_number != 1:
            return blocks
        ocr_refs = [
            OcrWordRef(line_idx=line_idx, word_idx=word_idx)
            for line_idx, line in enumerate(context.geometry.ocr_lines)
            for word_idx in range(len(line.words))
        ]
        out: list[Block] = []
        for block in blocks:
            if block.type == "paragraph" and block.text is not None and block.text.startswith(SECOND[0][:12]):
                data = {**block.model_dump(), "type": "figure", "text": None, "line_refs": [], "bbox": FIGURE_BOX}
                refs = [ref.model_dump() for ref in [*block.line_refs, *ocr_refs]]
                block = Block.model_validate(
                    {
                        **data,
                        "label_line_refs": refs,
                        "labels": [block.text],
                        "flags": ["crop_pending", "table_unresolved"],
                    }
                )
            out.append(block)
        return out


def scanned_page() -> SamplePage:
    base = text_page("1. Gioi thieu", extra=SECOND).geometry
    ocr = [geo_line(OCR_TEXT, 0.1, 0.7)]
    total = sum(len(word.text) for line in ocr for word in line.words)
    return SamplePage(geometry(base.lines, ocr_lines=ocr, ocr_nonws_total=total))


@pytest.fixture(params=["memory", "sqlite"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[AppStore]:
    opened = open_kind(str(request.param), tmp_path)
    try:
        yield opened
    finally:
        close(opened)


@dataclass
class Flow:
    client: TestClient
    store: AppStore
    runtime: ImportRuntime
    reader: GeoReader
    doc_id: str
    transports: FakeTransports

    def page(self, number: int = 1) -> PageExtraction:
        page = self.store.get_page(self.doc_id, number, PageExtraction)
        assert page is not None
        return page

    def figure(self) -> Block:
        [block] = [b for b in self.store.list_blocks(self.doc_id, 1) if "table_unresolved" in b.flags]
        return block

    def block(self, block_id: str) -> Block:
        [block] = [b for b in self.store.list_blocks(self.doc_id, 1) if b.block_id == block_id]
        return block

    def url(self, block_id: str) -> str:
        return f"/v1/documents/{self.doc_id}/pages/1/blocks/{block_id}/ai-reread"

    def reread(self, block_id: str) -> dict[str, Any]:
        response = self.client.post(self.url(block_id))
        assert response.status_code == 200, response.text
        body: dict[str, Any] = response.json()
        return body

    def accept(self, block_id: str, proposal_id: str, version: int) -> tuple[int, Any]:
        response = self.client.post(
            f"{self.url(block_id)}/{proposal_id}/accept", json={"editor_id": "u1", "expected_version": version}
        )
        return response.status_code, response.json()

    def patch_title(self, text: str) -> PageExtraction:
        response = self.client.patch(
            f"/v1/documents/{self.doc_id}/pages/1/lines",
            json={
                "editor_id": "u1",
                "expected_version": self.page().version,
                "edits": [{"line_index": 0, "text": text}],
            },
        )
        assert response.status_code == 200, response.text
        return PageExtraction.model_validate(response.json())


def make_flow(store: AppStore, tmp_path: Path, reply: list[dict[str, object]]) -> Flow:
    layout = dataclasses.replace(default_layout_config(LANG), page_stages=(*PAGE_STAGES, ScannedTableFigure()))
    runtime = runtime_for(tmp_path, layout)
    reader = GeoReader([scanned_page(), text_page("2. Kien truc"), text_page("3. Bo nho")])
    doc_id = import_doc(store, runtime, reader)
    transports = FakeTransports((200, chat_body(reply)))
    selection = ProviderSelection(lambda: [candidate()], InMemorySpendLedger())
    app = FastAPI()
    app.include_router(documents.router)
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[documents.get_import_runtime] = lambda: runtime
    app.dependency_overrides[documents.get_region_renderer] = lambda: FakeRenderer()
    app.dependency_overrides[documents.get_vlm_mode] = lambda: "unset"
    app.dependency_overrides[get_ai_consent] = lambda: Switch(image=False)
    app.dependency_overrides[get_provider_selection] = lambda: selection
    app.dependency_overrides[get_llm_transport_factory] = lambda: transports
    app.dependency_overrides[get_runtime_config] = lambda: load_runtime_config({})
    app.dependency_overrides[get_clock] = lambda: lambda: NOW
    return Flow(TestClient(app), store, runtime, reader, doc_id, transports)


def by_type(store: AppStore, doc_id: str) -> dict[str, int]:
    [ledger] = [item for item in store.list_extraction_ledgers(doc_id) if item.page_number == 1]
    assert ledger.balanced
    return {item.type: item.count for item in ledger.by_type}


TEXT_REPLY: list[dict[str, object]] = [{"type": "paragraph", "text": OCR_TEXT}]


def test_ai_reread_accept_end_to_end_on_imported_document(store: AppStore, tmp_path: Path) -> None:
    flow = make_flow(store, tmp_path, TEXT_REPLY)
    before = flow.page()
    figure = flow.figure()
    assert before.status == "ok" and not before.confirmed  # khối chặn tự xác nhận lúc nhập
    assert all(flow.page(n).confirmed_by == "auto" for n in (2, 3))
    types_before = by_type(store, flow.doc_id)
    proposal = flow.reread(figure.block_id)
    assert proposal["status"] == "accepted_text" and proposal["ocr_text"] == OCR_TEXT
    status, body = flow.accept(figure.block_id, str(proposal["proposal_id"]), before.version)
    assert status == 200, body
    page = flow.page()
    assert page.version == before.version + 1 and page.review_reasons == ["VLM_REGION"]
    assert page.status == "needs_review" and not page.confirmed and not is_trusted_source(page)
    block = flow.block(figure.block_id)
    assert (block.source, block.type, block.text, block.ocr_text) == ("vlm", "paragraph", OCR_TEXT, OCR_TEXT)
    assert block.line_refs == figure.label_line_refs and block.bbox == figure.bbox
    # Ledger vẫn cân; đơn vị của vùng chuyển từ figure sang paragraph.
    types_after = by_type(store, flow.doc_id)
    assert "figure" not in types_after
    assert types_after["paragraph"] == types_before.get("paragraph", 0) + types_before["figure"]
    # Chạy lại bước kết thúc (như khởi động lại) không tự xác nhận trang có khối vlm.
    finish_again(store, flow.doc_id, flow.reader, flow.runtime.layout)
    assert not flow.page().confirmed
    # Xác nhận theo lô bỏ qua trang có VLM_REGION.
    response = flow.client.post(f"/v1/documents/{flow.doc_id}/confirm", json={"editor_id": "u1"})
    assert response.status_code == 200, response.text
    assert not flow.page().confirmed
    # Sửa dòng khác của trang ⇒ dựng lại khối giữ khối vlm (cùng tham chiếu) và VLM_REGION.
    edited = flow.patch_title("1. Gioi thieu chung")
    assert "VLM_REGION" in edited.review_reasons and not edited.confirmed
    kept = flow.block(figure.block_id)
    assert kept.source == "vlm" and kept.text == OCR_TEXT
    assert by_type(store, flow.doc_id).get("figure") is None
    # Người dùng tự xác nhận được trang (không còn ô bảng lệch).
    response = flow.client.post(
        f"/v1/documents/{flow.doc_id}/pages/1/confirm", json={"editor_id": "u1", "expected_version": edited.version}
    )
    assert response.status_code == 200, response.text
    assert flow.page().confirmed_by == "user" and not is_trusted_source(flow.page())


def test_ai_reread_stale_proposal_after_patch_returns_409(store: AppStore, tmp_path: Path) -> None:
    flow = make_flow(store, tmp_path, TEXT_REPLY)
    figure = flow.figure()
    proposal = flow.reread(figure.block_id)
    edited = flow.patch_title("1. Gioi thieu lai")
    blocks_before = store.list_blocks(flow.doc_id, 1)
    status, body = flow.accept(figure.block_id, str(proposal["proposal_id"]), edited.version)
    assert (status, body["detail"]["code"]) == (409, "PROPOSAL_STALE")
    assert flow.page() == edited and store.list_blocks(flow.doc_id, 1) == blocks_before
    assert flow.figure().source == "text_layer"


def test_ai_reread_accept_twice_on_imported_document_not_pending(store: AppStore, tmp_path: Path) -> None:
    flow = make_flow(store, tmp_path, TEXT_REPLY)
    figure = flow.figure()
    proposal_id = str(flow.reread(figure.block_id)["proposal_id"])
    assert flow.accept(figure.block_id, proposal_id, flow.page().version)[0] == 200
    after = flow.page()
    status, body = flow.accept(figure.block_id, proposal_id, after.version)
    assert (status, body["detail"]["code"]) == (409, "PROPOSAL_NOT_PENDING")
    assert flow.page() == after


def test_vlm_table_mismatch_blocks_confirm_after_accept(store: AppStore, tmp_path: Path) -> None:
    rows = [["Thanh ghi R13 chua", "gia tri 43"]]
    flow = make_flow(store, tmp_path, [{"type": "table", "rows": rows}])
    figure = flow.figure()
    proposal = flow.reread(figure.block_id)
    ids = [m["mismatch_id"] for m in proposal["mismatches"]]
    assert len(ids) == 2
    status, body = flow.accept(figure.block_id, str(proposal["proposal_id"]), flow.page().version)
    assert status == 200, body
    page = flow.page()
    assert page.review_reasons == ["VLM_REGION", "TABLE_TOKEN_MISMATCH"]
    assert [(m.mismatch_id, m.block_id) for m in page.vlm_mismatches] == [(i, figure.block_id) for i in ids]
    table = flow.block(figure.block_id)
    assert table.type == "table" and table.source == "vlm" and "table_token_mismatch" in table.flags
    assert "crop_pending" in table.flags
    response = flow.client.post(
        f"/v1/documents/{flow.doc_id}/pages/1/confirm", json={"editor_id": "u1", "expected_version": page.version}
    )
    assert response.status_code == 409 and "lệch" in response.json()["detail"]
    assert not flow.page().confirmed
    # Dựng lại sau sửa dòng khác giữ khối bảng vlm và chỗ lệch chưa giải quyết (vẫn BLOCKING).
    edited = flow.patch_title("1. Gioi thieu bang")
    assert edited.review_reasons == ["VLM_REGION", "TABLE_TOKEN_MISMATCH"] and len(edited.vlm_mismatches) == 2


def test_body_lines_unchanged_by_vlm_accept(store: AppStore, tmp_path: Path) -> None:
    """Chữ VLM không bao giờ vào `lines` (ADR-009 mục 2): dòng của trang giữ nguyên sau khi Nhận."""
    flow = make_flow(store, tmp_path, TEXT_REPLY)
    before = flow.page()
    figure = flow.figure()
    proposal_id = str(flow.reread(figure.block_id)["proposal_id"])
    assert flow.accept(figure.block_id, proposal_id, before.version)[0] == 200
    assert flow.page().lines == before.lines
    assert all(OCR_TEXT not in line.text for line in flow.page().lines)
    assert BODY[0] in [line.text for line in flow.page().lines]
    assert pages_of(store, flow.doc_id)[0].page_number == 1
