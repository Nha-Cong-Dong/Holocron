"""Gia cố kết nối AI sau review V04-min (ADR-009 mục 12.2, 12.3, 12.4, 12.8, 12.11; spec §5.16, §13.1).

Không gọi mạng: bộ phân giải, hàm nối và context TLS đều giả. Thư mục dữ liệu là thư mục tạm của pytest.
"""

from __future__ import annotations

import base64
import ipaddress
import json
import logging
import os
import socket
import ssl
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from holocron_core.domain.ai import (
    BASE_URL_MAX_LENGTH,
    KEY_MAX_LENGTH,
    LABEL_MAX_LENGTH,
    MAX_TEST_MODELS,
    TEMPLATE_DEFAULT_PRICES,
    AiUsage,
    CostEstimate,
    IpPolicy,
    KeyStorageError,
    ProviderCandidate,
    ProviderCreate,
    ProviderInputError,
    ProviderModels,
    ProviderTestRequest,
    SelectedProvider,
    estimate_cost_usd,
)
from holocron_core.domain.llm import ChatMessage, LlmUpstreamError, TextPart
from holocron_core.infrastructure import key_protection, provider_probe, vision_reader
from holocron_core.infrastructure.ai_consent_store import (
    AI_SETTINGS_FILENAME,
    MAX_SETTINGS_BYTES,
    AiSettingsFile,
    FileAiConsentStore,
    SettingsReadError,
)
from holocron_core.infrastructure.ai_runtime import AI_USAGE_FILENAME, FileSpendLedger, InMemorySpendLedger
from holocron_core.infrastructure.key_protection import DPAPI_AVAILABLE, DpapiKeyProtector
from holocron_core.infrastructure.llm_client import UrllibTransport
from holocron_core.infrastructure.llm_config import LlmHttpLimits, VlmSettings
from holocron_core.infrastructure.provider_probe import (
    AddressGuard,
    BlockedAddressError,
    GuardedHTTPHandler,
    GuardedHTTPSHandler,
    ProviderProbe,
    Resolver,
    guarded_transport,
    probe_estimate,
)
from holocron_core.infrastructure.provider_store import (
    PROVIDERS_FIELD,
    FileProviderStore,
    address_is_public,
    check_label,
    merge_providers,
    normalize_base_url,
)
from holocron_core.routers import deps
from holocron_core.services.provider_selection import ProviderSelection

SECRET_KEY = "sk-hardening-secret-0123456789"
PUBLIC_IP = "93.184.216.34"


def _resolver(addresses: list[str]) -> Resolver:
    def resolve(host: str, port: int) -> list[str]:
        return addresses

    return resolve


# --------------------------------------------------------------------------- #
# Địa chỉ IPv6 nhúng IPv4 (NAT64, IPv4-compatible, 6to4)
# --------------------------------------------------------------------------- #

EMBEDDED_PRIVATE = [
    "64:ff9b::a00:1",  # NAT64 → 10.0.0.1
    "64:ff9b::a9fe:a9fe",  # NAT64 → 169.254.169.254
    "64:ff9b::7f00:1",  # NAT64 → 127.0.0.1
    "64:ff9b::c0a8:101",  # NAT64 → 192.168.1.1
    "::a00:1",  # IPv4-compatible → 10.0.0.1
    "::a9fe:a9fe",  # IPv4-compatible → 169.254.169.254
    "::7f00:1",  # IPv4-compatible → 127.0.0.1
    "2002:a00:1::1",  # 6to4 → 10.0.0.1
    "64:ff9b:1::a00:1",  # NAT64 dùng cục bộ
    "::ffff:a00:1",  # IPv4-mapped
]


@pytest.mark.parametrize("address", EMBEDDED_PRIVATE)
def test_embedded_private_ipv4_is_not_public(address: str) -> None:
    assert not address_is_public(ipaddress.ip_address(address))
    with pytest.raises(BlockedAddressError):
        AddressGuard("public_only", resolver=_resolver([address])).pick("api.example.com", 443)


@pytest.mark.parametrize("address", ["64:ff9b::a00:1", "::a9fe:a9fe"])
def test_base_url_with_embedded_private_literal_is_rejected(address: str) -> None:
    with pytest.raises(ProviderInputError):
        normalize_base_url(f"https://[{address}]/v1")


