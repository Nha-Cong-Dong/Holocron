"""Biên đầu vào: tên file quá dài và bảng vượt số tiến trình tối đa của `CPUSchedulingProblem`."""

from typing import Any

from fastapi.testclient import TestClient

from holocron_core.routers.api import app
from tests.test_api_item_state import _body

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
RR_TOPIC = "os.cpu_sched.rr.avg_waiting_time"


def test_upload_with_overlong_filename_is_422_not_500() -> None:
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(
        "/v1/ingestion/upload",
        files={"file": ("a" * 300 + ".png", PNG_BYTES, "image/png")},
        data={"topic_key": RR_TOPIC},
    )
    assert resp.status_code == 422


def _body_with_rows(n: int) -> dict[str, Any]:
    body = _body({"quantum": 4})
    cells = [
        {"row": r, "col": c, "text": t, "confidence": 0.97}
        for r in range(n)
        for c, t in enumerate((f"P{r + 1}", str(r), "3"))
    ]
    body["table_primary"]["cells"] = cells
    body["table_secondary"]["cells"] = cells
    return body


def test_more_than_ten_processes_is_blocked_with_out_of_range_not_not_a_number() -> None:
    client = TestClient(app)
    resp = client.post("/v1/ingestion/items", json=_body_with_rows(12))
    assert resp.status_code == 201
    report = resp.json()["report"]
    assert report["status"] == "BLOCKED"
    codes = [i["code"] for i in report["issues"]]
    assert "OUT_OF_RANGE" in codes
    assert "NOT_A_NUMBER" not in codes
    assert client.post(f"/v1/ingestion/items/{resp.json()['item_id']}/approve").status_code == 409


def test_ten_processes_is_accepted_by_the_row_limit() -> None:
    client = TestClient(app)
    resp = client.post("/v1/ingestion/items", json=_body_with_rows(10))
    assert resp.status_code == 201
    assert all(i["code"] != "OUT_OF_RANGE" for i in resp.json()["report"]["issues"])
