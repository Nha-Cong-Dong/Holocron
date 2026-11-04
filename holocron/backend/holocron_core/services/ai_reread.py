"""Đọc lại một khối bằng AI theo yêu cầu người dùng (spec §6.10a; ADR-009 mục 15).

Luồng tạo đề xuất (code): render vùng `bbox` của khối (`RegionRenderer`) → `VisionReader.read_region_on_request` →
chữ OCR độc lập của CÙNG vùng → `text_layer_overlap` của vùng → `vision_gate.judge_region`. Đề xuất KHÔNG ghi kho
trang: nó vào kho tạm theo instance store (`AiRereadProposalRepository`) và mang `page_version` lúc tạo. Chữ VLM chỉ
ra response khi cổng nhận (I3); đề xuất `rejected` không mang chữ VLM nào.

Thao tác người dùng bấm: KHÔNG xét công tắc đồng ý (ADR-009 mục 7), nhưng `HOLOCRON_VLM_MODE = off`, thiếu nhà cung
cấp `vlm` và chạm trần đều chặn (409). Lỗi gọi VLM ⇒ 503 có mã. Thông điệp lỗi là câu tiếng Việt cố định, không nêu
khoá, URL hay tên model.

Nguồn chữ OCR độc lập của vùng (ADR-009 mục 2, 3.1: khối nhận có `line_refs` là các từ OCR của vùng): các từ OCR của
`PageGeometry` có tâm hộp nằm trong vùng — `ocr_lines` của trang lớp chữ, hoặc `lines` của trang mà hình học lấy từ
OCR. Không có từ nào ⇒ `None` (cổng từ chối `ocr_missing`). `text_layer_overlap` = diện tích phần giao của vùng với
hợp các hộp từ lớp chữ, chia diện tích vùng; trang không có lớp chữ ⇒ 0; thiếu hình học hoặc vùng suy biến ⇒ NaN
(cổng từ chối, fail closed).
"""

from __future__ import annotations

import itertools
import math
import secrets
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from ..domain.ai import AiConsent, AiRole, AiVlmMode, CostEstimate, ProviderPreview, SelectedProvider
from ..domain.ai_reread import (
    AI_REREAD_FLAGS,
    AiRereadBlock,
    AiRereadErrorCode,
    AiRereadMismatch,
    AiRereadProposal,
)
from ..domain.blocks import Block, UnitBBox
from ..domain.documents import BBox, GeoLine, PageExtraction, PageGeometry
from ..domain.repositories import AppStore, ImportedDocument, VaultRepository
from ..infrastructure.document_readers.pdf_reader import RegionRenderer, RenderedCrop
from ..infrastructure.document_readers.protocol import CropRegion
from ..infrastructure.vision_reader import (
    ClientFactory,
    RegionImage,
    VisionReader,
    VisionReaderSettings,
    VisionSkipped,
    VlmRegionResult,
)
from .document_import import DocumentNotFoundError, PageStateError, check_page_editable, get_page_extraction
from .provider_selection import ProviderSelection
from .vision_gate import judge_region, mismatch_ids

ERROR_MESSAGES: dict[AiRereadErrorCode, str] = {
    "BLOCK_NOT_ELIGIBLE": "Khối này không đọc lại bằng AI được (khối không có cờ phù hợp, không cắt được vùng, hoặc "
    "trang/tài liệu không còn sửa được).",
    "AI_NOT_CONFIGURED": "Chưa cấu hình nhà cung cấp AI đọc ảnh. Hãy thêm nhà cung cấp ở trang Cài đặt.",
    "AI_MODE_OFF": "Người vận hành đã tắt gửi ảnh tới AI trên máy này (HOLOCRON_VLM_MODE = off).",
    "AI_BUDGET_EXHAUSTED": "Mọi nhà cung cấp AI đọc ảnh đã chạm trần chi phí. Hãy nâng trần ở trang Cài đặt.",
    "VLM_TIMEOUT": "Nhà cung cấp AI không trả lời kịp. Hãy thử lại sau.",
    "VLM_RATE_LIMITED": "Nhà cung cấp AI đang giới hạn số yêu cầu. Hãy thử lại sau ít phút.",
    "VLM_UPSTREAM_ERROR": "Gọi nhà cung cấp AI thất bại. Hãy thử lại sau.",
}
_NO_LABEL = "nhà cung cấp AI"


