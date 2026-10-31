"""Thứ tự nhận file trong cùng môn (SPECIFICATION §6.5 bước 5, §5.7 "Chương tự động"; GĐ1 1A).

Yêu cầu: kết quả nhiều lần nhận chạy đồng thời phải tương đương MỘT thứ tự tuần tự nào đó của chúng. Chọn chương cần
biết chương đã có của môn, nhưng tài liệu chỉ được tạo sau khi mở thử PDF (tới vài giây), và một lần nhận có thể lỗi
(file hỏng) mà không tạo gì. Cách làm: mỗi lần nhận lấy một PHIẾU theo thứ tự đến (số thứ tự theo môn) rồi mở thử PDF
đồng thời với các lần khác; chỉ tới lúc cần chọn chương (đã mở thử xong, sắp tạo tài liệu) lần nhận mới CHỜ mọi phiếu
đến trước của cùng môn kết thúc (đã tạo tài liệu, trả bản cũ, hay lỗi: phiếu được trả ở `finally`), rồi chọn chương từ
kho. Phiếu đến sau không chọn trước phiếu đến trước nên chương không bị chiếm bởi một lần nhận rồi lỗi.

- Nhập lại cùng nội dung: chỉ có một phiếu sống cho mỗi `document_id` (toàn cục, kể cả khác môn). Lần nhận thứ hai
  cùng nội dung chờ lần đầu kết thúc rồi mới lấy phiếu: thấy tài liệu đã có thì trả bản cũ và KHÔNG chọn chương (không
  chiếm chương nào); lần đầu lỗi thì lần hai thành lần nhận chính.
- Mã người dùng nhập (`chapter_code`) không phụ thuộc lần nhận khác nên không chờ.

Không có vòng chờ: lần nhận chỉ chờ phiếu đến trước nó (khác `document_id`, vì mỗi `document_id` chỉ một phiếu sống),
mà phiếu đó chỉ có thể chờ phiếu còn sớm hơn; khoá của tài liệu chỉ do chính chủ phiếu tương ứng hoặc luồng đọc (không
chờ phiếu nào) giữ.

Thứ tự khoá: khoá môn chỉ giữ trong lúc lấy phiếu, nhả TRƯỚC khi lấy khoá của tài liệu; không bao giờ lấy khoá môn
khi đang giữ khoá tài liệu.
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from typing import Protocol

from ..domain.repositories import DocumentRepository
from .chapter_naming import InitialChapter, initial_chapter


class KeyedLock(Protocol):
    def hold(self, key: str) -> AbstractContextManager[None]: ...


@dataclass(eq=False)
class ChapterTicket:
    """Phiếu của một lần nhận đang chạy; `done` được bật khi lần nhận kết thúc (mọi kết cục)."""

    subject_code: str
    document_id: str
    order: int
    done: threading.Event = field(default_factory=threading.Event)


class ChapterReservations:
    """Các phiếu đang sống của lần nhận file; thứ tự phiếu tính theo từng môn."""

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._live: list[ChapterTicket] = []
        self._next_order: dict[str, int] = {}

    def open(self, subject_code: str, document_id: str) -> tuple[ChapterTicket, bool]:
        """Lấy phiếu mới. Đã có phiếu sống của cùng `document_id` thì không lấy: trả phiếu đó kèm `False`."""
        with self._guard:
            for live in self._live:
                if live.document_id == document_id:
                    return live, False
            order = self._next_order.get(subject_code, 0)
            self._next_order[subject_code] = order + 1
            ticket = ChapterTicket(subject_code, document_id, order)
            self._live.append(ticket)
            return ticket, True

    def earlier(self, ticket: ChapterTicket) -> list[ChapterTicket]:
        """Phiếu sống của cùng môn đến trước `ticket`."""
        with self._guard:
            return [
                live for live in self._live if live.subject_code == ticket.subject_code and live.order < ticket.order
            ]

    def close(self, ticket: ChapterTicket) -> None:
        with self._guard:
            self._live.remove(ticket)
            if not any(live.subject_code == ticket.subject_code for live in self._live):
                del self._next_order[ticket.subject_code]
        ticket.done.set()

    def __len__(self) -> int:
        """Số phiếu đang sống (mọi môn)."""
        with self._guard:
            return len(self._live)


@dataclass(frozen=True)
class ChapterTurn:
    """Lượt nhận của một file: `settle` chọn chương và tên tạm khi tới lúc tạo tài liệu."""

    reservations: ChapterReservations
    ticket: ChapterTicket
    store: DocumentRepository
    filename: str
    chapter_code: str | None

    def settle(self) -> InitialChapter:
        """Chờ mọi lần nhận đến trước của cùng môn kết thúc (trừ khi mã do người dùng nhập, không phụ thuộc ai), rồi
        chọn chương từ kho. Gọi một lần, ngay trước khi dựng bản ghi tài liệu mới; không giữ khoá môn."""
        if not self.chapter_code:
            for earlier in self.reservations.earlier(self.ticket):
                earlier.done.wait()
        return initial_chapter(
            self.filename,
            self.chapter_code,
            self.store.list_imported_documents(self.ticket.subject_code),
            self.ticket.document_id,
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
    """Lấy phiếu theo thứ tự đến (dưới khoá của môn, nhả ngay), giữ tới khi thoát khối. Cùng nội dung đang được nhận
    ở lần khác thì chờ lần đó kết thúc rồi thử lại."""
    while True:
        with subject_locks.hold(subject_code):
            ticket, opened = reservations.open(subject_code, document_id)
        if opened:
            break
        ticket.done.wait()
    try:
        yield ChapterTurn(reservations, ticket, store, filename, chapter_code)
    finally:
        reservations.close(ticket)
