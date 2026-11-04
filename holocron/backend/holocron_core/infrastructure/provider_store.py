"""Kho nhà cung cấp AI do người dùng thêm (spec §5.16 "Lưu trữ", §6.11; ADR-009 mục 12.1, 12.2, 12.4, 12.8, 12.9).

- Lưu ở field `providers` của `HOLOCRON_DATA_DIR/ai_settings.json` (file dùng chung với công tắc đồng ý, cùng khoá,
  ghi nguyên tử). Khoá mã hoá DPAPI trên Windows, dạng rõ trong file 0600 trên Unix (`key_protection.py`); DPAPI lỗi
  ⇒ `KeyStorageError`, không ghi gì.
- Không có thư mục dữ liệu ⇒ `UnpersistedProviderStore`: không có nhà cung cấp người dùng, ghi ⇒
  `SettingsNotPersistentError`.
- Kiểm tĩnh lúc lưu (không phân giải DNS): base URL `https://`, hoặc `http://` khi tên máy ∈ `LOOPBACK_HOSTS`; không
  userinfo, query, fragment, khoảng trắng hay ký tự điều khiển; IP viết thẳng phải công khai (trừ tên máy loopback).
  Nhãn ≤ 80 ký tự, không ký tự điều khiển, không `://`, không chứa khoá, model hay tên máy của chính nhà cung cấp.
- File hỏng ⇒ không có nhà cung cấp người dùng (không 500); bản ghi hỏng bị bỏ qua (cảnh báo chỉ nêu số lượng); khoá
  không giải mã được ⇒ `key_unreadable = True`, không được chọn.
- `merge_providers`: hợp nhất nguồn env và người dùng; cùng base URL chuẩn hoá VÀ cùng khoá ⇒ cùng nhóm bộ đếm (nhóm lấy
  id của nhà cung cấp đứng trước theo thứ tự chọn). Việc CHỌN vẫn chỉ ở `services/provider_selection.py`.

Khoá chỉ ở dạng `SecretStr` trong RAM; không vào `repr`, log hay thông điệp lỗi.
"""

from __future__ import annotations

import ipaddress
import logging
import secrets
import urllib.parse
from collections.abc import Callable, Sequence
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from ..domain.ai import (
    BASE_URL_MAX_LENGTH,
    KEY_MAX_LENGTH,
    LABEL_MAX_LENGTH,
    MAX_USER_PROVIDERS,
    TEMPLATE_DEFAULT_BUDGET_USD,
    TEMPLATE_DEFAULT_PRICES,
    USER_PROVIDER_ID_PATTERN,
    KeyStorage,
    KeyStorageError,
    ProviderCandidate,
    ProviderCreate,
    ProviderInputError,
    ProviderLimitError,
    ProviderModels,
    ProviderNotFoundError,
    ProviderPrices,
    ProviderPricesInput,
    ProviderTemplate,
    ProviderUpdate,
    SettingsNotPersistentError,
    key_last4,
)
from .ai_consent_store import AiSettingsFile
from .key_protection import KeyProtector, StoredKeyKind, default_key_protector, protector_for
from .llm_config import LOOPBACK_HOSTS, has_control_characters, is_loopback_url, is_valid_key

PROVIDERS_FIELD = "providers"
USER_PROVIDER_ID_PREFIX = "usr_"
USER_PROVIDER_ID_HEX_BYTES = 6
DEFAULT_PORTS = {"https": 443, "http": 80}

BASE_URL_REJECTED = "base_url_rejected"
LABEL_REJECTED = "label_rejected"
KEY_INVALID = "key_invalid"
KEY_REQUIRED_AFTER_BASE_URL_CHANGE = "key_required_after_base_url_change"
MODELS_REQUIRED = "models_required"

BASE_URL_REJECTED_MESSAGE = (
    "Địa chỉ nhà cung cấp không hợp lệ: cần https:// (http:// chỉ cho 127.0.0.1, ::1, localhost), không có tên đăng "
    "nhập, tham số truy vấn, dấu # hay địa chỉ IP nội bộ"
)
LABEL_REJECTED_MESSAGE = (
    "Tên hiển thị không hợp lệ: tối đa 80 ký tự, không ký tự điều khiển, không chứa ://, khoá, tên model hay tên máy "
    "của nhà cung cấp"
)
KEY_INVALID_MESSAGE = (
    "Khoá API không hợp lệ: cần khoá gồm ký tự ASCII in được, không khoảng trắng (bắt buộc trừ địa chỉ loopback)"
)
KEY_REQUIRED_MESSAGE = "Đổi địa chỉ nhà cung cấp thì phải nhập lại khoá API"
MODELS_REQUIRED_MESSAGE = "Cần model cho ít nhất một vai (VLM hoặc LLM)"

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Kiểm tĩnh base URL và nhãn
# --------------------------------------------------------------------------- #


