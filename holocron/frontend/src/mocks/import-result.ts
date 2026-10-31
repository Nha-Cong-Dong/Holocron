/**
 * Tài liệu mẫu cho màn kết quả nhập (T2.2; spec §5.7, §5.8, §6.5): một tài liệu có trang tự xác nhận, trang cần xem,
 * trang bỏ mềm và một trang đọc lỗi; chấp nhận được; sau đó một job đề xuất cặp chạy qua vài lô rồi `succeeded`.
 * Gỡ cùng thư mục `src/mocks/` khi backend có đủ field T1e và endpoint job.
 *
 * Đây là NHÀ GIẢ của backend: nó giữ trạng thái trong phiên (khôi phục trang, xác nhận trang và chấp nhận đều được lưu)
 * và dựng số đếm của `DocumentImport` từ trang hiện tại, đúng như backend sẽ làm. Frontend KHÔNG dùng lại các hàm đếm
 * này; nó chỉ đọc số trong response (I1).
 *
 * Biến thể không LLM: theo `?mock_runtime=` sẵn có (`mocks/store.ts`); kịch bản mà `RUNTIME_FIXTURES` đặt khả năng
 * `concept_pair_llm_proposal` là không khả dụng (mặc định `offline`) thì chấp nhận xong không có job và
 * `proposal_job_absent_reason = "llm_unavailable"`; `?mock_runtime=llm` cho job chạy qua các lô.
 *
 * Tài liệu mẫu thuộc môn `MAU102` (xem `MOCK_IMPORT_RESULT_SUBJECT`): mở tab Tài liệu của môn đó để thấy nó.
 */

import {
  type DocumentSummaryView,
  type DocumentView,
  type PageView,
  parseDocumentSummaries,
  parsePageExtraction,
} from "@/lib/documents-data";
import { ApiError, type ClassifyOptions, classifyHttpError, invalidResponseError } from "@/lib/errors";
import { type ImportResultView, type ProposalJobView, parseImportResult, parseProposalJob } from "@/lib/import-result";
import {
  type DocumentImportPending,
  type DroppedRoleCount,
  type PageExtractionPending,
  type PageSummaryPending,
  type ProposalJob,
  type ProposalJobAbsentReason,
  type ProposalJobStatus,
  type ProposalRejectReason,
  REVIEW_REASONS,
  type ReviewReasonCount,
} from "@/types/contract-pending";
import type { DocumentMeta, DocumentSummary, ExtractedLine } from "@/types/holocron";

import { MOCK_CONFIRMED_AT } from "./fixtures/documents";
import { RUNTIME_FIXTURES } from "./fixtures/runtime";
import { currentRuntimeScenario } from "./store";

export const MOCK_IMPORT_RESULT_DOC_ID = "doc_7c3e9a1b5d2f4086";

/** Môn của tài liệu mẫu: khác `MAU101` của tài liệu mẫu cũ để hai bộ mẫu không lẫn danh sách của nhau. */
export const MOCK_IMPORT_RESULT_SUBJECT = "MAU102";

/** Tài liệu mẫu của màn kết quả: chỉ mã này dùng nhà giả này. */
export function isImportResultMock(documentId: string): boolean {
  return documentId === MOCK_IMPORT_RESULT_DOC_ID;
}

const META: DocumentMeta = {
  doc_id: MOCK_IMPORT_RESULT_DOC_ID,
  subject_code: MOCK_IMPORT_RESULT_SUBJECT,
  title: "Tài liệu mẫu: Kết quả nhập",
  source_kind: "lecture_slides",
  sha256: "7c3e9a1b5d2f40866e1d2c3b4a59687f0e1d2c3b4a59687f0e1d2c3b4a596870",
  page_count: 8,
  has_text_layer: true,
  uploaded_at: "2026-11-01T08:00:00Z",
};

function lines(texts: readonly string[], engine: ExtractedLine["engine"], conf: number): ExtractedLine[] {
  return texts.map((text, lineIndex) => {
    const top = 0.08 + lineIndex * 0.06;
    return {
      line_index: lineIndex,
      text,
      bbox: [0.08, top, 0.92, top + 0.04],
      conf,
      engine,
      flags: [],
      edited_by_human: false,
      is_header_footer: false,
    };
  });
}

