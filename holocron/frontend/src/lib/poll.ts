/**
 * Hỏi lại trạng thái theo chu kỳ (spec §6.5: `GET` 1–2 s một lần khi tài liệu đang đọc). Quy tắc:
 * - lượt kế tiếp chỉ được hẹn SAU khi lượt trước trả về, nên không bao giờ có hai request chồng nhau;
 * - `stop()` (rời màn, đổi môn) huỷ hẹn giờ và bỏ phản hồi của lượt còn đang chờ (dùng `lib/latest.ts`);
 * - dừng khi `shouldContinue` trả `false` (ví dụ không còn tài liệu `processing`);
 * - lỗi liên tiếp thì giãn khoảng chờ, quá `maxConsecutiveErrors` lần thì dừng và để giao diện hiện nút thử lại.
 */

import { createLatestCounter } from "@/lib/latest";

/** Khoảng hỏi lại khi đang đọc: 1,5 s, nằm trong khoảng 1–2 s spec khuyến nghị. */
export const POLL_INTERVAL_MS = 1500;
/** Khoảng chờ tối đa khi lỗi liên tiếp. */
export const POLL_MAX_DELAY_MS = 12_000;
/** Số lần lỗi liên tiếp trước khi dừng hỏi. */
export const POLL_MAX_CONSECUTIVE_ERRORS = 4;

/** Hẹn giờ; trả hàm huỷ. Test truyền bộ hẹn giờ giả. */
export type Schedule = (run: () => void, delayMs: number) => () => void;

/** Bộ hẹn giờ thật (`setTimeout`). */
export const defaultSchedule: Schedule = (run, delayMs) => {
  const handle = setTimeout(run, delayMs);
  return () => clearTimeout(handle);
};

/** Khoảng chờ trước lượt kế tiếp: bình thường `POLL_INTERVAL_MS`; sau n lỗi liên tiếp nhân đôi n lần, có trần. */
export function pollDelay(consecutiveErrors: number, base: number = POLL_INTERVAL_MS): number {
  if (consecutiveErrors <= 0) return base;
  return Math.min(base * 2 ** consecutiveErrors, POLL_MAX_DELAY_MS);
}

export interface PollOptions<T> {
  load: () => Promise<T>;
  /** Có hỏi tiếp sau giá trị này không. */
  shouldContinue: (value: T) => boolean;
  onValue: (value: T) => void;
  /** `gaveUp = true` khi đã dừng vì lỗi liên tiếp. */
  onError: (error: unknown, gaveUp: boolean) => void;
  schedule?: Schedule;
  maxConsecutiveErrors?: number;
}

export interface Poller {
  /** Bắt đầu (gọi `load` ngay); đang chạy thì không làm gì. */
  start: () => void;
  stop: () => void;
  isRunning: () => boolean;
}

export function createPoller<T>(options: PollOptions<T>): Poller {
  const schedule = options.schedule ?? defaultSchedule;
  const maxErrors = options.maxConsecutiveErrors ?? POLL_MAX_CONSECUTIVE_ERRORS;
  const calls = createLatestCounter();
  let running = false;
  let cancelTimer: (() => void) | null = null;
  let errors = 0;

  function tick(ticket: number): void {
    cancelTimer = null;
    options
      .load()
      .then((value) => {
        if (!calls.isLatest(ticket)) return;
        errors = 0;
        options.onValue(value);
        // `onValue` có thể đã gọi `stop()`.
        if (!calls.isLatest(ticket)) return;
        if (options.shouldContinue(value)) {
          cancelTimer = schedule(() => tick(ticket), pollDelay(0));
        } else {
          running = false;
        }
      })
      .catch((error: unknown) => {
        if (!calls.isLatest(ticket)) return;
        errors += 1;
        const gaveUp = errors >= maxErrors;
        if (gaveUp) running = false;
        options.onError(error, gaveUp);
        if (!gaveUp && calls.isLatest(ticket)) {
          cancelTimer = schedule(() => tick(ticket), pollDelay(errors));
        }
      });
  }

  return {
    start: () => {
      if (running) return;
      running = true;
      errors = 0;
      tick(calls.begin());
    },
    stop: () => {
      calls.invalidate();
      cancelTimer?.();
      cancelTimer = null;
      running = false;
    },
    isRunning: () => running,
  };
}