class AiRereadError(Exception):
    """Lỗi nghiệp vụ có mã của §6.10a; router chọn mã HTTP theo `code`."""

    def __init__(self, code: AiRereadErrorCode) -> None:
        super().__init__(ERROR_MESSAGES[code])
        self.code: AiRereadErrorCode = code
        self.message = ERROR_MESSAGES[code]


class ProposalNotFoundError(LookupError):
    """Kho tạm không có đề xuất này cho đúng tài liệu, trang và khối (404)."""


class OriginalPaths(Protocol):
    def path_for(self, sha256: str) -> Path: ...

    def exists(self, sha256: str) -> bool: ...


@dataclass(frozen=True)
class AiRereadRuntime:
    """Phụ thuộc của một lần tạo đề xuất; router dựng từ `Depends`, test thay bằng bản giả (không gọi mạng)."""

    store: AppStore
    vault: VaultRepository
    files: OriginalPaths
    renderer: RegionRenderer
    selection: ProviderSelection
    consent: AiConsent
    client_factory: ClientFactory
    settings: VisionReaderSettings
    vlm_mode: AiVlmMode
    clock: Callable[[], datetime]


class _RecordingSelector:
    """`ProviderSelector` ghi lại nhà cung cấp đã chọn để báo `recipient_label` (nhãn của nơi đã nhận ảnh)."""

    def __init__(self, selection: ProviderSelection) -> None:
        self._selection = selection
        self.provider_ids: list[str] = []

    def select(self, role: AiRole, estimate: CostEstimate) -> SelectedProvider | None:
        selected = self._selection.select(role, estimate)
        if selected is not None:
            self.provider_ids.append(selected.provider_id)
        return selected

    def peek(self, role: AiRole) -> ProviderPreview | None:
        return self._selection.peek(role)

    def label(self) -> str:
        if not self.provider_ids:
            return _NO_LABEL
        last = self.provider_ids[-1]
        labels = {candidate.provider_id: candidate.label for candidate in self._selection.candidates()}
        return labels.get(last, _NO_LABEL)


# --------------------------------------------------------------------------- #
# Điều kiện của khối
# --------------------------------------------------------------------------- #


def block_has_reread_flag(block: Block) -> bool:
    """Khối offline có `bbox` và mang ít nhất một cờ của `AI_REREAD_FLAGS` (điều kiện theo khối của §6.10a)."""
    return block.source != "vlm" and block.bbox is not None and bool(AI_REREAD_FLAGS.intersection(block.flags))


def _find_block(store: AppStore, document_id: str, page_number: int, block_id: str) -> Block:
    for block in store.list_blocks(document_id, page_number):
        if block.block_id == block_id:
            return block
    raise DocumentNotFoundError(document_id)


def _load(
    store: AppStore, document_id: str, page_number: int, block_id: str
) -> tuple[ImportedDocument, PageExtraction, Block]:
    page = get_page_extraction(store, document_id, page_number)
    document = store.get_imported_document(document_id)
    if document is None:
        raise DocumentNotFoundError(document_id)
    return document, page, _find_block(store, document_id, page_number, block_id)


def check_block_eligible(
    document: ImportedDocument, page: PageExtraction, block: Block, vault: VaultRepository
) -> None:
    """409 `BLOCK_NOT_ELIGIBLE` (§6.10a): khối không đủ điều kiện, hoặc trang/tài liệu không còn sửa được (cùng
    điều kiện 409 của `PATCH …/lines`)."""
    if not block_has_reread_flag(block):
        raise AiRereadError("BLOCK_NOT_ELIGIBLE")
    try:
        check_page_editable(document, page, vault)
    except PageStateError as exc:
        raise AiRereadError("BLOCK_NOT_ELIGIBLE") from exc


# --------------------------------------------------------------------------- #
# OCR độc lập và độ giao với lớp chữ của vùng
# --------------------------------------------------------------------------- #


def _center_inside(box: BBox, region: UnitBBox) -> bool:
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    return region[0] <= cx <= region[2] and region[1] <= cy <= region[3]


def _region_text(lines: Sequence[GeoLine], region: UnitBBox) -> str | None:
    rows = [
        " ".join(word.text for word in line.words if word.bbox is not None and _center_inside(word.bbox, region))
        for line in lines
    ]
    text = "\n".join(row for row in rows if row)
    return text or None


