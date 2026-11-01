"""Phần thuần của công cụ đo cấu trúc bóc tách (`scripts/eval_layout.py`): loại khối, hình dạng JSON dự đoán, bộ đếm.

Chỉ dùng thư viện chuẩn, không đọc đĩa ngoài `parse_predictions_file`, không in nội dung tài liệu.

Loại khối (BlockType, file quyết định 2026-11-01 mục 1): heading, paragraph, list_item, table, figure, formula, code,
caption, header_footer, page_number. Ground truth hiện có dùng tên cũ, ánh xạ bằng `GT_TYPE_MAP`; tên lạ ⇒ `None`
(báo `unmapped`, không đoán).

Hình dạng JSON dự đoán (đọc qua `--pred` của `eval_layout`; `--predictions` là bí danh):

    {"chapters": {"ch2": {"pages": [{"page": 1,
        "blocks": [{"id": "b1", "type": "heading", "text": "...", "reading_order": 0, "level": 1,
                    "flags": ["LOW_CONF"], "source": "text_layer", "cells": [["a", "b"]]}],
        "ledger": {"total_chars": 120, "assigned_chars": 110, "dropped_by_reason": {"template": 10}}}]}}}

Chỉ `type` và `text` bắt buộc. Mọi field khác tuỳ chọn; thiếu thì chỉ số tương ứng để trống, không lỗi:
`reading_order` (mặc định: vị trí), `id`, `flags` (danh sách lý do; rỗng = không cờ), `source` (text_layer | ocr | vlm),
`level` (>= 1, chỉ ở heading; thiếu ở heading ⇒ khối không tham gia F1 cây), `cells` (lưới ô của bảng, chỉ ở table),
`ledger` ở cấp trang (độ phủ trang = (assigned_chars + tổng dropped_by_reason) / total_chars; đạt khi bằng đúng 1).
"""

from __future__ import annotations

import json
import unicodedata
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

BLOCK_TYPES = (
    "heading",
    "paragraph",
    "list_item",
    "table",
    "figure",
    "formula",
    "code",
    "caption",
    "header_footer",
    "page_number",
)
SOURCES = frozenset({"text_layer", "ocr", "vlm"})
CHROME_TYPES = frozenset({"header_footer", "page_number"})
NON_TEXT_TYPES = frozenset({"figure", "table"})
GT_TYPE_MAP: dict[str, str] = {
    "title": "heading",
    "paragraph": "paragraph",
    "list": "list_item",
    "table": "table",
    "figure": "figure",
    "code": "code",
    "caption": "caption",
    "header": "header_footer",
    "page_number": "page_number",
}
GT_HEADING_LEVEL = 1
NOT_MEASURED = "chưa đo"


class PredictionFormatError(Exception):
    """Lỗi hình dạng file dự đoán (thông điệp không chứa nội dung tài liệu)."""


@dataclass(frozen=True)
class LBlock:
    id: str
    page: int
    type: str
    text: str
    order: int
    level: int | None = None
    flags: tuple[str, ...] = ()
    source: str | None = None
    cells: tuple[tuple[str, ...], ...] | None = None


@dataclass(frozen=True)
class LedgerPage:
    page: int
    total_chars: int
    assigned_chars: int
    dropped_by_reason: tuple[tuple[str, int], ...] = ()

    @property
    def accounted(self) -> int:
        return self.assigned_chars + sum(n for _, n in self.dropped_by_reason)


@dataclass(frozen=True)
class NeutralDoc:
    blocks: tuple[LBlock, ...]
    ledger: tuple[LedgerPage, ...] | None = None
    pages: frozenset[int] = frozenset()


