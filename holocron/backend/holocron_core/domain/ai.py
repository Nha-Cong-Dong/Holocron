"""Kết nối AI: công tắc đồng ý, chọn nhà cung cấp, bộ đếm chi phí, khoá cache AI (spec §5.16; ADR-009 mục 7, 8, 12).

Chỉ có model và `Protocol`. Cài đặt tối thiểu của V02: `infrastructure/ai_runtime.py` (công tắc từ biến môi trường,
nhà cung cấp chỉ từ `HOLOCRON_VLM_*`, sổ chi tiêu trong RAM) và `services/provider_selection.py` (nơi DUY NHẤT chọn
nhà cung cấp theo vai, thứ tự và trần). V04 thay cài đặt, giữ `Protocol`.

Chi phí USD là số vận hành, KHÔNG thuộc I1 (I1 chỉ áp cho nội dung câu hỏi, đáp án, lời giải; ADR-009 mục 12.3).
`SelectedProvider` và `ProviderCandidate` mang khoá trong `SecretStr`: không ra response, log hay thông điệp exception.
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from .schemas import StrictModel

AiRole = Literal["vlm", "llm"]
AiContentKind = Literal["image", "text"]
ProviderTemplate = Literal["gemini", "organizer_gateway", "custom"]
AiCacheKind = Literal["vlm_region", "structure_proposal"]
IpPolicy = Literal["public_only", "operator"]

TOKENS_PER_PRICE_UNIT = 1000


class AiUsage(StrictModel):
    """Một lần gọi; `UsageRecorder` ghi. Thiếu `usage` của nhà cung cấp ⇒ ước tính bảo thủ (`estimated = True`)."""

    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    images: int = Field(ge=0)
    estimated: bool


class CostEstimate(StrictModel):
    """Chi phí TỐI ĐA của lời gọi sắp gửi, để chặn và giữ chỗ trước khi vượt trần."""

    input_tokens: int = Field(ge=0)
    max_output_tokens: int = Field(ge=0)
    images: int = Field(ge=0)


class ProviderModels(StrictModel):
    vlm: str | None = Field(default=None, min_length=1, max_length=200)
    llm: str | None = Field(default=None, min_length=1, max_length=200)


class ProviderPrices(StrictModel):
    """Giá USD; `source` cho biết giá lấy từ người dùng, biến môi trường hay bảng mặc định theo mẫu."""

    input_per_1k_usd: float = Field(ge=0, le=1, allow_inf_nan=False)
    output_per_1k_usd: float = Field(ge=0, le=1, allow_inf_nan=False)
    per_image_usd: float = Field(ge=0, le=1, allow_inf_nan=False)
    source: Literal["user", "env", "template_default"]


# Bảng giá và trần MẶC ĐỊNH theo mẫu (spec §5.16; con số bảo thủ do người viết ADR đặt 2026-11-03, ADR-009 OPEN 5).
# Mẫu chỉ quyết định các giá trị mặc định này; code không rẽ nhánh theo nhà cung cấp.
TEMPLATE_DEFAULT_PRICES: dict[ProviderTemplate, ProviderPrices] = {
    "gemini": ProviderPrices(
        input_per_1k_usd=0.0025, output_per_1k_usd=0.015, per_image_usd=0.003, source="template_default"
    ),
    "organizer_gateway": ProviderPrices(
        input_per_1k_usd=0.005, output_per_1k_usd=0.025, per_image_usd=0.005, source="template_default"
    ),
    "custom": ProviderPrices(
        input_per_1k_usd=0.005, output_per_1k_usd=0.025, per_image_usd=0.005, source="template_default"
    ),
}
TEMPLATE_DEFAULT_BUDGET_USD: dict[ProviderTemplate, float] = {
    "organizer_gateway": 4.0,
    "gemini": 5.0,
    "custom": 1.0,
}


def cost_usd(prices: ProviderPrices, *, input_tokens: int, output_tokens: int, images: int) -> float:
    """`vào/1000 × giá vào + ra/1000 × giá ra + ảnh × giá ảnh` (ADR-009 mục 12.3); số vận hành, ngoài I1."""
    return (
        input_tokens / TOKENS_PER_PRICE_UNIT * prices.input_per_1k_usd
        + output_tokens / TOKENS_PER_PRICE_UNIT * prices.output_per_1k_usd
        + images * prices.per_image_usd
    )


def estimate_cost_usd(prices: ProviderPrices, estimate: CostEstimate) -> float:
    return cost_usd(
        prices, input_tokens=estimate.input_tokens, output_tokens=estimate.max_output_tokens, images=estimate.images
    )


def usage_cost_usd(prices: ProviderPrices, usage: AiUsage) -> float:
    return cost_usd(prices, input_tokens=usage.input_tokens, output_tokens=usage.output_tokens, images=usage.images)


class ProviderCandidate(BaseModel):
    """Một nhà cung cấp đủ cấu hình để chọn (nội bộ; KHÔNG ra response, log hay exception).

    `budget_group` là khoá nhóm bộ đếm dùng chung (cùng base URL chuẩn hoá VÀ cùng khoá ⇒ một nhóm); V02 chỉ có
    một nhà cung cấp env nên nhóm = id của nó.
    """

    model_config = ConfigDict(frozen=True)

    provider_id: str
    template: ProviderTemplate
    label: str
    base_url: str = Field(repr=False)
    api_key: SecretStr | None = None
    models: ProviderModels = Field(repr=False)
    local: bool
    ip_policy: IpPolicy
    priority: int = Field(ge=0, le=1000)
    budget_usd: float = Field(ge=0, le=1000, allow_inf_nan=False)
    prices: ProviderPrices
    budget_group: str
    # Nhà cung cấp người dùng đã tắt: không được chọn, nhưng vẫn góp trần vào nhóm bộ đếm và vẫn kiểm thử được.
    enabled: bool = True

    def model_for(self, role: AiRole) -> str | None:
        return self.models.vlm if role == "vlm" else self.models.llm


class SelectedProvider(BaseModel):
    """Nhà cung cấp được chọn cho một lời gọi (nội bộ; KHÔNG ra response, log hay exception). `reservation_id` là id
    chỗ giữ (chi phí tối đa của lời gọi) do `select` tạo, dùng cho `UsageRecorder.record`/`release`."""

    model_config = ConfigDict(frozen=True)

    provider_id: str
    base_url: str = Field(repr=False)
    api_key: SecretStr | None
    model: str = Field(repr=False)
    local: bool
    ip_policy: IpPolicy
    reservation_id: str = Field(min_length=1)


class ProviderPreview(BaseModel):
    """Nhà cung cấp `select` sẽ chọn lúc này cho trạng thái (`ai_status`); KHÔNG có khoá, endpoint hay chỗ giữ."""

    model_config = ConfigDict(frozen=True)

    provider_id: str
    label: str
    local: bool


AiConsentSource = Literal["env", "setting", "default"]
AiVlmMode = Literal["unset", "off", "images_only"]
WriteProtection = Literal["token", "token_not_configured"]
AiAutoBlockReason = Literal["no_provider", "budget_exhausted", "mode_off", "mode_images_only", "consent_off"]
AI_ROLES: tuple[AiRole, ...] = ("vlm", "llm")


class AiConsentState(StrictModel):
    """Response `GET/PUT /v1/settings/ai-consent` (spec §5.16, §6.11). Nguồn: `HOLOCRON_AI_CONSENT` thắng file cài
    đặt; chưa lưu (hoặc file hỏng) ⇒ tắt, `source = "default"`."""

    enabled: bool
    source: AiConsentSource
    vlm_mode: AiVlmMode
    persistent: bool


class AiRoleStatus(StrictModel):
    """Trạng thái gửi tự động của một vai; chỉ id, nhãn đã lọc, boolean và mã lý do (không khoá, không endpoint)."""

    role: AiRole
    auto_send_allowed: bool
    reason: AiAutoBlockReason | None
    provider_id: str | None
    recipient_label: str | None
    local: bool


class AiStatus(StrictModel):
    """`RuntimeInfo.ai_status` (spec §5.12, §5.16): tính lại ở MỖI `GET /v1/runtime`, không gọi mạng, không giữ chỗ."""

    consent_enabled: bool
    consent_source: AiConsentSource
    vlm_mode: AiVlmMode
    settings_persistent: bool
    write_protection: WriteProtection
    roles: list[AiRoleStatus] = Field(min_length=len(AI_ROLES), max_length=len(AI_ROLES))


KeyStorage = Literal["dpapi", "plaintext_file", "env", "none"]
ProviderTestAction = Literal["chat", "list_models"]
ProviderTestCode = Literal[
    "ok",
    "auth_failed",
    "not_found",
    "rate_limited",
    "upstream_error",
    "timeout",
    "blocked_address",
    "redirect_blocked",
    "invalid_response",
    "image_rejected",
    "key_unreadable",
    "no_model_for_role",
    "budget_exhausted",
]
USER_PROVIDER_ID_PATTERN = r"^usr_[0-9a-f]{12}$"
PROVIDER_ID_PATTERN = r"^(usr_[0-9a-f]{12}|env_(llm|vlm|shared))$"
MAX_USER_PROVIDERS = 20
USER_PROVIDER_DEFAULT_PRIORITY = 100
# `key_last4` chỉ có khi khoá dài ≥ 12 ký tự (ADR-009 mục 12.1, B13).
KEY_LAST4_MIN_LENGTH = 12
KEY_MAX_LENGTH = 512
BASE_URL_MAX_LENGTH = 2048
LABEL_MAX_LENGTH = 80
MAX_TEST_MODELS = 200


def key_last4(key: str | None) -> str | None:
    """4 ký tự cuối CHỈ khi khoá dài ≥ 12 ký tự; ngắn hơn hoặc không khoá ⇒ `None` (không hiện ký tự nào)."""
    if key is None or len(key) < KEY_LAST4_MIN_LENGTH:
        return None
    return key[-4:]


class ProviderConfig(StrictModel):
    """Một nhà cung cấp trong `GET /v1/settings/providers` (spec §5.16); KHÔNG BAO GIỜ có khoá đầy đủ."""

    id: str = Field(pattern=PROVIDER_ID_PATTERN)
    kind: Literal["openai_compatible"]
    source: Literal["user", "env"]
    template: ProviderTemplate
    label: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    base_url: str | None
    local: bool
    models: ProviderModels
    key_set: bool
    key_last4: str | None = Field(default=None, pattern=r"^[\x21-\x7e]{4}$")
    key_storage: KeyStorage
    key_unreadable: bool
    enabled: bool
    priority: int = Field(ge=0, le=1000)
    budget_usd: float = Field(ge=0, le=1000, allow_inf_nan=False)
    spent_usd: float = Field(ge=0, allow_inf_nan=False)
    budget_exhausted: bool
    budget_shared_with: list[str] = Field(default_factory=list)
    prices: ProviderPrices


class ProviderList(StrictModel):
    """Response `GET /v1/settings/providers`: theo thứ tự chọn (env trước, rồi `priority`, rồi id)."""

    providers: list[ProviderConfig]
    settings_persistent: bool
    write_protection: WriteProtection


class ProviderPricesInput(StrictModel):
    """Giá USD người dùng nhập; như `ProviderPrices` bỏ `source`."""

    input_per_1k_usd: float = Field(ge=0, le=1, allow_inf_nan=False)
    output_per_1k_usd: float = Field(ge=0, le=1, allow_inf_nan=False)
    per_image_usd: float = Field(ge=0, le=1, allow_inf_nan=False)


class ProviderCreate(StrictModel):
    """Body `POST /v1/settings/providers` (spec §6.11). `api_key` KHÔNG BAO GIỜ được trả lại."""

    template: ProviderTemplate
    label: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    base_url: str = Field(min_length=1, max_length=BASE_URL_MAX_LENGTH)
    api_key: SecretStr | None = Field(default=None, max_length=KEY_MAX_LENGTH)
    models: ProviderModels
    enabled: bool = True
    priority: int = Field(default=USER_PROVIDER_DEFAULT_PRIORITY, ge=1, le=1000)
    budget_usd: float | None = Field(default=None, ge=0, le=1000, allow_inf_nan=False)
    prices: ProviderPricesInput | None = None

    @model_validator(mode="after")
    def _one_role_model(self) -> ProviderCreate:
        if self.models.vlm is None and self.models.llm is None:
            raise ValueError("cần model cho ít nhất một vai (vlm hoặc llm)")
        return self


# Field của `ProviderUpdate` được gửi với giá trị null tường minh ⇒ 422 (riêng `budget_usd`: null ⇒ trần mặc định).
_UPDATE_NULLABLE_FIELDS = frozenset({"budget_usd"})


class ProviderUpdate(StrictModel):
    """Body `PATCH /v1/settings/providers/{id}`: field vắng = giữ nguyên; `api_key` vắng ⇒ giữ khoá cũ."""

    label: str | None = Field(default=None, min_length=1, max_length=LABEL_MAX_LENGTH)
    base_url: str | None = Field(default=None, min_length=1, max_length=BASE_URL_MAX_LENGTH)
    api_key: SecretStr | None = Field(default=None, max_length=KEY_MAX_LENGTH)
    models: ProviderModels | None = None
    enabled: bool | None = None
    priority: int | None = Field(default=None, ge=1, le=1000)
    budget_usd: float | None = Field(default=None, ge=0, le=1000, allow_inf_nan=False)
    prices: ProviderPricesInput | None = None
    clear_prices: bool = False

    @model_validator(mode="after")
    def _no_explicit_null(self) -> ProviderUpdate:
        for name in self.model_fields_set - _UPDATE_NULLABLE_FIELDS:
            if getattr(self, name) is None:
                raise ValueError(f"field {name} không được là null; bỏ field để giữ nguyên giá trị cũ")
        if self.clear_prices and self.prices is not None:
            raise ValueError("không gửi cùng lúc prices và clear_prices")
        return self


class ProviderTestRequest(StrictModel):
    """Body `POST /v1/settings/providers/{id}/test`: `role` bắt buộc ⇔ `action = "chat"`."""

    action: ProviderTestAction
    role: AiRole | None = None

    @model_validator(mode="after")
    def _role_for_chat(self) -> ProviderTestRequest:
        if (self.action == "chat") != (self.role is not None):
            raise ValueError("role bắt buộc khi action = chat và không được gửi khi action = list_models")
        return self


class ProviderTestResult(StrictModel):
    """Kết quả kiểm thử đã làm sạch: không thân phản hồi, không URL, không khoá."""

    ok: bool
    code: ProviderTestCode
    http_status: int | None = Field(default=None, ge=100, le=599)
    latency_ms: int = Field(ge=0)
    models: list[str] = Field(default_factory=list, max_length=MAX_TEST_MODELS)


class KeyStorageError(RuntimeError):
    """Không lưu hoặc không giải mã được khoá (DPAPI lỗi). Thông điệp KHÔNG chứa khoá."""


class ProviderNotFoundError(LookupError):
    """Không có nhà cung cấp người dùng với id đã cho."""


class ProviderLimitError(RuntimeError):
    """Đã có đủ `MAX_USER_PROVIDERS` nhà cung cấp người dùng."""


class ProviderInputError(ValueError):
    """Dữ liệu nhà cung cấp bị từ chối khi lưu; `code` là mã 422 của spec §6.11, thông điệp không nêu giá trị."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class AiConsentStore(Protocol):
    """Kho giá trị công tắc đồng ý (ADR-009 mục 7, 12.4). `persistent = False` khi không có `HOLOCRON_DATA_DIR`."""

    @property
    def persistent(self) -> bool: ...

    def load(self) -> bool | None:
        """Giá trị đã lưu; chưa lưu, file hỏng hoặc không đọc được ⇒ `None` (coi như tắt), không ném lỗi."""
        ...

    def save(self, enabled: bool) -> None:
        """Ghi nguyên tử; kho không lưu được (`persistent = False`) ⇒ `SettingsNotPersistentError`."""
        ...


