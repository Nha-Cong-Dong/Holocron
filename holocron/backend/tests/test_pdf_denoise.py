"""Khử nhiễu trang GĐ0 phía worker: 0A (đối tượng, template, nền, cờ hình), 0B (chất lượng chữ), 0C (đọc ảnh).

Spec §5.7 "Khử nhiễu trang và vai trò trang"; ADR-008 mục 6, 9, 12. PDF tổng hợp tự tạo (`tests/denoise_samples.py`),
không mạng, không eval_data, không Tesseract thật.
"""

from __future__ import annotations

import time
import unicodedata
from pathlib import Path

import pytest
from pydantic import ValidationError

from holocron_core.domain.documents import ExtractedLine
from holocron_core.infrastructure.document_readers import _pdf_worker
from holocron_core.infrastructure.document_readers.config import PdfReaderConfig
from holocron_core.infrastructure.document_readers.page_objects import (
    PageObject,
    background_paths,
    compose,
    contrast_ratio,
    covered_fraction,
    transform_box,
)
from holocron_core.infrastructure.document_readers.pdfium_adapter import PdfiumBackend, PdfiumDocument
from holocron_core.infrastructure.document_readers.protocol import (
    PageMessage,
    PageRequest,
    PathKey,
    RawOcrLine,
    RawOcrPage,
    RawPdfPage,
    RawTextLine,
    TemplateScan,
    TemplateScanMessage,
    TextKey,
    WorkerRequest,
)
from holocron_core.infrastructure.document_readers.template_images import (
    PageImageKeys,
    TemplateFilter,
    find_template_images,
    normalize_template_text,
    scan_template_images,
)
from holocron_core.infrastructure.document_readers.text_layer import (
    PdfChar,
    content_token_count,
    font_encoding_suspect,
    group_lines,
    merge_spaced_chars,
    ocr_reason,
)
from holocron_core.infrastructure.document_readers.tiers import assemble_text_layer_page
from tests.denoise_samples import Form, SyntheticPage, build, form, gradient, image, rect, solid, text

CONFIG = PdfReaderConfig()
DOC_ID = "doc_00000000000000d0"
RED = (0.8, 0.0, 0.0)
BODY = "Mot dong noi dung co du so tu de giu lop chu tren trang"  # 12 token


def _open(pages: list[SyntheticPage]) -> PdfiumDocument:
    opened = PdfiumBackend.load().open(build(pages))
    assert not isinstance(opened, str)
    return opened


def _read(pages: list[SyntheticPage], index: int = 0, config: PdfReaderConfig = CONFIG) -> RawPdfPage:
    with _open(pages) as document:
        return document.read_page(index, None, config)


def _texts(raw: RawPdfPage) -> list[str]:
    return [line.text for line in raw.lines]


def _raw_text(pages: list[SyntheticPage], index: int = 0) -> str:
    """Văn bản lớp chữ THÔ của trang (mọi ký tự PDFium trả, kể cả ký tự tự sinh), để kiểm I3."""
    with _open(pages) as document:
        page = document.backend.call(document._handle, "get_page", index)
        try:
            chars = document._read_chars(page)
        finally:
            document.backend.call(page, "close")
    return unicodedata.normalize("NFC", "".join(char.text for char, _ in chars))


def _is_subsequence(needle: str, haystack: str) -> bool:
    it = iter(haystack)
    return all(char in it for char in needle)


def _mid_contrast_rgb() -> tuple[float, float, float]:
    """Màu chữ trên nền đỏ `RED` có tương phản WCAG nằm giữa `hidden_text_drop_max` và `hidden_text_keep_min`."""
    background = (204, 0, 0)
    for value in range(0, 256, 5):
        color = (value, value, value)
        ratio = contrast_ratio(color, background)
        if CONFIG.hidden_text_drop_max + 0.1 < ratio < CONFIG.hidden_text_keep_min - 0.1:
            return (value / 255, value / 255, value / 255)
    raise AssertionError("không tìm được màu tương phản vùng giữa")


