"""Cắt chunk theo khối, hàm thuần `build_block_chunks` (SPECIFICATION §12.1; ADR-010 mục 16).

Biên giới hạn 450 token và 8 ký tự, gộp mảnh ngắn của khối dài (không chữ nào của khối bị bỏ khỏi `raw_text`, I3),
cờ `too_short_for_chunk` chỉ cho khối không vào chunk nào, trang/khoảng trang của chunk và trích dẫn mặc định.
Văn bản tự tạo, không gọi mạng.
"""

from __future__ import annotations

import hashlib

from holocron_core.domain.schemas import normalize_quote, quote_in_text
from holocron_core.domain.vault import BlockDocumentText, ChunkBlock, ConfirmedPage
from holocron_core.services.vault_chunking import MAX_CHUNK_TOKENS, BlockChunks, build_block_chunks

DOC = "doc_00000000000000d1"
SEC_A = "sec_" + "a" * 16
SEC_B = "sec_" + "b" * 16
SEC_C = "sec_" + "c" * 16


def words(count: int, end: str = ".") -> str:
    """`count` token (mỗi từ `w` một token), kết thúc bằng `end`."""
    return " ".join(["w"] * count) + end


def block(
    order: int, text: str, *, page: int = 1, section: str = SEC_A, heading: bool = False, key: str = ""
) -> ChunkBlock:
    block_id = "blk_" + hashlib.sha256(f"{page}|{order}|{key}".encode()).hexdigest()[:16]
    return ChunkBlock(
        block_id=block_id, page_number=page, reading_order=order, is_heading=heading, text=text, section_id=section
    )


def chunk(blocks: list[ChunkBlock], line_pages: list[ConfirmedPage] | None = None) -> BlockChunks:
    document = BlockDocumentText(
        doc_id=DOC, subject_code="IT3070", chapter_code="ch2", blocks=blocks, line_pages=line_pages or []
    )
    return build_block_chunks(document)


def chunked_text(result: BlockChunks, block_id: str) -> str:
    return "\n\n".join(c.raw_text for c in result.chunks if block_id in c.block_ids)


def test_block_of_exactly_limit_stays_one_segment() -> None:
    text = words(225) + "\n" + words(225)
    only = block(0, text)
    result = chunk([only])
    (single,) = result.chunks
    assert single.token_count == MAX_CHUNK_TOKENS
    assert single.raw_text == text  # không tách theo câu (tách sẽ nối lại bằng dấu cách)


def test_two_blocks_summing_to_limit_share_one_chunk() -> None:
    first, second = block(0, words(225)), block(1, words(225))
    (single,) = chunk([first, second]).chunks
    assert single.block_ids == [first.block_id, second.block_id]
    assert single.token_count == MAX_CHUNK_TOKENS


def test_short_tail_of_hard_cut_word_glued_and_kept() -> None:
    text = "-".join(["a"] * (MAX_CHUNK_TOKENS + 1))
    only = block(0, text)
    result = chunk([only])
    assert result.too_short_block_ids == []
    assert chunked_text(result, only.block_id) == text  # mảnh cắt cứng nối liền, không chen dấu cách (I3)
    assert all(quote_in_text(c.verbatim_quote, c.raw_text) for c in result.chunks)


def test_short_last_sentence_of_long_block_never_dropped() -> None:
    text = words(MAX_CHUNK_TOKENS) + " " + "Het."
    only = block(0, text)
    result = chunk([only])
    assert result.too_short_block_ids == []
    assert sum(c.raw_text.count("Het.") for c in result.chunks) == 1
    assert chunked_text(result, only.block_id) == text
    # khối lớn kế tiếp cùng Section không nhận được mảnh ngắn ⇒ mảnh ngắn quay về chunk trước (cùng khối)
    following = block(1, words(MAX_CHUNK_TOKENS))
    result = chunk([only, following])
    assert result.too_short_block_ids == []
    assert chunked_text(result, only.block_id) == text
    assert [c.block_ids for c in result.chunks] == [[only.block_id], [following.block_id]]


