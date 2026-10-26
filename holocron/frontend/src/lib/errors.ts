/**
 * Phân loại lỗi gọi backend thành thông điệp tiếng Việt ngắn và một hành động UI (kế hoạch UI mục 5.6).
 *
 * Thân lỗi đọc như `unknown` và kiểm từng dạng `detail` của spec §6.0 bằng type guard: chuỗi, danh sách lỗi validate
 * (422), `{message, issues}` (409 khi approve còn BLOCKING), `CapabilityError` (409) và `LLM_UPSTREAM_ERROR` (502).
 * `CapabilityError` và `LLM_UPSTREAM_ERROR` nhận diện theo `code` trước; `capability`/`reason` lạ vẫn là lỗi "chưa
 * cấu hình", chỉ đổi sang thông điệp chung. Thông điệp hiển thị là câu cố định theo loại lỗi và luồng gọi
 * (`ClassifyOptions.context`): không lộ mã HTTP, tên field hay câu chữ nội bộ của backend.
 * Ngoại lệ duy nhất: danh sách `issues` của 409 BLOCKING là thông điệp Gate viết cho người dùng.
 */

import {
  DOCUMENT_MAX_PAGES,
  DOCUMENT_UPLOAD_MAX_MIB,
  EXAM_UPLOAD_MAX_MIB,
  FILENAME_MAX_CHARS,
  MAX_THEORY_SLOTS,
  QUANTUM_MAX,
  QUANTUM_MIN,
} from "@/lib/limits";
import { MAX_TWIN_COUNT, MIN_TWIN_COUNT } from "@/lib/twin";
import {
  CAPABILITY_NAMES,
  CAPABILITY_REASONS,
  type CapabilityError,
  type CapabilityName,
  type CapabilityReason,
} from "@/types/contract-pending";

export type ErrorKind =
  | "network"
  | "server"
  | "capability"
  | "llm_upstream"
  | "blocking"
  | "version_conflict"
  | "state_conflict"
  | "unapproved_source"
  | "too_large"
  | "unsupported_media"
  | "validation"
  | "not_found"
  | "forbidden"
  | "bad_request"
  | "busy"
  | "unknown";

/** Hành động gợi ý trên UI: tải lại bản mới nhất, thử lại, hoặc không có nút (cần cấu hình hay sửa dữ liệu). */
export type ErrorAction = "reload" | "retry" | "none";

export interface UiError {
  kind: ErrorKind;
  message: string;
  action: ErrorAction;
  /** Chỉ có với `kind = "blocking"`: thông điệp của các issue BLOCKING do Gate trả. */
  issues: string[];
  /** Chỉ có với `kind = "capability"`; `name`/`reason` là `null` khi backend trả mã frontend chưa biết. */
  capability: { name: CapabilityName | null; reason: CapabilityReason | null } | null;
}

/** Luồng gọi API, để chọn câu riêng cho 400/404/409/413/415/422 thay vì câu chung. */
export type ErrorContext =
  | "runtime"
  | "exam_upload"
  | "document_upload"
  | "table_edit"
  | "item_review"
  | "blueprint_create"
  | "twin_generation"
  | "essay_grade"
  | "vault_source";

export interface ClassifyOptions {
  /** Request có gửi `expected_version`: 409 dạng chuỗi được hiểu là bản đã đổi ở nơi khác. */
  versioned?: boolean;
  /** Luồng gọi; không truyền thì dùng câu chung theo loại lỗi. */
  context?: ErrorContext;
}

/** Gợi ý ngắn theo mã lý do; không nêu tên khả năng nội bộ hay giá trị cấu hình. */
const CAPABILITY_REASON_HINT_VI: Record<CapabilityReason, string> = {
  llm_not_configured: "cần LLM, mà backend chưa được cấu hình LLM.",
  vlm_not_configured: "cần mô hình đọc ảnh (VLM), mà backend chưa được cấu hình VLM.",
  ocr_engine_missing: "cần Tesseract, mà máy chưa cài hoặc backend không chạy được Tesseract.",
  ocr_language_missing: "Tesseract còn thiếu dữ liệu tiếng Việt (vie.traineddata).",
  secondary_ocr_disabled: "lượt đọc thứ hai đang tắt trong cấu hình backend.",
  not_implemented: "bản đang chạy chưa có tính năng này.",
};

/** Câu chung khi `CapabilityError` mang mã lý do frontend chưa biết. */
const CAPABILITY_GENERIC_VI =
  "Tính năng này chưa sẵn sàng: backend chưa được cấu hình hoặc máy còn thiếu thành phần cần thiết. Xem màn Bắt đầu để biết khả năng nào đang tắt.";