@pytest.mark.parametrize("address", ["64:ff9b::808:808", "2001:4860:4860::8888", "8.8.8.8"])
def test_embedded_public_ipv4_stays_public(address: str) -> None:
    assert address_is_public(ipaddress.ip_address(address))


def test_empty_resolution_is_blocked() -> None:
    with pytest.raises(BlockedAddressError):
        AddressGuard("public_only", resolver=_resolver([])).pick("api.example.com", 443)


# --------------------------------------------------------------------------- #
# Kiểm tĩnh: biên độ dài và ký tự cấm
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("suffix", ["#", "?", "#frag", "?a=1"])
def test_base_url_with_empty_fragment_or_query_is_rejected(suffix: str) -> None:
    with pytest.raises(ProviderInputError):
        normalize_base_url("https://api.example.com/v1" + suffix)


def test_base_url_length_boundary() -> None:
    prefix = "https://api.example.com/"
    exact = prefix + "a" * (BASE_URL_MAX_LENGTH - len(prefix))
    assert normalize_base_url(exact) == exact
    with pytest.raises(ProviderInputError):
        normalize_base_url(exact + "a")


def test_label_length_boundary() -> None:
    models = ProviderModels(llm="m")
    exact = "a" * LABEL_MAX_LENGTH
    assert check_label(exact, base_url="https://api.example.com/v1", key=None, models=models) == exact
    with pytest.raises(ProviderInputError):
        check_label(exact + "a", base_url="https://api.example.com/v1", key=None, models=models)


def _unchecked_create(key: str) -> ProviderCreate:
    """Body bỏ qua kiểm của pydantic để chạm lớp kiểm thứ hai trong kho."""
    return ProviderCreate.model_construct(
        template="custom",
        label="Nhà cung cấp",
        base_url="https://api.example.com/v1",
        api_key=SecretStr(key),
        models=ProviderModels(llm="m"),
        enabled=True,
        priority=100,
        budget_usd=None,
        prices=None,
    )


def test_key_length_boundary_in_store(tmp_path: Path) -> None:
    store = FileProviderStore(AiSettingsFile(tmp_path))
    assert store.create(_unchecked_create("k" * KEY_MAX_LENGTH)).key_set
    with pytest.raises(ProviderInputError) as caught:
        store.create(_unchecked_create("k" * (KEY_MAX_LENGTH + 1)))
    assert caught.value.code == "key_invalid"
    assert len(store.entries()) == 1


# --------------------------------------------------------------------------- #
# Bản ghi trong file: khoá thiếu/hỏng, id trùng
# --------------------------------------------------------------------------- #


def _record(
    provider_id: str, *, base_url: str = "https://api.example.com/v1", key: dict[str, str] | None = None
) -> dict[str, Any]:
    return {
        "id": provider_id,
        "template": "custom",
        "label": "Nhà cung cấp",
        "base_url": base_url,
        "models": {"vlm": None, "llm": "text-model"},
        "enabled": True,
        "priority": 100,
        "budget_usd": None,
        "prices": None,
        "key": key,
    }


def _write_records(data_dir: Path, records: list[dict[str, Any]]) -> FileProviderStore:
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / AI_SETTINGS_FILENAME).write_text(json.dumps({PROVIDERS_FIELD: records}), encoding="utf-8")
    return FileProviderStore(AiSettingsFile(data_dir))


def test_remote_record_without_key_is_never_a_candidate(tmp_path: Path) -> None:
    store = _write_records(tmp_path, [_record("usr_000000000001")])
    entry = store.entries()[0]
    assert entry.candidate is None and not entry.key_set and not entry.local


def test_loopback_record_with_unreadable_key_is_never_a_candidate(tmp_path: Path) -> None:
    store = _write_records(
        tmp_path,
        [
            _record(
                "usr_000000000002",
                base_url="http://127.0.0.1:11434/v1",
                key={"kind": "dpapi", "value": "@@@ không phải base64 @@@"},
            )
        ],
    )
    entry = store.entries()[0]
    assert entry.local and entry.key_unreadable and entry.candidate is None


