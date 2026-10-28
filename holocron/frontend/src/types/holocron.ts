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

export type SourceKind = "textbook" | "lecture_slides" | "past_exam" | "lab_manual";

/** Thông tin tài liệu môn học (§5.8). `doc_id` = "doc_" + sha256[:16]; `has_text_layer` true khi ≥ 1 trang dùng tầng 1. */
export interface DocumentMeta {
  doc_id: string;
  subject_code: string;
  title: string;
  source_kind: SourceKind;
  sha256: string;
  page_count: number;
  has_text_layer: boolean;
  uploaded_at: string;
}

export type ChunkCategory = "THEORY" | "PRACTICE";

export type LabelStatus = "proposed" | "confirmed";

/** Một chunk của Knowledge Vault (§5.8). `verbatim_quote` là chuỗi con của `raw_text` sau NFC và gộp khoảng trắng (I3). */
export interface KnowledgeChunk {
  chunk_id: string;
  doc_id: string;
  subject_code: string;
  chapter_code: string;
  page_number: number;
  page_end: number | null;
  raw_text: string;
  verbatim_quote: string;
  category: ChunkCategory;
  tags: string[];
  topic_keys: string[];
  linked_chunk_ids: string[];
  token_count: number;
  embedding_model: string | null;
  label_status: LabelStatus;
  mapping_confidence: number | null;
  version: number;
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
  | "LATEX_UNBALANCED"
  /** Ô số của bảng đọc từ trang OCR không có lượt đọc thứ hai (ADR-008 mục 11; spec §7.4); ô đó không được AUTO_PASS. */
  | "CROSS_CHECK_UNAVAILABLE";

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

// -------------------------------------------------------------- domain/documents.py

export type LineEngine = "text_layer" | "tesseract" | "vlm" | "human";

export type TierUsed = "text_layer" | "ocr" | "layout" | "vlm";

export type LineFlag = "LOW_LINE_CONF" | "TOKEN_MISMATCH" | "HIGH_LINE_CER";

/** Lý do trang cần xem kỹ (§5.7), sắp theo thứ tự khai báo, không trùng. */
export type ReviewReason =
  | "LOW_OCR_SCORE"
  | "LOW_LINE_COVERAGE"
  | "HIGH_INK_OUTSIDE_TEXT"
  | "FIGURE_ON_SCAN"
  | "FORMULA_ON_SCAN"
  | "TIER3_UNAVAILABLE"
  | "LOW_CONF_LINES"
  | "TOKEN_MISMATCH"
  | "CROSS_CHECK_UNAVAILABLE"
  | "HIGH_PAGE_CER"
  | "OCR_ENGINE_MISSING";

export type FailureReason = "PAGE_TIMEOUT" | "PAGE_ERROR" | "OCR_ENGINE_MISSING";

export type PageStatus = "pending" | "ok" | "needs_review" | "failed";

export type CrossCheckStatus = "ok" | "mismatch" | "unavailable" | "not_applicable";

/** Một dòng văn bản của trang (NFC). `bbox` = [x0, y0, x1, y1] chia theo khổ trang, ∈ [0, 1], gốc trên-trái. */
export interface ExtractedLine {
  line_index: number;
  text: string;
  bbox: [number, number, number, number] | null;
  conf: number;
  engine: LineEngine;
  flags: LineFlag[];
  edited_by_human: boolean;
}

export interface TokenMismatch {
  line_index: number;
  primary_value: string;
  secondary_value: string | null;
}

export interface CrossCheck {
  status: CrossCheckStatus;
  secondary_engine: "rapidocr" | null;
  mismatches: TokenMismatch[];
}

/** Kết quả đọc một trang PDF (§5.7); mọi điểm và cờ do backend tính, frontend chỉ hiển thị. */
export interface PageExtraction {
  document_id: string;
  page_number: number;
  version: number;
  status: PageStatus;
  tier_used: TierUsed | null;
  text_score: number | null;
  ocr_score: number | null;
  coverage_ratio: number | null;
  ink_outside_text_ratio: number | null;
  has_figure: boolean;
  has_table: boolean;
  has_formula: boolean;
  lines: ExtractedLine[];
  tables: ExtractedTable[];
  cross_check: CrossCheck;
  review_reasons: ReviewReason[];
  failure_reason: FailureReason | null;
  confirmed: boolean;
  confirmed_at: string | null;
}

export type DocumentStatus = "processing" | "review" | "confirmed" | "failed";

export type OutputLanguage = "vi" | "en";

export interface PageSummary {
  page_number: number;
  status: PageStatus;
  tier_used: TierUsed | null;
  review_reasons: ReviewReason[];
  confirmed: boolean;
}

/** `POST /v1/documents` (202 hoặc 200) và `GET /v1/documents/{document_id}`; `pages` đủ `page_count` phần tử. */
export interface DocumentImport {
  meta: DocumentMeta;
  filename: string;
  size_bytes: number;
  output_language: OutputLanguage;
  status: DocumentStatus;
  pages: PageSummary[];
}

/** Một phần tử của `GET /v1/documents`, mới nhất trước. */
export interface DocumentSummary {
  meta: DocumentMeta;
  status: DocumentStatus;
  pages_pending: number;
  pages_needs_review: number;
  pages_failed: number;
  pages_confirmed: number;
}

// ------------------------------------------------------------------ domain/vault.py

export type ConceptPairStatus = "proposed" | "confirmed" | "rejected";

/** Cặp khái niệm – trích dẫn định nghĩa (§5.8, ADR-007). `term_spans` là vị trí `[start, end)` của `term` trong `definition_quote`. */
export interface ConceptPair {
  pair_id: string;
  chunk_id: string;
  subject_code: string;
  chapter_code: string;
  term: string;
  definition_quote: string;
  term_spans: [number, number][];
  status: ConceptPairStatus;
  proposed_by: "llm" | "user";
  mapping_confidence: number | null;
  version: number;
  created_at: string;
  confirmed_at: string | null;
}

export type SearchIndex = "primary" | "no_diacritics";

/** Một kết quả của `GET /v1/vault/search`; `score` (BM25) chỉ để xếp hạng. */
export interface SearchHit {
  chunk: KnowledgeChunk;
  score: number;
  index: SearchIndex;
}

// ----------------------------------------------------------------- routers/vault.py

/** `GET /v1/vault/chunks`. */
export interface ChunkListResponse {
  total: number;
  chunks: KnowledgeChunk[];
}

/** Body của `PATCH /v1/vault/chunks/{chunk_id}/labels`; field nhãn bỏ trống thì giữ nguyên. */
export interface ChunkLabelsPatch {
  editor_id: string;
  expected_version: number;
  chapter_code?: string | null;
  category?: ChunkCategory | null;
  topic_keys?: string[] | null;
}

/** Body của `POST /v1/vault/concept-pairs` (người dùng bôi đoạn trong chunk). */
export interface ConceptPairCreate {
  editor_id: string;
  chunk_id: string;
  term: string;
  definition_quote: string;
}

/** Body của `POST /v1/vault/concept-pairs/{pair_id}/confirm`; có thể sửa `term`/`definition_quote` rồi xác nhận. */
export interface ConceptPairConfirm {
  editor_id: string;
  expected_version: number;
  term?: string | null;
  definition_quote?: string | null;
}

/** Body của `POST /v1/vault/concept-pairs/{pair_id}/reject`. */
export interface ConceptPairReject {
  editor_id: string;
  expected_version: number;
}

/** Body của `POST /v1/vault/concept-pairs/propose`: 1–20 chunk, cùng `subject_code`. */
export interface ConceptPairProposeRequest {
  subject_code: string;
  chunk_ids: string[];
}

/**
 * `POST /v1/vault/concept-pairs/propose` (200): cặp mới ở trạng thái `proposed`, `proposed_by = "llm"`; đề xuất
 * không qua cổng kiểm (trích dẫn không nguyên văn, trùng, vượt số cặp mỗi chunk) chỉ được đếm trong `rejected_count`.
 * Lỗi: 409 với `detail: CapabilityError`, 502 với `detail: LlmUpstreamErrorDetail`.
 */
export interface ConceptPairProposeResponse {
  pairs: ConceptPair[];
  rejected_count: number;
}

// ----------------------------------------------------------- domain/capabilities.py

export type CapabilityName =
  | "ocr"
  | "ocr_cross_check"
  | "vault_search"
  | "theory_generation"
  | "concept_pair_llm_proposal"
  | "essay_grading_offline"
  | "essay_grading_llm"
  | "template_authoring_llm"
  | "vlm";

export type CapabilityReason =
  | "llm_not_configured"
  | "vlm_not_configured"
  | "ocr_engine_missing"
  | "ocr_language_missing"
  | "secondary_ocr_disabled"
  | "not_implemented";

/** Một khả năng của app (spec §5.12, §11.4); `reason` là `null` khi và chỉ khi `available = true`. */
export interface Capability {
  name: CapabilityName;
  available: boolean;
  reason: CapabilityReason | null;
  detail_vi: string | null;
}

/** `detail` của 409 khi endpoint cần một khả năng đang không khả dụng (spec §6.0); `message` chỉ nêu tên biến. */
export interface CapabilityError {
  code: "CAPABILITY_UNAVAILABLE";
  capability: CapabilityName;
  reason: CapabilityReason;
  message: string;
}

// -------------------------------------------------------------------- domain/llm.py

/** `detail` của 502 khi gọi LLM lỗi (spec §6.0): không chứa endpoint, tên model hay nội dung phản hồi. */
export interface LlmUpstreamErrorDetail {
  code: "LLM_UPSTREAM_ERROR";
  message: string;
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
  app_version: string;
  commit: string;
  log_dir: string | null;
}

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
