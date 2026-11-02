/**
 * Hẹn giờ sau khi xem lại xong một tài liệu (design-handoff/README.md màn 6): sao băng và huy hiệu, rồi tự sang tab
 * Khái niệm sau 1,6 s. Giảm chuyển động: không tự chuyển, người dùng bấm "Sang Khái niệm" (nút luôn có để đi ngay).
 */

import { defaultSchedule, type Schedule } from "@/lib/poll";

/** Thời gian giữ màn chúc mừng trước khi tự sang tab Khái niệm. */
export const FINISH_DELAY_MS = 1600;

export interface FinishPlan {
  /** Tự chuyển sang tab Khái niệm. */
  autoAdvance: boolean;
  /** Chờ bao lâu trước khi tự chuyển; `null` khi không tự chuyển. */
  delayMs: number | null;
}

export function finishPlan(reducedMotion: boolean): FinishPlan {
  return reducedMotion ? { autoAdvance: false, delayMs: null } : { autoAdvance: true, delayMs: FINISH_DELAY_MS };
}

export interface FinishTimerOptions {
  reducedMotion: boolean;
  onFinish: () => void;
  schedule?: Schedule;
}

/**
 * Hẹn tự chuyển theo `finishPlan`; trả hàm huỷ (rời màn, bấm nút đi ngay). Gọi `onFinish` tối đa một lần; giảm chuyển
 * động thì không hẹn gì.
 */
export function scheduleFinish(options: FinishTimerOptions): () => void {
  const plan = finishPlan(options.reducedMotion);
  if (!plan.autoAdvance || plan.delayMs === null) return () => {};
  const schedule = options.schedule ?? defaultSchedule;
  let done = false;
  const cancel = schedule(() => {
    if (done) return;
    done = true;
    options.onFinish();
  }, plan.delayMs);
  return () => {
    done = true;
    cancel();
  };
}
