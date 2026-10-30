"""Kiểm bộ chữ của `vie.traineddata` dùng chung giữa kiểm lúc khởi động và worker đọc PDF (duyệt 2026-10-30).

- Chỉ kiểm `vie.traineddata` ở ĐÚNG thư mục lần gọi OCR thật dùng (`tesseract.resolve_tessdata_dir`, duyệt
  2026-10-30 "Tessdata 5.x"): `--tessdata-dir` app truyền (`HOLOCRON_TESSDATA_DIR`) → thư mục ở dòng đầu
  `--list-langs` (Tesseract 5.x) → với Tesseract 4.x, đường lùi là thư mục mặc định của bản cài (rồi `tessdata` cạnh
  file chạy trên Windows). Thư mục đã xác định không có file thường ⇒ thiếu `vie`; đường lùi không thấy ⇒ bỏ qua kiểm.
  `TESSDATA_PREFIX` không tham gia.
- Worker (`_pdf_worker.build_ocr`) gọi CÙNG hàm `tesseract.check_vie_charset`: thiếu chữ ngoài danh sách cho phép ⇒
  không có engine, trang cần OCR thành `failed` (`OCR_ENGINE_MISSING`, ADR-008 mục 14).
- Test opt-in với `vie.traineddata` CHÍNH THỨC (tessdata, tessdata_best, tessdata_fast): đặt
  `HOLOCRON_TEST_TESSDATA_STD`, `HOLOCRON_TEST_TESSDATA_BEST`, `HOLOCRON_TEST_TESSDATA_FAST` trỏ tới thư mục chứa
  `vie.traineddata` của từng bản; không đặt thì test tự bỏ qua. Không commit file `.traineddata` thật.

Fixture `.traineddata` ở đây là file tự tạo tối thiểu (chỉ có unicharset của LSTM), không chép từ bản chính thức.
"""

from __future__ import annotations

import logging
import os
import struct
import subprocess
import sys
import unicodedata
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

from holocron_core.domain.documents import PageExtraction
from holocron_core.infrastructure.document_readers import _pdf_worker
from holocron_core.infrastructure.document_readers._pdf_worker import build_ocr
from holocron_core.infrastructure.document_readers.config import (
    ENV_TESSDATA_DIR,
    ENV_TESSERACT_CMD,
    OCR_CHARSET_APPROVED_MISSING,
    TESSDATA_DEFAULT_DIRS,
    OcrSettings,
    PdfReaderConfig,
    _has_root,
    load_ocr_settings,
)
from holocron_core.infrastructure.document_readers.pdf_reader import WorkerPdfReader
from holocron_core.infrastructure.document_readers.pdfium_adapter import PdfiumBackend
from holocron_core.infrastructure.document_readers.protocol import (
    PageMessage,
    PageRequest,
    ReadyMessage,
    WorkerRequest,
)
from holocron_core.infrastructure.document_readers.tesseract import (
    TESSDATA_LSTM_UNICHARSET,
    VIE_TRAINEDDATA,
    CharsetCheck,
    Runner,
    RunResult,
    TesseractEngine,
    check_vie_charset,
    fallback_tessdata_dirs,
    passed_tessdata_dir,
    read_bounded,
    resolve_tessdata_dir,
    run_bounded,
    vietnamese_letters,
)
from holocron_core.infrastructure.document_readers.tiers import assemble_text_layer_page
from holocron_core.infrastructure.llm_config import ConfigError
from holocron_core.services import capability_checks
from tests.pdf_samples import Page, text_lines, write_pdf

DOC_ID = "doc_00000000000000ff"
# Không dò thư mục mặc định của bản cài: kết quả không phụ thuộc máy chạy test. Ảnh nhỏ cho test nhanh.
CONFIG = PdfReaderConfig(ocr_dpi=36, ocr_tessdata_default_dirs=())
ALL_LETTERS = sorted(vietnamese_letters())
# Bản "cắt bớt chữ có dấu": bỏ mọi chữ mang dấu thanh, chỉ giữ ă â ê ô ơ ư đ (thường và hoa).
UNTONED = [letter for letter in ALL_LETTERS if len(unicodedata.normalize("NFD", letter)) == 1]
TCVN3 = ["HÖ ®iÒu hµnh cÊp ph¸t CPU cho c¸c tiÕn tr×nh theo l­ît vµ ®­îc ch¹y tèi ®a mét l­îng tö."] * 4
BODY = ["The scheduler gives each ready process the processor for one time quantum."] * 4
TSV = (
    "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
    "5\t1\t1\t1\t1\t1\t10\t10\t100\t20\t95.0\tHệ\n"
)
POSIX_ONLY = pytest.mark.skipif(sys.platform == "win32" or not hasattr(os, "mkfifo"), reason="FIFO chỉ có trên POSIX")


def _traineddata(letters: Sequence[str], count: int = 24) -> bytes:
    """File `.traineddata` tối thiểu tự tạo: bảng offset và unicharset của LSTM chứa `letters`."""
    entries = ["NULL 0 Common 0", *[f"{c} 3 0,255 Latin 5 0 5 {c}" for c in letters]]
    unicharset = (f"{len(entries)}\n" + "\n".join(entries) + "\n").encode("utf-8")
    offsets = [-1] * count
    offsets[TESSDATA_LSTM_UNICHARSET] = 4 + 8 * count
    return struct.pack(f"<i{count}q", count, *offsets) + unicharset


def _tessdata(root: Path, letters: Sequence[str]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / VIE_TRAINEDDATA).write_bytes(_traineddata(letters))
    return root


