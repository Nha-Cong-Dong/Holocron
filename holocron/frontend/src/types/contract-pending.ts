/**
 * Kiểu của contract đã có trong SPECIFICATION.md (v0.7) nhưng CHƯA có trong backend của nhánh này.
 * `types/holocron.ts` chỉ chứa kiểu khớp 1:1 với Pydantic đang chạy; khi backend triển khai một model dưới đây,
 * chuyển kiểu đó sang `holocron.ts` trong cùng PR và xoá khỏi file này (kế hoạch UI mục 3.2).
 * Mỗi kiểu ghi mục spec và trạng thái duyệt.
 */

import type { RuntimeInfo } from "@/types/holocron";

// --------------------------------------------------------------------------------- §5.12 Capability

/**
 * Tên khả năng, đúng thứ tự khai báo `CapabilityName` (§5.12, đã duyệt 2026-10-25).
 * Đã có trong backend ở nhánh `feat/vault-theory-llm` (`domain/capabilities.py`), chưa nối vào `/v1/runtime`.
 */
export const CAPABILITY_NAMES = [
  "ocr",
  "ocr_cross_check",
  "vault_search",
  "theory_generation",
  "concept_pair_llm_proposal",
  "essay_grading_offline",
  "essay_grading_llm",
  "template_authoring_llm",
  "vlm",
] as const;

export type CapabilityName = (typeof CAPABILITY_NAMES)[number];

/** Mã lý do không khả dụng (§5.12; danh sách mã còn đề xuất, chờ duyệt). */
export const CAPABILITY_REASONS = [
  "llm_not_configured",
  "vlm_not_configured",
  "ocr_engine_missing",
  "ocr_language_missing",
  "secondary_ocr_disabled",
  "not_implemented",
] as const;

export type CapabilityReason = (typeof CAPABILITY_REASONS)[number];

/** Một khả năng của app (§5.12, đã duyệt 2026-10-25). `reason` là null khi và chỉ khi `available = true`. */
export interface Capability {
  name: CapabilityName;
  available: boolean;
  reason: CapabilityReason | null;
  /** Câu cố định theo `reason`; không chứa giá trị biến, khoá, tên model hay địa chỉ endpoint. */
  detail_vi: string | null;
}

/**
 * `GET /v1/runtime` theo §5.12. `capabilities` đã duyệt (đủ 9 phần tử theo thứ tự `CAPABILITY_NAMES`);
 * `mock_extractor` còn đề xuất (§17 D8). Backend hiện tại chỉ trả ba cờ của `RuntimeInfo`, nên hai field này
 * tuỳ chọn ở phía frontend: thiếu nghĩa là backend chưa báo, không phải "không khả dụng".
 */
export interface RuntimeInfoPending extends RuntimeInfo {
  mock_extractor?: boolean;
  capabilities?: Capability[];
}

// ------------------------------------------------------------------------------------- §6.0 lỗi

/** `detail` của 409 khi endpoint cần một khả năng đang không khả dụng (§6.0, đã duyệt 2026-10-25). */
export interface CapabilityError {
  code: "CAPABILITY_UNAVAILABLE";
  capability: CapabilityName;
  reason: CapabilityReason;
  message: string;
}

/** `detail` của 502 khi gọi LLM lỗi sau khi hết số lần thử của adapter (§6.0, đã duyệt 2026-10-25). */
export interface LlmUpstreamErrorDetail {
  code: "LLM_UPSTREAM_ERROR";
  message: string;
}

// ------------------------------------------------------------------------------------ §5.14 Subject

/** Hình thức thi của môn (§5.14; giá trị đã duyệt 2026-10-25, tên enum đề xuất). */
export type ExamFormat = "mcq" | "essay" | "mixed";

/**
 * Môn học (§5.14, model đã duyệt tối 2026-10-25). Endpoint `GET /v1/subjects` (§6.8) còn đề xuất.
 * `subject_code` theo mẫu `^[A-Z]{2,6}[0-9]{2,4}$`; `display_name` dài 1–200 ký tự.
 */
export interface Subject {
  subject_code: string;
  display_name: string;
  exam_format: ExamFormat;
  version: number;
  created_at: string;
  updated_at: string;
}
