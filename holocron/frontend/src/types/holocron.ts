/**
 * Kiểu dữ liệu khớp 1:1 với Pydantic models của backend (holocron/backend/holocron_core).
 * Tên field giữ nguyên snake_case như JSON FastAPI trả về; field Optional ở Python
 * (`X | None` có default None) luôn có mặt trong response với giá trị `null`.
 */

// ---------------------------------------------------------------- domain/schemas.py

export type OptionKey = "A" | "B" | "C" | "D";

export type QuestionFormat = "MCQ4" | "NUMERICAL";

export type ReviewStatus = "AUTO_PASS" | "AUTO_FALLBACK" | "NEEDS_REVIEW" | "APPROVED" | "REJECTED";

export type TemplateProvenance = "llm_generated" | "precomputed_verified";

export type SchedulingAlgorithm = "RR" | "SJF";

export interface Citation {
  doc_id: string;
  page_number: number;
  verbatim_quote: string;
  chunk_id: string | null;
}

/** `SolverInputs` (extra="allow"): response luôn có `quantum`, có thể kèm khoá phụ theo topic. */
export interface SolverInputs {
  quantum: number | null;
  [extra: string]: unknown;
}

export interface ProcessItem {
  name: string;
  arrival_time: number;
  burst_time: number;
}

export interface CPUSchedulingProblem {
  algorithm: SchedulingAlgorithm;
  processes: ProcessItem[];
  quantum: number | null;
}

export type SelfSolveStatus = "MATCHED" | "NO_MATCH" | "AMBIGUOUS" | "SKIPPED";

export interface SelfSolveResult {
  status: SelfSolveStatus;
  solved_answer_display: string | null;
  matched_option_key: OptionKey | null;
  solver_confidence: number;
  supporting_citations: Citation[];
  solver_template_id: string | null;
}

export interface MisconceptionRef {
  misconception_code: string;
  label_vi: string;
  rationale_vi: string;
}

export interface NumericalAnswer {
  value: number;
  tolerance_abs: number;
  unit: string | null;
  decimals: number;
}

export interface GenerationTrace {
  sandbox_duration_ms: number;
  llm_attempts: number;
  seed_rerolls: number;
  template_sha256: string;
  provenance: TemplateProvenance;
}

/** Một đoạn CPU chạy một tiến trình trong `[start, end)`; do sandbox sinh, frontend chỉ vẽ lại. */
export interface GanttSegment {
  process: string;
  start: number;
  end: number;
}

export interface AttemptLog {
  attempt: number;
  provenance: TemplateProvenance;
  template_sha256: string | null;
  outcome: string;
  seeds_tried: number;
  assertion_histogram: Record<string, number>;
  feedback_sent: string[];
}

/** Slot không sinh được (services/pipeline.py): cần người xem lại thủ công. */
export interface ReviewTicket {
  blueprint_id: string;
  slot_index: number;
  topic_key: string;
  reason: string;
  attempts: AttemptLog[];
}

export interface TwinQuestion {
  twin_id: string;
  blueprint_id: string;
  slot_index: number;
  template_id: string;
  topic_key: string;
  seed: number;
  format: QuestionFormat;
  stem: string;
  options: Record<OptionKey, string> | null;
  correct_key: OptionKey | null;
  numerical_answer: NumericalAnswer | null;
  distractor_to_misconception_map: Partial<Record<OptionKey, MisconceptionRef>>;
  solution_explanation_vi: string;
  citation: Citation;
  trace: GenerationTrace;
  review_status: ReviewStatus;
  generated_at: string;
  /** Sắp theo `start`, không chồng lấn; khoảng CPU rảnh không có đoạn; không rỗng với topic CPU Scheduling. */
  gantt: GanttSegment[];
}

// ---------------------------------------------------------- services/ingestion_gate.py

export type Severity = "info" | "warning" | "blocking";

