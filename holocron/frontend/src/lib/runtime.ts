/**
 * Trạng thái chạy và khả năng của app (spec §5.12, §11.4; kế hoạch UI mục 5.6), logic thuần để dùng lại và test.
 *
 * Mọi trạng thái suy ra từ dữ liệu backend trả (`RuntimeInfo`, mã lỗi đã phân loại); frontend không tự kiểm cấu hình.
 * Backend cũ chưa trả `capabilities` thì mọi khả năng là "không rõ" và không chặn tính năng nào.
 */

import type { ErrorKind, UiError } from "@/lib/errors";
import type { Capability, CapabilityName, CapabilityReason, RuntimeInfoPending } from "@/types/contract-pending";

export type BackendStatus = "connecting" | "connected" | "unreachable" | "error";

export interface RuntimeState {
  backend: BackendStatus;
  runtime: RuntimeInfoPending | null;
  error: UiError | null;
  /** Lần gọi LLM gần nhất trả 502 `LLM_UPSTREAM_ERROR`; xoá khi một lần gọi LLM sau đó thành công. */
  llmTransientError: boolean;
}

export type RuntimeAction =
  | { type: "load_started" }
  | { type: "load_succeeded"; runtime: RuntimeInfoPending }
  | { type: "load_failed"; error: UiError }
  | { type: "llm_upstream_failed" }
  | { type: "llm_call_succeeded" };

export const INITIAL_RUNTIME_STATE: RuntimeState = {
  backend: "connecting",
  runtime: null,
  error: null,
  llmTransientError: false,
};

const UNREACHABLE_KINDS: readonly ErrorKind[] = ["network", "server"];

export function runtimeReducer(state: RuntimeState, action: RuntimeAction): RuntimeState {
  switch (action.type) {
    case "load_started":
      return { ...state, backend: "connecting", error: null };
    case "load_succeeded":
      return { ...state, backend: "connected", runtime: action.runtime, error: null };
    case "load_failed":
      return {
        ...state,
        backend: UNREACHABLE_KINDS.includes(action.error.kind) ? "unreachable" : "error",
        error: action.error,
      };
    case "llm_upstream_failed":
      return { ...state, llmTransientError: true };
    case "llm_call_succeeded":
      return { ...state, llmTransientError: false };
  }
}

// ------------------------------------------------------------------------------------- khả năng

export const CAPABILITY_LABEL_VI: Record<CapabilityName, string> = {
  ocr: "Đọc trang scan bằng Tesseract",
  ocr_cross_check: "Đối chiếu số bằng lượt đọc thứ hai",
  vault_search: "Tìm trong kho tri thức",
  theory_generation: "Sinh câu hỏi lý thuyết từ cặp khái niệm",
  concept_pair_llm_proposal: "LLM đề xuất cặp khái niệm",
  essay_grading_offline: "Chấm tự luận offline",
  essay_grading_llm: "Chấm tự luận bằng LLM",
  template_authoring_llm: "LLM viết template cho dạng bài mới",
  vlm: "Đọc ảnh bằng mô hình thị giác (VLM)",
};

export type CapabilityView =
  | { state: "unknown" }
  | { state: "available" }
  | { state: "unavailable"; reason: CapabilityReason | null; detail: string | null };

/** Danh sách khả năng backend trả; `null` khi backend chưa báo (bản cũ) hoặc chưa tải được. */
export function capabilitiesOf(runtime: RuntimeInfoPending | null): Capability[] | null {
  if (runtime === null || !Array.isArray(runtime.capabilities)) return null;
  return runtime.capabilities;
}

export function capabilityOf(runtime: RuntimeInfoPending | null, name: CapabilityName): CapabilityView {
  const capability = capabilitiesOf(runtime)?.find((item) => item.name === name);
  if (capability === undefined) return { state: "unknown" };
  if (capability.available) return { state: "available" };
  return { state: "unavailable", reason: capability.reason, detail: capability.detail_vi };
}

/** Nút gọi endpoint cần khả năng `name` chỉ bị vô hiệu khi backend báo rõ là không khả dụng. */
export function isCapabilityBlocked(runtime: RuntimeInfoPending | null, name: CapabilityName): boolean {
  return capabilityOf(runtime, name).state === "unavailable";
}

/**
 * Khả năng gửi dữ liệu ra khỏi máy khi chạy (gọi LLM hoặc VLM đã cấu hình; §11.4, §13.1). Bảng theo mã enum của
 * contract, không theo môn hay topic.
 */
export const CAPABILITY_USES_NETWORK: Record<CapabilityName, boolean> = {
  ocr: false,
  ocr_cross_check: false,
  vault_search: false,
  theory_generation: false,
  concept_pair_llm_proposal: true,
  essay_grading_offline: false,
  essay_grading_llm: true,
  template_authoring_llm: true,
  vlm: true,
};

/** `offline`: khả dụng, chạy hoàn toàn trên máy; `online`: khả dụng nhưng gửi dữ liệu tới LLM/VLM đã cấu hình. */
export type CapabilityGroup = "offline" | "online" | "install" | "configure" | "not_implemented";

