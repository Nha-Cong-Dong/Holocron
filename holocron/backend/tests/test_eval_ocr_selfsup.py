"""Phần thuần của `scripts/eval_ocr_selfsup.py` (CER, WER, tỉ lệ giữ dấu, từ chối ghi trong repo, tham số); không OCR."""

from __future__ import annotations

import random
import unicodedata
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace

import pytest

from holocron_core.infrastructure.document_readers.config import PdfReaderConfig
from holocron_core.infrastructure.document_readers.protocol import RawPdfPage, RawTextLine
from holocron_core.infrastructure.document_readers.tesseract import RunResult, TesseractEngine
from scripts import eval_ocr_selfsup
from scripts.eval_ocr_selfsup import (
    ComboPage,
    PageScore,
    Report,
    aggregate,
    build_parser,
    build_rapidocr,
    diacritic_chars,
    has_diacritic,
    kept_flags,
    main,
    markdown,
    normalize,
    parse_int_list,
    rapidocr_text,
    recommendation,
    refuses_output_dir,
    repo_roots,
    sample_evenly,
    score_page,
    sequence_distance,
    summarize,
    tesseract_text,
    to_payload,
    truth_text,
)


def _reference(a: str, b: str) -> int:
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def test_sequence_distance_matches_reference_on_random_strings() -> None:
    rng = random.Random(7)
    for _ in range(60):
        a = "".join(rng.choice("abcđ ") for _ in range(rng.randint(0, 25)))
        b = "".join(rng.choice("abcđ ") for _ in range(rng.randint(0, 25)))
        assert sequence_distance(a, b) == _reference(a, b)


def test_sequence_distance_on_word_lists() -> None:
    assert sequence_distance(["a", "b", "c"], ["a", "c"]) == 1
    assert sequence_distance([], ["x"]) == 1


def test_normalize_nfc_and_whitespace() -> None:
    nfd = unicodedata.normalize("NFD", "Việt  Nam\n")
    assert normalize(nfd) == "Việt Nam"


def test_has_diacritic() -> None:
    assert has_diacritic("ế") and has_diacritic("đ") and has_diacritic("Ư")
    assert not has_diacritic("a") and not has_diacritic("7")
    assert diacritic_chars("Việt 12 đa") == "ệđ"


def test_perfect_prediction_scores_zero_error() -> None:
    score = score_page("Tiếng Việt có dấu 123", "Tiếng  Việt có dấu 123")
    assert score.char_distance == 0 and score.word_distance == 0 and score.digit_errors == 0
    assert score.diacritic_total == score.diacritic_kept > 0
    assert score.diacritic_seq_distance == 0


def test_dropped_diacritics_lower_retention_but_not_other_letters() -> None:
    truth = "Việt Nam đẹp"  # ệ, đ, ẹ có dấu
    score = score_page(truth, "Viet Nam dep")
    assert score.diacritic_total == 3
    assert score.diacritic_kept == 0
    assert score.char_distance == 3
    assert score.word_distance == 2
    assert score.diacritic_seq_distance == 3


def test_kept_flags_marks_aligned_matches() -> None:
    assert kept_flags("abc", "abc") == [True, True, True]
    assert kept_flags("abc", "") == [False, False, False]
    assert kept_flags("abc", "axc") == [True, False, True]


def test_digit_errors_counted_on_digit_sequence() -> None:
    score = score_page("so 1234", "so 1284")
    assert score.truth_digits == 4 and score.digit_errors == 1


def test_aggregate_micro_ratios() -> None:
    a = PageScore(100, 10, 20, 4, 10, 8, 2, 5, 1)
    b = PageScore(100, 30, 20, 6, 10, 4, 6, 5, 5)
    result = aggregate([a, b], [1.0, 3.0], failures=1)
    assert result["cer_micro"] == pytest.approx(0.2)
    assert result["wer_micro"] == pytest.approx(0.25)
    assert result["diacritic_retention"] == pytest.approx(0.6)
    assert result["diacritic_cer"] == pytest.approx(0.4)
    assert result["digit_accuracy_micro"] == pytest.approx(0.4)  # 4/5 + 0/5 trên 10 chữ số
    assert result["seconds_median"] == 2.0 and result["failures"] == 1
    assert aggregate([], [])["cer_micro"] is None


def test_refuses_output_dir_inside_repo(tmp_path: Path) -> None:
    assert refuses_output_dir(tmp_path / "repo" / "out", tmp_path / "repo")
    assert not refuses_output_dir(tmp_path / "eval" / "x01", tmp_path / "repo")


def test_parse_int_list_and_arguments() -> None:
    assert parse_int_list("150, 200,300") == [150, 200, 300]
    for bad in ("", "0", "3,3", "-1"):
        with pytest.raises(ValueError):
            parse_int_list(bad)
    args = build_parser().parse_args(["root", "out"])
    assert args.dpi == [150, 200, 300] and args.psm == [3, 4, 6] and args.max_pages == 40 and not args.degrade
    args = build_parser().parse_args(["root", "out", "--dpi", "200", "--psm", "6", "--max-pages", "5", "--degrade"])
    assert args.dpi == [200] and args.psm == [6] and args.max_pages == 5 and args.degrade


def test_sample_evenly_is_deterministic_and_spread() -> None:
    items = list(range(100))
    assert sample_evenly(items, 4) == [0, 25, 50, 75]
    assert sample_evenly(items[:3], 40) == [0, 1, 2]
    assert sample_evenly(items, 0) == []


def test_has_diacritic_only_vietnamese_alphabet() -> None:
    assert has_diacritic("ế") and has_diacritic("é") and has_diacritic("Đ")
    assert not has_diacritic("ü") and not has_diacritic("ç") and not has_diacritic("e")


