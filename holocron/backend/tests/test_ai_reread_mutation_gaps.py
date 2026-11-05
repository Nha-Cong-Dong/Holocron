"""Nhánh của đọc lại bằng AI chưa có test giết đột biến (SPECIFICATION §6.10a, §5.7; ADR-009 mục 2, 3, 15).

I3: chữ VLM chỉ vào kho sau cổng và sau khi người dùng Nhận; khối người dùng đã bỏ (`vlm_rejected_block_ids`) không
bao giờ được gửi đọc lại; Nhận kiểm lại điều kiện của khối và đầu vào của cổng. I5: trang còn chỗ lệch bảng chưa giải
quyết giữ `TABLE_TOKEN_MISMATCH`, trang `failed` không đổi trạng thái. Bảo mật: không cắt ảnh, không gọi nhà cung cấp
khi không đủ điều kiện; báo đúng nơi đã nhận ảnh. Mọi lời gọi VLM qua transport giả (không gọi mạng); bộ cắt ảnh là
bản giả.
"""

from __future__ import annotations

import dataclasses
import http.client
import json
import socket
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest

from holocron_core.domain.ai import AiRole, CostEstimate, SelectedProvider
from holocron_core.domain.ai_reread import AiRereadAcceptRequest, AiRereadBlock, AiRereadProposal
from holocron_core.domain.blocks import Block, OcrWordRef
from holocron_core.domain.documents import GeoLine, GeoWord, PageExtraction, PageGeometry, VlmTokenMismatch
from holocron_core.domain.repositories import AppStore
from holocron_core.infrastructure.document_readers.pdf_reader import CropOutcome, RenderedCrop
from holocron_core.infrastructure.document_readers.protocol import CropRegion
from holocron_core.services.ai_reread import region_ocr_text
from holocron_core.services.ai_reread_accept import accept_proposal, vlm_block, with_vlm_block
from tests.block_samples import geometry
from tests.test_ai_reread import (
    BASE,
    DOC,
    ELIGIBLE,
    MATCHING,
    OCR_LINE,
    Env,
    FakeRenderer,
    chat_body,
    make_env,
    seed,
)
from tests.test_ai_reread_accept import TABLE, _page, _proposal
from tests.test_ai_reread_accept_e2e import make_flow
from tests.test_block_store import close, open_kind
from tests.vision_support import NOW, candidate, region_image

OLD_MISMATCH = "vmm_0000000000b1"
OTHER_MISMATCH = "vmm_0000000000b2"
OTHER_BLOCK = "blk_00000000000000b7"


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


def accept(env: Env, proposal_id: str, version: int) -> tuple[int, Any]:
    response = env.client.post(
        f"{BASE}/{ELIGIBLE.block_id}/ai-reread/{proposal_id}/accept",
        json={"editor_id": "u1", "expected_version": version},
    )
    return response.status_code, response.json()


def stored_proposal(page: PageExtraction, block: AiRereadBlock) -> AiRereadProposal:
    """Đề xuất `accepted_text` đặt thẳng vào kho tạm (như kho bị sửa ngoài luồng) cho khối `ELIGIBLE`."""
    return AiRereadProposal(
        proposal_id="prp_0000000000c1",
        document_id=DOC,
        page_number=1,
        block_id=ELIGIBLE.block_id,
        page_version=page.version,
        status="accepted_text",
        reason="ok",
        blocks=[block],
        mismatches=[],
        ocr_text=OCR_LINE,
        input_sha256="0" * 64,
        from_cache=False,
        recipient_label="Gemini",
    )


def assert_nothing_written(env: Env, store: AppStore, before: PageExtraction, blocks_before: list[Block]) -> None:
    assert env.page() == before and store.list_blocks(DOC, 1) == blocks_before


# --------------------------------------------------------------------------- #
# Tạo đề xuất: điều kiện của khối và lỗi trước khi gửi ảnh (§6.10a)
# --------------------------------------------------------------------------- #


