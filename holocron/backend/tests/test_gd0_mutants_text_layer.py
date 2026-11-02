"""Test biên cho lớp chữ GĐ0 (`text_layer`: ghép ký tự rời, cổng glyph, token, header/footer, luật chọn OCR) và cờ
`tiers`. Giết đột biến sống của báo cáo 857e035. Ngưỡng đọc từ `PdfReaderConfig`; dữ liệu dựng để đại lượng đúng
BẰNG ngưỡng. Không PDF thật, không mạng, không eval_data."""

from __future__ import annotations

import math
import unicodedata

import pytest

from holocron_core.infrastructure.document_readers.config import PdfReaderConfig
from holocron_core.infrastructure.document_readers.protocol import RawPdfPage, RawTextLine, TextKey
from holocron_core.infrastructure.document_readers.template_images import quantize_bbox, text_key
from holocron_core.infrastructure.document_readers.text_layer import (
    PdfChar,
    content_token_count,
    font_encoding_suspect,
    group_lines,
    image_needs_reading,
    mark_header_footer,
    merge_spaced_chars,
    ocr_reason,
    tokens,
)
from holocron_core.infrastructure.document_readers.tiers import denoise_unavailable

CONFIG = PdfReaderConfig()
PAGE_BOX = (0.0, 0.0, 612.0, 792.0)


# --------------------------------------------------------------------------- #
# merge_spaced_chars
# --------------------------------------------------------------------------- #


def _run(
    letters: str,
    *,
    gaps: list[float] | None = None,
    bottoms: list[float] | None = None,
    height: float = 8.0,
    separator: list[PdfChar] | None = None,
) -> list[PdfChar]:
    """Chuỗi chữ đứng riêng, rộng 6 pt, nối bằng ký tự tự sinh `separator` (mặc định `\\r\\n`)."""
    out: list[PdfChar] = []
    x = 50.0
    for index, char in enumerate(letters):
        if index:
            out.extend(
                [PdfChar("\r", None, generated=True), PdfChar("\n", None, generated=True)]
                if separator is None
                else separator
            )
            x += 6.0 + (2.0 if gaps is None else gaps[index - 1])
        bottom = 100.0 if bottoms is None else bottoms[index]
        out.append(PdfChar(char, (x, bottom, x + 6.0, bottom + height)))
    return out


def _text(chars: list[PdfChar]) -> str:
    return "".join(char.text for char in chars)


def test_run_length_threshold_is_inclusive() -> None:
    minimum = CONFIG.char_merge_min_chars
    below = _run("ABCDEFGHIJ"[: minimum - 1])
    assert merge_spaced_chars(below, CONFIG) == below
    exact = _run("ABCDEFGHIJ"[:minimum])
    assert _text(merge_spaced_chars(exact, CONFIG)) == "ABCDEFGHIJ"[:minimum]
    config = PdfReaderConfig(char_merge_min_chars=minimum + 1)
    assert merge_spaced_chars(exact, config) == exact


def test_baseline_tolerance_is_inclusive_and_scaled_by_the_character_height() -> None:
    config = PdfReaderConfig(char_merge_baseline_tol=0.25)
    height = 8.0
    at_limit = _run("ABC", bottoms=[100.0, 102.0, 100.0], height=height)  # lệch 2.0 = 0.25 × 8
    assert _text(merge_spaced_chars(at_limit, config)) == "ABC"
    beyond = _run("ABC", bottoms=[100.0, 102.5, 100.0], height=height)
    assert merge_spaced_chars(beyond, config) == beyond
    tall = _run("ABC", bottoms=[100.0, 104.0, 100.0], height=16.0)  # 4.0 = 0.25 × 16: cùng tỉ lệ, nên vẫn ghép
    assert _text(merge_spaced_chars(tall, config)) == "ABC"
    tight = PdfReaderConfig(char_merge_baseline_tol=0.2)
    assert merge_spaced_chars(at_limit, tight) == at_limit


