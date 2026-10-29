"""Kiểm cục bộ của chế độ khả năng (SPECIFICATION §5.12, §11.4): Tesseract, gói `vie`, lượt đọc thứ hai.

Không chạy binary Tesseract hay nạp RapidOCR thật: probe, bộ chạy lệnh và bộ nạp lượt thứ hai đều là bản giả, nên
kết quả không phụ thuộc máy chạy test.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator, Mapping, Sequence
from types import MappingProxyType

import pytest
from fastapi.testclient import TestClient

from holocron_core.domain.capabilities import Capability, CapabilityName, CapabilityReason
from holocron_core.infrastructure.document_readers.config import (
    ENV_PDF_OCR_PRIMARY,
    ENV_PDF_OCR_SECONDARY,
    OcrSettings,
    PdfReaderConfig,
)
from holocron_core.infrastructure.document_readers.tesseract import RunResult
from holocron_core.infrastructure.llm_config import ConfigError, RuntimeConfig, load_runtime_config
from holocron_core.routers import blueprints, runtime
from holocron_core.routers.api import app
from holocron_core.routers.deps import get_runtime_config
from holocron_core.routers.documents import RUNTIME_STATE_ATTR
from holocron_core.routers.runtime import (
    LOCAL_FAILURES_STATE_ATTR,
    LocalCapabilityCheck,
    get_local_capability_check,
)
from holocron_core.services import capability_checks
from holocron_core.services.capabilities import IMPLEMENTED_CAPABILITIES
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
CONFIG = PdfReaderConfig()
LLM_ENV = {
    "HOLOCRON_LLM_BASE_URL": "https://gateway.example.test/v1",
    "HOLOCRON_LLM_API_KEY": "sk-test-0123456789-must-never-leak",
    "HOLOCRON_LLM_MODEL_EXTRACT": "secret-model-name",
}

Failures = dict[CapabilityName, CapabilityReason]


def _loaded() -> object | None:
    return object()


def _not_loaded() -> object | None:
    return None


def _must_not_load() -> object | None:
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
        (READY, {"ocr_cross_check": "secondary_ocr_disabled"}),
        (NO_BINARY, {"ocr": "ocr_engine_missing", "ocr_cross_check": "ocr_engine_missing"}),
        (NO_VIE, {"ocr": "ocr_language_missing", "ocr_cross_check": "ocr_language_missing"}),
    ],
    ids=["ready", "no-binary", "no-vie"],
)
def test_secondary_not_loaded_disables_cross_check(probe_result: OcrProbeResult, expected: Failures) -> None:
    assert local_capability_failures(probe_result, WITH_SECONDARY, secondary_loaded=False) == expected


class BrokenRapidOcr(OSError):
    """Lỗi giả khi thiếu DLL lúc import gói nhị phân (lọt ra ngoài `RapidOcrBackend.load`)."""


def _raises_on_load() -> object | None:
    raise BrokenRapidOcr("onnxruntime DLL hỏng")


def _import_breaks() -> object | None:
    raise ImportError("gói rapidocr hỏng giữa chừng")


@pytest.mark.parametrize(
    ("loader", "expected"),
    [
        (_loaded, {}),
        (_not_loaded, {"ocr_cross_check": "secondary_ocr_disabled"}),
        (_raises_on_load, {"ocr_cross_check": "secondary_ocr_disabled"}),
        (_import_breaks, {"ocr_cross_check": "secondary_ocr_disabled"}),
    ],
    ids=["loads", "returns-none", "raises-oserror", "raises-importerror"],
)
def test_rapidocr_must_load_for_cross_check(loader: Callable[[], object | None], expected: Failures) -> None:
    calls: list[int] = []

    def counting_loader() -> object | None:
        calls.append(1)
        return loader()

    failures = check_local_capabilities(lambda: WITH_SECONDARY, _probe_returning(READY), counting_loader, CONFIG)
    assert failures == expected
    assert len(calls) == 1


def test_rapidocr_load_failure_logs_only_the_error_type(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        assert secondary_ocr_loads(_raises_on_load) is False
    assert "BrokenRapidOcr" in caplog.text and "DLL" not in caplog.text


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
    assert probe_tesseract(WITH_SECONDARY, CONFIG, run=FakeRunner(results)) == expected


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


def test_probe_tesseract_default_runner_uses_reader_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """`run=None` ⇒ `run_bounded` với `kill_grace_s`, timeout và giới hạn byte lấy từ cấu hình của app."""
    config = PdfReaderConfig(ocr_probe_timeout_s=3.25, ocr_probe_max_bytes=2048, ocr_kill_grace_s=1.75)
    recorded: list[tuple[str, float, int, float]] = []

    def fake_run_bounded(
        args: Sequence[str], stdin_data: bytes, timeout_s: float, max_output_bytes: int, *, kill_grace_s: float
    ) -> RunResult:
        recorded.append((args[1], timeout_s, max_output_bytes, kill_grace_s))
        return {"--version": OK_VERSION, "--list-langs": LANGS_WITH_VIE}[args[1]]

    monkeypatch.setattr(capability_checks, "run_bounded", fake_run_bounded)
    assert probe_tesseract(WITH_SECONDARY, config) == READY
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
    monkeypatch.setattr(runtime, "load_secondary_ocr", _must_not_load)
    config = PdfReaderConfig(ocr_probe_timeout_s=4.0)
    failures = get_local_capability_check()(config)
    assert failures == {"ocr": "ocr_language_missing", "ocr_cross_check": "ocr_language_missing"}
    assert len(seen) == 1 and seen[0] is config


def test_default_check_loads_rapidocr_through_the_runtime_loader(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime, "load_ocr_settings", lambda: WITH_SECONDARY)
    monkeypatch.setattr(runtime, "probe_tesseract", _probe_returning(READY))
    monkeypatch.setattr(runtime, "load_secondary_ocr", _not_loaded)
    assert get_local_capability_check()(CONFIG) == {"ocr_cross_check": "secondary_ocr_disabled"}
    monkeypatch.setattr(runtime, "load_secondary_ocr", _raises_on_load)
    assert get_local_capability_check()(CONFIG) == {"ocr_cross_check": "secondary_ocr_disabled"}
    monkeypatch.setattr(runtime, "load_secondary_ocr", _loaded)
    assert get_local_capability_check()(CONFIG) == {}


def test_default_check_reports_engine_missing_on_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def load() -> OcrSettings:
        raise ConfigError(f"{ENV_PDF_OCR_PRIMARY} không hợp lệ")

    def probe(settings: OcrSettings, config: PdfReaderConfig, /) -> OcrProbeResult:
        raise AssertionError("không được probe khi cấu hình OCR sai")

    monkeypatch.setattr(runtime, "load_ocr_settings", load)
    monkeypatch.setattr(runtime, "probe_tesseract", probe)
    monkeypatch.setattr(runtime, "load_secondary_ocr", _must_not_load)
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