def normalize(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", text).split())


def levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def map_gt_type(kind: str) -> str | None:
    return GT_TYPE_MAP.get(kind)


def _int(value: object, where: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise PredictionFormatError(f"{where}: cần số nguyên")
    return value


def _parse_cells(raw: object, at: str) -> tuple[tuple[str, ...], ...]:
    if not isinstance(raw, list) or not all(
        isinstance(row, list) and all(isinstance(c, str) for c in row) for row in raw
    ):
        raise PredictionFormatError(f"{at}: `cells` phải là lưới chuỗi")
    return tuple(tuple(row) for row in raw)


def _parse_block(item: object, index: int, page: int, where: str) -> LBlock:
    at = f"{where} trang {page} khối {index}"
    if not isinstance(item, dict):
        raise PredictionFormatError(f"{at}: không phải đối tượng")
    kind, text, ident = item.get("type"), item.get("text"), item.get("id", f"p{page}.b{index}")
    if not isinstance(kind, str) or kind not in BLOCK_TYPES:
        raise PredictionFormatError(f"{at}: loại khối lạ")
    if not isinstance(text, str) or not isinstance(ident, str):
        raise PredictionFormatError(f"{at}: `text` và `id` phải là chuỗi")
    order = _int(item.get("reading_order", item.get("order", index)), f"{at} `reading_order`")
    level: int | None = None
    if item.get("level") is not None:
        if kind != "heading":
            raise PredictionFormatError(f"{at}: `level` chỉ có ở heading")
        level = _int(item["level"], f"{at} `level`")
        if level < 1:
            raise PredictionFormatError(f"{at}: `level` phải >= 1")
    raw_flags = item.get("flags", [])
    if not isinstance(raw_flags, list) or not all(isinstance(f, str) for f in raw_flags):
        raise PredictionFormatError(f"{at}: `flags` phải là danh sách chuỗi")
    source = item.get("source")
    if source is not None and source not in SOURCES:
        raise PredictionFormatError(f"{at}: `source` lạ")
    cells: tuple[tuple[str, ...], ...] | None = None
    if item.get("cells") is not None:
        if kind != "table":
            raise PredictionFormatError(f"{at}: `cells` chỉ có ở table")
        cells = _parse_cells(item["cells"], at)
    return LBlock(ident, page, kind, text, order, level, tuple(raw_flags), source, cells)


def _parse_ledger(raw: object, page: int, where: str) -> LedgerPage:
    at = f"{where} trang {page} ledger"
    if not isinstance(raw, dict):
        raise PredictionFormatError(f"{at}: phải là đối tượng")
    reasons = raw.get("dropped_by_reason", {})
    if not isinstance(reasons, dict):
        raise PredictionFormatError(f"{at}: `dropped_by_reason` phải là đối tượng")
    dropped = tuple(sorted((str(k), _int(v, f"{at} `{k}`")) for k, v in reasons.items()))
    entry = LedgerPage(
        page,
        _int(raw.get("total_chars"), f"{at} `total_chars`"),
        _int(raw.get("assigned_chars"), f"{at} `assigned_chars`"),
        dropped,
    )
    if min(entry.total_chars, entry.assigned_chars, *(n for _, n in dropped)) < 0:
        raise PredictionFormatError(f"{at}: số đếm không được âm")
    return entry


def parse_predictions(raw: object, where: str) -> NeutralDoc:
    """Đọc phần `{"pages": [...]}` của một chương (xem docstring module)."""
    if not isinstance(raw, dict) or not isinstance(raw.get("pages"), list):
        raise PredictionFormatError(f"{where}: cần đối tượng có `pages` là danh sách")
    blocks: list[LBlock] = []
    ledger: list[LedgerPage] = []
    seen: set[int] = set()
    for page_item in raw["pages"]:
        if not isinstance(page_item, dict):
            raise PredictionFormatError(f"{where}: trang không phải đối tượng")
        page = _int(page_item.get("page"), f"{where} `page`")
        if page in seen:
            raise PredictionFormatError(f"{where}: trang {page} lặp")
        seen.add(page)
        items = page_item.get("blocks", [])
        if not isinstance(items, list):
            raise PredictionFormatError(f"{where} trang {page}: `blocks` phải là danh sách")
        blocks += [_parse_block(item, i, page, where) for i, item in enumerate(items)]
        if page_item.get("ledger") is not None:
            ledger.append(_parse_ledger(page_item["ledger"], page, where))
    return NeutralDoc(tuple(blocks), tuple(ledger) if ledger else None, frozenset(seen))


def parse_predictions_file(path: Path) -> dict[str, NeutralDoc]:
    data = json.loads(path.read_text(encoding="utf-8"))
    chapters = data.get("chapters") if isinstance(data, dict) else None
    if not isinstance(chapters, dict):
        raise PredictionFormatError("file dự đoán phải có `chapters` là đối tượng")
    return {str(name): parse_predictions(body, f"dự đoán {name}") for name, body in chapters.items()}


@dataclass
class LayoutTally:
    """Bộ cộng dồn số đếm của các chỉ số mới; gộp bằng `merge`."""

    pages: int = 0
    missing_pages: int = 0
    gt_blocks: int = 0
    pred_blocks: int = 0
    matched: int = 0
    type_tp: Counter[str] = field(default_factory=Counter)
    type_gt: Counter[str] = field(default_factory=Counter)
    type_pred: Counter[str] = field(default_factory=Counter)
    tree_tp: int = 0
    tree_gt: int = 0
    tree_pred: int = 0
    code_tp: int = 0
    code_gt: int = 0
    cer_edits: int = 0
    cer_chars: int = 0
    text_pred: int = 0
    silent_wrong: int = 0
    ledger_pages: int = 0
    ledger_exact: int = 0
    ledger_total: int = 0
    ledger_covered: int = 0
    taus: list[float] = field(default_factory=list)
    cells_gt: int = 0
    cells_tp: int = 0

    def merge(self, other: LayoutTally) -> None:
        for name in (
            "pages",
            "missing_pages",
            "gt_blocks",
            "pred_blocks",
            "matched",
            "tree_tp",
            "tree_gt",
            "tree_pred",
            "code_tp",
            "code_gt",
            "cer_edits",
            "cer_chars",
            "text_pred",
            "silent_wrong",
            "ledger_pages",
            "ledger_exact",
            "ledger_total",
            "ledger_covered",
            "cells_gt",
            "cells_tp",
        ):
            setattr(self, name, getattr(self, name) + getattr(other, name))
        self.type_tp.update(other.type_tp)
        self.type_gt.update(other.type_gt)
        self.type_pred.update(other.type_pred)
        self.taus += other.taus


def _ordered_text(blocks: Sequence[LBlock]) -> str:
    return normalize(" ".join(b.text for b in sorted(blocks, key=lambda b: b.order)))


def _tree(blocks: Sequence[LBlock]) -> Counter[tuple[int, str]]:
    return Counter((b.level, normalize(b.text)) for b in blocks if b.type == "heading" and b.level is not None)


def _cells(block: LBlock) -> list[str]:
    return [normalize(c) for row in block.cells or () for c in row]


def _code_lines(blocks: Sequence[LBlock]) -> Counter[str]:
    lines: Counter[str] = Counter()
    for b in blocks:
        if b.type == "code":
            for line in unicodedata.normalize("NFC", b.text).replace("\r\n", "\n").split("\n"):
                if line.strip():
                    lines[line.rstrip()] += 1
    return lines


def page_tally(
    gt: Sequence[LBlock],
    pred: Sequence[LBlock] | None,
    pairs: Sequence[tuple[int, int]],
    ledger: LedgerPage | None = None,
    *,
    has_ledger: bool = False,
) -> LayoutTally:
    """Đếm một trang. `pairs` = khớp ranh giới (chỉ số ground truth, chỉ số dự đoán) do bộ gọi tính; `pred is None` = thiếu trang.

    Ledger: `has_ledger` và `ledger is None` ⇒ trang thiếu dòng ledger, tính là trang không đạt.
    """
    t = LayoutTally(pages=1, gt_blocks=len(gt))
    t.type_gt.update(b.type for b in gt)
    gt_text = _ordered_text(gt)
    t.cer_chars = len(gt_text)
    gt_tree = _tree(gt)
    t.tree_gt = sum(gt_tree.values())
    gt_code = _code_lines(gt)
    t.code_gt = sum(gt_code.values())
    if has_ledger:
        t.ledger_pages = 1
        if ledger is not None:
            t.ledger_total = ledger.total_chars
            t.ledger_covered = min(ledger.total_chars, ledger.accounted)
            t.ledger_exact = int(ledger.accounted == ledger.total_chars)
    if pred is None:
        t.missing_pages = 1
        t.cer_edits = len(gt_text)
        return t
    t.pred_blocks = len(pred)
    t.matched = len(pairs)
    t.type_pred.update(b.type for b in pred)
    for gi, pi in pairs:
        if gt[gi].type == pred[pi].type:
            t.type_tp[gt[gi].type] += 1
    t.cer_edits = levenshtein(_ordered_text(pred), gt_text)
    pred_tree = _tree(pred)
    t.tree_pred = sum(pred_tree.values())
    t.tree_tp = sum((gt_tree & pred_tree).values())
    t.code_tp = sum((gt_code & _code_lines(pred)).values())
    for gi, pi in pairs:
        g, p = gt[gi], pred[pi]
        if g.type == "table" and p.type == "table" and g.cells is not None:
            expected = _cells(g)
            t.cells_gt += len(expected)
            t.cells_tp += sum((Counter(expected) & Counter(_cells(p))).values())
    matched_pred = {pi for _, pi in pairs}
    for index, block in enumerate(pred):
        if block.type in NON_TEXT_TYPES:
            continue
        t.text_pred += 1
        if index not in matched_pred and not block.flags:
            t.silent_wrong += 1
    return t


def _ratio(num: float, den: float) -> float | None:
    return num / den if den > 0 else None


def _f1(tp: int, pred: int, gt: int) -> float | None:
    return _ratio(2 * tp, pred + gt)


def summarize_layout(t: LayoutTally) -> dict[str, float | int | str | None]:
    gt_types = [k for k, v in t.type_gt.items() if v > 0]
    macro = [_f1(t.type_tp[k], t.type_pred[k], t.type_gt[k]) for k in sorted(gt_types)]
    macro_values = [m for m in macro if m is not None]
    return {
        "pages": t.pages,
        "missing_pages": t.missing_pages,
        "gt_blocks": t.gt_blocks,
        "pred_blocks": t.pred_blocks,
        "matched": t.matched,
        "type_f1": _f1(sum(t.type_tp.values()), t.pred_blocks, t.gt_blocks),
        "type_f1_macro": sum(macro_values) / len(macro_values) if macro_values else None,
        "boundary_f1": _f1(t.matched, t.pred_blocks, t.gt_blocks),
        "kendall_tau_mean": _ratio(sum(t.taus), len(t.taus)),
        "kendall_tau_pages": len(t.taus),
        "tree_f1": _f1(t.tree_tp, t.tree_pred, t.tree_gt),
        "table_cell_accuracy": _ratio(t.cells_tp, t.cells_gt) if t.cells_gt else NOT_MEASURED,
        "code_line_match": _ratio(t.code_tp, t.code_gt),
        "page_cer": _ratio(t.cer_edits, t.cer_chars),
        "silent_wrong": t.silent_wrong,
        "text_blocks_pred": t.text_pred,
        "ledger_coverage": _ratio(t.ledger_covered, t.ledger_total) if t.ledger_pages else None,
        "ledger_pages_exact": t.ledger_exact if t.ledger_pages else None,
        "ledger_pages": t.ledger_pages if t.ledger_pages else None,
    }
