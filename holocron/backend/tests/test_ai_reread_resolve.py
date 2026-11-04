"""Giải quyết khối VLM sau khi Nhận và đồng thời Nhận/Bỏ (SPECIFICATION §6.5 `kept_mismatches`,
`rejected_vlm_blocks`; §5.7; §6.10a; ADR-009 mục 3.4–3.6).

I5: ô bảng VLM lệch là BLOCKING cho tới khi người dùng giữ TỪNG chỗ lệch hoặc bỏ khối; trang có `VLM_REGION` không tự
xác nhận. I3: khối VLM người dùng bỏ về khối offline và không bao giờ nhận lại chữ VLM (kể cả sau dựng lại). Mọi lời
gọi VLM qua transport giả (không gọi mạng).
"""

from __future__ import annotations

import http.client
import socket
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from holocron_core.domain.ai_reread import AiRereadBlock, AiRereadMismatch, AiRereadProposal
from holocron_core.domain.documents import MAX_VLM_MISMATCHES, PageExtraction, PageVlmAction
from holocron_core.domain.repositories import AppStore, DocumentLayout, VersionConflictError
from holocron_core.services.ai_reread import AiRereadError, reject_proposal
from tests.block_samples import geo_line, geometry
from tests.test_ai_reread import BASE, DOC, ELIGIBLE, MATCHING, OCR_LINE, chat_body, make_env, seed
from tests.test_ai_reread_accept_e2e import Flow, by_type, make_flow
from tests.test_block_store import close, open_kind

TABLE_ROWS = [["Thanh ghi R13 chua", "gia tri 43"]]


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("test không được gọi mạng")

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(http.client.HTTPConnection, "connect", refuse)


