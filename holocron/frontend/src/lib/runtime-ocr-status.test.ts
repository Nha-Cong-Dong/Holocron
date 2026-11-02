import { describe, expect, it } from "vitest";

import {
  INITIAL_RUNTIME_STATE,
  normalizeRuntimeInfo,
  type RuntimeState,
  runtimeReducer,
  selectStatusItems,
} from "./runtime";

const BASE = { demo_mode: false, llm_configured: false, vlm_configured: false };

function stateWith(body: Record<string, unknown>): RuntimeState {
  const runtime = normalizeRuntimeInfo({ ...BASE, ...body });
  if (runtime === null) throw new Error("fixture không qua được kiểm dạng");
  return runtimeReducer(INITIAL_RUNTIME_STATE, { type: "load_succeeded", runtime });
}

function ocrItem(state: RuntimeState): { value: string; tone: string } | undefined {
  return selectStatusItems(state, false).find((item) => item.id === "ocr");
}

describe("ô trạng thái OCR", () => {
  it("ocr khả dụng: sẵn sàng", () => {
    const state = stateWith({ capabilities: [{ name: "ocr", available: true, reason: null, detail_vi: null }] });
    expect(ocrItem(state)).toMatchObject({ value: "sẵn sàng", tone: "success" });
  });

  it("ocr_engine_missing: thiếu Tesseract", () => {
    const state = stateWith({
      capabilities: [{ name: "ocr", available: false, reason: "ocr_engine_missing", detail_vi: "x" }],
    });
    expect(ocrItem(state)).toMatchObject({ value: "thiếu Tesseract", tone: "warning" });
  });

  it("ocr_language_missing: thiếu vie.traineddata", () => {
    const state = stateWith({
      capabilities: [{ name: "ocr", available: false, reason: "ocr_language_missing", detail_vi: "x" }],
    });
    expect(ocrItem(state)).toMatchObject({ value: "thiếu vie.traineddata", tone: "warning" });
  });

  it("backend cũ không trả capabilities: backend chưa báo", () => {
    expect(ocrItem(stateWith({}))).toMatchObject({ value: "backend chưa báo", tone: "neutral" });
  });
});