def test_decoded_key_that_is_not_a_valid_key_is_unreadable(tmp_path: Path) -> None:
    store = _write_records(
        tmp_path, [_record("usr_000000000003", key={"kind": "plaintext_file", "value": "sk bad key with spaces"})]
    )
    entry = store.entries()[0]
    assert entry.key_unreadable and entry.candidate is None and entry.key_last4 is None


def test_duplicate_ids_in_file_keep_only_the_first(tmp_path: Path) -> None:
    key = {"kind": "plaintext_file", "value": SECRET_KEY}
    first = _record("usr_000000000004", key=key)
    second = _record("usr_000000000004", key=key) | {"label": "Bản sao"}
    entries = _write_records(tmp_path, [first, second]).entries()
    assert len(entries) == 1 and entries[0].label == "Nhà cung cấp"


# --------------------------------------------------------------------------- #
# Hợp nhất nguồn và trần nhóm
# --------------------------------------------------------------------------- #


def _candidate(
    provider_id: str,
    *,
    base_url: str = "https://api.example.com/v1",
    key: str | None = SECRET_KEY,
    budget: float = 10.0,
    priority: int = 100,
) -> ProviderCandidate:
    return ProviderCandidate(
        provider_id=provider_id,
        template="custom",
        label=f"Nhà cung cấp {provider_id}",
        base_url=base_url,
        api_key=None if key is None else SecretStr(key),
        models=ProviderModels(vlm="vision", llm="text"),
        local=False,
        ip_policy="public_only",
        priority=priority,
        budget_usd=budget,
        prices=TEMPLATE_DEFAULT_PRICES["custom"],
        budget_group=provider_id,
    )


def test_merge_groups_same_canonical_url_and_same_key_only() -> None:
    merged = merge_providers(
        [],
        [
            _candidate("usr_00000000000a", base_url="https://api.example.com/v1", priority=1),
            _candidate("usr_00000000000b", base_url="HTTPS://API.Example.com:443/v1/", priority=2),
            _candidate(
                "usr_00000000000c", base_url="https://api.example.com/v1", key="sk-other-key-000000", priority=3
            ),
            _candidate("usr_00000000000d", base_url="https://api.example.com/v2", priority=4),
        ],
    )
    groups = {candidate.provider_id: candidate.budget_group for candidate in merged}
    assert groups == {
        "usr_00000000000a": "usr_00000000000a",
        "usr_00000000000b": "usr_00000000000a",
        "usr_00000000000c": "usr_00000000000c",
        "usr_00000000000d": "usr_00000000000d",
    }


ESTIMATE = CostEstimate(input_tokens=10, max_output_tokens=10, images=0)


def _shared_group_selection() -> tuple[ProviderSelection, InMemorySpendLedger]:
    """Hai nhà cung cấp cùng nhóm: trần riêng 0.01 và 10 USD ⇒ trần nhóm 0.01 USD, đã tiêu hết."""
    candidates = merge_providers(
        [],
        [
            _candidate("usr_0000000000a1", budget=0.01, priority=1),
            _candidate("usr_0000000000a2", budget=10.0, priority=2),
        ],
    )
    ledger = InMemorySpendLedger()
    ledger.settle("usr_0000000000a1", 0.0, 0.01)
    return ProviderSelection(lambda: candidates, ledger), ledger


def test_select_uses_the_group_budget_not_the_own_budget() -> None:
    selection, _ = _shared_group_selection()
    assert selection.select("llm", ESTIMATE) is None
    assert selection.peek("llm") is None


def test_reserve_uses_the_group_budget_not_the_own_budget() -> None:
    selection, _ = _shared_group_selection()
    assert selection.reserve("usr_0000000000a2", "text", ESTIMATE) is None


def test_budget_exhausted_uses_the_group_budget() -> None:
    selection, _ = _shared_group_selection()
    assert selection.budget_exhausted("llm") is True


