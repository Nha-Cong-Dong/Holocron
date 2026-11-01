/**
 * Hỏi tiến độ job đề xuất cặp (§5.7 quy tắc `proposal_job`, §6.5): đọc lại `GET /v1/documents/{id}` (một nguồn, field
 * `proposal_job`) mỗi 2 s, tối đa 600 lần, dừng khi job không còn chạy hoặc khi rời màn. Quy tắc:
 * - lượt kế tiếp chỉ được hẹn SAU khi lượt trước trả về (không có hai request chồng nhau);
 * - `stop()` (rời màn, huỷ) huỷ hẹn giờ và bỏ phản hồi của lượt còn đang chờ;
 * - lỗi liên tiếp quá ngưỡng thì dừng và báo để giao diện hiện nút "Tải lại";
 * - tới trần số lượt thì dừng và báo `limit`.
 */

import { type ImportResultView, shouldPollJob } from "@/lib/import-result";
import { createLatestCounter } from "@/lib/latest";
import { defaultSchedule, type Schedule } from "@/lib/poll";

/** Khoảng hỏi lại: 2 s (§5.7 khuyến nghị 1–2 s một lần). */
export const JOB_POLL_INTERVAL_MS = 2000;
/** Trần số lượt hỏi cho một lần theo dõi (600 lượt × 2 s ≈ 20 phút). */
export const JOB_POLL_MAX_POLLS = 600;
/** Số lần lỗi liên tiếp trước khi dừng. */
export const JOB_POLL_MAX_CONSECUTIVE_ERRORS = 4;

/** Vì sao một lần theo dõi dừng (không tính `stop()` do người dùng rời màn). */
export type JobPollEnd = "finished" | "limit" | "errors";

export interface JobPollOptions {
  load: () => Promise<ImportResultView>;
  onValue: (value: ImportResultView) => void;
  /** Gọi một lần khi tự dừng; `error` là lỗi cuối khi `reason = "errors"`. */
  onEnd: (reason: JobPollEnd, error?: unknown) => void;
  schedule?: Schedule;
  intervalMs?: number;
  maxPolls?: number;
  maxConsecutiveErrors?: number;
}

export interface JobPoller {
  /** Bắt đầu: lượt đầu chạy sau một khoảng `intervalMs`; đang chạy thì không làm gì. */
  start: () => void;
  stop: () => void;
  isRunning: () => boolean;
}

export function createJobPoller(options: JobPollOptions): JobPoller {
  const schedule = options.schedule ?? defaultSchedule;
  const interval = options.intervalMs ?? JOB_POLL_INTERVAL_MS;
  const maxPolls = options.maxPolls ?? JOB_POLL_MAX_POLLS;
  const maxErrors = options.maxConsecutiveErrors ?? JOB_POLL_MAX_CONSECUTIVE_ERRORS;
  const calls = createLatestCounter();
  let running = false;
  let cancelTimer: (() => void) | null = null;
  let polls = 0;
  let errors = 0;

  function end(reason: JobPollEnd, error?: unknown): void {
    running = false;
    cancelTimer = null;
    options.onEnd(reason, error);
  }

  function next(ticket: number): void {
    if (polls >= maxPolls) {
      end("limit");
      return;
    }
    cancelTimer = schedule(() => tick(ticket), interval);
  }

  function tick(ticket: number): void {
    cancelTimer = null;
    polls += 1;
    options
      .load()
      .then((value) => {
        if (!calls.isLatest(ticket)) return;
        errors = 0;
        options.onValue(value);
        // `onValue` có thể đã gọi `stop()`.
        if (!calls.isLatest(ticket)) return;
        if (shouldPollJob(value)) next(ticket);
        else end("finished");
      })
      .catch((error: unknown) => {
        if (!calls.isLatest(ticket)) return;
        errors += 1;
        if (errors >= maxErrors) end("errors", error);
        else next(ticket);
      });
  }

  return {
    start: () => {
      if (running) return;
      running = true;
      polls = 0;
      errors = 0;
      next(calls.begin());
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
