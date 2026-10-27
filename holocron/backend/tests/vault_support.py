"""Hỗ trợ chung cho test Knowledge Vault: ba loại kho (in-memory, SQLite có FTS5, SQLite thiếu FTS5) và dữ liệu mẫu.

Văn bản mẫu tự viết cho test (không phải tài liệu môn học có bản quyền).
"""

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from holocron_core.domain.ingestion import UploadedDocument
from holocron_core.domain.repositories import AppStore
from holocron_core.domain.vault import ConfirmedDocumentText, ConfirmedPage
from holocron_core.infrastructure.memory_store import IngestionStore
from holocron_core.infrastructure.storage import SQLiteStore, sqlite_store

STORE_KINDS = ("memory", "sqlite", "sqlite_no_fts")
NOW = datetime(2026, 10, 25, 8, 0, tzinfo=UTC)


def document_id(seed: str) -> str:
    return "doc_" + (seed * 16)[:16]


def add_document(store: AppStore, doc_id: str) -> None:
    """Tạo bản ghi `documents` mà chunk trỏ tới (cả hai kho đều kiểm khoá ngoại tài liệu)."""
    store.save_document(
        UploadedDocument(
            document_id=doc_id,
            sha256=(doc_id[4:] * 4)[:64],
            filename="tai_lieu.pdf",
            media_type="application/pdf",
            size_bytes=10,
            uploaded_at=NOW,
        )
    )


def open_store(kind: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[AppStore]:
    if kind == "memory":
        yield IngestionStore()
        return
    if kind == "sqlite_no_fts":
        monkeypatch.setattr(sqlite_store, "fts5_available", lambda conn: False)
    opened = SQLiteStore.open(tmp_path / f"{kind}.sqlite3")
    try:
        yield opened
    finally:
        opened.close()


def confirmed(
    doc_id: str,
    pages: list[list[str]],
    *,
    subject_code: str = "ET4010",
    chapter_code: str = "ch3",
    category: str = "THEORY",
    topic_keys: list[str] | None = None,
    first_page: int = 1,
) -> ConfirmedDocumentText:
    return ConfirmedDocumentText(
        doc_id=doc_id,
        subject_code=subject_code,
        chapter_code=chapter_code,
        category=category,
        topic_keys=topic_keys or [],
        pages=[ConfirmedPage(page_number=first_page + index, lines=lines) for index, lines in enumerate(pages)],
    )


ARM_PAGES = [
    [
        "3.1 Thanh ghi trạng thái",
        "Thanh ghi CPSR chứa các cờ trạng thái N, Z, C, V của bộ vi xử lý ARM.",
        "Cờ Z được đặt khi kết quả phép toán bằng không.",
        "",
        "Cờ C được đặt khi phép cộng có nhớ ra khỏi bit cao nhất.",
    ],
    [
        "3.2 Lệnh cộng",
        "Lệnh ADDS cộng hai thanh ghi và cập nhật cờ trong CPSR.",
        "Ví dụ: ADDS R0, R1, R2 ghi tổng vào R0.",
    ],
]

PHILOSOPHY_PAGES = [
    [
        "Chương 1 Vật chất và ý thức",
        "Vật chất là phạm trù triết học dùng để chỉ thực tại khách quan.",
        "Ý thức là sự phản ánh năng động, sáng tạo thế giới khách quan vào bộ óc con người.",
    ],
]
