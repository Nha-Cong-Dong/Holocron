/**
 * Trạng thái chạy và khả năng của app (spec §5.12, §11.4; kế hoạch UI mục 5.6), logic thuần để dùng lại và test.
 *
 * Mọi trạng thái suy ra từ dữ liệu backend trả (`RuntimeInfo`, mã lỗi đã phân loại); frontend không tự kiểm cấu hình.
 * `RuntimeInfo` được kiểm dạng một lần ở ranh giới (`normalizeRuntimeInfo`, gọi từ `lib/api.ts`), nên component chỉ
 * đọc `RuntimeView` đã chuẩn hoá và không bao giờ ném lỗi khi gặp giá trị lạ. Backend cũ chưa trả `capabilities` thì
 * mọi khả năng là "không rõ" và không chặn tính năng nào.
 */

import type { ErrorKind, UiError } from "@/lib/errors";
import { CAPABILITY_NAMES, CAPABILITY_REASONS, type CapabilityName, type CapabilityReason } from "@/types/holocron";

// ------------------------------------------------------------------------------- kiểm dạng ở ranh giới

/** Mã lý do của khả năng không khả dụng sau chuẩn hoá: mã đã biết, hoặc `"unrecognized"` khi backend trả mã lạ. */
export type CapabilityReasonView = CapabilityReason | "unrecognized";

/** Một phần tử `capabilities` sau khi kiểm dạng. */
export interface CapabilityEntry {
  /** Tên theo enum §5.12; `null` khi backend trả một tên frontend chưa biết (bản backend mới hơn). */
  name: CapabilityName | null;
  available: boolean;
  /** `null` khi và chỉ khi `available = true`; mã lạ hoặc thiếu mã khi không khả dụng thành `"unrecognized"`. */
  reason: CapabilityReasonView | null;
  detail_vi: string | null;
}

