/**
 * Kiểu của contract đã có trong SPECIFICATION.md (v0.7) nhưng CHƯA có trong backend của nhánh này.
 * `types/holocron.ts` chỉ chứa kiểu khớp 1:1 với Pydantic đang chạy; khi backend triển khai một model dưới đây,
 * chuyển kiểu đó sang `holocron.ts` trong cùng PR và xoá khỏi file này (kế hoạch UI mục 3.2).
 * Mỗi kiểu ghi mục spec và trạng thái duyệt.
 *
 * Câu lý thuyết: backend dùng phương án 14(b) (union `TwinQuestion | TheoryTwin`, `TheorySlot` riêng; ở `holocron.ts`).
 * Các kiểu "shape (a)" dưới đây (§5.9/§5.11 nguyên bản) chỉ còn dữ liệu mẫu và test của dữ liệu mẫu dùng: chờ (a) sau
 * 29/10 (nợ ADR-004).
 */

import type {
  BatchErrorKind,
  Citation,
  CognitiveLevel,
  DocumentImport,
  OptionKey,
  PageExtraction,
  PageSummary,
  ProposalJob,
  ProposalJobStatus,
  ProposalRejectReason,
  QuestionFormat,
  ReviewReason,
  ReviewStatus,
  ReviewTicket,
  RuntimeInfo,
} from "@/types/holocron";

export type {
  Capability,
  CapabilityError,
  CapabilityName,
  CapabilityReason,
  LlmUpstreamErrorDetail,
} from "@/types/holocron";
// Kiểu khả năng và thân lỗi §6.0 đã có ở backend (`domain/capabilities.py`, `domain/llm.py`) và ở `holocron.ts`; xuất
// lại ở đây cho nơi còn import từ file này.
export { CAPABILITY_NAMES, CAPABILITY_REASONS } from "@/types/holocron";

// --------------------------------------------------------------------------------- §5.12 Capability

/** `GET /v1/runtime`: `capabilities` đã ở `RuntimeInfo`; `mock_extractor` còn đề xuất (§17 D8), tuỳ chọn. */
export interface RuntimeInfoPending extends RuntimeInfo {
  mock_extractor?: boolean;
}

// ------------------------------------------------------------------------- §5.7, §6.5 bộ nhập PDF

/**
 * `ReviewReason` theo §5.7 v0.7: thêm `LOW_TEXT_SCORE` (duyệt 2026-10-25), backend của nhánh chưa có mã này.
 * Thứ tự là thứ tự khai báo của spec (backend sắp `review_reasons` theo thứ tự này).
 */
export type ReviewReasonPending = ReviewReason | "LOW_TEXT_SCORE";

export const REVIEW_REASONS: readonly ReviewReasonPending[] = [
  "LOW_TEXT_SCORE",
  "LOW_OCR_SCORE",
  "LOW_LINE_COVERAGE",
  "HIGH_INK_OUTSIDE_TEXT",
  "FIGURE_ON_SCAN",
  "FORMULA_ON_SCAN",
  "TIER3_UNAVAILABLE",
  "LOW_CONF_LINES",
  "TOKEN_MISMATCH",
  "CROSS_CHECK_UNAVAILABLE",
  "HIGH_PAGE_CER",
  "OCR_ENGINE_MISSING",
  "LOW_TEXT_CONTRAST",
  "TEXT_OCCLUDED",
  "FONT_ENCODING_SUSPECT",
  "IMAGE_NEEDS_READING",
  "PAGE_ROLE_UNCERTAIN",
  "DENOISE_UNAVAILABLE",
];

/** `PageSummary` với `ReviewReason` của §5.7 v0.7 (có `LOW_TEXT_SCORE`). */
export interface PageSummaryPending extends Omit<PageSummary, "review_reasons"> {
  review_reasons: ReviewReasonPending[];
}

/** `PageExtraction` với `ReviewReason` của §5.7 v0.7 (có `LOW_TEXT_SCORE`). */
export interface PageExtractionPending extends Omit<PageExtraction, "review_reasons"> {
  review_reasons: ReviewReasonPending[];
}

// ------------------------------------------------------- §5.7, §5.8, §6.5 màn kết quả nhập (T1e, T1b)

/**
 * `ProposalJob`, `ProposalJobStatus`, `ProposalRejectReason` và `BatchErrorKind` (§5.8) đã có ở backend và ở `holocron.ts`
 * (T1b); xuất lại ở đây cho nơi còn import từ file này. `DocumentImportPending` dùng đúng kiểu đó.
 */
export type { BatchErrorKind, ProposalJob, ProposalJobStatus, ProposalRejectReason };

