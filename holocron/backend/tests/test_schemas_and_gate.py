import unicodedata

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from holocron_core.domain.schemas import KnowledgeChunk
from holocron_core.routers.api import app
from holocron_core.services.ingestion_gate import latex_issues

ROWS = [("P1", "0", "2"), ("P2", "2", "9"), ("P3", "5", "5"), ("P4", "8", "2"), ("P5", "11", "4")]


def _table(rows, misread=None, conf=0.97):
    cells = []
    for r, row in enumerate(rows):
        for c, text in enumerate(row):
            if misread and (r, c) == misread[0]:
                cells.append({"row": r, "col": c, "text": misread[1], "confidence": 0.61})
            else:
                cells.append({"row": r, "col": c, "text": text, "confidence": conf})
    return {
        "table_id": "tbl_p2_q7",
        "page_number": 2,
        "headers": ["Tiến trình", "Thời điểm đến", "Burst time"],
        "cells": cells,
    }


def test_chunk_rejects_quote_not_in_text():
    with pytest.raises(ValidationError):
        KnowledgeChunk(
            doc_id="doc_x",
            subject_code="IT3070",
            chapter_code="ch5",
            page_number=1,
            raw_text="Round Robin dùng lượng tử thời gian q.",
            verbatim_quote="câu này không có trong text",
            category="THEORY",
            token_count=10,
        )


def test_chunk_accepts_quote_in_nfd_against_nfc_text():
    """I3 (ADR-006 mục 9): trích dẫn ở dạng tổ hợp NFD vẫn khớp văn bản NFC tương đương chính tắc."""
    raw_text = unicodedata.normalize("NFC", "Round Robin dùng lượng tử thời gian q.")
    quote = unicodedata.normalize("NFD", "dùng lượng tử   thời gian")
    assert quote not in raw_text  # so chuỗi con trực tiếp sẽ loại nhầm
    chunk = KnowledgeChunk(
        doc_id="doc_x",
        subject_code="IT3070",
        chapter_code="ch5",
        page_number=1,
        raw_text=raw_text,
        verbatim_quote=quote,
        category="THEORY",
        token_count=10,
    )
    assert chunk.verbatim_quote == quote


def test_gate_blocks_misread_then_user_fix_unblocks():
    client = TestClient(app)
    body = {
        "source_doc_id": "doc_os_midterm_2025_de2_photo",
        "page_number": 2,
        "topic_key": "os.cpu_sched.rr.avg_waiting_time",
        "stem_latex": "Round Robin với $q = 4$ ms. Tính $\\overline{WT}$.",
        "options": {"A": "6.00", "B": "4.20", "C": "2.60", "D": "10.40"},
        "table_primary": _table(ROWS, misread=((1, 2), "8")),
        "table_secondary": _table(ROWS),
        "solver_inputs": {"quantum": 4},
    }
    created = client.post("/v1/ingestion/items", json=body).json()
    codes = {i["code"] for i in created["report"]["issues"]}
    assert created["report"]["status"] == "BLOCKED"
    assert {"DUAL_PASS_MISMATCH", "LOW_CONFIDENCE", "SELF_SOLVE_MISMATCH"} <= codes
    assert client.post(f"/v1/ingestion/items/{created['item_id']}/approve").status_code == 409

    fixed = client.patch(
        f"/v1/ingestion/items/{created['item_id']}/table",
        json={
            "editor_id": "u_tuan",
            "expected_version": 1,
            "patches": [{"row": 1, "col": 2, "new_text": "9"}],
        },
    ).json()
    assert fixed["report"]["status"] == "AUTO_PASS", fixed["report"]["issues"]
    assert fixed["self_solve"]["matched_option_key"] == "A"
    assert fixed["audits"][0]["changes"] == [{"row": 1, "col": 2, "old": "8", "new": "9"}]

    stale = client.patch(
        f"/v1/ingestion/items/{created['item_id']}/table",
        json={
            "editor_id": "u_khac",
            "expected_version": 1,
            "patches": [{"row": 0, "col": 2, "new_text": "3"}],
        },
    )
    assert stale.status_code == 409
    assert client.post(f"/v1/ingestion/items/{created['item_id']}/approve").json()["approved"] is True


def test_latex_unbalanced_blocks():
    assert latex_issues("Tính $\\frac{1}{n$ ms")
    assert not latex_issues("Tính $\\frac{1}{n}$ ms")
