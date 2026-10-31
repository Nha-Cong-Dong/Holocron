"""Test giết đột biến sống GĐ0 ở các điểm I3/I5 (nhánh feat/core-pdf-page-denoise, báo cáo đột biến 857e035).

Mỗi test nêu hành vi mà một đột biến phá: ký tự trắng THẬT không bị coi là ký tự tự sinh (I3), dữ liệu GĐ0 của
worker đi hết vào `PageExtraction`, dòng header/footer không vào văn bản xác nhận hay quyết định "có chữ" (I5),
trang bỏ mềm không vào lô xác nhận. Không PDF thật, không mạng, không eval_data.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from holocron_core.domain.documents import (
    MAX_RELATED_PAGES,
    PageExtraction,
    PageImage,
    PageRoleEvidence,
    PageSummary,
    count_dropped,
    count_kept,
    count_needs_review,
)
from holocron_core.infrastructure.document_readers.config import PdfReaderConfig
from holocron_core.infrastructure.document_readers.protocol import (
    RawOcrLine,
    RawOcrPage,
    RawPageImage,
    RawPdfPage,
    RawTextLine,
)
from holocron_core.infrastructure.document_readers.text_layer import PdfChar, group_lines, merge_spaced_chars
from holocron_core.infrastructure.document_readers.tiers import assemble_text_layer_page, failed_page
from holocron_core.infrastructure.storage.original_files import OriginalFileStore
from holocron_core.infrastructure.storage.sqlite_store import SQLiteStore
from holocron_core.routers import documents
from holocron_core.routers.api import app
from holocron_core.services.document_import import (
    ImportRuntime,
    SynchronousImportQueue,
    _confirmed_lines,
    _has_text,
    _ready_to_confirm,
)
from holocron_core.services.page_roles import image_appears
from tests.document_import_support import FakePdfReader, ImportEnv, install, pdf_bytes
from tests.test_document_page_roles import _doc_id, _page_json, _reader
from tests.vault_support import STORE_KINDS, open_store

CONFIG = PdfReaderConfig()
DOC_ID = "doc_00000000000000f1"
SHA_A = "a" * 64
SHA_B = "b" * 64
HASH_0 = "0000000000000000"
HASH_FAR = "ffffffffffffffff"
EDITOR = "local_user"
PAGE_BOX = (0.0, 0.0, 612.0, 792.0)


# --------------------------------------------------------------------------- #
# text_layer._is_separator: chỉ ký tự trắng/ngắt dòng DO PDFium TỰ SINH mới bị bỏ khi ghép (I3)
# --------------------------------------------------------------------------- #


def _letters_with(separator: list[PdfChar]) -> list[PdfChar]:
    """Ba chữ đứng riêng, cùng baseline, khoảng cách đều, nối nhau bằng `separator`."""
    out: list[PdfChar] = []
    x = 50.0
    for index, char in enumerate("ABC"):
        if index:
            out.extend(separator)
        out.append(PdfChar(char, (x, 100.0, x + 6.0, 110.0)))
        x += 8.0
    return out


@pytest.mark.parametrize(
    "separator",
    [
        [PdfChar(" ", None)],
        [PdfChar("\n", None)],
        [PdfChar("\r", None), PdfChar("\n", None)],
    ],
    ids=["real_space", "real_lf", "real_crlf"],
)
def test_real_whitespace_between_isolated_letters_is_never_dropped(separator: list[PdfChar]) -> None:
    chars = _letters_with(separator)
    assert merge_spaced_chars(chars, CONFIG) == chars


@pytest.mark.parametrize(
    "text",
    [" ", "\n", "\r", "\t"],
    ids=["space", "lf", "cr", "tab"],
)
def test_generated_whitespace_between_isolated_letters_is_dropped(text: str) -> None:
    chars = _letters_with([PdfChar(text, None, generated=True)])
    merged = merge_spaced_chars(chars, CONFIG)
    assert "".join(char.text for char in merged) == "ABC"
    assert [line.text for line in group_lines(merged, PAGE_BOX, 0)] == ["ABC"]


def test_generated_non_whitespace_char_is_not_a_separator() -> None:
    chars = _letters_with([PdfChar("x", None, generated=True)])
    assert merge_spaced_chars(chars, CONFIG) == chars


# --------------------------------------------------------------------------- #
# tiers: dữ liệu GĐ0 của worker đi hết vào PageExtraction; cờ chất lượng không đặt lên header/footer
# --------------------------------------------------------------------------- #

BODY = "Mot dong noi dung co du so tu de giu lop chu tren trang"


def _raw(lines: list[RawTextLine], **extra: Any) -> RawPdfPage:
    data: dict[str, Any] = {
        "page_index": 2,
        "width_pt": 612.0,
        "height_pt": 792.0,
        "lines": lines,
        "image_area_ratio": 0.0,
        "path_area_ratio": 0.0,
        **extra,
    }
    return RawPdfPage(**data)


def _body_line(text: str = BODY, **extra: Any) -> RawTextLine:
    return RawTextLine(text=text, bbox=(0.1, 0.3, 0.9, 0.34), words=[], **extra)


IMAGES = [
    RawPageImage(bbox=(0.1, 0.2, 0.4, 0.5), sha256=SHA_A, dhash="00000000000000ff"),
    RawPageImage(bbox=(0.5, 0.6, 0.9, 0.95), sha256=None, dhash=None),
]
EXPECTED_IMAGES = [
    PageImage(bbox=(0.1, 0.2, 0.4, 0.5), sha256=SHA_A, dhash="00000000000000ff"),
    PageImage(bbox=(0.5, 0.6, 0.9, 0.95), sha256=None, dhash=None),
]


def _assert_gd0_fields(page: PageExtraction) -> None:
    assert page.own_images == EXPECTED_IMAGES
    assert page.hidden_chars_dropped == 7
    assert page.scan_text_layer is True


def test_text_layer_page_carries_worker_gd0_fields() -> None:
    raw = _raw([_body_line()], own_images=IMAGES, hidden_chars_dropped=7, scan_text_layer=True)
    built = assemble_text_layer_page(raw, DOC_ID, CONFIG)
    assert built.tier_used == "text_layer"
    _assert_gd0_fields(built)
    plain = assemble_text_layer_page(_raw([_body_line()]), DOC_ID, CONFIG)
    assert (plain.own_images, plain.hidden_chars_dropped, plain.scan_text_layer) == ([], 0, False)


def test_ocr_page_carries_worker_gd0_fields() -> None:
    ocr = RawOcrPage(
        dpi=300.0,
        lines=[RawOcrLine(text="Bieu do", bbox=(0.1, 0.05, 0.5, 0.08), words=[], conf=0.95)],
        ink_outside_text_ratio=0.0,
        secondary=None,
    )
    raw = _raw([], own_images=IMAGES, hidden_chars_dropped=7, scan_text_layer=True, ocr=ocr)
    built = assemble_text_layer_page(raw, DOC_ID, CONFIG)
    assert built.tier_used == "ocr"
    _assert_gd0_fields(built)


def test_failed_page_with_raw_keeps_gd0_fields_and_without_raw_has_defaults() -> None:
    raw = _raw([], own_images=IMAGES, hidden_chars_dropped=7, scan_text_layer=True)
    failed = failed_page(DOC_ID, 3, "OCR_ENGINE_MISSING", raw=raw)
    assert failed.status == "failed"
    _assert_gd0_fields(failed)
    bare = failed_page(DOC_ID, 3, "OCR_ENGINE_MISSING")
    assert (bare.own_images, bare.hidden_chars_dropped, bare.scan_text_layer) == ([], 0, False)


def test_page_without_ocr_engine_fails_with_gd0_fields() -> None:
    """Trang cần OCR mà worker không có engine (`raw.ocr` None): `failed` vẫn mang field GĐ0 của lớp chữ."""
    raw = _raw([], own_images=IMAGES, hidden_chars_dropped=7, scan_text_layer=True)
    built = assemble_text_layer_page(raw, DOC_ID, CONFIG)
    assert built.status == "failed" and built.failure_reason == "OCR_ENGINE_MISSING"
    _assert_gd0_fields(built)


def test_header_footer_lines_carry_no_quality_flags_but_body_lines_do() -> None:
    suspect = "Tieu de � lap lai"
    header = RawTextLine(
        text=suspect, bbox=(0.1, 0.01, 0.9, 0.04), words=[], flags=["LOW_TEXT_CONTRAST"], header_footer=True
    )
    body = _body_line(suspect, flags=["TEXT_OCCLUDED"])
    built = assemble_text_layer_page(_raw([header, body]), DOC_ID, CONFIG)
    by_header = {line.is_header_footer: line for line in built.lines}
    assert by_header[True].flags == []
    assert sorted(by_header[False].flags) == ["FONT_ENCODING_SUSPECT", "TEXT_OCCLUDED"]
    assert built.review_reasons == ["TEXT_OCCLUDED", "FONT_ENCODING_SUSPECT"]
    only_header = assemble_text_layer_page(_raw([header, _body_line()]), DOC_ID, CONFIG)
    assert only_header.lines[0].flags == [] and "LOW_TEXT_CONTRAST" not in only_header.review_reasons
    assert only_header.review_reasons == [] and only_header.status == "ok"


def _content_image_page(lines: list[RawTextLine], area: float) -> RawPdfPage:
    return _raw(lines, image_area_ratio=area, content_image_area_ratio=area)


def _ocr_stub(text: str = "Bieu do") -> RawOcrPage:
    return RawOcrPage(
        dpi=300.0,
        lines=[RawOcrLine(text=text, bbox=(0.1, 0.05, 0.5, 0.08), words=[], conf=0.95)],
        ink_outside_text_ratio=0.0,
        secondary=None,
    )


@pytest.mark.parametrize(
    "text",
    ["", "�" * 40],
    ids=["empty_text_layer", "garbled_text_layer"],
)
def test_image_needs_reading_is_added_whatever_rule_sent_the_page_to_ocr(text: str) -> None:
    """Spec §5.7 0C: trang thoả (e) "dù được đưa đi OCR bởi luật nào" mang `IMAGE_NEEDS_READING`, kể cả khi lớp chữ
    rỗng hay rác (luật (a), (b) xét trước (e))."""
    lines = [_body_line(text)] if text else []
    raw = _content_image_page(lines, CONFIG.image_read_area_min).model_copy(update={"ocr": _ocr_stub()})
    built = assemble_text_layer_page(raw, DOC_ID, CONFIG)
    assert built.tier_used == "ocr"
    assert "IMAGE_NEEDS_READING" in built.review_reasons
    small = _content_image_page(lines, CONFIG.image_read_area_min - 0.01).model_copy(update={"ocr": _ocr_stub()})
    assert "IMAGE_NEEDS_READING" not in assemble_text_layer_page(small, DOC_ID, CONFIG).review_reasons


def test_image_needs_reading_needs_the_condition_even_when_the_rule_is_image_based() -> None:
    """Trang OCR vì `sparse_text_on_image` nhưng ảnh nội dung dưới `image_read_area_min`: không có cờ (không chỉ vì
    luật OCR là luật dựa trên ảnh)."""
    config = PdfReaderConfig(text_sparse_image_min=0.2, image_read_area_min=0.8)
    raw = _content_image_page([_body_line("ngan")], 0.3).model_copy(update={"ocr": _ocr_stub()})
    built = assemble_text_layer_page(raw, DOC_ID, config)
    assert built.tier_used == "ocr"
    assert "IMAGE_NEEDS_READING" not in built.review_reasons


# --------------------------------------------------------------------------- #
# page_roles.image_appears
# --------------------------------------------------------------------------- #


def _img(sha: str | None, dhash: str | None) -> PageImage:
    return PageImage(bbox=(0.5, 0.5, 0.9, 0.9), sha256=sha, dhash=dhash)


SAME_MAX = CONFIG.build_step_dhash_same_max


def test_image_appears_by_equal_sha256_even_when_hashes_differ_a_lot() -> None:
    assert image_appears(_img(SHA_A, HASH_0), [_img(SHA_A, HASH_FAR)], SAME_MAX) is True


def test_image_does_not_appear_with_a_different_sha256_and_no_dhash() -> None:
    assert image_appears(_img(SHA_A, None), [_img(SHA_B, None)], SAME_MAX) is False


def test_missing_sha256_on_both_sides_is_not_a_match() -> None:
    assert image_appears(_img(None, None), [_img(None, None)], SAME_MAX) is False
    assert image_appears(_img(None, HASH_0), [_img(None, HASH_FAR)], SAME_MAX) is False


def test_sha256_present_on_one_side_only_is_not_a_match() -> None:
    assert image_appears(_img(SHA_A, None), [_img(None, None)], SAME_MAX) is False
    assert image_appears(_img(None, None), [_img(SHA_A, None)], SAME_MAX) is False


def test_image_appears_by_dhash_up_to_the_configured_distance() -> None:
    near = f"{(1 << SAME_MAX) - 1:016x}"
    over = f"{(1 << (SAME_MAX + 1)) - 1:016x}"
    assert image_appears(_img(SHA_A, HASH_0), [_img(SHA_B, near)], SAME_MAX) is True
    assert image_appears(_img(SHA_A, HASH_0), [_img(SHA_B, over)], SAME_MAX) is False
    assert image_appears(_img(SHA_A, HASH_0), [_img(SHA_B, None)], SAME_MAX) is False
    assert image_appears(_img(SHA_A, None), [_img(SHA_B, HASH_0)], SAME_MAX) is False


def test_image_appears_checks_every_other_image_and_none_means_false() -> None:
    others = [_img(SHA_B, HASH_FAR), _img(SHA_A, HASH_FAR)]
    assert image_appears(_img(SHA_A, HASH_0), others, SAME_MAX) is True
    assert image_appears(_img(SHA_A, HASH_0), [], SAME_MAX) is False


# --------------------------------------------------------------------------- #
# domain.documents: count_* và PageRoleEvidence.related_pages
# --------------------------------------------------------------------------- #

STATUSES = ("pending", "ok", "needs_review", "failed")


def _summary(status: str, *, confirmed: bool = False, dropped: bool = False) -> PageSummary:
    return PageSummary.model_construct(
        page_number=1,
        status=status,
        tier_used=None,
        review_reasons=[],
        confirmed=confirmed,
        page_role="content",
        dropped=dropped,
    )


@pytest.mark.parametrize("status", STATUSES)
@pytest.mark.parametrize("confirmed", [False, True])
@pytest.mark.parametrize("dropped", [False, True])
def test_count_needs_review_truth_table(status: str, confirmed: bool, dropped: bool) -> None:
    page = _summary(status, confirmed=confirmed, dropped=dropped)
    expected = int(status == "needs_review" and not dropped and not confirmed)
    assert count_needs_review([page]) == expected
    assert count_dropped([page]) == int(dropped)
    assert count_kept([page]) == int(status in ("ok", "needs_review") and not dropped)


def test_counts_add_up_over_many_pages() -> None:
    pages = [
        _summary("needs_review"),
        _summary("needs_review"),
        _summary("needs_review", confirmed=True),
        _summary("needs_review", dropped=True),
        _summary("ok"),
        _summary("failed"),
    ]
    assert count_needs_review(pages) == 2
    assert count_dropped(pages) == 1
    assert count_kept(pages) == 4


def _evidence(count: int) -> PageRoleEvidence:
    return PageRoleEvidence.model_validate(
        {
            "rule": "chapter_nav",
            "related_pages": [{"document_id": DOC_ID, "page_number": n} for n in range(1, count + 1)],
        }
    )


def test_related_pages_bounds() -> None:
    with pytest.raises(ValidationError):
        _evidence(0)
    assert len(_evidence(1).related_pages) == 1
    assert len(_evidence(2).related_pages) == 2
    assert len(_evidence(MAX_RELATED_PAGES).related_pages) == MAX_RELATED_PAGES
    with pytest.raises(ValidationError):
        _evidence(MAX_RELATED_PAGES + 1)


# --------------------------------------------------------------------------- #
# document_import: "có chữ" và văn bản xác nhận không tính header/footer; trang bỏ mềm không vào lô xác nhận
# --------------------------------------------------------------------------- #


def _extracted(document_id: str, number: int, lines: list[tuple[str, bool]], **extra: Any) -> PageExtraction:
    data: dict[str, Any] = {
        "document_id": document_id,
        "page_number": number,
        "status": "ok",
        "tier_used": "text_layer",
        "text_score": 1.0,
        "lines": [
            {
                "line_index": index,
                "text": text,
                "bbox": [0.1, 0.1 + 0.05 * index, 0.9, 0.14 + 0.05 * index],
                "conf": 1.0,
                "engine": "text_layer",
                "is_header_footer": header,
            }
            for index, (text, header) in enumerate(lines)
        ],
        "cross_check": {"status": "not_applicable", "secondary_engine": None},
        **extra,
    }
    return PageExtraction.model_validate(data)


def test_has_text_ignores_header_footer_lines() -> None:
    header_only = _extracted(DOC_ID, 1, [("Tieu de lap lai", True), ("3", True)])
    mixed = _extracted(DOC_ID, 1, [("Tieu de lap lai", True), ("Noi dung", False)])
    body_only = _extracted(DOC_ID, 1, [("Noi dung", False)])
    blank = PageExtraction.model_construct(
        **{**body_only.__dict__, "lines": [body_only.lines[0].model_copy(update={"text": "   "})]}
    )
    assert _has_text(header_only) is False
    assert _has_text(mixed) is True
    assert _has_text(body_only) is True
    assert _has_text(blank) is False
    assert _has_text(_extracted(DOC_ID, 1, [])) is False


def test_confirmed_lines_drop_header_footer_and_keep_body_order() -> None:
    page = _extracted(DOC_ID, 1, [("Tieu de", True), ("Dong mot", False), ("Dong hai", False), ("Chan trang", True)])
    assert _confirmed_lines(page) == ["Dong mot", "Dong hai"]
    blank = page.lines[1].model_copy(update={"text": "  "})
    assert _confirmed_lines(page.model_copy(update={"lines": [blank, *page.lines[2:]]})) == ["Dong hai"]
    assert _confirmed_lines(_extracted(DOC_ID, 1, [("Tieu de", True)])) == []


def test_ready_to_confirm_does_not_wait_for_header_only_or_dropped_pages() -> None:
    confirmed = _extracted(DOC_ID, 1, [("Noi dung", False)], confirmed=True)
    header_only = _extracted(DOC_ID, 2, [("Tieu de", True)])
    assert _ready_to_confirm([confirmed, header_only]) is True
    open_page = _extracted(DOC_ID, 3, [("Noi dung chua xac nhan", False)])
    assert _ready_to_confirm([confirmed, open_page]) is False
    dropped = _extracted(
        DOC_ID,
        4,
        [("Muc luc", False)],
        page_role="chapter_nav",
        dropped=True,
        role_evidence={"rule": "chapter_nav", "related_pages": [{"document_id": DOC_ID, "page_number": 1}]},
    )
    assert _ready_to_confirm([confirmed, dropped]) is True
    assert _ready_to_confirm([header_only, dropped]) is False, "chưa có trang nào xác nhận"
    assert (
        _ready_to_confirm([confirmed, _extracted(DOC_ID, 5, [], status="pending", tier_used=None, text_score=None)])
        is False
    )


def _header_page(document_id: str) -> PageExtraction:
    return _extracted(document_id, 5, [("Tieu de lap lai", True)])


@pytest.fixture(params=STORE_KINDS)
def env(request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ImportEnv]:
    reader = _reader(review_nav=False)
    reader.custom_pages[5] = _header_page
    for store in open_store(str(request.param), tmp_path, monkeypatch):
        yield install(store, tmp_path / "files", SynchronousImportQueue(), reader)
        app.dependency_overrides.clear()


def _import(env: ImportEnv) -> str:
    content = pdf_bytes("gd0-mutants")
    response = env.post(content)
    assert response.status_code == 202, response.text
    return _doc_id(content)


def test_batch_confirm_skips_soft_dropped_and_header_only_pages(env: ImportEnv) -> None:
    doc = _import(env)
    dropped = [number for number in range(1, 13) if _page_json(env, doc, number)["dropped"]]
    assert dropped == [2, 6, 11], "nền: các trang điều hướng (status ok) bị bỏ mềm khi đọc xong"
    response = env.client.post(f"/v1/documents/{doc}/confirm", json={"editor_id": EDITOR})
    assert response.status_code == 200, response.text
    for number in range(1, 13):
        page = _page_json(env, doc, number)
        should_confirm = number not in dropped and number != 5
        assert page["confirmed"] is should_confirm, number
    assert response.json()["status"] == "confirmed", "trang chỉ có header/footer và trang bỏ mềm không chặn chốt"
    single = env.client.post(f"/v1/documents/{doc}/pages/5/confirm", json={"editor_id": EDITOR, "expected_version": 1})
    assert single.status_code == 409


def test_batch_confirm_leaves_needs_review_pages_and_does_not_touch_confirmed_ones(env: ImportEnv) -> None:
    env.reader.custom_pages[8] = lambda document_id: _extracted(
        document_id, 8, [("Trang can xem lai", False)], status="needs_review", review_reasons=["LOW_TEXT_CONTRAST"]
    )
    doc = _import(env)
    version = _page_json(env, doc, 1)["version"]
    first = env.client.post(
        f"/v1/documents/{doc}/pages/1/confirm", json={"editor_id": EDITOR, "expected_version": version}
    )
    assert first.status_code == 200, first.text
    before = _page_json(env, doc, 1)
    assert before["confirmed"] is True
    response = env.client.post(f"/v1/documents/{doc}/confirm", json={"editor_id": EDITOR})
    assert response.status_code == 200, response.text
    assert _page_json(env, doc, 8)["confirmed"] is False, "trang needs_review phải xác nhận từng trang"
    assert response.json()["status"] == "review"
    assert _page_json(env, doc, 1) == before, "trang đã xác nhận không bị ghi lại (version, confirmed_at giữ nguyên)"
    assert _page_json(env, doc, 3)["confirmed"] is True


def test_single_confirm_of_header_only_page_is_rejected(env: ImportEnv) -> None:
    doc = _import(env)
    single = env.client.post(f"/v1/documents/{doc}/pages/5/confirm", json={"editor_id": EDITOR, "expected_version": 1})
    assert single.status_code == 409
    assert _page_json(env, doc, 5)["confirmed"] is False


# --------------------------------------------------------------------------- #
# routers.documents.requeue_on_startup chuyển cấu hình của runtime sang việc đọc xếp lại
# --------------------------------------------------------------------------- #


def test_requeue_on_startup_passes_the_runtime_reader_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = PdfReaderConfig(page_role_min_tokens=17)
    runtime = ImportRuntime(
        files=OriginalFileStore(tmp_path / "files"), queue=SynchronousImportQueue(), reader_config=config
    )
    store = SQLiteStore.open(tmp_path / "db.sqlite3")
    captured: dict[str, Any] = {}
    reader = FakePdfReader()

    def fake_requeue(*args: Any, **kwargs: Any) -> list[str]:
        captured["args"] = args
        captured.update(kwargs)
        return ["doc_00000000000000f1"]

    monkeypatch.setattr(documents, "load_pdf_reader", lambda cfg: reader)
    monkeypatch.setattr(documents, "requeue_pending", fake_requeue)
    try:
        assert documents.requeue_on_startup(store, runtime) == ["doc_00000000000000f1"]
    finally:
        store.close()
    assert captured["config"] is config
    assert captured["vault"] is store
    assert captured["locks"] is runtime.locks
    assert captured["args"][2] is reader
