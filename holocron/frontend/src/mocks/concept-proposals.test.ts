/**
 * Nhà giả của màn khái niệm với cặp đề xuất (T2.3): cặp mẫu có `pre_selected` và `failed_gates`, trích dẫn tìm được trong
 * đoạn (I3), nhận/từ chối lưu trong phiên, 404/409/422 như backend, job chạy qua các lô theo `?mock_runtime=`.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { findQuoteRange } from "@/lib/quote-highlight";

import {
  isConceptProposalMock,
  isConceptProposalPair,
  MOCK_CONCEPT_PROPOSAL_DOC_ID,
  MOCK_CONCEPT_PROPOSAL_SUBJECT,
  mockConceptProposalApi as mock,
  resetConceptProposalMock,
} from "./concept-proposals";

const SUBJECT = MOCK_CONCEPT_PROPOSAL_SUBJECT;

function stubScenario(query: string): void {
  vi.stubGlobal("window", { location: { search: query } });
}

function chunkFilter(subjectCode: string) {
  return { subjectCode, chapterCode: null, labelStatus: null, topicKeys: [], offset: 0, limit: 50 };
}

async function proposed() {
  return (await mock.listConceptPairs(SUBJECT)).pairs.filter((pair) => pair.status === "proposed");
}

beforeEach(() => resetConceptProposalMock());
afterEach(() => {
  vi.unstubAllGlobals();
  resetConceptProposalMock();
});

describe("dữ liệu mẫu", () => {
  it("chỉ nhận đúng môn và đúng mã cặp mẫu", () => {
    expect(isConceptProposalMock(SUBJECT)).toBe(true);
    expect(isConceptProposalMock("MAU101")).toBe(false);
    expect(isConceptProposalPair("pair_cp0000000000000001")).toBe(true);
    expect(isConceptProposalPair("pair_0123456789abcdef")).toBe(false);
  });

  it("môn khác không có cặp, đoạn hay tài liệu mẫu", async () => {
    expect((await mock.listConceptPairs("MAU101")).pairs).toEqual([]);
    expect((await mock.browseVaultChunks(chunkFilter("MAU101"))).chunks).toEqual([]);
    expect(mock.listDocuments("MAU101")).toEqual([]);
    expect(mock.listDocuments(SUBJECT).map((doc) => doc.docId)).toEqual([MOCK_CONCEPT_PROPOSAL_DOC_ID]);
  });

  it("có cặp tích sẵn và cặp cần kiểm tra; pre_selected chỉ khi failed_gates rỗng", async () => {
    const pairs = await proposed();
    expect(pairs.length).toBeGreaterThanOrEqual(4);
    expect(pairs.some((pair) => pair.pre_selected)).toBe(true);
    expect(pairs.some((pair) => pair.failed_gates.length > 0)).toBe(true);
    for (const pair of pairs) {
      if (pair.pre_selected) expect(pair.failed_gates).toEqual([]);
    }
  });

  it("mọi trích dẫn tìm được trong văn bản đoạn của nó (kể cả đoạn lưu dạng NFD)", async () => {
    const chunks = (await mock.browseVaultChunks(chunkFilter(SUBJECT))).chunks;
    expect(chunks.some((chunk) => chunk.rawText !== chunk.rawText.normalize("NFC"))).toBe(true);
    for (const pair of await proposed()) {
      const chunk = chunks.find((item) => item.chunkId === pair.chunk_id);
      expect(chunk, pair.term).toBeDefined();
      expect(findQuoteRange(chunk?.rawText ?? "", pair.definition_quote), pair.term).not.toBeNull();
    }
  });
});

describe("nhận và từ chối", () => {
  it("nhận: cặp thành confirmed, tăng version, gửi tên đã sửa; rời danh sách proposed", async () => {
    const [first] = await proposed();
    if (first === undefined) throw new Error("cần cặp mẫu");
    const done = await mock.confirmConceptPair(first.pair_id, first.version, { term: "Tên mới" });
    expect(done.status).toBe("confirmed");
    expect(done.term).toBe("Tên mới");
    expect(done.version).toBe(first.version + 1);
    expect((await proposed()).some((pair) => pair.pair_id === first.pair_id)).toBe(false);
  });

  it("từ chối: cặp thành rejected", async () => {
    const [first] = await proposed();
    if (first === undefined) throw new Error("cần cặp mẫu");
    expect((await mock.rejectConceptPair(first.pair_id, first.version)).status).toBe("rejected");
  });

  it("gửi lại cặp đã ghi (version cũ) ⇒ 409; mã lạ ⇒ 404", async () => {
    const [first] = await proposed();
    if (first === undefined) throw new Error("cần cặp mẫu");
    await mock.confirmConceptPair(first.pair_id, first.version, {});
    await expect(mock.confirmConceptPair(first.pair_id, first.version, {})).rejects.toMatchObject({ status: 409 });
    await expect(mock.rejectConceptPair(first.pair_id, 1)).rejects.toMatchObject({ status: 409 });
    await expect(mock.confirmConceptPair("pair_cp9999999999999999", 1, {})).rejects.toMatchObject({ status: 404 });
  });

  it("cặp Bài tập 3 luôn trả 422 khi nhận (đoạn trích không khớp)", async () => {
    const exercise = (await proposed()).find((pair) => pair.term === "Bài tập 3");
    if (exercise === undefined) throw new Error("cần cặp mẫu");
    await expect(mock.confirmConceptPair(exercise.pair_id, exercise.version, {})).rejects.toMatchObject({
      status: 422,
    });
  });
});

describe("job đề xuất", () => {
  it("không LLM (mặc định) ⇒ chưa có job; có LLM ⇒ job tiến qua các lô rồi succeeded", async () => {
    stubScenario("");
    expect(await mock.listProposalJobs(SUBJECT)).toEqual([]);
    stubScenario("?mock_runtime=llm");
    const statuses: string[] = [];
    for (let i = 0; i < 4; i += 1) {
      const jobs = await mock.listProposalJobs(SUBJECT);
      statuses.push(jobs[0]?.status ?? "none");
    }
    expect(statuses).toEqual(["queued", "running", "succeeded", "succeeded"]);
  });

  it("môn khác ⇒ không có job; tạo job khi không có LLM ⇒ 409 CapabilityError", async () => {
    stubScenario("?mock_runtime=llm");
    expect(await mock.listProposalJobs("MAU101")).toEqual([]);
    stubScenario("");
    await expect(mock.startProposalJob(SUBJECT)).rejects.toMatchObject({ status: 409 });
  });

  it("tạo job khi có LLM ⇒ job queued, đặt lại tiến độ", async () => {
    stubScenario("?mock_runtime=llm");
    await mock.listProposalJobs(SUBJECT);
    await mock.listProposalJobs(SUBJECT);
    const job = await mock.startProposalJob(SUBJECT);
    expect(job.status).toBe("queued");
  });
});
