import { describe, expect, it } from "vitest";

import { RUNTIME_FIXTURES } from "@/mocks/fixtures/runtime";
import type { RuntimeInfoPending } from "@/types/contract-pending";

import { classifyHttpError, classifyNetworkError } from "./errors";
import {
  CAPABILITY_LABEL_VI,
  CAPABILITY_USES_NETWORK,
  capabilitiesOf,
  capabilityOf,
  groupCapabilities,
  INITIAL_RUNTIME_STATE,
  isCapabilityBlocked,
  needsTesseractHelp,
  type RuntimeState,
  runtimeReducer,
  selectLlmStatus,
  selectOfflineMode,
  selectStatusItems,
} from "./runtime";

/** Backend hiện tại (trước §5.12 đầy đủ): chỉ ba cờ boolean. */
const LEGACY: RuntimeInfoPending = { demo_mode: true, llm_configured: false, vlm_configured: false };

function loaded(runtime: RuntimeInfoPending): RuntimeState {
  return runtimeReducer(INITIAL_RUNTIME_STATE, { type: "load_succeeded", runtime });
}

describe("runtimeReducer", () => {
  it("tải thành công thì đã kết nối và giữ RuntimeInfo", () => {
    const state = loaded(RUNTIME_FIXTURES.offline);
    expect(state.backend).toBe("connected");
    expect(state.runtime).toEqual(RUNTIME_FIXTURES.offline);
    expect(state.error).toBeNull();
  });

  it("lỗi mạng hoặc server là không kết nối được; lỗi khác là backend từ chối", () => {
    const unreachable = runtimeReducer(INITIAL_RUNTIME_STATE, {
      type: "load_failed",
      error: classifyNetworkError("/api/holocron"),
    });
    expect(unreachable.backend).toBe("unreachable");
    const server = runtimeReducer(INITIAL_RUNTIME_STATE, { type: "load_failed", error: classifyHttpError(500, null) });
    expect(server.backend).toBe("unreachable");
    const forbidden = runtimeReducer(INITIAL_RUNTIME_STATE, {
      type: "load_failed",
      error: classifyHttpError(403, { detail: "x" }),
    });
    expect(forbidden.backend).toBe("error");
  });

  it("tải lại giữ RuntimeInfo cũ trong lúc chờ và xoá lỗi", () => {
    const failed = runtimeReducer(loaded(RUNTIME_FIXTURES.llm), {
      type: "load_failed",
      error: classifyNetworkError(""),
    });
    const retrying = runtimeReducer(failed, { type: "load_started" });
    expect(retrying.backend).toBe("connecting");
    expect(retrying.error).toBeNull();
    expect(retrying.runtime).toEqual(RUNTIME_FIXTURES.llm);
  });

  it("lỗi LLM tạm thời bật rồi tắt khi gọi LLM thành công", () => {
    const failed = runtimeReducer(loaded(RUNTIME_FIXTURES.llm), { type: "llm_upstream_failed" });
    expect(selectLlmStatus(failed)).toBe("transient_error");
    expect(selectLlmStatus(runtimeReducer(failed, { type: "llm_call_succeeded" }))).toBe("ready");
  });
});

describe("selectLlmStatus và selectOfflineMode", () => {
  it("ba trạng thái theo dữ liệu backend", () => {
    expect(selectLlmStatus(INITIAL_RUNTIME_STATE)).toBe("unknown");
    expect(selectLlmStatus(loaded(RUNTIME_FIXTURES.offline))).toBe("not_configured");
    expect(selectLlmStatus(loaded(RUNTIME_FIXTURES.llm))).toBe("ready");
    expect(selectOfflineMode(loaded(RUNTIME_FIXTURES.offline))).toBe(true);
    expect(selectOfflineMode(loaded(RUNTIME_FIXTURES.llm))).toBe(false);
    expect(selectOfflineMode(INITIAL_RUNTIME_STATE)).toBe(false);
  });

  it("chưa cấu hình LLM thì 502 cũ không biến thành lỗi tạm thời", () => {
    const state = runtimeReducer(loaded(RUNTIME_FIXTURES.offline), { type: "llm_upstream_failed" });
    expect(selectLlmStatus(state)).toBe("not_configured");
  });
});

