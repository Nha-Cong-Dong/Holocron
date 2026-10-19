import type {
  ApiErrorBody,
  CellPatch,
  IngestionItem,
  NonCloneableRequest,
  TablePatchRequest,
  TwinBatch,
  TwinBatchRequest,
  UploadResponse,
} from "@/types/holocron";

/** Đi qua rewrite của Next (next.config.ts) tới FastAPI cục bộ. */
const API_BASE = "/api/holocron";

/** Ứng dụng local-first một người dùng: mọi chỉnh sửa ghi audit dưới định danh này. */
export const LOCAL_EDITOR_ID = "local_user";

export class ApiError extends Error {
  readonly status: number;
  readonly body: ApiErrorBody | null;

  constructor(status: number, body: ApiErrorBody | null) {
    super(describeError(status, body));
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

function describeError(status: number, body: ApiErrorBody | null): string {
  if (body === null) {
    return `Máy chủ trả lỗi HTTP ${status}.`;
  }
  const { detail } = body;
  if (typeof detail === "string") {
    return detail;
  }
  if (Array.isArray(detail)) {
    return detail.map((item) => `${item.loc.join(".")}: ${item.msg}`).join("; ");
  }
  return detail.issues.length > 0 ? `${detail.message}: ${detail.issues.join("; ")}` : detail.message;
}

function isApiErrorBody(value: unknown): value is ApiErrorBody {
  return typeof value === "object" && value !== null && "detail" in value;
}

async function request<T>(path: string, init: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, init);
  if (!response.ok) {
    let body: ApiErrorBody | null = null;
    try {
      const parsed: unknown = await response.json();
      body = isApiErrorBody(parsed) ? parsed : null;
    } catch {
      body = null;
    }
    throw new ApiError(response.status, body);
  }
  return (await response.json()) as T;
}

function jsonInit(method: "POST" | "PATCH", payload: unknown): RequestInit {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  };
}

export function uploadDocument(file: File, topicKey: string, quantum: number | null): Promise<UploadResponse> {
  const form = new FormData();
  form.append("file", file);
  form.append("topic_key", topicKey);
  if (quantum !== null) {
    form.append("quantum", String(quantum));
  }
  return request<UploadResponse>("/v1/ingestion/upload", { method: "POST", body: form });
}

export function getItem(itemId: string): Promise<IngestionItem> {
  return request<IngestionItem>(`/v1/ingestion/items/${encodeURIComponent(itemId)}`, { method: "GET" });
}

export function patchTable(itemId: string, expectedVersion: number, patches: CellPatch[]): Promise<IngestionItem> {
  const payload: TablePatchRequest = { editor_id: LOCAL_EDITOR_ID, expected_version: expectedVersion, patches };
  return request<IngestionItem>(`/v1/ingestion/items/${encodeURIComponent(itemId)}/table`, jsonInit("PATCH", payload));
}

export function flagNonCloneable(itemId: string, reason: string): Promise<IngestionItem> {
  const payload: NonCloneableRequest = { editor_id: LOCAL_EDITOR_ID, reason };
  return request<IngestionItem>(
    `/v1/ingestion/items/${encodeURIComponent(itemId)}/non-cloneable`,
    jsonInit("POST", payload),
  );
}

export function approveItem(itemId: string): Promise<IngestionItem> {
  return request<IngestionItem>(`/v1/ingestion/items/${encodeURIComponent(itemId)}/approve`, { method: "POST" });
}

/**
 * Sinh bộ Twin Questions cho blueprint. `Idempotency-Key` cho phép gửi lại cùng body trong 24 h mà backend trả đúng
 * batch cũ, không chạy lại sandbox.
 */
export function createTwinBatch(
  blueprintId: string,
  payload: TwinBatchRequest,
  idempotencyKey: string | null,
): Promise<TwinBatch> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (idempotencyKey !== null) {
    headers["Idempotency-Key"] = idempotencyKey;
  }
  return request<TwinBatch>(`/v1/blueprints/${encodeURIComponent(blueprintId)}/twins`, {
    method: "POST",
    headers,
    body: JSON.stringify(payload),
  });
}
