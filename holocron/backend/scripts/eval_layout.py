"""Đánh giá opt-in: khung đo bố cục trang cho GĐ2 (spec v0.6 §11.5).

KHÔNG chạy trong pytest, CI hay pre-commit. Không gọi mạng. Chỉ dùng thư viện chuẩn. Bộ sinh khối của GĐ2 CHƯA có:
lúc này script chỉ chạy được với file dự đoán TỔNG HỢP (ví dụ do `--write-oracle` sinh từ chính ground truth, điểm
phải gần tuyệt đối) để kiểm khung đo. Không in nội dung tài liệu: stdout chỉ có số.

Chạy trong `holocron/backend`:

    python -m scripts.eval_layout --gt-root <thư mục chứa arm_ch*/> --pred <file dự đoán JSON> \\
        [--iou 0.5] [--dev ch2] [--output <file JSON kết quả, ngoài repo>]
    python -m scripts.eval_layout --gt-root <...> --write-oracle <file dự đoán tổng hợp, ngoài repo>

Ground truth: mọi `*.json` trong `<gt-root>/arm_ch*/` (chương = hậu tố `chN` của tên thư mục), dạng
`{document, page_count, pages: [{page, source, topic, traps, blocks: [{order, type, text}]}]}`. KHÔNG có bbox và KHÔNG
có `unit_id`. Nội dung ground truth không được chép vào repo hay test.

Tập loại khối = đúng tập của ground truth: title, list, figure, code, paragraph, table, caption, header, page_number.
Loại lạ (ở ground truth hay dự đoán) ⇒ lỗi.

File dự đoán (do script này định nghĩa):

    {"chapters": {"ch2": {"pages": [{"page": 1, "blocks": [
        {"type": "title", "text": "...", "order": 0, "unit_id": "u1", "bbox": [x0, y0, x1, y1]}]}]}}}

`order` (mặc định: vị trí trong danh sách), `unit_id` và `bbox` là tuỳ chọn; bbox chuẩn hoá 0..1 NHƯNG KHÔNG dùng để
chấm (ground truth không có bbox).

Các phép đo (mọi chuỗi qua NFC và gộp khoảng trắng trước khi so):
- Ranh giới khối: văn bản trang của ground truth = các khối (theo `order`) nối bằng một dấu cách; khối có khoảng ký tự
  `[start, end)` trong văn bản đó. Khối dự đoán được định vị vào văn bản trang này bằng khớp chuỗi con chung dài nhất
  (`difflib`; khớp đúng thì chính xác), rồi tính khoảng ký tự tương ứng. Khớp một-một THAM LAM theo IoU khoảng ký tự
  giảm dần, chỉ nhận cặp có IoU >= `--iou` (mặc định 0.5). Báo precision, recall, F1 ranh giới (gộp mọi trang).
- CER theo khối: trên cặp khối khớp, tổng khoảng cách Levenshtein / tổng số ký tự của ground truth.
- Độ chính xác loại khối: tỉ lệ cặp khớp có cùng `type`.
- Kendall tau thứ tự đọc: trên cặp khớp của từng trang so `order` ground truth với `order` dự đoán; trang < 2 cặp khớp
  không có tau (bỏ qua, báo số trang dùng); báo trung bình theo trang.
- V-measure nhóm chủ đề (beta = 1): mỗi TRANG là một phần tử; nhóm ground truth = `topic` cấp trang; nhóm dự đoán =
  `unit_id` của khối dài nhất trên trang (trang không có unit_id ⇒ nhóm riêng "không nhãn"; trang không có trong dự
  đoán ⇒ nhóm riêng "thiếu trang", được đếm). Một nhóm duy nhất ở cả hai phía ⇒ 1.0.

Hai chế độ: `no_chrome` (mặc định, bỏ khối `header` và `page_number` ở cả hai phía, giống eval_concept_proposal.py) và
`all` (giữ hết). Báo theo từng chương, rồi gộp "dev" (`--dev`, mặc định ch2) và "mù" (mọi chương còn lại, ví dụ
ch3+ch4) tách riêng; KHÔNG chỉnh ngưỡng hay phương pháp theo kết quả tập mù.
"""

from __future__ import annotations

import argparse
import difflib
import io
import json
import math
import subprocess
import sys
import unicodedata
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

BLOCK_TYPES = frozenset({"title", "list", "figure", "code", "paragraph", "table", "caption", "header", "page_number"})
CHROME_TYPES = frozenset({"header", "page_number"})
DEFAULT_IOU = 0.5
MODES = ("no_chrome", "all")
MISSING_PAGE_GROUP = "\x00thieu-trang"
NO_UNIT_GROUP = "\x00khong-nhan"