def _empty(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    return root


def _dir_named_vie(root: Path) -> Path:
    """Thư mục tessdata mà `vie.traineddata` lại là một thư mục (không phải file thường)."""
    (root / VIE_TRAINEDDATA).mkdir(parents=True)
    return root


def _fifo_named_vie(root: Path) -> Path:
    """Thư mục tessdata mà `vie.traineddata` là FIFO (POSIX): mở để đọc sẽ chặn nếu không kiểm loại file."""
    root.mkdir(parents=True, exist_ok=True)
    os.mkfifo(root / VIE_TRAINEDDATA)
    return root


def _settings(tessdata_dir: Path | None = None, command: str = "tesseract-test") -> OcrSettings:
    return OcrSettings(
        tesseract_cmd=command,
        tessdata_dir=None if tessdata_dir is None else str(tessdata_dir),
        secondary=None,
    )


def _defaults(*dirs: Path) -> PdfReaderConfig:
    """Cấu hình có đường lùi là `dirs` (thay thư mục mặc định của bản cài)."""
    return PdfReaderConfig(ocr_dpi=36, ocr_tessdata_default_dirs=tuple(str(directory) for directory in dirs))


# --------------------------------------------------------------------------- #
# Thư mục tessdata engine dùng (`resolve_tessdata_dir`)
# --------------------------------------------------------------------------- #


def test_default_dirs_are_the_approved_install_locations() -> None:
    assert TESSDATA_DEFAULT_DIRS == (
        "/usr/share/tesseract-ocr/5/tessdata",
        "/usr/share/tesseract-ocr/4.00/tessdata",
        "/usr/share/tessdata",
    )
    assert PdfReaderConfig().ocr_tessdata_default_dirs == TESSDATA_DEFAULT_DIRS


def test_passed_tessdata_dir_wins_over_the_list_langs_header_and_the_fallback(tmp_path: Path) -> None:
    """Tesseract 5.x CÓ `--tessdata-dir`: chỉ thư mục đó, kể cả khi nó không có file (không dò tiếp)."""
    full = _tessdata(tmp_path / "full", ALL_LETTERS)
    lacking = _tessdata(tmp_path / "lacking", UNTONED)
    empty = _empty(tmp_path / "empty")
    assert resolve_tessdata_dir(_settings(full), str(lacking), [str(lacking)]) == str(full)
    assert resolve_tessdata_dir(_settings(empty), str(full), [str(full)]) == str(empty)
    assert check_vie_charset(_settings(full), str(lacking), _defaults(lacking)).status == "ok"
    assert check_vie_charset(_settings(lacking), str(full), _defaults(full)).status == "missing_letters"
    assert check_vie_charset(_settings(empty), str(full), _defaults(full)).status == "missing_file"


def test_list_langs_header_is_used_when_no_tessdata_dir_is_passed(tmp_path: Path) -> None:
    """Tesseract 5.x KHÔNG truyền `--tessdata-dir`: thư mục ở dòng đầu `--list-langs`, không dò đường lùi."""
    full = _tessdata(tmp_path / "full", ALL_LETTERS)
    lacking = _tessdata(tmp_path / "lacking", UNTONED)
    empty = _empty(tmp_path / "empty")
    assert resolve_tessdata_dir(_settings(), f"{lacking}/", [str(full)]) == f"{lacking}/"
    assert check_vie_charset(_settings(), f"{full}/", _defaults(lacking)).status == "ok"
    assert check_vie_charset(_settings(), f"{lacking}/", _defaults(full)).status == "missing_letters"
    assert check_vie_charset(_settings(), str(empty), _defaults(full)).status == "missing_file"


def test_tesseract_4_falls_back_to_the_install_dirs_in_order(tmp_path: Path) -> None:
    """Tesseract 4.x (không truyền, `--list-langs` không ghi thư mục): đường lùi, thư mục đầu tiên có file thường."""
    full = _tessdata(tmp_path / "full", ALL_LETTERS)
    lacking = _tessdata(tmp_path / "lacking", UNTONED)
    empty = _empty(tmp_path / "empty")
    as_dir = _dir_named_vie(tmp_path / "as-dir")
    missing = tmp_path / "khong-co"
    assert resolve_tessdata_dir(_settings(), None, [str(lacking), str(full)]) == str(lacking)
    assert resolve_tessdata_dir(_settings(), None, [str(missing), str(empty), str(as_dir), str(full)]) == str(full)
    assert resolve_tessdata_dir(_settings(), None, [str(missing), str(empty), str(as_dir)]) is None
    assert check_vie_charset(_settings(), None, _defaults(empty, full, lacking)).status == "ok"
    assert check_vie_charset(_settings(), None, _defaults(as_dir, lacking, full)).status == "missing_letters"
    unknown = check_vie_charset(_settings(), None, _defaults(missing, empty, as_dir))
    assert unknown.status == "unknown_tessdata" and unknown.usable


def test_windows_also_looks_next_to_an_absolute_tesseract_binary(tmp_path: Path) -> None:
    binary = tmp_path / "Tesseract-OCR" / "tesseract.exe"
    settings = _settings(command=str(binary))
    assert fallback_tessdata_dirs(settings, ["/d1"], platform="win32") == ["/d1", str(binary.parent / "tessdata")]
    assert fallback_tessdata_dirs(settings, ["/d1"], platform="linux") == ["/d1"]
    # File chạy chỉ là tên (chưa tìm được trong PATH): không biết thư mục cài.
    assert fallback_tessdata_dirs(_settings(command="tesseract"), [], platform="win32") == []


def test_windows_install_dir_is_searched_last(tmp_path: Path) -> None:
    binary = tmp_path / "Tesseract-OCR" / "tesseract.exe"
    _tessdata(binary.parent / "tessdata", UNTONED)
    settings = _settings(command=str(binary))
    assert check_vie_charset(settings, None, CONFIG, platform="win32").status == "missing_letters"
    assert check_vie_charset(settings, None, CONFIG, platform="linux").status == "unknown_tessdata"
    full = _tessdata(tmp_path / "full", ALL_LETTERS)
    assert check_vie_charset(settings, None, _defaults(full), platform="win32").status == "ok"


def test_empty_entries_never_probe_the_working_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Giá trị rỗng không phải thư mục: không truyền `--tessdata-dir` rỗng, không dò `vie.traineddata` ở thư mục
    hiện hành (đường dẫn rỗng ghép với tên file)."""
    monkeypatch.chdir(_tessdata(tmp_path / "cwd", UNTONED))
    settings = OcrSettings(tesseract_cmd="tesseract-test", tessdata_dir="", secondary=None)
    assert passed_tessdata_dir(settings) is None
    assert fallback_tessdata_dirs(settings, ("",), platform="linux") == []
    assert resolve_tessdata_dir(settings, None, ("",), platform="linux") is None
    config = PdfReaderConfig(ocr_tessdata_default_dirs=("",))
    assert check_vie_charset(settings, None, config, platform="linux").status == "unknown_tessdata"
    calls: list[list[str]] = []
    outcomes = _outcomes(None)

    def run(args: Sequence[str], stdin_data: bytes, timeout_s: float, max_output_bytes: int) -> RunResult:
        calls.append(list(args))
        return outcomes[args[1]]

    assert TesseractEngine.probe(settings, 1.0, run) is not None
    assert calls and all("--tessdata-dir" not in args for args in calls)


def test_tessdata_prefix_is_not_part_of_the_settings() -> None:
    """Cũ → mới: `TESSDATA_PREFIX` (trước ghi vào `OcrSettings.tessdata_prefix`) bị bỏ khỏi thứ tự dò (duyệt
    2026-10-30): không đọc, không có field."""

    def which(name: str) -> str | None:
        return None

    assert load_ocr_settings({"TESSDATA_PREFIX": "/opt/td"}, which) == load_ocr_settings({}, which)
    with pytest.raises(ValidationError):
        OcrSettings.model_validate({"tesseract_cmd": "tesseract", "tessdata_prefix": "/opt/td"})


@pytest.mark.parametrize("tesseract_5", [True, False], ids=["tesseract-5", "tesseract-4"])
def test_tessdata_prefix_elsewhere_does_not_change_the_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tesseract_5: bool
) -> None:
    """`TESSDATA_PREFIX` trỏ tới bản đủ chữ không cứu được thư mục engine dùng thiếu chữ, và ngược lại. Cấu hình đọc
    từ môi trường thật của tiến trình (chỉ cố định các biến Holocron để không phụ thuộc máy chạy test)."""
    for name in ("HOLOCRON_TESSDATA_DIR", "HOLOCRON_TESSERACT_CMD", "HOLOCRON_PDF_OCR_PRIMARY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOLOCRON_PDF_OCR_SECONDARY", "")
    full = _tessdata(tmp_path / "full", ALL_LETTERS)
    lacking = _tessdata(tmp_path / "lacking", UNTONED)
    listed = str(lacking) if tesseract_5 else None
    config = CONFIG if tesseract_5 else _defaults(lacking)
    for prefix in (None, full):
        if prefix is not None:
            monkeypatch.setenv("TESSDATA_PREFIX", str(prefix))
        settings = load_ocr_settings(None, lambda name: None)
        assert check_vie_charset(settings, listed, config).status == "missing_letters"
        assert not capability_checks.probe_tesseract(settings, config, _runner(listed)).vie_available
        assert build_ocr(settings, config, _runner(listed)) is None
    monkeypatch.setenv("TESSDATA_PREFIX", str(lacking))
    settings = load_ocr_settings(None, lambda name: None)
    good_config = CONFIG if tesseract_5 else _defaults(full)
    good_listed = str(full) if tesseract_5 else None
    assert check_vie_charset(settings, good_listed, good_config).status == "ok"
    assert capability_checks.probe_tesseract(settings, good_config, _runner(good_listed)).vie_available
    assert build_ocr(settings, good_config, _runner(good_listed)) is not None


@pytest.mark.parametrize(
    ("letters", "status"),
    [
        (ALL_LETTERS, "ok"),
        ([letter for letter in ALL_LETTERS if letter not in OCR_CHARSET_APPROVED_MISSING], "ok"),
        (UNTONED, "missing_letters"),
    ],
    ids=["full", "approved-missing", "untoned"],
)
def test_check_vie_charset_statuses(tmp_path: Path, letters: list[str], status: str) -> None:
    check = check_vie_charset(_settings(_tessdata(tmp_path / "td", letters)), None, CONFIG)
    assert check.status == status
    assert check.usable == (status == "ok")
    assert check.missing == frozenset(vietnamese_letters()) - frozenset(letters)
    assert check.outside == check.missing - OCR_CHARSET_APPROVED_MISSING


def test_check_vie_charset_missing_file_unreadable_and_unknown(tmp_path: Path) -> None:
    """Cũ → mới (duyệt 2026-10-30): `--tessdata-dir` trỏ thư mục không có file trước là "unknown_tessdata" (dò tiếp),
    nay là "missing_file"; `vie.traineddata` là thư mục trước là "unreadable", nay là "missing_file"."""
    as_dir = check_vie_charset(_settings(_dir_named_vie(tmp_path / "as-dir")), None, CONFIG)
    assert as_dir.status == "missing_file" and not as_dir.usable
    absent = check_vie_charset(_settings(tmp_path / "khong-co"), None, CONFIG)
    assert absent.status == "missing_file" and not absent.usable
    full = _tessdata(tmp_path / "full", ALL_LETTERS)
    size = (full / VIE_TRAINEDDATA).stat().st_size
    capped = PdfReaderConfig(ocr_traineddata_max_bytes=size - 1, ocr_tessdata_default_dirs=())
    over_cap = check_vie_charset(_settings(full), None, capped)
    assert over_cap.status == "unreadable" and not over_cap.usable
    unknown = check_vie_charset(_settings(), None, CONFIG)
    assert unknown.status == "unknown_tessdata" and unknown.usable


def test_read_bounded_rejects_a_directory(tmp_path: Path) -> None:
    assert read_bounded(tmp_path, 1024) is None


@POSIX_ONLY
def test_fifo_traineddata_is_not_a_regular_file(tmp_path: Path) -> None:
    """FIFO tên `vie.traineddata`: thư mục đã xác định ⇒ "missing_file"; ở đường lùi ⇒ bỏ qua, dò tiếp; đọc trực tiếp
    không bao giờ chặn (mở không chặn, kiểm loại file trên descriptor)."""
    fifo_dir = _fifo_named_vie(tmp_path / "fifo")
    full = _tessdata(tmp_path / "full", ALL_LETTERS)
    assert check_vie_charset(_settings(fifo_dir), None, CONFIG).status == "missing_file"
    assert check_vie_charset(_settings(), str(fifo_dir), CONFIG).status == "missing_file"
    assert resolve_tessdata_dir(_settings(), None, [str(fifo_dir), str(full)]) == str(full)
    assert read_bounded(fifo_dir / VIE_TRAINEDDATA, 1024) is None


# --------------------------------------------------------------------------- #
# Worker: cùng hàm kiểm khi dựng engine
# --------------------------------------------------------------------------- #


def _outcomes(listing_dir: str | None, languages: Sequence[str] = ("eng", "vie")) -> dict[str, RunResult]:
    header = (
        f'List of available languages in "{listing_dir}" ({len(languages)}):'
        if listing_dir
        else f"List of available languages ({len(languages)}):"
    )
    listing = "".join(f"{language}\n" for language in languages)
    return {
        "--version": RunResult(status="ok", stdout=b"tesseract 5.4.0\n"),
        "--list-langs": RunResult(status="ok", stdout=f"{header}\n{listing}".encode()),
        "stdin": RunResult(status="ok", stdout=TSV.encode("utf-8")),
    }


def _runner(listing_dir: str | None, languages: Sequence[str] = ("eng", "vie")) -> Runner:
    """`Runner` giả cho `build_ocr(run=...)` và `probe_tesseract(run=...)`."""
    outcomes = _outcomes(listing_dir, languages)

    def run(args: Sequence[str], stdin_data: bytes, timeout_s: float, max_output_bytes: int) -> RunResult:
        return outcomes[args[1]]

    return run


def _fake_run_bounded(listing_dir: str | None, languages: Sequence[str]) -> Callable[..., RunResult]:
    """Thay `_pdf_worker.run_bounded` (worker gọi kèm `kill_grace_s`)."""
    outcomes = _outcomes(listing_dir, languages)

    def run(
        args: Sequence[str], stdin_data: bytes, timeout_s: float, max_output_bytes: int, *, kill_grace_s: float
    ) -> RunResult:
        return outcomes[args[1]]

    return run


def _read_in_process(
    tmp_path: Path,
    settings: OcrSettings,
    monkeypatch: pytest.MonkeyPatch,
    listing_dir: str | None = None,
    *,
    languages: Sequence[str] = ("eng", "vie"),
    config: PdfReaderConfig = CONFIG,
) -> tuple[bool, list[PageExtraction]]:
    """Worker chạy trong tiến trình test (giới hạn OS giả, Tesseract giả) trên PDF có một trang số và một trang cần
    OCR; tiến trình cha dựng `PageExtraction` như `WorkerPdfReader`. Trả về (có engine OCR không, các trang)."""
    path = write_pdf(tmp_path / "scan.pdf", [Page(texts=text_lines(BODY)), Page(texts=text_lines(TCVN3))])
    monkeypatch.setattr(_pdf_worker, "run_bounded", _fake_run_bounded(listing_dir, languages))
    request = WorkerRequest(path=str(path), page_budget=2, config=config, ocr=settings)
    emitted: list[BaseModel] = []

    def no_limits(mem_bytes: int, cpu_seconds: int) -> None:
        emitted.append(ReadyMessage())  # đánh dấu thứ tự: kiểm bộ chữ chạy trước khi áp giới hạn

    backend, ocr = _pdf_worker.start(
        request, load=PdfiumBackend.load, warm=lambda _backend: None, limit=no_limits, emit=emitted.append
    )
    _pdf_worker.run(request, backend, [PageRequest(page_index=0), PageRequest(page_index=1)], emitted.append, ocr)
    pages = [message.page for message in emitted if isinstance(message, PageMessage)]
    return ocr is not None, [assemble_text_layer_page(raw, DOC_ID, config) for raw in pages]


def test_worker_with_untoned_vie_marks_scan_pages_engine_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`vie.traineddata` bị cắt bớt chữ có dấu ⇒ worker coi như không có engine: trang cần OCR `failed`
    (`OCR_ENGINE_MISSING`), trang số vẫn đọc bình thường."""
    settings = _settings(_tessdata(tmp_path / "td", UNTONED))
    has_engine, pages = _read_in_process(tmp_path, settings, monkeypatch)
    assert not has_engine
    assert [(page.status, page.failure_reason) for page in pages] == [("ok", None), ("failed", "OCR_ENGINE_MISSING")]


def test_worker_reads_the_tessdata_dir_named_by_list_langs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Không đặt `HOLOCRON_TESSDATA_DIR`: worker dò thư mục ở dòng đầu `--list-langs`, như kiểm lúc khởi động."""
    lacking = _tessdata(tmp_path / "listed", UNTONED)
    has_engine, pages = _read_in_process(tmp_path, _settings(), monkeypatch, listing_dir=f"{lacking}/")
    assert not has_engine and pages[1].failure_reason == "OCR_ENGINE_MISSING"


@pytest.mark.parametrize(
    "letters",
    [ALL_LETTERS, [letter for letter in ALL_LETTERS if letter not in OCR_CHARSET_APPROVED_MISSING]],
    ids=["full", "approved-missing"],
)
def test_worker_with_a_complete_vie_ocrs_scan_pages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, letters: list[str]
) -> None:
    settings = _settings(_tessdata(tmp_path / "td", letters))
    has_engine, pages = _read_in_process(tmp_path, settings, monkeypatch)
    assert has_engine
    assert pages[1].tier_used == "ocr" and pages[1].failure_reason is None


def test_worker_skips_the_charset_check_when_tessdata_is_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ngoại lệ Tesseract 4.x (duyệt 2026-10-30): không truyền `--tessdata-dir`, `--list-langs` không ghi thư mục và
    đường lùi không có file ⇒ worker cũng bỏ qua kiểm.

    Cũ → mới: trước đây ca này dùng `HOLOCRON_TESSDATA_DIR` trỏ thư mục không tồn tại; nay thư mục đó là thư mục engine
    dùng nên là thiếu `vie` (`test_worker_with_a_passed_dir_without_the_file_has_no_engine`)."""
    has_engine, pages = _read_in_process(tmp_path, _settings(), monkeypatch, config=_defaults(tmp_path / "khong-co"))
    assert has_engine
    assert pages[1].tier_used == "ocr" and pages[1].status != "failed"


@pytest.mark.parametrize("passed", [True, False], ids=["tessdata-dir", "list-langs"])
def test_worker_with_a_passed_dir_without_the_file_has_no_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, passed: bool
) -> None:
    """Thư mục engine dùng (truyền `--tessdata-dir` hoặc `--list-langs` ghi) không có `vie.traineddata` ⇒ không dò
    tiếp sang đường lùi đủ chữ: trang cần OCR `failed` (`OCR_ENGINE_MISSING`)."""
    empty = _empty(tmp_path / "empty")
    full = _tessdata(tmp_path / "full", ALL_LETTERS)
    settings = _settings(empty) if passed else _settings()
    listing = None if passed else str(empty)
    has_engine, pages = _read_in_process(tmp_path, settings, monkeypatch, listing, config=_defaults(full))
    assert not has_engine
    assert [(page.status, page.failure_reason) for page in pages] == [("ok", None), ("failed", "OCR_ENGINE_MISSING")]


