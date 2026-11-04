"""Công tắc đồng ý gửi AI và `RuntimeInfo.ai_status` (spec §5.12, §5.16, §6.0, §6.11, §11.2; ADR-009 mục 7, 12.7, 12.11).

Không gọi mạng; thư mục dữ liệu là thư mục tạm của pytest; nhà cung cấp và token cấp bằng `dependency_overrides`.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from holocron_core.domain.ai import (
    TEMPLATE_DEFAULT_PRICES,
    AiConsentState,
    AiRole,
    ProviderCandidate,
    ProviderModels,
    ProviderPreview,
    SettingsNotPersistentError,
)
from holocron_core.infrastructure.ai_consent_store import (
    AI_SETTINGS_FILENAME,
    CONSENT_FIELD,
    MAX_SETTINGS_BYTES,
    FileAiConsentStore,
    UnpersistedAiConsentStore,
)
from holocron_core.infrastructure.ai_runtime import (
    EnvAiConsent,
    InMemorySpendLedger,
    build_ai_status,
    read_consent_state,
)
from holocron_core.routers import deps
from holocron_core.routers import settings as settings_router
from holocron_core.routers.access import LocalAccessMiddleware, get_allowed_hosts, get_required_token
from holocron_core.routers.api import app as main_app
from holocron_core.services.provider_selection import ProviderSelection

TOKEN = "t" * 43
AUTH = {"X-Holocron-Token": TOKEN}
CONSENT_URL = "/v1/settings/ai-consent"
SECRET_KEY = "sk-consent-secret-key-must-never-leak"
SECRET_HOST = "vision.secret-host.example.test"
SECRET_MODEL = "secret-vision-model-name"
LEAKS = (SECRET_KEY, SECRET_HOST, SECRET_MODEL)
AI_ENV_PREFIXES = ("HOLOCRON_LLM_", "HOLOCRON_VLM_")
AI_ENV_NAMES = ("HOLOCRON_AI_CONSENT", "HOLOCRON_DATA_DIR", "HOLOCRON_REQUIRE_TOKEN")


@pytest.fixture(autouse=True)
def _clean_ai_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in [n for n in os.environ if n.startswith(AI_ENV_PREFIXES) or n in AI_ENV_NAMES]:
        monkeypatch.delenv(name)


def _settings_client(store: object, token: str | None) -> TestClient:
    app = FastAPI()
    app.add_middleware(LocalAccessMiddleware, overrides=lambda: app.dependency_overrides)
    app.include_router(settings_router.router)
    app.dependency_overrides[deps.get_ai_consent_store] = lambda: store
    app.dependency_overrides[get_required_token] = lambda: token
    app.dependency_overrides[get_allowed_hosts] = lambda: frozenset({"testserver"})
    return TestClient(app)


@pytest.fixture
def file_store(tmp_path: Path) -> FileAiConsentStore:
    return FileAiConsentStore(tmp_path / "data")


# --------------------------------------------------------------------------- #
# PUT /v1/settings/ai-consent: 403 / 409 từng nhánh
# --------------------------------------------------------------------------- #


def test_put_without_configured_token_is_403_token_not_configured(file_store: FileAiConsentStore) -> None:
    response = _settings_client(file_store, None).put(CONSENT_URL, json={"enabled": True})
    assert response.status_code == 403
    assert response.json() == {
        "detail": settings_router.TOKEN_NOT_CONFIGURED_DETAIL,
        "code": "token_not_configured",
    }
    assert file_store.load() is None
    assert not file_store.path.exists()


def test_put_without_configured_token_is_403_even_with_an_invalid_body(file_store: FileAiConsentStore) -> None:
    response = _settings_client(file_store, None).put(CONSENT_URL, json={"enabled": "nope"})
    assert response.status_code == 403
    assert response.json()["code"] == "token_not_configured"


def test_put_without_configured_token_is_403_before_any_409(monkeypatch: pytest.MonkeyPatch) -> None:
    # Fail-closed: chưa đặt token thì 403 thắng cả khoá env lẫn kho không lưu được (không lộ trạng thái cấu hình).
    monkeypatch.setenv("HOLOCRON_AI_CONSENT", "1")
    response = _settings_client(UnpersistedAiConsentStore(), None).put(CONSENT_URL, json={"enabled": True})
    assert response.status_code == 403
    assert response.json()["code"] == "token_not_configured"


def test_main_app_serves_ai_consent_and_put_is_fail_closed(file_store: FileAiConsentStore) -> None:
    main_app.dependency_overrides[deps.get_ai_consent_store] = lambda: file_store
    main_app.dependency_overrides[get_required_token] = lambda: None
    try:
        client = TestClient(main_app)
        assert client.get(CONSENT_URL).json()["source"] == "default"
        response = client.put(CONSENT_URL, json={"enabled": True})
    finally:
        main_app.dependency_overrides.pop(deps.get_ai_consent_store, None)
        main_app.dependency_overrides.pop(get_required_token, None)
    assert response.status_code == 403
    assert response.json()["code"] == "token_not_configured"
    assert not file_store.path.exists()


@pytest.mark.parametrize("headers", [{}, {"X-Holocron-Token": "sai"}], ids=["missing", "wrong"])
def test_put_with_missing_or_wrong_token_is_403_and_writes_nothing(
    file_store: FileAiConsentStore, headers: dict[str, str]
) -> None:
    response = _settings_client(file_store, TOKEN).put(CONSENT_URL, json={"enabled": True}, headers=headers)
    assert response.status_code == 403
    assert not file_store.path.exists()


@pytest.mark.parametrize("raw", ["1", "0", "yes"])
def test_put_when_env_consent_is_set_is_409_consent_env_locked(
    file_store: FileAiConsentStore, monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    monkeypatch.setenv("HOLOCRON_AI_CONSENT", raw)
    response = _settings_client(file_store, TOKEN).put(CONSENT_URL, json={"enabled": True}, headers=AUTH)
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "consent_env_locked"
    assert detail["message"] == settings_router.CONSENT_ENV_LOCKED_MESSAGE
    assert not file_store.path.exists()


def test_put_without_data_dir_is_409_settings_not_persistent() -> None:
    response = _settings_client(UnpersistedAiConsentStore(), TOKEN).put(
        CONSENT_URL, json={"enabled": True}, headers=AUTH
    )
    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "settings_not_persistent",
        "message": settings_router.SETTINGS_NOT_PERSISTENT_MESSAGE,
    }


def test_put_with_invalid_body_is_422_without_echoing_the_input(file_store: FileAiConsentStore) -> None:
    response = _settings_client(file_store, TOKEN).put(
        CONSENT_URL, json={"enabled": True, "api_key": SECRET_KEY}, headers=AUTH
    )
    assert response.status_code == 422
    for error in response.json()["detail"]:
        assert set(error) <= {"loc", "msg", "type"}
    assert SECRET_KEY not in response.text
    assert not file_store.path.exists()


# --------------------------------------------------------------------------- #
# PUT rồi GET; env thắng file
# --------------------------------------------------------------------------- #


def test_get_defaults_to_off_and_reports_persistence(file_store: FileAiConsentStore) -> None:
    client = _settings_client(file_store, None)
    assert client.get(CONSENT_URL).json() == {
        "enabled": False,
        "source": "default",
        "vlm_mode": "unset",
        "persistent": True,
    }
    unpersisted = _settings_client(UnpersistedAiConsentStore(), None)
    assert unpersisted.get(CONSENT_URL).json()["persistent"] is False


def test_put_then_get_round_trips_through_the_settings_file(file_store: FileAiConsentStore) -> None:
    client = _settings_client(file_store, TOKEN)
    put = client.put(CONSENT_URL, json={"enabled": True}, headers=AUTH)
    assert put.status_code == 200
    expected = {"enabled": True, "source": "setting", "vlm_mode": "unset", "persistent": True}
    assert put.json() == expected
    assert client.get(CONSENT_URL, headers=AUTH).json() == expected
    assert json.loads(file_store.path.read_text(encoding="utf-8")) == {CONSENT_FIELD: True}
    off = client.put(CONSENT_URL, json={"enabled": False}, headers=AUTH)
    assert off.json() == {**expected, "enabled": False}
    # Một instance kho mới đọc lại đúng giá trị đã lưu (lưu bền, không chỉ trong RAM).
    assert FileAiConsentStore(file_store.path.parent).load() is False


def test_save_keeps_other_fields_and_leaves_no_temp_file(file_store: FileAiConsentStore) -> None:
    file_store.path.parent.mkdir(parents=True)
    file_store.path.write_text(json.dumps({"providers": [], CONSENT_FIELD: False}), encoding="utf-8")
    file_store.save(True)
    assert json.loads(file_store.path.read_text(encoding="utf-8")) == {"providers": [], CONSENT_FIELD: True}
    assert sorted(p.name for p in file_store.path.parent.iterdir()) == [AI_SETTINGS_FILENAME]


@pytest.mark.skipif(sys.platform == "win32", reason="quyền POSIX 0600 chỉ kiểm được trên Unix")
def test_settings_file_is_owner_only_on_unix(file_store: FileAiConsentStore) -> None:
    file_store.save(True)
    assert file_store.path.stat().st_mode & 0o777 == 0o600


def test_env_consent_wins_over_the_saved_value(file_store: FileAiConsentStore, monkeypatch: pytest.MonkeyPatch) -> None:
    file_store.save(True)
    monkeypatch.setenv("HOLOCRON_AI_CONSENT", "0")
    state = _settings_client(file_store, None).get(CONSENT_URL).json()
    assert (state["enabled"], state["source"]) == (False, "env")
    assert not EnvAiConsent(store=file_store).auto_send_allowed("image")
    monkeypatch.setenv("HOLOCRON_AI_CONSENT", "1")
    file_store.save(False)
    assert EnvAiConsent(store=file_store).auto_send_allowed("image")


def test_invalid_env_consent_locks_and_counts_as_off(file_store: FileAiConsentStore) -> None:
    file_store.save(True)
    state = read_consent_state({"HOLOCRON_AI_CONSENT": "yes"}, file_store)
    assert state == AiConsentState(enabled=False, source="env", vlm_mode="unset", persistent=True)


def test_ai_consent_reads_the_saved_value_on_every_call(file_store: FileAiConsentStore) -> None:
    consent = EnvAiConsent(dict, store=file_store)
    assert not consent.auto_send_allowed("image")
    file_store.save(True)
    assert consent.auto_send_allowed("image") and consent.auto_send_allowed("text")
    file_store.save(False)
    assert not consent.auto_send_allowed("image")


@pytest.mark.parametrize(
    ("mode", "image", "text"), [("off", False, False), ("images_only", True, False)], ids=["off", "images_only"]
)
def test_vlm_mode_still_caps_the_saved_consent(
    file_store: FileAiConsentStore, mode: str, image: bool, text: bool
) -> None:
    file_store.save(True)
    consent = EnvAiConsent(lambda: {"HOLOCRON_VLM_MODE": mode}, store=file_store)
    assert (consent.auto_send_allowed("image"), consent.auto_send_allowed("text")) == (image, text)


def test_unpersisted_store_refuses_to_save() -> None:
    with pytest.raises(SettingsNotPersistentError):
        UnpersistedAiConsentStore().save(True)


# --------------------------------------------------------------------------- #
# File hỏng ⇒ coi như tắt, không 500
# --------------------------------------------------------------------------- #

CORRUPT_CONTENTS: dict[str, bytes] = {
    "not-json": b"{enabled: true",
    "not-utf8": b"\xff\xfe\x00{",
    "array": b"[true]",
    "string-value": json.dumps({CONSENT_FIELD: "true"}).encode(),
    "number-value": json.dumps({CONSENT_FIELD: 1}).encode(),
    "too-large": b'{"' + CONSENT_FIELD.encode() + b'": true, "pad": "' + b"x" * MAX_SETTINGS_BYTES + b'"}',
}


@pytest.mark.parametrize("content", list(CORRUPT_CONTENTS.values()), ids=list(CORRUPT_CONTENTS))
def test_corrupt_settings_file_counts_as_off_without_500(
    file_store: FileAiConsentStore, content: bytes, caplog: pytest.LogCaptureFixture
) -> None:
    file_store.path.parent.mkdir(parents=True)
    file_store.path.write_bytes(content)
    with caplog.at_level(logging.WARNING):
        response = _settings_client(file_store, None).get(CONSENT_URL)
        allowed = EnvAiConsent(dict, store=file_store).auto_send_allowed("image")
    assert response.status_code == 200
    assert (response.json()["enabled"], response.json()["source"]) == (False, "default")
    assert allowed is False
    assert str(file_store.path.parent) not in caplog.text


def test_put_over_a_corrupt_file_rewrites_it(file_store: FileAiConsentStore) -> None:
    file_store.path.parent.mkdir(parents=True)
    file_store.path.write_bytes(b"{broken")
    response = _settings_client(file_store, TOKEN).put(CONSENT_URL, json={"enabled": True}, headers=AUTH)
    assert response.status_code == 200 and response.json()["source"] == "setting"
    assert file_store.load() is True


def test_settings_path_that_is_a_directory_counts_as_off(file_store: FileAiConsentStore) -> None:
    file_store.path.mkdir(parents=True)
    assert file_store.load() is None
    assert _settings_client(file_store, None).get(CONSENT_URL).status_code == 200


# --------------------------------------------------------------------------- #
# Kho cấp qua Depends từ HOLOCRON_DATA_DIR
# --------------------------------------------------------------------------- #


def test_store_dependency_follows_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert deps.get_ai_consent_store().persistent is False
    monkeypatch.setenv("HOLOCRON_DATA_DIR", str(tmp_path))
    store = deps.get_ai_consent_store()
    assert store.persistent is True
    assert deps.get_ai_consent_store() is store
    store.save(True)
    assert (tmp_path / AI_SETTINGS_FILENAME).is_file()
    assert deps.get_ai_consent(store).auto_send_allowed("image")


# --------------------------------------------------------------------------- #
# RuntimeInfo.ai_status
# --------------------------------------------------------------------------- #


def _candidate(*, models: ProviderModels | None = None, local: bool = False) -> ProviderCandidate:
    return ProviderCandidate(
        provider_id="env_vlm",
        template="gemini",
        label="Gemini của tôi",
        base_url=f"https://{SECRET_HOST}/v1",
        api_key=SECRET_KEY,
        models=models or ProviderModels(vlm=SECRET_MODEL),
        local=local,
        ip_policy="operator",
        priority=0,
        budget_usd=1.0,
        prices=TEMPLATE_DEFAULT_PRICES["gemini"],
        budget_group="env_vlm",
    )


class Runtime:
    """`main_app` với kho công tắc tạm và bộ chọn thật trên nguồn nhà cung cấp do test đặt."""

    def __init__(self, store: FileAiConsentStore) -> None:
        self.providers: list[ProviderCandidate] = [_candidate()]
        self.ledger = InMemorySpendLedger()
        self.token: str | None = None
        selection = ProviderSelection(lambda: self.providers, self.ledger)
        main_app.dependency_overrides[deps.get_ai_consent_store] = lambda: store
        main_app.dependency_overrides[deps.get_provider_selection] = lambda: selection
        main_app.dependency_overrides[get_required_token] = lambda: self.token
        self.client = TestClient(main_app)

    def ai_status(self) -> dict[str, object]:
        response = self.client.get("/v1/runtime", headers=AUTH if self.token else {})
        assert response.status_code == 200
        for leaked in LEAKS:
            assert leaked not in response.text
        status: dict[str, object] = response.json()["ai_status"]
        return status

    def role(self, name: str) -> dict[str, object]:
        roles = self.ai_status()["roles"]
        assert isinstance(roles, list)
        found: dict[str, object] = next(role for role in roles if role["role"] == name)
        return found


@pytest.fixture
def runtime(file_store: FileAiConsentStore) -> Iterator[Runtime]:
    harness = Runtime(file_store)
    try:
        yield harness
    finally:
        for dependency in (deps.get_ai_consent_store, deps.get_provider_selection, get_required_token):
            main_app.dependency_overrides.pop(dependency, None)


def _blocked(role: str, reason: str) -> dict[str, object]:
    return {
        "role": role,
        "auto_send_allowed": False,
        "reason": reason,
        "provider_id": None,
        "recipient_label": None,
        "local": False,
    }


def test_ai_status_default_is_consent_off_with_the_recipient(runtime: Runtime) -> None:
    assert runtime.ai_status() == {
        "consent_enabled": False,
        "consent_source": "default",
        "vlm_mode": "unset",
        "settings_persistent": True,
        "write_protection": "token_not_configured",
        "roles": [
            {
                "role": "vlm",
                "auto_send_allowed": False,
                "reason": "consent_off",
                "provider_id": "env_vlm",
                "recipient_label": "Gemini của tôi",
                "local": False,
            },
            _blocked("llm", "no_provider"),
        ],
    }


def test_ai_status_follows_saved_consent_on_every_get(runtime: Runtime, file_store: FileAiConsentStore) -> None:
    file_store.save(True)
    vlm = runtime.role("vlm")
    assert (vlm["auto_send_allowed"], vlm["reason"]) == (True, None)
    status = runtime.ai_status()
    assert (status["consent_enabled"], status["consent_source"]) == (True, "setting")
    file_store.save(False)
    assert runtime.role("vlm")["reason"] == "consent_off"


def test_ai_status_reports_env_lock_and_token_protection(
    runtime: Runtime, file_store: FileAiConsentStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    file_store.save(False)
    monkeypatch.setenv("HOLOCRON_AI_CONSENT", "1")
    runtime.token = TOKEN
    status = runtime.ai_status()
    assert (status["consent_enabled"], status["consent_source"], status["write_protection"]) == (True, "env", "token")


@pytest.mark.parametrize(
    ("mode", "vlm_reason", "llm_reason"),
    [("off", "mode_off", "mode_off"), ("images_only", None, "mode_images_only"), ("", None, None)],
    ids=["off", "images_only", "unset"],
)
def test_ai_status_vlm_mode_caps_each_role(
    runtime: Runtime,
    file_store: FileAiConsentStore,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    vlm_reason: str | None,
    llm_reason: str | None,
) -> None:
    file_store.save(True)
    monkeypatch.setenv("HOLOCRON_VLM_MODE", mode)
    runtime.providers = [_candidate(models=ProviderModels(vlm=SECRET_MODEL, llm=SECRET_MODEL))]
    assert (runtime.role("vlm")["reason"], runtime.role("llm")["reason"]) == (vlm_reason, llm_reason)
    assert runtime.ai_status()["vlm_mode"] == (mode or "unset")


def test_ai_status_mode_off_wins_over_consent_off(runtime: Runtime, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOLOCRON_VLM_MODE", "off")
    assert runtime.role("vlm")["reason"] == "mode_off"


def test_ai_status_budget_exhausted_hides_the_provider(runtime: Runtime, file_store: FileAiConsentStore) -> None:
    file_store.save(True)
    runtime.ledger.settle("env_vlm", 0.0, 1.0)
    assert runtime.role("vlm") == _blocked("vlm", "budget_exhausted")


def test_ai_status_without_provider_is_no_provider_for_both_roles(runtime: Runtime) -> None:
    runtime.providers = []
    assert runtime.ai_status()["roles"] == [_blocked("vlm", "no_provider"), _blocked("llm", "no_provider")]


def test_ai_status_reports_local_provider(runtime: Runtime, file_store: FileAiConsentStore) -> None:
    file_store.save(True)
    runtime.providers = [_candidate(local=True)]
    assert runtime.role("vlm")["local"] is True


def test_ai_status_never_reserves_budget_and_leaks_nothing_to_logs(
    runtime: Runtime, file_store: FileAiConsentStore, caplog: pytest.LogCaptureFixture
) -> None:
    file_store.save(True)
    with caplog.at_level(logging.DEBUG):
        for _ in range(3):
            assert runtime.role("vlm")["auto_send_allowed"] is True
    assert runtime.ledger.reserved_usd("env_vlm") == 0.0
    assert runtime.ledger.spent_usd("env_vlm") == 0.0
    for leaked in LEAKS:
        assert leaked not in caplog.text


def test_ai_status_from_real_env_provider_leaks_no_key_or_endpoint(
    file_store: FileAiConsentStore, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Đường mặc định (không override bộ chọn): nhà cung cấp `env_vlm` đọc từ `HOLOCRON_VLM_*`."""
    monkeypatch.setenv("HOLOCRON_VLM_BASE_URL", f"https://{SECRET_HOST}/v1")
    monkeypatch.setenv("HOLOCRON_VLM_API_KEY", SECRET_KEY)
    monkeypatch.setenv("HOLOCRON_VLM_MODEL", SECRET_MODEL)
    main_app.dependency_overrides[deps.get_ai_consent_store] = lambda: file_store
    try:
        with caplog.at_level(logging.DEBUG):
            response = TestClient(main_app).get("/v1/runtime")
    finally:
        main_app.dependency_overrides.pop(deps.get_ai_consent_store, None)
    assert response.status_code == 200
    vlm = response.json()["ai_status"]["roles"][0]
    assert (vlm["role"], vlm["provider_id"], vlm["reason"]) == ("vlm", "env_vlm", "consent_off")
    for leaked in LEAKS:
        assert leaked not in response.text
        assert leaked not in caplog.text


