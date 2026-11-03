"""Biên của ràng buộc trong `domain/blocks.py` (Block, Section, ExtractionLedger, CropImage)."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from holocron_core.domain.blocks import Block, CropImage, ExtractionLedger, Section

DOC = "doc_" + "a" * 16
BLK = "blk_" + "b" * 16
SEC = "sec_" + "c" * 16
CROP = "d" * 64
MAX_TITLE = 300
MAX_CROP_BYTES = 1_048_576


def _block(**over: Any) -> Block:
    data: dict[str, Any] = {
        "block_id": BLK,
        "document_id": DOC,
        "page_number": 1,
        "type": "paragraph",
        "bbox": (0.1, 0.1, 0.5, 0.5),
        "text": "abc",
        "source": "text_layer",
        "reading_order": 0,
    }
    data.update(over)
    return Block(**data)


def _crop(**over: Any) -> CropImage:
    data: dict[str, Any] = {
        "document_id": DOC,
        "block_id": BLK,
        "crop_ref": CROP,
        "page_number": 1,
        "bbox": (0.1, 0.1, 0.5, 0.5),
        "width_px": 10,
        "height_px": 10,
        "size_bytes": 100,
    }
    data.update(over)
    return CropImage(**data)


def _section(**over: Any) -> Section:
    data: dict[str, Any] = {
        "section_id": SEC,
        "document_id": DOC,
        "parent_id": None,
        "kind": "chapter",
        "level": 0,
        "title": "T",
        "ordinal": 0,
        "page_start": 1,
        "page_end": 1,
    }
    data.update(over)
    return Section(**data)


def test_block_bbox_zero_width_and_height_valid() -> None:
    assert _block(bbox=(0.3, 0.1, 0.3, 0.5)).bbox == (0.3, 0.1, 0.3, 0.5)
    assert _block(bbox=(0.1, 0.3, 0.5, 0.3)).bbox == (0.1, 0.3, 0.5, 0.3)


def test_crop_bbox_zero_width_and_height_valid() -> None:
    assert _crop(bbox=(0.3, 0.1, 0.3, 0.5)).bbox == (0.3, 0.1, 0.3, 0.5)
    assert _crop(bbox=(0.1, 0.3, 0.5, 0.3)).bbox == (0.1, 0.3, 0.5, 0.3)


def test_bbox_inverted_axes_rejected() -> None:
    for bad in [(0.5, 0.1, 0.3, 0.5), (0.1, 0.5, 0.5, 0.3)]:
        with pytest.raises(ValidationError):
            _block(bbox=bad)
        with pytest.raises(ValidationError):
            _crop(bbox=bad)


def test_heading_level_bounds() -> None:
    assert _block(type="heading", level=6).level == 6
    with pytest.raises(ValidationError):
        _block(type="heading", level=7)


def test_list_depth_bounds() -> None:
    assert _block(type="list_item", list_depth=8).list_depth == 8
    with pytest.raises(ValidationError):
        _block(type="list_item", list_depth=9)


def test_non_root_section_with_chapter_code_rejected() -> None:
    assert _section(chapter_code="ch1").chapter_code == "ch1"
    with pytest.raises(ValidationError):
        _section(kind="section", parent_id=SEC, level=1, chapter_code="ch1")


def test_ledger_empty_bases_rejected() -> None:
    with pytest.raises(ValidationError):
        ExtractionLedger(document_id=DOC, page_number=1, bases=[], balanced=True)


def test_crop_size_bytes_bounds() -> None:
    assert _crop(size_bytes=MAX_CROP_BYTES).size_bytes == MAX_CROP_BYTES
    with pytest.raises(ValidationError):
        _crop(size_bytes=MAX_CROP_BYTES + 1)


def test_section_title_length_bounds() -> None:
    assert len(_section(title="x" * MAX_TITLE).title or "") == MAX_TITLE
    with pytest.raises(ValidationError):
        _section(title="x" * (MAX_TITLE + 1))
