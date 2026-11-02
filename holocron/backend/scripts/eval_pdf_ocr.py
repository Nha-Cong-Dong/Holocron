"""Đánh giá opt-in tầng 2 của bộ nhập PDF trên scan giả lập (ADR-008 mục 5 (i), 14; CLAUDE.md §2.2, §5).

Chạy NGOÀI pytest, không chạy trong CI hay pre-commit; cần Tesseract `vie` đã cài (đọc `HOLOCRON_TESSERACT_CMD`,
`HOLOCRON_TESSDATA_DIR`, `HOLOCRON_PDF_OCR_SECONDARY` như app). Không gọi mạng.

    cd holocron/backend
    python -m scripts.eval_pdf_ocr <thư mục PDF> <thư mục kết quả> [--max-pages 10]

Với mỗi trang PDF mà app dùng lớp chữ (`text_layer.ocr_reason` là None) và lớp chữ có ít nhất `--min-truth-chars`
ký tự không trắng (mặc định 170, để tập trang gần với tập của công thức chọn tầng cũ `min(1, non_ws/200) × clean ×
valid ≥ 0.85` và số liệu so được với các lần đo trước. Hai tập chỉ trùng nhau trên trang có lớp chữ sạch hoàn toàn
(`clean × valid = 1`, khi đó công thức cũ cũng đòi `non_ws ≥ 170`); trang có `clean × valid` trong [0.85, 1) và
`170 ≤ non_ws < 170 / (clean × valid)` nay được tính thêm, cũng như trang mà heuristic TCVN3/VNI sửa ngày
2026-10-29 không còn bắt nhầm (ký hiệu nhãn hiệu cuối tên Latin, từ nước ngoài có chữ Latin-1)): lớp chữ làm đáp
án; trang được render 300 DPI, làm mờ và thêm nhiễu (scan giả lập), rồi OCR bằng đúng đường của worker (Tesseract
qua stdin/stdout, RapidOCR chỉ lấy token số và mã) và dựng `PageExtraction`. Chỉ số: CER sau chuẩn hoá NFC (gộp
khoảng trắng), tỉ lệ chữ số đúng, `ocr_score`, `coverage_ratio`, kết quả đối chiếu chéo, lý do xem lại và thời gian.

Chữ số đúng: mỗi trang lấy dãy chữ số của đáp án và của kết quả OCR (theo thứ tự đọc), `digit_errors` là khoảng cách
sửa giữa hai dãy; số chữ số đúng của trang là `max(0, truth_digits − digit_errors)` (`digit_correct`). Chỉ số tổng
`digit_accuracy_micro` = tổng chữ số đúng / tổng chữ số của đáp án. Trước 2026-10-29 chỉ số tổng là
`1 − Σ digit_errors / Σ truth_digits`, không kẹp từng trang: trang mà OCR đọc thêm nhiều chữ số không có trong lớp
chữ (chữ số trong ảnh chụp màn hình, sơ đồ) kéo cả tài liệu về gần 0. Lưu ý: dãy chữ số so theo thứ tự đọc, nên thứ
tự đọc khác lớp chữ (nhiều cột, bảng) cũng bị tính là lỗi; chữ số OCR đọc thêm từ ảnh cũng là lỗi chèn. Script cũng
chạy kiểm bộ chữ (`missing_vietnamese_letters`) trên `vie.traineddata` thật.

Kết quả chỉ gồm số liệu (không chép văn bản tài liệu), ghi ra `results.json` và `results.md` trong thư mục kết
quả; script từ chối ghi vào trong repo. Script đọc PDF ngay trong tiến trình này, nên chỉ dùng cho tài liệu đánh giá
của chính người chạy. Làm mờ và thêm nhiễu dùng OpenCV và numpy (numpy là phụ thuộc bắt buộc của
`opencv-python-headless`); app không dùng hai gói này ngoài RapidOCR.
"""

from __future__ import annotations

import argparse
import io
import json
import statistics
import sys
import time
import unicodedata
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path

import cv2
import numpy as np

