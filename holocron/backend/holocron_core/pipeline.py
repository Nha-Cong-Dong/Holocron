"""Điều phối: LLM template -> Sandbox -> TwinQuestion, kèm retry budget & fallback.

Phân loại lỗi (quyết định chi phí retry):
  * ASSERTION_FAILED  -> lỗi của BỘ SỐ, không phải của code: re-roll seed (rẻ, không gọi LLM).
  * AST_REJECTED / RUNTIME_ERROR / CONTRACT_ERROR / TIMEOUT / MEMORY_EXCEEDED / CRASHED
                      -> lỗi của CODE: trừ 1 lượt LLM, gửi feedback có cấu trúc để LLM sửa.
  * Re-roll hết ngân sách seed mà không có seed hợp lệ -> template "ill-posed": trừ 1 lượt LLM.

Hết 3 lượt LLM -> fallback template pre-computed (đã kiểm chứng) cùng topic_key.
Không có fallback hoặc fallback cũng hỏng -> ReviewTicket, slot gắn NEEDS_REVIEW.
"""

from __future__ import annotations

import random
from collections import Counter
from typing import Any, Protocol

from pydantic import BaseModel, Field

from .sandbox import SandboxLimits, SandboxResult, SandboxStatus, run_template
from .schemas import (
    BlueprintSlot,
    Citation,
    GenerationTrace,
    MisconceptionRef,
    NumericalAnswer,
    OptionKey,
    QuestionFormat,
    QuestionTemplate,
    ReviewStatus,
    TemplateProvenance,
    TwinQuestion,
)

LLM_ATTEMPT_BUDGET = 3
SEED_REROLL_BUDGET = 40
SEED_STRIDE = 7_919


class TemplateAuthor(Protocol):
    """Adapter tới LLM. feedback rỗng ở lượt đầu; các lượt sau chứa lỗi có cấu trúc."""

    def author(self, slot: BlueprintSlot, feedback: list[str]) -> QuestionTemplate: ...


class TemplateRegistry(Protocol):
    def get_verified(self, topic_key: str) -> QuestionTemplate | None: ...


class AttemptLog(BaseModel):
    attempt: int
    provenance: TemplateProvenance
    template_sha256: str | None
    outcome: str
    seeds_tried: int
    assertion_histogram: dict[str, int] = Field(default_factory=dict)
    feedback_sent: list[str] = Field(default_factory=list)


class ReviewTicket(BaseModel):
    blueprint_id: str
    slot_index: int
    topic_key: str
    reason: str
    attempts: list[AttemptLog]


class ProductionOutcome(BaseModel):
    twin: TwinQuestion | None = None
    ticket: ReviewTicket | None = None
    attempts: list[AttemptLog]


def _seed_sequence(base_seed: int) -> list[int]:
    return [(base_seed + k * SEED_STRIDE) % (2**63) for k in range(SEED_REROLL_BUDGET)]


def _run_with_rerolls(
    template: QuestionTemplate, base_seed: int, limits: SandboxLimits
) -> tuple[SandboxResult | None, SandboxResult | None, int, Counter[str]]:
    """Trả về (kết_quả_ok, lỗi_code_đầu_tiên, số_seed_đã_thử, histogram assert)."""
    histogram: Counter[str] = Counter()
    tried = 0
    for seed in _seed_sequence(base_seed):
        tried += 1
        result = run_template(template.generator_source, seed, limits)
        if result.status is SandboxStatus.OK:
            return result, None, tried, histogram
        if result.is_seed_problem:
            histogram[result.message or "UNLABELED"] += 1
            continue
        return None, result, tried, histogram
    return None, None, tried, histogram


def _feedback_from_error(err: SandboxResult) -> list[str]:
    if err.status is SandboxStatus.AST_REJECTED:
        return [f"AST_REJECTED: {v}" for v in err.violations]
    where = f" (stage={err.stage}, line={err.line})" if err.stage or err.line else ""
    return [f"{err.status.value.upper()}{where}: {err.message}"]


