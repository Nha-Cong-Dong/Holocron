"""Sổ bảo toàn bóc tách (ADR-010 mục 6; bảng §9 "Bảo toàn bóc tách (ADR-010)").

`test_ledger_conserves_every_char` đọc MỘT PDF tự tạo ≥ 100 trang (mỗi trang một seed: một/hai cột, bullet nhiều dòng,
tiêu đề, header lặp, chữ ẩn, gạch nối cuối dòng) qua `WorkerPdfReader` với `geometry_sink`.
"""

from __future__ import annotations

import hashlib
import inspect
import unicodedata
from collections import Counter

import pytest
from pydantic import ValidationError

from holocron_core.domain.blocks import (
    Block,
    ConfirmedLineRef,
    ExtractionLedger,
    LedgerBasisCount,
    LedgerDrop,
    LedgerTypeCount,
    OcrWordRef,
    SourceRef,
    WordRef,
)
from holocron_core.domain.documents import ExtractedLine, PageExtraction, PageGeometry
from holocron_core.infrastructure.document_readers.pdf_reader import WorkerPdfReader
from holocron_core.infrastructure.lang_patterns import load_lang_patterns
from holocron_core.services.block_pipeline import PageBlocks, build_page_blocks
from holocron_core.services.extraction_ledger import (
    LedgerError,
    SourceIndex,
    block_refs,
    compute_ledger,
    nonws_units,
    retype_by_type,
    with_page_drop_reason,
)
from holocron_core.services.page_numbers import PageNumberStage
from tests.block_samples import DOC_ID, extraction_for, geo_line, geometry, write_document

PAGES = 100
LANG = load_lang_patterns()


@pytest.fixture(scope="module")
def document(tmp_path_factory: pytest.TempPathFactory) -> list[tuple[PageExtraction, PageGeometry | None, PageBlocks]]:
    path = write_document(tmp_path_factory.mktemp("blocks") / "doc.pdf", PAGES)
    geometries: dict[int, PageGeometry | None] = {}

    def sink(page_number: int, geo: PageGeometry | None) -> None:
        geometries[page_number] = geo

    out: list[tuple[PageExtraction, PageGeometry | None, PageBlocks]] = []
    for page in WorkerPdfReader().read_pages(path, DOC_ID, geometry_sink=sink):
        geo = geometries[page.page_number]
        built = build_page_blocks(
            document_id=DOC_ID, page_number=page.page_number, geometry=geo, extraction=page, lang=LANG
        )
        out.append((page, geo, built))
    return out


def test_ledger_conserves_every_char(
    document: list[tuple[PageExtraction, PageGeometry | None, PageBlocks]],
) -> None:
    """Mỗi trang: mọi từ của geometry thuộc ĐÚNG một khối; Σ gán + chữ ẩn = số đếm độc lập của worker."""
    assert len(document) == PAGES
    hidden_pages = two_column_pages = 0
    for extraction, geo, built in document:
        assert geo is not None, extraction.page_number
        assert not built.layout_failed, extraction.page_number
        ledger = built.ledger
        assert ledger is not None and ledger.balanced
        (basis,) = ledger.bases
        assert basis.ledger_basis == "pdfium"
        assert basis.source_count == geo.pdfium_nonws_total
        assert basis.dropped == [LedgerDrop(reason="hidden", count=geo.hidden_nonws_dropped)]
        assert basis.assigned_count + geo.hidden_nonws_dropped == geo.pdfium_nonws_total
        refs = Counter(ref for block in built.blocks for ref in block_refs(block))
        expected = {WordRef(line_idx=i, word_idx=j) for i, line in enumerate(geo.lines) for j in range(len(line.words))}
        assert set(refs) == expected and set(refs.values()) == {1}
        assert sum(t.count for t in ledger.by_type) == basis.assigned_count
        assert sorted(b.reading_order for b in built.blocks) == list(range(len(built.blocks)))
        hidden_pages += geo.hidden_nonws_dropped > 0
        lefts = {round(b.bbox[0], 2) for b in built.blocks if b.bbox is not None and b.type == "paragraph"}
        two_column_pages += len(lefts) >= 2
    assert hidden_pages > 0 and two_column_pages > 0


