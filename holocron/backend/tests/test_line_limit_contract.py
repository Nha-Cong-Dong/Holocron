"""Khoá hợp đồng giới hạn độ dài dòng giữa bộ đọc tài liệu và Knowledge Vault (ADR-004, mục LINE_TOO_LONG).

Không có mã lỗi riêng cho dòng quá dài; thay vào đó:
(a) chỉ có một hằng `MAX_LINE_CHARS` trong `holocron_core`, và giới hạn bộ đọc ≤ giới hạn Vault;
(b) bộ đọc (lắp trang lớp chữ và trang OCR) luôn cắt dòng dài thành các đoạn ≤ giới hạn, sau chuẩn hoá NFC;
(c) mọi dòng bộ đọc tạo ra, qua đúng biến đổi `_confirmed_lines`, vào được `ConfirmedPage` của Vault.
"""

from __future__ import annotations

import ast
import unicodedata
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import ValidationError

from holocron_core.domain import documents, limits, vault
from holocron_core.domain.documents import ExtractedLine, LineEdit, PageExtraction
from holocron_core.domain.vault import ConfirmedPage
from holocron_core.infrastructure.document_readers.config import PdfReaderConfig
from holocron_core.infrastructure.document_readers.protocol import (
    MAX_RAW_LINE_CHARS,
    RawOcrLine,
    RawOcrPage,
    RawPdfPage,
    RawTextLine,
)
from holocron_core.infrastructure.document_readers.tiers import assemble_text_layer_page
from holocron_core.services.document_import import _confirmed_lines, _fits_vault

PACKAGE_ROOT = Path(__file__).resolve().parent.parent / "holocron_core"
MAX = limits.MAX_LINE_CHARS
DOC_ID = "doc_00000000000000aa"
BBOX = (0.1, 0.1, 0.9, 0.12)
# Ký tự nằm trong danh sách loại trừ hợp thành: NFC tách thành 2 ký tự, nên làm chuỗi DÀI RA.
DEVANAGARI_QA = chr(0x0958)
GREEK_DIALYTIKA_TONOS = chr(0x0344)
VIETNAMESE_SENTENCE = "Hệ điều hành quản lý tiến trình và bộ nhớ. "


# --------------------------------------------------------------------------- #
# (a) Một hằng, giới hạn bộ đọc ≤ giới hạn Vault
# --------------------------------------------------------------------------- #


def _assigned_names(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, ast.Tuple | ast.List):
        return [name for element in node.elts for name in _assigned_names(element)]
    return []


def _max_line_chars_assignments() -> list[str]:
    found: list[str] = []
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            targets: list[ast.expr] = []
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, ast.AnnAssign | ast.AugAssign):
                targets = [node.target]
            for target in targets:
                if "MAX_LINE_CHARS" in _assigned_names(target):
                    found.append(f"{path.relative_to(PACKAGE_ROOT).as_posix()}:{node.lineno}")
    return found


def test_single_max_line_chars_assignment() -> None:
    assignments = _max_line_chars_assignments()
    assert len(assignments) == 1, assignments
    assert assignments[0].startswith("domain/limits.py:")


def test_reader_limit_not_above_vault_limit() -> None:
    assert documents.MAX_LINE_CHARS <= vault.MAX_LINE_CHARS
    assert documents.MAX_LINE_CHARS == vault.MAX_LINE_CHARS == limits.MAX_LINE_CHARS == 2000


def test_raw_line_limit_is_four_times_line_limit() -> None:
    """Theo chú thích ở `document_readers/protocol.py`: dòng thô dài tối đa 4 lần `ExtractedLine.text`."""
    assert MAX_RAW_LINE_CHARS == 4 * MAX


# --------------------------------------------------------------------------- #
# (b) Bộ đọc luôn tách dòng dài (qua đường public)
# --------------------------------------------------------------------------- #


def _text_layer_page(text: str) -> PageExtraction:
    raw = RawPdfPage(
        page_index=0,
        width_pt=612.0,
        height_pt=792.0,
        lines=[RawTextLine(text=text, bbox=BBOX, words=[])],
        image_area_ratio=0.0,
        path_area_ratio=0.0,
    )
    page = assemble_text_layer_page(raw, DOC_ID, PdfReaderConfig(text_layer_min=0.0))
    assert page.tier_used == "text_layer"
    return page


def _ocr_page(text: str) -> PageExtraction:
    ocr = RawOcrPage(
        dpi=300.0,
        lines=[RawOcrLine(text=text, bbox=BBOX, words=[], conf=0.95)],
        ink_outside_text_ratio=0.0,
        secondary=None,
    )
    raw = RawPdfPage(
        page_index=0,
        width_pt=612.0,
        height_pt=792.0,
        lines=[],
        image_area_ratio=0.0,
        path_area_ratio=0.0,
        ocr=ocr,
    )
    page = assemble_text_layer_page(raw, DOC_ID, PdfReaderConfig())
    assert page.tier_used == "ocr"
    return page


ASSEMBLERS: dict[str, Callable[[str], PageExtraction]] = {"text_layer": _text_layer_page, "ocr": _ocr_page}

