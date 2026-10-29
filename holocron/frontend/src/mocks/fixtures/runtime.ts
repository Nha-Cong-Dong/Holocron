/**
 * Dữ liệu mẫu cho `GET /v1/runtime` theo spec §5.12 (M1 của kế hoạch UI): ba kịch bản dựng sẵn.
 * `detail_vi` chép nguyên văn `REASON_DETAILS_VI` của `services/capabilities.py` (nhánh `feat/vault-theory-llm`).
 * Backend đã trả `capabilities` (12 phần tử); gỡ cùng thư mục `src/mocks/`.
 */

import type { Capability, CapabilityName, CapabilityReason, RuntimeInfoPending } from "@/types/contract-pending";

export const REASON_DETAILS_VI: Record<CapabilityReason, string> = {
  llm_not_configured:
    "Chưa cấu hình LLM: đặt đủ ba biến môi trường HOLOCRON_LLM_BASE_URL, HOLOCRON_LLM_API_KEY và " +
    "HOLOCRON_LLM_MODEL_EXTRACT cho backend rồi khởi động lại.",
  vlm_not_configured:
    "Chưa cấu hình VLM: đặt đủ HOLOCRON_VLM_BASE_URL, HOLOCRON_VLM_API_KEY và HOLOCRON_VLM_MODEL " +
    "rồi khởi động lại backend.",
  ocr_engine_missing: "Không chạy được Tesseract: cài Tesseract hoặc đặt HOLOCRON_TESSERACT_CMD trỏ tới file chạy.",
  ocr_language_missing:
    "Thiếu dữ liệu tiếng Việt cho Tesseract: thêm vie.traineddata vào thư mục tessdata hoặc đặt " +
    "HOLOCRON_TESSDATA_DIR.",
  secondary_ocr_disabled: "Lượt đọc thứ hai đang tắt vì HOLOCRON_PDF_OCR_SECONDARY để trống.",
  secondary_ocr_unavailable:
    "Không nạp được lượt đọc thứ hai (RapidOCR) trên máy này. Bản rút gọn không kèm RapidOCR: dùng bản đầy đủ. " +
    "Nếu đang dùng bản đầy đủ, cài lại ứng dụng.",
  not_implemented: "Bản đang chạy chưa có khả năng này.",
};

function available(name: CapabilityName): Capability {
  return { name, available: true, reason: null, detail_vi: null };
}

function unavailable(name: CapabilityName, reason: CapabilityReason): Capability {
  return { name, available: false, reason, detail_vi: REASON_DETAILS_VI[reason] };
}

export type RuntimeScenario = "offline" | "llm" | "no_tesseract";

export const RUNTIME_SCENARIOS: readonly RuntimeScenario[] = ["offline", "llm", "no_tesseract"];

/** Chạy offline, không có LLM: mọi khả năng không cần LLM đều khả dụng. */
const offline: RuntimeInfoPending = {
  demo_mode: true,
  llm_configured: false,
  vlm_configured: false,
  mock_extractor: true,
  app_version: "0.0.0-mock",
  commit: "0000000",
  log_dir: null,
  capabilities: [
    available("ocr"),
    available("ocr_cross_check"),
    available("vault_search"),
    available("theory_generation"),
    unavailable("concept_pair_llm_proposal", "llm_not_configured"),
    available("essay_grading_offline"),
    unavailable("essay_grading_llm", "not_implemented"),
    unavailable("template_authoring_llm", "llm_not_configured"),
    unavailable("vlm", "vlm_not_configured"),
    unavailable("concept_auto_proposal", "not_implemented"),
    unavailable("line_review_queue", "not_implemented"),
    unavailable("page_progress_eta", "not_implemented"),
  ],
};

/** Đã cấu hình LLM; VLM vẫn đang park. */
const llm: RuntimeInfoPending = {
  demo_mode: false,
  llm_configured: true,
  vlm_configured: false,
  mock_extractor: true,
  app_version: "0.0.0-mock",
  commit: "0000000",
  log_dir: null,
  capabilities: [
    available("ocr"),
    available("ocr_cross_check"),
    available("vault_search"),
    available("theory_generation"),
    available("concept_pair_llm_proposal"),
    available("essay_grading_offline"),
    unavailable("essay_grading_llm", "not_implemented"),
    available("template_authoring_llm"),
    unavailable("vlm", "vlm_not_configured"),
    unavailable("concept_auto_proposal", "not_implemented"),
    unavailable("line_review_queue", "not_implemented"),
    unavailable("page_progress_eta", "not_implemented"),
  ],
};

/** Đã cấu hình LLM nhưng máy chưa cài Tesseract và lượt đọc thứ hai đang tắt. */
const noTesseract: RuntimeInfoPending = {
  demo_mode: false,
  llm_configured: true,
  vlm_configured: false,
  mock_extractor: true,
  app_version: "0.0.0-mock",
  commit: "0000000",
  log_dir: null,
  capabilities: [
    unavailable("ocr", "ocr_engine_missing"),
    unavailable("ocr_cross_check", "secondary_ocr_disabled"),
    available("vault_search"),
    available("theory_generation"),
    available("concept_pair_llm_proposal"),
    available("essay_grading_offline"),
    unavailable("essay_grading_llm", "not_implemented"),
    available("template_authoring_llm"),
    unavailable("vlm", "vlm_not_configured"),
    unavailable("concept_auto_proposal", "not_implemented"),
    unavailable("line_review_queue", "not_implemented"),
    unavailable("page_progress_eta", "not_implemented"),
  ],
};

export const RUNTIME_FIXTURES: Record<RuntimeScenario, RuntimeInfoPending> = {
  offline,
  llm,
  no_tesseract: noTesseract,
};
