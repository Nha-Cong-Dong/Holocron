"""Các luồng API chính chạy trên kho SQLite (`tmp_path`), cấp qua `app.dependency_overrides[get_store]`.

Luồng upload → sửa bảng → approve → sinh twin giữ đúng hành vi của bộ test in-memory, và dữ liệu còn nguyên khi
mở lại file (như khởi động lại backend). Optimistic locking của §6.2 trả 409 khi ghi với version cũ.
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from holocron_core.domain.schemas import (
    BlueprintSlot,
    Citation,
    ExamBlueprint,
    KnowledgeChunk,
    QuestionTemplate,
    TemplateProvenance,
)
from holocron_core.infrastructure.storage import DATABASE_FILENAME, SQLiteStore
from holocron_core.infrastructure.template_library import rr_avg_waiting_template
from holocron_core.routers.api import app, get_citation_provider, get_clock, get_store, get_template_author
from holocron_core.services import twin_batch
from tests.test_api_item_state import _body

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
RR_TOPIC = "os.cpu_sched.rr.avg_waiting_time"
BASE_SEED = 20260419


class RrAuthor:
    def author(self, slot: BlueprintSlot, feedback: list[str]) -> QuestionTemplate:
        return rr_avg_waiting_template(TemplateProvenance.LLM_GENERATED)


class FixedCitations:
    def __init__(self, citation: Citation) -> None:
        self._citation = citation

    def citation_for(self, slot: BlueprintSlot) -> Citation | None:
        return self._citation


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 1, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / DATABASE_FILENAME


@pytest.fixture
def store(db_path: Path) -> Iterator[SQLiteStore]:
    opened = SQLiteStore.open(db_path)
    app.dependency_overrides[get_store] = lambda: opened
    yield opened
    app.dependency_overrides.clear()
    opened.close()


@pytest.fixture
def clock() -> Clock:
    fake = Clock()
    app.dependency_overrides[get_clock] = lambda: fake
    return fake


@pytest.fixture
def stubs(theory_chunk: KnowledgeChunk) -> None:
    app.dependency_overrides[get_template_author] = lambda: RrAuthor()
    app.dependency_overrides[get_citation_provider] = lambda: FixedCitations(theory_chunk.to_citation())


@pytest.fixture
def sandbox_calls(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    calls: list[int] = []
    real = twin_batch.produce_twin

    def counting(*args: Any, **kwargs: Any) -> Any:
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(twin_batch, "produce_twin", counting)
    return calls


def _upload(client: TestClient) -> dict[str, Any]:
    response = client.post(
        "/v1/ingestion/upload",
        files={"file": ("de_thi.png", PNG_BYTES, "image/png")},
        data={"topic_key": RR_TOPIC},
    )
    assert response.status_code == 201, response.text
    item: dict[str, Any] = response.json()["item"]
    return item


def _patch(client: TestClient, item_id: str, expected_version: int, text: str = "9") -> Any:
    return client.patch(
        f"/v1/ingestion/items/{item_id}/table",
        json={
            "editor_id": "u_tuan",
            "expected_version": expected_version,
            "patches": [{"row": 1, "col": 2, "new_text": text}],
        },
    )


def _blueprint(rr_slot: BlueprintSlot) -> ExamBlueprint:
    return ExamBlueprint(
        blueprint_id="bp_sqlite_1",
        source_exam_doc_id="doc_exam_1",
        subject_code="IT3070",
        total_questions=1,
        slots=[rr_slot.model_copy(update={"slot_index": 1})],
    )


def _approved_item(client: TestClient) -> str:
    item = _upload(client)
    assert item["report"]["status"] == "BLOCKED"
    fixed = _patch(client, item["item_id"], expected_version=1)
    assert fixed.status_code == 200 and fixed.json()["report"]["status"] == "AUTO_PASS"
    approved = client.post(f"/v1/ingestion/items/{item['item_id']}/approve")
    assert approved.status_code == 200 and approved.json()["approved"] is True
    item_id: str = item["item_id"]
    return item_id


def test_upload_patch_approve_twins_and_survive_a_restart(
    store: SQLiteStore, stubs: None, db_path: Path, rr_slot: BlueprintSlot
) -> None:
    client = TestClient(app)
    item_id = _approved_item(client)
    store.save_blueprint(_blueprint(rr_slot), item_ids=[item_id])
    twins = client.post("/v1/blueprints/bp_sqlite_1/twins", json={"count": 2, "base_seed": BASE_SEED})
    assert twins.status_code == 201, twins.text
    assert len(twins.json()["twins"]) == 2 and twins.json()["review_tickets"] == []

    before = client.get(f"/v1/ingestion/items/{item_id}").json()
    reopened = SQLiteStore.open(db_path)
    try:
        app.dependency_overrides[get_store] = lambda: reopened
        after = client.get(f"/v1/ingestion/items/{item_id}")
        assert after.status_code == 200 and after.json() == before
        assert after.json()["table_primary"]["version"] == 2
        assert [audit["changes"] for audit in after.json()["audits"]] == [
            [{"row": 1, "col": 2, "old": "8", "new": "9"}]
        ]
        assert reopened.blueprint_item_ids("bp_sqlite_1") == (item_id,)
    finally:
        reopened.close()


def test_stale_expected_version_is_409_and_does_not_overwrite(store: SQLiteStore) -> None:
    client = TestClient(app)
    item_id = _upload(client)["item_id"]
    assert _patch(client, item_id, expected_version=1, text="9").status_code == 200
    stale = _patch(client, item_id, expected_version=1, text="7")
    assert stale.status_code == 409
    assert stale.json()["detail"] == "version lệch: hiện tại 2"
    stored = client.get(f"/v1/ingestion/items/{item_id}").json()
    assert stored["table_primary"]["version"] == 2 and len(stored["audits"]) == 1


def test_write_racing_another_connection_is_409(
    store: SQLiteStore, db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Một kết nối khác (như tiến trình thứ hai) sửa bảng giữa lúc router đọc và ghi: router nhận 409, không ghi đè."""
    client = TestClient(app)
    uploaded = _upload(client)
    item_id = uploaded["item_id"]
    other = SQLiteStore.open(db_path)
    real_get_item = store.get_item

    def get_then_race(requested: str) -> Any:
        item = real_get_item(requested)
        assert item is not None
        rival = item.model_copy(deep=True)
        rival.table_primary = rival.table_primary.model_copy(update={"version": item.table_primary.version + 1})
        other.update_item(rival, expected_version=item.table_primary.version)
        return item

    monkeypatch.setattr(store, "get_item", get_then_race)
    try:
        response = client.patch(
            f"/v1/ingestion/items/{item_id}/stem",
            json={"editor_id": "u_tuan", "stem_latex": r"Round Robin với $q = 2$ ms. Tính $\overline{WT}$."},
        )
    finally:
        monkeypatch.undo()
        other.close()
    assert response.status_code == 409
    assert response.json()["detail"] == "version lệch: hiện tại 2"
    stored = store.get_item(item_id)
    assert stored is not None and stored.stem_latex == uploaded["stem_latex"] and stored.table_primary.version == 2