def test_worker_without_vie_in_list_langs_has_no_engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`--version` chạy được mà `--list-langs` chỉ có `eng` ⇒ `build_ocr` trả None, trang scan `OCR_ENGINE_MISSING`
    (Tesseract đã báo thiếu `vie` thì không rơi vào ngoại lệ "không xác định được tessdata")."""
    assert build_ocr(_settings(), CONFIG, _runner(None, ("eng",))) is None
    has_engine, pages = _read_in_process(tmp_path, _settings(), monkeypatch, languages=("eng",))
    assert not has_engine
    assert [(page.status, page.failure_reason) for page in pages] == [("ok", None), ("failed", "OCR_ENGINE_MISSING")]


def test_worker_rejects_a_listed_traineddata_over_the_byte_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`vie.traineddata` đủ chữ ở thư mục `--list-langs` nhưng vượt `ocr_traineddata_max_bytes` ⇒ không đọc được ⇒
    `build_ocr` trả None; đúng bằng trần thì dùng được."""
    full = _tessdata(tmp_path / "full", ALL_LETTERS)
    size = (full / VIE_TRAINEDDATA).stat().st_size
    capped = PdfReaderConfig(ocr_dpi=36, ocr_traineddata_max_bytes=size - 1, ocr_tessdata_default_dirs=())
    exact = PdfReaderConfig(ocr_dpi=36, ocr_traineddata_max_bytes=size, ocr_tessdata_default_dirs=())
    assert build_ocr(_settings(), capped, _runner(str(full))) is None
    assert build_ocr(_settings(), exact, _runner(str(full))) is not None
    has_engine, pages = _read_in_process(tmp_path, _settings(), monkeypatch, str(full), config=capped)
    assert not has_engine and pages[1].failure_reason == "OCR_ENGINE_MISSING"


