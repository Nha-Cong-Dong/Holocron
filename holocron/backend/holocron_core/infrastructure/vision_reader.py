"""Bộ đọc vùng VLM (ADR-009 mục 1, 2, 4, 5, 8, 9; spec §5.7 "Khung VLM đọc vùng", §5.16).

`VisionReader` gửi MỘT ảnh vùng (PNG đã cắt qua `CropRequest`) tới nhà cung cấp VLM và trả `VlmRegionResult` (nội bộ,
không ra response API). Đầu ra VLM là dữ liệu KHÔNG tin cậy (LLM Gate, CLAUDE.md §2.1): kết quả luôn đi qua cổng
`services/vision_gate.py` trước khi chữ nào thành văn bản khối; kể cả khi trúng cache, cổng vẫn chạy lại.

Chỉ gọi khi đủ điều kiện (mục 1): `AiConsent.auto_send_allowed("image")` đúng ở CHÍNH lời gọi đó, `ProviderSelector`
chọn được nhà cung cấp chưa chạm trần (chi phí tối đa của lời gọi được giữ chỗ), ảnh qua kiểm của tiến trình cha, và
chưa vượt trần vùng của trang/tài liệu. Thiếu một điều kiện ⇒ không gọi mạng, không tra cache (`VisionSkipped`).

Request chỉ có prompt v1 nguyên văn và ảnh; VLM KHÔNG nhận token hay chữ OCR. Không đặt `temperature`/`top_p`.
Phản hồi hỏng ⇒ gửi lại đúng một lần (cùng message, thêm câu nhắc cố định); hỏng nữa ⇒ `vlm_invalid`. Timeout, 429,
lỗi mạng, thiếu cấu hình ⇒ `timeout`/`upstream_error`, không ném ra luồng nhập. Log chỉ mã lỗi: không khoá, endpoint,
tên model hay nội dung.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import threading
import time
import unicodedata
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..domain.ai import (
    AiCacheEntry,
    AiCacheKind,
    AiConsent,
    AiUsage,
    CostEstimate,
    ProviderSelector,
    SelectedProvider,
    UsageRecorder,
    ai_cache_key,
)
from ..domain.blocks import BlockType
from ..domain.llm import (
    PNG_DATA_URI_PREFIX,
    ChatCompletionClient,
    ChatCompletionResult,
    ChatMessage,
    ContentPart,
    ImageTooLargeError,
    ImageUrl,
    ImageUrlPart,
    LlmUpstreamError,
    TextPart,
)
from ..domain.repositories import AiCacheRepository
from .document_readers.config import PdfReaderConfig
from .document_readers.raster import png_dimensions
from .llm_client import OpenAiChatClient, Transport, UrllibTransport
from .llm_config import ENV_VLM_API_KEY, ENV_VLM_BASE_URL, ConfigError, VlmSettings

PROMPT_VERSION = "v1"

# Prompt v1 (người dùng gửi 2026-10-29), chép nguyên văn từ `.claude/state/vlm/prompt-v1.txt` (ADR-009 mục 4). Đổi
# bất kỳ ký tự nào của hai văn bản dưới đây phải tăng `PROMPT_VERSION` (giá trị này nằm trong khoá cache).
PROMPT_V1 = """Bạn chép lại nội dung một trang tài liệu học tập tiếng Việt từ ảnh.
Chỉ chép những gì NHÌN THẤY. Không thêm, không sửa lỗi chính tả, không suy đoán.
Chỗ không đọc được ghi đúng "[không đọc được]".
Trả về DUY NHẤT một mảng JSON, mỗi phần tử là một khối theo thứ tự đọc:
{"type": "header|title|paragraph|list|table|code|figure|caption|page_number",
 "text": "...",                       // list: mỗi ý một dòng bắt đầu "- ", ý con thụt 2 dấu cách
 "rows": [["ô", "ô"], ...]            // chỉ khi type = table
}
Bố cục nhiều cột: đọc hết cột trái rồi mới sang cột phải.
Bảng: giữ đúng hàng và cột, ô xuống dòng vẫn là một ô.
Hình hoặc sơ đồ: type = figure, text = mô tả ngắn + "Nhãn:" + chữ trong hình.
Giữ nguyên chữ số, mã lệnh, ký hiệu."""
RETRY_REMINDER = "Chỉ trả về một mảng JSON đúng schema, không kèm giải thích."
UNREADABLE_MARKER = "[không đọc được]"

# Ánh xạ cố định khoá `type` của prompt v1 sang `BlockType` (ADR-009 mục 2); giá trị khác ⇒ `vlm_invalid`.
PROMPT_TYPE_TO_BLOCK: dict[str, BlockType] = {
    "header": "header_footer",
    "title": "heading",
    "paragraph": "paragraph",
    "list": "list_item",
    "table": "table",
    "code": "code",
    "figure": "figure",
    "caption": "caption",
    "page_number": "page_number",
}

VlmStatus = Literal["ok", "vlm_invalid", "upstream_error", "timeout"]
SkipReason = Literal["consent_off", "no_provider", "image_rejected", "region_cap"]
KIND: AiCacheKind = "vlm_region"
CHARS_PER_TOKEN_ESTIMATE = 2

logger = logging.getLogger(__name__)


class VlmTaskLimits(BaseModel):
    """Giới hạn của một lần đọc vùng (ADR-009 mục 6, 9): field có mặc định, không phải biến môi trường."""

    model_config = ConfigDict(frozen=True)

    json_retry_max: int = Field(default=1, ge=0, le=1)
    max_response_chars: int = Field(default=100_000, ge=1)
    max_json_depth: int = Field(default=32, ge=2)
    max_parse_starts: int = Field(default=64, ge=1)
    max_tokens: int = Field(default=4096, ge=1)
    max_blocks_per_region: int = Field(default=200, ge=1)
    max_table_rows: int = Field(default=200, ge=1)
    max_table_cols: int = Field(default=30, ge=1)
    max_text_chars: int = Field(default=20_000, ge=1)


class VisionReaderSettings(BaseModel):
    """Trần ảnh và trần vùng của bộ đọc. `max_image_bytes` là trần HIỆU LỰC = min(`HOLOCRON_VLM_MAX_IMAGE_BYTES`,
    `crop_max_image_bytes`) (ADR-009 mục 5, OPEN 6)."""

    model_config = ConfigDict(frozen=True)

    max_edge_px: int = Field(default=1024, ge=64, le=1024)
    max_image_bytes: int = Field(default=1024 * 1024, ge=1)
    max_regions_per_page: int = Field(default=16, ge=1)
    max_regions_per_document: int = Field(default=200, ge=1)
    limits: VlmTaskLimits = Field(default_factory=VlmTaskLimits)

    @classmethod
    def from_config(cls, vlm: VlmSettings, pdf: PdfReaderConfig) -> VisionReaderSettings:
        return cls(
            max_edge_px=min(vlm.max_edge_px, pdf.crop_max_edge_px),
            max_image_bytes=min(vlm.max_image_bytes, pdf.crop_max_image_bytes),
        )


class VlmRegionBlock(BaseModel):
    """Một khối VLM đã parse và validate (ADR-009 mục 2). `text` null với `table` và `figure` (mô tả hình do VLM tự
    viết bị bỏ ngay khi parse); `rows` khác null ⇔ `type = table`. Chữ đã chuẩn hoá NFC."""

    model_config = ConfigDict(frozen=True)

    type: BlockType
    text: str | None
    rows: list[list[str]] | None
    order: int = Field(ge=0)


class VlmRegionResult(BaseModel):
    """Kết quả đọc một vùng; không chứa tên model, endpoint hay khoá. `attempts = 0` khi trúng cache."""

    model_config = ConfigDict(frozen=True)

    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    prompt_version: str
    status: VlmStatus
    blocks: list[VlmRegionBlock] = []
    attempts: int = Field(ge=0, le=2)
    from_cache: bool = False


class VisionSkipped(BaseModel):
    """Không gọi VLM cho vùng này (không gọi mạng, không tra cache); khối giữ kết quả offline."""

    model_config = ConfigDict(frozen=True)

    reason: SkipReason


class RegionImage(BaseModel):
    """Ảnh vùng sẽ gửi: đúng các byte PNG đã kiểm ở bước cắt ảnh, kèm kích thước và sha256 khai báo."""

    model_config = ConfigDict(frozen=True)

    png: bytes = Field(repr=False)
    width_px: int = Field(ge=1)
    height_px: int = Field(ge=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class _CachedBlocks(BaseModel):
    """`result_json` của cache: chỉ các khối đã validate (không tên model, không ảnh)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    blocks: list[VlmRegionBlock]