def test_configured_and_snapshot_read_the_source_once() -> None:
    calls: list[int] = []
    disabled = _candidate("usr_0000000000b1").model_copy(update={"enabled": False})

    def source() -> list[ProviderCandidate]:
        calls.append(1)
        return [disabled, _candidate("usr_0000000000b2").model_copy(update={"models": ProviderModels(llm="t")})]

    view = ProviderSelection(source, InMemorySpendLedger()).snapshot()
    assert view.configured("llm") and not view.configured("vlm")
    view.peek("llm")
    view.budget_exhausted("vlm")
    assert len(calls) == 1


# --------------------------------------------------------------------------- #
# Khoá DPAPI: entropy và base64 chặt
# --------------------------------------------------------------------------- #


def test_stored_blob_with_non_base64_characters_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(key_protection, "dpapi_unprotect", lambda blob: blob)
    stored = base64.b64encode(SECRET_KEY.encode("ascii")).decode("ascii")
    assert DpapiKeyProtector().unprotect(stored) == SECRET_KEY
    with pytest.raises(KeyStorageError):
        DpapiKeyProtector().unprotect(stored[:4] + "*" + stored[4:])


@pytest.mark.skipif(not DPAPI_AVAILABLE, reason="DPAPI chỉ có trên Windows")
def test_dpapi_blob_needs_the_holocron_entropy(monkeypatch: pytest.MonkeyPatch) -> None:
    protector = DpapiKeyProtector()
    monkeypatch.setattr(key_protection, "DPAPI_ENTROPY", b"other-application-entropy")
    foreign = protector.protect(SECRET_KEY)
    monkeypatch.undo()
    with pytest.raises(KeyStorageError):
        protector.unprotect(foreign)
    assert protector.unprotect(protector.protect(SECRET_KEY)) == SECRET_KEY


# --------------------------------------------------------------------------- #
# File cài đặt: không đè mất dữ liệu khi không đọc được
# --------------------------------------------------------------------------- #


def _pad_json(payload: dict[str, Any], size: int) -> bytes:
    raw = json.dumps(payload).encode("utf-8")
    return raw + b" " * (size - len(raw))


def test_settings_file_of_exactly_max_bytes_is_read(tmp_path: Path) -> None:
    tmp_path.joinpath(AI_SETTINGS_FILENAME).write_bytes(_pad_json({"ai_consent_enabled": True}, MAX_SETTINGS_BYTES))
    assert FileAiConsentStore(tmp_path).load() is True


def test_settings_file_over_max_bytes_is_not_read_and_not_overwritten(tmp_path: Path) -> None:
    original = _pad_json({"ai_consent_enabled": True, PROVIDERS_FIELD: []}, MAX_SETTINGS_BYTES + 1)
    path = tmp_path / AI_SETTINGS_FILENAME
    path.write_bytes(original)
    store = FileAiConsentStore(tmp_path)
    assert store.load() is None
    with pytest.raises(SettingsReadError):
        store.save(False)
    with pytest.raises(SettingsReadError):
        FileProviderStore(AiSettingsFile(tmp_path)).create(_unchecked_create(SECRET_KEY))
    assert path.read_bytes() == original
    assert sorted(item.name for item in tmp_path.iterdir()) == [AI_SETTINGS_FILENAME]


def test_settings_file_io_error_refuses_to_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / AI_SETTINGS_FILENAME
    path.write_text(json.dumps({"ai_consent_enabled": True, PROVIDERS_FIELD: []}), encoding="utf-8")
    before = path.read_bytes()
    real_open = Path.open

    def failing_open(self: Path, *args: Any, **kwargs: Any) -> Any:
        if self == path:
            raise PermissionError("giả: bị khoá")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", failing_open)
    with pytest.raises(SettingsReadError):
        FileAiConsentStore(tmp_path).save(False)
    monkeypatch.undo()
    assert path.read_bytes() == before


def test_settings_file_with_invalid_json_is_replaced(tmp_path: Path) -> None:
    path = tmp_path / AI_SETTINGS_FILENAME
    path.write_bytes(b"{not json")
    FileAiConsentStore(tmp_path).save(True)
    assert json.loads(path.read_text(encoding="utf-8")) == {"ai_consent_enabled": True}


# --------------------------------------------------------------------------- #
# Bộ đếm chi phí lưu bền (ai_usage.json)
# --------------------------------------------------------------------------- #


