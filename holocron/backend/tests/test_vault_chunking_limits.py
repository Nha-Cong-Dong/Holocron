"""Giới hạn của bộ cắt chunk (§12.1) và của văn bản đã xác nhận: 450 token mỗi chunk kể cả khi một "từ" không có dấu
cách dài hơn 450 token; tối đa 300 trang mỗi tài liệu.

Văn bản mẫu tự viết cho test (không phải tài liệu môn học có bản quyền).
"""

import pytest
from pydantic import ValidationError

from holocron_core.domain.schemas import normalize_quote
from holocron_core.domain.search_text import search_tokens
from holocron_core.domain.vault import MAX_DOCUMENT_PAGES, MAX_LINE_CHARS, ConfirmedDocumentText, ConfirmedPage
from holocron_core.services.vault_chunking import MAX_CHUNK_TOKENS, _raw_text, _Unit, build_chunks
from tests.vault_support import confirmed, document_id

DOC = document_id("e")


def _document_text(pages: list[list[str]]) -> str:
    return normalize_quote(" ".join(" ".join(lines) for lines in pages))


def _assert_within_limit_and_verbatim(pages: list[list[str]]) -> int:
    chunks = build_chunks(confirmed(DOC, pages))
    source = _document_text(pages)
    for chunk in chunks:
        assert chunk.token_count <= MAX_CHUNK_TOKENS
        assert len(search_tokens(chunk.raw_text)) <= MAX_CHUNK_TOKENS
        assert normalize_quote(chunk.raw_text) in source, "raw_text phải là đoạn liền của văn bản đã xác nhận"
    covered = sum(len(search_tokens(chunk.raw_text)) for chunk in chunks)
    assert covered >= len(search_tokens(source)), "không được làm mất token nào"
    return len(chunks)


@pytest.mark.parametrize(
    "word",
    [
        "a-" * 999 + "a",  # 1000 token, không có dấu cách
        "x1/" * 666,  # 666 token ngăn bởi "/"
        "đ." * 1000,  # chữ Việt, ngăn bởi dấu chấm không kèm khoảng trắng nên không thành câu riêng
    ],
    ids=["hyphen", "slash", "dot"],
)
def test_a_single_long_word_is_hard_cut_by_token(word: str) -> None:
    assert len(word) <= MAX_LINE_CHARS
    assert len(search_tokens(word)) > MAX_CHUNK_TOKENS
    assert _assert_within_limit_and_verbatim([[word]]) >= 2


def test_long_word_between_ordinary_words_keeps_the_text_verbatim() -> None:
    pages = [["Mở đầu đoạn văn " + "b-" * 600 + "b kết thúc đoạn.", "Câu tiếp theo của đoạn sau."]]
    assert _assert_within_limit_and_verbatim(pages) >= 2


def test_several_long_words_in_one_paragraph() -> None:
    pages = [["c-" * 700 + "c", "d-" * 700 + "d", "e " * 300]]
    _assert_within_limit_and_verbatim(pages)


def test_a_token_that_expands_on_casefold_is_still_cut() -> None:
    """`İ` là một ký tự chữ nhưng `casefold` thành `i` + dấu chấm kết hợp: 1000 ký tự liền nhau cho 1000 token."""
    word = "İ" * 1000
    assert len(search_tokens(word)) > MAX_CHUNK_TOKENS
    assert _assert_within_limit_and_verbatim([[word]]) >= 3


def test_ordinary_text_is_not_affected() -> None:
    pages = [["3.1 Thanh ghi trạng thái", "Thanh ghi CPSR chứa các cờ trạng thái N, Z, C, V."]]
    (chunk,) = build_chunks(confirmed(DOC, pages))
    assert chunk.raw_text == "3.1 Thanh ghi trạng thái\n\nThanh ghi CPSR chứa các cờ trạng thái N, Z, C, V."


def test_confirmed_document_has_at_most_300_pages() -> None:
    pages = [ConfirmedPage(page_number=number, lines=["x"]) for number in range(1, MAX_DOCUMENT_PAGES + 2)]
    base = {"doc_id": DOC, "subject_code": "ET4010", "chapter_code": "ch1"}
    assert MAX_DOCUMENT_PAGES == 300
    assert len(ConfirmedDocumentText(**base, pages=pages[:MAX_DOCUMENT_PAGES]).pages) == 300
    with pytest.raises(ValidationError, match="300"):
        ConfirmedDocumentText(**base, pages=pages)


def test_topic_keys_are_bounded() -> None:
    base = {"doc_id": DOC, "subject_code": "ET4010", "chapter_code": "ch1", "pages": [{"page_number": 1, "lines": []}]}
    with pytest.raises(ValidationError):
        ConfirmedDocumentText(**base, topic_keys=[f"a.b.k{index}" for index in range(33)])
    with pytest.raises(ValidationError):
        ConfirmedDocumentText(**base, topic_keys=["a.b." + "c" * 130])
    assert ConfirmedDocumentText(**base, topic_keys=[f"a.b.k{index}" for index in range(32)]).topic_keys


def test_pieces_of_a_cut_word_are_joined_without_a_space() -> None:
    """Hai mảnh liền nhau của cùng một từ nằm chung chunk thì nối liền, để `raw_text` vẫn là nguyên văn."""
    units = [
        _Unit(paragraph=0, page_number=1, text="ab-", tokens=1, is_heading=False),
        _Unit(paragraph=0, page_number=1, text="cd", tokens=1, is_heading=False, glued=True),
        _Unit(paragraph=0, page_number=1, text="Câu sau.", tokens=2, is_heading=False),
        _Unit(paragraph=1, page_number=1, text="ef", tokens=1, is_heading=False, glued=True),
    ]
    assert _raw_text(units) == "ab-cd Câu sau.\n\nef"
