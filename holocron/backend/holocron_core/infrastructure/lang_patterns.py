"""Bộ nạp mẫu phụ thuộc ngôn ngữ của bộ dựng khối (ADR-010 mục 12, 16; ADR-006 mục 10).

Mẫu có chữ ("Trang", "Page … of …"), hậu tố nối trang ("(tiếp)", "(cont.)") và từ khoá tiêu đề nằm ở
`infrastructure/lang/{vi,en}.toml`, nạp bằng `tomllib`. Bộ nạp nạp HỢP mọi tệp được cho (không đoán ngôn ngữ tài
liệu): mỗi khoá là hợp các giá trị, đã NFC, bỏ trùng và sắp, nên kết quả không phụ thuộc thứ tự tệp. Không cache cấp
module (CLAUDE.md §2.2): người gọi giữ đối tượng `LangPatterns`.
"""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
import unicodedata
from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

LANG_DIR = Path(__file__).resolve().parent / "lang"
DEFAULT_LANG_FILES: tuple[str, ...] = ("vi.toml", "en.toml")
# Nhóm có tên mà mẫu số trang phải có: `n` = số Ả Rập, `r` = số La Mã.
PAGE_NUMBER_GROUPS: frozenset[str] = frozenset({"n", "r"})
MAX_LANG_FILE_BYTES = 64 * 1024
MAX_PATTERN_CHARS = 200
MAX_ENTRIES = 200


class LangPatternsError(ValueError):
    """Tệp mẫu ngôn ngữ không đọc được hoặc sai cấu trúc."""


def _nfc_unique_sorted(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(sorted({unicodedata.normalize("NFC", value) for value in values}))


class _LangFile(BaseModel):
    """Nội dung một tệp `lang/*.toml`; khoá lạ bị từ chối."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    page_number_patterns: list[str] = Field(default_factory=list, max_length=MAX_ENTRIES)
    continuation_suffixes: list[str] = Field(default_factory=list, max_length=MAX_ENTRIES)
    heading_keywords: list[str] = Field(default_factory=list, max_length=MAX_ENTRIES)


class LangPatterns(BaseModel):
    """Mẫu ngôn ngữ ĐÃ HỢP; bất biến. `sha256()` vào `fingerprint()` của stage dùng mẫu (ADR-010 mục 15)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    page_number_patterns: tuple[str, ...] = ()
    continuation_suffixes: tuple[str, ...] = ()
    heading_keywords: tuple[str, ...] = ()

    @field_validator("page_number_patterns")
    @classmethod
    def _patterns_compile(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for pattern in value:
            if not 0 < len(pattern) <= MAX_PATTERN_CHARS:
                raise ValueError(f"mẫu số trang phải dài 1..{MAX_PATTERN_CHARS} ký tự")
            try:
                compiled = re.compile(pattern, re.IGNORECASE)
            except re.error as exc:
                raise ValueError(f"mẫu số trang không biên dịch được: {exc}") from exc
            if not set(compiled.groupindex) & PAGE_NUMBER_GROUPS:
                raise ValueError("mẫu số trang phải có nhóm có tên `n` (số) hoặc `r` (số La Mã)")
        return value

    @field_validator("continuation_suffixes", "heading_keywords")
    @classmethod
    def _non_empty(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() or len(item) > MAX_PATTERN_CHARS for item in value):
            raise ValueError(f"mục mẫu ngôn ngữ phải khác rỗng và dài tối đa {MAX_PATTERN_CHARS} ký tự")
        return value

    def compiled_page_number_patterns(self) -> tuple[re.Pattern[str], ...]:
        return tuple(re.compile(pattern, re.IGNORECASE) for pattern in self.page_number_patterns)

    def sha256(self) -> str:
        """sha256 của JSON chuẩn hoá (`sort_keys`, không ASCII-hoá, không khoảng trắng) của mẫu đã hợp."""
        canonical = json.dumps(self.model_dump(mode="json"), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _read_lang_file(path: Path) -> _LangFile:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise LangPatternsError(f"không đọc được tệp mẫu ngôn ngữ {path.name}: {exc}") from exc
    if len(data) > MAX_LANG_FILE_BYTES:
        raise LangPatternsError(f"tệp mẫu ngôn ngữ {path.name} lớn quá {MAX_LANG_FILE_BYTES} byte")
    try:
        return _LangFile.model_validate(tomllib.loads(data.decode("utf-8")))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError, ValidationError) as exc:
        raise LangPatternsError(f"tệp mẫu ngôn ngữ {path.name} sai cấu trúc: {exc}") from exc


def load_lang_patterns(paths: Sequence[Path] | None = None) -> LangPatterns:
    """Nạp HỢP các tệp mẫu ngôn ngữ; `None` ⇒ hai tệp đóng gói `lang/vi.toml`, `lang/en.toml`.

    Mỗi lần gọi đọc lại đĩa (không cache cấp module). Lỗi đọc hay cấu trúc ⇒ `LangPatternsError`.
    """
    files = [LANG_DIR / name for name in DEFAULT_LANG_FILES] if paths is None else list(paths)
    loaded = [_read_lang_file(path) for path in files]
    try:
        return LangPatterns(
            page_number_patterns=_nfc_unique_sorted([p for item in loaded for p in item.page_number_patterns]),
            continuation_suffixes=_nfc_unique_sorted([s for item in loaded for s in item.continuation_suffixes]),
            heading_keywords=_nfc_unique_sorted([k for item in loaded for k in item.heading_keywords]),
        )
    except ValidationError as exc:
        raise LangPatternsError(f"mẫu ngôn ngữ không hợp lệ: {exc}") from exc