def test_file_ledger_survives_restart_and_blocks_the_budget(tmp_path: Path) -> None:
    ledger = FileSpendLedger(tmp_path)
    assert ledger.try_reserve("usr_0000000000c1", 0.6, 1.0)
    ledger.settle("usr_0000000000c1", 0.6, 0.6)
    assert ledger.try_reserve("usr_0000000000c1", 0.3, 1.0)  # chỗ giữ chỉ ở RAM
    restarted = FileSpendLedger(tmp_path)
    assert restarted.spent_usd("usr_0000000000c1") == pytest.approx(0.6)
    assert restarted.reserved_usd("usr_0000000000c1") == 0.0
    assert not restarted.try_reserve("usr_0000000000c1", 0.5, 1.0)
    saved = json.loads((tmp_path / AI_USAGE_FILENAME).read_text(encoding="utf-8"))
    assert saved == {"version": 1, "spent_usd": {"usr_0000000000c1": pytest.approx(0.6)}}


def test_file_ledger_release_without_cost_does_not_write(tmp_path: Path) -> None:
    ledger = FileSpendLedger(tmp_path)
    assert ledger.try_reserve("env_vlm", 0.1, 1.0)
    ledger.settle("env_vlm", 0.1, 0.0)
    assert not (tmp_path / AI_USAGE_FILENAME).exists()
    assert ledger.reserved_usd("env_vlm") == 0.0


def test_file_ledger_corrupt_file_starts_from_zero_with_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    (tmp_path / AI_USAGE_FILENAME).write_bytes(b"{broken")
    with caplog.at_level(logging.WARNING):
        ledger = FileSpendLedger(tmp_path)
    assert ledger.spent_usd("env_vlm") == 0.0
    assert "bộ đếm chi phí" in caplog.text and str(tmp_path) not in caplog.text


def test_file_ledger_drops_invalid_entries(tmp_path: Path) -> None:
    payload = {
        "version": 1,
        "spent_usd": {"env_vlm": 0.25, "usr_0000000000c2": -1, "BAD ID": 1.0, "usr_0000000000c3": True},
    }
    (tmp_path / AI_USAGE_FILENAME).write_text(json.dumps(payload), encoding="utf-8")
    ledger = FileSpendLedger(tmp_path)
    assert ledger.spent_usd("env_vlm") == 0.25
    assert ledger.spent_usd("usr_0000000000c2") == 0.0 and ledger.spent_usd("usr_0000000000c3") == 0.0


def test_file_ledger_write_failure_keeps_ram_count(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ledger = FileSpendLedger(tmp_path)

    def broken_replace(src: object, dst: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", broken_replace)
    ledger.settle("env_vlm", 0.0, 0.4)
    assert ledger.spent_usd("env_vlm") == pytest.approx(0.4)
    assert list(tmp_path.iterdir()) == []


def test_provider_selection_dependency_persists_spending_with_data_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in [n for n in os.environ if n.startswith(("HOLOCRON_LLM_", "HOLOCRON_VLM_"))]:
        monkeypatch.delenv(name)
    deps._provider_selection_for.cache_clear()
    try:
        monkeypatch.delenv("HOLOCRON_DATA_DIR", raising=False)
        assert isinstance(deps.get_provider_selection()._ledger, InMemorySpendLedger)
        assert not isinstance(deps.get_provider_selection()._ledger, FileSpendLedger)
        monkeypatch.setenv("HOLOCRON_DATA_DIR", str(tmp_path))
        selection = deps.get_provider_selection()
        assert isinstance(selection._ledger, FileSpendLedger)
        assert selection._ledger.path == tmp_path / AI_USAGE_FILENAME
        assert deps.get_provider_selection() is selection
    finally:
        deps._provider_selection_for.cache_clear()


# --------------------------------------------------------------------------- #
# Kết nối có chặn địa chỉ: ghim IP, SNI, chứng chỉ, handler
# --------------------------------------------------------------------------- #


class SniRecorder(ssl.SSLContext):
    """Context TLS giả: ghi lại tên máy SNI rồi dừng (không bắt tay thật)."""

    server_names: list[str | bytes | None]

    def wrap_socket(
        self,
        sock: socket.socket,
        server_side: bool = False,
        do_handshake_on_connect: bool = True,
        suppress_ragged_eofs: bool = True,
        server_hostname: str | bytes | None = None,
        session: ssl.SSLSession | None = None,
    ) -> ssl.SSLSocket:
        self.server_names.append(server_hostname)
        sock.close()
        raise ssl.SSLError("giả: dừng trước khi bắt tay")


def _recording_guard(policy: IpPolicy, addresses: list[str], connected: list[tuple[str, int]]) -> AddressGuard:
    def connector(address: tuple[str, int], timeout: float | None) -> socket.socket:
        connected.append(address)
        return socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    return AddressGuard(policy, resolver=_resolver(addresses), connector=connector)


def test_https_connection_pins_checked_ip_and_keeps_host_name_for_sni() -> None:
    connected: list[tuple[str, int]] = []
    tls = SniRecorder(ssl.PROTOCOL_TLS_CLIENT)
    tls.server_names = []
    guard = _recording_guard("public_only", [PUBLIC_IP], connected)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), GuardedHTTPSHandler(guard, tls))
    with pytest.raises(urllib.error.URLError) as caught:
        opener.open(urllib.request.Request("https://api.example.com:8443/v1/models"), timeout=1.0)
    assert isinstance(caught.value.reason, ssl.SSLError)
    assert connected == [(PUBLIC_IP, 8443)]
    assert tls.server_names == ["api.example.com"]


