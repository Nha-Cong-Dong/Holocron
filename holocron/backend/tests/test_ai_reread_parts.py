"""Các phần của đọc lại bằng AI (SPECIFICATION §6.10a): độ giao lớp chữ, chữ OCR của vùng, kho tạm đề xuất, phân
loại lỗi 429 của adapter và đường đọc do người dùng bấm của `VisionReader`. Không gọi mạng."""

from __future__ import annotations

import math
import urllib.request

import pytest
from pydantic import SecretStr

from holocron_core.domain.ai_reread import MAX_AI_REREAD_PROPOSALS, AiRereadProposal
from holocron_core.domain.llm import ChatMessage, LlmRateLimitedError, LlmUpstreamError, TextPart
from holocron_core.infrastructure.ai_reread_proposals import AiRereadProposalCache
from holocron_core.infrastructure.llm_client import OpenAiChatClient
from holocron_core.infrastructure.vision_reader import VisionReaderSettings, VisionSkipped, VlmRegionResult
from holocron_core.services.ai_reread import region_ocr_text, text_layer_overlap, union_area
from tests.block_samples import geo_line, geometry
from tests.vision_support import DOC, Harness, Switch, region_image

REGION = (0.0, 0.0, 0.5, 0.5)


# --------------------------------------------------------------------------- #
# Hình học của vùng
# --------------------------------------------------------------------------- #


def test_union_area_counts_overlap_once() -> None:
    assert union_area([]) == 0.0
    assert union_area([(0.0, 0.0, 0.2, 0.2), (0.1, 0.1, 0.3, 0.3)]) == pytest.approx(0.04 + 0.04 - 0.01)
    assert union_area([(0.0, 0.0, 0.4, 0.1), (0.1, 0.0, 0.2, 0.1)]) == pytest.approx(0.04)
    assert union_area([(0.0, 0.0, 0.1, 0.1), (0.5, 0.5, 0.6, 0.6)]) == pytest.approx(0.02)


def test_text_layer_overlap_clips_to_region_and_fails_closed() -> None:
    geo = geometry([geo_line("aaaaa", 0.0, 0.0, height=0.25, char_w=0.2)])  # một từ phủ (0, 0)–(1, 0.25)
    assert text_layer_overlap(geo, REGION) == pytest.approx(0.5)
    assert text_layer_overlap(geometry([geo_line("x", 0.8, 0.8)]), REGION) == 0.0
    assert text_layer_overlap(geometry([geo_line("x", 0.1, 0.1)], source="ocr"), REGION) == 0.0
    assert math.isnan(text_layer_overlap(None, REGION))
    assert math.isnan(text_layer_overlap(geo, (0.2, 0.2, 0.2, 0.4)))


def test_region_ocr_text_uses_independent_ocr_words_inside_region() -> None:
    lines = [geo_line("trong vung", 0.05, 0.1), geo_line("ngoai vung", 0.05, 0.8), geo_line("dong hai", 0.05, 0.2)]
    assert region_ocr_text(geometry(lines, source="ocr"), REGION) == "trong vung\ndong hai"
    # Trang lớp chữ: dòng lớp chữ KHÔNG phải OCR độc lập; chỉ `ocr_lines` được dùng.
    text_layer = geometry(lines)
    assert region_ocr_text(text_layer, REGION) is None
    with_ocr = text_layer.model_copy(update={"ocr_lines": [geo_line("chu ocr", 0.1, 0.1)]})
    assert region_ocr_text(with_ocr, REGION) == "chu ocr"
    assert region_ocr_text(None, REGION) is None


# --------------------------------------------------------------------------- #
# Kho tạm đề xuất
# --------------------------------------------------------------------------- #


def _proposal(index: int, document_id: str = "doc_00000000000000e1") -> AiRereadProposal:
    return AiRereadProposal(
        proposal_id=f"prp_{index:012x}",
        document_id=document_id,
        page_number=1,
        block_id="blk_00000000000000a1",
        page_version=2,
        status="rejected",
        reason="ocr_missing",
        blocks=[],
        mismatches=[],
        ocr_text="",
        input_sha256="0" * 64,
        from_cache=False,
        recipient_label="Gemini",
    )


def test_proposal_cache_is_bounded_and_drops_oldest() -> None:
    cache = AiRereadProposalCache()
    for index in range(MAX_AI_REREAD_PROPOSALS + 3):
        cache.put(_proposal(index))
    assert len(cache) == MAX_AI_REREAD_PROPOSALS
    assert cache.get(_proposal(0).proposal_id) is None and cache.get(_proposal(2).proposal_id) is None
    assert cache.get(_proposal(3).proposal_id) is not None


