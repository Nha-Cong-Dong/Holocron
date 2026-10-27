import { describe, expect, it } from "vitest";

import {
  DEFAULT_API_BASE,
  normalizeApiBase,
  parseInjectedRuntime,
  parseMockFlag,
  resolveApiBase,
  resolveAuthHeaders,
  TOKEN_HEADER,
} from "./config";

describe("normalizeApiBase", () => {
  it("nhận URL tuyệt đối http(s) và đường dẫn gốc, bỏ dấu / cuối", () => {
    expect(normalizeApiBase("http://127.0.0.1:8123/")).toBe("http://127.0.0.1:8123");
    expect(normalizeApiBase("https://localhost:8000/api")).toBe("https://localhost:8000/api");
    expect(normalizeApiBase("  /api/holocron//  ")).toBe("/api/holocron");
    expect(normalizeApiBase("/")).toBe("");
  });

  it("từ chối giá trị không dùng được", () => {
    for (const bad of ["", "   ", "api/holocron", "//evil.example", "ftp://host", "javascript:alert(1)", "/a b"]) {
      expect(normalizeApiBase(bad)).toBeNull();
    }
  });
});

describe("normalizeApiBase: query và fragment", () => {
  it("từ chối base URL có query hoặc fragment, kể cả rỗng", () => {
    for (const bad of [
      "http://127.0.0.1:8000?x=1",
      "http://127.0.0.1:8000/api?token=abc",
      "http://127.0.0.1:8000/#frag",
      "/api/holocron?",
      "/api/holocron#",
      "https://localhost:8000/api/?a=1#b",
    ]) {
      expect(normalizeApiBase(bad)).toBeNull();
    }
  });

  it("object chèn lúc chạy có base URL kèm query thì bị bỏ cả object", () => {
    expect(parseInjectedRuntime({ apiBase: "http://127.0.0.1:9000/?debug=1", token: "abc" })).toBeNull();
  });
});

describe("parseInjectedRuntime", () => {
  it("nhận object đúng dạng, token tuỳ chọn", () => {
    expect(parseInjectedRuntime({ apiBase: "http://127.0.0.1:9000/" })).toEqual({
      apiBase: "http://127.0.0.1:9000",
      token: null,
    });
    expect(parseInjectedRuntime({ apiBase: "http://127.0.0.1:9000", token: null })).toEqual({
      apiBase: "http://127.0.0.1:9000",
      token: null,
    });
    expect(parseInjectedRuntime({ apiBase: "/", token: "abcDEF123-_." })).toEqual({
      apiBase: "",
      token: "abcDEF123-_.",
    });
  });

  it("bỏ cả object khi bất kỳ field nào sai dạng", () => {
    const bad: unknown[] = [
      undefined,
      null,
      "http://127.0.0.1:9000",
      42,
      [],
      {},
      { apiBase: 9000 },
      { apiBase: "relative/path" },
      { apiBase: "http://127.0.0.1:9000", token: 123 },
      { apiBase: "http://127.0.0.1:9000", token: "" },
      { apiBase: "http://127.0.0.1:9000", token: "có dấu" },
      { apiBase: "http://127.0.0.1:9000", token: "line\nbreak" },
      { apiBase: "http://127.0.0.1:9000", token: "x".repeat(513) },
    ];
    for (const value of bad) {
      expect(parseInjectedRuntime(value)).toBeNull();
    }
  });
});

describe("resolveApiBase", () => {
  const injected = { apiBase: "http://127.0.0.1:9000", token: null };

  it("ưu tiên object chèn lúc chạy, rồi biến build, rồi mặc định", () => {
    expect(resolveApiBase(injected, "http://127.0.0.1:8000")).toBe("http://127.0.0.1:9000");
    expect(resolveApiBase(null, "http://127.0.0.1:8000/")).toBe("http://127.0.0.1:8000");
    expect(resolveApiBase(null, undefined)).toBe(DEFAULT_API_BASE);
  });

  it("biến build sai dạng thì dùng mặc định", () => {
    expect(resolveApiBase(null, "")).toBe(DEFAULT_API_BASE);
    expect(resolveApiBase(null, "localhost:8000")).toBe(DEFAULT_API_BASE);
  });

  it("base URL có query hoặc fragment thì rơi về mặc định", () => {
    expect(resolveApiBase(null, "http://127.0.0.1:8000/?x=1")).toBe(DEFAULT_API_BASE);
    expect(resolveApiBase(null, "/api/holocron#a")).toBe(DEFAULT_API_BASE);
  });
});

describe("resolveAuthHeaders", () => {
  it("chỉ gắn header token khi vỏ desktop cấp token", () => {
    expect(resolveAuthHeaders(null)).toEqual({});
    expect(resolveAuthHeaders({ apiBase: "", token: null })).toEqual({});
    expect(resolveAuthHeaders({ apiBase: "", token: "t0k3n" })).toEqual({ [TOKEN_HEADER]: "t0k3n" });
    expect(TOKEN_HEADER).toBe("X-Holocron-Token");
  });
});

describe("parseMockFlag", () => {
  it("chỉ bật với 1 hoặc true", () => {
    expect(parseMockFlag("1")).toBe(true);
    expect(parseMockFlag("true")).toBe(true);
    expect(parseMockFlag(undefined)).toBe(false);
    expect(parseMockFlag("0")).toBe(false);
    expect(parseMockFlag("")).toBe(false);
    expect(parseMockFlag("yes")).toBe(false);
  });
});

describe("normalizeApiBase: dấu gạch ngược và thông tin xác thực", () => {
  it("từ chối dấu gạch ngược ở bất kỳ đâu và phần user:pass@ trước tên máy", () => {
    for (const bad of [
      "http://127.0.0.1:8000\\api",
      "http:\\\\127.0.0.1:8000",
      "/api\\holocron",
      "http://127.0.0.1:8000\\@may-khac",
      "http://user:pass@127.0.0.1:8000",
      "http://127.0.0.1@may-khac.example",
      "https://token@localhost:8000/api",
      "/api/@holocron",
    ]) {
      expect(normalizeApiBase(bad)).toBeNull();
    }
  });

  it("biến build hay object chèn có thông tin xác thực thì không dùng, rơi về mặc định", () => {
    expect(resolveApiBase(null, "http://user:pass@127.0.0.1:8000")).toBe(DEFAULT_API_BASE);
    expect(parseInjectedRuntime({ apiBase: "http://a:b@127.0.0.1:9000", token: null })).toBeNull();
  });
});
