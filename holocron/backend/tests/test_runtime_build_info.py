"""`GET /v1/runtime` báo version, commit và thư mục log (SPECIFICATION §6.0; không lộ khoá, model, endpoint)."""

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from holocron_core.launcher_logging import LOG_DIR_ENV, BuildInfo, fallback_build_info, read_build_info
from holocron_core.routers import deps
from holocron_core.routers.api import app


@pytest.fixture
def client() -> Iterator[TestClient]:
    yield TestClient(app)
    app.dependency_overrides.clear()


def _use_build_info(path: Path) -> None:
    info = read_build_info(path)
    app.dependency_overrides[deps.get_build_info] = lambda: info


def _write(path: Path, payload: object) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_valid_build_info_reported(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(LOG_DIR_ENV, raising=False)
    _use_build_info(
        _write(
            tmp_path / "b.json", {"version": "1.2.3", "commit": "abcdef0123456789", "dirty": False, "built_at": "now"}
        )
    )
    body = client.get("/v1/runtime").json()
    assert body["app_version"] == "1.2.3"
    assert body["commit"] == "abcdef0123456789"
    assert body["log_dir"] is None


@pytest.mark.parametrize("content", [None, "{khong phai json", '{"version": 1}'])
def test_missing_or_corrupt_build_info_falls_back(client: TestClient, tmp_path: Path, content: str | None) -> None:
    path = tmp_path / "b.json"
    if content is not None:
        path.write_text(content, encoding="utf-8")
    _use_build_info(path)
    body = client.get("/v1/runtime").json()
    expected: BuildInfo = fallback_build_info()
    assert body["app_version"] == expected.version
    assert body["commit"] == "unknown"


def test_log_dir_from_launcher_env(client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(LOG_DIR_ENV, str(tmp_path))
    assert client.get("/v1/runtime").json()["log_dir"] == str(tmp_path)
    monkeypatch.setenv(LOG_DIR_ENV, "  ")
    assert client.get("/v1/runtime").json()["log_dir"] is None


def test_default_build_info_is_cached() -> None:
    assert deps.get_build_info() is deps.get_build_info()


def test_get_log_dir_strips_control_chars_and_keeps_long_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    long_path = "/" + "d" * 100 + "/logs"
    monkeypatch.setenv("HOLOCRON_LOG_DIR", f"\x1b[31m{long_path}\n\x00 ")
    assert deps.get_log_dir() == long_path
    assert len(long_path) > 64


def test_existing_fields_unchanged_and_no_secrets(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOLOCRON_LLM_API_KEY", "sk-secret-0123456789")
    body = client.get("/v1/runtime").json()
    assert set(body) == {"demo_mode", "llm_configured", "vlm_configured", "app_version", "commit", "log_dir"}
    assert isinstance(body["demo_mode"], bool)
    assert "sk-secret" not in json.dumps(body)