# --------------------------------------------------------------------------- #
# 0A: hộp thật qua form XObject, nền, ảnh nội dung
# --------------------------------------------------------------------------- #


def test_matrix_helpers_compose_like_pdf() -> None:
    inner = (2.0, 0.0, 0.0, 2.0, 10.0, 10.0)
    outer = (1.0, 0.0, 0.0, 1.0, 100.0, 0.0)
    assert transform_box((0.0, 0.0, 10.0, 10.0), compose(outer, inner)) == (110.0, 10.0, 130.0, 30.0)
    assert covered_fraction((0.0, 0.0, 10.0, 10.0), [(0.0, 0.0, 5.0, 10.0), (0.0, 0.0, 5.0, 10.0)]) == 0.5


def test_image_inside_nested_forms_is_measured_after_matrices() -> None:
    # Ảnh 150×195 trong Fm2, Fm2 trong Fm1, Fm1 nhân 2 lần và dời (10, 10): hộp thật 300×390 pt (≈ 0.24 trang).
    inner = Form("Fm2", image("B", 0, 0, 150, 195))
    outer = Form("Fm1", form("Fm2") + b"\n" + text(10, 380, "Inside nested form"))
    raw = _read(
        [SyntheticPage(ops=[form("Fm1", (2, 0, 0, 2, 10, 10))], forms=[outer, inner], images={"B": gradient()})]
    )
    assert raw.image_area_ratio == pytest.approx(300 * 390 / (612 * 792), rel=1e-3)
    assert len(raw.own_images) == 1
    assert raw.own_images[0].bbox == pytest.approx((10 / 612, 1 - 400 / 792, 310 / 612, 1 - 10 / 792), rel=1e-3)
    assert _texts(raw) == ["Inside nested form"]


def test_content_image_never_background() -> None:
    """Ảnh không-template phủ ≥ 0.98 vẽ DƯỚI một dòng header vẫn là ảnh nội dung (tương đương ch4 tr.22): trang OCR
    theo điều kiện (d) `image_covers_page`, không bao giờ coi ảnh đó là nền."""
    header = "Header line ve tren anh noi dung phu gan kin ca trang slide nay"  # 55 ký tự không trắng
    page = SyntheticPage(
        ops=[image("C", 6, 6, 600, 780), text(40, 770, header, size=9)], images={"C": gradient(seed=5)}
    )
    raw = _read([page])
    assert raw.image_area_ratio >= 0.96 and len(raw.own_images) == 1
    assert ocr_reason(raw, CONFIG) == "image_covers_page"
    failed = assemble_text_layer_page(raw, DOC_ID, CONFIG)
    assert (failed.status, failed.failure_reason) == ("failed", "OCR_ENGINE_MISSING")


def test_solid_path_drawn_before_text_is_background_not_figure() -> None:
    page = SyntheticPage(ops=[rect(0, 0, 612, 792, rgb=(0.2, 0.3, 0.6)), text(50, 700, BODY, rgb=(1, 1, 1))])
    raw = _read([page])
    assert raw.path_area_ratio == pytest.approx(1.0)
    assert raw.content_path_area_ratio == 0.0
    built = assemble_text_layer_page(raw, DOC_ID, CONFIG)
    assert not built.has_figure and built.status == "ok"


def test_path_drawn_after_text_is_not_background() -> None:
    objects = [
        PageObject(kind="text", z=0, box=(10.0, 10.0, 50.0, 20.0)),
        PageObject(kind="path", z=1, box=(0.0, 0.0, 100.0, 100.0), fill=(0, 0, 0, 255)),
        PageObject(kind="path", z=2, box=(0.0, 0.0, 100.0, 100.0), fill=(0, 0, 0, 128)),
    ]
    paths = [obj for obj in objects if obj.kind == "path"]
    assert background_paths(paths, [(0, objects[0].box)], (0.0, 0.0, 100.0, 100.0), CONFIG) == frozenset()
    early = PageObject(kind="path", z=0, box=(0.0, 0.0, 100.0, 100.0), fill=(9, 9, 9, 255))
    assert background_paths([early], [(3, (10.0, 10.0, 50.0, 20.0))], (0.0, 0.0, 100.0, 100.0), CONFIG) == {0}
    small = PageObject(kind="path", z=0, box=(0.0, 0.0, 40.0, 40.0), fill=(9, 9, 9, 255))
    assert background_paths([small], [], (0.0, 0.0, 100.0, 100.0), CONFIG) == frozenset()


