"""Giao thức giữa tiến trình cha và worker đọc PDF (ADR-008 mục 3, 8, 11).

Tiến trình cha gửi `WorkerRequest` rồi từng `PageRequest` qua stdin, mỗi dòng một JSON; worker chỉ đọc trang khi
được yêu cầu, nên wall-clock timeout của tiến trình cha đo đúng thời gian đọc một trang.
Worker ghi mỗi thông điệp trên một dòng stdout, có tiền tố `MESSAGE_PREFIX`, nội dung là JSON của một trong các
model dưới đây. Tiến trình cha bỏ qua dòng không có tiền tố và validate từng thông điệp bằng Pydantic: dữ liệu
từ worker không được tin cậy (worker đọc file không tin cậy).
Trang cần OCR có thêm `ocr_started` trước `page`; kết quả OCR (dòng Tesseract, token của lượt thứ hai) nằm trong
`RawPdfPage.ocr` và chịu cùng các giới hạn số ký tự, số dòng, số từ dưới đây.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from .config import OcrSettings, PdfReaderConfig, SecondaryEngine

MESSAGE_PREFIX = "HOLOCRON_PDF:"

# Giới hạn cấu trúc của dữ liệu thô từ worker (ngoài giới hạn byte mỗi thông điệp `max_message_bytes`), để một
# worker bị khai thác không đẩy được cấu trúc khổng lồ vào tiến trình cha.
# - Dòng thô dài tối đa 4 lần `ExtractedLine.text` (2000 ký tự, SPECIFICATION §5.7): tiến trình cha chuẩn hoá NFC
#   rồi mới cắt thành các dòng ≤ 2000 (`tiers._chunks`), vì NFC có thể làm chuỗi dài ra; worker tự ngắt dòng dài
#   hơn giới hạn này (`text_layer.group_lines`) nên PDF hợp lệ không bị loại vì một dòng quá dài.
# - Một trang A4 dày đặc có cỡ vài nghìn từ và vài trăm dòng; giới hạn dưới đây cao hơn nhiều lần.
MAX_RAW_LINE_CHARS = 8000
MAX_PAGE_LINES = 10_000
MAX_PAGE_WORDS = 50_000

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
    ocr: OcrSettings | None = None


class PageRequest(_Frozen):
    """Mỗi dòng tiếp theo trên stdin: yêu cầu đọc đúng một trang (0-based). Đóng stdin để worker thoát."""

    page_index: int = Field(ge=0)


class RawWord(_Frozen):
    """Một cụm ký tự không trắng liền nhau trong dòng; `bbox` chuẩn hoá về [0, 1], gốc trên-trái."""

    text: str = Field(max_length=MAX_RAW_LINE_CHARS)
    bbox: UnitBBox


class RawTextLine(_Frozen):
    """Một dòng của lớp chữ theo thứ tự đọc của PDFium; `text` giữ nguyên khoảng trắng bên trong."""

    text: str = Field(max_length=MAX_RAW_LINE_CHARS)
    bbox: UnitBBox | None
    words: list[RawWord] = Field(max_length=MAX_RAW_LINE_CHARS)


class RawOcrLine(RawTextLine):
    """Một dòng của lượt đọc chính (Tesseract); `conf` là trung bình `conf` của các từ, trọng số theo số ký tự."""

    conf: UnitFloat


class RawSecondaryLine(_Frozen):
    """Một dòng của lượt đọc thứ hai: chỉ hộp toạ độ và token số/mã (ADR-008 mục 11), không mang văn bản."""

    bbox: UnitBBox
    tokens: list[Annotated[str, Field(min_length=1, max_length=MAX_RAW_LINE_CHARS)]] = Field(
        max_length=MAX_RAW_LINE_CHARS
    )


class RawSecondaryPass(_Frozen):
    engine: SecondaryEngine
    lines: list[RawSecondaryLine] = Field(max_length=MAX_PAGE_LINES)

    @model_validator(mode="after")
    def _bounded_tokens(self) -> RawSecondaryPass:
        if sum(len(line.tokens) for line in self.lines) > MAX_PAGE_WORDS:
            raise ValueError(f"lượt đọc thứ hai có quá {MAX_PAGE_WORDS} token")
        return self


class RawOcrPage(_Frozen):
    """Kết quả OCR của một trang (tầng 2). `secondary` là None khi không có hoặc không chạy được lượt thứ hai."""

    dpi: float = Field(gt=0)
    lines: list[RawOcrLine] = Field(max_length=MAX_PAGE_LINES)
    ink_outside_text_ratio: UnitFloat
    secondary: RawSecondaryPass | None

    @model_validator(mode="after")
    def _bounded_words(self) -> RawOcrPage:
        if sum(len(line.words) for line in self.lines) > MAX_PAGE_WORDS:
            raise ValueError(f"trang có quá {MAX_PAGE_WORDS} từ")
        return self


class RawPdfPage(_Frozen):
    """Dữ liệu thô của một trang. Khổ trang tính bằng point, theo hướng hiển thị (đã áp `/Rotate`).

    `ocr` chỉ có khi lớp chữ không đủ tốt (`text_score < TEXT_LAYER_MIN`) và worker có engine OCR.
    """

    page_index: int = Field(ge=0)
    width_pt: float = Field(gt=0)
    height_pt: float = Field(gt=0)
    lines: list[RawTextLine] = Field(max_length=MAX_PAGE_LINES)
    image_area_ratio: UnitFloat
    path_area_ratio: UnitFloat
    ocr: RawOcrPage | None = None

    @model_validator(mode="after")
    def _bounded_words(self) -> RawPdfPage:
        if sum(len(line.words) for line in self.lines) > MAX_PAGE_WORDS:
            raise ValueError(f"trang có quá {MAX_PAGE_WORDS} từ")
        return self


class ReadyMessage(_Frozen):
    kind: Literal["ready"] = "ready"


class OpenedMessage(_Frozen):
    kind: Literal["opened"] = "opened"
    page_count: int = Field(ge=0)


class OpenFailedMessage(_Frozen):
    kind: Literal["open_failed"] = "open_failed"
    code: Literal["PASSWORD_REQUIRED", "OPEN_FAILED"]


class OcrStartedMessage(_Frozen):
    """Worker báo trang `page_index` cần OCR; tiến trình cha chuyển sang hạn `ocr_page_timeout_s` cho trang đó."""

    kind: Literal["ocr_started"] = "ocr_started"
    page_index: int = Field(ge=0)


class PageMessage(_Frozen):
    kind: Literal["page"] = "page"
    page: RawPdfPage


class PageErrorMessage(_Frozen):
    kind: Literal["page_error"] = "page_error"
    page_index: int = Field(ge=0)
    message: str = Field(max_length=500)


WorkerMessage = Annotated[
    ReadyMessage | OpenedMessage | OpenFailedMessage | OcrStartedMessage | PageMessage | PageErrorMessage,
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
