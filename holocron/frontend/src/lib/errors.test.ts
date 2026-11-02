import { describe, expect, it } from "vitest";

import { CAPABILITY_ERROR_BODY, LLM_UPSTREAM_ERROR_BODY } from "@/mocks/fixtures/errors";

import {
  ApiError,
  actionLabel,
  classifyHttpError,
  classifyNetworkError,
  describeUiError,
  displayBase,
  type ErrorContext,
  type ErrorKind,
  extractDetail,
  invalidResponseError,
  isCapabilityError,
  keyedMessages,
  localError,
  PAIR_QUOTE_INVALID_VI,
  PAIR_TERM_INVALID_VI,
  pairValidationMessage,
  renderError,
  toUiError,
} from "./errors";
import {
  DOCUMENT_UPLOAD_MAX_MIB,
  EXAM_UPLOAD_MAX_MIB,
  PAIR_QUOTE_MAX_CHARS,
  PAIR_QUOTE_MIN_CHARS,
  PAIR_TERM_MAX_CHARS,
  PAIR_TERM_MIN_CHARS,
} from "./limits";
import { MAX_TWIN_COUNT, MIN_TWIN_COUNT } from "./twin";

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

describe("CapabilityError nhận diện theo code trước", () => {
  it("capability và reason lạ vẫn là chưa cấu hình, với thông điệp chung", () => {
    const body = {
      detail: { code: "CAPABILITY_UNAVAILABLE", capability: "cloud_sync", reason: "quota_exhausted", message: "x" },
    };
    const error = classifyHttpError(409, body);
    expect(error.kind).toBe("capability");
    expect(error.action).toBe("none");
    expect(error.capability).toEqual({ name: null, reason: null });
    expect(error.message).toContain("chưa sẵn sàng");
    expect(error.message).not.toMatch(/cloud_sync|quota_exhausted/);
    expectUserFacing(error.message);
  });

  it("tên đã biết nhưng reason lạ: giữ tên, thông điệp chung", () => {
    const body = { detail: { ...CAPABILITY_ERROR_BODY.detail, reason: "quota_exhausted" } };
    const error = classifyHttpError(409, body);
    expect(error.kind).toBe("capability");
    expect(error.capability).toEqual({ name: "concept_pair_llm_proposal", reason: null });
    expect(error.message).not.toContain("quota_exhausted");
  });

  it("thiếu message hay capability không phải chuỗi vẫn không ném lỗi và vẫn là capability", () => {
    const error = classifyHttpError(409, { detail: { code: "CAPABILITY_UNAVAILABLE", capability: 3 } });
    expect(error.kind).toBe("capability");
    expect(error.capability).toEqual({ name: null, reason: null });
  });
});

describe("câu riêng theo luồng cho 400/413/415/422", () => {
  const contexts: ErrorContext[] = [
    "exam_upload",
    "document_upload",
    "table_edit",
    "item_review",
    "blueprint_create",
    "twin_generation",
    "essay_grade",
    "vault_source",
  ];

  it("tải đề và tải tài liệu có câu riêng, khác câu chung", () => {
    const generic400 = classifyHttpError(400, { detail: "file rỗng" }).message;
    const generic422 = classifyHttpError(422, { detail: [] }).message;
    const exam400 = classifyHttpError(400, { detail: "file rỗng" }, { context: "exam_upload" });
    expect(exam400.kind).toBe("bad_request");
    expect(exam400.message).toContain("rỗng");
    expect(exam400.message).not.toBe(generic400);

    const doc422 = classifyHttpError(422, { detail: "PDF có mật khẩu" }, { context: "document_upload" });
    expect(doc422.kind).toBe("validation");
    expect(doc422.message).toContain("mật khẩu");
    expect(doc422.message).toContain("300 trang");
    expect(doc422.message).toContain("Không mở được");
    expect(doc422.message).not.toBe(generic422);

    expect(classifyHttpError(413, null, { context: "exam_upload" }).message).toContain("10 MiB");
    expect(classifyHttpError(413, null, { context: "document_upload" }).message).toContain("50 MiB");
    expect(classifyHttpError(415, null, { context: "document_upload" }).message).toContain("PDF");
  });

  it("luồng không có câu riêng cho mã đó thì dùng câu chung; mọi câu không lộ thuật ngữ backend", () => {
    expect(classifyHttpError(413, null, { context: "table_edit" }).message).toBe(classifyHttpError(413, null).message);
    for (const context of contexts) {
      for (const status of [400, 413, 415, 422]) {
        const error = classifyHttpError(status, { detail: "chi tiết nội bộ" }, { context });
        expectUserFacing(error.message);
        expect(error.message).not.toContain("chi tiết nội bộ");
        expect(error.action).toBe("none");
      }
    }
  });

  it("ngữ cảnh không đổi phân loại 409 có phiên bản", () => {
    const error = classifyHttpError(409, { detail: "x" }, { versioned: true, context: "table_edit" });
    expect(error.kind).toBe("version_conflict");
  });
});

