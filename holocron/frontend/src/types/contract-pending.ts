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

// ------------------------------------------------------------------- §5.9 Blueprint (lát cắt dọc)

/**
 * Hình thức câu (§5.14): `QuestionFormat` của backend hiện chỉ có `MCQ4 | NUMERICAL`; `ESSAY` đã duyệt 2026-10-25
 * nhưng chưa có trong code.
 */
export type QuestionFormatPending = "MCQ4" | "NUMERICAL" | "ESSAY";

/** Hình thức của slot lý thuyết (§5.9, field `format` duyệt 2026-10-26). */
export type TheorySlotFormat = "MCQ4" | "ESSAY";

/** Một slot lý thuyết do người dùng yêu cầu (§5.9, duyệt 2026-10-26). */
export interface TheorySlotSpec {
  /** Mẫu `^ch[0-9]{1,2}(\.[0-9]{1,2})?$`. */
  chapter_code: string;
  cognitive_level?: "remember" | "understand";
  source_chunk_id?: string | null;
  /** Giao diện luôn gửi lựa chọn tường minh; `null` để backend lấy mặc định từ `exam_format` (còn đề xuất). */
  format?: TheorySlotFormat | null;
}

/** Body của `POST /v1/blueprints` (§5.9, §6.7). `item_ids` và `theory_slots` không cùng rỗng. */
export interface BlueprintCreate {
  subject_code: string;
  item_ids?: string[];
  theory_slots?: TheorySlotSpec[];
  duration_minutes?: number | null;
}

export type CognitiveLevel = "remember" | "understand" | "apply" | "analyze" | "evaluate" | "create";

/** `BlueprintSlot` theo §5.9 (bản đổi duyệt 2026-10-26: `source_page_number`, `topic_key`, `self_solve` nullable). */
export interface BlueprintSlotPending {
  slot_index: number;
  source_page_number: number | null;
  format: QuestionFormatPending;
  chapter_code: string;
  topic_key: string | null;
  cognitive_level: CognitiveLevel;
  question_kind: "computational" | "theory";
  non_cloneable: boolean;
  non_cloneable_reason: string | null;
  distractor_archetypes: unknown[];
  self_solve: unknown;
  mapping_confidence: number;
  review_status: "AUTO_PASS" | "AUTO_FALLBACK" | "NEEDS_REVIEW" | "APPROVED" | "REJECTED";
}

/** `201 ExamBlueprint` của `POST /v1/blueprints` (§5.9, §6.7). */
export interface ExamBlueprintPending {
  blueprint_id: string;
  source_exam_doc_id: string;
  subject_code: string;
  total_questions: number;
  duration_minutes: number | null;
  slots: BlueprintSlotPending[];
  created_at: string;
}

// ---------------------------------------------------------------- §5.11 TwinQuestion sau ADR-005/007

/** `Citation` (§5.8), như `types/holocron.ts`; chép lại để file này tự đứng. */
export interface CitationPending {
  doc_id: string;
  page_number: number;
  verbatim_quote: string;
  chunk_id: string | null;
}

/** `MisconceptionRef` (§5.11) với `citation` đã duyệt 2026-10-25 (câu lý thuyết; null với câu tính toán). */
export interface MisconceptionRefPending {
  misconception_code: string;
  label_vi: string;
  rationale_vi: string;
  citation: CitationPending | null;
}

export interface ComputationalTrace {
  kind: "computational";
  sandbox_duration_ms: number;
  llm_attempts: number;
  seed_rerolls: number;
  template_sha256: string;
  provenance: "llm_generated" | "precomputed_verified";
}

export interface TheoryTrace {
  kind: "theory";
  generator_version: string;
  pool_sha256: string;
  seed_rerolls: number;
}

/** `GenerationTrace` dạng union theo `kind` (§5.11, duyệt 2026-10-25). */
export type GenerationTracePending = ComputationalTrace | TheoryTrace;

