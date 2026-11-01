"""Đo nền OCR tự sinh từ lớp chữ: CER theo engine, DPI, PSM (brief X01; CLAUDE.md §2.2, §4.4).

Chạy NGOÀI pytest, không chạy trong CI hay pre-commit; cần Tesseract `vie` đã cài (đọc `HOLOCRON_TESSERACT_CMD`,
`HOLOCRON_TESSDATA_DIR` như app) và gói `rapidocr` đã ghim. Không gọi mạng, không sửa `holocron_core/`.

    cd holocron/backend
    python -m scripts.eval_ocr_selfsup <gốc eval_data> <thư mục kết quả> [--dpi 150,200,300] [--psm 3,4,6]
        [--max-pages 40] [--degrade] [--workers 3]

Tham số thứ nhất là gốc eval_data: mỗi thư mục con chứa PDF là một bộ (nếu gốc chứa thẳng PDF thì là một bộ duy
nhất). Bộ `arm_ch2` là dev, các bộ còn lại là mù (báo riêng; KHÔNG chỉnh tham số theo mù, khuyến nghị chỉ chọn trên
dev rồi đối chiếu mù).

Đáp án: trang mà app dùng lớp chữ (`text_layer.ocr_reason` là None, đọc với template như worker) và lớp chữ có ít
nhất `--min-truth-chars` ký tự không trắng; chỉ lấy các dòng KHÔNG có cờ `LOW_TEXT_CONTRAST`, `TEXT_OCCLUDED` hay
`header_footer` (chữ ẩn đã bị loại sẵn khi đọc lớp chữ), để CER không bị thổi phồng. Mỗi bộ lấy tối đa `--max-pages`
trang, lấy mẫu tất định theo chỉ số đều nhau trong danh sách trang đủ điều kiện. Mỗi trang được render ở từng DPI
(pdfium, như worker), chạy Tesseract `-l vie` với từng PSM (App không truyền `--psm`, tức PSM mặc định 3) và RapidOCR
(`rapidocr.RapidOCR` với đúng tham số model của `rapidocr_adapter.RapidOcrBackend.load_outcome`, lấy TOÀN BỘ chữ,
khác `RapidOcrBackend.read` chỉ trả token số/mã).

Chỉ số mỗi tổ hợp (engine, DPI, PSM), sau NFC và gộp khoảng trắng:
- `cer_micro`: tổng khoảng cách sửa ký tự / tổng ký tự đáp án; `cer_median`: trung vị CER theo trang;
- `wer_micro`: như trên theo từ (tách theo khoảng trắng);
- `diacritic_cer`: CER của dãy ký tự có dấu tiếng Việt (ký tự có dấu ghép sau NFD, hoặc đ/Đ) rút ra riêng từ đáp án
  và từ kết quả OCR, chèn tính là lỗi;
- `diacritic_retention`: số ký tự có dấu của đáp án mà OCR trả đúng đúng ký tự đó (căn chỉnh toàn trang theo
  khoảng cách sửa) / tổng ký tự có dấu của đáp án;
- `digit_accuracy_micro`: định nghĩa như `scripts/eval_pdf_ocr.py` (tổng chữ số đúng / tổng chữ số đáp án, kẹp từng
  trang);
- `seconds_median`: thời gian trung vị mỗi trang (một lần gọi engine, không gồm render; các lần gọi Tesseract chạy
  song song nên thời gian bị đẩy lên khi `--workers` > 1).

Giới hạn: lớp chữ PDF và OCR có thể khác thứ tự đọc (nhiều cột, bảng), làm CER cao hơn thực; chữ trong ảnh nhúng bị
OCR đọc thêm tính là lỗi chèn; ảnh render sạch từ PDF số cho CER lạc quan hơn scan thật. `--degrade` làm mờ và thêm
nhiễu (như `eval_pdf_ocr.py`, seed cố định, σ làm mờ tỉ lệ theo DPI/300) nhưng vẫn chỉ là mô phỏng.

Kết quả (chỉ số, không chép văn bản tài liệu) ghi `results.json` và `results.md` vào thư mục kết quả; script từ chối
ghi vào trong repo.
"""

from __future__ import annotations

import argparse
import importlib
import io
import json
import statistics
import sys
import time
import unicodedata
from collections import defaultdict
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import numpy.typing as npt

