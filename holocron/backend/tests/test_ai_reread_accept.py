"""Nhận đề xuất đọc lại bằng AI (SPECIFICATION §6.10a "Accept"; ADR-009 mục 2, 3) trên kho dựng tay, và ràng buộc
mới của `PageExtraction` cho vùng VLM (§5.7).

I3: chữ VLM chỉ vào kho sau cổng VÀ sau khi người dùng bấm Nhận; I5: trang có khối `vlm` không tự xác nhận, không là
nguồn đáng tin. Mọi lời gọi VLM qua transport giả (không gọi mạng).
"""

from __future__ import annotations

import http.client
import socket
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from holocron_core.domain.ai_reread import AiRereadBlock, AiRereadMismatch, AiRereadProposal
from holocron_core.domain.blocks import Block, OcrWordRef, WordRef
from holocron_core.domain.documents import CrossCheck, PageExtraction, VlmTokenMismatch
from holocron_core.domain.repositories import AppStore
from holocron_core.domain.schemas import SourceKind
from holocron_core.services.ai_reread_accept import vlm_block, with_vlm_block
from holocron_core.services.auto_confirm import is_trusted_source, passes_page_clean
from tests.test_ai_reread import (
    BASE,
    DOC,
    ELIGIBLE,
    MATCHING,
    OCR_LINE,
    Env,
    chat_body,
    make_env,
    seed,
)
from tests.test_block_store import close, open_kind

PROPOSAL_ID = "prp_0000000000a1"
MISMATCH_ID = "vmm_0000000000a1"


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


def accept(env: Env, proposal_id: str, version: int, block_id: str = ELIGIBLE.block_id) -> tuple[int, Any]:
    response = env.client.post(
        f"{BASE}/{block_id}/ai-reread/{proposal_id}/accept", json={"editor_id": "u1", "expected_version": version}
    )
    return response.status_code, response.json()


# --------------------------------------------------------------------------- #
# Accept qua API (kho dựng tay)
# --------------------------------------------------------------------------- #


def test_ai_reread_accept_writes_vlm_block_and_page_not_auto_confirmed(store: AppStore, tmp_path: Path) -> None:
    before = seed(store)
    env = make_env(store, tmp_path, (200, chat_body(MATCHING)))
    proposal_id = str(env.reread()[1]["proposal_id"])
    status, body = accept(env, proposal_id, before.version)
    assert status == 200, body
    page = PageExtraction.model_validate(body)
    assert page == env.page()
    assert page.version == before.version + 1 and not page.confirmed and page.confirmed_by is None
    assert "VLM_REGION" in page.review_reasons and page.status == "needs_review" and page.vlm_mismatches == []
    [block] = [b for b in store.list_blocks(DOC, 1) if b.block_id == ELIGIBLE.block_id]
    assert block.source == "vlm" and block.type == "code" and block.text == OCR_LINE and block.ocr_text == OCR_LINE
    assert block.line_refs == ELIGIBLE.line_refs and block.bbox == ELIGIBLE.bbox
    assert not {"code_layout_uncertain", "table_unresolved", "low_text_contrast"}.intersection(block.flags)
    # I5: trang có khối vlm không là nguồn đáng tin và không qua `page_clean_v1`, kể cả khi người dùng xác nhận.
    assert not is_trusted_source(page)
    assert not is_trusted_source(page.model_copy(update={"confirmed": True, "confirmed_by": "user"}))
    assert not passes_page_clean(
        page, auto_confirm=True, source_kind=SourceKind.LECTURE_SLIDES, has_text=True, fits_vault=True
    )
    record = store.get_ai_reread_proposal(proposal_id)
    assert record is not None and record.state == "accepted"
    # Khối đã là vlm ⇒ không đọc lại lần nữa.
    assert env.reread()[1]["detail"]["code"] == "BLOCK_NOT_ELIGIBLE"


def test_ai_reread_accept_twice_returns_409_not_pending(store: AppStore, tmp_path: Path) -> None:
    before = seed(store)
    env = make_env(store, tmp_path, (200, chat_body(MATCHING)))
    proposal_id = str(env.reread()[1]["proposal_id"])
    assert accept(env, proposal_id, before.version)[0] == 200
    after = env.page()
    assert after is not None
    for version in (after.version, before.version):
        status, body = accept(env, proposal_id, version)
        assert (status, body["detail"]["code"]) == (409, "PROPOSAL_NOT_PENDING")
    assert env.page() == after


def test_ai_reread_accept_version_conflict_and_not_found(store: AppStore, tmp_path: Path) -> None:
    before = seed(store)
    blocks_before = store.list_blocks(DOC, 1)
    env = make_env(store, tmp_path, (200, chat_body(MATCHING)))
    proposal_id = str(env.reread()[1]["proposal_id"])
    status, body = accept(env, proposal_id, before.version + 5)
    assert (status, body["detail"]["code"]) == (409, "VERSION_CONFLICT")
    assert str(before.version) in body["detail"]["message"]
    assert accept(env, "prp_00000000ffff", before.version)[0] == 404
    assert accept(env, proposal_id, before.version, block_id="blk_00000000000000a2")[0] == 404
    assert env.page() == before and store.list_blocks(DOC, 1) == blocks_before
    record = store.get_ai_reread_proposal(proposal_id)
    assert record is not None and record.state == "pending"