/** `RuntimeInfo` (§5.12) sau khi kiểm dạng; mọi component đọc kiểu này, không đọc JSON thô. */
export interface RuntimeView {
  demo_mode: boolean;
  llm_configured: boolean;
  vlm_configured: boolean;
  /** `null` khi backend chưa báo field này (đề xuất §17 D8). */
  mock_extractor: boolean | null;
  /** `null` khi backend chưa báo danh sách khả năng (bản cũ). */
  capabilities: CapabilityEntry[] | null;
  /** Thông tin bản chạy; vắng khi backend cũ chưa báo hoặc `log_dir = null` (không chạy qua launcher). */
  app_version?: string;
  commit?: string;
  log_dir?: string;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function knownName(value: string): CapabilityName | null {
  return CAPABILITY_NAMES.find((name) => name === value) ?? null;
}

function knownReason(value: unknown): CapabilityReason | null {
  return CAPABILITY_REASONS.find((reason) => reason === value) ?? null;
}

/** Một phần tử `capabilities`; `null` (bị bỏ qua) khi phần tử không phải object, thiếu `name` dạng chuỗi hay `available`. */
export function normalizeCapability(value: unknown): CapabilityEntry | null {
  if (!isRecord(value) || typeof value.name !== "string" || typeof value.available !== "boolean") return null;
  const detail = typeof value.detail_vi === "string" ? value.detail_vi : null;
  if (value.available) {
    return { name: knownName(value.name), available: true, reason: null, detail_vi: detail };
  }
  return {
    name: knownName(value.name),
    available: false,
    reason: knownReason(value.reason) ?? "unrecognized",
    detail_vi: detail,
  };
}

/**
 * Kiểm dạng thân `GET /v1/runtime`. Trả `null` khi thiếu một trong ba cờ boolean bắt buộc (khi đó `lib/api.ts` báo
 * phản hồi không hợp lệ). Field tuỳ chọn sai dạng thì coi như backend chưa báo; phần tử `capabilities` hỏng bị bỏ qua.
 */
export function normalizeRuntimeInfo(value: unknown): RuntimeView | null {
  if (!isRecord(value)) return null;
  const { demo_mode, llm_configured, vlm_configured } = value;
  if (typeof demo_mode !== "boolean" || typeof llm_configured !== "boolean" || typeof vlm_configured !== "boolean") {
    return null;
  }
  const capabilities = Array.isArray(value.capabilities)
    ? value.capabilities.map(normalizeCapability).filter((entry): entry is CapabilityEntry => entry !== null)
    : null;
  return {
    demo_mode,
    llm_configured,
    vlm_configured,
    mock_extractor: typeof value.mock_extractor === "boolean" ? value.mock_extractor : null,
    capabilities,
    ...(typeof value.app_version === "string" ? { app_version: value.app_version } : {}),
    ...(typeof value.commit === "string" ? { commit: value.commit } : {}),
    ...(typeof value.log_dir === "string" && value.log_dir !== "" ? { log_dir: value.log_dir } : {}),
  };
}

/** Số ký tự đầu của commit hiển thị trên thanh trạng thái. */
export const COMMIT_DISPLAY_LENGTH = 7;

/** Nhãn bản chạy `Holocron <app_version> (<commit 7 ký tự>)`; `null` khi backend chưa báo version. */
export function buildLabel(runtime: RuntimeView | null): string | null {
  if (runtime === null || runtime.app_version === undefined) return null;
  const base = `Holocron ${runtime.app_version}`;
  if (runtime.commit === undefined || runtime.commit === "") return base;
  return `${base} (${runtime.commit.slice(0, COMMIT_DISPLAY_LENGTH)})`;
}

/** Thư mục log launcher đang ghi; `null` khi không chạy qua launcher hoặc backend chưa báo. */
export function logDirOf(runtime: RuntimeView | null): string | null {
  return runtime?.log_dir ?? null;
}

// ------------------------------------------------------------------------------------- trạng thái tải

export type BackendStatus = "connecting" | "connected" | "unreachable" | "error";

export interface RuntimeState {
  backend: BackendStatus;
  /** Lần tải thành công gần nhất. Chỉ là dữ liệu hiện hành khi `backend = "connected"` (xem `freshRuntime`). */
  runtime: RuntimeView | null;
  error: UiError | null;
  /** Lần gọi LLM gần nhất trả 502 `LLM_UPSTREAM_ERROR`; xoá khi một lần gọi LLM sau đó thành công. */
  llmTransientError: boolean;
}

export type RuntimeAction =
  | { type: "load_started" }
  | { type: "load_succeeded"; runtime: RuntimeView }
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

/**
 * `RuntimeInfo` còn đúng với backend đang chạy: chỉ khi lần tải gần nhất thành công. Đang tải lại hoặc tải lại thất
 * bại thì dữ liệu cũ là "chưa cập nhật", không được hiển thị như trạng thái hiện tại.
 */
export function freshRuntime(state: RuntimeState): RuntimeView | null {
  return state.backend === "connected" ? state.runtime : null;
}

// ------------------------------------------------------------------------------------- khả năng

export const CAPABILITY_LABEL_VI: Record<CapabilityName, string> = {
  ocr: "Đọc trang scan bằng Tesseract",
  ocr_cross_check: "Đối chiếu số bằng lượt đọc thứ hai",
  vault_search: "Tìm trong kho tri thức",
  theory_generation: "Soạn câu hỏi lý thuyết từ khái niệm đã nhận",
  concept_pair_llm_proposal: "LLM đề xuất cặp khái niệm",
  essay_grading_offline: "Chấm tự luận offline",
  essay_grading_llm: "Chấm tự luận bằng LLM",
  template_authoring_llm: "LLM học cách sinh câu cho dạng bài mới",
  vlm: "Đọc ảnh bằng mô hình thị giác (VLM)",
  concept_auto_proposal: "Đề xuất khái niệm tự động",
  line_review_queue: "Hàng đợi duyệt theo dòng nghi ngờ",
  page_progress_eta: "Tiến độ theo trang và thời gian còn lại",
};

/** Nhãn cho tên khả năng frontend chưa biết; không chép tên thô của backend ra giao diện. */
export const UNRECOGNIZED_CAPABILITY_LABEL_VI = "Khả năng mà giao diện này chưa nhận ra";

export function capabilityLabel(entry: CapabilityEntry): string {
  return entry.name === null ? UNRECOGNIZED_CAPABILITY_LABEL_VI : CAPABILITY_LABEL_VI[entry.name];
}

export type CapabilityView =
  | { state: "unknown" }
  | { state: "available" }
  | { state: "unavailable"; reason: CapabilityReasonView; detail: string | null };

/** Danh sách khả năng backend trả; `null` khi backend chưa báo (bản cũ) hoặc chưa tải được. */
export function capabilitiesOf(runtime: RuntimeView | null): CapabilityEntry[] | null {
  return runtime === null ? null : runtime.capabilities;
}

export function capabilityOf(runtime: RuntimeView | null, name: CapabilityName): CapabilityView {
  const capability = capabilitiesOf(runtime)?.find((item) => item.name === name);
  if (capability === undefined) return { state: "unknown" };
  if (capability.available) return { state: "available" };
  return { state: "unavailable", reason: capability.reason ?? "unrecognized", detail: capability.detail_vi };
}

/** Nút gọi endpoint cần khả năng `name` chỉ bị vô hiệu khi backend báo rõ là không khả dụng. */
export function isCapabilityBlocked(runtime: RuntimeView | null, name: CapabilityName): boolean {
  return capabilityOf(runtime, name).state === "unavailable";
}

/**
 * Khả năng gửi dữ liệu ra khỏi máy khi chạy (gọi LLM hoặc VLM đã cấu hình; §11.4, §13.1). Bảng theo mã enum của
 * contract, không theo môn hay topic. Khả năng không có trong bảng (tên lạ) luôn được coi là có thể dùng mạng.
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
  concept_auto_proposal: false,
  line_review_queue: false,
  page_progress_eta: false,
};

/** Khả năng có thể gửi dữ liệu ra khỏi máy; tên lạ mặc định là có. */
export function mayUseNetwork(entry: CapabilityEntry): boolean {
  return entry.name === null || CAPABILITY_USES_NETWORK[entry.name];
}

/**
 * `offline`: khả dụng, chạy hoàn toàn trên máy; `online`: khả dụng nhưng gửi dữ liệu tới LLM/VLM đã cấu hình;
 * `unrecognized`: tên hoặc mã lý do mà giao diện này chưa biết, coi như có thể dùng mạng.
 */
export type CapabilityGroup =
  | "offline"
  | "online"
  | "pending"
  | "install"
  | "configure"
  | "not_implemented"
  | "unrecognized";

export type UnavailableGroup = Exclude<CapabilityGroup, "offline" | "online">;

// `secondary_ocr_disabled` là lựa chọn trong cấu hình backend (biến để trống), không phải thứ còn thiếu trên máy.
const REASON_GROUP: Record<CapabilityReason, UnavailableGroup> = {
  ocr_engine_missing: "install",
  ocr_language_missing: "install",
  secondary_ocr_disabled: "configure",
  secondary_ocr_unavailable: "install",
  local_check_pending: "pending",
  llm_not_configured: "configure",
  vlm_not_configured: "configure",
  not_implemented: "not_implemented",
};

export function capabilityGroup(entry: CapabilityEntry): CapabilityGroup {
  if (entry.name === null) return "unrecognized";
  if (entry.available) return mayUseNetwork(entry) ? "online" : "offline";
  if (entry.reason === null || entry.reason === "unrecognized") return "unrecognized";
  return REASON_GROUP[entry.reason];
}

/** Nhóm khả năng cho màn chạy lần đầu; giữ thứ tự backend trả trong từng nhóm. */
export function groupCapabilities(capabilities: CapabilityEntry[]): Record<CapabilityGroup, CapabilityEntry[]> {
  const groups: Record<CapabilityGroup, CapabilityEntry[]> = {
    offline: [],
    online: [],
    pending: [],
    install: [],
    configure: [],
    not_implemented: [],
    unrecognized: [],
  };
  for (const capability of capabilities) {
    groups[capabilityGroup(capability)].push(capability);
  }
  return groups;
}

/**
 * Câu giới thiệu nhóm "giao diện chưa nhận ra". Chỉ phần tử đang bật mới có thể gửi dữ liệu ra khỏi máy; phần tử đang
 * tắt thì không, nên câu cảnh báo dữ liệu chỉ có khi nhóm có ít nhất một phần tử đang bật.
 */
export function unrecognizedGroupIntro(entries: readonly CapabilityEntry[]): string {
  const base = "Backend báo các khả năng hoặc trạng thái dưới đây nhưng giao diện này chưa biết chúng.";
  const anyOn = entries.some((entry) => entry.available);
  const anyOff = entries.some((entry) => !entry.available);
  const parts = [base];
  if (anyOn) parts.push("Coi như phần đang bật có thể gửi dữ liệu ra khỏi máy.");
  if (anyOff) parts.push("Phần đang tắt thì chưa dùng được và không gửi dữ liệu đi đâu.");
  parts.push("Cập nhật giao diện để xem chi tiết.");
  return parts.join(" ");
}

/**
 * Màn Bắt đầu hiện gì ở phần khả năng: `legacy` khi backend chưa báo danh sách (bản cũ), `empty` khi backend trả danh
 * sách rỗng (hoặc mọi phần tử đều sai dạng nên bị bỏ ở bước kiểm dạng), `list` khi có ít nhất một khả năng.
 */
export type CapabilityOverviewKind = "legacy" | "empty" | "list";

export function capabilityOverviewKind(runtime: RuntimeView): CapabilityOverviewKind {
  if (runtime.capabilities === null) return "legacy";
  return runtime.capabilities.length === 0 ? "empty" : "list";
}

/**
 * Nhãn "Dữ liệu mẫu" ở trang tải đề và trang item (§5.12). `confirmed`: backend báo `mock_extractor = true`.
 * `assumed`: backend chưa có field `mock_extractor` nhưng `demo_mode = true`; với backend hiện tại `demo_mode` đang
 * bám extractor mẫu (§5.12, "Code hiện: extractor đang chạy là Mock"), nên giả định an toàn là dữ liệu có thể là mẫu
 * và nói rõ đó là giả định. `null`: không hiện nhãn (backend báo không phải dữ liệu mẫu, hoặc không có dấu hiệu nào).
 */
export type MockExtractorNotice = "confirmed" | "assumed" | null;

export function mockExtractorNotice(runtime: RuntimeView | null): MockExtractorNotice {
  if (runtime === null) return null;
  if (runtime.mock_extractor !== null) return runtime.mock_extractor ? "confirmed" : null;
  return runtime.demo_mode ? "assumed" : null;
}

/** Backend còn đang kiểm OCR nền: có khả năng báo `local_check_pending`. */
export function hasPendingLocalCheck(runtime: RuntimeView | null): boolean {
  return (capabilitiesOf(runtime) ?? []).some((entry) => !entry.available && entry.reason === "local_check_pending");
}

/** Cần hiện hướng dẫn cài Tesseract và `vie.traineddata`. */
export function needsTesseractHelp(runtime: RuntimeView | null): boolean {
  const view = capabilityOf(runtime, "ocr");
  return (
    view.state === "unavailable" && (view.reason === "ocr_engine_missing" || view.reason === "ocr_language_missing")
  );
}

/**
 * Dữ liệu có thể rời máy hay không (§13.1): chỉ khi LLM hoặc VLM đã cấu hình và có khả năng dùng mạng đang khả dụng.
 * `unknown` khi chưa có `RuntimeInfo` hiện hành. Tên khả năng lạ đang khả dụng được tính là có thể gửi dữ liệu.
 */
export type DataEgress = "unknown" | "local_only" | "may_leave";

export function selectDataEgress(runtime: RuntimeView | null): DataEgress {
  if (runtime === null) return "unknown";
  if (runtime.llm_configured || runtime.vlm_configured) return "may_leave";
  const capabilities = runtime.capabilities ?? [];
  return capabilities.some((entry) => entry.available && mayUseNetwork(entry)) ? "may_leave" : "local_only";
}

// ------------------------------------------------------------------------------- thanh trạng thái

export type LlmStatus = "unknown" | "ready" | "not_configured" | "transient_error";

export function selectLlmStatus(state: RuntimeState): LlmStatus {
  if (state.runtime === null) return "unknown";
  if (!state.runtime.llm_configured) return "not_configured";
  return state.llmTransientError ? "transient_error" : "ready";
}

/**
 * Băng "Đang chạy offline, không có LLM" trên mọi trang: chỉ khi backend báo rõ `llm_configured = false`.
 * Không dựa vào `demo_mode`, vì backend hiện đặt cờ đó theo extractor mẫu chứ chưa theo LLM (§5.12).
 */
export function selectOfflineMode(state: RuntimeState): boolean {
  return freshRuntime(state)?.llm_configured === false;
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

const OCR_REASON_VALUE: Record<CapabilityReasonView, string> = {
  ocr_engine_missing: "thiếu Tesseract",
  ocr_language_missing: "thiếu vie.traineddata",
  secondary_ocr_disabled: "lượt đọc thứ hai tắt",
  secondary_ocr_unavailable: "lượt đọc thứ hai không chạy được",
  local_check_pending: "đang kiểm tra…",
  llm_not_configured: "chưa cấu hình",
  vlm_not_configured: "chưa cấu hình",
  not_implemented: "chưa có trong bản này",
  unrecognized: "không khả dụng",
};

function ocrItem(runtime: RuntimeView): Pick<StatusItem, "value" | "tone"> {
  const view = capabilityOf(runtime, "ocr");
  if (view.state === "unavailable" && view.reason === "local_check_pending") {
    return { value: OCR_REASON_VALUE.local_check_pending, tone: "info" };
  }
  if (view.state === "unknown") return { value: "backend chưa báo", tone: "neutral" };
  if (view.state === "available") {
    const second = capabilityOf(runtime, "ocr_cross_check");
    if (second.state === "unavailable" && second.reason === "secondary_ocr_unavailable") {
      return { value: OCR_REASON_VALUE.secondary_ocr_unavailable, tone: "warning" };
    }
    return { value: "sẵn sàng", tone: "success" };
  }
  return { value: OCR_REASON_VALUE[view.reason], tone: "warning" };
}

/** Hậu tố cho ô đọc từ lần kiểm tra trước, khi backend đang kết nối lại hoặc không trả lời. */
export const STALE_SUFFIX = " (chưa cập nhật)";

function stale(item: StatusItem): StatusItem {
  return { ...item, value: `${item.value}${STALE_SUFFIX}`, tone: "neutral" };
}

/**
 * Các ô của thanh trạng thái, theo thứ tự hiển thị. Ô nào cũng có chữ, không chỉ dựa vào màu. Khi `RuntimeInfo` không
 * còn hiện hành, ô LLM và OCR ghi "chưa cập nhật" với màu trung tính, ô chế độ offline bị ẩn.
 */
export function selectStatusItems(state: RuntimeState, useMocks: boolean): StatusItem[] {
  const items: StatusItem[] = [{ id: "backend", label: "Backend", ...BACKEND_ITEM[state.backend] }];
  const runtime = state.runtime;
  if (runtime !== null) {
    const isFresh = freshRuntime(state) !== null;
    const capabilityItems: StatusItem[] = [];
    const llm = selectLlmStatus(state);
    if (llm !== "unknown") {
      capabilityItems.push({ id: "llm", label: "LLM", ...LLM_ITEM[llm] });
    }
    capabilityItems.push({ id: "ocr", label: "OCR", ...ocrItem(runtime) });
    items.push(...(isFresh ? capabilityItems : capabilityItems.map(stale)));
    if (selectOfflineMode(state)) {
      items.push({ id: "mode", label: "Chế độ", value: "đang chạy offline", tone: "info" });
    }
  }
  if (useMocks) {
    items.push({ id: "mock", label: "Nguồn", value: "Dữ liệu mẫu (mock)", tone: "info" });
  }
  return items;
}

/** Thời gian (ms) giữ nhãn kết quả sao chép trước khi về "Sao chép". */
export const COPY_FEEDBACK_MS = 2000;

export type CopyState = "idle" | "copied" | "failed";

export const COPY_LABEL_VI: Record<CopyState, string> = {
  idle: "Sao chép",
  copied: "Đã sao chép",
  failed: "Không sao chép được",
};

interface ClipboardLike {
  writeText: (text: string) => Promise<void>;
}

/** Sao chép `text`; không có clipboard, lỗi đồng bộ hoặc bị từ chối đều trả `"failed"`, không ném lỗi. */
export async function copyText(clipboard: ClipboardLike | null | undefined, text: string): Promise<CopyState> {
  if (!clipboard || typeof clipboard.writeText !== "function") return "failed";
  try {
    await clipboard.writeText(text);
    return "copied";
  } catch {
    return "failed";
  }
}
