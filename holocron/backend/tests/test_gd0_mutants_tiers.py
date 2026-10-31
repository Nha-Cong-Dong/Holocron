"""Test biên cho cờ trang và lý do xem lại GĐ0 ở `tiers`/`text_layer` (cờ hình theo đối tượng nội dung, lý do cờ dòng,
chọn tầng). Giết đột biến sống của báo cáo 857e035. Ngưỡng đọc từ `PdfReaderConfig`."""

from __future__ import annotations

import math

from holocron_core.domain.documents import ExtractedLine
from holocron_core.infrastructure.document_readers.config import PdfReaderConfig
from holocron_core.infrastructure.document_readers.protocol import RawPdfPage, RawTextLine
from holocron_core.infrastructure.document_readers.text_layer import has_figure
from holocron_core.infrastructure.document_readers.tiers import (
    PageFlags,
    assemble_text_layer_page,
    line_flag_reasons,
    page_flags,
    select_tier,
)

CONFIG = PdfReaderConfig()
DOC_ID = "doc_00000000000000f2"
BODY = "Mot dong noi dung co du so tu de giu lop chu tren trang"
IMAGE_MIN = CONFIG.figure_image_area_min
PATH_MIN = CONFIG.figure_path_area_min


def _below(value: float) -> float:
    return math.nextafter(value, 0.0)


def _raw(
    *,
    image: float = 0.0,
    path: float = 0.0,
    content_image_area_ratio: float | None = None,
    content_path_area_ratio: float | None = None,
) -> RawPdfPage:
    return RawPdfPage(
        page_index=0,
        width_pt=612.0,
        height_pt=792.0,
        lines=[RawTextLine(text=BODY, bbox=(0.1, 0.3, 0.9, 0.34), words=[])],
        image_area_ratio=image,
        path_area_ratio=path,
        content_image_area_ratio=content_image_area_ratio,
        content_path_area_ratio=content_path_area_ratio,
    )


# --------------------------------------------------------------------------- #
# has_figure / page_flags
# --------------------------------------------------------------------------- #


def test_has_figure_thresholds_are_inclusive_and_the_path_branch_needs_an_image() -> None:
    assert has_figure(IMAGE_MIN, 0.0, CONFIG) is True
    assert has_figure(_below(IMAGE_MIN), 0.0, CONFIG) is False
    assert has_figure(0.01, PATH_MIN, CONFIG) is True
    assert has_figure(0.01, _below(PATH_MIN), CONFIG) is False
    assert has_figure(0.0, 1.0, CONFIG) is False, "đường vẽ không đứng một mình"
    assert has_figure(0.9, 0.0, CONFIG, content_image_area_ratio=0.0) is False, "nhánh ảnh dùng độ phủ nội dung"
    assert has_figure(0.9, 0.0, CONFIG, content_image_area_ratio=IMAGE_MIN) is True
    assert has_figure(0.9, 0.0, CONFIG, content_image_area_ratio=None) is True


def test_page_flags_figure_counts_only_content_objects() -> None:
    template_only = _raw(image=0.9, content_image_area_ratio=0.0, path=1.0, content_path_area_ratio=0.0)
    assert page_flags(template_only, BODY, CONFIG).has_figure is False
    content_image = _raw(image=0.9, content_image_area_ratio=IMAGE_MIN)
    assert page_flags(content_image, BODY, CONFIG).has_figure is True
    just_below = _raw(image=0.9, content_image_area_ratio=_below(IMAGE_MIN))
    assert page_flags(just_below, BODY, CONFIG).has_figure is False
    content_path = _raw(image=0.9, content_image_area_ratio=0.01, path=0.0, content_path_area_ratio=PATH_MIN)
    assert page_flags(content_path, BODY, CONFIG).has_figure is True
    below_path = _raw(image=0.9, content_image_area_ratio=0.01, path=1.0, content_path_area_ratio=_below(PATH_MIN))
    assert page_flags(below_path, BODY, CONFIG).has_figure is False
    no_content_image = _raw(image=0.9, content_image_area_ratio=0.0, path=1.0, content_path_area_ratio=PATH_MIN)
    assert page_flags(no_content_image, BODY, CONFIG).has_figure is False, "đường vẽ cần ảnh NỘI DUNG"