def test_gap_tolerance_is_inclusive_around_the_median_gap() -> None:
    config = PdfReaderConfig(char_merge_gap_tol=0.125)  # × cao 8 = 1.0
    median = 2.0
    for gap, merged in ((median + 1.0, True), (median - 1.0, True), (median + 0.0, True), (median - 1.5, False)):
        chars = _run("ABCD", gaps=[median, gap, median])
        result = merge_spaced_chars(chars, config)
        assert (_text(result) == "ABCD") is merged, gap
        if not merged:
            assert result == chars


def test_gap_beyond_the_tolerance_is_a_word_boundary_that_keeps_one_generated_space() -> None:
    config = PdfReaderConfig(char_merge_gap_tol=0.125)
    median = 2.0
    separator = [
        PdfChar("\r", None, generated=True),
        PdfChar(" ", None, generated=True),
        PdfChar("\n", None, generated=True),
    ]
    chars = _run("ABCD", gaps=[median, median + 1.5, median], separator=separator)
    merged = merge_spaced_chars(chars, config)
    assert [line.text for line in group_lines(merged, PAGE_BOX, 0)] == ["AB CD"]
    assert sum(1 for char in merged if char.generated and char.text == " ") == 1
    only_breaks = _run("ABCD", gaps=[median, median + 1.5, median])
    assert merge_spaced_chars(only_breaks, config) == only_breaks, "không có ký tự trắng tự sinh ⇒ không ghép (I3)"


def test_zero_height_characters_are_never_merged() -> None:
    flat = _run("ABC", height=0.0)
    assert merge_spaced_chars(flat, CONFIG) == flat


def test_a_run_needs_standalone_letters_on_both_sides() -> None:
    glued = [PdfChar("x", (20.0, 100.0, 26.0, 108.0)), *_run("ABC")]
    assert merge_spaced_chars(glued, CONFIG) == glued, "chữ dính liền chữ khác không phải ký tự đứng riêng"
    trailing = [*_run("ABC"), PdfChar("y", (200.0, 100.0, 206.0, 108.0))]
    assert merge_spaced_chars(trailing, CONFIG) == trailing
    both = [PdfChar("\n", None, generated=True), *_run("ABC"), PdfChar("\n", None, generated=True)]
    assert _text(merge_spaced_chars(both, CONFIG)) == "\n" + "ABC" + "\n"
    assert len(merge_spaced_chars(both, CONFIG)) == 5


def test_characters_without_a_box_or_text_break_the_run() -> None:
    no_box = _run("ABC")
    no_box[2] = PdfChar("B", None)
    assert merge_spaced_chars(no_box, CONFIG) == no_box
    separated = _run("ABC") + [PdfChar("\n", None, generated=True)] + _run("DEF")
    merged = merge_spaced_chars(separated, CONFIG)
    assert "".join(char.text for char in merged if not char.generated) == "ABCDEF"


def test_a_real_whitespace_between_runs_keeps_both_untouched() -> None:
    """Ký tự trắng THẬT cạnh một chữ làm chữ đó không còn "đứng riêng": hai chuỗi chỉ còn 2 chữ, dưới ngưỡng."""
    first = _run("ABC")
    second = [
        PdfChar(char.text, (char.box[0] + 200.0, char.box[1], char.box[2] + 200.0, char.box[3])) if char.box else char
        for char in _run("DEF")
    ]
    chars = [*first, PdfChar(" ", None), *second]
    assert merge_spaced_chars(chars, CONFIG) == chars


def test_merge_never_changes_the_non_generated_characters() -> None:
    chars = _run("ABCDEFG", gaps=[2.0, 2.0, 9.0, 2.0, 2.0, 2.0], separator=[PdfChar(" ", None, generated=True)])
    merged = merge_spaced_chars(chars, CONFIG)
    assert [char for char in merged if not char.generated] == [char for char in chars if not char.generated]


