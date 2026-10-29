"""Kiểm bộ chữ của `vie.traineddata` dùng chung giữa kiểm lúc khởi động và worker đọc PDF (duyệt 2026-10-30).

- Dò `vie.traineddata` theo thứ tự `HOLOCRON_TESSDATA_DIR` → `TESSDATA_PREFIX` → thư mục ở dòng đầu `--list-langs`
  → thư mục mặc định của bản cài (rồi `tessdata` cạnh file chạy trên Windows); không thấy ở đâu ⇒ bỏ qua kiểm.
- Worker (`_pdf_worker.build_ocr`) gọi CÙNG hàm `tesseract.check_vie_charset`: thiếu chữ ngoài danh sách cho phép ⇒
  không có engine, trang cần OCR thành `failed` (`OCR_ENGINE_MISSING`, ADR-008 mục 14).
- Test opt-in với `vie.traineddata` CHÍNH THỨC (tessdata, tessdata_best, tessdata_fast): đặt
  `HOLOCRON_TEST_TESSDATA_STD`, `HOLOCRON_TEST_TESSDATA_BEST`, `HOLOCRON_TEST_TESSDATA_FAST` trỏ tới thư mục chứa
  `vie.traineddata` của từng bản; không đặt thì test tự bỏ qua. Không commit file `.traineddata` thật.

Fixture `.traineddata` ở đây là file tự tạo tối thiểu (chỉ có unicharset của LSTM), không chép từ bản chính thức.
"""

from __future__ import annotations

import os
import struct
import unicodedata
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest
from pydantic import BaseModel

