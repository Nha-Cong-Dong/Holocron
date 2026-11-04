"""Đọc lại khối bằng AI theo yêu cầu (SPECIFICATION §6.10a; ADR-009 mục 15): đề xuất và bỏ đề xuất.

I3: chữ VLM chỉ ra response sau `vision_gate`; đề xuất bị cổng từ chối không mang chữ VLM nào; đề xuất KHÔNG ghi kho
trang. Mọi lời gọi VLM qua transport giả (không gọi mạng); PDF không cần thật vì bộ cắt ảnh là bản giả trả PNG tự tạo.
"""

from __future__ import annotations

import http.client
import json
import logging
import socket
import urllib.request
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from holocron_core.domain.ai import AiVlmMode, IpPolicy, ProviderCandidate
from holocron_core.domain.ai_reread import AiRereadProposal
from holocron_core.domain.blocks import Block, BlockFlag, WordRef
from holocron_core.domain.documents import PageExtraction, PageGeometry
from holocron_core.domain.repositories import AppStore
from holocron_core.infrastructure.ai_runtime import InMemorySpendLedger
from holocron_core.infrastructure.document_readers.pdf_reader import CropFailure, CropOutcome, RenderedCrop
from holocron_core.infrastructure.document_readers.protocol import CropRegion
from holocron_core.infrastructure.llm_config import LlmHttpLimits, load_runtime_config
from holocron_core.routers import documents
from holocron_core.routers.deps import (
    get_ai_consent,
    get_clock,
    get_llm_transport_factory,
    get_provider_selection,
    get_runtime_config,
    get_store,
)
from holocron_core.services.document_import import ImportRuntime, SynchronousImportQueue, pending_page
from holocron_core.services.provider_selection import ProviderSelection
from tests.block_samples import extraction_for, geo_line, geometry
from tests.test_block_store import close, imported, layout, open_kind
from tests.vision_support import MODEL, NOW, SECRET, Switch, candidate, region_image

DOC = "doc_00000000000000e1"
REGION = (0.1, 0.1, 0.9, 0.3)
OCR_LINE = "Thanh ghi R13 chua gia tri 42 sau lenh MOV"
BASE = f"/v1/documents/{DOC}/pages/1/blocks"
VLM_ONLY = "CHU_CHI_VLM_DOC"


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("test không được gọi mạng")

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(http.client.HTTPConnection, "connect", refuse)


def chat_body(blocks: list[dict[str, object]]) -> bytes:
    content = json.dumps(blocks, ensure_ascii=False)
    body = {"id": "x", "choices": [{"index": 0, "message": {"role": "assistant", "content": content}}]}
    return json.dumps(body).encode("utf-8")


Outcome = tuple[int, bytes] | BaseException


class FakeTransports:
    """`TransportFactory` giả: ghi chính sách IP và mọi request; trả lần lượt các phản hồi (hết thì lặp phản hồi cuối)."""

    def __init__(self, *outcomes: Outcome) -> None:
        self.outcomes = list(outcomes)
        self.policies: list[IpPolicy] = []
        self.requests: list[urllib.request.Request] = []

    def __call__(self, policy: IpPolicy, limits: LlmHttpLimits) -> FakeTransports.Bound:
        self.policies.append(policy)
        return FakeTransports.Bound(self)

    class Bound:
        def __init__(self, owner: FakeTransports) -> None:
            self.owner = owner

        def __call__(self, request: urllib.request.Request, timeout_s: float) -> tuple[int, bytes]:
            self.owner.requests.append(request)
            outcome = self.owner.outcomes.pop(0) if len(self.owner.outcomes) > 1 else self.owner.outcomes[0]
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome


@dataclass
class FakeRenderer:
    """`RegionRenderer` giả: trả một PNG tự tạo cho mỗi vùng, hoặc lỗi khi `fail`."""

    fail: bool = False
    requests: list[CropRegion] = field(default_factory=list)

    def render_regions(self, path: Path, requests: Sequence[CropRegion], *, max_edge_px: int) -> list[CropOutcome]:
        self.requests.extend(requests)
        if self.fail:
            return [CropFailure(region.region_id, "render_error") for region in requests]
        image = region_image()
        return [
            RenderedCrop(region.region_id, image.png, image.width_px, image.height_px, image.sha256)
            for region in requests
        ]


class FakeFiles:
    def __init__(self, root: Path, *, present: bool = True) -> None:
        self.root = root
        self.present = present

    def path_for(self, sha256: str) -> Path:
        return self.root / f"{sha256}.pdf"

    def exists(self, sha256: str) -> bool:
        return self.present