def test_pdf_blocks_keep_i3_and_types(
    document: list[tuple[PageExtraction, PageGeometry | None, PageBlocks]],
) -> None:
    """I3: chữ khối = nối NFC các từ nguồn; mỗi trang có tiêu đề, header lặp và (sau số trang cấp tài liệu) số trang."""
    pages = PageNumberStage(LANG).run([built for _, _, built in document])
    for (extraction, geo, _), built in zip(document, pages, strict=True):
        assert geo is not None
        index = SourceIndex(geo, extraction)
        kinds = Counter(block.type for block in built.blocks)
        assert kinds["heading"] >= 1 and kinds["header_footer"] >= 1 and kinds["page_number"] == 1
        for block in built.blocks:
            assert block.text == " ".join(index.text(ref) for ref in block.line_refs)
            assert unicodedata.is_normalized("NFC", block.text)
        assert built.ledger is not None and built.ledger.balanced
        assert {t.type: t.count for t in built.ledger.by_type}["page_number"] == len(str(extraction.page_number))
    assert any(kinds["list_item"] for kinds in (Counter(b.type for b in p.blocks) for p in pages))


# --------------------------------------------------------------------------- #
# Ledger lệch, tham chiếu sai
# --------------------------------------------------------------------------- #


def _geo() -> PageGeometry:
    return geometry([geo_line("alpha beta", 0.1, 0.1), geo_line("gamma delta", 0.1, 0.118)], hidden=3)


def test_ledger_mismatch_marks_layout_failed() -> None:
    """Lệch dù 1 đơn vị ⇒ trang LAYOUT_FAILED, không có khối, ledger lưu `balanced = false` (cả hai basis)."""
    geo = _geo()
    ok = build_page_blocks(document_id=DOC_ID, page_number=1, geometry=geo, extraction=extraction_for(geo), lang=LANG)
    assert not ok.layout_failed and ok.ledger is not None and ok.ledger.balanced
    off = geo.model_copy(update={"pdfium_nonws_total": (geo.pdfium_nonws_total or 0) + 1})
    failed = build_page_blocks(
        document_id=DOC_ID, page_number=1, geometry=off, extraction=extraction_for(geo), lang=LANG
    )
    assert failed.layout_failed and failed.blocks == []
    assert failed.ledger is not None and not failed.ledger.balanced
    assert failed.ledger.bases[0].ledger_basis == "pdfium"
    # Basis confirmed_lines: dòng 1 sửa; dòng 0 chưa sửa nhưng chữ của nó (theo người dùng/bộ đọc) khác từ geometry.
    extraction = extraction_for(geo)
    lines = [
        extraction.lines[0].model_copy(update={"text": "alpha betaX"}),
        extraction.lines[1].model_copy(update={"text": "gamma fixed", "edited_by_human": True}),
    ]
    patched = extraction.model_copy(update={"lines": lines})
    result = build_page_blocks(document_id=DOC_ID, page_number=1, geometry=geo, extraction=patched, lang=LANG)
    assert result.layout_failed and result.blocks == []
    assert result.ledger is not None and not result.ledger.balanced
    assert [b.ledger_basis for b in result.ledger.bases] == ["confirmed_lines"]
    assert result.ledger.bases[0].dropped == []


def test_confirmed_lines_basis_counts_every_extracted_line() -> None:
    geo = _geo()
    extraction = extraction_for(geo)
    lines = [extraction.lines[0], extraction.lines[1].model_copy(update={"text": "gamma", "edited_by_human": True})]
    patched = extraction.model_copy(update={"lines": lines})
    result = build_page_blocks(document_id=DOC_ID, page_number=1, geometry=geo, extraction=patched, lang=LANG)
    assert result.ledger is not None and result.ledger.balanced
    (basis,) = result.ledger.bases
    assert basis.source_count == basis.assigned_count == nonws_units("alpha beta") + nonws_units("gamma")


