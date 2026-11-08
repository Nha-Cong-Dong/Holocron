"""Chặn SSRF khi kiểm thử nhà cung cấp (ADR-009 mục 12.8): kiểm mọi địa chỉ phân giải, ghim IP, không gọi mạng.

Bộ phân giải và hàm nối là giả; kiểm rằng kết nối đi tới đúng địa chỉ đã kiểm, còn `Host` của request vẫn là tên máy.
"""

from __future__ import annotations

import ipaddress
import socket
import urllib.error
import urllib.request

import pytest

from holocron_core.domain.ai import IpPolicy
from holocron_core.infrastructure.provider_probe import (
    AddressGuard,
    BlockedAddressError,
    GuardedHTTPConnection,
    GuardedHTTPHandler,
    GuardedHTTPSHandler,
    Resolver,
    guarded_transport,
    probe_png,
)
from holocron_core.infrastructure.provider_store import address_is_public, normalize_base_url


def _resolver(addresses: list[str]) -> Resolver:
    def resolve(host: str, port: int) -> list[str]:
        return addresses

    return resolve


def _guard(policy: IpPolicy, addresses: list[str], connected: list[tuple[str, int]] | None = None) -> AddressGuard:
    def connector(address: tuple[str, int], timeout: float | None) -> socket.socket:
        if connected is not None:
            connected.append(address)
        raise ConnectionRefusedError("giả: không nối thật")

    return AddressGuard(policy, resolver=_resolver(addresses), connector=connector)


BLOCKED = [
    "10.1.2.3",
    "172.16.0.1",
    "192.168.0.1",
    "100.64.0.1",
    "169.254.169.254",
    "127.0.0.1",
    "0.0.0.0",
    "224.0.0.1",
    "240.0.0.1",
    "fc00::1",
    "fe80::1",
    "::1",
    "::",
    "ff02::1",
    "::ffff:10.0.0.1",
    "::ffff:169.254.169.254",
]


@pytest.mark.parametrize("address", BLOCKED)
def test_public_only_blocks_private_and_special_ranges(address: str) -> None:
    with pytest.raises(BlockedAddressError) as caught:
        _guard("public_only", [address]).pick("api.example.com", 443)
    assert address not in str(caught.value) and "api.example.com" not in str(caught.value)


def test_public_only_blocks_when_any_resolved_address_is_private() -> None:
    with pytest.raises(BlockedAddressError):
        _guard("public_only", ["8.8.8.8", "10.0.0.1"]).pick("api.example.com", 443)


def test_public_only_allows_public_and_pins_first_checked_address() -> None:
    connected: list[tuple[str, int]] = []
    guard = _guard("public_only", ["8.8.8.8", "2001:4860:4860::8888"], connected)
    assert guard.pick("api.example.com", 443) == "8.8.8.8"
    with pytest.raises(ConnectionRefusedError):
        guard.open("api.example.com", 443, 1.0)
    assert connected == [("8.8.8.8", 443)]


def test_loopback_only_for_loopback_host_names() -> None:
    assert _guard("public_only", ["127.0.0.1"]).pick("localhost", 8080) == "127.0.0.1"
    with pytest.raises(BlockedAddressError):
        _guard("public_only", ["127.0.0.1"]).pick("rebind.example.com", 443)
    with pytest.raises(BlockedAddressError):
        _guard("public_only", ["8.8.8.8"]).pick("localhost", 8080)


def test_operator_policy_allows_private_gateway() -> None:
    assert _guard("operator", ["10.0.0.5"]).pick("gateway.internal", 443) == "10.0.0.5"


def test_resolution_failure_is_blocked() -> None:
    def fail(host: str, port: int) -> list[str]:
        raise socket.gaierror("no such host")

    with pytest.raises(BlockedAddressError):
        AddressGuard("public_only", resolver=fail).pick("nope.example", 443)


def test_connection_connects_to_checked_ip_not_host_name() -> None:
    connected: list[tuple[str, int]] = []

    class Connection(GuardedHTTPConnection):
        pass

    Connection.guard = _guard("public_only", ["127.0.0.1"], connected)
    connection = Connection("localhost:9000", timeout=1.0)
    with pytest.raises(ConnectionRefusedError):
        connection.connect()
    assert connected == [("127.0.0.1", 9000)]


def test_guarded_transport_reports_blocked_address_before_sending() -> None:
    guard = _guard("public_only", ["10.0.0.1"])
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), GuardedHTTPHandler(guard), GuardedHTTPSHandler(guard)
    )
    with pytest.raises(urllib.error.URLError) as caught:
        opener.open(urllib.request.Request("https://api.example.com/v1/models"), timeout=1.0)
    assert isinstance(caught.value.reason, BlockedAddressError)


def test_guarded_transport_is_a_callable_transport() -> None:
    assert callable(guarded_transport("public_only"))


def test_address_is_public_examples() -> None:
    assert address_is_public(ipaddress.ip_address("8.8.8.8"))
    assert not address_is_public(ipaddress.ip_address("::ffff:192.168.1.1"))


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [
        ("HTTPS://API.Example.COM:443/v1/", "https://api.example.com/v1"),
        ("https://api.example.com:8443/v1", "https://api.example.com:8443/v1"),
        ("http://LOCALHOST:80/v1", "http://localhost/v1"),
        ("http://[::1]:11434/v1", "http://[::1]:11434/v1"),
    ],
)
def test_normalize_base_url(raw: str, normalized: str) -> None:
    assert normalize_base_url(raw) == normalized


def test_probe_png_is_8x8_png() -> None:
    data = probe_png()
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    assert int.from_bytes(data[16:20], "big") == 8 and int.from_bytes(data[20:24], "big") == 8
