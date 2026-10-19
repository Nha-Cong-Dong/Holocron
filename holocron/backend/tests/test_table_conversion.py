"""Chuẩn hoá bảng đã qua Gate thành `CPUSchedulingProblem`/params và validator của `ExtractedTable`."""

import pytest
from pydantic import ValidationError

from holocron_core.services.ingestion_gate import (
    ExtractedCell,
    ExtractedTable,
    table_to_params,
    table_to_problem,
)

RR_TOPIC = "os.cpu_sched.rr.avg_waiting_time"
HEADERS = ["Tiến trình", "Thời điểm đến", "Burst time"]


def _cells(rows: list[tuple[str, str, str]]) -> list[ExtractedCell]:
    return [
        ExtractedCell(row=r, col=c, text=t, confidence=1.0) for r, row in enumerate(rows) for c, t in enumerate(row)
    ]


def _table(rows: list[tuple[str, str, str]]) -> ExtractedTable:
    return ExtractedTable(table_id="tbl_x", page_number=1, headers=HEADERS, cells=_cells(rows))


def test_table_rejects_duplicate_coordinates_and_out_of_range_columns() -> None:
    cell = ExtractedCell(row=0, col=0, text="P1", confidence=1.0)
    with pytest.raises(ValidationError, match="trùng toạ độ"):
        ExtractedTable(table_id="t", page_number=1, headers=HEADERS, cells=[cell, cell])
    with pytest.raises(ValidationError, match="vượt số cột"):
        ExtractedTable(
            table_id="t",
            page_number=1,
            headers=HEADERS,
            cells=[ExtractedCell(row=0, col=3, text="x", confidence=1.0)],
        )


def test_table_to_problem_unknown_topic_and_missing_cell_and_non_number() -> None:
    table = _table([("P1", "0", "2")])
    with pytest.raises(ValueError, match="chưa có bộ chuyển đổi"):
        table_to_problem("os.unknown.topic.x", table, 4)
    missing = table.model_copy(update={"cells": [c for c in table.cells if c.col != 2]})
    with pytest.raises(ValueError, match="thiếu ô"):
        table_to_problem(RR_TOPIC, missing, 4)
    with pytest.raises(ValueError, match="không phải số"):
        table_to_problem(RR_TOPIC, _table([("P1", "abc", "2")]), 4)


def test_table_to_params_passes_quantum_and_requires_it_for_rr() -> None:
    table = _table([("P1", "0", "2"), ("P2", "1", "3")])
    params = table_to_params(RR_TOPIC, table, {"quantum": 4})
    assert params["quantum"] == 4 and params["bursts"] == [2, 3]
    with pytest.raises(ValueError, match="quantum"):
        table_to_params(RR_TOPIC, table, {})
