/**
 * Cài đặt › "Kết nối AI" (SPEC §5.16, §6.11): kiểm dạng thân backend ở ranh giới và logic thuần cho màn hình.
 *
 * Không tính số nào: chi phí, trần và 4 ký tự cuối khoá do backend trả, giao diện chỉ hiển thị. Khoá API chỉ đi một
 * chiều (form → backend), không bao giờ có trong kiểu nhận về.
 */

import type {
  AiAutoBlockReason,
  AiConsentSource,
  AiConsentState,
  AiRoleStatus,
  AiStatus,
  AiVlmMode,
  KeyStorage,
  ProviderConfig,
  ProviderCreate,
  ProviderList,
  ProviderTemplate,
  ProviderTestCode,
  ProviderTestResult,
  WriteProtection,
} from "@/types/holocron";

// ------------------------------------------------------------------------------------------- kiểm dạng

const CONSENT_SOURCES: readonly AiConsentSource[] = ["env", "setting", "default"];
const VLM_MODES: readonly AiVlmMode[] = ["unset", "off", "images_only"];
const WRITE_PROTECTIONS: readonly WriteProtection[] = ["token", "token_not_configured"];
const BLOCK_REASONS: readonly AiAutoBlockReason[] = [
  "no_provider",
  "budget_exhausted",
  "mode_off",
  "mode_images_only",
  "consent_off",
];
const TEMPLATES: readonly ProviderTemplate[] = ["gemini", "organizer_gateway", "custom"];
const KEY_STORAGES: readonly KeyStorage[] = ["dpapi", "plaintext_file", "env", "none"];
const TEST_CODES: readonly ProviderTestCode[] = [
  "ok",
  "auth_failed",
  "not_found",
  "rate_limited",
  "upstream_error",
  "timeout",
  "blocked_address",
  "redirect_blocked",
  "invalid_response",
  "image_rejected",
  "key_unreadable",
  "no_model_for_role",
  "budget_exhausted",
];

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function pick<T extends string>(options: readonly T[], value: unknown): T | null {
  return options.find((option) => option === value) ?? null;
}

