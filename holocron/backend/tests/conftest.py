"""Fixture dùng chung cho test; độc lập với thư mục scripts/."""

from collections.abc import Callable, Iterator, Mapping, Sequence

import pytest
from starlette.types import ASGIApp

from holocron_core.domain.capabilities import CapabilityName, CapabilityReason
from holocron_core.domain.schemas import (
    BlueprintSlot,
    CognitiveLevel,
    DistractorArchetype,
    KnowledgeChunk,
    OptionKey,
    QuestionFormat,
    SelfSolveResult,
)
from holocron_core.infrastructure.document_readers import secondary_probe
from holocron_core.infrastructure.document_readers.config import OcrSettings, PdfReaderConfig
from holocron_core.infrastructure.document_readers.secondary_probe import REPORT_PREFIX, SecondaryProbeReport
from holocron_core.infrastructure.document_readers.tesseract import RunResult
from holocron_core.routers import access, runtime
from holocron_core.routers.access import DEFAULT_ALLOWED_HOSTS, ProviderOverrides, get_allowed_hosts
from holocron_core.routers.api import app
from holocron_core.routers.runtime import (
    LocalCapabilityCheck,
    LocalCheckLauncher,
    clear_local_capability_check,
    get_local_capability_check,
    get_local_check_launcher,
)
from holocron_core.services.capability_checks import OcrProbeResult

THEORY_CHUNK = KnowledgeChunk(
    chunk_id="chk_os05_rr_definition",
    doc_id="doc_os_lecture05_cpu_scheduling",
    subject_code="IT3070",
    chapter_code="ch5.3",
    page_number=14,
    raw_text=(
        "5.3.4 Round Robin. Mỗi tiến trình được cấp CPU tối đa một lượng tử thời gian q. "
        "Hết q mà chưa xong, tiến trình bị ngắt và đưa về cuối hàng đợi sẵn sàng. "
        "Thời gian chờ của tiến trình i: WT_i = CT_i - AT_i - BT_i."
    ),
    verbatim_quote="Hết q mà chưa xong, tiến trình bị ngắt và đưa về cuối hàng đợi sẵn sàng.",
    category="THEORY",
    tags=["#theory", "#cpu_scheduling"],
    topic_keys=["os.cpu_sched.rr.avg_waiting_time"],
    linked_chunk_ids=["chk_os05_rr_example_2"],
    token_count=62,
)

SLOT = BlueprintSlot(
    slot_index=7,
    source_page_number=2,
    format=QuestionFormat.MCQ4,
    chapter_code="ch5.3",
    topic_key="os.cpu_sched.rr.avg_waiting_time",
    cognitive_level=CognitiveLevel.APPLY,
    is_computational=True,
    distractor_archetypes=[
        DistractorArchetype(
            misconception_code="RR_TREATED_AS_FCFS",
            description="Phương án B = kết quả FCFS",
            observed_option_key=OptionKey.B,
            observed_option_text="5.25",
            reproduction_confidence=0.93,
        ),
        DistractorArchetype(
            misconception_code="WAIT_EQUALS_FIRST_RESPONSE",
            description="Phương án C = response time",
            observed_option_key=OptionKey.C,
            observed_option_text="3.00",
            reproduction_confidence=0.88,
        ),
        DistractorArchetype(
            misconception_code="WAIT_CONFUSED_WITH_TURNAROUND",
            description="Phương án D = turnaround trung bình",
            observed_option_key=OptionKey.D,
            observed_option_text="11.50",
            reproduction_confidence=0.91,
        ),
    ],
    self_solve=SelfSolveResult(
        status="MATCHED",
        solved_answer_display="6.75 ms",
        matched_option_key=OptionKey.A,
        solver_confidence=1.0,
        supporting_citations=[THEORY_CHUNK.to_citation()],
        solver_template_id="tpl_os_rr_avg_wait_v1",
    ),
    mapping_confidence=0.91,
    review_status="AUTO_PASS",
)


@pytest.fixture
def theory_chunk() -> KnowledgeChunk:
    return THEORY_CHUNK


@pytest.fixture
def rr_slot() -> BlueprintSlot:
    return SLOT


def _allow_testserver_hosts() -> frozenset[str]:
    return DEFAULT_ALLOWED_HOSTS | {"testserver"}


class _TestserverAccessMiddleware(access.LocalAccessMiddleware):
    """`LocalAccessMiddleware` cho app dựng MỚI trong lúc test (ví dụ nạp lại `routers/api.py` thành module khác).

    App mới có `dependency_overrides` riêng, nên override gắn vào `app` không áp cho nó. Lớp này thêm `testserver` làm
    giá trị mặc định của provider host; override riêng của app mới (nếu có) vẫn thắng.
    """

    def __init__(self, inner_app: ASGIApp, *, overrides: Callable[[], ProviderOverrides]) -> None:
        super().__init__(inner_app, overrides=lambda: {get_allowed_hosts: _allow_testserver_hosts, **overrides()})


