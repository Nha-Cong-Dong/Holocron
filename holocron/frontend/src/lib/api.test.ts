/**
 * `request()` và `getRuntime()` với `fetch` giả: không gọi mạng. Kiểm phân loại lỗi ở ranh giới, gộp header token và
 * việc cờ mock chỉ bù field còn thiếu của `/v1/runtime` thay vì thay cả endpoint.
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import { RUNTIME_FIXTURES } from "@/mocks/fixtures/runtime";

import { ApiError, createApi, realApi, toUiError } from "./api";
import { DEFAULT_API_BASE, TOKEN_HEADER } from "./config";

interface FetchCall {
  url: string;
  init: RequestInit | undefined;
}

/** Thay `fetch` toàn cục bằng hàm trả `respond()` và ghi lại các lời gọi. */
function stubFetch(respond: () => Promise<Response>): FetchCall[] {
  const calls: FetchCall[] = [];
  vi.stubGlobal("fetch", (input: string, init?: RequestInit) => {
    calls.push({ url: input, init });
    return respond();
  });
  return calls;
}

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function textResponse(status: number, body: string): Response {
  return new Response(body, { status, headers: { "Content-Type": "text/html" } });
}

async function captureError(promise: Promise<unknown>): Promise<ApiError> {
  try {
    await promise;
  } catch (error) {
    if (error instanceof ApiError) return error;
    throw error;
  }
  throw new Error("lời gọi không ném lỗi");
}

function headersOf(call: FetchCall | undefined): Record<string, string> {
  const headers = call?.init?.headers;
  const result: Record<string, string> = {};
  new Headers(headers).forEach((value, key) => {
    result[key] = value;
  });
  return result;
}

const BACKEND_RUNTIME = { demo_mode: true, llm_configured: false, vlm_configured: false };

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("request()", () => {
  it("fetch ném lỗi: lỗi mạng, status null, có nút thử lại", async () => {
    stubFetch(() => Promise.reject(new TypeError("Failed to fetch")));
    const error = await captureError(realApi.getItem("ing_1"));
    expect(error.status).toBeNull();
    expect(error.info.kind).toBe("network");
    expect(error.info.action).toBe("retry");
    expect(error.message).toContain(DEFAULT_API_BASE);
  });

  it("503 có thân JSON là kho bận; 503 thân không phải JSON là không tới được backend", async () => {
    stubFetch(() => Promise.resolve(jsonResponse(503, { detail: "Kho dữ liệu đang bận" })));
    expect((await captureError(realApi.getItem("ing_1"))).info.kind).toBe("busy");

    stubFetch(() => Promise.resolve(textResponse(503, "<html>Service Unavailable</html>")));
    const proxy = await captureError(realApi.getItem("ing_1"));
    expect(proxy.status).toBe(503);
    expect(proxy.info.kind).toBe("server");
  });

  it("500 thân không phải JSON là lỗi server, có nút thử lại", async () => {
    stubFetch(() => Promise.resolve(textResponse(500, "Internal Server Error")));
    const error = await captureError(realApi.getItem("ing_1"));
    expect(error.info.kind).toBe("server");
    expect(error.info.action).toBe("retry");
  });

  it("mã thành công nhưng thân không phải JSON là phản hồi không hợp lệ", async () => {
    stubFetch(() => Promise.resolve(textResponse(200, "<!doctype html><html></html>")));
    const error = await captureError(realApi.getItem("ing_1"));
    expect(error.status).toBe(200);
    expect(error.info.kind).toBe("server");
  });

  it("gộp header token với header riêng của request; header riêng không ghi đè được token", async () => {
    vi.stubGlobal("window", {
      __HOLOCRON_RUNTIME__: { apiBase: "http://127.0.0.1:9000/", token: "tok-123" },
      location: { search: "" },
    });
    const calls = stubFetch(() => Promise.resolve(jsonResponse(201, { batch_id: "b" })));
    await realApi.createTwinBatch("bp_1", { count: 2, base_seed: null }, "idem-1");
    expect(calls[0]?.url).toBe("http://127.0.0.1:9000/v1/blueprints/bp_1/twins");
    expect(headersOf(calls[0])).toEqual({
      "content-type": "application/json",
      "idempotency-key": "idem-1",
      [TOKEN_HEADER.toLowerCase()]: "tok-123",
    });
  });

  it("không có vỏ desktop thì không gửi header token", async () => {
    const calls = stubFetch(() => Promise.resolve(jsonResponse(200, { item_id: "ing_1" })));
    await realApi.getItem("ing 1/2");
    expect(calls[0]?.url).toBe(`${DEFAULT_API_BASE}/v1/ingestion/items/ing%201%2F2`);
    expect(headersOf(calls[0])).toEqual({});
  });

  it("luồng gọi truyền ngữ cảnh: 422 khi tải đề có câu riêng", async () => {
    stubFetch(() =>
      Promise.resolve(jsonResponse(422, { detail: [{ loc: ["body", "quantum"], msg: "x", type: "x" }] })),
    );
    const file = new File(["x"], "de.png", { type: "image/png" });
    const error = await captureError(realApi.uploadDocument(file, "a.b.c", 30));
    expect(error.info.kind).toBe("validation");
    expect(error.info.message).toContain("quantum");
    expect(error.info.message).not.toMatch(/body|loc|422/);
  });
});

