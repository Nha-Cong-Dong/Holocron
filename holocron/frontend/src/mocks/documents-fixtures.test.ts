/**
 * Tài liệu mẫu phải đúng validator của §5.7 và quy tắc sửa dòng 26/10 của §6.5, và handler giả chỉ chọn fixture
 * dựng sẵn; dữ liệu đi qua đúng bước kiểm dạng như dữ liệu thật.
 */

import { describe, expect, it } from "vitest";

import { parseDocumentImport, parsePageExtraction } from "@/lib/documents-data";
import { CHAPTER_CODE_PATTERN, DOCUMENT_ID_PATTERN } from "@/lib/limits";
import { type PageExtractionPending, REVIEW_REASONS } from "@/types/contract-pending";

import { mockDocumentApi } from "./documents";
import {
  EDITED_PAGES,
  INITIAL_PAGES,
  MOCK_DOCUMENT_META,
  MOCK_DOCUMENT_SCENARIO,
  MOCK_DOCUMENT_SUMMARY,
  MOCK_IMPORT_DOC_ID,
  MOCK_IMPORT_SUBJECT,
  mockDocumentImport,
} from "./fixtures/documents";
import { isMockDocument, resetMockDocumentSessions } from "./store";

function allPages(): PageExtractionPending[] {
  return [...INITIAL_PAGES, ...Object.values(EDITED_PAGES)];
}

describe("PageExtraction mẫu theo validator của §5.7", () => {
  for (const page of allPages()) {
    describe(`trang ${page.page_number} bản ${page.version}`, () => {
      it("needs_review ⇔ review_reasons không rỗng và trang chưa failed", () => {
        expect(page.status === "needs_review").toBe(page.review_reasons.length > 0 && page.status !== "failed");
      });

      it("failure_reason có giá trị ⇔ status = failed; trang lỗi không có dòng", () => {
        expect(page.failure_reason !== null).toBe(page.status === "failed");
        if (page.status === "failed") expect(page.lines).toEqual([]);
      });

      it("line_index liên tục 0..n-1; mỗi token lệch trỏ tới dòng có cờ TOKEN_MISMATCH", () => {
        expect(page.lines.map((line) => line.line_index)).toEqual(page.lines.map((_, index) => index));
        for (const mismatch of page.cross_check.mismatches) {
          expect(page.lines[mismatch.line_index]?.flags).toContain("TOKEN_MISMATCH");
        }
      });

      it("mismatches không rỗng ⇔ cross_check.status = mismatch; lý do sắp theo khai báo, không trùng", () => {
        expect(page.cross_check.mismatches.length > 0).toBe(page.cross_check.status === "mismatch");
        const order = page.review_reasons.map((reason) => REVIEW_REASONS.indexOf(reason));
        expect(order).toEqual([...order].sort((a, b) => a - b));
        expect(new Set(page.review_reasons).size).toBe(page.review_reasons.length);
        expect(order.every((index) => index >= 0)).toBe(true);
      });

      it("điểm nằm trong [0, 1]; định danh đúng mẫu; dòng đã sửa không còn cờ", () => {
        for (const score of [page.text_score, page.ocr_score, page.coverage_ratio, page.ink_outside_text_ratio]) {
          if (score !== null) expect(score >= 0 && score <= 1).toBe(true);
        }
        expect(page.document_id).toMatch(DOCUMENT_ID_PATTERN);
        for (const line of page.lines) {
          if (line.edited_by_human) expect(line.flags).toEqual([]);
        }
      });

      it("đi qua kiểm dạng nguyên vẹn", () => {
        const view = parsePageExtraction(structuredClone(page));
        expect(view?.lines).toHaveLength(page.lines.length);
        expect(view?.hasSkippedLines).toBe(false);
      });
    });
  }

  it("đủ bốn kịch bản của kế hoạch: ok; token lệch và dòng conf thấp; LOW_TEXT_SCORE; failed vì thiếu OCR", () => {
    expect(INITIAL_PAGES.map((page) => page.status)).toEqual(["ok", "needs_review", "needs_review", "failed"]);
    expect(INITIAL_PAGES[1]?.review_reasons).toEqual(["LOW_CONF_LINES", "TOKEN_MISMATCH"]);
    expect(INITIAL_PAGES[2]?.review_reasons).toEqual(["LOW_TEXT_SCORE"]);
    expect(INITIAL_PAGES[3]?.failure_reason).toBe("OCR_ENGINE_MISSING");
  });

  it("bản sau khi sửa: version tăng đúng 1, lý do cấp trang giữ nguyên, lý do dòng bỏ khi hết dòng có cờ", () => {
    for (const edited of Object.values(EDITED_PAGES)) {
      const before = INITIAL_PAGES.find((page) => page.page_number === edited.page_number);
      expect(edited.version).toBe((before?.version ?? 0) + 1);
      expect(edited.review_reasons.includes("LOW_TEXT_SCORE")).toBe(
        before?.review_reasons.includes("LOW_TEXT_SCORE") ?? false,
      );
      const anyLowConf = edited.lines.some((line) => line.flags.includes("LOW_LINE_CONF"));
      expect(edited.review_reasons.includes("LOW_CONF_LINES")).toBe(anyLowConf);
    }
  });
});

