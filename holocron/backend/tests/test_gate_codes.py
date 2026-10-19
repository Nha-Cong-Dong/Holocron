"""Mọi mã issue của Ingestion Gate và các nhánh lỗi của PATCH/approve."""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from holocron_core.routers.api import app
from holocron_core.services.ingestion_gate import (
    ExtractedCell,
    ExtractedTable,
    Severity,
    check_table,
    gate_status,
    latex_issues,
)
from tests.test_api_item_state import _body, _create

RR_TOPIC = "os.cpu_sched.rr.avg_waiting_time"
HEADERS = ["Tiến trình", "Thời điểm đến", "Burst time"]


def _table(rows: list[tuple[str, str, str]], *, headers: list[str] | None = None, conf: float = 0.97) -> ExtractedTable:
    cells = [
        ExtractedCell(row=r, col=c, text=t, confidence=conf) for r, row in enumerate(rows) for c, t in enumerate(row)
    ]
    return ExtractedTable(table_id="tbl_x", page_number=1, headers=headers or HEADERS, cells=cells)


GOOD_ROWS = [("P1", "0", "2"), ("P2", "2", "9"), ("P3", "5", "5"), ("P4", "8", "2")]


def _codes(table: ExtractedTable, **kw: Any) -> set[tuple[str, Severity]]:
    issues = check_table(RR_TOPIC, table, kw.get("secondary"), kw.get("text_layer"), None)
    return {(i.code, i.severity) for i in issues}


def test_clean_table_has_no_issue() -> None:
    assert _codes(_table(GOOD_ROWS)) == set()


def test_header_column_count_mismatch_is_blocking() -> None:
    table = _table([("P1", "0")], headers=["Tiến trình", "Thời điểm đến"])
    assert ("HEADER_MISMATCH", Severity.BLOCKING) in _codes(table)


def test_header_alias_mismatch_is_warning() -> None:
    table = _table(GOOD_ROWS, headers=["Tiến trình", "Thời điểm đến", "Cột lạ"])
    codes = _codes(table)
    assert ("HEADER_MISMATCH", Severity.WARNING) in codes
    assert ("HEADER_MISMATCH", Severity.BLOCKING) not in codes


def test_missing_cell_is_blocking() -> None:
    table = _table(GOOD_ROWS)
    table = table.model_copy(update={"cells": [c for c in table.cells if (c.row, c.col) != (1, 2)]})
    assert ("MISSING_CELL", Severity.BLOCKING) in _codes(table)


def test_low_confidence_is_warning_only() -> None:
    codes = _codes(_table(GOOD_ROWS, conf=0.5))
    assert codes == {("LOW_CONFIDENCE", Severity.WARNING)}


def test_not_a_number_and_letter_o_is_tolerated() -> None:
    assert ("NOT_A_NUMBER", Severity.BLOCKING) in _codes(_table([("P1", "abc", "2")]))
    assert ("NOT_A_NUMBER", Severity.BLOCKING) not in _codes(_table([("P1", "O", "2")]))


@pytest.mark.parametrize("arrival, burst", [("201", "2"), ("0", "0"), ("0", "101"), ("-1", "2")])
def test_values_outside_domain_are_out_of_range(arrival: str, burst: str) -> None:
    issues = _codes(_table([("P1", arrival, burst)]))
    assert ("OUT_OF_RANGE", Severity.BLOCKING) in issues or ("NOT_A_NUMBER", Severity.BLOCKING) in issues


def test_duplicate_label_is_blocking() -> None:
    assert ("DUPLICATE_LABEL", Severity.BLOCKING) in _codes(_table([("P1", "0", "2"), ("p1 ", "1", "3")]))


def test_dual_pass_mismatch_is_blocking_with_alternatives() -> None:
    primary = _table(GOOD_ROWS)
    secondary = _table([("P1", "0", "2"), ("P2", "2", "8"), ("P3", "5", "5"), ("P4", "8", "2")])
    issues = check_table(RR_TOPIC, primary, secondary, None, None)
    mismatch = [i for i in issues if i.code == "DUAL_PASS_MISMATCH"]
    assert len(mismatch) == 1 and (mismatch[0].row, mismatch[0].col) == (1, 2)
    assert mismatch[0].alternatives == ["8", "9"]


def test_secondary_missing_cell_is_dual_pass_mismatch() -> None:
    secondary = _table(GOOD_ROWS[:3])
    assert ("DUAL_PASS_MISMATCH", Severity.BLOCKING) in _codes(_table(GOOD_ROWS), secondary=secondary)


