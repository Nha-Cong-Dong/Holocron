"""Đo khử nhiễu trang và vai trò trang (GĐ0) trên tài liệu đánh giá, opt-in (spec §5.7; ADR-008 "Đo GĐ0").

Chạy NGOÀI pytest, không chạy trong CI hay pre-commit; không gọi mạng. Đọc PDF và ground truth JSON cùng thư mục
`<eval-root>/arm_ch*/<tên>.pdf|.json`, so `page_role`, bỏ mềm và `needs_review` với topic của ground truth (`bia`,
`muc-luc-*` là trang điều hướng; `bai-tap`, `on-tap` là bài tập, KHÔNG bị bỏ; còn lại là nội dung).

    cd holocron/backend
    python -m scripts.eval_pages <thư mục kết quả> [--eval-root D:/prj/Holocron/eval_data] [--template-ratio 0.6]
        [--no-ocr] [--dump-templates <thư mục PNG>]

- Lượt đọc chính đi qua `WorkerPdfReader` (worker có giới hạn, OCR theo biến môi trường như app; `--no-ocr` tắt OCR),
  đo giây/trang; lượt phụ đọc trong tiến trình này (`PdfiumBackend`) để lấy số đo thô (độ phủ ảnh, lượt quét
  template). Vai trò trang tính bằng `services.page_roles.assign_roles` so với các chương khác cùng môn (trạng thái
  cuối sau khi đánh giá lại trang bìa).
- Báo dev (ch2) và mù (ch3 + ch4) TÁCH RIÊNG: needs_review %, nav precision/recall, build_step precision (proxy:
  token ground truth của trang ⊂ trang sau), recall trang nội dung, trang bỏ nhầm/sót, has_figure, trang bỏ theo lý
  do, bảng trang bẫy, trang 0C phải bắt, độ phủ ảnh form XObject, chuỗi ảnh song song, số template mỗi loại.
- `--dump-templates`: cắt PNG các ảnh là template ở `--template-ratio` 0.40 nhưng không ở 0.60 (để người đo tự xem).
- Chạy được trên code TRƯỚC GĐ0: field hay module mới thiếu ⇒ in "n/a", không vỡ.

Kết quả chỉ gồm số trang, số đo và mã (không chép văn bản tài liệu), ghi ra `results.json` và `results.md`; script
từ chối ghi vào trong repo. eval_data không commit, không dùng trong pytest.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import io
import json
import re
import sys
import time
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from holocron_core.domain.documents import PageExtraction
from holocron_core.infrastructure.document_readers.config import PdfReaderConfig, load_ocr_settings
from holocron_core.infrastructure.document_readers.pdf_reader import WorkerPdfReader
from holocron_core.infrastructure.document_readers.pdfium_adapter import PdfiumBackend
from holocron_core.infrastructure.document_readers.template_images import TemplateFilter, scan_template_images

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_EVAL_ROOT = Path("D:/prj/Holocron/eval_data")
NA = "n/a"
DEV_SETS = ("arm_ch2",)
BLIND_SETS = ("arm_ch3", "arm_ch4")
NAV_ROLES = ("cover_or_course_nav", "chapter_nav")
EXERCISE_TOPICS = ("bai-tap", "on-tap")
GT_SKIP_BLOCKS = ("header", "footer", "page_number")
BUILD_STEP_GT_SUBSET = 0.95
_TOKEN = re.compile(r"\w+")

# Trang bẫy và trang phải bắt (spec §5.7, ADR-008 "Đo GĐ0"): (bộ, trang).
TRAPS: dict[str, list[tuple[str, int]]] = {
    "hidden": [("arm_ch2", 24)],
    "white_on_color_keep": [("arm_ch3", 13), ("arm_ch3", 52), ("arm_ch3", 61), ("arm_ch3", 86)],
    "occluded": [("arm_ch2", 23), ("arm_ch3", 86), ("arm_ch4", 14), ("arm_ch4", 22), ("arm_ch4", 46)],
    "glyph": [("arm_ch2", 42)],
}
MUST_READ_IMAGE: list[tuple[str, int]] = [
    *[("arm_ch3", n) for n in (24, 25, 26, 27, 42, 43, 51, 62, 63, 64, 65, 66, 73, 80, 81, 82, 85)],
    ("arm_ch4", 22),
]
FORM_COVERAGE: list[tuple[str, int]] = [("arm_ch3", n) for n in (25, 26, 27, 80, 81, 82)]
PARALLEL_SERIES: list[tuple[str, int]] = [("arm_ch3", n) for n in (62, 63, 64, 65, 66)]


# --------------------------------------------------------------------------- #
# Phần tính chỉ số (hàm thuần, có test đơn vị: tests/test_eval_pages.py)
# --------------------------------------------------------------------------- #


def is_nav_topic(topic: object) -> bool:
    """Trang điều hướng theo ground truth: bìa hoặc mục lục (`bia`, `muc-luc-*`)."""
    return isinstance(topic, str) and (topic == "bia" or topic.startswith("muc-luc"))


def gt_tokens(blocks: Iterable[dict[str, Any]]) -> frozenset[str]:
    """Token của ground truth (bỏ header, footer, số trang), cùng cách tách token của app (NFC, casefold, `\\w+`)."""
    found: set[str] = set()
    for block in blocks:
        if block.get("type") in GT_SKIP_BLOCKS:
            continue
        text = block.get("text")
        if isinstance(text, str):
            found.update(_TOKEN.findall(unicodedata.normalize("NFC", text).casefold()))
    return frozenset(found)


@dataclass
class PageRow:
    """Một trang đã đo: chỉ số trang, mã và số đo (không văn bản)."""

    set_name: str
    page: int
    topic: str | None
    status: str
    page_role: str
    dropped: bool | None
    review_reasons: list[str]
    has_figure: bool
    tier_used: str | None
    failure_reason: str | None
    line_flags: dict[str, int] = field(default_factory=dict)
    hidden_chars_dropped: int | None = None
    image_area_ratio: float | None = None
    content_image_area_ratio: float | None = None
    ocr_reason: str | None = None
    gt_build_subset: float | None = None


def ratio(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else numerator / denominator


def metrics(rows: Sequence[PageRow]) -> dict[str, Any]:
    """Chỉ số GĐ0 của một nhóm trang (dev hoặc mù)."""
    total = len(rows)
    nav_true = {(r.set_name, r.page) for r in rows if is_nav_topic(r.topic)}
    nav_pred = {(r.set_name, r.page) for r in rows if r.page_role in NAV_ROLES and r.dropped}
    dropped = {(r.set_name, r.page) for r in rows if r.dropped}
    content_true = {(r.set_name, r.page) for r in rows} - nav_true
    build = [r for r in rows if r.page_role == "build_step" and r.dropped]
    build_ok = [r for r in build if r.gt_build_subset is not None and r.gt_build_subset >= BUILD_STEP_GT_SUBSET]
    needs_review = [r for r in rows if r.status == "needs_review" and not r.dropped]
    roles_known = any(r.dropped is not None for r in rows)
    return {
        "pages": total,
        "needs_review": len(needs_review),
        "needs_review_pct": ratio(len(needs_review), total),
        "nav_precision": ratio(len(nav_pred & nav_true), len(nav_pred)) if roles_known else NA,
        "nav_recall": ratio(len(nav_pred & nav_true), len(nav_true)) if roles_known else NA,
        "build_step_count": len(build) if roles_known else NA,
        "build_step_precision": ratio(len(build_ok), len(build)) if roles_known else NA,
        "content_recall": ratio(len(content_true - dropped), len(content_true)) if roles_known else NA,
        "dropped_wrongly": sorted(content_true & dropped) if roles_known else NA,
        "nav_missed": sorted(nav_true - nav_pred) if roles_known else NA,
        "has_figure": sum(r.has_figure for r in rows),
        "dropped_by_role": dict(Counter(r.page_role for r in rows if r.dropped)) if roles_known else NA,
        "reasons": dict(Counter(reason for r in rows for reason in r.review_reasons)),
    }


def trap_report(rows: Sequence[PageRow]) -> dict[str, list[dict[str, Any]]]:
    by_key = {(r.set_name, r.page): r for r in rows}
    report: dict[str, list[dict[str, Any]]] = {}
    for name, keys in TRAPS.items():
        report[name] = [
            {
                "page": f"{s}:{n}",
                "status": by_key[(s, n)].status,
                "line_flags": by_key[(s, n)].line_flags,
                "hidden_chars_dropped": by_key[(s, n)].hidden_chars_dropped,
                "reasons": by_key[(s, n)].review_reasons,
            }
            for s, n in keys
            if (s, n) in by_key
        ]
    return report


def must_read_report(rows: Sequence[PageRow]) -> list[dict[str, Any]]:
    by_key = {(r.set_name, r.page): r for r in rows}
    out: list[dict[str, Any]] = []
    for s, n in MUST_READ_IMAGE:
        row = by_key.get((s, n))
        if row is None:
            continue
        caught = row.tier_used == "ocr" or row.failure_reason == "OCR_ENGINE_MISSING"
        out.append(
            {
                "page": f"{s}:{n}",
                "caught": caught,
                "ocr_reason": row.ocr_reason,
                "image_needs_reading": "IMAGE_NEEDS_READING" in row.review_reasons,
                "content_image_area_ratio": row.content_image_area_ratio,
            }
        )
    return out


# --------------------------------------------------------------------------- #
# Đọc tài liệu
# --------------------------------------------------------------------------- #


def make_config(template_ratio: float) -> PdfReaderConfig:
    """Cấu hình mặc định với tỉ lệ template đã chọn; code trước GĐ0 dùng tên cũ của ngưỡng."""
    fields = PdfReaderConfig.model_fields
    name = "template_min_page_ratio" if "template_min_page_ratio" in fields else "template_image_min_page_ratio"
    return PdfReaderConfig.model_validate({name: template_ratio})


def _attr(obj: object, name: str) -> Any:
    return getattr(obj, name, None)


def find_sets(root: Path) -> list[tuple[str, Path, Path]]:
    sets: list[tuple[str, Path, Path]] = []
    for folder in sorted(root.glob("arm_ch*")):
        pdfs = sorted(folder.glob("*.pdf"))
        if pdfs and pdfs[0].with_suffix(".json").is_file():
            sets.append((folder.name, pdfs[0], pdfs[0].with_suffix(".json")))
    return sets


@dataclass
class RawStats:
    scan_status: str
    template_counts: dict[str, int | str]
    image_area: dict[int, float]
    content_image_area: dict[int, float | None]
    ocr_reasons: dict[int, str | None]


def raw_pass(path: Path, config: PdfReaderConfig) -> RawStats:
    """Lượt đọc trong tiến trình (tài liệu đánh giá của chính người chạy): lượt quét template và số đo thô."""
    text_layer = importlib.import_module("holocron_core.infrastructure.document_readers.text_layer")
    reason_of: Callable[..., object] | None = getattr(text_layer, "ocr_reason", None)
    opened = PdfiumBackend.load().open(path.read_bytes())
    if isinstance(opened, str):
        raise SystemExit(f"không mở được {path.name}: {opened}")
    with opened as document:
        count = document.page_count()
        scan = scan_template_images(document, count, config)
        templates = TemplateFilter.from_scan(scan, config)
        image_area: dict[int, float] = {}
        content: dict[int, float | None] = {}
        reasons: dict[int, str | None] = {}
        for index in range(count):
            try:
                raw = document.read_page(index, templates, config)
            except TypeError:
                raw = document.read_page(index, templates)
            image_area[index + 1] = raw.image_area_ratio
            content[index + 1] = raw.content_image_area_ratio
            reason = None if reason_of is None else reason_of(raw, config)
            reasons[index + 1] = None if reason is None else str(reason)
    return RawStats(
        scan_status=scan.status,
        template_counts={
            "image": len(scan.keys),
            "path": len(_attr(scan, "path_keys")) if _attr(scan, "path_keys") is not None else NA,
            "text": len(_attr(scan, "text_keys")) if _attr(scan, "text_keys") is not None else NA,
        },
        image_area=image_area,
        content_image_area=content,
        ocr_reasons=reasons,
    )


def worker_pass(path: Path, config: PdfReaderConfig, *, use_ocr: bool) -> tuple[list[PageExtraction], float]:
    ocr = load_ocr_settings() if use_ocr else None
    reader = WorkerPdfReader(config, ocr)
    document_id = "doc_" + hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    started = time.perf_counter()
    pages = list(reader.read_pages(path, document_id))
    return pages, time.perf_counter() - started


def assign_all_roles(docs: dict[str, list[PageExtraction]], config: PdfReaderConfig) -> dict[str, list[PageExtraction]]:
    """Vai trò trang như service sau khi mọi chương đã nhập (so với các chương khác cùng môn); code cũ ⇒ giữ nguyên."""
    try:
        roles = importlib.import_module("holocron_core.services.page_roles")
    except ImportError:
        return docs
    result: dict[str, list[PageExtraction]] = {}
    for name, pages in docs.items():
        others = [page for other, items in docs.items() if other != name for page in items]
        decisions = roles.assign_roles(pages, len(pages), others, config)
        updated: list[PageExtraction] = []
        for page in pages:
            decision = decisions.get(page.page_number)
            changed = None if decision is None else roles.apply_decision(page, decision)
            updated.append(page if changed is None else changed)
        result[name] = updated
    return result


def _line_flags(page: PageExtraction) -> dict[str, int]:
    return dict(Counter(flag for line in page.lines for flag in line.flags))


def build_rows(
    name: str, pages: Sequence[PageExtraction], ground_truth: dict[str, Any], raw: RawStats
) -> list[PageRow]:
    truth = {int(page["page"]): page for page in ground_truth["pages"]}
    rows: list[PageRow] = []
    for page in pages:
        gt = truth.get(page.page_number, {})
        following = truth.get(page.page_number + 1)
        subset: float | None = None
        if following is not None:
            current, later = gt_tokens(gt.get("blocks", [])), gt_tokens(following.get("blocks", []))
            subset = ratio(len(current & later), len(current))
        rows.append(
            PageRow(
                set_name=name,
                page=page.page_number,
                topic=gt.get("topic"),
                status=page.status,
                page_role=str(_attr(page, "page_role") or "content"),
                dropped=_attr(page, "dropped"),
                review_reasons=list(page.review_reasons),
                has_figure=page.has_figure,
                tier_used=page.tier_used,
                failure_reason=page.failure_reason,
                line_flags=_line_flags(page),
                hidden_chars_dropped=_attr(page, "hidden_chars_dropped"),
                image_area_ratio=raw.image_area.get(page.page_number),
                content_image_area_ratio=raw.content_image_area.get(page.page_number),
                ocr_reason=raw.ocr_reasons.get(page.page_number),
                gt_build_subset=subset,
            )
        )
    return rows


# --------------------------------------------------------------------------- #
# Cắt PNG ảnh template (ảnh template ở 0.40 nhưng không ở 0.60)
# --------------------------------------------------------------------------- #


def dump_template_diff(path: Path, name: str, out_dir: Path) -> list[dict[str, Any]]:
    """Cắt PNG (150 DPI) mỗi ảnh là template ở 0.40 mà không ở 0.60, tại một trang có ảnh đó; trả danh sách khoá."""
    raster = importlib.import_module("holocron_core.infrastructure.document_readers.raster")
    low, high = make_config(0.40), make_config(0.60)
    opened = PdfiumBackend.load().open(path.read_bytes())
    if isinstance(opened, str):
        return []
    found: list[dict[str, Any]] = []
    with opened as document:
        count = document.page_count()
        extra = set(scan_template_images(document, count, low).keys) - set(
            scan_template_images(document, count, high).keys
        )
        for number, key in enumerate(sorted(extra, key=lambda k: (k.digest, k.bbox))):
            pages = []
            for index in range(count):
                scanned = document.page_image_keys(index, low)
                if scanned is not None and key in scanned.keys:
                    pages.append(index + 1)
            if not pages:
                continue
            image = document.render_gray(pages[0] - 1, 150, 25_000_000)
            step = low.template_image_bbox_step
            x0, y0, x1, y1 = (min(1.0, max(0.0, v * step)) for v in key.bbox)
            left, top = int(x0 * image.width), int(y0 * image.height)
            right, bottom = max(left + 1, int(x1 * image.width)), max(top + 1, int(y1 * image.height))
            rows = b"".join(image.pixels[y * image.width + left : y * image.width + right] for y in range(top, bottom))
            crop = raster.GrayImage(pixels=rows, width=right - left, height=bottom - top, dpi=150.0)
            target = out_dir / f"{name}_key{number:02d}_p{pages[0]}.png"
            target.write_bytes(raster.encode_png_gray(crop))
            found.append({"key": number, "digest": key.digest[:12], "bbox": list(key.bbox), "pages": pages})
    return found


# --------------------------------------------------------------------------- #
# Chạy
# --------------------------------------------------------------------------- #


def _inside_repo(path: Path) -> bool:
    try:
        path.resolve().relative_to(REPO_ROOT)
    except ValueError:
        return False
    return True


def _fmt(value: object) -> str:
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def render_markdown(result: dict[str, Any]) -> str:
    lines = [f"# GĐ0 eval_pages — template_ratio={result['template_ratio']}, ocr={result['ocr']}", ""]
    for group in ("dev", "blind"):
        lines.append(f"## {group}")
        for key, value in result[group]["metrics"].items():
            lines.append(f"- {key}: {_fmt(value)}")
        lines.append("")
    lines.append("## Template")
    for name, counts in result["templates"].items():
        lines.append(f"- {name}: {counts}")
    lines.append("")
    lines.append("## Tốc độ (s/trang)")
    for name, value in result["seconds_per_page"].items():
        lines.append(f"- {name}: {_fmt(value)}")
    lines.append("")
    lines.append("## Trang bẫy")
    for name, items in result["traps"].items():
        lines.append(f"- {name}: {json.dumps(items, ensure_ascii=False)}")
    lines.append("")
    lines.append("## 0C phải bắt")
    for item in result["must_read"]:
        lines.append(f"- {json.dumps(item, ensure_ascii=False)}")
    lines.append("")
    lines.append("## Độ phủ ảnh form XObject")
    for item in result["form_coverage"]:
        lines.append(f"- {json.dumps(item, ensure_ascii=False)}")
    lines.append("")
    lines.append("## Chuỗi ảnh song song")
    for item in result["parallel_series"]:
        lines.append(f"- {json.dumps(item, ensure_ascii=False)}")
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Đo GĐ0 (khử nhiễu, vai trò trang) trên eval_data")
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--eval-root", type=Path, default=DEFAULT_EVAL_ROOT)
    parser.add_argument("--template-ratio", type=float, default=0.60)
    parser.add_argument("--no-ocr", action="store_true")
    parser.add_argument("--dump-templates", type=Path, default=None)
    args = parser.parse_args(argv)
    if _inside_repo(args.out_dir) or (args.dump_templates is not None and _inside_repo(args.dump_templates)):
        print("từ chối ghi kết quả vào trong repo", file=sys.stderr)
        return 2
    args.out_dir.mkdir(parents=True, exist_ok=True)
    config = make_config(args.template_ratio)
    sets = find_sets(args.eval_root)
    docs: dict[str, list[PageExtraction]] = {}
    raws: dict[str, RawStats] = {}
    truths: dict[str, dict[str, Any]] = {}
    speeds: dict[str, float] = {}
    dumps: dict[str, list[dict[str, Any]]] = {}
    for name, pdf, truth in sets:
        raws[name] = raw_pass(pdf, config)
        pages, seconds = worker_pass(pdf, config, use_ocr=not args.no_ocr)
        docs[name] = pages
        speeds[name] = seconds / max(len(pages), 1)
        truths[name] = json.loads(truth.read_text(encoding="utf-8"))
        if args.dump_templates is not None:
            args.dump_templates.mkdir(parents=True, exist_ok=True)
            dumps[name] = dump_template_diff(pdf, name, args.dump_templates)
        print(f"{name}: {len(pages)} trang, {speeds[name]:.3f} s/trang", flush=True)
    docs = assign_all_roles(docs, config)
    rows = [row for name in docs for row in build_rows(name, docs[name], truths[name], raws[name])]
    dev = [r for r in rows if r.set_name in DEV_SETS]
    blind = [r for r in rows if r.set_name in BLIND_SETS]
    by_key = {(r.set_name, r.page): r for r in rows}
    result: dict[str, Any] = {
        "template_ratio": args.template_ratio,
        "ocr": not args.no_ocr,
        "dev": {"metrics": metrics(dev)},
        "blind": {"metrics": metrics(blind)},
        "templates": {name: {"scan": raws[name].scan_status, **raws[name].template_counts} for name in raws},
        "seconds_per_page": speeds,
        "traps": trap_report(rows),
        "must_read": must_read_report(rows),
        "form_coverage": [
            {
                "page": f"{s}:{n}",
                "image_area_ratio": by_key[(s, n)].image_area_ratio,
                "content_image_area_ratio": by_key[(s, n)].content_image_area_ratio,
            }
            for s, n in FORM_COVERAGE
            if (s, n) in by_key
        ],
        "parallel_series": [
            {"page": f"{s}:{n}", "page_role": by_key[(s, n)].page_role, "dropped": by_key[(s, n)].dropped}
            for s, n in PARALLEL_SERIES
            if (s, n) in by_key
        ],
        "template_diff_040_vs_060": dumps,
        "rows": [row.__dict__ for row in rows],
    }
    (args.out_dir / "results.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    (args.out_dir / "results.md").write_text(render_markdown(result), encoding="utf-8")
    print(render_markdown(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