describe("DocumentImport và DocumentSummary mẫu", () => {
  it("pages đủ page_count phần tử theo page_number tăng dần; chapter_code đúng mẫu", () => {
    const document = mockDocumentImport(INITIAL_PAGES, "review");
    expect(document.pages.map((page) => page.page_number)).toEqual([1, 2, 3, 4]);
    expect(document.pages).toHaveLength(MOCK_DOCUMENT_META.page_count);
    expect(document.chapter_code).toMatch(CHAPTER_CODE_PATTERN);
    expect(parseDocumentImport(document)?.pages).toHaveLength(4);
  });

  it("số đếm dựng sẵn của DocumentSummary khớp với các trang ban đầu", () => {
    const count = (predicate: (page: PageExtractionPending) => boolean) => INITIAL_PAGES.filter(predicate).length;
    expect(MOCK_DOCUMENT_SUMMARY.pages_pending).toBe(count((page) => page.status === "pending"));
    expect(MOCK_DOCUMENT_SUMMARY.pages_needs_review).toBe(count((page) => page.status === "needs_review"));
    expect(MOCK_DOCUMENT_SUMMARY.pages_failed).toBe(count((page) => page.status === "failed"));
    expect(MOCK_DOCUMENT_SUMMARY.pages_confirmed).toBe(count((page) => page.confirmed));
  });
});

