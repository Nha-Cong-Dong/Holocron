import { describe, expect, it } from "vitest";

import {
  consentControl,
  emptyForm,
  keyLabel,
  keyStorageWarning,
  parseAiStatus,
  parseConsentState,
  parseProvider,
  parseProviderList,
  parseTestResult,
  roleView,
  testMessage,
  toCreateBody,
  validateForm,
} from "./ai-settings";

const SECRET = "sk-test-SECRET-VALUE-1234567890";

function provider(extra: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: "usr_aaaa",
    kind: "openai_compatible",
    source: "user",
    template: "gemini",
    label: "Gemini",
    base_url: "https://generativelanguage.googleapis.com/v1beta/openai/",
    local: false,
    models: { vlm: "m1", llm: null },
    key_set: true,
    key_last4: "7890",
    key_storage: "dpapi",
    key_unreadable: false,
    enabled: true,
    priority: 100,
    budget_usd: 5,
    spent_usd: 0.25,
    budget_exhausted: false,
    budget_shared_with: [],
    prices: { input_per_1k_usd: 0.1, output_per_1k_usd: 0.1, per_image_usd: 0.1, source: "template_default" },
    ...extra,
  };
}

function roles(reasonVlm: string | null): Record<string, unknown>[] {
  return [
    {
      role: "vlm",
      auto_send_allowed: reasonVlm === null,
      reason: reasonVlm,
      provider_id: "env_vlm",
      recipient_label: "Gemini",
      local: false,
    },
    {
      role: "llm",
      auto_send_allowed: false,
      reason: "no_provider",
      provider_id: null,
      recipient_label: null,
      local: false,
    },
  ];
}

function runtime(reasonVlm: string | null): Record<string, unknown> {
  return {
    ai_status: {
      consent_enabled: false,
      consent_source: "default",
      vlm_mode: "unset",
      settings_persistent: true,
      write_protection: "token",
      roles: roles(reasonVlm),
    },
  };
}

describe("parseAiStatus", () => {
  it("nhận ai_status đúng dạng", () => {
    const status = parseAiStatus(runtime("consent_off"));
    expect(status?.roles).toHaveLength(2);
    expect(status?.roles[0]?.reason).toBe("consent_off");
  });

  it("thiếu hoặc sai dạng thì null", () => {
    expect(parseAiStatus({})).toBeNull();
    expect(parseAiStatus(null)).toBeNull();
    expect(parseAiStatus({ ai_status: { roles: [] } })).toBeNull();
    expect(parseAiStatus(runtime("khong_biet"))).toBeNull();
  });
});

describe("roleView", () => {
  const cases: [string | null, string, string][] = [
    [null, "ok", "Đã cấu hình"],
    ["no_provider", "warn", "Thiếu"],
    ["budget_exhausted", "warn", "Chạm trần"],
    ["mode_off", "off", "Chế độ tắt"],
  ];
  for (const [reason, tone, text] of cases) {
    it(`lý do ${reason ?? "không"} thành ${tone}`, () => {
      const first = parseAiStatus(runtime(reason))?.roles[0];
      expect(first).toBeDefined();
      if (first === undefined) return;
      const view = roleView(first);
      expect(view.tone).toBe(tone);
      expect(view.text).toContain(text);
    });
  }
});

describe("consentControl", () => {
  const base = { enabled: false, source: "default" as const, vlm_mode: "unset" as const, persistent: true };
  it("khoá và nêu lý do khi env đặt", () => {
    const control = consentControl({ ...base, source: "env" }, "token", true);
    expect(control.disabled).toBe(true);
    expect(control.reason).toContain("HOLOCRON_AI_CONSENT");
  });
  it("hướng dẫn đặt HOLOCRON_REQUIRE_TOKEN khi chưa đặt token", () => {
    const control = consentControl(base, "token_not_configured", false);
    expect(control.disabled).toBe(true);
    expect(control.reason).toContain("HOLOCRON_REQUIRE_TOKEN");
  });
  it("khoá khi chưa lưu được", () => {
    expect(consentControl({ ...base, persistent: false }, "token", true).reason).toContain("HOLOCRON_DATA_DIR");
  });
  it("mở khi đủ điều kiện", () => {
    expect(consentControl(base, "token", true)).toEqual({ disabled: false, reason: null });
  });
});

describe("parseConsentState, parseProviderList, parseTestResult", () => {
  it("kiểm dạng consent", () => {
    expect(parseConsentState({ enabled: true, source: "setting", vlm_mode: "off", persistent: true })).not.toBeNull();
    expect(parseConsentState({ enabled: "yes" })).toBeNull();
  });
  it("kiểm dạng danh sách nhà cung cấp", () => {
    const list = parseProviderList({
      providers: [provider()],
      settings_persistent: true,
      write_protection: "token",
    });
    expect(list?.providers[0]?.key_last4).toBe("7890");
    expect(
      parseProviderList({ providers: [{ id: 1 }], settings_persistent: true, write_protection: "token" }),
    ).toBeNull();
  });
  it("kiểm dạng kết quả kiểm thử và mã lạ", () => {
    const ok = parseTestResult({ ok: true, code: "ok", http_status: 200, latency_ms: 12, models: ["a"] });
    expect(ok).not.toBeNull();
    if (ok !== null) expect(testMessage(ok)).toContain("Kết nối được");
    expect(parseTestResult({ ok: false, code: "lạ", http_status: null, latency_ms: 0, models: [] })).toBeNull();
  });
});

describe("nhà cung cấp và khoá", () => {
  it("chỉ hiện 4 ký tự cuối", () => {
    const parsed = parseProvider(provider());
    expect(parsed).not.toBeNull();
    if (parsed !== null) expect(keyLabel(parsed)).toBe("Khoá ••••7890");
  });
  it("cảnh báo khi plaintext_file, không cảnh báo khi dpapi", () => {
    const plain = parseProvider(provider({ key_storage: "plaintext_file" }));
    const dpapi = parseProvider(provider());
    expect(plain && keyStorageWarning(plain)).toContain("không được mã hoá");
    expect(dpapi && keyStorageWarning(dpapi)).toBeNull();
  });
  it("khoá không đọc được thì báo nhập lại", () => {
    const parsed = parseProvider(provider({ key_unreadable: true }));
    expect(parsed && keyLabel(parsed)).toContain("nhập lại");
  });
});

describe("form thêm nhà cung cấp", () => {
  it("mẫu Gemini điền sẵn địa chỉ", () => {
    expect(emptyForm("gemini").baseUrl).toContain("generativelanguage.googleapis.com");
    expect(emptyForm("custom").baseUrl).toBe("");
  });
  it("báo lỗi từng ô", () => {
    const errors = validateForm({ ...emptyForm("custom"), label: "", baseUrl: "http://example.com", apiKey: "" });
    expect(errors.label).toBeDefined();
    expect(errors.baseUrl).toContain("https");
    expect(errors.apiKey).toBeDefined();
    expect(errors.models).toBeDefined();
  });
  it("máy này không cần khoá, http loopback được", () => {
    const form = { ...emptyForm("custom"), baseUrl: "http://127.0.0.1:11434/v1", modelLlm: "llama" };
    expect(validateForm(form)).toEqual({});
  });
  it("body tạo chứa khoá đã cắt khoảng trắng và model trống thành null", () => {
    const body = toCreateBody({
      ...emptyForm("gemini"),
      apiKey: `  ${SECRET} `,
      modelVlm: " m1 ",
    });
    expect(body.api_key).toBe(SECRET);
    expect(body.models).toEqual({ vlm: "m1", llm: null });
  });
});
