"""Test phần đo cấu trúc bóc tách của `scripts/eval_layout.py` và `scripts/layout_metrics.py` (dữ liệu tổng hợp)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from holocron_core.infrastructure.document_readers.config import PdfReaderConfig
from holocron_core.infrastructure.document_readers.pdf_reader import WorkerPdfReader
from scripts import eval_layout as el
from scripts import layout_metrics as lm
from tests.pdf_samples import Page as PdfPage
from tests.pdf_samples import text_lines, write_pdf


def _gt_page(number: int, blocks: list[tuple[str, str]]) -> el.Page:
    return el.Page(number, tuple(el.Block(k, t, i) for i, (k, t) in enumerate(blocks)), "t")


def _pred(blocks: list[dict[str, object]], ledger: dict[str, object] | None = None) -> dict[str, object]:
    page: dict[str, object] = {"page": 1, "blocks": blocks}
    if ledger is not None:
        page["ledger"] = ledger
    return {"chapters": {"ch2": {"pages": [page]}}}


def _load(tmp_path: Path, data: dict[str, object]) -> dict[str, lm.NeutralDoc]:
    path = tmp_path / "p.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return el.load_predictions(path)


def _eval(
    gt: list[tuple[str, str]], docs: dict[str, lm.NeutralDoc], mode: str = "all"
) -> dict[str, float | int | str | None]:
    return el.evaluate_layout({"ch2": {1: _gt_page(1, gt)}}, docs)[mode]["ch2"]


def test_gt_type_mapping_covers_every_gt_type() -> None:
    assert set(lm.GT_TYPE_MAP) == set(el.BLOCK_TYPES)
    assert set(lm.GT_TYPE_MAP.values()) <= set(lm.BLOCK_TYPES)
    assert lm.map_gt_type("title") == "heading" and lm.map_gt_type("list") == "list_item"
    assert lm.map_gt_type("header") == "header_footer"
    assert lm.map_gt_type("formula") is None
    blocks, unmapped = el.gt_lblocks(_gt_page(1, [("title", "A"), ("list", "b")]))
    assert [b.type for b in blocks] == ["heading", "list_item"] and unmapped == 0
    assert blocks[0].level == lm.GT_HEADING_LEVEL and blocks[1].level is None


def test_unmapped_gt_block_is_counted_not_guessed() -> None:
    page = el.Page(1, (el.Block("paragraph", "a", 0), el.Block("formula", "b", 1)), "t")
    blocks, unmapped = el.gt_lblocks(page)
    assert len(blocks) == 1 and unmapped == 1


def test_predictions_json_optional_fields(tmp_path: Path) -> None:
    docs = _load(
        tmp_path,
        _pred(
            [
                {"type": "heading", "text": "A", "level": 2, "flags": ["X"], "source": "ocr", "reading_order": 3},
                {"type": "table", "text": "t", "cells": [["a", "b"]]},
                {"type": "paragraph", "text": "p"},
            ],
            {"total_chars": 10, "assigned_chars": 6, "dropped_by_reason": {"template": 4}},
        ),
    )
    doc = docs["ch2"]
    heading, table, para = doc.blocks
    assert (heading.level, heading.flags, heading.source, heading.order) == (2, ("X",), "ocr", 3)
    assert table.cells == (("a", "b"),) and para.flags == () and para.source is None and para.order == 2
    assert doc.ledger is not None and doc.ledger[0].accounted == 10


@pytest.mark.parametrize(
    "block",
    [
        {"type": "title", "text": "x"},
        {"type": "paragraph", "text": "x", "level": 1},
        {"type": "heading", "text": "x", "level": 0},
        {"type": "paragraph", "text": "x", "cells": [["a"]]},
        {"type": "paragraph", "text": "x", "flags": "X"},
        {"type": "paragraph", "text": "x", "source": "magic"},
        {"type": "paragraph"},
    ],
)
def test_predictions_json_rejects_bad_shapes(tmp_path: Path, block: dict[str, object]) -> None:
    with pytest.raises(el.EvalLayoutError):
        _load(tmp_path, _pred([block]))


def test_type_f1_and_boundary_f1(tmp_path: Path) -> None:
    gt = [("title", "Tiêu đề"), ("paragraph", "Đoạn một"), ("list", "mục a")]
    docs = _load(
        tmp_path,
        _pred(
            [
                {"type": "heading", "text": "Tiêu đề", "level": 1},
                {"type": "paragraph", "text": "Đoạn một"},
                {"type": "paragraph", "text": "mục a"},
            ]
        ),
    )
    metrics = _eval(gt, docs)
    assert metrics["boundary_f1"] == 1.0
    assert metrics["type_f1"] == pytest.approx(2 * 2 / 6)
    assert metrics["kendall_tau_mean"] == 1.0
    assert metrics["page_cer"] == 0.0


def test_tree_f1_requires_level_and_text(tmp_path: Path) -> None:
    gt = [("title", "A"), ("title", "B")]
    docs = _load(
        tmp_path,
        _pred([{"type": "heading", "text": "A", "level": 1}, {"type": "heading", "text": "B", "level": 2}]),
    )
    assert _eval(gt, docs)["tree_f1"] == pytest.approx(0.5)
    no_level = _load(tmp_path, _pred([{"type": "heading", "text": "A"}]))
    assert _eval(gt, no_level)["tree_f1"] == 0.0


def test_code_line_match_keeps_leading_whitespace(tmp_path: Path) -> None:
    gt = [("code", "MOV R0, #1\n    ADD R1, R0\nB end")]
    exact = _load(tmp_path, _pred([{"type": "code", "text": "MOV R0, #1\n    ADD R1, R0\nB end"}]))
    stripped = _load(tmp_path, _pred([{"type": "code", "text": "MOV R0, #1\nADD R1, R0\nB end"}]))
    assert _eval(gt, exact)["code_line_match"] == 1.0
    assert _eval(gt, stripped)["code_line_match"] == pytest.approx(2 / 3)
    assert _eval([("paragraph", "a")], exact)["code_line_match"] is None


def test_silent_wrong_counts_unmatched_unflagged_text(tmp_path: Path) -> None:
    gt = [("paragraph", "một đoạn đủ dài để định vị")]
    docs = _load(
        tmp_path,
        _pred(
            [
                {"type": "paragraph", "text": "một đoạn đủ dài để định vị"},
                {"type": "paragraph", "text": "zzzz qqqq"},
                {"type": "paragraph", "text": "yyyy wwww", "flags": ["LOW_CONF"]},
                {"type": "figure", "text": "ảnh"},
            ]
        ),
    )
    metrics = _eval(gt, docs)
    assert metrics["silent_wrong"] == 1 and metrics["text_blocks_pred"] == 3


def test_ledger_coverage_only_with_ledger(tmp_path: Path) -> None:
    gt = [("paragraph", "abc")]
    block = [{"type": "paragraph", "text": "abc"}]
    without = _eval(gt, _load(tmp_path, _pred(block)))
    assert without["ledger_coverage"] is None and without["ledger_pages"] is None
    full = _load(tmp_path, _pred(block, {"total_chars": 10, "assigned_chars": 7, "dropped_by_reason": {"a": 3}}))
    assert _eval(gt, full)["ledger_coverage"] == 1.0 and _eval(gt, full)["ledger_pages_exact"] == 1
    short = _load(tmp_path, _pred(block, {"total_chars": 10, "assigned_chars": 7}))
    metrics = _eval(gt, short)
    assert metrics["ledger_coverage"] == pytest.approx(0.7) and metrics["ledger_pages_exact"] == 0


def test_table_cells_not_measured_without_gt_cells(tmp_path: Path) -> None:
    docs = _load(tmp_path, _pred([{"type": "table", "text": "| a | b |", "cells": [["a", "b"]]}]))
    assert _eval([("table", "| a | b |")], docs)["table_cell_accuracy"] == lm.NOT_MEASURED


def test_table_cell_accuracy_when_gt_has_cells() -> None:
    gt = [lm.LBlock("g", 1, "table", "t", 0, cells=(("a", "b"), ("c", "d")))]
    pred = [lm.LBlock("p", 1, "table", "t", 0, cells=(("a", "b"), ("c", "x")))]
    tally = lm.page_tally(gt, pred, [(0, 0)])
    assert lm.summarize_layout(tally)["table_cell_accuracy"] == pytest.approx(0.75)


def test_missing_page_counts_all_gt_as_missing(tmp_path: Path) -> None:
    docs = _load(tmp_path, {"chapters": {"ch2": {"pages": []}}})
    metrics = _eval([("paragraph", "abc")], docs)
    assert metrics["missing_pages"] == 1 and metrics["page_cer"] == 1.0 and metrics["boundary_f1"] == 0.0


def test_dev_blind_split_and_results_files(tmp_path: Path) -> None:
    truth = {"ch2": {1: _gt_page(1, [("paragraph", "abc")])}, "ch3": {1: _gt_page(1, [("paragraph", "xyz")])}}
    docs = {
        "ch2": lm.NeutralDoc((lm.LBlock("a", 1, "paragraph", "abc", 0),), None, frozenset({1})),
        "ch3": lm.NeutralDoc((lm.LBlock("b", 1, "paragraph", "xyz", 0),), None, frozenset({1})),
    }
    report = el.evaluate_layout(truth, docs, dev=("ch2",))["all"]
    assert report["dev"]["gt_blocks"] == 1 and report["blind"]["gt_blocks"] == 1
    assert report["dev"]["boundary_f1"] == 1.0 and report["blind"]["boundary_f1"] == 1.0


def test_cli_writes_results_outside_repo_and_refuses_inside(tmp_path: Path) -> None:
    root = tmp_path / "eval_data" / "arm_ch2"
    root.mkdir(parents=True)
    gt = {
        "document": "x.pdf",
        "page_count": 1,
        "pages": [{"page": 1, "topic": "t", "blocks": [{"order": 0, "type": "paragraph", "text": "abc"}]}],
    }
    (root / "x.json").write_text(json.dumps(gt), encoding="utf-8")
    other = tmp_path / "eval_data" / "de_cuong"
    other.mkdir()
    (other / "d.pdf").write_bytes(b"%PDF")
    pred = tmp_path / "pred.json"
    pred.write_text(json.dumps(_pred([{"type": "paragraph", "text": "abc"}])), encoding="utf-8")
    out = tmp_path / "out"
    code = el.main([str(tmp_path / "eval_data"), str(out), "--predictions", str(pred), "--ai-pred", str(pred)])
    assert code == 0
    results = json.loads((out / "results.json").read_text(encoding="utf-8"))
    assert results["no_ground_truth"] == ["de_cuong"]
    assert results["results"]["all"]["ai"]["ch2"]["boundary_f1"] == 1.0
    assert "offline" in (out / "results.md").read_text(encoding="utf-8")
    assert (
        el.main(
            [
                str(tmp_path / "eval_data"),
                str(Path(__file__).resolve().parent / "out_must_not_exist"),
                "--pred",
                str(pred),
            ]
        )
        == 2
    )
    assert not (Path(__file__).resolve().parent / "out_must_not_exist").exists()


def test_baseline_line_blocks_from_real_pdf(tmp_path: Path) -> None:
    path = write_pdf(tmp_path / "a.pdf", [PdfPage(texts=text_lines(["Dong mot", "Dong hai"]))])
    pages = list(WorkerPdfReader(PdfReaderConfig()).read_pages(path, "doc_" + "0" * 16))
    doc = el.lines_to_blocks(pages)
    assert doc.pages == frozenset({1})
    assert [b.text for b in doc.blocks] == ["Dong mot", "Dong hai"]
    assert {b.type for b in doc.blocks} == {"paragraph"} and all(b.source == "text_layer" for b in doc.blocks)
    metrics = _eval([("paragraph", "Dong mot Dong hai")], {"ch2": doc})
    assert metrics["pred_blocks"] == 2 and metrics["page_cer"] == 0.0


def _eval_data(tmp_path: Path, md: str | None = None) -> Path:
    root = tmp_path / "eval_data"
    arm = root / "arm_ch2"
    arm.mkdir(parents=True, exist_ok=True)
    pages = [
        {
            "page": 1,
            "topic": "t",
            "blocks": [{"order": 0, "type": "title", "text": "A"}, {"order": 1, "type": "title", "text": "B"}],
        }
    ]
    (arm / "x.json").write_text(json.dumps({"document": "x.pdf", "page_count": 1, "pages": pages}), encoding="utf-8")
    if md is not None:
        (arm / "x.md").write_text(md, encoding="utf-8")
    return root


MD_LEVELS = "# Tài liệu\n\n## Quy ước\n\n## Trang 1\n- nguồn: x\n\n#### [title]\nA\n\n### [title]\nB\n"


def test_md_title_levels_skip_page_markers_and_document_title() -> None:
    assert lm_levels(MD_LEVELS) == {1: [4, 3]}
    assert lm_levels("## Trang 2\n\n#### [paragraph]\nx\n") == {}


def lm_levels(text: str) -> dict[int, list[int]]:
    return el.md_title_levels(text)


def test_heading_levels_from_md_are_relative_and_used(tmp_path: Path) -> None:
    root = _eval_data(tmp_path, MD_LEVELS)
    levels, mode = el.load_heading_levels(root, ["ch2"])
    assert mode == "from_md" and levels == {"ch2": {1: [2, 1]}}
    blocks, _ = el.gt_lblocks(_gt_page(1, [("title", "A"), ("title", "B")]), levels["ch2"][1])
    assert [b.level for b in blocks] == [2, 1]
    docs = {
        "ch2": lm.NeutralDoc(
            (lm.LBlock("a", 1, "heading", "A", 0, 2), lm.LBlock("b", 1, "heading", "B", 1, 1)), None, frozenset({1})
        )
    }
    truth = {"ch2": {1: _gt_page(1, [("title", "A"), ("title", "B")])}}
    assert el.evaluate_layout(truth, docs, heading_levels=levels)["all"]["ch2"]["tree_f1"] == 1.0
    assert el.evaluate_layout(truth, docs)["all"]["ch2"]["tree_f1"] == 0.5


def test_flat_heading_levels_reported_in_results(tmp_path: Path) -> None:
    root = _eval_data(tmp_path, "## Trang 1\n\n#### [title]\nA\n\n#### [title]\nB\n")
    assert el.load_heading_levels(root, ["ch2"]) == ({}, "flat")
    pred = tmp_path / "p.json"
    pred.write_text(json.dumps(_pred([{"type": "paragraph", "text": "A"}])), encoding="utf-8")
    out = tmp_path / "out"
    assert el.main([str(root), str(out), "--pred", str(pred)]) == 0
    assert json.loads((out / "results.json").read_text(encoding="utf-8"))["gt_heading_levels"] == "flat"


def test_level_count_mismatch_falls_back_to_level_one() -> None:
    blocks, _ = el.gt_lblocks(_gt_page(1, [("title", "A")]), [3, 2])
    assert blocks[0].level == lm.GT_HEADING_LEVEL


def test_prediction_pages_outside_ground_truth_count_as_silent_wrong() -> None:
    extra = lm.NeutralDoc(
        (
            lm.LBlock("a", 9, "paragraph", "zz", 0),
            lm.LBlock("b", 9, "paragraph", "yy", 1, flags=("X",)),
            lm.LBlock("c", 9, "figure", "f", 2),
        ),
        None,
        frozenset({1, 9}),
    )
    truth = {"ch2": {1: _gt_page(1, [("paragraph", "abc")])}}
    metrics = el.evaluate_layout(truth, {"ch2": extra})["all"]["ch2"]
    assert metrics["silent_wrong"] == 1 and metrics["text_blocks_pred"] == 2 and metrics["pred_blocks"] == 3


def test_match_pdfs_pairs_by_stem(tmp_path: Path) -> None:
    for name in ("a.pdf", "a.json", "b.pdf", "c.json"):
        (tmp_path / name).write_bytes(b"x")
    matched, unmatched = el.match_pdfs(tmp_path)
    assert [p.name for p in matched] == ["a.pdf"] and [p.name for p in unmatched] == ["b.pdf"]


def _bad(tmp_path: Path, content: str | dict[str, object]) -> list[str]:
    root = _eval_data(tmp_path)
    pred = tmp_path / "bad.json"
    pred.write_text(content if isinstance(content, str) else json.dumps(content), encoding="utf-8")
    return [str(root), str(tmp_path / "out"), "--pred", str(pred)]


def test_layout_mode_error_paths_return_2(tmp_path: Path) -> None:
    assert el.main(_bad(tmp_path, {"chapters": {"ch9": {"pages": []}}})) == 2
    assert el.main(_bad(tmp_path, {"chapters": {"ch2": {"pages": [{"page": 1}, {"page": 1}]}}})) == 2
    assert el.main(_bad(tmp_path, "{not json")) == 2
    for ledger in (
        {"total_chars": -1, "assigned_chars": 0},
        {"total_chars": "5", "assigned_chars": 0},
        {"total_chars": 5, "assigned_chars": 0, "dropped_by_reason": {"a": -2}},
        {"total_chars": 5, "assigned_chars": 0, "dropped_by_reason": []},
    ):
        assert el.main(_bad(tmp_path, _pred([{"type": "paragraph", "text": "a"}], ledger))) == 2


def test_layout_mode_requires_results_dir_and_rejects_legacy_outputs(tmp_path: Path) -> None:
    root = _eval_data(tmp_path)
    assert el.main([str(root)]) == 2
    out = tmp_path / "out"
    assert el.main([str(root), str(out), "--write-oracle", str(tmp_path / "o.json")]) == 2
    assert el.main([str(root), str(out), "--output", str(tmp_path / "r.json")]) == 2


def test_flat_levels_match_on_text_and_page_only() -> None:
    gt = [lm.LBlock("g", 1, "heading", "A", 0, 1)]
    pred = [lm.LBlock("p", 1, "heading", "A", 0, 2), lm.LBlock("q", 1, "heading", "B", 1)]
    flat = lm.summarize_layout(lm.page_tally(gt, pred, [(0, 0)], flat_levels=True))
    assert flat["tree_f1"] == pytest.approx(2 / 3)
    strict = lm.summarize_layout(lm.page_tally(gt, pred[:1], [(0, 0)]))
    assert strict["tree_f1"] == 0.0


def test_from_md_levels_still_require_exact_level(tmp_path: Path) -> None:
    root = _eval_data(tmp_path, MD_LEVELS)
    levels, mode = el.load_heading_levels(root, ["ch2"])
    assert mode == "from_md"
    truth = {"ch2": {1: _gt_page(1, [("title", "A"), ("title", "B")])}}
    wrong = lm.NeutralDoc(
        (lm.LBlock("a", 1, "heading", "A", 0, 1), lm.LBlock("b", 1, "heading", "B", 1, 1)), None, frozenset({1})
    )
    assert el.evaluate_layout(truth, {"ch2": wrong}, heading_levels=levels)["all"]["ch2"]["tree_f1"] == 0.5


def test_level_mismatch_pages_counted_in_results(tmp_path: Path) -> None:
    root = _eval_data(tmp_path, "## Trang 1\n\n#### [title]\nA\n")
    raw = el.raw_heading_levels(root, ["ch2"])
    truth = {"ch2": {1: _gt_page(1, [("title", "A"), ("title", "B")])}}
    assert el.count_level_mismatch_pages(truth, raw) == 1
    pred = tmp_path / "p.json"
    pred.write_text(json.dumps(_pred([{"type": "paragraph", "text": "A"}])), encoding="utf-8")
    out = tmp_path / "out"
    assert el.main([str(root), str(out), "--pred", str(pred)]) == 0
    assert json.loads((out / "results.json").read_text(encoding="utf-8"))["heading_level_mismatch_pages"] == 1


def test_page_cer_skipped_over_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lm, "MAX_CER_PAGE_CHARS", 5)
    gt = [lm.LBlock("g", 1, "paragraph", "abcdefghij", 0)]
    pred = [lm.LBlock("p", 1, "paragraph", "abcdefghij", 0)]
    metrics = lm.summarize_layout(lm.page_tally(gt, pred, [(0, 0)]))
    assert metrics["page_cer"] == lm.NOT_MEASURED and metrics["page_cer_skipped_pages"] == 1
    small = lm.summarize_layout(lm.page_tally([lm.LBlock("g", 1, "paragraph", "abc", 0)], None, []))
    assert small["page_cer"] == 1.0 and small["page_cer_skipped_pages"] == 0


def test_pred_file_size_and_block_caps_exit_2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    args = _bad(tmp_path, _pred([{"type": "paragraph", "text": "a"}, {"type": "paragraph", "text": "b"}]))
    assert el.main(args) == 0
    monkeypatch.setattr(lm, "MAX_PRED_BLOCKS", 1)
    assert el.main(args) == 2
    monkeypatch.setattr(lm, "MAX_PRED_BLOCKS", 100)
    monkeypatch.setattr(lm, "MAX_PRED_BYTES", 10)
    assert el.main(args) == 2


def test_baseline_with_several_pdfs_in_a_chapter_is_an_error(tmp_path: Path) -> None:
    arm = tmp_path / "arm_ch2"
    arm.mkdir()
    for name in ("a.pdf", "a.json", "b.pdf", "b.json"):
        (arm / name).write_bytes(b"x")
    with pytest.raises(el.EvalLayoutError):
        el.baseline_docs(tmp_path, ["ch2"], use_ocr=False)


def test_unknown_dev_chapter_exits_2_and_unmapped_sums_into_groups(tmp_path: Path) -> None:
    args = _bad(tmp_path, _pred([{"type": "paragraph", "text": "a"}]))
    assert el.main([*args, "--dev", "ch9"]) == 2
    assert el.main([*args, "--dev", "ch2"]) == 0
    results = json.loads((tmp_path / "out" / "results.json").read_text(encoding="utf-8"))
    assert results["results"]["all"]["offline"]["dev"]["unmapped"] == 0
    assert results["results"]["all"]["offline"]["blind"]["unmapped"] == 0
