"""Đo luật chọn tầng của bộ nhập PDF trên tài liệu thật, opt-in (ADR-008 mục 6; sửa đổi 2026-10-29).

Chạy NGOÀI pytest, không chạy trong CI hay pre-commit; không cần OCR, không gọi mạng.

    cd holocron/backend
    python -m scripts.eval_pdf_tiers <thư mục kết quả> <thư mục PDF> [<thư mục PDF> ...]

Với mỗi trang của mỗi PDF trong các thư mục: quyết định theo công thức CŨ (`text_score = min(1, non_ws / 200) ×
clean × valid`, OCR khi < 0.85; hai hằng ghi cứng ở đây chỉ để so) và theo luật MỚI của app
(`text_layer.ocr_reason` với `PdfReaderConfig()` mặc định). Báo cáo theo từng thư mục: số trang theo tầng cũ/mới,
số trang đổi tầng, danh sách trang còn đi OCR kèm lý do và các số `clean`, `valid`, `non_ws`, `image_area_ratio`;
riêng bảng "lớp chữ ít + ảnh phủ lớn" (lớp chữ sạch, có chữ, ít hơn `--sparse-report-chars` ký tự, ảnh phủ ít nhất
`--sparse-report-image`) kèm quyết định và độ phủ ảnh.

Kết quả chỉ gồm số liệu (không chép văn bản tài liệu), ghi ra `results.json` và `results.md`; script từ chối ghi
vào trong repo. Script đọc PDF ngay trong tiến trình này (không qua worker có giới hạn), nên chỉ dùng cho tài liệu
đánh giá của chính người chạy.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path

from holocron_core.infrastructure.document_readers.config import PdfReaderConfig
from holocron_core.infrastructure.document_readers.pdfium_adapter import PdfiumBackend
from holocron_core.infrastructure.document_readers.protocol import RawPdfPage
from holocron_core.infrastructure.document_readers.text_layer import (
    clean_char_ratio,
    non_whitespace_count,
    ocr_reason,
    page_text,
    valid_word_ratio,
)

REPO_ROOT = Path(__file__).resolve().parents[3]

# Công thức cũ (trước 2026-10-29), chỉ để so sánh.
OLD_TEXT_FULL_CHARS = 200
OLD_TEXT_LAYER_MIN = 0.85


@dataclass
class PageRow:
    file: str
    page_number: int
    non_ws: int
    clean: float
    valid: float
    image_area_ratio: float
    old_score: float
    old_tier: str
    new_tier: str
    new_reason: str | None


@dataclass
class DirReport:
    pdf_dir: str
    files: list[str] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)
    pages: list[PageRow] = field(default_factory=list)


def old_text_score(text: str) -> float:
    """`text_score` theo công thức cũ: min(1, non_ws / 200) × clean × valid, trên văn bản NFC."""
    normalized = unicodedata.normalize("NFC", text)
    sufficiency = min(1.0, non_whitespace_count(normalized) / OLD_TEXT_FULL_CHARS)
    return sufficiency * clean_char_ratio(normalized) * valid_word_ratio(normalized)


def page_row(file_name: str, raw: RawPdfPage, config: PdfReaderConfig) -> PageRow:
    text = unicodedata.normalize("NFC", page_text(raw.lines))
    old_score = old_text_score(text)
    reason = ocr_reason(raw, config)
    return PageRow(
        file=file_name,
        page_number=raw.page_index + 1,
        non_ws=non_whitespace_count(text),
        clean=clean_char_ratio(text),
        valid=valid_word_ratio(text),
        image_area_ratio=raw.image_area_ratio,
        old_score=old_score,
        old_tier="ocr" if old_score < OLD_TEXT_LAYER_MIN else "text_layer",
        new_tier="text_layer" if reason is None else "ocr",
        new_reason=reason,
    )


def is_sparse_on_image(row: PageRow, config: PdfReaderConfig, max_chars: int, min_image: float) -> bool:
    """Trang cho bảng riêng: lớp chữ sạch, có chữ nhưng ít, ảnh phủ lớn."""
    clean_layer = row.clean * row.valid >= config.text_layer_min
    return clean_layer and 0 < row.non_ws < max_chars and row.image_area_ratio >= min_image


def summary(report: DirReport) -> dict[str, object]:
    pages = report.pages
    reasons: dict[str, int] = {}
    for row in pages:
        if row.new_reason is not None:
            reasons[row.new_reason] = reasons.get(row.new_reason, 0) + 1
    return {
        "pages": len(pages),
        "old_ocr": sum(1 for r in pages if r.old_tier == "ocr"),
        "old_text_layer": sum(1 for r in pages if r.old_tier == "text_layer"),
        "new_ocr": sum(1 for r in pages if r.new_tier == "ocr"),
        "new_text_layer": sum(1 for r in pages if r.new_tier == "text_layer"),
        "changed": sum(1 for r in pages if r.old_tier != r.new_tier),
        "ocr_to_text_layer": sum(1 for r in pages if (r.old_tier, r.new_tier) == ("ocr", "text_layer")),
        "text_layer_to_ocr": sum(1 for r in pages if (r.old_tier, r.new_tier) == ("text_layer", "ocr")),
        "new_ocr_reasons": dict(sorted(reasons.items())),
    }


def _row_cells(row: PageRow) -> str:
    return (
        f"{row.non_ws} | {row.clean:.3f} | {row.valid:.3f} | {row.image_area_ratio:.3f} | {row.old_score:.3f} | "
        f"{row.old_tier} | {row.new_tier}"
    )


def markdown(reports: list[DirReport], config: PdfReaderConfig, max_chars: int, min_image: float) -> str:
    lines = [
        "# Luật chọn tầng: công thức cũ và luật mới",
        "",
        f"- Cũ: `min(1, non_ws/{OLD_TEXT_FULL_CHARS}) × clean × valid < {OLD_TEXT_LAYER_MIN}` ⇒ OCR.",
        (
            f"- Mới: `non_ws = 0` ⇒ OCR; `clean × valid < {config.text_layer_min}` ⇒ OCR; "
            f"`non_ws < {config.text_sparse_chars}` và `image_area_ratio ≥ {config.text_sparse_image_min}` ⇒ OCR; "
            "còn lại giữ lớp chữ."
        ),
        "",
        "## Tổng",
        "",
        "| Thư mục | Trang | OCR cũ | Lớp chữ cũ | OCR mới | Lớp chữ mới | Đổi tầng | OCR→lớp chữ | Lớp chữ→OCR |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for report in reports:
        s = summary(report)
        lines.append(
            f"| {Path(report.pdf_dir).name} | {s['pages']} | {s['old_ocr']} | {s['old_text_layer']} | {s['new_ocr']} | "
            f"{s['new_text_layer']} | {s['changed']} | {s['ocr_to_text_layer']} | {s['text_layer_to_ocr']} |"
        )
    header = "| Trang | non_ws | clean | valid | image_area_ratio | điểm cũ | tầng cũ | tầng mới |"
    for report in reports:
        name = Path(report.pdf_dir).name
        still_ocr = [r for r in report.pages if r.new_tier == "ocr"]
        lines += ["", f"## {name}: trang còn đi OCR ({len(still_ocr)})", ""]
        lines += [header.replace("| tầng mới |", "| tầng mới | lý do |"), "|---|---|---|---|---|---|---|---|---|"]
        lines += [f"| {r.page_number} | {_row_cells(r)} | {r.new_reason} |" for r in still_ocr]
        sparse = [r for r in report.pages if is_sparse_on_image(r, config, max_chars, min_image)]
        lines += [
            "",
            f"## {name}: lớp chữ ít (0 < non_ws < {max_chars}) + ảnh phủ ≥ {min_image} ({len(sparse)})",
            "",
            header,
            "|---|---|---|---|---|---|---|---|",
        ]
        lines += [f"| {r.page_number} | {_row_cells(r)} |" for r in sparse]
        if report.skipped:
            lines += ["", f"## {name}: file bỏ qua", ""]
            lines += [f"- {item['file']}: {item['reason']}" for item in report.skipped]
    return "\n".join(lines) + "\n"


def evaluate_dir(backend: PdfiumBackend, pdf_dir: Path, config: PdfReaderConfig) -> DirReport:
    report = DirReport(pdf_dir=str(pdf_dir))
    for path in sorted(pdf_dir.glob("*.pdf")):
        document = backend.open(path.read_bytes())
        if isinstance(document, str):
            report.skipped.append({"file": path.name, "reason": document})
            continue
        report.files.append(path.name)
        with document:
            for index in range(document.page_count()):
                report.pages.append(page_row(path.name, document.read_page(index), config))
    return report


def main(argv: list[str] | None = None) -> int:
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("pdf_dirs", type=Path, nargs="+")
    parser.add_argument("--sparse-report-chars", type=int, default=120, help="trần non_ws của bảng trang ít chữ")
    parser.add_argument("--sparse-report-image", type=float, default=0.30, help="độ phủ ảnh tối thiểu của bảng đó")
    args = parser.parse_args(argv)

    out_dir = args.out_dir.resolve()
    if out_dir.is_relative_to(REPO_ROOT):
        print(f"Từ chối ghi kết quả vào trong repo: {out_dir}", file=sys.stderr)
        return 2
    config = PdfReaderConfig()
    backend = PdfiumBackend.load()
    reports = [evaluate_dir(backend, pdf_dir, config) for pdf_dir in args.pdf_dirs]
    text = markdown(reports, config, args.sparse_report_chars, args.sparse_report_image)
    payload = {
        "old_formula": {"text_full_chars": OLD_TEXT_FULL_CHARS, "text_layer_min": OLD_TEXT_LAYER_MIN},
        "new_config": {
            "text_layer_min": config.text_layer_min,
            "text_sparse_chars": config.text_sparse_chars,
            "text_sparse_image_min": config.text_sparse_image_min,
        },
        "dirs": [{"summary": summary(report), **asdict(report)} for report in reports],
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "results.md").write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
