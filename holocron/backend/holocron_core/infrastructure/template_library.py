"""Kho template pre-computed đã kiểm chứng (fallback khi LLM hết retry budget)."""

from __future__ import annotations

from importlib.resources import files

from ..domain.schemas import (
    AnswerSpec,
    AssertionPurpose,
    MisconceptionSpec,
    QuestionTemplate,
    TemplateAssertion,
    TemplateProvenance,
    TemplateVariable,
)

RR_TOPIC = "os.cpu_sched.rr.avg_waiting_time"
SJF_TOPIC = "os.cpu_sched.sjf.avg_waiting_time"


def load_template_source(filename: str) -> str:
    return files("holocron_core.infrastructure.templates").joinpath(filename).read_text(encoding="utf-8")


def rr_avg_waiting_template(provenance: TemplateProvenance) -> QuestionTemplate:
    return QuestionTemplate(
        template_id="tpl_os_rr_avg_wait_v1",
        topic_key=RR_TOPIC,
        version=1,
        provenance=provenance,
        generator_source=load_template_source("os_rr_avg_waiting.py"),
        variables=[
            TemplateVariable(name="n", kind="int", min_value=4, max_value=5, description="Số tiến trình"),
            TemplateVariable(name="quantum", kind="choice_int", choices=[2, 3, 4], description="Time quantum (ms)"),
            TemplateVariable(
                name="arrivals",
                kind="list_int",
                min_value=0,
                max_value=12,
                length_min=4,
                length_max=5,
                description="Thời điểm đến, tăng ngặt, bắt đầu từ 0, bước 1..3",
            ),
            TemplateVariable(
                name="bursts",
                kind="list_int",
                min_value=2,
                max_value=9,
                length_min=4,
                length_max=5,
                description="CPU burst (ms)",
            ),
        ],
        assertions=[
            TemplateAssertion(
                assert_id="VALIDITY_PROCESS_COUNT", purpose=AssertionPurpose.VALIDITY, description="4 ≤ n ≤ 5"
            ),
            TemplateAssertion(
                assert_id="VALIDITY_START_AT_ZERO",
                purpose=AssertionPurpose.VALIDITY,
                description="Tiến trình đầu tiên đến tại t=0",
            ),
            TemplateAssertion(
                assert_id="TIE_SIMULTANEOUS_ARRIVAL",
                purpose=AssertionPurpose.TIE_BREAKING,
                description="Không có hai tiến trình đến cùng lúc (tránh phụ thuộc thứ tự PID)",
            ),
            TemplateAssertion(
                assert_id="TIE_ARRIVAL_AT_PREEMPTION",
                purpose=AssertionPurpose.TIE_BREAKING,
                description="Không tiến trình nào đến đúng thời điểm một tiến trình khác hết quantum "
                "(loại bỏ tranh cãi quy ước thứ tự vào ready queue)",
            ),
            TemplateAssertion(
                assert_id="NONDEG_CPU_IDLE",
                purpose=AssertionPurpose.NON_DEGENERACY,
                description="CPU không rảnh giữa chừng",
            ),
            TemplateAssertion(
                assert_id="NONDEG_TOO_FEW_PREEMPTIONS",
                purpose=AssertionPurpose.NON_DEGENERACY,
                description="Ít nhất 2 lần preemption để RR khác FCFS thực sự",
            ),
            TemplateAssertion(
                assert_id="NONDEG_NO_REWAIT",
                purpose=AssertionPurpose.NON_DEGENERACY,
                description="Ít nhất 1 tiến trình phải chờ lại sau khi bị ngắt",
            ),
            TemplateAssertion(
                assert_id="DISTINCT_OPTION_GAP",
                purpose=AssertionPurpose.DISTINCTNESS,
                description="4 giá trị phương án đôi một cách nhau ≥ 0.25 ms sau làm tròn",
            ),
        ],
        answer=AnswerSpec(unit="ms", decimals=2, tolerance_abs=0.01, min_option_gap=0.25),
        misconceptions=[
            MisconceptionSpec(
                misconception_code="RR_TREATED_AS_FCFS",
                label_vi="Coi Round Robin như FCFS",
                explanation_vi="Bỏ qua time quantum, cho mỗi tiến trình chạy hết burst theo thứ tự đến.",
            ),
            MisconceptionSpec(
                misconception_code="WAIT_EQUALS_FIRST_RESPONSE",
                label_vi="Nhầm waiting time với response time",
                explanation_vi="Chỉ đếm thời gian chờ trước lần chạy đầu tiên, bỏ qua các lần chờ lại.",
            ),
            MisconceptionSpec(
                misconception_code="WAIT_CONFUSED_WITH_TURNAROUND",
                label_vi="Nhầm waiting time với turnaround time",
                explanation_vi="Tính CT − AT nhưng quên trừ burst time.",
            ),
        ],
        theory_chunk_ids=["chk_os05_rr_definition"],
    )


