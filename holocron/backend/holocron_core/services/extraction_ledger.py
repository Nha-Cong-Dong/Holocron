"""Sổ bảo toàn bóc tách (`ExtractionLedger`, ADR-010 mục 6) và tra cứu tham chiếu nguồn của một trang.

`SourceIndex` giải mọi `SourceRef` của trang về chữ, đơn vị ledger, hộp, vị trí luồng PDFium và sha256 dòng nguồn,
từ `PageGeometry` (dòng thô) và `PageExtraction` (dòng đã xác nhận). Đơn vị ledger (`nonws_nfc_codepoint`): số code
point không phải khoảng trắng của NFC(từ), cộng theo từ; dòng xác nhận: của `ExtractedLine.text`.

`source_count` là số đếm ĐỘC LẬP: basis `pdfium` lấy `PageGeometry.pdfium_nonws_total` (worker đếm trước khi lọc chữ
ẩn và dựng dòng); basis `confirmed_lines` cộng đơn vị của MỌI `ExtractedLine` của trang; không bao giờ đếm lại từ khối.
"""

from __future__ import annotations

import hashlib
import unicodedata
from collections.abc import Iterable, Sequence

from ..domain.blocks import (
    BLOCK_TYPE_ORDER,
    Block,
    BlockFlag,
    BlockType,
    ConfirmedLineRef,
    ExtractionLedger,
    LedgerBasis,
    LedgerBasisCount,
    LedgerDrop,
    LedgerFlagCount,
    LedgerTypeCount,
    OcrWordRef,
    SourceRef,
    WordRef,
)
from ..domain.documents import (
    DROPPABLE_ROLE_ORDER,
    BBox,
    DroppableRole,
    ExtractedLine,
    GeoLine,
    PageExtraction,
    PageGeometry,
    PageRole,
)

# Vị trí trong luồng PDFium: (chỉ số dòng luồng, chỉ số trong dòng). Dòng gõ thêm đứng sau mọi dòng thô.
FlowKey = tuple[int, int]

_DROP_REASONS: dict[str, DroppableRole] = {role: role for role in DROPPABLE_ROLE_ORDER}


class LedgerError(ValueError):
    """Tham chiếu nguồn không giải được (chỉ số lạ, trùng) hoặc thiếu số đếm độc lập: trang `LAYOUT_FAILED`."""