# --------------------------------------------------------------------------- #
# font_encoding_suspect
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("code", [0xE000, 0xE001, 0xF000, 0xF8FE, 0xF8FF])
def test_private_use_area_is_suspect_including_both_ends(code: int) -> None:
    assert font_encoding_suspect(f"abc{chr(code)}def") is True
    assert font_encoding_suspect(chr(code)) is True


@pytest.mark.parametrize("code", [0xDFFF - 0x1000, 0xD7FF, 0xF900, 0xFB00, 0xFFFC])
def test_characters_just_outside_the_private_use_area_are_fine(code: int) -> None:
    char = chr(code)
    assert unicodedata.category(char) not in ("Co", "Cc")
    assert font_encoding_suspect(f"abc{char}def") is False


@pytest.mark.parametrize("char", ["\u0000", "\u0001", "\u001f", "\u007f", "\u0080", "\u009f", "\n", "\r"])
def test_control_characters_are_suspect_except_tab(char: str) -> None:
    assert font_encoding_suspect(f"abc{char}def") is True


def test_tab_and_the_replacement_character() -> None:
    assert font_encoding_suspect("abc\tdef") is False
    assert font_encoding_suspect("abc�def") is True
    assert font_encoding_suspect("") is False
    assert font_encoding_suspect("Trang chu thuong") is False


def test_latin1_symbols_are_suspect_only_next_to_a_letter() -> None:
    for code in (0x00A1, 0x00A9, 0x00AE, 0x00B5, 0x00BF):
        symbol = chr(code)
        assert font_encoding_suspect(f"a{symbol}") is True, hex(code)
        assert font_encoding_suspect(f"{symbol}a") is True, hex(code)
        assert font_encoding_suspect(f"1{symbol}2") is False
        assert font_encoding_suspect(f" {symbol} ") is False
        assert font_encoding_suspect(symbol) is False
        assert font_encoding_suspect(f"1{symbol}") is False
        assert font_encoding_suspect(f"{symbol}1") is False
    for code in (0x00A1, 0x00AE, 0x00BF):
        symbol = chr(code)
        assert font_encoding_suspect(f"1{symbol}b") is True, "chữ cái ngay SAU, chữ số ngay trước"
        assert font_encoding_suspect(f"b{symbol}1") is True, "chữ cái ngay TRƯỚC, chữ số ngay sau"
        assert font_encoding_suspect(f"1{symbol}1b") is False, "chữ cái cách một ký tự không phải liền kề"
        assert font_encoding_suspect(f"b1{symbol}1") is False
    assert font_encoding_suspect("a×") is True
    assert font_encoding_suspect("÷b") is True
    assert font_encoding_suspect("2×3") is False
    assert font_encoding_suspect("6÷2") is False
    assert font_encoding_suspect("a b") is False, "U+00A0 không thuộc U+00A1–U+00BF"
    assert font_encoding_suspect("aÀ") is False, "U+00C0 là chữ À"
    assert font_encoding_suspect("aÖ") is False
    assert font_encoding_suspect("x ®") is False, "cách bởi khoảng trắng không phải liền kề"


# --------------------------------------------------------------------------- #
# tokens, content_token_count, mark_header_footer
# --------------------------------------------------------------------------- #


def test_tokens_are_nfc_casefolded_word_runs() -> None:
    assert tokens("Xin Chao, THE GIOI!") == ["xin", "chao", "the", "gioi"]
    assert tokens("a_b c-d x2") == ["a_b", "c", "d", "x2"]
    assert tokens("") == []
    assert tokens("...") == []
    composed = "Đà Nẵng"
    decomposed = unicodedata.normalize("NFD", composed)
    assert tokens(decomposed) == tokens(composed) == ["đà", "nẵng"]
    assert tokens("STRASSE") == ["strasse"] and tokens("Straße") == ["strasse"]