def _feedback_ill_posed(histogram: Counter[str], tried: int) -> list[str]:
    top = ", ".join(f"{k}×{v}" for k, v in histogram.most_common(3))
    return [
        (
            f"ILL_POSED: 0/{tried} seed vượt qua validate(). Assert hay vi phạm nhất: {top}. "
            "Hãy thu hẹp miền sinh biến trong generate_params() để thoả các assert này thường xuyên hơn; "
            "KHÔNG được xoá assert."
        )
    ]


def build_twin(
    template: QuestionTemplate,
    result: SandboxResult,
    *,
    blueprint_id: str,
    slot_index: int,
    fmt: QuestionFormat,
    citation: Citation,
    llm_attempts: int,
    seed_rerolls: int,
    review_status: ReviewStatus,
) -> TwinQuestion:
    assert result.status is SandboxStatus.OK and result.solution is not None and result.distractors is not None
    labels = {m.misconception_code: m.label_vi for m in template.misconceptions}
    trace = GenerationTrace(
        sandbox_duration_ms=result.duration_ms,
        llm_attempts=llm_attempts,
        seed_rerolls=seed_rerolls,
        template_sha256=template.source_sha256,
        provenance=template.provenance,
    )
    common = {
        "blueprint_id": blueprint_id,
        "slot_index": slot_index,
        "template_id": template.template_id,
        "topic_key": template.topic_key,
        "seed": result.seed,
        "format": fmt,
        "stem": result.stem,
        "solution_explanation_vi": result.solution["explanation_vi"],
        "citation": citation,
        "trace": trace,
        "review_status": review_status,
    }
    if fmt is QuestionFormat.NUMERICAL:
        return TwinQuestion(
            **common,
            numerical_answer=NumericalAnswer(
                value=float(result.solution["value"]),
                tolerance_abs=template.answer.tolerance_abs,
                unit=template.answer.unit,
                decimals=template.answer.decimals,
            ),
        )

    entries: list[tuple[str, dict[str, Any] | None]] = [(result.solution["display"], None)]
    entries += [(d["display"], d) for d in result.distractors]
    shuffle_rng = random.Random(f"{template.source_sha256}:{result.seed}")
    shuffle_rng.shuffle(entries)
    keys = list(OptionKey)
    options: dict[OptionKey, str] = {}
    mis_map: dict[OptionKey, MisconceptionRef] = {}
    correct_key: OptionKey | None = None
    for key, (display, distractor) in zip(keys, entries):
        options[key] = display
        if distractor is None:
            correct_key = key
        else:
            code = distractor["misconception_code"]
            if code not in labels:
                raise ValueError(f"template trả về misconception lạ: {code}")
            mis_map[key] = MisconceptionRef(
                misconception_code=code, label_vi=labels[code], rationale_vi=distractor["rationale_vi"]
            )
    return TwinQuestion(**common, options=options, correct_key=correct_key, distractor_to_misconception_map=mis_map)