def test_https_handler_default_context_verifies_certificate_and_host_name() -> None:
    handler = GuardedHTTPSHandler(AddressGuard("public_only"))
    assert handler.tls.verify_mode == ssl.CERT_REQUIRED and handler.tls.check_hostname is True


def test_http_handler_opener_connects_to_checked_loopback_ip() -> None:
    connected: list[tuple[str, int]] = []

    def connector(address: tuple[str, int], timeout: float | None) -> socket.socket:
        connected.append(address)
        raise ConnectionRefusedError("giả: không nối thật")

    guard = AddressGuard("public_only", resolver=_resolver(["127.0.0.1"]), connector=connector)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), GuardedHTTPHandler(guard))
    with pytest.raises(urllib.error.URLError):
        opener.open(urllib.request.Request("http://localhost:11434/v1/models"), timeout=1.0)
    assert connected == [("127.0.0.1", 11434)]


def test_guarded_transport_applies_http_limits() -> None:
    limits = LlmHttpLimits(max_response_bytes=4096)
    transport = guarded_transport("public_only", limits)
    assert isinstance(transport, UrllibTransport)
    handlers = transport.opener.handlers
    assert any(isinstance(item, GuardedHTTPSHandler) for item in handlers)
    assert any(isinstance(item, GuardedHTTPHandler) for item in handlers)
    assert transport._limits == limits


# --------------------------------------------------------------------------- #
# Bộ đọc VLM dùng transport có chặn địa chỉ theo ip_policy (MAJOR review)
# --------------------------------------------------------------------------- #


def _selected(policy: IpPolicy, base_url: str = "https://user-provider.example/v1") -> SelectedProvider:
    return SelectedProvider(
        provider_id="usr_0000000000d1",
        base_url=base_url,
        api_key=SecretStr(SECRET_KEY),
        model="vision",
        local=False,
        ip_policy=policy,
        reservation_id="r1",
    )


VLM = VlmSettings(base_url="https://vision.example.test/v1", api_key=SecretStr(SECRET_KEY), model="vision")


@pytest.mark.parametrize("policy", ["public_only", "operator"])
def test_vision_client_factory_builds_guarded_transport_for_the_provider_policy(
    policy: IpPolicy, monkeypatch: pytest.MonkeyPatch
) -> None:
    built: list[tuple[str, LlmHttpLimits]] = []

    def fake_guarded(ip_policy: IpPolicy, limits: LlmHttpLimits) -> UrllibTransport:
        built.append((ip_policy, limits))
        return UrllibTransport()

    monkeypatch.setattr(vision_reader, "guarded_transport", fake_guarded)
    vision_reader.openai_client_factory(VLM, 1024)(_selected(policy))
    assert built == [(policy, VLM.http_limits)]


