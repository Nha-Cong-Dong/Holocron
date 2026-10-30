"""Đo chương tự động của bộ nhập trên tài liệu thật, KHÔNG có LLM, opt-in (SPECIFICATION §5.7 "Chương tự động").

Chạy NGOÀI pytest, không chạy trong CI hay pre-commit; không gọi mạng.

    cd holocron/backend
    python -m scripts.eval_chapters <thư mục kết quả> <pdf dev> [<pdf dev> ...] [--blind <pdf mù> ...]

Mỗi PDF được nhập lần lượt vào CÙNG một môn qua `services.document_import.accept_document` (kho in-memory, bộ đọc
`WorkerPdfReader` không OCR, không gửi `chapter_code`), đúng thứ tự trên dòng lệnh: tài liệu dev trước, rồi tài liệu
mù. Hàng đợi đọc của script không đọc trang nào: chương được chọn lúc nhận file (202), không phụ thuộc nội dung trang.

Báo cho từng file: `chapter_code` nhận được (đã lưu trong kho), nguồn và cờ của nó (tính lại bằng đúng hàm của
service trên các chương đã có trước file đó), tên chương theo luật tất định của §5.7 lúc nhận file (tên file đã làm
sạch; nhánh này chưa có nguồn ứng viên header lặp) và `chapter_code` đích lấy từ tên thư mục cha `..._chN` nếu có.
Kết quả chỉ gồm tên file, mã và tên chương (không chép nội dung trang), ghi ra `results.json` và `results.md`; script
từ chối ghi vào trong repo.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from holocron_core.domain.schemas import SourceKind
from holocron_core.infrastructure.document_readers.config import PdfReaderConfig
from holocron_core.infrastructure.document_readers.pdf_reader import WorkerPdfReader
from holocron_core.infrastructure.memory_store import IngestionStore
from holocron_core.infrastructure.storage.original_files import OriginalFileStore
from holocron_core.services.chapter_naming import auto_chapter, choose_chapter_title
from holocron_core.services.document_import import (
    QUEUE_CLOSE_TIMEOUT_S,
    DocumentImportError,
    ImportRequest,
    ImportRuntime,
    ReadJob,
    accept_document,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
SUBJECT_CODE = "EVAL01"
_TARGET_DIR = re.compile(r"_(ch[0-9]{1,2})$")


class _NoReadQueue:
    """Hàng đợi giữ việc mà không đọc trang nào (chương được chọn lúc nhận file)."""

    def __init__(self) -> None:
        self.jobs: list[ReadJob] = []

    def submit(self, job: ReadJob) -> None:
        self.jobs.append(job)

    def close(self, timeout_s: float = QUEUE_CLOSE_TIMEOUT_S) -> bool:
        return True

    def is_active(self, document_id: str) -> bool:
        return any(job.document_id == document_id for job in self.jobs)


@dataclass(frozen=True)
class ChapterRow:
    group: str
    filename: str
    target: str | None
    chapter_code: str | None
    code_source: str | None
    flags: list[str]
    chapter_title: str | None
    title_source: str | None
    error: str | None = None

    @property
    def hit(self) -> bool | None:
        return None if self.target is None else self.target == self.chapter_code


def _target(path: Path) -> str | None:
    match = _TARGET_DIR.search(path.parent.name)
    return None if match is None else match.group(1)


def evaluate(pdfs: list[tuple[str, Path]], files_dir: Path) -> list[ChapterRow]:
    store = IngestionStore()
    config = PdfReaderConfig()
    runtime = ImportRuntime(files=OriginalFileStore(files_dir), queue=_NoReadQueue(), reader_config=config)
    reader = WorkerPdfReader(config, ocr=None)
    rows: list[ChapterRow] = []
    titles: list[str] = []
    try:
        for group, path in pdfs:
            content = path.read_bytes()
            before = store.list_imported_documents(SUBJECT_CODE)
            request = ImportRequest(
                content=content,
                filename=path.name,
                declared_type="application/pdf",
                subject_code=SUBJECT_CODE,
                source_kind=SourceKind.LECTURE_SLIDES,
            )
            try:
                outcome = accept_document(request, store=store, runtime=runtime, reader=reader, now=datetime.now(UTC))
            except DocumentImportError as exc:
                rows.append(ChapterRow(group, path.name, _target(path), None, None, [], None, None, exc.code))
                continue
            document_id = outcome.document.meta.doc_id
            choice = auto_chapter(path.name, before, document_id)
            title = choose_chapter_title(path.name, [], {*titles, SUBJECT_CODE})
            titles.append(title.title)
            rows.append(
                ChapterRow(
                    group=group,
                    filename=path.name,
                    target=_target(path),
                    chapter_code=outcome.document.chapter_code,
                    code_source=choice.source,
                    flags=list(choice.flags),
                    chapter_title=title.title,
                    title_source=title.source,
                )
            )
    finally:
        runtime.close()
    return rows


def markdown(rows: list[ChapterRow]) -> str:
    lines = [
        "# Chương tự động (không LLM, không ứng viên header)",
        "",
        "| nhóm | file | đích | chapter_code | nguồn | cờ | tên chương | nguồn tên | khớp đích |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        hit = "" if row.hit is None else ("có" if row.hit else "KHÔNG")
        lines.append(
            f"| {row.group} | {row.filename} | {row.target or ''} | {row.chapter_code or row.error or ''} | "
            f"{row.code_source or ''} | {', '.join(row.flags)} | {row.chapter_title or ''} | "
            f"{row.title_source or ''} | {hit} |"
        )
    for group in ("dev", "blind"):
        scored = [row for row in rows if row.group == group and row.hit is not None]
        if scored:
            lines.append("")
            lines.append(f"- {group}: khớp đích {sum(bool(row.hit) for row in scored)}/{len(scored)}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("dev_pdfs", type=Path, nargs="+")
    parser.add_argument("--blind", type=Path, nargs="*", default=[], help="PDF mù, nhập sau tài liệu dev")
    args = parser.parse_args(argv)

    out_dir = args.out_dir.resolve()
    if out_dir.is_relative_to(REPO_ROOT):
        print(f"Từ chối ghi kết quả vào trong repo: {out_dir}", file=sys.stderr)
        return 2
    out_dir.mkdir(parents=True, exist_ok=True)
    pdfs = [("dev", path) for path in args.dev_pdfs] + [("blind", path) for path in args.blind]
    with tempfile.TemporaryDirectory(prefix="holocron_eval_chapters_") as files_dir:
        rows = evaluate(pdfs, Path(files_dir))
    payload = {"subject_code": SUBJECT_CODE, "rows": [{**asdict(row), "hit": row.hit} for row in rows]}
    (out_dir / "results.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    text = markdown(rows)
    (out_dir / "results.md").write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
