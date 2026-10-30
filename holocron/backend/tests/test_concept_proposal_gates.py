"""Cổng tích sẵn `preselect_page_flags_v1` (`services/concept_proposal_gates.py`; spec §5.8; ADR-007 mục 3).

Kiểm từng cổng trên cờ trang nguồn, quy tắc "thiếu dữ liệu ⇒ trượt" (trang đã xoá, JSON trang trước GĐ0, nhãn bài
tập chưa xác định), `PAIR_PRESELECT_ENABLED` bật/tắt, và đường đồng bộ `propose` cũng gắn `failed_gates`.
LLM là stub; KHÔNG gọi mạng.
"""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from holocron_core.domain.vault import ConceptPair, ConceptPairStatus
from holocron_core.infrastructure.llm_config import (
    PAIR_PRESELECT_ENABLED,
    ConceptProposalSettings,
    LlmSettings,
    LlmTaskLimits,
    RuntimeConfig,
    load_runtime_config,
)
from holocron_core.infrastructure.memory_store import IngestionStore
from holocron_core.routers import vault
from holocron_core.routers.deps import get_clock, get_runtime_config, get_store
from holocron_core.services.concept_proposal import propose_concept_pairs
from holocron_core.services.concept_proposal_gates import PageGateView, failed_gates, page_lookup, pre_selected
from holocron_core.services.concept_proposal_pointer import run_pointer_batch
from tests.proposal_support import (
    DEF_PROCESS,
    SUBJECT,
    GatePage,
    LegacyPage,
    StubClient,
    as_json,
    label,
    pointer,
    seed,
    span_of,
)
from tests.vault_support import NOW

ALL_PAGE_GATES = ["G1_SOURCE", "G2_GLYPH", "G3_OCCLUDED", "G4_COLUMN_MIX", "G5_NUMBERS", "EXERCISE"]
CONFIGURED_ENV = {
    "HOLOCRON_LLM_API_KEY": "sk-test-gates",
    "HOLOCRON_LLM_BASE_URL": "https://gateway.example.test/v1",
    "HOLOCRON_LLM_MODEL_EXTRACT": "model-x",
}


def _gates_for(
    page: GatePage | LegacyPage | None, quote: str = DEF_PROCESS, exercise: bool | None = False
) -> list[str]:
    store = IngestionStore()
    chunk = seed(store, [page or GatePage(), GatePage()])[0]
    if page is None:
        store.delete_document(chunk.doc_id)
    return list(failed_gates(chunk, quote, exercise, page_lookup(store)))


def test_clean_page_passes_every_gate() -> None:
    assert _gates_for(GatePage()) == []


@pytest.mark.parametrize(
    ("page", "gate"),
    [
        (GatePage(tier_used="ocr"), "G1_SOURCE"),
        (GatePage(tier_used=None), "G1_SOURCE"),
        (GatePage(scan_text_layer=True), "G1_SOURCE"),
        (GatePage(review_reasons=["FONT_ENCODING_SUSPECT"]), "G2_GLYPH"),
        (GatePage(review_reasons=["TEXT_OCCLUDED"]), "G3_OCCLUDED"),
        (GatePage(review_reasons=["LOW_TEXT_CONTRAST"]), "G3_OCCLUDED"),
        (GatePage(has_table=True), "G4_COLUMN_MIX"),
        (GatePage(cross_check={"status": "mismatch"}), "G5_NUMBERS"),
        (GatePage(cross_check={"status": "unavailable"}), "G5_NUMBERS"),
        (GatePage(page_role="exercise"), "EXERCISE"),
    ],
)
def test_each_page_flag_fails_exactly_its_gate(page: GatePage, gate: str) -> None:
    assert _gates_for(page) == [gate]


@pytest.mark.parametrize("status", ["ok", "not_applicable"])
def test_cross_check_ok_or_not_applicable_passes_g5(status: str) -> None:
    assert _gates_for(GatePage(cross_check={"status": status})) == []


