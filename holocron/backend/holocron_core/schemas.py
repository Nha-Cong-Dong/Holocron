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
from datetime import UTC, datetime
from enum import Enum
from typing import Literal
from uuid import uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
    model_validator,
)

TOPIC_KEY_PATTERN = r"^[a-z0-9_]+(\.[a-z0-9_]+){2,}$"
MISCONCEPTION_CODE_PATTERN = r"^[A-Z][A-Z0-9_]{2,63}$"
TAG_PATTERN = r"^#[a-z0-9_]+$"

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
        if _collapse_ws(self.verbatim_quote) not in _collapse_ws(self.raw_text):
            raise ValueError("verbatim_quote phải là chuỗi con (đã chuẩn hoá khoảng trắng) của raw_text")
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


class ExamBlueprint(StrictModel):
    blueprint_id: str = Field(default_factory=lambda: _new_id("bp"))
    source_exam_doc_id: str = Field(min_length=3, max_length=128)
    subject_code: str = Field(pattern=r"^[A-Z]{2,6}[0-9]{2,4}$")
    total_questions: int = Field(ge=1, le=200)
    duration_minutes: int | None = Field(default=None, ge=5, le=300)
    slots: list[BlueprintSlot]
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
        else:
            if self.options is not None or self.correct_key is not None:
                raise ValueError("NUMERICAL không có options/correct_key")
            if self.numerical_answer is None:
                raise ValueError("NUMERICAL yêu cầu numerical_answer")
        return self
