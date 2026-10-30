"""Test phần tính toán của `scripts/eval_layout.py` bằng dữ liệu tổng hợp (không mạng, không eval_data)."""

from __future__ import annotations

import json
import math
import unicodedata
from pathlib import Path

import pytest

from scripts import eval_layout as el


def _page(number: int, topic: str | None, blocks: list[tuple[str, str]], unit: str | None = None) -> el.Page:
    return el.Page(
        number,
        tuple(el.Block(kind, text, order, unit) for order, (kind, text) in enumerate(blocks)),
        topic,
    )


def _gt_json(pages: list[dict[str, object]]) -> str:
    return json.dumps({"document": "x.pdf", "page_count": len(pages), "pages": pages}, ensure_ascii=False)


def test_levenshtein_and_normalize() -> None:
    assert el.levenshtein("kitten", "sitting") == 3
    assert el.levenshtein("", "abc") == 3 and el.levenshtein("abc", "") == 3
    assert el.levenshtein("ế", "ế") == 0
    nfd = unicodedata.normalize("NFD", "Tiến  trình")
    assert el.normalize(nfd) == "Tiến trình"


def test_interval_iou() -> None:
    assert el.interval_iou((0, 10), (0, 10)) == 1.0
    assert el.interval_iou((0, 10), (5, 15)) == pytest.approx(5 / 15)
    assert el.interval_iou((0, 5), (5, 10)) == 0.0
    assert el.interval_iou((0, 0), (0, 0)) == 0.0


def test_greedy_match_is_one_to_one_and_respects_threshold() -> None:
    gt = [(0, 10), (11, 20)]
    pred = [(0, 10), (0, 9), (12, 20)]
    pairs = el.greedy_match(gt, pred, 0.5)
    assert sorted(pairs) == [(0, 0), (1, 2)]
    assert el.greedy_match([(0, 10)], [(8, 18)], 0.5) == []
    assert el.greedy_match([(0, 10)], [(2, 10)], 0.5) == [(0, 0)]


def test_boundary_f1_with_split_and_merged_blocks() -> None:
    gt = {1: _page(1, "t", [("paragraph", "alpha beta gamma"), ("paragraph", "delta epsilon zz")])}
    exact = {1: _page(1, None, [("paragraph", "alpha beta gamma"), ("paragraph", "delta epsilon zz")])}
    tally = el.evaluate_chapter("ch2", gt, exact, "all", 0.5)
    assert el.summarize(tally)["boundary_f1"] == 1.0
    merged = {1: _page(1, None, [("paragraph", "alpha beta gamma delta epsilon zz")])}
    summary = el.summarize(el.evaluate_chapter("ch2", gt, merged, "all", 0.5))
    assert summary["matched"] == 0 and summary["boundary_f1"] == 0.0
    one_of_two = {1: _page(1, None, [("paragraph", "alpha beta gamma")])}
    summary = el.summarize(el.evaluate_chapter("ch2", gt, one_of_two, "all", 0.5))
    assert summary["boundary_precision"] == 1.0 and summary["boundary_recall"] == 0.5
    assert summary["boundary_f1"] == pytest.approx(2 / 3)


def test_cer_and_type_accuracy_on_matched_blocks() -> None:
    gt = {1: _page(1, "t", [("title", "abcdefghij"), ("code", "xyz xyz")])}
    pred = {1: _page(1, None, [("title", "abcdefghix"), ("paragraph", "xyz xyz")])}
    summary = el.summarize(el.evaluate_chapter("ch2", gt, pred, "all", 0.5))
    assert summary["matched"] == 2
    assert summary["cer"] == pytest.approx(1 / 17)
    assert summary["type_accuracy"] == 0.5


def test_kendall_tau_cases() -> None:
    assert el.kendall_tau([]) is None
    assert el.kendall_tau([(0, 0)]) is None
    assert el.kendall_tau([(0, 0), (1, 1), (2, 2)]) == 1.0
    assert el.kendall_tau([(0, 2), (1, 1), (2, 0)]) == -1.0
    assert el.kendall_tau([(0, 0), (1, 2), (2, 1)]) == pytest.approx(1 / 3)