def test_compute_ledger_rejects_bad_refs() -> None:
    geo = _geo()
    extraction = extraction_for(geo)
    index = SourceIndex(geo, extraction)
    built = build_page_blocks(document_id=DOC_ID, page_number=1, geometry=geo, extraction=extraction, lang=LANG)
    block = built.blocks[0]
    duplicated = [block, block.model_copy(update={"block_id": "blk_00000000000000ff"})]
    with pytest.raises(LedgerError):
        compute_ledger(DOC_ID, 1, duplicated, index)
    bad_refs: list[SourceRef] = [WordRef(line_idx=9, word_idx=0)]
    with pytest.raises(LedgerError):
        compute_ledger(DOC_ID, 1, [block.model_copy(update={"line_refs": bad_refs})], index)
    with pytest.raises(LedgerError):
        compute_ledger(DOC_ID, 1, [block.model_copy(update={"line_refs": [ConfirmedLineRef(line_index=7)]})], index)
    missing = geo.model_copy(update={"pdfium_nonws_total": None, "hidden_nonws_dropped": 0})
    with pytest.raises(LedgerError):
        compute_ledger(DOC_ID, 1, [block], SourceIndex(missing, extraction))
    bad_map = geo.model_copy(update={"lines": [geo.lines[0].model_copy(update={"extracted_line_indices": [5]})]})
    with pytest.raises(LedgerError):
        SourceIndex(bad_map, extraction)


def test_edited_line_must_use_confirmed_refs() -> None:
    geo = _geo()
    extraction = extraction_for(geo)
    lines = [extraction.lines[0].model_copy(update={"edited_by_human": True}), extraction.lines[1]]
    index = SourceIndex(geo, extraction.model_copy(update={"lines": lines}))
    with pytest.raises(LedgerError):
        index.validate(WordRef(line_idx=0, word_idx=0))


def test_geometry_missing_gives_layout_failed_without_ledger() -> None:
    geo = _geo()
    result = build_page_blocks(
        document_id=DOC_ID, page_number=1, geometry=None, extraction=extraction_for(geo), lang=LANG
    )
    assert result.layout_failed and result.blocks == [] and result.ledger is None


# --------------------------------------------------------------------------- #
# Model và hàm thuần
# --------------------------------------------------------------------------- #


def _ledger(**extra: object) -> ExtractionLedger:
    data: dict[str, object] = {
        "document_id": DOC_ID,
        "page_number": 1,
        "bases": [
            LedgerBasisCount(
                ledger_basis="pdfium", source_count=10, assigned_count=7, dropped=[LedgerDrop(reason="hidden", count=3)]
            )
        ],
        "by_type": [LedgerTypeCount(type="paragraph", count=7)],
        "balanced": True,
    }
    data.update(extra)
    return ExtractionLedger.model_validate(data)


def test_ledger_model_validators() -> None:
    assert _ledger().balanced
    with pytest.raises(ValidationError):
        _ledger(balanced=False)
    with pytest.raises(ValidationError):
        _ledger(by_type=[LedgerTypeCount(type="paragraph", count=6)])
    with pytest.raises(ValidationError):
        _ledger(by_type=[LedgerTypeCount(type="paragraph", count=3), LedgerTypeCount(type="heading", count=4)])
    two = [
        LedgerBasisCount(ledger_basis="ocr", source_count=0, assigned_count=0),
        LedgerBasisCount(ledger_basis="pdfium", source_count=7, assigned_count=7),
    ]
    with pytest.raises(ValidationError):
        _ledger(bases=two)
    with pytest.raises(ValidationError):
        _ledger(flagged=[{"reason": "occluded", "count": 1}, {"reason": "occluded", "count": 2}])


def test_with_page_drop_reason() -> None:
    ledger = _ledger()
    assert with_page_drop_reason(ledger, "build_step").page_drop_reason == "build_step"
    assert with_page_drop_reason(ledger, "chapter_nav").page_drop_reason == "chapter_nav"
    assert with_page_drop_reason(ledger, "content").page_drop_reason is None
    assert list(inspect.signature(with_page_drop_reason).parameters) == ["ledger", "page_role"]
    dropped = with_page_drop_reason(ledger, "cover_or_course_nav")
    assert with_page_drop_reason(dropped, "parallel_visual_series").page_drop_reason is None


def test_retype_by_type_moves_units_and_rejects_negative() -> None:
    ledger = _ledger()
    moved = retype_by_type(ledger, [("paragraph", "page_number", 2)])
    assert [(t.type, t.count) for t in moved.by_type] == [("paragraph", 5), ("page_number", 2)]
    with pytest.raises(LedgerError):
        retype_by_type(ledger, [("heading", "page_number", 1)])