describe("renderError", () => {
  it("lỗi hiển thị màn hình: câu tiếng Việt, có nút thử lại", () => {
    const error = renderError();
    expect(error.action).toBe("retry");
    expect(actionLabel(error.action)).toBe("Thử lại");
    expectUserFacing(error.message);
  });
});

describe("keyedMessages", () => {
  it("key không trùng kể cả khi hai câu giống hệt, giữ thứ tự", () => {
    const keyed = keyedMessages(["Ô (1, 2) lệch", "Ô (1, 2) lệch", "Khác", "Ô (1, 2) lệch"]);
    expect(keyed.map((k) => k.message)).toEqual(["Ô (1, 2) lệch", "Ô (1, 2) lệch", "Khác", "Ô (1, 2) lệch"]);
    expect(new Set(keyed.map((k) => k.key)).size).toBe(4);
  });
});

describe("404 theo thân lỗi và luồng gọi", () => {
  it("404 thân không phải JSON là không tới được backend, không phải thiếu dữ liệu", () => {
    for (const body of [null, "<html>Not Found</html>", { other: 1 }]) {
      const error = classifyHttpError(404, body);
      expect(error.kind).toBe("server");
      expect(error.action).toBe("retry");
      expect(error.message).not.toContain("Không tìm thấy dữ liệu");
    }
  });

  it("404 JSON của /v1/runtime là sai địa chỉ backend; luồng khác có câu riêng", () => {
    const runtime = classifyHttpError(404, { detail: "Not Found" }, { context: "runtime" });
    expect(runtime.kind).toBe("server");
    expectUserFacing(runtime.message);
    const blueprint = classifyHttpError(404, { detail: "Not Found" }, { context: "blueprint_create" });
    expect(blueprint.kind).toBe("not_found");
    expect(blueprint.message).toContain("chưa hỗ trợ tạo bộ đề");
    expect(classifyHttpError(404, { detail: "x" }).message).toBe(
      "Không tìm thấy dữ liệu yêu cầu. Có thể nó đã bị xoá.",
    );
  });
});

describe("409 khi tạo bộ đề hoặc sinh câu", () => {
  it("đề chưa duyệt: lý do dễ hiểu, không có nút tải lại hay gửi lại", () => {
    for (const context of ["twin_generation", "blueprint_create"] as const) {
      const error = classifyHttpError(409, { detail: "item chưa approved" }, { context });
      expect(error.kind).toBe("unapproved_source");
      expect(error.action).toBe("none");
      expect(actionLabel(error.action)).toBeNull();
      expect(error.message).toContain("chưa được duyệt");
      expect(error.message).not.toContain("approved");
      expectUserFacing(error.message);
    }
  });

  it("409 BLOCKING khi sinh câu giữ danh sách lỗi chặn với câu dẫn riêng", () => {
    const error = classifyHttpError(
      409,
      { detail: { message: "x", issues: ["Ô (1, 2) lệch"] } },
      { context: "twin_generation" },
    );
    expect(error.kind).toBe("blocking");
    expect(error.issues).toEqual(["Ô (1, 2) lệch"]);
    expect(error.message).toContain("chưa sinh câu hỏi");
    expect(error.action).toBe("none");
  });

  it("CapabilityError vẫn được nhận diện trước, kể cả khi tạo bộ đề", () => {
    const error = classifyHttpError(409, CAPABILITY_ERROR_BODY, { context: "blueprint_create" });
    expect(error.kind).toBe("capability");
  });
});

describe("đơn vị dung lượng và giới hạn dùng hằng chung", () => {
  it("dung lượng ghi theo MiB như spec §6.0, số lấy từ lib/limits", () => {
    expect(classifyHttpError(413, null, { context: "exam_upload" }).message).toContain(`${EXAM_UPLOAD_MAX_MIB} MiB`);
    expect(classifyHttpError(413, null, { context: "document_upload" }).message).toContain(
      `${DOCUMENT_UPLOAD_MAX_MIB} MiB`,
    );
    for (const context of ["exam_upload", "document_upload"] as const) {
      expect(classifyHttpError(413, null, { context }).message).not.toMatch(/\bMB\b/);
    }
    expect(classifyHttpError(422, null, { context: "twin_generation" }).message).toContain(
      `từ ${MIN_TWIN_COUNT} đến ${MAX_TWIN_COUNT}`,
    );
  });
});

describe("displayBase", () => {
  it("không in thông tin xác thực trong địa chỉ backend", () => {
    expect(displayBase("http://user:pass@127.0.0.1:8000/api")).toBe("http://127.0.0.1:8000/api");
    expect(classifyNetworkError("https://tok@localhost:8000").message).not.toContain("tok@");
    expect(displayBase("/api/holocron")).toBe("/api/holocron");
  });
});

