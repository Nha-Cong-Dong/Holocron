/** Cài đặt AI: mã truy cập giữ trong RAM, header, thân gửi đi, phân loại 403/409, khoá không lọt vào kết quả. */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, realApi, setAccessToken } from "./api";

const SECRET = "sk-test-SECRET-VALUE-1234567890";

interface FetchCall {
  url: string;
  init: RequestInit | undefined;
}

function stubFetch(status: number, body: unknown): FetchCall[] {
  const calls: FetchCall[] = [];
  vi.stubGlobal("fetch", (input: string, init?: RequestInit) => {
    calls.push({ url: input, init });
    return Promise.resolve(
      status === 204
        ? new Response(null, { status })
        : new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }),
    );
  });
  return calls;
}

function header(call: FetchCall | undefined, name: string): string | undefined {
  const headers = call?.init?.headers;
  if (headers === undefined || Array.isArray(headers) || headers instanceof Headers) return undefined;
  return headers[name];
}

beforeEach(() => setAccessToken(null));
afterEach(() => {
  setAccessToken(null);
  vi.unstubAllGlobals();
});

describe("mã truy cập trong RAM", () => {
  const consent = { enabled: true, source: "setting", vlm_mode: "off", persistent: true };

  it("gửi X-Holocron-Token sau khi đặt, không gửi khi chưa đặt hoặc đã xoá", async () => {
    const calls = stubFetch(200, consent);
    await realApi.getAiConsent();
    setAccessToken("  abc123  ");
    await realApi.getAiConsent();
    setAccessToken("");
    await realApi.getAiConsent();
    expect(header(calls[0], "X-Holocron-Token")).toBeUndefined();
    expect(header(calls[1], "X-Holocron-Token")).toBe("abc123");
    expect(header(calls[2], "X-Holocron-Token")).toBeUndefined();
  });

  it("không ghi vào localStorage", async () => {
    const setItem = vi.fn();
    vi.stubGlobal("localStorage", { setItem, getItem: () => null, removeItem: vi.fn() });
    stubFetch(200, consent);
    setAccessToken("abc123");
    await realApi.getAiConsent();
    expect(setItem).not.toHaveBeenCalled();
  });

  it("PUT ai-consent gửi đúng thân", async () => {
    const calls = stubFetch(200, consent);
    await realApi.setAiConsent(true);
    expect(calls[0]?.url).toContain("/v1/settings/ai-consent");
    expect(calls[0]?.init?.method).toBe("PUT");
    expect(calls[0]?.init?.body).toBe(JSON.stringify({ enabled: true }));
  });
});

describe("phân loại lỗi ghi cài đặt", () => {
  async function failure(status: number, body: unknown): Promise<ApiError> {
    stubFetch(status, body);
    try {
      await realApi.setAiConsent(true);
    } catch (error) {
      if (error instanceof ApiError) return error;
    }
    throw new Error("không ném ApiError");
  }

  it("403 token_not_configured hướng dẫn đặt HOLOCRON_REQUIRE_TOKEN", async () => {
    const error = await failure(403, { detail: "x", code: "token_not_configured" });
    expect(error.info.message).toContain("HOLOCRON_REQUIRE_TOKEN");
  });

  it("403 khác nói mã truy cập sai hoặc thiếu", async () => {
    const error = await failure(403, { detail: "Forbidden" });
    expect(error.info.message).toContain("Mã truy cập sai hoặc thiếu");
  });

  it("409 consent_env_locked và settings_not_persistent có câu riêng, không chép message backend", async () => {
    const locked = await failure(409, { detail: { code: "consent_env_locked", message: "RAW-BACKEND-TEXT" } });
    expect(locked.info.message).toContain("HOLOCRON_AI_CONSENT");
    expect(locked.info.message).not.toContain("RAW-BACKEND-TEXT");
    const notPersistent = await failure(409, { detail: { code: "settings_not_persistent", message: "x" } });
    expect(notPersistent.info.message).toContain("HOLOCRON_DATA_DIR");
  });
});

describe("nhà cung cấp", () => {
  const created = {
    id: "usr_aaaa",
    kind: "openai_compatible",
    source: "user",
    template: "custom",
    label: "X",
    base_url: "https://example.com/v1",
    local: false,
    models: { vlm: null, llm: "m" },
    key_set: true,
    key_last4: "7890",
    key_storage: "plaintext_file",
    key_unreadable: false,
    enabled: true,
    priority: 100,
    budget_usd: 1,
    spent_usd: 0,
    budget_exhausted: false,
    budget_shared_with: [],
    prices: { input_per_1k_usd: 0.005, output_per_1k_usd: 0.025, per_image_usd: 0.005, source: "template_default" },
  };

  it("POST gửi khoá trong thân và kết quả chỉ có 4 ký tự cuối", async () => {
    const calls = stubFetch(201, created);
    const result = await realApi.createProvider({
      template: "custom",
      label: "X",
      base_url: "https://example.com/v1",
      api_key: SECRET,
      models: { vlm: null, llm: "m" },
    });
    expect(String(calls[0]?.init?.body)).toContain(SECRET);
    expect(JSON.stringify(result)).not.toContain(SECRET);
    expect(result.key_last4).toBe("7890");
  });

  it("DELETE 204 không đọc thân; kiểm thử gửi action và role", async () => {
    const calls = stubFetch(204, null);
    await realApi.deleteProvider("usr_aaaa");
    expect(calls[0]?.init?.method).toBe("DELETE");
    expect(calls[0]?.url).toContain("/v1/settings/providers/usr_aaaa");
    const test = stubFetch(200, { ok: true, code: "ok", http_status: 200, latency_ms: 5, models: [] });
    await realApi.testProvider("usr_aaaa", { action: "chat", role: "llm" });
    expect(test[0]?.url).toContain("/usr_aaaa/test");
    expect(test[0]?.init?.body).toBe(JSON.stringify({ action: "chat", role: "llm" }));
  });

  it("thân sai dạng thì lỗi phản hồi không hợp lệ", async () => {
    stubFetch(200, { providers: "x" });
    await expect(realApi.listProviders()).rejects.toBeInstanceOf(ApiError);
  });
});