def _line(
    text: str, *, header: bool = False, bbox: tuple[float, float, float, float] | None = (0.1, 0.1, 0.9, 0.14)
) -> RawTextLine:
    return RawTextLine(text=text, bbox=bbox, words=[], header_footer=header)


def test_content_token_count_skips_header_footer_lines() -> None:
    lines = [_line("mot hai ba"), _line("tieu de lap lai", header=True), _line("bon nam")]
    assert content_token_count(lines) == 5
    assert content_token_count([]) == 0
    assert content_token_count([_line("chi header", header=True)]) == 0
    assert content_token_count(iter(lines)) == 5


def _text_keys_for(text: str, bbox: tuple[float, float, float, float]) -> frozenset[TextKey]:
    key = text_key(text, bbox, CONFIG.template_image_bbox_step)
    assert key is not None
    return frozenset({key})


def test_mark_header_footer_flags_only_lines_that_match_a_template_key() -> None:
    step = CONFIG.template_image_bbox_step
    bbox = (0.1, 0.02, 0.5, 0.05)
    keys = _text_keys_for("Trang 7", bbox)
    lines = [_line("Trang 8", bbox=bbox), _line("Noi dung", bbox=bbox), _line("Trang 8", bbox=(0.1, 0.5, 0.5, 0.53))]
    marked = mark_header_footer(lines, keys, step)
    assert [line.header_footer for line in marked] == [True, False, False]
    assert [line.text for line in marked] == [line.text for line in lines], "chỉ gắn cờ, không đổi chữ (I3)"
    assert all(not line.header_footer for line in lines), "không sửa dòng gốc"
    near = quantize_bbox(bbox, step)
    shifted = (bbox[0] + step, bbox[1], bbox[2], bbox[3])
    assert near != quantize_bbox(shifted, step)
    assert mark_header_footer([_line("Trang 8", bbox=shifted)], keys, step)[0].header_footer is True, "lệch một ô lưới"
    far = (bbox[0] + 3 * step, bbox[1], bbox[2], bbox[3])
    assert mark_header_footer([_line("Trang 8", bbox=far)], keys, step)[0].header_footer is False
    assert mark_header_footer([_line("Trang 8", bbox=None)], keys, step)[0].header_footer is False
    assert mark_header_footer([_line("   ", bbox=bbox)], keys, step)[0].header_footer is False


def test_mark_header_footer_without_keys_returns_an_equal_copy() -> None:
    lines = [_line("a"), _line("b", header=True)]
    result = mark_header_footer(lines, frozenset(), CONFIG.template_image_bbox_step)
    assert result == lines and result is not lines


# --------------------------------------------------------------------------- #
# ocr_reason: biên các luật (c), (d), (e)
# --------------------------------------------------------------------------- #


def _raw(
    lines: list[RawTextLine],
    image: float,
    *,
    objects_over_cap: bool = False,
    template_scan_ok: bool = True,
) -> RawPdfPage:
    return RawPdfPage(
        page_index=0,
        width_pt=612.0,
        height_pt=792.0,
        lines=lines,
        image_area_ratio=image,
        path_area_ratio=0.0,
        content_image_area_ratio=image,
        objects_over_cap=objects_over_cap,
        template_scan_ok=template_scan_ok,
    )


def _below(value: float) -> float:
    return math.nextafter(value, 0.0)


def _body(non_ws: int) -> list[RawTextLine]:
    """Một dòng có đúng `non_ws` ký tự không trắng, một token."""
    return [_line("a" * non_ws)]


def test_sparse_rule_boundaries() -> None:
    config = PdfReaderConfig(text_full_image_min=1.0, figure_text_min_tokens=1)
    chars, image = config.text_sparse_chars, config.text_sparse_image_min
    assert ocr_reason(_raw(_body(chars - 1), image), config) == "sparse_text_on_image"
    assert ocr_reason(_raw(_body(chars), image), config) is None
    assert ocr_reason(_raw(_body(chars - 1), _below(image)), config) is None


