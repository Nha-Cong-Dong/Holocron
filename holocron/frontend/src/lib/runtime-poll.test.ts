import { describe, expect, it } from "vitest";

import { toUiError } from "./errors";
import type { Schedule } from "./poll";
import {
  INITIAL_RUNTIME_STATE,
  normalizeRuntimeInfo,
  type RuntimeAction,
  runtimeReducer,
  selectStatusItems,
} from "./runtime";
import { createRuntimePoller } from "./runtime-poll";

const BASE = { demo_mode: false, llm_configured: false, vlm_configured: false };
const PENDING = { name: "ocr", available: false, reason: "local_check_pending", detail_vi: "Đang kiểm tra." };
const READY = { name: "ocr", available: true, reason: null, detail_vi: null };

function view(capability: object) {
  const runtime = normalizeRuntimeInfo({ ...BASE, capabilities: [capability] });
  if (runtime === null) throw new Error("fixture không qua được kiểm dạng");
  return runtime;
}

function fakeSchedule() {
  const pending: { run: () => void; cancelled: boolean }[] = [];
  const schedule: Schedule = (run) => {
    const entry = { run, cancelled: false };
    pending.push(entry);
    return () => {
      entry.cancelled = true;
    };
  };
  return {
    schedule,
    active: () => pending.filter((entry) => !entry.cancelled).length,
    fire: () => {
      const next = pending.find((entry) => !entry.cancelled);
      if (next === undefined) throw new Error("không có hẹn nào");
      next.cancelled = true;
      next.run();
    },
  };
}

const flush = () => new Promise<void>((resolve) => setTimeout(resolve, 0));

/** Mỗi lượt gọi lấy một kết quả: `RuntimeView` là thành công, `Error` là lỗi mạng. */
function setup(results: (ReturnType<typeof view> | Error)[], maxConsecutiveErrors?: number) {
  const timer = fakeSchedule();
  const actions: RuntimeAction[] = [];
  let calls = 0;
  const poller = createRuntimePoller({
    load: () => {
      const next = results[calls];
      calls += 1;
      if (next === undefined) throw new Error("hỏi quá số lượt dự kiến");
      return next instanceof Error ? Promise.reject(next) : Promise.resolve(next);
    },
    dispatch: (action) => actions.push(action),
    toUiError,
    schedule: timer.schedule,
    maxConsecutiveErrors,
  });
  return { timer, actions, poller, calls: () => calls };
}

describe("vòng hỏi lại local_check_pending", () => {
  it("pending kéo dài rồi xong: hỏi tiếp tới khi hết pending thì dừng", async () => {
    const { timer, actions, poller, calls } = setup([view(PENDING), view(PENDING), view(READY)]);
    poller.start();
    await flush();
    expect(timer.active()).toBe(1);
    timer.fire();
    await flush();
    expect(timer.active()).toBe(1);
    timer.fire();
    await flush();
    expect(calls()).toBe(3);
    expect(timer.active()).toBe(0);
    expect(actions.map((action) => action.type)).toEqual(["load_succeeded", "load_succeeded", "load_succeeded"]);
  });

  it("một lần lỗi mạng: không phát hành động nào, runtime đang hiển thị được giữ", async () => {
    const { timer, actions, poller } = setup([view(PENDING), new TypeError("mạng"), view(READY)]);
    let state = runtimeReducer(INITIAL_RUNTIME_STATE, { type: "load_succeeded", runtime: view(PENDING) });
    poller.start();
    await flush();
    timer.fire();
    await flush();
    for (const action of actions) state = runtimeReducer(state, action);
    expect(actions.map((action) => action.type)).toEqual(["load_succeeded"]);
    expect(state.backend).toBe("connected");
    expect(selectStatusItems(state, false).find((entry) => entry.id === "ocr")?.value).toBe("đang kiểm tra…");
    expect(timer.active()).toBe(1);
    timer.fire();
    await flush();
    expect(actions.map((action) => action.type)).toEqual(["load_succeeded", "load_succeeded"]);
  });

  it("lỗi liên tiếp tới ngưỡng: dừng và chuyển sang trạng thái lỗi", async () => {
    const boom = new TypeError("mạng");
    const { timer, actions, poller, calls } = setup([boom, boom, boom], 3);
    poller.start();
    await flush();
    timer.fire();
    await flush();
    expect(actions).toEqual([]);
    timer.fire();
    await flush();
    expect(calls()).toBe(3);
    expect(timer.active()).toBe(0);
    expect(actions.map((action) => action.type)).toEqual(["load_failed"]);
    const state = actions.reduce(runtimeReducer, INITIAL_RUNTIME_STATE);
    expect(state.backend).not.toBe("connected");
    expect(state.error).not.toBeNull();
  });

  it("stop() huỷ hẹn đang chờ", async () => {
    const { timer, poller } = setup([view(PENDING)]);
    poller.start();
    await flush();
    poller.stop();
    expect(timer.active()).toBe(0);
  });
});
