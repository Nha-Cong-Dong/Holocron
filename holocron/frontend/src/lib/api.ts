/**
 * Client API có kiểu: mọi lời gọi backend của frontend đi qua module này (CLAUDE.md §4.2).
 *
 * - Base URL và header token lấy từ `lib/config.ts` lúc gọi (chạy được dưới `next dev`, static export và vỏ desktop).
 * - Lỗi được phân loại tập trung ở `lib/errors.ts`; component đọc `ApiError.info` để chọn thông điệp và hành động.
 * - `api` gộp dữ liệu mẫu cho endpoint backend CHƯA có khi bật `NEXT_PUBLIC_HOLOCRON_MOCKS`; endpoint đã có luôn gọi
 *   backend thật (kế hoạch UI mục 3.2). `/v1/runtime` đã có nên luôn gọi backend; cờ mock chỉ bù field backend chưa trả.
 * - Thân `/v1/runtime` được kiểm dạng ở đây (`normalizeRuntimeInfo`) trước khi tới component.
 */

import { apiBase, authHeaders, USE_MOCKS } from "@/lib/config";
import {
  type ClassifyOptions,
  classifyHttpError,
  classifyNetworkError,
  describeUiError,
  invalidResponseError,
  type UiError,
  unknownError,
} from "@/lib/errors";
import { normalizeRuntimeInfo, type RuntimeView } from "@/lib/runtime";
import { fillRuntimeGaps, mockApi } from "@/mocks";
import type { Subject } from "@/types/contract-pending";
import type {
  CellPatch,
  IngestionItem,
  NonCloneableRequest,
  TablePatchRequest,
  TwinBatch,
  TwinBatchRequest,
  UploadResponse,
} from "@/types/holocron";

/** Ứng dụng local-first một người dùng: mọi chỉnh sửa ghi audit dưới định danh này. */
export const LOCAL_EDITOR_ID = "local_user";

