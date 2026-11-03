/**
 * Nhà giả của màn kết quả nhập (T2.2): tài liệu mẫu đúng contract §5.7, có trang bỏ mềm, khôi phục và xác nhận được LƯU
 * trong phiên, chấp nhận ⇒ job chạy qua các lô; biến thể không LLM theo `?mock_runtime=`.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { parseDocumentSummaries } from "@/lib/documents-data";
import { ApiError } from "@/lib/errors";
import { acceptGate, acceptOutcome, droppedGroups, importSummaryLine, jobHeadline } from "@/lib/import-result";

import {
  isImportResultMock,
  MOCK_IMPORT_RESULT_DOC_ID,
  MOCK_IMPORT_RESULT_SUBJECT,
  mockImportResultApi as mock,
  resetImportResultMock,
} from "./import-result";

const ID = MOCK_IMPORT_RESULT_DOC_ID;

function stubScenario(query: string): void {
  vi.stubGlobal("window", { location: { search: query } });
}

beforeEach(() => {
  resetImportResultMock();
});

afterEach(() => {
  vi.unstubAllGlobals();
  resetImportResultMock();
});

describe("tài liệu mẫu ban đầu", () => {
  it("chỉ nhận đúng mã tài liệu mẫu", () => {
    expect(isImportResultMock(ID)).toBe(true);
    expect(isImportResultMock("doc_0123456789abcdef")).toBe(false);
  });

  it("có trang tự xác nhận, cần xem, bỏ mềm và đọc lỗi; số đếm do nhà giả (backend giả) trả", async () => {
    const result = await mock.getImportResult(ID);
    expect(result.document.status).toBe("review");
    expect(result).toMatchObject({
      pagesKept: 5,
      pagesDropped: 2,
      pagesNeedsReview: 2,
      pagesAutoConfirmed: 2,
      pagesKeptUnconfirmed: 1,
      pagesFailed: 1,
    });
    expect(importSummaryLine(result)).toContain("2 trang tự xác nhận");
    expect(result.reviewReasons.map((group) => [group.reason, group.pages])).toEqual([
      ["LOW_OCR_SCORE", [4, 5]],
      ["FIGURE_ON_SCAN", [5]],
    ]);
    expect(droppedGroups(result).map((group) => group.pages)).toEqual([[6], [8]]);
    expect(result.job).toBeNull();
    expect(result.absentReason).toBe("not_confirmed");
    expect(acceptGate(result)).toBe("enabled");
  });

  it("có ít nhất một trang bỏ mềm đọc được qua getDocumentPage", async () => {
    const page = await mock.getDocumentPage(ID, 6);
    expect(page.dropped).toBe(true);
    expect(page.pageRole).toBe("cover_or_course_nav");
    expect(page.roleEvidence?.rule).toBe("cover_or_course_nav");
  });

  it("xuất hiện ở danh sách tài liệu của môn mẫu (MAU102), không ở môn khác kể cả MAU101", () => {
    const summaries = mock.listDocuments(MOCK_IMPORT_RESULT_SUBJECT);
    expect(summaries.map((item) => item.docId)).toEqual([ID]);
    expect(summaries[0]).toMatchObject({ pagesDropped: 2, pagesNeedsReview: 2, pagesFailed: 1 });
    expect(mock.listDocuments("ZZZ999")).toEqual([]);
    expect(mock.listDocuments("MAU101")).toEqual([]);
    expect(parseDocumentSummaries(summaries)).not.toBeNull();
  });
});

describe("khôi phục trang được lưu trong phiên", () => {
  it("khôi phục trang 6 ⇒ version + 1, hết bỏ mềm, số đếm cập nhật, đọc lại vẫn thế", async () => {
    const before = await mock.getDocumentPage(ID, 6);
    const restored = await mock.restorePage(ID, 6, before.version);
    expect(restored).toMatchObject({ dropped: false, restored: true, version: before.version + 1 });
    expect((await mock.getDocumentPage(ID, 6)).dropped).toBe(false);
    const result = await mock.getImportResult(ID);
    expect(result).toMatchObject({ pagesDropped: 1, pagesKept: 6, pagesKeptUnconfirmed: 2 });
    expect(droppedGroups(result).map((group) => group.pages)).toEqual([[8]]);
  });

  it("lệch version ⇒ 409; trang không bỏ mềm trả nguyên; trang lạ ⇒ 404", async () => {
    const error = await mock.restorePage(ID, 6, 99).catch((caught: unknown) => caught);
    expect(error instanceof ApiError ? error.status : null).toBe(409);
    expect((await mock.restorePage(ID, 1, 1)).dropped).toBe(false);
    const missing = await mock.restorePage(ID, 77, 1).catch((caught: unknown) => caught);
    expect(missing instanceof ApiError ? missing.status : null).toBe(404);
  });

  it("xoá tài liệu ⇒ 404 và biến khỏi danh sách", async () => {
    await mock.deleteDocument(ID);
    expect(mock.listDocuments(MOCK_IMPORT_RESULT_SUBJECT)).toEqual([]);
    const error = await mock.getImportResult(ID).catch((caught: unknown) => caught);
    expect(error instanceof ApiError ? error.status : null).toBe(404);
  });
});

describe("chấp nhận tài liệu", () => {
  it("còn trang cần xem ⇒ 200, vẫn review, trang giữ được xác nhận, chưa có job", async () => {
    const after = await mock.confirmImport(ID);
    expect(after.document.status).toBe("review");
    expect(after).toMatchObject({ pagesKeptUnconfirmed: 0, pagesNeedsReview: 2 });
    expect(after.job).toBeNull();
    const outcome = acceptOutcome(after);
    expect(outcome.kind).toBe("pages_left");
    if (outcome.kind === "pages_left") expect(outcome.firstPage).toBe(4);
    expect(acceptGate(after)).toBe("enabled");
  });

  it("xem lại xong các trang cần xem rồi chấp nhận lần nữa ⇒ confirmed", async () => {
    await mock.confirmImport(ID);
    for (const pageNumber of [4, 5]) {
      const page = await mock.getDocumentPage(ID, pageNumber);
      await mock.confirmPage(ID, pageNumber, page.version);
    }
    const after = await mock.confirmImport(ID);
    expect(after.document.status).toBe("confirmed");
    expect(acceptOutcome(after)).toEqual({ kind: "accepted" });
    expect(acceptGate(after)).toBe("accepted");
  });

  it("khôi phục sau khi chấp nhận ⇒ 409", async () => {
    for (const pageNumber of [4, 5]) {
      const page = await mock.getDocumentPage(ID, pageNumber);
      await mock.confirmPage(ID, pageNumber, page.version);
    }
    await mock.confirmImport(ID);
    const error = await mock.restorePage(ID, 6, 1).catch((caught: unknown) => caught);
    expect(error instanceof ApiError ? error.status : null).toBe(409);
  });
});

describe("biến thể có LLM (?mock_runtime=llm): job chạy qua các lô rồi succeeded", () => {
  async function acceptAll(): Promise<void> {
    for (const pageNumber of [4, 5]) {
      const page = await mock.getDocumentPage(ID, pageNumber);
      await mock.confirmPage(ID, pageNumber, page.version);
    }
  }

  it("job queued ngay sau chấp nhận, rồi tiến từng lô tới succeeded và dừng ở đó", async () => {
    stubScenario("?mock_runtime=llm");
    await acceptAll();
    const confirmed = await mock.confirmImport(ID);
    expect(confirmed.job?.status).toBe("queued");
    expect(confirmed.absentReason).toBeNull();

    const seen: string[] = [];
    for (let i = 0; i < 8; i += 1) {
      const result = await mock.getImportResult(ID);
      seen.push(`${result.job?.status}:${result.job?.batchesDone}`);
    }
    expect(seen.slice(0, 4)).toEqual(["running:1", "running:2", "running:3", "succeeded:4"]);
    expect(new Set(seen.slice(3))).toEqual(new Set(["succeeded:4"]));
    const done = await mock.getImportResult(ID);
    expect(done.job).toMatchObject({ proposedCount: 7, rejectedCount: 3, batchesFailed: 0 });
    expect(done.job?.rejectedByReason).toEqual([
      { reason: "duplicate", count: 1 },
      { reason: "quote_not_verbatim", count: 2 },
    ]);
    if (done.job !== null) expect(jobHeadline(done.job)).toContain("xong");
  });

  it("startProposalJob tạo lại job ở trạng thái queued", async () => {
    stubScenario("?mock_runtime=llm");
    const job = await mock.startProposalJob(MOCK_IMPORT_RESULT_SUBJECT, ID);
    expect(job.status).toBe("queued");
  });
});

describe("biến thể không LLM (mặc định, ?mock_runtime=offline)", () => {
  it("chấp nhận xong không có job; lý do llm_unavailable", async () => {
    for (const pageNumber of [4, 5]) {
      const page = await mock.getDocumentPage(ID, pageNumber);
      await mock.confirmPage(ID, pageNumber, page.version);
    }
    const after = await mock.confirmImport(ID);
    expect(after.document.status).toBe("confirmed");
    expect(after.job).toBeNull();
    expect(after.absentReason).toBe("llm_unavailable");
  });

  it("startProposalJob ⇒ 409 thiếu khả năng", async () => {
    const error = await mock.startProposalJob(MOCK_IMPORT_RESULT_SUBJECT, ID).catch((caught: unknown) => caught);
    expect(error instanceof ApiError ? error.status : null).toBe(409);
    expect(error instanceof ApiError ? error.info.kind : null).toBe("capability");
  });
});
