"""Pipeline dựng khối của một trang lớp chữ và hash cấu hình bộ dựng (ADR-010 mục 6, 13, 15; spec §5.15).

Thứ tự stage từng trang chốt MỘT chỗ ở `PAGE_STAGES`: `base_blocks` (X05) → [bảng/hình vector, X07] → [code, X06] →
`reading_order` (X05, LUÔN cuối, gán `reading_order` và id) → kiểm ledger. `page_numbers` là stage CẤP TÀI LIỆU riêng
(`services/page_numbers.py`), chạy sau khi mọi trang đã dựng khối.

Mọi nhánh lỗi (thiếu geometry, stage ném BẤT KỲ `Exception` nào, vượt `MAX_PAGE_BLOCKS`, XY-cut vượt trần, khối không
phải `paragraph` quá dài, tham chiếu không giải được, ledger lệch) ⇒ `PageBlocks.layout_failed = True`, `blocks = []`,
KHÔNG ném `Exception` ra ngoài (`KeyboardInterrupt`/`SystemExit` không phải `Exception` nên vẫn lan ra); ledger là None
khi thiếu geometry hay lỗi trước khi đếm, và có `balanced = false` khi ledger lệch.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import Field, model_validator

from ..domain.blocks import LAYOUT_VERSION, MAX_PAGE_BLOCKS, Block, ExtractionLedger
from ..domain.documents import PageExtraction, PageGeometry
from ..domain.schemas import StrictModel
from ..infrastructure.lang_patterns import LangPatterns
from .block_builder import BaseBlockStage
from .extraction_ledger import LedgerError, SourceIndex, compute_ledger
from .page_numbers import PageNumberCandidate, PageNumberSettings, PageNumberStage
from .reading_order import ReadingOrderStage

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PageContext:
    """Đầu vào bất biến của các stage từng trang (nội bộ, không ra response)."""

    document_id: str
    page_number: int
    geometry: PageGeometry
    extraction: PageExtraction
    lang: LangPatterns
    # Ngưỡng số trang: MỘT nguồn, cùng đối tượng với `PageNumberStage.settings` của người gọi (vào hash qua stage đó).
    page_number_settings: PageNumberSettings = field(default_factory=PageNumberSettings)


class PageStage(Protocol):
    """Một stage tất định của pipeline từng trang; `fingerprint()` = `{"name", "version", "settings"}`."""

    name: str

    def fingerprint(self) -> dict[str, Any]: ...

    def run(self, context: PageContext, blocks: list[Block]) -> list[Block]: ...


class PageBlocks(StrictModel):
    """Kết quả pipeline của MỘT trang (nội bộ, không ra response; response khối của trang là `PageBlockList`, §6.10).

    `layout_failed` ⇒ `blocks = []`; X09 gắn `LAYOUT_FAILED` từ field này. `page_number_candidates` = None khi
    `page_numbers` chưa chạy trên trang (stage cấp tài liệu tự lập danh sách ứng viên và loại gốc ở lần chạy đầu); khối
    đã lưu không giữ danh sách này (điều kiện cho X09: docstring `services/page_numbers.py`).
    """

    document_id: str = Field(pattern=r"^doc_[0-9a-f]{16}$")
    page_number: int = Field(ge=1)
    blocks: list[Block] = Field(default_factory=list)
    ledger: ExtractionLedger | None = None
    layout_failed: bool = False
    page_number_candidates: list[PageNumberCandidate] | None = None

    @model_validator(mode="after")
    def _failed_has_no_blocks(self) -> PageBlocks:
        if self.layout_failed and self.blocks:
            raise ValueError("trang layout_failed không có khối")
        return self


# Danh sách stage từng trang theo thứ tự chạy — NƠI DUY NHẤT chốt thứ tự (X07 chèn bảng/hình vector, X06 chèn code
# giữa `base_blocks` và `reading_order`).
PAGE_STAGES: tuple[PageStage, ...] = (
    BaseBlockStage(),
    ReadingOrderStage(),
)


def _failed(document_id: str, page_number: int, ledger: ExtractionLedger | None = None) -> PageBlocks:
    return PageBlocks(document_id=document_id, page_number=page_number, layout_failed=True, ledger=ledger)


def build_page_blocks(
    *,
    document_id: str,
    page_number: int,
    geometry: PageGeometry | None,
    extraction: PageExtraction,
    lang: LangPatterns,
    stages: Sequence[PageStage] = PAGE_STAGES,
    page_number_settings: PageNumberSettings | None = None,
) -> PageBlocks:
    """Chạy các stage từng trang rồi kiểm ledger. Dùng cho cả lần đọc đầu (basis `pdfium`) và dựng lại sau
    `PATCH …/lines` (dòng đã sửa/gõ thêm ⇒ basis `confirmed_lines`). Lỗi `Exception` của stage ⇒ `layout_failed`."""
    if geometry is None:
        return _failed(document_id, page_number)
    context = PageContext(
        document_id, page_number, geometry, extraction, lang, page_number_settings or PageNumberSettings()
    )
    try:
        blocks: list[Block] = []
        for stage in stages:
            blocks = stage.run(context, blocks)
            if len(blocks) > MAX_PAGE_BLOCKS:
                raise LedgerError(f"trang có quá {MAX_PAGE_BLOCKS} khối")
        ledger = compute_ledger(document_id, page_number, blocks, SourceIndex(geometry, extraction))
    except Exception as exc:  # mọi lỗi của bộ dựng ⇒ LAYOUT_FAILED (ADR-010 mục 6), không làm hỏng lần đọc
        logger.warning(
            "Dựng khối trang %d thất bại (%s): trang dùng đường dòng", page_number, type(exc).__name__, exc_info=True
        )
        return _failed(document_id, page_number)
    if not ledger.balanced:
        logger.warning("Ledger trang %d lệch: trang dùng đường dòng", page_number)
        return _failed(document_id, page_number, ledger)
    return PageBlocks(document_id=document_id, page_number=page_number, blocks=blocks, ledger=ledger)


def _canonical_sha256(value: Any) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def layout_fingerprints(
    page_numbers: PageNumberStage, stages: Sequence[PageStage] = PAGE_STAGES
) -> list[dict[str, Any]]:
    """`fingerprint()` của mọi stage tất định theo thứ tự chạy: stage từng trang rồi `page_numbers`."""
    return [*(stage.fingerprint() for stage in stages), page_numbers.fingerprint()]


def layout_settings_sha256(
    page_numbers: PageNumberStage,
    stages: Sequence[PageStage] = PAGE_STAGES,
    *,
    layout_version: int = LAYOUT_VERSION,
) -> str:
    """sha256 JSON chuẩn hoá của `{"layout_version", "fingerprints"}` (ADR-010 mục 15)."""
    return _canonical_sha256(
        {"layout_version": layout_version, "fingerprints": layout_fingerprints(page_numbers, stages)}
    )


def default_layout_settings_sha256(lang: LangPatterns) -> str:
    """Hash của cấu hình mặc định (`PAGE_STAGES`, `PageNumberStage` mặc định) với mẫu ngôn ngữ `lang`."""
    return layout_settings_sha256(PageNumberStage(lang))
