/**
 * Nút xác nhận trang ở màn Xem lại (I5: trang `needs_review` chỉ được xác nhận khi người dùng chủ ý; quyết định
 * 2026-10-29 câu 17 A). Trang chỉ được xác nhận bằng nút; không có phím tắt, không tự chuyển trang sau khi xác nhận.
 *
 * - `canConfirmNow` là điều kiện DUY NHẤT: thuộc tính `disabled` của nút và handler `onClick` (nơi mọi cách kích hoạt
 *   đều đi qua: chuột, chạm, Enter/Space khi nút đang focus) cùng gọi hàm này. `onClick` gọi lại vì trạng thái ô thêm
 *   dòng lên component cha trễ một lần vẽ.
 * - `focusOnShow`: trang mới hiện thì focus đặt vào tiêu đề trang, không bao giờ vào nút xác nhận.
 * - `nextReviewTarget`: đích của nút "Sang trang cần xem kế tiếp", tính từ tài liệu đọc lại SAU khi xác nhận.
 */

import type { ConfirmPageBlock } from "@/lib/documents";
import type { DocumentView } from "@/lib/documents-data";
import { nextAfterConfirm, reviewQueue } from "@/lib/review-queue";
import { beginSend, CONFIRM_GUARD_MS, msSinceShown, type ReviewSession } from "@/lib/review-session";

export interface ConfirmNowInput {
  /** `confirmPageBlock(page)`: lý do suy từ dữ liệu trang. */
  pageBlock: ConfirmPageBlock | null;
  /** Có dòng backend trả sai dạng bị bỏ khi hiển thị. */
  hasSkippedLines: boolean;
  /** Ô "Thêm dòng vào cuối trang" còn chữ chưa lưu. */
  appendDraft: boolean;
  /** Có ô "Sửa" dòng đang mở, chưa lưu. */
  editing: boolean;
  /** Đang gửi yêu cầu xác nhận (hoặc thao tác ghi khác) và chưa có trả lời. */
  sending: boolean;
  /** Số mili giây từ lúc trang hiện ra (phiên `pageNumber:version`). */
  msSinceShown: number;
}

/**
 * Lý do hiện cạnh nút xác nhận: lý do suy từ trang trước, rồi "còn dòng chưa lưu". `null` khi không có lý do để nói
 * (nút vẫn có thể tạm khoá vì đang gửi hoặc trang vừa hiện, xem `canConfirmNow`). Không tự lưu hộ dòng nào.
 */
export function confirmButtonBlock(
  input: Pick<ConfirmNowInput, "pageBlock" | "appendDraft" | "editing">,
): ConfirmPageBlock | null {
  if (input.pageBlock !== null) return input.pageBlock;
  return input.appendDraft || input.editing ? "unsaved_lines" : null;
}

/** Nút xác nhận nhận kích hoạt lúc này không. */
export function canConfirmNow(input: ConfirmNowInput): boolean {
  return (
    confirmButtonBlock(input) === null &&
    !input.hasSkippedLines &&
    !input.sending &&
    input.msSinceShown >= CONFIRM_GUARD_MS
  );
}

/** Dữ liệu của trang lúc bấm nút; `busy`: đang có thao tác ghi khác (lưu dòng, xác nhận cả tài liệu). */
export type ConfirmClickInput = Omit<ConfirmNowInput, "sending" | "msSinceShown"> & { busy: boolean };

/**
 * Một lần kích hoạt nút xác nhận (handler `onClick`) lúc `now`: đọc mốc thời gian và khoá đang gửi từ phiên, rồi
 * `canConfirmNow`. Được nhận thì phiên chuyển sang đang gửi ngay (không chờ vẽ lại), nên cú bấm thứ hai liền sau bị bỏ.
 */
export function confirmClick(
  session: ReviewSession,
  input: ConfirmClickInput,
  now: number,
): { accepted: boolean; session: ReviewSession } {
  const { busy, ...page } = input;
  const accepted = canConfirmNow({
    ...page,
    sending: session.sending || busy,
    msSinceShown: msSinceShown(session, now),
  });
  return { accepted, session: accepted ? beginSend(session) : session };
}

/** Phần tử đang focus lúc trang mới hiện. */
export type ActiveOnShow =
  /** Không có gì (`body` hoặc `null`), ví dụ phần tử cũ vừa bị gỡ. */
  | "none"
  /** Chính nút xác nhận của trang. */
  | "confirm"
  /** Phần tử khác. */
  | "other";

/**
 * Nơi đặt focus khi một trang hiện ra: đổi sang trang khác (nút "Sang trang…", ←/→, chọn ở danh sách) hay focus đang
 * mất hoặc nằm trên nút xác nhận thì vào tiêu đề trang; còn lại giữ nguyên. Không có kết quả nào đặt focus vào nút xác
 * nhận.
 */
export function focusOnShow(input: { pageChanged: boolean; active: ActiveOnShow }): "heading" | "keep" {
  if (input.pageChanged || input.active !== "other") return "heading";
  return "keep";
}

/**
 * Đích của nút "Sang trang cần xem kế tiếp": trang `needs_review` chưa xác nhận kế sau trang hiện tại, hết thì trang
 * đầu tiên còn lại; không bao giờ là chính trang hiện tại. `null` khi không còn trang nào khác cần xem. `document` là
 * bản đọc lại sau khi xác nhận (không dùng hàng đợi cũ).
 */
export function nextReviewTarget(document: Pick<DocumentView, "pages">, current: number): number | null {
  return nextAfterConfirm(reviewQueue(document), current);
}
