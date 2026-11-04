"""Nhà cung cấp AI do người dùng thêm: lưu/sửa/xoá/kiểm thử khoá (spec §5.16, §6.11; ADR-009 mục 12.1–12.9).

Không gọi mạng: kiểm thử dùng transport giả; thư mục dữ liệu là thư mục tạm của pytest. Khoá ngẫu nhiên không được
xuất hiện ở response, log, `repr` hay 422 (property test nhiều seed).
"""

from __future__ import annotations

import json
import logging
import os
import random
import string
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from holocron_core.domain.ai import (
    MAX_USER_PROVIDERS,
    TEMPLATE_DEFAULT_BUDGET_USD,
    TEMPLATE_DEFAULT_PRICES,
    AiUsage,
    CostEstimate,
    KeyStorageError,
    ProviderCandidate,
    ProviderCreate,
    ProviderModels,
)
from holocron_core.infrastructure.ai_consent_store import AI_SETTINGS_FILENAME, AiSettingsFile, FileAiConsentStore
from holocron_core.infrastructure.ai_runtime import InMemorySpendLedger
from holocron_core.infrastructure.key_protection import (
    DPAPI_AVAILABLE,
    DpapiKeyProtector,
    PlaintextKeyProtector,
    StoredKeyKind,
    default_key_protector,
)
from holocron_core.infrastructure.provider_probe import BlockedAddressError, ProviderProbe
from holocron_core.infrastructure.provider_store import (
    PROVIDERS_FIELD,
    CombinedProviderSource,
    FileProviderStore,
    ProviderStore,
    UnpersistedProviderStore,
)
from holocron_core.routers import deps
from holocron_core.routers import settings as settings_router
from holocron_core.routers.access import LocalAccessMiddleware, get_allowed_hosts, get_required_token
from holocron_core.services.provider_selection import ProviderSelection

TOKEN = "t" * 43
AUTH = {"X-Holocron-Token": TOKEN}
PROVIDERS_URL = "/v1/settings/providers"
SECRET_KEY = "sk-user-provider-secret-0123456789"
HOST = "api.provider.example"
BASE_URL = f"https://{HOST}/v1"
VLM_MODEL = "vision-model-x"
LLM_MODEL = "text-model-y"


@pytest.fixture(autouse=True)
def _clean_ai_env(monkeypatch: pytest.MonkeyPatch) -> None:
    prefixes = ("HOLOCRON_LLM_", "HOLOCRON_VLM_")
    names = ("HOLOCRON_AI_CONSENT", "HOLOCRON_DATA_DIR", "HOLOCRON_REQUIRE_TOKEN")
    for name in [n for n in os.environ if n.startswith(prefixes) or n in names]:
        monkeypatch.delenv(name)


class FakeTransport:
    """Transport giả: trả `(mã, body)` cố định hoặc ném lỗi; ghi lại request đã nhận (không gọi mạng)."""

    def __init__(self, status: int = 200, body: bytes = b"", error: Exception | None = None) -> None:
        self.status = status
        self.body = body
        self.error = error
        self.requests: list[urllib.request.Request] = []
        self.policies: list[str] = []

    def for_policy(self, policy: str) -> Callable[[urllib.request.Request, float], tuple[int, bytes]]:
        self.policies.append(policy)
        return self

    def __call__(self, request: urllib.request.Request, timeout_s: float) -> tuple[int, bytes]:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return self.status, self.body


def _chat_body(usage: dict[str, int] | None = None) -> bytes:
    payload: dict[str, Any] = {"choices": [{"message": {"content": "ok"}}]}
    if usage is not None:
        payload["usage"] = usage
    return json.dumps(payload).encode("utf-8")