/** `ProposalJobAbsentReason` (§5.7, T1e; đề xuất suy ra): vì sao `DocumentImport` không có `proposal_job`. */
export type ProposalJobAbsentReason = "not_confirmed" | "past_exam" | "no_chunks" | "llm_unavailable" | "not_retained";

/** `ReviewReasonCount` (§5.7, T1e): số trang cần xem theo từng lý do; `pages` tăng dần, không trùng. */
export interface ReviewReasonCount {
  reason: ReviewReasonPending;
  count: number;
  pages: number[];
}

/** `DroppedRoleCount` (§5.7, T1e): số trang bỏ mềm theo vai trò trang. */
export interface DroppedRoleCount {
  reason: "cover_or_course_nav" | "chapter_nav" | "build_step";
  count: number;
  pages: number[];
}

/**
 * `DocumentImport` với các field T1e (số đếm, lý do, job); backend chưa trả ở nhánh này, `lib/import-result.ts` parse
 * với mặc định an toàn. Gỡ khi `holocron.ts` có field thật.
 */
export type DocumentImportPending = DocumentImport & {
  pages_auto_confirmed: number;
  pages_kept_unconfirmed: number;
  pages_failed: number;
  review_reason_counts: ReviewReasonCount[];
  dropped_role_counts: DroppedRoleCount[];
  proposal_job: ProposalJob | null;
  proposal_job_absent_reason: ProposalJobAbsentReason | null;
};

/** Body của `POST /v1/vault/concept-pairs/jobs` (§6.6): đúng một trong `document_id` và `chunk_ids`. */
export interface ProposalJobCreate {
  subject_code: string;
  document_id: string;
}

// ------------------------------------------------------------------------ §5.9 Blueprint, shape (a)

/**
 * `BlueprintSlot` theo §5.9 (bản đổi duyệt 2026-10-26: `source_page_number`, `topic_key`, `self_solve` nullable).
 * Backend dùng `TheorySlot` riêng (14(b)); chỉ dữ liệu mẫu còn dùng kiểu này: chờ (a) sau 29/10 (nợ ADR-004).
 */
export interface BlueprintSlotPending {
  slot_index: number;
  source_page_number: number | null;
  format: QuestionFormat;
  chapter_code: string;
  topic_key: string | null;
  cognitive_level: CognitiveLevel;
  question_kind: "computational" | "theory";
  non_cloneable: boolean;
  non_cloneable_reason: string | null;
  distractor_archetypes: unknown[];
  self_solve: unknown;
  mapping_confidence: number;
  review_status: ReviewStatus;
}

/** `ExamBlueprint` shape (a) (§5.9); backend trả `ExamBlueprint` của `holocron.ts`. Chờ (a) sau 29/10 (nợ ADR-004). */
export interface ExamBlueprintPending {
  blueprint_id: string;
  source_exam_doc_id: string;
  subject_code: string;
  total_questions: number;
  duration_minutes: number | null;
  slots: BlueprintSlotPending[];
  created_at: string;
}

// ------------------------------------------------------ §5.11 TwinQuestion sau ADR-005/007, shape (a)

/** `MisconceptionRef` (§5.11) với `citation` đã duyệt 2026-10-25 (câu lý thuyết; null với câu tính toán). */
export interface MisconceptionRefPending {
  misconception_code: string;
  label_vi: string;
  rationale_vi: string;
  citation: Citation | null;
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

/**
 * `TwinQuestion` sau ADR-005 và ADR-007 (§5.11, §5.14). Backend trả union `TwinQuestion | TheoryTwin` (14(b)); chỉ dữ
 * liệu mẫu còn dùng kiểu này: chờ (a) sau 29/10 (nợ ADR-004).
 */
export interface TwinQuestionPending {
  twin_id: string;
  blueprint_id: string;
  slot_index: number;
  question_kind: "computational" | "theory";
  template_id: string | null;
  topic_key: string | null;
  seed: number;
  format: QuestionFormat;
  stem: string;
  options: Record<OptionKey, string> | null;
  correct_key: OptionKey | null;
  numerical_answer: { value: number; tolerance_abs: number; unit: string | null; decimals: number } | null;
  distractor_to_misconception_map: Partial<Record<OptionKey, MisconceptionRefPending>>;
  solution_explanation_vi: string;
  citation: Citation;
  trace: GenerationTracePending;
  review_status: ReviewStatus;
  generated_at: string;
  artifacts: SolutionArtifact[];
  essay: EssaySpec | null;
}

/** `TwinBatch` shape (a): `twins` là `TwinQuestionPending`. Chờ (a) sau 29/10 (nợ ADR-004). */
export interface TwinBatchPending {
  batch_id: string;
  blueprint_id: string;
  base_seed: number;
  twins: TwinQuestionPending[];
  review_tickets: ReviewTicket[];
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
  citation: Citation;
}