def _reject_url() -> ProviderInputError:
    return ProviderInputError(BASE_URL_REJECTED, BASE_URL_REJECTED_MESSAGE)


def _literal_ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


def address_is_public(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Địa chỉ công khai: không riêng, link-local, loopback, không xác định, multicast, dành riêng, kể cả dạng
    IPv4-mapped (`::ffff:a.b.c.d`) của các dải đó (ADR-009 mục 12.8)."""
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return address.is_global and not address.is_multicast


def normalize_base_url(value: str) -> str:
    """Base URL đã chuẩn hoá (scheme, tên máy chữ thường; bỏ cổng mặc định; bỏ `/` cuối) hoặc `ProviderInputError`
    `base_url_rejected`. Chỉ kiểm tĩnh, không phân giải DNS."""
    if (
        len(value) > BASE_URL_MAX_LENGTH
        or has_control_characters(value)
        or any(char.isspace() or char in "\\?#" for char in value)
    ):
        raise _reject_url()
    try:
        parts = urllib.parse.urlsplit(value)
        port = parts.port
    except ValueError:
        raise _reject_url() from None
    scheme = parts.scheme.lower()
    host = parts.hostname
    if not host or "@" in parts.netloc or parts.query or parts.fragment:
        raise _reject_url()
    loopback_name = host in LOOPBACK_HOSTS
    if not (scheme == "https" or (scheme == "http" and loopback_name)):
        raise _reject_url()
    literal = _literal_ip(host)
    if literal is not None and not loopback_name and not address_is_public(literal):
        raise _reject_url()
    return _compose(scheme, host, port, parts.path)


def _compose(scheme: str, host: str, port: int | None, path: str) -> str:
    netloc = f"[{host}]" if ":" in host else host
    if port is not None and port != DEFAULT_PORTS.get(scheme):
        netloc = f"{netloc}:{port}"
    return f"{scheme}://{netloc}{path.rstrip('/')}"


def canonical_url(value: str) -> str:
    """Như `normalize_base_url` nhưng không từ chối (dùng để so nhóm bộ đếm, kể cả URL env); lỗi ⇒ giữ nguyên."""
    try:
        parts = urllib.parse.urlsplit(value)
        port = parts.port
    except ValueError:
        return value
    host = parts.hostname
    if not host:
        return value
    return _compose(parts.scheme.lower(), host, port, parts.path)


def _host_of(base_url: str) -> str | None:
    try:
        return urllib.parse.urlsplit(base_url).hostname
    except ValueError:
        return None


def check_label(label: str, *, base_url: str, key: str | None, models: ProviderModels) -> str:
    """Nhãn đã lọc (spec §5.16) hoặc `ProviderInputError` `label_rejected`; so tên máy và model không phân biệt hoa thường."""
    lowered = label.lower()
    forbidden = [value for value in (models.vlm, models.llm, _host_of(base_url)) if value]
    if (
        not label
        or len(label) > LABEL_MAX_LENGTH
        or has_control_characters(label)
        or "://" in label
        or (key is not None and key in label)
        or any(value.lower() in lowered for value in forbidden)
    ):
        raise ProviderInputError(LABEL_REJECTED, LABEL_REJECTED_MESSAGE)
    return label


def _checked_key(api_key: SecretStr | None) -> str | None:
    if api_key is None:
        return None
    value = api_key.get_secret_value()
    if len(value) > KEY_MAX_LENGTH or not is_valid_key(value):
        raise ProviderInputError(KEY_INVALID, KEY_INVALID_MESSAGE)
    return value


# --------------------------------------------------------------------------- #
# Bản ghi lưu trong file và mục trả cho tầng trên
# --------------------------------------------------------------------------- #


class StoredKey(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: StoredKeyKind
    value: str = Field(min_length=1, repr=False)


class StoredProvider(BaseModel):
    """Một nhà cung cấp người dùng trong file (KHÔNG ra response); `key.value` là blob DPAPI hoặc khoá dạng rõ."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=USER_PROVIDER_ID_PATTERN)
    template: ProviderTemplate
    label: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    base_url: str = Field(min_length=1, max_length=BASE_URL_MAX_LENGTH, repr=False)
    models: ProviderModels = Field(repr=False)
    enabled: bool
    priority: int = Field(ge=1, le=1000)
    budget_usd: float | None = Field(default=None, ge=0, le=1000, allow_inf_nan=False)
    prices: ProviderPricesInput | None = None
    key: StoredKey | None = Field(default=None, repr=False)


class UserProviderEntry(BaseModel):
    """Nhà cung cấp người dùng đã đọc từ kho: phần hiển thị (không khoá đầy đủ) và ứng viên để chọn.

    `candidate` là `None` khi khoá không đọc được, URL đã lưu không còn hợp lệ, hoặc thiếu khoá với URL không loopback.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    template: ProviderTemplate
    label: str
    base_url: str = Field(repr=False)
    local: bool
    models: ProviderModels = Field(repr=False)
    key_set: bool
    key_last4: str | None
    key_storage: KeyStorage
    key_unreadable: bool
    enabled: bool
    priority: int
    budget_usd: float
    prices: ProviderPrices
    candidate: ProviderCandidate | None = Field(default=None, repr=False)


def _resolved_prices(template: ProviderTemplate, prices: ProviderPricesInput | None) -> ProviderPrices:
    if prices is None:
        return TEMPLATE_DEFAULT_PRICES[template]
    return ProviderPrices(**prices.model_dump(), source="user")


def _read_key(stored: StoredKey | None) -> tuple[str | None, bool]:
    """`(khoá, không đọc được)`; khoá giải mã ra không hợp lệ cũng coi là không đọc được."""
    if stored is None:
        return None, False
    try:
        key = protector_for(stored.kind).unprotect(stored.value)
    except KeyStorageError:
        return None, True
    if not is_valid_key(key):
        return None, True
    return key, False


def _entry(record: StoredProvider) -> UserProviderEntry:
    key, unreadable = _read_key(record.key)
    budget = TEMPLATE_DEFAULT_BUDGET_USD[record.template] if record.budget_usd is None else record.budget_usd
    prices = _resolved_prices(record.template, record.prices)
    local = is_loopback_url(record.base_url)
    try:
        base_url: str | None = normalize_base_url(record.base_url)
    except ProviderInputError:
        base_url = None
    candidate = None
    if base_url is not None and not unreadable and (key is not None or local):
        candidate = ProviderCandidate(
            provider_id=record.id,
            template=record.template,
            label=record.label,
            base_url=base_url,
            api_key=None if key is None else SecretStr(key),
            models=record.models,
            local=local,
            ip_policy="public_only",
            priority=record.priority,
            budget_usd=budget,
            prices=prices,
            budget_group=record.id,
            enabled=record.enabled,
        )
    return UserProviderEntry(
        id=record.id,
        template=record.template,
        label=record.label,
        base_url=record.base_url,
        local=local,
        models=record.models,
        key_set=record.key is not None,
        key_last4=key_last4(key),
        key_storage="none" if record.key is None else record.key.kind,
        key_unreadable=unreadable,
        enabled=record.enabled,
        priority=record.priority,
        budget_usd=budget,
        prices=prices,
        candidate=candidate,
    )


def _parse_records(settings: dict[str, Any] | None) -> list[StoredProvider]:
    if settings is None:
        return []
    raw = settings.get(PROVIDERS_FIELD)
    if raw is None:
        return []
    if not isinstance(raw, list):
        logger.warning("file cài đặt AI có danh sách nhà cung cấp hỏng, coi như rỗng")
        return []
    records: list[StoredProvider] = []
    seen: set[str] = set()
    skipped = 0
    for item in raw:
        try:
            record = StoredProvider.model_validate(item)
        except ValidationError:
            skipped += 1
            continue
        if record.id in seen:
            skipped += 1
            continue
        seen.add(record.id)
        records.append(record)
    if skipped:
        logger.warning("bỏ qua %d bản ghi nhà cung cấp hỏng trong file cài đặt AI", skipped)
    return records


def _dump(records: Sequence[StoredProvider]) -> list[dict[str, Any]]:
    return [record.model_dump(mode="json") for record in records]


# --------------------------------------------------------------------------- #
# Kho
# --------------------------------------------------------------------------- #


class ProviderStore(Protocol):
    """Kho nhà cung cấp người dùng (spec §5.16, ADR-009 mục 12.4); cấp qua `Depends`, test dùng thư mục tạm."""

    @property
    def persistent(self) -> bool: ...

    def entries(self) -> list[UserProviderEntry]:
        """Mọi nhà cung cấp người dùng theo `priority` rồi id; file hỏng ⇒ rỗng, không ném lỗi."""
        ...

    def create(self, body: ProviderCreate) -> UserProviderEntry: ...

    def update(self, provider_id: str, body: ProviderUpdate) -> UserProviderEntry: ...

    def delete(self, provider_id: str) -> None: ...


class UnpersistedProviderStore:
    """Không có `HOLOCRON_DATA_DIR`: chỉ nhà cung cấp env; mọi thao tác ghi ⇒ `SettingsNotPersistentError`."""

    @property
    def persistent(self) -> bool:
        return False

    def entries(self) -> list[UserProviderEntry]:
        return []

    def create(self, body: ProviderCreate) -> UserProviderEntry:
        raise SettingsNotPersistentError

    def update(self, provider_id: str, body: ProviderUpdate) -> UserProviderEntry:
        raise SettingsNotPersistentError

    def delete(self, provider_id: str) -> None:
        raise SettingsNotPersistentError


def _new_provider_id() -> str:
    return USER_PROVIDER_ID_PREFIX + secrets.token_hex(USER_PROVIDER_ID_HEX_BYTES)


class FileProviderStore:
    """Nhà cung cấp người dùng trong file cài đặt dùng chung; mọi đọc-sửa-ghi trong MỘT đoạn khoá của file."""

    def __init__(
        self,
        settings_file: AiSettingsFile,
        *,
        protector: KeyProtector | None = None,
        new_id: Callable[[], str] = _new_provider_id,
    ) -> None:
        self._file = settings_file
        self._protector = default_key_protector() if protector is None else protector
        self._new_id = new_id

    @property
    def persistent(self) -> bool:
        return True

    def entries(self) -> list[UserProviderEntry]:
        records = _parse_records(self._file.read())
        return [_entry(record) for record in sorted(records, key=lambda record: (record.priority, record.id))]

    def _protect(self, key: str) -> StoredKey:
        return StoredKey(kind=self._protector.kind, value=self._protector.protect(key))

    def create(self, body: ProviderCreate) -> UserProviderEntry:
        base_url = normalize_base_url(body.base_url)
        key = _checked_key(body.api_key)
        if key is None and not is_loopback_url(base_url):
            raise ProviderInputError(KEY_INVALID, KEY_INVALID_MESSAGE)
        label = check_label(body.label, base_url=base_url, key=key, models=body.models)
        stored_key = None if key is None else self._protect(key)
        created: list[StoredProvider] = []

        def mutate(settings: dict[str, Any]) -> None:
            records = _parse_records(settings)
            if len(records) >= MAX_USER_PROVIDERS:
                raise ProviderLimitError
            existing = {record.id for record in records}
            provider_id = self._new_id()
            while provider_id in existing:
                provider_id = self._new_id()
            record = StoredProvider(
                id=provider_id,
                template=body.template,
                label=label,
                base_url=base_url,
                models=body.models,
                enabled=body.enabled,
                priority=body.priority,
                budget_usd=body.budget_usd,
                prices=body.prices,
                key=stored_key,
            )
            settings[PROVIDERS_FIELD] = _dump([*records, record])
            created.append(record)

        self._file.update(mutate)
        return _entry(created[0])

    def update(self, provider_id: str, body: ProviderUpdate) -> UserProviderEntry:
        updated: list[StoredProvider] = []

        def mutate(settings: dict[str, Any]) -> None:
            records = _parse_records(settings)
            index = next((i for i, record in enumerate(records) if record.id == provider_id), None)
            if index is None:
                raise ProviderNotFoundError(provider_id)
            record = self._apply(records[index], body)
            records[index] = record
            settings[PROVIDERS_FIELD] = _dump(records)
            updated.append(record)

        self._file.update(mutate)
        return _entry(updated[0])

    def _apply(self, record: StoredProvider, body: ProviderUpdate) -> StoredProvider:
        fields = body.model_fields_set
        base_url = record.base_url if body.base_url is None else normalize_base_url(body.base_url)
        url_changed = canonical_url(base_url) != canonical_url(record.base_url)
        models = record.models if body.models is None else body.models
        if models.vlm is None and models.llm is None:
            raise ProviderInputError(MODELS_REQUIRED, MODELS_REQUIRED_MESSAGE)
        stored_key: StoredKey | None
        if "api_key" in fields:
            key = _checked_key(body.api_key)
            stored_key = None if key is None else self._protect(key)
        elif url_changed:
            if not is_loopback_url(base_url):
                raise ProviderInputError(KEY_REQUIRED_AFTER_BASE_URL_CHANGE, KEY_REQUIRED_MESSAGE)
            key, stored_key = None, None  # loopback không khoá: bỏ khoá cũ, không gửi nó tới máy khác
        else:
            key, _ = _read_key(record.key)
            stored_key = record.key
        if key is None and stored_key is None and not is_loopback_url(base_url):
            raise ProviderInputError(KEY_INVALID, KEY_INVALID_MESSAGE)
        label = check_label(
            record.label if body.label is None else body.label, base_url=base_url, key=key, models=models
        )
        prices = record.prices
        if body.clear_prices:
            prices = None
        elif body.prices is not None:
            prices = body.prices
        return StoredProvider(
            id=record.id,
            template=record.template,
            label=label,
            base_url=base_url,
            models=models,
            enabled=record.enabled if body.enabled is None else body.enabled,
            priority=record.priority if body.priority is None else body.priority,
            budget_usd=body.budget_usd if "budget_usd" in fields else record.budget_usd,
            prices=prices,
            key=stored_key,
        )

    def delete(self, provider_id: str) -> None:
        def mutate(settings: dict[str, Any]) -> None:
            records = _parse_records(settings)
            remaining = [record for record in records if record.id != provider_id]
            if len(remaining) == len(records):
                raise ProviderNotFoundError(provider_id)
            settings[PROVIDERS_FIELD] = _dump(remaining)

        self._file.update(mutate)


# --------------------------------------------------------------------------- #
# Hợp nhất nguồn env và người dùng
# --------------------------------------------------------------------------- #

_Fingerprint = tuple[str, str]
KeyOrigin = Literal["env", "user"]


def _fingerprint(candidate: ProviderCandidate) -> _Fingerprint:
    key = "" if candidate.api_key is None else candidate.api_key.get_secret_value()
    return canonical_url(candidate.base_url), key


def merge_providers(env: Sequence[ProviderCandidate], user: Sequence[ProviderCandidate]) -> list[ProviderCandidate]:
    """Env trước, rồi người dùng theo `priority`, hoà theo id. Cùng base URL chuẩn hoá VÀ cùng khoá ⇒ cùng nhóm bộ
    đếm (id nhóm = `budget_group` của nhà cung cấp đứng trước). So khớp chỉ trong RAM, không lưu khoá hay hash khoá."""
    ordered = [*sorted(env, key=_order), *sorted(user, key=_order)]
    groups: dict[_Fingerprint, str] = {}
    merged: list[ProviderCandidate] = []
    for candidate in ordered:
        group = groups.setdefault(_fingerprint(candidate), candidate.budget_group)
        merged.append(
            candidate if group == candidate.budget_group else candidate.model_copy(update={"budget_group": group})
        )
    return merged


def _order(candidate: ProviderCandidate) -> tuple[int, str]:
    return candidate.priority, candidate.provider_id


class CombinedProviderSource:
    """Nguồn của `ProviderSelection`: env (đọc lại mỗi lần) + kho người dùng (lấy kho ở mỗi lần gọi qua `store`)."""

    def __init__(self, env: Callable[[], Sequence[ProviderCandidate]], store: Callable[[], ProviderStore]) -> None:
        self._env = env
        self._store = store

    def __call__(self) -> list[ProviderCandidate]:
        user = [entry.candidate for entry in self._store().entries() if entry.candidate is not None]
        return merge_providers(self._env(), user)