def _same_config_cases(root: Path) -> list[tuple[str, OcrSettings, str | None, PdfReaderConfig, bool]]:
    """(tên, cấu hình OCR, thư mục `--list-langs`, cấu hình bộ đọc, dùng được không) cho cả ba nhánh xác định."""
    full = _tessdata(root / "full", ALL_LETTERS)
    lacking = _tessdata(root / "lacking", UNTONED)
    empty = _empty(root / "empty")
    as_dir = _dir_named_vie(root / "as-dir")
    return [
        ("passed-full", _settings(full), str(lacking), _defaults(lacking), True),
        ("passed-lacking", _settings(lacking), str(full), _defaults(full), False),
        ("passed-empty", _settings(empty), str(full), _defaults(full), False),
        ("passed-as-dir", _settings(as_dir), str(full), _defaults(full), False),
        ("listed-full", _settings(), str(full), _defaults(lacking), True),
        ("listed-lacking", _settings(), str(lacking), _defaults(full), False),
        ("listed-empty", _settings(), str(empty), _defaults(full), False),
        ("fallback-full", _settings(), None, _defaults(empty, as_dir, full, lacking), True),
        ("fallback-lacking", _settings(), None, _defaults(lacking, full), False),
        ("fallback-unknown", _settings(), None, _defaults(empty, as_dir), True),
    ]


