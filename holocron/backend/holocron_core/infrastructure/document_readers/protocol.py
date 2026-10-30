"""Giao thức giữa tiến trình cha và worker đọc PDF (ADR-008 mục 3, 8, 11).

Tiến trình cha gửi `WorkerRequest` rồi từng `PageRequest` qua stdin, mỗi dòng một JSON; worker chỉ đọc trang khi
được yêu cầu, nên wall-clock timeout của tiến trình cha đo đúng thời gian đọc một trang.
Worker ghi mỗi thông điệp trên một dòng stdout, có tiền tố `MESSAGE_PREFIX`, nội dung là JSON của một trong các
model dưới đây. Tiến trình cha bỏ qua dòng không có tiền tố và validate từng thông điệp bằng Pydantic: dữ liệu
từ worker không được tin cậy (worker đọc file không tin cậy).
Trang cần OCR có thêm `ocr_started` trước `page`; kết quả OCR (dòng Tesseract, token của lượt thứ hai) nằm trong
`RawPdfPage.ocr` và chịu cùng các giới hạn số ký tự, số dòng, số từ dưới đây.
Worker được yêu cầu quét ảnh template (`WorkerRequest.scan_templates`) gửi `template_scan_started` ngay sau `opened`
rồi `template_scan` khi quét xong, trước trang đầu; tiến trình cha nhận hai thông điệp này trong lượt chờ trang đầu
(như `ocr_started`) và chuyển sang hạn riêng của lượt quét. Số khoá ảnh template có trần `MAX_TEMPLATE_KEYS` (sửa đổi
2026-10-30).

GĐ0 (sửa đổi 2026-10-31): lượt quét trả thêm khoá đường vẽ (`PathKey`) và dòng chữ lặp (`TextKey`, header/footer),
mỗi loại có trần `MAX_TEMPLATE_KEYS` riêng; `RawPdfPage` mang thêm dữ liệu khử nhiễu của worker (ảnh nội dung, diện
tích đường vẽ nội dung, cờ dòng, chữ ẩn đã loại, lớp OCR của PDF scan, trang vượt trần đối tượng). Đây là giao thức nội
bộ giữa worker và tiến trình cha, không phải contract API (spec §5.7).
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

# Ảnh template (sửa đổi 2026-10-30): trần số khoá ảnh template của một tài liệu gửi qua giao thức, số bộ lọc và độ
# dài tên bộ lọc của một ảnh, và chỉ số ô lưới lớn nhất của hộp ảnh (1 / bước nhỏ nhất `template_image_bbox_step`).
# Tài liệu có nhiều ảnh template hơn `MAX_TEMPLATE_KEYS` bị coi là vượt trần (không ảnh nào là template).
MAX_TEMPLATE_KEYS = 1024
MAX_IMAGE_FILTERS = 16
MAX_IMAGE_FILTER_CHARS = 64
MAX_GRID_INDEX = 10_000

GridIndex = Annotated[int, Field(ge=0, le=MAX_GRID_INDEX)]
GridBox = tuple[GridIndex, GridIndex, GridIndex, GridIndex]
ColorChannel = Annotated[int, Field(ge=0, le=255)]
Rgba = tuple[ColorChannel, ColorChannel, ColorChannel, ColorChannel]
# Dòng chữ dài hơn mức này không có khoá TEXT (header/footer là dòng ngắn); giữ thông điệp lượt quét nhỏ.
MAX_TEMPLATE_TEXT_CHARS = 500
MAX_PATH_SEGMENTS = 10_000_000
# Số ảnh nội dung tối đa của một trang gửi qua giao thức (= trần `PageExtraction.own_images`).
MAX_OWN_IMAGES = 256
RawLineFlag = Literal["LOW_TEXT_CONTRAST", "TEXT_OCCLUDED"]
TemplateScanStatus = Literal["ok", "over_cap", "timeout", "error", "skipped"]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


class ImageKey(_Frozen):
    """Khoá của một lần vẽ ảnh nhúng trên trang: nội dung VÀ vị trí (`template_images.image_key`).

    `digest` là SHA-256 (hex) của dữ liệu thô của luồng ảnh (chưa giải nén); `bbox` là hộp của ảnh chuẩn hoá về
    [0, 1] theo khổ trang đã xoay, làm tròn thành chỉ số ô lưới theo `template_image_bbox_step`. Không có khoá nào
    chỉ dựa trên metadata.
    """

    digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    width_px: int = Field(ge=0, le=1_000_000)
    height_px: int = Field(ge=0, le=1_000_000)
    bits_per_pixel: int = Field(default=0, ge=0, le=0)
    filters: tuple[Annotated[str, Field(max_length=MAX_IMAGE_FILTER_CHARS)], ...] = Field(max_length=MAX_IMAGE_FILTERS)
    bbox: GridBox


class PathKey(_Frozen):
    """Khoá của một đường vẽ (GĐ0 0A): hộp lượng tử, màu tô RGBA (None: không tô), màu viền RGBA (None: không viền),
    số đoạn của path (`template_images.path_key`)."""

    bbox: GridBox
    fill: Rgba | None
    stroke: Rgba | None
    segments: int = Field(ge=0, le=MAX_PATH_SEGMENTS)


class TextKey(_Frozen):
    """Khoá của một dòng chữ (GĐ0 0A, 0B(5)): hộp lượng tử của dòng và chuỗi chuẩn hoá (`template_images.text_key`)."""

    bbox: GridBox
    text: str = Field(min_length=1, max_length=MAX_TEMPLATE_TEXT_CHARS)


TemplateKey = ImageKey | PathKey | TextKey


class TemplateScan(_Frozen):
    """Kết quả lượt quét ảnh template của cả tài liệu (ADR-008 mục 6; sửa đổi 2026-10-30).

    - `ok`: quét xong; `keys` là các ảnh template (có thể rỗng).
    - `over_cap`: vượt trần số ảnh, số byte đem băm hoặc số khoá; `timeout`: quá `template_scan_timeout_s`;
      `error`: lỗi khi quét; `skipped`: không quét (worker của `probe`, hay worker không được yêu cầu quét).
    Mọi trạng thái khác `ok` có `keys` rỗng: không ảnh nào là template (hành vi cũ). `path_keys`, `text_keys` (GĐ0):
    đường vẽ template và dòng chữ lặp (header/footer); cùng quy tắc.
    """

    status: TemplateScanStatus
    keys: list[ImageKey] = Field(default_factory=list, max_length=MAX_TEMPLATE_KEYS)
    path_keys: list[PathKey] = Field(default_factory=list, max_length=MAX_TEMPLATE_KEYS)
    text_keys: list[TextKey] = Field(default_factory=list, max_length=MAX_TEMPLATE_KEYS)

    @model_validator(mode="after")
    def _keys_only_when_ok(self) -> TemplateScan:
        if self.status != "ok" and (self.keys or self.path_keys or self.text_keys):
            raise ValueError("chỉ lượt quét `ok` mới có khoá template")
        return self


class WorkerRequest(_Frozen):
    """Dòng đầu tiên gửi qua stdin khi khởi động worker.

    `page_budget` là số trang tiến trình cha định đọc bằng worker này (0: chỉ mở thử và báo số trang); worker dùng
    nó để đặt giới hạn CPU và không đọc quá số trang đó.

    Ảnh template: `scan_templates` yêu cầu worker quét ảnh của cả tài liệu sau khi mở (chỉ khi `page_budget ≥ 1`),
    báo `TemplateScanStartedMessage` rồi `TemplateScanMessage`; `template_scan` là kết quả quét tiến trình cha đã
    giữ từ worker trước (worker dựng lại sau khi trang lỗi hay quá hạn), worker dùng đúng kết quả đó và không quét
    lại. Không có cả hai ⇒ không ảnh nào là template.
    """

    path: str = Field(min_length=1)
    page_budget: int = Field(ge=0)
    config: PdfReaderConfig
    ocr: OcrSettings | None = None
    scan_templates: bool = False
    template_scan: TemplateScan | None = None

    @model_validator(mode="after")
    def _scan_or_reuse(self) -> WorkerRequest:
        if self.scan_templates and self.template_scan is not None:
            raise ValueError("worker không vừa quét ảnh template vừa nhận kết quả quét có sẵn")
        return self


class PageRequest(_Frozen):
    """Mỗi dòng tiếp theo trên stdin: yêu cầu đọc đúng một trang (0-based). Đóng stdin để worker thoát."""

    page_index: int = Field(ge=0)


class RawWord(_Frozen):
    """Một cụm ký tự không trắng liền nhau trong dòng; `bbox` chuẩn hoá về [0, 1], gốc trên-trái."""

    text: str = Field(max_length=MAX_RAW_LINE_CHARS)
    bbox: UnitBBox


class RawTextLine(_Frozen):
    """Một dòng của lớp chữ theo thứ tự đọc của PDFium; `text` giữ nguyên khoảng trắng bên trong.

    GĐ0: `flags` là cờ dòng do worker đo (tương phản thấp, bị che); `header_footer` là dòng khớp một khoá TEXT
    template của tài liệu (0B(5)). Dòng OCR luôn để mặc định.
    """

    text: str = Field(max_length=MAX_RAW_LINE_CHARS)
    bbox: UnitBBox | None
    words: list[RawWord] = Field(max_length=MAX_RAW_LINE_CHARS)
    flags: list[RawLineFlag] = Field(default_factory=list, max_length=2)
    header_footer: bool = False


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


class RawPageImage(_Frozen):
    """Một ảnh nội dung (không-template) của trang, theo thứ tự vẽ (GĐ0 0A); `dhash` là None khi không render được."""

    bbox: UnitBBox
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    dhash: str | None = Field(default=None, pattern=r"^[0-9a-f]{16}$")


class RawPdfPage(_Frozen):
    """Dữ liệu thô của một trang. Khổ trang tính bằng point, theo hướng hiển thị (đã áp `/Rotate`).

    `ocr` chỉ có khi trang cần OCR theo `text_layer.needs_ocr_page` và worker có engine OCR.
    `image_area_ratio` là diện tích hợp của MỌI ảnh nhúng; `content_image_area_ratio` là diện tích hợp của các ảnh
    KHÔNG phải template (sửa đổi 2026-10-30), None khi tài liệu không có ảnh template (khi đó dùng
    `image_area_ratio`, xem `text_layer.content_image_area`).

    GĐ0 (sửa đổi 2026-10-31): `content_path_area_ratio` là tổng diện tích (chặn ở 1) đường vẽ không-template,
    không-nền (None ⇒ `path_area_ratio`, dữ liệu trước GĐ0); `own_images` là ảnh nội dung; `hidden_chars_dropped` là số
    ký tự bị loại vì chữ ẩn (không có trong `lines`); `scan_text_layer` là trang được nhận là lớp OCR của PDF scan;
    `objects_over_cap` là trang vượt trần đối tượng (`page_objects_max`, `pdf_form_max_depth`); `template_scan_ok` là
    False khi lượt quét cấp tài liệu không `ok` (worker đặt).
    """

    page_index: int = Field(ge=0)
    width_pt: float = Field(gt=0)
    height_pt: float = Field(gt=0)
    lines: list[RawTextLine] = Field(max_length=MAX_PAGE_LINES)
    image_area_ratio: UnitFloat
    path_area_ratio: UnitFloat
    ocr: RawOcrPage | None = None
    content_image_area_ratio: UnitFloat | None = None
    content_path_area_ratio: UnitFloat | None = None
    own_images: list[RawPageImage] = Field(default_factory=list, max_length=MAX_OWN_IMAGES)
    hidden_chars_dropped: int = Field(default=0, ge=0, le=MAX_PAGE_WORDS * MAX_RAW_LINE_CHARS)
    scan_text_layer: bool = False
    objects_over_cap: bool = False
    template_scan_ok: bool = True

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


class TemplateScanStartedMessage(_Frozen):
    """Worker bắt đầu quét ảnh template (ngay sau `opened`); tiến trình cha chờ theo hạn `template_scan_timeout_s`."""

    kind: Literal["template_scan_started"] = "template_scan_started"


class TemplateScanMessage(_Frozen):
    """Kết quả lượt quét ảnh template, gửi sau `TemplateScanStartedMessage`, trước trang đầu."""

    kind: Literal["template_scan"] = "template_scan"
    scan: TemplateScan


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
    ReadyMessage
    | OpenedMessage
    | OpenFailedMessage
    | TemplateScanStartedMessage
    | TemplateScanMessage
    | OcrStartedMessage
    | PageMessage
    | PageErrorMessage,
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