class SettingsNotPersistentError(RuntimeError):
    """Ghi cài đặt khi không có thư mục dữ liệu (`HOLOCRON_DATA_DIR`)."""


class AiConsent(Protocol):
    def auto_send_allowed(self, content: AiContentKind) -> bool:
        """Đọc ở MỖI lời gọi: công tắc bật VÀ `HOLOCRON_VLM_MODE` cho phép `content`. Chỉ áp cho lần gửi TỰ ĐỘNG."""
        ...


class ProviderSelector(Protocol):
    def select(self, role: AiRole, estimate: CostEstimate) -> SelectedProvider | None:
        """Nhà cung cấp đầu tiên đủ điều kiện cho `role` mà nhóm bộ đếm còn chỗ cho `estimate`; chi phí tối đa của
        `estimate` được GIỮ CHỖ nguyên tử cùng lúc kiểm trần. Không còn ⇒ `None` (lùi offline)."""
        ...

    def peek(self, role: AiRole) -> ProviderPreview | None:
        """Xem trước nhà cung cấp sẽ chọn cho `role` (nhóm chưa tiêu hết trần) mà KHÔNG giữ chỗ hay đổi số dư."""
        ...


class ProviderStatusView(Protocol):
    """Phần chỉ đọc của bộ chọn dùng cho `ai_status`: xem trước và trạng thái trần, KHÔNG giữ chỗ."""

    def peek(self, role: AiRole) -> ProviderPreview | None: ...

    def budget_exhausted(self, role: AiRole) -> bool: ...


