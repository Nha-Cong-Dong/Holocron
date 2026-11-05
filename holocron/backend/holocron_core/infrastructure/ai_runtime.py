"""Cài đặt tối thiểu của Kết nối AI cho V02 (spec §5.16, §11.2; ADR-009 mục 7, 12.6).

- `EnvAiConsent`: công tắc đồng ý đọc `HOLOCRON_AI_CONSENT`, giá trị lưu ở kho cài đặt (`AiConsentStore`) và
  `HOLOCRON_VLM_MODE` ở MỖI lời gọi (không chụp lúc khởi động). Env thắng file; chưa lưu ⇒ tắt (`read_consent_state`).
- `build_ai_status`: `RuntimeInfo.ai_status` từ trạng thái công tắc và phần chỉ đọc của bộ chọn (`peek`, không giữ chỗ).
- `EnvProviderSource`: nhà cung cấp CHỈ từ biến môi trường `HOLOCRON_VLM_*` (`env_vlm`), đọc lại ở mỗi lần gọi. Chỉ
  đọc nguồn; việc chọn theo vai, thứ tự và trần nằm DUY NHẤT ở `services/provider_selection.py`.
- `InMemorySpendLedger`: sổ chi tiêu trong RAM (thuộc tính instance, có khoá), reset khi khởi động lại; dùng khi không
  có `HOLOCRON_DATA_DIR`.
- `FileSpendLedger`: như trên nhưng phần ĐÃ TIÊU lưu bền ở `HOLOCRON_DATA_DIR/ai_usage.json` (spec §5.16 "Lưu trữ";
  ADR-009 mục 12.3): đọc một lần khi tạo, ghi nguyên tử sau mỗi `settle` có chi phí, dưới khoá của instance. Chỗ giữ
  (`reserved`) chỉ ở RAM. File chỉ gồm id nhóm bộ đếm và số USD, không khoá hay hash khoá. File có mà không đọc
  được ⇒ fail-closed: từ chối giữ chỗ, không ghi đè file.

Mọi `ConfigError` chỉ nêu tên biến, không nêu giá trị. Đọc lenient: biến sai ⇒ công tắc tắt / nhà cung cấp env coi
như chưa cấu hình, log cảnh báo chỉ gồm tên biến.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from ..domain.ai import (
    AI_ROLES,
    TEMPLATE_DEFAULT_BUDGET_USD,
    TEMPLATE_DEFAULT_PRICES,
    AiAutoBlockReason,
    AiConsentState,
    AiConsentStore,
    AiContentKind,
    AiRole,
    AiRoleStatus,
    AiStatus,
    ProviderCandidate,
    ProviderModels,
    ProviderPrices,
    ProviderStatusView,
    ProviderTemplate,
    WriteProtection,
)
from .ai_consent_store import Unreadable, read_bounded, write_atomic
from .llm_config import BROKEN_VLM_MODE, ConfigError, VlmMode, read_vlm_mode, read_vlm_settings

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
AI_USAGE_FILENAME = "ai_usage.json"
AI_USAGE_VERSION = 1
# Tối đa 20 nhà cung cấp người dùng + vài nhà cung cấp env: vài KB; lớn hơn trần này coi như hỏng.
MAX_USAGE_BYTES = 256 * 1024
_GROUP_ID = re.compile(r"^[a-z0-9_]{1,64}$")

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


def ai_consent_env_locked(env: Mapping[str, str]) -> bool:
    """`HOLOCRON_AI_CONSENT` có giá trị (kể cả giá trị sai): công tắc bị khoá bởi biến môi trường, UI chỉ đọc."""
    return _read(env, ENV_AI_CONSENT) is not None


def _lenient_vlm_mode(env: Mapping[str, str]) -> VlmMode:
    try:
        return read_vlm_mode(env)
    except ConfigError as exc:
        logger.warning("cấu hình chế độ gửi AI không hợp lệ, coi như tắt: %s", exc)
        return BROKEN_VLM_MODE


def read_consent_state(env: Mapping[str, str], store: AiConsentStore | None) -> AiConsentState:
    """Giá trị công tắc đang hiệu lực (ADR-009 mục 7): `HOLOCRON_AI_CONSENT` đặt ⇒ giá trị đó (`source = "env"`; giá
    trị sai ⇒ tắt, vẫn khoá); không thì giá trị lưu ở kho (`"setting"`); chưa lưu hoặc file hỏng ⇒ tắt (`"default"`)."""
    persistent = store is not None and store.persistent
    mode = _lenient_vlm_mode(env)
    if ai_consent_env_locked(env):
        try:
            from_env = read_ai_consent(env)
        except ConfigError as exc:
            logger.warning("cấu hình đồng ý gửi AI không hợp lệ, coi như tắt: %s", exc)
            from_env = False
        return AiConsentState(enabled=from_env is True, source="env", vlm_mode=mode, persistent=persistent)
    saved = None if store is None else store.load()
    if saved is None:
        return AiConsentState(enabled=False, source="default", vlm_mode=mode, persistent=persistent)
    return AiConsentState(enabled=saved, source="setting", vlm_mode=mode, persistent=persistent)


def _mode_allows(mode: VlmMode, content: AiContentKind) -> bool:
    if mode == "off":
        return False
    return not (mode == "images_only" and content != "image")


class EnvAiConsent:
    """`AiConsent` đọc biến môi trường và kho cài đặt ở MỖI lời gọi (ADR-009 mục 7).

    `auto_send_allowed(kind)` = công tắc bật VÀ `HOLOCRON_VLM_MODE` cho phép `kind`: không đặt ⇒ không cấm; `off` ⇒
    cấm mọi gửi tự động; `images_only` ⇒ chỉ ảnh. Biến sai hoặc file cài đặt hỏng ⇒ coi như tắt (lenient).
    """

    def __init__(self, env: EnvReader = _environ, store: AiConsentStore | None = None) -> None:
        self._env = env
        self._store = store

    def auto_send_allowed(self, content: AiContentKind) -> bool:
        state = read_consent_state(self._env(), self._store)
        return state.enabled and _mode_allows(state.vlm_mode, content)


_ROLE_CONTENT: dict[AiRole, AiContentKind] = {"vlm": "image", "llm": "text"}


def _role_status(role: AiRole, consent: AiConsentState, providers: ProviderStatusView) -> AiRoleStatus:
    """Lý do đầu tiên đúng theo thứ tự `AiAutoBlockReason` (spec §5.16): nhà cung cấp, trần, chế độ, công tắc."""
    preview = providers.peek(role)
    reason: AiAutoBlockReason | None
    if preview is None:
        reason = "budget_exhausted" if providers.budget_exhausted(role) else "no_provider"
        return AiRoleStatus(
            role=role, auto_send_allowed=False, reason=reason, provider_id=None, recipient_label=None, local=False
        )
    if consent.vlm_mode == "off":
        reason = "mode_off"
    elif not _mode_allows(consent.vlm_mode, _ROLE_CONTENT[role]):
        reason = "mode_images_only"
    elif not consent.enabled:
        reason = "consent_off"
    else:
        reason = None
    return AiRoleStatus(
        role=role,
        auto_send_allowed=reason is None,
        reason=reason,
        provider_id=preview.provider_id,
        recipient_label=preview.label,
        local=preview.local,
    )


def build_ai_status(
    consent: AiConsentState, providers: ProviderStatusView, write_protection: WriteProtection
) -> AiStatus:
    """`RuntimeInfo.ai_status`: không gọi mạng, không giữ chỗ; chỉ id, nhãn, boolean và mã lý do."""
    return AiStatus(
        consent_enabled=consent.enabled,
        consent_source=consent.source,
        vlm_mode=consent.vlm_mode,
        settings_persistent=consent.persistent,
        write_protection=write_protection,
        roles=[_role_status(role, consent, providers) for role in AI_ROLES],
    )


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


def _parse_usage(raw: bytes) -> dict[str, float] | None:
    """`spent_usd` hợp lệ trong `ai_usage.json`; file không đúng dạng ⇒ `None`. Mục sai (id lạ, số âm, NaN) bị bỏ."""
    try:
        parsed: object = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(parsed, dict) or parsed.get("version") != AI_USAGE_VERSION:
        return None
    spent = parsed.get("spent_usd")
    if not isinstance(spent, dict):
        return None
    result: dict[str, float] = {}
    for group, value in spent.items():
        if (
            isinstance(group, str)
            and _GROUP_ID.fullmatch(group)
            and isinstance(value, int | float)
            and not isinstance(value, bool)
            and math.isfinite(value)
            and value >= 0
        ):
            result[group] = float(value)
    return result


class FileSpendLedger(InMemorySpendLedger):
    """`SpendLedger` lưu phần đã tiêu ở `<data_dir>/ai_usage.json` để khởi động lại không vượt trần (spec §5.16).

    - File hỏng (đọc được nhưng sai dạng) ⇒ bắt đầu từ 0 kèm MỘT cảnh báo (không nội dung, không đường dẫn); ghi lại
      file khi `settle` kế tiếp.
    - File có nhưng KHÔNG đọc được (`Unreadable`: lỗi I/O, quyền, quá `MAX_USAGE_BYTES`) ⇒ fail-closed: số đã tiêu
      không biết nên mọi `try_reserve` bị từ chối (như hết trần) và file KHÔNG bao giờ bị ghi đè, kèm cảnh báo. Mỗi
      `try_reserve` đọc lại file; đọc được (hoặc file đã bị xoá) thì nạp số đã tiêu và hoạt động lại bình thường.
    - Ghi lỗi ⇒ giữ số trong RAM (vẫn chặn trần trong phiên), log cảnh báo, không ném lỗi cho lời gọi đã trả tiền.
    """

    def __init__(self, data_dir: Path) -> None:
        super().__init__()
        self._data_dir = data_dir
        self._path = data_dir / AI_USAGE_FILENAME
        loaded = self._load()
        self._unreadable = loaded is None
        self._spent = {} if loaded is None else loaded

    @property
    def path(self) -> Path:
        return self._path

    @property
    def unreadable(self) -> bool:
        """File bộ đếm đang không đọc được: mọi giữ chỗ bị từ chối, file không bị ghi."""
        with self._lock:
            return self._unreadable

    def _load(self) -> dict[str, float] | None:
        """Số đã tiêu trong file; `None` khi file có nhưng không đọc được (fail-closed)."""
        raw = read_bounded(self._path, MAX_USAGE_BYTES, "file bộ đếm chi phí AI")
        if isinstance(raw, Unreadable):
            logger.warning(
                "file bộ đếm chi phí AI không đọc được: từ chối mọi lời gọi AI có tính phí và không ghi đè file "
                "cho tới khi đọc lại được"
            )
            return None
        if raw is None:
            return {}
        spent = _parse_usage(raw)
        if spent is None:
            logger.warning("file bộ đếm chi phí AI hỏng, bắt đầu lại từ 0")
            return {}
        return spent

    def try_reserve(self, group: str, amount_usd: float, budget_usd: float) -> bool:
        with self._lock:
            if self._unreadable:
                loaded = self._load()
                if loaded is None:
                    return False
                # Đọc lại được: nạp số đã tiêu (cộng phần đã ghi trong RAM khi file chưa đọc được).
                for key, value in loaded.items():
                    self._spent[key] = self._spent.get(key, 0.0) + value
                self._unreadable = False
                logger.warning("file bộ đếm chi phí AI đọc lại được, tiếp tục tính chi phí")
        return super().try_reserve(group, amount_usd, budget_usd)

    def settle(self, group: str, reserved_usd: float, actual_usd: float) -> None:
        with self._lock:
            self._reserved[group] = max(0.0, self._reserved.get(group, 0.0) - reserved_usd)
            if actual_usd <= 0:
                return
            self._spent[group] = self._spent.get(group, 0.0) + actual_usd
            if self._unreadable:
                logger.warning("file bộ đếm chi phí AI không đọc được: không ghi đè, chi phí giữ trong RAM")
                return
            payload: dict[str, Any] = {"version": AI_USAGE_VERSION, "spent_usd": dict(sorted(self._spent.items()))}
            try:
                write_atomic(self._data_dir, self._path, json.dumps(payload, sort_keys=True).encode("utf-8"))
            except OSError as exc:
                logger.warning("không ghi được file bộ đếm chi phí AI (%s); bộ đếm giữ trong RAM", type(exc).__name__)
