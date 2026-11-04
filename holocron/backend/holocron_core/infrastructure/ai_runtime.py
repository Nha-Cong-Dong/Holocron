"""Cài đặt tối thiểu của Kết nối AI cho V02 (spec §5.16, §11.2; ADR-009 mục 7, 12.6).

- `EnvAiConsent`: công tắc đồng ý đọc `HOLOCRON_AI_CONSENT` và `HOLOCRON_VLM_MODE` ở MỖI lời gọi (không chụp lúc
  khởi động). Không đặt công tắc ⇒ tắt (V04 thêm giá trị lưu ở file cài đặt và UI).
- `EnvProviderSource`: nhà cung cấp CHỈ từ biến môi trường `HOLOCRON_VLM_*` (`env_vlm`), đọc lại ở mỗi lần gọi. Chỉ
  đọc nguồn; việc chọn theo vai, thứ tự và trần nằm DUY NHẤT ở `services/provider_selection.py`.
- `InMemorySpendLedger`: sổ chi tiêu trong RAM (thuộc tính instance, có khoá), reset khi khởi động lại.

Mọi `ConfigError` chỉ nêu tên biến, không nêu giá trị. Đọc lenient: biến sai ⇒ công tắc tắt / nhà cung cấp env coi
như chưa cấu hình, log cảnh báo chỉ gồm tên biến.
"""

from __future__ import annotations

import logging
import math
import os
import threading
from collections.abc import Callable, Mapping

from ..domain.ai import (
    TEMPLATE_DEFAULT_BUDGET_USD,
    TEMPLATE_DEFAULT_PRICES,
    AiContentKind,
    ProviderCandidate,
    ProviderModels,
    ProviderPrices,
    ProviderTemplate,
)
from .llm_config import ConfigError, read_vlm_mode, read_vlm_settings

ENV_AI_CONSENT = "HOLOCRON_AI_CONSENT"
ENV_VLM_TEMPLATE = "HOLOCRON_VLM_TEMPLATE"
ENV_VLM_BUDGET_USD = "HOLOCRON_VLM_BUDGET_USD"
ENV_VLM_PRICE_IN_PER_1K = "HOLOCRON_VLM_PRICE_IN_PER_1K"
ENV_VLM_PRICE_OUT_PER_1K = "HOLOCRON_VLM_PRICE_OUT_PER_1K"
ENV_VLM_PRICE_PER_IMAGE = "HOLOCRON_VLM_PRICE_PER_IMAGE"

DEFAULT_VLM_TEMPLATE: ProviderTemplate = "custom"
PROVIDER_TEMPLATES: tuple[ProviderTemplate, ...] = ("gemini", "organizer_gateway", "custom")
ENV_VLM_PROVIDER_ID = "env_vlm"
ENV_PROVIDER_PRIORITY = 0
DEFAULT_VLM_LABEL = "nhà cung cấp AI đã cấu hình"
MAX_PRICE_USD = 1.0
MAX_BUDGET_USD = 1000.0

EnvReader = Callable[[], Mapping[str, str]]

logger = logging.getLogger(__name__)


def _environ() -> Mapping[str, str]:
    return os.environ


def _read(env: Mapping[str, str], name: str) -> str | None:
    value = env.get(name, "").strip()
    return value or None


def read_ai_consent(env: Mapping[str, str]) -> bool | None:
    """`HOLOCRON_AI_CONSENT`: `1` ⇒ bật, `0` ⇒ tắt, không đặt ⇒ `None`; giá trị khác ⇒ `ConfigError`."""
    raw = _read(env, ENV_AI_CONSENT)
    if raw is None:
        return None
    if raw == "1":
        return True
    if raw == "0":
        return False
    raise ConfigError(f"{ENV_AI_CONSENT} không hợp lệ: chỉ nhận 1 hoặc 0")


def _read_template(env: Mapping[str, str]) -> ProviderTemplate:
    raw = _read(env, ENV_VLM_TEMPLATE)
    if raw is None:
        return DEFAULT_VLM_TEMPLATE
    for template in PROVIDER_TEMPLATES:
        if raw == template:
            return template
    raise ConfigError(f"{ENV_VLM_TEMPLATE} không hợp lệ: chỉ nhận gemini, organizer_gateway hoặc custom")


def _read_usd(env: Mapping[str, str], name: str, high: float) -> float | None:
    raw = _read(env, name)
    if raw is None:
        return None
    try:
        value = float(raw)
    except ValueError:
        raise ConfigError(f"{name} không hợp lệ: cần một số USD từ 0 đến {high:g}") from None
    if not math.isfinite(value) or not 0.0 <= value <= high:
        raise ConfigError(f"{name} không hợp lệ: cần một số USD từ 0 đến {high:g}")
    return value