from holocron_core.infrastructure.document_readers.config import (
    TESSERACT_LANGUAGE,
    PdfReaderConfig,
    load_ocr_settings,
)
from holocron_core.infrastructure.document_readers.pdfium_adapter import PdfiumBackend, PdfiumDocument
from holocron_core.infrastructure.document_readers.protocol import RawPdfPage
from holocron_core.infrastructure.document_readers.rapidocr_adapter import CLS_MODEL, DET_MODEL, REC_MODEL
from holocron_core.infrastructure.document_readers.raster import encode_png_gray
from holocron_core.infrastructure.document_readers.template_images import TemplateFilter, scan_template_images
from holocron_core.infrastructure.document_readers.tesseract import (
    TesseractEngine,
    run_bounded,
)
from holocron_core.infrastructure.document_readers.text_layer import non_whitespace_count, ocr_reason
from scripts.eval_pdf_ocr import digit_accuracy_micro, digit_correct, simulate_scan

REPO_ROOT = Path(__file__).resolve().parents[3]
DEV_SET = "arm_ch2"
RAPIDOCR = "rapidocr"
TESSERACT = "tesseract"
IntArray = npt.NDArray[np.int32]


# --------------------------------------------------------------------------- #
# Phần thuần: chuẩn hoá, khoảng cách sửa, căn chỉnh, chỉ số
# --------------------------------------------------------------------------- #


def normalize(text: str) -> str:
    """NFC rồi gộp mọi khoảng trắng thành một dấu cách."""
    return " ".join(unicodedata.normalize("NFC", text).split())


def has_diacritic(char: str) -> bool:
    """Ký tự có dấu: sau NFD có dấu ghép (thanh, mũ, móc, trăng…), hoặc đ/Đ."""
    if char in "đĐ":
        return True
    return any(unicodedata.combining(part) for part in unicodedata.normalize("NFD", char))


def diacritic_chars(text: str) -> str:
    return "".join(char for char in text if has_diacritic(char))


def _codes(items: Sequence[object]) -> IntArray:
    table: dict[object, int] = {}
    return np.array([table.setdefault(item, len(table)) for item in items], dtype=np.int32)


def _joint_codes(a: Sequence[object], b: Sequence[object]) -> tuple[IntArray, IntArray]:
    table: dict[object, int] = {}
    left = np.array([table.setdefault(item, len(table)) for item in a], dtype=np.int32)
    right = np.array([table.setdefault(item, len(table)) for item in b], dtype=np.int32)
    return left, right


def _matrix(a: IntArray, b: IntArray) -> npt.NDArray[np.int32]:
    """Ma trận khoảng cách sửa `(len(a)+1) × (len(b)+1)`; mỗi hàng tính bằng numpy (cummin cho phép chèn)."""
    n, m = len(a), len(b)
    matrix = np.empty((n + 1, m + 1), dtype=np.int32)
    matrix[0] = np.arange(m + 1, dtype=np.int32)
    index = np.arange(m + 1, dtype=np.int32)
    for i in range(1, n + 1):
        prev = matrix[i - 1]
        row = np.empty(m + 1, dtype=np.int32)
        row[0] = i
        row[1:] = np.minimum(prev[:-1] + (b != a[i - 1]), prev[1:] + 1)
        matrix[i] = np.minimum.accumulate(row - index) + index
    return matrix


def sequence_distance(a: Sequence[object], b: Sequence[object]) -> int:
    """Khoảng cách Levenshtein giữa hai dãy (chèn, xoá, thay đều tính 1); chỉ giữ hai hàng."""
    if not a:
        return len(b)
    if not b:
        return len(a)
    left, right = _joint_codes(a, b)
    m = len(right)
    index = np.arange(m + 1, dtype=np.int32)
    prev = index.copy()
    for i in range(1, len(left) + 1):
        row = np.empty(m + 1, dtype=np.int32)
        row[0] = i
        row[1:] = np.minimum(prev[:-1] + (right != left[i - 1]), prev[1:] + 1)
        prev = np.minimum.accumulate(row - index) + index
    return int(prev[-1])


def kept_flags(truth: str, predicted: str) -> list[bool]:
    """Với mỗi ký tự của `truth`: True khi căn chỉnh tối ưu ghép nó với đúng ký tự đó của `predicted`."""
    if not truth:
        return []
    if not predicted:
        return [False] * len(truth)
    left, right = _joint_codes(truth, predicted)
    matrix = _matrix(left, right)
    flags = [False] * len(truth)
    i, j = len(truth), len(predicted)
    while i > 0 and j > 0:
        same = left[i - 1] == right[j - 1]
        if matrix[i, j] == matrix[i - 1, j - 1] + (0 if same else 1):
            flags[i - 1] = bool(same)
            i, j = i - 1, j - 1
        elif matrix[i, j] == matrix[i - 1, j] + 1:
            i -= 1
        else:
            j -= 1
    return flags