function page(pageNumber: number, overrides: Partial<PageExtractionPending>): PageExtractionPending {
  return {
    document_id: MOCK_IMPORT_RESULT_DOC_ID,
    page_number: pageNumber,
    version: 1,
    status: "ok",
    tier_used: "text_layer",
    text_score: 0.96,
    ocr_score: null,
    coverage_ratio: null,
    ink_outside_text_ratio: null,
    has_figure: false,
    has_table: false,
    has_formula: false,
    lines: lines([`Nội dung mẫu của trang ${pageNumber}.`, "Một câu thứ hai để trang có văn bản."], "text_layer", 0.96),
    tables: [],
    cross_check: { status: "not_applicable", secondary_engine: null, mismatches: [] },
    review_reasons: [],
    failure_reason: null,
    confirmed: false,
    confirmed_at: null,
    own_images: [],
    hidden_chars_dropped: 0,
    scan_text_layer: false,
    content_token_count: 12,
    page_role: "content",
    role_evidence: null,
    dropped: false,
    restored: false,
    ...overrides,
  };
}

const OCR_PAGE: Partial<PageExtractionPending> = {
  tier_used: "ocr",
  text_score: 0.3,
  ocr_score: 0.6,
  coverage_ratio: 1,
  ink_outside_text_ratio: 0.03,
  lines: lines(["Dòng đọc bằng OCR, độ tin cậy vừa phải.", "Dòng thứ hai của trang quét."], "tesseract", 0.7),
  cross_check: { status: "ok", secondary_engine: "rapidocr", mismatches: [] },
};

/** Trang ban đầu của tài liệu mẫu (đã đọc xong, tự xác nhận đã chạy). */
function initialPages(): PageExtractionPending[] {
  return [
    page(1, { confirmed: true, confirmed_at: MOCK_CONFIRMED_AT }),
    page(2, { confirmed: true, confirmed_at: MOCK_CONFIRMED_AT }),
    page(3, {
      page_role: "parallel_visual_series",
      role_evidence: {
        rule: "parallel_visual_series",
        related_pages: [{ document_id: MOCK_IMPORT_RESULT_DOC_ID, page_number: 2 }],
        jaccard: null,
        dhash_distance: null,
      },
    }),
    page(4, { ...OCR_PAGE, status: "needs_review", review_reasons: ["LOW_OCR_SCORE"] }),
    page(5, {
      ...OCR_PAGE,
      status: "needs_review",
      review_reasons: ["LOW_OCR_SCORE", "FIGURE_ON_SCAN"],
      has_figure: true,
    }),
    page(6, {
      dropped: true,
      page_role: "cover_or_course_nav",
      role_evidence: { rule: "cover_or_course_nav", related_pages: [], jaccard: 0.92, dhash_distance: null },
      lines: lines(["Môn học mẫu", "Giảng viên: (mẫu)"], "text_layer", 0.96),
    }),
    page(7, {
      status: "failed",
      tier_used: null,
      text_score: null,
      lines: [],
      cross_check: { status: "unavailable", secondary_engine: null, mismatches: [] },
      failure_reason: "OCR_ENGINE_MISSING",
      content_token_count: null,
    }),
    page(8, {
      dropped: true,
      page_role: "chapter_nav",
      role_evidence: { rule: "chapter_nav", related_pages: [], jaccard: null, dhash_distance: null },
      lines: lines(["Mục lục chương"], "text_layer", 0.96),
    }),
  ];
}

function jobStep(
  status: ProposalJobStatus,
  done: number,
  proposed: number,
  rejected: number,
  rejectedByReason: Partial<Record<ProposalRejectReason, number>>,
): ProposalJob {
  return {
    job_id: "pjob_00112233445566ff",
    subject_code: MOCK_IMPORT_RESULT_SUBJECT,
    trigger: "document_confirmed",
    document_id: MOCK_IMPORT_RESULT_DOC_ID,
    status,
    chunk_count: 18,
    batch_size: 5,
    batches_total: 4,
    batches_done: done,
    batches_failed: 0,
    batch_errors: {},
    proposed_count: proposed,
    pre_selected_count: 0,
    rejected_count: rejected,
    rejected_by_reason: rejectedByReason,
    exercise_chunks: 0,
    cancel_requested: false,
    created_at: "2026-11-01T08:10:00Z",
    started_at: status === "queued" ? null : "2026-11-01T08:10:02Z",
    finished_at: status === "succeeded" ? "2026-11-01T08:10:20Z" : null,
  };
}

