"""`routers/api.py` sau khi tách: giữ thứ tự route, tên re-export và hàm DI là đúng đối tượng các route dùng."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from holocron_core.domain.ingestion import GateStatus, IngestionItem
from holocron_core.domain.schemas import QuestionTemplate
from holocron_core.domain.twins import IdempotencyRecord, TwinBatch
from holocron_core.infrastructure import memory_store
from holocron_core.routers import api, blueprints, deps, ingestion, runtime
from holocron_core.services.ingestion_gate import recompute_item
from tests.test_api_item_state import _body

EXPECTED_PATHS = [
    ("/v1/runtime", ["get"]),
    ("/v1/ingestion/upload", ["post"]),
    ("/v1/ingestion/items", ["post"]),
    ("/v1/ingestion/items/{item_id}", ["get"]),
    ("/v1/ingestion/items/{item_id}/table", ["patch"]),
    ("/v1/ingestion/items/{item_id}/stem", ["patch"]),
    ("/v1/ingestion/items/{item_id}/non-cloneable", ["post"]),
    ("/v1/ingestion/items/{item_id}/approve", ["post"]),
    ("/v1/blueprints/{blueprint_id}/twins", ["post"]),
]


class EmptyRegistry:
    def get_verified(self, topic_key: str) -> QuestionTemplate | None:
        return None


def test_openapi_paths_and_order_are_unchanged() -> None:
    """Mọi path và method của `EXPECTED_PATHS` vẫn còn (tập con); router mới được thêm path khác."""
    spec = api.app.openapi()
    for path, methods in EXPECTED_PATHS:
        assert path in spec["paths"], path
        assert set(methods) <= set(spec["paths"][path]), path
    assert spec["info"] == {"title": "Holocron Ingestion Gate", "version": "0.2.0a0"}


@pytest.mark.parametrize(
    "name",
    ["get_store", "get_extractor", "get_runtime_config", "get_template_author", "get_citation_provider", "get_clock"],
)
def test_api_reexports_the_same_dependency_functions(name: str) -> None:
    """`app.dependency_overrides[api.get_xxx]` chỉ có tác dụng khi đó đúng là hàm các route dùng."""
    assert getattr(api, name) is getattr(deps, name)


def test_api_reexports_moved_names() -> None:
    assert api.IngestionStore is memory_store.IngestionStore
    assert api.IDEMPOTENCY_TTL is memory_store.IDEMPOTENCY_TTL
    assert api.REGISTRY is deps.REGISTRY
    assert api.MAX_UPLOAD_BYTES == ingestion.MAX_UPLOAD_BYTES == 10 * 1024 * 1024
    assert api.RuntimeInfo is runtime.RuntimeInfo
    assert api.create_twins is blueprints.create_twins
    assert api._recompute is ingestion._recompute


def test_override_through_api_reaches_the_route() -> None:
    fresh = api.IngestionStore()
    api.app.dependency_overrides[api.get_store] = lambda: fresh
    try:
        created = TestClient(api.app).post("/v1/ingestion/items", json=_body({"quantum": 4}))
    finally:
        api.app.dependency_overrides.clear()
    assert created.status_code == 201
    assert fresh.get_item(created.json()["item_id"]) is not None


def test_recompute_item_uses_the_injected_registry() -> None:
    """Logic Gate ở `services/` nhận registry qua tham số: registry rỗng ⇒ không tự giải được ⇒ BLOCKED (I5)."""
    blocked = recompute_item(IngestionItem(item_id="ing_a", **_body({"quantum": 4})), EmptyRegistry())
    assert blocked.report is not None and blocked.report.status is GateStatus.BLOCKED
    assert blocked.self_solve is None
    assert [i.code for i in blocked.report.issues] == ["SELF_SOLVE_MISMATCH"]

    passed = recompute_item(IngestionItem(item_id="ing_b", **_body({"quantum": 4})), deps.REGISTRY)
    via_router = api._recompute(IngestionItem(item_id="ing_c", **_body({"quantum": 4})))
    assert passed.report is not None and via_router.report is not None
    assert passed.report.status is via_router.report.status is GateStatus.AUTO_PASS
    assert passed.self_solve == via_router.self_solve


def test_memory_store_idempotency_expires_after_ttl() -> None:
    store = memory_store.IngestionStore()
    stored_at = datetime(2026, 1, 1, tzinfo=UTC)
    record = IdempotencyRecord(
        body_digest="d",
        stored_at=stored_at,
        batch=TwinBatch(blueprint_id="bp", base_seed=1, twins=[], review_tickets=[]),
    )
    store.save_idempotent("bp", "key", record)
    assert store.get_idempotent("bp", "key", stored_at + memory_store.IDEMPOTENCY_TTL) == record
    assert store.get_idempotent("bp", "key", stored_at + memory_store.IDEMPOTENCY_TTL + timedelta(seconds=1)) is None
    assert store.get_idempotent("bp", "other", stored_at) is None
