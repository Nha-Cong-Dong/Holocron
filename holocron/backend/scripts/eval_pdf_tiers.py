"""Đo luật chọn tầng của bộ nhập PDF trên tài liệu thật, opt-in (ADR-008 mục 6; sửa đổi 2026-10-29).

Chạy NGOÀI pytest, không chạy trong CI hay pre-commit; không cần OCR, không gọi mạng.

    cd holocron/backend
    python -m scripts.eval_pdf_tiers <thư mục kết quả> <thư mục PDF> [<thư mục PDF> ...]

Với mỗi trang của mỗi PDF trong các thư mục: quyết định theo công thức CŨ (`text_score = min(1, non_ws / 200) ×
clean × valid`, OCR khi < 0.85; hai hằng ghi cứng ở đây chỉ để so) và theo luật MỚI của app
(`text_layer.ocr_reason` với `PdfReaderConfig()` mặc định). Báo cáo theo từng thư mục: số trang theo tầng cũ/mới,
số trang đổi tầng, danh sách trang còn đi OCR kèm lý do và các số `clean`, `valid`, `non_ws`, `image_area_ratio`;
riêng bảng "lớp chữ ít + ảnh phủ lớn" (lớp chữ sạch, có chữ, ít hơn `--sparse-report-chars` ký tự, ảnh phủ ít nhất
`--sparse-report-image`) kèm quyết định và độ phủ ảnh, và bảng "trang giữ lớp chữ có ảnh phủ ít nhất
`--figure-report-image`" kèm `review_reasons`.

Mỗi trang ghi `review_reasons`, `status` và cờ `has_figure`, `has_table`, `has_formula` theo đúng đường của app khi
không có engine OCR: `tiers.assemble_text_layer_page` (cờ `page_flags`, chọn tầng `select_tier` với
`layout_available=False` như app mặc định). Trang đi OCR thì thành `failed` (`OCR_ENGINE_MISSING`), `review_reasons`
rỗng: lý do xem lại của trang đó do tầng 2 quyết, script không gọi OCR.

Ảnh template (sửa đổi 2026-10-30): mỗi PDF được quét ảnh template bằng đúng hàm của worker
(`template_images.scan_template_images`) rồi đọc từng trang với tập ảnh template đó, nên luật chọn tầng và cờ dùng độ
phủ ảnh KHÔNG phải template như app. Mỗi dòng ghi thêm `content_image_area_ratio`, trạng thái quét và số ảnh
template của tài liệu; báo cáo thêm (ở cuối, không đổi các bảng cũ) bảng ảnh template theo file và bảng "trang giữ lớp
chữ có ảnh không phải template phủ ít nhất `--figure-report-image`".

Kết quả chỉ gồm số liệu (không chép văn bản tài liệu), ghi ra `results.json` và `results.md`; script từ chối ghi
vào trong repo. Script đọc PDF ngay trong tiến trình này (không qua worker có giới hạn), nên chỉ dùng cho tài liệu
đánh giá của chính người chạy; vẫn áp trần kích thước và số trang của app (`max_bytes`, `max_pages`), và PDF hỏng chỉ
làm bỏ qua file đó (ghi vào danh sách file bỏ qua). Cột "cũ" dùng `clean`, `valid` và `image_area_ratio` của code
hiện tại (heuristic TCVN3/VNI và diện tích hợp của ảnh, sửa 2026-10-29), chỉ thay luật chọn tầng.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import unicodedata
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

from holocron_core.domain.documents import PageExtraction
from holocron_core.infrastructure.document_readers.config import PdfReaderConfig
from holocron_core.infrastructure.document_readers.pdfium_adapter import PdfiumBackend
from holocron_core.infrastructure.document_readers.protocol import RawPdfPage, TemplateScan
from holocron_core.infrastructure.document_readers.template_images import TemplateFilter, scan_template_images
from holocron_core.infrastructure.document_readers.text_layer import (
    clean_char_ratio,
    content_image_area,
    non_whitespace_count,
    ocr_reason,
    page_text,
    valid_word_ratio,
)
from holocron_core.infrastructure.document_readers.tiers import assemble_text_layer_page

REPO_ROOT = Path(__file__).resolve().parents[3]

# Công thức cũ (trước 2026-10-29), chỉ để so sánh.
OLD_TEXT_FULL_CHARS = 200
OLD_TEXT_LAYER_MIN = 0.85

# `document_id` giả để dựng `PageExtraction` bằng hàm của app; không ghi ra kết quả.
EVAL_DOCUMENT_ID = "doc_0000000000000000"


@dataclass
class PageRow:
    file: str
    page_number: int
    non_ws: int
    clean: float
    valid: float
    image_area_ratio: float
    path_area_ratio: float
    old_score: float
    old_tier: str
    new_tier: str
    new_reason: str | None
    status: str
    review_reasons: list[str]
    has_figure: bool
    has_table: bool
    has_formula: bool
    # Ảnh template (sửa đổi 2026-10-30): độ phủ ảnh không phải template của trang, trạng thái quét và số ảnh
    # template của cả tài liệu (giống nhau ở mọi dòng của cùng file).
    content_image_area_ratio: float = 0.0
    template_scan: str = "skipped"
    template_images: int = 0


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


def app_page(raw: RawPdfPage, config: PdfReaderConfig) -> PageExtraction:
    """Trang như app dựng khi không có engine OCR (`assemble_text_layer_page`, tầng 3 chưa có)."""
    return assemble_text_layer_page(raw.model_copy(update={"ocr": None}), EVAL_DOCUMENT_ID, config)


def page_row(file_name: str, raw: RawPdfPage, config: PdfReaderConfig) -> PageRow:
    """Dòng số liệu của một trang (trạng thái quét ảnh template ghi sau bằng `with_template_scan`)."""
    text = unicodedata.normalize("NFC", page_text(raw.lines))
    old_score = old_text_score(text)
    reason = ocr_reason(raw, config)
    page = app_page(raw, config)
    return PageRow(
        file=file_name,
        page_number=raw.page_index + 1,
        non_ws=non_whitespace_count(text),
        clean=clean_char_ratio(text),
        valid=valid_word_ratio(text),
        image_area_ratio=raw.image_area_ratio,
        path_area_ratio=raw.path_area_ratio,
        old_score=old_score,
        old_tier="ocr" if old_score < OLD_TEXT_LAYER_MIN else "text_layer",
        new_tier="text_layer" if reason is None else "ocr",
        new_reason=reason,
        status=page.status,
        review_reasons=list(page.review_reasons),
        has_figure=page.has_figure,
        has_table=page.has_table,
        has_formula=page.has_formula,
        content_image_area_ratio=content_image_area(raw),
    )


def with_template_scan(row: PageRow, scan: TemplateScan) -> PageRow:
    """Ghi trạng thái quét và số ảnh template của tài liệu vào dòng của một trang."""
    return replace(row, template_scan=scan.status, template_images=len(scan.keys))


def is_sparse_on_image(row: PageRow, config: PdfReaderConfig, max_chars: int, min_image: float) -> bool:
    """Trang cho bảng riêng: lớp chữ sạch, có chữ nhưng ít, ảnh phủ lớn."""
    clean_layer = row.clean * row.valid >= config.text_layer_min
    return clean_layer and 0 < row.non_ws < max_chars and row.image_area_ratio >= min_image


def is_text_layer_with_image(row: PageRow, min_image: float) -> bool:
    """Trang cho bảng "giữ lớp chữ có ảnh lớn": tầng mới là lớp chữ, ảnh phủ ít nhất `min_image`."""
    return row.new_tier == "text_layer" and row.image_area_ratio >= min_image


def is_text_layer_with_content_image(row: PageRow, min_image: float) -> bool:
    """Trang cho bảng "giữ lớp chữ có ảnh không phải template lớn": tầng mới là lớp chữ, ảnh đó phủ ≥ `min_image`."""
    return row.new_tier == "text_layer" and row.content_image_area_ratio >= min_image


def is_unflagged_figure(row: PageRow) -> bool:
    """Trang giữ lớp chữ có cờ hình mà không có lý do xem lại: vi phạm `TIER3_UNAVAILABLE` (ADR-008 mục 9)."""
    return row.new_tier == "text_layer" and row.has_figure and not row.review_reasons


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
        "unflagged_figure_pages": sum(1 for r in pages if is_unflagged_figure(r)),
    }


def _row_cells(row: PageRow) -> str:
    return (
        f"{row.non_ws} | {row.clean:.3f} | {row.valid:.3f} | {row.image_area_ratio:.3f} | {row.old_score:.3f} | "
        f"{row.old_tier} | {row.new_tier}"
    )


def _figure_cells(row: PageRow) -> str:
    reasons = ", ".join(row.review_reasons) or "(rỗng)"
    return (
        f"{row.file} | {row.page_number} | {row.non_ws} | {row.image_area_ratio * 100:.1f} | {row.new_tier} | {reasons}"
    )


def _content_figure_cells(row: PageRow) -> str:
    reasons = ", ".join(row.review_reasons) or "(rỗng)"
    return (
        f"{row.file} | {row.page_number} | {row.non_ws} | {row.image_area_ratio * 100:.1f} | "
        f"{row.content_image_area_ratio * 100:.1f} | {row.new_tier} | {reasons}"
    )


def _template_lines(report: DirReport, figure_image: float) -> list[str]:
    """Hai bảng thêm ở cuối báo cáo: ảnh template theo file, trang giữ lớp chữ có ảnh không-template lớn."""
    name = Path(report.pdf_dir).name
    lines = [
        "",
        f"## {name}: ảnh template theo file",
        "",
        "| File | Trạng thái quét | Số ảnh template |",
        "|---|---|---|",
    ]
    for file_name in report.files:
        first = next((r for r in report.pages if r.file == file_name), None)
        if first is not None:
            lines.append(f"| {file_name} | {first.template_scan} | {first.template_images} |")
    rows = [r for r in report.pages if is_text_layer_with_content_image(r, figure_image)]
    lines += [
        "",
        f"## {name}: trang giữ lớp chữ có ảnh không phải template ≥ {figure_image} ({len(rows)})",
        "",
        "| File | Trang | non_ws | % ảnh | % ảnh không-template | tier | review_reasons |",
        "|---|---|---|---|---|---|---|",
    ]
    lines += [f"| {_content_figure_cells(r)} |" for r in rows]
    return lines


def markdown(
    reports: list[DirReport], config: PdfReaderConfig, max_chars: int, min_image: float, figure_image: float
) -> str:
    lines = [
        "# Luật chọn tầng: công thức cũ và luật mới",
        "",
        f"- Cũ: `min(1, non_ws/{OLD_TEXT_FULL_CHARS}) × clean × valid < {OLD_TEXT_LAYER_MIN}` ⇒ OCR.",
        (
            f"- Mới: `non_ws = 0` ⇒ OCR; `clean × valid < {config.text_layer_min}` ⇒ OCR; "
            f"`non_ws < {config.text_sparse_chars}` và `image_area_ratio ≥ {config.text_sparse_image_min}` ⇒ OCR; "
            f"`non_ws < {config.text_full_image_max_chars}` và `image_area_ratio ≥ {config.text_full_image_min}` "
            "⇒ OCR; còn lại giữ lớp chữ."
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
        figures = [r for r in report.pages if is_text_layer_with_image(r, figure_image)]
        lines += [
            "",
            f"## {name}: trang giữ lớp chữ có ảnh ≥ {figure_image} ({len(figures)})",
            "",
            "| File | Trang | non_ws | % ảnh | tier | review_reasons |",
            "|---|---|---|---|---|---|",
        ]
        lines += [f"| {_figure_cells(r)} |" for r in figures]
        if report.skipped:
            lines += ["", f"## {name}: file bỏ qua", ""]
            lines += [f"- {item['file']}: {item['reason']}" for item in report.skipped]
    for report in reports:
        lines += _template_lines(report, figure_image)
    return "\n".join(lines) + "\n"


def evaluate_file(backend: PdfiumBackend, path: Path, config: PdfReaderConfig) -> list[PageRow] | str:
    """Các dòng của một PDF, hoặc lý do bỏ qua file.

    Áp cùng trần của app (`config.max_bytes`, `config.max_pages`): file được đọc MỘT lần, tối đa `max_bytes + 1`
    byte, rồi mới kiểm kích thước. File không đọc được hoặc PDF hỏng giữa chừng (PDFium báo lỗi, dữ liệu sai kiểu,
    trang vượt giới hạn của protocol) chỉ làm bỏ qua file đó, không dừng cả lượt đo. Chỉ bắt lỗi quanh phần đọc và mở
    PDF; lỗi khi tính dòng số liệu (`page_row`) là lỗi của script, không bị nuốt. Ảnh template được quét bằng
    đúng hàm của worker trước khi đọc trang; lỗi của lượt quét không làm bỏ qua file (lượt quét tự trả `error`).
    """
    try:
        with path.open("rb") as handle:
            data = handle.read(config.max_bytes + 1)
    except OSError as exc:
        return f"READ_FAILED: {type(exc).__name__}"
    if len(data) > config.max_bytes:
        return f"TOO_LARGE: > {config.max_bytes} byte"
    try:
        document = backend.open(data)
        if isinstance(document, str):
            return document
        with document:
            count = document.page_count()
            if count > config.max_pages:
                return f"TOO_MANY_PAGES: {count} > {config.max_pages} trang"
            scan = scan_template_images(document, count, config)
            templates = TemplateFilter.from_scan(scan, config)
            # Không có ảnh template thì đọc trang đúng như trước (như `_pdf_worker._read_one`).
            raws = [
                document.read_page(index) if templates is None else document.read_page(index, templates)
                for index in range(count)
            ]
    # `PdfiumError` và `PdfiumDataError` là `RuntimeError`; `ValidationError` của pydantic là `ValueError`.
    except (RuntimeError, ValueError, OSError, TypeError, KeyError, IndexError) as exc:
        return f"READ_FAILED: {type(exc).__name__}"
    return [with_template_scan(page_row(path.name, raw, config), scan) for raw in raws]


def evaluate_dir(backend: PdfiumBackend, pdf_dir: Path, config: PdfReaderConfig) -> DirReport:
    report = DirReport(pdf_dir=str(pdf_dir))
    for path in sorted(pdf_dir.glob("*.pdf")):
        rows = evaluate_file(backend, path, config)
        if isinstance(rows, str):
            report.skipped.append({"file": path.name, "reason": rows})
            continue
        report.files.append(path.name)
        report.pages.extend(rows)
    return report


def main(argv: list[str] | None = None) -> int:
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("pdf_dirs", type=Path, nargs="+")
    parser.add_argument("--sparse-report-chars", type=int, default=120, help="trần non_ws của bảng trang ít chữ")
    parser.add_argument("--sparse-report-image", type=float, default=0.30, help="độ phủ ảnh tối thiểu của bảng đó")
    parser.add_argument(
        "--figure-report-image", type=float, default=0.30, help="độ phủ ảnh tối thiểu của bảng trang giữ lớp chữ"
    )
    args = parser.parse_args(argv)

    out_dir = args.out_dir.resolve()
    if out_dir.is_relative_to(REPO_ROOT):
        print(f"Từ chối ghi kết quả vào trong repo: {out_dir}", file=sys.stderr)
        return 2
    config = PdfReaderConfig()
    backend = PdfiumBackend.load()
    reports = [evaluate_dir(backend, pdf_dir, config) for pdf_dir in args.pdf_dirs]
    text = markdown(reports, config, args.sparse_report_chars, args.sparse_report_image, args.figure_report_image)
    payload = {
        "old_formula": {"text_full_chars": OLD_TEXT_FULL_CHARS, "text_layer_min": OLD_TEXT_LAYER_MIN},
        "new_config": {
            "text_layer_min": config.text_layer_min,
            "text_sparse_chars": config.text_sparse_chars,
            "text_sparse_image_min": config.text_sparse_image_min,
            "text_full_image_min": config.text_full_image_min,
            "text_full_image_max_chars": config.text_full_image_max_chars,
            "figure_image_area_min": config.figure_image_area_min,
            "template_image_min_page_ratio": config.template_image_min_page_ratio,
            "template_image_min_pages": config.template_image_min_pages,
            "template_image_bbox_step": config.template_image_bbox_step,
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