class EvalLayoutError(Exception):
    """Lỗi đầu vào của script (thông điệp an toàn để in, không chứa nội dung tài liệu)."""


@dataclass(frozen=True)
class Block:
    type: str
    text: str
    order: int
    unit_id: str | None = None


@dataclass(frozen=True)
class Page:
    page: int
    blocks: tuple[Block, ...]
    topic: str | None = None


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


def interval_iou(a: tuple[int, int], b: tuple[int, int]) -> float:
    inter = max(0, min(a[1], b[1]) - max(a[0], b[0]))
    union = (a[1] - a[0]) + (b[1] - b[0]) - inter
    return inter / union if union > 0 else 0.0


def gt_spans(blocks: Sequence[Block]) -> tuple[str, list[tuple[int, int]]]:
    """Văn bản trang (khối theo `order` nối bằng dấu cách) và khoảng `[start, end)` của từng khối (theo thứ tự đưa vào)."""
    ordered = sorted(range(len(blocks)), key=lambda i: blocks[i].order)
    spans: list[tuple[int, int]] = [(0, 0)] * len(blocks)
    cursor = 0
    parts: list[str] = []
    for index in ordered:
        text = normalize(blocks[index].text)
        if parts:
            cursor += 1
        spans[index] = (cursor, cursor + len(text))
        parts.append(text)
        cursor += len(text)
    return " ".join(parts), spans


def locate(page_text: str, text: str) -> tuple[int, int]:
    """Khoảng ký tự ước lượng của `text` trong `page_text` theo khớp chuỗi con chung dài nhất; không khớp ⇒ rỗng."""
    block = normalize(text)
    if not block or not page_text:
        return (0, 0)
    found = page_text.find(block)
    if found >= 0:
        return (found, found + len(block))
    match = difflib.SequenceMatcher(None, page_text, block, autojunk=False).find_longest_match(
        0, len(page_text), 0, len(block)
    )
    if match.size == 0:
        return (0, 0)
    start = match.a - match.b
    return (start, start + len(block))


def greedy_match(
    gt_blocks: Sequence[tuple[int, int]], pred_blocks: Sequence[tuple[int, int]], threshold: float
) -> list[tuple[int, int]]:
    """Khớp một-một tham lam theo IoU giảm dần (hoà: chỉ số nhỏ trước); chỉ nhận IoU >= `threshold`."""
    candidates = [(interval_iou(g, p), gi, pi) for gi, g in enumerate(gt_blocks) for pi, p in enumerate(pred_blocks)]
    candidates.sort(key=lambda item: (-item[0], item[1], item[2]))
    used_g: set[int] = set()
    used_p: set[int] = set()
    pairs: list[tuple[int, int]] = []
    for iou, gi, pi in candidates:
        if iou < threshold or iou <= 0.0:
            break
        if gi in used_g or pi in used_p:
            continue
        used_g.add(gi)
        used_p.add(pi)
        pairs.append((gi, pi))
    return pairs


def kendall_tau(pairs: Sequence[tuple[int, int]]) -> float | None:
    """Kendall tau-a giữa hai danh sách thứ tự `(a, b)`; ít hơn 2 phần tử ⇒ `None`; cặp hoà tính là 0."""
    n = len(pairs)
    if n < 2:
        return None
    score = 0
    for i in range(n):
        for j in range(i + 1, n):
            da = pairs[i][0] - pairs[j][0]
            db = pairs[i][1] - pairs[j][1]
            product = da * db
            score += (product > 0) - (product < 0)
    return score / (n * (n - 1) / 2)


def _entropy(counts: Sequence[int]) -> float:
    total = sum(counts)
    return -sum(c / total * math.log(c / total) for c in counts if c > 0) if total else 0.0


