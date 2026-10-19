"""Trích xuất đề từ file tải lên (ảnh/PDF) thành dữ liệu cho Ingestion Gate.

`DocumentExtractor` là ranh giới với VLM: mọi output của nó bị coi là KHÔNG tin cậy và luôn
phải đi qua `check_table` (5 tín hiệu) trước khi tới sandbox. Tuần 2 dùng `MockDualPassExtractor`
(tất định, không gọi mạng) để Frontend và test có dữ liệu thật đi qua toàn bộ Gate; Tuần 3 thay
bằng adapter VLM thật qua dependency injection mà không đổi router.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from ..domain.schemas import OptionKey
from .ingestion_gate import ExtractedCell, ExtractedTable

MediaType = Literal["image/png", "image/jpeg", "image/webp", "application/pdf"]

_MAGIC_PREFIXES: tuple[tuple[bytes, MediaType], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"%PDF-", "application/pdf"),
)


def sniff_media_type(content: bytes) -> MediaType | None:
    """Xác định định dạng bằng magic bytes; không tin `Content-Type` do client khai báo."""
    for prefix, media_type in _MAGIC_PREFIXES:
        if content.startswith(prefix):
            return media_type
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    return None


class UploadedDocument(BaseModel):
    model_config = ConfigDict(frozen=True)

    document_id: str = Field(pattern=r"^doc_[0-9a-f]{16}$")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    filename: str = Field(min_length=1, max_length=255)
    media_type: MediaType
    size_bytes: int = Field(ge=1)
    uploaded_at: datetime

    @classmethod
    def from_content(cls, content: bytes, filename: str, media_type: MediaType) -> UploadedDocument:
        """`document_id` suy ra từ sha256 nội dung: cùng file ⇒ cùng id (tải lại không nhân bản tài liệu)."""
        digest = hashlib.sha256(content).hexdigest()
        return cls(
            document_id=f"doc_{digest[:16]}",
            sha256=digest,
            filename=filename,
            media_type=media_type,
            size_bytes=len(content),
            uploaded_at=datetime.now(UTC),
        )


class DualPassExtraction(BaseModel):
    """Kết quả hai lượt đọc độc lập trên cùng một trang đề."""

    page_number: int = Field(ge=1)
    stem_latex: str
    options: dict[OptionKey, str]
    table_primary: ExtractedTable
    table_secondary: ExtractedTable
    page_text_layer: str | None = None
    quantum: int | None = Field(default=None, ge=1, le=20)


class UnsupportedTopicError(ValueError):
    """Extractor chưa hỗ trợ topic được yêu cầu."""


class DocumentExtractor(Protocol):
    def extract(self, document: UploadedDocument, content: bytes, topic_key: str) -> DualPassExtraction: ...


RR_DEMO_HEADERS = ["Tiến trình", "Thời điểm đến", "Burst time"]
RR_DEMO_ROWS: tuple[tuple[str, str, str], ...] = (
    ("P1", "0", "2"),
    ("P2", "2", "9"),
    ("P3", "5", "5"),
    ("P4", "8", "2"),
    ("P5", "11", "4"),
)
RR_DEMO_MISREAD: tuple[int, int, str, float] = (1, 2, "8", 0.61)
RR_DEMO_OPTIONS: dict[OptionKey, str] = {
    OptionKey.A: "6.00",
    OptionKey.B: "4.20",
    OptionKey.C: "2.60",
    OptionKey.D: "10.40",
}
RR_DEMO_STEM = (
    r"Cho 5 tiến trình được lập lịch Round Robin với $q = 4$ ms. "
    r"Tính thời gian chờ trung bình $\overline{WT}$."
)


class MockDualPassExtractor:
    """Giả lập VLM dual-pass cho đề Round Robin mẫu, tất định theo nội dung file.

    Lượt A đọc nhầm burst time của P2 ("9" thành "8", confidence 0.61); lượt B đọc đúng. Nhờ vậy
    một lần tải lên đi qua đủ đường Gate thật: DUAL_PASS_MISMATCH + LOW_CONFIDENCE + SELF_SOLVE_MISMATCH,
    rồi người dùng sửa ô thành "9" để Gate chuyển sang AUTO_PASS.
    """

    supported_topics: frozenset[str] = frozenset({"os.cpu_sched.rr.avg_waiting_time"})

    def extract(self, document: UploadedDocument, content: bytes, topic_key: str) -> DualPassExtraction:
        if topic_key not in self.supported_topics:
            raise UnsupportedTopicError(f"extractor chưa hỗ trợ topic {topic_key}")
        table_id = f"tbl_{document.sha256[:12]}"
        return DualPassExtraction(
            page_number=1,
            stem_latex=RR_DEMO_STEM,
            options=dict(RR_DEMO_OPTIONS),
            table_primary=self._table(table_id, misread=True),
            table_secondary=self._table(table_id, misread=False),
            page_text_layer=None,
            quantum=4,
        )

    @staticmethod
    def _table(table_id: str, *, misread: bool) -> ExtractedTable:
        bad_row, bad_col, bad_text, bad_confidence = RR_DEMO_MISREAD
        cells: list[ExtractedCell] = []
        for row, values in enumerate(RR_DEMO_ROWS):
            for col, text in enumerate(values):
                if misread and (row, col) == (bad_row, bad_col):
                    cells.append(ExtractedCell(row=row, col=col, text=bad_text, confidence=bad_confidence))
                else:
                    cells.append(ExtractedCell(row=row, col=col, text=text, confidence=0.97))
        return ExtractedTable(table_id=table_id, page_number=1, headers=list(RR_DEMO_HEADERS), cells=cells)