function str(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function num(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function strOrNull(value: unknown): string | null | undefined {
  if (value === null) return null;
  return typeof value === "string" ? value : undefined;
}

function parseRoleStatus(value: unknown): AiRoleStatus | null {
  if (!isRecord(value)) return null;
  const role = value.role === "vlm" || value.role === "llm" ? value.role : null;
  const reason = value.reason === null ? null : pick(BLOCK_REASONS, value.reason);
  const providerId = strOrNull(value.provider_id);
  const label = strOrNull(value.recipient_label);
  if (
    role === null ||
    (reason === null && value.reason !== null) ||
    typeof value.auto_send_allowed !== "boolean" ||
    providerId === undefined ||
    label === undefined ||
    typeof value.local !== "boolean"
  ) {
    return null;
  }
  return {
    role,
    auto_send_allowed: value.auto_send_allowed,
    reason,
    provider_id: providerId,
    recipient_label: label,
    local: value.local,
  };
}

/** `RuntimeInfo.ai_status` (§5.12); sai dạng ⇒ `null` (màn hình báo không đọc được, không sập). */
export function parseAiStatus(raw: unknown): AiStatus | null {
  if (!isRecord(raw)) return null;
  const status = isRecord(raw.ai_status) ? raw.ai_status : null;
  if (status === null || !Array.isArray(status.roles)) return null;
  const source = pick(CONSENT_SOURCES, status.consent_source);
  const mode = pick(VLM_MODES, status.vlm_mode);
  const protection = pick(WRITE_PROTECTIONS, status.write_protection);
  if (
    source === null ||
    mode === null ||
    protection === null ||
    typeof status.consent_enabled !== "boolean" ||
    typeof status.settings_persistent !== "boolean"
  ) {
    return null;
  }
  const roles: AiRoleStatus[] = [];
  for (const item of status.roles) {
    const role = parseRoleStatus(item);
    if (role === null) return null;
    roles.push(role);
  }
  return {
    consent_enabled: status.consent_enabled,
    consent_source: source,
    vlm_mode: mode,
    settings_persistent: status.settings_persistent,
    write_protection: protection,
    roles,
  };
}

/** Response `GET/PUT /v1/settings/ai-consent`. */
export function parseConsentState(raw: unknown): AiConsentState | null {
  if (!isRecord(raw)) return null;
  const source = pick(CONSENT_SOURCES, raw.source);
  const mode = pick(VLM_MODES, raw.vlm_mode);
  if (source === null || mode === null || typeof raw.enabled !== "boolean" || typeof raw.persistent !== "boolean") {
    return null;
  }
  return { enabled: raw.enabled, source, vlm_mode: mode, persistent: raw.persistent };
}

function parseModels(value: unknown): ProviderConfig["models"] | null {
  if (!isRecord(value)) return null;
  const vlm = strOrNull(value.vlm);
  const llm = strOrNull(value.llm);
  return vlm === undefined || llm === undefined ? null : { vlm, llm };
}

function parsePrices(value: unknown): ProviderConfig["prices"] | null {
  if (!isRecord(value)) return null;
  const input = num(value.input_per_1k_usd);
  const output = num(value.output_per_1k_usd);
  const image = num(value.per_image_usd);
  const source =
    value.source === "user" || value.source === "env" || value.source === "template_default" ? value.source : null;
  if (input === null || output === null || image === null || source === null) return null;
  return { input_per_1k_usd: input, output_per_1k_usd: output, per_image_usd: image, source };
}

/** Một `ProviderConfig`; sai dạng ⇒ `null`. */
export function parseProvider(raw: unknown): ProviderConfig | null {
  if (!isRecord(raw)) return null;
  const template = pick(TEMPLATES, raw.template);
  const storage = pick(KEY_STORAGES, raw.key_storage);
  const models = parseModels(raw.models);
  const prices = parsePrices(raw.prices);
  const baseUrl = strOrNull(raw.base_url);
  const last4 = strOrNull(raw.key_last4);
  const id = str(raw.id);
  const label = str(raw.label);
  const budget = num(raw.budget_usd);
  const spent = num(raw.spent_usd);
  const priority = num(raw.priority);
  if (
    template === null ||
    storage === null ||
    models === null ||
    prices === null ||
    baseUrl === undefined ||
    last4 === undefined ||
    id === null ||
    label === null ||
    budget === null ||
    spent === null ||
    priority === null ||
    (raw.source !== "user" && raw.source !== "env") ||
    typeof raw.local !== "boolean" ||
    typeof raw.key_set !== "boolean" ||
    typeof raw.key_unreadable !== "boolean" ||
    typeof raw.enabled !== "boolean" ||
    typeof raw.budget_exhausted !== "boolean" ||
    !Array.isArray(raw.budget_shared_with)
  ) {
    return null;
  }
  return {
    id,
    kind: "openai_compatible",
    source: raw.source,
    template,
    label,
    base_url: baseUrl,
    local: raw.local,
    models,
    key_set: raw.key_set,
    key_last4: last4,
    key_storage: storage,
    key_unreadable: raw.key_unreadable,
    enabled: raw.enabled,
    priority,
    budget_usd: budget,
    spent_usd: spent,
    budget_exhausted: raw.budget_exhausted,
    budget_shared_with: raw.budget_shared_with.filter((item): item is string => typeof item === "string"),
    prices,
  };
}

/** Response `GET /v1/settings/providers`. */
export function parseProviderList(raw: unknown): ProviderList | null {
  if (!isRecord(raw) || !Array.isArray(raw.providers)) return null;
  const protection = pick(WRITE_PROTECTIONS, raw.write_protection);
  if (protection === null || typeof raw.settings_persistent !== "boolean") return null;
  const providers: ProviderConfig[] = [];
  for (const item of raw.providers) {
    const provider = parseProvider(item);
    if (provider === null) return null;
    providers.push(provider);
  }
  return { providers, settings_persistent: raw.settings_persistent, write_protection: protection };
}

/** Response `POST …/test`. */
export function parseTestResult(raw: unknown): ProviderTestResult | null {
  if (!isRecord(raw) || !Array.isArray(raw.models)) return null;
  const code = pick(TEST_CODES, raw.code);
  const status = raw.http_status === null ? null : num(raw.http_status);
  const latency = num(raw.latency_ms);
  if (
    code === null ||
    typeof raw.ok !== "boolean" ||
    latency === null ||
    (status === null && raw.http_status !== null)
  ) {
    return null;
  }
  return {
    ok: raw.ok,
    code,
    http_status: status,
    latency_ms: latency,
    models: raw.models.filter((item): item is string => typeof item === "string"),
  };
}

// --------------------------------------------------------------------------------------- hiển thị vai

export type RoleTone = "ok" | "warn" | "off";

export interface RoleView {
  /** "Đọc ảnh" hoặc "Ngôn ngữ". */
  name: string;
  tone: RoleTone;
  /** Một câu trạng thái, không lộ mã nội bộ. */
  text: string;
}

const ROLE_NAME: Record<AiRoleStatus["role"], string> = { vlm: "Đọc ảnh (VLM)", llm: "Ngôn ngữ (LLM)" };

const REASON_VIEW: Record<AiAutoBlockReason, { tone: RoleTone; text: string }> = {
  no_provider: { tone: "warn", text: "Thiếu: chưa có nhà cung cấp cho vai này." },
  budget_exhausted: { tone: "warn", text: "Chạm trần chi phí: nhà cung cấp tạm dừng cho tới khi tăng trần." },
  mode_off: { tone: "off", text: "Chế độ tắt: không gửi ảnh tài liệu đi đâu." },
  mode_images_only: { tone: "off", text: "Chế độ chỉ ảnh: đoạn chữ không được gửi tự động." },
  consent_off: { tone: "off", text: "Chưa cho phép gửi nội dung tài liệu." },
};

/** Trạng thái một vai từ `AiRoleStatus` (backend đã quyết; giao diện chỉ diễn đạt). */
export function roleView(status: AiRoleStatus): RoleView {
  const name = ROLE_NAME[status.role];
  if (status.reason !== null) {
    const view = REASON_VIEW[status.reason];
    const via = status.recipient_label === null ? "" : ` (${status.recipient_label})`;
    return { name, tone: view.tone, text: `${view.text}${via}` };
  }
  const label = status.recipient_label ?? "nhà cung cấp";
  const where = status.local ? `${label}, chạy trên máy này` : label;
  return { name, tone: "ok", text: `Đã cấu hình: gửi tới ${where}.` };
}

// ------------------------------------------------------------------------------------ công tắc đồng ý

export interface ConsentControl {
  /** Công tắc không đổi được (khoá). */
  disabled: boolean;
  /** Lý do khoá hoặc hướng dẫn; `null` khi đổi được. */
  reason: string | null;
}

/** Công tắc khoá khi env đặt; hiện lý do khi chưa lưu được hay chưa đặt mã truy cập. */
export function consentControl(
  consent: AiConsentState | null,
  protection: WriteProtection,
  tokenEntered: boolean,
): ConsentControl {
  if (consent === null) return { disabled: true, reason: "Chưa đọc được trạng thái đồng ý." };
  if (consent.source === "env") {
    return {
      disabled: true,
      reason: "Đang được đặt bằng biến môi trường HOLOCRON_AI_CONSENT nên không đổi ở đây.",
    };
  }
  if (!consent.persistent) {
    return {
      disabled: true,
      reason: "Chưa lưu được cài đặt vì backend chưa có thư mục dữ liệu (HOLOCRON_DATA_DIR).",
    };
  }
  if (protection === "token_not_configured") {
    return {
      disabled: true,
      reason: "Cần đặt biến môi trường HOLOCRON_REQUIRE_TOKEN cho backend rồi khởi động lại để đổi cài đặt AI.",
    };
  }
  if (!tokenEntered) return { disabled: false, reason: "Nhập “Mã truy cập” ở dưới để đổi công tắc này." };
  return { disabled: false, reason: null };
}

// -------------------------------------------------------------------------------------------- form

export interface ProviderTemplateInfo {
  template: ProviderTemplate;
  name: string;
  defaultLabel: string;
  /** Gợi ý điền sẵn; rỗng khi mẫu không có địa chỉ cố định. */
  defaultBaseUrl: string;
  hint: string;
}

export const PROVIDER_TEMPLATES: readonly ProviderTemplateInfo[] = [
  {
    template: "gemini",
    name: "Gemini (Google AI Studio)",
    defaultLabel: "Gemini",
    defaultBaseUrl: "https://generativelanguage.googleapis.com/v1beta/openai/",
    hint: "Dán khoá API tạo ở Google AI Studio.",
  },
  {
    template: "organizer_gateway",
    name: "Gateway ban tổ chức",
    defaultLabel: "Gateway ban tổ chức",
    defaultBaseUrl: "",
    hint: "Điền địa chỉ và khoá do ban tổ chức cấp.",
  },
  {
    template: "custom",
    name: "Tuỳ chỉnh (tương thích OpenAI)",
    defaultLabel: "Nhà cung cấp tuỳ chỉnh",
    defaultBaseUrl: "",
    hint: "Địa chỉ https của dịch vụ tương thích OpenAI; http chỉ dùng cho máy này.",
  },
];

export function templateInfo(template: ProviderTemplate): ProviderTemplateInfo {
  return PROVIDER_TEMPLATES.find((info) => info.template === template) ?? PROVIDER_TEMPLATES[2] ?? FALLBACK_TEMPLATE;
}

const FALLBACK_TEMPLATE: ProviderTemplateInfo = {
  template: "custom",
  name: "Tuỳ chỉnh",
  defaultLabel: "Nhà cung cấp tuỳ chỉnh",
  defaultBaseUrl: "",
  hint: "",
};

export interface ProviderForm {
  template: ProviderTemplate;
  label: string;
  baseUrl: string;
  apiKey: string;
  modelVlm: string;
  modelLlm: string;
}

export function emptyForm(template: ProviderTemplate): ProviderForm {
  const info = templateInfo(template);
  return {
    template,
    label: info.defaultLabel,
    baseUrl: info.defaultBaseUrl,
    apiKey: "",
    modelVlm: "",
    modelLlm: "",
  };
}

/** Tên form thêm, ví dụ "Thêm Gemini (Google AI Studio)". */
export function formTitle(form: ProviderForm): string {
  const info = templateInfo(form.template);
  return `Thêm ${info.name}`;
}

export type FormField = "label" | "baseUrl" | "apiKey" | "models";

/** Địa chỉ máy này (loopback) không cần khoá; backend vẫn là nơi quyết định cuối. */
function isLoopbackUrl(value: string): boolean {
  try {
    const host = new URL(value).hostname.toLowerCase();
    return host === "localhost" || host === "127.0.0.1" || host === "[::1]" || host === "::1";
  } catch {
    return false;
  }
}

/** Kiểm nhanh phía giao diện; không thay cho kiểm của backend (SSRF, định dạng khoá). */
export function validateForm(form: ProviderForm): Partial<Record<FormField, string>> {
  const errors: Partial<Record<FormField, string>> = {};
  if (form.label.trim() === "") errors.label = "Điền tên để nhận ra nhà cung cấp này.";
  const url = form.baseUrl.trim();
  if (url === "") {
    errors.baseUrl = "Điền địa chỉ dịch vụ.";
  } else if (!/^https?:\/\/[^\s/]+/i.test(url)) {
    errors.baseUrl = "Địa chỉ phải bắt đầu bằng https:// (hoặc http:// cho máy này).";
  } else if (/^http:\/\//i.test(url) && !isLoopbackUrl(url)) {
    errors.baseUrl = "Địa chỉ http chỉ dùng được cho máy này; dịch vụ bên ngoài phải dùng https.";
  }
  if (form.apiKey.trim() === "" && !isLoopbackUrl(url)) errors.apiKey = "Dán khoá API của nhà cung cấp.";
  if (form.modelVlm.trim() === "" && form.modelLlm.trim() === "") {
    errors.models = "Điền model cho ít nhất một vai (đọc ảnh hoặc ngôn ngữ).";
  }
  return errors;
}

/** Body tạo nhà cung cấp; model trống thành `null`. Khoá bỏ khoảng trắng hai đầu. */
export function toCreateBody(form: ProviderForm): ProviderCreate {
  const vlm = form.modelVlm.trim();
  const llm = form.modelLlm.trim();
  const key = form.apiKey.trim();
  return {
    template: form.template,
    label: form.label.trim(),
    base_url: form.baseUrl.trim(),
    api_key: key === "" ? null : key,
    models: { vlm: vlm === "" ? null : vlm, llm: llm === "" ? null : llm },
  };
}

// ------------------------------------------------------------------------------------ nhà cung cấp

/** Cảnh báo khi khoá nằm trong file thường (Unix, không có DPAPI). */
export const PLAINTEXT_KEY_WARNING =
  "Khoá này đang lưu ở dạng chữ thường trong thư mục dữ liệu của máy (không được mã hoá). Chỉ dùng trên máy riêng của bạn.";

export function keyStorageWarning(provider: ProviderConfig): string | null {
  return provider.key_storage === "plaintext_file" ? PLAINTEXT_KEY_WARNING : null;
}

/** Dòng mô tả khoá: "Khoá •••• ab12", chưa đặt, hoặc không đọc được. */
export function keyLabel(provider: ProviderConfig): string {
  if (provider.key_unreadable) return "Không đọc được khoá đã lưu: nhập lại khoá.";
  if (!provider.key_set) return provider.local ? "Không cần khoá (máy này)." : "Chưa có khoá.";
  if (provider.key_storage === "env") return "Khoá lấy từ biến môi trường.";
  return provider.key_last4 === null ? "Đã lưu khoá." : `Khoá ••••${provider.key_last4}`;
}

/** Kết quả kiểm thử thành một câu; backend chỉ trả mã đã làm sạch. */
export function testMessage(result: ProviderTestResult): string {
  switch (result.code) {
    case "ok":
      return result.models.length > 0 ? `Kết nối được. Tìm thấy ${result.models.length} model.` : "Kết nối được.";
    case "auth_failed":
      return "Nhà cung cấp từ chối khoá. Kiểm tra khoá rồi nhập lại.";
    case "not_found":
      return "Không tìm thấy dịch vụ ở địa chỉ này hoặc model không tồn tại.";
    case "rate_limited":
      return "Nhà cung cấp đang giới hạn số lần gọi. Thử lại sau ít phút.";
    case "upstream_error":
      return "Nhà cung cấp đang lỗi. Thử lại sau.";
    case "timeout":
      return "Nhà cung cấp không trả lời kịp. Thử lại sau.";
    case "blocked_address":
      return "Địa chỉ này bị chặn vì trỏ vào mạng nội bộ hoặc không an toàn.";
    case "redirect_blocked":
      return "Nhà cung cấp chuyển hướng sang địa chỉ khác nên Holocron không đi theo.";
    case "invalid_response":
      return "Nhà cung cấp trả về dữ liệu không đúng dạng tương thích OpenAI.";
    case "image_rejected":
      return "Model này không nhận ảnh. Chọn model có thị giác cho vai đọc ảnh.";
    case "key_unreadable":
      return "Không đọc được khoá đã lưu. Nhập lại khoá.";
    case "no_model_for_role":
      return "Chưa điền model cho vai này.";
    case "budget_exhausted":
      return "Đã chạm trần chi phí của nhà cung cấp này.";
  }
}