const MESSAGES: Record<Exclude<ErrorKind, "network" | "capability" | "blocking">, string> = {
  server: "Không kết nối được backend, hoặc backend vừa gặp lỗi. Kiểm tra backend đang chạy rồi thử lại.",
  llm_upstream: "Dịch vụ LLM đang lỗi tạm thời. Thử lại sau ít phút.",
  version_conflict: "Dữ liệu đã được sửa ở nơi khác hoặc không còn sửa được. Tải lại bản mới nhất rồi làm lại.",
  state_conflict: "Thao tác này không còn phù hợp với trạng thái hiện tại. Tải lại để xem trạng thái mới nhất.",
  unapproved_source:
    "Bộ đề có đề thi chưa được duyệt hoặc còn lỗi chặn ở bước kiểm duyệt, nên chưa sinh câu hỏi. Mở đề đó ở mục “Nhập đề thi”, sửa các ô bị chặn rồi duyệt lại.",
  too_large: "File vượt giới hạn dung lượng cho phép.",
  unsupported_media: "Định dạng file không được hỗ trợ.",
  validation: "Dữ liệu gửi lên không hợp lệ. Kiểm tra lại các ô đã nhập.",
  not_found: "Không tìm thấy dữ liệu yêu cầu. Có thể nó đã bị xoá.",
  forbidden: "Backend từ chối yêu cầu từ cửa sổ này. Khởi động lại ứng dụng rồi thử lại.",
  bad_request: "Thao tác không hợp lệ với trạng thái hiện tại.",
  busy: "Kho dữ liệu đang bận. Thử lại sau giây lát.",
  unknown: "Đã có lỗi không mong muốn. Thử lại sau.",
};

type ContextualKind = "bad_request" | "not_found" | "too_large" | "unsupported_media" | "validation";

/**
 * Câu riêng theo luồng (§6.0, §6.1, §6.5). Không chép `detail` của backend; một câu nêu mọi nguyên nhân có thể của mã
 * đó trong luồng, kèm việc người dùng làm tiếp.
 */
const CONTEXT_MESSAGES: Record<ErrorContext, Partial<Record<ContextualKind, string>>> = {
  runtime: {},
  exam_upload: {
    bad_request: "File đề rỗng. Chọn lại file có nội dung.",
    too_large: `File đề vượt ${EXAM_UPLOAD_MAX_MIB} MiB. Chọn ảnh nhỏ hơn hoặc PDF ít trang hơn.`,
    unsupported_media: "Chỉ nhận ảnh PNG, JPEG, WebP hoặc PDF, và nội dung file phải đúng định dạng đó.",
    validation: `Không xử lý được đề với lựa chọn hiện tại. Kiểm tra dạng bài và quantum (số nguyên ${QUANTUM_MIN}–${QUANTUM_MAX}) rồi thử lại.`,
  },
  document_upload: {
    bad_request: "File tài liệu rỗng. Chọn lại file có nội dung.",
    too_large: `Tài liệu vượt ${DOCUMENT_UPLOAD_MAX_MIB} MiB. Tách thành nhiều file nhỏ hơn rồi nhập lại.`,
    unsupported_media: "Chỉ nhận file PDF, và nội dung file phải đúng là PDF.",
    validation: `Không mở được tài liệu: PDF có mật khẩu, dài quá ${DOCUMENT_MAX_PAGES} trang, bị hỏng, hoặc tên file dài quá ${FILENAME_MAX_CHARS} ký tự. Gỡ mật khẩu, tách nhỏ hoặc đổi tên file rồi nhập lại.`,
  },
  table_edit: {
    validation: "Giá trị vừa nhập không hợp lệ. Kiểm tra lại ô vừa sửa.",
  },
  item_review: {
    bad_request: "Đề này đã được duyệt hoặc đã bị đánh dấu không nhân bản được, nên không làm được thao tác này.",
  },
  blueprint_create: {
    not_found:
      "Backend đang chạy chưa hỗ trợ tạo bộ đề luyện tập. Cập nhật backend lên bản có tính năng này rồi thử lại.",
    validation: `Không tạo được bộ đề với lựa chọn hiện tại. Kiểm tra mã chương (ví dụ ch1 hoặc ch2.1) và số câu (từ 1 đến ${MAX_THEORY_SLOTS}).`,
  },
  twin_generation: {
    not_found: "Không tìm thấy bộ đề này ở backend. Có thể backend vừa khởi động lại; tạo lại bộ đề rồi thử lại.",
    validation: `Yêu cầu sinh câu không hợp lệ. Số câu mỗi lần phải từ ${MIN_TWIN_COUNT} đến ${MAX_TWIN_COUNT}.`,
  },
  essay_grade: {
    not_found: "Không tìm thấy câu hỏi này ở backend. Có thể backend vừa khởi động lại; tạo lại bộ đề rồi làm lại.",
    validation:
      "Chưa chấm được: câu trả lời đang trống, dài quá giới hạn của backend, hoặc câu này không phải câu tự luận. Sửa câu trả lời rồi nộp lại.",
  },
  vault_source: {
    not_found: "Backend đang chạy chưa cho xem văn bản nguồn trong kho tri thức. Trích dẫn ở trên vẫn là nguyên văn.",
  },
};

