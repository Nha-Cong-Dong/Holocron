"""Nhận đề xuất đọc lại bằng AI (`POST …/ai-reread/{proposal_id}/accept`, spec §6.10a "Accept"; ADR-009 mục 2, 3).

Thứ tự kiểm (đều TRƯỚC khi ghi gì, dưới khoá của tài liệu): đề xuất có trong kho tạm cho đúng tài liệu/trang/khối
(404) → đề xuất còn `pending` và cổng đã nhận (`PROPOSAL_NOT_PENDING`) → `expected_version` = `version` hiện tại
(`VERSION_CONFLICT`) → `page_version` của đề xuất = `version` hiện tại (`PROPOSAL_STALE`) → khối còn đủ điều kiện
(`BLOCK_NOT_ELIGIBLE`) → đầu vào của cổng không đổi (chữ OCR độc lập của vùng tính lại từ `PageGeometry` khớp
`ocr_text` của đề xuất; lệch hoặc không dựng được khối/ledger nhất quán ⇒ `PROPOSAL_STALE`, không ghi gì).

Ghi trong MỘT giao dịch (`save_document_layout`): khối của vùng thành khối `source = "vlm"` (cùng `block_id`, cùng
`bbox`, cùng tham chiếu nguồn của khối offline nên ledger vẫn cân; `by_type` chuyển đơn vị theo loại mới), trang
`version` + 1 kèm `VLM_REGION` (và `TABLE_TOKEN_MISMATCH` khi còn chỗ lệch chưa giữ), `PageEditAudit`. Accept không
bao giờ xác nhận trang hay tài liệu; trang có `VLM_REGION` luôn `needs_review` nên không tự xác nhận (ADR-009 mục 3.6).
Kết quả VLM của vùng đã vào cache AI kèm liên kết tài liệu lúc tạo đề xuất (`VisionReader`), nên ở đây không ghi
cache. Chữ VLM ghi vào kho là chữ ĐÃ QUA cổng, lưu trong đề xuất (I3).
"""

from __future__ import annotations

from datetime import datetime

from ..domain.ai_reread import AiRereadAcceptRequest, AiRereadBlock, AiRereadProposal
from ..domain.blocks import CROPPED_TYPES, Block, BlockFlag, ExtractionLedger, TableCell
from ..domain.documents import PageEditAudit, PageExtraction, PageGeometry, ReviewReason, VlmTokenMismatch
from ..domain.repositories import (
    AppStore,
    DocumentLayout,
    PageLayout,
    PageRewrite,
    VaultRepository,
    VersionConflictError,
)
from .ai_reread import AiRereadError, ProposalNotFoundError, check_block_eligible, region_ocr_text
from .document_import import DocumentNotFoundError, ImportRuntime, get_page_extraction, schedule_crops
from .document_layout import retype_ledger
from .extraction_ledger import LedgerError, block_refs

__all__ = ["accept_proposal", "vlm_block", "with_vlm_block"]

_CROP_FLAGS: frozenset[BlockFlag] = frozenset({"crop_pending", "crop_failed", "crop_capped"})
VLM_REGION: ReviewReason = "VLM_REGION"
TABLE_TOKEN_MISMATCH: ReviewReason = "TABLE_TOKEN_MISMATCH"


