"""Chương tự động của tài liệu nhập (SPECIFICATION §5.7 "Chương tự động", §6.5; ADR-006 mục 2; GĐ1 1A).

Hàm thuần, không đọc kho hay file:
- `choose_chapter_code`: số chương lấy từ TÊN FILE (không đọc trang), trùng hoặc không có thì chương trống nhỏ nhất
  của môn kèm cờ. Tầng gọi chọn dưới khoá của môn để hai file nhập cùng lúc không nhận cùng chương trống.
- `choose_chapter_title`: ứng viên header lặp (nguồn ứng viên là `ChapterTitleCandidateSource`, nối ở tầng gọi), rồi
  tên file đã làm sạch; loại ứng viên trùng với các chuỗi tầng gọi đưa vào (tên chương của tài liệu khác cùng môn,
  `display_name`, `subject_code`).
- `select_title_with_llm`: bộ chọn tên (LLM, tuỳ chọn) chỉ được CHỌN hoặc LÀM GỌN một ứng viên; kết quả không phải
  chuỗi con của một ứng viên thì bị bỏ, giữ lựa chọn tất định.

Không có chuỗi theo môn hay theo ngôn ngữ ở đây (CLAUDE.md §1, ADR-006 mục 10): chỉ dùng chữ số ASCII, dấu câu theo
`unicodedata` và khoảng trắng.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from ..domain.documents import (
    MAX_CHAPTER_TITLE_CHARS,
    ChapterFlag,
    PageExtraction,
    normalize_chapter_title,
)
from ..domain.repositories import ImportedDocument

# `chapter_code` giữ mẫu `^ch[0-9]{1,2}(\.[0-9]{1,2})?$` (§5.7): số chương tự động là 1..99.
MIN_CHAPTER_NUMBER = 1
MAX_CHAPTER_NUMBER = 99
# Tên chương khi tên file sau khi làm sạch không còn ký tự nào (ví dụ "___.pdf"); như tiêu đề mặc định của §6.5.
EMPTY_FILENAME_TITLE = "Tài liệu"

_ASCII_DIGIT_RUN = re.compile(r"[0-9]+")  # `[0-9]` chỉ khớp chữ số ASCII
_FILENAME_SEPARATORS = str.maketrans({"_": " ", "-": " "})

_LOGGER = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Số chương
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ChapterChoice:
    """Chương do code chọn khi form không có `chapter_code` (§5.7)."""

    chapter_code: str
    source: Literal["file_name", "next_free"]
    flags: tuple[ChapterFlag, ...]


def filename_stem(filename: str) -> str:
    """Tên file bỏ đuôi (phần sau dấu chấm cuối); tên chỉ có đuôi (".pdf") giữ nguyên."""
    stem, dot, _ = filename.rpartition(".")
    return stem if dot and stem.strip() else filename


def chapter_number_from_filename(filename: str) -> int | None:
    """Số nguyên của đoạn chữ số ASCII liền nhau ĐẦU TIÊN dài 1–2 ký tự trong tên file đã bỏ đuôi (§5.7).

    Đoạn ≥ 3 chữ số (ví dụ năm `2020`) bị bỏ qua để xét đoạn sau. Đoạn đầu tiên dài 1–2 ký tự có giá trị 0 (`0`,
    `00`) ⇒ coi như không có số (số chương ≥ 1; quyết định của brief, chưa ghi trong spec).
    """
    for match in _ASCII_DIGIT_RUN.finditer(filename_stem(filename)):
        digits = match.group()
        if len(digits) <= 2:
            number = int(digits)
            return number if number >= MIN_CHAPTER_NUMBER else None
    return None


def next_free_chapter(taken: Collection[str]) -> str | None:
    """`chN` với N nhỏ nhất trong 1..99 mà chưa có trong `taken`; hết chỗ ⇒ `None`.

    So khớp đúng mã: `ch3.2` của tài liệu khác không làm `ch3` thành đã dùng.
    """
    for number in range(MIN_CHAPTER_NUMBER, MAX_CHAPTER_NUMBER + 1):
        code = f"ch{number}"
        if code not in taken:
            return code
    return None


def choose_chapter_code(filename: str, taken: Collection[str]) -> ChapterChoice:
    """Chương của tài liệu mới theo §5.7; `taken` là `chapter_code` của các tài liệu KHÁC cùng môn.

    Số trong tên file chưa dùng ⇒ `file_name`; không có số ⇒ `next_free` + `CHAPTER_NUMBER_NOT_IN_FILENAME`; số đã
    dùng ⇒ `next_free` + `CHAPTER_NUMBER_TAKEN`. Cả 99 chương đã dùng (spec không nói) ⇒ `ch99`, vẫn nguồn
    `next_free` kèm cờ tương ứng, để luôn có mã hợp lệ.
    """
    number = chapter_number_from_filename(filename)
    if number is not None:
        code = f"ch{number}"
        if code not in taken:
            return ChapterChoice(chapter_code=code, source="file_name", flags=())
        flag: ChapterFlag = "CHAPTER_NUMBER_TAKEN"
    else:
        flag = "CHAPTER_NUMBER_NOT_IN_FILENAME"
    free = next_free_chapter(taken)
    return ChapterChoice(
        chapter_code=free if free is not None else f"ch{MAX_CHAPTER_NUMBER}", source="next_free", flags=(flag,)
    )


# --------------------------------------------------------------------------- #
# Tên chương
# --------------------------------------------------------------------------- #


class ChapterTitleCandidateSource(Protocol):
    """Nguồn ứng viên header lặp của một tài liệu (§5.7 "Tên chương", ứng viên (1)).

    Trả các dòng header/footer lặp đã xếp theo thứ tự của spec (số trang có dòng giảm dần, mép trên tăng dần, chuỗi).
    Nhánh này chưa có bộ dò header: điểm nối để nối hàm dò header của bộ nhập PDF.
    """

    def __call__(self, pages: Sequence[PageExtraction]) -> list[str]: ...


@dataclass(frozen=True)
class TitleChoice:
    title: str
    source: Literal["repeated_header", "file_name"]


def comparison_key(text: str) -> str:
    """Khoá so trùng của §5.7: NFC + `casefold` + gộp khoảng trắng."""
    return " ".join(unicodedata.normalize("NFC", text).casefold().split())


def clean_filename_title(filename: str) -> str:
    """Tên file đã làm sạch (§5.7): bỏ đuôi, `_` và `-` thành dấu cách, NFC, gộp khoảng trắng, cắt 300 ký tự."""
    cleaned = normalize_chapter_title(filename_stem(filename).translate(_FILENAME_SEPARATORS))
    cleaned = cleaned[:MAX_CHAPTER_TITLE_CHARS].rstrip()
    return cleaned or EMPTY_FILENAME_TITLE


def _only_marks(text: str) -> bool:
    """Chuỗi chỉ gồm `#`, dấu câu (nhóm `P*` của Unicode) và khoảng trắng."""
    return all(char == "#" or char.isspace() or unicodedata.category(char).startswith("P") for char in text)


def usable_header_candidates(candidates: Sequence[str]) -> list[str]:
    """Ứng viên header đã chuẩn hoá (NFC, gộp khoảng trắng), giữ thứ tự, bỏ trùng.

    Bỏ ứng viên rỗng, chỉ gồm `#`/dấu câu/khoảng trắng (§5.7), hoặc dài quá 300 ký tự sau chuẩn hoá (không cắt hộ,
    vì tên phải là nguyên văn một dòng của tài liệu).
    """
    usable: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        normalized = normalize_chapter_title(candidate)
        if not normalized or _only_marks(normalized) or len(normalized) > MAX_CHAPTER_TITLE_CHARS:
            continue
        key = comparison_key(normalized)
        if key not in seen:
            seen.add(key)
            usable.append(normalized)
    return usable


def choose_chapter_title(filename: str, header_candidates: Sequence[str], excluded: Collection[str]) -> TitleChoice:
    """Tên chương tất định (§5.7): ứng viên header đầu tiên không trùng `excluded`, không còn thì tên file đã làm sạch
    (kể cả khi trùng, để luôn có tên).

    `excluded`: chuỗi mà tên chương không được trùng (so bằng `comparison_key`): tên chương của tài liệu khác cùng
    môn, ứng viên header của tài liệu khác cùng môn khi có, `display_name` và `subject_code` của môn.
    """
    blocked = {comparison_key(text) for text in excluded}
    for candidate in usable_header_candidates(header_candidates):
        if comparison_key(candidate) not in blocked:
            return TitleChoice(title=candidate, source="repeated_header")
    return TitleChoice(title=clean_filename_title(filename), source="file_name")


# --------------------------------------------------------------------------- #
# Bộ chọn tên (LLM, tuỳ chọn)
# --------------------------------------------------------------------------- #


class ChapterTitleSelector(Protocol):
    """Chọn hoặc làm gọn MỘT ứng viên; chỉ nhận danh sách ứng viên (không nhận trang bìa hay văn bản trang).

    Trả chuỗi con của một ứng viên, hoặc `None` khi không chọn. Kết quả là đầu ra không tin cậy: luôn qua
    `checked_selection` trước khi dùng.
    """

    def select(self, candidates: Sequence[str]) -> str | None: ...


def checked_selection(candidates: Sequence[str], proposal: str | None) -> str | None:
    """Kết quả của bộ chọn sau chuẩn hoá (NFC, gộp khoảng trắng) nếu dài 1–300 ký tự và là chuỗi con của một ứng
    viên (cùng chuẩn hoá); sai ⇒ `None` (§5.7: bỏ kết quả LLM, giữ lựa chọn tất định)."""
    if proposal is None:
        return None
    normalized = normalize_chapter_title(proposal)
    if not 1 <= len(normalized) <= MAX_CHAPTER_TITLE_CHARS:
        return None
    if any(normalized in normalize_chapter_title(candidate) for candidate in candidates):
        return normalized
    return None


def select_title_with_llm(selector: ChapterTitleSelector, candidates: Sequence[str]) -> str | None:
    """Gọi bộ chọn trên danh sách ứng viên rồi kiểm kết quả; không có ứng viên ⇒ không gọi.

    Lỗi của bộ chọn (mạng, định dạng…) chỉ ghi log, trả `None` (§5.7: lỗi LLM không chặn gì).
    """
    if not candidates:
        return None
    sent = list(candidates)
    try:
        proposal = selector.select(sent)
    except Exception:
        _LOGGER.exception("bộ chọn tên chương lỗi; giữ tên tất định")
        return None
    return checked_selection(sent, proposal)


def auto_chapter(filename: str, subject_documents: Iterable[ImportedDocument], document_id: str) -> ChapterChoice:
    """`choose_chapter_code` với các chương đã có của môn: `subject_documents` là tài liệu nhập cùng `subject_code`;
    chính tài liệu `document_id` và bản ghi chưa có `chapter_code` không tính là "chương đã có" (§5.7).

    Tầng gọi PHẢI đọc `subject_documents` và tạo tài liệu dưới CÙNG khoá của môn (§6.5 bước 5).
    """
    taken = {
        document.chapter_code
        for document in subject_documents
        if document.meta.doc_id != document_id and document.chapter_code is not None
    }
    return choose_chapter_code(filename, taken)
