"""Kiểm cục bộ của chế độ khả năng (SPECIFICATION §5.12, §11.4): Tesseract, gói `vie`, lượt đọc thứ hai.

Không chạy binary Tesseract thật: probe và bộ chạy lệnh đều là bản giả, nên kết quả không phụ thuộc máy chạy test.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator, Sequence

import pytest
from fastapi.testclient import TestClient

from holocron_core.domain.capabilities import CapabilityName, CapabilityReason
from holocron_core.infrastructure.document_readers.config import (
    ENV_PDF_OCR_PRIMARY,
    OcrSettings,
    PdfReaderConfig,
)
from holocron_core.infrastructure.document_readers.tesseract import RunResult
from holocron_core.infrastructure.llm_config import ConfigError, load_runtime_config
from holocron_core.routers.api import app
from holocron_core.routers.deps import get_runtime_config
from holocron_core.routers.runtime import LocalCapabilityCheck, get_local_capability_check
from holocron_core.services.capabilities import IMPLEMENTED_CAPABILITIES
from holocron_core.services.capability_checks import (
    OcrProbeResult,
    check_local_capabilities,
    local_capability_failures,
    probe_tesseract,
)

WITH_SECONDARY = OcrSettings(tesseract_cmd="/opt/tesseract", tessdata_dir="/opt/tessdata", secondary="rapidocr")
WITHOUT_SECONDARY = OcrSettings(tesseract_cmd="/opt/tesseract", tessdata_dir=None, secondary=None)
READY = OcrProbeResult(engine_runs=True, vie_available=True)
NO_BINARY = OcrProbeResult(engine_runs=False, vie_available=False)
NO_VIE = OcrProbeResult(engine_runs=True, vie_available=False)
LLM_ENV = {
    "HOLOCRON_LLM_BASE_URL": "https://gateway.example.test/v1",
    "HOLOCRON_LLM_API_KEY": "sk-test-0123456789-must-never-leak",
    "HOLOCRON_LLM_MODEL_EXTRACT": "secret-model-name",
}

Failures = dict[CapabilityName, CapabilityReason]


class CountingCheck:
    """Bộ kiểm cục bộ đếm số lần chạy; mỗi lần chạy gọi `check_local_capabilities` với cấu hình và probe giả."""

    def __init__(self, settings: OcrSettings, probe_result: OcrProbeResult) -> None:
        self.settings = settings
        self.probe_result = probe_result
        self.calls = 0

    def _probe(self, settings: OcrSettings, /) -> OcrProbeResult:
        assert settings is self.settings
        return self.probe_result

    def __call__(self) -> Failures:
        self.calls += 1
        return check_local_capabilities(lambda: self.settings, self._probe)


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
    assert local_capability_failures(probe_result, settings) == expected


def test_invalid_ocr_config_reports_engine_missing_without_probing(caplog: pytest.LogCaptureFixture) -> None:
    bad_value = "khong-phai-tesseract"

    def load() -> OcrSettings:
        raise ConfigError(f"{ENV_PDF_OCR_PRIMARY} không hợp lệ: MVP chỉ nhận 'tesseract'")

    def probe(settings: OcrSettings, /) -> OcrProbeResult:
        raise AssertionError("không được probe khi cấu hình OCR sai")

    with caplog.at_level(logging.WARNING):
        failures = check_local_capabilities(load, probe)
    assert failures == {"ocr": "ocr_engine_missing", "ocr_cross_check": "ocr_engine_missing"}
    assert ENV_PDF_OCR_PRIMARY in caplog.text and bad_value not in caplog.text


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
    results: dict[str, RunResult], expected: OcrProbeResult
) -> None:
    assert probe_tesseract(WITH_SECONDARY, run=FakeRunner(results)) == expected


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

    def check() -> Failures:
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
