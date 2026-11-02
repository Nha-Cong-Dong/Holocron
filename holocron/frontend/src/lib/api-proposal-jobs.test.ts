/**
 * `listProposalJobs` (T2.3; §6.6): đường dẫn và tham số `subject_code`, kiểm dạng danh sách, lỗi 422 thành `ApiError`,
 * và định tuyến mock theo môn. `fetch` giả, không gọi mạng.
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import { MOCK_CONCEPT_PROPOSAL_SUBJECT, resetConceptProposalMock } from "@/mocks/concept-proposals";

import { ApiError, createApi, realApi } from "./api";
import { DEFAULT_API_BASE } from "./config";

function stubFetch(status: number, body: unknown): string[] {
  const urls: string[] = [];
  vi.stubGlobal("fetch", (input: string) => {
    urls.push(input);
    return Promise.resolve(
      new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }),
    );
  });
  return urls;
}

const JOB = { job_id: "pjob_00112233445566ff", status: "running", batches_total: 3, batches_done: 1 };

afterEach(() => {
  vi.unstubAllGlobals();
  resetConceptProposalMock();
});

describe("realApi.listProposalJobs", () => {
  it("GET /v1/vault/concept-pairs/jobs?subject_code=…; trả danh sách đã kiểm dạng, mới nhất trước", async () => {
    const urls = stubFetch(200, [JOB, { job_id: "pjob_aa", status: "succeeded" }, { status: "x" }]);
    const jobs = await realApi.listProposalJobs("MAU101");
    expect(urls[0]).toBe(`${DEFAULT_API_BASE}/v1/vault/concept-pairs/jobs?subject_code=MAU101`);
    expect(jobs.map((job) => job.jobId)).toEqual(["pjob_00112233445566ff", "pjob_aa"]);
    expect(jobs[0]?.status).toBe("running");
  });

  it("thân không phải mảng ⇒ lỗi phản hồi không hợp lệ", async () => {
    stubFetch(200, { jobs: [] });
    await expect(realApi.listProposalJobs("MAU101")).rejects.toBeInstanceOf(ApiError);
  });

  it("422 ⇒ ApiError mang mã HTTP", async () => {
    stubFetch(422, { detail: [] });
    const error = await realApi.listProposalJobs("").catch((caught: unknown) => caught);
    expect(error instanceof ApiError ? error.status : null).toBe(422);
  });
});

describe("createApi(true).listProposalJobs", () => {
  it("môn mẫu dùng nhà giả (không gọi mạng); môn khác gọi backend thật", async () => {
    const urls = stubFetch(200, [JOB]);
    vi.stubGlobal("window", { location: { search: "?mock_runtime=llm" } });
    const mocked = createApi(true);
    const fromMock = await mocked.listProposalJobs(MOCK_CONCEPT_PROPOSAL_SUBJECT);
    expect(fromMock[0]?.jobId).toBe("pjob_00112233445566ff");
    expect(urls).toEqual([]);
    const real = await mocked.listProposalJobs("MAU101");
    expect(real).toHaveLength(1);
    expect(urls).toHaveLength(1);
  });
});