def test_ai_reread_accept_rejected_or_discarded_proposal_not_pending(store: AppStore, tmp_path: Path) -> None:
    before = seed(store)
    rejected_env = make_env(store, tmp_path, (200, chat_body([{"type": "code", "text": "chu khac hoan toan"}])))
    rejected = rejected_env.reread()[1]
    assert rejected["status"] == "rejected"
    assert accept(rejected_env, str(rejected["proposal_id"]), before.version)[1]["detail"]["code"] == (
        "PROPOSAL_NOT_PENDING"
    )
    env = make_env(store, tmp_path, (200, chat_body(MATCHING)))
    proposal_id = str(env.reread()[1]["proposal_id"])
    assert env.client.post(f"{BASE}/{ELIGIBLE.block_id}/ai-reread/{proposal_id}/reject").status_code == 204
    assert accept(env, proposal_id, before.version)[1]["detail"]["code"] == "PROPOSAL_NOT_PENDING"
    assert env.page() == before


def test_ai_reread_accept_ledger_mismatch_is_stale_and_writes_nothing(store: AppStore, tmp_path: Path) -> None:
    """Ledger dựng tay không khớp đơn vị thật của tham chiếu: đổi loại khối (code → table) không tính lại được
    `by_type` ⇒ 409 PROPOSAL_STALE, không ghi khối, trang hay trạng thái đề xuất (fail closed)."""
    before = seed(store)
    blocks_before = store.list_blocks(DOC, 1)
    rows = [["Thanh ghi R13 chua", "gia tri 42"], ["sau lenh MOV", ""]]
    env = make_env(store, tmp_path, (200, chat_body([{"type": "table", "rows": rows}])))
    proposal_id = str(env.reread()[1]["proposal_id"])
    status, body = accept(env, proposal_id, before.version)
    assert (status, body["detail"]["code"]) == (409, "PROPOSAL_STALE")
    assert env.page() == before and store.list_blocks(DOC, 1) == blocks_before
    record = store.get_ai_reread_proposal(proposal_id)
    assert record is not None and record.state == "pending"


def test_ai_reread_accept_after_page_confirmed_is_stale(store: AppStore, tmp_path: Path) -> None:
    before = seed(store)
    env = make_env(store, tmp_path, (200, chat_body(MATCHING)))
    proposal_id = str(env.reread()[1]["proposal_id"])
    confirmed = before.model_copy(update={"confirmed": True, "confirmed_by": "user", "version": before.version + 1})
    store.update_page(DOC, 1, confirmed, status=confirmed.status, confirmed=True, expected_version=before.version)
    # Trang đã đổi version (xác nhận) ⇒ đề xuất cũ, kiểm trước điều kiện của khối; không ghi gì.
    assert accept(env, proposal_id, confirmed.version)[1]["detail"]["code"] == "PROPOSAL_STALE"
    assert env.page() == confirmed


# --------------------------------------------------------------------------- #
# Hàm thuần
# --------------------------------------------------------------------------- #


def _proposal(blocks: list[AiRereadBlock], mismatches: list[AiRereadMismatch]) -> AiRereadProposal:
    return AiRereadProposal(
        proposal_id=PROPOSAL_ID,
        document_id=DOC,
        page_number=1,
        block_id=ELIGIBLE.block_id,
        page_version=2,
        status="accepted_text",
        reason="ok",
        blocks=blocks,
        mismatches=mismatches,
        ocr_text=OCR_LINE,
        input_sha256="0" * 64,
        from_cache=False,
        recipient_label="Gemini",
    )


def _page(**update: object) -> PageExtraction:
    page = PageExtraction(
        document_id=DOC,
        page_number=1,
        version=2,
        status="ok",
        tier_used="text_layer",
        text_score=1.0,
        cross_check=CrossCheck(status="not_applicable", secondary_engine=None),
    )
    return PageExtraction.model_validate({**page.model_dump(), **update})


TABLE = AiRereadBlock(type="table", text=None, rows=[["a", "43"]], flags=["table_token_mismatch"])
EXTRA = AiRereadMismatch(mismatch_id=MISMATCH_ID, token_kind="number", row=0, col=1, vlm_value="43")


def test_with_vlm_block_adds_reasons_and_mismatches() -> None:
    page = with_vlm_block(_page(), _proposal([TABLE], [EXTRA]))
    assert page.version == 3 and page.status == "needs_review" and not page.confirmed
    assert page.review_reasons == ["VLM_REGION", "TABLE_TOKEN_MISMATCH"]
    assert [(m.mismatch_id, m.block_id) for m in page.vlm_mismatches] == [(MISMATCH_ID, ELIGIBLE.block_id)]
    # Chỗ lệch người dùng đã giữ không quay lại.
    kept = with_vlm_block(_page(kept_vlm_mismatch_ids=[MISMATCH_ID]), _proposal([TABLE], [EXTRA]))
    assert kept.review_reasons == ["VLM_REGION"] and kept.vlm_mismatches == []


