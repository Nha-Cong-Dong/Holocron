"""Interface gọi LLM kiểu OpenAI Chat Completions (SPECIFICATION §11.5; CLAUDE.md §2.2).

`ChatCompletionClient` là `Protocol`: bản cài đặt thật ở `infrastructure/llm_client.py`, test dùng stub và không gọi
mạng. Model ở đây chỉ chứa nội dung trao đổi và số đếm (số lần thử, token) để nơi gọi ghi log; không chứa khoá API,
địa chỉ endpoint hay tên model.

Đầu ra của LLM là dữ liệu KHÔNG tin cậy (LLM Gate, CLAUDE.md §2.1): nơi gọi tự parse, validate và đưa qua cổng kiểm
của đúng việc đang làm (ví dụ kiểm nguyên văn I3 với cặp khái niệm).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

ChatRole = Literal["system", "user", "assistant"]


class ChatMessage(BaseModel):
    model_config = ConfigDict(frozen=True)

    role: ChatRole
    content: str


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


class LlmUpstreamErrorDetail(BaseModel):
    """`detail` của 502 khi gọi LLM lỗi (spec v0.6 §6.0, đề xuất): không lộ endpoint, tên model hay phản hồi."""

    code: Literal["LLM_UPSTREAM_ERROR"] = "LLM_UPSTREAM_ERROR"
    message: str


class ChatCompletionClient(Protocol):
    """Một endpoint Chat Completions đã cấu hình (địa chỉ, khoá, model, timeout nằm trong bản cài đặt)."""

    def complete(self, messages: Sequence[ChatMessage], *, max_tokens: int) -> ChatCompletionResult:
        """Gửi `messages`, trả nội dung của lựa chọn đầu tiên. Lỗi sau khi hết số lần thử ⇒ `LlmUpstreamError`."""
        ...