def test_reading_order_tau_uses_matched_blocks_and_skips_short_pages() -> None:
    gt = {
        1: _page(1, "t", [("paragraph", "một hai ba"), ("paragraph", "bốn năm sáu"), ("paragraph", "bảy tám chín")]),
        2: _page(2, "t", [("paragraph", "chỉ một khối duy nhất")]),
    }
    swapped = el.Page(
        1,
        (
            el.Block("paragraph", "bốn năm sáu", 0),
            el.Block("paragraph", "một hai ba", 1),
            el.Block("paragraph", "bảy tám chín", 2),
        ),
    )
    pred = {1: swapped, 2: _page(2, None, [("paragraph", "chỉ một khối duy nhất")])}
    summary = el.summarize(el.evaluate_chapter("ch2", gt, pred, "all", 0.5))
    assert summary["matched"] == 4
    assert summary["kendall_tau_pages"] == 1
    assert summary["kendall_tau_mean"] == pytest.approx(1 / 3)


def test_v_measure_cases() -> None:
    assert el.v_measure(["a", "a", "b", "b"], ["x", "x", "y", "y"]) == pytest.approx(1.0)
    assert el.v_measure(["a", "a", "b", "b"], ["y", "y", "x", "x"]) == pytest.approx(1.0)
    assert el.v_measure(["a", "a", "a"], ["x", "x", "x"]) == 1.0
    assert el.v_measure([], []) == 1.0
    # Một nhóm dự đoán duy nhất cho nhiều chủ đề: đồng nhất 0 ⇒ V = 0.
    assert el.v_measure(["a", "a", "b", "b"], ["x", "x", "x", "x"]) == 0.0
    # Mỗi phần tử một nhóm: đồng nhất 1, đầy đủ thấp.
    split = el.v_measure(["a", "a", "b", "b"], ["1", "2", "3", "4"])
    assert 0.0 < split < 1.0
    expected = 2 * 1.0 * 0.5 / 1.5
    assert math.isclose(split, expected, rel_tol=1e-9)
    with pytest.raises(ValueError):
        el.v_measure(["a"], [])


def test_pred_group_uses_unit_of_longest_block_and_missing_pages_get_own_group() -> None:
    page = el.Page(
        1,
        (el.Block("paragraph", "ngắn", 0, "u1"), el.Block("paragraph", "đoạn dài hơn nhiều", 1, "u2")),
    )
    assert el._pred_group(page.blocks) == "u2"
    assert el._pred_group((el.Block("paragraph", "x", 0, None),)) == el.NO_UNIT_GROUP
    truth = {1: _page(1, "a", [("paragraph", "một")]), 2: _page(2, "b", [("paragraph", "hai")])}
    tally = el.evaluate_chapter("ch2", truth, {1: _page(1, None, [("paragraph", "một")], "u")}, "all", 0.5)
    assert tally.missing_pages == 1 and tally.pages == 2
    assert tally.pred_groups[1].endswith(el.MISSING_PAGE_GROUP)


def test_chrome_mode_drops_header_and_page_number() -> None:
    gt = {
        1: _page(1, "t", [("header", "tiêu đề đầu trang"), ("paragraph", "nội dung chính"), ("page_number", "7")]),
    }
    pred = {1: _page(1, None, [("paragraph", "nội dung chính")])}
    chrome = el.summarize(el.evaluate_chapter("ch2", gt, pred, "no_chrome", 0.5))
    assert chrome["boundary_f1"] == 1.0
    everything = el.summarize(el.evaluate_chapter("ch2", gt, pred, "all", 0.5))
    assert everything["boundary_recall"] == pytest.approx(1 / 3)


def test_locate_finds_substring_and_partial_overlap() -> None:
    assert el.locate("aaa bbb ccc", "bbb") == (4, 7)
    assert el.locate("aaa bbb ccc", "") == (0, 0)
    start, end = el.locate("aaa bbb ccc", "bbb cxx")
    assert (start, end) == (4, 11)
    assert el.locate("aaa", "zzz") == (0, 0)