def _read_prices(env: Mapping[str, str], template: ProviderTemplate) -> ProviderPrices:
    """Giá env khi đặt; thiếu giá nào ⇒ lấy đúng cột đó từ bảng mặc định bảo thủ theo mẫu (một quy tắc duy nhất)."""
    default = TEMPLATE_DEFAULT_PRICES[template]
    values = (
        _read_usd(env, ENV_VLM_PRICE_IN_PER_1K, MAX_PRICE_USD),
        _read_usd(env, ENV_VLM_PRICE_OUT_PER_1K, MAX_PRICE_USD),
        _read_usd(env, ENV_VLM_PRICE_PER_IMAGE, MAX_PRICE_USD),
    )
    if all(value is None for value in values):
        return default
    price_in, price_out, per_image = values
    return ProviderPrices(
        input_per_1k_usd=default.input_per_1k_usd if price_in is None else price_in,
        output_per_1k_usd=default.output_per_1k_usd if price_out is None else price_out,
        per_image_usd=default.per_image_usd if per_image is None else per_image,
        source="env",
    )


def load_env_vlm_provider(env: Mapping[str, str]) -> ProviderCandidate | None:
    """Nhà cung cấp `env_vlm` từ `HOLOCRON_VLM_*` (ADR-009 mục 12.2); chưa đủ cấu hình ⇒ `None`, biến sai ⇒
    `ConfigError` chỉ nêu tên biến."""
    settings = read_vlm_settings(env)
    template = _read_template(env)
    prices = _read_prices(env, template)
    budget = _read_usd(env, ENV_VLM_BUDGET_USD, MAX_BUDGET_USD)
    if settings is None:
        return None
    return ProviderCandidate(
        provider_id=ENV_VLM_PROVIDER_ID,
        template=template,
        label=settings.data_recipient or DEFAULT_VLM_LABEL,
        base_url=settings.base_url,
        api_key=settings.api_key,
        models=ProviderModels(vlm=settings.model),
        local=settings.local,
        ip_policy="operator",
        priority=ENV_PROVIDER_PRIORITY,
        budget_usd=TEMPLATE_DEFAULT_BUDGET_USD[template] if budget is None else budget,
        prices=prices,
        budget_group=ENV_VLM_PROVIDER_ID,
    )


class EnvAiConsent:
    """`AiConsent` đọc biến môi trường ở MỖI lời gọi (ADR-009 mục 7).

    `auto_send_allowed(kind)` = công tắc bật VÀ `HOLOCRON_VLM_MODE` cho phép `kind`: không đặt ⇒ không cấm; `off` ⇒
    cấm mọi gửi tự động; `images_only` ⇒ chỉ ảnh. Biến sai ⇒ coi như tắt (lenient).
    """

    def __init__(self, env: EnvReader = _environ) -> None:
        self._env = env

    def auto_send_allowed(self, content: AiContentKind) -> bool:
        source = self._env()
        try:
            consent = read_ai_consent(source)
            mode = read_vlm_mode(source)
        except ConfigError as exc:
            logger.warning("cấu hình đồng ý gửi AI không hợp lệ, coi như tắt: %s", exc)
            return False
        if consent is not True or mode == "off":
            return False
        return not (mode == "images_only" and content != "image")


class EnvProviderSource:
    """Danh sách nhà cung cấp từ biến môi trường, đọc lại ở mỗi lần gọi (V02: chỉ `env_vlm`)."""

    def __init__(self, env: EnvReader = _environ) -> None:
        self._env = env

    def __call__(self) -> list[ProviderCandidate]:
        try:
            provider = load_env_vlm_provider(self._env())
        except ConfigError as exc:
            logger.warning("cấu hình nhà cung cấp VLM không hợp lệ, coi như chưa cấu hình: %s", exc)
            return []
        return [] if provider is None else [provider]


class InMemorySpendLedger:
    """`SpendLedger` trong RAM: kiểm trần và giữ chỗ dưới cùng một khoá, nên lời gọi song song không vượt trần."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._spent: dict[str, float] = {}
        self._reserved: dict[str, float] = {}

    def try_reserve(self, group: str, amount_usd: float, budget_usd: float) -> bool:
        with self._lock:
            committed = self._spent.get(group, 0.0) + self._reserved.get(group, 0.0)
            if committed + amount_usd > budget_usd:
                return False
            self._reserved[group] = self._reserved.get(group, 0.0) + amount_usd
            return True

    def settle(self, group: str, reserved_usd: float, actual_usd: float) -> None:
        with self._lock:
            self._reserved[group] = max(0.0, self._reserved.get(group, 0.0) - reserved_usd)
            self._spent[group] = self._spent.get(group, 0.0) + actual_usd

    def spent_usd(self, group: str) -> float:
        with self._lock:
            return self._spent.get(group, 0.0)

    def reserved_usd(self, group: str) -> float:
        with self._lock:
            return self._reserved.get(group, 0.0)
