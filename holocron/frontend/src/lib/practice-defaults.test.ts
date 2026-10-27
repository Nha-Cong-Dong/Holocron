/**
 * Luồng mặc định của màn luyện tập với backend thật không rơi vào 409: tự luận chỉ được chọn khi backend báo rõ chấm
 * tự luận offline khả dụng; ticket của slot lý thuyết hiện nguyên lý do backend kèm một gợi ý cố định.
 */

import { describe, expect, it } from "vitest";

import { buildBlueprintCreate, essayChoiceAvailable, prefilledFormatChoice, ticketLine } from "./practice";
import { capabilityOf, normalizeRuntimeInfo, type RuntimeView } from "./runtime";

const EXAM_FORMATS = ["mcq", "essay", "mixed"] as const;

/** `GET /v1/runtime` của backend nhánh này: chỉ ba cờ, chưa có `capabilities`. */
function legacyRuntime(): RuntimeView {
  const runtime = normalizeRuntimeInfo({ demo_mode: false, llm_configured: false, vlm_configured: false });
  if (runtime === null) throw new Error("runtime phải hợp lệ");
  return runtime;
}

function runtimeWithEssay(available: boolean): RuntimeView {
  const runtime = normalizeRuntimeInfo({
    demo_mode: false,
    llm_configured: false,
    vlm_configured: false,
    capabilities: [
      {
        name: "essay_grading_offline",
        available,
        reason: available ? null : "not_implemented",
        detail_vi: available ? null : "Bản này chưa có.",
      },
    ],
  });
  if (runtime === null) throw new Error("runtime phải hợp lệ");
  return runtime;
}

describe("mặc định MCQ4 khi chưa biết chấm tự luận", () => {
  it("capabilities null (backend chưa báo) hay chưa tải runtime: chỉ trắc nghiệm, mọi môn điền sẵn MCQ4", () => {
    const legacy = legacyRuntime();
    expect(legacy.capabilities).toBeNull();
    for (const runtime of [null, legacy]) {
      const essay = essayChoiceAvailable(capabilityOf(runtime, "essay_grading_offline"));
      expect(essay).toBe(false);
      for (const examFormat of EXAM_FORMATS) {
        expect(prefilledFormatChoice(examFormat, essay)).toBe("MCQ4");
      }
    }
  });

  it("đề dựng từ lựa chọn mặc định không có slot ESSAY (không gặp 409 CapabilityError)", () => {
    const essay = essayChoiceAvailable(capabilityOf(legacyRuntime(), "essay_grading_offline"));
    for (const examFormat of EXAM_FORMATS) {
      const result = buildBlueprintCreate({
        subjectCode: "SSH1110",
        chapterCode: "ch1",
        questionCount: 4,
        choice: prefilledFormatChoice(examFormat, essay),
      });
      expect(result.ok).toBe(true);
      expect(result.ok && result.payload.theory_slots?.map((slot) => slot.format)).toEqual([
        "MCQ4",
        "MCQ4",
        "MCQ4",
        "MCQ4",
      ]);
    }
  });

  it("backend báo không khả dụng: vẫn MCQ4; báo khả dụng: điền sẵn theo exam_format", () => {
    expect(essayChoiceAvailable(capabilityOf(runtimeWithEssay(false), "essay_grading_offline"))).toBe(false);
    const essay = essayChoiceAvailable(capabilityOf(runtimeWithEssay(true), "essay_grading_offline"));
    expect(essay).toBe(true);
    expect(prefilledFormatChoice("essay", essay)).toBe("ESSAY");
    expect(prefilledFormatChoice("mixed", essay)).toBe("mixed");
    expect(prefilledFormatChoice("mcq", essay)).toBe("MCQ4");
  });
});

describe("ticketLine", () => {
  const hint = "Kiểm tra chương đã có tài liệu xác nhận và cặp khái niệm đã xác nhận ở trang Cặp khái niệm.";

  it("ticket lý thuyết: lý do nguyên văn của backend rồi gợi ý cố định, không phụ thuộc nội dung lý do", () => {
    for (const reason of ["không đủ căn cứ trong Vault: chương ch3", "Lý do bất kỳ khác."]) {
      const line = ticketLine({ reason, theory: true });
      expect(line.startsWith(reason)).toBe(true);
      expect(line.endsWith(hint)).toBe(true);
    }
  });

  it("ticket câu tính toán chỉ có lý do của backend", () => {
    expect(ticketLine({ reason: "hết lượt thử", theory: false })).toBe("hết lượt thử");
  });
});
