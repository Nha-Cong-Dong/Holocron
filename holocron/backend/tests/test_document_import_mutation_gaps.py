"""Bổ sung test cho các đột biến còn sống của bộ nhập tài liệu (SPECIFICATION §6.5): giới hạn của body sửa dòng và
xác nhận (`domain/documents.py`) và hàm thuần `apply_line_edits` (`services/document_import.py`).

Phần chạy qua API và kho (ba loại kho) ở `test_document_import_mutation_gaps_api.py` và
`test_document_import_mutation_gaps_store.py`. Văn bản là mẫu tự viết.
"""

import unicodedata
from typing import Any

import pytest
from pydantic import ValidationError

from holocron_core.domain.documents import (
    MAX_EDITOR_ID_CHARS,
    MAX_LINE_CHARS,
    CrossCheck,
    DocumentConfirmRequest,
    ExtractedLine,
    LineEdit,
    PageConfirmRequest,
    PageExtraction,
    PageLinesPatch,
)
from holocron_core.domain.vault import MAX_PAGE_LINES
from holocron_core.services.document_import import InvalidPageEditError, apply_line_edits

DOC = "doc_dddddddddddddddd"
EDITOR = "local_user"
CONF_MIN = 0.7

# "ệ" ở dạng NFD là 3 code point ("e" + dấu mũ + dấu nặng), ở dạng NFC là 1.
NFD_E = unicodedata.normalize("NFD", "ệ")


def _patch(**body: Any) -> PageLinesPatch:
    return PageLinesPatch.model_validate({"editor_id": EDITOR, "expected_version": 1, **body})


# --------------------------------------------------------------------------- #
# Body của PATCH …/lines, POST …/pages/{n}/confirm, POST …/confirm (domain/documents.py)
# --------------------------------------------------------------------------- #


def test_editor_id_limit_is_128_characters_for_every_body() -> None:
    assert MAX_EDITOR_ID_CHARS == 128
    at_limit, over = "a" * 128, "a" * 129
    assert _patch(editor_id=at_limit, appended_lines=["x"]).editor_id == at_limit
    assert PageConfirmRequest(editor_id=at_limit, expected_version=1).editor_id == at_limit
    assert DocumentConfirmRequest(editor_id=at_limit).editor_id == at_limit
    with pytest.raises(ValidationError):
        _patch(editor_id=over, appended_lines=["x"])
    with pytest.raises(ValidationError):
        PageConfirmRequest(editor_id=over, expected_version=1)
    with pytest.raises(ValidationError):
        DocumentConfirmRequest(editor_id=over)


def test_empty_editor_id_is_rejected_for_every_body() -> None:
    with pytest.raises(ValidationError):
        _patch(editor_id="", appended_lines=["x"])
    with pytest.raises(ValidationError):
        PageConfirmRequest(editor_id="", expected_version=1)
    with pytest.raises(ValidationError):
        DocumentConfirmRequest(editor_id="")


@pytest.mark.parametrize("version", [0, -1])
def test_expected_version_below_one_is_rejected(version: int) -> None:
    with pytest.raises(ValidationError):
        _patch(expected_version=version, appended_lines=["x"])
    with pytest.raises(ValidationError):
        PageConfirmRequest(editor_id=EDITOR, expected_version=version)
    assert _patch(expected_version=1, appended_lines=["x"]).expected_version == 1


def test_line_of_exactly_max_chars_is_accepted_and_one_more_is_rejected() -> None:
    assert MAX_LINE_CHARS == 2000
    at_limit = "a" * MAX_LINE_CHARS
    assert LineEdit(line_index=0, text=at_limit).text == at_limit
    assert _patch(appended_lines=[at_limit]).appended_lines == [at_limit]
    with pytest.raises(ValidationError):
        LineEdit(line_index=0, text=at_limit + "a")
    with pytest.raises(ValidationError):
        _patch(appended_lines=[at_limit + "a"])


def test_edits_and_appended_lines_are_normalized_to_nfc() -> None:
    nfd_text = f"Thanh ghi tr{NFD_E}n"
    nfc_text = unicodedata.normalize("NFC", nfd_text)
    assert nfd_text != nfc_text
    patch = _patch(edits=[{"line_index": 0, "text": nfd_text}], appended_lines=[nfd_text])
    assert patch.edits[0].text == nfc_text
    assert patch.appended_lines == [nfc_text]


def test_appended_line_is_measured_after_nfc() -> None:
    """Dòng gõ thêm dài hơn giới hạn ở dạng NFD nhưng vừa đúng giới hạn sau NFC ⇒ nhận, lưu ở dạng NFC."""
    nfd_line = NFD_E * MAX_LINE_CHARS
    assert len(nfd_line) > MAX_LINE_CHARS
    patch = _patch(appended_lines=[nfd_line])
    assert patch.appended_lines == ["ệ" * MAX_LINE_CHARS]
    with pytest.raises(ValidationError):
        _patch(appended_lines=[NFD_E * (MAX_LINE_CHARS + 1)])


# --------------------------------------------------------------------------- #
# apply_line_edits (hàm thuần)
# --------------------------------------------------------------------------- #


def _line(index: int, text: str, conf: float, flags: list[str] | None = None) -> dict[str, Any]:
    return {"line_index": index, "text": text, "bbox": None, "conf": conf, "engine": "tesseract", "flags": flags or []}


def _page(lines: list[dict[str, Any]], cross_check: dict[str, Any], reasons: list[str]) -> PageExtraction:
    return PageExtraction.model_validate(
        {
            "document_id": DOC,
            "page_number": 1,
            "status": "needs_review" if reasons else "ok",
            "tier_used": "ocr",
            "text_score": 0.1,
            "ocr_score": 0.9,
            "lines": lines,
            "cross_check": cross_check,
            "review_reasons": reasons,
        }
    )


