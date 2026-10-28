import { describe, expect, it } from "vitest";

import { FAILURE_REASON_LABEL_VI, failureReasonLabel, pageWarnings, UNKNOWN_FAILURE_REASON_VI } from "@/lib/documents";
import { parsePageExtraction } from "@/lib/documents-data";

// Trang vượt giới hạn dòng của Knowledge Vault thành `failed` với `TOO_MANY_LINES` (câu 18 (a), duyệt 2026-10-28).
describe("lý do lỗi trang TOO_MANY_LINES", () => {
  it("có nhãn tiếng Việt riêng, không lộ mã, nói rõ các trang khác vẫn xác nhận được", () => {
    const label = failureReasonLabel("TOO_MANY_LINES");
    expect(label).toBe(FAILURE_REASON_LABEL_VI.TOO_MANY_LINES);
    expect(label).not.toBe(UNKNOWN_FAILURE_REASON_VI);
    expect(label).not.toContain("TOO_MANY_LINES");
    expect(label).toContain("quá nhiều dòng");
    expect(label).toContain("các trang khác vẫn xác nhận");
  });

  it("cảnh báo của trang lỗi hiện nhãn đó", () => {
    const warnings = pageWarnings({
      status: "failed",
      tierUsed: null,
      reviewReasons: [],
      failureReason: "TOO_MANY_LINES",
    });
    expect(warnings[0]).toMatchObject({ tone: "danger", label: `Đọc lỗi: ${FAILURE_REASON_LABEL_VI.TOO_MANY_LINES}` });
  });

  it("trang từ backend mang mã này được đọc thành trang lỗi kèm lý do", () => {
    const page = parsePageExtraction({
      document_id: "doc_0123456789abcdef",
      page_number: 1,
      version: 2,
      status: "failed",
      tier_used: null,
      text_score: 0.95,
      ocr_score: null,
      coverage_ratio: null,
      ink_outside_text_ratio: null,
      has_figure: false,
      has_table: false,
      has_formula: false,
      lines: [],
      tables: [],
      cross_check: { status: "not_applicable", secondary_engine: null, mismatches: [] },
      review_reasons: [],
      failure_reason: "TOO_MANY_LINES",
      confirmed: false,
      confirmed_at: null,
    });
    expect(page?.status).toBe("failed");
    expect(page?.failureReason).toBe("TOO_MANY_LINES");
    expect(failureReasonLabel(page?.failureReason ?? null)).toBe(FAILURE_REASON_LABEL_VI.TOO_MANY_LINES);
  });
});