def test_other_review_reasons_do_not_fail_any_gate() -> None:
    assert _gates_for(GatePage(review_reasons=["LOW_CONF_LINES", "HIGH_INK_OUTSIDE_TEXT"])) == []


def test_deleted_source_page_fails_g1_and_every_page_gate() -> None:
    assert _gates_for(None) == ALL_PAGE_GATES


def test_page_without_denoise_fields_fails_the_gates_that_need_them() -> None:
    """Thiếu dữ liệu ⇒ trượt (UNCONFIRMED tới khi nối đủ field của GĐ0): G1 (nửa `scan_text_layer`), G2, G3, EXERCISE
    (nửa `page_role`); G4, G5 đọc được nên vẫn qua."""
    assert _gates_for(LegacyPage()) == ["G1_SOURCE", "G2_GLYPH", "G3_OCCLUDED", "EXERCISE"]


@pytest.mark.parametrize("char", ["�", "", "\U000f0001"])
def test_replacement_or_private_use_character_in_the_quote_fails_g2(char: str) -> None:
    assert _gates_for(GatePage(), quote=f"Tiến trình {char} là một chương trình") == ["G2_GLYPH"]


@pytest.mark.parametrize("exercise", [True, None])
def test_exercise_or_unknown_label_fails_the_exercise_gate(exercise: bool | None) -> None:
    assert _gates_for(GatePage(), exercise=exercise) == ["EXERCISE"]


def test_every_page_of_a_multi_page_chunk_is_checked() -> None:
    store = IngestionStore()
    chunk = seed(store)[0].model_copy(update={"page_end": 2})
    assert failed_gates(chunk, DEF_PROCESS, False, page_lookup(store)) == ALL_PAGE_GATES  # trang 2 không có
    assert failed_gates(chunk, DEF_PROCESS, False, page_lookup(None)) == ALL_PAGE_GATES


def test_page_view_ignores_unknown_fields_and_missing_ones_are_none() -> None:
    view = PageGateView.model_validate({"tier_used": "text_layer", "lines": [], "extra": 1})
    assert view.scan_text_layer is None and view.page_role is None and view.has_table is None


def test_pre_selected_requires_the_rule_enabled_and_no_failed_gate() -> None:
    assert pre_selected([], True) and not pre_selected([], False) and not pre_selected(["G4_COLUMN_MIX"], True)
    assert PAIR_PRESELECT_ENABLED is False
    assert ConceptProposalSettings().pair_preselect_enabled is False
    assert LlmSettings().proposal.pair_preselect_enabled is False


def test_concept_pair_orders_gates_and_refuses_pre_selected_with_failed_gates() -> None:
    base = {
        "chunk_id": "chk_1",
        "subject_code": SUBJECT,
        "chapter_code": "ch2",
        "term": "Tiến trình",
        "definition_quote": DEF_PROCESS,
        "status": ConceptPairStatus.PROPOSED,
        "proposed_by": "llm",
        "created_at": NOW,
    }
    pair = ConceptPair.model_validate({**base, "failed_gates": ["EXERCISE", "G1_SOURCE", "EXERCISE"]})
    assert pair.failed_gates == ["G1_SOURCE", "EXERCISE"] and not pair.pre_selected
    with pytest.raises(ValidationError):
        ConceptPair.model_validate({**base, "failed_gates": ["G2_GLYPH"], "pre_selected": True})
    with pytest.raises(ValidationError):
        ConceptPair.model_validate({**base, "failed_gates": ["G9"]})
    legacy = ConceptPair.model_validate(base)
    assert legacy.pre_selected is False and legacy.failed_gates == []


