"""Cache AI (ADR-009 mục 8; spec §5.16): khoá theo ảnh + model + PROMPT_VERSION, gắn tài liệu, trần, xoá.

Chạy trên CẢ kho in-memory và SQLite (có/không FTS5) với cùng ngữ nghĩa.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from holocron_core.domain.ai import AiCacheEntry, ai_cache_key
from holocron_core.domain.repositories import AppStore
from holocron_core.infrastructure import vision_reader
from holocron_core.infrastructure.memory_store import IngestionStore
from holocron_core.infrastructure.storage.connection import connect
from holocron_core.infrastructure.storage.sqlite_store import SQLiteStore
from holocron_core.infrastructure.vision_reader import VisionSkipped, VlmRegionResult
from holocron_core.routers import deps
from holocron_core.routers import settings as settings_router
from holocron_core.routers.access import LocalAccessMiddleware, get_allowed_hosts, get_required_token
from tests.vault_support import STORE_KINDS, add_document, open_store
from tests.vision_support import MODEL, NOW, Harness, Switch, candidate, region_image

DOC_A = "doc_aaaaaaaaaaaaaaaa"
DOC_B = "doc_bbbbbbbbbbbbbbbb"
REPLY = json.dumps([{"type": "paragraph", "text": "Lệnh MOV R1, #5"}], ensure_ascii=False)


@pytest.fixture(params=STORE_KINDS)
def store(request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[AppStore]:
    yield from open_store(str(request.param), tmp_path, monkeypatch)


def _entry(seed: str, *, model: str = MODEL) -> AiCacheEntry:
    sha = (seed * 64)[:64]
    return AiCacheEntry(
        cache_key=ai_cache_key("vlm_region", sha, model, "v1"),
        kind="vlm_region",
        input_sha256=sha,
        model=model,
        prompt_version="v1",
        result_json='{"blocks":[]}',
    )


def _ok(outcome: VlmRegionResult | VisionSkipped) -> VlmRegionResult:
    assert isinstance(outcome, VlmRegionResult) and outcome.status == "ok"
    return outcome


# --------------------------------------------------------------------------- #
# Bộ đọc: trúng/trượt cache
# --------------------------------------------------------------------------- #


def test_cache_hit_does_not_call_transport_and_gives_identical_blocks(store: AppStore) -> None:
    harness = Harness([REPLY], cache=store)
    image = region_image()
    first = _ok(harness.reader.read_region(DOC_A, 1, image))
    second = _ok(harness.reader.read_region(DOC_A, 1, image))
    assert len(harness.client.received) == 1
    assert second.from_cache and second.attempts == 0 and second.blocks == first.blocks
    assert harness.ledger.reserved_usd("env_vlm") == 0.0


def test_changing_model_or_prompt_version_misses(store: AppStore, monkeypatch: pytest.MonkeyPatch) -> None:
    image = region_image()
    _ok(Harness([REPLY], cache=store).reader.read_region(DOC_A, 1, image))
    other_model = Harness([REPLY], cache=store, providers=[candidate(model="vision-model-y")])
    assert not _ok(other_model.reader.read_region(DOC_A, 1, image)).from_cache
    assert len(other_model.client.received) == 1
    monkeypatch.setattr(vision_reader, "PROMPT_VERSION", "v1-test")
    bumped = Harness([REPLY], cache=store)
    assert not _ok(bumped.reader.read_region(DOC_A, 1, image)).from_cache
    assert len(bumped.client.received) == 1


def test_consent_off_does_not_touch_the_cache(store: AppStore) -> None:
    image = region_image()
    _ok(Harness([REPLY], cache=store).reader.read_region(DOC_A, 1, image))
    off = Harness([], cache=store, consent=Switch(image=False))
    assert off.reader.read_region(DOC_B, 1, image) == VisionSkipped(reason="consent_off")
    add_document(store, DOC_A)
    store.delete_document(DOC_A)
    # Không tra cache ⇒ không gắn DOC_B: dòng bị dọn cùng DOC_A.
    assert store.clear_ai_cache() == 0


def test_invalid_cached_json_is_dropped_and_refetched(store: AppStore) -> None:
    image = region_image()
    key = ai_cache_key("vlm_region", image.sha256, MODEL, "v1")
    broken = AiCacheEntry(
        cache_key=key,
        kind="vlm_region",
        input_sha256=image.sha256,
        model=MODEL,
        prompt_version="v1",
        result_json='{"blocks":[{"type":"bogus"}]}',
    )
    store.put_ai_cache(broken, DOC_A, now=NOW)
    harness = Harness([REPLY], cache=store)
    assert not _ok(harness.reader.read_region(DOC_A, 1, image)).from_cache
    assert len(harness.client.received) == 1
    assert _ok(Harness([], cache=store).reader.read_region(DOC_A, 1, image)).from_cache


def test_only_ok_results_are_cached(store: AppStore) -> None:
    image = region_image()
    Harness(["không phải JSON", "vẫn không"], cache=store).reader.read_region(DOC_A, 1, image)
    assert store.get_ai_cache(ai_cache_key("vlm_region", image.sha256, MODEL, "v1"), DOC_A, now=NOW) is None


# --------------------------------------------------------------------------- #
# Kho: liên kết tài liệu, xoá, trần
# --------------------------------------------------------------------------- #


def test_delete_document_removes_links_and_orphans_in_both_stores(store: AppStore) -> None:
    add_document(store, DOC_A)
    add_document(store, DOC_B)
    shared, only_a = _entry("a"), _entry("b")
    store.put_ai_cache(shared, DOC_A, now=NOW)
    store.put_ai_cache(only_a, DOC_A, now=NOW)
    assert store.get_ai_cache(shared.cache_key, DOC_B, now=NOW) == shared  # trúng ⇒ gắn thêm DOC_B
    assert store.delete_document(DOC_A) is True
    assert store.get_ai_cache(only_a.cache_key, DOC_A, now=NOW) is None
    assert store.get_ai_cache(shared.cache_key, DOC_B, now=NOW) == shared
    assert store.delete_document(DOC_B) is True
    assert store.get_ai_cache(shared.cache_key, DOC_B, now=NOW) is None
    assert store.clear_ai_cache() == 0


def test_clear_ai_cache_returns_deleted_rows(store: AppStore) -> None:
    for seed in "abc":
        store.put_ai_cache(_entry(seed), DOC_A, now=NOW)
    assert store.clear_ai_cache() == 3
    assert store.get_ai_cache(_entry("a").cache_key, DOC_A, now=NOW) is None
    assert store.clear_ai_cache() == 0


@pytest.mark.parametrize("kind", ["memory", "sqlite"])
def test_row_cap_evicts_least_recently_used(kind: str, tmp_path: Path) -> None:
    capped: AppStore = (
        IngestionStore(ai_cache_max_rows=2)
        if kind == "memory"
        else SQLiteStore(_connect(tmp_path), ai_cache_max_rows=2)
    )
    a, b, c = _entry("a"), _entry("b"), _entry("c")
    capped.put_ai_cache(a, DOC_A, now=NOW)
    capped.put_ai_cache(b, DOC_A, now=NOW + timedelta(seconds=1))
    assert capped.get_ai_cache(a.cache_key, DOC_A, now=NOW + timedelta(seconds=2)) == a  # a dùng gần hơn b
    capped.put_ai_cache(c, DOC_A, now=NOW + timedelta(seconds=3))
    later = NOW + timedelta(seconds=4)
    assert capped.get_ai_cache(b.cache_key, DOC_A, now=later) is None
    assert capped.get_ai_cache(a.cache_key, DOC_A, now=later) == a
    assert capped.get_ai_cache(c.cache_key, DOC_A, now=later) == c


@pytest.mark.parametrize("kind", ["memory", "sqlite"])
def test_ttl_expires_rows(kind: str, tmp_path: Path) -> None:
    ttl: AppStore = (
        IngestionStore(ai_cache_ttl_days=1)
        if kind == "memory"
        else SQLiteStore(_connect(tmp_path), ai_cache_ttl_days=1)
    )
    old, fresh = _entry("a"), _entry("b")
    ttl.put_ai_cache(old, DOC_A, now=NOW)
    assert ttl.get_ai_cache(old.cache_key, DOC_A, now=NOW + timedelta(days=2)) is None
    ttl.put_ai_cache(fresh, DOC_A, now=NOW + timedelta(days=2))
    assert ttl.clear_ai_cache() == 1  # dòng quá TTL bị xoá khi ghi


def _connect(tmp_path: Path) -> sqlite3.Connection:
    return connect(tmp_path / "capped.sqlite3")


# --------------------------------------------------------------------------- #
# DELETE /v1/settings/ai-cache (spec §6.11; ADR-009 mục 12.7)
# --------------------------------------------------------------------------- #

TOKEN = "t" * 43


def _settings_app(store: AppStore, token: str | None) -> TestClient:
    app = FastAPI()
    app.add_middleware(LocalAccessMiddleware, overrides=lambda: app.dependency_overrides)
    app.include_router(settings_router.router)
    app.dependency_overrides[deps.get_store] = lambda: store
    app.dependency_overrides[get_required_token] = lambda: token
    app.dependency_overrides[get_allowed_hosts] = lambda: frozenset({"testserver"})
    return TestClient(app)


def test_settings_write_forbidden_without_token(store: AppStore) -> None:
    store.put_ai_cache(_entry("a"), DOC_A, now=NOW)
    response = _settings_app(store, None).delete("/v1/settings/ai-cache")
    assert response.status_code == 403
    assert response.json() == {
        "detail": settings_router.TOKEN_NOT_CONFIGURED_DETAIL,
        "code": "token_not_configured",
    }
    client = _settings_app(store, TOKEN)
    assert client.delete("/v1/settings/ai-cache").status_code == 403
    assert client.delete("/v1/settings/ai-cache", headers={"X-Holocron-Token": "sai"}).status_code == 403
    assert store.get_ai_cache(_entry("a").cache_key, DOC_A, now=NOW) is not None


def test_delete_ai_cache_with_token_clears_everything(store: AppStore) -> None:
    for seed in "ab":
        store.put_ai_cache(_entry(seed), DOC_A, now=NOW)
    client = _settings_app(store, TOKEN)
    response = client.delete("/v1/settings/ai-cache", headers={"X-Holocron-Token": TOKEN})
    assert response.status_code == 200 and response.json() == {"deleted_entries": 2}
    again = client.delete("/v1/settings/ai-cache", headers={"X-Holocron-Token": TOKEN})
    assert again.json() == {"deleted_entries": 0}


# --------------------------------------------------------------------------- #
# Review V02: cache hỏng hoặc vượt giới hạn bị bỏ; ngữ nghĩa TTL, trần, ghi đè
# --------------------------------------------------------------------------- #


def _stored(image_sha: str, result_json: str) -> AiCacheEntry:
    return AiCacheEntry(
        cache_key=ai_cache_key("vlm_region", image_sha, MODEL, "v1"),
        kind="vlm_region",
        input_sha256=image_sha,
        model=MODEL,
        prompt_version="v1",
        result_json=result_json,
    )


def test_invalid_cache_row_is_dropped_even_when_the_refetch_fails(store: AppStore) -> None:
    from holocron_core.domain.llm import LlmUpstreamError

    image = region_image()
    broken = _stored(image.sha256, '{"blocks":[{"type":"bogus"}]}')
    store.put_ai_cache(broken, DOC_A, now=NOW)
    harness = Harness([LlmUpstreamError("HTTP 500", attempts=1)], cache=store)
    outcome = harness.reader.read_region(DOC_A, 1, image)
    assert isinstance(outcome, VlmRegionResult) and outcome.status == "upstream_error"
    assert store.get_ai_cache(broken.cache_key, DOC_A, now=NOW) is None


@pytest.mark.parametrize(
    "blocks",
    [
        [{"type": "paragraph", "text": "x", "rows": None, "order": k} for k in range(3)],
        [{"type": "paragraph", "text": "x" * 6, "rows": None, "order": 0}],
        [{"type": "table", "text": None, "rows": [["a"], ["b"], ["c"]], "order": 0}],
        [{"type": "table", "text": None, "rows": [["a", "b", "c"]], "order": 0}],
        [{"type": "table", "text": None, "rows": [["abcdef"]], "order": 0}],
        [{"type": "table", "text": None, "rows": [], "order": 0}],
        [{"type": "paragraph", "text": "x", "rows": [["a"]], "order": 0}],
    ],
    ids=["blocks", "text", "rows", "cols", "cell", "empty-table", "shape"],
)
def test_cached_blocks_over_task_limits_are_dropped_and_refetched(
    store: AppStore, blocks: list[dict[str, object]]
) -> None:
    from holocron_core.infrastructure.vision_reader import VisionReaderSettings, VlmTaskLimits

    limits = VlmTaskLimits(max_blocks_per_region=2, max_table_rows=2, max_table_cols=2, max_text_chars=5)
    image = region_image()
    entry = _stored(image.sha256, json.dumps({"blocks": blocks}))
    store.put_ai_cache(entry, DOC_A, now=NOW)
    reply = json.dumps([{"type": "paragraph", "text": "abc"}])
    harness = Harness([reply], cache=store, settings=VisionReaderSettings(limits=limits))
    result = _ok(harness.reader.read_region(DOC_A, 1, image))
    assert not result.from_cache and len(harness.client.received) == 1


def test_cached_blocks_exactly_at_limits_are_served(store: AppStore) -> None:
    from holocron_core.infrastructure.vision_reader import VisionReaderSettings, VlmTaskLimits

    limits = VlmTaskLimits(max_blocks_per_region=2, max_table_rows=2, max_table_cols=2, max_text_chars=5)
    blocks = [
        {"type": "table", "text": None, "rows": [["abcde", "b"], ["c", "d"]], "order": 0},
        {"type": "paragraph", "text": "abcde", "rows": None, "order": 1},
    ]
    image = region_image()
    store.put_ai_cache(_stored(image.sha256, json.dumps({"blocks": blocks})), DOC_A, now=NOW)
    harness = Harness([], cache=store, settings=VisionReaderSettings(limits=limits))
    assert _ok(harness.reader.read_region(DOC_A, 1, image)).from_cache


def test_ttl_boundary_and_overwrite_semantics(store: AppStore) -> None:
    from holocron_core.infrastructure.storage.sqlite_store import AI_CACHE_TTL_DAYS

    first = _entry("a")
    store.put_ai_cache(first, DOC_A, now=NOW)
    edge = NOW + timedelta(days=AI_CACHE_TTL_DAYS)
    assert store.get_ai_cache(first.cache_key, DOC_A, now=edge) == first  # đúng bằng TTL: còn dùng được
    assert store.get_ai_cache(first.cache_key, DOC_A, now=edge + timedelta(days=AI_CACHE_TTL_DAYS, seconds=1)) is None
    replaced = first.model_copy(update={"result_json": '{"blocks":[]} '})
    store.put_ai_cache(replaced, DOC_A, now=NOW)
    assert store.get_ai_cache(first.cache_key, DOC_A, now=NOW) == replaced


@pytest.mark.parametrize("kind", ["memory", "sqlite"])
def test_overwrite_refreshes_last_used_so_ttl_keeps_new_value(kind: str, tmp_path: Path) -> None:
    ttl: AppStore = (
        IngestionStore(ai_cache_ttl_days=1)
        if kind == "memory"
        else SQLiteStore(_connect(tmp_path), ai_cache_ttl_days=1)
    )
    old = _entry("a")
    ttl.put_ai_cache(old, DOC_A, now=NOW)
    later = NOW + timedelta(days=2)
    newer = old.model_copy(update={"result_json": '{"blocks":[]} '})
    ttl.put_ai_cache(newer, DOC_A, now=later)
    assert ttl.get_ai_cache(old.cache_key, DOC_A, now=later) == newer


@pytest.mark.parametrize("kind", ["memory", "sqlite"])
@pytest.mark.parametrize("fields", [{"ai_cache_max_rows": 0}, {"ai_cache_ttl_days": 0}])
def test_cache_limits_must_be_positive(kind: str, fields: dict[str, int], tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="ai_cache_max_rows"):
        if kind == "memory":
            IngestionStore(**fields)
        else:
            SQLiteStore(_connect(tmp_path), **fields)
