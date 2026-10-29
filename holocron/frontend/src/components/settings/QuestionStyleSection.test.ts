import { describe, expect, it } from "vitest";
import { localError } from "@/lib/errors";
import { INITIAL_RUNTIME_STATE, type RuntimeState, type RuntimeView } from "@/lib/runtime";

import { questionStyleStatus } from "./QuestionStyleSection";

function runtime(llm: boolean): RuntimeView {
  return {
    demo_mode: false,
    llm_configured: llm,
    vlm_configured: false,
    mock_extractor: null,
    capabilities: null,
  };
}

describe("questionStyleStatus", () => {
  it("đang kiểm tra: báo đang kiểm tra, không báo Chưa kiểm tra được", () => {
    const status = questionStyleStatus(INITIAL_RUNTIME_STATE);
    expect(status).toEqual({ text: "Đang kiểm tra…", on: false });
    const rechecking: RuntimeState = { ...INITIAL_RUNTIME_STATE, backend: "connecting", runtime: runtime(true) };
    expect(questionStyleStatus(rechecking)).toEqual({ text: "Đang kiểm tra…", on: false });
  });

  it("kiểm tra lỗi: Chưa kiểm tra được, kể cả khi còn dữ liệu cũ", () => {
    const failed: RuntimeState = {
      ...INITIAL_RUNTIME_STATE,
      backend: "unreachable",
      runtime: runtime(true),
      error: { ...localError("x"), kind: "network" },
    };
    expect(questionStyleStatus(failed).text).toMatch(/^Chưa kiểm tra được/);
    expect(questionStyleStatus(failed).on).toBe(false);
    expect(questionStyleStatus({ ...failed, backend: "error" }).text).toMatch(/^Chưa kiểm tra được/);
  });

  it("đã kiểm tra: bật hoặc chưa bật theo backend báo", () => {
    const connected = (llm: boolean): RuntimeState => ({
      ...INITIAL_RUNTIME_STATE,
      backend: "connected",
      runtime: runtime(llm),
    });
    expect(questionStyleStatus(connected(true))).toEqual({ text: "Đang bật", on: true });
    expect(questionStyleStatus(connected(false))).toEqual({
      text: "Chưa bật. Tắt đi, app vẫn ra đề bình thường.",
      on: false,
    });
  });
});
