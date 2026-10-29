"""Chế độ khả năng (spec v0.6 §5.12, §11.4, đã duyệt 2026-10-25): mỗi khả năng của app kèm `available` và lý do.

Tên khả năng và thứ tự theo `CapabilityName`; mã lý do `CapabilityReason` và `CapabilityError` (detail của 409 khi
endpoint cần một khả năng đang không khả dụng) là đề xuất của spec, chờ duyệt. Không model nào ở đây chứa giá trị
biến môi trường, khoá, tên model hay địa chỉ endpoint.
"""

from __future__ import annotations

from typing import Literal, get_args

from pydantic import model_validator

from .schemas import StrictModel

CapabilityName = Literal[
    "ocr",
    "ocr_cross_check",
    "vault_search",
    "theory_generation",
    "concept_pair_llm_proposal",
    "essay_grading_offline",
    "essay_grading_llm",
    "template_authoring_llm",
    "vlm",
    # Ba tên cho UI mới (đề xuất, chờ duyệt (2026-10-29)): luôn `not_implemented` cho tới khi có code.
    "concept_auto_proposal",
    "line_review_queue",
    "page_progress_eta",
]
CapabilityReason = Literal[
    "llm_not_configured",
    "vlm_not_configured",
    "ocr_engine_missing",
    "ocr_language_missing",
    "secondary_ocr_disabled",
    "not_implemented",
    # Lượt đọc thứ hai được bật mà không nạp được RapidOCR (bản rút gọn không kèm, thiếu gói, model hỏng);
    # duyệt 2026-10-29.
    "secondary_ocr_unavailable",
]

CAPABILITY_NAMES: tuple[CapabilityName, ...] = get_args(CapabilityName)


class Capability(StrictModel):
    name: CapabilityName
    available: bool
    reason: CapabilityReason | None
    detail_vi: str | None

    @model_validator(mode="after")
    def _reason_iff_unavailable(self) -> Capability:
        if (self.reason is None) != self.available:
            raise ValueError("reason là null khi và chỉ khi available = true")
        return self


class CapabilityError(StrictModel):
    """`detail` của 409 khi endpoint cần một khả năng đang không khả dụng (spec v0.6 §6.0, đề xuất)."""

    code: Literal["CAPABILITY_UNAVAILABLE"] = "CAPABILITY_UNAVAILABLE"
    capability: CapabilityName
    reason: CapabilityReason
    message: str
