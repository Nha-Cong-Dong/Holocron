"""Cổng nhận chữ VLM (ADR-009 mục 3; I3, I5): chỉ nhận khi khớp OCR độc lập, token số/mã tuyệt đối, bảng lệch BLOCKING."""

from __future__ import annotations

import random
import time
import unicodedata

import pytest

from holocron_core.infrastructure.vision_reader import VlmRegionBlock, VlmRegionResult, VlmStatus
from holocron_core.services.vision_gate import (
    GateTokenMismatch,
    VisionGateSettings,
    judge_region,
    levenshtein,
    mismatch_ids,
    within_edit_distance,
)

SHA = "a" * 64
OCR = "Thanh ghi R13 chứa giá trị 42 sau lệnh MOV"


def _result(*blocks: VlmRegionBlock, status: VlmStatus = "ok") -> VlmRegionResult:
    return VlmRegionResult(input_sha256=SHA, prompt_version="v1", status=status, blocks=list(blocks), attempts=1)


def _text(text: str, kind: str = "paragraph", order: int = 0) -> VlmRegionBlock:
    return VlmRegionBlock.model_validate({"type": kind, "text": text, "rows": None, "order": order})


def _table(rows: list[list[str]], order: int = 0) -> VlmRegionBlock:
    return VlmRegionBlock(type="table", text=None, rows=rows, order=order)


def _figure(order: int = 0) -> VlmRegionBlock:
    return VlmRegionBlock(type="figure", text=None, rows=None, order=order)


# --------------------------------------------------------------------------- #
# Nhận chữ (I3: chữ chỉ thành văn bản khối SAU cổng)
# --------------------------------------------------------------------------- #


def test_matching_text_is_accepted_as_vlm_source() -> None:
    decision = judge_region(_result(_text(OCR)), OCR, 0.0)
    assert decision.outcome == "accepted_text" and decision.block_type == "paragraph"
    assert decision.text == OCR and decision.flags == []


def test_vlm_text_only_becomes_quote_after_gate() -> None:
    rejected = judge_region(_result(_text("Thanh ghi R13 chứa giá trị 43 sau lệnh MOV")), OCR, 0.0)
    assert rejected.outcome == "rejected" and rejected.text is None and rejected.rows is None
    assert rejected.flags == ["vlm_gate_rejected"]
    accepted = judge_region(_result(_text(unicodedata.normalize("NFD", OCR))), OCR, 0.0)
    assert accepted.text is not None and unicodedata.normalize("NFC", OCR) in accepted.text


def test_several_text_blocks_join_in_order_as_uncertain_paragraph() -> None:
    blocks = (_text("sau lệnh MOV", order=1), _text("Thanh ghi R13 chứa giá trị 42", "heading", order=0))
    decision = judge_region(_result(*blocks), OCR, 0.0)
    assert decision.outcome == "accepted_text" and decision.block_type == "paragraph"
    assert decision.text == "Thanh ghi R13 chứa giá trị 42\nsau lệnh MOV"
    assert decision.flags == ["layout_uncertain"]


def test_single_block_keeps_its_type() -> None:
    decision = judge_region(_result(_text(OCR, "code")), OCR, 0.0)
    assert decision.block_type == "code"


# --------------------------------------------------------------------------- #
# Từ chối (I5: số/mã sai không bao giờ lọt)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "vlm",
    [
        "Thanh ghi R13 chứa giá trị 43 sau lệnh MOV",  # số lệch 1 ký tự
        "Thanh ghi R13 chứa giá trị 4 2 sau lệnh MOV",
        "Thanh ghi R1 chứa giá trị 42 sau lệnh MOV",  # mã (và số) lệch
        "Thanh ghi R13 chứa giá trị 42 sau lệnh MVN",  # mã lệch
    ],
)
def test_vision_gate_rejects_number_or_code_token_mismatch(vlm: str) -> None:
    decision = judge_region(_result(_text(vlm)), OCR, 0.0)
    assert decision.outcome == "rejected" and decision.reason in ("number_tokens", "code_tokens", "cer")
    assert decision.text is None


def test_number_token_mismatch_is_caught_even_with_generous_thresholds() -> None:
    loose = VisionGateSettings(max_cer=1.0, min_word_jaccard=0.0)
    decision = judge_region(_result(_text("Thanh ghi R13 chứa giá trị 43 sau lệnh MOV")), OCR, 0.0, loose)
    assert decision.reason == "number_tokens"
    code = judge_region(_result(_text("Thanh ghi R13 chứa giá trị 42 sau lệnh MVN")), OCR, 0.0, loose)
    assert code.reason == "code_tokens"