def test_item_deleted_between_read_and_write_is_404(store: SQLiteStore, monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(app)
    item_id = _upload(client)["item_id"]
    ghost = store.get_item(item_id)
    assert ghost is not None
    monkeypatch.setattr(store, "get_item", lambda requested: ghost.model_copy(update={"item_id": "ing_gone"}))
    response = client.post(
        "/v1/ingestion/items/ing_gone/non-cloneable", json={"editor_id": "u", "reason": "đề cần hình"}
    )
    assert response.status_code == 404


def test_items_must_point_to_a_stored_document(store: SQLiteStore) -> None:
    response = TestClient(app).post("/v1/ingestion/items", json=_body({"quantum": 4}))
    assert response.status_code == 422
    assert "doc_os_midterm_2025_de2_photo" in response.json()["detail"]


def test_blueprint_with_an_unapproved_item_is_409_I5(store: SQLiteStore, stubs: None, rr_slot: BlueprintSlot) -> None:
    client = TestClient(app)
    item_id = _upload(client)["item_id"]
    store.save_blueprint(_blueprint(rr_slot), item_ids=[item_id])
    response = client.post("/v1/blueprints/bp_sqlite_1/twins", json={"count": 1})
    assert response.status_code == 409 and item_id in str(response.json()["detail"])


def test_idempotency_replay_works_after_a_restart_and_expires(
    store: SQLiteStore,
    stubs: None,
    clock: Clock,
    sandbox_calls: list[int],
    db_path: Path,
    rr_slot: BlueprintSlot,
) -> None:
    client = TestClient(app)
    store.save_blueprint(_blueprint(rr_slot))
    body = {"count": 1, "base_seed": BASE_SEED}
    headers = {"Idempotency-Key": "key-1"}
    first = client.post("/v1/blueprints/bp_sqlite_1/twins", json=body, headers=headers)
    assert first.status_code == 201 and len(sandbox_calls) == 1

    reopened = SQLiteStore.open(db_path)
    try:
        app.dependency_overrides[get_store] = lambda: reopened
        clock.now += timedelta(hours=24)
        replay = client.post("/v1/blueprints/bp_sqlite_1/twins", json=body, headers=headers)
        assert replay.status_code == 201 and replay.json() == first.json()
        assert len(sandbox_calls) == 1, "replay không được chạy lại sandbox"
        clock.now += timedelta(seconds=1)
        expired = client.post("/v1/blueprints/bp_sqlite_1/twins", json=body, headers=headers)
        assert len(sandbox_calls) == 2
        assert expired.json()["twins"][0]["twin_id"] != first.json()["twins"][0]["twin_id"]
    finally:
        reopened.close()