def sjf_avg_waiting_template(provenance: TemplateProvenance) -> QuestionTemplate:
    return QuestionTemplate(
        template_id="tpl_os_sjf_avg_wait_v1",
        topic_key=SJF_TOPIC,
        version=1,
        provenance=provenance,
        generator_source=load_template_source("os_sjf_avg_waiting.py"),
        variables=[
            TemplateVariable(name="n", kind="int", min_value=4, max_value=5, description="Số tiến trình"),
            TemplateVariable(
                name="arrivals",
                kind="list_int",
                min_value=0,
                max_value=12,
                length_min=4,
                length_max=5,
                description="Thời điểm đến, tăng ngặt, bắt đầu từ 0, bước 1..3",
            ),
            TemplateVariable(
                name="bursts",
                kind="list_int",
                min_value=2,
                max_value=9,
                length_min=4,
                length_max=5,
                description="CPU burst (ms)",
            ),
        ],
        assertions=[
            TemplateAssertion(
                assert_id="VALIDITY_PROCESS_COUNT", purpose=AssertionPurpose.VALIDITY, description="4 ≤ n ≤ 5"
            ),
            TemplateAssertion(
                assert_id="VALIDITY_SHAPE",
                purpose=AssertionPurpose.VALIDITY,
                description="Số tên, thời điểm đến và burst bằng nhau",
            ),
            TemplateAssertion(
                assert_id="VALIDITY_POSITIVE_BURST", purpose=AssertionPurpose.VALIDITY, description="Mọi burst ≥ 1"
            ),
            TemplateAssertion(
                assert_id="VALIDITY_START_AT_ZERO",
                purpose=AssertionPurpose.VALIDITY,
                description="Tiến trình đầu tiên đến tại t=0",
            ),
            TemplateAssertion(
                assert_id="TIE_EQUAL_BURST_READY",
                purpose=AssertionPurpose.TIE_BREAKING,
                description="Tại mỗi lần chọn, các tiến trình sẵn sàng có burst đôi một khác nhau "
                "(loại bỏ tranh cãi quy ước khi hòa burst)",
            ),
            TemplateAssertion(
                assert_id="NONDEG_CPU_IDLE",
                purpose=AssertionPurpose.NON_DEGENERACY,
                description="CPU không rảnh giữa chừng",
            ),
            TemplateAssertion(
                assert_id="NONDEG_NO_OVERTAKE",
                purpose=AssertionPurpose.NON_DEGENERACY,
                description="Thứ tự chạy khác thứ tự đến: có tiến trình ngắn hơn chen lên trước",
            ),
            TemplateAssertion(
                assert_id="NONDEG_ARRIVAL_ORDER_MATTERS",
                purpose=AssertionPurpose.NON_DEGENERACY,
                description="Thứ tự chạy khác thứ tự sắp theo burst thuần, để thời điểm đến thực sự ảnh hưởng",
            ),
            TemplateAssertion(
                assert_id="NONDEG_TIMELINE_TOO_LONG",
                purpose=AssertionPurpose.NON_DEGENERACY,
                description="Mọi tiến trình hoàn thành trước mốc 40 ms",
            ),
            TemplateAssertion(
                assert_id="DISTINCT_OPTION_VALUES",
                purpose=AssertionPurpose.DISTINCTNESS,
                description="4 giá trị phương án đôi một khác nhau sau làm tròn",
            ),
            TemplateAssertion(
                assert_id="DISTINCT_OPTION_GAP",
                purpose=AssertionPurpose.DISTINCTNESS,
                description="4 giá trị phương án đôi một cách nhau ≥ 0.25 ms sau làm tròn",
            ),
        ],
        answer=AnswerSpec(unit="ms", decimals=2, tolerance_abs=0.01, min_option_gap=0.25),
        misconceptions=[
            MisconceptionSpec(
                misconception_code="WAIT_CONFUSED_WITH_TURNAROUND",
                label_vi="Nhầm waiting time với turnaround time",
                explanation_vi="Tính CT − AT nhưng quên trừ burst time.",
            ),
            MisconceptionSpec(
                misconception_code="SJF_IGNORES_ARRIVAL",
                label_vi="Bỏ qua thời điểm đến khi sắp theo burst",
                explanation_vi="Sắp mọi tiến trình theo burst tăng dần như thể tất cả đến lúc 0.",
            ),
            MisconceptionSpec(
                misconception_code="SJF_COMPUTED_AS_SRTF",
                label_vi="Tính SJF không ưu tiên như SRTF",
                explanation_vi="Cho phép ngắt tiến trình đang chạy khi có tiến trình ngắn hơn đến.",
            ),
        ],
    )


class InMemoryTemplateRegistry:
    def __init__(self) -> None:
        verified = (
            rr_avg_waiting_template(TemplateProvenance.PRECOMPUTED_VERIFIED),
            sjf_avg_waiting_template(TemplateProvenance.PRECOMPUTED_VERIFIED),
        )
        self._by_topic: dict[str, QuestionTemplate] = {template.topic_key: template for template in verified}

    def get_verified(self, topic_key: str) -> QuestionTemplate | None:
        return self._by_topic.get(topic_key)
