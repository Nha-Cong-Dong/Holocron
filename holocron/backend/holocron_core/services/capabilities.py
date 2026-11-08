"""Tính danh sách khả năng của app (spec v0.6 §5.12, §11.4) từ cấu hình và kết quả kiểm cục bộ; không gọi mạng.

LLM "đã cấu hình" khi đủ ba biến `HOLOCRON_LLM_BASE_URL`, `HOLOCRON_LLM_API_KEY`, `HOLOCRON_LLM_MODEL_EXTRACT`
(`RuntimeConfig.llm_configured`, spec v0.6 §11.2; không có nhà cung cấp mặc định). LLM đã cấu hình mà gateway đang lỗi
vẫn là `available = true`; lỗi lúc gọi trả 502 (§6.0). Cấu hình có biến sai (khoá có khoảng trắng, base URL không phải
https…) được `routers/deps.get_runtime_config` nạp như chưa cấu hình, nên khả năng tương ứng có lý do
`llm_not_configured` / `vlm_not_configured` thay vì làm request lỗi 500. Đã nối vào `GET /v1/runtime`
(`routers/runtime.py`, trường `capabilities`).

Thứ tự xét lý do cho mỗi khả năng: chưa có code trong bản đang chạy (`not_implemented`) → thiếu cấu hình LLM/VLM →
lỗi kiểm cục bộ do tầng gọi cấp (binary Tesseract, gói `vie`, lượt đọc thứ hai tắt hoặc không nạp được; `services/capability_checks.py`;
hoặc `local_check_pending` khi lần kiểm chạy nền sau khi mở cổng chưa xong) → khả dụng.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping

from ..domain.capabilities import CAPABILITY_NAMES, Capability, CapabilityError, CapabilityName, CapabilityReason
from ..infrastructure.llm_config import RuntimeConfig

# Khả năng đã có code trên nhánh này. OCR đã có: bộ nhập PDF đọc bằng Tesseract (`ocr`) và lượt đọc thứ hai RapidOCR
# (`ocr_cross_check`); máy thiếu binary, thiếu `vie`, tắt lượt thứ hai hay không nạp được RapidOCR được báo qua
# `local_failures` (`services/capability_checks.py`, kiểm một lần lúc khởi động). Chấm tự luận, `TemplateAuthor` thật và adapter VLM
# chưa có.
IMPLEMENTED_CAPABILITIES: frozenset[CapabilityName] = frozenset(
    {"ocr", "ocr_cross_check", "vault_search", "theory_generation", "concept_pair_llm_proposal"}
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
    "secondary_ocr_unavailable": (
        "Không nạp được lượt đọc thứ hai (RapidOCR) trên máy này. Bản rút gọn không kèm RapidOCR: dùng bản đầy đủ. "
        "Nếu đang dùng bản đầy đủ, cài lại ứng dụng."
    ),
    "local_check_pending": "Holocron đang kiểm tra OCR trên máy này. Đợi vài giây rồi xem lại.",
}


def _reason(
    name: CapabilityName,
    configured: tuple[bool, bool],
    implemented: Collection[CapabilityName],
    local_failures: Mapping[CapabilityName, CapabilityReason],
) -> CapabilityReason | None:
    llm_configured, vlm_configured = configured
    if name not in implemented:
        return "not_implemented"
    if name in LLM_CAPABILITIES and not llm_configured:
        return "llm_not_configured"
    if name in VLM_CAPABILITIES and not vlm_configured:
        return "vlm_not_configured"
    return local_failures.get(name)


def compute_capabilities(
    config: RuntimeConfig,
    *,
    implemented: Collection[CapabilityName] = IMPLEMENTED_CAPABILITIES,
    local_failures: Mapping[CapabilityName, CapabilityReason] | None = None,
    llm_configured: bool | None = None,
    vlm_configured: bool | None = None,
) -> list[Capability]:
    """Đủ mọi khả năng, theo thứ tự khai báo của `CapabilityName`.

    `local_failures`: lý do không khả dụng mà tầng gọi đã kiểm cục bộ lúc khởi động (ví dụ `"ocr"` →
    `"ocr_engine_missing"`); chỉ dùng khi khả năng đã có code và đủ cấu hình.
    `llm_configured` / `vlm_configured`: cấu hình theo nguồn hợp nhất env + nhà cung cấp người dùng (ADR-009 mục
    12.11), do `GET /v1/runtime` tính; `None` ⇒ chỉ theo biến môi trường trong `config`.
    """
    failures = local_failures or {}
    configured = (
        config.llm_configured if llm_configured is None else llm_configured,
        config.vlm is not None if vlm_configured is None else vlm_configured,
    )
    capabilities: list[Capability] = []
    for name in CAPABILITY_NAMES:
        reason = _reason(name, configured, implemented, failures)
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
