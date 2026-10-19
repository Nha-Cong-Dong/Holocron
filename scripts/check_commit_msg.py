"""Kiểm tra commit message / tiêu đề PR theo Conventional Commits.

Chế độ:
  python scripts/check_commit_msg.py <file>          # hook commit-msg của pre-commit
  python scripts/check_commit_msg.py --range A..B    # CI: kiểm mọi commit trong khoảng
  python scripts/check_commit_msg.py --title "..."   # CI: kiểm tiêu đề PR
  python scripts/check_commit_msg.py --self-test     # tự kiểm quy tắc
"""

from __future__ import annotations

import argparse
import io
import re
import subprocess
import sys
from pathlib import Path

TYPES = ("feat", "fix", "docs", "test", "refactor", "chore", "perf", "ci", "style", "revert")
MAX_SUBJECT_LENGTH = 100
EXEMPT_PREFIXES = ("Merge ", 'Revert "', "fixup!", "squash!")
PATTERN = re.compile(
    rf"^(?P<type>{'|'.join(TYPES)})(\((?P<scope>[a-z0-9]+(-[a-z0-9]+)*)\))?(?P<breaking>!)?: (?P<subject>.+)$"
)


def check_subject(subject: str) -> str | None:
    """Trả về thông điệp lỗi, hoặc None nếu hợp lệ."""
    if subject.startswith(EXEMPT_PREFIXES):
        return None
    if len(subject) > MAX_SUBJECT_LENGTH:
        return f"dòng đầu dài {len(subject)} > {MAX_SUBJECT_LENGTH} ký tự"
    match = PATTERN.match(subject)
    if match is None:
        return f"không theo dạng `type(scope)!: mô tả`, type ∈ {', '.join(TYPES)}"
    if match.group("subject").rstrip().endswith("."):
        return "mô tả không được kết thúc bằng dấu chấm"
    return None


def _first_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip() and not line.startswith("#"):
            return line.strip()
    return ""


def _commit_subjects(rev_range: str) -> list[tuple[str, str]]:
    out = subprocess.run(
        ["git", "log", "--format=%h%x09%s", rev_range], capture_output=True, text=True, encoding="utf-8", check=True
    ).stdout
    pairs: list[tuple[str, str]] = []
    for line in out.splitlines():
        sha, sep, subject = line.partition("\t")
        if sep:
            pairs.append((sha, subject))
    return pairs


def _report(errors: list[str]) -> int:
    for error in errors:
        print(f"LỖI commit message: {error}", file=sys.stderr)
    return 1 if errors else 0


def self_test() -> int:
    valid = [
        "feat: add upload",
        "fix(api): reject null quantum",
        "feat(sandbox)!: drop old limits",
        "chore(deps): bump next",
        "Merge branch 'develop' into x",
        'Revert "feat: x"',
        "fixup! feat: x",
        "squash! fix: y",
    ]
    invalid = [
        "Add upload",
        "feat add upload",
        "feat(Scope): bad scope case",
        "feat(a_b): bad scope underscore",
        "wip: something",
        "feat: ends with dot.",
        "feat:",
        "feat: " + "x" * 100,
    ]
    failures = [f"phải hợp lệ: {s!r}" for s in valid if check_subject(s) is not None]
    failures += [f"phải bị từ chối: {s!r}" for s in invalid if check_subject(s) is None]
    for failure in failures:
        print(f"SELF-TEST LỖI: {failure}", file=sys.stderr)
    if not failures:
        print(f"self-test OK ({len(valid)} hợp lệ, {len(invalid)} bị từ chối)")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("file", nargs="?", help="file chứa commit message (hook commit-msg)")
    parser.add_argument("--range", dest="rev_range", help="khoảng commit, ví dụ origin/develop..HEAD")
    parser.add_argument("--title", help="tiêu đề PR")
    parser.add_argument("--self-test", action="store_true", help="tự kiểm quy tắc")
    args = parser.parse_args(argv)
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")  # console Windows mặc định cp1252, không in được tiếng Việt
    if isinstance(sys.stderr, io.TextIOWrapper):
        sys.stderr.reconfigure(encoding="utf-8")

    if args.self_test:
        return self_test()
    if args.title is not None:
        error = check_subject(args.title.strip())
        return _report([f"tiêu đề PR {args.title!r}: {error}"] if error else [])
    if args.rev_range is not None:
        errors = []
        for sha, subject in _commit_subjects(args.rev_range):
            error = check_subject(subject)
            if error:
                errors.append(f"{sha} {subject!r}: {error}")
        return _report(errors)
    if args.file is not None:
        subject = _first_line(Path(args.file).read_text(encoding="utf-8"))
        error = check_subject(subject)
        return _report([f"{subject!r}: {error}"] if error else [])
    parser.error("cần một trong: <file>, --range, --title, --self-test")
    return 2


if __name__ == "__main__":
    sys.exit(main())
