"""Tính danh sách khả năng của app (spec v0.6 §5.12, §11.4) từ cấu hình và kết quả kiểm cục bộ; không gọi mạng.

LLM "đã cấu hình" khi đủ ba biến `HOLOCRON_LLM_BASE_URL`, `HOLOCRON_LLM_API_KEY`, `HOLOCRON_LLM_MODEL_EXTRACT`
(`RuntimeConfig.llm_configured`, spec v0.6 §11.2; không có nhà cung cấp mặc định). LLM đã cấu hình mà gateway đang lỗi
vẫn là `available = true`; lỗi lúc gọi trả 502 (§6.0). Cấu hình có biến sai (khoá có khoảng trắng, base URL không phải
https…) được `routers/deps.get_runtime_config` nạp như chưa cấu hình, nên khả năng tương ứng có lý do
`llm_not_configured` / `vlm_not_configured` thay vì làm request lỗi 500. Chưa nối vào `GET /v1/runtime` (việc đó sửa
`routers/runtime.py` và `tests/test_llm_config.py` theo §17 D12, làm ở một thay đổi riêng).

Thứ tự xét lý do cho mỗi khả năng: chưa có code trong bản đang chạy (`not_implemented`) → thiếu cấu hình LLM/VLM →
lỗi kiểm cục bộ do tầng gọi cấp (binary Tesseract, unicharset, lượt đọc thứ hai) → khả dụng.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping

from ..domain.capabilities import CAPABILITY_NAMES, Capability, CapabilityError, CapabilityName, CapabilityReason
from ..infrastructure.llm_config import RuntimeConfig

# Khả năng đã có code trên nhánh này. Bộ nhập PDF (OCR), chấm tự luận, `TemplateAuthor` thật và adapter VLM chưa có.
IMPLEMENTED_CAPABILITIES: frozenset[CapabilityName] = frozenset(
    {"vault_search", "theory_generation", "concept_pair_llm_proposal"}
)
LLM_CAPABILITIES: frozenset[CapabilityName] = frozenset(
    {"concept_pair_llm_proposal", "essay_grading_llm", "template_authoring_llm"}
)
VLM_CAPABILITIES: frozenset[CapabilityName] = frozenset({"vlm"})

REASON_DETAILS_VI: dict[CapabilityReason, str] = {
    "llm_not_configured": (
        "Chưa cấu hình LLM: đặt đủ ba biến môi trường HOLOCRON_LLM_BASE_URL, HOLOCRON_LLM_API_KEY và "
        "HOLOCRON_LLM_MODEL_EXTRACT cho backend rồi khởi động lại."
    ),
    "vlm_not_configured": (
        "Chưa cấu hình VLM: đặt đủ HOLOCRON_VLM_BASE_URL, HOLOCRON_VLM_API_KEY và HOLOCRON_VLM_MODEL "
        "rồi khởi động lại backend."
    ),
    "ocr_engine_missing": "Không chạy được Tesseract: cài Tesseract hoặc đặt HOLOCRON_TESSERACT_CMD trỏ tới file chạy.",
    "ocr_language_missing": (
        "Thiếu dữ liệu tiếng Việt cho Tesseract: thêm vie.traineddata vào thư mục tessdata hoặc đặt "
        "HOLOCRON_TESSDATA_DIR."
    ),
    "secondary_ocr_disabled": "Lượt đọc thứ hai đang tắt vì HOLOCRON_PDF_OCR_SECONDARY để trống.",
    "not_implemented": "Bản đang chạy chưa có khả năng này.",
}


def _reason(
    name: CapabilityName,
    config: RuntimeConfig,
    implemented: Collection[CapabilityName],
    local_failures: Mapping[CapabilityName, CapabilityReason],
) -> CapabilityReason | None:
    if name not in implemented:
        return "not_implemented"
    if name in LLM_CAPABILITIES and not config.llm_configured:
        return "llm_not_configured"
    if name in VLM_CAPABILITIES and config.vlm is None:
        return "vlm_not_configured"
    return local_failures.get(name)


def compute_capabilities(
    config: RuntimeConfig,
    *,
    implemented: Collection[CapabilityName] = IMPLEMENTED_CAPABILITIES,
    local_failures: Mapping[CapabilityName, CapabilityReason] | None = None,
) -> list[Capability]:
    """Đủ mọi khả năng, theo thứ tự khai báo của `CapabilityName`.

    `local_failures`: lý do không khả dụng mà tầng gọi đã kiểm cục bộ lúc khởi động (ví dụ `"ocr"` →
    `"ocr_engine_missing"`); chỉ dùng khi khả năng đã có code và đủ cấu hình.
    """
    failures = local_failures or {}
    capabilities: list[Capability] = []
    for name in CAPABILITY_NAMES:
        reason = _reason(name, config, implemented, failures)
        capabilities.append(
            Capability(
                name=name,
                available=reason is None,
                reason=reason,
                detail_vi=None if reason is None else REASON_DETAILS_VI[reason],
            )
        )
    return capabilities


def unavailable_error(capabilities: Collection[Capability], name: CapabilityName) -> CapabilityError | None:
    """`None` khi khả năng `name` khả dụng; ngược lại `detail` của 409 (chỉ nêu tên biến, không nêu giá trị)."""
    capability = next(capability for capability in capabilities if capability.name == name)
    if capability.reason is None:
        return None
    return CapabilityError(
        capability=name,
        reason=capability.reason,
        message=f"Khả năng {name} không khả dụng. {REASON_DETAILS_VI[capability.reason]}",
    )