export interface TimelineSegment {
  label: string;
  start: number;
  end: number;
}

export interface TimelineArtifact {
  kind: "timeline";
  caption_vi: string | null;
  segments: TimelineSegment[];
}

export interface TableArtifact {
  kind: "table";
  caption_vi: string | null;
  headers: string[];
  rows: string[][];
}

/** Artifact lời giải theo `kind` (§5.11, ADR-005 bước 3); do sandbox sinh, frontend chỉ vẽ. */
export type SolutionArtifact = TimelineArtifact | TableArtifact;

/** Một ý của câu tự luận (§5.14, tên model đề xuất): lát cắt nguyên văn của `definition_quote`. */
export interface EssayPoint {
  point_index: number;
  text: string;
  syllable_count: number;
}

/** `TwinQuestion.essay` (§5.14; field đã duyệt 2026-10-25, chi tiết đề xuất). */
export interface EssaySpec {
  essay_kind: "define_concept";
  pair_id: string;
  term: string;
  points: EssayPoint[];
  splitter_version: string;
}

export type OptionKeyPending = "A" | "B" | "C" | "D";

/** `TwinQuestion` sau ADR-005 và ADR-007 (§5.11, §5.14). */
export interface TwinQuestionPending {
  twin_id: string;
  blueprint_id: string;
  slot_index: number;
  question_kind: "computational" | "theory";
  template_id: string | null;
  topic_key: string | null;
  seed: number;
  format: QuestionFormatPending;
  stem: string;
  options: Record<OptionKeyPending, string> | null;
  correct_key: OptionKeyPending | null;
  numerical_answer: { value: number; tolerance_abs: number; unit: string | null; decimals: number } | null;
  distractor_to_misconception_map: Partial<Record<OptionKeyPending, MisconceptionRefPending>>;
  solution_explanation_vi: string;
  citation: CitationPending;
  trace: GenerationTracePending;
  review_status: "AUTO_PASS" | "AUTO_FALLBACK" | "NEEDS_REVIEW" | "APPROVED" | "REJECTED";
  generated_at: string;
  artifacts: SolutionArtifact[];
  essay: EssaySpec | null;
}

/** `ReviewTicket` (§5.11) với `topic_key` nullable (duyệt 2026-10-26, null với slot lý thuyết). */
export interface ReviewTicketPending {
  blueprint_id: string;
  slot_index: number;
  topic_key: string | null;
  reason: string;
  attempts: unknown[];
}

/** `TwinBatch` với `batch_id`, `created_at` (§6.7, duyệt 2026-10-25). */
export interface TwinBatchPending {
  batch_id: string;
  blueprint_id: string;
  base_seed: number;
  twins: TwinQuestionPending[];
  review_tickets: ReviewTicketPending[];
  created_at: string;
}

// ------------------------------------------------------------------ §5.14, §6.9 chấm tự luận

/** Body của `POST /v1/twins/{twin_id}/essay-grade` (§6.9). */
export interface EssayGradeRequest {
  answer_text: string;
}

export interface NgramCoverage {
  bigram: number;
  trigram: number;
  combined: number;
}

export interface EssayPointResult {
  point_index: number;
  text: string;
  /** Chỉ để backend kiểm và tái lập; giao diện KHÔNG hiển thị như điểm (§5.14). */
  ngram_coverage: NgramCoverage;
}

export interface EssayGradingThresholds {
  point_min_syllables: number;
  point_max_syllables: number;
  bigram_weight: number;
  trigram_weight: number;
  point_match_min: number;
}

/** `200 EssayGradeResult` (§5.14; tên model và `matched_count` đề xuất). */
export interface EssayGradeResult {
  twin_id: string;
  grader: "offline_ngram";
  grader_version: string;
  thresholds: EssayGradingThresholds;
  total_points: number;
  matched_count: number;
  matched_points: EssayPointResult[];
  missing_points: EssayPointResult[];
  answer_sha256: string;
  citation: CitationPending;
}