def nonws_units(text: str) -> int:
    """Số code point không phải khoảng trắng của NFC(`text`) (đơn vị ledger)."""
    return sum(1 for char in unicodedata.normalize("NFC", text) if not char.isspace())


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class SourceIndex:
    """Giải tham chiếu nguồn của một trang. Dựng lại được bất cứ lúc nào từ `(geometry, extraction)` (hàm thuần).

    Trang có dòng người dùng sửa (`edited_by_human`) hay gõ thêm (dòng không thuộc dòng thô nào) dùng basis
    `confirmed_lines`: dòng thô có ít nhất một mảnh đã sửa được tham chiếu bằng `confirmed_line` cho MỌI mảnh của nó.
    """

    def __init__(self, geometry: PageGeometry, extraction: PageExtraction) -> None:
        self.geometry = geometry
        self.extraction = extraction
        line_count = len(extraction.lines)
        self.owner: dict[int, tuple[int, int]] = {}
        for geo_idx, line in enumerate(geometry.lines):
            for position, line_index in enumerate(line.extracted_line_indices):
                if not 0 <= line_index < line_count or line_index in self.owner:
                    raise LedgerError("extracted_line_indices trỏ tới dòng không tồn tại hoặc trùng")
                self.owner[line_index] = (geo_idx, position)
        self.edited_geo_lines: frozenset[int] = frozenset(
            self.owner[line.line_index][0]
            for line in extraction.lines
            if line.edited_by_human and line.line_index in self.owner
        )
        self.typed_lines: tuple[int, ...] = tuple(i for i in range(line_count) if i not in self.owner)
        self.confirmed_basis = bool(self.edited_geo_lines or self.typed_lines)
        self._line_sha: dict[int, str] = {}

    # ----------------------------------------------------------------------- tra cứu theo tham chiếu

    def _geo_lines(self, ref: WordRef | OcrWordRef) -> list[GeoLine]:
        return self.geometry.lines if isinstance(ref, WordRef) else self.geometry.ocr_lines

    def _word_text(self, ref: WordRef | OcrWordRef) -> str:
        lines = self._geo_lines(ref)
        if not 0 <= ref.line_idx < len(lines) or not 0 <= ref.word_idx < len(lines[ref.line_idx].words):
            raise LedgerError("tham chiếu từ trỏ ra ngoài geometry")
        return lines[ref.line_idx].words[ref.word_idx].text

    def _confirmed(self, ref: ConfirmedLineRef) -> ExtractedLine:
        if not 0 <= ref.line_index < len(self.extraction.lines):
            raise LedgerError("tham chiếu dòng xác nhận trỏ ra ngoài trang")
        return self.extraction.lines[ref.line_index]

    def validate(self, ref: SourceRef) -> None:
        """Ném `LedgerError` khi tham chiếu không giải được hoặc sai loại so với basis của trang."""
        if isinstance(ref, ConfirmedLineRef):
            self._confirmed(ref)
            return
        self._word_text(ref)
        if isinstance(ref, WordRef) and ref.line_idx in self.edited_geo_lines:
            raise LedgerError("dòng thô đã sửa phải được tham chiếu bằng confirmed_line")

    def text(self, ref: SourceRef) -> str:
        """Chữ NFC của tham chiếu (từ: NFC của từ; dòng xác nhận: `ExtractedLine.text`, đã NFC)."""
        if isinstance(ref, ConfirmedLineRef):
            return self._confirmed(ref).text
        return unicodedata.normalize("NFC", self._word_text(ref))

    def units(self, ref: SourceRef) -> int:
        return nonws_units(self.text(ref))

    def bbox(self, ref: SourceRef) -> BBox | None:
        if isinstance(ref, ConfirmedLineRef):
            line = self._confirmed(ref)
            return line.bbox if ref.line_index in self.owner else None
        self._word_text(ref)
        return self._geo_lines(ref)[ref.line_idx].words[ref.word_idx].bbox

    def flow_key(self, ref: SourceRef) -> FlowKey:
        if isinstance(ref, ConfirmedLineRef):
            self._confirmed(ref)
            owner = self.owner.get(ref.line_index)
            return owner if owner is not None else (len(self.geometry.lines) + ref.line_index, 0)
        self._word_text(ref)
        return (ref.line_idx, ref.word_idx)

    def basis(self, ref: SourceRef) -> LedgerBasis:
        if isinstance(ref, OcrWordRef):
            return "ocr"
        if isinstance(ref, ConfirmedLineRef) or self.confirmed_basis:
            return "confirmed_lines"
        return "pdfium"

    def line_sha256(self, ref: SourceRef) -> str:
        """sha256 UTF-8 của nội dung dòng nguồn: dòng thô = các từ (NFC) nối bằng một dấu cách; dòng xác nhận =
        `ExtractedLine.text` (ADR-010 mục 3)."""
        if isinstance(ref, ConfirmedLineRef):
            return _sha256(self._confirmed(ref).text)
        self._word_text(ref)
        if isinstance(ref, OcrWordRef):
            words = self.geometry.ocr_lines[ref.line_idx].words
            return _sha256(" ".join(unicodedata.normalize("NFC", w.text) for w in words))
        cached = self._line_sha.get(ref.line_idx)
        if cached is None:
            words = self.geometry.lines[ref.line_idx].words
            cached = _sha256(" ".join(unicodedata.normalize("NFC", w.text) for w in words))
            self._line_sha[ref.line_idx] = cached
        return cached

    def line_flags(self, ref: SourceRef) -> frozenset[BlockFlag]:
        """Cờ khối suy từ cờ dòng nguồn (ADR-010 mục 6; spec §5.15)."""
        flags: set[BlockFlag] = set()
        if isinstance(ref, ConfirmedLineRef):
            line = self._confirmed(ref)
            flags.add("human_edited")
            line_flags: Iterable[str] = line.flags
        elif isinstance(ref, OcrWordRef):
            return frozenset()
        else:
            self._word_text(ref)
            geo = self.geometry.lines[ref.line_idx]
            line_flags = [*geo.flags, *self._extracted_flags(geo)]
        for flag in line_flags:
            if flag == "TEXT_OCCLUDED":
                flags.add("text_occluded")
            elif flag == "LOW_TEXT_CONTRAST":
                flags.add("low_text_contrast")
            elif flag == "FONT_ENCODING_SUSPECT":
                flags.add("font_encoding_suspect")
        return frozenset(flags)

    def _extracted_flags(self, geo: GeoLine) -> list[str]:
        return [flag for i in geo.extracted_line_indices for flag in self.extraction.lines[i].flags]

    def flagged_reason(self, ref: SourceRef) -> tuple[bool, bool]:
        """(bị che, tương phản thấp) của dòng nguồn — để đếm `ledger.flagged`."""
        flags = self.line_flags(ref)
        return "text_occluded" in flags, "low_text_contrast" in flags


