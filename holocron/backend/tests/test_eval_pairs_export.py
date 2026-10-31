"""Test phần tính toán của `scripts/eval_pairs_export.py` bằng dữ liệu tổng hợp (không mạng, không eval_data)."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from pathlib import Path

import pytest

from holocron_core.domain.schemas import ChunkCategory, KnowledgeChunk
from holocron_core.domain.vault import ConceptPair, ConceptPairStatus
from holocron_core.infrastructure.storage import SQLiteStore
from holocron_core.infrastructure.storage.factory import DATABASE_FILENAME
from scripts import eval_pairs_export as ep
from tests.vault_support import add_document

STAMP = datetime(2026, 10, 31, 8, 0, tzinfo=UTC)
DOC = "doc_abcdef0123456789"
RAW = "Tiến trình là một chương trình đang được thực thi trong bộ nhớ."


def _pair(index: int, chunk_id: str, *, pre_selected: bool = False, quote: str = RAW) -> ConceptPair:
    return ConceptPair(
        pair_id=f"cp_{index:04d}",
        chunk_id=chunk_id,
        subject_code="IT3070",
        chapter_code="ch2",
        term=f"Thuật ngữ {index}",
        definition_quote=quote,
        status=ConceptPairStatus.PROPOSED,
        proposed_by="llm",
        created_at=STAMP,
        pre_selected=pre_selected,
        failed_gates=[] if pre_selected else ["G1_SOURCE"],
    )


def _seed_store(data_dir: Path, pair_count: int = 6, long_quote: bool = False) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    store = SQLiteStore.open(data_dir / DATABASE_FILENAME)
    try:
        add_document(store, DOC)
        raw = RAW if not long_quote else RAW * 5
        single = KnowledgeChunk(
            chunk_id="chk_single",
            doc_id=DOC,
            subject_code="IT3070",
            chapter_code="ch2",
            page_number=4,
            raw_text=raw,
            verbatim_quote=RAW,
            category=ChunkCategory.THEORY,
            token_count=10,
        )
        span = single.model_copy(update={"chunk_id": "chk_span", "page_number": 7, "page_end": 9})
        store.add_chunks([single, span])
        for index in range(pair_count):
            chunk = "chk_span" if index % 2 else "chk_single"
            store.add_concept_pair(_pair(index, chunk, pre_selected=index % 3 == 0))
    finally:
        store.close()


def test_truncate_excerpt_cuts_by_characters() -> None:
    assert ep.truncate_excerpt("a" * 120) == "a" * 120
    cut = ep.truncate_excerpt("ế" * 121)
    assert cut == "ế" * 120 + "…"
    assert ep.truncate_excerpt("a\n  b") == "a b"


def test_page_label_range() -> None:
    assert ep.page_label(4, None) == "4"
    assert ep.page_label(7, 9) == "7–9"
    assert ep.page_label(7, 7) == "7"


def test_select_sample_is_reproducible_and_order_independent() -> None:
    pairs = [_pair(i, "chk_single") for i in range(30)]
    first = ep.select_sample(pairs, 10, 5)
    again = ep.select_sample(list(reversed(pairs)), 10, 5)
    assert [p.pair_id for p in first] == [p.pair_id for p in again]
    other = ep.select_sample(pairs, 10, 6)
    assert [p.pair_id for p in first] != [p.pair_id for p in other]
    assert len(ep.select_sample(pairs, 99, 5)) == 30


def test_wilson_interval_known_values() -> None:
    assert ep.wilson_interval(0, 0) is None
    low, high = ep.wilson_interval(8, 10) or (0.0, 0.0)
    assert math.isclose(low, 0.4902, abs_tol=1e-3)
    assert math.isclose(high, 0.9433, abs_tol=1e-3)
    low0, high0 = ep.wilson_interval(0, 5) or (1.0, 0.0)
    assert low0 == 0.0 and 0.4 < high0 < 0.5
    low1, high1 = ep.wilson_interval(5, 5) or (0.0, 0.0)
    assert high1 == 1.0 and 0.5 < low1 < 0.6


def test_parse_judgement_tokens() -> None:
    for yes in ("1", "y", "Y", "đ", " true "):
        assert ep.parse_judgement(yes) is True
    for no in ("0", "n", "s", "False"):
        assert ep.parse_judgement(no) is False
    assert ep.parse_judgement("  ") is None
    with pytest.raises(ep.EvalPairsError):
        ep.parse_judgement("có thể")


def test_score_csv_counts_and_skips_blank() -> None:
    text = (
        "term,trích,trang,pre_selected,failed_gates,đúng?\n"
        "a,x,1,true,,1\n"
        "b,x,1,true,,0\n"
        "c,x,1,false,G1_SOURCE,y\n"
        "d,x,1,true,,\n"
    )
    report = ep.score_csv(text)
    assert (report.graded, report.ungraded) == (3, 1)
    assert (report.selected_n, report.selected_correct, report.all_correct) == (2, 1, 2)
    out = ep.format_report(report)
    assert "n=2" in out and "n=3" in out and "cảnh báo" in out


def test_score_csv_missing_column_and_bad_value() -> None:
    with pytest.raises(ep.EvalPairsError, match="thiếu cột"):
        ep.score_csv("term,pre_selected\na,true\n")
    with pytest.raises(ep.EvalPairsError, match="thiếu cột"):
        ep.score_csv("term,đúng?\na,1\n")
    with pytest.raises(ep.EvalPairsError, match="không hiểu"):
        ep.score_csv("pre_selected,đúng?\ntrue,maybe\n")


def test_score_with_zero_graded_prints_na_without_dividing() -> None:
    report = ep.score_csv("pre_selected,đúng?\ntrue,\nfalse,\n")
    assert report.graded == 0
    out = ep.format_report(report)
    assert out.count("n/a") == 2


def test_score_without_warning_for_large_sample() -> None:
    rows = "".join("true,1\n" for _ in range(30))
    out = ep.format_report(ep.score_csv("pre_selected,đúng?\n" + rows))
    assert "cảnh báo" not in out and "n=30" in out


def test_render_markdown_escapes_pipes_and_has_judged_column() -> None:
    row = ep.ExportRow("a|b", "dòng\nhai", "3", True, "")
    md = ep.render_markdown([row])
    assert md.splitlines()[0].endswith("| đúng? |")
    assert "a\\|b" in md and "dòng hai" in md


def test_read_rows_from_copy_and_pages(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _seed_store(data_dir)
    original = (data_dir / DATABASE_FILENAME).read_bytes()
    rows, matched, missing = ep.read_rows(data_dir, "IT3070", "ch2", 40, 1)
    assert (matched, len(rows), missing) == (6, 6, 0)
    assert {row.page for row in rows} == {"4", "7–9"}
    assert any(row.pre_selected for row in rows) and any(row.failed_gates == "G1_SOURCE" for row in rows)
    # Chỉ đọc bản sao: file thật không đổi.
    assert (data_dir / DATABASE_FILENAME).read_bytes() == original
    none_rows, none_matched, _ = ep.read_rows(data_dir, "IT3070", "ch9", 5, 1)
    assert (none_rows, none_matched) == ([], 0)


def test_read_rows_reports_missing_chunk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data_dir = tmp_path / "data"
    _seed_store(data_dir)
    monkeypatch.setattr(SQLiteStore, "get_chunk", lambda self, chunk_id: None)
    rows, _, missing = ep.read_rows(data_dir, "IT3070", None, 40, 1)
    assert missing == len(rows) == 6
    assert all(row.page == "" for row in rows)


def test_read_rows_does_not_create_missing_database(tmp_path: Path) -> None:
    with pytest.raises(ep.EvalPairsError, match="kho dữ liệu"):
        ep.read_rows(tmp_path, "IT3070", None, 5, 1)
    assert list(tmp_path.iterdir()) == []


def test_read_rows_sees_uncheckpointed_wal(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    store = SQLiteStore.open(data_dir / DATABASE_FILENAME)  # giữ mở: dữ liệu còn nằm trong -wal
    try:
        add_document(store, DOC)
        chunk = KnowledgeChunk(
            chunk_id="chk_single",
            doc_id=DOC,
            subject_code="IT3070",
            chapter_code="ch2",
            page_number=2,
            raw_text=RAW,
            verbatim_quote=RAW,
            category=ChunkCategory.THEORY,
            token_count=10,
        )
        store.add_chunks([chunk])
        store.add_concept_pair(_pair(1, "chk_single"))
        assert (data_dir / (DATABASE_FILENAME + "-wal")).is_file()
        rows, matched, _ = ep.read_rows(data_dir, "IT3070", None, 40, 1)
    finally:
        store.close()
    assert (matched, [row.page for row in rows]) == (1, ["2"])


def test_write_outputs_encoding_and_repo_rejection(tmp_path: Path) -> None:
    rows = [ep.ExportRow("Tiến trình", "định nghĩa", "4", False, "G1_SOURCE")]
    md, csv_path = ep.write_outputs(rows, tmp_path / "out" / "mau.csv", tmp_path / "repo")
    assert md.name == "mau.md" and csv_path.name == "mau.csv"
    assert csv_path.read_bytes().startswith(b"\xef\xbb\xbf")
    assert "Tiến trình" in md.read_text(encoding="utf-8")
    report = ep.score_csv(csv_path.read_text(encoding="utf-8-sig"))
    assert report.ungraded == 1
    with pytest.raises(ep.EvalPairsError, match="ngoài cây repo"):
        ep.write_outputs(rows, tmp_path / "repo" / "sub" / "mau", tmp_path / "repo")
    assert not (tmp_path / "repo").exists()


def test_main_end_to_end_and_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    data_dir = tmp_path / "data"
    _seed_store(data_dir)
    out = tmp_path / "ra" / "mau"
    code = ep.main(["--data-dir", str(data_dir), "--subject-code", "IT3070", "--count", "4", "--output", str(out)])
    printed = capsys.readouterr().out
    assert code == 0 and "đã chọn: 4" in printed and "Thuật ngữ" not in printed
    assert (tmp_path / "ra" / "mau.csv").is_file()
    assert ep.main(["--score", str(tmp_path / "ra" / "mau.csv")]) == 0
    assert "chưa chấm" in capsys.readouterr().out
    assert ep.main(["--subject-code", "IT3070"]) == 2
    assert ep.main(["--score", str(tmp_path / "khong_co.csv")]) == 2
    repo_inside = ep.repo_root() / "holocron" / "backend" / "scripts" / "khong_duoc_ghi"
    assert ep.main(["--data-dir", str(data_dir), "--subject-code", "IT3070", "--output", str(repo_inside)]) == 2
    assert not repo_inside.with_name(repo_inside.name + ".csv").exists()


def test_score_csv_pre_selected_accepts_case_and_rejects_other_values() -> None:
    report = ep.score_csv("pre_selected,đúng?\nTRUE,1\nFalse,1\n")
    assert (report.selected_n, report.graded) == (1, 2)
    for bad in ("yes", "1", ""):
        with pytest.raises(ep.EvalPairsError, match="pre_selected"):
            ep.score_csv(f"pre_selected,đúng?\n{bad},1\n")
