import { describe, expect, it } from "vitest";

import { RUNTIME_FIXTURES, type RuntimeScenario } from "@/mocks/fixtures/runtime";

import { classifyHttpError, classifyNetworkError } from "./errors";
import {
  capabilitiesOf,
  capabilityLabel,
  capabilityOf,
  freshRuntime,
  groupCapabilities,
  INITIAL_RUNTIME_STATE,
  isCapabilityBlocked,
  mayUseNetwork,
  needsTesseractHelp,
  normalizeCapability,
  normalizeRuntimeInfo,
  type RuntimeState,
  type RuntimeView,
  runtimeReducer,
  STALE_SUFFIX,
  selectDataEgress,
  selectLlmStatus,
  selectOfflineMode,
  selectStatusItems,
  UNRECOGNIZED_CAPABILITY_LABEL_VI,
} from "./runtime";

/** Thân JSON đi qua đúng bước kiểm dạng ở ranh giới như `lib/api.ts`. */
function view(raw: unknown): RuntimeView {
  const runtime = normalizeRuntimeInfo(raw);
  if (runtime === null) throw new Error("fixture không qua được kiểm dạng");
  return runtime;
}

const FIXTURES: Record<RuntimeScenario, RuntimeView> = {
  offline: view(RUNTIME_FIXTURES.offline),
  llm: view(RUNTIME_FIXTURES.llm),
  no_tesseract: view(RUNTIME_FIXTURES.no_tesseract),
};

/** Backend hiện tại (trước §5.12 đầy đủ): chỉ ba cờ boolean. */
const LEGACY_BODY = { demo_mode: true, llm_configured: false, vlm_configured: false };
const LEGACY: RuntimeView = view(LEGACY_BODY);

function loaded(runtime: RuntimeView): RuntimeState {
  return runtimeReducer(INITIAL_RUNTIME_STATE, { type: "load_succeeded", runtime });
}

describe("runtimeReducer", () => {
  it("tải thành công thì đã kết nối và giữ RuntimeInfo", () => {
    const state = loaded(FIXTURES.offline);
    expect(state.backend).toBe("connected");
    expect(state.runtime).toEqual(FIXTURES.offline);
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
    const failed = runtimeReducer(loaded(FIXTURES.llm), {
      type: "load_failed",
      error: classifyNetworkError(""),
    });
    const retrying = runtimeReducer(failed, { type: "load_started" });
    expect(retrying.backend).toBe("connecting");
    expect(retrying.error).toBeNull();
    expect(retrying.runtime).toEqual(FIXTURES.llm);
  });

  it("lỗi LLM tạm thời bật rồi tắt khi gọi LLM thành công", () => {
    const failed = runtimeReducer(loaded(FIXTURES.llm), { type: "llm_upstream_failed" });
    expect(selectLlmStatus(failed)).toBe("transient_error");
    expect(selectLlmStatus(runtimeReducer(failed, { type: "llm_call_succeeded" }))).toBe("ready");
  });
});

describe("selectLlmStatus và selectOfflineMode", () => {
  it("ba trạng thái theo dữ liệu backend", () => {
    expect(selectLlmStatus(INITIAL_RUNTIME_STATE)).toBe("unknown");
    expect(selectLlmStatus(loaded(FIXTURES.offline))).toBe("not_configured");
    expect(selectLlmStatus(loaded(FIXTURES.llm))).toBe("ready");
    expect(selectOfflineMode(loaded(FIXTURES.offline))).toBe(true);
    expect(selectOfflineMode(loaded(FIXTURES.llm))).toBe(false);
    expect(selectOfflineMode(INITIAL_RUNTIME_STATE)).toBe(false);
  });

  it("chưa cấu hình LLM thì 502 cũ không biến thành lỗi tạm thời", () => {
    const state = runtimeReducer(loaded(FIXTURES.offline), { type: "llm_upstream_failed" });
    expect(selectLlmStatus(state)).toBe("not_configured");
  });
});

describe("capabilityOf", () => {
  it("đọc khả năng và lý do từ backend", () => {
    expect(capabilityOf(FIXTURES.offline, "vault_search")).toEqual({ state: "available" });
    const view = capabilityOf(FIXTURES.offline, "concept_pair_llm_proposal");
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
    expect(isCapabilityBlocked(FIXTURES.offline, "concept_pair_llm_proposal")).toBe(true);
    expect(isCapabilityBlocked(FIXTURES.llm, "concept_pair_llm_proposal")).toBe(false);
  });

  it("hướng dẫn cài Tesseract chỉ khi thiếu Tesseract hoặc vie.traineddata", () => {
    expect(needsTesseractHelp(FIXTURES.no_tesseract)).toBe(true);
    expect(needsTesseractHelp(FIXTURES.offline)).toBe(false);
    const missingLanguage = view({
      ...LEGACY_BODY,
      capabilities: [{ name: "ocr", available: false, reason: "ocr_language_missing", detail_vi: "x" }],
    });
    expect(needsTesseractHelp(missingLanguage)).toBe(true);
  });
});

