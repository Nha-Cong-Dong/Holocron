"""Chương tự động, phần thuần (SPECIFICATION §5.7 "Chương tự động"; GĐ1 1A): số chương từ tên file, chương trống
nhỏ nhất kèm cờ, tên chương từ ứng viên header hoặc tên file đã làm sạch, bộ chọn tên (stub thay LLM) chỉ được trả
chuỗi con của một ứng viên, và `DocumentChapterPatch`.
"""

import logging
import unicodedata
from collections.abc import Sequence

import pytest
from pydantic import ValidationError

from holocron_core.domain.documents import ChapterSource, DocumentChapterPatch
from holocron_core.services.chapter_naming import (
    EMPTY_FILENAME_TITLE,
    ChapterChoice,
    TitleChoice,
    chapter_number_from_filename,
    checked_selection,
    choose_chapter_code,
    choose_chapter_title,
    clean_filename_title,
    comparison_key,
    next_free_chapter,
    select_title_with_llm,
    usable_header_candidates,
)

# --------------------------------------------------------------------------- #
# Số chương từ tên file
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("filename", "number"),
    [
        ("Chuong-2_Vi-xu-ly-ARM.pdf", 2),
        ("ch02.pdf", 2),
        ("bai 7 tong ket.pdf", 7),
        ("12-slides.pdf", 12),
        ("2020 - chuong 3.pdf", 3),  # đoạn ≥ 3 chữ số bị bỏ qua
        ("de-2024-ver1.pdf", 1),
        ("lecture10part2.pdf", 10),
        ("v1.2-notes.pdf", 1),  # đuôi là phần sau dấu chấm cuối; "v1.2-notes" còn "1" đầu tiên
        ("report.03.pdf", 3),
        ("tai-lieu.pdf", None),
        ("2020-2021.pdf", None),
        ("chương ٣.pdf", None),  # chữ số không phải ASCII không tính
        ("chuong１.pdf", None),  # chữ số toàn khổ không tính
    ],
)
def test_chapter_number_is_the_first_one_or_two_digit_ascii_run_of_the_stem(filename: str, number: int | None) -> None:
    assert chapter_number_from_filename(filename) == number


def test_the_extension_is_not_searched() -> None:
    assert chapter_number_from_filename("slides.mp4") is None
    assert chapter_number_from_filename("slides.7z") is None
    assert chapter_number_from_filename(".pdf") is None


@pytest.mark.parametrize("filename", ["chuong-0.pdf", "ch00.pdf", "00 gioi thieu 3.pdf"])
def test_zero_counts_as_no_number(filename: str) -> None:
    """Đoạn 1–2 chữ số đầu tiên bằng 0 ⇒ không có số (N ≥ 1), không xét đoạn sau."""
    assert chapter_number_from_filename(filename) is None


def test_next_free_chapter_skips_used_codes_by_exact_match() -> None:
    assert next_free_chapter([]) == "ch1"
    assert next_free_chapter(["ch1", "ch2", "ch4"]) == "ch3"
    assert next_free_chapter(["ch1.2", "ch2"]) == "ch1"
    assert next_free_chapter([f"ch{n}" for n in range(1, 100)]) is None


def test_choose_from_the_file_name_when_the_number_is_free() -> None:
    assert choose_chapter_code("Chuong-2_Vi-xu-ly-ARM.pdf", {"ch1", "ch3"}) == ChapterChoice("ch2", "file_name", ())
    assert choose_chapter_code("ch02.pdf", set()) == ChapterChoice("ch2", "file_name", ())


def test_no_number_takes_the_smallest_free_chapter_with_a_flag() -> None:
    assert choose_chapter_code("tai-lieu.pdf", {"ch1", "ch3"}) == ChapterChoice(
        "ch2", "next_free", ("CHAPTER_NUMBER_NOT_IN_FILENAME",)
    )
    assert choose_chapter_code("2020.pdf", set()) == ChapterChoice(
        "ch1", "next_free", ("CHAPTER_NUMBER_NOT_IN_FILENAME",)
    )


def test_a_taken_number_takes_the_smallest_free_chapter_with_a_flag() -> None:
    assert choose_chapter_code("chuong-2.pdf", {"ch1", "ch2"}) == ChapterChoice(
        "ch3", "next_free", ("CHAPTER_NUMBER_TAKEN",)
    )


def test_every_chapter_taken_still_gives_a_valid_code() -> None:
    every = {f"ch{n}" for n in range(1, 100)}
    assert choose_chapter_code("chuong-5.pdf", every) == ChapterChoice("ch99", "next_free", ("CHAPTER_NUMBER_TAKEN",))
    assert choose_chapter_code("x.pdf", every) == ChapterChoice(
        "ch99", "next_free", ("CHAPTER_NUMBER_NOT_IN_FILENAME",)
    )


# --------------------------------------------------------------------------- #
# Tên chương
# --------------------------------------------------------------------------- #


def test_clean_filename_title() -> None:
    assert clean_filename_title("Chuong-2_Vi-xu-ly-ARM.pdf") == "Chuong 2 Vi xu ly ARM"
    assert clean_filename_title("  a__b--c   d .pdf") == "a b c d"
    assert clean_filename_title("___.pdf") == EMPTY_FILENAME_TITLE
    nfd = unicodedata.normalize("NFD", "Chương_một.pdf")
    assert clean_filename_title(nfd) == unicodedata.normalize("NFC", "Chương một")
    long_title = clean_filename_title("a" * 299 + " b" * 10 + ".pdf")
    assert len(long_title) <= 300 and long_title == "a" * 299