def test_page_flags_falls_back_to_the_total_areas_when_no_template_data() -> None:
    assert page_flags(_raw(image=IMAGE_MIN), BODY, CONFIG).has_figure is True
    assert page_flags(_raw(image=0.01, path=PATH_MIN), BODY, CONFIG).has_figure is True
    assert page_flags(_raw(image=0.01, path=_below(PATH_MIN)), BODY, CONFIG).has_figure is False
    assert page_flags(_raw(), BODY, CONFIG) == PageFlags()


# --------------------------------------------------------------------------- #
# line_flag_reasons, select_tier
# --------------------------------------------------------------------------- #


def _flagged(*flags: str) -> ExtractedLine:
    return ExtractedLine.model_validate(
        {"line_index": 0, "text": "x", "bbox": None, "conf": 1.0, "engine": "text_layer", "flags": list(flags)}
    )


def test_line_flag_reasons_follow_the_declared_order_and_report_each_once() -> None:
    assert line_flag_reasons([]) == []
    assert line_flag_reasons([_flagged()]) == []
    assert line_flag_reasons([_flagged("TEXT_OCCLUDED")]) == ["TEXT_OCCLUDED"]
    lines = [
        _flagged("FONT_ENCODING_SUSPECT"),
        _flagged("TEXT_OCCLUDED", "LOW_TEXT_CONTRAST"),
        _flagged("TEXT_OCCLUDED"),
    ]
    assert line_flag_reasons(lines) == ["LOW_TEXT_CONTRAST", "TEXT_OCCLUDED", "FONT_ENCODING_SUSPECT"]
    assert line_flag_reasons([_flagged("LOW_TEXT_CONTRAST")]) == ["LOW_TEXT_CONTRAST"]
    assert line_flag_reasons([_flagged("FONT_ENCODING_SUSPECT")]) == ["FONT_ENCODING_SUSPECT"]


def test_select_tier_threshold_and_flags() -> None:
    minimum = CONFIG.text_layer_min
    assert select_tier(minimum, PageFlags(), CONFIG, layout_available=False).tier == "text_layer"
    assert select_tier(_below(minimum), PageFlags(), CONFIG, layout_available=False).tier == "ocr"
    for flags in (PageFlags(has_table=True), PageFlags(has_formula=True), PageFlags(has_figure=True)):
        unavailable = select_tier(1.0, flags, CONFIG, layout_available=False)
        assert unavailable.tier == "text_layer" and unavailable.review_reasons == ("TIER3_UNAVAILABLE",)
        available = select_tier(1.0, flags, CONFIG, layout_available=True)
        assert available.tier == "layout" and available.review_reasons == ()
    plain = select_tier(1.0, PageFlags(), CONFIG, layout_available=True)
    assert plain.tier == "text_layer" and plain.review_reasons == ()


def test_figure_flag_alone_does_not_add_tier3_reason_to_a_text_layer_page() -> None:
    """GĐ0: cờ hình không đưa trang lên tầng 3 (luật 0C), nên không có `TIER3_UNAVAILABLE` chỉ vì hình."""
    raw = _raw(image=0.9, content_image_area_ratio=IMAGE_MIN)
    built = assemble_text_layer_page(raw, DOC_ID, CONFIG)
    assert built.has_figure is True
    assert "TIER3_UNAVAILABLE" not in built.review_reasons
    assert built.tier_used == "text_layer"


def test_text_layer_chunks_are_cut_after_nfc_and_keep_consecutive_indices() -> None:
    decomposed = "é" * 3000
    raw = RawPdfPage(
        page_index=0,
        width_pt=612.0,
        height_pt=792.0,
        lines=[RawTextLine(text="  " + decomposed + "  ", bbox=(0.1, 0.3, 0.9, 0.34), words=[])],
        image_area_ratio=0.0,
        path_area_ratio=0.0,
    )
    built = assemble_text_layer_page(raw, DOC_ID, CONFIG)
    assert [line.line_index for line in built.lines] == list(range(len(built.lines)))
    assert "".join(line.text for line in built.lines) == "é" * 3000
    assert len(built.lines) == 2