def vlm_block(offline: Block, proposed: AiRereadBlock, ocr_text: str) -> Block:
    """Khối `source = "vlm"` thay cho khối offline của vùng (ADR-009 mục 2, hàm thuần).

    Giữ `block_id`, `bbox`, `reading_order` và MỌI tham chiếu nguồn của khối offline (`line_refs` ∪ `label_line_refs`
    thành `line_refs`; ô bảng không mang tham chiếu riêng). Loại, chữ, ô và cờ cổng lấy từ khối đề xuất đã qua cổng.
    `heading`/`list_item` giữ `level`/`list_depth` của khối offline khi cùng loại, ngược lại 1 và 0. Loại cần ảnh cắt
    giữ ảnh cắt của khối offline (cùng vùng) hoặc mang `crop_pending`. Ném `ValueError` (pydantic) khi không dựng được.
    """
    kind = proposed.type
    refs = block_refs(offline)
    cells = None
    if proposed.rows is not None:
        cells = [
            [TableCell(text=text, line_refs=[], row=row, col=col) for col, text in enumerate(cols)]
            for row, cols in enumerate(proposed.rows)
        ]
    flags: set[BlockFlag] = set(proposed.flags)
    crop_ref = None
    if kind in CROPPED_TYPES and offline.bbox is not None:
        crop_ref = offline.crop_ref
        if crop_ref is None:
            flags |= _CROP_FLAGS.intersection(offline.flags) | {"crop_pending"}
    return Block.model_validate(
        {
            "block_id": offline.block_id,
            "document_id": offline.document_id,
            "page_number": offline.page_number,
            "type": kind,
            "bbox": offline.bbox,
            "text": proposed.text,
            "line_refs": [ref.model_dump() for ref in refs],
            "level": (offline.level or 1) if kind == "heading" else None,
            "list_depth": (offline.list_depth or 0) if kind == "list_item" else None,
            "cells": None if cells is None else [[cell.model_dump() for cell in row] for row in cells],
            "flags": sorted(flags),
            "crop_ref": crop_ref,
            "source": "vlm",
            "ocr_text": ocr_text,
            "reading_order": offline.reading_order,
        }
    )


def _status_for(reasons: list[ReviewReason], page: PageExtraction) -> str:
    if page.status == "failed":
        return "failed"
    return "needs_review" if reasons else "ok"


def with_vlm_block(page: PageExtraction, proposal: AiRereadProposal) -> PageExtraction:
    """Trang sau khi nhận khối VLM của `proposal` (hàm thuần): chỗ lệch của khối thay chỗ lệch cũ của cùng khối, bỏ
    chỗ lệch người dùng đã giữ (`kept_vlm_mismatch_ids`); thêm `VLM_REGION`; `TABLE_TOKEN_MISMATCH` ⇔ còn chỗ lệch.
    `version` + 1; không xác nhận."""
    kept = set(page.kept_vlm_mismatch_ids)
    mismatches = [m for m in page.vlm_mismatches if m.block_id != proposal.block_id]
    mismatches += [
        VlmTokenMismatch(block_id=proposal.block_id, **m.model_dump())
        for m in proposal.mismatches
        if m.mismatch_id not in kept
    ]
    reasons = [reason for reason in page.review_reasons if reason != TABLE_TOKEN_MISMATCH]
    reasons.append(VLM_REGION)
    if mismatches:
        reasons.append(TABLE_TOKEN_MISMATCH)
    return PageExtraction.model_validate(
        {
            **page.model_dump(),
            "version": page.version + 1,
            "review_reasons": reasons,
            "status": _status_for(reasons, page),
            "vlm_mismatches": [m.model_dump() for m in mismatches],
        }
    )


def _record(store: AppStore, document_id: str, page_number: int, block_id: str, proposal_id: str) -> AiRereadProposal:
    record = store.get_ai_reread_proposal(proposal_id)
    if record is None:
        raise ProposalNotFoundError(proposal_id)
    proposal = record.proposal
    if (proposal.document_id, proposal.page_number, proposal.block_id) != (document_id, page_number, block_id):
        raise ProposalNotFoundError(proposal_id)
    if record.state != "pending" or proposal.status != "accepted_text" or len(proposal.blocks) != 1:
        raise AiRereadError("PROPOSAL_NOT_PENDING")
    return proposal


def _find(blocks: list[Block], block_id: str) -> Block:
    for block in blocks:
        if block.block_id == block_id:
            return block
    raise DocumentNotFoundError(block_id)


def _page_ledger(store: AppStore, document_id: str, page_number: int) -> ExtractionLedger | None:
    for ledger in store.list_extraction_ledgers(document_id):
        if ledger.page_number == page_number:
            return ledger
    return None


def _drop_unfit_mismatch_flag(block: Block, page: PageExtraction) -> Block:
    """Khối không còn chỗ lệch chưa giải quyết trên trang (mọi chỗ lệch đã giữ) ⇒ gỡ `table_token_mismatch`."""
    if "table_token_mismatch" not in block.flags or any(m.block_id == block.block_id for m in page.vlm_mismatches):
        return block
    flags = [flag for flag in block.flags if flag != "table_token_mismatch"]
    return Block.model_validate({**block.model_dump(), "flags": flags})