def test_sparse_rule_ignores_header_footer_characters() -> None:
    config = PdfReaderConfig(text_full_image_min=1.0, figure_text_min_tokens=1)
    chars, image = config.text_sparse_chars, config.text_sparse_image_min
    lines = [*_body(chars - 1), _line("x" * 100, header=True)]
    assert ocr_reason(_raw(lines, image), config) == "sparse_text_on_image"


def test_full_image_rule_boundaries() -> None:
    config = PdfReaderConfig(figure_text_min_tokens=1)
    chars, image = config.text_full_image_max_chars, config.text_full_image_min
    assert ocr_reason(_raw(_body(chars - 1), image), config) == "image_covers_page"
    assert ocr_reason(_raw(_body(chars), image), config) is None
    assert ocr_reason(_raw(_body(chars - 1), _below(image)), config) is None


def test_full_image_rule_counts_header_footer_characters() -> None:
    config = PdfReaderConfig(figure_text_min_tokens=1)
    chars, image = config.text_full_image_max_chars, config.text_full_image_min
    lines = [*_body(chars - 10), _line("x" * 10, header=True)]
    assert ocr_reason(_raw(lines, image), config) is None, "luật (d) tính mọi dòng"
    lines = [*_body(chars - 11), _line("x" * 10, header=True)]
    assert ocr_reason(_raw(lines, image), config) == "image_covers_page"


def test_image_needs_reading_boundaries() -> None:
    tokens_min, image = CONFIG.figure_text_min_tokens, CONFIG.image_read_area_min
    config = PdfReaderConfig(text_sparse_chars=1, text_full_image_max_chars=1)

    def words(count: int) -> list[RawTextLine]:
        return [_line(" ".join(f"w{index}" * 3 for index in range(count)))]

    assert image_needs_reading(_raw(words(tokens_min - 1), image), config) is True
    assert image_needs_reading(_raw(words(tokens_min), image), config) is False
    assert image_needs_reading(_raw(words(tokens_min - 1), _below(image)), config) is False
    assert image_needs_reading(_raw([], image), config) is True
    assert ocr_reason(_raw(words(tokens_min - 1), image), config) == "image_needs_reading"
    assert ocr_reason(_raw(words(tokens_min), image), config) is None
    with_header = [*words(tokens_min - 1), _line("tieu de lap lai nhieu tu", header=True)]
    assert image_needs_reading(_raw(with_header, image), config) is True


def test_rule_order_is_empty_garbled_sparse_full_then_reading() -> None:
    assert ocr_reason(_raw([], 0.0), CONFIG) == "empty_text_layer"
    assert ocr_reason(_raw([_line("�" * 40)], 0.0), CONFIG) == "garbled_text_layer"
    assert ocr_reason(_raw([_line("a" * (CONFIG.text_sparse_chars - 1))], CONFIG.text_sparse_image_min), CONFIG) == (
        "sparse_text_on_image"
    )


def test_garbled_rule_uses_the_text_layer_minimum_inclusively() -> None:
    junk = "�"
    # 20 ký tự: một ký tự rác ⇒ điểm 0.95; ngưỡng bằng đúng điểm thì không rác.
    text = "a" * 19 + junk
    config_equal = PdfReaderConfig(text_layer_min=0.95)
    config_above = PdfReaderConfig(text_layer_min=0.96)
    assert ocr_reason(_raw([_line(text)], 0.0), config_equal) is None
    assert ocr_reason(_raw([_line(text)], 0.0), config_above) == "garbled_text_layer"


# --------------------------------------------------------------------------- #
# tiers.denoise_unavailable
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("over_cap", [False, True])
@pytest.mark.parametrize("scan_ok", [False, True])
def test_denoise_unavailable_truth_table(over_cap: bool, scan_ok: bool) -> None:
    raw = _raw([_line("mot dong")], 0.0, objects_over_cap=over_cap, template_scan_ok=scan_ok)
    assert denoise_unavailable(raw) is (over_cap or not scan_ok)
