import { describe, expect, it } from "vitest";

import { parseDocumentImport, parseDocumentSummaries, parsePageExtraction } from "./documents-data";

const META = {
  doc_id: "doc_0123456789abcdef",
  subject_code: "MAU101",
  title: "Tài liệu",
  source_kind: "textbook",
  sha256: "a".repeat(64),
  page_count: 2,
  has_text_layer: true,
  uploaded_at: "2026-10-26T08:00:00Z",
};

const IMPORT = {
  meta: META,
  filename: "tai-lieu.pdf",
  size_bytes: 2048,
  output_language: "vi",
  chapter_code: "ch1",
  status: "processing",
  pages: [
    { page_number: 1, status: "ok", tier_used: "text_layer", review_reasons: [], confirmed: false },
    { page_number: 2, status: "pending", tier_used: null, review_reasons: [], confirmed: false },
  ],
};

const PAGE = {
  document_id: "doc_0123456789abcdef",
  page_number: 2,
  version: 3,
  status: "needs_review",
  tier_used: "ocr",
  text_score: 0.3,
  ocr_score: 0.81,
  coverage_ratio: 0.9,
  ink_outside_text_ratio: 0.02,
  has_figure: false,
  has_table: true,
  has_formula: false,
  lines: [
    {
      line_index: 0,
      text: "Dòng một",
      bbox: [0.1, 0.1, 0.9, 0.15],
      conf: 0.95,
      engine: "tesseract",
      flags: [],
      edited_by_human: false,
    },
    {
      line_index: 1,
      text: "Giá trị 7",
      bbox: null,
      conf: 0.6,
      engine: "tesseract",
      flags: ["TOKEN_MISMATCH"],
      edited_by_human: false,
    },
  ],
  tables: [],
  cross_check: {
    status: "mismatch",
    secondary_engine: "rapidocr",
    mismatches: [{ line_index: 1, primary_value: "7", secondary_value: "1" }],
  },
  review_reasons: ["TOKEN_MISMATCH"],
  failure_reason: null,
  confirmed: false,
  confirmed_at: null,
};

describe("parseDocumentImport", () => {
  it("giữ nguyên số backend trả và chuyển sang dạng hiển thị", () => {
    const doc = parseDocumentImport(IMPORT);
    expect(doc).toMatchObject({
      docId: META.doc_id,
      pageCount: 2,
      sizeBytes: 2048,
      chapterCode: "ch1",
      status: "processing",
    });
    expect(doc?.pages.map((p) => p.status)).toEqual(["ok", "pending"]);
  });

  it("backend chưa trả chapter_code (bản của nhánh) vẫn đọc được, chapterCode = null", () => {
    const { chapter_code: _omit, ...withoutChapter } = IMPORT;
    expect(parseDocumentImport(withoutChapter)?.chapterCode).toBeNull();
    expect(parseDocumentImport({ ...IMPORT, chapter_code: "Chương 1" })?.chapterCode).toBeNull();
  });

  it("mã lạ không làm sập: status lạ thành unknown, mã lý do lạ được giữ để gắn nhãn chung", () => {
    const doc = parseDocumentImport({
      ...IMPORT,
      status: "archived",
      pages: [{ page_number: 1, status: "queued", tier_used: "tier9", review_reasons: ["NEW", 5, "NEW"] }, null],
    });
    expect(doc?.status).toBe("unknown");
    expect(doc?.pages).toEqual([
      { pageNumber: 1, status: "unknown", tierUsed: "tier9", reviewReasons: ["NEW"], confirmed: false },
    ]);
  });

  it("thiếu meta, filename hay pages thì không nhận", () => {
    expect(parseDocumentImport(null)).toBeNull();
    expect(parseDocumentImport("x")).toBeNull();
    expect(parseDocumentImport({ ...IMPORT, meta: { ...META, page_count: 0 } })).toBeNull();
    expect(parseDocumentImport({ ...IMPORT, filename: "" })).toBeNull();
    expect(parseDocumentImport({ ...IMPORT, pages: "hỏng" })).toBeNull();
  });
});

describe("parseDocumentSummaries", () => {
  const summary = {
    meta: META,
    status: "review",
    pages_pending: 0,
    pages_needs_review: 1,
    pages_failed: 0,
    pages_confirmed: 1,
  };

  it("bỏ phần tử hỏng hay trùng, giữ thứ tự backend", () => {
    const other = { ...summary, meta: { ...META, doc_id: "doc_fedcba9876543210" } };
    const list = parseDocumentSummaries([summary, { ...summary, pages_failed: -1 }, 7, other, summary]);
    expect(list?.map((s) => s.docId)).toEqual([META.doc_id, "doc_fedcba9876543210"]);
    expect(list?.[0]).toMatchObject({ pagesNeedsReview: 1, pagesConfirmed: 1 });
  });

  it("thân không phải mảng thì không nhận", () => {
    expect(parseDocumentSummaries({ items: [] })).toBeNull();
  });
});

describe("parsePageExtraction", () => {
  it("đọc dòng, cờ, token lệch và điểm như backend trả", () => {
    const page = parsePageExtraction(PAGE);
    expect(page).toMatchObject({ version: 3, status: "needs_review", ocrScore: 0.81, hasTable: true });
    expect(page?.lines.map((l) => l.flags)).toEqual([[], ["TOKEN_MISMATCH"]]);
    expect(page?.crossCheck.mismatches).toEqual([{ lineIndex: 1, primaryValue: "7", secondaryValue: "1" }]);
    expect(page?.hasSkippedLines).toBe(false);
  });

  it("dòng hỏng hoặc trùng line_index bị bỏ và được báo; điểm ngoài [0, 1] thành null", () => {
    const page = parsePageExtraction({
      ...PAGE,
      ocr_score: 1.7,
      text_score: "0.3",
      lines: [...PAGE.lines, { line_index: 1, text: "trùng" }, { text: "thiếu chỉ số" }],
    });
    expect(page?.lines).toHaveLength(2);
    expect(page?.hasSkippedLines).toBe(true);
    expect(page?.ocrScore).toBeNull();
    expect(page?.textScore).toBeNull();
  });

  it("trang lỗi: giữ failure_reason dạng chuỗi kể cả mã lạ", () => {
    const failed = parsePageExtraction({ ...PAGE, status: "failed", failure_reason: "NEW_FAILURE", lines: [] });
    expect(failed).toMatchObject({ status: "failed", failureReason: "NEW_FAILURE", lines: [] });
  });

  it("thiếu version, lines hay cross_check thì không nhận", () => {
    expect(parsePageExtraction({ ...PAGE, version: 0 })).toBeNull();
    expect(parsePageExtraction({ ...PAGE, lines: null })).toBeNull();
    expect(parsePageExtraction({ ...PAGE, cross_check: null })).toBeNull();
    expect(parsePageExtraction([])).toBeNull();
  });
});