def test_below_thresholds_is_rejected() -> None:
    far = judge_region(_result(_text("Một câu hoàn toàn khác không liên quan R13 42 MOV")), OCR, 0.0)
    assert far.outcome == "rejected" and far.reason in ("cer", "jaccard")
    strict = VisionGateSettings(max_cer=1.0, min_word_jaccard=0.99)
    shuffled = judge_region(_result(_text("Thanh ghi R13 chứa 42 sau lệnh MOV")), OCR, 0.0, strict)
    assert shuffled.reason == "jaccard"


@pytest.mark.parametrize("ocr", [None, "", "   \n "])
def test_vision_gate_rejects_without_independent_ocr(ocr: str | None) -> None:
    decision = judge_region(_result(_text(OCR)), ocr, 0.0)
    assert decision.outcome == "rejected" and decision.reason == "ocr_missing" and decision.text is None


def test_vlm_text_rejected_on_text_layer_region() -> None:
    decision = judge_region(_result(_text(OCR)), OCR, 0.20)
    assert decision.outcome == "keep_offline" and decision.reason == "text_layer_overlap"
    assert decision.text is None and decision.flags == []
    assert judge_region(_result(_text(OCR)), OCR, 0.19).outcome == "accepted_text"


def test_unreadable_marker_is_rejected() -> None:
    decision = judge_region(_result(_text("Thanh ghi [không đọc được] 42")), "Thanh ghi R13 42", 0.0)
    assert decision.outcome == "rejected" and decision.reason == "unreadable_marker"


def test_too_long_region_is_rejected() -> None:
    settings = VisionGateSettings(max_region_chars=10)
    assert judge_region(_result(_text(OCR)), OCR, 0.0, settings).reason == "too_long"


@pytest.mark.parametrize(
    ("status", "flags"), [("vlm_invalid", ["vlm_invalid"]), ("timeout", []), ("upstream_error", [])]
)
def test_failed_vlm_keeps_offline_block(status: VlmStatus, flags: list[str]) -> None:
    decision = judge_region(_result(status=status), OCR, 0.0)
    assert decision.outcome == "keep_offline" and decision.flags == flags and decision.text is None


def test_figure_only_keeps_offline_block_and_mixed_table_is_rejected() -> None:
    assert judge_region(_result(_figure()), OCR, 0.0).outcome == "figure_confirmed"
    mixed = judge_region(_result(_table([["R13", "42"]]), _text(OCR, order=1)), OCR, 0.0)
    assert mixed.outcome == "rejected" and mixed.reason == "mixed_table"
    assert judge_region(_result(), OCR, 0.0).reason == "empty"


def test_rapidocr_comparison_ignores_tone_marks_but_not_numbers() -> None:
    ocr = "Thanh ghi R13 chua gia tri 42 sau lenh MOV"
    assert judge_region(_result(_text(OCR)), ocr, 0.0, ocr_engine="rapidocr").outcome == "accepted_text"
    assert judge_region(_result(_text(OCR)), ocr.replace("42", "43"), 0.0, ocr_engine="rapidocr").outcome == "rejected"


# --------------------------------------------------------------------------- #
# Bảng: ô lệch là BLOCKING
# --------------------------------------------------------------------------- #

TABLE_OCR = "Thanh ghi Giá trị R13 42 R14 0x1F"


def test_matching_table_is_accepted_without_mismatch() -> None:
    rows = [["Thanh ghi", "Giá trị"], ["R13", "42"], ["R14", "0x1F"]]
    decision = judge_region(_result(_table(rows)), TABLE_OCR, 0.0)
    assert decision.outcome == "accepted_table" and decision.rows == rows
    assert decision.flags == [] and decision.mismatches == []


def test_table_cell_mismatch_is_blocking() -> None:
    rows = [["Thanh ghi", "Giá trị"], ["R13", "43"], ["R14", "0x1F"]]
    decision = judge_region(_result(_table(rows)), TABLE_OCR, 0.0)
    assert decision.outcome == "accepted_table" and decision.flags == ["table_token_mismatch"]
    assert decision.mismatches == [
        GateTokenMismatch(token_kind="number", row=1, col=1, vlm_value="43"),
        GateTokenMismatch(token_kind="number", ocr_value="42"),
    ]


def test_table_dropped_number_is_blocking() -> None:
    rows = [["Thanh ghi", "Giá trị"], ["R13", "42"], ["R14", ""]]
    decision = judge_region(_result(_table(rows)), TABLE_OCR, 0.0, VisionGateSettings(min_word_jaccard=0.5))
    assert decision.flags == ["table_token_mismatch"]
    assert decision.mismatches == [GateTokenMismatch(token_kind="number", ocr_value="0x1F")]


