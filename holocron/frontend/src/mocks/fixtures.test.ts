/**
 * Fixture phải tuân thủ chính contract của spec (kế hoạch UI mục 3.3), để dữ liệu mẫu không dạy giao diện một dạng
 * dữ liệu backend sẽ không bao giờ trả.
 */

import { describe, expect, it } from "vitest";

import { CAPABILITY_NAMES, CAPABILITY_REASONS, type CapabilityName } from "@/types/contract-pending";

import { CAPABILITY_ERROR_BODY, LLM_UPSTREAM_ERROR_BODY } from "./fixtures/errors";
import { REASON_DETAILS_VI, RUNTIME_FIXTURES, RUNTIME_SCENARIOS } from "./fixtures/runtime";
import { SUBJECT_FIXTURES } from "./fixtures/subjects";
import { fillRuntimeGaps, mockApi, RUNTIME_GAP_FIELDS } from "./index";
import { DEFAULT_RUNTIME_SCENARIO, scenarioFromSearch } from "./store";

/** Khả năng cần LLM theo §11.4. */
const LLM_CAPABILITIES: readonly CapabilityName[] = [
  "concept_pair_llm_proposal",
  "essay_grading_llm",
  "template_authoring_llm",
];

const ISO_UTC = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$/;

describe("RuntimeInfo (§5.12)", () => {
  for (const scenario of RUNTIME_SCENARIOS) {
    const runtime = RUNTIME_FIXTURES[scenario];

    describe(scenario, () => {
      it("capabilities đủ 12 phần tử theo đúng thứ tự khai báo", () => {
        expect(runtime.capabilities?.map((c) => c.name)).toEqual([...CAPABILITY_NAMES]);
      });

      it("reason là null khi và chỉ khi available; detail_vi theo reason", () => {
        for (const capability of runtime.capabilities ?? []) {
          expect(capability.reason === null).toBe(capability.available);
          if (capability.reason === null) {
            expect(capability.detail_vi).toBeNull();
          } else {
            expect(CAPABILITY_REASONS).toContain(capability.reason);
            expect(capability.detail_vi).toBe(REASON_DETAILS_VI[capability.reason]);
          }
        }
      });

      it("demo_mode = not llm_configured; khả năng cần LLM thiếu LLM thì llm_not_configured", () => {
        expect(runtime.demo_mode).toBe(!runtime.llm_configured);
        for (const capability of runtime.capabilities ?? []) {
          if (!LLM_CAPABILITIES.includes(capability.name) || capability.reason === "not_implemented") continue;
          expect(capability.reason === "llm_not_configured").toBe(!runtime.llm_configured);
        }
      });

      it("VLM khả dụng khi và chỉ khi vlm_configured; thiếu VLM thì upload đề dùng extractor mẫu", () => {
        const vlm = runtime.capabilities?.find((c) => c.name === "vlm");
        expect(vlm?.available).toBe(runtime.vlm_configured);
        if (!runtime.vlm_configured) {
          expect(vlm?.reason).toBe("vlm_not_configured");
          expect(runtime.mock_extractor).toBe(true);
        }
      });

      it("chấm tự luận bằng LLM luôn not_implemented trong MVP", () => {
        expect(runtime.capabilities?.find((c) => c.name === "essay_grading_llm")?.reason).toBe("not_implemented");
      });
    });
  }

  it("ba kịch bản phủ chạy offline, đủ LLM và thiếu Tesseract", () => {
    expect(RUNTIME_FIXTURES.offline.demo_mode).toBe(true);
    expect(RUNTIME_FIXTURES.llm.llm_configured).toBe(true);
    expect(RUNTIME_FIXTURES.no_tesseract.capabilities?.find((c) => c.name === "ocr")?.reason).toBe(
      "ocr_engine_missing",
    );
  });

  it("detail_vi không chứa key, địa chỉ endpoint hay giá trị biến", () => {
    for (const detail of Object.values(REASON_DETAILS_VI)) {
      expect(detail).not.toMatch(/https?:|sk-|=|127\.0\.0\.1|localhost/i);
    }
  });
});

describe("Subject (§5.14, §6.8)", () => {
  it("đúng mẫu và giới hạn của model", () => {
    for (const subject of SUBJECT_FIXTURES) {
      expect(subject.subject_code).toMatch(/^[A-Z]{2,6}[0-9]{2,4}$/);
      expect(subject.display_name.length).toBeGreaterThanOrEqual(1);
      expect(subject.display_name.length).toBeLessThanOrEqual(200);
      expect(["mcq", "essay", "mixed"]).toContain(subject.exam_format);
      expect(Number.isInteger(subject.version) && subject.version >= 1).toBe(true);
      expect(subject.created_at).toMatch(ISO_UTC);
      expect(subject.updated_at).toMatch(ISO_UTC);
      expect(subject.updated_at >= subject.created_at).toBe(true);
    }
  });

  it("sắp theo subject_code, không trùng", () => {
    const codes = SUBJECT_FIXTURES.map((s) => s.subject_code);
    expect(codes).toEqual([...codes].sort());
    expect(new Set(codes).size).toBe(codes.length);
  });
});

