/**
 * Client API có kiểu: mọi lời gọi backend của frontend đi qua module này (CLAUDE.md §4.2).
 *
 * - Base URL và header token lấy từ `lib/config.ts` lúc gọi (chạy được dưới `next dev`, static export và vỏ desktop).
 * - Lỗi được phân loại tập trung ở `lib/errors.ts`; component đọc `ApiError.info` để chọn thông điệp và hành động.
 * - `api` gộp dữ liệu mẫu cho endpoint backend CHƯA có khi bật `NEXT_PUBLIC_HOLOCRON_MOCKS`; endpoint đã có luôn gọi
 *   backend thật (kế hoạch UI mục 3.2), gồm môn học, tạo bộ đề và sinh câu. `/v1/runtime` đã có nên luôn gọi backend;
 *   cờ mock chỉ bù field backend chưa trả.
 * - Thân `/v1/runtime` được kiểm dạng ở đây (`normalizeRuntimeInfo`) trước khi tới component.
 */

import { apiBase, authHeaders, USE_MOCKS } from "@/lib/config";
import {
  type DocumentImportOutcome,
  type DocumentSummaryView,
  type DocumentView,
  type PageView,
  parseDocumentImport,
  parseDocumentSummaries,
  parsePageExtraction,
} from "@/lib/documents-data";
import {
  ApiError,
  type ClassifyOptions,
  classifyHttpError,
  classifyNetworkError,
  invalidResponseError,
} from "@/lib/errors";
import { type ImportResultView, type ProposalJobView, parseImportResult, parseProposalJob } from "@/lib/import-result";
import { type ConceptPairList, parseConceptPair, parseConceptPairList } from "@/lib/pairs";
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
import { parseSubject, parseSubjectList } from "@/lib/subjects";
import {
  type ChunkFilter,
  type ChunkLabelChange,
  type ChunkListView,
  type ChunkView,
  chunkQueryString,
  parseChunkList,
  parseKnowledgeChunk,
  parseSearchHits,
  type SearchQuery,
  type SearchResultView,
  searchQueryString,
} from "@/lib/vault";
import { fillRuntimeGaps, mockApi, mockDocumentApi } from "@/mocks";
import { isImportResultMock, mockImportResultApi } from "@/mocks/import-result";
import { isMockDocument } from "@/mocks/store";
import type { EssayGradeRequest, ProposalJobCreate } from "@/types/contract-pending";
import type {
  BlueprintCreate,
  CellPatch,
  ChunkLabelsPatch,
  ConceptPair,
  ConceptPairConfirm,
  ConceptPairCreate,
  ConceptPairReject,
  ConceptPairStatus,
  DocumentConfirmRequest,
  IngestionItem,
  LineEdit,
  NonCloneableRequest,
  PageConfirmRequest,
  PageLinesPatch,
  PageRestoreRequest,
  Subject,
  SubjectCreate,
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

/** Gửi request; mã lỗi HTTP và lỗi mạng thành `ApiError` đã phân loại. Trả `Response` thành công (chưa đọc thân). */
async function send(path: string, spec: RequestSpec, options: ClassifyOptions = {}): Promise<Response> {
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
  return response;
}

/** Thân JSON của phản hồi thành công, chưa kiểm dạng; thân không phải JSON ⇒ lỗi phản hồi không hợp lệ. */
async function readJson(response: Response): Promise<unknown> {
  try {
    const body: unknown = await response.json();
    return body;
  } catch {
    throw new ApiError(response.status, invalidResponseError());
  }
}

async function request<T>(path: string, spec: RequestSpec, options: ClassifyOptions = {}): Promise<T> {
  return (await readJson(await send(path, spec, options))) as T;
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
  /** Môn của blueprint sinh ra câu (ADR-006 mục 6), không phải môn đang chọn ở thanh bên. */
  subjectCode: string;
  chapterCode: string | null;
  offset: number;
  limit: number;
}

/** Tham số của `GET /v1/vault/concept-pairs` (§6.6); `null` là không lọc. */
export interface ConceptPairQuery {
  subjectCode: string;
  chapterCode: string | null;
  status: ConceptPairStatus | null;
}

/** Phần sửa khi xác nhận cặp (§6.6); field `undefined` thì giữ nguyên ở backend. */
export interface ConceptPairEdits {
  term?: string;
  definitionQuote?: string;
}

/** Dữ liệu của form nhập tài liệu, đã kiểm ở `lib/documents.ts`. */
export interface DocumentImportInput {
  file: File;
  subjectCode: string;
  sourceKind: string;
  chapterCode: string;
  /** `null` để backend lấy tên file bỏ đuôi. */
  title: string | null;
}

/** Mọi hàm gọi backend. Mock chỉ được thay phần tử của endpoint chưa có ở backend. */
export interface HolocronApi {
  /** `GET /v1/runtime` (§5.12), đã kiểm dạng. */
  getRuntime: () => Promise<RuntimeView>;
  /** `GET /v1/subjects` (§6.8), đã kiểm dạng từng phần tử. */
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
  /** `POST /v1/blueprints` (§6.7): 201 `ExamBlueprint`, đã kiểm dạng. */
  createBlueprint: (payload: BlueprintCreate) => Promise<PracticeBlueprint>;
  /**
   * `POST /v1/blueprints/{id}/twins` (§6.3) cho màn luyện tập: cùng endpoint với `createTwinBatch` nhưng thân trả về
   * (union `TwinQuestion | TheoryTwin`) được kiểm dạng thành từng câu (câu sai dạng bị bỏ và được đếm).
   */
  generatePracticeBatch: (
    blueprintId: string,
    payload: TwinBatchRequest,
    idempotencyKey: string | null,
  ) => Promise<PracticeBatch>;
  /** `POST /v1/twins/{twin_id}/essay-grade` (§6.9; chưa có ở backend), đã kiểm dạng. */
  gradeEssay: (twinId: string, answerText: string) => Promise<EssayGradeView>;
  /** `GET /v1/vault/chunks` (§6.6) cho màn luyện tập (tìm văn bản nguồn của trích dẫn), đã kiểm dạng. */
  listVaultChunks: (query: ChunkQuery) => Promise<ChunkPageView>;
  /** `POST /v1/subjects` (§6.8): 201 `Subject`, đã kiểm dạng. */
  createSubject: (payload: SubjectCreate) => Promise<Subject>;
  /** `GET /v1/documents?subject_code=` (§6.5), đã kiểm dạng; mới nhất trước. */
  listDocuments: (subjectCode: string) => Promise<DocumentSummaryView[]>;
  /** `POST /v1/documents` (§6.5): 202 ⇒ `created = true`; 200 (cùng nội dung, cùng môn) ⇒ `created = false`. */
  importDocument: (input: DocumentImportInput) => Promise<DocumentImportOutcome>;
  /** `GET /v1/documents/{document_id}` (§6.5), dùng để hỏi trạng thái khi đang đọc. */
  getDocument: (documentId: string) => Promise<DocumentView>;
  /** `GET /v1/documents/{document_id}/pages/{page_number}` (§6.5). */
  getDocumentPage: (documentId: string, pageNumber: number) => Promise<PageView>;
  /** `PATCH /v1/documents/{document_id}/pages/{page_number}/lines` (§6.5; chưa có ở backend của nhánh). */
  patchPageLines: (
    documentId: string,
    pageNumber: number,
    expectedVersion: number,
    edits: LineEdit[],
    appendedLines: string[],
  ) => Promise<PageView>;
  /** `POST /v1/documents/{document_id}/pages/{page_number}/confirm` (§6.5; chưa có ở backend của nhánh). */
  confirmPage: (documentId: string, pageNumber: number, expectedVersion: number) => Promise<PageView>;
  /**
   * `POST /v1/documents/{document_id}/pages/{page_number}/restore` (§6.5, GĐ0 0D; đường dẫn là đề xuất suy ra, chờ
   * duyệt): hoàn tác bỏ mềm, trả trang mới (`version` + 1). Gọi trên trang không bỏ mềm trả 200, không đổi gì.
   */
  restorePage: (documentId: string, pageNumber: number, expectedVersion: number) => Promise<PageView>;
  /** `POST /v1/documents/{document_id}/confirm` (§6.5; chưa có ở backend của nhánh): chỉ xác nhận trang `ok`. */
  confirmDocument: (documentId: string) => Promise<DocumentView>;
  /**
   * `GET /v1/documents/{document_id}` (§6.5) cho màn kết quả nhập: `DocumentImport` với số đếm, lý do và `proposal_job`
   * (T1e; field chưa có ở `holocron.ts`), đã kiểm dạng với mặc định an toàn.
   */
  getImportResult: (documentId: string) => Promise<ImportResultView>;
  /**
   * `POST /v1/documents/{document_id}/confirm` (§6.5) cho nút "Chấp nhận tài liệu": cùng endpoint với
   * `confirmDocument` nhưng trả cả field T1e (job vừa tạo). 409 ném `ApiError`; nơi gọi phân loại bằng cách đọc lại.
   */
  confirmImport: (documentId: string) => Promise<ImportResultView>;
  /** `POST /v1/vault/concept-pairs/jobs` (§6.6): 202 `ProposalJob` (`trigger = "manual"`), đã kiểm dạng. */
  startProposalJob: (subjectCode: string, documentId: string) => Promise<ProposalJobView>;
  /** `DELETE /v1/documents/{document_id}` (§6.5): 204. */
  deleteDocument: (documentId: string) => Promise<void>;
  /** `GET /v1/vault/chunks` (§6.6) cho màn Vault, đã kiểm dạng. */
  browseVaultChunks: (filter: ChunkFilter) => Promise<ChunkListView>;
  /** `GET /v1/vault/search` (§6.6), đã kiểm dạng; không có `score`. */
  searchVault: (query: SearchQuery) => Promise<SearchResultView>;
  /**
   * `PATCH /v1/vault/chunks/{chunk_id}/labels` (§6.6): sửa và xác nhận nhãn; body chỉ gồm field đã đổi kèm
   * `expected_version` (dựng ở `lib/vault.ts`). Trả chunk đã kiểm dạng.
   */
  patchChunkLabels: (chunkId: string, change: ChunkLabelChange) => Promise<ChunkView>;
  /** `GET /v1/vault/concept-pairs` (§6.6), đã kiểm dạng; giữ thứ tự backend `(chapter_code, term, pair_id)`. */
  listConceptPairs: (query: ConceptPairQuery) => Promise<ConceptPairList>;
  /** `POST /v1/vault/concept-pairs` (§6.6): 201, cặp `confirmed` với `proposed_by = "user"`. */
  createConceptPair: (chunkId: string, term: string, definitionQuote: string) => Promise<ConceptPair>;
  /** `POST /v1/vault/concept-pairs/{pair_id}/confirm` (§6.6): sửa (nếu có) rồi xác nhận. */
  confirmConceptPair: (pairId: string, expectedVersion: number, edits: ConceptPairEdits) => Promise<ConceptPair>;
  /** `POST /v1/vault/concept-pairs/{pair_id}/reject` (§6.6). */
  rejectConceptPair: (pairId: string, expectedVersion: number) => Promise<ConceptPair>;
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

  createSubject: async (payload) => {
    const raw = await request<unknown>("/v1/subjects", jsonSpec("POST", payload), { context: "subject_create" });
    return validated(parseSubject(raw));
  },

  listDocuments: async (subjectCode) => {
    const params = new URLSearchParams({ subject_code: subjectCode });
    const raw = await request<unknown>(
      `/v1/documents?${params.toString()}`,
      { method: "GET" },
      { context: "document_list" },
    );
    return validated(parseDocumentSummaries(raw));
  },

  importDocument: async (input) => {
    const form = new FormData();
    form.append("file", input.file);
    form.append("subject_code", input.subjectCode);
    form.append("source_kind", input.sourceKind);
    form.append("chapter_code", input.chapterCode);
    if (input.title !== null) form.append("title", input.title);
    const response = await send("/v1/documents", { method: "POST", body: form }, { context: "document_upload" });
    const document = validated(parseDocumentImport(await readJson(response)));
    return { created: response.status === 202, document };
  },

  getDocument: async (documentId) => {
    const raw = await request<unknown>(
      `/v1/documents/${segment(documentId)}`,
      { method: "GET" },
      { context: "document_review" },
    );
    return validated(parseDocumentImport(raw));
  },

  getDocumentPage: async (documentId, pageNumber) => {
    const raw = await request<unknown>(
      `/v1/documents/${segment(documentId)}/pages/${pageNumber}`,
      { method: "GET" },
      { context: "document_review" },
    );
    return validated(parsePageExtraction(raw));
  },

  patchPageLines: async (documentId, pageNumber, expectedVersion, edits, appendedLines) => {
    const payload: PageLinesPatch = {
      editor_id: LOCAL_EDITOR_ID,
      expected_version: expectedVersion,
      edits,
      appended_lines: appendedLines,
    };
    const raw = await request<unknown>(
      `/v1/documents/${segment(documentId)}/pages/${pageNumber}/lines`,
      jsonSpec("PATCH", payload),
      { versioned: true, context: "document_review" },
    );
    return validated(parsePageExtraction(raw));
  },

  confirmPage: async (documentId, pageNumber, expectedVersion) => {
    const payload: PageConfirmRequest = { editor_id: LOCAL_EDITOR_ID, expected_version: expectedVersion };
    const raw = await request<unknown>(
      `/v1/documents/${segment(documentId)}/pages/${pageNumber}/confirm`,
      jsonSpec("POST", payload),
      { versioned: true, context: "page_confirm" },
    );
    return validated(parsePageExtraction(raw));
  },

  restorePage: async (documentId, pageNumber, expectedVersion) => {
    const payload: PageRestoreRequest = { editor_id: LOCAL_EDITOR_ID, expected_version: expectedVersion };
    const raw = await request<unknown>(
      `/v1/documents/${segment(documentId)}/pages/${pageNumber}/restore`,
      jsonSpec("POST", payload),
      { versioned: true, context: "document_review" },
    );
    return validated(parsePageExtraction(raw));
  },

  confirmDocument: async (documentId) => {
    const payload: DocumentConfirmRequest = { editor_id: LOCAL_EDITOR_ID };
    const raw = await request<unknown>(`/v1/documents/${segment(documentId)}/confirm`, jsonSpec("POST", payload), {
      context: "document_confirm",
    });
    return validated(parseDocumentImport(raw));
  },

  getImportResult: async (documentId) => {
    const raw = await request<unknown>(
      `/v1/documents/${segment(documentId)}`,
      { method: "GET" },
      { context: "document_review" },
    );
    return validated(parseImportResult(raw));
  },

  confirmImport: async (documentId) => {
    const payload: DocumentConfirmRequest = { editor_id: LOCAL_EDITOR_ID };
    const raw = await request<unknown>(`/v1/documents/${segment(documentId)}/confirm`, jsonSpec("POST", payload), {
      context: "document_confirm",
    });
    return validated(parseImportResult(raw));
  },

  startProposalJob: async (subjectCode, documentId) => {
    const payload: ProposalJobCreate = { subject_code: subjectCode, document_id: documentId };
    const raw = await request<unknown>("/v1/vault/concept-pairs/jobs", jsonSpec("POST", payload));
    return validated(parseProposalJob(raw));
  },

  deleteDocument: async (documentId) => {
    await send(`/v1/documents/${segment(documentId)}`, { method: "DELETE" }, { context: "document_delete" });
  },

  browseVaultChunks: async (filter) => {
    const raw = await request<unknown>(
      `/v1/vault/chunks?${chunkQueryString(filter)}`,
      { method: "GET" },
      { context: "vault_browse" },
    );
    return validated(parseChunkList(raw));
  },

  searchVault: async (query) => {
    const raw = await request<unknown>(
      `/v1/vault/search?${searchQueryString(query)}`,
      { method: "GET" },
      { context: "vault_browse" },
    );
    return validated(parseSearchHits(raw));
  },

  patchChunkLabels: async (chunkId, change) => {
    const payload: ChunkLabelsPatch = { editor_id: LOCAL_EDITOR_ID, ...change };
    const raw = await request<unknown>(`/v1/vault/chunks/${segment(chunkId)}/labels`, jsonSpec("PATCH", payload), {
      versioned: true,
      context: "vault_labels",
    });
    return validated(parseKnowledgeChunk(raw));
  },

  listConceptPairs: async (query) => {
    const params = new URLSearchParams({ subject_code: query.subjectCode });
    if (query.chapterCode !== null) params.set("chapter_code", query.chapterCode);
    if (query.status !== null) params.set("status", query.status);
    const raw = await request<unknown>(
      `/v1/vault/concept-pairs?${params.toString()}`,
      { method: "GET" },
      { context: "pairs_list" },
    );
    return validated(parseConceptPairList(raw));
  },

  createConceptPair: async (chunkId, term, definitionQuote) => {
    const payload: ConceptPairCreate = {
      editor_id: LOCAL_EDITOR_ID,
      chunk_id: chunkId,
      term,
      definition_quote: definitionQuote,
    };
    const raw = await request<unknown>("/v1/vault/concept-pairs", jsonSpec("POST", payload), {
      context: "pair_create",
    });
    return validated(parseConceptPair(raw));
  },

  confirmConceptPair: async (pairId, expectedVersion, edits) => {
    const payload: ConceptPairConfirm = { editor_id: LOCAL_EDITOR_ID, expected_version: expectedVersion };
    if (edits.term !== undefined) payload.term = edits.term;
    if (edits.definitionQuote !== undefined) payload.definition_quote = edits.definitionQuote;
    const raw = await request<unknown>(
      `/v1/vault/concept-pairs/${segment(pairId)}/confirm`,
      jsonSpec("POST", payload),
      { versioned: true, context: "pair_confirm" },
    );
    return validated(parseConceptPair(raw));
  },

  rejectConceptPair: async (pairId, expectedVersion) => {
    const payload: ConceptPairReject = { editor_id: LOCAL_EDITOR_ID, expected_version: expectedVersion };
    const raw = await request<unknown>(`/v1/vault/concept-pairs/${segment(pairId)}/reject`, jsonSpec("POST", payload), {
      versioned: true,
      context: "pair_reject",
    });
    return validated(parseConceptPair(raw));
  },
};

/**
 * Bộ hàm gọi backend theo cờ mock. Bật mock: endpoint backend chưa có lấy từ `mockApi`; `/v1/runtime` vẫn gọi backend
 * thật (backend không chạy thì vẫn báo không kết nối), chỉ bù `capabilities` và `mock_extractor` còn thiếu. Môn học,
 * tạo bộ đề và sinh câu luôn gọi backend thật, kể cả khi bật mock.
 */
export function createApi(useMocks: boolean): HolocronApi {
  if (!useMocks) return realApi;
  const documents = mockDocumentApi;
  const result = mockImportResultApi;
  return {
    ...realApi,
    ...mockApi,
    getRuntime: () => loadRuntime(fillRuntimeGaps),
    // Các endpoint dưới đây đã có ở backend: chỉ bản ghi do dữ liệu mẫu tạo ra (chọn theo mã) mới lấy fixture.
    // `GET /v1/documents`: danh sách thật (mới nhất trước), thêm các tài liệu mẫu của môn ở cuối để mở được màn xem lại
    // và màn kết quả nhập mẫu.
    listDocuments: async (subjectCode) => [
      ...(await realApi.listDocuments(subjectCode)),
      ...documents.listDocuments(subjectCode),
      ...result.listDocuments(subjectCode),
    ],
    getDocument: (id) =>
      isImportResultMock(id)
        ? result.getDocument(id)
        : isMockDocument(id)
          ? documents.getDocument(id)
          : realApi.getDocument(id),
    getDocumentPage: (id, page) =>
      isImportResultMock(id)
        ? result.getDocumentPage(id, page)
        : isMockDocument(id)
          ? documents.getDocumentPage(id, page)
          : realApi.getDocumentPage(id, page),
    deleteDocument: (id) =>
      isImportResultMock(id)
        ? result.deleteDocument(id)
        : isMockDocument(id)
          ? documents.deleteDocument(id)
          : realApi.deleteDocument(id),
    // Sửa dòng và xác nhận (§6.5) chưa có ở backend của nhánh: tài liệu mẫu dùng fixture; tài liệu thật vẫn gọi
    // backend (backend chưa có endpoint thì trả 404 và giao diện báo backend chưa hỗ trợ).
    patchPageLines: (id, page, version, edits, appended) =>
      isImportResultMock(id)
        ? result.patchPageLines(id, page)
        : isMockDocument(id)
          ? documents.patchPageLines(id, page, version)
          : realApi.patchPageLines(id, page, version, edits, appended),
    confirmPage: (id, page, version) =>
      isImportResultMock(id)
        ? result.confirmPage(id, page, version)
        : isMockDocument(id)
          ? documents.confirmPage(id, page, version)
          : realApi.confirmPage(id, page, version),
    restorePage: (id, page, version) =>
      isImportResultMock(id)
        ? result.restorePage(id, page, version)
        : isMockDocument(id)
          ? documents.restorePage(id, page, version)
          : realApi.restorePage(id, page, version),
    confirmDocument: (id) =>
      isImportResultMock(id)
        ? result.confirmDocument(id)
        : isMockDocument(id)
          ? documents.confirmDocument(id)
          : realApi.confirmDocument(id),
    // Màn kết quả nhập (T1e, T1b): backend chưa trả field mới nên tài liệu mẫu riêng dùng nhà giả; mọi tài liệu khác
    // gọi backend thật (backend cũ không trả field mới thì màn vẫn dựng với mặc định an toàn).
    getImportResult: (id) => (isImportResultMock(id) ? result.getImportResult(id) : realApi.getImportResult(id)),
    confirmImport: (id) => (isImportResultMock(id) ? result.confirmImport(id) : realApi.confirmImport(id)),
    startProposalJob: (subjectCode, id) =>
      isImportResultMock(id) ? result.startProposalJob(subjectCode, id) : realApi.startProposalJob(subjectCode, id),
  };
}

export const api: HolocronApi = createApi(USE_MOCKS);