@dataclass(frozen=True)
class PageScore:
    truth_chars: int
    char_distance: int
    truth_words: int
    word_distance: int
    diacritic_total: int
    diacritic_kept: int
    diacritic_seq_distance: int
    truth_digits: int
    digit_errors: int

    @property
    def cer(self) -> float:
        return self.char_distance / max(1, self.truth_chars)


def score_page(truth_raw: str, predicted_raw: str) -> PageScore:
    """Chỉ số của một trang (đáp án và kết quả OCR đều được chuẩn hoá NFC + khoảng trắng trước)."""
    truth, predicted = normalize(truth_raw), normalize(predicted_raw)
    truth_words, predicted_words = truth.split(), predicted.split()
    truth_dia, predicted_dia = diacritic_chars(truth), diacritic_chars(predicted)
    truth_digits = "".join(c for c in truth if "0" <= c <= "9")
    predicted_digits = "".join(c for c in predicted if "0" <= c <= "9")
    flags = kept_flags(truth, predicted)
    return PageScore(
        truth_chars=len(truth),
        char_distance=sequence_distance(truth, predicted),
        truth_words=len(truth_words),
        word_distance=sequence_distance(truth_words, predicted_words),
        diacritic_total=len(truth_dia),
        diacritic_kept=sum(1 for char, kept in zip(truth, flags, strict=True) if kept and has_diacritic(char)),
        diacritic_seq_distance=sequence_distance(truth_dia, predicted_dia),
        truth_digits=len(truth_digits),
        digit_errors=sequence_distance(truth_digits, predicted_digits),
    )


