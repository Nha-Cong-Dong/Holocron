"""Bộ đọc vùng VLM (ADR-009 mục 1, 2, 4, 5, 9): định tuyến theo công tắc/nhà cung cấp/trần, parse an toàn, lỗi lùi."""

from __future__ import annotations

import json
import logging
import unicodedata
import urllib.request
from collections.abc import Sequence

import pytest
from pydantic import SecretStr, ValidationError

from holocron_core.domain.ai import AiUsage, CostEstimate, estimate_cost_usd, usage_cost_usd
from holocron_core.domain.llm import (
    ChatCompletionResult,
    ChatMessage,
    ImageTooLargeError,
    ImageUrlPart,
    LlmTimeoutError,
    LlmUpstreamError,
    TextPart,
    TokenUsage,
)
from holocron_core.infrastructure.llm_config import ConfigError, VlmSettings
from holocron_core.infrastructure.vision_reader import (
    PROMPT_V1,
    PROMPT_VERSION,
    RETRY_REMINDER,
    RegionImage,
    VisionReaderSettings,
    VisionSkipped,
    VlmRegionBlock,
    VlmRegionResult,
    VlmTaskLimits,
    openai_client_factory,
    parse_vlm_blocks,
)
from tests.vision_support import DOC, MODEL, SECRET, Harness, StubClient, Switch, candidate, region_image

PARAGRAPH = json.dumps([{"type": "paragraph", "text": "Thanh ghi R13 = 42"}], ensure_ascii=False)


def _result(outcome: VlmRegionResult | VisionSkipped) -> VlmRegionResult:
    assert isinstance(outcome, VlmRegionResult)
    return outcome


# --------------------------------------------------------------------------- #
# Định tuyến: công tắc, nhà cung cấp, trần
# --------------------------------------------------------------------------- #


def test_consent_off_never_calls_transport_or_cache() -> None:
    harness = Harness([PARAGRAPH], consent=Switch(image=False))
    assert harness.reader.read_region(DOC, 1, region_image()) == VisionSkipped(reason="consent_off")
    assert harness.client.received == [] and harness.built == []


def test_consent_is_read_on_every_call() -> None:
    switch = Switch()
    harness = Harness([PARAGRAPH, PARAGRAPH], consent=switch)
    assert _result(harness.reader.read_region(DOC, 1, region_image(shade=1))).status == "ok"
    switch.image = False
    assert harness.reader.read_region(DOC, 1, region_image(shade=2)) == VisionSkipped(reason="consent_off")
    assert switch.calls == 2 and len(harness.client.received) == 1


def test_no_provider_or_exhausted_budget_never_calls() -> None:
    assert Harness([PARAGRAPH], providers=[]).reader.read_region(DOC, 1, region_image()) == VisionSkipped(
        reason="no_provider"
    )
    harness = Harness([PARAGRAPH], providers=[candidate(budget=0.0)])
    assert harness.reader.read_region(DOC, 1, region_image()) == VisionSkipped(reason="no_provider")
    assert harness.client.received == []


def test_region_caps_per_page_and_document() -> None:
    settings = VisionReaderSettings(max_regions_per_page=1, max_regions_per_document=2)
    harness = Harness([PARAGRAPH] * 3, settings=settings)
    assert _result(harness.reader.read_region(DOC, 1, region_image(shade=1))).status == "ok"
    assert harness.reader.read_region(DOC, 1, region_image(shade=2)) == VisionSkipped(reason="region_cap")
    assert _result(harness.reader.read_region(DOC, 2, region_image(shade=3))).status == "ok"
    assert harness.reader.read_region(DOC, 3, region_image(shade=4)) == VisionSkipped(reason="region_cap")
    assert len(harness.client.received) == 2
    assert harness.ledger.reserved_usd("env_vlm") == 0.0


# --------------------------------------------------------------------------- #
# Ảnh: kiểm trước khi gửi
# --------------------------------------------------------------------------- #


def _tampered(image: RegionImage, **update: object) -> RegionImage:
    return image.model_copy(update=update)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda image: _tampered(image, sha256="0" * 64),
        lambda image: _tampered(image, width_px=image.width_px + 1),
        lambda image: _tampered(image, png=b"GIF89a" + image.png[6:]),
    ],
    ids=["sha", "ihdr", "magic"],
)
def test_bad_image_is_rejected_before_sending(mutate: object) -> None:
    assert callable(mutate)
    harness = Harness([PARAGRAPH])
    outcome = harness.reader.read_region(DOC, 1, mutate(region_image()))
    assert outcome == VisionSkipped(reason="image_rejected") and harness.client.received == []