from holocron_core.domain.documents import PageExtraction
from holocron_core.infrastructure.document_readers import _pdf_worker
from holocron_core.infrastructure.document_readers._pdf_worker import build_ocr
from holocron_core.infrastructure.document_readers.config import (
    ENV_TESSDATA_PREFIX,
    OCR_CHARSET_APPROVED_MISSING,
    TESSDATA_DEFAULT_DIRS,
    OcrSettings,
    PdfReaderConfig,
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
    find_vie_traineddata,
    tessdata_candidates,
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


def _settings(
    tessdata_dir: Path | None = None, prefix: Path | None = None, command: str = "tesseract-test"
) -> OcrSettings:
    return OcrSettings(
        tesseract_cmd=command,
        tessdata_dir=None if tessdata_dir is None else str(tessdata_dir),
        tessdata_prefix=None if prefix is None else str(prefix),
        secondary=None,
    )


# --------------------------------------------------------------------------- #
# Thứ tự dò `vie.traineddata`
# --------------------------------------------------------------------------- #


def test_default_dirs_are_the_approved_install_locations() -> None:
    assert TESSDATA_DEFAULT_DIRS == (
        "/usr/share/tesseract-ocr/5/tessdata",
        "/usr/share/tesseract-ocr/4.00/tessdata",
        "/usr/share/tessdata",
    )
    assert PdfReaderConfig().ocr_tessdata_default_dirs == TESSDATA_DEFAULT_DIRS


def test_candidates_follow_the_approved_order(tmp_path: Path) -> None:
    settings = _settings(tmp_path / "holocron", tmp_path / "prefix")
    listed = str(tmp_path / "listed")
    assert tessdata_candidates(settings, listed, ["/d1", "/d2"], platform="linux") == [
        str(tmp_path / "holocron"),
        str(tmp_path / "prefix"),
        listed,
        "/d1",
        "/d2",
    ]
    assert tessdata_candidates(_settings(), None, [], platform="linux") == []


def test_windows_also_looks_next_to_an_absolute_tesseract_binary(tmp_path: Path) -> None:
    binary = tmp_path / "Tesseract-OCR" / "tesseract.exe"
    settings = _settings(command=str(binary))
    assert tessdata_candidates(settings, None, [], platform="win32") == [str(binary.parent / "tessdata")]
    assert tessdata_candidates(settings, None, [], platform="linux") == []
    # File chạy chỉ là tên (chưa tìm được trong PATH): không biết thư mục cài.
    assert tessdata_candidates(_settings(command="tesseract"), None, [], platform="win32") == []


def test_first_directory_holding_the_file_wins(tmp_path: Path) -> None:
    full = _tessdata(tmp_path / "full", ALL_LETTERS)
    lacking = _tessdata(tmp_path / "lacking", UNTONED)
    empty = tmp_path / "empty"
    empty.mkdir()
    # HOLOCRON_TESSDATA_DIR → TESSDATA_PREFIX → dòng đầu `--list-langs` → thư mục mặc định.
    assert find_vie_traineddata(_settings(full, lacking), str(lacking), [str(lacking)]) == full / VIE_TRAINEDDATA
    assert find_vie_traineddata(_settings(empty, lacking), str(full), [str(full)]) == lacking / VIE_TRAINEDDATA
    assert find_vie_traineddata(_settings(empty, empty), str(lacking), [str(full)]) == lacking / VIE_TRAINEDDATA
    assert find_vie_traineddata(_settings(empty, empty), str(empty), [str(empty), str(full)]) == full / VIE_TRAINEDDATA
    assert find_vie_traineddata(_settings(empty, empty), None, [str(tmp_path / "khong-co")]) is None


def test_windows_install_dir_is_searched_last(tmp_path: Path) -> None:
    binary = tmp_path / "Tesseract-OCR" / "tesseract.exe"
    _tessdata(binary.parent / "tessdata", UNTONED)
    settings = _settings(command=str(binary))
    assert check_vie_charset(settings, None, CONFIG, platform="win32").status == "missing_letters"
    assert check_vie_charset(settings, None, CONFIG, platform="linux").status == "unknown_tessdata"


def test_tessdata_prefix_is_read_from_the_environment() -> None:
    def which(name: str) -> str | None:
        return None

    assert load_ocr_settings({ENV_TESSDATA_PREFIX: " /opt/td "}, which).tessdata_prefix == "/opt/td"
    assert load_ocr_settings({ENV_TESSDATA_PREFIX: "  "}, which).tessdata_prefix is None
    assert load_ocr_settings({}, which).tessdata_prefix is None


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


def test_check_vie_charset_unreadable_and_unknown(tmp_path: Path) -> None:
    as_dir = tmp_path / "as-dir"
    (as_dir / VIE_TRAINEDDATA).mkdir(parents=True)
    unreadable = check_vie_charset(_settings(as_dir), None, CONFIG)
    assert unreadable.status == "unreadable" and not unreadable.usable
    full = _tessdata(tmp_path / "full", ALL_LETTERS)
    size = (full / VIE_TRAINEDDATA).stat().st_size
    capped = PdfReaderConfig(ocr_traineddata_max_bytes=size - 1, ocr_tessdata_default_dirs=())
    assert check_vie_charset(_settings(full), None, capped).status == "unreadable"
    unknown = check_vie_charset(_settings(tmp_path / "khong-co"), None, CONFIG)
    assert unknown.status == "unknown_tessdata" and unknown.usable


# --------------------------------------------------------------------------- #
# Worker: cùng hàm kiểm khi dựng engine
# --------------------------------------------------------------------------- #


def _outcomes(listing_dir: str | None) -> dict[str, RunResult]:
    header = (
        f'List of available languages in "{listing_dir}" (2):' if listing_dir else "List of available languages (2):"
    )
    return {
        "--version": RunResult(status="ok", stdout=b"tesseract 5.4.0\n"),
        "--list-langs": RunResult(status="ok", stdout=f"{header}\neng\nvie\n".encode()),
        "stdin": RunResult(status="ok", stdout=TSV.encode("utf-8")),
    }


def _runner(listing_dir: str | None) -> Runner:
    """`Runner` giả cho `build_ocr(run=...)`."""
    outcomes = _outcomes(listing_dir)

    def run(args: Sequence[str], stdin_data: bytes, timeout_s: float, max_output_bytes: int) -> RunResult:
        return outcomes[args[1]]

    return run


def _fake_run_bounded(listing_dir: str | None) -> Callable[..., RunResult]:
    """Thay `_pdf_worker.run_bounded` (worker gọi kèm `kill_grace_s`)."""
    outcomes = _outcomes(listing_dir)

    def run(
        args: Sequence[str], stdin_data: bytes, timeout_s: float, max_output_bytes: int, *, kill_grace_s: float
    ) -> RunResult:
        return outcomes[args[1]]

    return run


def _read_in_process(
    tmp_path: Path, settings: OcrSettings, monkeypatch: pytest.MonkeyPatch, listing_dir: str | None = None
) -> tuple[bool, list[PageExtraction]]:
    """Worker chạy trong tiến trình test (giới hạn OS giả, Tesseract giả) trên PDF có một trang số và một trang cần
    OCR; tiến trình cha dựng `PageExtraction` như `WorkerPdfReader`. Trả về (có engine OCR không, các trang)."""
    path = write_pdf(tmp_path / "scan.pdf", [Page(texts=text_lines(BODY)), Page(texts=text_lines(TCVN3))])
    monkeypatch.setattr(_pdf_worker, "run_bounded", _fake_run_bounded(listing_dir))
    request = WorkerRequest(path=str(path), page_budget=2, config=CONFIG, ocr=settings)
    emitted: list[BaseModel] = []

    def no_limits(mem_bytes: int, cpu_seconds: int) -> None:
        emitted.append(ReadyMessage())  # đánh dấu thứ tự: kiểm bộ chữ chạy trước khi áp giới hạn

    backend, ocr = _pdf_worker.start(
        request, load=PdfiumBackend.load, warm=lambda _backend: None, limit=no_limits, emit=emitted.append
    )
    _pdf_worker.run(request, backend, [PageRequest(page_index=0), PageRequest(page_index=1)], emitted.append, ocr)
    pages = [message.page for message in emitted if isinstance(message, PageMessage)]
    return ocr is not None, [assemble_text_layer_page(raw, DOC_ID, CONFIG) for raw in pages]


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
    """Ngoại lệ Tesseract 4.x (duyệt 2026-10-30): không tìm thấy `vie.traineddata` ⇒ worker cũng bỏ qua kiểm."""
    has_engine, pages = _read_in_process(tmp_path, _settings(tmp_path / "khong-co"), monkeypatch)
    assert has_engine
    assert pages[1].tier_used == "ocr" and pages[1].status != "failed"


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