def produce_twin(
    slot: BlueprintSlot,
    *,
    blueprint_id: str,
    citation: Citation,
    author: TemplateAuthor,
    registry: TemplateRegistry,
    base_seed: int,
    limits: SandboxLimits | None = None,
) -> ProductionOutcome:
    limits = limits or SandboxLimits()
    attempts: list[AttemptLog] = []

    if slot.non_cloneable:
        return ProductionOutcome(
            ticket=ReviewTicket(
                blueprint_id=blueprint_id,
                slot_index=slot.slot_index,
                topic_key=slot.topic_key,
                reason=f"non_cloneable: {slot.non_cloneable_reason}",
                attempts=[],
            ),
            attempts=[],
        )

    feedback: list[str] = []
    for attempt in range(1, LLM_ATTEMPT_BUDGET + 1):
        try:
            template = author.author(slot, feedback)
        except ValueError as exc:
            feedback = [f"SCHEMA_INVALID: {exc}"]
            attempts.append(
                AttemptLog(
                    attempt=attempt,
                    provenance=TemplateProvenance.LLM_GENERATED,
                    template_sha256=None,
                    outcome="schema_invalid",
                    seeds_tried=0,
                    feedback_sent=feedback,
                )
            )
            continue
        ok, err, tried, hist = _run_with_rerolls(template, base_seed, limits)
        if ok is not None:
            try:
                twin = build_twin(
                    template,
                    ok,
                    blueprint_id=blueprint_id,
                    slot_index=slot.slot_index,
                    fmt=slot.format,
                    citation=citation,
                    llm_attempts=attempt,
                    seed_rerolls=tried - 1,
                    review_status=ReviewStatus.AUTO_PASS,
                )
            except ValueError as exc:
                # Sandbox chạy được nhưng đầu ra vi phạm hợp đồng TwinQuestion (vd. misconception_code
                # không khai báo trong template): lỗi của CODE -> trừ 1 lượt LLM, gửi feedback để sửa.
                feedback = [f"TWIN_ASSEMBLY_FAILED: {exc}"]
                attempts.append(
                    AttemptLog(
                        attempt=attempt,
                        provenance=template.provenance,
                        template_sha256=template.source_sha256,
                        outcome="twin_assembly_failed",
                        seeds_tried=tried,
                        assertion_histogram=dict(hist),
                        feedback_sent=feedback,
                    )
                )
                continue
            attempts.append(
                AttemptLog(
                    attempt=attempt,
                    provenance=template.provenance,
                    template_sha256=template.source_sha256,
                    outcome="ok",
                    seeds_tried=tried,
                    assertion_histogram=dict(hist),
                )
            )
            return ProductionOutcome(twin=twin, attempts=attempts)
        feedback = _feedback_from_error(err) if err is not None else _feedback_ill_posed(hist, tried)
        attempts.append(
            AttemptLog(
                attempt=attempt,
                provenance=template.provenance,
                template_sha256=template.source_sha256,
                outcome=(err.status.value if err is not None else "ill_posed"),
                seeds_tried=tried,
                assertion_histogram=dict(hist),
                feedback_sent=feedback,
            )
        )

    fallback = registry.get_verified(slot.topic_key)
    if fallback is not None:
        ok, err, tried, hist = _run_with_rerolls(fallback, base_seed, limits)
        twin_or_error: TwinQuestion | str | None = None
        if ok is not None:
            try:
                twin_or_error = build_twin(
                    fallback,
                    ok,
                    blueprint_id=blueprint_id,
                    slot_index=slot.slot_index,
                    fmt=slot.format,
                    citation=citation,
                    llm_attempts=LLM_ATTEMPT_BUDGET,
                    seed_rerolls=tried - 1,
                    review_status=ReviewStatus.AUTO_FALLBACK,
                )
            except ValueError as exc:
                twin_or_error = f"TWIN_ASSEMBLY_FAILED: {exc}"
        if isinstance(twin_or_error, TwinQuestion):
            outcome = "ok"
        elif isinstance(twin_or_error, str):
            outcome = "twin_assembly_failed"
        else:
            outcome = err.status.value if err is not None else "ill_posed"
        attempts.append(
            AttemptLog(
                attempt=LLM_ATTEMPT_BUDGET + 1,
                provenance=fallback.provenance,
                template_sha256=fallback.source_sha256,
                outcome=outcome,
                seeds_tried=tried,
                assertion_histogram=dict(hist),
                feedback_sent=[twin_or_error] if isinstance(twin_or_error, str) else [],
            )
        )
        if isinstance(twin_or_error, TwinQuestion):
            return ProductionOutcome(twin=twin_or_error, attempts=attempts)

    return ProductionOutcome(
        ticket=ReviewTicket(
            blueprint_id=blueprint_id,
            slot_index=slot.slot_index,
            topic_key=slot.topic_key,
            reason="hết LLM retry budget và không có/không chạy được template pre-computed",
            attempts=attempts,
        ),
        attempts=attempts,
    )