/** Các bước tiến độ dựng sẵn của job (giá trị cố định, nhà giả không tính): chờ, ba lô đang chạy, xong. */
const JOB_STEPS: readonly ProposalJob[] = [
  jobStep("queued", 0, 0, 0, {}),
  jobStep("running", 1, 2, 0, {}),
  jobStep("running", 2, 4, 1, { duplicate: 1 }),
  jobStep("running", 3, 5, 2, { duplicate: 1, quote_not_verbatim: 1 }),
  jobStep("succeeded", 4, 7, 3, { duplicate: 1, quote_not_verbatim: 2 }),
];

interface Session {
  deleted: boolean;
  pages: PageExtractionPending[];
  /** Trang do tự xác nhận chốt (`confirmed_by = "auto"`). */
  autoPages: Set<number>;
  confirmed: boolean;
  /** Bước của job trong `JOB_STEPS`; `null` khi chưa có job. */
  jobStep: number | null;
}

let current: Session | null = null;

function session(): Session {
  if (current === null) {
    const pages = initialPages();
    current = {
      deleted: false,
      pages,
      autoPages: new Set(pages.filter((item) => item.confirmed).map((item) => item.page_number)),
      confirmed: false,
      jobStep: null,
    };
  }
  return current;
}

/** Đưa tài liệu mẫu về trạng thái ban đầu (test không phụ thuộc thứ tự chạy). */
export function resetImportResultMock(): void {
  current = null;
}

function llmAvailable(): boolean {
  const capabilities = RUNTIME_FIXTURES[currentRuntimeScenario()].capabilities;
  return capabilities.some((item) => item.name === "concept_pair_llm_proposal" && item.available);
}

function checked<T>(value: T | null): T {
  if (value === null) throw new ApiError(200, invalidResponseError());
  return value;
}

function reject(status: number, detail: unknown, options: ClassifyOptions): Promise<never> {
  return Promise.reject(new ApiError(status, classifyHttpError(status, { detail }, options)));
}

const REVIEW: ClassifyOptions = { context: "document_review" };
const VERSIONED: ClassifyOptions = { context: "document_review", versioned: true };
const CONFIRM_PAGE: ClassifyOptions = { context: "page_confirm", versioned: true };
const CONFIRM_DOCUMENT: ClassifyOptions = { context: "document_confirm" };

function summaryOf(item: PageExtractionPending): PageSummaryPending {
  return {
    page_number: item.page_number,
    status: item.status,
    tier_used: item.tier_used,
    review_reasons: [...item.review_reasons],
    confirmed: item.confirmed,
    page_role: item.page_role,
    dropped: item.dropped,
  };
}

function isNeedsReview(item: PageExtractionPending): boolean {
  return item.status === "needs_review" && !item.confirmed && !item.dropped;
}

function reasonCounts(pages: readonly PageExtractionPending[]): ReviewReasonCount[] {
  const counts: ReviewReasonCount[] = [];
  for (const reason of REVIEW_REASONS) {
    const matching = pages.filter((item) => isNeedsReview(item) && item.review_reasons.some((code) => code === reason));
    if (matching.length > 0) {
      counts.push({ reason, count: matching.length, pages: matching.map((item) => item.page_number) });
    }
  }
  return counts;
}

const DROPPED_ROLES: readonly DroppedRoleCount["reason"][] = ["cover_or_course_nav", "chapter_nav", "build_step"];

function roleCounts(pages: readonly PageExtractionPending[]): DroppedRoleCount[] {
  const counts: DroppedRoleCount[] = [];
  for (const reason of DROPPED_ROLES) {
    const matching = pages.filter((item) => item.dropped && item.page_role === reason);
    if (matching.length > 0) {
      counts.push({ reason, count: matching.length, pages: matching.map((item) => item.page_number) });
    }
  }
  return counts;
}

function absentReasonOf(state: Session, job: ProposalJob | null): ProposalJobAbsentReason | null {
  if (job !== null) return null;
  if (!state.confirmed) return "not_confirmed";
  return llmAvailable() ? "not_retained" : "llm_unavailable";
}

type RawDocumentImport = Omit<DocumentImportPending, "pages"> & { pages: PageSummaryPending[] };

