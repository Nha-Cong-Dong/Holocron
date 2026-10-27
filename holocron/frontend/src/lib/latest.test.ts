import { describe, expect, it } from "vitest";

import { createLatestCounter } from "./latest";
import { INITIAL_RUNTIME_STATE, type RuntimeAction, type RuntimeState, runtimeReducer } from "./runtime";

describe("createLatestCounter", () => {
  it("chỉ lượt mới nhất được áp; lượt cũ trả về muộn bị bỏ", () => {
    const counter = createLatestCounter();
    const first = counter.begin();
    const second = counter.begin();
    expect(counter.isLatest(first)).toBe(false);
    expect(counter.isLatest(second)).toBe(true);
    counter.invalidate();
    expect(counter.isLatest(second)).toBe(false);
  });

  it("mô phỏng hai lần kiểm tra lại backend: kết quả cũ đến sau không ghi đè kết quả mới", () => {
    const counter = createLatestCounter();
    let state: RuntimeState = INITIAL_RUNTIME_STATE;
    const apply = (ticket: number, action: RuntimeAction): void => {
      if (counter.isLatest(ticket)) state = runtimeReducer(state, action);
    };
    const oldTicket = counter.begin();
    state = runtimeReducer(state, { type: "load_started" });
    const newTicket = counter.begin();
    state = runtimeReducer(state, { type: "load_started" });

    const fresh = {
      demo_mode: false,
      llm_configured: true,
      vlm_configured: false,
      mock_extractor: null,
      capabilities: [],
    };
    apply(newTicket, { type: "load_succeeded", runtime: fresh });
    apply(oldTicket, {
      type: "load_failed",
      error: { kind: "network", message: "x", action: "retry", issues: [], capability: null },
    });
    expect(state.backend).toBe("connected");
    expect(state.runtime).toEqual(fresh);
  });
});