def test_table_with_low_jaccard_is_rejected() -> None:
    decision = judge_region(_result(_table([["x", "y"]])), TABLE_OCR, 0.0)
    assert decision.outcome == "rejected" and decision.reason == "jaccard" and decision.rows is None


def test_mismatch_ids_are_stable_and_distinguish_repeats() -> None:
    repeated = [
        GateTokenMismatch(token_kind="number", ocr_value="7"),
        GateTokenMismatch(token_kind="number", ocr_value="7"),
    ]
    first, second = mismatch_ids("blk_1", repeated)
    assert first != second and first.startswith("vmm_") and len(first) == 16
    assert mismatch_ids("blk_1", repeated) == [first, second]
    assert mismatch_ids("blk_2", repeated)[0] != first


# --------------------------------------------------------------------------- #
# Khoảng cách sửa có dải
# --------------------------------------------------------------------------- #


def test_banded_edit_distance_matches_full_levenshtein() -> None:
    rng = random.Random(7)
    for _ in range(3000):
        left = "".join(rng.choice("ab1") for _ in range(rng.randint(0, 15)))
        right = "".join(rng.choice("ab1") for _ in range(rng.randint(0, 15)))
        limit = rng.randint(0, 8)
        assert within_edit_distance(left, right, limit) == (levenshtein(left, right) <= limit)


def test_long_region_check_is_bounded() -> None:
    text = "lệnh MOV R1 " * 1600  # ~19 000 ký tự
    started = time.monotonic()
    assert judge_region(_result(_text(text)), text, 0.0).outcome == "accepted_text"
    assert time.monotonic() - started < 30


def test_long_shuffled_region_is_rejected_within_time_bound() -> None:
    """MAJOR review V02: vùng ~19 000 ký tự từ xáo trộn (Jaccard = 1, token số/mã bằng nhau) phải kết thúc < 1 s."""
    rng = random.Random(11)
    vocabulary = ["lệnh", "thanh", "ghi", "giá", "trị", "bộ", "nhớ", "địa", "chỉ", "ngắt", "cờ", "nạp"]
    words = [rng.choice(vocabulary) + rng.choice("abcdefgh") for _ in range(4000)]
    ocr = " ".join(words)[:19_000].rsplit(" ", 1)[0]
    shuffled = ocr.split()
    rng.shuffle(shuffled)
    vlm = " ".join(shuffled)
    assert 18_000 < len(ocr) <= 19_000 and len(vlm) == len(ocr)
    started = time.perf_counter()
    decision = judge_region(_result(_text(vlm)), ocr, 0.0)
    elapsed = time.perf_counter() - started
    assert decision.outcome == "rejected" and decision.reason == "cer" and decision.text is None
    assert elapsed < 1.0


def test_edit_distance_on_long_unicode_strings_matches_reference() -> None:
    rng = random.Random(3)
    alphabet = "aăâđêôơưáàảãạ R0"
    for _ in range(60):
        left = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 90)))
        right = list(left)
        for _ in range(rng.randint(0, 20)):
            position = rng.randint(0, len(right))
            choice = rng.random()
            if choice < 0.4 or not right:
                right.insert(position, rng.choice(alphabet))
            elif choice < 0.7:
                del right[min(position, len(right) - 1)]
            else:
                right[min(position, len(right) - 1)] = rng.choice(alphabet)
        other = "".join(right)
        distance = levenshtein(left, other)
        assert within_edit_distance(left, other, distance)
        assert not within_edit_distance(left, other, distance - 1)


@pytest.mark.parametrize("overlap", [float("nan"), float("inf"), float("-inf"), -0.1, 1.1])
def test_invalid_text_layer_overlap_fails_closed(overlap: float) -> None:
    """MAJOR review V02 (I3): độ giao không hữu hạn hoặc ngoài [0, 1] ⇒ không nhận chữ VLM, có cờ."""
    decision = judge_region(_result(_text(OCR)), OCR, overlap)
    assert decision.outcome == "rejected" and decision.reason == "invalid_overlap"
    assert decision.text is None and decision.rows is None and decision.flags == ["vlm_gate_rejected"]
    table = judge_region(_result(_table([["R13", "42"]])), "R13 42", overlap)
    assert table.outcome == "rejected" and table.rows is None


def test_overlap_bounds_zero_and_one_are_valid() -> None:
    assert judge_region(_result(_text(OCR)), OCR, 0.0).outcome == "accepted_text"
    assert judge_region(_result(_text(OCR)), OCR, 1.0).reason == "text_layer_overlap"


def test_whitespace_is_collapsed_before_comparison() -> None:
    ocr = "thanh ghi chứa giá trị sau lệnh"
    vlm = ocr.replace(" ", "\n\n   ")
    decision = judge_region(_result(_text(vlm)), ocr, 0.0)
    assert decision.outcome == "accepted_text" and decision.text == vlm


