"""Chọn chương lúc nhận file (SPECIFICATION §6.5 bước 5, §5.7 "Chương tự động"; GĐ1 1A).

Yêu cầu: kết quả nhiều lần nhận chạy đồng thời phải tương đương MỘT thứ tự tuần tự nào đó của chúng. Chọn chương cần
biết chương đã có của môn, nhưng tài liệu chỉ được tạo sau khi mở thử PDF (tới vài giây), và một lần nhận có thể lỗi
(file hỏng) mà không tạo gì. Vì vậy chương KHÔNG được giữ chỗ từ đầu: lần nhận mở thử PDF đồng thời với các lần khác;
chỉ khi đã mở thử xong, sắp tạo tài liệu, nó vào đoạn găng của môn (`ChapterTurn.settled`): chọn chương từ kho rồi tạo
tài liệu trong cùng đoạn găng. Thứ tự tuần tự tương đương là thứ tự vào đoạn găng; file lỗi không bao giờ vào đoạn
găng nên không chiếm chương nào, và lần nhập lại cùng nội dung thấy tài liệu đã có (§6.5 bước 3, trước khi tới đoạn
găng) thì trả bản cũ mà không chọn chương.

Đoạn găng chỉ bọc việc chọn từ kho, lưu file gốc và tạo bản ghi; không bọc mở thử PDF, không bọc việc xếp hàng đợi đọc
(với hàng đợi đồng bộ việc đọc có thể lấy khoá của tài liệu khác). Bên trong không lấy khoá nào khác ngoài khoá nội
bộ của kho và của file gốc, nên không tạo vòng chờ với khoá tài liệu.

Thứ tự khoá: khoá môn (`subject_locks`) chỉ giữ lúc ghi nhận lần nhận mới, nhả TRƯỚC khi lấy khoá của tài liệu; không
bao giờ lấy khoá môn khi đang giữ khoá tài liệu. Đoạn găng nằm trong khoá của tài liệu.
"""

from __future__ import annotations

import contextlib
import threading
from collections import Counter
from collections.abc import Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Protocol

from ..domain.repositories import DocumentRepository
from .chapter_naming import InitialChapter, initial_chapter


class KeyedLock(Protocol):
    def hold(self, key: str) -> AbstractContextManager[None]: ...


class ChapterReservations:
    """Số lần nhận đang chạy theo môn và đoạn găng chọn chương của từng môn (khoá cấp phát khi cần, bỏ khi không còn
    ai giữ hay chờ)."""

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._active: Counter[str] = Counter()
        self._critical: dict[str, tuple[threading.Lock, int]] = {}

    def arrive(self, subject_code: str) -> None:
        with self._guard:
            self._active[subject_code] += 1

    def leave(self, subject_code: str) -> None:
        with self._guard:
            self._active[subject_code] -= 1
            if self._active[subject_code] <= 0:
                del self._active[subject_code]

    @contextlib.contextmanager
    def critical_section(self, subject_code: str) -> Iterator[None]:
        """Đoạn găng của môn: mỗi lúc một lần nhận."""
        with self._guard:
            lock, users = self._critical.get(subject_code, (threading.Lock(), 0))
            self._critical[subject_code] = (lock, users + 1)
        try:
            with lock:
                yield
        finally:
            with self._guard:
                lock, users = self._critical[subject_code]
                if users == 1:
                    del self._critical[subject_code]
                else:
                    self._critical[subject_code] = (lock, users - 1)

    def __len__(self) -> int:
        """Số lần nhận đang chạy (mọi môn)."""
        with self._guard:
            return self._active.total()

    def critical_sections_open(self) -> int:
        """Số đoạn găng đang có người giữ hoặc chờ."""
        with self._guard:
            return len(self._critical)


@dataclass(frozen=True)
class ChapterTurn:
    """Lượt nhận của một file: `settled` là đoạn găng chọn chương và tên tạm, bọc việc tạo tài liệu."""

    reservations: ChapterReservations
    store: DocumentRepository
    subject_code: str
    document_id: str
    filename: str
    chapter_code: str | None

    @contextlib.contextmanager
    def settled(self) -> Iterator[InitialChapter]:
        """Vào đoạn găng của môn rồi chọn chương từ kho; người gọi PHẢI tạo tài liệu trước khi thoát khối (nếu không,
        lần nhận sau có thể chọn trùng chương). Gọi sau khi mở thử PDF xong, không giữ khoá môn."""
        with self.reservations.critical_section(self.subject_code):
            yield initial_chapter(
                self.filename,
                self.chapter_code,
                self.store.list_imported_documents(self.subject_code),
                self.document_id,
            )


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
) -> Iterator[ChapterTurn]:
    """Ghi nhận lần nhận mới của môn (dưới khoá môn, nhả ngay) và trao `ChapterTurn`; chưa chọn chương nào."""
    with subject_locks.hold(subject_code):
        reservations.arrive(subject_code)
    try:
        yield ChapterTurn(reservations, store, subject_code, document_id, filename, chapter_code)
    finally:
        reservations.leave(subject_code)