def test_image_over_effective_byte_cap_or_edge_is_rejected_before_sending() -> None:
    image = region_image(width=200, height=10)
    small = Harness([PARAGRAPH], settings=VisionReaderSettings(max_image_bytes=len(image.png) - 1))
    assert small.reader.read_region(DOC, 1, image) == VisionSkipped(reason="image_rejected")
    narrow = Harness([PARAGRAPH], settings=VisionReaderSettings(max_edge_px=128))
    assert narrow.reader.read_region(DOC, 1, image) == VisionSkipped(reason="image_rejected")
    assert small.client.received == [] and narrow.client.received == []


def test_effective_image_cap_is_min_of_env_and_crop_cap() -> None:
    from holocron_core.infrastructure.document_readers.config import PdfReaderConfig

    vlm = VlmSettings(base_url="https://v.example.test/v1", api_key=SecretStr(SECRET), model=MODEL)
    settings = VisionReaderSettings.from_config(vlm, PdfReaderConfig())
    assert settings.max_image_bytes == 1024 * 1024 and settings.max_edge_px == 1024


# --------------------------------------------------------------------------- #
# Request: chỉ prompt + ảnh; parse và gọi lại
# --------------------------------------------------------------------------- #


def test_request_has_only_prompt_and_image_no_ocr_text() -> None:
    harness = Harness([PARAGRAPH])
    image = region_image()
    _result(harness.reader.read_region(DOC, 1, image))
    (messages,) = harness.client.received
    (message,) = messages
    assert message.role == "user" and isinstance(message.content, list)
    text, picture = message.content
    assert isinstance(text, TextPart) and text.text == PROMPT_V1
    assert isinstance(picture, ImageUrlPart) and picture.image_url.decoded_size == len(image.png)
    assert PROMPT_VERSION == "v1" and PROMPT_V1.startswith("Bạn chép lại nội dung")


def test_ok_result_maps_types_and_normalizes_nfc() -> None:
    nfd = unicodedata.normalize("NFD", "Tiếng Việt")
    reply = json.dumps(
        [
            {"type": "title", "text": nfd, "extra": 1},
            {"type": "list", "text": "- một"},
            {"type": "table", "rows": [["a", "1"], ["b", "2"]]},
        ],
        ensure_ascii=False,
    )
    result = _result(Harness([reply]).reader.read_region(DOC, 1, region_image()))
    assert result.status == "ok" and result.attempts == 1 and not result.from_cache
    assert [(block.type, block.order) for block in result.blocks] == [("heading", 0), ("list_item", 1), ("table", 2)]
    assert result.blocks[0].text == "Tiếng Việt" and result.blocks[2].rows == [["a", "1"], ["b", "2"]]
    assert result.blocks[2].text is None


def test_vlm_figure_description_never_in_block_text() -> None:
    reply = json.dumps([{"type": "figure", "text": "Sơ đồ mô tả do VLM viết. Nhãn: CPU"}], ensure_ascii=False)
    result = _result(Harness([reply]).reader.read_region(DOC, 1, region_image()))
    assert result.blocks[0].type == "figure" and result.blocks[0].text is None and result.blocks[0].rows is None


def test_code_fenced_json_is_accepted() -> None:
    reply = "```json\n" + PARAGRAPH + "\n```"
    assert _result(Harness([reply]).reader.read_region(DOC, 1, region_image())).status == "ok"


def test_broken_reply_is_retried_once_with_the_fixed_reminder() -> None:
    harness = Harness(["không phải JSON", PARAGRAPH])
    result = _result(harness.reader.read_region(DOC, 1, region_image()))
    assert result.status == "ok" and result.attempts == 2
    first, second = harness.client.received
    assert len(first) == len(second) == 1
    assert isinstance(second[0].content, list) and isinstance(first[0].content, list)
    assert second[0].content[:2] == first[0].content
    reminder = second[0].content[2]
    assert isinstance(reminder, TextPart) and reminder.text == RETRY_REMINDER
    assert all("không phải JSON" not in str(part) for part in second[0].content)


