"""Sửa theo review B5a: format slot tính toán, khoá phân biệt slot, 5xx không để lại môn ngầm (SPECIFICATION §5.9, §6.7)."""

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from holocron_core.domain.repositories import AppStore, StorageBusyError, StorageError
from holocron_core.domain.schemas import BlueprintSlot, ExamBlueprint, QuestionFormat, TheorySlot
from holocron_core.infrastructure.storage import sqlite_store
from holocron_core.infrastructure.storage.sqlite_store import SQLiteStore
from holocron_core.routers.api import app, get_store
from tests.test_theory_blueprint_api import NEW_SUBJECT, _post, _slots
from tests.test_theory_twins import STORE_KINDS, close_kind, open_kind

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


@pytest.fixture(params=STORE_KINDS)
def store(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[AppStore]:
    opened = open_kind(str(request.param), tmp_path)
    app.dependency_overrides[get_store] = lambda: opened
    yield opened
    app.dependency_overrides.clear()
    close_kind(opened)


def test_computational_slot_rejects_essay(rr_slot: BlueprintSlot) -> None:
    with pytest.raises(ValidationError, match="MCQ4 hoặc NUMERICAL"):
        BlueprintSlot.model_validate({**rr_slot.model_dump(mode="json"), "format": "ESSAY"})


def _plain(dumped: dict[str, Any]) -> dict[str, Any]:
    """Bỏ bốn số tổng hợp (computed field) vì `StrictModel` cấm field thừa khi đọc lại."""
    computed = ("chapter_distribution", "format_distribution", "cloneable_ratio", "mean_mapping_confidence")
    return {k: v for k, v in dumped.items() if k not in computed}


def _legacy_blueprint_json(rr_slot: BlueprintSlot) -> dict[str, Any]:
    slot = {**rr_slot.model_dump(mode="json"), "is_computational": False}
    blueprint = ExamBlueprint(
        source_exam_doc_id="doc_legacy_exam",
        subject_code="IT3070",
        total_questions=1,
        slots=[rr_slot.model_copy(update={"slot_index": 1})],
    )
    return _plain(blueprint.model_dump(mode="json")) | {"slots": [{**slot, "slot_index": 1}]}


def test_legacy_non_computational_blueprint_slot_still_validates(rr_slot: BlueprintSlot) -> None:
    blueprint = ExamBlueprint.model_validate(_legacy_blueprint_json(rr_slot))
    assert isinstance(blueprint.slots[0], BlueprintSlot)
    assert blueprint.slots[0].is_computational is False


def test_theory_slot_is_selected_only_by_question_kind() -> None:
    theory = TheorySlot(slot_index=1, chapter_code="ch1", format=QuestionFormat.MCQ4)
    blueprint = ExamBlueprint.model_validate(
        {"subject_code": "TRI1001", "total_questions": 1, "slots": [theory.model_dump(mode="json")]}
    )
    assert isinstance(blueprint.slots[0], TheorySlot)


def test_example_blueprint_sample_still_validates() -> None:
    sample = json.loads((EXAMPLES / "exam_blueprint_sample.json").read_text(encoding="utf-8"))
    assert isinstance(ExamBlueprint.model_validate(_plain(sample)).slots[0], BlueprintSlot)


def test_get_blueprint_reads_legacy_json_on_sqlite(tmp_path: Path, rr_slot: BlueprintSlot) -> None:
    path = tmp_path / "holocron.sqlite3"
    opened = SQLiteStore.open(path)
    try:
        blueprint = ExamBlueprint(
            source_exam_doc_id="doc_legacy_exam",
            subject_code="IT3070",
            total_questions=1,
            slots=[rr_slot.model_copy(update={"slot_index": 1})],
        )
        opened.save_blueprint(blueprint)
        legacy = _legacy_blueprint_json(rr_slot) | {"blueprint_id": blueprint.blueprint_id}
        raw = sqlite3.connect(path)
        raw.execute("UPDATE blueprints SET blueprint_json = ?", (json.dumps(legacy),))
        raw.commit()
        raw.close()
        app.dependency_overrides[get_store] = lambda: opened
        try:
            response = TestClient(app).get(f"/v1/blueprints/{blueprint.blueprint_id}")
        finally:
            app.dependency_overrides.clear()
        assert response.status_code == 200
        assert response.json()["slots"][0]["is_computational"] is False
    finally:
        opened.close()


@pytest.mark.parametrize("error", [StorageBusyError("busy"), StorageError("hỏng")])
def test_failed_save_leaves_no_implicit_subject(
    store: AppStore,
    error: StorageError,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*_: object, **__: object) -> Any:
        raise error

    if isinstance(store, SQLiteStore):
        monkeypatch.setattr(sqlite_store, "_dump", boom)  # lỗi giữa giao dịch, sau khi môn ngầm đã được INSERT
    else:
        monkeypatch.setattr(store, "_ensure_subject", boom)
    client = TestClient(app, raise_server_exceptions=False)
    response = _post(client, {"subject_code": NEW_SUBJECT, "theory_slots": _slots("ch1")})
    assert response.status_code >= 500
    monkeypatch.undo()
    assert store.get_subject(NEW_SUBJECT) is None
    assert client.get("/v1/blueprints", params={"subject_code": NEW_SUBJECT}).json() == []