@pytest.fixture(autouse=True)
def allow_testserver_host() -> Iterator[None]:
    """`TestClient` gửi `Host: testserver`; thêm tên này vào danh sách host cho phép trong lúc test.

    SPECIFICATION §13.1, §17 D13: danh sách mặc định của code KHÔNG chứa `testserver`. Áp cho `app` qua
    `app.dependency_overrides`; test cần danh sách thật thì bỏ override này
    (`monkeypatch.delitem(app.dependency_overrides, get_allowed_hosts)`). App dựng mới trong lúc test (gọi
    `add_middleware(LocalAccessMiddleware, ...)` sau khi fixture chạy) nhận `_TestserverAccessMiddleware`; `app` đã
    gắn middleware lúc import nên không bị ảnh hưởng. Dùng `MonkeyPatch` riêng, không dùng fixture `monkeypatch`, để
    test gọi `monkeypatch.undo()` giữa chừng không gỡ mất hai thay đổi này.
    """
    with pytest.MonkeyPatch.context() as patch:
        patch.setitem(app.dependency_overrides, get_allowed_hosts, _allow_testserver_hosts)
        patch.setattr(access, "LocalAccessMiddleware", _TestserverAccessMiddleware)
        yield


def _no_local_failures(config: PdfReaderConfig, /) -> dict[CapabilityName, CapabilityReason]:
    """Bộ kiểm cục bộ giả: máy có Tesseract, có `vie`, lượt đọc thứ hai bật và nạp được."""
    return {}


def _fake_local_capability_check() -> LocalCapabilityCheck:
    return _no_local_failures


def _fake_ocr_settings() -> OcrSettings:
    return OcrSettings(tesseract_cmd="tesseract", tessdata_dir=None, secondary="rapidocr")


def _fake_probe(settings: OcrSettings, config: PdfReaderConfig, /) -> OcrProbeResult:
    return OcrProbeResult(engine_runs=True, vie_available=True)


LOADED_REPORT = RunResult(
    status="ok",
    stdout=(REPORT_PREFIX + SecondaryProbeReport(loaded=True, error=None).model_dump_json() + "\n").encode("utf-8"),
)


def _fake_secondary_runner(
    args: Sequence[str],
    stdin_data: bytes,
    timeout_s: float,
    max_output_bytes: int,
    *,
    kill_grace_s: float,
    env: Mapping[str, str] | None = None,
    cwd: str | None = None,
) -> RunResult:
    """Bộ chạy tiến trình con giả của bộ thăm dò RapidOCR: không dựng tiến trình, báo đã nạp được."""
    return LOADED_REPORT


def run_inline(work: Callable[[], None]) -> None:
    """Cách chạy kiểm lúc khởi động cho test: chạy ngay trong `lifespan`, nên kết quả có sẵn trước request đầu tiên."""
    work()


def _inline_launcher() -> LocalCheckLauncher:
    return run_inline


@pytest.fixture(autouse=True)
def fake_local_capability_check() -> Iterator[None]:
    """Mọi `TestClient` dùng bộ kiểm cục bộ giả, không chạy binary Tesseract hay đọc cấu hình OCR của máy chạy test.

    `app` nhận override `get_local_capability_check`. App dựng mới trong test (chỉ gắn vài router, không có override
    của `app`) dùng bộ kiểm mặc định, nên bộ nạp cấu hình OCR và probe mà bộ kiểm mặc định gọi cũng được thay bằng
    bản giả (máy có Tesseract, có `vie`, lượt đọc thứ hai bật); bộ chạy tiến trình con của bộ thăm dò RapidOCR
    (tầng thấp nhất, `secondary_probe.run_bounded`) cũng là bản giả báo "nạp được" (không dựng tiến trình, không
    nạp model thật). Kết quả kiểm lưu ở `app.state` (một lần mỗi lần khởi động); bỏ trước và sau mỗi test để test sau không
    thấy kết quả của test trước. Test cần kết quả khác thì đặt override riêng cho `get_local_capability_check`.
    Kiểm lúc khởi động của `app` chạy ngay trong `lifespan` (override `get_local_check_launcher`) thay vì luồng nền,
    để test không thấy trạng thái `local_check_pending` thoáng qua; test của kiểm nền tự đặt override khác.
    """
    clear_local_capability_check(app)
    with pytest.MonkeyPatch.context() as patch:
        patch.setitem(app.dependency_overrides, get_local_capability_check, _fake_local_capability_check)
        patch.setitem(app.dependency_overrides, get_local_check_launcher, _inline_launcher)
        patch.setattr(runtime, "load_ocr_settings", _fake_ocr_settings)
        patch.setattr(runtime, "probe_tesseract", _fake_probe)
        patch.setattr(secondary_probe, "run_bounded", _fake_secondary_runner)
        yield
    clear_local_capability_check(app)