def test_figure_needs_content_image_and_path_branch_needs_an_image() -> None:
    vector_frame = SyntheticPage(ops=[text(50, 700, BODY), rect(30, 100, 550, 600, rgb=(0.5, 0.5, 0.5))])
    raw = _read([vector_frame])
    assert raw.content_path_area_ratio is not None and raw.content_path_area_ratio >= CONFIG.figure_path_area_min
    assert not assemble_text_layer_page(raw, DOC_ID, CONFIG).has_figure
    with_picture = SyntheticPage(ops=[*vector_frame.ops, image("P", 400, 50, 20, 20)], images={"P": gradient()})
    assert assemble_text_layer_page(_read([with_picture]), DOC_ID, CONFIG).has_figure


# --------------------------------------------------------------------------- #
# 0B(1): chữ ẩn, chữ trắng trên nền màu, lớp OCR của PDF scan
# --------------------------------------------------------------------------- #


def test_white_text_on_colored_background_is_kept() -> None:
    page = SyntheticPage(ops=[rect(0, 0, 612, 792, rgb=RED), text(50, 700, "White text on a red slide", rgb=(1, 1, 1))])
    raw = _read([page])
    assert _texts(raw) == ["White text on a red slide"]
    assert raw.hidden_chars_dropped == 0 and raw.lines[0].flags == []


def test_text_with_background_color_is_dropped_and_counted() -> None:
    page = SyntheticPage(
        ops=[rect(0, 0, 612, 792, rgb=RED), text(50, 700, "Visible words"), text(50, 600, "HiddenWords", rgb=RED)]
    )
    raw = _read([page])
    assert _texts(raw) == ["Visible words"]
    assert raw.hidden_chars_dropped == len("HiddenWords")


def test_mid_contrast_text_is_kept_and_flagged() -> None:
    page = SyntheticPage(
        ops=[rect(0, 0, 612, 792, rgb=RED), text(50, 700, "Low contrast words", rgb=_mid_contrast_rgb())]
    )
    raw = _read([page])
    assert _texts(raw) == ["Low contrast words"] and raw.lines[0].flags == ["LOW_TEXT_CONTRAST"]
    built = assemble_text_layer_page(raw, DOC_ID, CONFIG)
    assert built.lines[0].flags == ["LOW_TEXT_CONTRAST"] and "LOW_TEXT_CONTRAST" in built.review_reasons
    assert built.status == "needs_review"


def test_scan_ocr_layer_keeps_all_text() -> None:
    """PDF dạng scan (ảnh phủ cả trang) + lớp chữ render mode 3: không mất ký tự nào, không cờ chữ ẩn hay bị che."""
    words = ["Scanned page line one", "Second invisible line", "Third line with 2026 digits"]
    ops = [image("S", 0, 0, 612, 792)] + [text(50, 700 - 30 * i, value, mode=3) for i, value in enumerate(words)]
    raw = _read([SyntheticPage(ops=ops, images={"S": solid(255, 8, 8)})])
    assert raw.scan_text_layer
    assert _texts(raw) == words and raw.hidden_chars_dropped == 0
    assert all(line.flags == [] for line in raw.lines)


def test_invisible_text_on_other_pages_is_never_dropped() -> None:
    page = SyntheticPage(ops=[text(50, 700, BODY), text(50, 600, "Invisible but kept", mode=3)])
    raw = _read([page])
    assert not raw.scan_text_layer
    assert _texts(raw) == [BODY, "Invisible but kept"] and raw.hidden_chars_dropped == 0


