"""Opt-in: xuất khối của bộ dựng (X05) ra file dự đoán `--pred` của `scripts/eval_layout.py` (`layout_metrics.py`).

KHÔNG chạy trong pytest, CI hay pre-commit. Không gọi mạng (offline: không có cột AI). Không in nội dung tài liệu:
stdout chỉ có số. File ra PHẢI nằm ngoài cây repo.

Chạy trong `holocron/backend`:

    python -m scripts.export_blocks <eval_data> <file dự đoán JSON, ngoài repo> [--chapter ch2 ...] [--no-ocr]
    python -m scripts.eval_layout <eval_data> <thư mục kết quả, ngoài repo> --pred <file dự đoán JSON>

Mỗi chương `chN` đọc các PDF có `.json` cùng tên trong `<eval_data>/arm_chN/` (như đường nền của `eval_layout`) bằng
`WorkerPdfReader` với `geometry_sink`, dựng khối từng trang (`block_pipeline.build_page_blocks`) rồi số trang cấp tài
liệu (`PageNumberStage`). Trang có khối: mỗi khối một mục (`figure`/`table`: `text` = các nhãn nối bằng xuống dòng);
`ledger` = `{total_chars: Σ source_count, assigned_chars: Σ assigned_count, dropped_by_reason: {"hidden": n}}` cộng trên
mọi basis (`flagged` không tính vào đâu). Trang `LAYOUT_FAILED` (hay không có geometry, ví dụ trang OCR): đường dòng —
mỗi `ExtractedLine` một khối `paragraph` (`header_footer` nếu là dòng lặp) kèm cờ `LAYOUT_FAILED`, ledger nếu có.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from holocron_core.domain.blocks import Block, ExtractionLedger
from holocron_core.domain.documents import PageExtraction, PageGeometry
from holocron_core.infrastructure.document_readers.config import PdfReaderConfig, load_ocr_settings
from holocron_core.infrastructure.document_readers.pdf_reader import WorkerPdfReader
from holocron_core.infrastructure.lang_patterns import load_lang_patterns
from holocron_core.services.block_pipeline import PageBlocks, build_page_blocks
from holocron_core.services.page_numbers import PageNumberStage
from scripts.eval_layout import EvalLayoutError, match_pdfs, require_outside_repo


def _block_entry(block: Block) -> dict[str, Any]:
    text = block.text if block.text is not None else "\n".join(block.labels)
    entry: dict[str, Any] = {
        "id": block.block_id,
        "type": block.type,
        "text": text,
        "reading_order": block.reading_order,
        "flags": list(block.flags),
        "source": block.source,
    }
    if block.level is not None:
        entry["level"] = block.level
    return entry


def _ledger_entry(ledger: ExtractionLedger) -> dict[str, Any]:
    hidden = sum(drop.count for basis in ledger.bases for drop in basis.dropped if drop.reason == "hidden")
    return {
        "total_chars": sum(basis.source_count for basis in ledger.bases),
        "assigned_chars": sum(basis.assigned_count for basis in ledger.bases),
        "dropped_by_reason": {"hidden": hidden},
    }


def _line_entries(extraction: PageExtraction) -> list[dict[str, Any]]:
    return [
        {
            "id": f"p{extraction.page_number}.l{line.line_index}",
            "type": "header_footer" if line.is_header_footer else "paragraph",
            "text": line.text,
            "reading_order": line.line_index,
            "flags": ["LAYOUT_FAILED", *line.flags],
            "source": "text_layer" if line.engine == "text_layer" else "ocr",
        }
        for line in extraction.lines
    ]


def page_entry(extraction: PageExtraction, blocks: PageBlocks) -> dict[str, Any]:
    """Một trang theo hình dạng `--pred` của `layout_metrics.py`."""
    entry: dict[str, Any] = {"page": extraction.page_number}
    if blocks.layout_failed:
        entry["blocks"] = _line_entries(extraction)
    else:
        entry["blocks"] = [_block_entry(block) for block in sorted(blocks.blocks, key=lambda b: b.reading_order)]
    if blocks.ledger is not None:
        entry["ledger"] = _ledger_entry(blocks.ledger)
    return entry


def export_pdf(path: Path, reader: WorkerPdfReader, page_offset: int = 0) -> tuple[list[dict[str, Any]], int]:
    """Các trang của một PDF (đánh số tiếp sau `page_offset`) và số trang `LAYOUT_FAILED`."""
    document_id = "doc_" + hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    lang = load_lang_patterns()
    geometries: dict[int, PageGeometry | None] = {}

    def sink(page_number: int, geometry: PageGeometry | None) -> None:
        geometries[page_number] = geometry

    extractions: list[PageExtraction] = []
    built: list[PageBlocks] = []
    for page in reader.read_pages(path, document_id, geometry_sink=sink):
        extractions.append(page)
        built.append(
            build_page_blocks(
                document_id=document_id,
                page_number=page.page_number,
                geometry=geometries.get(page.page_number),
                extraction=page,
                lang=lang,
            )
        )
    built = PageNumberStage(lang).run(built)
    pages: list[dict[str, Any]] = []
    for extraction, blocks in zip(extractions, built, strict=True):
        entry = page_entry(extraction, blocks)
        entry["page"] = page_offset + extraction.page_number
        pages.append(entry)
    return pages, sum(1 for blocks in built if blocks.layout_failed)


def export(eval_data: Path, chapters: Sequence[str], *, use_ocr: bool) -> tuple[dict[str, Any], dict[str, int]]:
    reader = WorkerPdfReader(PdfReaderConfig(), load_ocr_settings() if use_ocr else None)
    out: dict[str, Any] = {"chapters": {}}
    counts: dict[str, int] = {"pages": 0, "layout_failed": 0}
    for chapter in chapters:
        matched, _ = match_pdfs(eval_data / f"arm_{chapter}")
        pages: list[dict[str, Any]] = []
        for pdf in matched:
            exported, failed = export_pdf(pdf, reader, len(pages))
            pages += exported
            counts["layout_failed"] += failed
        counts["pages"] += len(pages)
        out["chapters"][chapter] = {"pages": pages}
    return out, counts


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Xuất khối của bộ dựng ra file dự đoán của eval_layout.")
    parser.add_argument("eval_data", type=Path, help="thư mục eval_data (chứa arm_chN/)")
    parser.add_argument("output", type=Path, help="file dự đoán JSON (ngoài repo)")
    parser.add_argument("--chapter", action="append", help="chương cần xuất (lặp được; mặc định mọi arm_ch*)")
    parser.add_argument("--no-ocr", action="store_true", help="tắt OCR")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    try:
        require_outside_repo(args.output, "<file dự đoán>")
        chapters = args.chapter or sorted(p.name[4:] for p in args.eval_data.glob("arm_ch*") if p.is_dir())
        data, counts = export(args.eval_data, chapters, use_ocr=not args.no_ocr)
    except (EvalLayoutError, OSError) as exc:
        print(f"lỗi: {exc}", file=sys.stderr)
        return 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    print(f"đã ghi {counts['pages']} trang ({counts['layout_failed']} trang LAYOUT_FAILED): {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
