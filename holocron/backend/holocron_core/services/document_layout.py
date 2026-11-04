"""Dựng, lưu và dựng lại khối của MỘT tài liệu nhập (ADR-010 mục 13, 15; SPECIFICATION §5.7, §5.15; X09).

Các hàm ở đây chỉ tính kế hoạch lưu (`DocumentLayout`) hoặc ghi qua method của kho (`BlockRepository`); khoá tài liệu,
thứ tự với 0D/tự xác nhận/settle và thử lại do `services/document_import.py` quyết định.

- `plan_initial_layout` (bước kết thúc lần đọc, `_finish`, CHỈ khi tài liệu chưa có khối lưu): mọi trang lớp chữ
  (`tier_used = "text_layer"`) được dựng khối từ `PageGeometry` đã lưu bằng ĐÚNG `LayoutConfig` của lần chạy →
  `page_numbers` cấp tài liệu → ledger mang `page_drop_reason` theo vai trò hiệu lực → trang lỗi bố cục (bộ dựng lỗi,
  vượt trần, ledger lệch, thiếu geometry) mang `LAYOUT_FAILED`. Trang OCR/bitmap: không khối, không `LAYOUT_FAILED`.
  `layout_version`/`layout_settings_sha256` luôn được ghi (kể cả khi mọi trang hỏng); hash là
  `layout.settings_sha256()` của đúng đối tượng đã chạy.
- `plan_rebuild` (`PATCH …/lines` trên tài liệu chưa đóng có khối lưu): dựng lại mọi trang lớp chữ từ geometry và dòng
  HIỆN TẠI (dòng thô không sửa giữ tham chiếu `word`; dòng sửa/gõ thêm thành `confirmed_line` do bộ dựng X05 quyết),
  chạy lại `page_numbers` trên toàn tài liệu, tính lại `LAYOUT_FAILED`, rồi xét lại MỌI trang tự xác nhận: trang có khối
  mang cờ chặn (`auto_confirm.BLOCKING_BLOCK_FLAGS`) hay có lý do xem lại về trạng thái xem lại (`confirmed = false`,
  `confirmed_by`/`confirm_rule` null, `auto_confirm_undone` giữ false). Trang người dùng xác nhận giữ nguyên. Khối
  `source = "vlm"` đã lưu (đọc lại bằng AI, §6.10a) được giữ khi khối dựng lại cùng id, cùng `bbox`, cùng tập tham chiếu
  và không nằm trong `vlm_rejected_block_ids` (đầu vào của cổng không đổi; `by_type` của ledger chuyển theo loại);
  ngược lại khối thành offline và chỗ lệch của nó bị bỏ khỏi `vlm_mismatches` (`VLM_REGION` giữ nguyên). Khối có
  tập tham chiếu không đổi giữ id (id theo nội dung nguồn) và giữ ảnh cắt; khối đổi id mất hàng `crop_images` cũ (kho
  xoá khi lưu). Không dựng Section, không gọi LLM.
- `refresh_drop_reasons`: ghi lại `page_drop_reason` của ledger theo vai trò hiệu lực hiện tại, qua
  `extraction_ledger.with_page_drop_reason` (bước khối của `_finish` trên tài liệu chưa đóng đã có khối: sửa ledger
  lệch). Đổi `page_role`/`dropped` (0D, đánh giá lại trang bìa, `…/restore`) ghi trang CÙNG ledger trong một giao
  dịch ở `services/document_import.py`.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping, Sequence

from ..domain.blocks import CROPPED_TYPES, LAYOUT_VERSION, Block, BlockFlag, BlockType, ExtractionLedger
from ..domain.documents import PageExtraction, PageGeometry, PageRole, ReviewReason
from ..domain.repositories import DocumentLayout, DocumentRepository, PageLayout, PageRewrite
from .auto_confirm import has_blocking_block_flags
from .block_pipeline import LayoutConfig, PageBlocks
from .extraction_ledger import LedgerError, SourceIndex, block_refs, retype_by_type, with_page_drop_reason

__all__ = [
    "LAYOUT_FAILED",
    "blocks_needing_crop",
    "build_document_blocks",
    "effective_role",
    "is_text_layer_page",
    "plan_failed_layout",
    "plan_initial_layout",
    "plan_rebuild",
    "refresh_drop_reasons",
    "retype_ledger",
    "with_layout_reason",
]

_LOGGER = logging.getLogger(__name__)

LAYOUT_FAILED: ReviewReason = "LAYOUT_FAILED"
_CROP_FLAGS: frozenset[BlockFlag] = frozenset({"crop_pending", "crop_failed", "crop_capped"})
_CROP_GIVEN_UP: frozenset[BlockFlag] = frozenset({"crop_failed", "crop_capped"})


def blocks_needing_crop(blocks: Iterable[Block], cropped_block_ids: Iterable[str] = ()) -> list[Block]:
    """Khối cần ảnh cắt mà chưa có (ADR-010 mục 13 "Job cắt ảnh nền"; XCROP), giữ thứ tự vào: `type` thuộc
    `CROPPED_TYPES`, có `bbox`, mang `crop_pending`, KHÔNG mang `crop_failed`/`crop_capped` (không tự thử lại) và chưa
    có hàng `crop_images` (`cropped_block_ids`). Khối `bbox = null` không bao giờ được cắt."""
    cropped = set(cropped_block_ids)
    return [
        block
        for block in blocks
        if block.type in CROPPED_TYPES
        and block.bbox is not None
        and "crop_pending" in block.flags
        and not _CROP_GIVEN_UP.intersection(block.flags)
        and block.block_id not in cropped
    ]


def is_text_layer_page(page: PageExtraction) -> bool:
    """Trang dựng khối được: đã đọc bằng lớp chữ (`tier_used = "text_layer"`, không `pending`/`failed`)."""
    return page.tier_used == "text_layer" and page.status in ("ok", "needs_review")


def effective_role(page: PageExtraction) -> PageRole:
    """Vai trò HIỆU LỰC cho `with_page_drop_reason`: vai trò của trang khi đang bỏ mềm; `"content"` khi không bỏ mềm
    (kể cả trang đã được khôi phục)."""
    return page.page_role if page.dropped else "content"


def build_document_blocks(
    document_id: str,
    pages: Sequence[PageExtraction],
    geometries: Mapping[int, PageGeometry | None],
    layout: LayoutConfig,
) -> dict[int, PageBlocks]:
    """Khối của mọi trang lớp chữ (stage từng trang của `layout`) rồi `page_numbers` CẤP TÀI LIỆU; khoá = số trang.
    Trang lớp chữ không có geometry ⇒ `layout_failed`."""
    built = [
        layout.build_page_blocks(
            document_id=document_id,
            page_number=page.page_number,
            geometry=geometries.get(page.page_number),
            extraction=page,
        )
        for page in pages
        if is_text_layer_page(page)
    ]
    return {page.page_number: page for page in layout.page_numbers.run(built)}


def with_layout_reason(page: PageExtraction, failed: bool) -> PageExtraction:
    """Trang với `LAYOUT_FAILED` thêm (`failed`) hoặc gỡ, `status` tính lại (`needs_review` ⇔ còn lý do); không đổi gì
    khác (kể cả `version`). Lý do không đổi ⇒ trả đúng `page`."""
    kept = [reason for reason in page.review_reasons if reason != LAYOUT_FAILED]
    reasons = [*kept, LAYOUT_FAILED] if failed else kept
    if reasons == list(page.review_reasons):
        return page
    return PageExtraction.model_validate(
        {**page.model_dump(), "review_reasons": reasons, "status": "needs_review" if reasons else "ok"}
    )


def _ledger_for(page: PageExtraction, built: PageBlocks) -> ExtractionLedger | None:
    if built.ledger is None:
        return None
    return with_page_drop_reason(built.ledger, effective_role(page))


def _rewrite(stored: PageExtraction, updated: PageExtraction) -> PageRewrite:
    """Hàng trang ghi lại cùng giao dịch lưu khối: `version` của JSON trang = version mới của hàng."""
    return PageRewrite(page=updated.model_copy(update={"version": stored.version + 1}), expected_version=stored.version)


def plan_initial_layout(
    document_id: str,
    pages: Sequence[PageExtraction],
    geometries: Mapping[int, PageGeometry | None],
    layout: LayoutConfig,
    *,
    blocks_after_close: bool,
) -> DocumentLayout:
    """Lần lưu khối ĐẦU của tài liệu (C2, C3): khối + ledger của mọi trang lớp chữ, `LAYOUT_FAILED` cho trang lỗi bố
    cục, `layout_version`/hash của `layout`, cờ `blocks_after_close`. Người gọi lưu NGUYÊN TỬ bằng
    `save_document_layout`."""
    built = build_document_blocks(document_id, pages, geometries, layout)
    page_layouts: list[PageLayout] = []
    rewrites: list[PageRewrite] = []
    for page in pages:
        result = built.get(page.page_number)
        if result is None:
            continue
        page_layouts.append(
            PageLayout(page_number=page.page_number, blocks=result.blocks, ledger=_ledger_for(page, result))
        )
        updated = with_layout_reason(page, result.layout_failed)
        if updated is not page:
            rewrites.append(_rewrite(page, updated))
    return DocumentLayout(
        layout_version=LAYOUT_VERSION,
        layout_settings_sha256=layout.settings_sha256(),
        blocks_after_close=blocks_after_close,
        pages=page_layouts,
        page_rewrites=rewrites,
    )


def plan_failed_layout(
    pages: Sequence[PageExtraction], layout: LayoutConfig, *, blocks_after_close: bool
) -> DocumentLayout:
    """Lần lưu khối đầu khi CHÍNH bộ dựng hoặc `page_numbers` ném lỗi dữ liệu (không dựng được kế hoạch nào): không
    khối, không ledger; MỌI trang lớp chữ mang `LAYOUT_FAILED` (lý do hiển thị, trang không được tự xác nhận) và
    `layout_version`/hash vẫn được ghi như khi mọi trang hỏng ở `plan_initial_layout` (Q1: không null im lặng)."""
    rewrites: list[PageRewrite] = []
    for page in pages:
        if not is_text_layer_page(page):
            continue
        updated = with_layout_reason(page, True)
        if updated is not page:
            rewrites.append(_rewrite(page, updated))
    return DocumentLayout(
        layout_version=LAYOUT_VERSION,
        layout_settings_sha256=layout.settings_sha256(),
        blocks_after_close=blocks_after_close,
        page_rewrites=rewrites,
    )


def _carry_crop(block: Block, previous: Block | None) -> Block:
    """Khối dựng lại cùng id, cùng loại, cùng hộp với khối đã lưu giữ ảnh cắt (`crop_ref` và cờ ảnh cắt) của nó."""
    if (
        previous is None
        or block.type not in CROPPED_TYPES
        or previous.type != block.type
        or previous.bbox != block.bbox
    ):
        return block
    if previous.crop_ref == block.crop_ref and _CROP_FLAGS.intersection(previous.flags) == _CROP_FLAGS.intersection(
        block.flags
    ):
        return block
    flags = (set(block.flags) - _CROP_FLAGS) | (_CROP_FLAGS.intersection(previous.flags))
    return Block.model_validate({**block.model_dump(), "crop_ref": previous.crop_ref, "flags": sorted(flags)})


def retype_ledger(
    ledger: ExtractionLedger, swaps: Iterable[tuple[Block, Block]], geometry: PageGeometry | None, page: PageExtraction
) -> ExtractionLedger:
    """Ledger sau khi thay khối `old` bằng `new` CÙNG tập tham chiếu nguồn (ADR-009 mục 2: khối `vlm` thay khối offline
    của vùng): `by_type` chuyển đơn vị của các tham chiếu từ loại cũ sang loại mới; số đếm của basis không đổi. Ném
    `LedgerError` khi tập tham chiếu khác nhau, thiếu geometry hoặc tham chiếu không giải được."""
    moves: list[tuple[BlockType, BlockType, int]] = []
    index: SourceIndex | None = None
    for old, new in swaps:
        refs = block_refs(old)
        if set(refs) != set(block_refs(new)):
            raise LedgerError("khối thay thế phải giữ đúng tập tham chiếu nguồn")
        if old.type == new.type:
            continue
        if geometry is None:
            raise LedgerError("thiếu geometry để tính lại by_type")
        index = index or SourceIndex(geometry, page)
        for ref in refs:
            index.validate(ref)
        moves.append((old.type, new.type, sum(index.units(ref) for ref in refs)))
    return retype_by_type(ledger, moves) if moves else ledger


def _carry_vlm(built: list[Block], previous: Mapping[str, Block], page: PageExtraction) -> list[tuple[Block, Block]]:
    """Cặp (khối offline vừa dựng, khối `vlm` đã lưu) để giữ khối `vlm` qua dựng lại (ADR-009 mục 3.5): cùng
    `block_id` (id theo tham chiếu nguồn), cùng `bbox`, cùng tập tham chiếu, khối không nằm trong
    `vlm_rejected_block_ids`. Đầu vào của cổng (tham chiếu, geometry) không đổi nên quyết định của cổng không đổi;
    khối đổi id (dòng của vùng bị sửa) ⇒ không giữ, khối thành offline (phía an toàn)."""
    rejected = set(page.vlm_rejected_block_ids)
    pairs: list[tuple[Block, Block]] = []
    for block in built:
        stored = previous.get(block.block_id)
        if (
            stored is not None
            and stored.source == "vlm"
            and block.source != "vlm"
            and block.block_id not in rejected
            and stored.bbox == block.bbox
            and set(block_refs(stored)) == set(block_refs(block))
        ):
            pairs.append((block, stored))
    return pairs


def _with_vlm_blocks(
    built: list[Block],
    ledger: ExtractionLedger | None,
    previous: Mapping[str, Block],
    page: PageExtraction,
    geometry: PageGeometry | None,
) -> tuple[list[Block], ExtractionLedger | None]:
    pairs = _carry_vlm(built, previous, page)
    if not pairs:
        return built, ledger
    try:
        retyped = None if ledger is None else retype_ledger(ledger, pairs, geometry, page)
    except LedgerError:
        _LOGGER.warning("không giữ được khối VLM của trang %d khi dựng lại (ledger không khớp)", page.page_number)
        return built, ledger
    carried = {block.block_id: stored for block, stored in pairs}
    return [carried.get(block.block_id, block) for block in built], retyped


def _with_vlm_mismatches(page: PageExtraction, blocks: Sequence[Block]) -> PageExtraction:
    """Bỏ chỗ lệch của khối không còn là khối `vlm` sau dựng lại; hết chỗ lệch ⇒ bỏ `TABLE_TOKEN_MISMATCH`.
    `VLM_REGION` giữ nguyên (ADR-009 mục 3.6). Không đổi ⇒ trả đúng `page`."""
    vlm_ids = {block.block_id for block in blocks if block.source == "vlm"}
    mismatches = [m for m in page.vlm_mismatches if m.block_id in vlm_ids]
    if len(mismatches) == len(page.vlm_mismatches):
        return page
    reasons = [r for r in page.review_reasons if mismatches or r != "TABLE_TOKEN_MISMATCH"]
    status = page.status if page.status == "failed" else ("needs_review" if reasons else "ok")
    return PageExtraction.model_validate(
        {
            **page.model_dump(),
            "vlm_mismatches": [m.model_dump() for m in mismatches],
            "review_reasons": reasons,
            "status": status,
        }
    )


def _block_key(block: Block) -> tuple[int, str]:
    return block.reading_order, block.block_id


def _reopened(page: PageExtraction) -> PageExtraction:
    """Trang tự xác nhận về trạng thái xem lại (không phải người dùng hoàn tác: `auto_confirm_undone` giữ nguyên)."""
    return PageExtraction.model_validate(
        {**page.model_dump(), "confirmed": False, "confirmed_at": None, "confirmed_by": None, "confirm_rule": None}
    )


def plan_rebuild(
    document_id: str,
    pages: Sequence[PageExtraction],
    geometries: Mapping[int, PageGeometry | None],
    stored_blocks: Iterable[Block],
    stored_ledgers: Iterable[ExtractionLedger],
    layout: LayoutConfig,
    *,
    stored_sha256: str | None,
    rebuilt_page: int,
) -> tuple[DocumentLayout, dict[int, PageExtraction]]:
    """Kế hoạch dựng lại sau `PATCH …/lines` (C6): mọi trang lớp chữ dựng lại từ trạng thái HIỆN TẠI (`pages` đã gồm
    trang vừa sửa), `page_numbers` chạy lại toàn tài liệu. Trang được lưu lại: `rebuilt_page`, mọi trang lớp chữ khi
    `stored_sha256` khác hash của `layout`, và trang khác có khối/ledger đổi (ví dụ `page_numbers` đổi loại). Trả
    `(DocumentLayout, trang đổi)`; `trang đổi` gồm trang có `LAYOUT_FAILED` đổi hoặc tự xác nhận bị bỏ (chưa đặt
    `version`; người gọi ghi `rebuilt_page` cùng audit, các trang còn lại làm `page_rewrites`)."""
    new_sha = layout.settings_sha256()
    rebuild_all = stored_sha256 != new_sha
    built = build_document_blocks(document_id, pages, geometries, layout)
    by_page: dict[int, list[Block]] = {}
    for block in stored_blocks:
        by_page.setdefault(block.page_number, []).append(block)
    ledgers = {ledger.page_number: ledger for ledger in stored_ledgers}
    page_layouts: list[PageLayout] = []
    final_blocks: dict[int, list[Block]] = {number: list(blocks) for number, blocks in by_page.items()}
    layout_failed: dict[int, bool] = {}
    for page in pages:
        number = page.page_number
        result = built.get(number)
        if result is None:
            continue
        layout_failed[number] = result.layout_failed
        previous = {block.block_id: block for block in by_page.get(number, [])}
        blocks = [_carry_crop(block, previous.get(block.block_id)) for block in result.blocks]
        ledger = _ledger_for(page, result)
        blocks, ledger = _with_vlm_blocks(blocks, ledger, previous, page, geometries.get(number))
        same = sorted(blocks, key=_block_key) == sorted(previous.values(), key=_block_key) and ledger == ledgers.get(
            number
        )
        if rebuild_all or number == rebuilt_page or not same:
            page_layouts.append(PageLayout(page_number=number, blocks=blocks, ledger=ledger))
            final_blocks[number] = blocks
    changed: dict[int, PageExtraction] = {}
    for page in pages:
        number = page.page_number
        failed = layout_failed.get(number)
        current = page
        # Trang tự xác nhận có lý do xem lại (kể cả `LAYOUT_FAILED` mới) hay khối mang cờ chặn ⇒ về xem lại TRƯỚC khi
        # gắn lý do (trang `confirmed_by = "auto"` không được có `review_reasons`).
        if page.confirmed_by == "auto" and (
            bool(failed) or bool(page.review_reasons) or has_blocking_block_flags(final_blocks.get(number, []))
        ):
            current = _reopened(page)
            _LOGGER.info(
                "trang %d của tài liệu %s về xem lại sau khi dựng lại khối (cờ chặn hoặc lỗi bố cục)",
                number,
                document_id,
            )
        if failed is not None:
            current = with_layout_reason(current, failed)
            current = _with_vlm_mismatches(current, final_blocks.get(number, []))
        if current is not page:
            changed[number] = current
    plan = DocumentLayout(layout_version=LAYOUT_VERSION, layout_settings_sha256=new_sha, pages=page_layouts)
    return plan, changed


def refresh_drop_reasons(
    store: DocumentRepository, document_id: str, page_numbers: Iterable[int] | None = None
) -> None:
    """Ghi lại `page_drop_reason` của ledger đã lưu theo vai trò hiệu lực HIỆN TẠI của trang (`page_numbers` None ⇒
    mọi trang). Tài liệu không có khối lưu, trang không có ledger hoặc ledger không đổi ⇒ không ghi gì. Khối của trang
    được lưu lại nguyên như đang lưu (cùng giao dịch với ledger)."""
    document = store.get_imported_document(document_id)
    if document is None or document.layout_version is None or document.layout_settings_sha256 is None:
        return
    wanted = None if page_numbers is None else set(page_numbers)
    page_layouts: list[PageLayout] = []
    for ledger in store.list_extraction_ledgers(document_id):
        number = ledger.page_number
        if wanted is not None and number not in wanted:
            continue
        page = store.get_page(document_id, number, PageExtraction)
        if page is None:
            continue
        updated = with_page_drop_reason(ledger, effective_role(page))
        if updated != ledger:
            page_layouts.append(
                PageLayout(page_number=number, blocks=store.list_blocks(document_id, number), ledger=updated)
            )
    if page_layouts:
        store.save_document_layout(
            document_id,
            DocumentLayout(
                layout_version=document.layout_version,
                layout_settings_sha256=document.layout_settings_sha256,
                pages=page_layouts,
            ),
        )