# --------------------------------------------------------------------------- #
# Parse JSON an toàn (ADR-009 mục 9)
# --------------------------------------------------------------------------- #


def _depth_outside_strings(text: str) -> int:
    """Độ sâu lồng `[`/`{` lớn nhất, không đếm ký tự trong chuỗi JSON."""
    depth = deepest = 0
    in_string = escaped = False
    for char in text:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "[{":
            depth += 1
            deepest = max(deepest, depth)
        elif char in "]}":
            depth = max(0, depth - 1)
    return deepest


def _nfc(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def _block(raw: object, order: int, limits: VlmTaskLimits) -> VlmRegionBlock | None:
    if not isinstance(raw, dict):
        return None
    kind = raw.get("type")
    block_type = PROMPT_TYPE_TO_BLOCK.get(kind) if isinstance(kind, str) else None
    if block_type is None:
        return None
    text = raw.get("text")
    rows = raw.get("rows")
    if block_type == "table":
        if not isinstance(rows, list) or not rows or len(rows) > limits.max_table_rows:
            return None
        cells: list[list[str]] = []
        for row in rows:
            if not isinstance(row, list) or len(row) > limits.max_table_cols:
                return None
            if not all(isinstance(cell, str) and len(cell) <= limits.max_text_chars for cell in row):
                return None
            cells.append([_nfc(cell) for cell in row if isinstance(cell, str)])
        return VlmRegionBlock(type="table", text=None, rows=cells, order=order)
    if rows is not None:
        return None
    if block_type == "figure":
        return VlmRegionBlock(type="figure", text=None, rows=None, order=order)
    if not isinstance(text, str) or len(text) > limits.max_text_chars:
        return None
    return VlmRegionBlock(type=block_type, text=_nfc(text), rows=None, order=order)


def parse_vlm_blocks(content: str, limits: VlmTaskLimits) -> list[VlmRegionBlock] | None:
    """Mảng khối JSON đầu tiên trong `content` (chịu rào ``` và văn bản thừa quanh mảng); hỏng, khối lạ hay vượt
    giới hạn ⇒ None. Không dựa vào `response_format`."""
    if len(content) > limits.max_response_chars or _depth_outside_strings(content) > limits.max_json_depth:
        return None
    decoder = json.JSONDecoder()
    start = content.find("[")
    tries = 0
    while start >= 0 and tries < limits.max_parse_starts:
        tries += 1
        try:
            value, _ = decoder.raw_decode(content, start)
        except ValueError:
            start = content.find("[", start + 1)
            continue
        if not isinstance(value, list) or len(value) > limits.max_blocks_per_region:
            return None
        blocks = [_block(item, order, limits) for order, item in enumerate(value)]
        if any(block is None for block in blocks):
            return None
        return [block for block in blocks if block is not None]
    return None


# --------------------------------------------------------------------------- #
# Ảnh và client
# --------------------------------------------------------------------------- #


def check_region_image(image: RegionImage, settings: VisionReaderSettings) -> bool:
    """Kiểm trên chính các byte sẽ gửi (ADR-009 mục 5): trần byte hiệu lực, magic PNG, IHDR khớp kích thước khai báo,
    cạnh dài ≤ `max_edge_px`, sha256 khớp."""
    data = image.png
    if not data or len(data) > settings.max_image_bytes:
        return False
    if png_dimensions(data) != (image.width_px, image.height_px):
        return False
    if max(image.width_px, image.height_px) > settings.max_edge_px:
        return False
    return hashlib.sha256(data).hexdigest() == image.sha256


ClientFactory = Callable[[SelectedProvider], ChatCompletionClient]


def openai_client_factory(
    vlm: VlmSettings,
    max_image_bytes: int,
    *,
    transport: Transport | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> ClientFactory:
    """Client tương thích OpenAI cho nhà cung cấp đã chọn; thời gian và giới hạn HTTP theo `HOLOCRON_VLM_*`.
    `transport`, `sleep` cấp cho test (không gọi mạng); mặc định là urllib và đồng hồ thật."""

    def build(provider: SelectedProvider) -> ChatCompletionClient:
        limits = vlm.http_limits
        return OpenAiChatClient(
            base_url=provider.base_url,
            api_key=provider.api_key,
            model=provider.model,
            timeout_s=vlm.timeout_s,
            retry_max=limits.retry_max,
            backoff_base_s=limits.backoff_base_s,
            call_budget_s=vlm.timeout_s,
            max_image_bytes=max_image_bytes,
            base_url_variable=ENV_VLM_BASE_URL,
            key_variable=ENV_VLM_API_KEY,
            transport=transport or UrllibTransport(limits=limits),
            sleep=sleep,
        )

    return build


def _message(image: RegionImage, *, retry: bool) -> ChatMessage:
    data_uri = PNG_DATA_URI_PREFIX + base64.b64encode(image.png).decode("ascii")
    parts: list[ContentPart] = [TextPart(text=PROMPT_V1), ImageUrlPart(image_url=ImageUrl(url=data_uri))]
    if retry:
        parts.append(TextPart(text=RETRY_REMINDER))
    return ChatMessage(role="user", content=parts)


def _failure_status(error: LlmUpstreamError) -> VlmStatus:
    return "timeout" if "hết hạn" in str(error) else "upstream_error"


def _utc_now() -> datetime:
    return datetime.now(UTC)


class VisionReader:
    """Đọc một vùng bằng VLM (ADR-009). Mọi phụ thuộc là tham số; trần vùng đếm trong instance (có khoá)."""

    def __init__(
        self,
        *,
        consent: AiConsent,
        selector: ProviderSelector,
        recorder: UsageRecorder,
        cache: AiCacheRepository,
        client_factory: ClientFactory,
        settings: VisionReaderSettings | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._consent = consent
        self._selector = selector
        self._recorder = recorder
        self._cache = cache
        self._client_factory = client_factory
        self._settings = settings or VisionReaderSettings()
        self._clock = clock
        self._lock = threading.Lock()
        self._per_document: dict[str, int] = {}
        self._per_page: dict[tuple[str, int], int] = {}

    def _estimate(self) -> CostEstimate:
        prompt_chars = len(PROMPT_V1) + len(RETRY_REMINDER)
        return CostEstimate(
            input_tokens=prompt_chars // CHARS_PER_TOKEN_ESTIMATE + 1,
            max_output_tokens=self._settings.limits.max_tokens,
            images=1,
        )

    def _take_region_slot(self, document_id: str, page_number: int) -> bool:
        with self._lock:
            page_key = (document_id, page_number)
            if self._per_document.get(document_id, 0) >= self._settings.max_regions_per_document:
                return False
            if self._per_page.get(page_key, 0) >= self._settings.max_regions_per_page:
                return False
            self._per_document[document_id] = self._per_document.get(document_id, 0) + 1
            self._per_page[page_key] = self._per_page.get(page_key, 0) + 1
            return True

    def read_region(self, document_id: str, page_number: int, image: RegionImage) -> VlmRegionResult | VisionSkipped:
        if not self._consent.auto_send_allowed("image"):
            return VisionSkipped(reason="consent_off")
        if not check_region_image(image, self._settings):
            logger.info("ảnh vùng không qua kiểm, không gửi VLM: image_rejected")
            return VisionSkipped(reason="image_rejected")
        estimate = self._estimate()
        provider = self._selector.select("vlm", estimate)
        if provider is None:
            return VisionSkipped(reason="no_provider")
        if not self._take_region_slot(document_id, page_number):
            self._recorder.record(provider.provider_id, _zero_usage(), reserved=estimate)
            logger.info("vượt trần vùng VLM của tài liệu/trang: region_cap")
            return VisionSkipped(reason="region_cap")
        key = ai_cache_key(KIND, image.sha256, provider.model, PROMPT_VERSION)
        cached = self._from_cache(key, document_id, image.sha256)
        if cached is not None:
            self._recorder.record(provider.provider_id, _zero_usage(), reserved=estimate)
            return cached
        return self._call(document_id, image, provider, estimate)

    def _from_cache(self, key: str, document_id: str, input_sha256: str) -> VlmRegionResult | None:
        entry = self._cache.get_ai_cache(key, document_id, now=self._clock())
        if entry is None:
            return None
        try:
            stored = _CachedBlocks.model_validate_json(entry.result_json)
        except ValidationError:
            self._cache.drop_ai_cache_entry(key)
            return None
        return VlmRegionResult(
            input_sha256=input_sha256,
            prompt_version=PROMPT_VERSION,
            status="ok",
            blocks=stored.blocks,
            attempts=0,
            from_cache=True,
        )

    def _complete_once(
        self, provider: SelectedProvider, image: RegionImage, estimate: CostEstimate, *, retry: bool
    ) -> ChatCompletionResult | VlmStatus:
        """Một lời gọi; chi phí được ghi ngay (thiếu `usage` ⇒ ước tính bảo thủ). Lỗi ⇒ mã trạng thái."""
        try:
            client = self._client_factory(provider)
            result = client.complete([_message(image, retry=retry)], max_tokens=self._settings.limits.max_tokens)
        except ConfigError:
            self._recorder.record(provider.provider_id, _zero_usage(), reserved=estimate)
            logger.warning("cấu hình nhà cung cấp VLM không dùng được: upstream_error")
            return "upstream_error"
        except ImageTooLargeError:
            self._recorder.record(provider.provider_id, _zero_usage(), reserved=estimate)
            return "upstream_error"
        except LlmUpstreamError as exc:
            sent = exc.attempts > 0
            self._recorder.record(
                provider.provider_id, _worst_case(estimate) if sent else _zero_usage(), reserved=estimate
            )
            status = _failure_status(exc)
            logger.warning("gọi VLM thất bại: %s", status)
            return status
        usage = result.usage
        recorded = (
            AiUsage(input_tokens=usage.prompt_tokens, output_tokens=usage.completion_tokens, images=1, estimated=False)
            if usage is not None
            else _worst_case(estimate)
        )
        self._recorder.record(provider.provider_id, recorded, reserved=estimate)
        return result

    def _call(
        self, document_id: str, image: RegionImage, provider: SelectedProvider, estimate: CostEstimate
    ) -> VlmRegionResult:
        attempts = 0
        current = provider
        for attempt in range(self._settings.limits.json_retry_max + 1):
            if attempt > 0:
                again = self._selector.select("vlm", estimate)
                if again is None:
                    break
                current = again
            attempts += 1
            outcome = self._complete_once(current, image, estimate, retry=attempt > 0)
            if isinstance(outcome, str):
                return VlmRegionResult(
                    input_sha256=image.sha256, prompt_version=PROMPT_VERSION, status=outcome, attempts=attempts
                )
            blocks = parse_vlm_blocks(outcome.content, self._settings.limits)
            if blocks is not None:
                self._store(current, image, blocks, document_id)
                return VlmRegionResult(
                    input_sha256=image.sha256,
                    prompt_version=PROMPT_VERSION,
                    status="ok",
                    blocks=blocks,
                    attempts=attempts,
                )
        logger.info("phản hồi VLM không đúng schema sau khi gọi lại: vlm_invalid")
        return VlmRegionResult(
            input_sha256=image.sha256, prompt_version=PROMPT_VERSION, status="vlm_invalid", attempts=max(1, attempts)
        )

    def _store(
        self, provider: SelectedProvider, image: RegionImage, blocks: list[VlmRegionBlock], document_id: str
    ) -> None:
        # Lần gọi lại có thể sang nhà cung cấp khác: khoá theo model của lời gọi đã cho kết quả.
        final_key = ai_cache_key(KIND, image.sha256, provider.model, PROMPT_VERSION)
        entry = AiCacheEntry(
            cache_key=final_key,
            kind=KIND,
            input_sha256=image.sha256,
            model=provider.model,
            prompt_version=PROMPT_VERSION,
            result_json=_CachedBlocks(blocks=blocks).model_dump_json(),
        )
        self._cache.put_ai_cache(entry, document_id, now=self._clock())


def _zero_usage() -> AiUsage:
    return AiUsage(input_tokens=0, output_tokens=0, images=0, estimated=False)


def _worst_case(estimate: CostEstimate) -> AiUsage:
    return AiUsage(
        input_tokens=estimate.input_tokens,
        output_tokens=estimate.max_output_tokens,
        images=estimate.images,
        estimated=True,
    )