# --------------------------------------------------------------------------- #
# 0B(2): chữ bị che
# --------------------------------------------------------------------------- #


def test_text_covered_by_later_path_or_image_is_flagged_not_dropped() -> None:
    page = SyntheticPage(
        ops=[
            text(50, 700, "Covered by a rectangle"),
            rect(40, 690, 300, 30, rgb=(0, 0, 1)),
            text(50, 600, "Covered by an image"),
            image("A", 40, 590, 300, 30),
            text(50, 500, "Not covered at all"),
        ],
        images={"A": gradient()},
    )
    raw = _read([page])
    assert _texts(raw) == ["Covered by a rectangle", "Covered by an image", "Not covered at all"]
    assert [line.flags for line in raw.lines] == [["TEXT_OCCLUDED"], ["TEXT_OCCLUDED"], []]
    built = assemble_text_layer_page(raw, DOC_ID, CONFIG)
    assert "TEXT_OCCLUDED" in built.review_reasons


def test_text_drawn_over_a_path_is_not_occluded() -> None:
    page = SyntheticPage(ops=[rect(40, 690, 300, 30, rgb=(0.9, 0.9, 0.2)), text(50, 700, "Text on a highlight")])
    assert _read([page]).lines[0].flags == []


def test_small_cover_below_threshold_is_not_occluded() -> None:
    page = SyntheticPage(ops=[text(50, 700, "Mostly visible text line"), rect(40, 690, 20, 30, rgb=(0, 0, 0))])
    assert _read([page]).lines[0].flags == []


# --------------------------------------------------------------------------- #
# 0B(3): cổng glyph
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "value",
    ["a�b", "ab\x07c", "abc", "¼q", "x¼", "a×b", "a÷b", "Gi¹o"],
)
def test_glyph_gate_flags_suspect_text(value: str) -> None:
    assert font_encoding_suspect(value)


@pytest.mark.parametrize("value", ["Plain words", "tab\tinside", "goc 90°", "1 × 2", "¿ hola", "x = 3"])
def test_glyph_gate_passes_clean_text(value: str) -> None:
    assert not font_encoding_suspect(value)


def test_glyph_flag_becomes_review_reason_without_rewriting_text() -> None:
    raw = RawPdfPage(
        page_index=0,
        width_pt=612.0,
        height_pt=792.0,
        lines=[RawTextLine(text="Gia tr¼ q " + BODY, bbox=(0.1, 0.1, 0.9, 0.12), words=[])],
        image_area_ratio=0.0,
        path_area_ratio=0.0,
    )
    built = assemble_text_layer_page(raw, DOC_ID, CONFIG)
    assert built.lines[0].text == "Gia tr¼ q " + BODY
    assert built.lines[0].flags == ["FONT_ENCODING_SUSPECT"] and built.review_reasons == ["FONT_ENCODING_SUSPECT"]


def test_denoise_line_flags_only_on_text_layer_lines() -> None:
    with pytest.raises(ValidationError):
        ExtractedLine(line_index=0, text="x", bbox=None, conf=1.0, engine="tesseract", flags=["TEXT_OCCLUDED"])
    with pytest.raises(ValidationError):
        ExtractedLine(line_index=0, text="x", bbox=None, conf=1.0, engine="human", is_header_footer=True)


# --------------------------------------------------------------------------- #
# 0B(4): ghép ký tự rời
# --------------------------------------------------------------------------- #


def _spaced(chars: str, x0: float, gap: float, *, y: float = 100.0, height: float = 10.0) -> list[PdfChar]:
    out: list[PdfChar] = []
    x = x0
    for index, char in enumerate(chars):
        if index:
            out.append(PdfChar("\r", None, generated=True))
            out.append(PdfChar("\n", None, generated=True))
        out.append(PdfChar(char, (x, y, x + 6.0, y + height)))
        x += 6.0 + gap
    return out