def test_ocr_basis_requires_independent_count() -> None:
    geo = _geo()
    ocr_line = geo_line("scan words", 0.1, 0.5)
    with_ocr = geo.model_copy(update={"ocr_lines": [ocr_line], "ocr_nonws_total": nonws_units("scanwords")})
    extraction = extraction_for(geo)
    index = SourceIndex(with_ocr, extraction)
    built = build_page_blocks(document_id=DOC_ID, page_number=1, geometry=geo, extraction=extraction, lang=LANG)
    ocr_refs = [{"kind": "ocr_word", "line_idx": 0, "word_idx": i} for i in range(2)]
    ocr_block = Block.model_validate(
        {**built.blocks[0].model_dump(), "block_id": "blk_00000000000000aa", "source": "ocr", "line_refs": ocr_refs}
    )
    ledger = compute_ledger(DOC_ID, 1, [*built.blocks, ocr_block], index)
    assert [b.ledger_basis for b in ledger.bases] == ["pdfium", "ocr"] and ledger.balanced
    assert index.line_sha256(ocr_block.line_refs[0]) == index.line_sha256(ocr_block.line_refs[1])
    assert index.flagged_reason(ocr_block.line_refs[0]) == (False, False)
    no_total = with_ocr.model_copy(update={"ocr_nonws_total": None})
    with pytest.raises(LedgerError):
        compute_ledger(DOC_ID, 1, [*built.blocks, ocr_block], SourceIndex(no_total, extraction))


# --------------------------------------------------------------------------- #
# Đột biến sống ở vòng 1 (sổ bảo toàn, id, đơn vị)
# --------------------------------------------------------------------------- #


def test_assigned_more_than_source_is_never_balanced() -> None:
    assert not LedgerBasisCount(ledger_basis="pdfium", source_count=5, assigned_count=6).is_balanced
    assert not LedgerBasisCount(ledger_basis="pdfium", source_count=5, assigned_count=3).is_balanced
    assert LedgerBasisCount(ledger_basis="pdfium", source_count=5, assigned_count=5).is_balanced
    too_many = [LedgerBasisCount(ledger_basis="pdfium", source_count=6, assigned_count=7)]
    with pytest.raises(ValidationError):
        _ledger(bases=too_many, by_type=[LedgerTypeCount(type="paragraph", count=7)], balanced=True)
    geo = _geo()
    short = geo.model_copy(update={"pdfium_nonws_total": (geo.pdfium_nonws_total or 0) - 1})
    result = build_page_blocks(
        document_id=DOC_ID, page_number=1, geometry=short, extraction=extraction_for(geo), lang=LANG
    )
    assert result.layout_failed and result.blocks == []
    assert result.ledger is not None and not result.ledger.balanced


def test_block_ocr_text_iff_vlm_source() -> None:
    base = {
        "block_id": "blk_0000000000000000",
        "document_id": DOC_ID,
        "page_number": 1,
        "type": "paragraph",
        "bbox": None,
        "text": "t",
        "reading_order": 0,
    }
    with pytest.raises(ValidationError):
        Block.model_validate({**base, "source": "vlm", "ocr_text": None})
    with pytest.raises(ValidationError):
        Block.model_validate({**base, "source": "text_layer", "ocr_text": "x"})
    assert Block.model_validate({**base, "source": "vlm", "ocr_text": "x"}).ocr_text == "x"


def test_nonws_units_counts_nfc_codepoints() -> None:
    nfc, nfd = "ệ", unicodedata.normalize("NFD", "ệ")
    assert len(nfd) == 3 and nonws_units(nfd) == nonws_units(nfc) == 1
    assert nonws_units(" ệ ệ\t") == 2


def test_two_raw_lines_claiming_the_same_extracted_line_fail() -> None:
    geo = geometry([geo_line("alpha beta", 0.1, 0.1), geo_line("gamma delta", 0.1, 0.3)])
    shared = geo.model_copy(
        update={"lines": [line.model_copy(update={"extracted_line_indices": [0]}) for line in geo.lines]}
    )
    with pytest.raises(LedgerError):
        SourceIndex(shared, extraction_for(geo))
    result = build_page_blocks(
        document_id=DOC_ID, page_number=1, geometry=shared, extraction=extraction_for(geo), lang=LANG
    )
    assert result.layout_failed and result.blocks == [] and result.ledger is None


