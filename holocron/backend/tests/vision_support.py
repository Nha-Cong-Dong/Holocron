"""Hỗ trợ test bộ đọc vùng VLM: client giả (không gọi mạng), ảnh PNG tự tạo, bộ đọc dựng sẵn."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import UTC, datetime

from pydantic import SecretStr

from holocron_core.domain.ai import AiConsent, ProviderCandidate, ProviderModels, ProviderPrices, SelectedProvider
from holocron_core.domain.llm import ChatCompletionResult, ChatMessage, LlmUpstreamError, TokenUsage
from holocron_core.domain.repositories import AiCacheRepository
from holocron_core.infrastructure.ai_runtime import InMemorySpendLedger
from holocron_core.infrastructure.document_readers.raster import GrayImage, encode_png_gray
from holocron_core.infrastructure.memory_store import IngestionStore
from holocron_core.infrastructure.vision_reader import ClientFactory, RegionImage, VisionReader, VisionReaderSettings
from holocron_core.services.provider_selection import ProviderSelection

SECRET = "sk-vision-secret-abcdef123456"
MODEL = "vision-model-x"
NOW = datetime(2026, 11, 3, 8, 0, tzinfo=UTC)
DOC = "doc_aaaaaaaaaaaaaaaa"


def region_image(width: int = 32, height: int = 16, shade: int = 200) -> RegionImage:
    png = encode_png_gray(GrayImage(pixels=bytes([shade]) * (width * height), width=width, height=height, dpi=72.0))
    return RegionImage(png=png, width_px=width, height_px=height, sha256=hashlib.sha256(png).hexdigest())


class Switch:
    """`AiConsent` đổi được giữa hai lời gọi."""

    def __init__(self, image: bool = True) -> None:
        self.image = image
        self.calls = 0

    def auto_send_allowed(self, content: str) -> bool:
        self.calls += 1
        return self.image and content == "image"


Reply = str | LlmUpstreamError


class StubClient:
    """`ChatCompletionClient` giả: trả lần lượt các phản hồi, ghi lại mọi message đã nhận."""

    def __init__(self, replies: Sequence[Reply], *, usage: TokenUsage | None = None) -> None:
        self._replies = list(replies)
        self.received: list[list[ChatMessage]] = []
        self.usage = usage

    def complete(self, messages: Sequence[ChatMessage], *, max_tokens: int) -> ChatCompletionResult:
        self.received.append(list(messages))
        reply = self._replies.pop(0)
        if isinstance(reply, LlmUpstreamError):
            raise reply
        return ChatCompletionResult(content=reply, usage=self.usage, attempts=1)


def candidate(*, model: str = MODEL, budget: float = 5.0, provider_id: str = "env_vlm") -> ProviderCandidate:
    return ProviderCandidate(
        provider_id=provider_id,
        template="gemini",
        label="Gemini",
        base_url="https://vision.example.test/v1",
        api_key=SecretStr(SECRET),
        models=ProviderModels(vlm=model),
        local=False,
        ip_policy="operator",
        priority=0,
        budget_usd=budget,
        prices=ProviderPrices(input_per_1k_usd=0.001, output_per_1k_usd=0.001, per_image_usd=0.001, source="env"),
        budget_group=provider_id,
    )


class Harness:
    def __init__(
        self,
        replies: Sequence[Reply] = (),
        *,
        consent: AiConsent | None = None,
        providers: list[ProviderCandidate] | None = None,
        cache: AiCacheRepository | None = None,
        settings: VisionReaderSettings | None = None,
        usage: TokenUsage | None = None,
        client_factory: ClientFactory | None = None,
    ) -> None:
        self.client = StubClient(replies, usage=usage)
        self.providers = [candidate()] if providers is None else providers
        self.ledger = InMemorySpendLedger()
        self.selection = ProviderSelection(lambda: self.providers, self.ledger)
        self.cache: AiCacheRepository = cache if cache is not None else IngestionStore()
        self.built: list[SelectedProvider] = []
        self.reader = VisionReader(
            consent=consent or Switch(),
            selector=self.selection,
            recorder=self.selection,
            cache=self.cache,
            client_factory=client_factory or self._factory,
            settings=settings,
            clock=lambda: NOW,
        )

    def _factory(self, provider: SelectedProvider) -> StubClient:
        self.built.append(provider)
        return self.client