def test_spaced_characters_are_merged_into_one_line() -> None:
    page_box = (0.0, 0.0, 612.0, 792.0)
    chars = _spaced("CHUONG", 50.0, 2.0)
    assert [line.text for line in group_lines(chars, page_box, 0)] == list("CHUONG")
    merged = merge_spaced_chars(chars, CONFIG)
    assert [line.text for line in group_lines(merged, page_box, 0)] == ["CHUONG"]


def test_word_gap_keeps_one_generated_space() -> None:
    page_box = (0.0, 0.0, 612.0, 792.0)
    first = _spaced("ABC", 50.0, 2.0)
    second = _spaced("DEF", 50.0 + 3 * 8.0 + 10.0, 2.0)
    chars = [*first, PdfChar(" ", None, generated=True), *second]
    assert [line.text for line in group_lines(merge_spaced_chars(chars, CONFIG), page_box, 0)] == ["ABC DEF"]


def test_merge_leaves_normal_lines_and_uneven_runs_alone() -> None:
    page_box = (0.0, 0.0, 612.0, 792.0)
    normal = [PdfChar(c, (50.0 + 6 * i, 100.0, 56.0 + 6 * i, 110.0)) for i, c in enumerate("Hello")]
    assert merge_spaced_chars(normal, CONFIG) == normal
    two = _spaced("AB", 50.0, 2.0)
    assert merge_spaced_chars(two, CONFIG) == two  # dưới `char_merge_min_chars`
    uneven = _spaced("ABC", 50.0, 2.0)[:4] + [
        PdfChar("\n", None, generated=True),
        PdfChar("C", (50.0, 300.0, 56.0, 310.0)),
    ]
    assert [line.text for line in group_lines(merge_spaced_chars(uneven, CONFIG), page_box, 0)] == ["A", "B", "C"]


def test_merged_header_from_pdf_is_one_line() -> None:
    letters = [text(100 + 30 * i, 700, char, size=14) for i, char in enumerate("CHAPTER")]
    raw = _read([SyntheticPage(ops=[*letters, text(50, 600, BODY)])])
    assert "CHAPTER" in _texts(raw)


# --------------------------------------------------------------------------- #
# 0A/0B(5): template ảnh, đường vẽ, dòng chữ (một hàm), header/footer
# --------------------------------------------------------------------------- #


def _deck(page_count: int, *, with_logo: bool = True) -> list[SyntheticPage]:
    pages: list[SyntheticPage] = []
    for number in range(1, page_count + 1):
        ops = [
            rect(0, 740, 612, 52, rgb=(0.1, 0.2, 0.5)),
            text(30, 760, "Course header title", rgb=(1, 1, 1)),
            text(560, 20, f"{number}"),
            text(50, 600, f"Body {chr(64 + number) * 3} " + BODY),
            image("Own", 300, 200, 200, 150),
        ]
        images = {"Own": gradient(seed=number)}
        if with_logo:
            ops.insert(0, image("Logo", 540, 745, 40, 40))
            images["Logo"] = solid(90)
        pages.append(SyntheticPage(ops=ops, images=images))
    return pages


def test_find_template_images_handles_image_path_and_text_keys() -> None:
    path = PathKey(bbox=(0, 0, 100, 7), fill=(25, 51, 127, 255), stroke=None, segments=5)
    moved = PathKey(bbox=(0, 50, 100, 57), fill=(25, 51, 127, 255), stroke=None, segments=5)
    header = TextKey(bbox=(5, 1, 30, 3), text="Course header")
    assert find_template_images([[path]] * 4 + [[moved]], 5, CONFIG) == {path}
    assert find_template_images([[header]] * 5, 5, CONFIG) == {header}
    # Dòng lặp chỉ ở đầu tài liệu (độ trải < `header_footer_min_spread`) không phải header/footer.
    assert find_template_images([[header]] * 3 + [[]] * 7, 10, CONFIG) == frozenset()
    # Tài liệu 1–2 trang không có template loại nào.
    assert find_template_images([[path, header]] * 2, 2, CONFIG) == frozenset()
    assert normalize_template_text("  Trang  12 / ٣4 ") == "Trang ## / ##"