describe("capabilityOf", () => {
  it("đọc khả năng và lý do từ backend", () => {
    expect(capabilityOf(RUNTIME_FIXTURES.offline, "vault_search")).toEqual({ state: "available" });
    const view = capabilityOf(RUNTIME_FIXTURES.offline, "concept_pair_llm_proposal");
    expect(view.state).toBe("unavailable");
    if (view.state === "unavailable") {
      expect(view.reason).toBe("llm_not_configured");
      expect(view.detail).toContain("HOLOCRON_LLM_API_KEY");
    }
  });

  it("backend cũ không có capabilities: không rõ và không chặn tính năng nào", () => {
    expect(capabilitiesOf(LEGACY)).toBeNull();
    expect(capabilitiesOf(null)).toBeNull();
    expect(capabilityOf(LEGACY, "ocr")).toEqual({ state: "unknown" });
    expect(isCapabilityBlocked(LEGACY, "concept_pair_llm_proposal")).toBe(false);
    expect(isCapabilityBlocked(null, "concept_pair_llm_proposal")).toBe(false);
    expect(needsTesseractHelp(LEGACY)).toBe(false);
  });

  it("chỉ chặn khi backend báo rõ là không khả dụng", () => {
    expect(isCapabilityBlocked(RUNTIME_FIXTURES.offline, "concept_pair_llm_proposal")).toBe(true);
    expect(isCapabilityBlocked(RUNTIME_FIXTURES.llm, "concept_pair_llm_proposal")).toBe(false);
  });

  it("hướng dẫn cài Tesseract chỉ khi thiếu Tesseract hoặc vie.traineddata", () => {
    expect(needsTesseractHelp(RUNTIME_FIXTURES.no_tesseract)).toBe(true);
    expect(needsTesseractHelp(RUNTIME_FIXTURES.offline)).toBe(false);
    const missingLanguage: RuntimeInfoPending = {
      ...LEGACY,
      capabilities: [{ name: "ocr", available: false, reason: "ocr_language_missing", detail_vi: "x" }],
    };
    expect(needsTesseractHelp(missingLanguage)).toBe(true);
  });
});

describe("groupCapabilities", () => {
  it("chia theo: chạy offline, dùng LLM, cần cài thêm, cần cấu hình, chưa có trong bản này", () => {
    const groups = groupCapabilities(RUNTIME_FIXTURES.no_tesseract.capabilities ?? []);
    expect(groups.offline.map((c) => c.name)).toEqual(["vault_search", "theory_generation", "essay_grading_offline"]);
    expect(groups.online.map((c) => c.name)).toEqual(["concept_pair_llm_proposal", "template_authoring_llm"]);
    expect(groups.install.map((c) => c.name)).toEqual(["ocr", "ocr_cross_check"]);
    expect(groups.configure.map((c) => c.name)).toEqual(["vlm"]);
    expect(groups.not_implemented.map((c) => c.name)).toEqual(["essay_grading_llm"]);
  });

  it("chạy offline không có LLM: không khả năng nào ở nhóm dùng mạng", () => {
    const groups = groupCapabilities(RUNTIME_FIXTURES.offline.capabilities ?? []);
    expect(groups.online).toEqual([]);
    expect(groups.offline.every((c) => !CAPABILITY_USES_NETWORK[c.name])).toBe(true);
  });

  it("mọi khả năng có nhãn tiếng Việt", () => {
    for (const capability of RUNTIME_FIXTURES.offline.capabilities ?? []) {
      expect(CAPABILITY_LABEL_VI[capability.name].length).toBeGreaterThan(0);
    }
  });
});

describe("selectStatusItems", () => {
  it("đang kết nối: chỉ có ô backend (và nhãn mock khi bật)", () => {
    expect(selectStatusItems(INITIAL_RUNTIME_STATE, false).map((i) => i.id)).toEqual(["backend"]);
    const withMock = selectStatusItems(INITIAL_RUNTIME_STATE, true);
    expect(withMock.map((i) => i.id)).toEqual(["backend", "mock"]);
    expect(withMock[1]?.value).toBe("Dữ liệu mẫu (mock)");
  });

  it("chạy offline: LLM chưa cấu hình và ô chế độ offline", () => {
    const items = selectStatusItems(loaded(RUNTIME_FIXTURES.offline), false);
    expect(items.map((i) => [i.id, i.value])).toEqual([
      ["backend", "đã kết nối"],
      ["llm", "chưa cấu hình"],
      ["ocr", "sẵn sàng"],
      ["mode", "đang chạy offline"],
    ]);
  });

  it("lỗi LLM tạm thời và thiếu Tesseract", () => {
    const state = runtimeReducer(loaded(RUNTIME_FIXTURES.no_tesseract), { type: "llm_upstream_failed" });
    const byId = new Map(selectStatusItems(state, false).map((i) => [i.id, i]));
    expect(byId.get("llm")).toMatchObject({ value: "lỗi tạm thời, thử lại", tone: "warning" });
    expect(byId.get("ocr")).toMatchObject({ value: "thiếu Tesseract", tone: "warning" });
    expect(byId.has("mode")).toBe(false);
  });

  it("backend cũ: OCR là backend chưa báo", () => {
    const byId = new Map(selectStatusItems(loaded(LEGACY), false).map((i) => [i.id, i]));
    expect(byId.get("ocr")?.value).toBe("backend chưa báo");
  });

  it("không kết nối được", () => {
    const state = runtimeReducer(INITIAL_RUNTIME_STATE, { type: "load_failed", error: classifyNetworkError("") });
    expect(selectStatusItems(state, false)).toEqual([
      { id: "backend", label: "Backend", value: "không kết nối được", tone: "danger" },
    ]);
  });
});