@pytest.mark.parametrize(
    "reply",
    [
        "[{",
        json.dumps([{"type": "formula", "text": "x"}]),
        json.dumps([{"type": "paragraph"}]),
        json.dumps([{"type": "paragraph", "text": "x", "rows": [["a"]]}]),
        json.dumps([{"type": "table", "rows": "a"}]),
        json.dumps({"type": "paragraph", "text": "x"}),
        "[" * 40 + "]" * 40,
        json.dumps([{"type": "paragraph", "text": "x" * 200_000}]),
    ],
    ids=["truncated", "unknown-type", "no-text", "rows-on-text", "bad-rows", "object", "too-deep", "too-long"],
)
def test_invalid_twice_is_vlm_invalid(reply: str) -> None:
    harness = Harness([reply, reply])
    result = _result(harness.reader.read_region(DOC, 1, region_image()))
    assert result.status == "vlm_invalid" and result.blocks == [] and result.attempts == 2


def test_too_many_blocks_or_table_cells_are_rejected() -> None:
    limits = VlmTaskLimits(max_blocks_per_region=2, max_table_rows=2, max_table_cols=2)
    many = json.dumps([{"type": "paragraph", "text": "x"}] * 3)
    wide = json.dumps([{"type": "table", "rows": [["1", "2", "3"]]}])
    tall = json.dumps([{"type": "table", "rows": [["1"], ["2"], ["3"]]}])
    assert parse_vlm_blocks(many, limits) is None
    assert parse_vlm_blocks(wide, limits) is None and parse_vlm_blocks(tall, limits) is None


# --------------------------------------------------------------------------- #
# Lỗi mạng: lùi, không ném; chi phí
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (LlmTimeoutError("hết hạn thời gian tổng của lần gọi LLM", attempts=1), "timeout"),
        (LlmUpstreamError("LLM không phản hồi được sau 3 lần thử (HTTP 429)", attempts=3), "upstream_error"),
    ],
)
def test_timeout_and_429_fall_back_without_raising(error: LlmUpstreamError, status: str) -> None:
    harness = Harness([error])
    result = _result(harness.reader.read_region(DOC, 1, region_image()))
    assert result.status == status and result.blocks == []
    assert harness.ledger.reserved_usd("env_vlm") == 0.0 and harness.ledger.spent_usd("env_vlm") > 0.0


def test_usage_is_recorded_from_the_provider() -> None:
    harness = Harness([PARAGRAPH], usage=TokenUsage(prompt_tokens=1000, completion_tokens=1000))
    _result(harness.reader.read_region(DOC, 1, region_image()))
    assert harness.ledger.spent_usd("env_vlm") == pytest.approx(0.001 + 0.001 + 0.001)
    assert harness.ledger.reserved_usd("env_vlm") == 0.0


def test_key_endpoint_and_model_never_logged(caplog: pytest.LogCaptureFixture) -> None:
    def refuse(request: urllib.request.Request, timeout_s: float) -> tuple[int, bytes]:
        return 429, b"{}"

    vlm = VlmSettings(base_url="https://vision.example.test/v1", api_key=SecretStr(SECRET), model=MODEL)
    harness = Harness(client_factory=openai_client_factory(vlm, 1024 * 1024, transport=refuse, sleep=lambda _: None))
    with caplog.at_level(logging.DEBUG):
        result = _result(harness.reader.read_region(DOC, 1, region_image()))
    assert result.status == "upstream_error"
    for text in (caplog.text, repr(result), repr(harness.providers[0])):
        assert SECRET not in text and "vision.example.test" not in text and MODEL not in text


def test_budget_release_on_presend_error_and_estimate_on_postsend_error() -> None:
    def broken(provider: object) -> StubClient:
        raise ConfigError("HOLOCRON_VLM_BASE_URL không hợp lệ")

    presend = Harness([PARAGRAPH], client_factory=broken)
    assert _result(presend.reader.read_region(DOC, 1, region_image())).status == "upstream_error"
    assert presend.ledger.reserved_usd("env_vlm") == 0.0 and presend.ledger.spent_usd("env_vlm") == 0.0
    unsent = Harness([LlmUpstreamError("không dựng được yêu cầu", attempts=0)])
    _result(unsent.reader.read_region(DOC, 1, region_image()))
    assert unsent.ledger.spent_usd("env_vlm") == 0.0 and unsent.ledger.reserved_usd("env_vlm") == 0.0
    postsend = Harness([LlmUpstreamError("LLM từ chối yêu cầu (HTTP 500)", attempts=1)])
    _result(postsend.reader.read_region(DOC, 1, region_image()))
    assert postsend.ledger.spent_usd("env_vlm") > 0.0 and postsend.ledger.reserved_usd("env_vlm") == 0.0


