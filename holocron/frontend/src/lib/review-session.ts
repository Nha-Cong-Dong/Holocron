/**
 * Phiên xem một trang ở màn Xem lại (I5): mốc lúc trang hiện ra và việc người dùng đã chủ động tương tác với trang đó
 * chưa. Mỗi phiên gắn với một khoá `pageNumber:version`: sang trang khác hay trang vẽ lại sau khi lưu dòng (version đổi)
 * là phiên mới, khoá Enter lại từ đầu. Hàm thuần; component chỉ giữ phiên trong `useRef` và truyền thời điểm hiện tại.
 */

import { armsReview, type ReviewInteraction, type ReviewKeydown } from "@/lib/review-keys";

export interface ReviewSession {
  /** `pageNumber:version` của trang đang hiện. */
  pageKey: string;
  /** Thời điểm (ms, cùng mốc với `performance.now()`) trang hiện ra. */
  shownAt: number;
  /** Người dùng đã chủ động tương tác với trang này sau khi nó hiện ra. */
  armed: boolean;
}

/** Khoá phiên của một trang. */
export function reviewPageKey(pageNumber: number, version: number): string {
  return `${pageNumber}:${version}`;
}

/** Phiên của trang đang hiện: giữ phiên cũ nếu cùng khoá, khác khoá (hoặc chưa có) thì mở phiên mới, đang khoá. */
export function reviewSessionFor(session: ReviewSession | null, pageKey: string, now: number): ReviewSession {
  if (session !== null && session.pageKey === pageKey) return session;
  return { pageKey, shownAt: now, armed: false };
}

/** Áp một tương tác (bấm chuột, phím) vào phiên. */
export function applyInteraction(session: ReviewSession, interaction: ReviewInteraction): ReviewSession {
  return !session.armed && armsReview(interaction) ? { ...session, armed: true } : session;
}

export interface KeydownGate {
  /** Phiên ĐÃ mở khoá TRƯỚC phím này (một phím không vừa mở khoá vừa được xác nhận). */
  armed: boolean;
  msSinceShown: number;
}

/**
 * Một phím bấm trong vùng xem lại: điều kiện Enter đọc từ phiên TRƯỚC phím này, rồi mới áp phím vào phiên. Nhờ vậy phím
 * vừa mở khoá không tự xác nhận trang.
 */
export function reviewKeydown(
  session: ReviewSession,
  keydown: ReviewKeydown,
  now: number,
): { gate: KeydownGate; next: ReviewSession } {
  const gate: KeydownGate = { armed: session.armed, msSinceShown: Math.max(0, now - session.shownAt) };
  return { gate, next: applyInteraction(session, { type: "keydown", ...keydown }) };
}