def test_typed_line_with_low_index_goes_after_every_raw_block() -> None:
    """Dòng gõ thêm đứng sau MỌI dòng thô theo luồng, kể cả khi `line_index` của nó nhỏ hơn chỉ số dòng thô."""
    geo = geometry([geo_line(f"raw line {i}", 0.1, 0.1 + 0.2 * i) for i in range(3)])
    raw = extraction_for(geo)
    typed = ExtractedLine(line_index=0, text="typed extra", bbox=None, conf=1.0, engine="human", edited_by_human=True)
    shifted = [line.model_copy(update={"line_index": line.line_index + 1}) for line in raw.lines]
    extraction = raw.model_copy(update={"lines": [typed, *shifted]})
    reindexed = geo.model_copy(
        update={
            "lines": [line.model_copy(update={"extracted_line_indices": [i + 1]}) for i, line in enumerate(geo.lines)]
        }
    )
    index = SourceIndex(reindexed, extraction)
    assert index.typed_lines == (0,) and index.flow_key(ConfirmedLineRef(line_index=0)) == (3, 0)
    result = build_page_blocks(document_id=DOC_ID, page_number=1, geometry=reindexed, extraction=extraction, lang=LANG)
    assert not result.layout_failed
    assert [b.text for b in sorted(result.blocks, key=lambda b: b.reading_order)] == [
        "raw line 0",
        "raw line 1",
        "raw line 2",
        "typed extra",
    ]


def test_ocr_line_sha256_joins_words_with_a_space() -> None:
    geo = _geo().model_copy(update={"ocr_lines": [geo_line("w1 w2", 0.1, 0.5)], "ocr_nonws_total": 4})
    index = SourceIndex(geo, extraction_for(_geo()))
    expected = hashlib.sha256(b"w1 w2").hexdigest()
    assert index.line_sha256(OcrWordRef(line_idx=0, word_idx=0)) == expected
    assert index.line_sha256(OcrWordRef(line_idx=0, word_idx=1)) == expected


def test_raw_line_sha256_is_nfc_so_block_id_ignores_normal_form() -> None:
    nfc = "lệnh"
    nfd = unicodedata.normalize("NFD", nfc)
    assert nfc != nfd

    def ids(word: str) -> list[str]:
        geo = geometry([geo_line(f"{word} abc", 0.1, 0.1)])
        result = build_page_blocks(
            document_id=DOC_ID, page_number=1, geometry=geo, extraction=extraction_for(geo), lang=LANG
        )
        assert not result.layout_failed
        return [b.block_id for b in result.blocks]

    assert ids(nfc) == ids(nfd)
    geo = geometry([geo_line(f"{nfd} abc", 0.1, 0.1)])
    assert (
        SourceIndex(geo, extraction_for(geo)).line_sha256(WordRef(line_idx=0, word_idx=0))
        == hashlib.sha256(f"{nfc} abc".encode()).hexdigest()
    )


def test_ocr_basis_is_present_when_ocr_lines_exist_even_if_unassigned() -> None:
    geo = _geo()
    extraction = extraction_for(geo)
    built = build_page_blocks(document_id=DOC_ID, page_number=1, geometry=geo, extraction=extraction, lang=LANG)
    with_ocr = geo.model_copy(update={"ocr_lines": [geo_line("scan words", 0.1, 0.5)], "ocr_nonws_total": 9})
    ledger = compute_ledger(DOC_ID, 1, built.blocks, SourceIndex(with_ocr, extraction))
    ocr = [b for b in ledger.bases if b.ledger_basis == "ocr"]
    assert len(ocr) == 1 and ocr[0].assigned_count == 0 and ocr[0].source_count == 9
    assert not ledger.balanced
    # Không có từ OCR và không khối nào tham chiếu OCR ⇒ không có basis ocr.
    plain = compute_ledger(DOC_ID, 1, built.blocks, SourceIndex(geo, extraction))
    assert [b.ledger_basis for b in plain.bases] == ["pdfium"]