def reread_block(
    flags: list[BlockFlag], *, kind: str = "code", bbox: tuple[float, float, float, float] | None = REGION
) -> Block:
    return Block.model_validate(
        {
            "block_id": "blk_00000000000000a1" if flags else "blk_00000000000000a2",
            "document_id": DOC,
            "page_number": 1,
            "type": kind,
            "bbox": bbox,
            "text": OCR_LINE,
            "line_refs": [WordRef(line_idx=0, word_idx=0)],
            "flags": flags,
            "source": "ocr",
            "reading_order": 0 if flags else 1,
        }
    )


ELIGIBLE = reread_block(["code_layout_uncertain"])
PLAIN = reread_block([], kind="paragraph", bbox=(0.1, 0.5, 0.9, 0.6))


def ocr_geometry(source: str = "ocr") -> PageGeometry:
    """Hình học một dòng chữ trong vùng. `source = "ocr"` ⇒ từ OCR độc lập, không có lớp chữ; `"text_layer"` ⇒ dòng
    lớp chữ cao, phủ phần lớn vùng (độ giao vượt ngưỡng 0,20)."""
    if source == "ocr":
        return geometry([geo_line(OCR_LINE, 0.12, 0.15)], source="ocr")
    return geometry([geo_line(OCR_LINE, 0.12, 0.12, height=0.18, char_w=0.02)])


def seed(store: AppStore, *, geo: PageGeometry | None = None, confirmed: bool = False) -> PageExtraction:
    store.create_imported_document(imported(DOC, pages=1), [pending_page(DOC, 1)])
    shape = geo or ocr_geometry()
    page = extraction_for(shape, 1, document_id=DOC).model_copy(update={"version": 2})
    if confirmed:
        page = page.model_copy(update={"confirmed": True, "confirmed_at": NOW, "confirmed_by": "user"})
    store.update_page(DOC, 1, page, status=page.status, confirmed=confirmed, expected_version=1, geometry=shape)
    store.save_document_layout(DOC, layout({1: [ELIGIBLE, PLAIN]}, doc_id=DOC))
    return page


@dataclass
class Env:
    client: TestClient
    store: AppStore
    transports: FakeTransports
    renderer: FakeRenderer
    selection: ProviderSelection
    consent: Switch

    def reread(self, block_id: str = ELIGIBLE.block_id) -> tuple[int, dict[str, object]]:
        response = self.client.post(f"{BASE}/{block_id}/ai-reread")
        return response.status_code, response.json()

    def page(self) -> PageExtraction | None:
        return self.store.get_page(DOC, 1, PageExtraction)


def make_env(
    store: AppStore,
    tmp_path: Path,
    *outcomes: Outcome,
    providers: list[ProviderCandidate] | None = None,
    mode: AiVlmMode = "unset",
    renderer: FakeRenderer | None = None,
    files_present: bool = True,
) -> Env:
    transports = FakeTransports(*outcomes) if outcomes else FakeTransports((500, b"{}"))
    chosen = [candidate()] if providers is None else providers
    selection = ProviderSelection(lambda: chosen, InMemorySpendLedger())
    consent = Switch(image=False)  # công tắc tắt: thao tác người dùng bấm vẫn chạy (ADR-009 mục 7)
    cropper = renderer or FakeRenderer()
    runtime = ImportRuntime(files=FakeFiles(tmp_path, present=files_present), queue=SynchronousImportQueue())
    app = FastAPI()
    app.include_router(documents.router)
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[documents.get_import_runtime] = lambda: runtime
    app.dependency_overrides[documents.get_region_renderer] = lambda: cropper
    app.dependency_overrides[documents.get_vlm_mode] = lambda: mode
    app.dependency_overrides[get_ai_consent] = lambda: consent
    app.dependency_overrides[get_provider_selection] = lambda: selection
    app.dependency_overrides[get_llm_transport_factory] = lambda: transports
    app.dependency_overrides[get_runtime_config] = lambda: load_runtime_config({})
    app.dependency_overrides[get_clock] = lambda: lambda: NOW
    return Env(TestClient(app), store, transports, cropper, selection, consent)


