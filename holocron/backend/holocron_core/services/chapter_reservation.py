"""Giữ chỗ chương lúc nhận file (SPECIFICATION §6.5 bước 5, §5.7 "Chương tự động"; GĐ1 1A).

Hai file cùng môn nhập cùng lúc không được nhận cùng chương trống. Chọn chương cần biết chương đã có của môn, nhưng
tài liệu chỉ được tạo sau khi mở thử PDF (tới vài giây). Để không giữ khoá môn suốt lúc mở thử, việc chọn chương
chạy dưới khoá của môn và GIỮ CHỖ chương vừa chọn; lần chọn sau cùng môn coi chương đã giữ chỗ như chương đã có.
Chỗ được trả khi lần nhận kết thúc (đã tạo tài liệu, trả bản cũ, hay lỗi), nên sau đó chương được tính từ kho.

Thứ tự khoá: khoá môn chỉ giữ trong lúc chọn và giữ chỗ, nhả TRƯỚC khi lấy khoá của tài liệu; không bao giờ lấy khoá
môn khi đang giữ khoá tài liệu.
"""

from __future__ import annotations

import contextlib
import threading
from collections import Counter
from collections.abc import Iterator
from contextlib import AbstractContextManager
from typing import Protocol

from ..domain.repositories import DocumentRepository
from .chapter_naming import InitialChapter, initial_chapter


class KeyedLock(Protocol):
    def hold(self, key: str) -> AbstractContextManager[None]: ...


class ChapterReservations:
    """Chương đã chọn cho lần nhận đang chạy, theo môn; đếm số lần giữ (hai lần nhận có thể giữ cùng mã người dùng
    nhập)."""

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._codes: dict[str, Counter[str]] = {}

    def codes(self, subject_code: str) -> frozenset[str]:
        with self._guard:
            return frozenset(self._codes.get(subject_code, Counter()))

    def add(self, subject_code: str, chapter_code: str) -> None:
        with self._guard:
            self._codes.setdefault(subject_code, Counter())[chapter_code] += 1

    def remove(self, subject_code: str, chapter_code: str) -> None:
        with self._guard:
            codes = self._codes[subject_code]
            codes[chapter_code] -= 1
            if codes[chapter_code] <= 0:
                del codes[chapter_code]
            if not codes:
                del self._codes[subject_code]

    def __len__(self) -> int:
        """Số chỗ đang giữ (mọi môn)."""
        with self._guard:
            return sum(codes.total() for codes in self._codes.values())


@contextlib.contextmanager
def reserve_chapter(
    *,
    reservations: ChapterReservations,
    subject_locks: KeyedLock,
    store: DocumentRepository,
    subject_code: str,
    filename: str,
    chapter_code: str | None,
    document_id: str,
) -> Iterator[InitialChapter]:
    """Chọn chương và tên tạm (`initial_chapter`) dưới khoá của môn, giữ chỗ chương đó cho tới khi thoát khối."""
    with subject_locks.hold(subject_code):
        chapter = initial_chapter(
            filename,
            chapter_code,
            store.list_imported_documents(subject_code),
            document_id,
            reservations.codes(subject_code),
        )
        reservations.add(subject_code, chapter.chapter_code)
    try:
        yield chapter
    finally:
        reservations.remove(subject_code, chapter.chapter_code)
