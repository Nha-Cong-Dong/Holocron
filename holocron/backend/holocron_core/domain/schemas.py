"""Holocron domain schemas (Pydantic v2).

Mọi payload đi qua FastAPI, lưu DB hoặc trao đổi giữa các bước pipeline
đều phải hợp lệ theo các model ở đây. Validator được viết chặt để lỗi
logic (thiếu phương án, đáp án đúng bị gán misconception, tag sai category...)
bị chặn ngay ở biên hệ thống thay vì lọt xuống UI.
"""

from __future__ import annotations

import ast
import hashlib
import re
import unicodedata
from datetime import UTC, datetime
from enum import Enum
from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    Tag,
    computed_field,
    field_validator,
    model_validator,
)

TOPIC_KEY_PATTERN = r"^[a-z0-9_]+(\.[a-z0-9_]+){2,}$"
CPU_SCHEDULING_TOPIC_PREFIX = "os.cpu_sched."
MISCONCEPTION_CODE_PATTERN = r"^[A-Z][A-Z0-9_]{2,63}$"
TAG_PATTERN = r"^#[a-z0-9_]+$"
SUBJECT_CODE_PATTERN = r"^[A-Z]{2,6}[0-9]{2,4}$"
CHAPTER_CODE_PATTERN = r"^ch[0-9]{1,2}(\.[0-9]{1,2})?$"

SEED_SPACE = 2**63
"""Miền seed của twin và câu lý thuyết: `[0, 2**63)` (SPECIFICATION §6.3, §10)."""

REQUIRED_TEMPLATE_ENTRYPOINTS: tuple[str, ...] = (
    "generate_params",
    "validate",
    "solve",
    "make_distractors",
    "render_stem",
)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:16]}"


def _collapse_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def normalize_quote(text: str) -> str:
    """Dạng so khớp trích dẫn nguyên văn (I3, ADR-006 mục 9): Unicode NFC rồi gộp khoảng trắng.

    Chỉ chuẩn hoá tương đương chính tắc (NFC): không NFKC, không bỏ dấu, không `casefold`.
    """
    return _collapse_ws(unicodedata.normalize("NFC", text))


def quote_in_text(quote: str, text: str) -> bool:
    """`quote` là chuỗi con của `text` sau khi đưa cả hai về NFC và gộp khoảng trắng (I3)."""
    return normalize_quote(quote) in normalize_quote(text)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #


class ChunkCategory(str, Enum):
    THEORY = "THEORY"
    PRACTICE = "PRACTICE"


class SourceKind(str, Enum):
    TEXTBOOK = "textbook"
    LECTURE_SLIDES = "lecture_slides"
    PAST_EXAM = "past_exam"
    LAB_MANUAL = "lab_manual"


class QuestionFormat(str, Enum):
    MCQ4 = "MCQ4"
    NUMERICAL = "NUMERICAL"
    ESSAY = "ESSAY"  # thêm 2026-10-25 (§5.14); chỉ slot lý thuyết mang được, chưa có câu ESSAY nào được sinh


COMPUTATIONAL_FORMATS: frozenset[QuestionFormat] = frozenset({QuestionFormat.MCQ4, QuestionFormat.NUMERICAL})
"""Hình thức của câu tính toán và của `TwinBatchRequest.format` (§6.3: gửi `ESSAY` ⇒ 422)."""
THEORY_FORMATS: frozenset[QuestionFormat] = frozenset({QuestionFormat.MCQ4, QuestionFormat.ESSAY})
"""Hình thức của slot lý thuyết (§5.9 validator `theory`, §5.14)."""


class ExamFormat(str, Enum):
    """Hình thức thi của môn (§5.14): mặc định cho `TheorySlotSpec.format` khi null và để UI hiển thị."""

    MCQ = "mcq"
    ESSAY = "essay"
    MIXED = "mixed"

    def default_theory_format(self) -> QuestionFormat:
        """`mcq` → `MCQ4`, `essay` → `ESSAY`, `mixed` → `MCQ4` (§5.14, §6.7 bước 5)."""
        return QuestionFormat.ESSAY if self is ExamFormat.ESSAY else QuestionFormat.MCQ4