from holocron_core.infrastructure.document_readers.config import (
    TESSERACT_LANGUAGE,
    PdfReaderConfig,
    load_ocr_settings,
)
from holocron_core.infrastructure.document_readers.pdfium_adapter import PdfiumBackend, PdfiumDocument
from holocron_core.infrastructure.document_readers.protocol import RawOcrPage, RawSecondaryPass
from holocron_core.infrastructure.document_readers.rapidocr_adapter import RapidOcrBackend, RapidOcrDataError
from holocron_core.infrastructure.document_readers.raster import (
    GrayImage,
    encode_png_gray,
    ink_outside_text_ratio,
)
from holocron_core.infrastructure.document_readers.tesseract import (
    TesseractEngine,
    TesseractError,
    missing_vietnamese_letters,
)
from holocron_core.infrastructure.document_readers.text_layer import (
    non_whitespace_count,
    ocr_reason,
    page_text,
    text_score,
)
from holocron_core.infrastructure.document_readers.tiers import assemble_ocr_page

REPO_ROOT = Path(__file__).resolve().parents[3]
DOC_ID = "doc_0000000000000000"


@dataclass
class PageResult:
    file: str
    page_number: int
    truth_chars: int
    cer: float
    edit_distance: int
    truth_digits: int
    digit_errors: int
    digit_accuracy: float | None
    text_score: float
    ocr_score: float | None
    coverage_ratio: float | None
    ink_outside_text_ratio: float | None
    cross_check: str
    token_mismatches: int
    status: str
    review_reasons: list[str]
    tesseract_s: float
    secondary_s: float | None


@dataclass
class Report:
    pdf_dir: str
    dpi: int
    blur_sigma: float
    noise_sigma: float
    seed: int
    secondary: str | None
    charset_file: str | None
    charset_missing: list[str] | None
    pages: list[PageResult] = field(default_factory=list)
    skipped: list[dict[str, object]] = field(default_factory=list)


def edit_distance(a: str, b: str) -> int:
    """Khoảng cách Levenshtein (chèn, xoá, thay đều tính 1)."""
    if len(a) < len(b):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for i, char_a in enumerate(a, start=1):
        current = [i]
        for j, char_b in enumerate(b, start=1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (char_a != char_b)))
        previous = current
    return previous[-1]