def test_reread_block_in_vlm_rejected_ids_is_not_eligible_and_sends_nothing(store: AppStore, tmp_path: Path) -> None:
    """Khối offline còn cờ đọc lại nhưng nằm trong `vlm_rejected_block_ids` ⇒ 409, không cắt ảnh, không gọi VLM."""
    before = seed(store)
    rejected = PageExtraction.model_validate(
        {
            **before.model_dump(),
            "version": before.version + 1,
            "review_reasons": ["VLM_REGION"],
            "status": "needs_review",
            "vlm_rejected_block_ids": [ELIGIBLE.block_id],
        }
    )
    store.update_page(DOC, 1, rejected, status=rejected.status, confirmed=False, expected_version=before.version)
    env = make_env(store, tmp_path, (200, chat_body(MATCHING)))
    status, body = env.reread()
    assert (status, body["detail"]["code"]) == (409, "BLOCK_NOT_ELIGIBLE")
    assert env.transports.requests == [] and env.renderer.requests == []
    assert env.page() == rejected


def test_reread_missing_original_never_renders(store: AppStore, tmp_path: Path) -> None:
    """File gốc không còn ⇒ 409 `BLOCK_NOT_ELIGIBLE` trước khi gọi bộ cắt ảnh."""
    seed(store)
    env = make_env(store, tmp_path, (200, chat_body(MATCHING)), files_present=False)
    status, body = env.reread()
    assert (status, body["detail"]["code"]) == (409, "BLOCK_NOT_ELIGIBLE")
    assert env.renderer.requests == [] and env.transports.requests == []


@dataclasses.dataclass
class BadPngRenderer(FakeRenderer):
    """Trả ảnh cắt có sha256 khai báo sai: `VisionReader` từ chối ảnh (`image_rejected`) trước khi gửi."""

    def render_regions(self, path: Path, requests: Sequence[CropRegion], *, max_edge_px: int) -> list[CropOutcome]:
        self.requests.extend(requests)
        image = region_image()
        return [
            RenderedCrop(region.region_id, image.png, image.width_px, image.height_px, "f" * 64) for region in requests
        ]


def test_reread_image_rejected_is_block_not_eligible(store: AppStore, tmp_path: Path) -> None:
    before = seed(store)
    env = make_env(store, tmp_path, (200, chat_body(MATCHING)), renderer=BadPngRenderer())
    status, body = env.reread()
    assert (status, body["detail"]["code"]) == (409, "BLOCK_NOT_ELIGIBLE")
    assert len(env.renderer.requests) == 1 and env.transports.requests == []
    assert env.page() == before


@pytest.mark.parametrize(("exhausted_at_call", "code"), [(True, "AI_BUDGET_EXHAUSTED"), (False, "AI_NOT_CONFIGURED")])
def test_reread_skipped_at_call_maps_budget_or_not_configured(
    store: AppStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, exhausted_at_call: bool, code: str
) -> None:
    """Kiểm trước khi gửi còn trần nhưng lúc gọi không chọn được nhà cung cấp (`no_provider`): trần vừa bị tiêu hết
    giữa hai bước ⇒ `AI_BUDGET_EXHAUSTED`, ngược lại ⇒ `AI_NOT_CONFIGURED`; không gửi gì."""
    before = seed(store)
    env = make_env(store, tmp_path, (200, chat_body(MATCHING)))
    checks: list[AiRole] = []

    def budget_exhausted(role: AiRole) -> bool:
        checks.append(role)
        return exhausted_at_call and len(checks) > 1

    def select_none(role: AiRole, estimate: CostEstimate) -> SelectedProvider | None:
        return None

    monkeypatch.setattr(env.selection, "budget_exhausted", budget_exhausted)
    monkeypatch.setattr(env.selection, "select", select_none)
    status, body = env.reread()
    assert (status, body["detail"]["code"]) == (409, code)
    assert len(checks) == 2 and env.transports.requests == [] and env.page() == before