class OptionKey(str, Enum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"


class CognitiveLevel(str, Enum):
    REMEMBER = "remember"
    UNDERSTAND = "understand"
    APPLY = "apply"
    ANALYZE = "analyze"


class ReviewStatus(str, Enum):
    AUTO_PASS = "AUTO_PASS"
    AUTO_FALLBACK = "AUTO_FALLBACK"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class TemplateProvenance(str, Enum):
    LLM_GENERATED = "llm_generated"
    PRECOMPUTED_VERIFIED = "precomputed_verified"


class AssertionPurpose(str, Enum):
    VALIDITY = "validity"
    TIE_BREAKING = "tie_breaking"
    NON_DEGENERACY = "non_degeneracy"
    DISTINCTNESS = "distinctness"


# --------------------------------------------------------------------------- #
# Citation & Knowledge Vault
# --------------------------------------------------------------------------- #


class Citation(StrictModel):
    """Deep Checkpoint: client dùng PDF.js `find` với verbatim_quote trên page_number."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    doc_id: str = Field(min_length=3, max_length=128)
    page_number: int = Field(ge=1)
    verbatim_quote: str = Field(min_length=8, max_length=600)
    chunk_id: str | None = None

    @field_validator("verbatim_quote")
    @classmethod
    def _single_line_quote(cls, v: str) -> str:
        return _collapse_ws(v)


class DocumentMeta(StrictModel):
    doc_id: str = Field(min_length=3, max_length=128)
    subject_code: str = Field(pattern=r"^[A-Z]{2,6}[0-9]{2,4}$")
    title: str = Field(min_length=1, max_length=300)
    source_kind: SourceKind
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    page_count: int = Field(ge=1)
    has_text_layer: bool
    uploaded_at: datetime = Field(default_factory=_utcnow)


class KnowledgeChunk(StrictModel):
    chunk_id: str = Field(default_factory=lambda: _new_id("chk"))
    doc_id: str = Field(min_length=3, max_length=128)
    subject_code: str = Field(pattern=r"^[A-Z]{2,6}[0-9]{2,4}$")
    chapter_code: str = Field(pattern=r"^ch[0-9]{1,2}(\.[0-9]{1,2})?$")
    page_number: int = Field(ge=1)
    page_end: int | None = Field(default=None, ge=1)
    raw_text: str = Field(min_length=1)
    verbatim_quote: str = Field(min_length=8, max_length=600)
    category: ChunkCategory
    tags: list[str] = Field(default_factory=list)
    topic_keys: list[str] = Field(default_factory=list)
    linked_chunk_ids: list[str] = Field(default_factory=list)
    token_count: int = Field(ge=1)
    embedding_model: str | None = None
    # §5.8: nhãn (chương, loại, topic) do LLM hoặc code đề xuất, người dùng xác nhận qua `PATCH …/labels` (§6.6).
    label_status: Literal["proposed", "confirmed"] = "proposed"
    mapping_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    version: int = Field(default=1, ge=1)

    @field_validator("tags")
    @classmethod
    def _tags_format(cls, v: list[str]) -> list[str]:
        for tag in v:
            if not re.match(TAG_PATTERN, tag):
                raise ValueError(f"tag không hợp lệ: {tag!r}")
        return sorted(set(v))

    @field_validator("topic_keys")
    @classmethod
    def _topic_keys_format(cls, v: list[str]) -> list[str]:
        for key in v:
            if not re.match(TOPIC_KEY_PATTERN, key):
                raise ValueError(f"topic_key không hợp lệ: {key!r}")
        return sorted(set(v))

    @model_validator(mode="after")
    def _consistency(self) -> KnowledgeChunk:
        required_tag = "#theory" if self.category is ChunkCategory.THEORY else "#practice_example"
        forbidden_tag = "#practice_example" if self.category is ChunkCategory.THEORY else "#theory"
        if required_tag not in self.tags:
            self.tags = sorted(set(self.tags) | {required_tag})
        if forbidden_tag in self.tags:
            raise ValueError(f"chunk {self.category.value} không được mang tag {forbidden_tag}")
        if self.chunk_id in self.linked_chunk_ids:
            raise ValueError("linked_chunk_ids không được trỏ về chính nó")
        if self.page_end is not None and self.page_end < self.page_number:
            raise ValueError("page_end < page_number")
        if not quote_in_text(self.verbatim_quote, self.raw_text):
            raise ValueError("verbatim_quote phải là chuỗi con (đã chuẩn hoá NFC và khoảng trắng) của raw_text")
        return self

    def to_citation(self) -> Citation:
        return Citation(
            doc_id=self.doc_id,
            page_number=self.page_number,
            verbatim_quote=self.verbatim_quote,
            chunk_id=self.chunk_id,
        )


# --------------------------------------------------------------------------- #
# Exam Blueprint
# --------------------------------------------------------------------------- #


class DistractorArchetype(StrictModel):
    misconception_code: str = Field(pattern=MISCONCEPTION_CODE_PATTERN)
    description: str = Field(min_length=5, max_length=400)
    observed_option_key: OptionKey | None = None
    observed_option_text: str | None = None
    reproduction_confidence: float = Field(ge=0.0, le=1.0)


class SolverInputs(BaseModel):
    """Tham số đề bài nằm ngoài bảng (vd. time quantum của Round Robin).

    Field được phép vắng mặt (gate sẽ báo BLOCKING "chưa thể tự giải"), nhưng nếu client gửi
    tường minh thì phải đúng kiểu — `null` bị từ chối ngay ở tầng validate (HTTP 422).
    """

    model_config = ConfigDict(extra="allow")

    quantum: int | None = Field(default=None, gt=0)

    @field_validator("quantum", mode="before")
    @classmethod
    def _reject_explicit_null(cls, v: object) -> object:
        if v is None:
            raise ValueError("quantum không được là null; bỏ trống field nếu chưa có giá trị")
        return v


class SchedulingAlgorithm(str, Enum):
    RR = "RR"
    SJF = "SJF"


MAX_PROCESSES = 10


class ProcessItem(StrictModel):
    """Một dòng của bảng tiến trình sau khi đã qua Ingestion Gate."""

    name: str = Field(min_length=1, max_length=32)
    arrival_time: int = Field(ge=0, le=200)
    burst_time: int = Field(ge=1, le=100)


class CPUSchedulingProblem(StrictModel):
    """Đề CPU Scheduling đã chuẩn hoá — đầu vào duy nhất mà sandbox `solve()` nhận cho nhóm topic này."""

    algorithm: SchedulingAlgorithm
    processes: list[ProcessItem] = Field(min_length=1, max_length=MAX_PROCESSES)
    quantum: int | None = Field(default=None, ge=1, le=20)

    @model_validator(mode="after")
    def _rules(self) -> CPUSchedulingProblem:
        if self.algorithm is SchedulingAlgorithm.RR and self.quantum is None:
            raise ValueError("Round Robin bắt buộc có quantum")
        if self.algorithm is SchedulingAlgorithm.SJF and self.quantum is not None:
            raise ValueError("SJF không dùng quantum")
        names = [_collapse_ws(p.name).lower() for p in self.processes]
        if len(names) != len(set(names)):
            raise ValueError("tên tiến trình bị trùng")
        return self

    def to_template_params(self) -> dict[str, list[str] | list[int] | int]:
        """Dạng params mà Executable Template của nhóm CPU Scheduling nhận (xem SPECIFICATION §8)."""
        params: dict[str, list[str] | list[int] | int] = {
            "names": [p.name for p in self.processes],
            "arrivals": [p.arrival_time for p in self.processes],
            "bursts": [p.burst_time for p in self.processes],
        }
        if self.quantum is not None:
            params["quantum"] = self.quantum
        return params


class SelfSolveResult(StrictModel):
    status: Literal["MATCHED", "NO_MATCH", "AMBIGUOUS", "SKIPPED"]
    solved_answer_display: str | None = None
    matched_option_key: OptionKey | None = None
    solver_confidence: float = Field(ge=0.0, le=1.0)
    supporting_citations: list[Citation] = Field(default_factory=list)
    solver_template_id: str | None = None

    @model_validator(mode="after")
    def _status_consistency(self) -> SelfSolveResult:
        if self.status == "MATCHED" and self.matched_option_key is None:
            raise ValueError("MATCHED yêu cầu matched_option_key")
        if self.status != "MATCHED" and self.matched_option_key is not None:
            raise ValueError(f"{self.status} không được có matched_option_key")
        if self.status in ("MATCHED", "NO_MATCH") and not self.solved_answer_display:
            raise ValueError(f"{self.status} yêu cầu solved_answer_display")
        return self


class BlueprintSlot(StrictModel):
    slot_index: int = Field(ge=1)
    source_page_number: int = Field(ge=1)
    format: QuestionFormat
    chapter_code: str = Field(pattern=r"^ch[0-9]{1,2}(\.[0-9]{1,2})?$")
    topic_key: str = Field(pattern=TOPIC_KEY_PATTERN)
    cognitive_level: CognitiveLevel
    is_computational: bool
    non_cloneable: bool = False
    non_cloneable_reason: str | None = None
    distractor_archetypes: list[DistractorArchetype] = Field(default_factory=list, max_length=3)
    self_solve: SelfSolveResult
    mapping_confidence: float = Field(ge=0.0, le=1.0)
    review_status: ReviewStatus = ReviewStatus.NEEDS_REVIEW

    @model_validator(mode="after")
    def _rules(self) -> BlueprintSlot:
        if self.non_cloneable and not self.non_cloneable_reason:
            raise ValueError("non_cloneable=True bắt buộc có non_cloneable_reason")
        if not self.non_cloneable and self.non_cloneable_reason:
            raise ValueError("non_cloneable_reason chỉ dùng khi non_cloneable=True")
        if self.format is QuestionFormat.NUMERICAL and self.distractor_archetypes:
            raise ValueError("câu NUMERICAL không có distractor_archetypes")
        codes = [a.misconception_code for a in self.distractor_archetypes]
        if len(codes) != len(set(codes)):
            raise ValueError("misconception_code bị trùng trong cùng một slot")
        if self.self_solve.status in ("NO_MATCH", "AMBIGUOUS") and self.review_status is ReviewStatus.AUTO_PASS:
            raise ValueError("self-solve không khớp thì không được AUTO_PASS")
        return self


class TheorySlot(StrictModel):
    """Slot lý thuyết của blueprint, dựng từ `TheorySlotSpec` (§5.9; quyết định 14(b) ngày 2026-10-28).

    Model riêng thay cho `BlueprintSlot.question_kind = "theory"` của §5.9 (nợ ADR-004, chuyển sang phương án (a) sau
    29/10): `BlueprintSlot` giữ nguyên để JSON `examples/` không đổi. Có đủ các field mà computed field của
    `ExamBlueprint` đọc trên slot (`chapter_code`, `format`, `non_cloneable`, `mapping_confidence`). `is_computational`
    luôn `False` và là khoá phân biệt với `BlueprintSlot` trong `ExamBlueprint.slots`.
    """

    slot_index: int = Field(ge=1)
    is_computational: Literal[False] = False
    question_kind: Literal["theory"] = "theory"
    chapter_code: str = Field(pattern=CHAPTER_CODE_PATTERN)  # TheorySlotSpec.chapter_code (người dùng)
    source_chunk_id: str | None = Field(default=None, min_length=1, max_length=128)  # ngoài §5.9
    format: QuestionFormat  # TheorySlotSpec.format, hoặc mặc định theo Subject.exam_format
    topic_key: str | None = None  # §5.9: null với slot lý thuyết
    cognitive_level: CognitiveLevel = CognitiveLevel.REMEMBER  # TheorySlotSpec.cognitive_level
    non_cloneable: Literal[False] = False
    mapping_confidence: float = Field(default=1.0, ge=0.0, le=1.0)  # người dùng chọn chương
    review_status: ReviewStatus = ReviewStatus.NEEDS_REVIEW

    @model_validator(mode="after")
    def _theory_rules(self) -> TheorySlot:
        if self.format not in THEORY_FORMATS:
            raise ValueError("slot lý thuyết chỉ nhận format MCQ4 hoặc ESSAY")
        if self.cognitive_level not in (CognitiveLevel.REMEMBER, CognitiveLevel.UNDERSTAND):
            raise ValueError("slot lý thuyết chỉ nhận cognitive_level remember hoặc understand")
        return self


def _slot_kind(value: Any) -> str:
    """Khoá phân biệt của `ExamBlueprint.slots`: `is_computational is False` ⇒ `"theory"`, còn lại `"computational"`.

    Nhận cả dict (khi validate JSON) lẫn object (khi dựng bằng code); thiếu `is_computational` thì coi là tính toán.
    """
    flag = value.get("is_computational") if isinstance(value, dict) else getattr(value, "is_computational", None)
    return "theory" if flag is False else "computational"


ExamBlueprintSlot = Annotated[
    Annotated[BlueprintSlot, Tag("computational")] | Annotated[TheorySlot, Tag("theory")],
    Discriminator(_slot_kind),
]


class ExamBlueprint(StrictModel):
    blueprint_id: str = Field(default_factory=lambda: _new_id("bp"))
    source_exam_doc_id: str | None = Field(default=None, min_length=3, max_length=128)
    subject_code: str = Field(pattern=SUBJECT_CODE_PATTERN)
    total_questions: int = Field(ge=1, le=200)
    duration_minutes: int | None = Field(default=None, ge=5, le=300)
    slots: list[ExamBlueprintSlot]
    created_at: datetime = Field(default_factory=_utcnow)

    def _chapter_distribution(self) -> dict[str, int]:
        dist: dict[str, int] = {}
        for s in self.slots:
            dist[s.chapter_code] = dist.get(s.chapter_code, 0) + 1
        return dict(sorted(dist.items()))

    chapter_distribution = computed_field(property(_chapter_distribution))

    def _format_distribution(self) -> dict[str, int]:
        dist: dict[str, int] = {}
        for s in self.slots:
            dist[s.format.value] = dist.get(s.format.value, 0) + 1
        return dict(sorted(dist.items()))

    format_distribution = computed_field(property(_format_distribution))

    def _cloneable_ratio(self) -> float:
        return round(sum(1 for s in self.slots if not s.non_cloneable) / len(self.slots), 4)

    cloneable_ratio = computed_field(property(_cloneable_ratio))

    def _mean_mapping_confidence(self) -> float:
        return round(sum(s.mapping_confidence for s in self.slots) / len(self.slots), 4)

    mean_mapping_confidence = computed_field(property(_mean_mapping_confidence))

    @model_validator(mode="after")
    def _slots_match_total(self) -> ExamBlueprint:
        if len(self.slots) != self.total_questions:
            raise ValueError(f"total_questions={self.total_questions} nhưng có {len(self.slots)} slot")
        indices = sorted(s.slot_index for s in self.slots)
        if indices != list(range(1, self.total_questions + 1)):
            raise ValueError("slot_index phải liên tục 1..N, không trùng")
        return self

    @model_validator(mode="after")
    def _source_exam_doc_rule(self) -> ExamBlueprint:
        """`source_exam_doc_id = null` chỉ khi blueprint không có slot tính toán (§5.9, duyệt 2026-10-26 chiều)."""
        if self.source_exam_doc_id is None and any(isinstance(s, BlueprintSlot) for s in self.slots):
            raise ValueError("blueprint có slot tính toán phải có source_exam_doc_id")
        return self


class TheorySlotSpec(StrictModel):
    """Một slot lý thuyết người dùng yêu cầu ở `POST /v1/blueprints` (§5.9, duyệt 2026-10-26)."""

    chapter_code: str = Field(pattern=CHAPTER_CODE_PATTERN)
    cognitive_level: Literal["remember", "understand"] = "remember"
    source_chunk_id: str | None = Field(default=None, min_length=1, max_length=128)
    format: Literal["MCQ4", "ESSAY"] | None = None  # null ⇒ mặc định theo Subject.exam_format (§5.14)


MAX_BLUEPRINT_ITEMS = 50
MAX_THEORY_SLOTS = 50


class BlueprintCreate(StrictModel):
    """Body của `POST /v1/blueprints` (§5.9, §6.7): ≤ 50 item và ≤ 50 slot lý thuyết."""

    subject_code: str = Field(pattern=SUBJECT_CODE_PATTERN)
    item_ids: list[str] = Field(default_factory=list, max_length=MAX_BLUEPRINT_ITEMS)
    theory_slots: list[TheorySlotSpec] = Field(default_factory=list, max_length=MAX_THEORY_SLOTS)
    duration_minutes: int | None = Field(default=None, ge=5, le=300)

    @model_validator(mode="after")
    def _rules(self) -> BlueprintCreate:
        if len(set(self.item_ids)) != len(self.item_ids):
            raise ValueError("item_ids không được trùng")
        if not self.item_ids and not self.theory_slots:
            raise ValueError("item_ids và theory_slots không được cùng rỗng")
        return self


class BlueprintSummary(StrictModel):
    """Một dòng của `GET /v1/blueprints` (§6.7, F18)."""

    blueprint_id: str
    subject_code: str
    total_questions: int
    created_at: datetime
    batch_count: int = Field(ge=0)  # code: số batch twin đã lưu của blueprint


SUBJECT_DISPLAY_NAME_MAX_CHARS = 200


class Subject(StrictModel):
    """Môn học (§5.14, duyệt 2026-10-25): nơi lưu `exam_format`; `version` cho optimistic locking (§6.8)."""

    subject_code: str = Field(pattern=SUBJECT_CODE_PATTERN)
    display_name: str = Field(min_length=1, max_length=SUBJECT_DISPLAY_NAME_MAX_CHARS)
    exam_format: ExamFormat = ExamFormat.MIXED
    version: int = Field(default=1, ge=1)
    created_at: datetime
    updated_at: datetime

    @classmethod
    def implicit(cls, subject_code: str, now: datetime) -> Subject:
        """Môn tạo ngầm khi nhập tài liệu hay tạo blueprint gặp môn chưa có (§5.14): `display_name = subject_code`,
        `exam_format = mixed`."""
        return cls(
            subject_code=subject_code,
            display_name=subject_code,
            exam_format=ExamFormat.MIXED,
            created_at=now,
            updated_at=now,
        )


# --------------------------------------------------------------------------- #
# Question Template (Executable Template)
# --------------------------------------------------------------------------- #


class TemplateVariable(StrictModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    kind: Literal["int", "float", "list_int", "choice_int", "str"]
    min_value: float | None = None
    max_value: float | None = None
    length_min: int | None = Field(default=None, ge=1)
    length_max: int | None = Field(default=None, ge=1)
    choices: list[int] | None = None
    description: str = Field(min_length=3, max_length=300)

    @model_validator(mode="after")
    def _domain(self) -> TemplateVariable:
        if self.min_value is not None and self.max_value is not None and self.min_value > self.max_value:
            raise ValueError(f"{self.name}: min_value > max_value")
        if self.kind == "choice_int" and not self.choices:
            raise ValueError(f"{self.name}: choice_int cần choices")
        if self.kind == "list_int" and (self.length_min is None or self.length_max is None):
            raise ValueError(f"{self.name}: list_int cần length_min/length_max")
        return self


class TemplateAssertion(StrictModel):
    assert_id: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,47}$")
    purpose: AssertionPurpose
    description: str = Field(min_length=5, max_length=300)


class AnswerSpec(StrictModel):
    unit: str | None = Field(default=None, max_length=16)
    decimals: int = Field(ge=0, le=6)
    tolerance_abs: float = Field(ge=0.0)
    min_option_gap: float = Field(gt=0.0)


class MisconceptionSpec(StrictModel):
    misconception_code: str = Field(pattern=MISCONCEPTION_CODE_PATTERN)
    label_vi: str = Field(min_length=3, max_length=120)
    explanation_vi: str = Field(min_length=10, max_length=600)


class QuestionTemplate(StrictModel):
    template_id: str = Field(default_factory=lambda: _new_id("tpl"))
    topic_key: str = Field(pattern=TOPIC_KEY_PATTERN)
    version: int = Field(default=1, ge=1)
    runtime: Literal["python3.12"] = "python3.12"
    provenance: TemplateProvenance
    generator_source: str = Field(min_length=50, max_length=20_000)
    entrypoints: tuple[str, ...] = REQUIRED_TEMPLATE_ENTRYPOINTS
    solve_signature: Literal["def solve(params: dict) -> dict"] = "def solve(params: dict) -> dict"
    variables: list[TemplateVariable] = Field(min_length=1)
    assertions: list[TemplateAssertion] = Field(min_length=1)
    answer: AnswerSpec
    misconceptions: list[MisconceptionSpec] = Field(min_length=3, max_length=3)
    theory_chunk_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_utcnow)

    def _source_sha256(self) -> str:
        return hashlib.sha256(self.generator_source.encode("utf-8")).hexdigest()

    source_sha256 = computed_field(property(_source_sha256))

    @field_validator("generator_source")
    @classmethod
    def _must_define_entrypoints(cls, src: str) -> str:
        try:
            tree = ast.parse(src)
        except SyntaxError as exc:
            raise ValueError(f"generator_source lỗi cú pháp: dòng {exc.lineno}: {exc.msg}") from exc
        defined = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
        missing = [e for e in REQUIRED_TEMPLATE_ENTRYPOINTS if e not in defined]
        if missing:
            raise ValueError(f"generator_source thiếu hàm top-level: {missing}")
        has_assert = any(isinstance(n, ast.Assert) for n in ast.walk(tree))
        if not has_assert:
            raise ValueError("generator_source phải chứa ít nhất một câu lệnh assert")
        return src

    @model_validator(mode="after")
    def _unique_codes(self) -> QuestionTemplate:
        codes = [m.misconception_code for m in self.misconceptions]
        if len(set(codes)) != 3:
            raise ValueError("3 misconception phải có mã khác nhau")
        ids = [a.assert_id for a in self.assertions]
        if len(ids) != len(set(ids)):
            raise ValueError("assert_id bị trùng")
        return self


# --------------------------------------------------------------------------- #
# Twin Question (output)
# --------------------------------------------------------------------------- #


class MisconceptionRef(StrictModel):
    misconception_code: str = Field(pattern=MISCONCEPTION_CODE_PATTERN)
    label_vi: str = Field(min_length=3, max_length=120)
    rationale_vi: str = Field(min_length=10, max_length=600)


class NumericalAnswer(StrictModel):
    value: float
    tolerance_abs: float = Field(ge=0.0)
    unit: str | None = None
    decimals: int = Field(ge=0, le=6)


class GenerationTrace(StrictModel):
    sandbox_duration_ms: int = Field(ge=0)
    llm_attempts: int = Field(ge=0, le=3)
    seed_rerolls: int = Field(ge=0)
    template_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    provenance: TemplateProvenance


class GanttSegment(StrictModel):
    """Một đoạn CPU chạy một tiến trình trong `[start, end)`; do sandbox sinh (I1), không tính lại ở nơi khác."""

    process: str = Field(min_length=1, max_length=32)
    start: int = Field(ge=0)
    end: int

    @model_validator(mode="after")
    def _end_after_start(self) -> GanttSegment:
        if self.end <= self.start:
            raise ValueError("gantt: end phải lớn hơn start")
        return self


class TwinQuestion(StrictModel):
    twin_id: str = Field(default_factory=lambda: _new_id("twin"))
    blueprint_id: str
    slot_index: int = Field(ge=1)
    template_id: str
    topic_key: str = Field(pattern=TOPIC_KEY_PATTERN)
    seed: int = Field(ge=0, lt=2**63)
    format: QuestionFormat
    stem: str = Field(min_length=20)
    options: dict[OptionKey, str] | None = None
    correct_key: OptionKey | None = None
    numerical_answer: NumericalAnswer | None = None
    distractor_to_misconception_map: dict[OptionKey, MisconceptionRef] = Field(default_factory=dict)
    solution_explanation_vi: str = Field(min_length=10)
    citation: Citation
    trace: GenerationTrace
    review_status: ReviewStatus
    generated_at: datetime = Field(default_factory=_utcnow)
    gantt: list[GanttSegment] = Field(default_factory=list)

    @model_validator(mode="after")
    def _gantt_rules(self) -> TwinQuestion:
        if self.topic_key.startswith(CPU_SCHEDULING_TOPIC_PREFIX) and not self.gantt:
            raise ValueError("gantt không được rỗng với topic CPU Scheduling")
        for previous, current in zip(self.gantt, self.gantt[1:], strict=False):
            if current.start < previous.start:
                raise ValueError("gantt phải sắp theo start")
            if current.start < previous.end:
                raise ValueError("các đoạn gantt không được chồng lấn")
        return self

    @model_validator(mode="after")
    def _format_rules(self) -> TwinQuestion:
        if self.format is QuestionFormat.MCQ4:
            if self.options is None or set(self.options) != set(OptionKey):
                raise ValueError("MCQ4 yêu cầu đủ 4 options A-D")
            texts = [_collapse_ws(t) for t in self.options.values()]
            if len(set(texts)) != 4:
                raise ValueError("4 options phải khác nhau")
            if self.correct_key is None:
                raise ValueError("MCQ4 yêu cầu correct_key")
            if self.correct_key in self.distractor_to_misconception_map:
                raise ValueError("correct_key không được gán misconception")
            expected = set(OptionKey) - {self.correct_key}
            if set(self.distractor_to_misconception_map) != expected:
                raise ValueError("distractor_to_misconception_map phải phủ đúng 3 phương án sai")
            if self.numerical_answer is not None:
                raise ValueError("MCQ4 không dùng numerical_answer")
        elif self.format is QuestionFormat.ESSAY:
            raise ValueError("câu tính toán không có hình thức ESSAY")
        else:
            if self.options is not None or self.correct_key is not None:
                raise ValueError("NUMERICAL không có options/correct_key")
            if self.numerical_answer is None:
                raise ValueError("NUMERICAL yêu cầu numerical_answer")
        return self
