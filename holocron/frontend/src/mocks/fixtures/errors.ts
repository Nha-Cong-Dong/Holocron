/**
 * Thân lỗi mẫu theo spec §6.0 (M2 của kế hoạch UI), dùng cho test phân loại lỗi và handler giả sau này.
 * `message` chép theo cách backend dựng câu ở nhánh `feat/vault-theory-llm` (`unavailable_error`, `LlmUpstreamError`).
 */

import type { CapabilityError, LlmUpstreamErrorDetail } from "@/types/contract-pending";

import { REASON_DETAILS_VI } from "./runtime";

export const CAPABILITY_ERROR_BODY: { detail: CapabilityError } = {
  detail: {
    code: "CAPABILITY_UNAVAILABLE",
    capability: "concept_pair_llm_proposal",
    reason: "llm_not_configured",
    message: `Khả năng concept_pair_llm_proposal không khả dụng. ${REASON_DETAILS_VI.llm_not_configured}`,
  },
};

export const LLM_UPSTREAM_ERROR_BODY: { detail: LlmUpstreamErrorDetail } = {
  detail: {
    code: "LLM_UPSTREAM_ERROR",
    message: "lỗi không mong đợi khi gửi yêu cầu tới LLM",
  },
};
