from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from holocron_core.domain.schemas import CPUSchedulingProblem, ProcessItem, SchedulingAlgorithm
from holocron_core.routers.api import MAX_UPLOAD_BYTES, app
from holocron_core.services.extraction import sniff_media_type

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PDF_BYTES = b"%PDF-1.7\n" + b"0" * 64
RR_TOPIC = "os.cpu_sched.rr.avg_waiting_time"


def _upload(
    client: TestClient,
    content: bytes,
    *,
    filename: str = "de_thi.png",
    content_type: str = "image/png",
    data: dict[str, str] | None = None,
) -> Any:
    return client.post(
        "/v1/ingestion/upload",
        files={"file": (filename, content, content_type)},
        data=data if data is not None else {"topic_key": RR_TOPIC},
    )


def test_upload_runs_dual_pass_gate_and_flags_misread_cell() -> None:
    client = TestClient(app)
    resp = _upload(client, PNG_BYTES)
    assert resp.status_code == 201, resp.text
    body = resp.json()

    assert body["document"]["document_id"].startswith("doc_")
    assert body["document"]["media_type"] == "image/png"
    report = body["item"]["report"]
    assert report["status"] == "BLOCKED"
    flagged = {(i["code"], i["row"], i["col"]) for i in report["issues"]}
    assert ("DUAL_PASS_MISMATCH", 1, 2) in flagged
    assert ("LOW_CONFIDENCE", 1, 2) in flagged
    assert any(i["code"] == "SELF_SOLVE_MISMATCH" for i in report["issues"])
    mismatch = next(i for i in report["issues"] if i["code"] == "DUAL_PASS_MISMATCH")
    assert mismatch["alternatives"] == ["8", "9"]
    assert body["item"]["problem"]["algorithm"] == "RR"
    assert body["item"]["problem"]["quantum"] == 4


def test_fixing_flagged_cell_after_upload_unblocks_gate() -> None:
    client = TestClient(app)
    item = _upload(client, PNG_BYTES).json()["item"]
    fixed = client.patch(
        f"/v1/ingestion/items/{item['item_id']}/table",
        json={"editor_id": "u_tuan", "expected_version": 1, "patches": [{"row": 1, "col": 2, "new_text": "9"}]},
    )
    assert fixed.status_code == 200
    assert fixed.json()["report"]["status"] == "AUTO_PASS", fixed.json()["report"]["issues"]
    assert fixed.json()["self_solve"]["matched_option_key"] == "A"
    assert client.post(f"/v1/ingestion/items/{item['item_id']}/approve").json()["approved"] is True


def test_same_content_yields_same_document_id() -> None:
    client = TestClient(app)
    first = _upload(client, PNG_BYTES).json()
    second = _upload(client, PNG_BYTES, filename="ten_khac.png").json()
    assert first["document"]["document_id"] == second["document"]["document_id"]
    assert first["item"]["item_id"] != second["item"]["item_id"]


def test_pdf_is_accepted() -> None:
    resp = _upload(TestClient(app), PDF_BYTES, filename="de.pdf", content_type="application/pdf")
    assert resp.status_code == 201
    assert resp.json()["document"]["media_type"] == "application/pdf"


def test_form_quantum_overrides_extracted_quantum() -> None:
    resp = _upload(TestClient(app), PNG_BYTES, data={"topic_key": RR_TOPIC, "quantum": "3"})
    assert resp.status_code == 201
    assert resp.json()["item"]["problem"]["quantum"] == 3


def test_unknown_format_is_rejected_with_415() -> None:
    resp = _upload(TestClient(app), b"GIF89a" + b"\x00" * 32, filename="x.gif", content_type="image/gif")
    assert resp.status_code == 415


def test_declared_type_must_match_content() -> None:
    resp = _upload(TestClient(app), PNG_BYTES, filename="x.pdf", content_type="application/pdf")
    assert resp.status_code == 415


def test_empty_file_is_rejected_with_400() -> None:
    assert _upload(TestClient(app), b"").status_code == 400


def test_oversized_file_is_rejected_with_413() -> None:
    oversized = PNG_BYTES + b"\x00" * MAX_UPLOAD_BYTES
    assert _upload(TestClient(app), oversized).status_code == 413


def test_unsupported_topic_is_rejected_with_422() -> None:
    resp = _upload(TestClient(app), PNG_BYTES, data={"topic_key": "os.paging.lru.page_faults"})
    assert resp.status_code == 422


def test_invalid_quantum_form_value_is_rejected_with_422() -> None:
    resp = _upload(TestClient(app), PNG_BYTES, data={"topic_key": RR_TOPIC, "quantum": "0"})
    assert resp.status_code == 422


def test_sniff_media_type_recognises_webp() -> None:
    assert sniff_media_type(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "image/webp"
    assert sniff_media_type(b"not an image") is None


def test_rr_problem_requires_quantum_and_sjf_forbids_it() -> None:
    processes = [ProcessItem(name="P1", arrival_time=0, burst_time=3)]
    with pytest.raises(ValidationError):
        CPUSchedulingProblem(algorithm=SchedulingAlgorithm.RR, processes=processes)
    with pytest.raises(ValidationError):
        CPUSchedulingProblem(algorithm=SchedulingAlgorithm.SJF, processes=processes, quantum=2)
    sjf = CPUSchedulingProblem(algorithm=SchedulingAlgorithm.SJF, processes=processes)
    assert sjf.to_template_params() == {"names": ["P1"], "arrivals": [0], "bursts": [3]}


def test_problem_rejects_duplicate_process_names() -> None:
    with pytest.raises(ValidationError):
        CPUSchedulingProblem(
            algorithm=SchedulingAlgorithm.RR,
            quantum=2,
            processes=[
                ProcessItem(name="P1", arrival_time=0, burst_time=3),
                ProcessItem(name="p1", arrival_time=1, burst_time=2),
            ],
        )
