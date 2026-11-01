"""Phần thuần của `scripts/eval_ocr_selfsup.py` (CER, WER, tỉ lệ giữ dấu, từ chối ghi trong repo, tham số); không OCR."""

from __future__ import annotations

import random
import unicodedata
from pathlib import Path

import pytest

from scripts.eval_ocr_selfsup import (
    PageScore,
    aggregate,
    build_parser,
    diacritic_chars,
    has_diacritic,
    kept_flags,
    normalize,
    parse_int_list,
    refuses_output_dir,
    sample_evenly,
    score_page,
    sequence_distance,
)


def _reference(a: str, b: str) -> int:
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def test_sequence_distance_matches_reference_on_random_strings() -> None:
    rng = random.Random(7)
    for _ in range(60):
        a = "".join(rng.choice("abcđ ") for _ in range(rng.randint(0, 25)))
        b = "".join(rng.choice("abcđ ") for _ in range(rng.randint(0, 25)))
        assert sequence_distance(a, b) == _reference(a, b)


def test_sequence_distance_on_word_lists() -> None:
    assert sequence_distance(["a", "b", "c"], ["a", "c"]) == 1
    assert sequence_distance([], ["x"]) == 1


def test_normalize_nfc_and_whitespace() -> None:
    nfd = unicodedata.normalize("NFD", "Việt  Nam\n")
    assert normalize(nfd) == "Việt Nam"


def test_has_diacritic() -> None:
    assert has_diacritic("ế") and has_diacritic("đ") and has_diacritic("Ư")
    assert not has_diacritic("a") and not has_diacritic("7")
    assert diacritic_chars("Việt 12 đa") == "ệđ"


def test_perfect_prediction_scores_zero_error() -> None:
    score = score_page("Tiếng Việt có dấu 123", "Tiếng  Việt có dấu 123")
    assert score.char_distance == 0 and score.word_distance == 0 and score.digit_errors == 0
    assert score.diacritic_total == score.diacritic_kept > 0
    assert score.diacritic_seq_distance == 0


def test_dropped_diacritics_lower_retention_but_not_other_letters() -> None:
    truth = "Việt Nam đẹp"  # ệ, đ, ẹ có dấu
    score = score_page(truth, "Viet Nam dep")
    assert score.diacritic_total == 3
    assert score.diacritic_kept == 0
    assert score.char_distance == 3
    assert score.word_distance == 2
    assert score.diacritic_seq_distance == 3


def test_kept_flags_marks_aligned_matches() -> None:
    assert kept_flags("abc", "abc") == [True, True, True]
    assert kept_flags("abc", "") == [False, False, False]
    assert kept_flags("abc", "axc") == [True, False, True]


def test_digit_errors_counted_on_digit_sequence() -> None:
    score = score_page("so 1234", "so 1284")
    assert score.truth_digits == 4 and score.digit_errors == 1


def test_aggregate_micro_ratios() -> None:
    a = PageScore(100, 10, 20, 4, 10, 8, 2, 5, 1)
    b = PageScore(100, 30, 20, 6, 10, 4, 6, 5, 5)
    result = aggregate([a, b], [1.0, 3.0], failures=1)
    assert result["cer_micro"] == pytest.approx(0.2)
    assert result["wer_micro"] == pytest.approx(0.25)
    assert result["diacritic_retention"] == pytest.approx(0.6)
    assert result["diacritic_cer"] == pytest.approx(0.4)
    assert result["digit_accuracy_micro"] == pytest.approx(0.4)  # 4/5 + 0/5 trên 10 chữ số
    assert result["seconds_median"] == 2.0 and result["failures"] == 1
    assert aggregate([], [])["cer_micro"] is None


def test_refuses_output_dir_inside_repo(tmp_path: Path) -> None:
    assert refuses_output_dir(tmp_path / "repo" / "out", tmp_path / "repo")
    assert not refuses_output_dir(tmp_path / "eval" / "x01", tmp_path / "repo")


def test_parse_int_list_and_arguments() -> None:
    assert parse_int_list("150, 200,300") == [150, 200, 300]
    for bad in ("", "0", "3,3", "-1"):
        with pytest.raises(ValueError):
            parse_int_list(bad)
    args = build_parser().parse_args(["root", "out"])
    assert args.dpi == [150, 200, 300] and args.psm == [3, 4, 6] and args.max_pages == 40 and not args.degrade
    args = build_parser().parse_args(["root", "out", "--dpi", "200", "--psm", "6", "--max-pages", "5", "--degrade"])
    assert args.dpi == [200] and args.psm == [6] and args.max_pages == 5 and args.degrade


def test_sample_evenly_is_deterministic_and_spread() -> None:
    items = list(range(100))
    assert sample_evenly(items, 4) == [0, 25, 50, 75]
    assert sample_evenly(items[:3], 40) == [0, 1, 2]
    assert sample_evenly(items, 0) == []