def test_comparison_key_folds_case_width_of_spaces_and_nfd() -> None:
    assert comparison_key("  Bộ   NHỚ ") == comparison_key(unicodedata.normalize("NFD", "bộ nhớ"))


def test_usable_candidates_drop_marks_only_long_and_duplicate_lines() -> None:
    lines = ["  Bộ  nhớ ", "###", "— · —", "", "x" * 301, "BỘ NHỚ", "Ngắt"]
    assert usable_header_candidates(lines) == ["Bộ nhớ", "Ngắt"]


def test_title_takes_the_first_header_candidate_not_excluded() -> None:
    choice = choose_chapter_title("ch2.pdf", ["ET4010", "Kỹ thuật vi xử lý", "Bộ nhớ"], {"et4010", "KỸ THUẬT VI XỬ LÝ"})
    assert choice == TitleChoice("Bộ nhớ", "repeated_header")


def test_title_falls_back_to_the_cleaned_file_name_even_when_it_is_excluded() -> None:
    choice = choose_chapter_title("Bo-nho.pdf", ["###", "Bộ nhớ"], {"Bộ nhớ", "Bo nho"})
    assert choice == TitleChoice("Bo nho", "file_name")
    assert choose_chapter_title("Bo-nho.pdf", [], set()) == TitleChoice("Bo nho", "file_name")


def test_title_code_has_no_subject_or_language_words() -> None:
    """Luật chọn tên không chứa chuỗi theo môn hay ngôn ngữ (ADR-006 mục 10): cùng cấu trúc, chữ khác ⇒ cùng kết quả."""
    for word in ("Chapter 1", "Chương 1", "Kỳ 1", "Teil 1"):
        assert choose_chapter_title("x.pdf", [word], set()) == TitleChoice(word, "repeated_header")


# --------------------------------------------------------------------------- #
# Bộ chọn tên (stub thay LLM)
# --------------------------------------------------------------------------- #


class StubSelector:
    def __init__(self, answer: str | None = None, error: Exception | None = None) -> None:
        self.answer = answer
        self.error = error
        self.calls: list[list[str]] = []

    def select(self, candidates: Sequence[str]) -> str | None:
        self.calls.append(list(candidates))
        if self.error is not None:
            raise self.error
        return self.answer


CANDIDATES = ["Chương 2: Bộ nhớ và ngắt", "Chuong 2 Vi xu ly ARM"]


def test_selector_may_pick_or_shorten_a_candidate() -> None:
    assert select_title_with_llm(StubSelector("Chương 2: Bộ nhớ và ngắt"), CANDIDATES) == "Chương 2: Bộ nhớ và ngắt"
    assert select_title_with_llm(StubSelector("  Bộ nhớ   và ngắt "), CANDIDATES) == "Bộ nhớ và ngắt"
    nfd = unicodedata.normalize("NFD", "Bộ nhớ")
    assert select_title_with_llm(StubSelector(nfd), CANDIDATES) == unicodedata.normalize("NFC", "Bộ nhớ")


@pytest.mark.parametrize(
    "answer",
    [None, "", "   ", "Bộ nhớ, ngắt và DMA", "Chương 3", "Bộ nhớ Vi xu ly", "x" * 301],
)
def test_selector_output_that_is_not_a_substring_of_one_candidate_is_dropped(answer: str | None) -> None:
    assert select_title_with_llm(StubSelector(answer), CANDIDATES) is None


def test_selector_only_receives_the_candidate_strings() -> None:
    stub = StubSelector("Bộ nhớ")
    select_title_with_llm(stub, CANDIDATES)
    assert stub.calls == [CANDIDATES]
    empty = StubSelector("Bộ nhớ")
    assert select_title_with_llm(empty, []) is None and empty.calls == []


def test_selector_errors_are_logged_and_ignored(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="holocron_core.services.chapter_naming"):
        assert select_title_with_llm(StubSelector(error=TimeoutError("hết giờ")), CANDIDATES) is None
    assert "bộ chọn tên chương lỗi" in caplog.text


def test_checked_selection_accepts_only_substrings() -> None:
    assert checked_selection(CANDIDATES, "ARM") == "ARM"
    assert checked_selection(CANDIDATES, "arm") is None, "so khớp phân biệt hoa thường: kết quả phải là nguyên văn"


# --------------------------------------------------------------------------- #
# Model của contract
# --------------------------------------------------------------------------- #


def test_chapter_patch_normalizes_the_title_and_needs_one_field() -> None:
    patch = DocumentChapterPatch(editor_id="u", chapter_title=unicodedata.normalize("NFD", "  Bộ   nhớ "))
    assert patch.chapter_title == "Bộ nhớ" and patch.chapter_code is None
    assert DocumentChapterPatch(editor_id="u", chapter_code="ch3.2").chapter_code == "ch3.2"
    for bad in (
        {"editor_id": "u"},
        {"editor_id": "u", "chapter_title": "   "},
        {"editor_id": "u", "chapter_title": "x" * 301},
        {"editor_id": "u", "chapter_code": "chuong3"},
        {"editor_id": "", "chapter_code": "ch3"},
        {"editor_id": "u", "chapter_code": "ch3", "extra": 1},
    ):
        with pytest.raises(ValidationError):
            DocumentChapterPatch.model_validate(bad)


def test_chapter_source_values() -> None:
    assert ChapterSource(code="next_free", title=None).title is None
    with pytest.raises(ValidationError):
        ChapterSource.model_validate({"code": "llm", "title": None})
