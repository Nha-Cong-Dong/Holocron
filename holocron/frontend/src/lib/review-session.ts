/**
 * Phiên xem một trang ở màn Xem lại (I5): mốc lúc trang hiện ra và việc nút xác nhận đang gửi. Mỗi phiên gắn với một
 * khoá `pageNumber:version`: sang trang khác hay trang vẽ lại sau khi lưu dòng (version đổi) là phiên mới, đếm lại
 * `CONFIRM_GUARD_MS` từ đầu. Hàm thuần; component chỉ giữ phiên trong `useRef` và truyền thời điểm hiện tại.
 */

/**
 * Khoảng tối thiểu (ms) từ lúc trang hiện ra tới lúc nút xác nhận nhận kích hoạt. Áp cho mọi cách kích hoạt nút (chuột,
 * chạm, Enter/Space khi nút đang focus): cú bấm thừa rơi vào trang vừa hiện không xác nhận trang chưa đọc.
 */
export const CONFIRM_GUARD_MS = 600;

export interface ReviewSession {
  /** `pageNumber:version` của trang đang hiện. */
  pageKey: string;
  /** Thời điểm (ms, cùng mốc với `performance.now()`) trang hiện ra. */
  shownAt: number;
  /** Đã gửi yêu cầu xác nhận trang và chưa có trả lời: mọi cú bấm sau bị bỏ. */
  sending: boolean;
}

/** Khoá phiên của một trang. */
export function reviewPageKey(pageNumber: number, version: number): string {
  return `${pageNumber}:${version}`;
}

/** Phiên của trang đang hiện: giữ phiên cũ nếu cùng khoá, khác khoá (hoặc chưa có) thì mở phiên mới tính từ `now`. */
export function reviewSessionFor(session: ReviewSession | null, pageKey: string, now: number): ReviewSession {
  if (session !== null && session.pageKey === pageKey) return session;
  return { pageKey, shownAt: now, sending: false };
}

/** Số mili giây từ lúc trang hiện ra tới `now` (không âm). */
export function msSinceShown(session: ReviewSession, now: number): number {
  return Math.max(0, now - session.shownAt);
}

/** Số mili giây còn phải chờ trước khi nút xác nhận nhận kích hoạt; 0 khi đã đủ. */
export function guardRemainingMs(session: ReviewSession, now: number): number {
  return Math.max(0, CONFIRM_GUARD_MS - msSinceShown(session, now));
}

/** Cú bấm xác nhận được chấp nhận: phiên chuyển sang đang gửi. */
export function beginSend(session: ReviewSession): ReviewSession {
  return { ...session, sending: true };
}

/** Server đã trả lời (thành công hay lỗi): phiên hết đang gửi. */
export function endSend(session: ReviewSession): ReviewSession {
  return { ...session, sending: false };
}