def region_ocr_text(geometry: PageGeometry | None, region: UnitBBox) -> str | None:
    """Chữ OCR độc lập của vùng: từ OCR có tâm hộp trong vùng, theo thứ tự dòng rồi từ; không có ⇒ `None`."""
    if geometry is None:
        return None
    return _region_text(geometry.lines if geometry.source == "ocr" else geometry.ocr_lines, region)


def _clip(box: BBox, region: UnitBBox) -> BBox | None:
    x0, y0 = max(box[0], region[0]), max(box[1], region[1])
    x1, y1 = min(box[2], region[2]), min(box[3], region[3])
    return (x0, y0, x1, y1) if x1 > x0 and y1 > y0 else None


def _covered_length(intervals: list[tuple[float, float]]) -> float:
    total = 0.0
    end = -math.inf
    for start, stop in sorted(intervals):
        if stop <= end:
            continue
        total += stop - max(start, end)
        end = stop
    return total


def union_area(boxes: Sequence[BBox]) -> float:
    """Diện tích hợp của các hộp (quét theo trục x; mỗi dải tính độ phủ theo y của các hộp đang mở)."""
    if not boxes:
        return 0.0
    edges = sorted({x for box in boxes for x in (box[0], box[2])})
    by_start = sorted(boxes, key=lambda box: box[0])
    active: list[BBox] = []
    cursor = 0
    area = 0.0
    for left, right in itertools.pairwise(edges):
        while cursor < len(by_start) and by_start[cursor][0] <= left:
            active.append(by_start[cursor])
            cursor += 1
        active = [box for box in active if box[2] > left]
        area += (right - left) * _covered_length([(box[1], box[3]) for box in active])
    return area


def text_layer_overlap(geometry: PageGeometry | None, region: UnitBBox) -> float:
    """Tỉ lệ diện tích vùng bị hộp từ lớp chữ phủ (ADR-009 mục 3.2). Trang mà hình học lấy từ OCR ⇒ 0; thiếu hình học
    hoặc vùng diện tích 0 ⇒ NaN (cổng từ chối)."""
    region_area = (region[2] - region[0]) * (region[3] - region[1])
    if geometry is None or region_area <= 0:
        return math.nan
    if geometry.source == "ocr":
        return 0.0
    clipped = [
        box
        for line in geometry.lines
        for word in line.words
        if word.bbox is not None and (box := _clip(word.bbox, region)) is not None
    ]
    return min(1.0, union_area(clipped) / region_area)


# --------------------------------------------------------------------------- #
# Tạo, bỏ đề xuất
# --------------------------------------------------------------------------- #


def _check_ai_available(runtime: AiRereadRuntime) -> None:
    if runtime.vlm_mode == "off":
        raise AiRereadError("AI_MODE_OFF")
    if not runtime.selection.configured("vlm"):
        raise AiRereadError("AI_NOT_CONFIGURED")
    if runtime.selection.budget_exhausted("vlm"):
        raise AiRereadError("AI_BUDGET_EXHAUSTED")


def _render(runtime: AiRereadRuntime, document: ImportedDocument, page_number: int, bbox: UnitBBox) -> RegionImage:
    """Ảnh vùng qua `RegionRenderer` (worker có giới hạn OS); file gốc mất hay render lỗi ⇒ không cắt được vùng."""
    sha256 = document.meta.sha256
    if not runtime.files.exists(sha256):
        raise AiRereadError("BLOCK_NOT_ELIGIBLE")
    request = CropRegion(region_id=0, page_index=page_number - 1, bbox=bbox)
    outcomes = runtime.renderer.render_regions(
        runtime.files.path_for(sha256), [request], max_edge_px=runtime.settings.max_edge_px
    )
    crop = outcomes[0] if len(outcomes) == 1 else None
    if not isinstance(crop, RenderedCrop):
        raise AiRereadError("BLOCK_NOT_ELIGIBLE")
    return RegionImage(png=crop.png, width_px=crop.width_px, height_px=crop.height_px, sha256=crop.sha256)


def _skipped_error(runtime: AiRereadRuntime, skipped: VisionSkipped) -> AiRereadError:
    if skipped.reason == "image_rejected":
        return AiRereadError("BLOCK_NOT_ELIGIBLE")
    if runtime.selection.budget_exhausted("vlm"):
        return AiRereadError("AI_BUDGET_EXHAUSTED")
    return AiRereadError("AI_NOT_CONFIGURED")