describe("handler giả của tài liệu mẫu", () => {
  it("chỉ mã tài liệu mẫu dùng fixture; danh sách chỉ có ở môn của fixture", () => {
    expect(isMockDocument(MOCK_IMPORT_DOC_ID)).toBe(true);
    expect(isMockDocument("doc_ffffffffffffffff")).toBe(false);
    expect(mockDocumentApi.listDocuments(MOCK_IMPORT_SUBJECT)).toHaveLength(1);
    expect(mockDocumentApi.listDocuments("MAU202")).toEqual([]);
  });

  it("sửa dòng: lệch version ⇒ 409 tải lại; đúng version ⇒ bản sau khi sửa dựng sẵn", async () => {
    await expect(mockDocumentApi.patchPageLines(MOCK_IMPORT_DOC_ID, 2, 9)).rejects.toMatchObject({
      status: 409,
      info: { action: "reload" },
    });
    const edited = await mockDocumentApi.patchPageLines(MOCK_IMPORT_DOC_ID, 2, 1);
    expect(edited).toMatchObject({ version: 2, status: "ok", reviewReasons: [] });
    await expect(mockDocumentApi.patchPageLines(MOCK_IMPORT_DOC_ID, 4, 1)).rejects.toMatchObject({ status: 409 });
  });

  it("kịch bản cố định: sửa dòng trang 2 rồi xác nhận theo lô ⇒ confirmed; bước ngoài kịch bản trả bản hiện tại", async () => {
    resetMockDocumentSessions();
    try {
      await expect(mockDocumentApi.confirmPage(MOCK_IMPORT_DOC_ID, 4, 1)).rejects.toMatchObject({ status: 409 });
      // Ngoài kịch bản: xác nhận theo lô trước khi sửa trang 2, xác nhận riêng trang 1 ⇒ không đổi gì.
      expect((await mockDocumentApi.confirmDocument(MOCK_IMPORT_DOC_ID)).status).toBe("review");
      expect(await mockDocumentApi.confirmPage(MOCK_IMPORT_DOC_ID, 1, 1)).toMatchObject({ confirmed: false });
      const page3 = await mockDocumentApi.confirmPage(MOCK_IMPORT_DOC_ID, 3, 1);
      expect(page3).toMatchObject({ confirmed: true, status: "needs_review" });

      const edited = await mockDocumentApi.patchPageLines(MOCK_IMPORT_DOC_ID, 2, 1);
      expect(edited).toMatchObject({ version: 2, status: "ok" });
      expect((await mockDocumentApi.patchPageLines(MOCK_IMPORT_DOC_ID, 1, 1)).version).toBe(1);
      expect(mockDocumentApi.listDocuments(MOCK_IMPORT_SUBJECT)[0]).toMatchObject({ pagesNeedsReview: 1 });

      const document = await mockDocumentApi.confirmDocument(MOCK_IMPORT_DOC_ID);
      expect(document.pages.map((page) => page.confirmed)).toEqual([true, true, true, false]);
      expect(document.status).toBe("confirmed");
      expect(mockDocumentApi.listDocuments(MOCK_IMPORT_SUBJECT)[0]).toMatchObject({
        status: "confirmed",
        pagesConfirmed: 3,
      });
    } finally {
      resetMockDocumentSessions();
    }
  });

  it("xoá: sau khi xoá thì không còn trong danh sách và đọc lại là 404", async () => {
    await mockDocumentApi.deleteDocument(MOCK_IMPORT_DOC_ID);
    expect(mockDocumentApi.listDocuments(MOCK_IMPORT_SUBJECT)).toEqual([]);
    await expect(mockDocumentApi.getDocument(MOCK_IMPORT_DOC_ID)).rejects.toMatchObject({ status: 404 });
  });
});

describe("kịch bản tài liệu mẫu dựng sẵn", () => {
  for (const [step, snapshot] of Object.entries(MOCK_DOCUMENT_SCENARIO)) {
    it(`bước ${step}: số đếm và status dựng sẵn khớp các trang của bước (§6.5)`, () => {
      const count = (predicate: (page: PageExtractionPending) => boolean) => snapshot.pages.filter(predicate).length;
      expect(snapshot.summary.pages_pending).toBe(count((page) => page.status === "pending"));
      expect(snapshot.summary.pages_needs_review).toBe(count((page) => page.status === "needs_review"));
      expect(snapshot.summary.pages_failed).toBe(count((page) => page.status === "failed"));
      expect(snapshot.summary.pages_confirmed).toBe(count((page) => page.confirmed));
      const textPages = snapshot.pages.filter((page) => page.status !== "failed");
      const allConfirmed = textPages.length > 0 && textPages.every((page) => page.confirmed);
      expect(snapshot.document.status).toBe(allConfirmed ? "confirmed" : "review");
      expect(snapshot.summary.status).toBe(snapshot.document.status);
      expect(snapshot.document.pages.map((page) => page.page_number)).toEqual([1, 2, 3, 4]);
    });
  }

  it("bước cuối là confirmed", () => {
    expect(MOCK_DOCUMENT_SCENARIO.confirmed.document.status).toBe("confirmed");
  });
});