@pytest.fixture(params=("memory", "sqlite"))
def store(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[AppStore]:
    opened = open_kind(str(request.param), tmp_path)
    try:
        yield opened
    finally:
        close(opened)


MATCHING = [{"type": "code", "text": OCR_LINE}]


# --------------------------------------------------------------------------- #
# Đề xuất
# --------------------------------------------------------------------------- #


def test_ai_reread_proposal_does_not_write_store(store: AppStore, tmp_path: Path) -> None:
    before = seed(store)
    blocks_before = store.list_blocks(DOC, 1)
    env = make_env(store, tmp_path, (200, chat_body(MATCHING)))
    status, body = env.reread()
    assert status == 200, body
    proposal = AiRereadProposal.model_validate(body)
    assert proposal.status == "accepted_text" and proposal.reason == "ok"
    assert [(b.type, b.text) for b in proposal.blocks] == [("code", OCR_LINE)]
    assert proposal.page_version == before.version and proposal.block_id == ELIGIBLE.block_id
    assert proposal.ocr_text == OCR_LINE and proposal.recipient_label == "Gemini" and not proposal.from_cache
    assert proposal.input_sha256 == region_image().sha256
    # Không ghi kho trang: trang, version và khối giữ nguyên; đề xuất chỉ nằm ở kho tạm của store.
    assert env.page() == before and store.list_blocks(DOC, 1) == blocks_before
    record = store.get_ai_reread_proposal(proposal.proposal_id)
    assert record is not None and record.state == "pending" and record.proposal == proposal
    # Công tắc đồng ý tắt mà vẫn gửi đúng một ảnh vùng tới nhà cung cấp đã chọn qua transport có chặn địa chỉ.
    assert env.consent.calls == 0 and len(env.transports.requests) == 1 and env.transports.policies == ["operator"]
    assert [r.bbox for r in env.renderer.requests] == [REGION] and env.renderer.requests[0].page_index == 0


def test_ai_reread_cache_hit_reruns_gate(store: AppStore, tmp_path: Path) -> None:
    seed(store)
    env = make_env(store, tmp_path, (200, chat_body(MATCHING)))
    assert env.reread()[0] == 200
    status, body = env.reread()
    assert status == 200 and body["from_cache"] is True and body["status"] == "accepted_text"
    assert len(env.transports.requests) == 1


@pytest.mark.parametrize(
    ("reply", "geo_source", "reason"),
    [
        ([{"type": "code", "text": OCR_LINE.replace("42", "43") + f" {VLM_ONLY}"}], "ocr", "number_tokens"),
        ([{"type": "code", "text": f"{OCR_LINE} {VLM_ONLY} [không đọc được]"}], "ocr", "unreadable_marker"),
        ([{"type": "code", "text": f"{OCR_LINE} {VLM_ONLY}"}], "text_layer", "text_layer_overlap"),
        ([{"type": "table", "rows": [[VLM_ONLY]]}, {"type": "code", "text": VLM_ONLY}], "ocr", "mixed_table"),
        ([{"type": "figure", "text": f"Hình {VLM_ONLY}"}], "ocr", "figure_only"),
        ("không phải JSON", "ocr", "status_invalid"),
    ],
)
def test_ai_reread_gate_rejected_returns_200_without_vlm_text(
    store: AppStore, tmp_path: Path, reply: object, geo_source: str, reason: str
) -> None:
    before = seed(store, geo=ocr_geometry(geo_source))
    if isinstance(reply, list):
        body_bytes = chat_body(reply)
    else:
        body_bytes = json.dumps({"id": "x", "choices": [{"index": 0, "message": {"content": reply}}]}).encode()
    env = make_env(store, tmp_path, (200, body_bytes))
    status, body = env.reread()
    assert status == 200, body
    assert body["status"] == "rejected" and body["reason"] == reason
    assert body["blocks"] == [] and body["mismatches"] == []
    assert VLM_ONLY not in json.dumps(body, ensure_ascii=False)
    assert env.page() == before


def test_ai_reread_table_mismatch_is_reported_with_ids(store: AppStore, tmp_path: Path) -> None:
    seed(store)
    rows = [["Thanh ghi R13 chua", "gia tri 43"], ["sau lenh MOV", ""]]
    env = make_env(store, tmp_path, (200, chat_body([{"type": "table", "rows": rows}])))
    status, body = env.reread()
    assert status == 200 and body["status"] == "accepted_text"
    [block] = body["blocks"]
    assert block["type"] == "table" and block["text"] is None and "table_token_mismatch" in block["flags"]
    extra, missing = body["mismatches"]
    assert (extra["vlm_value"], extra["row"], extra["col"], extra["ocr_value"]) == ("43", 0, 1, None)
    assert (missing["ocr_value"], missing["row"], missing["vlm_value"]) == ("42", None, None)
    assert all(m["mismatch_id"].startswith("vmm_") for m in body["mismatches"])


def test_ai_reread_not_configured_and_budget_409(store: AppStore, tmp_path: Path) -> None:
    seed(store)
    cases: list[tuple[list[ProviderCandidate], AiVlmMode, str]] = [
        ([], "unset", "AI_NOT_CONFIGURED"),
        ([candidate(budget=0.0)], "unset", "AI_BUDGET_EXHAUSTED"),
        ([candidate()], "off", "AI_MODE_OFF"),
    ]
    for providers, mode, code in cases:
        env = make_env(store, tmp_path, (200, chat_body(MATCHING)), providers=providers, mode=mode)
        status, body = env.reread()
        assert (status, body["detail"]["code"]) == (409, code)
        assert isinstance(body["detail"]["message"], str) and body["detail"]["message"]
        assert env.transports.requests == [] and env.renderer.requests == []


@pytest.mark.parametrize(
    ("outcome", "code"),
    [
        ((429, b"{}"), "VLM_RATE_LIMITED"),
        (TimeoutError("hết giờ"), "VLM_TIMEOUT"),
        ((500, b"{}"), "VLM_UPSTREAM_ERROR"),
        ((401, b"{}"), "VLM_UPSTREAM_ERROR"),
    ],
)
def test_ai_reread_vlm_timeout_and_429_return_503(store: AppStore, tmp_path: Path, outcome: Outcome, code: str) -> None:
    before = seed(store)
    env = make_env(store, tmp_path, outcome)
    status, body = env.reread()
    assert (status, body["detail"]["code"]) == (503, code)
    assert env.page() == before


def test_ai_reread_ineligible_block_409(store: AppStore, tmp_path: Path) -> None:
    seed(store)
    env = make_env(store, tmp_path, (200, chat_body(MATCHING)))
    status, body = env.reread(PLAIN.block_id)  # khối không có cờ đọc lại
    assert (status, body["detail"]["code"]) == (409, "BLOCK_NOT_ELIGIBLE")
    assert env.client.post(f"{BASE}/blk_00000000000000ff/ai-reread").status_code == 404
    assert env.client.post(f"/v1/documents/{DOC}/pages/9/blocks/{ELIGIBLE.block_id}/ai-reread").status_code == 404
    broken_envs = (
        make_env(store, tmp_path, renderer=FakeRenderer(fail=True)),
        make_env(store, tmp_path, files_present=False),
    )
    for broken in broken_envs:
        assert broken.reread()[1]["detail"]["code"] == "BLOCK_NOT_ELIGIBLE"
        assert broken.transports.requests == []
    assert env.transports.requests == []


def test_ai_reread_confirmed_page_is_not_eligible(store: AppStore, tmp_path: Path) -> None:
    seed(store, confirmed=True)
    env = make_env(store, tmp_path, (200, chat_body(MATCHING)))
    status, body = env.reread()
    assert (status, body["detail"]["code"]) == (409, "BLOCK_NOT_ELIGIBLE")
    assert env.transports.requests == [] and env.renderer.requests == []


def test_ai_reread_key_never_in_response(store: AppStore, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    seed(store)
    caplog.set_level(logging.DEBUG)
    texts: list[str] = []
    for outcome in ((200, chat_body(MATCHING)), (429, b"{}"), (500, b"{}")):
        env = make_env(store, tmp_path, outcome)
        texts.append(env.client.post(f"{BASE}/{ELIGIBLE.block_id}/ai-reread").text)
    joined = "\n".join([*texts, caplog.text])
    for secret in (SECRET, MODEL, "vision.example.test"):
        assert secret not in joined


# --------------------------------------------------------------------------- #
# Bỏ đề xuất
# --------------------------------------------------------------------------- #


def test_ai_reread_reject_is_idempotent_and_writes_nothing(store: AppStore, tmp_path: Path) -> None:
    before = seed(store)
    env = make_env(store, tmp_path, (200, chat_body(MATCHING)))
    proposal_id = env.reread()[1]["proposal_id"]
    url = f"{BASE}/{ELIGIBLE.block_id}/ai-reread/{proposal_id}/reject"
    assert env.client.post(url).status_code == 204
    assert env.client.post(url).status_code == 204  # đã bỏ ⇒ vẫn 204
    record = store.get_ai_reread_proposal(str(proposal_id))
    assert record is not None and record.state == "discarded"
    assert env.page() == before  # không đổi version, không thêm vlm_rejected_block_ids
    assert env.client.post(f"{BASE}/{ELIGIBLE.block_id}/ai-reread/prp_000000000000/reject").status_code == 404
    assert env.client.post(f"{BASE}/{PLAIN.block_id}/ai-reread/{proposal_id}/reject").status_code == 404


def test_delete_document_drops_its_proposals(store: AppStore, tmp_path: Path) -> None:
    seed(store)
    env = make_env(store, tmp_path, (200, chat_body(MATCHING)))
    proposal_id = str(env.reread()[1]["proposal_id"])
    assert store.delete_document(DOC)
    assert store.get_ai_reread_proposal(proposal_id) is None