def test_proposal_cache_discard_and_drop_document() -> None:
    cache = AiRereadProposalCache(max_entries=4)
    cache.put(_proposal(1))
    cache.put(_proposal(2, "doc_00000000000000e2"))
    assert cache.discard(_proposal(1).proposal_id) and not cache.discard("prp_ffffffffffff")
    record = cache.get(_proposal(1).proposal_id)
    assert record is not None and record.state == "discarded"
    cache.drop_document("doc_00000000000000e2")
    assert cache.get(_proposal(2).proposal_id) is None and len(cache) == 1
    with pytest.raises(ValueError):
        AiRereadProposalCache(max_entries=0)


@pytest.mark.parametrize(
    ("status", "blocks", "reason", "mismatches"),
    [
        ("accepted_text", [], "ok", []),
        ("rejected", [{"type": "code", "text": "x", "rows": None, "flags": []}], "cer", []),
        ("accepted_text", [{"type": "code", "text": "x", "rows": None, "flags": []}], "cer", []),
        (
            "accepted_text",
            [{"type": "code", "text": "x", "rows": None, "flags": []}],
            "ok",
            [{"mismatch_id": "vmm_000000000000", "token_kind": "number", "ocr_value": "1"}],
        ),
    ],
)
def test_proposal_shape_validator(status: str, blocks: list[object], reason: str, mismatches: list[object]) -> None:
    data = {**_proposal(1).model_dump(), "status": status, "blocks": blocks, "reason": reason, "mismatches": mismatches}
    with pytest.raises(ValueError):
        AiRereadProposal.model_validate(data)


# --------------------------------------------------------------------------- #
# 429 và đường đọc do người dùng bấm
# --------------------------------------------------------------------------- #


def test_final_429_raises_rate_limited_and_other_failures_do_not() -> None:
    def client(*statuses: int) -> OpenAiChatClient:
        replies = list(statuses)

        def transport(request: urllib.request.Request, timeout_s: float) -> tuple[int, bytes]:
            return replies.pop(0), b"{}"

        return OpenAiChatClient(
            base_url="https://llm.example.test/v1",
            api_key=SecretStr("sk-test-0123456789abcdef"),
            model="m",
            timeout_s=5.0,
            retry_max=1,
            transport=transport,
            sleep=lambda _s: None,
        )

    message = [ChatMessage(role="user", content=[TextPart(text="x")])]
    with pytest.raises(LlmRateLimitedError):
        client(500, 429).complete(message, max_tokens=8)
    for statuses in ((429, 500), (400,)):
        with pytest.raises(LlmUpstreamError) as caught:
            client(*statuses).complete(message, max_tokens=8)
        assert not isinstance(caught.value, LlmRateLimitedError)


def test_rate_limited_error_sets_rate_limited_flag() -> None:
    harness = Harness([LlmRateLimitedError("HTTP 429", attempts=3)])
    result = harness.reader.read_region_on_request(DOC, region_image())
    assert isinstance(result, VlmRegionResult) and result.status == "upstream_error" and result.rate_limited
    assert result.blocks == [] and result.attempts == 1
    plain = Harness([LlmUpstreamError("HTTP 500", attempts=3)]).reader.read_region_on_request(DOC, region_image())
    assert isinstance(plain, VlmRegionResult) and not plain.rate_limited


def test_on_request_read_ignores_consent_and_region_caps() -> None:
    reply = '[{"type": "paragraph", "text": "Mot dong"}]'
    switch = Switch(image=False)
    harness = Harness(
        [reply, reply],
        consent=switch,
        settings=VisionReaderSettings(max_regions_per_page=1, max_regions_per_document=1),
    )
    first = harness.reader.read_region_on_request(DOC, region_image(shade=10))
    second = harness.reader.read_region_on_request(DOC, region_image(shade=20))
    assert isinstance(first, VlmRegionResult) and isinstance(second, VlmRegionResult)
    assert first.status == second.status == "ok" and switch.calls == 0
    # Lần gửi tự động vẫn bị công tắc chặn như cũ.
    assert harness.reader.read_region(DOC, 1, region_image(shade=30)) == VisionSkipped(reason="consent_off")


def test_on_request_retry_does_not_reread_consent() -> None:
    switch = Switch(image=False)
    harness = Harness(["không phải JSON", '[{"type": "paragraph", "text": "Mot dong"}]'], consent=switch)
    result = harness.reader.read_region_on_request(DOC, region_image())
    assert isinstance(result, VlmRegionResult) and result.status == "ok" and result.attempts == 2
    assert switch.calls == 0


def test_on_request_read_still_checks_image_and_provider() -> None:
    bad = region_image().model_copy(update={"sha256": "0" * 64})
    assert Harness().reader.read_region_on_request(DOC, bad) == VisionSkipped(reason="image_rejected")
    assert Harness(providers=[]).reader.read_region_on_request(DOC, region_image()) == VisionSkipped(
        reason="no_provider"
    )
