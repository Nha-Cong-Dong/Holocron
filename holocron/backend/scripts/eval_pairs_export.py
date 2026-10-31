"""Đánh giá opt-in: xuất mẫu cặp khái niệm để người chấm tay, rồi tính precision (spec v0.6 §11.5; GĐ1).

KHÔNG chạy trong pytest, CI hay pre-commit. Không gọi mạng, không gọi LLM. Không in nội dung cặp ra stdout (chỉ số
đếm và đường dẫn file); file xuất PHẢI nằm ngoài cây repo (script từ chối đường dẫn trong repo).

Xuất mẫu (trong `holocron/backend`):

    python -m scripts.eval_pairs_export --data-dir <HOLOCRON_DATA_DIR> --subject-code IT3070 [--chapter-code ch2] \\
        [--count 40] [--seed 20261031] --output <đường dẫn ngoài repo, không đuôi hoặc đuôi .md/.csv>

Ghi hai file cạnh nhau, `<output>.md` (bảng Markdown) và `<output>.csv` (UTF-8 có BOM), cùng các cột:
`term | trích | trang | pre_selected | failed_gates | đúng?`.
- `trích`: `definition_quote` của cặp, cắt theo KÝ TỰ tới 120 ký tự, thêm "…" khi bị cắt.
- `trang`: lấy từ chunk nguồn của cặp (`get_chunk(pair.chunk_id)`): `page_number`, hoặc "a–b" khi chunk có `page_end`;
  chunk không còn trong kho ⇒ ô rỗng và đếm cảnh báo.
- `pre_selected`: `true`/`false`; `failed_gates`: các cổng trượt, cách nhau bằng dấu phẩy.
- `đúng?`: cột trống để người chấm điền.
- Chọn mẫu: sắp cặp theo `pair_id`, rồi `random.Random(seed).sample` N cặp (tái lập theo seed).

Kho SQLite chỉ được ĐỌC: `SQLiteStore.open` chạy migrate và bật WAL nên script KHÔNG mở kho thật. Nó kiểm file
`holocron.sqlite3` trong `--data-dir` tồn tại (không có thì lỗi, không tạo mới), COPY file (cùng `-wal`, `-shm` nếu có)
sang thư mục tạm rồi mở bản sao.

Chấm điểm:

    python -m scripts.eval_pairs_export --score <file CSV đã chấm>

- Cột `đúng?` nhận 1/0 (cũng nhận y/n, đ/s, true/false); ô trống = chưa chấm (bỏ khỏi mẫu, báo số). Thiếu cột ⇒ lỗi.
- In n, số đúng, precision và khoảng tin cậy Wilson 95% cho tập `pre_selected = true` và cho toàn bộ cặp đã chấm;
  n = 0 ⇒ "n/a"; n < 30 ⇒ cảnh báo mẫu nhỏ. Không có ngưỡng đạt/trượt cứng trong script.
"""

from __future__ import annotations

import argparse
import csv
import io
import math
import random
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from holocron_core.domain.vault import ConceptPair
from holocron_core.infrastructure.storage import SQLiteStore
from holocron_core.infrastructure.storage.factory import DATABASE_FILENAME

DEFAULT_COUNT = 40
DEFAULT_SEED = 20261031
EXCERPT_MAX_CHARS = 120
SMALL_SAMPLE = 30
WILSON_Z = 1.96
JUDGED_COLUMN = "đúng?"
COLUMNS = ("term", "trích", "trang", "pre_selected", "failed_gates", JUDGED_COLUMN)
TRUE_TOKENS = frozenset({"1", "y", "yes", "true", "đ", "đúng"})
FALSE_TOKENS = frozenset({"0", "n", "no", "false", "s", "sai"})


class EvalPairsError(Exception):
    """Lỗi đầu vào của script (thông điệp an toàn để in)."""


@dataclass(frozen=True)
class ExportRow:
    term: str
    excerpt: str
    page: str
    pre_selected: bool
    failed_gates: str

    def cells(self) -> list[str]:
        return [self.term, self.excerpt, self.page, "true" if self.pre_selected else "false", self.failed_gates, ""]


def repo_root() -> Path:
    """Gốc git chứa script; không chạy được `git` thì suy từ vị trí file."""
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


def inside_repo(path: Path, root: Path) -> bool:
    return path.resolve().is_relative_to(root.resolve())