def test_not_in_text_layer_is_warning() -> None:
    codes = _codes(_table(GOOD_ROWS), text_layer="P1 0 2 P2 2 P3 5 5 P4 8 2")
    assert ("NOT_IN_TEXT_LAYER", Severity.WARNING) in codes


@pytest.mark.parametrize("stem", ["Tính $x + 1", r"Tính $\frac{1}{2$", "Tính $x}$"])
def test_latex_unbalanced_is_blocking(stem: str) -> None:
    assert [i.code for i in latex_issues(stem)] and all(i.code == "LATEX_UNBALANCED" for i in latex_issues(stem))


def test_balanced_latex_and_escaped_dollar_pass() -> None:
    assert latex_issues(r"Giá \$5 và $x_{1}$") == []


def test_gate_status_levels() -> None:
    assert gate_status([]).value == "AUTO_PASS"
    warn = check_table(RR_TOPIC, _table(GOOD_ROWS, conf=0.5), None, None, None)
    assert gate_status(warn).value == "NEEDS_REVIEW"
    block = check_table(RR_TOPIC, _table([("P1", "abc", "2")]), None, None, None)
    assert gate_status(block).value == "BLOCKED"


def test_ambiguous_self_solve_is_blocking() -> None:
    client = TestClient(app)
    body = _body({"quantum": 4})
    body["options"] = {"A": "6.00", "B": "6.00", "C": "2.60", "D": "10.40"}
    created = client.post("/v1/ingestion/items", json=body).json()
    assert created["self_solve"]["status"] == "AMBIGUOUS"
    assert created["report"]["status"] == "BLOCKED"


# --- PATCH / approve --------------------------------------------------------------------------------------------


def _patch(client: TestClient, item_id: str, **overrides: Any) -> Any:
    body = {"editor_id": "u_tuan", "expected_version": 1, "patches": [{"row": 0, "col": 2, "new_text": "2"}]}
    body.update(overrides)
    return client.patch(f"/v1/ingestion/items/{item_id}/table", json=body)


def test_patch_nonexistent_column_is_422() -> None:
    client = TestClient(app)
    item_id = _create(client)
    resp = _patch(client, item_id, patches=[{"row": 0, "col": 9, "new_text": "1"}])
    assert resp.status_code == 422


def test_patch_with_stale_version_is_409_and_does_not_mutate() -> None:
    client = TestClient(app)
    item_id = _create(client)
    assert _patch(client, item_id, expected_version=7).status_code == 409
    assert client.get(f"/v1/ingestion/items/{item_id}").json()["table_primary"]["version"] == 1


def test_patch_bumps_version_and_records_audit() -> None:
    client = TestClient(app)
    item_id = _create(client)
    resp = _patch(client, item_id, patches=[{"row": 0, "col": 2, "new_text": "2"}])
    assert resp.status_code == 200
    body = resp.json()
    assert body["table_primary"]["version"] == 2
    assert body["audits"][-1]["changes"] == [{"row": 0, "col": 2, "old": "2", "new": "2"}]


def test_patch_and_stem_patch_rejected_after_approve() -> None:
    client = TestClient(app)
    item_id = _create(client)
    assert client.post(f"/v1/ingestion/items/{item_id}/approve").status_code == 200
    assert _patch(client, item_id).status_code == 409
    stem = client.patch(
        f"/v1/ingestion/items/{item_id}/stem", json={"editor_id": "u", "stem_latex": r"Round Robin $q = 4$ ms."}
    )
    assert stem.status_code == 409


def test_patch_stem_recomputes_latex_issue_and_blocks_approve() -> None:
    client = TestClient(app)
    item_id = _create(client)
    bad = client.patch(
        f"/v1/ingestion/items/{item_id}/stem", json={"editor_id": "u", "stem_latex": "Round Robin $q = 4 ms."}
    )
    assert bad.status_code == 200
    assert bad.json()["report"]["status"] == "BLOCKED"
    assert any(i["code"] == "LATEX_UNBALANCED" for i in bad.json()["report"]["issues"])
    assert client.post(f"/v1/ingestion/items/{item_id}/approve").status_code == 409
    good = client.patch(
        f"/v1/ingestion/items/{item_id}/stem", json={"editor_id": "u", "stem_latex": r"Round Robin $q = 4$ ms."}
    )
    assert good.json()["report"]["status"] == "AUTO_PASS"


def test_unknown_item_returns_404_everywhere() -> None:
    client = TestClient(app)
    assert client.get("/v1/ingestion/items/ing_nope").status_code == 404
    assert _patch(client, "ing_nope").status_code == 404
    assert client.post("/v1/ingestion/items/ing_nope/approve").status_code == 404
