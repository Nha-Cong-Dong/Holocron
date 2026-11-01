/**
 * Kiểm dạng dữ liệu của bộ nhập tài liệu ở ranh giới (§5.7, §6.5): `DocumentImport`, `DocumentSummary`,
 * `PageExtraction`. Thân JSON đọc như `unknown`, kiểm từng field bằng type guard rồi chuyển sang dạng hiển thị.
 *
 * Quy tắc chung để dữ liệu lạ không làm sập màn hình:
 * - thiếu field bắt buộc hay sai kiểu ở cấp tài liệu/trang ⇒ `null` (nơi gọi báo "phản hồi không hợp lệ");
 * - phần tử hỏng trong danh sách (tài liệu, trang tóm tắt, dòng, token lệch) bị bỏ và được đếm khi cần;
 * - mã enum lạ (`status`, `review_reasons`, `failure_reason`, `tier_used`…) được giữ dạng chuỗi để `lib/documents.ts`
 *   gắn nhãn chung, không ném lỗi.
 *
 * Mọi con số (điểm, số trang, số đếm) giữ nguyên giá trị backend trả; module này không tính thêm số nào.
 */

import { CHAPTER_CODE_PATTERN } from "@/lib/limits";
import type { DocumentStatus, PageStatus } from "@/types/holocron";

export type PageStatusView = PageStatus | "unknown";
export type DocumentStatusView = DocumentStatus | "unknown";

export interface PageSummaryView {
  pageNumber: number;
  status: PageStatusView;
  /** Mã tầng đọc (`TierUsed`) hoặc mã lạ; `null` khi trang chưa đọc xong hay đọc lỗi. */
  tierUsed: string | null;
  reviewReasons: string[];
  confirmed: boolean;
  /**
   * Vai trò trang (`PageRole`, §5.7) hoặc mã lạ; vắng khi backend không trả (backend cũ). Chỉ để hiển thị: frontend
   * không suy luật.
   */
  pageRole?: string;
  /** Trang bỏ mềm (GĐ0 0D): không vào chunk, không tính "cần xem"; vắng nghĩa là không bỏ mềm. */
  dropped?: boolean;
}

/** Thông tin chung của tài liệu (`DocumentMeta`, §5.8). */
export interface DocumentMetaView {
  docId: string;
  subjectCode: string;
  title: string;
  sourceKind: string;
  pageCount: number;
  /** `null` khi backend báo chưa biết (đề xuất `bool | None` của §5.8). */
  hasTextLayer: boolean | null;
  uploadedAt: string;
}

/** `DocumentImport` (§5.7). */
export interface DocumentView extends DocumentMetaView {
  filename: string;
  sizeBytes: number;
  /** `null` khi backend chưa trả field này (backend của nhánh chưa có `chapter_code`). */
  chapterCode: string | null;
  status: DocumentStatusView;
  pages: PageSummaryView[];
}

/** Một phần tử của `GET /v1/documents` (`DocumentSummary`, §6.5); số đếm do backend đếm. */
export interface DocumentSummaryView extends DocumentMetaView {
  status: DocumentStatusView;
  pagesPending: number;
  /** `pages_needs_review` do backend đếm (cần xem, không bỏ mềm, chưa xác nhận, §6.5). */
  pagesNeedsReview: number;
  pagesFailed: number;
  pagesConfirmed: number;
  /** `pages_dropped` do backend đếm (trang bỏ mềm); vắng khi backend không trả số hợp lệ. Giao diện chỉ hiển thị. */
  pagesDropped?: number;
}

export interface LineView {
  lineIndex: number;
  text: string;
  conf: number;
  engine: string;
  flags: string[];
  editedByHuman: boolean;
}

export interface MismatchView {
  lineIndex: number;
  primaryValue: string;
  secondaryValue: string | null;
}

export interface CrossCheckView {
  status: string;
  secondaryEngine: string | null;
  mismatches: MismatchView[];
}

/** Một trang được nhắc tới trong lý do bỏ mềm (`PageRef`, §5.7). */
export interface PageRefView {
  documentId: string;
  pageNumber: number;
}

/** `PageRoleEvidence` (§5.7): luật máy đã dùng và các trang liên quan; chỉ hiển thị, không số đo. */
export interface RoleEvidenceView {
  /** Mã luật (`PageRoleEvidence.rule`) hoặc mã lạ. */
  rule: string;
  relatedPages: PageRefView[];
}

