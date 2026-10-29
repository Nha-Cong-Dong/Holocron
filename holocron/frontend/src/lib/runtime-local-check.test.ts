import { describe, expect, it } from "vitest";

import {
  capabilityGroup,
  groupCapabilities,
  hasPendingLocalCheck,
  INITIAL_RUNTIME_STATE,
  normalizeRuntimeInfo,
  runtimeReducer,
  selectStatusItems,
} from "./runtime";

const BASE = { demo_mode: false, llm_configured: false, vlm_configured: false };
const PENDING = { available: false, reason: "local_check_pending", detail_vi: "Đang kiểm tra." };

function load(capabilities: object[]) {
  const runtime = normalizeRuntimeInfo({ ...BASE, capabilities });
  if (runtime === null) throw new Error("fixture không qua được kiểm dạng");
  return runtime;
}

describe("local_check_pending", () => {
  it("ô OCR hiện đang kiểm tra, không phải thiếu Tesseract", () => {
    const runtime = load([{ name: "ocr", ...PENDING }]);
    const state = runtimeReducer(INITIAL_RUNTIME_STATE, { type: "load_succeeded", runtime });
    const item = selectStatusItems(state, false).find((entry) => entry.id === "ocr");
    expect(item?.value).toBe("đang kiểm tra…");
    expect(item?.tone).toBe("info");
  });

  it("xếp vào nhóm pending, không vào install", () => {
    const runtime = load([
      { name: "ocr", ...PENDING },
      { name: "ocr_cross_check", ...PENDING },
    ]);
    const groups = groupCapabilities(runtime.capabilities ?? []);
    expect(groups.pending).toHaveLength(2);
    expect(groups.install).toHaveLength(0);
    const first = runtime.capabilities?.[0];
    if (first === undefined) throw new Error("thiếu capability");
    expect(capabilityGroup(first)).toBe("pending");
  });

  it("hasPendingLocalCheck chỉ đúng khi còn khả năng đang kiểm", () => {
    expect(hasPendingLocalCheck(load([{ name: "ocr", ...PENDING }]))).toBe(true);
    const done = { name: "ocr", available: true, reason: null, detail_vi: null };
    expect(hasPendingLocalCheck(load([done]))).toBe(false);
    expect(hasPendingLocalCheck(null)).toBe(false);
  });
});