# (tên ca, dòng thô, độ dài các đoạn mong đợi)
LONG_LINE_CASES: list[tuple[str, str, list[int]]] = [
    ("exactly_max", "a" * MAX, [MAX]),
    ("max_plus_one", "a" * (MAX + 1), [MAX, 1]),
    ("two_max_plus_one", "a" * (2 * MAX + 1), [MAX, MAX, 1]),
    ("raw_limit", "a" * MAX_RAW_LINE_CHARS, [MAX] * 4),
    # Dòng thô ≤ MAX nhưng NFC làm dài ra quá MAX: cắt trước NFC sẽ tạo đoạn dài quá giới hạn.
    ("nfc_expands_past_max", DEVANAGARI_QA * (MAX // 2 + 1), [MAX, 2]),
    ("nfc_expands_one_past_max", "a" * (MAX - 1) + DEVANAGARI_QA, [MAX, 1]),
    ("nfc_expands_to_exactly_max", GREEK_DIALYTIKA_TONOS * (MAX // 2), [MAX]),
]


@pytest.mark.parametrize("assembler", ASSEMBLERS)
@pytest.mark.parametrize(
    ("raw_text", "expected"), [(c[1], c[2]) for c in LONG_LINE_CASES], ids=[c[0] for c in LONG_LINE_CASES]
)
def test_reader_always_splits_long_lines(assembler: str, raw_text: str, expected: list[int]) -> None:
    page = ASSEMBLERS[assembler](raw_text)
    lengths = [len(line.text) for line in page.lines]
    assert lengths == expected
    assert all(length <= MAX for length in lengths)
    assert "".join(line.text for line in page.lines) == unicodedata.normalize("NFC", raw_text)


@pytest.mark.parametrize("assembler", ASSEMBLERS)
def test_reader_split_of_vietnamese_nfd_rejoins_to_nfc(assembler: str) -> None:
    """NFD → NFC làm chuỗi NGẮN đi; ca này chỉ kiểm nối các đoạn lại đúng nguyên văn sau NFC."""
    nfc_text = (VIETNAMESE_SENTENCE * 100).strip()
    raw_text = unicodedata.normalize("NFD", nfc_text)
    assert len(nfc_text) > MAX
    assert len(nfc_text) < len(raw_text) <= MAX_RAW_LINE_CHARS
    page = ASSEMBLERS[assembler](raw_text)
    assert len(page.lines) > 1
    assert all(len(line.text) <= MAX for line in page.lines)
    assert "".join(line.text for line in page.lines) == nfc_text


# --------------------------------------------------------------------------- #
# (c) Dòng bộ đọc tạo ra luôn vào được Vault
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("assembler", ASSEMBLERS)
@pytest.mark.parametrize("raw_text", [c[1] for c in LONG_LINE_CASES], ids=[c[0] for c in LONG_LINE_CASES])
def test_reader_lines_fit_vault(assembler: str, raw_text: str) -> None:
    page = ASSEMBLERS[assembler](raw_text)
    lines = _confirmed_lines(page)
    assert lines
    confirmed = ConfirmedPage(page_number=page.page_number, lines=lines)
    assert confirmed.lines == lines
    assert _fits_vault(page)


# --------------------------------------------------------------------------- #
# Ghim hành vi hiện tại của các cổng độ dài (không đổi code)
# --------------------------------------------------------------------------- #

NFD_OVER_RAW = unicodedata.normalize("NFD", "ế") * 667  # thô 2001 code point, sau NFC chỉ 667


def test_confirmed_page_over_limit_line_rejected_but_whitespace_is_stripped_first() -> None:
    """`StrictModel` có `str_strip_whitespace=True` nên khoảng trắng thừa bị cắt TRƯỚC validator độ dài.

    Dòng chỉ quá dài nhờ khoảng trắng vì vậy được nhận (đã cắt); dòng quá dài thật vẫn bị từ chối.
    """
    assert ConfirmedPage(page_number=1, lines=["a" * MAX + " "]).lines == ["a" * MAX]
    assert ConfirmedPage(page_number=1, lines=[" " * (MAX + 1)]).lines == [""]
    with pytest.raises(ValidationError, match="mỗi dòng tối đa"):
        ConfirmedPage(page_number=1, lines=["a" * (MAX + 1)])


def test_extracted_line_rejects_raw_over_limit_even_if_nfc_fits() -> None:
    # Ghim hành vi hiện tại: giới hạn thô trước NFC; dòng thêm cuối trang (appended_lines) chỉ chặn sau NFC —
    # chờ người dùng chốt có thống nhất không (2026-10-29).
    assert len(NFD_OVER_RAW) == MAX + 1
    assert len(unicodedata.normalize("NFC", NFD_OVER_RAW)) <= MAX
    with pytest.raises(ValidationError, match="string_too_long"):
        ExtractedLine(line_index=0, text=NFD_OVER_RAW, bbox=None, conf=0.9, engine="text_layer")


def test_line_edit_rejects_raw_over_limit_even_if_nfc_fits() -> None:
    # Ghim hành vi hiện tại: giới hạn thô trước NFC; dòng thêm cuối trang (appended_lines) chỉ chặn sau NFC —
    # chờ người dùng chốt có thống nhất không (2026-10-29).
    with pytest.raises(ValidationError, match="string_too_long"):
        LineEdit(line_index=0, text=NFD_OVER_RAW)