def block_refs(block: Block) -> list[SourceRef]:
    """Mọi tham chiếu nguồn ledger của khối: `line_refs` ∪ `label_line_refs` (ô bảng là tập con của `line_refs`)."""
    return [*block.line_refs, *block.label_line_refs]


def compute_ledger(document_id: str, page_number: int, blocks: Sequence[Block], index: SourceIndex) -> ExtractionLedger:
    """Sổ bảo toàn của trang. Ném `LedgerError` khi tham chiếu không giải được, trùng, hoặc thiếu số đếm độc lập."""
    seen: set[SourceRef] = set()
    assigned: dict[LedgerBasis, int] = {}
    by_type: dict[BlockType, int] = {}
    occluded = low_contrast = 0
    for block in blocks:
        for ref in block_refs(block):
            if ref in seen:
                raise LedgerError("một tham chiếu nguồn thuộc hơn một khối")
            seen.add(ref)
            index.validate(ref)
            units = index.units(ref)
            basis = index.basis(ref)
            assigned[basis] = assigned.get(basis, 0) + units
            by_type[block.type] = by_type.get(block.type, 0) + units
            is_occluded, is_low = index.flagged_reason(ref)
            occluded += units if is_occluded else 0
            low_contrast += units if is_low else 0
    geometry = index.geometry
    bases: list[LedgerBasisCount] = []
    if index.confirmed_basis:
        source = sum(nonws_units(line.text) for line in index.extraction.lines)
        bases.append(
            LedgerBasisCount(
                ledger_basis="confirmed_lines", source_count=source, assigned_count=assigned.get("confirmed_lines", 0)
            )
        )
    else:
        if geometry.pdfium_nonws_total is None:
            raise LedgerError("trang lớp chữ thiếu pdfium_nonws_total")
        bases.append(
            LedgerBasisCount(
                ledger_basis="pdfium",
                source_count=geometry.pdfium_nonws_total,
                assigned_count=assigned.get("pdfium", 0),
                dropped=[LedgerDrop(reason="hidden", count=geometry.hidden_nonws_dropped)],
            )
        )
    if geometry.ocr_lines or "ocr" in assigned:
        if geometry.ocr_nonws_total is None:
            raise LedgerError("trang có từ OCR nhưng thiếu ocr_nonws_total")
        bases.append(
            LedgerBasisCount(
                ledger_basis="ocr", source_count=geometry.ocr_nonws_total, assigned_count=assigned.get("ocr", 0)
            )
        )
    flagged: list[LedgerFlagCount] = []
    if occluded:
        flagged.append(LedgerFlagCount(reason="occluded", count=occluded))
    if low_contrast:
        flagged.append(LedgerFlagCount(reason="low_contrast", count=low_contrast))
    return ExtractionLedger(
        document_id=document_id,
        page_number=page_number,
        bases=bases,
        by_type=[LedgerTypeCount(type=kind, count=by_type[kind]) for kind in BLOCK_TYPE_ORDER if by_type.get(kind)],
        flagged=flagged,
        balanced=all(basis.is_balanced for basis in bases),
    )


def with_page_drop_reason(ledger: ExtractionLedger, page_role: PageRole, *, dropped: bool = True) -> ExtractionLedger:
    """Ledger với `page_drop_reason` của trang (ADR-010 mục 6): = `page_role` khi trang bị bỏ mềm (`dropped`) với vai
    trò bỏ được; ngược lại null. Hàm thuần; X09 gọi lại sau MỌI lần đổi `page_role` hay `dropped` của trang."""
    reason = _DROP_REASONS.get(page_role) if dropped else None
    return ledger.model_copy(update={"page_drop_reason": reason})


def retype_by_type(ledger: ExtractionLedger, moves: Iterable[tuple[BlockType, BlockType, int]]) -> ExtractionLedger:
    """Ledger sau khi đổi loại khối: mỗi `(từ loại, sang loại, số đơn vị)` chuyển đơn vị giữa hai mục `by_type`."""
    counts: dict[BlockType, int] = {item.type: item.count for item in ledger.by_type}
    for source, target, units in moves:
        counts[source] = counts.get(source, 0) - units
        counts[target] = counts.get(target, 0) + units
    if any(value < 0 for value in counts.values()):
        raise LedgerError("by_type âm sau khi đổi loại khối")
    by_type = [LedgerTypeCount(type=kind, count=counts[kind]) for kind in BLOCK_TYPE_ORDER if counts.get(kind)]
    return ExtractionLedger.model_validate({**ledger.model_dump(), "by_type": [t.model_dump() for t in by_type]})
