"""Interface gọi LLM kiểu OpenAI Chat Completions (SPECIFICATION §11.5; CLAUDE.md §2.2).

`ChatCompletionClient` là `Protocol`: bản cài đặt thật ở `infrastructure/llm_client.py`, test dùng stub và không gọi
mạng. Model ở đây chỉ chứa nội dung trao đổi và số đếm (số lần thử, token) để nơi gọi ghi log; không chứa khoá API,
địa chỉ endpoint hay tên model.

`ChatMessage.content` là chuỗi (LLM văn bản, JSON gửi đi giữ nguyên như trước) hoặc danh sách phần nội dung kiểu
OpenAI (`text`, `image_url`) cho bộ đọc trang VLM (ADR-009 mục 4). Phần ảnh chỉ nhận data URI PNG base64; model ở đây
kiểm dạng (tiền tố, base64 chuẩn, magic PNG). Trần byte của ảnh là cấu hình (`HOLOCRON_VLM_MAX_IMAGE_BYTES`) nên được
kiểm ở adapter (`infrastructure/llm_client.py`) trước khi gửi, không ghi cứng ở `domain/`.

Đầu ra của LLM là dữ liệu KHÔNG tin cậy (LLM Gate, CLAUDE.md §2.1): nơi gọi tự parse, validate và đưa qua cổng kiểm
của đúng việc đang làm (ví dụ kiểm nguyên văn I3 với cặp khái niệm).
"""

from __future__ import annotations

import base64
from collections.abc import Sequence
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

ChatRole = Literal["system", "user", "assistant"]

PNG_DATA_URI_PREFIX = "data:image/png;base64,"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
BASE64_GROUP_CHARS = 4
BASE64_GROUP_BYTES = 3
INVALID_BASE64_MESSAGE = "phần base64 của ảnh không hợp lệ"


class TextPart(BaseModel):
    """Phần văn bản của một message nhiều phần (`{"type": "text", "text": …}`)."""

    model_config = ConfigDict(frozen=True)

    type: Literal["text"] = "text"
    text: str


class ImageUrl(BaseModel):
    """`image_url` của OpenAI Chat Completions; chỉ nhận data URI `data:image/png;base64,<…>`.

    Kiểm: đúng tiền tố; phần sau tiền tố là base64 chuẩn (chỉ bảng chữ chuẩn, đệm đúng, mã hoá lại ra đúng chuỗi);
    các byte giải mã bắt đầu bằng magic PNG. Thông điệp lỗi không chứa nội dung ảnh.
    """

    model_config = ConfigDict(frozen=True)

    url: str

    @field_validator("url")
    @classmethod
    def _png_data_uri(cls, value: str) -> str:
        if not value.startswith(PNG_DATA_URI_PREFIX):
            raise ValueError("ảnh phải là data URI dạng data:image/png;base64,…")
        encoded = value[len(PNG_DATA_URI_PREFIX) :]
        try:
            decoded = base64.b64decode(encoded, validate=True)
        except ValueError:  # gồm `binascii.Error` và chuỗi có ký tự ngoài ASCII
            raise ValueError(INVALID_BASE64_MESSAGE) from None
        if not decoded or base64.b64encode(decoded).decode("ascii") != encoded:
            raise ValueError(INVALID_BASE64_MESSAGE)
        if not decoded.startswith(PNG_MAGIC):
            raise ValueError("ảnh không phải PNG (sai magic)")
        return value

    @property
    def decoded_size(self) -> int:
        """Số byte của ảnh sau giải mã, tính từ độ dài base64 chuẩn (đã kiểm), không giải mã lại."""
        encoded = self.url[len(PNG_DATA_URI_PREFIX) :]
        padding = len(encoded) - len(encoded.rstrip("="))
        return len(encoded) // BASE64_GROUP_CHARS * BASE64_GROUP_BYTES - padding


class ImageUrlPart(BaseModel):
    """Phần ảnh của một message nhiều phần (`{"type": "image_url", "image_url": {"url": …}}`)."""

    model_config = ConfigDict(frozen=True)

    type: Literal["image_url"] = "image_url"
    image_url: ImageUrl


ContentPart = Annotated[TextPart | ImageUrlPart, Field(discriminator="type")]


class ChatMessage(BaseModel):
    model_config = ConfigDict(frozen=True)

    role: ChatRole
    content: str | list[ContentPart]

    def image_parts(self) -> list[ImageUrlPart]:
        """Các phần ảnh của message (rỗng khi `content` là chuỗi)."""
        if isinstance(self.content, str):
            return []
        return [part for part in self.content if isinstance(part, ImageUrlPart)]


class TokenUsage(BaseModel):
    """`usage` do nhà cung cấp trả về cho một lần gọi thành công."""

    model_config = ConfigDict(frozen=True)

    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)


class ChatCompletionResult(BaseModel):
    """Nội dung trả lời (chưa kiểm) kèm số lần thử HTTP đã dùng và `usage` (nếu nhà cung cấp có trả)."""

    model_config = ConfigDict(frozen=True)

    content: str
    usage: TokenUsage | None = None
    attempts: int = Field(ge=1)


class LlmUpstreamError(Exception):
    """Gọi LLM thất bại sau khi hết số lần thử (timeout, mạng, gateway từ chối, phản hồi sai dạng).

    Thông điệp KHÔNG chứa khoá API, địa chỉ endpoint, tên model, nội dung request hay nội dung phản hồi.
    `attempts` là số lần thử HTTP đã dùng, để nơi gọi ghi log.
    """

    def __init__(self, message: str, *, attempts: int = 0) -> None:
        super().__init__(message)
        self.attempts = attempts


class ImageTooLargeError(Exception):
    """Một ảnh trong request vượt trần byte cấu hình; adapter từ chối TRƯỚC khi gửi (ADR-009 mục 5, mục 9).

    Không phải lỗi upstream: không có request nào được gửi. Thông điệp chỉ nêu tên biến cấu hình, không nêu nội dung.
    """


class LlmUpstreamErrorDetail(BaseModel):
    """`detail` của 502 khi gọi LLM lỗi (spec v0.6 §6.0, đề xuất): không lộ endpoint, tên model hay phản hồi."""

    code: Literal["LLM_UPSTREAM_ERROR"] = "LLM_UPSTREAM_ERROR"
    message: str


class ChatCompletionClient(Protocol):
    """Một endpoint Chat Completions đã cấu hình (địa chỉ, khoá, model, timeout nằm trong bản cài đặt)."""

    def complete(self, messages: Sequence[ChatMessage], *, max_tokens: int) -> ChatCompletionResult:
        """Gửi `messages`, trả nội dung của lựa chọn đầu tiên. Lỗi sau khi hết số lần thử ⇒ `LlmUpstreamError`."""
        ...