/** `DocumentImport` đầy đủ field T1e dựng từ trang hiện tại (việc của backend; frontend không lặp lại). */
function rawDocument(state: Session): RawDocumentImport {
  const pages = state.pages;
  const job = state.jobStep === null ? null : (JOB_STEPS[state.jobStep] ?? null);
  const absent = absentReasonOf(state, job);
  return {
    meta: structuredClone(META),
    filename: "tai-lieu-mau-ket-qua-nhap.pdf",
    size_bytes: 412_160,
    output_language: "vi",
    chapter_code: "ch2",
    status: state.confirmed ? "confirmed" : "review",
    pages: pages.map(summaryOf),
    pages_kept: pages.filter((item) => item.status !== "failed" && item.status !== "pending" && !item.dropped).length,
    pages_dropped: pages.filter((item) => item.dropped).length,
    pages_needs_review: pages.filter(isNeedsReview).length,
    chapter_title: "Kết quả nhập mẫu",
    chapter_source: { code: "user", title: "file_name" },
    chapter_flags: [],
    auto_confirm: true,
    pages_auto_confirmed: pages.filter((item) => state.autoPages.has(item.page_number)).length,
    pages_kept_unconfirmed: pages.filter((item) => item.status === "ok" && !item.confirmed && !item.dropped).length,
    pages_failed: pages.filter((item) => item.status === "failed").length,
    review_reason_counts: reasonCounts(pages),
    dropped_role_counts: roleCounts(pages),
    proposal_job: job === null ? null : structuredClone(job),
    proposal_job_absent_reason: absent,
  };
}

function live(documentId: string): Session | null {
  if (!isImportResultMock(documentId)) return null;
  const state = session();
  return state.deleted ? null : state;
}

/** Mỗi lần đọc tài liệu đã chấp nhận, job tiến thêm một bước (nhà giả của backend đang chạy lô). */
function advanceJob(state: Session): void {
  if (state.jobStep !== null && state.jobStep < JOB_STEPS.length - 1) state.jobStep += 1;
}

function importResult(state: Session): ImportResultView {
  return checked(parseImportResult(structuredClone(rawDocument(state))));
}

function listDocuments(subjectCode: string): DocumentSummaryView[] {
  if (subjectCode !== MOCK_IMPORT_RESULT_SUBJECT) return [];
  const state = session();
  if (state.deleted) return [];
  const pages = state.pages;
  const summary: DocumentSummary = {
    meta: structuredClone(META),
    status: state.confirmed ? "confirmed" : "review",
    pages_pending: 0,
    pages_needs_review: pages.filter(isNeedsReview).length,
    pages_failed: pages.filter((item) => item.status === "failed").length,
    pages_confirmed: pages.filter((item) => item.confirmed).length,
    pages_dropped: pages.filter((item) => item.dropped).length,
  };
  return checked(parseDocumentSummaries([summary]));
}

function getImportResult(documentId: string): Promise<ImportResultView> {
  const state = live(documentId);
  if (state === null) return reject(404, "không tìm thấy tài liệu", REVIEW);
  advanceJob(state);
  return Promise.resolve(importResult(state));
}

function getDocument(documentId: string): Promise<DocumentView> {
  const state = live(documentId);
  if (state === null) return reject(404, "không tìm thấy tài liệu", REVIEW);
  advanceJob(state);
  return Promise.resolve(importResult(state).document);
}

function getDocumentPage(documentId: string, pageNumber: number): Promise<PageView> {
  const found = live(documentId)?.pages.find((item) => item.page_number === pageNumber);
  if (found === undefined) return reject(404, "không tìm thấy tài liệu hoặc trang", REVIEW);
  return Promise.resolve(checked(parsePageExtraction(structuredClone(found))));
}

function patchPageLines(documentId: string, pageNumber: number): Promise<PageView> {
  // Nhà giả không sửa dòng: trả trang hiện tại (kịch bản của màn này là chấp nhận, không sửa).
  return getDocumentPage(documentId, pageNumber);
}

