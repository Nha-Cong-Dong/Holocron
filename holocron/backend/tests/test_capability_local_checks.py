"""Kiểm cục bộ của chế độ khả năng (SPECIFICATION §5.12, §11.4): Tesseract, gói `vie`, lượt đọc thứ hai.

Không chạy binary Tesseract hay nạp RapidOCR thật: probe, bộ chạy lệnh và bộ thăm dò lượt thứ hai đều là bản giả,
nên kết quả không phụ thuộc máy chạy test. Riêng các test tiến trình con thăm dò RapidOCR dựng tiến trình thật với
gói `rapidocr` GIẢ (đặt trước trong `PYTHONPATH`), không nạp model thật.
"""

from __future__ import annotations

import functools
import io
import logging
import os
import struct
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from typing import get_args

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from holocron_core.domain.capabilities import Capability, CapabilityName, CapabilityReason
from holocron_core.infrastructure.document_readers import rapidocr_adapter, secondary_probe
from holocron_core.infrastructure.document_readers.config import (
    ENV_PDF_OCR_PRIMARY,
    ENV_PDF_OCR_SECONDARY,
    OCR_CHARSET_APPROVED_MISSING,
    OcrSettings,
    PdfReaderConfig,
    load_ocr_settings,
)
from holocron_core.infrastructure.document_readers.rapidocr_adapter import RapidOcrBackend
from holocron_core.infrastructure.document_readers.secondary_probe import (
    REPORT_PREFIX,
    SecondaryProbeReport,
    SecondaryProbeRequest,
    parse_report,
    probe_env,
    probe_secondary_ocr,
    run_probe,
)
from holocron_core.infrastructure.document_readers.tesseract import (
    TESSDATA_LSTM_UNICHARSET,
    ProbeOutcome,
    RunResult,
    RunStatus,
    TesseractEngine,
    listed_tessdata_dir,
    read_bounded,
    vietnamese_letters,
)
from holocron_core.infrastructure.document_readers.tesseract import run_bounded as real_run_bounded
from holocron_core.infrastructure.llm_config import ConfigError, RuntimeConfig, load_runtime_config
from holocron_core.routers import blueprints, runtime
from holocron_core.routers.api import app
from holocron_core.routers.deps import get_runtime_config
from holocron_core.routers.documents import RUNTIME_STATE_ATTR, create_import_runtime
from holocron_core.routers.runtime import (
    LOCAL_CHECK_STATE_ATTR,
    LOCAL_CHECK_THREAD_NAME,
    LOCAL_FAILURES_STATE_ATTR,
    LocalCapabilityCheck,
    LocalCheckLauncher,
    LocalCheckState,
    get_local_capability_check,
    get_local_check_launcher,
)
from holocron_core.services import capability_checks
from holocron_core.services.capabilities import IMPLEMENTED_CAPABILITIES, REASON_DETAILS_VI
from holocron_core.services.capability_checks import (
    OcrProbeResult,
    check_local_capabilities,
    local_capability_failures,
    probe_tesseract,
    secondary_ocr_loads,
)

WITH_SECONDARY = OcrSettings(tesseract_cmd="/opt/tesseract", tessdata_dir="/opt/tessdata", secondary="rapidocr")
WITHOUT_SECONDARY = OcrSettings(tesseract_cmd="/opt/tesseract", tessdata_dir=None, secondary=None)
READY = OcrProbeResult(engine_runs=True, vie_available=True)
NO_BINARY = OcrProbeResult(engine_runs=False, vie_available=False)
NO_VIE = OcrProbeResult(engine_runs=True, vie_available=False)
# Không dò thư mục tessdata mặc định của bản cài: kết quả không phụ thuộc máy chạy test có Tesseract hay không.
CONFIG = PdfReaderConfig(ocr_tessdata_default_dirs=())
LLM_ENV = {
    "HOLOCRON_LLM_BASE_URL": "https://gateway.example.test/v1",
    "HOLOCRON_LLM_API_KEY": "sk-test-0123456789-must-never-leak",
    "HOLOCRON_LLM_MODEL_EXTRACT": "secret-model-name",
}

Failures = dict[CapabilityName, CapabilityReason]


LOADED = SecondaryProbeReport(loaded=True, error=None)


def _loaded(config: PdfReaderConfig, /) -> SecondaryProbeReport:
    return LOADED


def _not_loaded(config: PdfReaderConfig, /) -> SecondaryProbeReport:
    return SecondaryProbeReport(loaded=False, error="ModuleNotFoundError")


def _must_not_load(config: PdfReaderConfig, /) -> SecondaryProbeReport:
    raise AssertionError("không được nạp lượt thứ hai")


def _probe_returning(result: OcrProbeResult) -> Callable[[OcrSettings, PdfReaderConfig], OcrProbeResult]:
    def probe(settings: OcrSettings, config: PdfReaderConfig, /) -> OcrProbeResult:
        return result

    return probe


class CountingCheck:
    """Bộ kiểm cục bộ đếm số lần chạy; mỗi lần chạy gọi `check_local_capabilities` với cấu hình và probe giả."""

    def __init__(self, settings: OcrSettings, probe_result: OcrProbeResult) -> None:
        self.settings = settings
        self.probe_result = probe_result
        self.calls = 0
        self.configs: list[PdfReaderConfig] = []

    def _probe(self, settings: OcrSettings, config: PdfReaderConfig, /) -> OcrProbeResult:
        assert settings is self.settings
        return self.probe_result

    def __call__(self, config: PdfReaderConfig, /) -> Failures:
        self.calls += 1
        self.configs.append(config)
        return check_local_capabilities(lambda: self.settings, self._probe, _loaded, config)


@pytest.fixture
def install_check() -> Iterator[Callable[[LocalCapabilityCheck], None]]:
    """Đặt bộ kiểm cục bộ riêng cho `app` (thay bộ giả mặc định của `conftest.py`); gỡ khi test xong."""
    previous = app.dependency_overrides[get_local_capability_check]

    def install(check: LocalCapabilityCheck) -> None:
        app.dependency_overrides[get_local_capability_check] = lambda: check

    yield install
    app.dependency_overrides[get_local_capability_check] = previous
    app.dependency_overrides.pop(get_runtime_config, None)


def _ocr_pair(body: dict[str, object]) -> dict[str, tuple[object, object]]:
    capabilities = body["capabilities"]
    assert isinstance(capabilities, list)
    return {
        item["name"]: (item["available"], item["reason"])
        for item in capabilities
        if item["name"] in ("ocr", "ocr_cross_check")
    }


# --------------------------------------------------------------------------- #
# Hàm thuần: kết quả probe + cấu hình → lý do
# --------------------------------------------------------------------------- #


def test_ocr_is_now_implemented() -> None:
    assert {"ocr", "ocr_cross_check"} <= IMPLEMENTED_CAPABILITIES


@pytest.mark.parametrize(
    ("probe_result", "settings", "expected"),
    [
        (READY, WITH_SECONDARY, {}),
        (NO_BINARY, WITH_SECONDARY, {"ocr": "ocr_engine_missing", "ocr_cross_check": "ocr_engine_missing"}),
        (NO_VIE, WITH_SECONDARY, {"ocr": "ocr_language_missing", "ocr_cross_check": "ocr_language_missing"}),
        (READY, WITHOUT_SECONDARY, {"ocr_cross_check": "secondary_ocr_disabled"}),
        # OCR chính hỏng thì lượt thứ hai mang lý do của OCR chính, kể cả khi lượt thứ hai đang tắt.
        (NO_BINARY, WITHOUT_SECONDARY, {"ocr": "ocr_engine_missing", "ocr_cross_check": "ocr_engine_missing"}),
        (NO_VIE, WITHOUT_SECONDARY, {"ocr": "ocr_language_missing", "ocr_cross_check": "ocr_language_missing"}),
    ],
    ids=["ready", "no-binary", "no-vie", "no-secondary", "no-binary-no-secondary", "no-vie-no-secondary"],
)
def test_local_capability_failures(probe_result: OcrProbeResult, settings: OcrSettings, expected: Failures) -> None:
    assert local_capability_failures(probe_result, settings, secondary_loaded=True) == expected


@pytest.mark.parametrize(
    ("probe_result", "expected"),
    [
        (READY, {"ocr_cross_check": "secondary_ocr_unavailable"}),
        (NO_BINARY, {"ocr": "ocr_engine_missing", "ocr_cross_check": "ocr_engine_missing"}),
        (NO_VIE, {"ocr": "ocr_language_missing", "ocr_cross_check": "ocr_language_missing"}),
    ],
    ids=["ready", "no-binary", "no-vie"],
)
def test_secondary_not_loaded_marks_cross_check_unavailable(probe_result: OcrProbeResult, expected: Failures) -> None:
    assert local_capability_failures(probe_result, WITH_SECONDARY, secondary_loaded=False) == expected


def _probe_reporting(error: str | None) -> Callable[[PdfReaderConfig], SecondaryProbeReport]:
    def probe(config: PdfReaderConfig, /) -> SecondaryProbeReport:
        return SecondaryProbeReport(loaded=error is None, error=error)

    return probe


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (None, {}),
        ("ModuleNotFoundError", {"ocr_cross_check": "secondary_ocr_unavailable"}),
        ("ImportError", {"ocr_cross_check": "secondary_ocr_unavailable"}),
        ("ModelLoadFailure", {"ocr_cross_check": "secondary_ocr_unavailable"}),
        ("ProbeTimeout", {"ocr_cross_check": "secondary_ocr_unavailable"}),
    ],
    ids=["loads", "no-package", "import-breaks", "model-load-fails", "probe-hangs"],
)
def test_rapidocr_must_load_for_cross_check(error: str | None, expected: Failures) -> None:
    """Biến bật (`rapidocr`) mà không nạp được ⇒ `secondary_ocr_unavailable`, KHÔNG phải `secondary_ocr_disabled`."""
    calls: list[PdfReaderConfig] = []
    probe = _probe_reporting(error)

    def counting_probe(config: PdfReaderConfig, /) -> SecondaryProbeReport:
        calls.append(config)
        return probe(config)

    failures = check_local_capabilities(lambda: WITH_SECONDARY, _probe_returning(READY), counting_probe, CONFIG)
    assert failures == expected
    assert calls == [CONFIG]