def _failure_error(result: VlmRegionResult) -> AiRereadError | None:
    if result.status == "timeout":
        return AiRereadError("VLM_TIMEOUT")
    if result.status == "upstream_error":
        return AiRereadError("VLM_RATE_LIMITED" if result.rate_limited else "VLM_UPSTREAM_ERROR")
    return None


def _proposal(
    page: PageExtraction,
    block: Block,
    result: VlmRegionResult,
    ocr_text: str | None,
    overlap: float,
    recipient_label: str,
) -> AiRereadProposal:
    decision = judge_region(result, ocr_text, overlap)
    blocks: list[AiRereadBlock] = []
    mismatches: list[AiRereadMismatch] = []
    # Cổng luôn đặt `block_type` khi nhận (`accepted_text`/`accepted_table`); mọi kết cục khác là `rejected`.
    if decision.outcome in ("accepted_text", "accepted_table") and decision.block_type is not None:
        blocks = [AiRereadBlock(type=decision.block_type, text=decision.text, rows=decision.rows, flags=decision.flags)]
        ids = mismatch_ids(block.block_id, decision.mismatches)
        mismatches = [
            AiRereadMismatch(mismatch_id=mismatch_id, **mismatch.model_dump())
            for mismatch_id, mismatch in zip(ids, decision.mismatches, strict=True)
        ]
    return AiRereadProposal(
        proposal_id="prp_" + secrets.token_hex(6),
        document_id=page.document_id,
        page_number=page.page_number,
        block_id=block.block_id,
        page_version=page.version,
        status="accepted_text" if blocks else "rejected",
        reason=decision.reason,
        blocks=blocks,
        mismatches=mismatches,
        ocr_text=ocr_text or "",
        input_sha256=result.input_sha256,
        from_cache=result.from_cache,
        recipient_label=recipient_label,
    )


def create_proposal(runtime: AiRereadRuntime, document_id: str, page_number: int, block_id: str) -> AiRereadProposal:
    """`POST …/blocks/{block_id}/ai-reread` (§6.10a). Không ghi kho trang; đề xuất vào kho tạm của store.

    Lỗi: không có tài liệu/trang/khối ⇒ `DocumentNotFoundError` (404); còn lại `AiRereadError` theo thứ tự kiểm: khối
    không đủ điều kiện, `AI_MODE_OFF`, `AI_NOT_CONFIGURED`, `AI_BUDGET_EXHAUSTED`, rồi lỗi gọi VLM (503)."""
    document, page, block = _load(runtime.store, document_id, page_number, block_id)
    check_block_eligible(document, page, block, runtime.vault)
    region = block.bbox
    if region is None:
        raise AiRereadError("BLOCK_NOT_ELIGIBLE")
    _check_ai_available(runtime)
    image = _render(runtime, document, block.page_number, region)
    selector = _RecordingSelector(runtime.selection)
    reader = VisionReader(
        consent=runtime.consent,
        selector=selector,
        recorder=runtime.selection,
        cache=runtime.store,
        client_factory=runtime.client_factory,
        settings=runtime.settings,
        clock=runtime.clock,
    )
    outcome = reader.read_region_on_request(document_id, image)
    if isinstance(outcome, VisionSkipped):
        raise _skipped_error(runtime, outcome)
    failure = _failure_error(outcome)
    if failure is not None:
        raise failure
    geometry = runtime.store.get_page_geometry(document_id, page_number)
    proposal = _proposal(
        page,
        block,
        outcome,
        region_ocr_text(geometry, region),
        text_layer_overlap(geometry, region),
        selector.label(),
    )
    runtime.store.put_ai_reread_proposal(proposal)
    return proposal


def reject_proposal(store: AppStore, document_id: str, page_number: int, block_id: str, proposal_id: str) -> None:
    """`POST …/ai-reread/{proposal_id}/reject` (§6.10a): chỉ bỏ đề xuất khỏi kho tạm; idempotent (đề xuất đã bỏ hay
    hết hiệu lực vẫn thành công). Không ghi trang, không đổi `version`. Kho không có đề xuất của đúng tài liệu, trang
    và khối ⇒ `ProposalNotFoundError`."""
    record = store.get_ai_reread_proposal(proposal_id)
    if record is None:
        raise ProposalNotFoundError(proposal_id)
    proposal = record.proposal
    if (proposal.document_id, proposal.page_number, proposal.block_id) != (document_id, page_number, block_id):
        raise ProposalNotFoundError(proposal_id)
    store.discard_ai_reread_proposal(proposal_id)
