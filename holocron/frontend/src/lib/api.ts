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
  ApiError,
  type ClassifyOptions,
  classifyHttpError,
  classifyNetworkError,
  invalidResponseError,
} from "@/lib/errors";
import {
  type ChunkPageView,
  type EssayGradeView,
  type PracticeBatch,
  type PracticeBlueprint,
  parseBlueprint,
  parseChunkPage,
  parseEssayGradeResult,
  parsePracticeBatch,
} from "@/lib/practice-data";
import { normalizeRuntimeInfo, type RuntimeView } from "@/lib/runtime";
import { parseSubjectList } from "@/lib/subjects";
import { fillRuntimeGaps, mockApi, mockPracticeBatch } from "@/mocks";
import { isMockBlueprint } from "@/mocks/store";
import type { BlueprintCreate, EssayGradeRequest, Subject } from "@/types/contract-pending";
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

// `ApiError` và `toUiError` nằm ở `lib/errors.ts` để dữ liệu mẫu dựng được lỗi đã phân loại mà không import vòng.
export { ApiError, toUiError } from "@/lib/errors";

interface RequestSpec {
  method: "GET" | "POST" | "PATCH" | "DELETE";
  body?: BodyInit;
  headers?: Record<string, string>;
}

async function request<T>(path: string, spec: RequestSpec, options: ClassifyOptions = {}): Promise<T> {
  const base = apiBase();
  // Header xác thực gộp sau cùng để header riêng của request không ghi đè được token.
  const init: RequestInit = { method: spec.method, headers: { ...spec.headers, ...authHeaders() } };
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

/** Kết quả của bước kiểm dạng; `null` (thân sai dạng) thành lỗi phản hồi không hợp lệ, như thân không phải JSON. */
function validated<T>(value: T | null): T {
  if (value === null) throw new ApiError(200, invalidResponseError());
  return value;
}

/** Tham số của `GET /v1/vault/chunks` (§6.6) mà màn luyện tập dùng để tìm văn bản nguồn của trích dẫn. */
export interface ChunkQuery {
  subjectCode: string;
  chapterCode: string | null;
  offset: number;
  limit: number;
}

/** Mọi hàm gọi backend. Mock chỉ được thay phần tử của endpoint chưa có ở backend. */
export interface HolocronApi {
  /** `GET /v1/runtime` (§5.12), đã kiểm dạng. */
  getRuntime: () => Promise<RuntimeView>;
  /** `GET /v1/subjects` (§6.8, đường dẫn còn đề xuất; chưa có ở backend), đã kiểm dạng từng phần tử. */
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
  /** `POST /v1/blueprints` (§6.7; chưa có ở backend), đã kiểm dạng. */
  createBlueprint: (payload: BlueprintCreate) => Promise<PracticeBlueprint>;
  /**
   * `POST /v1/blueprints/{id}/twins` (§6.3) cho màn luyện tập: cùng endpoint với `createTwinBatch` nhưng thân trả về
   * được kiểm dạng thành từng câu (câu sai dạng bị bỏ và được đếm).
   */
  generatePracticeBatch: (
    blueprintId: string,
    payload: TwinBatchRequest,
    idempotencyKey: string | null,
  ) => Promise<PracticeBatch>;
  /** `POST /v1/twins/{twin_id}/essay-grade` (§6.9; chưa có ở backend), đã kiểm dạng. */
  gradeEssay: (twinId: string, answerText: string) => Promise<EssayGradeView>;
  /** `GET /v1/vault/chunks` (§6.6; chưa có ở backend của nhánh này), đã kiểm dạng. */
  listVaultChunks: (query: ChunkQuery) => Promise<ChunkPageView>;
}

/**
 * Gọi `/v1/runtime`, cho thân JSON qua `transform` (chỉ dùng để bù field còn thiếu khi bật mock) rồi kiểm dạng.
 * Thiếu cờ bắt buộc ⇒ lỗi phản hồi không hợp lệ, như thân không phải JSON.
 */
async function loadRuntime(transform: (raw: unknown) => unknown): Promise<RuntimeView> {
  const raw = await request<unknown>("/v1/runtime", { method: "GET" }, { context: "runtime" });
  const runtime = normalizeRuntimeInfo(transform(raw));
  if (runtime === null) {
    throw new ApiError(200, invalidResponseError());
  }
  return runtime;
}

export const realApi: HolocronApi = {
  getRuntime: () => loadRuntime((raw) => raw),

  listSubjects: async () => {
    const subjects = parseSubjectList(await request<unknown>("/v1/subjects", { method: "GET" }));
    if (subjects === null) {
      throw new ApiError(200, invalidResponseError());
    }
    return subjects;
  },

  uploadDocument: (file, topicKey, quantum) => {
    const form = new FormData();
    form.append("file", file);
    form.append("topic_key", topicKey);
    if (quantum !== null) {
      form.append("quantum", String(quantum));
    }
    return request<UploadResponse>("/v1/ingestion/upload", { method: "POST", body: form }, { context: "exam_upload" });
  },

  getItem: (itemId) => request<IngestionItem>(`/v1/ingestion/items/${segment(itemId)}`, { method: "GET" }),

  patchTable: (itemId, expectedVersion, patches) => {
    const payload: TablePatchRequest = { editor_id: LOCAL_EDITOR_ID, expected_version: expectedVersion, patches };
    return request<IngestionItem>(`/v1/ingestion/items/${segment(itemId)}/table`, jsonSpec("PATCH", payload), {
      versioned: true,
      context: "table_edit",
    });
  },

  flagNonCloneable: (itemId, reason) => {
    const payload: NonCloneableRequest = { editor_id: LOCAL_EDITOR_ID, reason };
    return request<IngestionItem>(`/v1/ingestion/items/${segment(itemId)}/non-cloneable`, jsonSpec("POST", payload), {
      context: "item_review",
    });
  },

  approveItem: (itemId) =>
    request<IngestionItem>(
      `/v1/ingestion/items/${segment(itemId)}/approve`,
      { method: "POST" },
      { context: "item_review" },
    ),

  createTwinBatch: (blueprintId, payload, idempotencyKey) => {
    const headers: Record<string, string> = idempotencyKey === null ? {} : { "Idempotency-Key": idempotencyKey };
    return request<TwinBatch>(`/v1/blueprints/${segment(blueprintId)}/twins`, jsonSpec("POST", payload, headers), {
      context: "twin_generation",
    });
  },

  createBlueprint: async (payload) => {
    const raw = await request<unknown>("/v1/blueprints", jsonSpec("POST", payload), { context: "blueprint_create" });
    return validated(parseBlueprint(raw));
  },

  generatePracticeBatch: async (blueprintId, payload, idempotencyKey) => {
    const headers: Record<string, string> = idempotencyKey === null ? {} : { "Idempotency-Key": idempotencyKey };
    const raw = await request<unknown>(
      `/v1/blueprints/${segment(blueprintId)}/twins`,
      jsonSpec("POST", payload, headers),
      { context: "twin_generation" },
    );
    return validated(parsePracticeBatch(raw));
  },

  gradeEssay: async (twinId, answerText) => {
    const payload: EssayGradeRequest = { answer_text: answerText };
    const raw = await request<unknown>(`/v1/twins/${segment(twinId)}/essay-grade`, jsonSpec("POST", payload), {
      context: "essay_grade",
    });
    return validated(parseEssayGradeResult(raw));
  },

  listVaultChunks: async (query) => {
    const params = new URLSearchParams({
      subject_code: query.subjectCode,
      offset: String(query.offset),
      limit: String(query.limit),
    });
    if (query.chapterCode !== null) params.set("chapter_code", query.chapterCode);
    const raw = await request<unknown>(
      `/v1/vault/chunks?${params.toString()}`,
      { method: "GET" },
      { context: "vault_source" },
    );
    return validated(parseChunkPage(raw));
  },
};

/**
 * Bộ hàm gọi backend theo cờ mock. Bật mock: endpoint backend chưa có lấy từ `mockApi`; `/v1/runtime` vẫn gọi backend
 * thật (backend không chạy thì vẫn báo không kết nối), chỉ bù `capabilities` và `mock_extractor` còn thiếu.
 */
export function createApi(useMocks: boolean): HolocronApi {
  if (!useMocks) return realApi;
  return {
    ...realApi,
    ...mockApi,
    getRuntime: () => loadRuntime(fillRuntimeGaps),
    // Sinh câu đã có ở backend (câu tính toán): chỉ blueprint do dữ liệu mẫu tạo mới lấy batch mẫu (M8).
    generatePracticeBatch: (blueprintId, payload, idempotencyKey) =>
      isMockBlueprint(blueprintId)
        ? mockPracticeBatch()
        : realApi.generatePracticeBatch(blueprintId, payload, idempotencyKey),
  };
}

export const api: HolocronApi = createApi(USE_MOCKS);