def test_vlm_block_keeps_id_refs_and_crop_of_offline_figure() -> None:
    offline = Block.model_validate(
        {
            "block_id": ELIGIBLE.block_id,
            "document_id": DOC,
            "page_number": 1,
            "type": "figure",
            "bbox": (0.1, 0.1, 0.9, 0.5),
            "label_line_refs": [WordRef(line_idx=0, word_idx=0), OcrWordRef(line_idx=0, word_idx=0)],
            "labels": ["x"],
            "flags": ["crop_pending", "table_unresolved"],
            "source": "text_layer",
            "reading_order": 3,
        }
    )
    table = vlm_block(offline, TABLE, "a 42")
    assert (table.block_id, table.bbox, table.reading_order, table.source) == (
        offline.block_id,
        offline.bbox,
        3,
        "vlm",
    )
    assert table.line_refs == offline.label_line_refs and table.label_line_refs == []
    assert table.flags == ["crop_pending", "table_token_mismatch"] and table.ocr_text == "a 42"
    assert table.cells is not None and [c.text for c in table.cells[0]] == ["a", "43"]
    heading = vlm_block(offline, AiRereadBlock(type="heading", text="Tieu de", rows=None, flags=[]), "Tieu de")
    assert heading.level == 1 and heading.flags == [] and heading.crop_ref is None


# --------------------------------------------------------------------------- #
# Ràng buộc của PageExtraction (§5.7)
# --------------------------------------------------------------------------- #


def _mismatch(**update: object) -> dict[str, object]:
    data: dict[str, object] = {
        "mismatch_id": MISMATCH_ID,
        "block_id": ELIGIBLE.block_id,
        "token_kind": "number",
        "row": 0,
        "col": 1,
        "vlm_value": "43",
    }
    data.update(update)
    return data


@pytest.mark.parametrize(
    "update",
    [
        {"vlm_value": None},
        {"ocr_value": "42"},
        {"row": None},
        {"vlm_value": None, "ocr_value": "42"},
    ],
)
def test_vlm_token_mismatch_one_side(update: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        VlmTokenMismatch.model_validate(_mismatch(**update))


def test_vlm_token_mismatch_missing_token_is_valid() -> None:
    missing = VlmTokenMismatch.model_validate(_mismatch(row=None, col=None, vlm_value=None, ocr_value="42"))
    assert missing.ocr_value == "42"


@pytest.mark.parametrize(
    "update",
    [
        {"vlm_mismatches": [_mismatch()], "review_reasons": ["VLM_REGION"], "status": "needs_review"},
        {"review_reasons": ["VLM_REGION", "TABLE_TOKEN_MISMATCH"], "status": "needs_review"},
        {"vlm_mismatches": [_mismatch()], "review_reasons": ["TABLE_TOKEN_MISMATCH"], "status": "needs_review"},
        {
            "vlm_mismatches": [_mismatch(), _mismatch()],
            "review_reasons": ["VLM_REGION", "TABLE_TOKEN_MISMATCH"],
            "status": "needs_review",
        },
        {
            "vlm_mismatches": [_mismatch()],
            "kept_vlm_mismatch_ids": [MISMATCH_ID],
            "review_reasons": ["VLM_REGION", "TABLE_TOKEN_MISMATCH"],
            "status": "needs_review",
        },
        {"vlm_rejected_block_ids": [ELIGIBLE.block_id]},
        {"kept_vlm_mismatch_ids": ["vmm_bad"]},
        {"kept_vlm_mismatch_ids": [MISMATCH_ID, MISMATCH_ID]},
        {
            "vlm_rejected_block_ids": [ELIGIBLE.block_id, ELIGIBLE.block_id],
            "review_reasons": ["VLM_REGION"],
            "status": "needs_review",
        },
    ],
)
def test_page_vlm_validators_reject(update: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        _page(**update)


def test_page_vlm_fields_default_and_old_json_still_validates() -> None:
    page = _page()
    assert page.vlm_mismatches == [] and page.kept_vlm_mismatch_ids == [] and page.vlm_rejected_block_ids == []
    data = page.model_dump(mode="json")
    for key in ("vlm_mismatches", "kept_vlm_mismatch_ids", "vlm_rejected_block_ids"):
        del data[key]
    assert PageExtraction.model_validate(data) == page
    valid = _page(
        vlm_mismatches=[_mismatch()],
        review_reasons=["VLM_REGION", "TABLE_TOKEN_MISMATCH"],
        status="needs_review",
        vlm_rejected_block_ids=["blk_00000000000000b1"],
    )
    assert valid.review_reasons == ["VLM_REGION", "TABLE_TOKEN_MISMATCH"]
