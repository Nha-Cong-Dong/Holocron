/** Hẹn giờ sang tab Khái niệm sau khi xem lại xong: 1,6 s; giảm chuyển động thì không tự chuyển. */

import { describe, expect, it } from "vitest";

import { FINISH_DELAY_MS, finishPlan, scheduleFinish } from "./finish-timer";
import type { Schedule } from "./poll";

function fakeSchedule() {
  const timers: { run: () => void; delayMs: number; cancelled: boolean }[] = [];
  const schedule: Schedule = (run, delayMs) => {
    const timer = { run, delayMs, cancelled: false };
    timers.push(timer);
    return () => {
      timer.cancelled = true;
    };
  };
  return { schedule, timers };
}

describe("finishPlan", () => {
  it("bình thường tự chuyển sau 1,6 s; giảm chuyển động thì không", () => {
    expect(FINISH_DELAY_MS).toBe(1600);
    expect(finishPlan(false)).toEqual({ autoAdvance: true, delayMs: 1600 });
    expect(finishPlan(true)).toEqual({ autoAdvance: false, delayMs: null });
  });
});

describe("scheduleFinish", () => {
  it("hẹn đúng 1,6 s rồi gọi onFinish một lần", () => {
    const { schedule, timers } = fakeSchedule();
    let calls = 0;
    scheduleFinish({ reducedMotion: false, schedule, onFinish: () => (calls += 1) });
    expect(timers).toHaveLength(1);
    expect(timers[0]?.delayMs).toBe(1600);
    timers[0]?.run();
    timers[0]?.run();
    expect(calls).toBe(1);
  });

  it("giảm chuyển động: không hẹn gì", () => {
    const { schedule, timers } = fakeSchedule();
    let calls = 0;
    const cancel = scheduleFinish({ reducedMotion: true, schedule, onFinish: () => (calls += 1) });
    expect(timers).toHaveLength(0);
    cancel();
    expect(calls).toBe(0);
  });

  it("huỷ (rời màn hoặc bấm đi ngay) thì không gọi onFinish", () => {
    const { schedule, timers } = fakeSchedule();
    let calls = 0;
    const cancel = scheduleFinish({ reducedMotion: false, schedule, onFinish: () => (calls += 1) });
    cancel();
    expect(timers[0]?.cancelled).toBe(true);
    timers[0]?.run();
    expect(calls).toBe(0);
  });
});