def test_rapidocr_load_failure_logs_the_error_type_once(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        assert secondary_ocr_loads(_probe_reporting("ModelLoadFailure"), CONFIG) is False
    records = [record for record in caplog.records if "RapidOCR" in record.getMessage()]
    assert len(records) == 1 and "ModelLoadFailure" in records[0].getMessage()


def test_rapidocr_loaded_logs_nothing(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG):
        assert secondary_ocr_loads(_loaded, CONFIG) is True
    assert not caplog.records


@pytest.mark.parametrize(
    ("settings", "probe_result", "expected"),
    [
        (WITHOUT_SECONDARY, READY, {"ocr_cross_check": "secondary_ocr_disabled"}),
        (WITH_SECONDARY, NO_BINARY, {"ocr": "ocr_engine_missing", "ocr_cross_check": "ocr_engine_missing"}),
        (WITH_SECONDARY, NO_VIE, {"ocr": "ocr_language_missing", "ocr_cross_check": "ocr_language_missing"}),
    ],
    ids=["secondary-off", "no-binary", "no-vie"],
)
def test_rapidocr_is_not_loaded_when_it_would_not_run(
    settings: OcrSettings, probe_result: OcrProbeResult, expected: Failures
) -> None:
    """Worker chỉ nạp lượt thứ hai khi có OCR chính và `secondary` bật; bộ kiểm cũng vậy (không nạp model vô ích)."""
    failures = check_local_capabilities(lambda: settings, _probe_returning(probe_result), _must_not_load, CONFIG)
    assert failures == expected


def test_prober_receives_the_given_reader_config() -> None:
    config = PdfReaderConfig(ocr_probe_timeout_s=2.0)
    seen: list[PdfReaderConfig] = []

    def probe(settings: OcrSettings, reader_config: PdfReaderConfig, /) -> OcrProbeResult:
        seen.append(reader_config)
        return READY

    check_local_capabilities(lambda: WITH_SECONDARY, probe, _loaded, config)
    assert len(seen) == 1 and seen[0] is config


def test_invalid_ocr_config_reports_engine_missing_without_probing(caplog: pytest.LogCaptureFixture) -> None:
    bad_value = "khong-phai-tesseract"

    def load() -> OcrSettings:
        raise ConfigError(f"{ENV_PDF_OCR_PRIMARY} không hợp lệ: MVP chỉ nhận 'tesseract'")

    def probe(settings: OcrSettings, config: PdfReaderConfig, /) -> OcrProbeResult:
        raise AssertionError("không được probe khi cấu hình OCR sai")

    with caplog.at_level(logging.WARNING):
        failures = check_local_capabilities(load, probe, _must_not_load, CONFIG)
    assert failures == {"ocr": "ocr_engine_missing", "ocr_cross_check": "ocr_engine_missing"}
    assert ENV_PDF_OCR_PRIMARY in caplog.text and bad_value not in caplog.text


def test_invalid_ocr_config_log_never_echoes_the_error_message(caplog: pytest.LogCaptureFixture) -> None:
    """Thông điệp nội bộ của `ConfigError` có thể chứa giá trị người dùng đặt: log chỉ ghi câu cố định."""
    secret = "C:/Users/secret-user/token-abc123/tesseract.exe"

    def load() -> OcrSettings:
        raise ConfigError(f"{ENV_PDF_OCR_SECONDARY} không hợp lệ: nhận {secret!r}")

    with caplog.at_level(logging.DEBUG):
        failures = check_local_capabilities(load, _probe_returning(READY), _must_not_load, CONFIG)
    assert failures == {"ocr": "ocr_engine_missing", "ocr_cross_check": "ocr_engine_missing"}
    assert caplog.records, "phải có cảnh báo"
    assert "secret-user" not in caplog.text and "token-abc123" not in caplog.text
    assert all("token-abc123" not in str(record.args) for record in caplog.records)


# --------------------------------------------------------------------------- #
# Probe Tesseract qua bộ chạy lệnh giả
# --------------------------------------------------------------------------- #


class FakeRunner:
    """Bộ chạy lệnh giả: trả kết quả theo cờ thứ hai của lệnh (`--version`, `--list-langs`) và ghi lại lệnh."""

    def __init__(self, results: dict[str, RunResult]) -> None:
        self.results = results
        self.calls: list[tuple[list[str], float, int]] = []

    def __call__(self, args: Sequence[str], stdin_data: bytes, timeout_s: float, max_output_bytes: int) -> RunResult:
        assert stdin_data == b""
        self.calls.append((list(args), timeout_s, max_output_bytes))
        return self.results[args[1]]


OK_VERSION = RunResult(status="ok", stdout=b"tesseract 5.4.0\n")
LANGS_WITH_VIE = RunResult(status="ok", stdout=b'List of available languages in "/opt/tessdata/" (2):\neng\nvie\n')
LANGS_WITHOUT_VIE = RunResult(status="ok", stdout=b'List of available languages in "/opt/tessdata/" (1):\neng\n')
FAILED = RunResult(status="failed")


# --------------------------------------------------------------------------- #
# Unicharset của vie.traineddata (ADR-008 mục 14; spec §5.7, §5.12)
# --------------------------------------------------------------------------- #


def _traineddata(letters: Sequence[str], count: int = 24) -> bytes:
    """File `.traineddata` tối thiểu chỉ có unicharset của LSTM chứa `letters` (cùng định dạng test_pdf_ocr.py)."""
    entries = ["NULL 0 Common 0", *[f"{c} 3 0,255 Latin 5 0 5 {c}" for c in letters]]
    unicharset = (f"{len(entries)}\n" + "\n".join(entries) + "\n").encode("utf-8")
    offsets = [-1] * count
    offsets[TESSDATA_LSTM_UNICHARSET] = 4 + 8 * count
    return struct.pack(f"<i{count}q", count, *offsets) + unicharset


ALL_LETTERS = sorted(vietnamese_letters())


def _tessdata(root: Path, letters: Sequence[str]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "vie.traineddata").write_bytes(_traineddata(letters))
    return root


def _settings_in(tessdata: Path | None) -> OcrSettings:
    return OcrSettings(
        tesseract_cmd="/opt/tesseract",
        tessdata_dir=None if tessdata is None else str(tessdata),
        secondary="rapidocr",
    )


@pytest.fixture
def full_tessdata(tmp_path: Path) -> Path:
    return _tessdata(tmp_path / "tessdata", ALL_LETTERS)


def _probe_with(
    settings: OcrSettings, config: PdfReaderConfig = CONFIG, header_dir: str | None = None
) -> OcrProbeResult:
    header = f'List of available languages in "{header_dir}" (2):' if header_dir is not None else "List of languages:"
    listing = RunResult(status="ok", stdout=f"{header}\neng\nvie\n".encode())
    return probe_tesseract(settings, config, FakeRunner({"--version": OK_VERSION, "--list-langs": listing}))


def test_full_charset_is_available(full_tessdata: Path) -> None:
    assert _probe_with(_settings_in(full_tessdata)) == READY


def test_letter_outside_allowed_list_means_language_missing(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    lacking = [letter for letter in ALL_LETTERS if letter not in {"ơ"}]
    tessdata = _tessdata(tmp_path / "td", lacking)
    with caplog.at_level(logging.WARNING):
        assert _probe_with(_settings_in(tessdata)) == NO_VIE
    assert str(tmp_path) not in caplog.text


def test_only_allowed_letters_missing_is_available(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """`vie.traineddata` bản best thiếu đúng `Ỵ`, `ỵ`: vẫn khả dụng, kèm cảnh báo."""
    tessdata = _tessdata(tmp_path / "td", [letter for letter in ALL_LETTERS if letter not in {"Ỵ", "ỵ"}])
    with caplog.at_level(logging.WARNING):
        assert _probe_with(_settings_in(tessdata)) == READY
    assert "Ỵ" in caplog.text


def test_allowed_list_comes_from_reader_config(tmp_path: Path) -> None:
    tessdata = _tessdata(tmp_path / "td", [letter for letter in ALL_LETTERS if letter != "Ỵ"])
    strict = PdfReaderConfig(ocr_charset_allowed_missing=frozenset())
    assert _probe_with(_settings_in(tessdata), strict) == NO_VIE
    assert _probe_with(_settings_in(tessdata), CONFIG) == READY


def test_traineddata_over_the_byte_cap_means_language_missing(full_tessdata: Path) -> None:
    size = (full_tessdata / "vie.traineddata").stat().st_size
    assert size > 1024
    assert _probe_with(_settings_in(full_tessdata), PdfReaderConfig(ocr_traineddata_max_bytes=size - 1)) == NO_VIE
    assert _probe_with(_settings_in(full_tessdata), PdfReaderConfig(ocr_traineddata_max_bytes=size)) == READY


def test_unreadable_traineddata_means_language_missing(tmp_path: Path) -> None:
    # Thư mục đã đặt không có `vie.traineddata` và không thư mục nào khác có: không xác định được ⇒ bỏ qua kiểm
    # (dò theo thứ tự, duyệt 2026-10-30; trước đây là thiếu `vie`).
    assert _probe_with(_settings_in(tmp_path / "khong-co")) == READY
    garbage = tmp_path / "rac"
    garbage.mkdir()
    (garbage / "vie.traineddata").write_bytes(b"\x00" * 2048)
    assert _probe_with(_settings_in(garbage)) == NO_VIE
    # Đường dẫn là thư mục (lỗi đọc OSError) cũng là không đọc được.
    as_dir = tmp_path / "la-thu-muc"
    (as_dir / "vie.traineddata").mkdir(parents=True)
    assert _probe_with(_settings_in(as_dir)) == NO_VIE


def test_tessdata_dir_falls_back_to_the_list_langs_header(full_tessdata: Path, tmp_path: Path) -> None:
    """Không đặt `HOLOCRON_TESSDATA_DIR`: thư mục lấy từ dòng đầu của đúng lần `--list-langs` đã chạy."""
    runner = FakeRunner(
        {
            "--version": OK_VERSION,
            "--list-langs": RunResult(
                status="ok", stdout=f'List of available languages in "{full_tessdata}/" (2):\neng\nvie\n'.encode()
            ),
        }
    )
    assert probe_tesseract(_settings_in(None), CONFIG, runner) == READY
    assert [args[1] for args, _, _ in runner.calls] == ["--version", "--list-langs"]
    # Dòng đầu có ghi thư mục mà thư mục đó không có `vie.traineddata`, không thư mục nào khác có: bỏ qua kiểm (dò
    # theo thứ tự, duyệt 2026-10-30; trước đây là thiếu `vie`).
    assert _probe_with(_settings_in(None), header_dir=str(tmp_path / "khong-co")) == READY


def test_unknown_tessdata_dir_skips_the_charset_check(caplog: pytest.LogCaptureFixture) -> None:
    """Không đặt `HOLOCRON_TESSDATA_DIR` và dòng đầu `--list-langs` không ghi thư mục ⇒ bỏ qua kiểm unicharset."""
    with caplog.at_level(logging.WARNING):
        assert _probe_with(_settings_in(None), header_dir=None) == READY
    assert sum("bỏ qua kiểm unicharset" in record.getMessage() for record in caplog.records) == 1


def test_tesseract_4_list_langs_without_directory_is_available() -> None:
    """Tesseract 4.1.1 ghi `List of available languages (3):` (không có thư mục): có `vie` là khả dụng."""
    listing = RunResult(status="ok", stdout=b"List of available languages (3):\neng\nosd\nvie\n")
    version = RunResult(status="ok", stdout=b"tesseract 4.1.1\n")
    assert probe_tesseract(_settings_in(None), CONFIG, FakeRunner({"--version": version, "--list-langs": listing})) == (
        READY
    )
    without_vie = RunResult(status="ok", stdout=b"List of available languages (2):\neng\nosd\n")
    runner = FakeRunner({"--version": version, "--list-langs": without_vie})
    assert probe_tesseract(_settings_in(None), CONFIG, runner) == NO_VIE


def test_configured_tessdata_dir_wins_over_the_header(full_tessdata: Path, tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    lacking = _tessdata(tmp_path / "lacking", [letter for letter in ALL_LETTERS if letter != "ơ"])
    assert _probe_with(_settings_in(full_tessdata), header_dir=str(lacking)) == READY
    assert _probe_with(_settings_in(lacking), header_dir=str(full_tessdata)) == NO_VIE
    # Thư mục đã đặt không có file thì dò tiếp tới thư mục ở dòng đầu `--list-langs` (duyệt 2026-10-30).
    assert _probe_with(_settings_in(empty), header_dir=str(lacking)) == NO_VIE
    assert _probe_with(_settings_in(empty), header_dir=str(full_tessdata)) == READY


def test_listed_tessdata_dir_parses_the_header() -> None:
    assert listed_tessdata_dir('List of available languages in "/usr/share/tessdata/" (3):') == "/usr/share/tessdata/"
    assert listed_tessdata_dir('List of available languages in "C:\\Tesseract\\tessdata/" (1):') == (
        "C:\\Tesseract\\tessdata/"
    )
    assert listed_tessdata_dir("List of available languages (3):") is None


def test_read_bounded_respects_the_cap(tmp_path: Path) -> None:
    path = tmp_path / "f.bin"
    path.write_bytes(b"x" * 10)
    assert read_bounded(path, 10) == b"x" * 10
    assert read_bounded(path, 9) is None
    assert read_bounded(tmp_path / "missing", 10) is None


def test_default_check_end_to_end_reports_language_missing_on_bad_charset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bộ kiểm thật với probe thật (chỉ bộ chạy lệnh giả): unicharset thiếu chữ ⇒ `ocr_language_missing`."""
    tessdata = _tessdata(tmp_path / "td", [letter for letter in ALL_LETTERS if letter != "đ"])
    runner = FakeRunner({"--version": OK_VERSION, "--list-langs": LANGS_WITH_VIE})

    def probe(settings: OcrSettings, config: PdfReaderConfig, /) -> OcrProbeResult:
        return probe_tesseract(settings, config, runner)

    monkeypatch.setattr(runtime, "load_ocr_settings", lambda: _settings_in(tessdata))
    monkeypatch.setattr(runtime, "probe_tesseract", probe)
    monkeypatch.setattr(runtime, "probe_secondary_ocr", _must_not_load)
    assert get_local_capability_check()(CONFIG) == {
        "ocr": "ocr_language_missing",
        "ocr_cross_check": "ocr_language_missing",
    }


def test_allowed_missing_defaults_to_the_approved_letters_only() -> None:
    assert PdfReaderConfig().ocr_charset_allowed_missing == frozenset({"Ỵ", "ỵ"})
    assert PdfReaderConfig(ocr_charset_allowed_missing=frozenset({"ỵ"})).ocr_charset_allowed_missing == {"ỵ"}
    with pytest.raises(ValidationError):
        PdfReaderConfig(ocr_charset_allowed_missing=frozenset({"Ỵ", "ỵ", "ơ"}))
    with pytest.raises(ValidationError):
        PdfReaderConfig(ocr_traineddata_max_bytes=0)


@pytest.mark.parametrize(
    ("results", "expected"),
    [
        ({"--version": OK_VERSION, "--list-langs": LANGS_WITH_VIE}, READY),
        ({"--version": FAILED, "--list-langs": FAILED}, NO_BINARY),
        ({"--version": RunResult(status="timeout"), "--list-langs": FAILED}, NO_BINARY),
        ({"--version": OK_VERSION, "--list-langs": LANGS_WITHOUT_VIE}, NO_VIE),
        ({"--version": OK_VERSION, "--list-langs": FAILED}, NO_VIE),
    ],
    ids=["ready", "binary-fails", "binary-hangs", "no-vie", "list-langs-fails"],
)
def test_probe_tesseract_tells_missing_binary_from_missing_vie(
    results: dict[str, RunResult], expected: OcrProbeResult, full_tessdata: Path
) -> None:
    assert probe_tesseract(_settings_in(full_tessdata), CONFIG, run=FakeRunner(results)) == expected


@pytest.mark.parametrize(
    ("results", "expected_flags"),
    [
        ({"--version": FAILED, "--list-langs": FAILED}, ["--version"]),
        ({"--version": OK_VERSION, "--list-langs": LANGS_WITHOUT_VIE}, ["--version", "--list-langs"]),
        ({"--version": OK_VERSION, "--list-langs": FAILED}, ["--version", "--list-langs"]),
        ({"--version": OK_VERSION, "--list-langs": LANGS_WITH_VIE}, ["--version", "--list-langs"]),
    ],
    ids=["binary-fails", "no-vie", "list-langs-fails", "ready"],
)
def test_probe_tesseract_runs_each_command_once(results: dict[str, RunResult], expected_flags: list[str]) -> None:
    runner = FakeRunner(results)
    probe_tesseract(WITH_SECONDARY, CONFIG, runner)
    assert [args[1] for args, _, _ in runner.calls] == expected_flags


def test_probe_tesseract_default_runner_uses_reader_config(
    monkeypatch: pytest.MonkeyPatch, full_tessdata: Path
) -> None:
    """`run=None` ⇒ `run_bounded` với `kill_grace_s`, timeout và giới hạn byte lấy từ cấu hình của app."""
    config = PdfReaderConfig(ocr_probe_timeout_s=3.25, ocr_probe_max_bytes=2048, ocr_kill_grace_s=1.75)
    recorded: list[tuple[str, float, int, float]] = []

    def fake_run_bounded(
        args: Sequence[str], stdin_data: bytes, timeout_s: float, max_output_bytes: int, *, kill_grace_s: float
    ) -> RunResult:
        recorded.append((args[1], timeout_s, max_output_bytes, kill_grace_s))
        return {"--version": OK_VERSION, "--list-langs": LANGS_WITH_VIE}[args[1]]

    monkeypatch.setattr(capability_checks, "run_bounded", fake_run_bounded)
    assert probe_tesseract(_settings_in(full_tessdata), config) == READY
    assert recorded == [("--version", 3.25, 2048, 1.75), ("--list-langs", 3.25, 2048, 1.75)]


def test_probe_tesseract_uses_the_reader_limits_and_tessdata_dir() -> None:
    config = PdfReaderConfig(ocr_probe_timeout_s=3.5, ocr_probe_max_bytes=4096)
    runner = FakeRunner({"--version": OK_VERSION, "--list-langs": LANGS_WITH_VIE})
    probe_tesseract(WITH_SECONDARY, config, runner)
    assert runner.calls == [
        (["/opt/tesseract", "--version"], 3.5, 4096),
        (["/opt/tesseract", "--list-langs", "--tessdata-dir", "/opt/tessdata"], 3.5, 4096),
    ]


# --------------------------------------------------------------------------- #
# GET /v1/runtime và các route cần khả năng: kiểm một lần, dùng lại kết quả đã lưu
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("settings", "probe_result", "expected"),
    [
        (WITH_SECONDARY, READY, {"ocr": (True, None), "ocr_cross_check": (True, None)}),
        (
            WITH_SECONDARY,
            NO_BINARY,
            {"ocr": (False, "ocr_engine_missing"), "ocr_cross_check": (False, "ocr_engine_missing")},
        ),
        (
            WITH_SECONDARY,
            NO_VIE,
            {"ocr": (False, "ocr_language_missing"), "ocr_cross_check": (False, "ocr_language_missing")},
        ),
        (WITHOUT_SECONDARY, READY, {"ocr": (True, None), "ocr_cross_check": (False, "secondary_ocr_disabled")}),
    ],
    ids=["ready", "no-binary", "no-vie", "no-secondary"],
)
def test_runtime_reports_the_local_check(
    install_check: Callable[[LocalCapabilityCheck], None],
    settings: OcrSettings,
    probe_result: OcrProbeResult,
    expected: dict[str, tuple[object, object]],
) -> None:
    check = CountingCheck(settings, probe_result)
    install_check(check)
    client = TestClient(app)
    bodies = [client.get("/v1/runtime").json() for _ in range(3)]
    assert all(_ocr_pair(body) == expected for body in bodies)
    assert check.calls == 1


def test_lifespan_checks_once_at_startup_and_never_per_request(
    install_check: Callable[[LocalCapabilityCheck], None],
) -> None:
    check = CountingCheck(WITH_SECONDARY, NO_VIE)
    install_check(check)
    with TestClient(app) as client:
        assert check.calls == 1, "kiểm lúc khởi động, trước request đầu tiên"
        for _ in range(3):
            body = client.get("/v1/runtime").json()
            assert _ocr_pair(body)["ocr"] == (False, "ocr_language_missing")
        client.post("/v1/blueprints", json={"subject_code": "SSH1110", "theory_slots": []})
        client.post("/v1/vault/concept-pairs/propose", json={"subject_code": "SSH1110", "chunk_ids": ["chk_x"]})
        assert check.calls == 1
    # Tắt app thì bỏ kết quả đã lưu: lần khởi động sau kiểm lại.
    with TestClient(app):
        assert check.calls == 2


def test_vault_route_reads_the_stored_local_failures(install_check: Callable[[LocalCapabilityCheck], None]) -> None:
    """Nối dây: lý do kiểm cục bộ đã lưu đi tới 409 của route đề xuất cặp khái niệm (lý do ở đây chỉ để quan sát)."""
    calls: list[int] = []

    def check(config: PdfReaderConfig, /) -> Failures:
        calls.append(1)
        return {"concept_pair_llm_proposal": "ocr_language_missing"}

    install_check(check)
    config = load_runtime_config(LLM_ENV)
    app.dependency_overrides[get_runtime_config] = lambda: config
    client = TestClient(app)
    for _ in range(2):
        response = client.post(
            "/v1/vault/concept-pairs/propose", json={"subject_code": "SSH1110", "chunk_ids": ["chk_x"]}
        )
        assert response.status_code == 409
        assert response.json()["detail"]["reason"] == "ocr_language_missing"
    assert len(calls) == 1


def test_blueprints_route_passes_the_stored_local_failures(
    install_check: Callable[[LocalCapabilityCheck], None], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nối dây: route tạo blueprint (slot ESSAY) đưa đúng kết quả kiểm đã lưu vào `compute_capabilities`.

    `essay_grading_offline` chưa có code (luôn `not_implemented`) nên lý do kiểm cục bộ không lộ qua 409; quan sát
    bằng spy trên `compute_capabilities` của module route.
    """
    stored: Failures = {"ocr": "ocr_language_missing", "ocr_cross_check": "ocr_language_missing"}
    install_check(lambda _config: dict(stored))
    seen: list[Mapping[CapabilityName, CapabilityReason] | None] = []
    real = blueprints.compute_capabilities

    def spy(
        config: RuntimeConfig, *, local_failures: Mapping[CapabilityName, CapabilityReason] | None = None
    ) -> list[Capability]:
        seen.append(local_failures)
        return real(config, local_failures=local_failures)

    monkeypatch.setattr(blueprints, "compute_capabilities", spy)
    body = {"subject_code": "SSH1110", "theory_slots": [{"chapter_code": "ch1", "format": "ESSAY"}]}
    with TestClient(app) as client:
        for _ in range(2):
            response = client.post("/v1/blueprints", json=body)
            assert response.status_code == 409
    assert len(seen) == 2
    assert all(failures is not None and dict(failures) == stored for failures in seen)


def test_default_check_reports_language_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bộ kiểm thật (không override): cấu hình OCR và probe của module `runtime` được dùng, kèm `reader_config`."""
    seen: list[PdfReaderConfig] = []

    def probe(settings: OcrSettings, config: PdfReaderConfig, /) -> OcrProbeResult:
        assert settings is WITH_SECONDARY
        seen.append(config)
        return NO_VIE

    monkeypatch.setattr(runtime, "load_ocr_settings", lambda: WITH_SECONDARY)
    monkeypatch.setattr(runtime, "probe_tesseract", probe)
    monkeypatch.setattr(runtime, "probe_secondary_ocr", _must_not_load)
    config = PdfReaderConfig(ocr_probe_timeout_s=4.0)
    failures = get_local_capability_check()(config)
    assert failures == {"ocr": "ocr_language_missing", "ocr_cross_check": "ocr_language_missing"}
    assert len(seen) == 1 and seen[0] is config


def test_default_check_probes_rapidocr_through_the_runtime_prober(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime, "load_ocr_settings", lambda: WITH_SECONDARY)
    monkeypatch.setattr(runtime, "probe_tesseract", _probe_returning(READY))
    monkeypatch.setattr(runtime, "probe_secondary_ocr", _not_loaded)
    assert get_local_capability_check()(CONFIG) == {"ocr_cross_check": "secondary_ocr_unavailable"}
    monkeypatch.setattr(runtime, "probe_secondary_ocr", _loaded)
    assert get_local_capability_check()(CONFIG) == {}


def test_default_check_reports_engine_missing_on_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def load() -> OcrSettings:
        raise ConfigError(f"{ENV_PDF_OCR_PRIMARY} không hợp lệ")

    def probe(settings: OcrSettings, config: PdfReaderConfig, /) -> OcrProbeResult:
        raise AssertionError("không được probe khi cấu hình OCR sai")

    monkeypatch.setattr(runtime, "load_ocr_settings", load)
    monkeypatch.setattr(runtime, "probe_tesseract", probe)
    monkeypatch.setattr(runtime, "probe_secondary_ocr", _must_not_load)
    assert get_local_capability_check()(CONFIG) == {
        "ocr": "ocr_engine_missing",
        "ocr_cross_check": "ocr_engine_missing",
    }


def test_lifespan_checks_with_the_import_runtime_config(
    install_check: Callable[[LocalCapabilityCheck], None],
) -> None:
    """Kiểm chạy sau khi tạo bộ nhập, với đúng `reader_config` của bộ nhập; kết quả lưu ở dạng chỉ đọc."""
    check = CountingCheck(WITH_SECONDARY, NO_VIE)
    install_check(check)
    with TestClient(app):
        import_runtime = getattr(app.state, RUNTIME_STATE_ATTR)
        assert len(check.configs) == 1 and check.configs[0] is import_runtime.reader_config
        stored = getattr(app.state, LOCAL_FAILURES_STATE_ATTR)
        assert isinstance(stored, MappingProxyType)
        assert not hasattr(stored, "__setitem__")
        assert dict(stored) == {"ocr": "ocr_language_missing", "ocr_cross_check": "ocr_language_missing"}


def test_lazy_check_without_lifespan_stores_a_read_only_mapping(
    install_check: Callable[[LocalCapabilityCheck], None],
) -> None:
    check = CountingCheck(WITH_SECONDARY, READY)
    install_check(check)
    client = TestClient(app)
    client.get("/v1/runtime")
    client.get("/v1/runtime")
    stored = getattr(app.state, LOCAL_FAILURES_STATE_ATTR)
    assert isinstance(stored, MappingProxyType)
    assert check.configs == [PdfReaderConfig()]


def test_lazy_check_runs_once_under_concurrent_first_requests() -> None:
    """Hai request đầu tiên đến cùng lúc (dependency đồng bộ chạy trong threadpool): bộ kiểm chỉ chạy một lần."""
    application = FastAPI()
    request = SimpleNamespace(app=application)
    entered = threading.Event()
    release = threading.Event()
    calls: list[int] = []

    def slow_check(config: PdfReaderConfig, /) -> Failures:
        calls.append(1)
        entered.set()
        release.wait(5)
        return {"ocr": "ocr_language_missing"}

    results: list[Mapping[CapabilityName, CapabilityReason]] = []

    def call() -> None:
        results.append(runtime.get_local_failures(request, slow_check))

    first = threading.Thread(target=call)
    first.start()
    assert entered.wait(5)
    second = threading.Thread(target=call)
    second.start()
    time.sleep(0.2)  # lượt thứ hai đã chờ khoá (có khoá) hoặc đã vào bộ kiểm (không có khoá)
    release.set()
    first.join(5)
    second.join(5)
    assert calls == [1]
    assert len(results) == 2 and results[0] is results[1]


# --------------------------------------------------------------------------- #
# Mã lý do `secondary_ocr_unavailable` (duyệt 2026-10-29) và tiến trình con thăm dò RapidOCR
# --------------------------------------------------------------------------- #


def test_secondary_env_set_empty_means_disabled_without_probing() -> None:
    """`HOLOCRON_PDF_OCR_SECONDARY` đặt rỗng ⇒ `secondary_ocr_disabled`, không thăm dò RapidOCR."""
    settings = load_ocr_settings({ENV_PDF_OCR_SECONDARY: ""}, which=lambda _command: None)
    assert settings.secondary is None
    failures = check_local_capabilities(lambda: settings, _probe_returning(READY), _must_not_load, CONFIG)
    assert failures == {"ocr_cross_check": "secondary_ocr_disabled"}


def test_secondary_unavailable_detail_is_the_approved_sentence() -> None:
    assert REASON_DETAILS_VI["secondary_ocr_unavailable"] == (
        "Không nạp được lượt đọc thứ hai (RapidOCR) trên máy này. Bản rút gọn không kèm RapidOCR: dùng bản đầy đủ. "
        "Nếu đang dùng bản đầy đủ, cài lại ứng dụng."
    )
    # `local_check_pending` (duyệt 2026-10-30) thêm sau, ở cuối.
    assert get_args(CapabilityReason)[-2:] == ("secondary_ocr_unavailable", "local_check_pending")


FAKE_MODELS = (rapidocr_adapter.DET_MODEL, rapidocr_adapter.REC_MODEL, rapidocr_adapter.CLS_MODEL)

FAKE_LOADS = """
import sys


class LimitsNotApplied(Exception):
    pass


class RapidOCR:
    def __init__(self, params):
        if sys.platform != "win32":
            import resource

            if resource.getrlimit(resource.RLIMIT_FSIZE) != (0, 0):
                raise LimitsNotApplied()
        print("rapidocr giả in ra stdout trong lúc nạp")

    def __call__(self, image):
        return None
"""
FAKE_IMPORT_BREAKS = 'raise ImportError("C:/Users/secret-user/onnxruntime_providers.dll")\n'
FAKE_MODEL_FAILS = """
class ModelLoadFailure(Exception):
    pass


class RapidOCR:
    def __init__(self, params):
        raise ModelLoadFailure("/home/secret-user/models/PP-OCRv6_det_small.onnx")
"""
FAKE_CRASHES = "import os\n\nos._exit(3)\n"
FAKE_HANGS = "import time\n\ntime.sleep(10)\n"


def _fake_rapidocr(root: Path, source: str, *, with_models: bool = True) -> Path:
    """Gói `rapidocr` giả trong `root` (đặt trước trong `PYTHONPATH` của tiến trình con)."""
    package = root / "rapidocr"
    (package / "models").mkdir(parents=True)
    (package / "__init__.py").write_text(source, encoding="utf-8")
    if with_models:
        for name in FAKE_MODELS:
            (package / "models" / name).write_bytes(b"onnx")
    return root


def _env_with(root: Path) -> dict[str, str]:
    env = probe_env()
    env["PYTHONPATH"] = os.pathsep.join([str(root), env["PYTHONPATH"]])
    return env


def _child_probe(root: Path, config: PdfReaderConfig = CONFIG) -> SecondaryProbeReport:
    runner = functools.partial(real_run_bounded, kill_grace_s=config.ocr_kill_grace_s, env=_env_with(root))
    return probe_secondary_ocr(config, runner)


def test_child_probe_loads_rapidocr_after_applying_limits(tmp_path: Path) -> None:
    """Nạp được ⇒ `loaded`; trên Unix gói giả chỉ nạp được khi `RLIMIT_FSIZE` = 0 đã áp TRƯỚC khi nạp. Gói in ra
    stdout trong lúc nạp mà báo cáo vẫn đọc được."""
    assert _child_probe(_fake_rapidocr(tmp_path, FAKE_LOADS)) == LOADED


def test_child_probe_import_error_is_unavailable(tmp_path: Path) -> None:
    report = _child_probe(_fake_rapidocr(tmp_path, FAKE_IMPORT_BREAKS))
    assert report == SecondaryProbeReport(loaded=False, error="ImportError")


def test_child_probe_model_error_outside_builtin_errors_is_unavailable(tmp_path: Path) -> None:
    """Lỗi nạp model không thuộc `BUILTIN_ERRORS` (lọt ra khỏi `load_outcome`) vẫn thành "không nạp được"."""
    report = _child_probe(_fake_rapidocr(tmp_path, FAKE_MODEL_FAILS))
    assert report == SecondaryProbeReport(loaded=False, error="ModelLoadFailure")


def test_child_probe_missing_models_is_unavailable(tmp_path: Path) -> None:
    report = _child_probe(_fake_rapidocr(tmp_path, FAKE_LOADS, with_models=False))
    assert report == SecondaryProbeReport(loaded=False, error=rapidocr_adapter.MISSING_MODEL)


def test_child_probe_crash_is_unavailable(tmp_path: Path) -> None:
    report = _child_probe(_fake_rapidocr(tmp_path, FAKE_CRASHES))
    assert report == SecondaryProbeReport(loaded=False, error=secondary_probe.CHILD_FAILED)


def test_child_probe_hang_is_killed_at_the_startup_timeout(tmp_path: Path) -> None:
    config = PdfReaderConfig(worker_startup_timeout_s=1.5, ocr_kill_grace_s=0.5)
    started = time.monotonic()
    report = _child_probe(_fake_rapidocr(tmp_path, FAKE_HANGS), config)
    assert report == SecondaryProbeReport(loaded=False, error=secondary_probe.CHILD_TIMEOUT)
    assert time.monotonic() - started < 8


def test_child_probe_failure_log_has_only_the_error_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Log một lần: tên loại lỗi; không đường dẫn tuyệt đối (thông điệp lỗi chứa đường dẫn), không biến môi trường."""
    monkeypatch.setenv("HOLOCRON_LLM_API_KEY", "sk-env-secret-must-not-leak")
    root = _fake_rapidocr(tmp_path, FAKE_MODEL_FAILS)

    def probe(config: PdfReaderConfig, /) -> SecondaryProbeReport:
        return _child_probe(root, config)

    with caplog.at_level(logging.DEBUG):
        failures = check_local_capabilities(lambda: WITH_SECONDARY, _probe_returning(READY), probe, CONFIG)
    assert failures == {"ocr_cross_check": "secondary_ocr_unavailable"}
    messages = [record.getMessage() for record in caplog.records]
    assert len([message for message in messages if "RapidOCR" in message]) == 1
    text = caplog.text
    assert "ModelLoadFailure" in text
    for leaked in ("secret-user", str(tmp_path), tmp_path.name, "sk-env-secret-must-not-leak", "PYTHONPATH"):
        assert leaked not in text


def test_parent_never_calls_the_rapidocr_loader(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Tiến trình cha chỉ dựng tiến trình con: gọi bộ nạp RapidOCR ở đây là lỗi."""

    def forbidden(cls: type[RapidOcrBackend]) -> rapidocr_adapter.RapidOcrLoad:
        raise AssertionError("tiến trình cha không được nạp RapidOCR")

    monkeypatch.setattr(RapidOcrBackend, "load_outcome", classmethod(forbidden))
    assert _child_probe(_fake_rapidocr(tmp_path, FAKE_LOADS)) == LOADED


def test_app_process_does_not_import_rapidocr_or_its_native_libraries() -> None:
    """Nhập app và chạy bộ thăm dò thật trong một tiến trình Python sạch: không có `rapidocr`/`onnxruntime`/`cv2`."""
    code = (
        "import sys\n"
        "import holocron_core.routers.api\n"
        "from holocron_core.infrastructure.document_readers.config import PdfReaderConfig\n"
        "from holocron_core.infrastructure.document_readers.secondary_probe import probe_secondary_ocr\n"
        "probe_secondary_ocr(PdfReaderConfig())\n"
        "print(sorted(name for name in ('rapidocr', 'onnxruntime', 'cv2') if name in sys.modules))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        encoding="utf-8",
        env=probe_env(),
        timeout=120,
        check=True,
    )
    assert completed.stdout.strip().splitlines()[-1] == "[]"


def test_app_still_starts_when_rapidocr_model_load_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Bộ kiểm mặc định của app (không override), tiến trình con thật với gói giả lỗi nạp model: app vẫn khởi động
    và báo `secondary_ocr_unavailable`."""
    root = _fake_rapidocr(tmp_path, FAKE_MODEL_FAILS)
    monkeypatch.delitem(app.dependency_overrides, get_local_capability_check)
    monkeypatch.setattr(secondary_probe, "run_bounded", real_run_bounded)
    monkeypatch.setattr(secondary_probe, "probe_env", lambda: _env_with(root))
    with TestClient(app) as client:
        response = client.get("/v1/runtime")
    assert response.status_code == 200
    assert _ocr_pair(response.json()) == {"ocr": (True, None), "ocr_cross_check": (False, "secondary_ocr_unavailable")}


def _runner_returning(result: RunResult) -> Callable[..., RunResult]:
    def run(
        args: Sequence[str],
        stdin_data: bytes,
        timeout_s: float,
        max_output_bytes: int,
        *,
        kill_grace_s: float,
        env: Mapping[str, str] | None = None,
    ) -> RunResult:
        return result

    return run


def test_default_check_wires_the_real_prober_down_to_the_child_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    """`get_local_capability_check` nối đúng bộ thăm dò thật: thay bộ chạy tiến trình con (tầng thấp nhất) là đổi
    kết quả."""
    monkeypatch.setattr(runtime, "load_ocr_settings", lambda: WITH_SECONDARY)
    monkeypatch.setattr(runtime, "probe_tesseract", _probe_returning(READY))
    monkeypatch.setattr(secondary_probe, "run_bounded", _runner_returning(RunResult(status="failed")))
    assert get_local_capability_check()(CONFIG) == {"ocr_cross_check": "secondary_ocr_unavailable"}
    loaded = RunResult(status="ok", stdout=f"{REPORT_PREFIX}{LOADED.model_dump_json()}\n".encode())
    monkeypatch.setattr(secondary_probe, "run_bounded", _runner_returning(loaded))
    assert get_local_capability_check()(CONFIG) == {}


# --------------------------------------------------------------------------- #
# Bộ thăm dò: phía tiến trình cha (bộ chạy giả) và phía tiến trình con (gọi trong tiến trình test)
# --------------------------------------------------------------------------- #


class RecordingRunner:
    def __init__(self, result: RunResult) -> None:
        self.result = result
        self.calls: list[tuple[list[str], bytes, float, int]] = []

    def __call__(self, args: Sequence[str], stdin_data: bytes, timeout_s: float, max_output_bytes: int) -> RunResult:
        self.calls.append((list(args), stdin_data, timeout_s, max_output_bytes))
        return self.result


def _report_line(report: SecondaryProbeReport) -> bytes:
    return f"{REPORT_PREFIX}{report.model_dump_json()}\n".encode()


def test_probe_runs_the_child_module_with_the_worker_limits() -> None:
    config = PdfReaderConfig(
        worker_mem_bytes=512 * 1024 * 1024,
        ocr_cpu_seconds_per_page=7,
        worker_startup_timeout_s=12.5,
        ocr_probe_max_bytes=4096,
    )
    runner = RecordingRunner(RunResult(status="ok", stdout=_report_line(LOADED)))
    assert probe_secondary_ocr(config, runner) == LOADED
    [(args, stdin_data, timeout_s, max_bytes)] = runner.calls
    assert args == [sys.executable, "-P", "-s", "-m", secondary_probe.PROBE_MODULE]
    request = SecondaryProbeRequest.model_validate_json(stdin_data)
    assert request == SecondaryProbeRequest(mem_bytes=512 * 1024 * 1024, cpu_seconds=7)
    assert (timeout_s, max_bytes) == (12.5, 4096)


@pytest.mark.parametrize(
    ("status", "error"),
    [
        ("timeout", secondary_probe.CHILD_TIMEOUT),
        ("overflow", secondary_probe.CHILD_OVERFLOW),
        ("failed", secondary_probe.CHILD_FAILED),
    ],
)
def test_probe_maps_child_run_failures(status: RunStatus, error: str) -> None:
    runner = RecordingRunner(RunResult(status=status))
    assert probe_secondary_ocr(CONFIG, runner) == SecondaryProbeReport(loaded=False, error=error)


def test_probe_default_runner_uses_kill_grace_and_the_minimal_env(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[float, Mapping[str, str] | None]] = []

    def run(
        args: Sequence[str],
        stdin_data: bytes,
        timeout_s: float,
        max_output_bytes: int,
        *,
        kill_grace_s: float,
        env: Mapping[str, str] | None = None,
    ) -> RunResult:
        seen.append((kill_grace_s, env))
        return RunResult(status="ok", stdout=_report_line(LOADED))

    monkeypatch.setattr(secondary_probe, "run_bounded", run)
    assert probe_secondary_ocr(PdfReaderConfig(ocr_kill_grace_s=0.75)) == LOADED
    assert seen == [(0.75, probe_env())]


def test_probe_env_is_minimal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOLOCRON_LLM_API_KEY", "sk-env-secret-must-not-leak")
    env = probe_env()
    allowed = {"PYTHONHASHSEED", "PYTHONIOENCODING", "PYTHONUTF8", "PYTHONPATH", "SYSTEMROOT"}
    assert set(env) <= allowed
    assert (Path(env["PYTHONPATH"]) / "holocron_core").is_dir()
    assert ("SYSTEMROOT" in env) == (sys.platform == "win32")


@pytest.mark.parametrize(
    "stdout",
    [
        b"\xff\xfe khong phai utf-8",
        b"khong co dong bao cao\n",
        f"{REPORT_PREFIX}{{khong phai json\n".encode(),
        f'{REPORT_PREFIX}{{"loaded": false, "error": "C:/Users/secret-user/x.onnx"}}\n'.encode(),
        f'{REPORT_PREFIX}{{"loaded": true, "error": "ImportError"}}\n'.encode(),
        f'{REPORT_PREFIX}{{"loaded": false, "error": null}}\n'.encode(),
        f'{REPORT_PREFIX}{{"loaded": true, "error": null, "path": "/x"}}\n'.encode(),
    ],
    ids=["not-utf8", "no-report", "not-json", "path-as-error", "loaded-with-error", "failed-without-error", "extra"],
)
def test_parse_report_rejects_malformed_reports(stdout: bytes) -> None:
    assert parse_report(stdout) == SecondaryProbeReport(loaded=False, error=secondary_probe.INVALID_REPORT)


def test_parse_report_takes_the_last_report_line_among_noise() -> None:
    failed = SecondaryProbeReport(loaded=False, error="ImportError")
    stdout = b"tieng on cua thu vien\n" + _report_line(failed) + b"them dong\n" + _report_line(LOADED)
    assert parse_report(stdout) == LOADED


class ExoticError(Exception):
    pass


NonAsciiError = type("LỗiNạp", (Exception,), {})


def _outcome(backend: RapidOcrBackend | None, error: str | None) -> Callable[[], rapidocr_adapter.RapidOcrLoad]:
    def load() -> rapidocr_adapter.RapidOcrLoad:
        return rapidocr_adapter.RapidOcrLoad(backend, error)

    return load


def _raising(exc: BaseException) -> Callable[[], rapidocr_adapter.RapidOcrLoad]:
    def load() -> rapidocr_adapter.RapidOcrLoad:
        raise exc

    return load


REQUEST_LINE = SecondaryProbeRequest(mem_bytes=1 << 30, cpu_seconds=9).model_dump_json() + "\n"
BACKEND = RapidOcrBackend(lambda image: None)


class ProbeRecorder:
    """Ghi lại thứ tự áp giới hạn, nạp và báo cáo của `run_probe`."""

    def __init__(self, load: Callable[[], rapidocr_adapter.RapidOcrLoad]) -> None:
        self._load = load
        self.events: list[object] = []
        self.reports: list[SecondaryProbeReport] = []

    def limit(self, mem_bytes: int, cpu_seconds: int) -> None:
        self.events.append(("limit", mem_bytes, cpu_seconds))

    def load(self) -> rapidocr_adapter.RapidOcrLoad:
        self.events.append("load")
        return self._load()

    def emit(self, report: SecondaryProbeReport) -> None:
        self.events.append("emit")
        self.reports.append(report)

    def run(self, request_line: str = REQUEST_LINE) -> None:
        run_probe(request_line, limit=self.limit, load=self.load, emit=self.emit)


@pytest.mark.parametrize(
    ("load", "expected"),
    [
        (_outcome(BACKEND, None), LOADED),
        (_outcome(None, "ModuleNotFoundError"), SecondaryProbeReport(loaded=False, error="ModuleNotFoundError")),
        (_outcome(None, "C:/Users/secret-user"), SecondaryProbeReport(loaded=False, error="UnnamedError")),
        (_outcome(None, None), SecondaryProbeReport(loaded=False, error="UnnamedError")),
    ],
    ids=["loads", "none-with-error", "error-not-identifier", "none-without-error"],
)
def test_run_probe_applies_limits_then_loads(
    load: Callable[[], rapidocr_adapter.RapidOcrLoad], expected: SecondaryProbeReport
) -> None:
    recorder = ProbeRecorder(load)
    recorder.run()
    assert recorder.events == [("limit", 1 << 30, 9), "load", "emit"]
    assert recorder.reports == [expected]


@pytest.mark.parametrize(
    ("exc", "error"),
    [
        (ExoticError("/home/secret-user/x"), "ExoticError"),
        (NonAsciiError("x"), "UnnamedError"),
        (MemoryError(), "MemoryError"),
        (SystemExit(4), "SystemExit"),
    ],
    ids=["exotic", "non-ascii", "memory", "system-exit"],
)
def test_run_probe_reports_then_reraises_errors_the_loader_lets_through(exc: BaseException, error: str) -> None:
    """Lỗi lạ lúc nạp: báo một dòng "không nạp được" kèm tên lớp, rồi ném lại (tiến trình con thoát mã khác 0)."""
    recorder = ProbeRecorder(_raising(exc))
    with pytest.raises(type(exc)):
        recorder.run()
    assert recorder.reports == [SecondaryProbeReport(loaded=False, error=error)]
    assert recorder.events == [("limit", 1 << 30, 9), "load", "emit"]


def test_run_probe_rejects_a_bad_request_before_limits_or_loading() -> None:
    recorder = ProbeRecorder(_raising(AssertionError("không được nạp")))
    recorder.run('{"mem_bytes": 0}\n')
    assert recorder.events == ["emit"]
    assert recorder.reports == [SecondaryProbeReport(loaded=False, error=secondary_probe.INVALID_REQUEST)]


@pytest.mark.parametrize(
    ("stdout", "expected"),
    [
        (b"", secondary_probe.CHILD_FAILED),
        (b"traceback rac\n", secondary_probe.CHILD_FAILED),
        (_report_line(SecondaryProbeReport(loaded=False, error="ModelLoadFailure")), "ModelLoadFailure"),
        # Thoát mã khác 0 thì không bao giờ là "nạp được", kể cả khi dòng báo cáo nói vậy.
        (_report_line(LOADED), secondary_probe.CHILD_FAILED),
    ],
    ids=["no-output", "noise", "reported-error", "claims-loaded"],
)
def test_probe_reads_the_report_of_a_failed_child(stdout: bytes, expected: str) -> None:
    runner = RecordingRunner(RunResult(status="failed", stdout=stdout))
    assert probe_secondary_ocr(CONFIG, runner) == SecondaryProbeReport(loaded=False, error=expected)


def test_child_main_writes_one_report_line_and_hides_library_output(monkeypatch: pytest.MonkeyPatch) -> None:
    stdout = io.StringIO()
    limits: list[tuple[int, int]] = []

    def noisy_load() -> rapidocr_adapter.RapidOcrLoad:
        print("thư viện in ra stdout")
        return rapidocr_adapter.RapidOcrLoad(BACKEND, None)

    monkeypatch.setattr(sys, "stdin", io.StringIO(REQUEST_LINE))
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    monkeypatch.setattr(secondary_probe, "apply_limits", lambda mem, cpu: limits.append((mem, cpu)))
    monkeypatch.setattr(secondary_probe, "RapidOcrBackend", SimpleNamespace(load_outcome=noisy_load))
    secondary_probe.main()
    assert stdout.getvalue() == _report_line(LOADED).decode()
    assert limits == [(1 << 30, 9)]


def test_load_outcome_names_each_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    package = tmp_path / "rapidocr"
    (package / "models").mkdir(parents=True)

    class Factory:
        def __init__(self, params: dict[str, object]) -> None:
            pass

        def __call__(self, image: bytes) -> object:
            return None

    module = SimpleNamespace(__file__=str(package / "__init__.py"), RapidOCR=Factory)
    real_import = rapidocr_adapter.importlib.import_module

    def fake_import(name: str) -> object:
        if name == "rapidocr":
            return module
        if name.startswith("rapidocr."):
            raise ImportError(name)
        return real_import(name)

    monkeypatch.setattr(rapidocr_adapter.importlib, "import_module", fake_import)
    assert RapidOcrBackend.load_outcome() == (None, rapidocr_adapter.MISSING_MODEL)
    for name in FAKE_MODELS:
        (package / "models" / name).write_bytes(b"onnx")
    loaded = RapidOcrBackend.load_outcome()
    assert loaded.backend is not None and loaded.error is None

    class Broken:
        def __init__(self, params: dict[str, object]) -> None:
            raise RuntimeError("C:/Users/secret-user/model.onnx")

    module.RapidOCR = Broken
    assert RapidOcrBackend.load_outcome() == (None, "RuntimeError")
    module.RapidOCR = None
    assert RapidOcrBackend.load_outcome() == (None, rapidocr_adapter.MISSING_FACTORY)

    def no_package(name: str) -> object:
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(rapidocr_adapter.importlib, "import_module", no_package)
    assert RapidOcrBackend.load_outcome() == (None, "ModuleNotFoundError")


# --------------------------------------------------------------------------- #
# Kiểm bổ sung (đột biến còn sống ở vòng review 29/10)
# --------------------------------------------------------------------------- #


def test_charset_warning_counts_only_letters_outside_the_allowed_list(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Thiếu `ơ`, `Ỵ`, `ỵ`: chỉ `ơ` nằm ngoài danh sách cho phép ⇒ cảnh báo đếm đúng 1 chữ."""
    tessdata = _tessdata(tmp_path / "td", [letter for letter in ALL_LETTERS if letter not in {"ơ", "Ỵ", "ỵ"}])
    with caplog.at_level(logging.WARNING):
        assert _probe_with(_settings_in(tessdata)) == NO_VIE
    messages = [record.getMessage() for record in caplog.records]
    assert any("thiếu 1 chữ tiếng Việt ngoài danh sách cho phép" in message for message in messages)


def test_unreadable_traineddata_warning_has_no_path(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    missing = tmp_path / "khong-doc-duoc"
    (missing / "vie.traineddata").mkdir(parents=True)  # thấy file nhưng đọc lỗi (là thư mục)
    with caplog.at_level(logging.WARNING):
        assert _probe_with(_settings_in(missing)) == NO_VIE
    messages = [record.getMessage() for record in caplog.records]
    assert any("không đọc được" in message for message in messages)
    for leaked in (str(tmp_path), tmp_path.name, missing.name):
        assert all(leaked not in message for message in messages)


def _probe_status_with(listing: RunResult) -> ProbeOutcome:
    runner = FakeRunner({"--version": OK_VERSION, "--list-langs": listing})
    return TesseractEngine.probe_status(WITH_SECONDARY, 1.0, runner)


def test_list_langs_not_utf8_means_language_missing_not_engine_missing() -> None:
    listing = RunResult(status="ok", stdout=b'List of available languages in "/x/" (1):\n\xff\xfevie\n')
    assert _probe_status_with(listing).status == "language_missing"
    runner = FakeRunner({"--version": OK_VERSION, "--list-langs": listing})
    assert probe_tesseract(WITH_SECONDARY, CONFIG, runner) == NO_VIE


def test_list_langs_first_line_is_always_the_header() -> None:
    assert _probe_status_with(RunResult(status="ok", stdout=b"vie\neng\n")).status == "language_missing"


def test_list_langs_names_are_stripped(full_tessdata: Path) -> None:
    listing = RunResult(status="ok", stdout=b'List of available languages in "/x/" (2):\n  eng \t\n\tvie  \n')
    assert _probe_status_with(listing).status == "ok"
    runner = FakeRunner({"--version": OK_VERSION, "--list-langs": listing})
    assert probe_tesseract(_settings_in(full_tessdata), CONFIG, runner) == READY


def test_listed_tessdata_dir_rejects_an_empty_directory() -> None:
    assert listed_tessdata_dir('List of available languages in "" (2):') is None


def test_traineddata_cap_has_a_floor_of_1024_bytes() -> None:
    with pytest.raises(ValidationError):
        PdfReaderConfig(ocr_traineddata_max_bytes=1023)
    assert PdfReaderConfig(ocr_traineddata_max_bytes=1024).ocr_traineddata_max_bytes == 1024


def test_allowed_missing_error_lists_the_approved_letters() -> None:
    with pytest.raises(ValidationError) as caught:
        PdfReaderConfig(ocr_charset_allowed_missing=frozenset({"ơ"}))
    assert all(letter in str(caught.value) for letter in OCR_CHARSET_APPROVED_MISSING)


# --------------------------------------------------------------------------- #
# Kiểm nền sau khi mở cổng (duyệt 2026-10-30): `local_check_pending` tới khi kiểm xong
# --------------------------------------------------------------------------- #

PENDING_PAIR = {"ocr": (False, "local_check_pending"), "ocr_cross_check": (False, "local_check_pending")}
PENDING_DETAIL = "Holocron đang kiểm tra OCR trên máy này. Đợi vài giây rồi xem lại."


class HeldLauncher:
    """Cách chạy nền giả: giữ lại việc kiểm, test tự chạy khi muốn (mô phỏng kiểm chưa xong)."""

    def __init__(self) -> None:
        self.works: list[Callable[[], None]] = []

    def __call__(self, work: Callable[[], None]) -> None:
        self.works.append(work)


@pytest.fixture
def install_launcher() -> Iterator[Callable[[LocalCheckLauncher], None]]:
    """Thay cách chạy kiểm lúc khởi động của `app` (mặc định của test là chạy ngay, `conftest.py`)."""
    previous = app.dependency_overrides[get_local_check_launcher]

    def install(launcher: LocalCheckLauncher) -> None:
        app.dependency_overrides[get_local_check_launcher] = lambda: launcher

    yield install
    app.dependency_overrides[get_local_check_launcher] = previous


def _without_ocr(body: dict[str, object]) -> list[object]:
    capabilities = body["capabilities"]
    assert isinstance(capabilities, list)
    return [item for item in capabilities if item["name"] not in ("ocr", "ocr_cross_check")]


def test_runtime_reports_pending_until_the_background_check_finishes(
    install_check: Callable[[LocalCapabilityCheck], None],
    install_launcher: Callable[[LocalCheckLauncher], None],
) -> None:
    check = CountingCheck(WITH_SECONDARY, NO_VIE)
    install_check(check)
    held = HeldLauncher()
    install_launcher(held)
    with TestClient(app) as client:
        pending = client.get("/v1/runtime").json()
        assert _ocr_pair(pending) == PENDING_PAIR
        details = {item["name"]: item["detail_vi"] for item in pending["capabilities"]}
        assert details["ocr"] == details["ocr_cross_check"] == PENDING_DETAIL
        assert check.calls == 0, "lifespan không chờ kiểm"
        [work] = held.works
        work()
        for _ in range(3):
            done = client.get("/v1/runtime").json()
            assert _ocr_pair(done)["ocr"] == (False, "ocr_language_missing")
        assert check.calls == 1
        # Trạng thái đang kiểm chỉ đổi hai khả năng OCR.
        assert _without_ocr(pending) == _without_ocr(done)


def test_lifespan_runs_the_check_once_in_a_real_daemon_thread(
    install_check: Callable[[LocalCapabilityCheck], None],
    install_launcher: Callable[[LocalCheckLauncher], None],
) -> None:
    entered = threading.Event()
    release = threading.Event()
    threads: list[threading.Thread] = []

    def slow_check(config: PdfReaderConfig, /) -> Failures:
        threads.append(threading.current_thread())
        entered.set()
        release.wait(10)
        return {"ocr_cross_check": "secondary_ocr_unavailable"}

    install_check(slow_check)
    install_launcher(runtime.launch_daemon_thread)
    try:
        with TestClient(app) as client:
            assert entered.wait(5)
            assert _ocr_pair(client.get("/v1/runtime").json()) == PENDING_PAIR
            release.set()
            state = getattr(app.state, LOCAL_CHECK_STATE_ATTR)
            assert isinstance(state, LocalCheckState) and state.done.wait(5)
            for _ in range(2):
                assert _ocr_pair(client.get("/v1/runtime").json()) == {
                    "ocr": (True, None),
                    "ocr_cross_check": (False, "secondary_ocr_unavailable"),
                }
    finally:
        release.set()
    [thread] = threads
    assert thread.daemon and thread.name == LOCAL_CHECK_THREAD_NAME and thread is not threading.main_thread()


def test_background_check_error_is_logged_and_reported_as_engine_missing(
    install_check: Callable[[LocalCapabilityCheck], None],
    install_launcher: Callable[[LocalCheckLauncher], None],
    caplog: pytest.LogCaptureFixture,
) -> None:
    def broken(config: PdfReaderConfig, /) -> Failures:
        raise RuntimeError("C:/Users/secret-user/tessdata")

    install_check(broken)
    held = HeldLauncher()
    install_launcher(held)
    with caplog.at_level(logging.WARNING), TestClient(app) as client:
        [work] = held.works
        work()
        state = getattr(app.state, LOCAL_CHECK_STATE_ATTR)
        assert isinstance(state, LocalCheckState) and state.done.is_set()
        assert _ocr_pair(client.get("/v1/runtime").json()) == {
            "ocr": (False, "ocr_engine_missing"),
            "ocr_cross_check": (False, "ocr_engine_missing"),
        }
    assert "RuntimeError" in caplog.text
    assert "secret-user" not in caplog.text


class UnexpectedCheckError(Exception):
    pass


def test_background_check_unexpected_error_still_leaves_the_pending_state(
    install_check: Callable[[LocalCapabilityCheck], None],
    install_launcher: Callable[[LocalCheckLauncher], None],
) -> None:
    """Lỗi ngoài `CHECK_ERRORS` lan ra khỏi luồng nền nhưng kết quả vẫn là `ocr_engine_missing`, không treo pending."""

    def broken(config: PdfReaderConfig, /) -> Failures:
        raise UnexpectedCheckError()

    install_check(broken)
    held = HeldLauncher()
    install_launcher(held)
    with TestClient(app) as client:
        [work] = held.works
        with pytest.raises(UnexpectedCheckError):
            work()
        assert _ocr_pair(client.get("/v1/runtime").json())["ocr"] == (False, "ocr_engine_missing")


def test_check_of_a_previous_startup_never_overwrites_the_next(
    install_check: Callable[[LocalCapabilityCheck], None],
    install_launcher: Callable[[LocalCheckLauncher], None],
) -> None:
    held = HeldLauncher()
    install_launcher(held)
    install_check(lambda _config: {"ocr": "ocr_language_missing", "ocr_cross_check": "ocr_language_missing"})
    with TestClient(app):
        pass  # tắt app khi lần kiểm thứ nhất chưa chạy xong
    install_check(lambda _config: {})
    with TestClient(app) as client:
        stale, fresh = held.works
        stale()
        assert _ocr_pair(client.get("/v1/runtime").json()) == PENDING_PAIR
        fresh()
        assert _ocr_pair(client.get("/v1/runtime").json()) == {"ocr": (True, None), "ocr_cross_check": (True, None)}


def test_local_check_lock_lives_in_app_state_not_in_the_module() -> None:
    lock_type = type(threading.Lock())
    assert not any(isinstance(value, lock_type) for value in vars(runtime).values())
    state = getattr(app.state, LOCAL_CHECK_STATE_ATTR)
    assert isinstance(state, LocalCheckState) and isinstance(state.lock, lock_type)


def test_lazy_check_uses_the_reader_config_of_an_attached_import_runtime(
    install_check: Callable[[LocalCapabilityCheck], None],
) -> None:
    """App chạy không qua `lifespan` mà đã có bộ nhập tài liệu: kiểm lười dùng `reader_config` của bộ nhập đó."""
    check = CountingCheck(WITH_SECONDARY, READY)
    install_check(check)
    config = PdfReaderConfig(ocr_probe_timeout_s=4.5)
    import_runtime = create_import_runtime(None, config)
    setattr(app.state, RUNTIME_STATE_ATTR, import_runtime)
    try:
        TestClient(app).get("/v1/runtime")
    finally:
        delattr(app.state, RUNTIME_STATE_ATTR)
        import_runtime.close()
    assert len(check.configs) == 1 and check.configs[0] is config


# --------------------------------------------------------------------------- #
# Kiểm bổ sung (đột biến còn sống ở vòng review 3)
# --------------------------------------------------------------------------- #


def test_error_name_accepts_64_characters_and_rejects_65() -> None:
    longest = type("E" * 64, (Exception,), {})
    too_long = type("E" * 65, (Exception,), {})
    assert secondary_probe.error_name(longest()) == "E" * 64
    assert secondary_probe.error_name(too_long()) == secondary_probe.UNNAMED_ERROR
    assert SecondaryProbeReport(loaded=False, error="E" * 64).error == "E" * 64
    with pytest.raises(ValidationError):
        SecondaryProbeReport(loaded=False, error="E" * 65)


@pytest.mark.parametrize(
    "request_line",
    [
        '{"mem_bytes": 0, "cpu_seconds": 9}\n',
        '{"mem_bytes": 1073741824, "cpu_seconds": 0}\n',
        '{"mem_bytes": 1073741824, "cpu_seconds": 9, "path": "/x"}\n',
    ],
    ids=["mem-zero", "cpu-zero", "extra-field"],
)
def test_run_probe_rejects_out_of_range_or_extra_fields_before_limits(request_line: str) -> None:
    recorder = ProbeRecorder(_raising(AssertionError("không được nạp")))
    recorder.run(request_line)
    assert recorder.events == ["emit"]
    assert recorder.reports == [SecondaryProbeReport(loaded=False, error=secondary_probe.INVALID_REQUEST)]


def test_run_probe_accepts_the_smallest_limits() -> None:
    recorder = ProbeRecorder(_outcome(BACKEND, None))
    recorder.run('{"mem_bytes": 1, "cpu_seconds": 1}\n')
    assert recorder.events == [("limit", 1, 1), "load", "emit"]


def test_parse_report_rejects_stdout_with_a_non_utf8_line_before_the_report() -> None:
    assert parse_report(b"\xff\xfe rac\n" + _report_line(LOADED)) == SecondaryProbeReport(
        loaded=False, error=secondary_probe.INVALID_REPORT
    )


def test_parse_report_ignores_the_prefix_in_the_middle_of_a_line() -> None:
    failed = SecondaryProbeReport(loaded=False, error="ImportError")
    assert parse_report(_report_line(LOADED) + b"rac " + _report_line(failed)) == LOADED
    assert parse_report(b"rac " + _report_line(LOADED)) == SecondaryProbeReport(
        loaded=False, error=secondary_probe.INVALID_REPORT
    )


FAKE_MODEL_FAILS_THEN_HARD_EXITS = """
import atexit
import os

# Thoát cứng khi tiến trình con kết thúc: bộ đệm stdout không được Python xả hộ.
atexit.register(os._exit, 7)


class ModelLoadFailure(Exception):
    pass


class RapidOCR:
    def __init__(self, params):
        raise ModelLoadFailure("/home/secret-user/models/PP-OCRv6_det_small.onnx")
"""


def test_child_probe_flushes_the_report_before_a_hard_exit(tmp_path: Path) -> None:
    report = _child_probe(_fake_rapidocr(tmp_path, FAKE_MODEL_FAILS_THEN_HARD_EXITS))
    assert report == SecondaryProbeReport(loaded=False, error="ModelLoadFailure")


def test_secondary_prober_receives_the_given_reader_config() -> None:
    seen: list[PdfReaderConfig] = []

    def probe(config: PdfReaderConfig, /) -> SecondaryProbeReport:
        seen.append(config)
        return LOADED

    config = PdfReaderConfig(worker_startup_timeout_s=7.5)
    assert check_local_capabilities(lambda: WITH_SECONDARY, _probe_returning(READY), probe, config) == {}
    assert len(seen) == 1 and seen[0] is config


def test_list_langs_failed_with_a_listing_is_language_missing() -> None:
    failed_with_listing = RunResult(status="failed", stdout=LANGS_WITH_VIE.stdout)
    assert _probe_status_with(failed_with_listing).status == "language_missing"


def test_list_langs_with_a_non_utf8_line_elsewhere_is_language_missing() -> None:
    listing = RunResult(status="ok", stdout=b'List of available languages in "/x/" (2):\nvie\n\xff\xfe\n')
    assert _probe_status_with(listing).status == "language_missing"


@pytest.mark.skipif(sys.platform == "win32", reason="tín hiệu POSIX")
def test_run_bounded_reports_a_child_killed_by_a_signal_as_failed() -> None:
    code = "import os, signal\nos.kill(os.getpid(), signal.SIGTERM)\n"
    result = real_run_bounded([sys.executable, "-c", code], b"", 10.0, 1024)
    assert result.status == "failed"


FAKE_CHECKS_JOB_OBJECT = """
import ctypes
from ctypes import wintypes

EXPECTED_MEMORY_LIMIT = {mem_bytes}


class LimitsNotApplied(Exception):
    pass


class _Basic(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _Io(ctypes.Structure):
    _fields_ = [(n, ctypes.c_uint64) for n in ("R", "W", "O", "RB", "WB", "OB")]


class _Extended(ctypes.Structure):
    _fields_ = [
        ("Basic", _Basic),
        ("Io", _Io),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def _memory_limit_of_own_job():
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
    in_job = wintypes.BOOL()
    if not kernel32.IsProcessInJob(kernel32.GetCurrentProcess(), None, ctypes.byref(in_job)) or not in_job.value:
        return None
    kernel32.QueryInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p
    ]
    info = _Extended()
    if not kernel32.QueryInformationJobObject(None, 9, ctypes.byref(info), ctypes.sizeof(info), None):
        return None
    if not info.Basic.LimitFlags & 0x100:
        return None
    return info.ProcessMemoryLimit


class RapidOCR:
    def __init__(self, params):
        if _memory_limit_of_own_job() != EXPECTED_MEMORY_LIMIT:
            raise LimitsNotApplied()

    def __call__(self, image):
        return None
"""


@pytest.mark.skipif(sys.platform != "win32", reason="Job Object chỉ có trên Windows")
def test_child_probe_runs_inside_its_own_job_object_on_windows(tmp_path: Path) -> None:
    """Tiến trình con thăm dò nằm trong Job Object (IsProcessInJob) có đúng giới hạn RAM đã yêu cầu, trước khi nạp."""
    config = PdfReaderConfig(worker_mem_bytes=768 * 1024 * 1024)
    source = FAKE_CHECKS_JOB_OBJECT.replace("{mem_bytes}", str(config.worker_mem_bytes))
    assert _child_probe(_fake_rapidocr(tmp_path, source), config) == LOADED
