/**
 * `getImportResult`, `confirmImport`, `startProposalJob` (T2.2; §6.5, §6.6): đường dẫn, thân gửi đi, kiểm dạng kết
 * quả, 409 thành `ApiError`, và định tuyến mock theo mã tài liệu. `fetch` giả, không gọi mạng.
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import { MOCK_IMPORT_RESULT_DOC_ID, resetImportResultMock } from "@/mocks/import-result";

import { ApiError, createApi, LOCAL_EDITOR_ID, realApi } from "./api";
import { DEFAULT_API_BASE } from "./config";

const DOC_ID = "doc_ffffffffffffffff";

interface FetchCall {
  url: string;
  init: RequestInit | undefined;
}

function stubFetch(status: number, body: unknown): FetchCall[] {
  const calls: FetchCall[] = [];
  vi.stubGlobal("fetch", (input: string, init?: RequestInit) => {
    calls.push({ url: input, init });
    return Promise.resolve(
      new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }),
    );
  });
  return calls;
}

function importBody(extra: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    meta: {
      doc_id: DOC_ID,
      subject_code: "MAU101",
      title: "t",
      source_kind: "textbook",
      page_count: 1,
      has_text_layer: true,
      uploaded_at: "2026-11-01T00:00:00Z",
    },
    filename: "a.pdf",
    size_bytes: 1,
    chapter_code: "ch1",
    status: "confirmed",
    pages: [{ page_number: 1, status: "ok", tier_used: "text_layer", review_reasons: [], confirmed: true }],
    pages_kept: 1,
    pages_dropped: 0,
    pages_needs_review: 0,
    proposal_job: {
      job_id: "pjob_00112233445566ff",
      status: "queued",
      batches_total: 2,
      batches_done: 0,
    },
    proposal_job_absent_reason: null,
    ...extra,
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
  resetImportResultMock();
});

describe("realApi.getImportResult", () => {
  it("GET /v1/documents/{id}; kiểm dạng cả field T1e", async () => {
    const calls = stubFetch(200, importBody());
    const result = await realApi.getImportResult(DOC_ID);
    expect(calls[0]?.init?.method).toBe("GET");
    expect(calls[0]?.url).toBe(`${DEFAULT_API_BASE}/v1/documents/${DOC_ID}`);
    expect(result.pagesKept).toBe(1);
    expect(result.job?.status).toBe("queued");
  });

  it("thân sai dạng ⇒ lỗi phản hồi không hợp lệ, không trả bản hỏng", async () => {
    stubFetch(200, { pages: [] });
    await expect(realApi.getImportResult(DOC_ID)).rejects.toBeInstanceOf(ApiError);
  });
});

describe("realApi.confirmImport", () => {
  it("POST …/confirm gửi editor_id; trả kết quả kèm job vừa tạo", async () => {
    const calls = stubFetch(200, importBody());
    const result = await realApi.confirmImport(DOC_ID);
    expect(calls[0]?.init?.method).toBe("POST");
    expect(calls[0]?.url).toBe(`${DEFAULT_API_BASE}/v1/documents/${DOC_ID}/confirm`);
    expect(JSON.parse(String(calls[0]?.init?.body))).toEqual({ editor_id: LOCAL_EDITOR_ID });
    expect(result.document.status).toBe("confirmed");
    expect(result.job?.jobId).toBe("pjob_00112233445566ff");
  });

  it("409 ⇒ ApiError mang mã HTTP để màn phân loại", async () => {
    stubFetch(409, { detail: "x" });
    const error = await realApi.confirmImport(DOC_ID).catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(ApiError);
    expect(error instanceof ApiError ? error.status : null).toBe(409);
  });
});

describe("realApi.startProposalJob", () => {
  it("POST /v1/vault/concept-pairs/jobs gửi subject_code và document_id; trả job đã kiểm dạng", async () => {
    const calls = stubFetch(202, importBody().proposal_job);
    const job = await realApi.startProposalJob("MAU101", DOC_ID);
    expect(calls[0]?.init?.method).toBe("POST");
    expect(calls[0]?.url).toBe(`${DEFAULT_API_BASE}/v1/vault/concept-pairs/jobs`);
    expect(JSON.parse(String(calls[0]?.init?.body))).toEqual({ subject_code: "MAU101", document_id: DOC_ID });
    expect(job.status).toBe("queued");
  });

  it("409 thiếu khả năng ⇒ ApiError loại capability", async () => {
    stubFetch(409, {
      detail: {
        code: "CAPABILITY_UNAVAILABLE",
        capability: "concept_pair_llm_proposal",
        reason: "llm_not_configured",
        message: "x",
      },
    });
    const error = await realApi.startProposalJob("MAU101", DOC_ID).catch((caught: unknown) => caught);
    expect(error instanceof ApiError ? error.info.kind : null).toBe("capability");
  });

  it("thân sai dạng ⇒ lỗi phản hồi không hợp lệ", async () => {
    stubFetch(202, { nope: true });
    await expect(realApi.startProposalJob("MAU101", DOC_ID)).rejects.toBeInstanceOf(ApiError);
  });
});

describe("createApi(mock): định tuyến theo mã tài liệu", () => {
  it("mã tài liệu mẫu dùng nhà giả, không gọi mạng", async () => {
    const calls = stubFetch(500, {});
    const api = createApi(true);
    const result = await api.getImportResult(MOCK_IMPORT_RESULT_DOC_ID);
    expect(result.document.docId).toBe(MOCK_IMPORT_RESULT_DOC_ID);
    expect(calls).toHaveLength(0);
  });

  it("mã khác gọi backend thật", async () => {
    const calls = stubFetch(200, importBody());
    const api = createApi(true);
    await api.getImportResult(DOC_ID);
    expect(calls).toHaveLength(1);
  });
});
