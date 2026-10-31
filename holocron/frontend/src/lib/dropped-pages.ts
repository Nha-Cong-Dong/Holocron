/**
 * Trang bỏ mềm (GĐ0 0D; spec §5.7 "Khử nhiễu trang và vai trò trang", §6.5 `POST …/restore`): nhãn lý do, danh sách
 * trang liên quan, nhóm trang ở danh sách trang và phân loại lỗi khi khôi phục.
 *
 * Frontend chỉ hiển thị field backend trả (`page_role`, `role_evidence`); không suy luật, không đọc số đo (Jaccard,
 * khoảng cách dHash). Mã lạ (backend mới hơn frontend) rơi về câu chung, không lộ mã enum thô ra giao diện.
 */

import type { DocumentSummaryView, PageRefView, PageSummaryView, PageView } from "@/lib/documents-data";
import { ApiError, toUiError, type UiError } from "@/lib/errors";
import type { PageRole, PageRoleEvidence } from "@/types/holocron";

/** Câu cho mọi mã `PageRole`; bảng đủ khoá của `PageRole` (thiếu khoá là lỗi kiểu khi build). */
export const PAGE_ROLE_LABEL_VI: Record<PageRole, string> = {
  content: "Trang nội dung",
  cover_or_course_nav: "Trang bìa hoặc giới thiệu khoá học: trang này lặp lại ở các tài liệu khác của môn.",
  chapter_nav: "Trang điều hướng chương (mục lục, mở đầu chương), không có nội dung để học.",
  build_step: "Một bước trong chuỗi trang dựng dần: trang sau có đủ nội dung của trang này.",
  parallel_visual_series: "Một trang trong chuỗi trang gần giống nhau về hình, giữ lại để bạn xem.",
  exercise: "Trang bài tập.",
};

/** Câu cho mọi mã `PageRoleEvidence.rule`; ưu tiên hơn nhãn vai trò vì nêu đúng luật máy đã áp dụng. */
export const ROLE_RULE_LABEL_VI: Record<PageRoleEvidence["rule"], string> = {
  cover_or_course_nav: "Giống trang bìa hoặc giới thiệu khoá học ở tài liệu khác của môn.",
  chapter_nav: "Trông như trang điều hướng chương (mục lục, mở đầu chương), không có nội dung để học.",
  build_step: "Một bước trong chuỗi trang dựng dần: trang sau có đủ nội dung của trang này.",
  parallel_visual_series: "Cùng một chuỗi hình với các trang kề bên, giữ lại để bạn xem.",
  build_step_uncertain: "Có thể là một bước trong chuỗi trang dựng dần, nhưng máy chưa chắc nên giữ lại để bạn xem.",
};

/** Câu khi không có mã nào khớp (mã lạ hoặc thiếu bằng chứng): không lộ mã thô. */
export const DROPPED_GENERIC_VI = "Trang này được ẩn tự động vì máy cho rằng không có nội dung cần học.";

/** Tra bảng bằng mã dạng chuỗi bất kỳ; chỉ khớp khoá riêng của bảng (không khớp `constructor`, `toString`…). */
function lookup(table: Readonly<Record<string, string>>, code: string | null): string | null {
  if (code === null) return null;
  const entry = Object.entries(table).find(([key]) => key === code);
  return entry === undefined ? null : entry[1];
}

/**
 * Lý do một trang bị ẩn: câu của `role_evidence.rule` nếu biết, không thì câu của `page_role`, không thì câu chung.
 * Vai trò `content` hay `exercise` không phải lý do bỏ mềm nên dùng câu chung.
 */
export function droppedReasonLabel(pageRole: string, rule: string | null): string {
  const byRule = lookup(ROLE_RULE_LABEL_VI, rule);
  if (byRule !== null) return byRule;
  if (pageRole === "content" || pageRole === "exercise") return DROPPED_GENERIC_VI;
  return lookup(PAGE_ROLE_LABEL_VI, pageRole) ?? DROPPED_GENERIC_VI;
}

/** Một trang liên quan, đủ để vẽ liên kết: không lộ mã tài liệu ra chữ. */
export interface RelatedPageLink {
  documentId: string;
  pageNumber: number;
  /** `true` khi cùng tài liệu đang xem. */
  sameDocument: boolean;
  label: string;
}

/** Trang liên quan (`role_evidence.related_pages`) theo thứ tự backend trả; trang ở tài liệu khác có ghi chú. */
export function relatedPageLinks(related: readonly PageRefView[], currentDocumentId: string): RelatedPageLink[] {
  return related.map((ref) => {
    const sameDocument = ref.documentId === currentDocumentId;
    return {
      documentId: ref.documentId,
      pageNumber: ref.pageNumber,
      sameDocument,
      label: sameDocument ? `Trang ${ref.pageNumber}` : `Trang ${ref.pageNumber} (tài liệu khác của môn)`,
    };
  });
}

type DroppedFlag = Pick<PageSummaryView, "dropped">;

/** Trang đang bỏ mềm (`dropped` vắng nghĩa là không). */
export function isDropped(page: DroppedFlag): boolean {
  return page.dropped === true;
}

/** Tách trang của tài liệu thành hai nhóm giữ thứ tự backend trả: trang giữ lại và trang "Đã ẩn tự động". */
export function splitDroppedPages<T extends DroppedFlag>(pages: readonly T[]): { kept: T[]; dropped: T[] } {
  return { kept: pages.filter((page) => !isDropped(page)), dropped: pages.filter(isDropped) };
}

