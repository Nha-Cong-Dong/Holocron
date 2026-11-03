"""Bộ nạp mẫu ngôn ngữ của bộ dựng khối (ADR-010 mục 12, 16; `infrastructure/lang_patterns.py`)."""

from __future__ import annotations

from pathlib import Path

import pytest

from holocron_core.infrastructure.lang_patterns import (
    LANG_DIR,
    MAX_LANG_FILE_BYTES,
    LangPatterns,
    LangPatternsError,
    load_lang_patterns,
)

# Từ khoá tiêu đề hiện có của `vault_chunking.HEADING_RE` (X09 chuyển sang toml, không đổi hành vi).
EXISTING_HEADING_KEYWORDS = {"chương", "chapter", "bài", "phần", "part", "section", "lecture", "mục", "unit"}


def _write(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def test_default_files_are_merged() -> None:
    patterns = load_lang_patterns()
    assert set(patterns.heading_keywords) == EXISTING_HEADING_KEYWORDS
    compiled = patterns.compiled_page_number_patterns()
    assert any(p.match("Trang 12") for p in compiled) and any(p.match("Page 3 of 9") for p in compiled)
    assert "(tiếp)" in patterns.continuation_suffixes and "(cont.)" in patterns.continuation_suffixes
    assert list(patterns.heading_keywords) == sorted(patterns.heading_keywords)


def test_merge_is_independent_of_file_order() -> None:
    vi, en = LANG_DIR / "vi.toml", LANG_DIR / "en.toml"
    assert load_lang_patterns([vi, en]) == load_lang_patterns([en, vi]) == load_lang_patterns()
    assert load_lang_patterns([vi, en]).sha256() == load_lang_patterns([en, vi]).sha256()


def test_loader_accepts_temporary_paths_and_dedupes(tmp_path: Path) -> None:
    first = _write(tmp_path / "a.toml", 'heading_keywords = ["Bài", "bài"]\ncontinuation_suffixes = ["(x)"]\n')
    second = _write(tmp_path / "b.toml", "heading_keywords = [\"bài\"]\npage_number_patterns = ['^p(?P<n>\\d+)$']\n")
    patterns = load_lang_patterns([first, second])
    assert patterns.heading_keywords == ("Bài", "bài")
    assert patterns.continuation_suffixes == ("(x)",)
    assert patterns.page_number_patterns == ("^p(?P<n>\\d+)$",)


def test_loader_reads_disk_every_call(tmp_path: Path) -> None:
    path = _write(tmp_path / "a.toml", 'heading_keywords = ["one"]\n')
    before = load_lang_patterns([path])
    _write(path, 'heading_keywords = ["two"]\n')
    after = load_lang_patterns([path])
    assert before.heading_keywords == ("one",) and after.heading_keywords == ("two",)
    assert before.sha256() != after.sha256()


def test_nfd_entries_are_normalized(tmp_path: Path) -> None:
    path = _write(tmp_path / "a.toml", 'heading_keywords = ["chương"]\n')
    assert load_lang_patterns([path]).heading_keywords == ("chương",)


def test_empty_path_list_gives_empty_patterns() -> None:
    assert load_lang_patterns([]) == LangPatterns()


@pytest.mark.parametrize(
    "content",
    [
        "page_number_patterns = ['^(unclosed$']\n",
        "page_number_patterns = ['^\\\\d+$']\n",
        "unknown_key = ['x']\n",
        "heading_keywords = ['  ']\n",
        "heading_keywords = 'not a list'\n",
        "this is not toml\n",
    ],
)
def test_bad_files_are_rejected(tmp_path: Path, content: str) -> None:
    path = _write(tmp_path / "bad.toml", content)
    with pytest.raises(LangPatternsError):
        load_lang_patterns([path])


def test_missing_oversized_and_non_utf8_files_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(LangPatternsError):
        load_lang_patterns([tmp_path / "missing.toml"])
    big = tmp_path / "big.toml"
    big.write_bytes(b"#" * (MAX_LANG_FILE_BYTES + 1))
    with pytest.raises(LangPatternsError):
        load_lang_patterns([big])
    latin = tmp_path / "latin.toml"
    latin.write_bytes(b'heading_keywords = ["\xe9"]\n')
    with pytest.raises(LangPatternsError):
        load_lang_patterns([latin])
