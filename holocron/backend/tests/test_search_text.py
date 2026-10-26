"""Tách từ tiếng Việt cho BM25 (ADR-006 mục 5): NFC, vị trí dấu thanh, casefold, âm tiết, bản không dấu."""

import unicodedata

import pytest

from holocron_core.domain.search_text import (
    no_diacritics_tokens,
    normalize_search_text,
    normalize_tone_placement,
    search_body,
    search_tokens,
    strip_diacritics,
)


def test_ma_variants_stay_distinct_tokens() -> None:
    tokens = search_tokens("ma mà má mã mả mạ")
    assert tokens == ["ma", "mà", "má", "mã", "mả", "mạ"]
    assert len(set(tokens)) == 6


def test_no_diacritics_index_merges_ma_variants() -> None:
    assert set(no_diacritics_tokens("ma mà má mã mả mạ")) == {"ma"}


def test_nfc_and_nfd_give_the_same_tokens() -> None:
    text = "Lượng tử thời gian của tiến trình"
    nfd = unicodedata.normalize("NFD", text)
    assert nfd != text
    assert search_tokens(nfd) == search_tokens(text) == ["lượng", "tử", "thời", "gian", "của", "tiến", "trình"]


@pytest.mark.parametrize(
    ("old_or_new", "canonical"),
    [
        # vần oa, không phụ âm cuối: cả năm dấu thanh
        ("hoà", "hòa"),
        ("hoá", "hóa"),
        ("hoả", "hỏa"),
        ("hoã", "hõa"),
        ("hoạ", "họa"),
        ("hòa", "hòa"),
        # vần oe, không phụ âm cuối
        ("khoẻ", "khỏe"),
        ("khỏe", "khỏe"),
        ("xoè", "xòe"),
        # vần uy, không phụ âm cuối
        ("thuỷ", "thủy"),
        ("thủy", "thủy"),
        ("huỷ", "hủy"),
        ("tuý", "túy"),
        # có phụ âm cuối: dấu trên nguyên âm sau
        ("hoàng", "hoàng"),
        ("hòang", "hoàng"),
        ("toán", "toán"),
        ("tóan", "toán"),
        ("hoạch", "hoạch"),
        ("họach", "hoạch"),
        ("khoẻn", "khoẻn"),
        ("khỏen", "khoẻn"),
        ("huỳnh", "huỳnh"),
        ("hùynh", "huỳnh"),
        ("uỵch", "uỵch"),
        ("ụych", "uỵch"),
    ],
)
def test_tone_placement_pairs(old_or_new: str, canonical: str) -> None:
    assert normalize_tone_placement(old_or_new) == canonical
    assert search_tokens(old_or_new) == [canonical]


@pytest.mark.parametrize(
    "word",
    ["quý", "quả", "qua", "hoài", "ngoáy", "thuyền", "khuỷu", "hoa", "thuy", "mã", "x2oá", "hoáx"],
)
def test_other_syllables_are_unchanged(word: str) -> None:
    assert normalize_tone_placement(word) == word


def test_old_and_new_tone_styles_give_the_same_tokens() -> None:
    assert search_tokens("Hoà bình, thuỷ lợi, khoẻ mạnh") == search_tokens("hòa bình; thủy lợi; khỏe mạnh")


def test_casefold_keeps_d_stroke_distinct() -> None:
    assert search_tokens("ĐƯỜNG Đi") == ["đường", "đi"]
    assert search_tokens("đi") != search_tokens("di")
    assert strip_diacritics("đường") == "đuong"


def test_tokens_are_syllables_and_punctuation_is_dropped() -> None:
    assert search_tokens("Thanh ghi R0=0x1F; cờ N, Z (CPSR) — tiến_trình 3.14") == [
        "thanh",
        "ghi",
        "r0",
        "0x1f",
        "cờ",
        "n",
        "z",
        "cpsr",
        "tiến",
        "trình",
        "3",
        "14",
    ]


def test_normalize_search_text_keeps_punctuation_and_spacing() -> None:
    assert normalize_search_text("Hoà, THUỶ!") == "hòa, thủy!"


def test_search_body_joins_tokens() -> None:
    assert search_body("  Hoà   bình. ") == "hòa bình"
    assert search_body("!!!") == ""


def test_multiple_tone_marks_are_left_alone() -> None:
    weird = unicodedata.normalize("NFC", "hòá")
    assert normalize_tone_placement(weird) == weird