def v_measure(truth: Sequence[str], predicted: Sequence[str]) -> float:
    """V-measure (beta = 1) giữa hai cách gán nhóm; rỗng hoặc một nhóm duy nhất ở cả hai phía ⇒ 1.0."""
    if len(truth) != len(predicted):
        raise ValueError("hai danh sách nhóm phải cùng độ dài")
    n = len(truth)
    if n == 0:
        return 1.0
    h_c = _entropy(list(Counter(truth).values()))
    h_k = _entropy(list(Counter(predicted).values()))
    joint = Counter(zip(truth, predicted, strict=True))
    h_c_given_k = 0.0
    h_k_given_c = 0.0
    by_k = Counter(predicted)
    by_c = Counter(truth)
    for (c, k), count in joint.items():
        h_c_given_k -= count / n * math.log(count / by_k[k])
        h_k_given_c -= count / n * math.log(count / by_c[c])
    homogeneity = 1.0 if h_c == 0 else 1 - h_c_given_k / h_c
    completeness = 1.0 if h_k == 0 else 1 - h_k_given_c / h_k
    if homogeneity + completeness == 0:
        return 0.0
    return 2 * homogeneity * completeness / (homogeneity + completeness)


def _check_type(kind: object, where: str) -> str:
    if not isinstance(kind, str) or kind not in BLOCK_TYPES:
        raise EvalLayoutError(f"loại khối lạ ở {where}")
    return kind


def _blocks_from(raw: object, where: str) -> tuple[Block, ...]:
    if not isinstance(raw, list):
        raise EvalLayoutError(f"{where}: `blocks` phải là danh sách")
    blocks: list[Block] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise EvalLayoutError(f"{where}: khối {index} không phải đối tượng")
        text = item.get("text")
        order = item.get("order", index)
        unit = item.get("unit_id")
        if not isinstance(text, str) or not isinstance(order, int) or isinstance(order, bool):
            raise EvalLayoutError(f"{where}: khối {index} thiếu `text` chuỗi hoặc `order` số nguyên")
        if unit is not None and not isinstance(unit, str):
            raise EvalLayoutError(f"{where}: `unit_id` của khối {index} phải là chuỗi")
        blocks.append(Block(_check_type(item.get("type"), f"{where} khối {index}"), text, order, unit))
    return tuple(blocks)


def parse_pages(raw: object, where: str, *, ground_truth: bool) -> dict[int, Page]:
    if not isinstance(raw, list):
        raise EvalLayoutError(f"{where}: `pages` phải là danh sách")
    pages: dict[int, Page] = {}
    for item in raw:
        if not isinstance(item, dict) or not isinstance(item.get("page"), int):
            raise EvalLayoutError(f"{where}: trang thiếu `page` số nguyên")
        number = item["page"]
        topic = item.get("topic")
        if ground_truth and not isinstance(topic, str):
            raise EvalLayoutError(f"{where}: trang {number} thiếu `topic` chuỗi")
        if number in pages:
            raise EvalLayoutError(f"{where}: trang {number} lặp")
        pages[number] = Page(number, _blocks_from(item.get("blocks"), f"{where} trang {number}"), topic)
    return pages


