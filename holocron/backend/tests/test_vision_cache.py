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

from holocron_core.domain.ai import AiCacheEntry, ai_cache_key
from holocron_core.domain.repositories import AppStore
from holocron_core.infrastructure import vision_reader
from holocron_core.infrastructure.memory_store import IngestionStore
from holocron_core.infrastructure.storage.connection import connect
from holocron_core.infrastructure.storage.sqlite_store import SQLiteStore
from holocron_core.infrastructure.vision_reader import VisionSkipped, VlmRegionResult
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
