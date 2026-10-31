/**
 * Màn kết quả nhập tài liệu (T1e, T1b; spec §5.7 `DocumentImport`, §5.8 `ProposalJob`, §6.5 "Màn kết quả nhập" và
 * `POST …/confirm`): kiểm dạng ở ranh giới, nhãn tiếng Việt, điều kiện nút "Chấp nhận tài liệu" và phân loại 409.
 *
 * Mọi con số (số trang, số lô, số cặp) giữ nguyên giá trị backend trả; module này KHÔNG đếm lại từ `pages` (I1).
 * Số sai dạng (âm, không nguyên, không phải số) thành `undefined` và giao diện bỏ đoạn hiển thị số đó. Danh sách sai
 * dạng thành rỗng; mã enum lạ giữ dạng chuỗi và rơi về nhãn chung, không lộ mã thô ra giao diện.
 *
 * Các field mới của `DocumentImport` chưa có trong `types/holocron.ts` (`types/contract-pending.ts`): backend cũ không
 * trả chúng thì parse vẫn thành công với số `undefined`, danh sách rỗng và không có job.
 */

import { type DocumentView, parseDocumentImport } from "@/lib/documents-data";
import { isDropped } from "@/lib/dropped-pages";
import type { UiError } from "@/lib/errors";
import { reviewQueue } from "@/lib/review-queue";

/** Trạng thái của `ProposalJob` (§5.8) hoặc `unknown` khi backend trả mã lạ. */
export type JobStatusView = "queued" | "running" | "succeeded" | "partial" | "failed" | "cancelled" | "unknown";

const JOB_STATUSES: readonly Exclude<JobStatusView, "unknown">[] = [
  "queued",
  "running",
  "succeeded",
  "partial",
  "failed",
  "cancelled",
];

/** Một lý do `rejected_by_reason` kèm số đề xuất bị loại (do backend đếm). */
export interface RejectedReasonView {
  reason: string;
  count: number;
}

/** `ProposalJob` (§5.8) dạng hiển thị; số `undefined` khi backend trả số sai dạng. */
export interface ProposalJobView {
  jobId: string;
  status: JobStatusView;
  batchesTotal?: number;
  batchesDone?: number;
  batchesFailed?: number;
  proposedCount?: number;
  rejectedCount?: number;
  /** Theo thứ tự khoá backend trả; chỉ mục có số hợp lệ ≥ 1. */
  rejectedByReason: RejectedReasonView[];
}

/** Một lý do cần xem (`ReviewReasonCount`) hoặc một vai trò bỏ mềm (`DroppedRoleCount`) với danh sách trang. */
export interface PageGroupView {
  /** Mã `ReviewReason` hoặc `PageRole`, hoặc mã lạ. */
  reason: string;
  count: number;
  /** Số trang tăng dần, không trùng, chỉ phần tử hợp lệ. */
  pages: number[];
}