/**
 * Bản tài liệu không có trang bỏ mềm, cho các điều kiện "xác nhận cả tài liệu" và nháp chưa lưu: trang bỏ mềm không
 * xác nhận được và không chặn bước chốt (§5.7).
 */
export function withoutDropped<T extends { pages: readonly DroppedFlag[] }>(document: T): T {
  return { ...document, pages: document.pages.filter((page) => !isDropped(page)) };
}

/** Câu thành công sau khi khôi phục (vùng thông báo `role="status"`). */
export const RESTORE_DONE_VI =
  "Đã khôi phục trang. Trang này giờ như mọi trang khác: xem lại rồi xác nhận thì mới vào kho tri thức.";

/** 409 lệch phiên bản: trang đã đổi ở nơi khác, nút "Tải lại bản mới nhất". */
const RESTORE_CHANGED_VI = "Trang đã đổi (có thể đã được khôi phục hoặc sửa ở nơi khác). Tải lại để xem bản mới nhất.";

/** 409 tài liệu đã chốt hoặc đang chốt: văn bản tài liệu đã đóng, tải lại không giúp được gì. */
const RESTORE_CLOSED_VI =
  "Không khôi phục được trang này vì tài liệu đã được chấp nhận hoặc đang được chốt. Muốn lấy lại trang, xoá tài liệu rồi nhập lại file.";

/** 404, 422 và lỗi không rõ khi khôi phục: câu chung, không có nút (gửi lại không chắc giúp được). */
const RESTORE_GENERIC_VI = "Chưa khôi phục được trang này. Tải lại danh sách tài liệu rồi thử lại.";

function restoreError(kind: UiError["kind"], message: string, action: UiError["action"]): UiError {
  return { kind, message, action, issues: [], capability: null };
}

/** Kết quả đối chiếu sau một 409 khi khôi phục. */
export type RestoreConflict = "version_changed" | "document_closed";

/**
 * Phân biệt hai loại 409 của khôi phục (§6.5) mà không đọc chữ `detail` của backend: đọc lại trang. Trang đã đổi
 * version hoặc không còn bỏ mềm ⇒ lệch phiên bản; trang vẫn nguyên (cùng version, còn bỏ mềm) mà backend từ chối ⇒
 * văn bản tài liệu đã đóng. Không đọc lại được ⇒ coi là lệch phiên bản (có nút tải lại, không mất gì).
 */
export async function classifyRestoreConflict(
  load: () => Promise<Pick<PageView, "version" | "dropped">>,
  expectedVersion: number,
): Promise<RestoreConflict> {
  try {
    const current = await load();
    return current.version === expectedVersion && current.dropped ? "document_closed" : "version_changed";
  } catch {
    return "version_changed";
  }
}

/**
 * Lỗi hiển thị khi khôi phục thất bại. `status` là mã HTTP (`null` khi không tới được backend); `original` là lỗi đã
 * phân loại của lời gọi, giữ nguyên cho lỗi mạng và lỗi máy chủ (câu và nút thử lại của nó). 409 cần `conflict` từ
 * `classifyRestoreConflict`; 404, 422 và mã khác dùng câu chung của khôi phục (câu của luồng sửa dòng nói sai việc).
 */
export function restoreFailure(input: {
  status: number | null;
  conflict: RestoreConflict | null;
  original: UiError;
}): UiError {
  if (input.status === null || input.status >= 500) return input.original;
  if (input.status === 409) {
    return input.conflict === "document_closed"
      ? restoreError("state_conflict", RESTORE_CLOSED_VI, "none")
      : restoreError("version_conflict", RESTORE_CHANGED_VI, "reload");
  }
  return restoreError("unknown", RESTORE_GENERIC_VI, "none");
}

/**
 * Dòng số đếm của tài liệu đã đọc xong ở danh sách tài liệu. Chỉ chép số `DocumentSummary` trả (đã xác nhận, cần xem,
 * đọc lỗi, đã ẩn tự động); KHÔNG tự tính số trang giữ lại. `pages_dropped` không hợp lệ thì bỏ đoạn đó thay vì hiện
 * số sai.
 */
export function documentCountsLine(
  summary: Pick<
    DocumentSummaryView,
    "pagesConfirmed" | "pageCount" | "pagesNeedsReview" | "pagesFailed" | "pagesDropped"
  >,
): string {
  const parts = [
    `${summary.pagesConfirmed}/${summary.pageCount} trang đã xác nhận`,
    `${summary.pagesNeedsReview} trang cần xem lại`,
    `${summary.pagesFailed} trang đọc lỗi`,
  ];
  if (summary.pagesDropped !== undefined) parts.push(`${summary.pagesDropped} trang đã ẩn tự động`);
  return parts.join(" · ");
}

/**
 * Lỗi hiển thị cho một lời gọi khôi phục đã ném `caught`: lấy mã HTTP từ `ApiError`, đọc lại trang để phân loại 409
 * (`classifyRestoreConflict`) rồi chọn câu (`restoreFailure`). Lỗi không phải `ApiError` coi như không tới được backend.
 */
export async function explainRestoreFailure(
  caught: unknown,
  page: { load: () => Promise<Pick<PageView, "version" | "dropped">>; expectedVersion: number },
): Promise<UiError> {
  const status = caught instanceof ApiError ? caught.status : null;
  const conflict = status === 409 ? await classifyRestoreConflict(page.load, page.expectedVersion) : null;
  return restoreFailure({ status, conflict, original: toUiError(caught) });
}