def test_cache_hit_and_region_cap_release_the_reservation() -> None:
    harness = Harness([PARAGRAPH], settings=VisionReaderSettings(max_regions_per_document=1))
    image = region_image()
    _result(harness.reader.read_region(DOC, 1, image))
    spent = harness.ledger.spent_usd("env_vlm")
    assert harness.reader.read_region(DOC, 2, image) == VisionSkipped(reason="region_cap")
    assert harness.ledger.reserved_usd("env_vlm") == 0.0 and harness.ledger.spent_usd("env_vlm") == spent


# --------------------------------------------------------------------------- #
# Review V02: validator khối, cache, bộ đếm vùng, consent khi gửi lại, parse
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "fields",
    [
        {"type": "paragraph", "text": "x", "rows": [["a"]], "order": 0},
        {"type": "paragraph", "text": None, "rows": None, "order": 0},
        {"type": "table", "text": None, "rows": None, "order": 0},
        {"type": "table", "text": "x", "rows": [["a"]], "order": 0},
        {"type": "figure", "text": "mô tả", "rows": None, "order": 0},
        {"type": "figure", "text": None, "rows": [["a"]], "order": 0},
    ],
    ids=["rows-on-text", "text-missing", "table-no-rows", "table-text", "figure-text", "figure-rows"],
)
def test_region_block_shape_validator(fields: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        VlmRegionBlock.model_validate(fields)


def test_parse_accepts_values_exactly_at_each_limit() -> None:
    limits = VlmTaskLimits(max_blocks_per_region=2, max_table_rows=2, max_table_cols=2, max_text_chars=5)
    both = [{"type": "table", "rows": [["12345", "b"], ["c", "d"]]}, {"type": "paragraph", "text": "abcde"}]
    blocks = parse_vlm_blocks(json.dumps(both), limits)
    assert blocks is not None and len(blocks) == 2 and blocks[1].text == "abcde"
    assert parse_vlm_blocks(json.dumps([{"type": "paragraph", "text": "abcdef"}]), limits) is None
    assert parse_vlm_blocks(json.dumps([{"type": "table", "rows": [["123456"]]}]), limits) is None
    exact = json.dumps([{"type": "paragraph", "text": "x"}])
    assert parse_vlm_blocks(exact, VlmTaskLimits(max_response_chars=len(exact))) is not None
    assert parse_vlm_blocks(exact, VlmTaskLimits(max_response_chars=len(exact) - 1)) is None
    deep = json.dumps([{"type": "table", "rows": [["a"]]}])  # độ sâu 4: mảng, đối tượng, rows, hàng
    assert parse_vlm_blocks(deep, VlmTaskLimits(max_json_depth=4)) is not None
    assert parse_vlm_blocks(deep, VlmTaskLimits(max_json_depth=3)) is None


def test_parse_normalizes_table_cells_to_nfc() -> None:
    nfd = unicodedata.normalize("NFD", "Giá trị")
    blocks = parse_vlm_blocks(json.dumps([{"type": "table", "rows": [[nfd]]}], ensure_ascii=False), VlmTaskLimits())
    assert blocks is not None and blocks[0].rows == [["Giá trị"]]


def test_parse_skips_early_non_block_array_and_respects_start_cap() -> None:
    blocks = parse_vlm_blocks("Có [1] khối sau: " + PARAGRAPH, VlmTaskLimits())
    assert blocks is not None and blocks[0].text == "Thanh ghi R13 = 42"
    assert parse_vlm_blocks("[x [y " + PARAGRAPH, VlmTaskLimits(max_parse_starts=2)) is None
    assert parse_vlm_blocks("[x [y " + PARAGRAPH, VlmTaskLimits(max_parse_starts=3)) is not None
    unknown = json.dumps([{"type": "formula", "text": "x"}])
    assert parse_vlm_blocks("[1] " + unknown, VlmTaskLimits()) is None


def test_image_exactly_at_byte_and_edge_caps_is_sent() -> None:
    image = region_image(width=128, height=10)
    harness = Harness([PARAGRAPH], settings=VisionReaderSettings(max_image_bytes=len(image.png), max_edge_px=128))
    assert _result(harness.reader.read_region(DOC, 1, image)).status == "ok"


def test_cache_hit_does_not_consume_region_cap() -> None:
    harness = Harness([PARAGRAPH, PARAGRAPH], settings=VisionReaderSettings(max_regions_per_document=2))
    image = region_image(shade=1)
    _result(harness.reader.read_region(DOC, 1, image))
    for page in (2, 3, 4):
        assert _result(harness.reader.read_region(DOC, page, image)).from_cache
    assert _result(harness.reader.read_region(DOC, 5, region_image(shade=2))).status == "ok"
    assert harness.reader.read_region(DOC, 6, region_image(shade=3)) == VisionSkipped(reason="region_cap")
    assert len(harness.client.received) == 2


def test_begin_document_resets_and_tracking_is_bounded() -> None:
    settings = VisionReaderSettings(max_regions_per_document=1, max_tracked_documents=2)
    harness = Harness([PARAGRAPH] * 6, settings=settings)
    _result(harness.reader.read_region(DOC, 1, region_image(shade=1)))
    assert harness.reader.read_region(DOC, 1, region_image(shade=2)) == VisionSkipped(reason="region_cap")
    harness.reader.begin_document(DOC)
    assert _result(harness.reader.read_region(DOC, 1, region_image(shade=3))).status == "ok"
    for index in range(4):
        _result(harness.reader.read_region(f"doc_{index:016x}", 1, region_image(shade=10 + index)))
        assert harness.reader.tracked_documents() <= 2


def test_consent_is_reread_before_the_json_retry() -> None:
    switch = Switch()

    class OffAfterFirst(StubClient):
        def complete(self, messages: Sequence[ChatMessage], *, max_tokens: int) -> ChatCompletionResult:
            switch.image = False
            return super().complete(messages, max_tokens=max_tokens)

    client = OffAfterFirst(["không phải JSON", PARAGRAPH])
    harness = Harness(consent=switch, client_factory=lambda provider: client)
    result = _result(harness.reader.read_region(DOC, 1, region_image()))
    assert result.status == "vlm_invalid" and len(client.received) == 1
    assert harness.ledger.reserved_usd("env_vlm") == 0.0


def _estimate_input_tokens() -> int:
    return (len(PROMPT_V1) + len(RETRY_REMINDER)) // 2 + 1


def test_reservation_uses_two_chars_per_token_estimate() -> None:
    seen: list[float] = []
    holder: list[Harness] = []

    class Spy(StubClient):
        def complete(self, messages: Sequence[ChatMessage], *, max_tokens: int) -> ChatCompletionResult:
            seen.append(holder[0].ledger.reserved_usd("env_vlm"))
            return super().complete(messages, max_tokens=max_tokens)

    spy = Spy([PARAGRAPH], usage=TokenUsage(prompt_tokens=1, completion_tokens=1))
    holder.append(Harness(client_factory=lambda provider: spy))
    _result(holder[0].reader.read_region(DOC, 1, region_image()))
    estimate = CostEstimate(input_tokens=_estimate_input_tokens(), max_output_tokens=4096, images=1)
    assert seen == [pytest.approx(estimate_cost_usd(candidate().prices, estimate))]


def test_missing_usage_records_worst_case_cost() -> None:
    harness = Harness([PARAGRAPH], usage=None)
    _result(harness.reader.read_region(DOC, 1, region_image()))
    worst = AiUsage(input_tokens=_estimate_input_tokens(), output_tokens=4096, images=1, estimated=True)
    assert harness.ledger.spent_usd("env_vlm") == pytest.approx(usage_cost_usd(candidate().prices, worst))
    assert harness.ledger.reserved_usd("env_vlm") == 0.0


def test_image_too_large_in_client_releases_reservation() -> None:
    class Refuse(StubClient):
        def complete(self, messages: Sequence[ChatMessage], *, max_tokens: int) -> ChatCompletionResult:
            raise ImageTooLargeError("HOLOCRON_VLM_MAX_IMAGE_BYTES")

    harness = Harness(client_factory=lambda provider: Refuse([]))
    assert _result(harness.reader.read_region(DOC, 1, region_image())).status == "upstream_error"
    assert harness.ledger.reserved_usd("env_vlm") == 0.0 and harness.ledger.spent_usd("env_vlm") == 0.0


def test_upstream_error_with_deadline_text_is_not_a_timeout() -> None:
    error = LlmUpstreamError("hết hạn thời gian tổng của lần gọi LLM", attempts=1)
    assert _result(Harness([error]).reader.read_region(DOC, 1, region_image())).status == "upstream_error"