describe("thân lỗi (§6.0)", () => {
  it("CapabilityError đúng dạng và khả năng đó thực sự không khả dụng khi thiếu LLM", () => {
    const { detail } = CAPABILITY_ERROR_BODY;
    expect(detail.code).toBe("CAPABILITY_UNAVAILABLE");
    expect(CAPABILITY_NAMES).toContain(detail.capability);
    expect(CAPABILITY_REASONS).toContain(detail.reason);
    const capability = RUNTIME_FIXTURES.offline.capabilities?.find((c) => c.name === detail.capability);
    expect(capability?.reason).toBe(detail.reason);
  });

  it("LLM_UPSTREAM_ERROR không lộ endpoint hay tên model", () => {
    expect(LLM_UPSTREAM_ERROR_BODY.detail.code).toBe("LLM_UPSTREAM_ERROR");
    expect(LLM_UPSTREAM_ERROR_BODY.detail.message).not.toMatch(/https?:|gpt|claude|model/i);
  });
});

describe("handler giả", () => {
  it("chỉ thay endpoint chưa có ở backend; /v1/runtime và sinh câu tính toán không bị thay", () => {
    // M3 môn học, M7 tạo bộ đề, M9 chấm tự luận, chunk của Vault (§6.6) chưa có ở backend của nhánh này.
    expect(Object.keys(mockApi).sort()).toEqual(["gradeEssay"]);
  });

  it("chọn kịch bản theo query, sai giá trị thì về kịch bản dự phòng", () => {
    expect(scenarioFromSearch("?mock_runtime=llm")).toBe("llm");
    expect(scenarioFromSearch("?a=1&mock_runtime=no_tesseract")).toBe("no_tesseract");
    expect(scenarioFromSearch("?mock_runtime=khong-co")).toBe(DEFAULT_RUNTIME_SCENARIO);
    expect(scenarioFromSearch("")).toBe(DEFAULT_RUNTIME_SCENARIO);
    expect(scenarioFromSearch("", "llm")).toBe("llm");
    expect(scenarioFromSearch("?mock_runtime=offline", "llm")).toBe("offline");
  });
});

describe("fillRuntimeGaps (M1)", () => {
  const backendBody = { demo_mode: true, llm_configured: false, vlm_configured: false };

  it("chỉ bù capabilities và mock_extractor khi backend chưa trả, giữ nguyên cờ của backend", () => {
    const filled = fillRuntimeGaps(backendBody);
    expect(filled).toEqual({
      ...backendBody,
      capabilities: RUNTIME_FIXTURES.offline.capabilities,
      mock_extractor: RUNTIME_FIXTURES.offline.mock_extractor,
    });
    expect(RUNTIME_GAP_FIELDS).toEqual(["capabilities", "mock_extractor"]);
  });

  it("backend đã cấu hình LLM thì bù theo kịch bản có LLM", () => {
    const filled = fillRuntimeGaps({ ...backendBody, demo_mode: false, llm_configured: true });
    expect(filled).toMatchObject({ llm_configured: true, capabilities: RUNTIME_FIXTURES.llm.capabilities });
  });

  it("field backend đã trả (kể cả sai dạng) không bị thay", () => {
    const own = [{ name: "ocr", available: true, reason: null, detail_vi: null }];
    expect(fillRuntimeGaps({ ...backendBody, capabilities: own, mock_extractor: false })).toEqual({
      ...backendBody,
      capabilities: own,
      mock_extractor: false,
    });
    expect(fillRuntimeGaps({ ...backendBody, capabilities: "hỏng" })).toMatchObject({ capabilities: "hỏng" });
  });

  it("thân không phải object trả nguyên văn; fixture không bị sửa qua kết quả", () => {
    expect(fillRuntimeGaps(null)).toBeNull();
    expect(fillRuntimeGaps("x")).toBe("x");
    const filled = fillRuntimeGaps(backendBody);
    if (
      typeof filled === "object" &&
      filled !== null &&
      "capabilities" in filled &&
      Array.isArray(filled.capabilities)
    ) {
      filled.capabilities.pop();
    }
    expect(RUNTIME_FIXTURES.offline.capabilities).toHaveLength(12);
  });
});