export type GateIssueCode =
  | "DUAL_PASS_MISMATCH"
  | "LOW_CONFIDENCE"
  | "HEADER_MISMATCH"
  | "NOT_A_NUMBER"
  | "OUT_OF_RANGE"
  | "DUPLICATE_LABEL"
  | "MISSING_CELL"
  | "NOT_IN_TEXT_LAYER"
  | "SELF_SOLVE_MISMATCH"
  | "LATEX_UNBALANCED";

/** Issue gắn với ô khi `row`/`col` khác null; issue cấp bảng/đề (LaTeX, self-solve) có cả hai là null. */
export interface GateIssue {
  code: GateIssueCode;
  severity: Severity;
  message_vi: string;
  row: number | null;
  col: number | null;
  alternatives: string[];
}

export type GateStatus = "AUTO_PASS" | "NEEDS_REVIEW" | "BLOCKED";

export interface GateReport {
  table_id: string;
  table_version: number;
  status: GateStatus;
  issues: GateIssue[];
}

export interface ExtractedCell {
  row: number;
  col: number;
  text: string;
  confidence: number;
  verified_by_human: boolean;
}

export interface ExtractedTable {
  table_id: string;
  page_number: number;
  version: number;
  headers: string[];
  cells: ExtractedCell[];
}

export interface CellPatch {
  row: number;
  col: number;
  new_text: string;
}

/** Phần tử của `PatchAudit.changes` do `apply_patches` ghi. */
export interface PatchChange {
  row: number;
  col: number;
  old: string | null;
  new: string;
}

export interface PatchAudit {
  table_id: string;
  from_version: number;
  to_version: number;
  editor_id: string;
  changes: PatchChange[];
  at: string;
}

// ------------------------------------------------------------ services/extraction.py

export type MediaType = "image/png" | "image/jpeg" | "image/webp" | "application/pdf";

export interface UploadedDocument {
  document_id: string;
  sha256: string;
  filename: string;
  media_type: MediaType;
  size_bytes: number;
  uploaded_at: string;
}

// ------------------------------------------------------------------- routers/api.py

export interface IngestionItem {
  item_id: string;
  source_doc_id: string;
  page_number: number;
  topic_key: string;
  stem_latex: string;
  options: Record<OptionKey, string>;
  table_primary: ExtractedTable;
  table_secondary: ExtractedTable | null;
  page_text_layer: string | null;
  solver_inputs: SolverInputs;
  non_cloneable: boolean;
  non_cloneable_reason: string | null;
  problem: CPUSchedulingProblem | null;
  self_solve: SelfSolveResult | null;
  report: GateReport | null;
  audits: PatchAudit[];
  approved: boolean;
}

/** `GET /v1/runtime`: chỉ cờ boolean, không có khoá API, tên model hay endpoint. */
export interface RuntimeInfo {
  demo_mode: boolean;
  llm_configured: boolean;
  vlm_configured: boolean;
/** Body của `POST /v1/blueprints/{id}/twins`; mọi field đều tuỳ chọn khi gửi. */
export interface TwinBatchRequest {
  count?: number;
  format?: QuestionFormat;
  base_seed?: number | null;
}

export interface TwinBatch {
  blueprint_id: string;
  base_seed: number;
  twins: TwinQuestion[];
  review_tickets: ReviewTicket[];
}

export interface UploadResponse {
  document: UploadedDocument;
  item: IngestionItem;
}

export interface TablePatchRequest {
  editor_id: string;
  expected_version: number;
  patches: CellPatch[];
}

export interface StemPatchRequest {
  editor_id: string;
  stem_latex: string;
}

export interface NonCloneableRequest {
  editor_id: string;
  reason: string;
}

/** Một phần tử lỗi validate (HTTP 422) do FastAPI sinh. */
export interface ValidationErrorItem {
  loc: (string | number)[];
  msg: string;
  type: string;
}

/** Thân lỗi do backend trả về: chuỗi, danh sách lỗi validate, hoặc chi tiết BLOCKING khi approve (409). */
export interface ApiErrorBody {
  detail: string | ValidationErrorItem[] | { message: string; issues: string[] };
}
