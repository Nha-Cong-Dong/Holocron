/**
 * Mã lỗi để báo (`UiError.diagnostic`): mang mã HTTP, dạng `detail` và mã con để người dùng chép khi báo lỗi; thông
 * điệp hiển thị (`message`) vẫn là câu cố định, không lộ các mã đó.
 */

import { describe, expect, it } from "vitest";

import { classifyHttpError, classifyNetworkError, httpDiagnostic, invalidResponseError, localError } from "./errors";

/** Thân 409 thật của `POST /v1/blueprints` khi có slot ESSAY mà chấm tự luận offline chưa có (§6.7 bước 5). */
const ESSAY_CAPABILITY_409 = {
  detail: {
    code: "CAPABILITY_UNAVAILABLE",
    capability: "essay_grading_offline",
    reason: "not_implemented",
    message: "Khả năng essay_grading_offline không khả dụng.",
  },
};

describe("UiError.diagnostic", () => {
  it("409 CapabilityError: mã lỗi có HTTP, dạng detail, khả năng và lý do; message không lộ các mã đó", () => {
    const error = classifyHttpError(409, ESSAY_CAPABILITY_409, { context: "blueprint_create" });
    expect(error.kind).toBe("capability");
    expect(error.diagnostic).toBe(
      "HTTP 409 · CapabilityError · essay_grading_offline · not_implemented · blueprint_create",
    );
    expect(error.message).not.toMatch(/409|CapabilityError|not_implemented|essay_grading_offline/);
  });

  it("422 validate: chỉ lấy loc và type, không lấy input hay msg (có thể chứa nội dung người dùng nhập)", () => {
    const body = {
      detail: [
        {
          loc: ["body", "theory_slots", 0, "chapter_code"],
          msg: "String should match pattern",
          type: "string_pattern_mismatch",
          input: "chương bí mật",
        },
      ],
    };
    const error = classifyHttpError(422, body, { context: "blueprint_create" });
    expect(error.kind).toBe("validation");
    expect(error.diagnostic).toBe(
      "HTTP 422 · ValidationError · body.theory_slots.0.chapter_code:string_pattern_mismatch · blueprint_create",
    );
    expect(error.diagnostic).not.toContain("chương bí mật");
    expect(error.message).not.toMatch(/422|ValidationError|theory_slots/);
  });

  it("422 detail chuỗi của backend (câu tính toán chưa hỗ trợ) được chép nguyên văn", () => {
    const error = classifyHttpError(
      422,
      { detail: "Blueprint có câu tính toán chưa được hỗ trợ trong bản này" },
      { context: "blueprint_create" },
    );
    expect(error.diagnostic).toBe(
      "HTTP 422 · Blueprint có câu tính toán chưa được hỗ trợ trong bản này · blueprint_create",
    );
  });

  it("404 không tìm thấy blueprint khi sinh câu: câu riêng của luồng, mã lỗi giữ detail của backend", () => {
    const error = classifyHttpError(404, { detail: "không tìm thấy blueprint" }, { context: "twin_generation" });
    expect(error.kind).toBe("not_found");
    expect(error.message).toContain("tạo lại bộ đề");
    expect(error.diagnostic).toBe("HTTP 404 · không tìm thấy blueprint · twin_generation");
  });

  it("thân không phải JSON, LLM upstream, issue BLOCKING và detail lạ đều có mã ngắn", () => {
    expect(classifyHttpError(404, null).diagnostic).toBe("HTTP 404 · non-JSON");
    expect(httpDiagnostic(502, { detail: { code: "LLM_UPSTREAM_ERROR", message: "x" } })).toBe(
      "HTTP 502 · LLM_UPSTREAM_ERROR",
    );
    expect(httpDiagnostic(409, { detail: { message: "x", issues: ["a", "b"] } })).toBe("HTTP 409 · BlockingIssues(2)");
    expect(httpDiagnostic(500, { detail: 42 })).toBe("HTTP 500 · detail?");
    expect(httpDiagnostic(409, { detail: { code: "CAPABILITY_UNAVAILABLE", capability: "x y", reason: null } })).toBe(
      "HTTP 409 · CapabilityError · ? · ?",
    );
  });

  it("detail chuỗi dài bị cắt; nhiều lỗi validate chỉ liệt kê ba lỗi đầu", () => {
    const long = httpDiagnostic(400, { detail: "x".repeat(500) });
    expect([...long].length).toBeLessThan(200);
    expect(long.endsWith("…")).toBe(true);
    const items = Array.from({ length: 5 }, (_, index) => ({ loc: ["body", `f${index}`], type: "missing" }));
    expect(httpDiagnostic(422, { detail: items })).toBe(
      "HTTP 422 · ValidationError · body.f0:missing · body.f1:missing · body.f2:missing · +2",
    );
  });

  it("lỗi mạng và phản hồi sai dạng có mã riêng; lỗi dựng ở giao diện không có mã", () => {
    expect(classifyNetworkError("http://127.0.0.1:8000").diagnostic).toBe("NETWORK · fetch_failed");
    expect(invalidResponseError().diagnostic).toBe("RESPONSE · invalid_body");
    expect(localError("Câu trả lời đang trống.").diagnostic).toBeUndefined();
  });
});
