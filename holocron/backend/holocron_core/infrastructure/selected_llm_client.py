"""Client LLM (vai `llm`) đi qua bộ chọn nhà cung cấp (spec §5.16; ADR-009 mục 12.3, 12.8, 12.11).

`SelectedProviderChatClient` cài `ChatCompletionClient`: ở MỖI lần `complete`, ước tính chi phí tối đa (`CostEstimate`:
số ký tự phần chữ / 2, `max_tokens`, số ảnh), gọi `ProviderSelector.select("llm", …)` (chọn và giữ chỗ nguyên tử dưới
trần chi tiêu), dựng `OpenAiChatClient` cho nhà cung cấp được chọn với transport theo `ip_policy` của nó (mặc định
`provider_probe.guarded_transport`: kiểm mọi địa chỉ ở mỗi kết nối, không redirect, không proxy), rồi ghi chi phí
(`UsageRecorder.record`) hoặc nhả chỗ (`release`, lỗi trước khi gửi). Không còn nhà cung cấp nào trong trần ⇒
`LlmUpstreamError` (`attempts = 0`, không gửi gì). Thông điệp lỗi không nêu khoá, endpoint hay tên model.

Thời gian: như `OpenAiChatClient.from_settings`, cận tổng `LlmTaskLimits.deadline_s` tính từ lúc dựng client này, áp
cho mọi lần `complete`; giới hạn HTTP và thời gian chờ mỗi lần thử theo `LlmSettings` (`HOLOCRON_LLM_*`).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence

from ..domain.ai import AiUsage, CostEstimate, IpPolicy, ProviderSelector, SelectedProvider, UsageRecorder
from ..domain.llm import (
    ChatCompletionResult,
    ChatMessage,
    ImageTooLargeError,
    LlmTimeoutError,
    LlmUpstreamError,
)
from .llm_client import OpenAiChatClient, Transport
from .llm_config import ConfigError, LlmHttpLimits, LlmSettings
from .provider_probe import guarded_transport

CHARS_PER_TOKEN_ESTIMATE = 2
NO_PROVIDER_MESSAGE = "không còn nhà cung cấp LLM nào khả dụng trong trần chi tiêu"
PROVIDER_CONFIG_MESSAGE = "cấu hình nhà cung cấp LLM đã chọn không dùng được"
DEADLINE_MESSAGE = "hết hạn thời gian tổng của việc gọi LLM"

TransportFactory = Callable[[IpPolicy, LlmHttpLimits], Transport]

logger = logging.getLogger(__name__)


def _text_chars(messages: Sequence[ChatMessage]) -> int:
    total = 0
    for message in messages:
        if isinstance(message.content, str):
            total += len(message.content)
            continue
        for part in message.content:
            if part.type == "text":
                total += len(part.text)
    return total


def estimate_chat_cost(messages: Sequence[ChatMessage], max_tokens: int) -> CostEstimate:
    """Chi phí TỐI ĐA của một lời gọi (spec §5.16 `AiUsage`): phần chữ / 2 (làm tròn lên), `max_tokens`, số ảnh."""
    return CostEstimate(
        input_tokens=_text_chars(messages) // CHARS_PER_TOKEN_ESTIMATE + 1,
        max_output_tokens=max_tokens,
        images=sum(len(message.image_parts()) for message in messages),
    )


def _worst_case(estimate: CostEstimate) -> AiUsage:
    return AiUsage(
        input_tokens=estimate.input_tokens,
        output_tokens=estimate.max_output_tokens,
        images=estimate.images,
        estimated=True,
    )


class SelectedProviderChatClient:
    """`ChatCompletionClient` chọn nhà cung cấp vai `llm` ở mỗi lời gọi qua `ProviderSelector` (có trần chi tiêu)."""

    def __init__(
        self,
        *,
        selector: ProviderSelector,
        recorder: UsageRecorder,
        settings: LlmSettings,
        transport_for: TransportFactory = guarded_transport,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._selector = selector
        self._recorder = recorder
        self._settings = settings
        self._transport_for = transport_for
        self._sleep = sleep
        self._monotonic = monotonic
        self._deadline = monotonic() + settings.task_limits.deadline_s

    def _client(self, provider: SelectedProvider, remaining_s: float) -> OpenAiChatClient:
        limits = self._settings.http_limits
        return OpenAiChatClient(
            base_url=provider.base_url,
            api_key=provider.api_key,
            model=provider.model,
            timeout_s=self._settings.timeout_s,
            retry_max=limits.retry_max,
            backoff_base_s=limits.backoff_base_s,
            total_budget_s=remaining_s,
            transport=self._transport_for(provider.ip_policy, limits),
            sleep=self._sleep,
            monotonic=self._monotonic,
        )

    def complete(self, messages: Sequence[ChatMessage], *, max_tokens: int) -> ChatCompletionResult:
        remaining_s = self._deadline - self._monotonic()
        if remaining_s <= 0:
            raise LlmTimeoutError(DEADLINE_MESSAGE, attempts=0)
        estimate = estimate_chat_cost(messages, max_tokens)
        provider = self._selector.select("llm", estimate)
        if provider is None:
            logger.info("không gọi LLM: không còn nhà cung cấp trong trần chi tiêu")
            raise LlmUpstreamError(NO_PROVIDER_MESSAGE, attempts=0)
        try:
            client = self._client(provider, remaining_s)
        except (ConfigError, ValueError):
            self._recorder.release(provider.reservation_id)
            logger.warning("cấu hình nhà cung cấp LLM đã chọn không dùng được")
            raise LlmUpstreamError(PROVIDER_CONFIG_MESSAGE, attempts=0) from None
        try:
            result = client.complete(messages, max_tokens=max_tokens)
        except ImageTooLargeError:
            self._recorder.release(provider.reservation_id)
            raise
        except LlmUpstreamError as exc:
            if exc.attempts > 0:  # đã gửi: ghi chi phí ước tính bảo thủ
                self._recorder.record(provider.reservation_id, _worst_case(estimate))
            else:
                self._recorder.release(provider.reservation_id)
            raise
        except BaseException:
            # Lỗi không lường trước sau khi có thể đã gửi: ghi bảo thủ để không vượt trần, rồi ném tiếp.
            self._recorder.record(provider.reservation_id, _worst_case(estimate))
            raise
        usage = result.usage
        recorded = (
            AiUsage(
                input_tokens=usage.prompt_tokens,
                output_tokens=usage.completion_tokens,
                images=estimate.images,
                estimated=False,
            )
            if usage is not None
            else _worst_case(estimate)
        )
        self._recorder.record(provider.reservation_id, recorded)
        return result