def load_ground_truth(gt_root: Path) -> dict[str, dict[int, Page]]:
    chapters: dict[str, dict[int, Page]] = {}
    for directory in sorted(gt_root.glob("arm_ch*")):
        if not directory.is_dir():
            continue
        chapter = directory.name.removeprefix("arm_")
        pages: dict[int, Page] = {}
        for file in sorted(directory.glob("*.json")):
            data = json.loads(file.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise EvalLayoutError(f"ground truth {chapter}: gốc phải là đối tượng")
            for number, page in parse_pages(data.get("pages"), f"ground truth {chapter}", ground_truth=True).items():
                if number in pages:
                    raise EvalLayoutError(f"ground truth {chapter}: trang {number} lặp giữa các file")
                pages[number] = page
        if pages:
            chapters[chapter] = pages
    if not chapters:
        raise EvalLayoutError("không thấy ground truth nào trong --gt-root (cần arm_ch*/*.json)")
    return chapters


def load_prediction(path: Path) -> dict[str, dict[int, Page]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    chapters = data.get("chapters") if isinstance(data, dict) else None
    if not isinstance(chapters, dict):
        raise EvalLayoutError("file dự đoán phải có `chapters` là đối tượng")
    result: dict[str, dict[int, Page]] = {}
    for chapter, body in chapters.items():
        if not isinstance(body, dict):
            raise EvalLayoutError(f"dự đoán {chapter}: phải là đối tượng có `pages`")
        result[str(chapter)] = parse_pages(body.get("pages"), f"dự đoán {chapter}", ground_truth=False)
    return result


@dataclass
class Tally:
    """Bộ cộng dồn số đếm; gộp nhiều chương bằng `merge`."""

    true_positive: int = 0
    gt_blocks: int = 0
    pred_blocks: int = 0
    type_correct: int = 0
    edits: int = 0
    gt_chars: int = 0
    taus: list[float] = field(default_factory=list)
    truth_groups: list[str] = field(default_factory=list)
    pred_groups: list[str] = field(default_factory=list)
    missing_pages: int = 0
    pages: int = 0

    def merge(self, other: Tally) -> None:
        self.true_positive += other.true_positive
        self.gt_blocks += other.gt_blocks
        self.pred_blocks += other.pred_blocks
        self.type_correct += other.type_correct
        self.edits += other.edits
        self.gt_chars += other.gt_chars
        self.taus += other.taus
        self.truth_groups += other.truth_groups
        self.pred_groups += other.pred_groups
        self.missing_pages += other.missing_pages
        self.pages += other.pages


def _keep(blocks: Sequence[Block], mode: str) -> list[Block]:
    return [b for b in blocks if mode == "all" or b.type not in CHROME_TYPES]


def _pred_group(blocks: Sequence[Block]) -> str:
    labelled = [b for b in blocks if b.unit_id]
    if not labelled:
        return NO_UNIT_GROUP
    longest = max(labelled, key=lambda b: len(normalize(b.text)))
    return longest.unit_id or NO_UNIT_GROUP


def evaluate_chapter(chapter: str, truth: dict[int, Page], pred: dict[int, Page], mode: str, iou: float) -> Tally:
    tally = Tally()
    for number in sorted(truth):
        gt_page = truth[number]
        gt_blocks = _keep(gt_page.blocks, mode)
        tally.pages += 1
        tally.truth_groups.append(f"{chapter}:{gt_page.topic}")
        pred_page = pred.get(number)
        if pred_page is None:
            tally.missing_pages += 1
            tally.gt_blocks += len(gt_blocks)
            tally.pred_groups.append(f"{chapter}:{MISSING_PAGE_GROUP}")
            continue
        pred_blocks = _keep(pred_page.blocks, mode)
        tally.pred_groups.append(f"{chapter}:{_pred_group(pred_blocks)}")
        page_text, gt_intervals = gt_spans(gt_blocks)
        pred_intervals = [locate(page_text, b.text) for b in pred_blocks]
        pairs = greedy_match(gt_intervals, pred_intervals, iou)
        tally.true_positive += len(pairs)
        tally.gt_blocks += len(gt_blocks)
        tally.pred_blocks += len(pred_blocks)
        order_pairs: list[tuple[int, int]] = []
        for gi, pi in pairs:
            g, p = gt_blocks[gi], pred_blocks[pi]
            gt_text, pred_text = normalize(g.text), normalize(p.text)
            tally.edits += levenshtein(pred_text, gt_text)
            tally.gt_chars += len(gt_text)
            tally.type_correct += int(g.type == p.type)
            order_pairs.append((g.order, p.order))
        tau = kendall_tau(order_pairs)
        if tau is not None:
            tally.taus.append(tau)
    # Trang chỉ có ở dự đoán: đếm khối thừa để precision phản ánh đúng.
    for number, extra in pred.items():
        if number not in truth:
            tally.pred_blocks += len(_keep(extra.blocks, mode))
    return tally


def _ratio(num: float, den: float) -> float | None:
    return num / den if den > 0 else None


def summarize(tally: Tally) -> dict[str, float | int | None]:
    precision = _ratio(tally.true_positive, tally.pred_blocks)
    recall = _ratio(tally.true_positive, tally.gt_blocks)
    f1 = _ratio(2 * tally.true_positive, tally.pred_blocks + tally.gt_blocks)
    return {
        "pages": tally.pages,
        "missing_pages": tally.missing_pages,
        "gt_blocks": tally.gt_blocks,
        "pred_blocks": tally.pred_blocks,
        "matched": tally.true_positive,
        "boundary_precision": precision,
        "boundary_recall": recall,
        "boundary_f1": f1,
        "cer": _ratio(tally.edits, tally.gt_chars),
        "type_accuracy": _ratio(tally.type_correct, tally.true_positive),
        "kendall_tau_mean": _ratio(sum(tally.taus), len(tally.taus)),
        "kendall_tau_pages": len(tally.taus),
        "v_measure": v_measure(tally.truth_groups, tally.pred_groups) if tally.pages else None,
    }


def evaluate(
    truth: dict[str, dict[int, Page]],
    pred: dict[str, dict[int, Page]],
    iou: float = DEFAULT_IOU,
    dev: Sequence[str] = ("ch2",),
) -> dict[str, dict[str, dict[str, float | int | None]]]:
    """Kết quả `{mode: {"ch2": {...}, "dev": {...}, "blind": {...}}}`; chương thiếu trong dự đoán tính như thiếu trang."""
    report: dict[str, dict[str, dict[str, float | int | None]]] = {}
    for mode in MODES:
        per: dict[str, dict[str, float | int | None]] = {}
        dev_tally, blind_tally = Tally(), Tally()
        for chapter in sorted(truth):
            tally = evaluate_chapter(chapter, truth[chapter], pred.get(chapter, {}), mode, iou)
            per[chapter] = summarize(tally)
            (dev_tally if chapter in dev else blind_tally).merge(tally)
        per["dev"] = summarize(dev_tally)
        per["blind"] = summarize(blind_tally)
        report[mode] = per
    return report


def oracle_prediction(truth: dict[str, dict[int, Page]]) -> dict[str, Any]:
    """Dự đoán tổng hợp từ chính ground truth (`unit_id` = `topic`): dùng kiểm khung đo, điểm phải tuyệt đối."""
    return {
        "chapters": {
            chapter: {
                "pages": [
                    {
                        "page": number,
                        "blocks": [
                            {"type": b.type, "text": b.text, "order": b.order, "unit_id": page.topic}
                            for b in page.blocks
                        ],
                    }
                    for number, page in sorted(pages.items())
                ]
            }
            for chapter, pages in truth.items()
        }
    }


def repo_root() -> Path:
    here = Path(__file__).resolve().parent
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=here,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
            timeout=10,
        ).stdout.strip()
        if out:
            return Path(out).resolve()
    except (OSError, subprocess.SubprocessError):
        pass
    return here.parents[2]


def require_outside_repo(path: Path, flag: str) -> None:
    if path.resolve().is_relative_to(repo_root()):
        raise EvalLayoutError(f"{flag} phải nằm ngoài cây repo (không ghi dữ liệu đánh giá vào repo)")


def format_report(report: dict[str, dict[str, dict[str, float | int | None]]]) -> str:
    def cell(value: float | None) -> str:
        if value is None:
            return "n/a"
        return str(value) if isinstance(value, int) else f"{value:.3f}"

    lines: list[str] = []
    for mode, per in report.items():
        lines.append(f"[chế độ {mode}]")
        for group, metrics in per.items():
            lines.append(f"  {group}: " + ", ".join(f"{name}={cell(value)}" for name, value in metrics.items()))
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Đo bố cục trang so với ground truth (khung cho GĐ2).")
    parser.add_argument("--gt-root", type=Path, required=True, help="thư mục chứa arm_ch*/*.json (ground truth)")
    parser.add_argument("--pred", type=Path, help="file dự đoán JSON (schema trong docstring)")
    parser.add_argument("--iou", type=float, default=DEFAULT_IOU, help="ngưỡng IoU khoảng ký tự (mặc định 0.5)")
    parser.add_argument("--dev", action="append", help="chương thuộc tập dev (lặp được; mặc định ch2)")
    parser.add_argument("--output", type=Path, help="ghi kết quả (chỉ số) ra JSON, ngoài repo")
    parser.add_argument("--write-oracle", type=Path, help="ghi file dự đoán tổng hợp từ ground truth rồi thoát")
    return parser


def run(args: argparse.Namespace) -> int:
    if not 0.0 < args.iou <= 1.0:
        raise EvalLayoutError("--iou phải trong (0, 1]")
    truth = load_ground_truth(args.gt_root)
    if args.write_oracle is not None:
        require_outside_repo(args.write_oracle, "--write-oracle")
        args.write_oracle.parent.mkdir(parents=True, exist_ok=True)
        args.write_oracle.write_text(json.dumps(oracle_prediction(truth), ensure_ascii=False), encoding="utf-8")
        print(f"đã ghi dự đoán tổng hợp cho {len(truth)} chương: {args.write_oracle}")
        return 0
    if args.pred is None:
        raise EvalLayoutError("cần --pred (hoặc --write-oracle)")
    if args.output is not None:
        require_outside_repo(args.output, "--output")
    report = evaluate(truth, load_prediction(args.pred), args.iou, tuple(args.dev or ["ch2"]))
    print(format_report(report))
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"đã ghi: {args.output}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")
    try:
        return run(build_parser().parse_args(argv))
    except (EvalLayoutError, json.JSONDecodeError, OSError) as exc:
        print(
            f"lỗi: {type(exc).__name__}: {exc}" if not isinstance(exc, EvalLayoutError) else f"lỗi: {exc}",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