def test_short_first_sentence_of_long_block_merged_forward() -> None:
    text = "Mo. " + words(MAX_CHUNK_TOKENS) + " " + words(MAX_CHUNK_TOKENS - 1)
    only = block(0, text)
    result = chunk([only])
    assert result.too_short_block_ids == []
    assert " ".join(c.raw_text for c in result.chunks) == text
    assert result.chunks[0].raw_text.startswith("Mo. w w")


def test_short_whole_block_alone_is_flagged_but_split_blocks_are_not() -> None:
    lonely = block(0, "Ab.", section=SEC_A)
    long_block = block(1, words(MAX_CHUNK_TOKENS) + " " + "Het.", section=SEC_B)
    result = chunk([lonely, long_block])
    assert result.too_short_block_ids == [lonely.block_id]
    assert chunked_text(result, long_block.block_id).endswith("Het.")


def test_exactly_eight_chars_is_long_enough() -> None:
    only = block(0, "abcdefgh")
    result = chunk([only])
    assert result.too_short_block_ids == []
    assert [c.raw_text for c in result.chunks] == ["abcdefgh"]


def test_short_check_uses_collapsed_whitespace() -> None:
    only = block(0, "ab     cd")
    result = chunk([only])
    assert result.chunks == [] and result.too_short_block_ids == [only.block_id]


def test_block_without_tokens_is_too_short() -> None:
    only = block(0, "--------")
    result = chunk([only])
    assert result.chunks == [] and result.too_short_block_ids == [only.block_id]


def test_single_page_chunk_has_no_page_end_and_multi_page_has_one() -> None:
    first, second = block(0, words(10), page=1), block(0, words(10), page=2)
    (single,) = chunk([first]).chunks
    assert (single.page_number, single.page_end) == (1, None)
    (spanning,) = chunk([first, second]).chunks
    assert (spanning.page_number, spanning.page_end) == (1, 2)


def test_consecutive_line_pages_form_one_run() -> None:
    pages = [
        ConfirmedPage(page_number=2, lines=["Dong dau tien cua trang hai"]),
        ConfirmedPage(page_number=3, lines=["Dong dau tien cua trang ba"]),
    ]
    (single,) = chunk([], pages).chunks
    assert (single.page_number, single.page_end) == (2, 3)
    apart = [pages[0], ConfirmedPage(page_number=5, lines=["Dong dau tien cua trang nam"])]
    assert [(c.page_number, c.page_end) for c in chunk([], apart).chunks] == [(2, None), (5, None)]


def test_too_short_block_ids_sorted_and_unique() -> None:
    shorts = [block(0, "Ab.", section=SEC_A, key="x"), block(1, "Cd.", section=SEC_B, key="y")]
    shorts.append(block(2, "Ef.", section=SEC_C, key="z"))
    result = chunk(shorts)
    assert result.too_short_block_ids == sorted({b.block_id for b in shorts})
    assert result.too_short_block_ids != [b.block_id for b in shorts]  # thứ tự đọc khác thứ tự id: phép sắp có tác dụng


def test_quote_prefers_body_over_heading() -> None:
    title = block(0, "Tieu de day du cua muc", heading=True)
    body = block(1, "Noi dung chinh cua doan van.")
    (single,) = chunk([title, body]).chunks
    assert single.verbatim_quote == "Noi dung chinh cua doan van."


def test_fallback_quote_collapses_whitespace() -> None:
    parts = [block(i, text) for i, text in enumerate(["abc.", "def.", "ghi."])]
    (single,) = chunk(parts).chunks
    assert "\n" not in single.verbatim_quote
    assert single.verbatim_quote == normalize_quote(single.raw_text)
    assert quote_in_text(single.verbatim_quote, single.raw_text)


def test_block_order_does_not_depend_on_input_order() -> None:
    blocks = [
        block(0, "1. Mo dau", heading=True),
        block(1, words(300)),
        block(0, "2. Tiep theo", page=2, section=SEC_B, heading=True),
        block(1, words(300), page=2, section=SEC_B),
        block(2, words(300), page=2, section=SEC_B),
    ]
    expected = chunk(blocks)
    shuffled = chunk([blocks[3], blocks[0], blocks[4], blocks[2], blocks[1]])
    assert shuffled == expected
    assert len(expected.chunks) == 3