class Harness:
    def __init__(
        self,
        store: ProviderStore,
        *,
        token: str | None = TOKEN,
        env: list[ProviderCandidate] | None = None,
        transport: FakeTransport | None = None,
    ) -> None:
        self.store = store
        self.ledger = InMemorySpendLedger()
        env_list = env or []
        self.selection = ProviderSelection(CombinedProviderSource(lambda: env_list, lambda: store), self.ledger)
        self.transport = transport or FakeTransport(body=_chat_body({"prompt_tokens": 10, "completion_tokens": 2}))
        app = FastAPI()
        app.add_middleware(LocalAccessMiddleware, overrides=lambda: app.dependency_overrides)
        app.include_router(settings_router.router)
        app.dependency_overrides[deps.get_provider_store] = lambda: store
        app.dependency_overrides[deps.get_provider_selection] = lambda: self.selection
        app.dependency_overrides[deps.get_provider_probe] = lambda: ProviderProbe(self.transport.for_policy)
        app.dependency_overrides[get_required_token] = lambda: token
        app.dependency_overrides[get_allowed_hosts] = lambda: frozenset({"testserver"})
        self.client = TestClient(app, headers=AUTH if token is not None else None)

    def create(self, **overrides: Any) -> Any:
        body: dict[str, Any] = {
            "template": "gemini",
            "label": "Nhà cung cấp của tôi",
            "base_url": BASE_URL,
            "api_key": SECRET_KEY,
            "models": {"vlm": VLM_MODEL, "llm": LLM_MODEL},
        }
        body.update(overrides)
        return self.client.post(PROVIDERS_URL, json=body, headers=AUTH)


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "data"


@pytest.fixture
def settings_file(data_dir: Path) -> AiSettingsFile:
    return AiSettingsFile(data_dir)


@pytest.fixture
def store(settings_file: AiSettingsFile) -> FileProviderStore:
    return FileProviderStore(settings_file)


@pytest.fixture
def harness(store: FileProviderStore) -> Harness:
    return Harness(store)


def _env_candidate(*, base_url: str = BASE_URL, key: str | None = SECRET_KEY, budget: float = 4.0) -> ProviderCandidate:
    return ProviderCandidate(
        provider_id="env_vlm",
        template="custom",
        label="nhà cung cấp AI đã cấu hình",
        base_url=base_url,
        api_key=None if key is None else SecretStr(key),
        models=ProviderModels(vlm="env-vision-model"),
        local=False,
        ip_policy="operator",
        priority=0,
        budget_usd=budget,
        prices=TEMPLATE_DEFAULT_PRICES["custom"],
        budget_group="env_vlm",
    )


def _raw_file(data_dir: Path) -> str:
    return (data_dir / AI_SETTINGS_FILENAME).read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# Bảo vệ endpoint ghi: 403 / 409
# --------------------------------------------------------------------------- #

WRITE_CALLS: list[tuple[str, str, dict[str, Any] | None]] = [
    ("POST", PROVIDERS_URL, {"template": "gemini"}),
    ("PATCH", f"{PROVIDERS_URL}/usr_0123456789ab", {"label": "x"}),
    ("DELETE", f"{PROVIDERS_URL}/usr_0123456789ab", None),
    ("POST", f"{PROVIDERS_URL}/usr_0123456789ab/test", {"action": "list_models"}),
]


@pytest.mark.parametrize(("method", "url", "body"), WRITE_CALLS)
def test_write_endpoints_without_configured_token_are_403_token_not_configured(
    store: FileProviderStore, data_dir: Path, method: str, url: str, body: dict[str, Any] | None
) -> None:
    response = Harness(store, token=None).client.request(method, url, json=body)
    assert response.status_code == 403
    assert response.json() == {
        "detail": settings_router.TOKEN_NOT_CONFIGURED_DETAIL,
        "code": "token_not_configured",
    }
    assert not (data_dir / AI_SETTINGS_FILENAME).exists()


@pytest.mark.parametrize(("method", "url", "body"), WRITE_CALLS)
def test_write_endpoints_with_wrong_token_are_403(
    store: FileProviderStore, method: str, url: str, body: dict[str, Any] | None
) -> None:
    client = Harness(store).client
    response = client.request(method, url, json=body, headers={"X-Holocron-Token": "sai"})
    assert response.status_code == 403


def test_create_without_data_dir_is_409_settings_not_persistent() -> None:
    harness = Harness(UnpersistedProviderStore())
    response = harness.create()
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "settings_not_persistent"
    listing = harness.client.get(PROVIDERS_URL).json()
    assert listing["settings_persistent"] is False and listing["providers"] == []