const REASON_GROUP: Record<CapabilityReason, Exclude<CapabilityGroup, "offline" | "online">> = {
  ocr_engine_missing: "install",
  ocr_language_missing: "install",
  secondary_ocr_disabled: "install",
  llm_not_configured: "configure",
  vlm_not_configured: "configure",
  not_implemented: "not_implemented",
};

/** Nhóm khả năng cho màn chạy lần đầu; giữ thứ tự backend trả trong từng nhóm. */
export function groupCapabilities(capabilities: Capability[]): Record<CapabilityGroup, Capability[]> {
  const groups: Record<CapabilityGroup, Capability[]> = {
    offline: [],
    online: [],
    install: [],
    configure: [],
    not_implemented: [],
  };
  for (const capability of capabilities) {
    if (capability.available || capability.reason === null) {
      groups[CAPABILITY_USES_NETWORK[capability.name] ? "online" : "offline"].push(capability);
    } else {
      groups[REASON_GROUP[capability.reason]].push(capability);
    }
  }
  return groups;
}

/** Cần hiện hướng dẫn cài Tesseract và `vie.traineddata`. */
export function needsTesseractHelp(runtime: RuntimeInfoPending | null): boolean {
  const view = capabilityOf(runtime, "ocr");
  return (
    view.state === "unavailable" && (view.reason === "ocr_engine_missing" || view.reason === "ocr_language_missing")
  );
}

// ------------------------------------------------------------------------------- thanh trạng thái

export type LlmStatus = "unknown" | "ready" | "not_configured" | "transient_error";

export function selectLlmStatus(state: RuntimeState): LlmStatus {
  if (state.runtime === null) return "unknown";
  if (!state.runtime.llm_configured) return "not_configured";
  return state.llmTransientError ? "transient_error" : "ready";
}

/** `demo_mode = true`: băng "đang chạy offline" trên mọi trang (§5.12). */
export function selectOfflineMode(state: RuntimeState): boolean {
  return state.runtime?.demo_mode === true;
}

export type StatusTone = "neutral" | "success" | "info" | "warning" | "danger";

export interface StatusItem {
  id: "backend" | "llm" | "ocr" | "mode" | "mock";
  label: string;
  value: string;
  tone: StatusTone;
}

const BACKEND_ITEM: Record<BackendStatus, Pick<StatusItem, "value" | "tone">> = {
  connecting: { value: "đang kết nối…", tone: "neutral" },
  connected: { value: "đã kết nối", tone: "success" },
  unreachable: { value: "không kết nối được", tone: "danger" },
  error: { value: "từ chối yêu cầu", tone: "danger" },
};

const LLM_ITEM: Record<Exclude<LlmStatus, "unknown">, Pick<StatusItem, "value" | "tone">> = {
  ready: { value: "đã cấu hình", tone: "success" },
  not_configured: { value: "chưa cấu hình", tone: "neutral" },
  transient_error: { value: "lỗi tạm thời, thử lại", tone: "warning" },
};

const OCR_REASON_VALUE: Record<CapabilityReason, string> = {
  ocr_engine_missing: "thiếu Tesseract",
  ocr_language_missing: "thiếu vie.traineddata",
  secondary_ocr_disabled: "lượt đọc thứ hai tắt",
  llm_not_configured: "chưa cấu hình",
  vlm_not_configured: "chưa cấu hình",
  not_implemented: "chưa có trong bản này",
};

function ocrItem(runtime: RuntimeInfoPending): Pick<StatusItem, "value" | "tone"> {
  const view = capabilityOf(runtime, "ocr");
  if (view.state === "unknown") return { value: "backend chưa báo", tone: "neutral" };
  if (view.state === "available") return { value: "sẵn sàng", tone: "success" };
  return { value: view.reason === null ? "không khả dụng" : OCR_REASON_VALUE[view.reason], tone: "warning" };
}

/** Các ô của thanh trạng thái, theo thứ tự hiển thị. Ô nào cũng có chữ, không chỉ dựa vào màu. */
export function selectStatusItems(state: RuntimeState, useMocks: boolean): StatusItem[] {
  const items: StatusItem[] = [{ id: "backend", label: "Backend", ...BACKEND_ITEM[state.backend] }];
  const runtime = state.runtime;
  if (runtime !== null) {
    const llm = selectLlmStatus(state);
    if (llm !== "unknown") {
      items.push({ id: "llm", label: "LLM", ...LLM_ITEM[llm] });
    }
    items.push({ id: "ocr", label: "OCR", ...ocrItem(runtime) });
    if (selectOfflineMode(state)) {
      items.push({ id: "mode", label: "Chế độ", value: "đang chạy offline", tone: "info" });
    }
  }
  if (useMocks) {
    items.push({ id: "mock", label: "Nguồn", value: "Dữ liệu mẫu (mock)", tone: "info" });
  }
  return items;
}