@pytest.fixture(params=("memory", "sqlite"))
def store(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[AppStore]:
    opened = open_kind(str(request.param), tmp_path)
    try:
        yield opened
    finally:
        close(opened)


def patch_lines(flow: Flow, **body: Any) -> tuple[int, Any]:
    response = flow.client.patch(
        f"/v1/documents/{flow.doc_id}/pages/1/lines",
        json={"editor_id": "u1", "expected_version": flow.page().version, **body},
    )
    return response.status_code, response.json()


def confirm(flow: Flow) -> int:
    response = flow.client.post(
        f"/v1/documents/{flow.doc_id}/pages/1/confirm",
        json={"editor_id": "u1", "expected_version": flow.page().version},
    )
    return response.status_code


def accepted_table(store: AppStore, tmp_path: Path) -> tuple[Flow, str, list[str]]:
    """Tài liệu có khối bảng `vlm` vừa Nhận, mang hai chỗ lệch chưa giải quyết."""
    flow = make_flow(store, tmp_path, [{"type": "table", "rows": TABLE_ROWS}])
    block_id = flow.figure().block_id
    proposal = flow.reread(block_id)
    ids = [str(m["mismatch_id"]) for m in proposal["mismatches"]]
    assert len(ids) == 2
    status, body = flow.accept(block_id, str(proposal["proposal_id"]), flow.page().version)
    assert status == 200, body
    assert flow.page().review_reasons == ["VLM_REGION", "TABLE_TOKEN_MISMATCH"]
    return flow, block_id, ids


# --------------------------------------------------------------------------- #
# PATCH …/lines: kept_mismatches, rejected_vlm_blocks (§6.5)
# --------------------------------------------------------------------------- #


def test_kept_mismatches_one_by_one_unblock_confirm(store: AppStore, tmp_path: Path) -> None:
    flow, block_id, ids = accepted_table(store, tmp_path)
    before = flow.page()
    status, body = patch_lines(flow, kept_mismatches=[ids[0]])
    assert status == 200, body
    page = flow.page()
    assert page == PageExtraction.model_validate(body) and page.version == before.version + 1
    # Còn một chỗ lệch ⇒ vẫn BLOCKING (không có "giữ tất cả").
    assert [m.mismatch_id for m in page.vlm_mismatches] == [ids[1]] and page.kept_vlm_mismatch_ids == [ids[0]]
    assert "TABLE_TOKEN_MISMATCH" in page.review_reasons and "table_token_mismatch" in flow.block(block_id).flags
    assert confirm(flow) == 409
    status, body = patch_lines(flow, kept_mismatches=[ids[1]])
    assert status == 200, body
    page = flow.page()
    assert page.vlm_mismatches == [] and page.kept_vlm_mismatch_ids == ids
    assert page.review_reasons == ["VLM_REGION"] and page.status == "needs_review" and not page.confirmed
    table = flow.block(block_id)
    assert table.source == "vlm" and table.type == "table" and "table_token_mismatch" not in table.flags
    audits = store.list_page_audits(flow.doc_id, 1)
    assert audits[-1].vlm_actions == [PageVlmAction(action="keep_mismatch", id=ids[1])]
    assert audits[-1].changes == [] and audits[-1].to_version == page.version
    # Dựng lại sau sửa dòng khác không đưa lại chỗ lệch đã giữ.
    status, body = patch_lines(flow, edits=[{"line_index": 0, "text": "1. Gioi thieu bang"}])
    assert status == 200, body
    assert flow.page().vlm_mismatches == [] and "table_token_mismatch" not in flow.block(block_id).flags
    assert flow.block(block_id).source == "vlm"
    assert confirm(flow) == 200
    assert flow.page().confirmed_by == "user"


def test_rejected_vlm_block_returns_offline_and_cannot_reread(store: AppStore, tmp_path: Path) -> None:
    flow, block_id, _ = accepted_table(store, tmp_path)
    status, body = patch_lines(flow, rejected_vlm_blocks=[block_id])
    assert status == 200, body
    page = flow.page()
    assert page.vlm_rejected_block_ids == [block_id] and page.vlm_mismatches == []
    assert page.review_reasons == ["VLM_REGION"] and not page.confirmed
    block = flow.block(block_id)
    assert block.source != "vlm" and block.type == "figure" and block.text is None
    assert "table_token_mismatch" not in block.flags
    assert by_type(store, flow.doc_id).get("table") is None  # ledger cân, đơn vị về lại figure
    audits = store.list_page_audits(flow.doc_id, 1)
    assert audits[-1].vlm_actions == [PageVlmAction(action="reject_vlm_block", id=block_id)]
    # Không đọc lại bằng AI được nữa (§6.10a: block_id ∈ vlm_rejected_block_ids).
    response = flow.client.post(flow.url(block_id))
    assert (response.status_code, response.json()["detail"]["code"]) == (409, "BLOCK_NOT_ELIGIBLE")
    # Dựng lại sau sửa dòng không khôi phục khối đã bỏ.
    status, body = patch_lines(flow, edits=[{"line_index": 0, "text": "1. Gioi thieu lai"}])
    assert status == 200, body
    assert flow.block(block_id).source != "vlm" and flow.page().vlm_rejected_block_ids == [block_id]
    assert confirm(flow) == 200


def test_patch_unknown_vlm_ids_returns_422_and_writes_nothing(store: AppStore, tmp_path: Path) -> None:
    flow, block_id, ids = accepted_table(store, tmp_path)
    before = flow.page()
    blocks_before = store.list_blocks(flow.doc_id, 1)
    offline_id = next(b.block_id for b in blocks_before if b.source != "vlm")
    for body in (
        {"kept_mismatches": ["vmm_00000000ffff"]},  # không có trong vlm_mismatches
        {"kept_mismatches": [ids[0], ids[0]]},  # trùng
        {"kept_mismatches": ["khong-phai-id"]},  # sai mẫu
        {"rejected_vlm_blocks": [offline_id]},  # không phải khối vlm
        {"rejected_vlm_blocks": ["blk_00000000000000ff"]},  # không có trên trang
        {"rejected_vlm_blocks": [block_id, block_id]},  # trùng
        {},  # cả bốn danh sách rỗng
    ):
        status, response = patch_lines(flow, **body)
        assert status == 422, (body, response)
    assert flow.page() == before and store.list_blocks(flow.doc_id, 1) == blocks_before


def test_kept_mismatch_after_resolved_is_422(store: AppStore, tmp_path: Path) -> None:
    """Chỗ lệch đã giữ không còn trong `vlm_mismatches` ⇒ giữ lần nữa là 422 (mỗi phần tử phải đang có)."""
    flow, _, ids = accepted_table(store, tmp_path)
    assert patch_lines(flow, kept_mismatches=[ids[0]])[0] == 200
    after = flow.page()
    assert patch_lines(flow, kept_mismatches=[ids[0]])[0] == 422
    assert flow.page() == after


# --------------------------------------------------------------------------- #
# Đồng thời: Nhận và Bỏ (§6.10a)
# --------------------------------------------------------------------------- #


def test_reject_during_accept_never_both_204_and_written(
    store: AppStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bỏ gọi đúng lúc Nhận đang ghi: Bỏ chờ khoá của tài liệu; sau khi Nhận ghi xong, Bỏ trả 409
    `PROPOSAL_NOT_PENDING` (không bao giờ vừa Bỏ thành công vừa ghi trang)."""
    flow = make_flow(store, tmp_path, [{"type": "paragraph", "text": "Thanh ghi R13 chua gia tri 42"}])
    block_id = flow.figure().block_id
    proposal_id = str(flow.reread(block_id)["proposal_id"])
    outcome: list[object] = []
    original = store.save_document_layout

    def reject() -> None:
        try:
            reject_proposal(store, flow.runtime.locks, flow.doc_id, 1, block_id, proposal_id)
            outcome.append(204)
        except AiRereadError as exc:
            outcome.append(exc.code)

    worker = threading.Thread(target=reject)

    def save_with_reject(document_id: str, plan: DocumentLayout) -> None:
        worker.start()
        worker.join(timeout=0.3)
        assert worker.is_alive()  # Bỏ đang chờ khoá, chưa bỏ được gì
        original(document_id, plan)

    monkeypatch.setattr(store, "save_document_layout", save_with_reject)
    status, body = flow.accept(block_id, proposal_id, flow.page().version)
    worker.join(timeout=10)
    assert status == 200, body
    assert outcome == ["PROPOSAL_NOT_PENDING"]
    assert flow.block(block_id).source == "vlm"
    record = store.get_ai_reread_proposal(proposal_id)
    assert record is not None and record.state == "accepted"


def test_reject_before_accept_then_accept_writes_nothing(store: AppStore, tmp_path: Path) -> None:
    flow = make_flow(store, tmp_path, [{"type": "paragraph", "text": "Thanh ghi R13 chua gia tri 42"}])
    block_id = flow.figure().block_id
    proposal_id = str(flow.reread(block_id)["proposal_id"])
    before = flow.page()
    blocks_before = store.list_blocks(flow.doc_id, 1)
    response = flow.client.post(f"{flow.url(block_id)}/{proposal_id}/reject")
    assert response.status_code == 204
    status, body = flow.accept(block_id, proposal_id, before.version)
    assert (status, body["detail"]["code"]) == (409, "PROPOSAL_NOT_PENDING")
    assert flow.page() == before and store.list_blocks(flow.doc_id, 1) == blocks_before
    # Bỏ lần nữa vẫn 204 (idempotent); Bỏ sau khi Nhận ⇒ 409 (đã ghi, không báo Bỏ thành công).
    assert flow.client.post(f"{flow.url(block_id)}/{proposal_id}/reject").status_code == 204


def test_reject_after_accept_returns_409_not_pending(store: AppStore, tmp_path: Path) -> None:
    flow = make_flow(store, tmp_path, [{"type": "paragraph", "text": "Thanh ghi R13 chua gia tri 42"}])
    block_id = flow.figure().block_id
    proposal_id = str(flow.reread(block_id)["proposal_id"])
    assert flow.accept(block_id, proposal_id, flow.page().version)[0] == 200
    response = flow.client.post(f"{flow.url(block_id)}/{proposal_id}/reject")
    assert (response.status_code, response.json()["detail"]["code"]) == (409, "PROPOSAL_NOT_PENDING")
    record = store.get_ai_reread_proposal(proposal_id)
    assert record is not None and record.state == "accepted"


# --------------------------------------------------------------------------- #
# Nhánh còn thiếu test của accept (kho dựng tay)
# --------------------------------------------------------------------------- #


def accept(env_client: Any, proposal_id: str, version: int) -> tuple[int, Any]:
    response = env_client.post(
        f"{BASE}/{ELIGIBLE.block_id}/ai-reread/{proposal_id}/accept",
        json={"editor_id": "u1", "expected_version": version},
    )
    return response.status_code, response.json()


def test_accept_block_lost_flag_or_document_closed_is_not_eligible(store: AppStore, tmp_path: Path) -> None:
    before = seed(store)
    env = make_env(store, tmp_path, (200, chat_body(MATCHING)))
    proposal_id = str(env.reread()[1]["proposal_id"])
    plain = next(b for b in store.list_blocks(DOC, 1) if b.block_id != ELIGIBLE.block_id)
    layout = DocumentLayout.model_validate(
        {
            "layout_version": 1,
            "layout_settings_sha256": "0" * 64,
            "pages": [{"page_number": 1, "blocks": [ELIGIBLE.model_copy(update={"flags": []}), plain], "ledger": None}],
        }
    )
    stored_doc = store.get_imported_document(DOC)
    assert stored_doc is not None
    layout = layout.model_copy(
        update={
            "layout_version": stored_doc.layout_version,
            "layout_settings_sha256": stored_doc.layout_settings_sha256,
        }
    )
    store.save_document_layout(DOC, layout)
    blocks_before = store.list_blocks(DOC, 1)
    assert env.page() == before  # khối mất cờ mà version trang không đổi
    status, body = accept(env.client, proposal_id, before.version)
    assert (status, body["detail"]["code"]) == (409, "BLOCK_NOT_ELIGIBLE")
    assert env.page() == before and store.list_blocks(DOC, 1) == blocks_before
    record = store.get_ai_reread_proposal(proposal_id)
    assert record is not None and record.state == "pending"


def test_accept_after_document_closed_is_not_eligible(store: AppStore, tmp_path: Path) -> None:
    before = seed(store)
    env = make_env(store, tmp_path, (200, chat_body(MATCHING)))
    proposal_id = str(env.reread()[1]["proposal_id"])
    document = store.get_imported_document(DOC)
    assert document is not None
    store.update_imported_document(document.model_copy(update={"status": "confirmed"}), document.version)
    status, body = accept(env.client, proposal_id, before.version)
    assert (status, body["detail"]["code"]) == (409, "BLOCK_NOT_ELIGIBLE")
    assert env.page() == before


def test_accept_when_region_ocr_changed_is_stale(
    store: AppStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = seed(store)
    env = make_env(store, tmp_path, (200, chat_body(MATCHING)))
    proposal_id = str(env.reread()[1]["proposal_id"])
    blocks_before = store.list_blocks(DOC, 1)
    changed = geometry([geo_line(OCR_LINE.replace("42", "47"), 0.12, 0.15)], source="ocr")
    monkeypatch.setattr(store, "get_page_geometry", lambda document_id, page_number: changed)
    status, body = accept(env.client, proposal_id, before.version)
    assert (status, body["detail"]["code"]) == (409, "PROPOSAL_STALE")
    assert env.page() == before and store.list_blocks(DOC, 1) == blocks_before
    record = store.get_ai_reread_proposal(proposal_id)
    assert record is not None and record.state == "pending"


def test_accept_save_version_conflict_is_409_and_not_accepted(
    store: AppStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = seed(store)
    env = make_env(store, tmp_path, (200, chat_body(MATCHING)))
    proposal_id = str(env.reread()[1]["proposal_id"])
    blocks_before = store.list_blocks(DOC, 1)

    def conflict(document_id: str, plan: DocumentLayout) -> None:
        raise VersionConflictError("trang", f"{document_id}:1", before.version, before.version + 1)

    monkeypatch.setattr(store, "save_document_layout", conflict)
    status, body = accept(env.client, proposal_id, before.version)
    assert (status, body["detail"]["code"]) == (409, "VERSION_CONFLICT")
    assert str(before.version + 1) in body["detail"]["message"]
    assert env.page() == before and store.list_blocks(DOC, 1) == blocks_before
    record = store.get_ai_reread_proposal(proposal_id)
    assert record is not None and record.state == "pending"


def test_accept_over_mismatch_cap_is_409_not_500(store: AppStore, tmp_path: Path) -> None:
    """Đề xuất làm trang vượt `MAX_VLM_MISMATCHES` chỗ lệch ⇒ 409 `BLOCK_NOT_ELIGIBLE`, không ghi gì (không 500)."""
    before = seed(store)
    env = make_env(store, tmp_path, (200, chat_body(MATCHING)))
    blocks_before = store.list_blocks(DOC, 1)
    mismatches = [
        AiRereadMismatch(mismatch_id=f"vmm_{index:012x}", token_kind="number", row=0, col=0, vlm_value=str(index))
        for index in range(MAX_VLM_MISMATCHES + 1)
    ]
    proposal = AiRereadProposal(
        proposal_id="prp_0000000000b1",
        document_id=DOC,
        page_number=1,
        block_id=ELIGIBLE.block_id,
        page_version=before.version,
        status="accepted_text",
        reason="ok",
        blocks=[AiRereadBlock(type="table", text=None, rows=[["0"]], flags=["table_token_mismatch"])],
        mismatches=mismatches,
        ocr_text=OCR_LINE,
        input_sha256="0" * 64,
        from_cache=False,
        recipient_label="Gemini",
    )
    store.put_ai_reread_proposal(proposal)
    status, body = accept(env.client, proposal.proposal_id, before.version)
    assert (status, body["detail"]["code"]) == (409, "BLOCK_NOT_ELIGIBLE")
    assert env.page() == before and store.list_blocks(DOC, 1) == blocks_before
    record = store.get_ai_reread_proposal(proposal.proposal_id)
    assert record is not None and record.state == "pending"