/** Kết quả nhập của một tài liệu: `DocumentImport` (§5.7) với các field T1e. */
export interface ImportResultView {
  document: DocumentView;
  pagesKept?: number;
  pagesDropped?: number;
  pagesNeedsReview?: number;
  pagesAutoConfirmed?: number;
  pagesKeptUnconfirmed?: number;
  pagesFailed?: number;
  reviewReasons: PageGroupView[];
  droppedRoles: PageGroupView[];
  job: ProposalJobView | null;
  /** `proposal_job_absent_reason` (mã hoặc mã lạ); `null` khi backend không trả. */
  absentReason: string | null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Số nguyên ≥ 0 hoặc `undefined`. */
function count(value: unknown): number | undefined {
  return typeof value === "number" && Number.isInteger(value) && value >= 0 ? value : undefined;
}

function pageNumbers(value: unknown): number[] {
  if (!Array.isArray(value)) return [];
  const pages: number[] = [];
  for (const item of value) {
    if (typeof item === "number" && Number.isInteger(item) && item >= 1 && !pages.includes(item)) pages.push(item);
  }
  return pages.sort((a, b) => a - b);
}

function parseGroups(value: unknown): PageGroupView[] {
  if (!Array.isArray(value)) return [];
  const groups: PageGroupView[] = [];
  for (const raw of value) {
    if (!isRecord(raw) || typeof raw.reason !== "string" || raw.reason.trim() === "") continue;
    const total = count(raw.count);
    if (total === undefined || total < 1) continue;
    groups.push({ reason: raw.reason, count: total, pages: pageNumbers(raw.pages) });
  }
  return groups;
}

function jobStatusOf(value: unknown): JobStatusView {
  return JOB_STATUSES.find((status) => status === value) ?? "unknown";
}

function parseRejected(value: unknown): RejectedReasonView[] {
  if (!isRecord(value)) return [];
  const rejected: RejectedReasonView[] = [];
  for (const [reason, raw] of Object.entries(value)) {
    const total = count(raw);
    if (total !== undefined && total >= 1) rejected.push({ reason, count: total });
  }
  return rejected;
}

/** `ProposalJob` (§5.8); `null` khi thiếu `job_id` hay `status` dạng chuỗi. */
export function parseProposalJob(value: unknown): ProposalJobView | null {
  if (!isRecord(value)) return null;
  if (typeof value.job_id !== "string" || value.job_id === "" || typeof value.status !== "string") return null;
  const view: ProposalJobView = {
    jobId: value.job_id,
    status: jobStatusOf(value.status),
    rejectedByReason: parseRejected(value.rejected_by_reason),
  };
  const fields = [
    ["batchesTotal", "batches_total"],
    ["batchesDone", "batches_done"],
    ["batchesFailed", "batches_failed"],
    ["proposedCount", "proposed_count"],
    ["rejectedCount", "rejected_count"],
  ] as const;
  for (const [target, source] of fields) {
    const parsed = count(value[source]);
    if (parsed !== undefined) view[target] = parsed;
  }
  return view;
}

/** Chép số hợp lệ vào kết quả; số sai dạng để vắng. */
function withCount<K extends keyof ImportResultView>(view: ImportResultView, key: K, raw: unknown): void {
  const parsed = count(raw);
  if (parsed !== undefined) Object.assign(view, { [key]: parsed });
}

/**
 * `DocumentImport` (§5.7) với field T1e; `null` khi phần nền (`meta`, `filename`, `pages`) hỏng. Field T1e hỏng chỉ
 * làm vắng phần tương ứng, không làm cả màn lỗi.
 */
export function parseImportResult(value: unknown): ImportResultView | null {
  const document = parseDocumentImport(value);
  if (document === null || !isRecord(value)) return null;
  const view: ImportResultView = {
    document,
    reviewReasons: parseGroups(value.review_reason_counts),
    droppedRoles: parseGroups(value.dropped_role_counts),
    job: parseProposalJob(value.proposal_job),
    absentReason:
      typeof value.proposal_job_absent_reason === "string" && value.proposal_job_absent_reason !== ""
        ? value.proposal_job_absent_reason
        : null,
  };
  withCount(view, "pagesKept", value.pages_kept);
  withCount(view, "pagesDropped", value.pages_dropped);
  withCount(view, "pagesNeedsReview", value.pages_needs_review);
  withCount(view, "pagesAutoConfirmed", value.pages_auto_confirmed);
  withCount(view, "pagesKeptUnconfirmed", value.pages_kept_unconfirmed);
  withCount(view, "pagesFailed", value.pages_failed);
  return view;
}

// ------------------------------------------------------------------------------------------- dòng tóm tắt

/**
 * Dòng tóm tắt "giữ · tự xác nhận · cần xem · đã ẩn · lỗi": chỉ chép số backend trả, theo thứ tự cố định. Số vắng
 * (sai dạng hoặc backend không trả) thì bỏ đoạn đó thay vì hiện số sai.
 */
export function importSummaryLine(result: ImportResultView): string {
  const parts: string[] = [];
  if (result.pagesKept !== undefined) parts.push(`${result.pagesKept} trang giữ lại`);
  if (result.pagesAutoConfirmed !== undefined) parts.push(`${result.pagesAutoConfirmed} trang tự xác nhận`);
  if (result.pagesNeedsReview !== undefined) parts.push(`${result.pagesNeedsReview} trang cần xem`);
  if (result.pagesDropped !== undefined) parts.push(`${result.pagesDropped} trang đã ẩn tự động`);
  if (result.pagesFailed !== undefined) parts.push(`${result.pagesFailed} trang đọc lỗi`);
  return parts.join(" · ");
}

/** Chữ số trang cho một lý do: "trang 3", "trang 3, 5, 8". */
export function pagesText(pages: readonly number[]): string {
  return pages.length === 0 ? "" : `trang ${pages.join(", ")}`;
}

/** Nhãn ngắn của vai trò trang bị ẩn; mã lạ rơi về nhãn chung, không lộ mã thô. */
export const DROPPED_ROLE_SHORT_LABEL_VI: Record<string, string> = {
  cover_or_course_nav: "Trang bìa hoặc giới thiệu khoá học",
  chapter_nav: "Trang điều hướng chương",
  build_step: "Trang dựng dần, trang sau đã có đủ nội dung",
};

export const DROPPED_ROLE_UNKNOWN_VI = "Trang không có nội dung cần học";

/** Nhãn của một vai trò bỏ mềm; chỉ khớp khoá riêng của bảng (không khớp `constructor`…). */
export function droppedRoleLabel(code: string): string {
  const entry = Object.entries(DROPPED_ROLE_SHORT_LABEL_VI).find(([key]) => key === code);
  return entry === undefined ? DROPPED_ROLE_UNKNOWN_VI : entry[1];
}

/** Một nhóm trang đã ẩn tự động ở màn kết quả: nhãn vai trò và số trang (theo thứ tự backend trả). */
export interface DroppedGroupView {
  key: string;
  label: string;
  pages: number[];
}

/**
 * Nhóm trang "Đã ẩn tự động": theo `dropped_role_counts` của backend; backend không trả danh sách này (backend cũ hay
 * field sai dạng) thì lấy các trang `dropped` của `pages[]` thành một nhóm chung, để người dùng vẫn khôi phục được.
 */
export function droppedGroups(result: ImportResultView): DroppedGroupView[] {
  const fromRoles = result.droppedRoles
    .filter((group) => group.pages.length > 0)
    .map((group) => ({ key: group.reason, label: droppedRoleLabel(group.reason), pages: group.pages }));
  if (fromRoles.length > 0) return fromRoles;
  const pages = result.document.pages.filter((page) => isDropped(page)).map((page) => page.pageNumber);
  return pages.length === 0 ? [] : [{ key: "dropped", label: DROPPED_ROLE_UNKNOWN_VI, pages }];
}

/** Số trang đầu tiên cần xem để mở sau khi chấp nhận: ưu tiên hàng đợi từ `pages[]`, rồi danh sách trang của lý do. */
export function firstNeedsReviewPage(result: ImportResultView): number | null {
  const fromQueue = reviewQueue(result.document)[0];
  if (fromQueue !== undefined) return fromQueue;
  const all = result.reviewReasons.flatMap((group) => group.pages);
  return all.length === 0 ? null : Math.min(...all);
}

// ------------------------------------------------------------------------------------- nút "Chấp nhận tài liệu"

/**
 * Trạng thái nút "Chấp nhận tài liệu", chỉ theo `status` và các số backend trả (`pages_kept_unconfirmed`,
 * `pages_auto_confirmed`, `pages_needs_review`):
 * - `enabled`: còn trang giữ chưa xác nhận hoặc trang tự xác nhận chờ chốt (chỉ bước chốt của `POST …/confirm` đóng
 *   được tài liệu có trang tự xác nhận, §5.7);
 * - `accepted`: tài liệu đã `confirmed`; `reading`: còn đang đọc;
 * - `review_first`: không còn gì để chấp nhận, chỉ còn trang cần xem (xác nhận từng trang trước);
 * - `nothing`: không còn trang nào để chấp nhận; `unknown`: trạng thái hay số đếm không rõ (backend cũ hoặc lạ).
 */
export type AcceptGate = "enabled" | "accepted" | "reading" | "review_first" | "nothing" | "unknown";

export function acceptGate(result: ImportResultView): AcceptGate {
  const { status } = result.document;
  if (status === "confirmed") return "accepted";
  if (status === "processing") return "reading";
  if (status === "unknown") return "unknown";
  const unconfirmed = result.pagesKeptUnconfirmed;
  const auto = result.pagesAutoConfirmed;
  if (unconfirmed === undefined && auto === undefined) return "unknown";
  if ((unconfirmed ?? 0) > 0 || (auto ?? 0) > 0) return "enabled";
  return (result.pagesNeedsReview ?? 0) > 0 ? "review_first" : "nothing";
}

/** Câu giải thích bên cạnh nút khi nút khoá; `null` khi nút bật. */
export function acceptGateText(gate: AcceptGate, result: ImportResultView): string | null {
  switch (gate) {
    case "enabled":
      return null;
    case "accepted":
      return "Tài liệu đã được chấp nhận.";
    case "reading":
      return "Holocron đang đọc tài liệu. Chờ đọc xong rồi mới chấp nhận được.";
    case "review_first":
      return `Còn ${result.pagesNeedsReview ?? 0} trang cần xem. Xem lại các trang đó trước khi chấp nhận.`;
    case "nothing":
      return "Không còn trang nào để chấp nhận.";
    case "unknown":
      return "Chưa rõ trạng thái tài liệu. Tải lại màn này để xem bản mới nhất.";
  }
}

/** Kết quả sau khi `POST …/confirm` trả 200. */
export type AcceptOutcome =
  | { kind: "accepted" }
  | {
      /** Tài liệu chưa `confirmed` vì còn trang cần xem: KHÔNG hiện bước job. */
      kind: "pages_left";
      message: string;
      /** Trang cần xem đầu tiên để mở ngay; `null` khi backend không cho biết trang nào. */
      firstPage: number | null;
    };

/**
 * Phân loại bản `DocumentImport` backend trả sau khi chấp nhận. `status = "confirmed"` ⇒ chấp nhận xong và hiện bước
 * job; khác ⇒ "Còn N trang cần xem" với N là `pages_needs_review` do backend trả (không tự đếm).
 */
export function acceptOutcome(after: ImportResultView): AcceptOutcome {
  if (after.document.status === "confirmed") return { kind: "accepted" };
  const remaining = after.pagesNeedsReview;
  const message =
    remaining !== undefined && remaining > 0
      ? `Còn ${remaining} trang cần xem. Xem lại và xác nhận các trang đó, rồi bấm “Chấp nhận tài liệu” lần nữa.`
      : "Tài liệu chưa chốt xong. Tải lại màn này để xem trạng thái mới nhất.";
  return { kind: "pages_left", message, firstPage: firstNeedsReviewPage(after) };
}

// ------------------------------------------------------------------------------------------- 409 khi chấp nhận

/** Loại 409 của `POST …/confirm`, phân biệt bằng cách đọc lại tài liệu (không đọc chữ `detail`). */
export type ConfirmConflict = "changed" | "reading" | "settling" | "legacy";

/**
 * Phân loại 409 (§6.5) bằng số backend trả khi đọc lại tài liệu sau lỗi: tài liệu chưa có chương (nhập từ bản cũ) ⇒
 * `legacy`; còn trang đang đọc ⇒ `reading`; trạng thái hay số đếm đã đổi so với bản đang xem ⇒ `changed` (lệch bản,
 * tải lại); còn lại, hoặc không đọc lại được, ⇒ `settling` (đang chốt, thử lại sau).
 */
export function classifyConfirmConflict(before: ImportResultView, after: ImportResultView | null): ConfirmConflict {
  if (after === null) return "settling";
  if (after.document.chapterCode === null) return "legacy";
  if (after.document.status === "processing") return "reading";
  const changed =
    after.document.status !== before.document.status ||
    after.pagesNeedsReview !== before.pagesNeedsReview ||
    after.pagesKeptUnconfirmed !== before.pagesKeptUnconfirmed ||
    after.pagesAutoConfirmed !== before.pagesAutoConfirmed;
  return changed ? "changed" : "settling";
}

/** Lỗi hiển thị cho một loại 409 của chấp nhận. */
export function confirmConflictError(conflict: ConfirmConflict): UiError {
  const base = { issues: [], capability: null };
  switch (conflict) {
    case "changed":
      return {
        ...base,
        kind: "version_conflict",
        action: "reload",
        message:
          "Tài liệu vừa đổi ở nơi khác (có trang được xác nhận, khôi phục hoặc xử lý). Tải lại để xem bản mới nhất rồi chấp nhận lại.",
      };
    case "reading":
      return {
        ...base,
        kind: "state_conflict",
        action: "reload",
        message: "Tài liệu còn trang đang đọc. Chờ đọc xong, tải lại rồi chấp nhận.",
      };
    case "settling":
      return {
        ...base,
        kind: "busy",
        action: "retry",
        message: "Holocron đang chốt tài liệu này. Chờ vài giây rồi thử lại.",
      };
    case "legacy":
      return {
        ...base,
        kind: "state_conflict",
        action: "none",
        message:
          "Tài liệu này được nhập từ bản Holocron cũ chưa có chương nên không chấp nhận được. Xoá tài liệu rồi nhập lại file.",
      };
  }
}

// ----------------------------------------------------------------------------------------------- job đề xuất cặp

/** Nhãn lý do một đề xuất cặp bị loại (`ProposalRejectReason`, §5.8); bảng đủ 13 mã. */
export const REJECT_REASON_LABEL_VI: Record<string, string> = {
  quote_not_verbatim: "Trích dẫn không nguyên văn trong tài liệu",
  unknown_chunk: "Đoạn nguồn không còn trong kho tri thức",
  invalid_term: "Thuật ngữ không hợp lệ",
  invalid_quote: "Trích dẫn định nghĩa không hợp lệ",
  duplicate: "Trùng cặp đã có",
  chunk_limit: "Vượt số cặp tối đa của một đoạn",
  proposal_limit: "Vượt số cặp tối đa của một lần đề xuất",
  unknown_span: "Câu được chọn không có trong đoạn",
  span_cross_chunk: "Các câu định nghĩa không liền nhau hoặc nằm ở nhiều đoạn",
  term_span_cross_chunk: "Thuật ngữ và định nghĩa nằm ở hai đoạn khác nhau",
  term_not_in_span: "Thuật ngữ không nằm trong câu được chọn",
  anchor_not_found: "Không tìm thấy điểm đầu hoặc cuối của định nghĩa",
  anchor_ambiguous: "Điểm đầu hoặc cuối của định nghĩa xuất hiện nhiều lần",
};

export const REJECT_REASON_UNKNOWN_VI = "Lý do khác";

/** Nhãn của một lý do bị loại; mã lạ rơi về nhãn chung. */
export function rejectReasonLabel(code: string): string {
  const entry = Object.entries(REJECT_REASON_LABEL_VI).find(([key]) => key === code);
  return entry === undefined ? REJECT_REASON_UNKNOWN_VI : entry[1];
}

/** Job còn chạy (`queued`, `running`); mã lạ coi là không còn chạy để không hỏi lại mãi. */
export function isJobActive(status: JobStatusView): boolean {
  return status === "queued" || status === "running";
}

/** Có tiếp tục hỏi tiến độ sau bản này không: có job và job còn chạy. */
export function shouldPollJob(result: ImportResultView): boolean {
  return result.job !== null && isJobActive(result.job.status);
}

/** Câu trạng thái của job theo `status` và số lô backend trả. */
export function jobHeadline(job: ProposalJobView): string {
  switch (job.status) {
    case "queued":
      return "Đang chờ tới lượt đề xuất cặp khái niệm…";
    case "running":
      return job.batchesDone !== undefined && job.batchesTotal !== undefined
        ? `Đang đề xuất cặp khái niệm: lô ${job.batchesDone}/${job.batchesTotal}…`
        : "Đang đề xuất cặp khái niệm…";
    case "succeeded":
      return "Đã đề xuất xong các cặp khái niệm.";
    case "partial":
      return "Đã đề xuất xong một phần: có lô bị lỗi, các cặp của lô còn lại vẫn được giữ.";
    case "failed":
      return "Chưa đề xuất được cặp nào: mọi lô đều lỗi.";
    case "cancelled":
      return "Việc đề xuất cặp khái niệm đã bị huỷ.";
    case "unknown":
      return "Việc đề xuất cặp khái niệm ở trạng thái giao diện này chưa nhận ra.";
  }
}

/** Các dòng số của job (lô xong, lô lỗi, cặp đã đề xuất, bị loại); số vắng thì bỏ dòng đó. */
export function jobLines(job: ProposalJobView): string[] {
  const lines: string[] = [];
  if (job.batchesDone !== undefined && job.batchesTotal !== undefined) {
    lines.push(`Lô đã xong: ${job.batchesDone}/${job.batchesTotal}`);
  }
  if (job.batchesFailed !== undefined) lines.push(`Lô lỗi: ${job.batchesFailed}`);
  if (job.proposedCount !== undefined) lines.push(`Cặp đã đề xuất: ${job.proposedCount}`);
  if (job.rejectedCount !== undefined) lines.push(`Đề xuất bị loại: ${job.rejectedCount}`);
  return lines;
}

/** Chi tiết các đề xuất bị loại dạng "nhãn: số", theo thứ tự backend trả. */
export function rejectedLines(job: ProposalJobView): string[] {
  return job.rejectedByReason.map((entry) => `${rejectReasonLabel(entry.reason)}: ${entry.count}`);
}

/** Cách hiển thị khi tài liệu đã chấp nhận mà không có `proposal_job`. */
export type AbsentJobView =
  | { kind: "hidden" }
  | { kind: "message"; text: string }
  | { kind: "configure_llm"; text: string }
  | { kind: "restart"; text: string };

/**
 * Bảng đủ 5 `proposal_job_absent_reason` (§5.7): `not_confirmed` ẩn khối job; `past_exam`, `no_chunks` chỉ có câu;
 * `llm_unavailable` có câu và đường tới tự bôi ở tab Khái niệm; `not_retained` có nút "Đề xuất lại". `null` ẩn khối;
 * mã lạ rơi về câu chung, không lộ mã.
 */
export function absentJobView(reason: string | null): AbsentJobView {
  switch (reason) {
    case null:
    case "not_confirmed":
      return { kind: "hidden" };
    case "past_exam":
      return { kind: "message", text: "Đề thi cũ không dùng để đề xuất cặp khái niệm." };
    case "no_chunks":
      return { kind: "message", text: "Tài liệu này không có đoạn văn nào để đề xuất cặp khái niệm." };
    case "llm_unavailable":
      return {
        kind: "configure_llm",
        text: "Cần cấu hình LLM để tự đề xuất cặp khái niệm. Bạn vẫn tự bôi chọn định nghĩa ở tab Khái niệm.",
      };
    case "not_retained":
      return {
        kind: "restart",
        text: "Holocron không còn lần đề xuất cặp nào của tài liệu này (ví dụ sau khi khởi động lại). Bạn có thể đề xuất lại.",
      };
    default:
      return { kind: "message", text: "Chưa có đề xuất cặp khái niệm cho tài liệu này." };
  }
}