def test_vision_client_never_connects_to_private_address_of_user_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    connected: list[tuple[str, int]] = []

    def connector(address: tuple[str, int], timeout: float | None) -> socket.socket:
        connected.append(address)
        raise ConnectionRefusedError("giả: không nối thật")

    def rebinding_guard(policy: IpPolicy) -> AddressGuard:
        return AddressGuard(policy, resolver=_resolver(["10.0.0.7"]), connector=connector)

    monkeypatch.setattr(provider_probe, "AddressGuard", rebinding_guard)
    client = vision_reader.openai_client_factory(VLM, 1024, sleep=lambda _: None)(_selected("public_only"))
    with pytest.raises(LlmUpstreamError) as caught:
        client.complete([ChatMessage(role="user", content=[TextPart(text="ping")])], max_tokens=1)
    assert connected == []
    assert SECRET_KEY not in str(caught.value) and "user-provider.example" not in str(caught.value)


# --------------------------------------------------------------------------- #
# Kiểm thử nhà cung cấp: mã HTTP, danh sách model, usage
# --------------------------------------------------------------------------- #


def _probe(status: int, body: bytes) -> ProviderProbe:
    def transport(request: urllib.request.Request, timeout_s: float) -> tuple[int, bytes]:
        return status, body

    return ProviderProbe(lambda policy: transport, monotonic=lambda: 0.0)


CHAT_LLM = ProviderTestRequest(action="chat", role="llm")
LIST_MODELS = ProviderTestRequest(action="list_models")


@pytest.mark.parametrize("status", [300, 301, 399])
def test_redirect_statuses_are_blocked(status: int) -> None:
    result = _probe(status, b"").run(_selected("public_only"), CHAT_LLM).result
    assert result.code == "redirect_blocked" and result.ok is False and result.http_status == status


def test_list_models_drops_empty_names_and_caps_the_count() -> None:
    names = [""] + [f"model-{index}" for index in range(MAX_TEST_MODELS + 5)]
    body = json.dumps({"data": [{"id": name} for name in names]}).encode("utf-8")
    result = _probe(200, body).run(_selected("public_only"), LIST_MODELS).result
    assert result.ok and len(result.models) == MAX_TEST_MODELS and "" not in result.models
    assert result.models[0] == "model-0"


def _conservative(request: ProviderTestRequest) -> AiUsage:
    estimate = probe_estimate(request)
    return AiUsage(
        input_tokens=estimate.input_tokens, output_tokens=estimate.max_output_tokens, images=0, estimated=True
    )


@pytest.mark.parametrize(
    "usage",
    [None, {"prompt_tokens": -5, "completion_tokens": 3}, {"prompt_tokens": 4, "completion_tokens": -1}],
)
def test_missing_or_negative_usage_is_counted_conservatively(usage: dict[str, int] | None) -> None:
    payload: dict[str, Any] = {"choices": [{"message": {"content": "ok"}}]}
    if usage is not None:
        payload["usage"] = usage
    outcome = _probe(200, json.dumps(payload).encode("utf-8")).run(_selected("public_only"), CHAT_LLM)
    assert outcome.result.ok and outcome.usage == _conservative(CHAT_LLM)


def test_probe_estimate_rounds_input_tokens_up(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(provider_probe, "PROBE_PROMPT", "abc")
    assert probe_estimate(CHAT_LLM).input_tokens == 2


def test_missing_usage_increases_spending_by_the_estimate() -> None:
    selection = ProviderSelection(lambda: [_candidate("usr_0000000000e1")], InMemorySpendLedger())
    selected = selection.reserve("usr_0000000000e1", "text", probe_estimate(CHAT_LLM))
    assert selected is not None
    body = json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode("utf-8")
    outcome = _probe(200, body).run(selected, CHAT_LLM)
    assert outcome.usage is not None
    selection.record(selected.reservation_id, outcome.usage)
    expected = estimate_cost_usd(TEMPLATE_DEFAULT_PRICES["custom"], probe_estimate(CHAT_LLM))
    assert expected > 0 and selection.spent_usd("usr_0000000000e1") == pytest.approx(expected)