/** `PageExtraction` (§5.7). */
export interface PageView {
  docId: string;
  pageNumber: number;
  version: number;
  status: PageStatusView;
  tierUsed: string | null;
  textScore: number | null;
  ocrScore: number | null;
  coverageRatio: number | null;
  inkOutsideTextRatio: number | null;
  hasFigure: boolean;
  hasTable: boolean;
  hasFormula: boolean;
  lines: LineView[];
  /** Có dòng sai dạng (hoặc trùng `line_index`) đã bị bỏ: giao diện báo để không im lặng mất dòng. */
  hasSkippedLines: boolean;
  crossCheck: CrossCheckView;
  reviewReasons: string[];
  failureReason: string | null;
  confirmed: boolean;
  confirmedAt: string | null;
  /** Mã vai trò trang (`PageRole`) hoặc mã lạ; thiếu thì `content`. */
  pageRole: string;
  roleEvidence: RoleEvidenceView | null;
  /** Trang bỏ mềm (GĐ0 0D): chưa vào chunk, không sửa hay xác nhận được cho tới khi khôi phục. */
  dropped: boolean;
  /** Đã khôi phục sau khi bị bỏ mềm. */
  restored: boolean;
}

/** Kết quả của `POST /v1/documents`: `created = false` khi backend trả 200 (cùng nội dung đã nhập cho cùng môn). */
export interface DocumentImportOutcome {
  created: boolean;
  document: DocumentView;
}

const PAGE_STATUSES: readonly PageStatus[] = ["pending", "ok", "needs_review", "failed"];
const DOCUMENT_STATUSES: readonly DocumentStatus[] = ["processing", "review", "confirmed", "failed"];

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isText(value: unknown): value is string {
  return typeof value === "string" && value.trim() !== "";
}

function isInteger(value: unknown, min: number): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= min;
}

/** Điểm trong [0, 1] như §5.7; giá trị ngoài miền hay sai kiểu thành `null` (không hiển thị số sai). */
function unitScore(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 1 ? value : null;
}

function pageStatusOf(value: unknown): PageStatusView {
  return PAGE_STATUSES.find((status) => status === value) ?? "unknown";
}

function documentStatusOf(value: unknown): DocumentStatusView {
  return DOCUMENT_STATUSES.find((status) => status === value) ?? "unknown";
}

/** Danh sách mã dạng chuỗi, bỏ phần tử không phải chuỗi và mã trùng, giữ thứ tự backend trả. */
function codeList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  const codes: string[] = [];
  for (const item of value) {
    if (isText(item) && !codes.includes(item)) codes.push(item);
  }
  return codes;
}

function textOrNull(value: unknown): string | null {
  return isText(value) ? value : null;
}

function parseMeta(value: unknown): DocumentMetaView | null {
  if (!isRecord(value)) return null;
  const { doc_id, subject_code, title, source_kind, page_count, has_text_layer, uploaded_at } = value;
  if (!isText(doc_id) || !isText(subject_code) || !isText(title) || !isText(source_kind)) return null;
  if (!isInteger(page_count, 1) || typeof uploaded_at !== "string") return null;
  return {
    docId: doc_id,
    subjectCode: subject_code,
    title,
    sourceKind: source_kind,
    pageCount: page_count,
    hasTextLayer: typeof has_text_layer === "boolean" ? has_text_layer : null,
    uploadedAt: uploaded_at,
  };
}

function parsePageSummary(value: unknown): PageSummaryView | null {
  if (!isRecord(value) || !isInteger(value.page_number, 1)) return null;
  return {
    pageNumber: value.page_number,
    status: pageStatusOf(value.status),
    tierUsed: textOrNull(value.tier_used),
    reviewReasons: codeList(value.review_reasons),
    confirmed: value.confirmed === true,
    // Field GĐ0 chỉ có khi backend trả (backend cũ không có): vắng nghĩa là trang thường.
    ...(isText(value.page_role) ? { pageRole: value.page_role } : {}),
    ...(typeof value.dropped === "boolean" ? { dropped: value.dropped } : {}),
  };
}

/** `DocumentImport` (§5.7); `null` khi thiếu `meta` hợp lệ, `filename` hay danh sách `pages`. */
export function parseDocumentImport(value: unknown): DocumentView | null {
  if (!isRecord(value)) return null;
  const meta = parseMeta(value.meta);
  const { filename, size_bytes, chapter_code, pages } = value;
  if (meta === null || !isText(filename) || !isInteger(size_bytes, 0) || !Array.isArray(pages)) return null;
  const parsedPages: PageSummaryView[] = [];
  for (const raw of pages) {
    const page = parsePageSummary(raw);
    if (page !== null) parsedPages.push(page);
  }
  return {
    ...meta,
    filename,
    sizeBytes: size_bytes,
    chapterCode: typeof chapter_code === "string" && CHAPTER_CODE_PATTERN.test(chapter_code) ? chapter_code : null,
    status: documentStatusOf(value.status),
    pages: parsedPages,
  };
}

function parseSummary(value: unknown): DocumentSummaryView | null {
  if (!isRecord(value)) return null;
  const meta = parseMeta(value.meta);
  const { pages_pending, pages_needs_review, pages_failed, pages_confirmed } = value;
  if (meta === null) return null;
  if (
    !isInteger(pages_pending, 0) ||
    !isInteger(pages_needs_review, 0) ||
    !isInteger(pages_failed, 0) ||
    !isInteger(pages_confirmed, 0)
  ) {
    return null;
  }
  return {
    ...meta,
    status: documentStatusOf(value.status),
    pagesPending: pages_pending,
    pagesNeedsReview: pages_needs_review,
    pagesFailed: pages_failed,
    pagesConfirmed: pages_confirmed,
    ...(isInteger(value.pages_dropped, 0) ? { pagesDropped: value.pages_dropped } : {}),
  };
}