def test_recipient_label_is_provider_that_answered_after_fallback(
    store: AppStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lần gửi đầu trả JSON hỏng, lần gọi lại chuyển sang nhà cung cấp thứ hai ⇒ `recipient_label` là nhãn của nhà
    cung cấp CUỐI (nơi đã nhận ảnh và cho kết quả)."""
    seed(store)
    first = candidate()
    second = candidate(provider_id="usr_backup").model_copy(update={"label": "May du phong", "priority": 5})
    invalid = json.dumps({"id": "x", "choices": [{"index": 0, "message": {"content": "khong phai JSON"}}]}).encode()
    env = make_env(store, tmp_path, (200, invalid), (200, chat_body(MATCHING)), providers=[first, second])
    original = env.selection.select
    calls: list[str] = []

    def select(role: AiRole, estimate: CostEstimate) -> SelectedProvider | None:
        calls.append(role)
        if len(calls) == 1:
            return original(role, estimate)
        return env.selection.reserve(second.provider_id, "vlm-model-b", estimate)

    monkeypatch.setattr(env.selection, "select", select)
    status, body = env.reread()
    assert status == 200, body
    assert len(env.transports.requests) == 2 and len(calls) == 2
    assert body["status"] == "accepted_text" and body["recipient_label"] == "May du phong"


# --------------------------------------------------------------------------- #
# Nhận đề xuất: kiểm lại điều kiện và đầu vào của cổng trước khi ghi (§6.10a "Accept")
# --------------------------------------------------------------------------- #


def test_accept_block_in_vlm_rejected_ids_is_not_eligible(store: AppStore, tmp_path: Path) -> None:
    """Đề xuất tạo trước khi người dùng bỏ khối VLM (trang về cùng `version` do kho sửa ngoài luồng): Nhận vẫn kiểm
    lại điều kiện của khối ⇒ 409 `BLOCK_NOT_ELIGIBLE`, không ghi, đề xuất còn `pending`."""
    before = seed(store)
    env = make_env(store, tmp_path, (200, chat_body(MATCHING)))
    proposal_id = str(env.reread()[1]["proposal_id"])
    rejected = PageExtraction.model_validate(
        {
            **before.model_dump(),
            "version": before.version + 1,
            "review_reasons": ["VLM_REGION"],
            "status": "needs_review",
            "vlm_rejected_block_ids": [ELIGIBLE.block_id],
        }
    )
    store.update_page(DOC, 1, rejected, status=rejected.status, confirmed=False, expected_version=before.version)
    record = store.get_ai_reread_proposal(proposal_id)
    assert record is not None
    store.put_ai_reread_proposal(record.proposal.model_copy(update={"page_version": rejected.version}))
    blocks_before = store.list_blocks(DOC, 1)
    status, body = accept(env, proposal_id, rejected.version)
    assert (status, body["detail"]["code"]) == (409, "BLOCK_NOT_ELIGIBLE")
    assert_nothing_written(env, store, rejected, blocks_before)
    record = store.get_ai_reread_proposal(proposal_id)
    assert record is not None and record.state == "pending"


@pytest.mark.parametrize("missing", ["layout_version", "layout_settings_sha256"])
def test_accept_without_layout_version_is_not_eligible(
    store: AppStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    """Tài liệu không còn phiên bản bố cục (khối chưa dựng xong hay đã bị gỡ) ⇒ không dựng được giao dịch ghi khối:
    409 `BLOCK_NOT_ELIGIBLE`, không ghi gì."""
    before = seed(store)
    env = make_env(store, tmp_path, (200, chat_body(MATCHING)))
    proposal_id = str(env.reread()[1]["proposal_id"])
    blocks_before = store.list_blocks(DOC, 1)
    document = store.get_imported_document(DOC)
    assert document is not None and getattr(document, missing) is not None
    stripped = document.model_copy(update={missing: None})
    monkeypatch.setattr(store, "get_imported_document", lambda document_id: stripped)
    status, body = accept(env, proposal_id, before.version)
    assert (status, body["detail"]["code"]) == (409, "BLOCK_NOT_ELIGIBLE")
    assert_nothing_written(env, store, before, blocks_before)


def test_accept_when_proposal_ocr_text_differs_is_stale(store: AppStore, tmp_path: Path) -> None:
    """Chữ OCR độc lập của vùng tính lại khác `ocr_text` mà cổng đã dùng ⇒ 409 `PROPOSAL_STALE`, không ghi."""
    before = seed(store)
    env = make_env(store, tmp_path)
    blocks_before = store.list_blocks(DOC, 1)
    paragraph = AiRereadBlock(type="code", text=OCR_LINE, rows=None, flags=[])
    proposal = stored_proposal(before, paragraph).model_copy(update={"ocr_text": OCR_LINE + " 99"})
    store.put_ai_reread_proposal(proposal)
    status, body = accept(env, proposal.proposal_id, before.version)
    assert (status, body["detail"]["code"]) == (409, "PROPOSAL_STALE")
    assert_nothing_written(env, store, before, blocks_before)
    record = store.get_ai_reread_proposal(proposal.proposal_id)
    assert record is not None and record.state == "pending"


def test_accept_unbuildable_vlm_block_is_stale_and_writes_nothing(store: AppStore, tmp_path: Path) -> None:
    """Khối đề xuất không dựng được thành `Block` hợp lệ (`table_token_mismatch` ở khối không phải bảng) ⇒ 409
    `PROPOSAL_STALE`, không ghi, đề xuất còn `pending` (không 500)."""
    before = seed(store)
    env = make_env(store, tmp_path)
    blocks_before = store.list_blocks(DOC, 1)
    broken = AiRereadBlock(type="paragraph", text=OCR_LINE, rows=None, flags=["table_token_mismatch"])
    proposal = stored_proposal(before, broken)
    store.put_ai_reread_proposal(proposal)
    status, body = accept(env, proposal.proposal_id, before.version)
    assert (status, body["detail"]["code"]) == (409, "PROPOSAL_STALE")
    assert_nothing_written(env, store, before, blocks_before)
    record = store.get_ai_reread_proposal(proposal.proposal_id)
    assert record is not None and record.state == "pending"


def test_accept_records_editor_and_schedules_crop_for_table(store: AppStore, tmp_path: Path) -> None:
    """Nhận khối bảng: audit ghi đúng `editor_id`; sau commit xếp job cắt ảnh cho tài liệu (khối mang `crop_pending`)."""
    flow = make_flow(store, tmp_path, [{"type": "table", "rows": [["Thanh ghi R13 chua", "gia tri 42"]]}])
    block_id = flow.figure().block_id
    proposal_id = str(flow.reread(block_id)["proposal_id"])
    scheduled: list[str] = []

    class RecordingCrops:
        def schedule(self, store: AppStore, document_id: str) -> bool:
            scheduled.append(document_id)
            return True

        def close(self, timeout_s: float = 0.0) -> bool:
            return True

    runtime = dataclasses.replace(flow.runtime, crops=RecordingCrops())
    version = flow.page().version
    request = AiRereadAcceptRequest(editor_id="nguoi-duyet-7", expected_version=version)
    page = accept_proposal(store, store, runtime, flow.doc_id, 1, block_id, proposal_id, request, now=NOW)
    assert page.version == version + 1
    assert scheduled == [flow.doc_id]
    assert "crop_pending" in flow.block(block_id).flags
    audit = store.list_page_audits(flow.doc_id, 1)[-1]
    assert audit.editor_id == "nguoi-duyet-7" and audit.from_version == version


# --------------------------------------------------------------------------- #
# Hàm thuần: trang sau khi Nhận (§5.7) và khối VLM (ADR-009 mục 2)
# --------------------------------------------------------------------------- #


def _old_mismatch(mismatch_id: str, block_id: str) -> VlmTokenMismatch:
    return VlmTokenMismatch(
        mismatch_id=mismatch_id, block_id=block_id, token_kind="number", row=0, col=0, vlm_value="7", ocr_value=None
    )


def test_with_vlm_block_replaces_old_mismatches_of_same_block_and_clears_reason() -> None:
    """Đọc lại lần hai cùng khối, đề xuất mới hết lệch: chỗ lệch cũ của khối bị thay, `TABLE_TOKEN_MISMATCH` gỡ."""
    page = _page(
        status="needs_review",
        review_reasons=["VLM_REGION", "TABLE_TOKEN_MISMATCH"],
        vlm_mismatches=[_old_mismatch(OLD_MISMATCH, ELIGIBLE.block_id).model_dump()],
    )
    clean = AiRereadBlock(type="table", text=None, rows=[["a", "42"]], flags=[])
    updated = with_vlm_block(page, _proposal([clean], []))
    assert updated.vlm_mismatches == [] and updated.review_reasons == ["VLM_REGION"]
    assert updated.status == "needs_review" and updated.version == page.version + 1


def test_with_vlm_block_keeps_mismatches_of_other_blocks() -> None:
    other = _old_mismatch(OTHER_MISMATCH, OTHER_BLOCK)
    page = _page(
        status="needs_review",
        review_reasons=["VLM_REGION", "TABLE_TOKEN_MISMATCH"],
        vlm_mismatches=[other.model_dump(), _old_mismatch(OLD_MISMATCH, ELIGIBLE.block_id).model_dump()],
    )
    updated = with_vlm_block(page, _proposal([TABLE], []))
    assert updated.vlm_mismatches == [other]
    assert updated.review_reasons == ["VLM_REGION", "TABLE_TOKEN_MISMATCH"]


def test_with_vlm_block_failed_page_stays_failed() -> None:
    page = _page(status="failed", failure_reason="PAGE_ERROR", tier_used=None, text_score=None)
    updated = with_vlm_block(page, _proposal([TABLE], []))
    assert updated.status == "failed" and updated.failure_reason == page.failure_reason
    assert "VLM_REGION" in updated.review_reasons


def _offline(kind: str, **extra: object) -> Block:
    data: dict[str, object] = {
        "block_id": ELIGIBLE.block_id,
        "document_id": DOC,
        "page_number": 1,
        "type": kind,
        "bbox": (0.1, 0.1, 0.9, 0.5),
        "source": "ocr",
        "reading_order": 2,
        **extra,
    }
    return Block.model_validate(data)


def test_vlm_block_keeps_list_depth_of_offline_list_item() -> None:
    offline = _offline(
        "list_item",
        text="muc",
        line_refs=[OcrWordRef(line_idx=0, word_idx=0)],
        list_depth=2,
        flags=["low_text_contrast"],
    )
    proposed = AiRereadBlock(type="list_item", text="muc", rows=None, flags=[])
    assert vlm_block(offline, proposed, "muc").list_depth == 2


def test_vlm_block_keeps_crop_failure_flag_of_offline_figure() -> None:
    """Hình offline cắt ảnh lỗi (`crop_failed`, chưa có `crop_ref`) đổi thành bảng: giữ cờ lỗi cắt ảnh để báo người
    dùng, thêm `crop_pending`."""
    offline = _offline(
        "figure",
        label_line_refs=[OcrWordRef(line_idx=0, word_idx=0)],
        labels=["x"],
        flags=["crop_failed", "crop_pending", "table_unresolved"],
    )
    table = vlm_block(offline, TABLE, "a 42")
    assert {"crop_failed", "crop_pending"} <= set(table.flags) and table.crop_ref is None


def test_vlm_block_table_cells_keep_row_and_col_of_non_square_table() -> None:
    offline = _offline(
        "figure", label_line_refs=[OcrWordRef(line_idx=0, word_idx=0)], labels=["x"], flags=["crop_pending"]
    )
    rows = [["a", "b", "c"], ["d", "e", "f"]]
    table = vlm_block(offline, AiRereadBlock(type="table", text=None, rows=rows, flags=[]), "a b c d e f")
    assert table.cells is not None
    assert [[(cell.row, cell.col, cell.text) for cell in row] for row in table.cells] == [
        [(r, c, text) for c, text in enumerate(cols)] for r, cols in enumerate(rows)
    ]


# --------------------------------------------------------------------------- #
# Chữ OCR độc lập của vùng (ADR-009 mục 3.1)
# --------------------------------------------------------------------------- #


def _ocr_geometry(words: list[GeoWord]) -> PageGeometry:
    return geometry([GeoLine(words=words, bbox=(0.0, 0.0, 1.0, 1.0), extracted_line_indices=[])], source="ocr")


def test_region_ocr_text_includes_word_centered_on_region_edge() -> None:
    """Tâm hộp nằm đúng trên biên vùng vẫn thuộc vùng (biên đóng)."""
    # Toạ độ nhị phân đúng (không sai số làm tròn): tâm "bien" có x trùng biên trái, tâm "trong" có y trùng biên dưới.
    edge = GeoWord(text="bien", bbox=(0.0, 0.25, 0.25, 0.375))
    inside = GeoWord(text="trong", bbox=(0.5, 0.25, 0.75, 0.5))
    region = (0.125, 0.125, 0.875, 0.375)
    assert region_ocr_text(_ocr_geometry([edge, inside]), region) == "bien trong"


def test_region_ocr_text_skips_words_without_box() -> None:
    boxless = GeoWord(text="khonghop", bbox=None)
    word = GeoWord(text="co", bbox=(0.1, 0.1, 0.2, 0.2))
    assert region_ocr_text(_ocr_geometry([boxless, word]), (0.0, 0.0, 0.5, 0.5)) == "co"