export class ApiError extends Error {
  /** Mã HTTP; `null` khi không tới được backend. Chỉ dùng cho logic, không hiển thị. */
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

interface RequestSpec {
  method: "GET" | "POST" | "PATCH" | "DELETE";
  body?: BodyInit;
  headers?: Record<string, string>;
}

async function request<T>(path: string, spec: RequestSpec, options: ClassifyOptions = {}): Promise<T> {
  const base = apiBase();
  const init: RequestInit = { method: spec.method, headers: { ...authHeaders(), ...spec.headers } };
  if (spec.body !== undefined) {
    init.body = spec.body;
  }
  let response: Response;
  try {
    response = await fetch(`${base}${path}`, init);
  } catch {
    throw new ApiError(null, classifyNetworkError(base));
  }
  if (!response.ok) {
    let body: unknown = null;
    try {
      body = await response.json();
    } catch {
      body = null;
    }
    throw new ApiError(response.status, classifyHttpError(response.status, body, options));
  }
  try {
    return (await response.json()) as T;
  } catch {
    throw new ApiError(response.status, invalidResponseError());
  }
}

function jsonSpec(method: "POST" | "PATCH", payload: unknown, headers: Record<string, string> = {}): RequestSpec {
  return { method, headers: { "Content-Type": "application/json", ...headers }, body: JSON.stringify(payload) };
}

function segment(value: string): string {
  return encodeURIComponent(value);
}

/** Mọi hàm gọi backend. Mock chỉ được thay phần tử của endpoint chưa có ở backend. */
export interface HolocronApi {
  /** `GET /v1/runtime` (§5.12), đã kiểm dạng. */
  getRuntime: () => Promise<RuntimeView>;
  /** `GET /v1/subjects` (§6.8, đường dẫn còn đề xuất; chưa có ở backend). */
  listSubjects: () => Promise<Subject[]>;
  /** `POST /v1/ingestion/upload` (§6.1). */
  uploadDocument: (file: File, topicKey: string, quantum: number | null) => Promise<UploadResponse>;
  /** `GET /v1/ingestion/items/{item_id}`. */
  getItem: (itemId: string) => Promise<IngestionItem>;
  /** `PATCH /v1/ingestion/items/{item_id}/table` (§6.2). */
  patchTable: (itemId: string, expectedVersion: number, patches: CellPatch[]) => Promise<IngestionItem>;
  /** `POST /v1/ingestion/items/{item_id}/non-cloneable`. */
  flagNonCloneable: (itemId: string, reason: string) => Promise<IngestionItem>;
  /** `POST /v1/ingestion/items/{item_id}/approve`. */
  approveItem: (itemId: string) => Promise<IngestionItem>;
  /**
   * `POST /v1/blueprints/{id}/twins` (§6.3). `Idempotency-Key` cho phép gửi lại cùng body trong 24 h mà backend trả
   * đúng batch cũ, không chạy lại sandbox.
   */
  createTwinBatch: (
    blueprintId: string,
    payload: TwinBatchRequest,
    idempotencyKey: string | null,
  ) => Promise<TwinBatch>;
}

/**
 * Gọi `/v1/runtime`, cho thân JSON qua `transform` (chỉ dùng để bù field còn thiếu khi bật mock) rồi kiểm dạng.
 * Thiếu cờ bắt buộc ⇒ lỗi phản hồi không hợp lệ, như thân không phải JSON.
 */
async function loadRuntime(transform: (raw: unknown) => unknown): Promise<RuntimeView> {
  const raw = await request<unknown>("/v1/runtime", { method: "GET" });
  const runtime = normalizeRuntimeInfo(transform(raw));
  if (runtime === null) {
    throw new ApiError(200, invalidResponseError());
  }
  return runtime;
}

export const realApi: HolocronApi = {
  getRuntime: () => loadRuntime((raw) => raw),

  listSubjects: () => request<Subject[]>("/v1/subjects", { method: "GET" }),

  uploadDocument: (file, topicKey, quantum) => {
    const form = new FormData();
    form.append("file", file);
    form.append("topic_key", topicKey);
    if (quantum !== null) {
      form.append("quantum", String(quantum));
    }
    return request<UploadResponse>("/v1/ingestion/upload", { method: "POST", body: form });
  },

  getItem: (itemId) => request<IngestionItem>(`/v1/ingestion/items/${segment(itemId)}`, { method: "GET" }),

  patchTable: (itemId, expectedVersion, patches) => {
    const payload: TablePatchRequest = { editor_id: LOCAL_EDITOR_ID, expected_version: expectedVersion, patches };
    return request<IngestionItem>(`/v1/ingestion/items/${segment(itemId)}/table`, jsonSpec("PATCH", payload), {
      versioned: true,
    });
  },

  flagNonCloneable: (itemId, reason) => {
    const payload: NonCloneableRequest = { editor_id: LOCAL_EDITOR_ID, reason };
    return request<IngestionItem>(`/v1/ingestion/items/${segment(itemId)}/non-cloneable`, jsonSpec("POST", payload));
  },

  approveItem: (itemId) => request<IngestionItem>(`/v1/ingestion/items/${segment(itemId)}/approve`, { method: "POST" }),

  createTwinBatch: (blueprintId, payload, idempotencyKey) => {
    const headers: Record<string, string> = idempotencyKey === null ? {} : { "Idempotency-Key": idempotencyKey };
    return request<TwinBatch>(`/v1/blueprints/${segment(blueprintId)}/twins`, jsonSpec("POST", payload, headers));
  },
};

/**
 * Bộ hàm gọi backend theo cờ mock. Bật mock: endpoint backend chưa có lấy từ `mockApi`; `/v1/runtime` vẫn gọi backend
 * thật (backend không chạy thì vẫn báo không kết nối), chỉ bù `capabilities` và `mock_extractor` còn thiếu.
 */
export function createApi(useMocks: boolean): HolocronApi {
  if (!useMocks) return realApi;
  return { ...realApi, ...mockApi, getRuntime: () => loadRuntime(fillRuntimeGaps) };
}

export const api: HolocronApi = createApi(USE_MOCKS);