class _NoProviders:
    def peek(self, role: AiRole) -> ProviderPreview | None:
        return None

    def budget_exhausted(self, role: AiRole) -> bool:
        return False


def test_build_ai_status_always_lists_vlm_then_llm() -> None:
    consent = AiConsentState(enabled=True, source="setting", vlm_mode="unset", persistent=True)
    status = build_ai_status(consent, _NoProviders(), "token")
    assert [role.role for role in status.roles] == ["vlm", "llm"]
    assert all(role.reason == "no_provider" for role in status.roles)


# --------------------------------------------------------------------------- #
# llm_configured / vlm_configured / capabilities theo nguồn hợp nhất (ADR-009 mục 12.11)
# --------------------------------------------------------------------------- #


def _user_llm(*, enabled: bool = True, key: bool = True) -> ProviderCandidate:
    return ProviderCandidate(
        provider_id="usr_0123456789ab",
        template="custom",
        label="Nhà cung cấp người dùng",
        base_url=f"https://{SECRET_HOST}/v1",
        api_key=SECRET_KEY if key else None,
        models=ProviderModels(llm=SECRET_MODEL),
        local=False,
        ip_policy="public_only",
        priority=100,
        budget_usd=1.0,
        prices=TEMPLATE_DEFAULT_PRICES["custom"],
        budget_group="usr_0123456789ab",
        enabled=enabled,
    )