# --------------------------------------------------------------------------- #
# Tích sẵn khi lưu cặp
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("enabled", [True, False])
def test_pointer_batch_preselects_only_when_enabled(enabled: bool) -> None:
    store = IngestionStore()
    chunks = seed(store)
    s = span_of(chunks, DEF_PROCESS)
    response = as_json(
        pointer(s, "Tiến trình", [s], "Tiến trình là", "bộ nhớ."), *(label(chunk, False) for chunk in chunks)
    )
    outcome = run_pointer_batch(
        store,
        StubClient(response),
        chunks=chunks,
        now=NOW,
        limits=LlmTaskLimits(),
        monotonic=lambda: 0.0,
        pages=store,
        preselect_enabled=enabled,
        label_retries=0,
    )
    [pair] = outcome.pairs
    assert pair.failed_gates == [] and pair.pre_selected is enabled
    assert pair.status is ConceptPairStatus.PROPOSED and pair.confirmed_at is None  # tích sẵn không phải xác nhận
    assert store.list_concept_pairs(SUBJECT) == [pair]


def test_sync_propose_attaches_failed_gates_and_uses_the_stored_exercise_label() -> None:
    store = IngestionStore()
    chunks = seed(store, [GatePage(has_table=True), GatePage()])
    theory = chunks[0]
    old_protocol = json.dumps(
        [{"term": "Tiến trình", "definition_quote": DEF_PROCESS, "chunk_id": theory.chunk_id}], ensure_ascii=False
    )
    result = propose_concept_pairs(
        store,
        StubClient(old_protocol),
        subject_code=SUBJECT,
        chunk_ids=[theory.chunk_id],
        now=NOW,
        pages=store,
        preselect_enabled=True,
    )
    [pair] = result.pairs
    assert pair.failed_gates == ["G4_COLUMN_MIX", "EXERCISE"]  # chunk chưa có nhãn bài tập ⇒ EXERCISE trượt
    assert not pair.pre_selected


def test_sync_propose_without_pages_fails_every_page_gate() -> None:
    store = IngestionStore()
    theory = seed(store)[0]
    old_protocol = json.dumps(
        [{"term": "Tiến trình", "definition_quote": DEF_PROCESS, "chunk_id": theory.chunk_id}], ensure_ascii=False
    )
    [pair] = propose_concept_pairs(
        store, StubClient(old_protocol), subject_code=SUBJECT, chunk_ids=[theory.chunk_id], now=NOW
    ).pairs
    assert pair.failed_gates == ALL_PAGE_GATES and not pair.pre_selected


@pytest.mark.parametrize("enabled", [True, False])
def test_sync_endpoint_returns_gates_and_honours_the_preselect_setting(enabled: bool) -> None:
    store = IngestionStore()
    theory = seed(store)[0]
    store.set_chunk_exercise(theory.chunk_id, False, theory.version)
    base = load_runtime_config(CONFIGURED_ENV)
    config = RuntimeConfig(
        llm=base.llm.model_copy(update={"proposal": ConceptProposalSettings(pair_preselect_enabled=enabled)}),
        vlm=base.vlm,
        vlm_missing=base.vlm_missing,
    )
    old_protocol = json.dumps(
        [{"term": "Tiến trình", "definition_quote": DEF_PROCESS, "chunk_id": theory.chunk_id}], ensure_ascii=False
    )
    app = FastAPI()
    app.include_router(vault.router)
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_clock] = lambda: lambda: NOW
    app.dependency_overrides[get_runtime_config] = lambda: config
    app.dependency_overrides[vault.get_chat_client] = lambda: StubClient(old_protocol)
    response = TestClient(app).post(
        "/v1/vault/concept-pairs/propose", json={"subject_code": SUBJECT, "chunk_ids": [theory.chunk_id]}
    )
    assert response.status_code == 200
    [pair] = response.json()["pairs"]
    assert pair["failed_gates"] == [] and pair["pre_selected"] is enabled and pair["status"] == "proposed"
    listed = TestClient(app).get("/v1/vault/concept-pairs", params={"subject_code": SUBJECT}).json()
    assert [(item["pre_selected"], item["failed_gates"]) for item in listed] == [(enabled, [])]