def test_scan_finds_template_paths_and_header_lines(tmp_path: Path) -> None:
    pages = _deck(5)
    with _open(pages) as document:
        scan = scan_template_images(document, 5, CONFIG)
        assert scan.status == "ok"
        assert len(scan.keys) == 1  # logo; ảnh nội dung mỗi trang một khác
        assert len(scan.path_keys) == 1  # dải tiêu đề vẽ bằng vector
        assert {key.text for key in scan.text_keys} == {"Course header title", "#"}
        templates = TemplateFilter.from_scan(scan, CONFIG)
        assert templates is not None
        raw = document.read_page(2, templates, CONFIG)
    assert [(line.text, line.header_footer) for line in raw.lines] == [
        ("Course header title", True),
        ("3", True),
        ("Body CCC " + BODY, False),
    ]
    assert raw.content_image_area_ratio == pytest.approx(200 * 150 / (612 * 792), rel=1e-3)
    assert raw.content_path_area_ratio == 0.0 and len(raw.own_images) == 1


def test_header_footer_not_counted_in_non_ws_or_tokens() -> None:
    header = RawTextLine(
        text="Course header title that repeats", bbox=(0.0, 0.0, 1.0, 0.05), words=[], header_footer=True
    )
    body = RawTextLine(text="short", bbox=(0.1, 0.5, 0.2, 0.52), words=[])
    raw = RawPdfPage(
        page_index=0, width_pt=612.0, height_pt=792.0, lines=[header, body], image_area_ratio=0.45, path_area_ratio=0.0
    )
    assert content_token_count(raw.lines) == 1
    # Luật (c): `non_ws` không tính dòng header (5 ký tự < 32) ⇒ trang ảnh ít chữ.
    assert ocr_reason(raw, CONFIG) == "sparse_text_on_image"
    without_header = raw.model_copy(update={"lines": [header.model_copy(update={"header_footer": False}), body]})
    assert ocr_reason(without_header, CONFIG) == "image_needs_reading"
    built = assemble_text_layer_page(raw.model_copy(update={"image_area_ratio": 0.0}), DOC_ID, CONFIG)
    assert built.content_token_count == 1 and built.lines[0].is_header_footer


def test_documents_of_one_or_two_pages_have_no_templates() -> None:
    with _open(_deck(2)) as document:
        scan = scan_template_images(document, 2, CONFIG)
    assert scan == TemplateScan(status="ok")


def test_page_with_too_many_paths_has_no_path_keys() -> None:
    config = PdfReaderConfig(template_scan_max_paths_per_page=2)
    pages = [SyntheticPage(ops=[rect(10 * i, 10, 5, 5) for i in range(3)] + [text(50, 600, BODY)]) for _ in range(4)]
    with _open(pages) as document:
        keys = document.page_image_keys(0, config)
        assert keys is not None and keys.paths == ()
        assert scan_template_images(document, 4, config).path_keys == []
        assert scan_template_images(document, 4, CONFIG).path_keys != []