describe("groupCapabilities", () => {
  it("chia theo: chạy offline, dùng LLM, cần cài thêm, cần cấu hình, chưa có trong bản này", () => {
    const groups = groupCapabilities(FIXTURES.no_tesseract.capabilities ?? []);
    expect(groups.offline.map((c) => c.name)).toEqual(["vault_search", "theory_generation", "essay_grading_offline"]);
    expect(groups.online.map((c) => c.name)).toEqual(["concept_pair_llm_proposal", "template_authoring_llm"]);
    expect(groups.install.map((c) => c.name)).toEqual(["ocr", "ocr_cross_check"]);
    expect(groups.configure.map((c) => c.name)).toEqual(["vlm"]);
    expect(groups.not_implemented.map((c) => c.name)).toEqual(["essay_grading_llm"]);
  });

  it("chạy offline không có LLM: không khả năng nào ở nhóm dùng mạng", () => {
    const groups = groupCapabilities(FIXTURES.offline.capabilities ?? []);
    expect(groups.online).toEqual([]);
    expect(groups.offline.every((c) => !mayUseNetwork(c))).toBe(true);
  });

  it("mọi khả năng có nhãn tiếng Việt", () => {
    for (const capability of FIXTURES.offline.capabilities ?? []) {
      expect(capability.name).not.toBeNull();
      expect(capabilityLabel(capability)).not.toBe(UNRECOGNIZED_CAPABILITY_LABEL_VI);
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
    const items = selectStatusItems(loaded(FIXTURES.offline), false);
    expect(items.map((i) => [i.id, i.value])).toEqual([
      ["backend", "đã kết nối"],
      ["llm", "chưa cấu hình"],
      ["ocr", "sẵn sàng"],
      ["mode", "đang chạy offline"],
    ]);
  });

  it("lỗi LLM tạm thời và thiếu Tesseract", () => {
    const state = runtimeReducer(loaded(FIXTURES.no_tesseract), { type: "llm_upstream_failed" });
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

describe("normalizeRuntimeInfo (kiểm dạng ở ranh giới)", () => {
  it("fixture đúng contract đi qua nguyên vẹn", () => {
    const runtime = FIXTURES.no_tesseract;
    expect(runtime.capabilities?.map((c) => c.name)).toEqual(
      RUNTIME_FIXTURES.no_tesseract.capabilities?.map((c) => c.name),
    );
    expect(runtime.mock_extractor).toBe(true);
  });

  it("thiếu cờ bắt buộc hoặc không phải object thì không nhận", () => {
    expect(normalizeRuntimeInfo(null)).toBeNull();
    expect(normalizeRuntimeInfo([])).toBeNull();
    expect(normalizeRuntimeInfo("x")).toBeNull();
    expect(normalizeRuntimeInfo({ demo_mode: true, llm_configured: "false", vlm_configured: false })).toBeNull();
  });

  it("field tuỳ chọn sai dạng coi như backend chưa báo", () => {
    const runtime = view({ ...LEGACY_BODY, mock_extractor: "yes", capabilities: { ocr: true } });
    expect(runtime.mock_extractor).toBeNull();
    expect(runtime.capabilities).toBeNull();
  });

  it("phần tử null hoặc sai dạng bị bỏ qua, phần tử đúng giữ nguyên thứ tự", () => {
    const runtime = view({
      ...LEGACY_BODY,
      capabilities: [
        null,
        42,
        "ocr",
        { name: 7, available: true },
        { name: "vault_search" },
        { name: "vault_search", available: true, reason: null, detail_vi: null },
        undefined,
        { name: "ocr", available: false, reason: "ocr_engine_missing", detail_vi: "Cài Tesseract." },
      ],
    });
    expect(runtime.capabilities).toEqual([
      { name: "vault_search", available: true, reason: null, detail_vi: null },
      { name: "ocr", available: false, reason: "ocr_engine_missing", detail_vi: "Cài Tesseract." },
    ]);
    expect(() => groupCapabilities(runtime.capabilities ?? [])).not.toThrow();
    expect(() => selectStatusItems(loaded(runtime), false)).not.toThrow();
  });

  it("available quyết định reason: khả dụng thì reason null, không khả dụng mà thiếu mã thì không rõ", () => {
    expect(normalizeCapability({ name: "ocr", available: true, reason: "ocr_engine_missing" })?.reason).toBeNull();
    expect(normalizeCapability({ name: "ocr", available: false, reason: null })?.reason).toBe("unrecognized");
    expect(normalizeCapability({ name: "ocr", available: false, detail_vi: 3 })?.detail_vi).toBeNull();
  });
});

describe("khả năng lạ (backend mới hơn giao diện)", () => {
  it("reason lạ: vào nhóm không rõ, OCR báo không khả dụng, không ném lỗi", () => {
    const runtime = view({
      ...LEGACY_BODY,
      capabilities: [
        { name: "ocr", available: false, reason: "gpu_driver_missing", detail_vi: "Câu của backend." },
        { name: "vault_search", available: true, reason: null, detail_vi: null },
      ],
    });
    const groups = groupCapabilities(runtime.capabilities ?? []);
    expect(groups.unrecognized.map((c) => c.name)).toEqual(["ocr"]);
    expect(groups.install).toEqual([]);
    expect(groups.offline.map((c) => c.name)).toEqual(["vault_search"]);
    expect(capabilityOf(runtime, "ocr")).toEqual({
      state: "unavailable",
      reason: "unrecognized",
      detail: "Câu của backend.",
    });
    expect(isCapabilityBlocked(runtime, "ocr")).toBe(true);
    expect(needsTesseractHelp(runtime)).toBe(false);
    const byId = new Map(selectStatusItems(loaded(runtime), false).map((i) => [i.id, i]));
    expect(byId.get("ocr")).toMatchObject({ value: "không khả dụng", tone: "warning" });
  });

  it("name lạ available:true: không bao giờ vào nhóm không cần mạng, coi như có thể gửi dữ liệu", () => {
    const runtime = view({
      ...LEGACY_BODY,
      capabilities: [{ name: "cloud_sync", available: true, reason: null, detail_vi: null }],
    });
    const entry = runtime.capabilities?.[0];
    expect(entry?.name).toBeNull();
    const groups = groupCapabilities(runtime.capabilities ?? []);
    expect(groups.offline).toEqual([]);
    expect(groups.online).toEqual([]);
    expect(groups.unrecognized).toHaveLength(1);
    if (entry !== undefined) {
      expect(mayUseNetwork(entry)).toBe(true);
      expect(capabilityLabel(entry)).toBe(UNRECOGNIZED_CAPABILITY_LABEL_VI);
      expect(capabilityLabel(entry)).not.toContain("cloud_sync");
    }
    expect(selectDataEgress(runtime)).toBe("may_leave");
  });

  it("name lạ không khả dụng vẫn ở nhóm không rõ", () => {
    const runtime = view({
      ...LEGACY_BODY,
      capabilities: [{ name: "cloud_sync", available: false, reason: "llm_not_configured", detail_vi: null }],
    });
    expect(groupCapabilities(runtime.capabilities ?? []).unrecognized).toHaveLength(1);
    expect(groupCapabilities(runtime.capabilities ?? []).configure).toEqual([]);
  });
});

describe("selectDataEgress", () => {
  it("chỉ có thể rời máy khi bật LLM hoặc VLM hay có khả năng dùng mạng đang bật", () => {
    expect(selectDataEgress(null)).toBe("unknown");
    expect(selectDataEgress(FIXTURES.offline)).toBe("local_only");
    expect(selectDataEgress(LEGACY)).toBe("local_only");
    expect(selectDataEgress(FIXTURES.llm)).toBe("may_leave");
    expect(selectDataEgress(view({ ...LEGACY_BODY, vlm_configured: true }))).toBe("may_leave");
  });
});

describe("dữ liệu cũ khi tải lại thất bại", () => {
  const failed = runtimeReducer(loaded(FIXTURES.llm), { type: "load_failed", error: classifyNetworkError("") });

  it("freshRuntime chỉ trả dữ liệu khi lần tải gần nhất thành công", () => {
    expect(freshRuntime(loaded(FIXTURES.llm))).toEqual(FIXTURES.llm);
    expect(freshRuntime(failed)).toBeNull();
    expect(freshRuntime(runtimeReducer(failed, { type: "load_started" }))).toBeNull();
    expect(freshRuntime(INITIAL_RUNTIME_STATE)).toBeNull();
  });

  it("thanh trạng thái ghi chưa cập nhật, màu trung tính, ẩn ô chế độ", () => {
    const offlineFailed = runtimeReducer(loaded(FIXTURES.offline), {
      type: "load_failed",
      error: classifyNetworkError(""),
    });
    const items = selectStatusItems(offlineFailed, false);
    expect(items.map((i) => i.id)).toEqual(["backend", "llm", "ocr"]);
    expect(items[0]).toMatchObject({ value: "không kết nối được", tone: "danger" });
    for (const item of items.slice(1)) {
      expect(item.value.endsWith(STALE_SUFFIX)).toBe(true);
      expect(item.tone).toBe("neutral");
    }
  });
});

describe("băng chạy offline", () => {
  it("chỉ dựa vào llm_configured = false, không dựa vào demo_mode", () => {
    expect(selectOfflineMode(loaded(view({ demo_mode: true, llm_configured: true, vlm_configured: false })))).toBe(
      false,
    );
    expect(selectOfflineMode(loaded(view({ demo_mode: false, llm_configured: false, vlm_configured: false })))).toBe(
      true,
    );
  });
});