function contextual(kind: ContextualKind, options: ClassifyOptions): UiError {
  const message = options.context === undefined ? undefined : CONTEXT_MESSAGES[options.context][kind];
  return build(kind, message ?? MESSAGES[kind]);
}

const ACTIONS: Record<ErrorKind, ErrorAction> = {
  network: "retry",
  server: "retry",
  capability: "none",
  llm_upstream: "retry",
  blocking: "none",
  version_conflict: "reload",
  state_conflict: "reload",
  unapproved_source: "none",
  too_large: "none",
  unsupported_media: "none",
  validation: "none",
  not_found: "none",
  forbidden: "none",
  bad_request: "none",
  busy: "retry",
  unknown: "retry",
};

function build(kind: ErrorKind, message: string, extra: Partial<Pick<UiError, "issues" | "capability">> = {}): UiError {
  return { kind, message, action: ACTIONS[kind], issues: extra.issues ?? [], capability: extra.capability ?? null };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isCapabilityName(value: unknown): value is CapabilityName {
  return typeof value === "string" && CAPABILITY_NAMES.some((name) => name === value);
}

function isCapabilityReason(value: unknown): value is CapabilityReason {
  return typeof value === "string" && CAPABILITY_REASONS.some((reason) => reason === value);
}

/** `detail` có dạng `CapabilityError` (§6.0). */
export function isCapabilityError(detail: unknown): detail is CapabilityError {
  return (
    isRecord(detail) &&
    detail.code === "CAPABILITY_UNAVAILABLE" &&
    isCapabilityName(detail.capability) &&
    isCapabilityReason(detail.reason) &&
    typeof detail.message === "string"
  );
}

/** `detail` mang `code` của `CapabilityError`, bất kể `capability`/`reason` có thuộc enum frontend biết hay không. */
function hasCapabilityCode(detail: unknown): detail is Record<string, unknown> {
  return isRecord(detail) && detail.code === "CAPABILITY_UNAVAILABLE";
}

function isLlmUpstreamError(detail: unknown): boolean {
  return isRecord(detail) && detail.code === "LLM_UPSTREAM_ERROR";
}

function blockingIssues(detail: unknown): string[] | null {
  if (!isRecord(detail) || typeof detail.message !== "string" || !Array.isArray(detail.issues)) return null;
  return detail.issues.filter((issue): issue is string => typeof issue === "string");
}

/** Lấy `detail` từ thân lỗi; `undefined` khi thân không phải JSON dạng `{"detail": …}` của FastAPI. */
export function extractDetail(body: unknown): unknown {
  return isRecord(body) && "detail" in body ? body.detail : undefined;
}

/** Phân loại lỗi HTTP từ mã trạng thái và thân lỗi đã parse (hoặc `null` khi thân không phải JSON). */
export function classifyHttpError(status: number, body: unknown, options: ClassifyOptions = {}): UiError {
  const detail = extractDetail(body);

  if (hasCapabilityCode(detail)) {
    const name = isCapabilityName(detail.capability) ? detail.capability : null;
    const reason = isCapabilityReason(detail.reason) ? detail.reason : null;
    const message =
      reason === null ? CAPABILITY_GENERIC_VI : `Tính năng này chưa sẵn sàng: ${CAPABILITY_REASON_HINT_VI[reason]}`;
    return build("capability", message, { capability: { name, reason } });
  }
  if (isLlmUpstreamError(detail)) {
    return build("llm_upstream", MESSAGES.llm_upstream);
  }

  switch (status) {
    case 400:
      return contextual("bad_request", options);
    case 403:
      return build("forbidden", MESSAGES.forbidden);
    case 404:
      // Thân không phải JSON dạng FastAPI: proxy hay máy chủ tĩnh trả 404 vì không tới được backend, không phải
      // backend báo thiếu dữ liệu. `/v1/runtime` luôn có ở backend Holocron, nên 404 ở đó cũng là sai địa chỉ backend.
      if (detail === undefined) return build("server", MESSAGES.server);
      if (options.context === "runtime") return build("server", RUNTIME_NOT_FOUND_VI);
      return contextual("not_found", options);
    case 409:
      return classifyConflict(detail, options);
    case 413:
      return contextual("too_large", options);
    case 415:
      return contextual("unsupported_media", options);
    case 422:
      return contextual("validation", options);
    case 503:
      // 503 chỉ dùng khi kho SQLite bận (§6.0); thân không phải JSON nghĩa là proxy không tới được backend.
      return detail === undefined ? build("server", MESSAGES.server) : build("busy", MESSAGES.busy);
    default:
      if (status >= 500) return build("server", MESSAGES.server);
      return build("unknown", MESSAGES.unknown);
  }
}

/** Luồng sinh câu: 409 nghĩa là bộ đề dựa trên đề chưa duyệt hoặc còn lỗi chặn (I5), không phải bản cũ cần tải lại. */
const GENERATION_CONTEXTS: readonly ErrorContext[] = ["blueprint_create", "twin_generation"];

function classifyConflict(detail: unknown, options: ClassifyOptions): UiError {
  const forGeneration = options.context !== undefined && GENERATION_CONTEXTS.includes(options.context);
  const issues = blockingIssues(detail);
  if (issues !== null) {
    const message = forGeneration
      ? "Còn lỗi chặn ở bước kiểm duyệt đề, nên chưa sinh câu hỏi. Sửa các lỗi dưới đây rồi duyệt lại đề."
      : "Còn lỗi chặn cần sửa trước khi duyệt.";
    return build("blocking", message, { issues });
  }
  if (forGeneration) return build("unapproved_source", MESSAGES.unapproved_source);
  const kind = options.versioned === true ? "version_conflict" : "state_conflict";
  return build(kind, MESSAGES[kind]);
}

const RUNTIME_NOT_FOUND_VI =
  "Địa chỉ đã cấu hình không trả lời như backend Holocron (có thể nhầm địa chỉ, hoặc backend quá cũ). Kiểm tra backend đang chạy đúng địa chỉ rồi thử lại.";

/**
 * Địa chỉ backend để hiển thị: bỏ phần tên đăng nhập và mật khẩu (`user:pass@`) nếu có, để không in thông tin xác
 * thực ra giao diện. `lib/config.ts` đã từ chối base URL có phần này; hàm này là lớp chặn thứ hai.
 */
export function displayBase(base: string): string {
  return base.replace(/^([a-z][a-z0-9+.-]*:\/\/)[^/?#]*@/i, "$1");
}

/** Lỗi khi `fetch` không tới được backend (backend chưa chạy, sai địa chỉ). */
export function classifyNetworkError(base: string): UiError {
  const where = base === "" ? "cùng địa chỉ với giao diện" : displayBase(base);
  return build("network", `Không kết nối được backend ở ${where}. Kiểm tra backend đã chạy chưa rồi thử lại.`);
}

/** Backend trả mã thành công nhưng thân không phải JSON (thường do base URL trỏ nhầm chỗ). */
export function invalidResponseError(): UiError {
  return build("server", MESSAGES.server);
}

/** Lỗi gọi backend đã phân loại; `status` chỉ dùng cho logic, không hiển thị. */
export class ApiError extends Error {
  /** Mã HTTP; `null` khi không tới được backend. */
  readonly status: number | null;
  readonly info: UiError;

  constructor(status: number | null, info: UiError) {
    super(describeUiError(info));
    this.name = "ApiError";
    this.status = status;
    this.info = info;
  }
}

/** Lỗi bất kỳ bắt được từ lời gọi API → lỗi đã phân loại để hiển thị. */
export function toUiError(error: unknown): UiError {
  return error instanceof ApiError ? error.info : unknownError();
}

/** Lỗi không rõ nguồn (không phải `ApiError`). */
export function unknownError(): UiError {
  return build("unknown", MESSAGES.unknown);
}

/** Lỗi khi một màn hình không hiển thị được (dùng ở `app/error.tsx`); không nêu chi tiết kỹ thuật. */
export function renderError(): UiError {
  return build(
    "unknown",
    "Màn hình này gặp lỗi khi hiển thị. Bấm “Thử lại”; nếu vẫn lỗi, chuyển sang màn khác hoặc khởi động lại ứng dụng.",
  );
}

/** Một dòng thông điệp đầy đủ, ghép cả danh sách issue BLOCKING nếu có. */
export function describeUiError(error: UiError): string {
  return error.issues.length > 0 ? `${error.message} ${error.issues.join("; ")}` : error.message;
}

/** Gắn key React cho danh sách câu; câu trùng hệt nhau được thêm hậu tố `#n` (thứ tự xuất hiện) để key không trùng. */
export function keyedMessages(messages: string[]): { key: string; message: string }[] {
  const seen = new Map<string, number>();
  return messages.map((message) => {
    const count = seen.get(message) ?? 0;
    seen.set(message, count + 1);
    return { key: count === 0 ? message : `${message}#${count}`, message };
  });
}

/** Lỗi dựng ở giao diện (kiểm dữ liệu trước khi gửi); không có nút hành động. */
export function localError(message: string): UiError {
  return build("validation", message);
}

/** Nhãn nút theo hành động. */
export function actionLabel(action: ErrorAction): string | null {
  if (action === "reload") return "Tải lại bản mới nhất";
  if (action === "retry") return "Thử lại";
  return null;
}