def test_scan_key_over_cap_per_kind_is_over_cap() -> None:
    text_keys = tuple(TextKey(bbox=(i % 100, i // 100, 100, 100), text=f"line {i}") for i in range(1025))
    page = PageImageKeys(keys=[], hashed_bytes=0, texts=text_keys)

    class Source:
        def page_image_keys(self, index: int, config: PdfReaderConfig) -> PageImageKeys | None:
            return page

    assert scan_template_images(Source(), 3, CONFIG) == TemplateScan(status="over_cap")


# --------------------------------------------------------------------------- #
# 0C: khi nào phải đọc ảnh
# --------------------------------------------------------------------------- #


def _image_page(words: str, area: float) -> RawPdfPage:
    return RawPdfPage(
        page_index=0,
        width_pt=612.0,
        height_pt=792.0,
        lines=[RawTextLine(text=words, bbox=(0.1, 0.05, 0.9, 0.08), words=[])],
        image_area_ratio=area,
        path_area_ratio=0.0,
        content_image_area_ratio=area,
    )


SEVEN_TOKENS = "Bieudo thoigian cuabus dulieu tren vixuly ARMcortex"  # 7 token, 45 ký tự không trắng


def test_few_tokens_on_a_large_content_image_needs_reading() -> None:
    page = _image_page(SEVEN_TOKENS, CONFIG.image_read_area_min)
    assert content_token_count(page.lines) == 7
    assert ocr_reason(page, CONFIG) == "image_needs_reading"
    assert ocr_reason(_image_page(SEVEN_TOKENS, CONFIG.image_read_area_min - 0.01), CONFIG) is None
    eight = _image_page(SEVEN_TOKENS + " x", 0.35)
    assert content_token_count(eight.lines) == 8 and ocr_reason(eight, CONFIG) is None


def test_image_needs_reading_flag_on_ocr_page() -> None:
    page = _image_page(SEVEN_TOKENS, 0.30)
    ocr = RawOcrPage(
        dpi=300.0,
        lines=[RawOcrLine(text="Bieu do", bbox=(0.1, 0.05, 0.5, 0.08), words=[], conf=0.95)],
        ink_outside_text_ratio=0.0,
        secondary=None,
    )
    built = assemble_text_layer_page(page.model_copy(update={"ocr": ocr}), DOC_ID, CONFIG)
    assert built.tier_used == "ocr" and "IMAGE_NEEDS_READING" in built.review_reasons
    assert built.status == "needs_review" and built.content_token_count == 7


def test_figure_page_with_enough_tokens_has_no_figure_reason() -> None:
    page = _image_page(BODY, 0.35)
    built = assemble_text_layer_page(page, DOC_ID, CONFIG)
    assert built.has_figure and built.tier_used == "text_layer"
    assert built.review_reasons == [] and built.status == "ok"


def test_denoise_unavailable_flags_figure_pages() -> None:
    """Lượt quét cấp tài liệu không `ok` hoặc trang vượt trần đối tượng ⇒ trang giữ lớp chữ có hình mang
    `DENOISE_UNAVAILABLE`; trang không có hình thì không."""
    page = _image_page(BODY, 0.35)
    for broken in (
        page.model_copy(update={"template_scan_ok": False}),
        page.model_copy(update={"objects_over_cap": True}),
    ):
        built = assemble_text_layer_page(broken, DOC_ID, CONFIG)
        assert built.has_figure and built.review_reasons == ["DENOISE_UNAVAILABLE"]
    plain = _image_page(BODY, 0.0).model_copy(update={"template_scan_ok": False})
    assert assemble_text_layer_page(plain, DOC_ID, CONFIG).review_reasons == []


# --------------------------------------------------------------------------- #
# Trần 0A
# --------------------------------------------------------------------------- #


def test_page_objects_max_marks_page_over_cap_and_keeps_text() -> None:
    config = PdfReaderConfig(page_objects_max=5)
    ops = [rect(0, 0, 612, 792, rgb=RED), *[rect(5 * i, 5, 2, 2) for i in range(6)], text(50, 700, "Red text", rgb=RED)]
    raw = _read([SyntheticPage(ops=ops)], config=config)
    assert raw.objects_over_cap
    assert raw.hidden_chars_dropped == 0 and "Red text" in _texts(raw)  # không lọc chữ ẩn khi vượt trần
    assert not _read([SyntheticPage(ops=ops)]).objects_over_cap


def test_form_deeper_than_limit_is_over_cap() -> None:
    config = PdfReaderConfig(pdf_form_max_depth=2)
    forms = [Form("F1", form("F2")), Form("F2", form("F3")), Form("F3", image("I", 0, 0, 50, 50))]
    page = SyntheticPage(ops=[form("F1"), text(50, 700, BODY)], forms=forms, images={"I": gradient()})
    assert _read([page], config=config).objects_over_cap
    assert not _read([page]).objects_over_cap


def test_self_referencing_form_is_bounded() -> None:
    page = SyntheticPage(
        ops=[form("Loop"), text(50, 700, BODY)],
        forms=[Form("Loop", image("I", 0, 0, 5, 5) + b"\n" + form("Loop"))],
        images={"I": gradient()},
    )
    started = time.perf_counter()
    raw = _read([page])
    assert time.perf_counter() - started < CONFIG.text_layer_page_timeout_s
    assert BODY in _texts(raw)


def test_many_objects_page_reads_within_text_layer_timeout() -> None:
    """Hồi quy thời gian: trang nhiều đối tượng (gần trần) vẫn đọc xong dưới hạn của tầng 1."""
    ops: list[bytes] = [rect(0, 0, 612, 792, rgb=(0.95, 0.95, 0.95))]
    for i in range(1500):
        x, y = 20 + (i % 50) * 11, 40 + (i // 50) * 24
        ops.append(rect(x, y, 9, 9, rgb=(0.2, 0.4, 0.6)))
        ops.append(text(x, y + 10, "ab", size=6))
    started = time.perf_counter()
    raw = _read([SyntheticPage(ops=ops)])
    elapsed = time.perf_counter() - started
    assert not raw.objects_over_cap and raw.lines
    assert elapsed < CONFIG.text_layer_page_timeout_s


# --------------------------------------------------------------------------- #
# I3: lọc/đánh dấu không viết lại chữ
# --------------------------------------------------------------------------- #


def test_denoise_never_rewrites_text() -> None:
    """Mọi dòng tầng 1 sau GĐ0 là chuỗi con theo thứ tự của văn bản lớp chữ thô của trang (chữ ẩn, ký tự rời,
    header/footer, chữ bị che, chữ tương phản thấp)."""
    pages = _deck(4)
    for page in pages:
        page.ops.extend(
            [
                text(50, 400, "HiddenRun", rgb=(1, 1, 1)),
                *[text(100 + 25 * i, 300, char, size=12) for i, char in enumerate("SPACED")],
                text(50, 250, "Covered words"),
                rect(40, 240, 200, 30),
            ]
        )
    with _open(pages) as document:
        templates = TemplateFilter.from_scan(scan_template_images(document, 4, CONFIG), CONFIG)
        raws = [document.read_page(i, templates, CONFIG) for i in range(4)]
    for index, raw in enumerate(raws):
        source = _raw_text(pages, index)
        assert raw.hidden_chars_dropped == len("HiddenRun")
        assert any(line.header_footer for line in raw.lines)
        for line in assemble_text_layer_page(raw, DOC_ID, CONFIG).lines:
            assert _is_subsequence(line.text, source), line.text


# --------------------------------------------------------------------------- #
# Worker: lượt quét gửi khoá đường vẽ và dòng chữ; trang ghi trạng thái lượt quét
# --------------------------------------------------------------------------- #


def test_worker_marks_pages_when_scan_is_not_ok(tmp_path: Path) -> None:
    path = tmp_path / "deck.pdf"
    path.write_bytes(build(_deck(4)))
    emitted: list[object] = []
    request = WorkerRequest(path=str(path), page_budget=4, config=CONFIG, template_scan=TemplateScan(status="timeout"))
    _pdf_worker.run(request, PdfiumBackend.load(), [PageRequest(page_index=i) for i in range(4)], emitted.append)
    pages = [message.page for message in emitted if isinstance(message, PageMessage)]
    assert len(pages) == 4 and all(not page.template_scan_ok for page in pages)
    assert all(not line.header_footer for page in pages for line in page.lines)

    emitted.clear()
    request = WorkerRequest(path=str(path), page_budget=4, config=CONFIG, scan_templates=True)
    _pdf_worker.run(request, PdfiumBackend.load(), [PageRequest(page_index=i) for i in range(4)], emitted.append)
    scans = [message.scan for message in emitted if isinstance(message, TemplateScanMessage)]
    assert scans and scans[0].text_keys and scans[0].path_keys
    pages = [message.page for message in emitted if isinstance(message, PageMessage)]
    assert all(page.template_scan_ok for page in pages)
    assert all(any(line.header_footer for line in page.lines) for page in pages)
