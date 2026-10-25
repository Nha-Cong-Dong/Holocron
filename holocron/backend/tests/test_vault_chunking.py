"""Cắt chunk tất định (spec §12.1, ADR-006 mục 3): tiêu đề, 450 token, chồng lấn 1 câu, tái lập `chunk_id`."""

import unicodedata

import pytest
from pydantic import ValidationError

from holocron_core.domain.schemas import quote_in_text
from holocron_core.domain.search_text import search_tokens
from holocron_core.domain.vault import ConfirmedDocumentText, ConfirmedPage
from holocron_core.services.vault_chunking import (
    MAX_CHUNK_TOKENS,
    build_chunks,
    chunk_id_for,
    is_heading,
)
from tests.vault_support import ARM_PAGES, confirmed, document_id

DOC = document_id("a")


def _sentence(index: int, words: int = 20) -> str:
    """Câu có đúng `words` token, mở đầu bằng số thứ tự để phân biệt."""
    body = " ".join(f"từ{index}x{k}" for k in range(words - 1))
    return f"c{index} {body}."


def _lines(text: str, words_per_line: int = 100) -> list[str]:
    """Bẻ văn bản thành các dòng liền nhau (≤ 2000 ký tự mỗi dòng); dòng liền nhau nối lại bằng một dấu cách."""
    words = text.split(" ")
    return [" ".join(words[i : i + words_per_line]) for i in range(0, len(words), words_per_line)]


def test_same_input_gives_same_chunks_and_ids() -> None:
    first = build_chunks(confirmed(DOC, ARM_PAGES))
    second = build_chunks(confirmed(DOC, ARM_PAGES))
    assert [c.model_dump() for c in first] == [c.model_dump() for c in second]
    assert all(c.chunk_id.startswith("chk_") and len(c.chunk_id) == 20 for c in first)
    assert len({c.chunk_id for c in first}) == len(first)


def test_chunk_id_depends_on_document_order_and_text() -> None:
    base = chunk_id_for(DOC, 0, "văn bản")
    assert base == chunk_id_for(DOC, 0, "văn bản")
    assert base != chunk_id_for(document_id("b"), 0, "văn bản")
    assert base != chunk_id_for(DOC, 1, "văn bản")
    assert base != chunk_id_for(DOC, 0, "văn bản khác")


def test_nfd_input_gives_the_same_chunks_as_nfc() -> None:
    nfd_pages = [[unicodedata.normalize("NFD", line) for line in page] for page in ARM_PAGES]
    assert [c.model_dump() for c in build_chunks(confirmed(DOC, nfd_pages))] == [
        c.model_dump() for c in build_chunks(confirmed(DOC, ARM_PAGES))
    ]


def test_headings_start_new_chunks_and_paragraphs_are_kept() -> None:
    chunks = build_chunks(confirmed(DOC, ARM_PAGES, topic_keys=["mp.arm.cpsr.flags"]))
    assert len(chunks) == 2
    first, second = chunks
    assert first.raw_text == (
        "3.1 Thanh ghi trạng thái\n\n"
        "Thanh ghi CPSR chứa các cờ trạng thái N, Z, C, V của bộ vi xử lý ARM. "
        "Cờ Z được đặt khi kết quả phép toán bằng không.\n\n"
        "Cờ C được đặt khi phép cộng có nhớ ra khỏi bit cao nhất."
    )
    assert (first.page_number, first.page_end) == (1, None)
    assert second.raw_text.startswith("3.2 Lệnh cộng\n\nLệnh ADDS")
    assert second.page_number == 2
    # Trích dẫn mặc định: câu đầu tiên đủ 8 ký tự, ưu tiên câu không phải tiêu đề (§5.8).
    assert first.verbatim_quote == "Thanh ghi CPSR chứa các cờ trạng thái N, Z, C, V của bộ vi xử lý ARM."
    for chunk in chunks:
        assert chunk.label_status == "proposed" and chunk.version == 1
        assert chunk.topic_keys == ["mp.arm.cpsr.flags"]
        assert chunk.tags == ["#theory"]
        assert chunk.token_count == len(search_tokens(chunk.raw_text))
        assert quote_in_text(chunk.verbatim_quote, chunk.raw_text)


def test_heading_only_section_is_merged_into_the_next() -> None:
    chunks = build_chunks(confirmed(DOC, [["Chương 3", "3.1 Thanh ghi", "Nội dung của mục thanh ghi."]]))
    assert len(chunks) == 1
    assert chunks[0].raw_text == "Chương 3\n\n3.1 Thanh ghi\n\nNội dung của mục thanh ghi."


@pytest.mark.parametrize(
    "line",
    [
        "# Giới thiệu",
        "Chương 3 Bộ nhớ",
        "CHƯƠNG IV",
        "Bài 2 Lệnh",
        "Chapter 4 Memory",
        "3.2 Lệnh cộng",
        "1.2.3. Mục nhỏ",
    ],
)
def test_heading_patterns(line: str) -> None:
    assert is_heading(line)


@pytest.mark.parametrize(
    "line",
    [
        "",
        "Thanh ghi CPSR chứa các cờ.",
        "3.14 là giá trị gần đúng của số pi.",
        "1. Khởi tạo bộ đệm",
        "Chương trình con được gọi bằng lệnh BL",
        "3.2 " + "dài " * 40,
    ],
)
def test_not_headings(line: str) -> None:
    assert not is_heading(line.strip())