def _runtime_info(runtime: Runtime) -> dict[str, object]:
    response = runtime.client.get("/v1/runtime")
    assert response.status_code == 200
    for leaked in LEAKS:
        assert leaked not in response.text
    info: dict[str, object] = response.json()
    return info


def _capability(info: dict[str, object], name: str) -> dict[str, object]:
    capabilities = info["capabilities"]
    assert isinstance(capabilities, list)
    found: dict[str, object] = next(item for item in capabilities if item["name"] == name)
    return found


def test_runtime_flags_and_llm_capability_follow_user_providers(runtime: Runtime) -> None:
    runtime.providers = [_user_llm()]
    info = _runtime_info(runtime)
    assert info["llm_configured"] is True and info["vlm_configured"] is False
    assert _capability(info, "concept_pair_llm_proposal")["available"] is True
    assert _capability(info, "vlm")["reason"] == "not_implemented"


@pytest.mark.parametrize("candidate", [_user_llm(enabled=False), _user_llm(key=False)])
def test_disabled_or_keyless_user_provider_is_not_configured(runtime: Runtime, candidate: ProviderCandidate) -> None:
    runtime.providers = [candidate]
    info = _runtime_info(runtime)
    assert info["llm_configured"] is False and info["vlm_configured"] is False
    assert _capability(info, "concept_pair_llm_proposal")["reason"] == "llm_not_configured"


def test_runtime_vlm_configured_from_env_provider_in_selection(runtime: Runtime) -> None:
    info = _runtime_info(runtime)
    assert info["vlm_configured"] is True and info["llm_configured"] is False


def test_runtime_reads_providers_once_per_request(runtime: Runtime) -> None:
    calls: list[int] = []
    providers = [_candidate(), _user_llm()]

    def source() -> list[ProviderCandidate]:
        calls.append(1)
        return providers

    main_app.dependency_overrides[deps.get_provider_selection] = lambda: ProviderSelection(source, runtime.ledger)
    _runtime_info(runtime)
    assert len(calls) == 1