/** `GET /v1/documents` (§6.5): `null` khi thân không phải mảng; phần tử hỏng bị bỏ, giữ thứ tự backend (mới nhất trước). */
export function parseDocumentSummaries(value: unknown): DocumentSummaryView[] | null {
  if (!Array.isArray(value)) return null;
  const summaries: DocumentSummaryView[] = [];
  const seen = new Set<string>();
  for (const raw of value) {
    const summary = parseSummary(raw);
    if (summary === null || seen.has(summary.docId)) continue;
    seen.add(summary.docId);
    summaries.push(summary);
  }
  return summaries;
}

function parseLine(value: unknown): LineView | null {
  if (!isRecord(value)) return null;
  const { line_index, text, conf, engine } = value;
  if (!isInteger(line_index, 0) || typeof text !== "string") return null;
  return {
    lineIndex: line_index,
    text,
    conf: unitScore(conf) ?? 0,
    engine: isText(engine) ? engine : "unknown",
    flags: codeList(value.flags),
    editedByHuman: value.edited_by_human === true,
  };
}

function parseMismatch(value: unknown): MismatchView | null {
  if (!isRecord(value)) return null;
  const { line_index, primary_value, secondary_value } = value;
  if (!isInteger(line_index, 0) || typeof primary_value !== "string") return null;
  return {
    lineIndex: line_index,
    primaryValue: primary_value,
    secondaryValue: typeof secondary_value === "string" ? secondary_value : null,
  };
}

function parseCrossCheck(value: unknown): CrossCheckView | null {
  if (!isRecord(value) || !isText(value.status)) return null;
  const mismatches: MismatchView[] = [];
  if (Array.isArray(value.mismatches)) {
    for (const raw of value.mismatches) {
      const mismatch = parseMismatch(raw);
      if (mismatch !== null) mismatches.push(mismatch);
    }
  }
  return { status: value.status, secondaryEngine: textOrNull(value.secondary_engine), mismatches };
}

function parsePageRef(value: unknown): PageRefView | null {
  if (!isRecord(value) || !isText(value.document_id) || !isInteger(value.page_number, 1)) return null;
  return { documentId: value.document_id, pageNumber: value.page_number };
}

/** `PageRoleEvidence` (§5.7); `null` khi vắng hoặc thiếu `rule`. Trang liên quan hỏng bị bỏ; không đọc số đo nào. */
function parseRoleEvidence(value: unknown): RoleEvidenceView | null {
  if (!isRecord(value) || !isText(value.rule)) return null;
  const relatedPages: PageRefView[] = [];
  if (Array.isArray(value.related_pages)) {
    for (const raw of value.related_pages) {
      const ref = parsePageRef(raw);
      if (ref !== null) relatedPages.push(ref);
    }
  }
  return { rule: value.rule, relatedPages };
}

/** `PageExtraction` (§5.7); `null` khi thiếu field định danh, `version`, danh sách `lines` hay `cross_check`. */
export function parsePageExtraction(value: unknown): PageView | null {
  if (!isRecord(value)) return null;
  const { document_id, page_number, version, lines } = value;
  if (!isText(document_id) || !isInteger(page_number, 1) || !isInteger(version, 1) || !Array.isArray(lines)) {
    return null;
  }
  const crossCheck = parseCrossCheck(value.cross_check);
  if (crossCheck === null) return null;
  const parsedLines: LineView[] = [];
  const seen = new Set<number>();
  for (const raw of lines) {
    const line = parseLine(raw);
    if (line === null || seen.has(line.lineIndex)) continue;
    seen.add(line.lineIndex);
    parsedLines.push(line);
  }
  return {
    docId: document_id,
    pageNumber: page_number,
    version,
    status: pageStatusOf(value.status),
    tierUsed: textOrNull(value.tier_used),
    textScore: unitScore(value.text_score),
    ocrScore: unitScore(value.ocr_score),
    coverageRatio: unitScore(value.coverage_ratio),
    inkOutsideTextRatio: unitScore(value.ink_outside_text_ratio),
    hasFigure: value.has_figure === true,
    hasTable: value.has_table === true,
    hasFormula: value.has_formula === true,
    lines: parsedLines,
    hasSkippedLines: parsedLines.length !== lines.length,
    crossCheck,
    reviewReasons: codeList(value.review_reasons),
    failureReason: textOrNull(value.failure_reason),
    confirmed: value.confirmed === true,
    confirmedAt: typeof value.confirmed_at === "string" ? value.confirmed_at : null,
    pageRole: textOrNull(value.page_role) ?? "content",
    roleEvidence: parseRoleEvidence(value.role_evidence),
    dropped: value.dropped === true,
    restored: value.restored === true,
  };
}
