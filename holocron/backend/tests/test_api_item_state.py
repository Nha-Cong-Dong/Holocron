from typing import Any

from fastapi.testclient import TestClient

from holocron_core.routers.api import app

ROWS = [("P1", "0", "2"), ("P2", "2", "9"), ("P3", "5", "5"), ("P4", "8", "2"), ("P5", "11", "4")]


def _table() -> dict[str, Any]:
    cells = [
        {"row": r, "col": c, "text": t, "confidence": 0.97} for r, row in enumerate(ROWS) for c, t in enumerate(row)
    ]
    return {
        "table_id": "tbl_p2_q7",
        "page_number": 2,
        "headers": ["Tiến trình", "Thời điểm đến", "Burst time"],
        "cells": cells,
    }


def _body(solver_inputs: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_doc_id": "doc_os_midterm_2025_de2_photo",
        "page_number": 2,
        "topic_key": "os.cpu_sched.rr.avg_waiting_time",
        "stem_latex": r"Round Robin với $q = 4$ ms. Tính $\overline{WT}$.",
        "options": {"A": "6.00", "B": "4.20", "C": "2.60", "D": "10.40"},
        "table_primary": _table(),
        "table_secondary": _table(),
        "solver_inputs": solver_inputs,
    }


def _create(client: TestClient) -> str:
    created = client.post("/v1/ingestion/items", json=_body({"quantum": 4}))
    assert created.status_code == 201
    assert created.json()["report"]["status"] == "AUTO_PASS", created.json()["report"]["issues"]
    item_id: str = created.json()["item_id"]
    return item_id


def test_null_quantum_is_rejected_with_422() -> None:
    client = TestClient(app)
    resp = client.post("/v1/ingestion/items", json=_body({"quantum": None}))
    assert resp.status_code == 422
    assert any(err["loc"][-1] == "quantum" for err in resp.json()["detail"])


def test_missing_quantum_is_blocking_not_server_error() -> None:
    client = TestClient(app)
    resp = client.post("/v1/ingestion/items", json=_body({}))
    assert resp.status_code == 201
    assert resp.json()["report"]["status"] == "BLOCKED"


def test_cannot_approve_non_cloneable_item() -> None:
    client = TestClient(app)
    item_id = _create(client)
    flagged = client.post(
        f"/v1/ingestion/items/{item_id}/non-cloneable", json={"editor_id": "u_tuan", "reason": "đề cần hình vẽ"}
    )
    assert flagged.status_code == 200
    resp = client.post(f"/v1/ingestion/items/{item_id}/approve")
    assert resp.status_code == 400
    assert client.get(f"/v1/ingestion/items/{item_id}").json()["approved"] is False


def test_cannot_flag_approved_item_as_non_cloneable() -> None:
    client = TestClient(app)
    item_id = _create(client)
    assert client.post(f"/v1/ingestion/items/{item_id}/approve").status_code == 200
    resp = client.post(
        f"/v1/ingestion/items/{item_id}/non-cloneable", json={"editor_id": "u_tuan", "reason": "đề cần hình vẽ"}
    )
    assert resp.status_code == 400
    assert client.get(f"/v1/ingestion/items/{item_id}").json()["non_cloneable"] is False
