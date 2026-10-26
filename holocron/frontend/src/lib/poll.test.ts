import { describe, expect, it } from "vitest";

import { createPoller, POLL_INTERVAL_MS, POLL_MAX_DELAY_MS, pollDelay, type Schedule } from "./poll";

/** Bộ hẹn giờ giả: giữ các hẹn đang chờ, test tự chạy từng hẹn. */
function fakeSchedule() {
  const pending: { run: () => void; delay: number; cancelled: boolean }[] = [];
  const schedule: Schedule = (run, delay) => {
    const entry = { run, delay, cancelled: false };
    pending.push(entry);
    return () => {
      entry.cancelled = true;
    };
  };
  return {
    schedule,
    active: () => pending.filter((entry) => !entry.cancelled),
    /** Chạy hẹn đang chờ sớm nhất (theo thứ tự hẹn). */
    fire: () => {
      const next = pending.find((entry) => !entry.cancelled);
      if (next === undefined) throw new Error("không có hẹn nào");
      next.cancelled = true;
      next.run();
      return next.delay;
    },
  };
}

/** Promise điều khiển được từ test. */
function deferred<T>() {
  let resolve: (value: T) => void = () => undefined;
  let reject: (error: unknown) => void = () => undefined;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

const flush = () => new Promise<void>((resolve) => setTimeout(resolve, 0));

describe("pollDelay", () => {
  it("1,5 s khi không lỗi; nhân đôi theo số lỗi liên tiếp, có trần", () => {
    expect(POLL_INTERVAL_MS).toBeGreaterThanOrEqual(1000);
    expect(POLL_INTERVAL_MS).toBeLessThanOrEqual(2000);
    expect(pollDelay(0)).toBe(POLL_INTERVAL_MS);
    expect(pollDelay(1)).toBe(POLL_INTERVAL_MS * 2);
    expect(pollDelay(2)).toBe(POLL_INTERVAL_MS * 4);
    expect(pollDelay(20)).toBe(POLL_MAX_DELAY_MS);
  });
});

describe("createPoller", () => {
  it("gọi ngay khi start, hẹn lượt sau chỉ khi lượt trước đã trả, dừng khi shouldContinue = false", async () => {
    const timer = fakeSchedule();
    const values = ["processing", "processing", "review"];
    let calls = 0;
    let inFlight = 0;
    let maxInFlight = 0;
    const seen: string[] = [];
    const poller = createPoller({
      load: async () => {
        inFlight += 1;
        maxInFlight = Math.max(maxInFlight, inFlight);
        const value = values[calls] ?? "review";
        calls += 1;
        await flush();
        inFlight -= 1;
        return value;
      },
      shouldContinue: (value) => value === "processing",
      onValue: (value) => seen.push(value),
      onError: () => undefined,
      schedule: timer.schedule,
    });
    poller.start();
    poller.start();
    expect(calls).toBe(1);
    expect(timer.active()).toHaveLength(0);
    await flush();
    await flush();
    expect(timer.active()).toHaveLength(1);
    expect(timer.fire()).toBe(POLL_INTERVAL_MS);
    await flush();
    await flush();
    timer.fire();
    await flush();
    await flush();
    expect(seen).toEqual(["processing", "processing", "review"]);
    expect(timer.active()).toHaveLength(0);
    expect(poller.isRunning()).toBe(false);
    expect(maxInFlight).toBe(1);
  });

  it("stop() bỏ phản hồi đang chờ và huỷ hẹn giờ", async () => {
    const timer = fakeSchedule();
    const first = deferred<string>();
    const seen: string[] = [];
    const poller = createPoller({
      load: () => first.promise,
      shouldContinue: () => true,
      onValue: (value) => seen.push(value),
      onError: () => undefined,
      schedule: timer.schedule,
    });
    poller.start();
    poller.stop();
    first.resolve("cũ");
    await flush();
    expect(seen).toEqual([]);
    expect(timer.active()).toHaveLength(0);
    expect(poller.isRunning()).toBe(false);
  });

  it("phản hồi của lượt trước khi start lại bị bỏ (chỉ lượt mới nhất được áp)", async () => {
    const timer = fakeSchedule();
    const responses = [deferred<string>(), deferred<string>()];
    let index = 0;
    const seen: string[] = [];
    const poller = createPoller({
      load: () => responses[index++]?.promise ?? Promise.resolve("x"),
      shouldContinue: () => false,
      onValue: (value) => seen.push(value),
      onError: () => undefined,
      schedule: timer.schedule,
    });
    poller.start();
    poller.stop();
    poller.start();
    responses[1]?.resolve("mới");
    responses[0]?.resolve("cũ");
    await flush();
    expect(seen).toEqual(["mới"]);
  });

  it("lỗi liên tiếp thì giãn khoảng chờ, quá số lần thì dừng và báo gaveUp", async () => {
    const timer = fakeSchedule();
    const errors: boolean[] = [];
    const poller = createPoller({
      load: () => Promise.reject(new Error("mất kết nối")),
      shouldContinue: () => true,
      onValue: () => undefined,
      onError: (_error, gaveUp) => errors.push(gaveUp),
      schedule: timer.schedule,
      maxConsecutiveErrors: 3,
    });
    poller.start();
    await flush();
    expect(timer.fire()).toBe(pollDelay(1));
    await flush();
    expect(timer.fire()).toBe(pollDelay(2));
    await flush();
    expect(errors).toEqual([false, false, true]);
    expect(timer.active()).toHaveLength(0);
    expect(poller.isRunning()).toBe(false);
  });

  it("onValue gọi stop() thì không hẹn lượt sau", async () => {
    const timer = fakeSchedule();
    const poller = createPoller<string>({
      load: () => Promise.resolve("processing"),
      shouldContinue: () => true,
      onValue: () => poller.stop(),
      onError: () => undefined,
      schedule: timer.schedule,
    });
    poller.start();
    await flush();
    expect(timer.active()).toHaveLength(0);
  });
});