class UsageRecorder(Protocol):
    def record(self, reservation_id: str, usage: AiUsage) -> None:
        """Sau lời gọi (kể cả lỗi SAU khi đã gửi, `usage` ước tính bảo thủ): cộng chi phí thật và nhả chỗ, cùng khoá."""
        ...

    def release(self, reservation_id: str) -> None:
        """Lỗi TRƯỚC khi gửi request (hoặc không gửi, ví dụ trúng cache): chỉ nhả chỗ, không cộng chi phí."""
        ...


class SpendLedger(Protocol):
    """Sổ chi tiêu theo nhóm bộ đếm; mọi thao tác nguyên tử (thuộc tính instance, không dict cấp module)."""

    def try_reserve(self, group: str, amount_usd: float, budget_usd: float) -> bool:
        """Giữ chỗ `amount_usd` khi `đã tiêu + đang giữ + amount_usd ≤ budget_usd`; trả False (không giữ) khi vượt."""
        ...

    def settle(self, group: str, reserved_usd: float, actual_usd: float) -> None:
        """Trả lại phần giữ chỗ `reserved_usd` và cộng chi phí thật `actual_usd`."""
        ...

    def spent_usd(self, group: str) -> float: ...


def ai_cache_key(kind: AiCacheKind, input_sha256: str, model: str, prompt_version: str) -> str:
    """sha256 hex của JSON chuẩn hoá `[kind, input_sha256, model, prompt_version]` (ADR-009 mục 8)."""
    payload = json.dumps(
        [kind, input_sha256, model, prompt_version], ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class AiCacheEntry(BaseModel):
    """Một dòng cache AI (spec §5.16). `result_json` là kết quả `status = ok` ĐÃ validate; `model` chỉ ở DB cục bộ."""

    model_config = ConfigDict(frozen=True)

    cache_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    kind: AiCacheKind
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    model: str = Field(min_length=1, repr=False)
    prompt_version: str = Field(min_length=1)
    result_json: str = Field(min_length=1)