/** `POST …/restore` (§6.5): trang bỏ mềm thành trang thường, `version` + 1, LƯU trong phiên; trang thường trả nguyên. */
function restorePage(documentId: string, pageNumber: number, expectedVersion: number): Promise<PageView> {
  const state = live(documentId);
  const index = state?.pages.findIndex((item) => item.page_number === pageNumber) ?? -1;
  const found = state?.pages[index];
  if (state === null || found === undefined) return reject(404, "không tìm thấy tài liệu hoặc trang", REVIEW);
  if (!found.dropped) return getDocumentPage(documentId, pageNumber);
  if (state.confirmed || found.version !== expectedVersion) {
    return reject(409, `version lệch: hiện tại ${found.version}`, VERSIONED);
  }
  state.pages[index] = { ...found, dropped: false, restored: true, version: found.version + 1 };
  return getDocumentPage(documentId, pageNumber);
}

/** `POST …/confirm` của một trang: nhận trang `ok` và `needs_review`, LƯU `confirmed` trong phiên. */
function confirmPage(documentId: string, pageNumber: number, expectedVersion: number): Promise<PageView> {
  const state = live(documentId);
  const index = state?.pages.findIndex((item) => item.page_number === pageNumber) ?? -1;
  const found = state?.pages[index];
  if (state === null || found === undefined) return reject(404, "không tìm thấy tài liệu hoặc trang", REVIEW);
  if (found.confirmed && found.status !== "failed") return getDocumentPage(documentId, pageNumber);
  if (found.status === "failed" || found.status === "pending" || found.dropped || state.confirmed) {
    return reject(409, "trang không xác nhận được", CONFIRM_PAGE);
  }
  if (found.version !== expectedVersion) return reject(409, `version lệch: hiện tại ${found.version}`, CONFIRM_PAGE);
  state.pages[index] = { ...found, confirmed: true, confirmed_at: MOCK_CONFIRMED_AT, version: found.version + 1 };
  return getDocumentPage(documentId, pageNumber);
}

/**
 * `POST /v1/documents/{id}/confirm` (§6.5): xác nhận mọi trang `ok` chưa xác nhận (bỏ qua trang bỏ mềm, không đụng trang
 * cần xem). Chỉ khi không còn trang cần xem thì tài liệu `confirmed` và job tạo ra (nếu LLM khả dụng).
 */
function confirmImport(documentId: string): Promise<ImportResultView> {
  const state = live(documentId);
  if (state === null) return reject(404, "không tìm thấy tài liệu", CONFIRM_DOCUMENT);
  if (!state.confirmed) {
    state.pages = state.pages.map((item) =>
      item.status === "ok" && !item.confirmed && !item.dropped
        ? { ...item, confirmed: true, confirmed_at: MOCK_CONFIRMED_AT, version: item.version + 1 }
        : item,
    );
    if (!state.pages.some(isNeedsReview)) {
      state.confirmed = true;
      state.jobStep = llmAvailable() ? 0 : null;
    }
  }
  return Promise.resolve(importResult(state));
}

function confirmDocument(documentId: string): Promise<DocumentView> {
  return confirmImport(documentId).then((result) => result.document);
}

/** `POST /v1/vault/concept-pairs/jobs` (§6.6): job mới (`queued`); LLM không khả dụng ⇒ 409 `CapabilityError`. */
function startProposalJob(subjectCode: string, documentId: string): Promise<ProposalJobView> {
  const state = live(documentId);
  if (state === null) return reject(404, "không tìm thấy tài liệu", {});
  if (!llmAvailable() || subjectCode !== MOCK_IMPORT_RESULT_SUBJECT) {
    return reject(
      409,
      {
        code: "CAPABILITY_UNAVAILABLE",
        capability: "concept_pair_llm_proposal",
        reason: "llm_not_configured",
        message: "concept_pair_llm_proposal không khả dụng",
      },
      {},
    );
  }
  state.jobStep = 0;
  return Promise.resolve(checked(parseProposalJob(structuredClone(JOB_STEPS[0]))));
}

function deleteDocument(documentId: string): Promise<void> {
  const state = live(documentId);
  if (state === null) return reject(404, "không tìm thấy tài liệu", { context: "document_delete" });
  state.deleted = true;
  return Promise.resolve();
}

/** Handler của tài liệu mẫu màn kết quả, chỉ dùng cho `MOCK_IMPORT_RESULT_DOC_ID` (xem `createApi`). */
export const mockImportResultApi = {
  listDocuments,
  getImportResult,
  getDocument,
  getDocumentPage,
  patchPageLines,
  restorePage,
  confirmPage,
  confirmImport,
  confirmDocument,
  startProposalJob,
  deleteDocument,
};
