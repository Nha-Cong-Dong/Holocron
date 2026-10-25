"""Giao thức giữa tiến trình cha và worker đọc PDF (ADR-008 mục 3).

Tiến trình cha gửi `WorkerRequest` rồi từng `PageRequest` qua stdin, mỗi dòng một JSON; worker chỉ đọc trang khi
được yêu cầu, nên wall-clock timeout của tiến trình cha đo đúng thời gian đọc một trang.
Worker ghi mỗi thông điệp trên một dòng stdout, có tiền tố `MESSAGE_PREFIX`, nội dung là JSON của một trong các
model dưới đây. Tiến trình cha bỏ qua dòng không có tiền tố và validate từng thông điệp bằng Pydantic: dữ liệu
từ worker không được tin cậy (worker đọc file không tin cậy).
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from .config import PdfReaderConfig

MESSAGE_PREFIX = "HOLOCRON_PDF:"

UnitFloat = Annotated[float, Field(ge=0.0, le=1.0)]
UnitBBox = tuple[UnitFloat, UnitFloat, UnitFloat, UnitFloat]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


class WorkerRequest(_Frozen):
    """Dòng đầu tiên gửi qua stdin khi khởi động worker.

    `page_budget` là số trang tiến trình cha định đọc bằng worker này (0: chỉ mở thử và báo số trang); worker dùng
    nó để đặt giới hạn CPU và không đọc quá số trang đó.
    """

    path: str = Field(min_length=1)
    page_budget: int = Field(ge=0)
    config: PdfReaderConfig


class PageRequest(_Frozen):
    """Mỗi dòng tiếp theo trên stdin: yêu cầu đọc đúng một trang (0-based). Đóng stdin để worker thoát."""

    page_index: int = Field(ge=0)


class RawWord(_Frozen):
    """Một cụm ký tự không trắng liền nhau trong dòng; `bbox` chuẩn hoá về [0, 1], gốc trên-trái."""

    text: str
    bbox: UnitBBox


class RawTextLine(_Frozen):
    """Một dòng của lớp chữ theo thứ tự đọc của PDFium; `text` giữ nguyên khoảng trắng bên trong."""

    text: str
    bbox: UnitBBox | None
    words: list[RawWord]


class RawPdfPage(_Frozen):
    """Dữ liệu thô của một trang. Khổ trang tính bằng point, theo hướng hiển thị (đã áp `/Rotate`)."""

    page_index: int = Field(ge=0)
    width_pt: float = Field(gt=0)
    height_pt: float = Field(gt=0)
    lines: list[RawTextLine]
    image_area_ratio: UnitFloat
    path_area_ratio: UnitFloat


class ReadyMessage(_Frozen):
    kind: Literal["ready"] = "ready"


class OpenedMessage(_Frozen):
    kind: Literal["opened"] = "opened"
    page_count: int = Field(ge=0)


class OpenFailedMessage(_Frozen):
    kind: Literal["open_failed"] = "open_failed"
    code: Literal["PASSWORD_REQUIRED", "OPEN_FAILED"]


class PageMessage(_Frozen):
    kind: Literal["page"] = "page"
    page: RawPdfPage


class PageErrorMessage(_Frozen):
    kind: Literal["page_error"] = "page_error"
    page_index: int = Field(ge=0)
    message: str = Field(max_length=500)


WorkerMessage = Annotated[
    ReadyMessage | OpenedMessage | OpenFailedMessage | PageMessage | PageErrorMessage,
    Field(discriminator="kind"),
]
WORKER_MESSAGE: TypeAdapter[WorkerMessage] = TypeAdapter(WorkerMessage)


def encode_message(message: BaseModel) -> str:
    return MESSAGE_PREFIX + message.model_dump_json()


def decode_message(line: str) -> WorkerMessage | None:
    """Dòng không có tiền tố trả `None`; dòng có tiền tố nhưng sai dạng ném `pydantic.ValidationError`."""
    stripped = line.rstrip("\r\n")
    if not stripped.startswith(MESSAGE_PREFIX):
        return None
    return WORKER_MESSAGE.validate_json(stripped[len(MESSAGE_PREFIX) :])
