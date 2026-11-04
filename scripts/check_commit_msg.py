"""Kiểm tra commit message / tiêu đề PR theo Conventional Commits.

Chế độ:
  python scripts/check_commit_msg.py <file>          # hook commit-msg của pre-commit
  python scripts/check_commit_msg.py --range A..B    # CI: kiểm mọi commit trong khoảng
  python scripts/check_commit_msg.py --title "..."   # CI: kiểm tiêu đề PR
  python scripts/check_commit_msg.py --self-test     # tự kiểm quy tắc

Ngoài dòng đầu, toàn bộ message (chế độ <file> và --range) bị từ chối khi có dòng ghi công AI (CLAUDE.md §6):
"Co-Authored-By: Claude…", "Claude-Session:" hoặc "Generated with Claude…" (không phân biệt hoa thường).
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
AI_ATTRIBUTION = re.compile(
    r"^\s*(co-authored-by:\s*claude\b|claude-session:|.*generated with \[?claude\b)", re.IGNORECASE | re.MULTILINE
)
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


def check_body(message: str) -> str | None:
    """Lỗi khi message có dòng ghi công AI (CLAUDE.md §6); bỏ qua dòng chú thích `#` của git."""
    text = "\n".join(line for line in message.splitlines() if not line.startswith("#"))
    if AI_ATTRIBUTION.search(text):
        return "có dòng ghi công AI (Co-Authored-By: Claude / Claude-Session / Generated with Claude), CLAUDE.md §6"
    return None


def _first_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip() and not line.startswith("#"):
            return line.strip()
    return ""


def _commits(rev_range: str) -> list[tuple[str, str, str]]:
    """(sha ngắn, dòng đầu, toàn bộ message) của mọi commit trong khoảng."""
    out = subprocess.run(
        ["git", "log", "--format=%h%x1f%s%x1f%B%x1e", rev_range],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout
    commits: list[tuple[str, str, str]] = []
    for record in out.split("\x1e"):
        parts = record.strip("\n").split("\x1f")
        if len(parts) == 3:
            commits.append((parts[0], parts[1], parts[2]))
    return commits


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
    clean_bodies = [
        "feat: x\n\nMô tả dài.\n",
        "fix: y\n\nRefs: #12\nCo-Authored-By: Tran Van A <a@example.com>\n",
        "docs: z\n\n# Co-Authored-By: Claude (dòng chú thích của git, bị bỏ qua)\n",
    ]
    ai_bodies = [
        "feat: x\n\nCo-Authored-By: Claude Opus <noreply@anthropic.com>\n",
        "feat: x\n\nco-authored-by: claude <noreply@anthropic.com>\n",
        "fix: y\n\nClaude-Session: abc123\n",
        "docs: z\n\n🤖 Generated with [Claude Code](https://claude.com/claude-code)\n",
    ]
    failures = [f"phải hợp lệ: {s!r}" for s in valid if check_subject(s) is not None]
    failures += [f"phải bị từ chối: {s!r}" for s in invalid if check_subject(s) is None]
    failures += [f"thân phải hợp lệ: {b!r}" for b in clean_bodies if check_body(b) is not None]
    failures += [f"thân phải bị từ chối: {b!r}" for b in ai_bodies if check_body(b) is None]
    for failure in failures:
        print(f"SELF-TEST LỖI: {failure}", file=sys.stderr)
    if not failures:
        print(f"self-test OK ({len(valid) + len(clean_bodies)} hợp lệ, {len(invalid) + len(ai_bodies)} bị từ chối)")
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
        for sha, subject, body in _commits(args.rev_range):
            for error in (check_subject(subject), check_body(body)):
                if error:
                    errors.append(f"{sha} {subject!r}: {error}")
        return _report(errors)
    if args.file is not None:
        message = Path(args.file).read_text(encoding="utf-8")
        subject = _first_line(message)
        found = [error for error in (check_subject(subject), check_body(message)) if error]
        return _report([f"{subject!r}: {error}" for error in found])
    parser.error("cần một trong: <file>, --range, --title, --self-test")
    return 2


if __name__ == "__main__":
    sys.exit(main())