def test_word_jaccard_of_two_empty_word_sets_is_one() -> None:
    from holocron_core.services.vision_gate import word_jaccard

    assert word_jaccard("...", "...") == 1.0
    assert word_jaccard("a b c", "a b d") == 0.5


def test_jaccard_exactly_at_threshold_is_accepted() -> None:
    settings = VisionGateSettings(min_word_jaccard=0.5, max_cer=1.0)
    assert judge_region(_result(_text("a b d")), "a b c", 0.0, settings).outcome == "accepted_text"


def test_cer_limit_is_floor_of_threshold_times_ocr_length() -> None:
    ocr = "abcdefghij klmnopqrs"  # 20 ký tự ⇒ trần 0.15 × 20 = 3 lần sửa
    settings = VisionGateSettings(min_word_jaccard=0.0)
    assert judge_region(_result(_text("abcdefghij klmnopxyz")), ocr, 0.0, settings).outcome == "accepted_text"
    four = judge_region(_result(_text("abcdefghij klmnowxyz")), ocr, 0.0, settings)
    assert four.outcome == "rejected" and four.reason == "cer"


def test_two_table_blocks_are_mixed() -> None:
    decision = judge_region(_result(_table([["R13", "42"]]), _table([["R14", "7"]], order=1)), "R13 42 R14 7", 0.0)
    assert decision.outcome == "rejected" and decision.reason == "mixed_table"


def test_region_length_cap_applies_to_each_side() -> None:
    settings = VisionGateSettings(max_region_chars=20, min_word_jaccard=0.0, max_cer=1.0)
    exact = "a" * 20
    assert judge_region(_result(_text(exact)), exact, 0.0, settings).outcome == "accepted_text"
    long_vlm = VisionGateSettings(max_region_chars=50, min_word_jaccard=0.0, max_cer=1.0)
    decision = judge_region(_result(_text("b" * 200)), "a" * 20, 0.0, long_vlm)
    assert decision.reason == "too_long"
    assert judge_region(_result(_text("a" * 20)), "a" * 21, 0.0, settings).reason == "too_long"


def test_table_mismatch_carries_token_kind() -> None:
    rows = [["Thanh ghi", "Giá trị"], ["R15", "42"], ["R14", "0x1F"]]
    decision = judge_region(_result(_table(rows)), TABLE_OCR, 0.0)
    assert decision.flags == ["table_token_mismatch"]
    assert set(decision.mismatches) == {
        GateTokenMismatch(token_kind="code", row=1, col=0, vlm_value="R15"),
        GateTokenMismatch(token_kind="number", row=1, col=0, vlm_value="15"),
        GateTokenMismatch(token_kind="code", ocr_value="R13"),
        GateTokenMismatch(token_kind="number", ocr_value="13"),
    }


def test_table_token_over_200_chars_rejects_region() -> None:
    long_number = "9" * 201
    rows = [["Giá trị", long_number]]
    decision = judge_region(_result(_table(rows)), f"Giá trị {long_number}", 0.0)
    assert decision.outcome == "rejected" and decision.reason == "too_long" and decision.rows is None
    ok = "9" * 200
    accepted = judge_region(_result(_table([["Giá trị", ok]])), f"Giá trị {ok}", 0.0)
    assert accepted.outcome == "accepted_table" and accepted.mismatches == []


@pytest.mark.parametrize(
    "fields",
    [
        {"token_kind": "number"},
        {"token_kind": "number", "vlm_value": "1", "ocr_value": "1", "row": 0, "col": 0},
        {"token_kind": "number", "vlm_value": "1"},
        {"token_kind": "number", "vlm_value": "1", "row": 0},
        {"token_kind": "number", "ocr_value": "1", "row": 0, "col": 0},
        {"token_kind": "number", "ocr_value": "1" * 201},
        {"token_kind": "other", "ocr_value": "1"},
    ],
)
def test_token_mismatch_validator(fields: dict[str, object]) -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        GateTokenMismatch.model_validate(fields)


def test_rapidocr_table_compares_code_tokens_without_tone_marks() -> None:
    rows = [["Bước1 thanh ghi bộ nhớ", "42"]]
    ocr = "Buoc1 thanh ghi bo nho 42"
    decision = judge_region(_result(_table(rows)), ocr, 0.0, ocr_engine="rapidocr")
    assert decision.outcome == "accepted_table" and decision.mismatches == [] and decision.flags == []
    assert decision.rows == rows
    wrong = judge_region(_result(_table([["Bước1 thanh ghi bộ nhớ", "43"]])), ocr, 0.0, ocr_engine="rapidocr")
    assert wrong.flags == ["table_token_mismatch"]
