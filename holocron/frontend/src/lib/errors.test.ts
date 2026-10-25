import { describe, expect, it } from "vitest";

import { CAPABILITY_ERROR_BODY, LLM_UPSTREAM_ERROR_BODY } from "@/mocks/fixtures/errors";

import {
  actionLabel,
  classifyHttpError,
  classifyNetworkError,
  describeUiError,
  type ErrorKind,
  extractDetail,
  invalidResponseError,
  isCapabilityError,
} from "./errors";

/** Thông điệp hiển thị không được lộ mã HTTP hay tên field/nhận dạng nội bộ của backend. */
function expectUserFacing(message: string): void {
  expect(message).not.toMatch(/\b(4|5)\d\d\b/);
  expect(message).not.toMatch(/HTTP|detail|expected_version|concept_pair|CAPABILITY_UNAVAILABLE|LLM_UPSTREAM/);
  expect(message.length).toBeGreaterThan(0);
}

describe("classifyHttpError", () => {
  it("409 CapabilityError: chưa cấu hình, không có nút thử lại", () => {
    const error = classifyHttpError(409, CAPABILITY_ERROR_BODY);
    expect(error.kind).toBe("capability");
    expect(error.action).toBe("none");
    expect(error.capability).toEqual({ name: "concept_pair_llm_proposal", reason: "llm_not_configured" });
    expect(error.message).toContain("chưa được cấu hình LLM");
    expectUserFacing(error.message);
  });

  it("502 LLM_UPSTREAM_ERROR: lỗi tạm thời, có nút thử lại", () => {
    const error = classifyHttpError(502, LLM_UPSTREAM_ERROR_BODY);
    expect(error.kind).toBe("llm_upstream");
    expect(error.action).toBe("retry");
    expect(error.message).toContain("tạm thời");
    expectUserFacing(error.message);
  });

  it("không ném lỗi với mọi dạng detail dạng object (lỗi describeError cũ)", () => {
    const shapes: unknown[] = [
      { detail: {} },
      { detail: { message: "x" } },
      { detail: { code: "KHAC" } },
      { detail: { issues: "không phải mảng", message: "x" } },
      { detail: null },
      { detail: 12 },
      "chuỗi trần",
      null,
      [],
    ];
    for (const status of [400, 409, 422, 500, 502, 503]) {
      for (const body of shapes) {
        expect(() => classifyHttpError(status, body)).not.toThrow();
      }
    }
  });

  it("409 BLOCKING: giữ danh sách issue của Gate, bỏ phần tử không phải chuỗi", () => {
    const error = classifyHttpError(409, { detail: { message: "còn lỗi BLOCKING", issues: ["Ô (1, 2) lệch", 3] } });
    expect(error.kind).toBe("blocking");
    expect(error.issues).toEqual(["Ô (1, 2) lệch"]);
    expect(describeUiError(error)).toContain("Ô (1, 2) lệch");
    expectUserFacing(error.message);
  });

  it("409 dạng chuỗi: lệch phiên bản khi request có expected_version, ngược lại là xung đột trạng thái", () => {
    const body = { detail: "version lệch: hiện tại 3" };
    const versioned = classifyHttpError(409, body, { versioned: true });
    expect(versioned.kind).toBe("version_conflict");
    expect(versioned.action).toBe("reload");
    expect(versioned.message).not.toContain("version lệch");
    const state = classifyHttpError(409, { detail: "item đã approve, không sửa được" });
    expect(state.kind).toBe("state_conflict");
    expect(state.action).toBe("reload");
    expect(state.message).not.toContain("approve");
  });

  it("phân loại theo mã còn lại, không chép câu chữ của backend", () => {
    const cases: [number, unknown, ErrorKind][] = [
      [400, { detail: "file rỗng" }, "bad_request"],
      [403, { detail: "host không được phép" }, "forbidden"],
      [404, { detail: "không tìm thấy ingestion item" }, "not_found"],
      [413, { detail: "file vượt 10485760 bytes" }, "too_large"],
      [415, { detail: "Content-Type khai báo x không khớp" }, "unsupported_media"],
      [422, { detail: [{ loc: ["body", "quantum"], msg: "Input should be > 0", type: "x" }] }, "validation"],
      [503, { detail: "Kho dữ liệu đang bận, thử lại" }, "busy"],
      [503, null, "server"],
      [500, null, "server"],
      [502, null, "server"],
      [418, null, "unknown"],
    ];
    for (const [status, body, kind] of cases) {
      const error = classifyHttpError(status, body);
      expect(error.kind).toBe(kind);
      expectUserFacing(error.message);
      expect(error.message).not.toMatch(/quantum|bytes|Content-Type|ingestion item|Input should/);
    }
  });

  it("hành động: 503 bận và lỗi server thì thử lại; 422, 413 thì không", () => {
    expect(classifyHttpError(503, { detail: "bận" }).action).toBe("retry");
    expect(classifyHttpError(500, null).action).toBe("retry");
    expect(classifyHttpError(422, { detail: [] }).action).toBe("none");
    expect(classifyHttpError(413, { detail: "x" }).action).toBe("none");
  });
});

describe("isCapabilityError", () => {
  it("chỉ nhận đúng dạng §6.0", () => {
    expect(isCapabilityError(CAPABILITY_ERROR_BODY.detail)).toBe(true);
    expect(isCapabilityError({ ...CAPABILITY_ERROR_BODY.detail, capability: "khong_co" })).toBe(false);
    expect(isCapabilityError({ ...CAPABILITY_ERROR_BODY.detail, reason: "khong_co" })).toBe(false);
    expect(isCapabilityError({ ...CAPABILITY_ERROR_BODY.detail, code: "KHAC" })).toBe(false);
    expect(isCapabilityError("CAPABILITY_UNAVAILABLE")).toBe(false);
  });
});

describe("extractDetail", () => {
  it("chỉ đọc detail của thân JSON dạng FastAPI", () => {
    expect(extractDetail({ detail: "x" })).toBe("x");
    expect(extractDetail({ other: 1 })).toBeUndefined();
    expect(extractDetail(null)).toBeUndefined();
    expect(extractDetail([{ detail: "x" }])).toBeUndefined();
  });
});

describe("classifyNetworkError", () => {
  it("nêu địa chỉ backend, gợi ý thử lại", () => {
    const error = classifyNetworkError("http://127.0.0.1:8000");
    expect(error.kind).toBe("network");
    expect(error.action).toBe("retry");
    expect(error.message).toContain("http://127.0.0.1:8000");
    expect(classifyNetworkError("").message).toContain("cùng địa chỉ với giao diện");
  });
});

describe("invalidResponseError và actionLabel", () => {
  it("thân thành công không phải JSON là lỗi server", () => {
    expect(invalidResponseError().kind).toBe("server");
  });

  it("nhãn nút theo hành động", () => {
    expect(actionLabel("reload")).toBe("Tải lại bản mới nhất");
    expect(actionLabel("retry")).toBe("Thử lại");
    expect(actionLabel("none")).toBeNull();
  });
});