def test_parsing_errors_for_unknown_type_and_bad_shapes(tmp_path: Path) -> None:
    with pytest.raises(el.EvalLayoutError, match="loại khối lạ"):
        el.parse_pages([{"page": 1, "blocks": [{"type": "sidebar", "text": "x"}]}], "dự đoán", ground_truth=False)
    with pytest.raises(el.EvalLayoutError, match="topic"):
        el.parse_pages([{"page": 1, "blocks": []}], "gt", ground_truth=True)
    with pytest.raises(el.EvalLayoutError, match="lặp"):
        el.parse_pages([{"page": 1, "blocks": []}, {"page": 1, "blocks": []}], "dự đoán", ground_truth=False)
    with pytest.raises(el.EvalLayoutError, match="blocks"):
        el.parse_pages([{"page": 1, "blocks": "x"}], "dự đoán", ground_truth=False)
    with pytest.raises(el.EvalLayoutError, match="không thấy ground truth"):
        el.load_ground_truth(tmp_path)
    bad = tmp_path / "pred.json"
    bad.write_text("[]", encoding="utf-8")
    with pytest.raises(el.EvalLayoutError, match="chapters"):
        el.load_prediction(bad)


def _write_gt(root: Path) -> None:
    for chapter, topic in (("ch2", "alpha"), ("ch3", "beta")):
        directory = root / f"arm_{chapter}"
        directory.mkdir(parents=True)
        pages = [
            {
                "page": n,
                "source": "s",
                "topic": topic if n == 1 else f"{topic}{n}",
                "traps": [],
                "blocks": [
                    {"order": 0, "type": "title", "text": f"Tiêu đề {chapter} {n}"},
                    {"order": 1, "type": "paragraph", "text": f"Đoạn văn của trang {n} chương {chapter}"},
                ],
            }
            for n in (1, 2, 3)
        ]
        (directory / "gt.json").write_text(_gt_json(pages), encoding="utf-8")


def test_oracle_prediction_scores_perfectly_and_splits_dev_blind(tmp_path: Path) -> None:
    gt_root = tmp_path / "gt"
    _write_gt(gt_root)
    truth = el.load_ground_truth(gt_root)
    pred_file = tmp_path / "pred.json"
    pred_file.write_text(json.dumps(el.oracle_prediction(truth), ensure_ascii=False), encoding="utf-8")
    report = el.evaluate(truth, el.load_prediction(pred_file))
    for mode in el.MODES:
        assert set(report[mode]) == {"ch2", "ch3", "dev", "blind"}
        for group in ("ch2", "ch3", "dev", "blind"):
            metrics = report[mode][group]
            assert metrics["boundary_f1"] == 1.0 and metrics["cer"] == 0.0
            assert metrics["type_accuracy"] == 1.0 and metrics["v_measure"] == pytest.approx(1.0)
            assert metrics["kendall_tau_mean"] == 1.0
        assert report[mode]["dev"]["pages"] == 3 and report[mode]["blind"]["pages"] == 3
    assert "n/a" not in el.format_report(report)


def test_missing_chapter_in_prediction_counts_as_missing_pages(tmp_path: Path) -> None:
    gt_root = tmp_path / "gt"
    _write_gt(gt_root)
    truth = el.load_ground_truth(gt_root)
    report = el.evaluate(truth, {})
    assert report["all"]["ch2"]["missing_pages"] == 3
    assert report["all"]["ch2"]["boundary_f1"] == 0.0
    assert report["all"]["ch2"]["cer"] is None
    assert "n/a" in el.format_report(report)


def test_main_rejects_output_inside_repo_and_writes_oracle(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    gt_root = tmp_path / "gt"
    _write_gt(gt_root)
    inside = el.repo_root() / "holocron" / "backend" / "scripts" / "khong_duoc_ghi.json"
    assert el.main(["--gt-root", str(gt_root), "--write-oracle", str(inside)]) == 2
    assert not inside.exists()
    oracle = tmp_path / "ra" / "oracle.json"
    assert el.main(["--gt-root", str(gt_root), "--write-oracle", str(oracle)]) == 0
    result = tmp_path / "ra" / "ketqua.json"
    assert el.main(["--gt-root", str(gt_root), "--pred", str(oracle), "--output", str(result)]) == 0
    printed = capsys.readouterr().out
    assert "chế độ no_chrome" in printed and "Đoạn văn" not in printed
    assert json.loads(result.read_text(encoding="utf-8"))["all"]["blind"]["boundary_f1"] == 1.0
    assert el.main(["--gt-root", str(gt_root)]) == 2
    assert el.main(["--gt-root", str(gt_root), "--pred", str(oracle), "--iou", "0"]) == 2