@pytest.mark.parametrize("status", ["unavailable", "not_applicable"])
def test_edit_keeps_cross_check_status_that_is_not_mismatch(status: str) -> None:
    """I5: sửa dòng không biến kiểm chéo `unavailable`/`not_applicable` thành `ok`; lý do xem lại giữ nguyên."""
    reasons = ["CROSS_CHECK_UNAVAILABLE"] if status == "unavailable" else ["LOW_OCR_SCORE"]
    page = _page(
        [_line(0, "MOV R0, #0x1E", 0.95), _line(1, "ADDS R0, R1, R2", 0.95)],
        {"status": status, "secondary_engine": None},
        reasons,
    )
    edited, _ = apply_line_edits(
        page, _patch(edits=[{"line_index": 0, "text": "MOV R0, #0x1F"}]), line_conf_min=CONF_MIN
    )
    assert edited.cross_check.status == status
    assert edited.review_reasons == reasons
    assert edited.status == "needs_review"


def test_mismatch_cleared_by_edit_becomes_ok() -> None:
    """Đối chứng của test trên: `mismatch` hết token lệch ⇒ `ok`."""
    page = _page(
        [_line(0, "MOV R0, #0x1E", 0.95, ["TOKEN_MISMATCH"])],
        {
            "status": "mismatch",
            "secondary_engine": "rapidocr",
            "mismatches": [{"line_index": 0, "primary_value": "#0x1E", "secondary_value": "#0x1F"}],
        },
        ["TOKEN_MISMATCH"],
    )
    edited, _ = apply_line_edits(
        page, _patch(edits=[{"line_index": 0, "text": "MOV R0, #0x1F"}]), line_conf_min=CONF_MIN
    )
    assert edited.cross_check.status == "ok" and edited.status == "ok"


def test_line_with_conf_equal_to_the_minimum_is_not_low_conf() -> None:
    """`LOW_CONF_LINES` chỉ tính dòng có `conf < line_conf_min` (so sánh nghiêm ngặt)."""
    page = _page(
        [_line(0, "Thanh ghi CPSR", 0.42, ["LOW_LINE_CONF"]), _line(1, "Lệnh MOV", CONF_MIN)],
        {"status": "not_applicable", "secondary_engine": None},
        ["LOW_CONF_LINES"],
    )
    edited, _ = apply_line_edits(
        page, _patch(edits=[{"line_index": 0, "text": "Thanh ghi CPSR chứa cờ"}]), line_conf_min=CONF_MIN
    )
    assert edited.review_reasons == [] and edited.status == "ok"


def test_line_with_conf_just_below_the_minimum_keeps_low_conf() -> None:
    page = _page(
        [_line(0, "Thanh ghi CPSR", 0.42, ["LOW_LINE_CONF"]), _line(1, "Lệnh MOV", CONF_MIN - 0.01)],
        {"status": "not_applicable", "secondary_engine": None},
        ["LOW_CONF_LINES"],
    )
    edited, _ = apply_line_edits(
        page, _patch(edits=[{"line_index": 0, "text": "Thanh ghi CPSR chứa cờ"}]), line_conf_min=CONF_MIN
    )
    assert edited.review_reasons == ["LOW_CONF_LINES"]


def test_line_index_equal_to_the_line_count_does_not_exist() -> None:
    page = _page([_line(0, "a", 0.95), _line(1, "b", 0.95)], {"status": "not_applicable", "secondary_engine": None}, [])
    with pytest.raises(InvalidPageEditError, match=r"\[2\]"):
        apply_line_edits(page, _patch(edits=[{"line_index": 2, "text": "c"}]), line_conf_min=CONF_MIN)
    edited, _ = apply_line_edits(page, _patch(edits=[{"line_index": 1, "text": "c"}]), line_conf_min=CONF_MIN)
    assert [line.text for line in edited.lines] == ["a", "c"]


def _page_with(count: int) -> PageExtraction:
    return PageExtraction(
        document_id=DOC,
        page_number=1,
        status="ok",
        tier_used="text_layer",
        text_score=0.95,
        lines=[
            ExtractedLine(line_index=index, text=f"Dòng {index}", bbox=None, conf=0.95, engine="text_layer")
            for index in range(count)
        ],
        cross_check=CrossCheck(status="not_applicable", secondary_engine=None),
    )


def test_page_may_reach_exactly_max_page_lines_with_appended_lines() -> None:
    page = _page_with(MAX_PAGE_LINES - 2)
    edited, changes = apply_line_edits(page, _patch(appended_lines=["x", "y"]), line_conf_min=CONF_MIN)
    assert len(edited.lines) == MAX_PAGE_LINES
    assert [(c.line_index, c.old, c.new) for c in changes] == [
        (MAX_PAGE_LINES - 2, None, "x"),
        (MAX_PAGE_LINES - 1, None, "y"),
    ]


def test_one_line_over_max_page_lines_is_rejected() -> None:
    page = _page_with(MAX_PAGE_LINES - 2)
    with pytest.raises(InvalidPageEditError, match=str(MAX_PAGE_LINES)):
        apply_line_edits(page, _patch(appended_lines=["x", "y", "z"]), line_conf_min=CONF_MIN)
    with pytest.raises(InvalidPageEditError, match=str(MAX_PAGE_LINES)):
        apply_line_edits(_page_with(MAX_PAGE_LINES), _patch(appended_lines=["x"]), line_conf_min=CONF_MIN)
