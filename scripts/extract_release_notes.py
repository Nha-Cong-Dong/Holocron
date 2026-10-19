"""Trích mục `## [x.y.z]` của một tag từ CHANGELOG.md để làm release notes.

Cách dùng: python scripts/extract_release_notes.py v0.1.0-alpha
Thoát mã 1 nếu CHANGELOG thiếu mục hoặc mục rỗng.
"""

from __future__ import annotations

import io
import re
import sys
from pathlib import Path

CHANGELOG = Path(__file__).resolve().parent.parent / "CHANGELOG.md"
HEADING = re.compile(r"^## \[(?P<version>[^\]]+)\](?P<rest>.*)$")
PLACEHOLDER = re.compile(r"^_.*_$")


def extract(text: str, version: str) -> str | None:
    """Nội dung từ dòng sau tiêu đề mục `version` tới trước tiêu đề `## [` kế tiếp; None nếu không có mục."""
    lines = text.splitlines()
    start: int | None = None
    for index, line in enumerate(lines):
        match = HEADING.match(line)
        if match is None:
            continue
        if start is not None:
            return "\n".join(lines[start:index]).strip()
        if match.group("version") == version:
            start = index + 1
    return "\n".join(lines[start:]).strip() if start is not None else None


def has_content(body: str) -> bool:
    """Có ít nhất một dòng khác tiêu đề `###`, dòng trống và ghi chú giữ chỗ dạng `_..._`."""
    return any(
        line.strip() and not line.startswith("#") and not PLACEHOLDER.match(line.strip()) for line in body.splitlines()
    )


def main(argv: list[str]) -> int:
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")  # console Windows mặc định cp1252, không in được tiếng Việt
    if isinstance(sys.stderr, io.TextIOWrapper):
        sys.stderr.reconfigure(encoding="utf-8")
    if len(argv) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    version = argv[0].removeprefix("v")
    if not CHANGELOG.exists():
        print("LỖI: thiếu CHANGELOG.md", file=sys.stderr)
        return 1
    body = extract(CHANGELOG.read_text(encoding="utf-8"), version)
    if body is None:
        print(f"LỖI: CHANGELOG.md không có mục ## [{version}]", file=sys.stderr)
        return 1
    if not has_content(body):
        print(f"LỖI: mục ## [{version}] của CHANGELOG.md rỗng", file=sys.stderr)
        return 1
    print(body)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