def _ratio(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator else None


def aggregate(scores: Sequence[PageScore], seconds: Sequence[float], failures: int = 0) -> dict[str, object]:
    """Chỉ số tổng của một nhóm trang (cùng tổ hợp); rỗng ⇒ các tỉ lệ là None."""
    chars = sum(s.truth_chars for s in scores)
    return {
        "pages": len(scores),
        "failures": failures,
        "cer_micro": _ratio(sum(s.char_distance for s in scores), chars),
        "cer_median": statistics.median(s.cer for s in scores) if scores else None,
        "wer_micro": _ratio(sum(s.word_distance for s in scores), sum(s.truth_words for s in scores)),
        "diacritic_cer": _ratio(sum(s.diacritic_seq_distance for s in scores), sum(s.diacritic_total for s in scores)),
        "diacritic_retention": _ratio(sum(s.diacritic_kept for s in scores), sum(s.diacritic_total for s in scores)),
        "diacritic_total": sum(s.diacritic_total for s in scores),
        "digit_accuracy_micro": digit_accuracy_micro([(s.truth_digits, s.digit_errors) for s in scores]),
        "digit_correct": sum(digit_correct(s.truth_digits, s.digit_errors) for s in scores),
        "digit_total": sum(s.truth_digits for s in scores),
        "seconds_median": statistics.median(seconds) if seconds else None,
        "truth_chars": chars,
    }


def refuses_output_dir(out_dir: Path, repo_root: Path = REPO_ROOT) -> bool:
    """True khi `out_dir` nằm trong repo (script từ chối ghi kết quả vào đó)."""
    return out_dir.resolve().is_relative_to(repo_root.resolve())


def parse_int_list(text: str) -> list[int]:
    """`"150,200,300"` ⇒ `[150, 200, 300]`; rỗng, không dương hoặc trùng ⇒ ValueError."""
    values = [int(part) for part in text.split(",") if part.strip()]
    if not values or any(v <= 0 for v in values) or len(set(values)) != len(values):
        raise ValueError(f"danh sách số nguyên dương không trùng, nhận {text!r}")
    return values


def _int_list_arg(text: str) -> list[int]:
    try:
        return parse_int_list(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("pdf_root", type=Path, help="gốc eval_data (mỗi thư mục con là một bộ)")
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--dpi", type=_int_list_arg, default=[150, 200, 300])
    parser.add_argument("--psm", type=_int_list_arg, default=[3, 4, 6])
    parser.add_argument("--max-pages", type=int, default=40, help="số trang tối đa mỗi bộ")
    parser.add_argument("--min-truth-chars", type=int, default=170)
    parser.add_argument("--degrade", action="store_true", help="làm mờ và thêm nhiễu ảnh render (scan giả lập)")
    parser.add_argument("--blur", type=float, default=1.2, help="σ làm mờ ở 300 DPI (tỉ lệ theo DPI)")
    parser.add_argument("--noise", type=float, default=12.0)
    parser.add_argument("--seed", type=int, default=20261101)
    parser.add_argument("--workers", type=int, default=3, help="số lần gọi Tesseract song song")
    return parser


def sample_evenly(items: Sequence[int], limit: int) -> list[int]:
    """Lấy tối đa `limit` phần tử cách đều, tất định, giữ thứ tự (limit ≥ len ⇒ giữ hết)."""
    if limit <= 0:
        return []
    if len(items) <= limit:
        return list(items)
    return [items[(k * len(items)) // limit] for k in range(limit)]


# --------------------------------------------------------------------------- #
# Phần chạy engine
# --------------------------------------------------------------------------- #


def truth_text(raw: RawPdfPage) -> str:
    """Các dòng lớp chữ không cờ (LOW_TEXT_CONTRAST, TEXT_OCCLUDED, header_footer), nối bằng xuống dòng."""
    return "\n".join(line.text for line in raw.lines if not line.flags and not line.header_footer)


def build_rapidocr() -> Callable[[bytes], object] | None:
    """`rapidocr.RapidOCR` với đúng tham số của `RapidOcrBackend.load_outcome` (rapidocr_adapter.py:155-164), nhưng
    đọc toàn bộ chữ. None khi thiếu gói hay model đóng sẵn."""
    try:
        module = importlib.import_module("rapidocr")
    except ImportError:
        return None
    package_file = getattr(module, "__file__", None)
    factory = getattr(module, "RapidOCR", None)
    if not isinstance(package_file, str) or not callable(factory):
        return None
    models = Path(package_file).parent / "models"
    paths = {name: models / name for name in (DET_MODEL, REC_MODEL, CLS_MODEL)}
    if not all(path.is_file() for path in paths.values()):
        return None
    params: dict[str, object] = {
        "Global.use_cls": False,
        "Global.log_level": "critical",
        "Det.model_path": str(paths[DET_MODEL]),
        "Rec.model_path": str(paths[REC_MODEL]),
        "Cls.model_path": str(paths[CLS_MODEL]),
    }
    engine: object = factory(params=params)
    if not callable(engine):
        return None
    return engine


def rapidocr_text(engine: Callable[[bytes], object], png: bytes) -> str:
    output = engine(png)
    texts = getattr(output, "txts", None)
    if texts is None:
        return ""
    return " ".join(str(t) for t in texts)


def tesseract_text(
    engine: TesseractEngine, png: bytes, dpi: int, psm: int, config: PdfReaderConfig
) -> tuple[str, bool]:
    """Văn bản Tesseract với `--psm`; (chuỗi rỗng, False) khi lỗi/quá hạn. Cùng môi trường tối thiểu và
    `--tessdata-dir` như worker (`run_bounded` dùng `tesseract_env()`)."""
    args = [engine.command, "stdin", "stdout", "-l", TESSERACT_LANGUAGE]
    if engine.tessdata_dir is not None:
        args += ["--tessdata-dir", engine.tessdata_dir]
    args += ["--dpi", str(dpi), "--psm", str(psm)]
    result = run_bounded(args, png, config.tesseract_timeout_s, config.max_message_bytes)
    if result.status != "ok":
        return "", False
    try:
        return result.stdout.decode("utf-8"), True
    except UnicodeDecodeError:
        return "", False


@dataclass
class ComboPage:
    set_name: str
    group: str
    file: str
    page_number: int
    engine: str
    dpi: int
    psm: int | None
    ok: bool
    seconds: float
    score: PageScore


@dataclass
class Report:
    pdf_root: str
    dpis: list[int]
    psms: list[int]
    degrade: bool
    seed: int
    max_pages: int
    min_truth_chars: int
    pages: list[ComboPage] = field(default_factory=list)
    selected: dict[str, int] = field(default_factory=dict)
    skipped: dict[str, int] = field(default_factory=dict)


def combo_key(engine: str, dpi: int, psm: int | None) -> str:
    return f"{engine} dpi={dpi} psm={'-' if psm is None else psm}"


def eligible_pages(
    document: PdfiumDocument, config: PdfReaderConfig, min_chars: int
) -> tuple[list[tuple[int, str]], int]:
    """(trang đủ điều kiện kèm đáp án, số trang bị loại). Đọc có template như worker."""
    count = document.page_count()
    scan = scan_template_images(document, count, config)
    templates = TemplateFilter.from_scan(scan, config)
    eligible: list[tuple[int, str]] = []
    skipped = 0
    for index in range(count):
        raw = document.read_page(index) if templates is None else document.read_page(index, templates)
        truth = normalize(truth_text(raw))
        if ocr_reason(raw, config) is not None or non_whitespace_count(truth) < min_chars:
            skipped += 1
            continue
        eligible.append((index, truth))
    return eligible, skipped


def run_page(
    document: PdfiumDocument,
    index: int,
    truth: str,
    meta: tuple[str, str, str],
    args: argparse.Namespace,
    config: PdfReaderConfig,
    tesseract: TesseractEngine,
    rapid: Callable[[bytes], object] | None,
    pool: ThreadPoolExecutor,
) -> list[ComboPage]:
    set_name, group, file_name = meta
    rows: list[ComboPage] = []

    def add(engine: str, dpi: int, psm: int | None, text: str, ok: bool, seconds: float) -> None:
        rows.append(
            ComboPage(set_name, group, file_name, index + 1, engine, dpi, psm, ok, seconds, score_page(truth, text))
        )

    for dpi in args.dpi:
        image = document.render_gray(index, dpi, config.ocr_max_pixels)
        if args.degrade:
            rng = np.random.default_rng([args.seed, index, dpi])
            image = simulate_scan(image, args.blur * dpi / 300, args.noise, rng)
        png = encode_png_gray(image)
        actual_dpi = max(1, round(image.dpi))

        def timed(psm: int, png: bytes = png, actual_dpi: int = actual_dpi) -> tuple[int, str, bool, float]:
            started = time.perf_counter()
            text, ok = tesseract_text(tesseract, png, actual_dpi, psm, config)
            return psm, text, ok, time.perf_counter() - started

        for psm, text, ok, seconds in pool.map(timed, args.psm):
            add(TESSERACT, dpi, psm, text, ok, seconds)
        if rapid is not None:
            started = time.perf_counter()
            try:
                text, ok = rapidocr_text(rapid, png), True
            except (RuntimeError, ValueError, OSError, TypeError, LookupError, ArithmeticError):
                text, ok = "", False
            add(RAPIDOCR, dpi, None, text, ok, time.perf_counter() - started)
    return rows


# --------------------------------------------------------------------------- #
# Báo cáo
# --------------------------------------------------------------------------- #


def _select(pages: Sequence[ComboPage], **where: object) -> list[ComboPage]:
    return [p for p in pages if all(getattr(p, key) == value for key, value in where.items())]


def combos_of(pages: Sequence[ComboPage]) -> list[tuple[str, int, int | None]]:
    seen = {(p.engine, p.dpi, p.psm) for p in pages}
    return sorted(seen, key=lambda c: (c[0], c[1], -1 if c[2] is None else c[2]))


def summarize(pages: Sequence[ComboPage]) -> dict[str, dict[str, dict[str, object]]]:
    """`{nhóm: {khoá tổ hợp: chỉ số}}` cho nhóm `dev`, `blind` và từng bộ/tài liệu (`set:<tên>`, `doc:<tên>`)."""
    out: dict[str, dict[str, dict[str, object]]] = {}
    labels: dict[str, list[ComboPage]] = defaultdict(list)
    for page in pages:
        labels[page.group].append(page)
        labels[f"set:{page.set_name}"].append(page)
        labels[f"doc:{page.set_name}/{page.file}"].append(page)
    for label, members in labels.items():
        out[label] = {}
        for engine, dpi, psm in combos_of(members):
            rows = _select(members, engine=engine, dpi=dpi, psm=psm)
            out[label][combo_key(engine, dpi, psm)] = aggregate(
                [r.score for r in rows], [r.seconds for r in rows], sum(1 for r in rows if not r.ok)
            )
    return out


def _fmt(value: object, pattern: str = "{:.1%}") -> str:
    return "—" if not isinstance(value, float) else pattern.format(value)


def _table(block: dict[str, dict[str, object]]) -> list[str]:
    lines = [
        "| Tổ hợp | Trang | CER | CER trung vị | WER | CER dấu | Giữ dấu | Chữ số đúng | s/trang | Lỗi gọi |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for key, m in sorted(block.items()):
        lines.append(
            f"| {key} | {m['pages']} | {_fmt(m['cer_micro'])} | {_fmt(m['cer_median'])} | {_fmt(m['wer_micro'])} | "
            f"{_fmt(m['diacritic_cer'])} | {_fmt(m['diacritic_retention'])} | {_fmt(m['digit_accuracy_micro'])} | "
            f"{_fmt(m['seconds_median'], '{:.1f}')} | {m['failures']} |"
        )
    return lines


def _best(block: dict[str, dict[str, object]], engine: str) -> tuple[str, dict[str, object]] | None:
    candidates = [(k, m) for k, m in block.items() if k.startswith(engine + " ") and isinstance(m["cer_micro"], float)]
    if not candidates:
        return None
    return min(candidates, key=lambda item: float(str(item[1]["cer_micro"])))


def recommendation(summary: dict[str, dict[str, dict[str, object]]], degrade: bool) -> list[str]:
    """Khuyến nghị cấu hình bitmap (X11): chọn theo dev, đối chiếu mù; nhận định dấu của RapidOCR."""
    dev, blind = summary.get("dev", {}), summary.get("blind", {})
    lines: list[str] = []
    best_t, best_r = _best(dev, TESSERACT), _best(dev, RAPIDOCR)
    if best_t is not None:
        key, m = best_t
        b = blind.get(key)
        lines.append(
            f"- Tesseract tốt nhất trên dev: `{key}` (CER {_fmt(m['cer_micro'])}, giữ dấu {_fmt(m['diacritic_retention'])}, "
            f"{_fmt(m['seconds_median'], '{:.1f}')} s/trang); cùng tổ hợp trên mù: "
            + (f"CER {_fmt(b['cer_micro'])}, giữ dấu {_fmt(b['diacritic_retention'])}." if b else "không có số.")
        )
    if best_r is not None:
        key, m = best_r
        b = blind.get(key)
        lines.append(
            f"- RapidOCR (PP-OCRv6 small) tốt nhất trên dev: `{key}` (CER {_fmt(m['cer_micro'])}, giữ dấu "
            f"{_fmt(m['diacritic_retention'])}, CER dấu {_fmt(m['diacritic_cer'])}); trên mù: "
            + (f"CER {_fmt(b['cer_micro'])}, giữ dấu {_fmt(b['diacritic_retention'])}." if b else "không có số.")
        )
    if best_t is not None and best_r is not None:
        t_ret, r_ret = best_t[1]["diacritic_retention"], best_r[1]["diacritic_retention"]
        if isinstance(t_ret, float) and isinstance(r_ret, float):
            verdict = "kém hơn" if r_ret < t_ret else "không kém hơn"
            lines.append(
                f"- Dấu tiếng Việt: RapidOCR giữ dấu {verdict} Tesseract `vie` trên dev "
                f"({_fmt(r_ret)} so với {_fmt(t_ret)}); đối chiếu mù ở bảng mù. Số liệu chỉ đo trên tập này."
            )
    lines.append(
        "- Cấu hình đường bitmap (X11) nên chọn theo dòng dev tốt nhất ở trên, cân thời gian/trang; mù chỉ để xác "
        "nhận, không dùng để chỉnh."
    )
    if degrade:
        lines.append("- Có `--degrade`: số đo là scan mô phỏng (mờ + nhiễu Gauss), vẫn lạc quan hơn scan thật.")
    else:
        lines.append(
            "- GIỚI HẠN: không có `--degrade`, ảnh render sạch từ PDF số nên CER lạc quan hơn scan thật; chạy thêm "
            "`--degrade` trước khi chốt cho X11."
        )
    lines.append(
        "- GIỚI HẠN: đáp án là lớp chữ theo thứ tự đọc của PDFium; trang nhiều cột/bảng làm CER cao do lệch thứ tự, "
        "và chữ trong ảnh nhúng bị tính là lỗi chèn."
    )
    return lines


def markdown(report: Report, summary: dict[str, dict[str, dict[str, object]]]) -> str:
    lines = [
        "# Đo nền OCR tự sinh từ lớp chữ (X01)",
        "",
        (
            f"- DPI {report.dpis}, PSM {report.psms}, degrade = {report.degrade}, seed {report.seed}, "
            f"tối đa {report.max_pages} trang/bộ, đáp án ≥ {report.min_truth_chars} ký tự không trắng"
        ),
        f"- Trang đã chọn mỗi bộ: {report.selected}; trang loại (không dùng lớp chữ hoặc quá ít chữ): {report.skipped}",
        "",
        "## Khuyến nghị",
        "",
        *recommendation(summary, report.degrade),
        "",
    ]
    for label, title in (("dev", "Dev (arm_ch2)"), ("blind", "Mù (arm_ch3, arm_ch4, de_cuong)")):
        lines += [f"## {title}", "", *_table(summary.get(label, {})), ""]
    lines += ["## Theo tài liệu", ""]
    for label in sorted(k for k in summary if k.startswith("doc:")):
        lines += [f"### {label[4:]}", "", *_table(summary[label]), ""]
    return "\n".join(lines) + "\n"


def to_payload(report: Report, summary: dict[str, dict[str, dict[str, object]]]) -> dict[str, object]:
    pages = [
        {**{k: v for k, v in asdict(p).items() if k != "score"}, **asdict(p.score), "cer": p.score.cer}
        for p in report.pages
    ]
    meta = {k: v for k, v in asdict(report).items() if k != "pages"}
    return {"meta": meta, "summary": summary, "pages": pages}


def main(argv: list[str] | None = None) -> int:
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    if refuses_output_dir(args.out_dir):
        print(f"Từ chối ghi kết quả vào trong repo: {args.out_dir.resolve()}", file=sys.stderr)
        return 2
    config = PdfReaderConfig()
    tesseract = TesseractEngine.probe(load_ocr_settings(), config.ocr_probe_timeout_s)
    if tesseract is None:
        print("Không có Tesseract với gói `vie` (kiểm HOLOCRON_TESSERACT_CMD, HOLOCRON_TESSDATA_DIR).", file=sys.stderr)
        return 2
    rapid = build_rapidocr()
    if rapid is None:
        print("Không nạp được RapidOCR: bỏ qua engine này.", file=sys.stderr)
    root: Path = args.pdf_root
    sets = [d for d in sorted(root.iterdir()) if d.is_dir() and any(d.glob("*.pdf"))] if root.is_dir() else []
    if not sets and any(root.glob("*.pdf")):
        sets = [root]
    report = Report(str(root), args.dpi, args.psm, args.degrade, args.seed, args.max_pages, args.min_truth_chars)
    backend = PdfiumBackend.load()
    estimated = False
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        for directory in sets:
            set_name = directory.name
            group = "dev" if set_name == DEV_SET else "blind"
            candidates: list[tuple[Path, int, str]] = []
            skipped = 0
            for path in sorted(directory.glob("*.pdf")):
                document = backend.open(path.read_bytes())
                if isinstance(document, str):
                    skipped += 1
                    continue
                with document:
                    eligible, dropped = eligible_pages(document, config, args.min_truth_chars)
                skipped += dropped
                candidates += [(path, index, truth) for index, truth in eligible]
            chosen = [candidates[i] for i in sample_evenly(list(range(len(candidates))), args.max_pages)]
            report.selected[set_name] = len(chosen)
            report.skipped[set_name] = skipped
            print(f"{set_name}: {len(chosen)} trang đã chọn / {len(candidates)} đủ điều kiện ({skipped} loại)")
            by_file: dict[Path, list[tuple[int, str]]] = defaultdict(list)
            for path, index, truth in chosen:
                by_file[path].append((index, truth))
            for path, items in by_file.items():
                document = backend.open(path.read_bytes())
                if isinstance(document, str):
                    continue
                with document:
                    for index, truth in items:
                        started = time.perf_counter()
                        report.pages += run_page(
                            document, index, truth, (set_name, group, path.name), args, config, tesseract, rapid, pool
                        )
                        if not estimated:
                            estimated = True
                            per_page = time.perf_counter() - started
                            total = len(chosen)
                            print(
                                f"Ước tính: {per_page:.0f} s/trang (trang đầu), cả bộ {set_name} ~ {per_page * total / 60:.0f} phút"
                            )
    summary = summarize(report.pages)
    out_dir = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(
        json.dumps(to_payload(report, summary), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "results.md").write_text(markdown(report, summary), encoding="utf-8")
    print(f"Đã ghi {out_dir / 'results.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
