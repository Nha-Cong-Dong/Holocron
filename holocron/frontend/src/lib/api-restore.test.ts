/** `restorePage` (§6.5 `POST …/pages/{n}/restore`, GĐ0 0D): thân gửi đi, kiểm dạng kết quả, mock cùng contract. */

import { afterEach, describe, expect, it, vi } from "vitest";

import { INITIAL_PAGES, MOCK_IMPORT_DOC_ID } from "@/mocks/fixtures/documents";
import { resetMockDocumentSessions } from "@/mocks/store";

import { ApiError, createApi, LOCAL_EDITOR_ID, realApi } from "./api";
import { DEFAULT_API_BASE } from "./config";

const DOC_ID = "doc_ffffffffffffffff";

interface FetchCall {
  url: string;
  init: RequestInit | undefined;
}

function stubFetch(status: number, body: unknown): FetchCall[] {
  const calls: FetchCall[] = [];
  vi.stubGlobal("fetch", (input: string, init?: RequestInit) => {
    calls.push({ url: input, init });
    return Promise.resolve(
      new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }),
    );
  });
  return calls;
}

function droppedBody(extra: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    ...structuredClone(INITIAL_PAGES[0]),
    document_id: DOC_ID,
    page_number: 4,
    version: 3,
    page_role: "chapter_nav",
    dropped: true,
    ...extra,
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
  resetMockDocumentSessions();
});

describe("realApi.restorePage", () => {
  it("POST …/restore gửi editor_id và expected_version; trả trang đã khôi phục", async () => {
    const calls = stubFetch(200, droppedBody({ dropped: false, restored: true, version: 4 }));
    const page = await realApi.restorePage(DOC_ID, 4, 3);
    expect(calls[0]?.init?.method).toBe("POST");
    expect(calls[0]?.url).toBe(`${DEFAULT_API_BASE}/v1/documents/${DOC_ID}/pages/4/restore`);
    expect(JSON.parse(String(calls[0]?.init?.body))).toEqual({ editor_id: LOCAL_EDITOR_ID, expected_version: 3 });
    expect(page).toMatchObject({ dropped: false, restored: true, version: 4, pageNumber: 4 });
  });

  it("thân sai dạng ⇒ lỗi phản hồi không hợp lệ, không trả trang hỏng", async () => {
    stubFetch(200, { page_number: 4 });
    await expect(realApi.restorePage(DOC_ID, 4, 3)).rejects.toBeInstanceOf(ApiError);
  });

  it("409, 404, 422 thành ApiError mang mã HTTP để lớp hiển thị chọn câu", async () => {
    for (const status of [409, 404, 422]) {
      stubFetch(status, { detail: "x" });
      const error = await realApi.restorePage(DOC_ID, 4, 3).catch((caught: unknown) => caught);
      expect(error).toBeInstanceOf(ApiError);
      expect(error instanceof ApiError ? error.status : null).toBe(status);
    }
  });
});

describe("bật dữ liệu mẫu: restorePage", () => {
  it("tài liệu mẫu: không gọi fetch; trang không bỏ mềm trả nguyên trang, không kiểm version (§6.5)", async () => {
    const calls = stubFetch(200, droppedBody());
    const mocked = createApi(true);
    const page = await mocked.restorePage(MOCK_IMPORT_DOC_ID, 1, 999);
    expect(calls).toHaveLength(0);
    expect(page).toMatchObject({ pageNumber: 1, dropped: false });
  });

  it("tài liệu mẫu: trang không có ⇒ 404", async () => {
    const mocked = createApi(true);
    const error = await mocked.restorePage(MOCK_IMPORT_DOC_ID, 99, 1).catch((caught: unknown) => caught);
    expect(error instanceof ApiError ? error.status : null).toBe(404);
  });

  it("tài liệu khác gọi backend thật", async () => {
    const calls = stubFetch(200, droppedBody({ dropped: false, restored: true, version: 4 }));
    await createApi(true).restorePage(DOC_ID, 4, 3);
    expect(calls).toHaveLength(1);
  });
});