@pytest.mark.parametrize("method", ["PATCH", "DELETE"])
def test_patch_delete_without_data_dir_are_409(method: str) -> None:
    response = Harness(UnpersistedProviderStore()).client.request(
        method, f"{PROVIDERS_URL}/usr_0123456789ab", json={"label": "x"} if method == "PATCH" else None, headers=AUTH
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "settings_not_persistent"


@pytest.mark.parametrize("method", ["PATCH", "DELETE"])
def test_env_provider_is_read_only(store: FileProviderStore, method: str) -> None:
    harness = Harness(store, env=[_env_candidate()])
    response = harness.client.request(
        method, f"{PROVIDERS_URL}/env_vlm", json={"label": "x"} if method == "PATCH" else None, headers=AUTH
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "provider_read_only"


@pytest.mark.parametrize("method", ["PATCH", "DELETE"])
@pytest.mark.parametrize("provider_id", ["usr_0123456789ab", "bogus"])
def test_unknown_provider_is_404(harness: Harness, method: str, provider_id: str) -> None:
    response = harness.client.request(
        method, f"{PROVIDERS_URL}/{provider_id}", json={"label": "x"} if method == "PATCH" else None, headers=AUTH
    )
    assert response.status_code == 404


# --------------------------------------------------------------------------- #
# Thêm, liệt kê, sửa, xoá
# --------------------------------------------------------------------------- #


def test_create_then_list_shows_only_last4_and_template_defaults(harness: Harness, data_dir: Path) -> None:
    response = harness.create()
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["id"].startswith("usr_") and len(created["id"]) == 16
    assert created["key_set"] is True and created["key_last4"] == SECRET_KEY[-4:]
    assert created["key_storage"] == default_key_protector().kind
    assert created["base_url"] == BASE_URL and created["source"] == "user" and created["kind"] == "openai_compatible"
    assert created["budget_usd"] == TEMPLATE_DEFAULT_BUDGET_USD["gemini"]
    assert created["prices"]["source"] == "template_default"
    assert created["spent_usd"] == 0.0 and created["budget_exhausted"] is False
    listing = harness.client.get(PROVIDERS_URL)
    assert listing.status_code == 200
    body = listing.json()
    assert body["settings_persistent"] is True and body["write_protection"] == "token"
    assert [item["id"] for item in body["providers"]] == [created["id"]]
    assert SECRET_KEY not in response.text and SECRET_KEY not in listing.text


def test_key_is_not_stored_in_clear_on_windows(harness: Harness, data_dir: Path) -> None:
    harness.create()
    raw = _raw_file(data_dir)
    if DPAPI_AVAILABLE:
        assert SECRET_KEY not in raw
        assert json.loads(raw)[PROVIDERS_FIELD][0]["key"]["kind"] == "dpapi"
    else:
        assert json.loads(raw)[PROVIDERS_FIELD][0]["key"]["kind"] == "plaintext_file"


@pytest.mark.parametrize(("key", "last4"), [("abcdefghijk", None), ("abcdefghijkl", "ijkl")])
def test_key_last4_only_for_keys_of_at_least_12_chars(harness: Harness, key: str, last4: str | None) -> None:
    created = harness.create(api_key=key).json()
    assert created["key_last4"] == last4


def test_env_provider_listed_first_without_endpoint(store: FileProviderStore) -> None:
    harness = Harness(store, env=[_env_candidate()])
    user_id = harness.create(base_url="https://other.example/v1").json()["id"]
    providers = harness.client.get(PROVIDERS_URL).json()["providers"]
    assert [item["id"] for item in providers] == ["env_vlm", user_id]
    env = providers[0]
    assert env["source"] == "env" and env["base_url"] is None and env["key_storage"] == "env"
    assert env["priority"] == 0 and env["enabled"] is True


def test_list_without_token_reports_token_not_configured(store: FileProviderStore) -> None:
    body = Harness(store, token=None).client.get(PROVIDERS_URL).json()
    assert body["write_protection"] == "token_not_configured"


def test_create_with_user_prices_and_budget(harness: Harness) -> None:
    prices = {"input_per_1k_usd": 0.001, "output_per_1k_usd": 0.002, "per_image_usd": 0.0}
    created = harness.create(prices=prices, budget_usd=2.5, priority=7, enabled=False).json()
    assert created["prices"] == {**prices, "source": "user"}
    assert (created["budget_usd"], created["priority"], created["enabled"]) == (2.5, 7, False)


def test_loopback_provider_may_have_no_key(harness: Harness) -> None:
    created = harness.create(base_url="http://127.0.0.1:11434/v1", api_key=None, label="Máy tôi")
    assert created.status_code == 201, created.text
    body = created.json()
    assert (body["key_set"], body["key_storage"], body["local"]) == (False, "none", True)


def test_provider_limit_is_409(settings_file: AiSettingsFile) -> None:
    counter = iter(range(1000))
    harness = Harness(FileProviderStore(settings_file, new_id=lambda: f"usr_{next(counter):012x}"))
    for _ in range(MAX_USER_PROVIDERS):
        assert harness.create().status_code == 201
    response = harness.create()
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "provider_limit"


def test_patch_updates_fields_and_keeps_key(harness: Harness) -> None:
    provider_id = harness.create().json()["id"]
    response = harness.client.patch(
        f"{PROVIDERS_URL}/{provider_id}",
        json={"label": "Tên mới", "enabled": False, "priority": 5, "models": {"llm": LLM_MODEL}},
        headers=AUTH,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["label"], body["enabled"], body["priority"]) == ("Tên mới", False, 5)
    assert body["models"] == {"vlm": None, "llm": LLM_MODEL}
    assert body["key_last4"] == SECRET_KEY[-4:]


def test_patch_budget_null_resets_to_template_default_and_clear_prices(harness: Harness) -> None:
    prices = {"input_per_1k_usd": 0.001, "output_per_1k_usd": 0.002, "per_image_usd": 0.0}
    provider_id = harness.create(budget_usd=2.0, prices=prices).json()["id"]
    response = harness.client.patch(
        f"{PROVIDERS_URL}/{provider_id}", json={"budget_usd": None, "clear_prices": True}, headers=AUTH
    )
    body = response.json()
    assert body["budget_usd"] == TEMPLATE_DEFAULT_BUDGET_USD["gemini"]
    assert body["prices"]["source"] == "template_default"


def test_patch_explicit_null_field_is_422(harness: Harness) -> None:
    provider_id = harness.create().json()["id"]
    response = harness.client.patch(f"{PROVIDERS_URL}/{provider_id}", json={"label": None}, headers=AUTH)
    assert response.status_code == 422


def test_patch_base_url_without_key_is_422_key_required(harness: Harness) -> None:
    provider_id = harness.create().json()["id"]
    response = harness.client.patch(
        f"{PROVIDERS_URL}/{provider_id}", json={"base_url": "https://new-host.example/v1"}, headers=AUTH
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "key_required_after_base_url_change"
    new_key = "sk-new-key-for-new-host-000"
    ok = harness.client.patch(
        f"{PROVIDERS_URL}/{provider_id}",
        json={"base_url": "https://new-host.example/v1", "api_key": new_key},
        headers=AUTH,
    )
    assert ok.status_code == 200 and ok.json()["key_last4"] == new_key[-4:]


def test_patch_base_url_to_loopback_drops_old_key(harness: Harness) -> None:
    provider_id = harness.create().json()["id"]
    response = harness.client.patch(
        f"{PROVIDERS_URL}/{provider_id}", json={"base_url": "http://localhost:8080/v1"}, headers=AUTH
    )
    assert response.status_code == 200, response.text
    assert (response.json()["key_set"], response.json()["local"]) == (False, True)


def test_patch_same_base_url_with_trailing_slash_keeps_key(harness: Harness) -> None:
    provider_id = harness.create().json()["id"]
    response = harness.client.patch(f"{PROVIDERS_URL}/{provider_id}", json={"base_url": BASE_URL + "/"}, headers=AUTH)
    assert response.status_code == 200 and response.json()["key_set"] is True


def test_delete_removes_provider_and_its_key(harness: Harness, data_dir: Path) -> None:
    provider_id = harness.create().json()["id"]
    blob = json.loads(_raw_file(data_dir))[PROVIDERS_FIELD][0]["key"]["value"]
    response = harness.client.delete(f"{PROVIDERS_URL}/{provider_id}", headers=AUTH)
    assert response.status_code == 204
    raw = _raw_file(data_dir)
    assert blob not in raw and SECRET_KEY not in raw
    assert json.loads(raw)[PROVIDERS_FIELD] == []
    assert harness.client.get(PROVIDERS_URL).json()["providers"] == []


def test_providers_and_consent_share_the_settings_file(settings_file: AiSettingsFile, data_dir: Path) -> None:
    consent = FileAiConsentStore(data_dir, settings_file)
    harness = Harness(FileProviderStore(settings_file))
    consent.save(True)
    harness.create()
    consent.save(False)
    saved = json.loads(_raw_file(data_dir))
    assert saved["ai_consent_enabled"] is False and len(saved[PROVIDERS_FIELD]) == 1


# --------------------------------------------------------------------------- #
# 422: mã nghiệp vụ, che input
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "base_url",
    [
        "http://api.provider.example/v1",
        "https://user:pass@api.provider.example/v1",
        "https://api.provider.example/v1?x=1",
        "https://api.provider.example/v1#frag",
        "https://10.0.0.5/v1",
        "https://169.254.169.254/latest",
        "https://192.168.1.10/v1",
        "https://100.64.0.1/v1",
        "https://[::ffff:10.0.0.1]/v1",
        "https://[fe80::1]/v1",
        "https://127.0.0.2/v1",
        "ftp://api.provider.example/v1",
        "https://api.provider .example/v1",
        "https:///v1",
    ],
)
def test_bad_base_url_is_422_base_url_rejected(harness: Harness, data_dir: Path, base_url: str) -> None:
    response = harness.create(base_url=base_url)
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "base_url_rejected"
    assert base_url not in response.text
    assert not (data_dir / AI_SETTINGS_FILENAME).exists()


@pytest.mark.parametrize(
    "label",
    ["https://evil", "Nhãn\x07", f"Khoá {SECRET_KEY}", f"Máy {HOST.upper()}", f"Model {VLM_MODEL}"],
)
def test_bad_label_is_422_label_rejected(harness: Harness, label: str) -> None:
    response = harness.create(label=label)
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "label_rejected"
    assert SECRET_KEY not in response.text


@pytest.mark.parametrize("api_key", [None, "has space-0123456789", "khoá-có-dấu-0123456789"])
def test_missing_or_bad_key_is_422_key_invalid(harness: Harness, api_key: str | None) -> None:
    response = harness.create(api_key=api_key)
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "key_invalid"
    if api_key is not None:
        assert api_key not in response.text


def test_pydantic_422_masks_input_and_ctx(harness: Harness) -> None:
    too_long = "k" * 600
    response = harness.create(api_key=too_long, extra_field=SECRET_KEY)
    assert response.status_code == 422
    assert too_long not in response.text and SECRET_KEY not in response.text
    for error in response.json()["detail"]:
        assert set(error) <= {"loc", "msg", "type"}


def test_models_need_one_role(harness: Harness) -> None:
    response = harness.create(models={"vlm": None, "llm": None})
    assert response.status_code == 422


# --------------------------------------------------------------------------- #
# Kho: file hỏng, khoá không đọc được, ghi nguyên tử, DPAPI
# --------------------------------------------------------------------------- #


def test_corrupt_settings_file_lists_no_user_provider_and_no_500(harness: Harness, data_dir: Path) -> None:
    data_dir.mkdir(parents=True)
    (data_dir / AI_SETTINGS_FILENAME).write_bytes(b"{not json")
    response = harness.client.get(PROVIDERS_URL)
    assert response.status_code == 200 and response.json()["providers"] == []
    assert harness.create().status_code == 201


def test_corrupt_records_are_skipped(harness: Harness, data_dir: Path) -> None:
    harness.create()
    saved = json.loads(_raw_file(data_dir))
    saved[PROVIDERS_FIELD].append({"id": "usr_zzz", "junk": True})
    (data_dir / AI_SETTINGS_FILENAME).write_text(json.dumps(saved), encoding="utf-8")
    assert len(harness.client.get(PROVIDERS_URL).json()["providers"]) == 1


def test_unreadable_key_is_flagged_never_selected_and_test_reports_it(harness: Harness, data_dir: Path) -> None:
    provider_id = harness.create().json()["id"]
    saved = json.loads(_raw_file(data_dir))
    saved[PROVIDERS_FIELD][0]["key"]["value"] = "@@@ not a valid blob or key @@@"
    (data_dir / AI_SETTINGS_FILENAME).write_text(json.dumps(saved), encoding="utf-8")
    provider = harness.client.get(PROVIDERS_URL).json()["providers"][0]
    assert provider["key_unreadable"] is True and provider["key_last4"] is None
    assert harness.selection.peek("vlm") is None
    result = harness.client.post(f"{PROVIDERS_URL}/{provider_id}/test", json={"action": "list_models"}, headers=AUTH)
    assert result.status_code == 200 and result.json()["code"] == "key_unreadable"
    assert harness.transport.requests == []


class FailingProtector:
    @property
    def kind(self) -> StoredKeyKind:
        return "dpapi"

    def protect(self, key: str) -> str:
        raise KeyStorageError("DPAPI lỗi (mã 5)")

    def unprotect(self, stored: str) -> str:
        raise KeyStorageError("DPAPI lỗi (mã 5)")


def test_key_storage_failure_is_500_and_writes_nothing(settings_file: AiSettingsFile, data_dir: Path) -> None:
    harness = Harness(FileProviderStore(settings_file, protector=FailingProtector()))
    response = harness.create()
    assert response.status_code == 500
    assert response.json()["detail"]["code"] == "key_storage_failed"
    assert SECRET_KEY not in response.text
    assert not (data_dir / AI_SETTINGS_FILENAME).exists()


def test_atomic_write_keeps_previous_file_when_replace_fails(
    harness: Harness, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness.create()
    before = _raw_file(data_dir)

    def broken_replace(src: object, dst: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", broken_replace)
    with pytest.raises(OSError):
        harness.store.create(
            ProviderCreate(
                template="custom",
                label="Thứ hai",
                base_url="https://second.example/v1",
                api_key=SecretStr("sk-second-key-000000"),
                models=ProviderModels(llm="m"),
            )
        )
    assert _raw_file(data_dir) == before
    assert [path.name for path in data_dir.iterdir()] == [AI_SETTINGS_FILENAME]


@pytest.mark.skipif(sys.platform == "win32", reason="quyền 0600 chỉ kiểm được trên Unix; Windows dùng DPAPI")
def test_settings_file_is_0600_on_unix(harness: Harness, data_dir: Path) -> None:
    harness.create()
    assert (data_dir / AI_SETTINGS_FILENAME).stat().st_mode & 0o777 == 0o600


@pytest.mark.skipif(not DPAPI_AVAILABLE, reason="DPAPI chỉ có trên Windows; Unix lưu dạng rõ trong file 0600")
def test_dpapi_round_trip() -> None:
    protector = DpapiKeyProtector()
    stored = protector.protect(SECRET_KEY)
    assert SECRET_KEY not in stored
    assert protector.unprotect(stored) == SECRET_KEY
    with pytest.raises(KeyStorageError) as caught:
        protector.unprotect(stored[:-8] + "AAAAAAA=")
    assert SECRET_KEY not in str(caught.value)


def test_dpapi_unavailable_off_windows_raises_key_storage_error() -> None:
    if DPAPI_AVAILABLE:
        pytest.skip("Windows có DPAPI; nhánh này chỉ chạy trên Unix")
    with pytest.raises(KeyStorageError):
        DpapiKeyProtector().protect(SECRET_KEY)


def test_plaintext_protector_is_identity() -> None:
    protector = PlaintextKeyProtector()
    assert protector.unprotect(protector.protect(SECRET_KEY)) == SECRET_KEY


# --------------------------------------------------------------------------- #
# Bộ chọn đọc nhà cung cấp người dùng
# --------------------------------------------------------------------------- #


def test_selector_picks_enabled_user_provider_and_skips_disabled(harness: Harness) -> None:
    provider_id = harness.create().json()["id"]
    preview = harness.selection.peek("vlm")
    assert preview is not None and preview.provider_id == provider_id
    selected = harness.selection.select("llm", CostEstimate(input_tokens=1, max_output_tokens=1, images=0))
    assert selected is not None and selected.model == LLM_MODEL and selected.ip_policy == "public_only"
    harness.selection.release(selected.reservation_id)
    harness.client.patch(f"{PROVIDERS_URL}/{provider_id}", json={"enabled": False}, headers=AUTH)
    assert harness.selection.peek("vlm") is None


def test_env_provider_wins_and_same_url_and_key_share_one_budget(store: FileProviderStore) -> None:
    harness = Harness(store, env=[_env_candidate(budget=4.0)])
    user = harness.create(base_url=BASE_URL + "/", budget_usd=1.0).json()
    other = harness.create(base_url="https://other.example/v1").json()
    providers = {item["id"]: item for item in harness.client.get(PROVIDERS_URL).json()["providers"]}
    assert providers["env_vlm"]["budget_shared_with"] == [user["id"]]
    assert providers[user["id"]]["budget_shared_with"] == ["env_vlm"]
    assert providers[other["id"]]["budget_shared_with"] == []
    preview = harness.selection.peek("vlm")
    assert preview is not None and preview.provider_id == "env_vlm"
    # Trần nhóm = trần nhỏ nhất (1 USD): tiêu 1 USD ⇒ cả hai chạm trần, nhà cung cấp khác được chọn.
    harness.ledger.settle("env_vlm", 0.0, 1.0)
    listing = {item["id"]: item for item in harness.client.get(PROVIDERS_URL).json()["providers"]}
    assert listing["env_vlm"]["budget_exhausted"] is True and listing[user["id"]]["spent_usd"] == 1.0
    preview = harness.selection.peek("vlm")
    assert preview is not None and preview.provider_id == other["id"]


# --------------------------------------------------------------------------- #
# POST …/test
# --------------------------------------------------------------------------- #


def _test(harness: Harness, provider_id: str, body: dict[str, Any]) -> Any:
    return harness.client.post(f"{PROVIDERS_URL}/{provider_id}/test", json=body, headers=AUTH)


def test_chat_test_ok_sends_one_request_and_counts_usage(harness: Harness) -> None:
    provider_id = harness.create().json()["id"]
    response = _test(harness, provider_id, {"action": "chat", "role": "vlm"})
    assert response.status_code == 200, response.text
    assert response.json()["ok"] is True and response.json()["code"] == "ok"
    assert response.json()["http_status"] == 200
    assert len(harness.transport.requests) == 1 and harness.transport.policies == ["public_only"]
    request = harness.transport.requests[0]
    assert request.full_url == BASE_URL + "/chat/completions"
    sent = json.loads(request.data if isinstance(request.data, bytes) else b"{}")
    assert sent["model"] == VLM_MODEL and sent["messages"][0]["content"][1]["type"] == "image_url"
    spent = harness.client.get(PROVIDERS_URL).json()["providers"][0]["spent_usd"]
    expected = 10 / 1000 * 0.0025 + 2 / 1000 * 0.015 + 1 * 0.003
    assert spent == pytest.approx(expected)
    assert SECRET_KEY not in response.text


def test_list_models_filters_names(store: FileProviderStore) -> None:
    names = ["good-model", "bad name", "x" * 201, "ok/model:1"]
    body = json.dumps({"data": [{"id": name} for name in names] + [{"nope": 1}, "str"]}).encode()
    harness = Harness(store, transport=FakeTransport(body=body))
    provider_id = harness.create().json()["id"]
    result = _test(harness, provider_id, {"action": "list_models"}).json()
    assert result["code"] == "ok" and result["models"] == ["good-model", "ok/model:1"]
    assert harness.transport.requests[0].get_method() == "GET"
    assert harness.transport.requests[0].full_url == BASE_URL + "/models"


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (401, "auth_failed"),
        (403, "auth_failed"),
        (404, "not_found"),
        (429, "rate_limited"),
        (302, "redirect_blocked"),
        (500, "upstream_error"),
        (400, "image_rejected"),
    ],
)
def test_http_failures_map_to_sanitized_codes(store: FileProviderStore, status: int, code: str) -> None:
    harness = Harness(store, transport=FakeTransport(status=status, body=b"secret upstream body"))
    provider_id = harness.create().json()["id"]
    response = _test(harness, provider_id, {"action": "chat", "role": "vlm"})
    assert response.json()["code"] == code and response.json()["ok"] is False
    assert "secret upstream body" not in response.text


def test_invalid_json_is_invalid_response(store: FileProviderStore) -> None:
    harness = Harness(store, transport=FakeTransport(body=b"<html>"))
    provider_id = harness.create().json()["id"]
    assert _test(harness, provider_id, {"action": "chat", "role": "llm"}).json()["code"] == "invalid_response"


def test_blocked_address_is_reported_and_not_counted(store: FileProviderStore) -> None:
    error = urllib.error.URLError(BlockedAddressError("địa chỉ bị chặn"))
    harness = Harness(store, transport=FakeTransport(error=error))
    provider_id = harness.create().json()["id"]
    assert _test(harness, provider_id, {"action": "chat", "role": "llm"}).json()["code"] == "blocked_address"
    assert harness.client.get(PROVIDERS_URL).json()["providers"][0]["spent_usd"] == 0.0


def test_timeout_is_reported_and_counted_conservatively(store: FileProviderStore) -> None:
    harness = Harness(store, transport=FakeTransport(error=TimeoutError()))
    provider_id = harness.create().json()["id"]
    assert _test(harness, provider_id, {"action": "chat", "role": "llm"}).json()["code"] == "timeout"
    assert harness.client.get(PROVIDERS_URL).json()["providers"][0]["spent_usd"] > 0.0


def test_no_model_for_role_and_budget_exhausted(harness: Harness) -> None:
    provider_id = harness.create(models={"llm": LLM_MODEL}, budget_usd=0.0).json()["id"]
    assert _test(harness, provider_id, {"action": "chat", "role": "vlm"}).json()["code"] == "no_model_for_role"
    assert _test(harness, provider_id, {"action": "chat", "role": "llm"}).json()["code"] == "budget_exhausted"
    assert harness.transport.requests == []


def test_test_request_role_rules_and_unknown_provider(harness: Harness) -> None:
    provider_id = harness.create().json()["id"]
    assert _test(harness, provider_id, {"action": "chat"}).status_code == 422
    assert _test(harness, provider_id, {"action": "list_models", "role": "llm"}).status_code == 422
    assert _test(harness, "usr_0123456789ab", {"action": "list_models"}).status_code == 404


def test_env_provider_can_be_tested_with_operator_policy(store: FileProviderStore) -> None:
    harness = Harness(store, env=[_env_candidate()])
    assert _test(harness, "env_vlm", {"action": "chat", "role": "vlm"}).json()["code"] == "ok"
    assert harness.transport.policies == ["operator"]


# --------------------------------------------------------------------------- #
# Property: khoá ngẫu nhiên không lộ ở response, log, repr, 422
# --------------------------------------------------------------------------- #

KEY_ALPHABET = string.ascii_letters + string.digits + "-_.~+/="


@pytest.mark.parametrize("seed", range(100))
def test_random_key_never_leaks(seed: int, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    rng = random.Random(seed)
    key = "sk-" + "".join(rng.choice(KEY_ALPHABET) for _ in range(rng.randint(12, 80)))
    store = FileProviderStore(AiSettingsFile(tmp_path / "d"))
    harness = Harness(store, env=[_env_candidate(key=key)])
    texts: list[str] = []
    with caplog.at_level(logging.DEBUG):
        created = harness.create(api_key=key)
        provider_id = created.json()["id"]
        texts.append(created.text)
        texts.append(harness.client.get(PROVIDERS_URL).text)
        texts.append(harness.create(api_key=key, label=f"x{key}").text)  # 422 label chứa khoá
        texts.append(harness.create(api_key=key, base_url="http://evil.example").text)  # 422 base_url
        texts.append(harness.create(api_key=key + " ", label="y").text)  # 422 key_invalid
        texts.append(harness.create(api_key=key, unknown=key).text)  # 422 pydantic
        texts.append(
            harness.client.patch(
                f"{PROVIDERS_URL}/{provider_id}", json={"api_key": key, "label": None}, headers=AUTH
            ).text
        )
        texts.append(_test(harness, provider_id, {"action": "chat", "role": "llm"}).text)
    texts.append(caplog.text)
    entry = store.entries()[0]
    texts.extend([repr(entry), str(entry), repr(entry.candidate), repr(store.entries())])
    selected = harness.selection.select("vlm", CostEstimate(input_tokens=1, max_output_tokens=1, images=0))
    texts.append(repr(selected))
    for text in texts:
        assert key not in text
    if DPAPI_AVAILABLE:
        assert key not in _raw_file(tmp_path / "d")


def test_usage_record_after_probe_uses_reservation(harness: Harness) -> None:
    provider_id = harness.create().json()["id"]
    selected = harness.selection.reserve(
        provider_id, LLM_MODEL, CostEstimate(input_tokens=0, max_output_tokens=0, images=0)
    )
    assert selected is not None
    harness.selection.record(
        selected.reservation_id, AiUsage(input_tokens=1000, output_tokens=0, images=0, estimated=False)
    )
    assert harness.client.get(PROVIDERS_URL).json()["providers"][0]["spent_usd"] == pytest.approx(0.0025)