def _stale(message: str | None = None) -> AiRereadError:
    return AiRereadError("PROPOSAL_STALE", message)


def _plan(
    store: AppStore,
    vault: VaultRepository,
    page: PageExtraction,
    proposal: AiRereadProposal,
    request: AiRereadAcceptRequest,
    now: datetime,
) -> tuple[DocumentLayout, PageExtraction]:
    """Kiểm điều kiện còn lại và dựng kế hoạch ghi một giao dịch; không ghi gì."""
    document_id, page_number = page.document_id, page.page_number
    if request.expected_version != page.version:
        raise AiRereadError("VERSION_CONFLICT", f"Trang đã thay đổi (version hiện tại {page.version}).")
    if proposal.page_version != page.version:
        raise _stale()
    document = store.get_imported_document(document_id)
    if document is None:
        raise DocumentNotFoundError(document_id)
    blocks = store.list_blocks(document_id, page_number)
    offline = _find(blocks, proposal.block_id)
    check_block_eligible(document, page, offline, vault)
    if offline.bbox is None or document.layout_version is None or document.layout_settings_sha256 is None:
        raise AiRereadError("BLOCK_NOT_ELIGIBLE")
    geometry: PageGeometry | None = store.get_page_geometry(document_id, page_number)
    ocr_text = region_ocr_text(geometry, offline.bbox) or ""
    if geometry is None or not ocr_text or ocr_text != proposal.ocr_text:
        raise _stale()
    updated = with_vlm_block(page, proposal)
    try:
        replacement = _drop_unfit_mismatch_flag(vlm_block(offline, proposal.blocks[0], ocr_text), updated)
        ledger = _page_ledger(store, document_id, page_number)
        if ledger is not None:
            ledger = retype_ledger(ledger, [(offline, replacement)], geometry, page)
    except (ValueError, LedgerError) as exc:
        raise _stale() from exc
    page_blocks = [replacement if block.block_id == offline.block_id else block for block in blocks]
    audit = PageEditAudit(
        document_id=document_id,
        page_number=page_number,
        from_version=page.version,
        to_version=updated.version,
        editor_id=request.editor_id,
        changes=[],
        at=now,
    )
    plan = DocumentLayout(
        layout_version=document.layout_version,
        layout_settings_sha256=document.layout_settings_sha256,
        pages=[PageLayout(page_number=page_number, blocks=page_blocks, ledger=ledger)],
        page_rewrites=[PageRewrite(page=updated, expected_version=page.version, audit=audit)],
    )
    return plan, updated


def accept_proposal(
    store: AppStore,
    vault: VaultRepository,
    runtime: ImportRuntime,
    document_id: str,
    page_number: int,
    block_id: str,
    proposal_id: str,
    request: AiRereadAcceptRequest,
    *,
    now: datetime,
) -> PageExtraction:
    """`POST …/ai-reread/{proposal_id}/accept` (§6.10a). Lỗi: `ProposalNotFoundError` (404), `DocumentNotFoundError`
    (404), `AiRereadError` (409) theo thứ tự kiểm ở docstring của module. Ghi lỗi vì version lệch chen ngang ⇒
    `VERSION_CONFLICT`, không ghi gì. Sau commit và nhả khoá: xếp job cắt ảnh khi khối mới cần ảnh."""
    with runtime.locks.hold(document_id):
        proposal = _record(store, document_id, page_number, block_id, proposal_id)
        page = get_page_extraction(store, document_id, page_number)
        plan, updated = _plan(store, vault, page, proposal, request, now)
        try:
            store.save_document_layout(document_id, plan)
        except VersionConflictError as exc:
            raise AiRereadError(
                "VERSION_CONFLICT", f"Trang đã thay đổi (version hiện tại {exc.current_version})."
            ) from exc
        store.accept_ai_reread_proposal(proposal_id)
    schedule_crops(runtime.crops, store, document_id)
    return updated