describe("ApiError và toUiError", () => {
  it("toUiError đọc lỗi đã phân loại; lỗi khác thành lỗi không rõ", () => {
    const info = classifyHttpError(409, { detail: "x" }, { context: "twin_generation" });
    expect(toUiError(new ApiError(409, info))).toEqual(info);
    expect(toUiError(new Error("x")).kind).toBe("unknown");
    expect(localError("Câu trả lời đang trống.").action).toBe("none");
  });
});

describe("cặp khái niệm (§6.6)", () => {
  const GENERIC_422 = classifyHttpError(422, { detail: [] }).message;

  function invalid(...fields: unknown[][]): { detail: { loc: unknown[]; msg: string; type: string }[] } {
    return { detail: fields.map((loc) => ({ loc, msg: "x", type: "value_error" })) };
  }

  it("câu 422 lấy số từ limits.ts", () => {
    expect(PAIR_QUOTE_INVALID_VI).toContain(`${PAIR_QUOTE_MIN_CHARS} đến ${PAIR_QUOTE_MAX_CHARS}`);
    expect(PAIR_TERM_INVALID_VI).toContain(`${PAIR_TERM_MIN_CHARS} đến ${PAIR_TERM_MAX_CHARS}`);
    expectUserFacing(PAIR_QUOTE_INVALID_VI);
    expectUserFacing(PAIR_TERM_INVALID_VI);
  });

  it("pairValidationMessage đọc phần tử cuối của loc; ưu tiên definition_quote; field khác hay sai dạng ⇒ câu chung", () => {
    expect(pairValidationMessage(invalid(["body", "definition_quote"]).detail)).toBe(PAIR_QUOTE_INVALID_VI);
    expect(pairValidationMessage([{ loc: ["body", "term"] }])).toBe(PAIR_TERM_INVALID_VI);
    expect(pairValidationMessage([{ loc: ["body", "term"] }, { loc: ["body", "definition_quote"] }])).toBe(
      PAIR_QUOTE_INVALID_VI,
    );
    expect(pairValidationMessage([{ loc: ["body", "chunk_id"] }])).toBe(GENERIC_422);
    expect(pairValidationMessage([{ loc: ["body", "expected_version"] }])).toBe(GENERIC_422);
    expect(pairValidationMessage([{ loc: ["body"] }])).toBe(GENERIC_422);
    expect(pairValidationMessage([{ loc: ["body", "term", 0] }])).toBe(GENERIC_422);
    expect(pairValidationMessage("trích dẫn không khớp")).toBe(GENERIC_422);
    expect(pairValidationMessage(undefined)).toBe(GENERIC_422);
  });

  it("422 tạo, xác nhận theo field; 422 từ chối luôn là câu chung", () => {
    for (const context of ["pair_create", "pair_confirm"] satisfies ErrorContext[]) {
      const quote = classifyHttpError(422, invalid(["body", "definition_quote"]), { context });
      expect(quote).toMatchObject({ kind: "validation", action: "none", message: PAIR_QUOTE_INVALID_VI });
      const term = classifyHttpError(422, invalid(["body", "term"]), { context });
      expect(term.message).toBe(PAIR_TERM_INVALID_VI);
      expect(classifyHttpError(422, invalid(["body", "chunk_id"]), { context }).message).toBe(GENERIC_422);
    }
    const reject = classifyHttpError(422, invalid(["body", "definition_quote"]), { context: "pair_reject" });
    expect(reject.message).toBe(GENERIC_422);
  });

  it("409 xác nhận, từ chối: lệch phiên bản, tải lại; 404: cặp hoặc chunk không còn", () => {
    for (const context of ["pair_confirm", "pair_reject"] satisfies ErrorContext[]) {
      const conflict = classifyHttpError(409, { detail: "version lệch" }, { context, versioned: true });
      expect(conflict).toMatchObject({ kind: "version_conflict", action: "reload" });
      expect(conflict.message).toContain("Khái niệm này vừa được sửa ở nơi khác");
      expectUserFacing(conflict.message);
    }
    for (const context of ["pair_create", "pair_confirm", "pair_reject"] satisfies ErrorContext[]) {
      const missing = classifyHttpError(404, { detail: "không tìm thấy chunk" }, { context });
      expect(missing.kind).toBe("not_found");
      expect(missing.message).toContain("Khái niệm hoặc đoạn nguồn không còn (tài liệu có thể đã bị xoá)");
      expectUserFacing(missing.message);
    }
  });

  it("danh sách cặp: 404 và 422 có câu riêng, không lộ mã hay field", () => {
    const missing = classifyHttpError(404, { detail: "Not Found" }, { context: "pairs_list" });
    const invalidFilter = classifyHttpError(422, invalid(["query", "chapter_code"]), { context: "pairs_list" });
    expect(missing.kind).toBe("not_found");
    expect(invalidFilter.kind).toBe("validation");
    expect(missing.message).not.toBe(classifyHttpError(404, { detail: "x" }).message);
    expect(invalidFilter.message).not.toBe(GENERIC_422);
    expectUserFacing(missing.message);
    expectUserFacing(invalidFilter.message);
  });
});
