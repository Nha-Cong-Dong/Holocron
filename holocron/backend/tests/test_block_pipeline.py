"""Pipeline từng trang, trần và hash cấu hình bộ dựng (ADR-010 mục 6, 13, 15; `services/block_pipeline.py`)."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from holocron_core.domain import blocks as blocks_module
from holocron_core.domain.blocks import LAYOUT_HASH_REGISTRY, LAYOUT_VERSION, Block, ExtractionLedger
from holocron_core.domain.documents import GeoLine, PageGeometry
from holocron_core.infrastructure.lang_patterns import LANG_DIR, load_lang_patterns
from holocron_core.services import block_builder, block_pipeline
from holocron_core.services.block_builder import BaseBlockSettings, BaseBlockStage
from holocron_core.services.block_pipeline import (
    PAGE_STAGES,
    PageBlocks,
    PageContext,
    PageStage,
    build_page_blocks,
    default_layout_settings_sha256,
    layout_settings_sha256,
)
from holocron_core.services.extraction_ledger import SourceIndex, compute_ledger
from holocron_core.services.page_numbers import PageNumberSettings, PageNumberStage
from holocron_core.services.reading_order import ReadingOrderStage, XyCutSettings
from scripts import export_blocks
from tests.block_samples import DOC_ID, extraction_for, geo_line, geometry, write_document

LANG = load_lang_patterns()
CROP_FLAGS = {"crop_pending", "crop_failed", "crop_capped"}


def _sample() -> PageGeometry:
    lines = [geo_line("Title Words", 0.1, 0.05, height=0.02, size=18, font_id=1)]
    lines += [geo_line(f"left {i} text", 0.1, 0.12 + i * 0.018) for i in range(3)]
    lines += [geo_line(f"right {i} text", 0.55, 0.12 + i * 0.018) for i in range(3)]
    lines += [geo_line("x one", 0.3, 0.6), geo_line("x two", 0.305, 0.602)]
    return geometry(lines)


def _run(geo: PageGeometry | None, stages: tuple[PageStage, ...] = PAGE_STAGES) -> PageBlocks:
    base = geo or _sample()
    return build_page_blocks(
        document_id=DOC_ID, page_number=1, geometry=geo, extraction=extraction_for(base), lang=LANG, stages=stages
    )


# --------------------------------------------------------------------------- #
# Thứ tự stage và nhánh lỗi
# --------------------------------------------------------------------------- #


class _Recorder:
    def __init__(self, name: str, calls: list[str], inner: PageStage | None = None, fail: bool = False) -> None:
        self.name = name
        self.calls = calls
        self.inner = inner
        self.fail = fail

    def fingerprint(self) -> dict[str, Any]:
        return {"name": self.name, "version": 1, "settings": {}}

    def run(self, context: PageContext, blocks: list[Block]) -> list[Block]:
        self.calls.append(self.name)
        if self.fail:
            raise RuntimeError("stage lỗi")
        return blocks if self.inner is None else self.inner.run(context, blocks)


class _DropFirst:
    name = "drop_first"

    def fingerprint(self) -> dict[str, Any]:
        return {"name": self.name, "version": 1, "settings": {}}

    def run(self, context: PageContext, blocks: list[Block]) -> list[Block]:
        return blocks[1:]


def test_pipeline_stage_order_and_layout_failed_returns_no_blocks() -> None:
    assert [stage.name for stage in PAGE_STAGES] == ["base_blocks", "reading_order"]
    calls: list[str] = []
    stages: tuple[PageStage, ...] = (
        _Recorder("base_blocks", calls, BaseBlockStage()),
        _Recorder("x07", calls),
        _Recorder("x06", calls),
        _Recorder("reading_order", calls, ReadingOrderStage()),
    )
    ok = _run(_sample(), stages)
    assert calls == ["base_blocks", "x07", "x06", "reading_order"]
    assert not ok.layout_failed and ok.ledger is not None and ok.ledger.balanced
    # Stage làm mất từ ⇒ ledger lệch ⇒ không có khối nào, ledger lưu balanced = false.
    lost = _run(_sample(), (BaseBlockStage(), _DropFirst(), ReadingOrderStage()))
    assert lost.layout_failed and lost.blocks == []
    assert lost.ledger is not None and not lost.ledger.balanced


def test_stage_error_marks_layout_failed_without_ledger() -> None:
    calls: list[str] = []
    result = _run(_sample(), (BaseBlockStage(), _Recorder("boom", calls, fail=True), ReadingOrderStage()))
    assert calls == ["boom"] and result.layout_failed and result.blocks == [] and result.ledger is None


def test_missing_geometry_marks_layout_failed() -> None:
    result = _run(None)
    assert result.layout_failed and result.blocks == [] and result.ledger is None


def test_xy_cut_depth_exceeded_marks_layout_failed() -> None:
    shallow = XyCutSettings(xy_cut_max_depth=1)
    stages: tuple[PageStage, ...] = (BaseBlockStage(BaseBlockSettings(xy_cut=shallow)), ReadingOrderStage(shallow))
    result = _run(_sample(), stages)
    assert result.layout_failed and result.blocks == [] and result.ledger is None
    assert not _run(_sample()).layout_failed


def test_max_page_blocks_marks_layout_failed() -> None:
    """> `MAX_PAGE_BLOCKS` khối ⇒ LAYOUT_FAILED (5 cột × 2001 dòng xen kẽ cỡ chữ, mỗi đoạn một khối)."""
    lines: list[GeoLine] = []
    rows, pitch = 2001, 1.0 / 2002
    for row in range(rows):
        size = 10.0 if row % 2 else 14.0
        y = row * pitch
        words = []
        for col in range(5):
            line = geo_line(f"c{col}", 0.02 + col * 0.2, y, height=pitch * 0.6, char_w=0.01, size=size)
            words.extend(line.words)
        lines.append(
            GeoLine(
                words=words,
                bbox=(0.02, y, 0.84, y + pitch * 0.6),
                font_size_pt=size,
                font_id=0,
                extracted_line_indices=[],
            )
        )
    geo = geometry(lines)
    result = build_page_blocks(
        document_id=DOC_ID, page_number=1, geometry=geo, extraction=extraction_for(geo), lang=LANG
    )
    assert result.layout_failed and result.blocks == [] and result.ledger is None
    small = geometry(lines[:40])
    ok = build_page_blocks(
        document_id=DOC_ID, page_number=1, geometry=small, extraction=extraction_for(small), lang=LANG
    )
    assert not ok.layout_failed and len(ok.blocks) == 200


def test_pipeline_block_cap_after_stages(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(block_pipeline, "MAX_PAGE_BLOCKS", 2)
    assert _run(_sample()).layout_failed
    monkeypatch.setattr(block_builder, "MAX_PAGE_BLOCKS", 2)
    assert _run(_sample()).layout_failed


def test_page_with_50k_words_is_bounded_and_deterministic() -> None:
    """Trang 50 000 từ: XY-cut ≤ `xy_cut_max_nodes` hoặc LAYOUT_FAILED; hai lần chạy như nhau (không đồng hồ)."""
    lines: list[GeoLine] = []
    for row in range(250):
        y = row * 0.004
        words = []
        for col in range(200):
            # Khe giữa hai từ (0.003) lớn hơn ngưỡng tách đoạn (1.6 × cao dòng 0.001): mỗi từ một đoạn dòng.
            words.extend(geo_line("w", col * 0.005, y, height=0.001, char_w=0.002).words)
        lines.append(GeoLine(words=words, bbox=(0.0, y, 0.997, y + 0.001), extracted_line_indices=[]))
    geo = geometry(lines)
    assert sum(len(line.words) for line in geo.lines) == 50_000
    extraction = extraction_for(geo)
    first = build_page_blocks(document_id=DOC_ID, page_number=1, geometry=geo, extraction=extraction, lang=LANG)
    second = build_page_blocks(document_id=DOC_ID, page_number=1, geometry=geo, extraction=extraction, lang=LANG)
    assert first == second
    assert first.layout_failed or len(first.blocks) <= XyCutSettings().xy_cut_max_nodes
    # 250 hàng + 50 000 nút con > `xy_cut_max_nodes` mặc định ⇒ trần chặn trước khi dựng khối.
    assert first.layout_failed and first.ledger is None


def test_page_blocks_failed_has_no_blocks() -> None:
    block = _run(_sample()).blocks[0]
    with pytest.raises(ValidationError):
        PageBlocks(document_id=DOC_ID, page_number=1, blocks=[block], layout_failed=True)


# --------------------------------------------------------------------------- #
# Hash cấu hình (I4)
# --------------------------------------------------------------------------- #


def test_layout_hash_registered_for_current_version() -> None:
    assert default_layout_settings_sha256(load_lang_patterns()) == LAYOUT_HASH_REGISTRY[LAYOUT_VERSION]


def test_layout_hash_v1_pinned() -> None:
    assert LAYOUT_HASH_REGISTRY[1] == "6dbbacb166ac527442c052009189b0b50f2e024b55d1762baac69175f9414ae6"


def test_layout_version_change_changes_hash() -> None:
    stage = PageNumberStage(LANG)
    assert layout_settings_sha256(stage, layout_version=LAYOUT_VERSION + 1) != layout_settings_sha256(stage)


def test_lang_toml_change_changes_layout_hash(tmp_path: Path) -> None:
    vi = tmp_path / "vi.toml"
    vi.write_text((LANG_DIR / "vi.toml").read_text(encoding="utf-8"), encoding="utf-8")
    en = LANG_DIR / "en.toml"
    same = load_lang_patterns([vi, en])
    assert default_layout_settings_sha256(same) == default_layout_settings_sha256(LANG)
    vi.write_text(
        (LANG_DIR / "vi.toml").read_text(encoding="utf-8").replace('"(tiếp)"', '"(tiếp)", "(còn nữa)"'),
        encoding="utf-8",
    )
    changed = load_lang_patterns([vi, en])
    assert changed != LANG
    assert default_layout_settings_sha256(changed) != default_layout_settings_sha256(LANG)


@pytest.mark.parametrize(
    ("stages", "page_numbers"),
    [
        ((BaseBlockStage(BaseBlockSettings(segment_gap_ratio=2.0)), ReadingOrderStage()), None),
        ((BaseBlockStage(BaseBlockSettings(heading_size_ratio=1.3)), ReadingOrderStage()), None),
        ((BaseBlockStage(BaseBlockSettings(xy_cut=XyCutSettings(xy_cut_max_depth=40))), ReadingOrderStage()), None),
        ((BaseBlockStage(BaseBlockSettings(overlap_max_pair_checks=1000)), ReadingOrderStage()), None),
        ((BaseBlockStage(BaseBlockSettings(gap_sample_height_factor=2.0)), ReadingOrderStage()), None),
        ((BaseBlockStage(BaseBlockSettings(backward_gap_height_factor=0.4)), ReadingOrderStage()), None),
        ((BaseBlockStage(), ReadingOrderStage(XyCutSettings(xy_cut_max_depth=40))), None),
        ((BaseBlockStage(), ReadingOrderStage(XyCutSettings(xy_cut_max_nodes=1000))), None),
        ((BaseBlockStage(), ReadingOrderStage(XyCutSettings(column_gap_min=0.02))), None),
        (PAGE_STAGES, PageNumberSettings(page_number_min_pages=4)),
        (PAGE_STAGES, PageNumberSettings(page_number_margin=0.08)),
        (PAGE_STAGES, PageNumberSettings(page_number_max_chars=10)),
    ],
)
def test_changing_a_stage_threshold_changes_hash(
    stages: tuple[PageStage, ...], page_numbers: PageNumberSettings | None
) -> None:
    default = default_layout_settings_sha256(LANG)
    assert layout_settings_sha256(PageNumberStage(LANG, page_numbers), stages) != default


def _strip_crop(blocks: list[Block]) -> list[dict[str, Any]]:
    out = []
    for block in blocks:
        data = block.model_dump()
        data["crop_ref"] = None
        data["flags"] = [f for f in data["flags"] if f not in CROP_FLAGS]
        out.append(data)
    return out


def test_layout_rebuild_reproducible_by_settings_sha256() -> None:
    """Cùng `PageGeometry` + cùng `layout_settings_sha256` ⇒ cùng khối và cùng id (bỏ field ảnh cắt)."""
    geo = _sample()
    roundtrip = PageGeometry.model_validate_json(geo.model_dump_json())
    first = _run(geo)
    again = _run(roundtrip)
    assert default_layout_settings_sha256(LANG) == default_layout_settings_sha256(load_lang_patterns())
    assert _strip_crop(first.blocks) == _strip_crop(again.blocks)
    assert first.ledger == again.ledger


# --------------------------------------------------------------------------- #
# Script xuất khối (đo, opt-in)
# --------------------------------------------------------------------------- #


def test_export_blocks_writes_prediction_format(tmp_path: Path) -> None:
    eval_data = tmp_path / "eval_data"
    chapter = eval_data / "arm_ch9"
    chapter.mkdir(parents=True)
    write_document(chapter / "doc.pdf", 4)
    (chapter / "doc.json").write_text("{}", encoding="utf-8")
    output = tmp_path / "out" / "pred.json"
    assert export_blocks.main([str(eval_data), str(output), "--no-ocr"]) == 0
    data = json.loads(output.read_text(encoding="utf-8"))
    pages = data["chapters"]["ch9"]["pages"]
    assert [p["page"] for p in pages] == [1, 2, 3, 4]
    for page in pages:
        ledger = page["ledger"]
        assert ledger["total_chars"] == ledger["assigned_chars"] + ledger["dropped_by_reason"]["hidden"]
        assert all(isinstance(b["text"], str) and b["type"] for b in page["blocks"])
    assert export_blocks.main([str(eval_data), str(Path(__file__).parent / "x.json")]) == 2


def test_export_page_entry_for_failed_page() -> None:
    geo = _sample()
    extraction = extraction_for(geo)
    failed = PageBlocks(document_id=DOC_ID, page_number=1, layout_failed=True)
    entry = export_blocks.page_entry(extraction, failed)
    assert len(entry["blocks"]) == len(extraction.lines) and "ledger" not in entry
    assert all(block["flags"][0] == "LAYOUT_FAILED" for block in entry["blocks"])
    built = _run(geo)
    figure = export_blocks.page_entry(extraction, built)
    assert any(block["type"] == "figure" and block["text"] == "x one\nx two" for block in figure["blocks"])
    assert blocks_module.LAYOUT_VERSION == LAYOUT_VERSION


class _Raiser:
    name = "raiser"

    def __init__(self, error: BaseException) -> None:
        self.error = error

    def fingerprint(self) -> dict[str, Any]:
        return {"name": self.name, "version": 1, "settings": {}}

    def run(self, context: PageContext, blocks: list[Block]) -> list[Block]:
        raise self.error


@pytest.mark.parametrize(
    "error",
    [
        OSError("đĩa"),
        KeyError("k"),
        IndexError("i"),
        re.error("mẫu"),
        UnicodeDecodeError("utf-8", b"\xff", 0, 1, "x"),
        ZeroDivisionError("z"),
        RecursionError("r"),
        MemoryError(),
    ],
)
def test_any_stage_exception_marks_layout_failed(error: Exception) -> None:
    result = _run(_sample(), (BaseBlockStage(), _Raiser(error), ReadingOrderStage()))
    assert result.layout_failed and result.blocks == [] and result.ledger is None


def test_non_exception_base_errors_still_propagate() -> None:
    with pytest.raises(KeyboardInterrupt):
        _run(_sample(), (BaseBlockStage(), _Raiser(KeyboardInterrupt()), ReadingOrderStage()))


class _Many:
    """Stage trả `count` bản sao một khối: chỉ để thử trần số khối trước khi đếm ledger."""

    name = "many"

    def __init__(self, block: Block, count: int) -> None:
        self.block = block
        self.count = count

    def fingerprint(self) -> dict[str, Any]:
        return {"name": self.name, "version": 1, "settings": {}}

    def run(self, context: PageContext, blocks: list[Block]) -> list[Block]:
        return [self.block] * self.count


def test_pipeline_block_cap_is_exactly_max_page_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Đúng MAX_PAGE_BLOCKS + 1 khối ⇒ `layout_failed` TRƯỚC khi tính ledger; đúng MAX thì vẫn qua bước kiểm trần."""
    reached: list[int] = []
    block = _run(_sample()).blocks[0]

    def spy(document_id: str, page_number: int, blocks: Sequence[Block], index: SourceIndex) -> ExtractionLedger:
        reached.append(len(blocks))
        return compute_ledger(document_id, page_number, blocks, index)

    monkeypatch.setattr(block_pipeline, "compute_ledger", spy)
    monkeypatch.setattr(block_pipeline, "MAX_PAGE_BLOCKS", 3)
    over = _run(_sample(), (_Many(block, 4),))
    assert over.layout_failed and over.ledger is None and reached == []
    at_cap = _run(_sample(), (_Many(block, 3),))
    assert at_cap.layout_failed and reached == [3]  # qua trần; ledger từ chối vì khối trùng tham chiếu