def test_worker_and_startup_give_the_same_verdict_on_the_same_config(tmp_path: Path) -> None:
    """Cùng cấu hình (thư mục truyền, `--list-langs`, đường lùi) ⇒ kiểm lúc khởi động và worker cho cùng kết quả."""
    for name, settings, listed, config, usable in _same_config_cases(tmp_path):
        startup = capability_checks.probe_tesseract(settings, config, _runner(listed)).vie_available
        worker = build_ocr(settings, config, _runner(listed)) is not None
        assert (startup, worker) == (usable, usable), name


def test_worker_and_startup_check_share_one_charset_function(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`build_ocr` gọi đúng `tesseract.check_vie_charset` (không bản sao riêng), với thư mục `--list-langs` ghi."""
    assert _pdf_worker.check_vie_charset is check_vie_charset
    assert capability_checks.check_vie_charset is check_vie_charset
    calls: list[tuple[OcrSettings, str | None]] = []

    def spy(settings: OcrSettings, listed_dir: str | None, config: PdfReaderConfig) -> CharsetCheck:
        calls.append((settings, listed_dir))
        return CharsetCheck(status="missing_letters", missing=frozenset({"ơ"}), outside=frozenset({"ơ"}))

    monkeypatch.setattr(_pdf_worker, "check_vie_charset", spy)
    settings = _settings(_tessdata(tmp_path / "td", ALL_LETTERS))
    assert build_ocr(settings, CONFIG, _runner("/listed/")) is None
    assert calls == [(settings, "/listed/")]


# --------------------------------------------------------------------------- #
# Tích hợp: worker thật (tiến trình con) với binary Tesseract thật (tự skip khi máy không có)
# --------------------------------------------------------------------------- #


def _real_tesseract() -> OcrSettings | None:
    try:
        settings = load_ocr_settings()
    except ConfigError:
        return None
    settings = settings.model_copy(update={"secondary": None})  # không chạy RapidOCR thật trong pytest
    return settings if TesseractEngine.probe(settings, 10.0) is not None else None


def test_real_worker_with_untoned_vie_marks_scan_pages_engine_missing(tmp_path: Path) -> None:
    """Tesseract thật liệt kê `vie` theo tên file, nên chỉ kiểm bộ chữ của worker mới chặn được file bị cắt."""
    real = _real_tesseract()
    if real is None:
        pytest.skip("máy không có Tesseract với gói `vie`")
    settings = real.model_copy(update={"tessdata_dir": str(_tessdata(tmp_path / "td", UNTONED))})
    path = write_pdf(tmp_path / "mixed.pdf", [Page(texts=text_lines(BODY)), Page(texts=text_lines(TCVN3))])
    pages = list(WorkerPdfReader(PdfReaderConfig(), ocr=settings).read_pages(path, DOC_ID))
    assert [(page.status, page.failure_reason) for page in pages] == [("ok", None), ("failed", "OCR_ENGINE_MISSING")]


# --------------------------------------------------------------------------- #
# Opt-in: `vie.traineddata` chính thức của tessdata, tessdata_best, tessdata_fast
# --------------------------------------------------------------------------- #


def _official_dir(variant: str) -> Path | None:
    raw = os.environ.get(f"HOLOCRON_TEST_TESSDATA_{variant}", "").strip()
    if not raw or not (Path(raw) / VIE_TRAINEDDATA).is_file():
        return None
    return Path(raw)


@pytest.mark.parametrize(
    "variant",
    [
        pytest.param(
            variant,
            marks=pytest.mark.skipif(
                _official_dir(variant) is None,
                reason=f"chưa đặt HOLOCRON_TEST_TESSDATA_{variant} (thư mục chứa vie.traineddata chính thức)",
            ),
        )
        for variant in ("STD", "BEST", "FAST")
    ],
)
def test_official_vie_traineddata_passes_the_charset_check(variant: str) -> None:
    """Cả ba bản chính thức PHẢI qua kiểm: chữ thiếu (đã đo: `Ỵ`, `ỵ`) nằm trong danh sách được phép thiếu."""
    directory = _official_dir(variant)
    assert directory is not None
    settings = _settings(directory)
    check = check_vie_charset(settings, None, PdfReaderConfig())
    assert check.status == "ok", sorted(check.outside)
    assert check.missing <= OCR_CHARSET_APPROVED_MISSING
    assert build_ocr(settings, PdfReaderConfig(), _runner(None)) is not None


# --------------------------------------------------------------------------- #
# Đường dẫn tương đối trong cấu hình (duyệt 2026-10-30): khởi động và worker phải thấy cùng một đường dẫn
# --------------------------------------------------------------------------- #


def _no_which(name: str) -> str | None:
    return None


def test_relative_paths_become_absolute_and_command_names_stay_bare(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    no_which = _no_which
    settings = load_ocr_settings({ENV_TESSDATA_DIR: "td", ENV_TESSERACT_CMD: "./bin/tesseract"}, no_which)
    assert settings.tessdata_dir == os.path.abspath("td")
    assert settings.tesseract_cmd == os.path.abspath("./bin/tesseract")
    assert load_ocr_settings({ENV_TESSERACT_CMD: "tesseract"}, no_which).tesseract_cmd == "tesseract"
    absolute = str(tmp_path / "abs")
    kept = load_ocr_settings({ENV_TESSDATA_DIR: absolute, ENV_TESSERACT_CMD: absolute + "/tesseract"}, no_which)
    assert (kept.tessdata_dir, kept.tesseract_cmd) == (absolute, absolute + "/tesseract")


def test_relative_config_gives_startup_and_worker_the_same_verdict_from_another_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Cấu hình đọc ở cwd của app; sau đó cwd đổi (worker chạy ở thư mục tạm) mà hai bên vẫn cùng kết quả."""
    app_cwd = tmp_path / "app"
    _tessdata(app_cwd / "td", ALL_LETTERS)
    monkeypatch.chdir(app_cwd)
    settings = load_ocr_settings({ENV_TESSDATA_DIR: "td", ENV_TESSERACT_CMD: "./bin/tesseract"}, _no_which)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    seen: list[list[str]] = []
    outcomes = _outcomes(None)

    def run(args: Sequence[str], stdin_data: bytes, timeout_s: float, max_output_bytes: int) -> RunResult:
        seen.append(list(args))
        return outcomes[args[1]]

    with caplog.at_level(logging.DEBUG):
        startup = capability_checks.probe_tesseract(settings, CONFIG, run).vie_available
        worker = build_ocr(settings, CONFIG, run) is not None
    assert (startup, worker) == (True, True)
    assert all(args[0] == settings.tesseract_cmd for args in seen)
    assert all(str(app_cwd / "td") == args[-1] for args in seen if "--tessdata-dir" in args)
    assert str(tmp_path) not in caplog.text


# --------------------------------------------------------------------------- #
# Giới hạn byte và loại file của `tesseract` (đột biến đã sống ở vòng 5)
# --------------------------------------------------------------------------- #


def _writer(size: int) -> list[str]:
    return [sys.executable, "-c", f"import sys; sys.stdout.buffer.write(b'x' * {size})"]


def test_run_bounded_output_limit_is_inclusive() -> None:
    """Đúng N byte với `max_output_bytes=N` ⇒ ok đủ N byte; N+1 byte ⇒ overflow."""
    size = 4096
    exact = run_bounded(_writer(size), b"", 30.0, size)
    assert exact.status == "ok" and len(exact.stdout) == size
    assert run_bounded(_writer(size + 1), b"", 30.0, size).status == "overflow"


def test_read_bounded_rejects_a_device_file() -> None:
    """File thiết bị (`NUL` trên Windows, `/dev/null` trên POSIX) mở được nhưng không phải file thường ⇒ None."""
    assert read_bounded(Path(os.devnull), 1024) is None


@POSIX_ONLY
def test_read_bounded_never_blocks_on_a_fifo(tmp_path: Path) -> None:
    """Chạy trong tiến trình con có hạn ngắn: thiếu `O_NONBLOCK` thì `open` chặn mãi và test báo fail rõ thay vì treo."""
    fifo = _fifo_named_vie(tmp_path / "fifo") / VIE_TRAINEDDATA
    source_root = Path(read_bounded.__code__.co_filename).parents[3]
    code = (
        "import sys; from pathlib import Path; "
        "from holocron_core.infrastructure.document_readers.tesseract import read_bounded; "
        "print(read_bounded(Path(sys.argv[1]), 1024))"
    )
    try:
        done = subprocess.run(
            [sys.executable, "-c", code, str(fifo)],
            capture_output=True,
            timeout=15,
            env={**os.environ, "PYTHONPATH": str(source_root)},
            check=False,
        )
    except subprocess.TimeoutExpired:
        pytest.fail("read_bounded bị chặn trên FIFO (thiếu O_NONBLOCK)")
    assert done.stdout.strip() == b"None"


# --------------------------------------------------------------------------- #
# `run_bounded(cwd=...)`: tiến trình con thật chạy trong đúng thư mục được truyền
# --------------------------------------------------------------------------- #


def _cwd_printer_env() -> dict[str, str]:
    env: dict[str, str] = {"PYTHONIOENCODING": "utf-8"}
    if sys.platform == "win32":
        env["SYSTEMROOT"] = os.environ.get("SYSTEMROOT", r"C:\Windows")
    return env


CWD_PRINTER = [sys.executable, "-c", "import os; print(os.getcwd())"]


def test_run_bounded_runs_the_child_in_the_given_cwd(tmp_path: Path) -> None:
    """Tiến trình con in `os.getcwd()`: phải là đúng thư mục truyền vào (bỏ `cwd=` khỏi `Popen` ⇒ test đỏ)."""
    workdir = tmp_path / "probe-cwd"
    workdir.mkdir()
    assert not os.path.samefile(workdir, os.getcwd())
    result = run_bounded(CWD_PRINTER, b"", 30.0, 4096, env=_cwd_printer_env(), cwd=str(workdir))
    assert result.status == "ok"
    printed = result.stdout.decode("utf-8").strip()
    assert os.path.samefile(printed, workdir)
    assert os.path.realpath(printed) == os.path.realpath(workdir)


def test_run_bounded_with_a_missing_cwd_fails(tmp_path: Path) -> None:
    """`cwd` không tồn tại ⇒ không dựng được tiến trình con ⇒ "failed" (không ném lỗi ra nơi gọi)."""
    missing = tmp_path / "khong-ton-tai"
    result = run_bounded(CWD_PRINTER, b"", 30.0, 4096, env=_cwd_printer_env(), cwd=str(missing))
    assert result.status == "failed" and result.stdout == b""


# --------------------------------------------------------------------------- #
# Đột biến còn sống ở vòng 6: dạng đường dẫn đặc biệt của cấu hình, tên file có byte NUL
# --------------------------------------------------------------------------- #


def test_drive_relative_tessdata_dir_becomes_absolute(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`C:td` (Windows: tương đối theo ổ C; POSIX: tên thư mục thường) KHÔNG phải dạng có ổ đã tuyệt đối ⇒ abspath."""
    monkeypatch.chdir(tmp_path)
    assert load_ocr_settings({ENV_TESSDATA_DIR: "C:td"}, _no_which).tessdata_dir == os.path.abspath("C:td")


@pytest.mark.skipif(sys.platform != "win32", reason="gạch ngược chỉ là dấu phân cách trên Windows")
def test_backslash_rooted_tessdata_dir_is_kept_as_is(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Windows — hành vi hiện tại (đổi sang tính từ gốc ổ đã hoãn): dạng bắt đầu bằng gạch ngược coi như đã có gốc,
    giữ nguyên."""
    monkeypatch.chdir(tmp_path)
    rooted = r"\opt\tessdata"
    assert load_ocr_settings({ENV_TESSDATA_DIR: rooted}, _no_which).tessdata_dir == rooted


@pytest.mark.skipif(sys.platform == "win32", reason="trên Windows gạch ngược là dấu phân cách")
def test_backslash_tessdata_dir_is_relative_on_posix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """POSIX: `\\` không phải dấu phân cách nên `\\opt\\tessdata` là tên tương đối ⇒ đổi sang tuyệt đối theo cwd."""
    monkeypatch.chdir(tmp_path)
    relative = r"\opt\tessdata"
    expected = os.path.abspath(relative)
    assert load_ocr_settings({ENV_TESSDATA_DIR: relative}, _no_which).tessdata_dir == expected
    assert expected != relative


@pytest.mark.parametrize(
    ("value", "sep", "altsep", "rooted"),
    [
        (r"\opt\tessdata", "\\", "/", True),  # Windows: gạch ngược là `sep`
        (r"\opt\tessdata", "/", "\\", True),  # gạch ngược là `altsep`
        (r"\opt\tessdata", "/", None, False),  # POSIX: tên tương đối
        ("/opt/tessdata", "/", None, True),
        ("/opt/tessdata", "\\", "/", True),
        ("C:/Tesseract/tesseract.exe", "/", None, True),
        (r"C:\Tesseract\tesseract.exe", "/", None, True),
        (r"C:\Tesseract\tesseract.exe", "\\", "/", True),
        ("C:td", "\\", "/", False),
        (r"opt\tessdata", "\\", "/", False),
        ("opt/tessdata", "/", None, False),
        ("", "\\", "/", False),
    ],
)
def test_has_root_depends_on_the_platform_separators_only_for_backslash(
    value: str, sep: str, altsep: str | None, rooted: bool
) -> None:
    """Hàm thuần `_has_root`: `/x` và dạng có ổ đĩa có gốc trên mọi nền tảng; `\\x` chỉ khi gạch ngược là dấu phân
    cách (`sep` hoặc `altsep`)."""
    assert _has_root(value, sep=sep, altsep=altsep) is rooted


def test_command_with_only_backslash_separators_becomes_absolute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lệnh chỉ có dấu gạch ngược (dạng Windows) vẫn là đường dẫn tương đối, không phải tên lệnh trần ⇒ abspath."""
    monkeypatch.chdir(tmp_path)
    command = r"bin\tesseract.exe"
    assert load_ocr_settings({ENV_TESSERACT_CMD: command}, _no_which).tesseract_cmd == os.path.abspath(command)


def test_read_bounded_returns_none_for_a_path_with_a_nul_byte(tmp_path: Path) -> None:
    """`os.open` ném `ValueError` (không phải `OSError`) với byte NUL trong đường dẫn ⇒ None, không ném lỗi."""
    assert read_bounded(tmp_path / "a\x00b", 10) is None


def test_drive_letter_in_the_middle_of_a_relative_path_is_not_a_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ổ đĩa chỉ có nghĩa ở ĐẦU chuỗi: `sub/C:/td` vẫn là đường dẫn tương đối trên mọi nền tảng."""
    assert _has_root("sub/C:/td", sep="/", altsep=None) is False
    assert _has_root("sub/C:/td", sep="\\", altsep="/") is False
    assert _has_root("C:/td", sep="/", altsep=None) is True
    monkeypatch.chdir(tmp_path)
    settings = load_ocr_settings({ENV_TESSDATA_DIR: "sub/C:/td"}, _no_which)
    assert settings.tessdata_dir == os.path.abspath("sub/C:/td")