def truncate_excerpt(text: str, limit: int = EXCERPT_MAX_CHARS) -> str:
    flat = " ".join(text.split())
    if len(flat) <= limit:
        return flat
    return flat[:limit] + "…"


def page_label(page_number: int, page_end: int | None) -> str:
    if page_end is not None and page_end != page_number:
        return f"{page_number}–{page_end}"
    return str(page_number)


def select_sample(pairs: Sequence[ConceptPair], count: int, seed: int) -> list[ConceptPair]:
    """Sắp theo `pair_id` rồi chọn ngẫu nhiên `count` cặp bằng `random.Random(seed)`; ít hơn thì lấy hết."""
    ordered = sorted(pairs, key=lambda pair: pair.pair_id)
    rng = random.Random(seed)
    return rng.sample(ordered, min(count, len(ordered)))


def read_rows(
    data_dir: Path, subject_code: str, chapter_code: str | None, count: int, seed: int
) -> tuple[list[ExportRow], int, int]:
    """Đọc cặp từ BẢN SAO của kho; trả `(hàng, số cặp khớp bộ lọc, số chunk thiếu)`."""
    source = data_dir / DATABASE_FILENAME
    if not source.is_file():
        raise EvalPairsError(f"không thấy file kho dữ liệu trong --data-dir (cần {DATABASE_FILENAME})")
    with tempfile.TemporaryDirectory(prefix="holocron-pairs-") as tmp:
        target = Path(tmp) / DATABASE_FILENAME
        shutil.copy2(source, target)
        for suffix in ("-wal", "-shm"):
            sidecar = source.with_name(source.name + suffix)
            if sidecar.is_file():
                shutil.copy2(sidecar, target.with_name(target.name + suffix))
        store = SQLiteStore.open(target)
        try:
            pairs = store.list_concept_pairs(subject_code, chapter_code)
            chosen = select_sample(pairs, count, seed)
            rows: list[ExportRow] = []
            missing = 0
            for pair in chosen:
                chunk = store.get_chunk(pair.chunk_id)
                if chunk is None:
                    missing += 1
                    page = ""
                else:
                    page = page_label(chunk.page_number, chunk.page_end)
                rows.append(
                    ExportRow(
                        term=pair.term,
                        excerpt=truncate_excerpt(pair.definition_quote),
                        page=page,
                        pre_selected=pair.pre_selected,
                        failed_gates=",".join(pair.failed_gates),
                    )
                )
        finally:
            store.close()
    return rows, len(pairs), missing


def _md_cell(value: str) -> str:
    return value.replace("\\", "\\\\").replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def render_markdown(rows: Sequence[ExportRow]) -> str:
    lines = ["| " + " | ".join(COLUMNS) + " |", "|" + "---|" * len(COLUMNS)]
    for row in rows:
        lines.append("| " + " | ".join(_md_cell(cell) for cell in row.cells()) + " |")
    return "\n".join(lines) + "\n"


def render_csv(rows: Sequence[ExportRow]) -> str:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(COLUMNS)
    for row in rows:
        writer.writerow(row.cells())
    return buffer.getvalue()


def output_paths(output: Path) -> tuple[Path, Path]:
    stem = output.with_suffix("") if output.suffix.lower() in {".md", ".csv"} else output
    return stem.with_name(stem.name + ".md"), stem.with_name(stem.name + ".csv")


def check_output_outside_repo(output: Path, root: Path) -> tuple[Path, Path]:
    md_path, csv_path = output_paths(output)
    if inside_repo(md_path, root) or inside_repo(csv_path, root):
        raise EvalPairsError("--output phải nằm ngoài cây repo (không ghi dữ liệu đánh giá vào repo)")
    return md_path, csv_path


def write_outputs(rows: Sequence[ExportRow], output: Path, root: Path) -> tuple[Path, Path]:
    md_path, csv_path = check_output_outside_repo(output, root)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(render_markdown(rows), encoding="utf-8", newline="\n")
    csv_path.write_text(render_csv(rows), encoding="utf-8-sig", newline="")
    return md_path, csv_path


