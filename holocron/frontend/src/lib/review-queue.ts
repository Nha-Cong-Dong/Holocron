/**
 * Hàng đợi xem lại của một tài liệu (design-handoff/README.md màn 6; spec §5.7, §6.5). Hàng đợi tính từ `pages[]` của
 * `GET /v1/documents/{id}`: trang `needs_review` chưa xác nhận. KHÔNG dùng `pages_needs_review` của danh sách tài liệu
 * vì backend đếm cả trang đã xác nhận.
 *
 * "Xong" chỉ khi `DocumentImport.status === "confirmed"` (backend chốt trạng thái, §5.7): còn trang đọc tốt chưa xác
 * nhận, trang đang đọc hay tài liệu chưa chốt thì là việc còn lại, không chúc mừng. Chỉ đếm phần tử backend trả.
 */

import type { DocumentView, PageSummaryView } from "@/lib/documents-data";

type PagesInput = Pick<DocumentView, "pages">;

function needsReview(page: Pick<PageSummaryView, "status" | "confirmed">): boolean {
  return page.status === "needs_review" && !page.confirmed;
}

/** Số trang cần xem lại (chưa xác nhận), theo thứ tự backend trả. */
export function reviewQueue(document: PagesInput): number[] {
  return document.pages.filter(needsReview).map((page) => page.pageNumber);
}

export interface ReviewProgress {
  /** Trang `needs_review` đã xác nhận. */
  done: number;
  /** Mọi trang `needs_review` (đã hoặc chưa xác nhận). */
  total: number;
}

/** Tiến độ xem lại: đếm trang `needs_review` đã xác nhận trên mọi trang `needs_review`. */
export function reviewProgress(document: PagesInput): ReviewProgress {
  const flagged = document.pages.filter((page) => page.status === "needs_review");
  return { done: flagged.filter((page) => page.confirmed).length, total: flagged.length };
}

/** Vị trí (tính từ 1) của trang trong hàng đợi; `null` khi trang không nằm trong hàng đợi. */
export function queuePosition(queue: readonly number[], page: number): number | null {
  const index = queue.indexOf(page);
  return index === -1 ? null : index + 1;
}

export interface QueueNeighbors {
  previous: number | null;
  next: number | null;
}

/**
 * Trang cần xem trước và sau trang đang mở (←/→). Trang đang mở không nằm trong hàng đợi (ví dụ trang đọc tốt) thì lấy
 * trang cần xem gần nhất ở mỗi phía, theo thứ tự hàng đợi.
 */
export function queueNeighbors(queue: readonly number[], page: number): QueueNeighbors {
  const index = queue.indexOf(page);
  if (index !== -1) {
    return { previous: queue[index - 1] ?? null, next: queue[index + 1] ?? null };
  }
  const before = queue.filter((item) => item < page);
  const after = queue.filter((item) => item > page);
  return { previous: before[before.length - 1] ?? null, next: after[0] ?? null };
}

/** Trang mở tiếp sau khi xác nhận trang đang xem: trang cần xem kế tiếp, hết thì quay lại trang cần xem đầu tiên. */
export function nextAfterConfirm(queue: readonly number[], page: number): number | null {
  const rest = queue.filter((item) => item !== page);
  return rest.find((item) => item > page) ?? rest[0] ?? null;
}

export interface RemainingWork {
  needsReview: number;
  /** Trang đọc tốt chưa xác nhận: xác nhận bằng "Xác nhận các trang đọc tốt". */
  okUnconfirmed: number;
  pending: number;
  /** Trang đọc lỗi: không xác nhận được, không chặn việc chốt tài liệu (§5.7). */
  failed: number;
  /** Trang có trạng thái giao diện chưa nhận ra. */
  unknown: number;
}

export type ReviewOutcome = { kind: "done" } | { kind: "remaining"; work: RemainingWork };

/** Tài liệu đã xong khi và chỉ khi backend báo `confirmed`; còn lại thì đếm việc còn lại theo `pages[]`. */
export function reviewOutcome(document: Pick<DocumentView, "status" | "pages">): ReviewOutcome {
  if (document.status === "confirmed") return { kind: "done" };
  const work: RemainingWork = { needsReview: 0, okUnconfirmed: 0, pending: 0, failed: 0, unknown: 0 };
  for (const page of document.pages) {
    if (page.status === "pending") work.pending += 1;
    else if (page.status === "failed") work.failed += 1;
    else if (page.status === "unknown") work.unknown += 1;
    else if (page.confirmed) continue;
    else if (page.status === "needs_review") work.needsReview += 1;
    else work.okUnconfirmed += 1;
  }
  return { kind: "remaining", work };
}

/** Câu nói việc còn lại, mỗi việc một câu; rỗng khi không còn trang nào phải làm mà tài liệu vẫn chưa chốt. */
export function remainingWorkMessages(work: RemainingWork): string[] {
  const messages: string[] = [];
  if (work.pending > 0) messages.push(`Còn ${work.pending} trang đang đọc. Chờ đọc xong rồi xem lại.`);
  if (work.needsReview > 0) messages.push(`Còn ${work.needsReview} trang cần xem lại và xác nhận từng trang.`);
  if (work.okUnconfirmed > 0) {
    messages.push(`Còn ${work.okUnconfirmed} trang đọc tốt chưa xác nhận: bấm “Xác nhận các trang đọc tốt”.`);
  }
  if (work.unknown > 0) messages.push(`Có ${work.unknown} trang chưa rõ trạng thái. Tải lại để xem trạng thái mới.`);
  if (work.failed > 0) {
    messages.push(
      `Có ${work.failed} trang đọc lỗi: trang này không vào kho. Muốn có văn bản của trang, xoá tài liệu rồi nhập lại file.`,
    );
  }
  return messages;
}

/** Không còn trang nào phải làm mà tài liệu chưa chốt: tải lại để xem trạng thái mới nhất. */
export const NOT_SETTLED_VI = "Các trang đã xác nhận nhưng tài liệu chưa hoàn tất. Tải lại để xem trạng thái mới nhất.";

/**
 * Mọi trang có thể xác nhận đều đã xác nhận (trang còn lại chỉ là trang đọc lỗi) mà tài liệu chưa `confirmed`: backend
 * chưa chốt xong, người dùng tải lại để xem trạng thái mới nhất.
 */
export function awaitingSettle(document: Pick<DocumentView, "status" | "pages">): boolean {
  if (document.status === "confirmed") return false;
  const someConfirmed = document.pages.some((page) => page.confirmed);
  return someConfirmed && document.pages.every((page) => page.confirmed || page.status === "failed");
}