def normalize(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", text).split())


def digits(text: str) -> str:
    return "".join(c for c in text if "0" <= c <= "9")


def digit_correct(truth_digits: int, digit_errors: int) -> int:
    """Số chữ số đúng của một trang: số chữ số của đáp án trừ khoảng cách sửa, kẹp về 0.

    Khoảng cách sửa lớn hơn số chữ số của đáp án khi OCR đọc thêm chữ số (lỗi chèn); phần vượt không được trừ sang
    các trang khác.
    """
    return max(0, truth_digits - digit_errors)


def digit_accuracy_micro(pages: Sequence[tuple[int, int]]) -> float | None:
    """Tổng chữ số đúng / tổng chữ số của đáp án trên các trang `(truth_digits, digit_errors)`.

    Không có chữ số nào ⇒ None.
    """
    total = sum(truth for truth, _ in pages)
    if total == 0:
        return None
    return sum(digit_correct(truth, errors) for truth, errors in pages) / total


def simulate_scan(image: GrayImage, blur_sigma: float, noise_sigma: float, rng: np.random.Generator) -> GrayImage:
    """Scan giả lập: làm mờ Gauss rồi cộng nhiễu Gauss, cắt về [0, 255]."""
    pixels = np.frombuffer(image.pixels, dtype=np.uint8).reshape(image.height, image.width)
    blurred = cv2.GaussianBlur(pixels, (0, 0), blur_sigma) if blur_sigma > 0 else pixels
    noisy = blurred.astype(np.float32) + rng.normal(0.0, noise_sigma, blurred.shape).astype(np.float32)
    out = np.clip(noisy, 0, 255).astype(np.uint8)
    return GrayImage(pixels=out.tobytes(), width=image.width, height=image.height, dpi=image.dpi)


def _secondary_pass(
    secondary: RapidOcrBackend | None, png: bytes, image: GrayImage
) -> tuple[RawSecondaryPass | None, float | None]:
    if secondary is None:
        return None, None
    started = time.perf_counter()
    try:
        result = secondary.read(png, image.width, image.height)
    except RapidOcrDataError:
        result = None
    return result, time.perf_counter() - started


def evaluate_page(
    document: PdfiumDocument,
    file_name: str,
    index: int,
    engine: TesseractEngine,
    secondary: RapidOcrBackend | None,
    config: PdfReaderConfig,
    args: argparse.Namespace,
    rng: np.random.Generator,
) -> PageResult | str:
    raw = document.read_page(index)
    truth = normalize(page_text(raw.lines))
    score = text_score(page_text(raw.lines), config)
    reason = ocr_reason(raw, config)
    if reason is not None:
        return f"app OCR trang này ({reason}): lớp chữ không dùng làm đáp án"
    non_ws = non_whitespace_count(truth)
    if non_ws < args.min_truth_chars:
        return f"lớp chữ có {non_ws} < {args.min_truth_chars} ký tự: quá ít để làm đáp án"
    image = simulate_scan(
        document.render_gray(index, config.ocr_dpi, config.ocr_max_pixels), args.blur, args.noise, rng
    )
    png = encode_png_gray(image)
    started = time.perf_counter()
    try:
        lines, boxes = engine.recognize(png, image, config)
    except TesseractError as exc:
        return f"Tesseract lỗi: {exc}"
    tesseract_s = time.perf_counter() - started
    second, secondary_s = _secondary_pass(secondary, png, image)
    ocr = RawOcrPage(
        dpi=image.dpi,
        lines=lines,
        ink_outside_text_ratio=ink_outside_text_ratio(image, boxes, config.ink_threshold),
        secondary=second,
    )
    page = assemble_ocr_page(raw, ocr, DOC_ID, score, config)
    predicted = normalize(" ".join(line.text for line in page.lines))
    distance = edit_distance(truth, predicted)
    truth_digits, predicted_digits = digits(truth), digits(predicted)
    digit_errors = edit_distance(truth_digits, predicted_digits)
    return PageResult(
        file=file_name,
        page_number=index + 1,
        truth_chars=len(truth),
        cer=distance / max(1, len(truth)),
        edit_distance=distance,
        truth_digits=len(truth_digits),
        digit_errors=digit_errors,
        digit_accuracy=digit_correct(len(truth_digits), digit_errors) / len(truth_digits) if truth_digits else None,
        text_score=score,
        ocr_score=page.ocr_score,
        coverage_ratio=page.coverage_ratio,
        ink_outside_text_ratio=page.ink_outside_text_ratio,
        cross_check=page.cross_check.status,
        token_mismatches=len(page.cross_check.mismatches),
        status=page.status,
        review_reasons=list(page.review_reasons),
        tesseract_s=tesseract_s,
        secondary_s=secondary_s,
    )


def _fmt(value: float | None, pattern: str = "{:.3f}") -> str:
    return "—" if value is None else pattern.format(value)


def summary(report: Report) -> dict[str, object]:
    pages = report.pages
    chars = sum(p.truth_chars for p in pages)
    digit_total = sum(p.truth_digits for p in pages)
    reasons: dict[str, int] = {}
    for page in pages:
        for reason in page.review_reasons:
            reasons[reason] = reasons.get(reason, 0) + 1
    return {
        "pages": len(pages),
        "skipped": len(report.skipped),
        "cer_micro": sum(p.edit_distance for p in pages) / chars if chars else None,
        "cer_median": statistics.median(p.cer for p in pages) if pages else None,
        "digit_accuracy_micro": digit_accuracy_micro([(p.truth_digits, p.digit_errors) for p in pages]),
        "digit_total": digit_total,
        "digit_correct": sum(digit_correct(p.truth_digits, p.digit_errors) for p in pages),
        "digit_pages_errors_exceed_truth": sum(1 for p in pages if p.digit_errors > p.truth_digits),
        "tesseract_s_median": statistics.median(p.tesseract_s for p in pages) if pages else None,
        "secondary_s_median": (
            statistics.median(p.secondary_s for p in pages if p.secondary_s is not None)
            if any(p.secondary_s is not None for p in pages)
            else None
        ),
        "status": {s: sum(1 for p in pages if p.status == s) for s in sorted({p.status for p in pages})},
        "cross_check": {c: sum(1 for p in pages if p.cross_check == c) for c in sorted({p.cross_check for p in pages})},
        "review_reasons": dict(sorted(reasons.items())),
    }


def markdown(report: Report, totals: dict[str, object]) -> str:
    lines = [
        "# Đánh giá OCR tầng 2 trên scan giả lập",
        "",
        f"- DPI {report.dpi}, làm mờ σ = {report.blur_sigma}, nhiễu σ = {report.noise_sigma}, seed {report.seed}",
        f"- Lượt thứ hai: {report.secondary or 'không có'}",
        f"- Bộ chữ `{TESSERACT_LANGUAGE}.traineddata`: "
        + (
            "không kiểm (không biết thư mục tessdata)"
            if report.charset_missing is None
            else f"thiếu {len(report.charset_missing)}/134 chữ: {' '.join(report.charset_missing) or 'không thiếu'}"
        ),
        "",
        "## Tổng hợp",
        "",
        "```json",
        json.dumps(totals, ensure_ascii=False, indent=2),
        "```",
        "",
        "## Từng trang",
        "",
        (
            "| File | Trang | Ký tự | CER | Chữ số đúng | ocr_score | coverage | ink ngoài chữ | Đối chiếu | Lệch | "
            "Trạng thái | Lý do | Tesseract (s) | RapidOCR (s) |"
        ),
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for p in report.pages:
        lines.append(
            f"| {p.file} | {p.page_number} | {p.truth_chars} | {p.cer:.2%} | {_fmt(p.digit_accuracy, '{:.1%}')} | "
            f"{_fmt(p.ocr_score)} | {_fmt(p.coverage_ratio)} | {_fmt(p.ink_outside_text_ratio)} | {p.cross_check} | "
            f"{p.token_mismatches} | {p.status} | {', '.join(p.review_reasons) or '—'} | {p.tesseract_s:.1f} | "
            f"{_fmt(p.secondary_s, '{:.1f}')} |"
        )
    if report.skipped:
        lines += ["", "## Trang bỏ qua", ""]
        lines += [f"- {item['file']} trang {item['page_number']}: {item['reason']}" for item in report.skipped]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("pdf_dir", type=Path)
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--max-pages", type=int, default=10)
    parser.add_argument("--blur", type=float, default=1.2, help="σ của làm mờ Gauss (điểm ảnh)")
    parser.add_argument("--noise", type=float, default=12.0, help="σ của nhiễu Gauss (mức xám)")
    parser.add_argument("--seed", type=int, default=20261025)
    parser.add_argument(
        "--min-truth-chars", type=int, default=170, help="số ký tự không trắng tối thiểu của lớp chữ làm đáp án"
    )
    args = parser.parse_args(argv)

    out_dir = args.out_dir.resolve()
    if out_dir.is_relative_to(REPO_ROOT):
        print(f"Từ chối ghi kết quả vào trong repo: {out_dir}", file=sys.stderr)
        return 2
    config = PdfReaderConfig()
    settings = load_ocr_settings()
    engine = TesseractEngine.probe(settings, config.ocr_probe_timeout_s)
    if engine is None:
        print("Không có Tesseract với gói `vie` (kiểm HOLOCRON_TESSERACT_CMD, HOLOCRON_TESSDATA_DIR).", file=sys.stderr)
        return 2
    secondary = RapidOcrBackend.load() if settings.secondary == "rapidocr" else None
    charset_file = Path(settings.tessdata_dir, f"{TESSERACT_LANGUAGE}.traineddata") if settings.tessdata_dir else None
    charset_missing = (
        missing_vietnamese_letters(charset_file.read_bytes()) if charset_file and charset_file.is_file() else None
    )
    report = Report(
        pdf_dir=str(args.pdf_dir),
        dpi=config.ocr_dpi,
        blur_sigma=args.blur,
        noise_sigma=args.noise,
        seed=args.seed,
        secondary=settings.secondary if secondary is not None else None,
        charset_file=charset_file.name if charset_file else None,
        charset_missing=charset_missing,
    )
    rng = np.random.default_rng(args.seed)
    backend = PdfiumBackend.load()
    for path in sorted(args.pdf_dir.glob("*.pdf")):
        if len(report.pages) >= args.max_pages:
            break
        document = backend.open(path.read_bytes())
        if isinstance(document, str):
            report.skipped.append({"file": path.name, "page_number": 0, "reason": document})
            continue
        with document:
            for index in range(document.page_count()):
                if len(report.pages) >= args.max_pages:
                    break
                result = evaluate_page(document, path.name, index, engine, secondary, config, args, rng)
                if isinstance(result, str):
                    report.skipped.append({"file": path.name, "page_number": index + 1, "reason": result})
                    continue
                report.pages.append(result)
                print(f"{path.name} trang {index + 1}: CER {result.cer:.2%}, {result.status}")

    totals = summary(report)
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {"summary": totals, **asdict(report)}
    (out_dir / "results.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "results.md").write_text(markdown(report, totals), encoding="utf-8")
    print(json.dumps(totals, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
