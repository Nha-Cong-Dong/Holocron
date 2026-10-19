"""Cưỡng chế I5 và hợp đồng đầu ra sandbox: F1 (topic lạ), F2 (self-solve SKIPPED), F3 (kiểu đầu ra)."""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from holocron_core.domain.schemas import (
    BlueprintSlot,
    KnowledgeChunk,
    QuestionFormat,
    QuestionTemplate,
    TemplateProvenance,
)
from holocron_core.infrastructure.template_library import rr_avg_waiting_template
from holocron_core.routers.api import IngestionItem, _recompute, app
from holocron_core.sandbox.sandbox import SandboxResult, SandboxStatus, run_template
from holocron_core.services.pipeline import ProductionOutcome, produce_twin
from tests.test_api_item_state import _body


def test_create_item_with_unknown_topic_is_422() -> None:
    client = TestClient(app)
    body = _body({"quantum": 4})
    body["topic_key"] = "os.cpu_sched.sjf.avg_waiting_time"
    resp = client.post("/v1/ingestion/items", json=body)
    assert resp.status_code == 422


def test_unknown_topic_cannot_auto_pass_through_recompute() -> None:
    """Phòng thủ chiều sâu: dù lọt qua tầng validate, item không có solver không thể AUTO_PASS."""
    item = IngestionItem(item_id="ing_x", **_body({"quantum": 4}))
    item.topic_key = "os.unknown.topic.x"
    assert _recompute(item).report is not None
    assert item.report is not None
    assert item.report.status.value == "BLOCKED"


def test_sandbox_timeout_in_self_solve_blocks_and_approve_409(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run_template(*args: Any, **kwargs: Any) -> SandboxResult:
        return SandboxResult(status=SandboxStatus.TIMEOUT, seed=0, duration_ms=2000, message="timeout")

    monkeypatch.setattr("holocron_core.services.ingestion_gate.run_template", fake_run_template)
    client = TestClient(app)
    created = client.post("/v1/ingestion/items", json=_body({"quantum": 4}))
    assert created.status_code == 201
    report = created.json()["report"]
    assert report["status"] == "BLOCKED"
    assert any(i["code"] == "SELF_SOLVE_MISMATCH" and i["severity"] == "blocking" for i in report["issues"])
    assert created.json()["self_solve"]["status"] == "SKIPPED"
    assert client.post(f"/v1/ingestion/items/{created.json()['item_id']}/approve").status_code == 409


def _template_with_none_value() -> QuestionTemplate:
    good = rr_avg_waiting_template(TemplateProvenance.LLM_GENERATED)
    marker = '"value": '
    assert marker in good.generator_source
    source = good.generator_source.replace(marker, '"value": None, "_unused": ', 1)
    return good.model_copy(update={"generator_source": source})


def test_sandbox_rejects_non_numeric_value_as_contract_error() -> None:
    result = run_template(_template_with_none_value().generator_source, seed=5)
    assert result.status is SandboxStatus.CONTRACT_ERROR


def test_bool_value_is_contract_error() -> None:
    good = rr_avg_waiting_template(TemplateProvenance.LLM_GENERATED)
    source = good.generator_source.replace('"value": ', '"value": True, "_unused": ', 1)
    assert run_template(source, seed=5).status is SandboxStatus.CONTRACT_ERROR


class _SingleAuthor:
    def __init__(self, template: QuestionTemplate) -> None:
        self._template = template

    def author(self, slot: BlueprintSlot, feedback: list[str]) -> QuestionTemplate:
        return self._template


class _EmptyRegistry:
    def get_verified(self, topic_key: str) -> QuestionTemplate | None:
        return None


def test_numerical_twin_with_none_value_does_not_raise(rr_slot: BlueprintSlot, theory_chunk: KnowledgeChunk) -> None:
    slot = rr_slot.model_copy(update={"format": QuestionFormat.NUMERICAL, "distractor_archetypes": []})
    outcome: ProductionOutcome = produce_twin(
        slot,
        blueprint_id="bp_test",
        citation=theory_chunk.to_citation(),
        author=_SingleAuthor(_template_with_none_value()),
        registry=_EmptyRegistry(),
        base_seed=5,
    )
    assert outcome.twin is None and outcome.ticket is not None
    assert [a.outcome for a in outcome.attempts] == ["contract_error"] * 3