def wilson_interval(successes: int, total: int, z: float = WILSON_Z) -> tuple[float, float] | None:
    """Khoảng tin cậy Wilson; `total = 0` ⇒ `None` (không chia 0)."""
    if total <= 0:
        return None
    p = successes / total
    denom = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denom
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def parse_judgement(raw: str) -> bool | None:
    token = raw.strip().lower()
    if not token:
        return None
    if token in TRUE_TOKENS:
        return True
    if token in FALSE_TOKENS:
        return False
    raise EvalPairsError(f"giá trị không hiểu ở cột {JUDGED_COLUMN}: {raw.strip()!r} (dùng 1/0, y/n hoặc đ/s)")


@dataclass(frozen=True)
class ScoreReport:
    graded: int
    ungraded: int
    selected_n: int
    selected_correct: int
    all_correct: int


def score_csv(text: str) -> ScoreReport:
    reader = csv.DictReader(io.StringIO(text, newline=""))
    fields = reader.fieldnames or []
    for needed in ("pre_selected", JUDGED_COLUMN):
        if needed not in fields:
            raise EvalPairsError(f"file CSV thiếu cột {needed!r}")
    graded = ungraded = selected_n = selected_correct = all_correct = 0
    for record in reader:
        verdict = parse_judgement(record.get(JUDGED_COLUMN) or "")
        if verdict is None:
            ungraded += 1
            continue
        graded += 1
        all_correct += int(verdict)
        flag = (record.get("pre_selected") or "").strip().lower()
        if flag not in ("true", "false"):
            raise EvalPairsError("cột pre_selected chỉ nhận true hoặc false (không phân biệt hoa thường)")
        if flag == "true":
            selected_n += 1
            selected_correct += int(verdict)
    return ScoreReport(graded, ungraded, selected_n, selected_correct, all_correct)


def _describe(label: str, correct: int, total: int) -> list[str]:
    interval = wilson_interval(correct, total)
    if interval is None:
        return [f"{label}: n=0, precision n/a"]
    summary = (
        f"{label}: n={total}, đúng={correct}, precision={correct / total:.3f}, "
        f"Wilson 95% [{interval[0]:.3f}, {interval[1]:.3f}]"
    )
    out = [summary]
    if total < SMALL_SAMPLE:
        out.append(f"  cảnh báo: n={total} < {SMALL_SAMPLE}, mẫu nhỏ, khoảng tin cậy rộng")
    return out


def format_report(report: ScoreReport) -> str:
    lines = [f"đã chấm: {report.graded}; chưa chấm (bỏ khỏi mẫu): {report.ungraded}"]
    lines += _describe("pre_selected = true", report.selected_correct, report.selected_n)
    lines += _describe("toàn bộ", report.all_correct, report.graded)
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Xuất mẫu cặp khái niệm để chấm tay và tính precision.")
    parser.add_argument("--score", type=Path, help="file CSV đã chấm: tính precision rồi thoát")
    parser.add_argument("--data-dir", type=Path, help="thư mục dữ liệu chứa holocron.sqlite3 (chỉ đọc bản sao)")
    parser.add_argument("--subject-code")
    parser.add_argument("--chapter-code")
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--output", type=Path, help="đường dẫn file ra (ngoài repo); ghi .md và .csv")
    return parser


def run(args: argparse.Namespace) -> int:
    if args.score is not None:
        if not args.score.is_file():
            raise EvalPairsError("không thấy file CSV cần chấm")
        print(format_report(score_csv(args.score.read_text(encoding="utf-8-sig"))))
        return 0
    if args.data_dir is None or args.subject_code is None or args.output is None:
        raise EvalPairsError("cần --data-dir, --subject-code và --output (hoặc --score)")
    if args.count < 1:
        raise EvalPairsError("--count phải >= 1")
    root = repo_root()
    check_output_outside_repo(args.output, root)
    rows, matched, missing = read_rows(args.data_dir, args.subject_code, args.chapter_code, args.count, args.seed)
    md_path, csv_path = write_outputs(rows, args.output, root)
    print(f"cặp khớp bộ lọc: {matched}; đã chọn: {len(rows)}; seed: {args.seed}")
    if missing:
        print(f"cảnh báo: {missing} cặp có chunk nguồn không còn trong kho (cột trang để trống)")
    print(f"đã ghi: {md_path}")
    print(f"đã ghi: {csv_path}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")
    try:
        return run(build_parser().parse_args(argv))
    except EvalPairsError as exc:
        print(f"lỗi: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