def test_truth_text_drops_flagged_lines() -> None:
    def line(text: str, **extra: object) -> RawTextLine:
        return RawTextLine.model_validate({"text": text, "bbox": None, "words": [], **extra})

    lines = [
        line("giu"),
        line("mo", flags=["LOW_TEXT_CONTRAST"]),
        line("che", flags=["TEXT_OCCLUDED"]),
        line("dau", header_footer=True),
        line("cuoi"),
    ]
    raw = RawPdfPage(page_index=0, width_pt=720, height_pt=540, lines=lines, image_area_ratio=0, path_area_ratio=0)
    assert truth_text(raw) == "giu\ncuoi"


def _engine(result: RunResult) -> TesseractEngine:
    def run(args: Sequence[str], data: bytes, timeout: float, limit: int) -> RunResult:
        assert "--psm" in args and "6" in args
        return result

    return TesseractEngine(command="tesseract", tessdata_dir=None, run=run)


def test_tesseract_text_ok_and_failure_branches() -> None:
    config = PdfReaderConfig()
    assert tesseract_text(_engine(RunResult("ok", "Việt".encode())), b"", 300, 6, config) == ("Việt", True)
    assert tesseract_text(_engine(RunResult("timeout")), b"", 300, 6, config) == ("", False)
    assert tesseract_text(_engine(RunResult("ok", b"\xff\xfe")), b"", 300, 6, config) == ("", False)


def test_rapidocr_text_handles_missing_txts() -> None:
    assert rapidocr_text(lambda _png: SimpleNamespace(txts=None), b"") == ""
    assert rapidocr_text(lambda _png: SimpleNamespace(txts=("a", "b")), b"") == "a b"


def test_build_rapidocr_missing_package_or_models(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def missing(_name: str) -> object:
        raise ImportError

    monkeypatch.setattr(eval_ocr_selfsup.importlib, "import_module", missing)
    assert build_rapidocr() is None
    fake = SimpleNamespace(__file__=str(tmp_path / "rapidocr" / "__init__.py"), RapidOCR=lambda params: None)
    monkeypatch.setattr(eval_ocr_selfsup.importlib, "import_module", lambda _name: fake)
    assert build_rapidocr() is None  # thiếu file model


def test_main_refuses_output_dir_inside_repo(capsys: pytest.CaptureFixture[str]) -> None:
    inside = eval_ocr_selfsup.REPO_ROOT / "tmp_x01_should_not_exist"
    assert main(["whatever", str(inside)]) == 2
    assert not inside.exists()
    assert "Từ chối" in capsys.readouterr().err


def test_repo_roots_include_worktree_and_are_absolute() -> None:
    roots = repo_roots()
    assert roots[0] == eval_ocr_selfsup.REPO_ROOT.resolve()
    assert all(root.is_absolute() for root in roots)
    for root in roots:
        assert refuses_output_dir(root / "out")


def test_kept_flags_properties_on_random_strings() -> None:
    rng = random.Random(11)
    for _ in range(80):
        a = "".join(rng.choice("abcd") for _ in range(rng.randint(0, 20)))
        b = "".join(rng.choice("abcd") for _ in range(rng.randint(0, 20)))
        flags = kept_flags(a, b)
        assert len(flags) == len(a)
        assert sum(flags) <= min(len(a), len(b))
        assert sequence_distance(a, b) >= len(a) - sum(flags)  # mỗi ký tự không giữ tốn ít nhất 1
        assert all(kept_flags(a, a))
    assert kept_flags("abcde", "abXde") == [True, True, False, True, True]


def _page(file: str, dpi: int, psm: int | None, engine: str, ok: bool = True, group: str = "dev") -> ComboPage:
    score = score_page("Việt Nam 12", "Viet Nam 12") if ok else None
    return ComboPage("s", group, file, 1, engine, dpi, psm, ok, 1.0, score)


def test_failed_calls_excluded_from_metrics_and_reported() -> None:
    pages = [
        _page("a.pdf", 300, 6, "tesseract"),
        _page("a.pdf", 300, 6, "tesseract", ok=False),
        _page("a.pdf", 300, None, "rapidocr"),
        _page("b.pdf", 300, 6, "tesseract", group="blind"),
    ]
    summary = summarize(pages)
    cell = summary["dev"]["tesseract dpi=300 psm=6"]
    assert cell["pages"] == 1 and cell["failures"] == 1  # lần lỗi không tính như chuỗi rỗng
    assert cell["cer_micro"] == pytest.approx(1 / 11)
    assert set(summary) == {"dev", "blind", "set:s", "doc:s/a.pdf", "doc:s/b.pdf"}
    report = Report("root", [300], [6], False, 1, 40, 170, pages, {"s": 3}, {"s": 0})
    text = markdown(report, summary)
    assert "CẢNH BÁO" in text and "Lỗi gọi" in text and "Khuyến nghị" in text
    payload = to_payload(report, summary)
    rows = payload["pages"]
    assert isinstance(rows, list)
    failed_rows = [p for p in rows if isinstance(p, dict) and not p["ok"]]
    assert len(failed_rows) == 1 and "cer" not in failed_rows[0]


def test_recommendation_without_failures_and_degrade() -> None:
    summary = summarize([_page("a.pdf", 300, 6, "tesseract"), _page("a.pdf", 300, None, "rapidocr")])
    lines = recommendation(summary, degrade=True)
    assert any("không có lần gọi" in line.lower() for line in lines)
    assert any("--degrade" in line and "mô phỏng" in line for line in lines)
    assert any("Tesseract tốt nhất" in line for line in lines)
