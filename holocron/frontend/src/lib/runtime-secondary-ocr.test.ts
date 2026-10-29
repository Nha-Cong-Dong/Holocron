import { describe, expect, it } from "vitest";

import {
  capabilityGroup,
  INITIAL_RUNTIME_STATE,
  normalizeRuntimeInfo,
  runtimeReducer,
  selectStatusItems,
} from "./runtime";

const BASE = { demo_mode: false, llm_configured: false, vlm_configured: false };

describe("secondary_ocr_unavailable", () => {
  it("thuộc nhóm install, còn secondary_ocr_disabled vẫn là configure", () => {
    const unavailable = {
      name: "ocr_cross_check",
      available: false,
      reason: "secondary_ocr_unavailable",
      detail_vi: "x",
    };
    const disabled = { ...unavailable, reason: "secondary_ocr_disabled" };
    const runtime = normalizeRuntimeInfo({ ...BASE, capabilities: [unavailable, disabled] });
    if (runtime === null) throw new Error("fixture không qua được kiểm dạng");
    const [first, second] = runtime.capabilities ?? [];
    if (first === undefined || second === undefined) throw new Error("thiếu capability");
    expect(capabilityGroup(first)).toBe("install");
    expect(capabilityGroup(second)).toBe("configure");
  });

  it("nhãn ô trạng thái: lượt đọc thứ hai không chạy được", () => {
    const runtime = normalizeRuntimeInfo({
      ...BASE,
      capabilities: [{ name: "ocr", available: false, reason: "secondary_ocr_unavailable", detail_vi: "x" }],
    });
    if (runtime === null) throw new Error("fixture không qua được kiểm dạng");
    const state = runtimeReducer(INITIAL_RUNTIME_STATE, { type: "load_succeeded", runtime });
    const item = selectStatusItems(state, false).find((entry) => entry.id === "ocr");
    expect(item?.value).toBe("lượt đọc thứ hai không chạy được");
  });
});