def test_long_section_is_split_at_paragraph_boundaries_with_one_sentence_overlap() -> None:
    # 6 đoạn, mỗi đoạn 5 câu × 20 token = 100 token ⇒ 600 token, vượt 450.
    paragraphs = [[_sentence(p * 10 + s) for s in range(5)] for p in range(6)]
    lines: list[str] = ["2.1 Mục dài"]
    for paragraph in paragraphs:
        lines.extend([" ".join(paragraph), ""])
    chunks = build_chunks(confirmed(DOC, [lines]))
    assert len(chunks) == 2
    assert all(c.token_count <= MAX_CHUNK_TOKENS for c in chunks)
    first_paragraphs = chunks[0].raw_text.split("\n\n")
    second_paragraphs = chunks[1].raw_text.split("\n\n")
    # Chunk đầu dừng ở ranh giới đoạn: tiêu đề + 4 đoạn đầy đủ.
    assert first_paragraphs[0] == "2.1 Mục dài"
    assert first_paragraphs[1:] == [" ".join(p) for p in paragraphs[:4]]
    # Chunk sau mở đầu bằng câu cuối của chunk trước (chồng lấn 1 câu).
    assert second_paragraphs[0] == paragraphs[3][-1]
    assert second_paragraphs[1:] == [" ".join(p) for p in paragraphs[4:]]


def test_long_paragraph_is_split_at_sentence_boundaries() -> None:
    sentences = [_sentence(i) for i in range(30)]  # một đoạn 600 token
    chunks = build_chunks(confirmed(DOC, [sentences]))
    assert len(chunks) == 2
    assert chunks[0].raw_text == " ".join(sentences[:22])  # 22 × 20 = 440 ≤ 450 < 460
    assert chunks[1].raw_text == " ".join(sentences[21:])  # câu 21 lặp lại làm chồng lấn
    assert all(c.token_count <= MAX_CHUNK_TOKENS for c in chunks)


def test_very_long_sentence_is_split_by_words() -> None:
    sentence = " ".join(f"w{k}" for k in range(1000)) + "."
    chunks = build_chunks(confirmed(DOC, [_lines(sentence)]))
    assert [c.token_count for c in chunks] == [450, 450, 100]
    assert " ".join(c.raw_text for c in chunks) == sentence
    assert all(len(c.verbatim_quote) <= 600 and quote_in_text(c.verbatim_quote, c.raw_text) for c in chunks)


def test_overlap_is_dropped_when_it_does_not_fit() -> None:
    big = _sentence(1, words=300)
    other = _sentence(2, words=300)
    chunks = build_chunks(confirmed(DOC, [[*_lines(big), "", *_lines(other)]]))
    assert [c.raw_text for c in chunks] == [big, other]


def test_chunk_spanning_pages_records_page_end() -> None:
    chunks = build_chunks(confirmed(DOC, [["Đoạn đầu ở trang một."], ["Đoạn tiếp ở trang hai."]], first_page=4))
    assert len(chunks) == 1
    assert (chunks[0].page_number, chunks[0].page_end) == (4, 5)
    assert chunks[0].raw_text == "Đoạn đầu ở trang một.\n\nĐoạn tiếp ở trang hai."


def test_chunks_without_citable_text_are_skipped() -> None:
    assert build_chunks(confirmed(DOC, [["---", "", "!!!"]])) == []
    assert build_chunks(confirmed(DOC, [["# Ngắn", "Ok"]])) == []  # có token nhưng không câu nào đủ 8 ký tự
    assert build_chunks(confirmed(DOC, [["Ok."]])) == []


def test_heading_is_used_as_quote_when_no_body_sentence_is_long_enough() -> None:
    chunks = build_chunks(confirmed(DOC, [["3.4 Tiêu đề đủ dài", "Ngắn."]]))
    assert len(chunks) == 1
    assert chunks[0].verbatim_quote == "3.4 Tiêu đề đủ dài"


def test_long_first_sentence_quote_is_cut_at_a_word_boundary() -> None:
    sentence = " ".join(["chữ"] * 250) + "."  # 999 ký tự, 250 token
    chunk = build_chunks(confirmed(DOC, [[sentence]]))[0]
    assert len(chunk.verbatim_quote) <= 600
    assert chunk.verbatim_quote == " ".join(["chữ"] * 150)


def test_quote_without_spaces_is_cut_at_600_chars() -> None:
    word = "a" * 700
    chunk = build_chunks(confirmed(DOC, [[word]]))[0]
    assert chunk.verbatim_quote == "a" * 600


def test_practice_category_gets_practice_tag() -> None:
    chunk = build_chunks(confirmed(DOC, ARM_PAGES, category="PRACTICE"))[0]
    assert chunk.tags == ["#practice_example"]


def test_input_model_rejects_unordered_pages_and_bad_labels() -> None:
    with pytest.raises(ValidationError):
        ConfirmedDocumentText(
            doc_id=DOC,
            subject_code="ET4010",
            chapter_code="ch3",
            pages=[ConfirmedPage(page_number=2, lines=["a"]), ConfirmedPage(page_number=1, lines=["b"])],
        )
    with pytest.raises(ValidationError):
        confirmed(DOC, ARM_PAGES, topic_keys=["khong hop le"])
    with pytest.raises(ValidationError):
        ConfirmedPage(page_number=1, lines=["x" * 2001])
