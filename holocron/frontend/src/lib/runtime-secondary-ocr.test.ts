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
});

function ocrTile(ocr: object, cross: object): string | undefined {
  const runtime = normalizeRuntimeInfo({ ...BASE, capabilities: [ocr, cross] });
  if (runtime === null) throw new Error("fixture không qua được kiểm dạng");
  const state = runtimeReducer(INITIAL_RUNTIME_STATE, { type: "load_succeeded", runtime });
  return selectStatusItems(state, false).find((entry) => entry.id === "ocr")?.value;
}

const OCR_OK = { name: "ocr", available: true, reason: null, detail_vi: null };

describe("ô OCR khi xét thêm ocr_cross_check", () => {
  it("ocr khả dụng, lượt đọc thứ hai không nạp được: báo lượt đọc thứ hai không chạy được", () => {
    const cross = { name: "ocr_cross_check", available: false, reason: "secondary_ocr_unavailable", detail_vi: "x" };
    expect(ocrTile(OCR_OK, cross)).toBe("lượt đọc thứ hai không chạy được");
  });

  it("lượt đọc thứ hai do người dùng tắt: vẫn sẵn sàng", () => {
    const cross = { name: "ocr_cross_check", available: false, reason: "secondary_ocr_disabled", detail_vi: "x" };
    expect(ocrTile(OCR_OK, cross)).toBe("sẵn sàng");
  });
});