describe("getRuntime()", () => {
  it("không bật mock: trả đúng dữ liệu backend đã kiểm dạng", async () => {
    stubFetch(() => Promise.resolve(jsonResponse(200, BACKEND_RUNTIME)));
    const runtime = await realApi.getRuntime();
    expect(runtime).toEqual({ ...BACKEND_RUNTIME, mock_extractor: null, capabilities: null });
  });

  it("thân thiếu cờ bắt buộc là phản hồi không hợp lệ", async () => {
    stubFetch(() => Promise.resolve(jsonResponse(200, { demo_mode: true })));
    expect((await captureError(realApi.getRuntime())).info.kind).toBe("server");
  });

  it("bật mock: vẫn gọi backend thật và chỉ bù field còn thiếu", async () => {
    const calls = stubFetch(() => Promise.resolve(jsonResponse(200, BACKEND_RUNTIME)));
    const runtime = await createApi(true).getRuntime();
    expect(calls.map((c) => c.url)).toEqual([`${DEFAULT_API_BASE}/v1/runtime`]);
    expect(runtime.llm_configured).toBe(false);
    expect(runtime.capabilities?.map((c) => c.name)).toEqual(RUNTIME_FIXTURES.offline.capabilities?.map((c) => c.name));
    expect(runtime.mock_extractor).toBe(true);
  });

  it("bật mock: field backend đã trả được giữ, không bị fixture ghi đè", async () => {
    const own = [{ name: "ocr", available: false, reason: "ocr_engine_missing", detail_vi: "Cài Tesseract." }];
    stubFetch(() =>
      Promise.resolve(jsonResponse(200, { ...BACKEND_RUNTIME, capabilities: own, mock_extractor: false })),
    );
    const runtime = await createApi(true).getRuntime();
    expect(runtime.capabilities).toEqual(own);
    expect(runtime.mock_extractor).toBe(false);
  });

  it("bật mock mà backend không chạy: vẫn báo không kết nối được, không trả fixture", async () => {
    stubFetch(() => Promise.reject(new TypeError("Failed to fetch")));
    const error = await captureError(createApi(true).getRuntime());
    expect(error.info.kind).toBe("network");
    expect(toUiError(error).kind).toBe("network");
  });

  it("bật mock: endpoint backend chưa có lấy từ dữ liệu mẫu, không gọi fetch", async () => {
    const calls = stubFetch(() => Promise.reject(new TypeError("không được gọi")));
    const subjects = await createApi(true).listSubjects();
    expect(subjects.length).toBeGreaterThan(0);
    expect(calls).toEqual([]);
  });
});
