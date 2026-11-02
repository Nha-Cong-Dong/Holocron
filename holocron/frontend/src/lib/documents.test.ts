import { describe, expect, it } from "vitest";

import { DOCUMENT_UPLOAD_MAX_BYTES, LINE_TEXT_MAX_CHARS } from "@/lib/limits";
import { REVIEW_REASONS } from "@/types/contract-pending";

import {
  buildLineEdits,
  CONFIRM_DOCUMENT_BLOCK_MESSAGE_VI,
  CONFIRM_PAGE_BLOCK_MESSAGE_VI,
  canConfirmPage,
  checkDocumentFile,
  checkImportForm,
  checkLineText,
  confirmDocumentBlock,
  confirmPageBlock,
  crossCheckLabel,
  EDIT_BLOCK_MESSAGE_VI,
  failureReasonLabel,
  lineEditBlock,
  lineHighlight,
  mismatchesByLine,
  pageWarnings,
  REVIEW_REASON_LABEL_VI,
  reviewReasonLabel,
  reviewReasonLabels,
  SOURCE_KINDS,
  sourceKindLabel,
  tierLabel,
  UNKNOWN_FAILURE_REASON_VI,
  UNKNOWN_REVIEW_REASON_VI,
} from "./documents";
import type { DocumentView, LineView, PageSummaryView } from "./documents-data";

function line(lineIndex: number, text: string, flags: string[] = []): LineView {
  return { lineIndex, text, conf: 0.9, engine: "tesseract", flags, editedByHuman: false };
}

/** Nhãn hiển thị không được lộ mã enum thô (chữ in hoa nối gạch dưới hay mã thường có gạch dưới). */
function expectNoRawCode(label: string): void {
  expect(label).not.toMatch(/[A-Z]{2,}_[A-Z_]+/);
  expect(label).not.toMatch(/\b[a-z]+_[a-z_]+\b/);
}

describe("nhãn tiếng Việt cho mã của §5.7", () => {
  it("mọi ReviewReason của spec (kể cả LOW_TEXT_SCORE) có nhãn riêng, không lộ mã", () => {
    expect(REVIEW_REASONS).toContain("LOW_TEXT_SCORE");
    expect(Object.keys(REVIEW_REASON_LABEL_VI).sort()).toEqual([...REVIEW_REASONS].sort());
    const labels = REVIEW_REASONS.map(reviewReasonLabel);
    expect(new Set(labels).size).toBe(REVIEW_REASONS.length);
    for (const label of labels) {
      expect(label).not.toBe(UNKNOWN_REVIEW_REASON_VI);
      expectNoRawCode(label);
    }
  });

  it("mã lạ, rỗng hay trùng tên thuộc tính của Object có nhãn chung", () => {
    for (const code of ["NEW_REASON", "", "toString", "__proto__", "constructor"]) {
      expect(reviewReasonLabel(code)).toBe(UNKNOWN_REVIEW_REASON_VI);
    }
    expect(failureReasonLabel("NEW_FAILURE")).toBe(UNKNOWN_FAILURE_REASON_VI);
    expect(failureReasonLabel(null)).toBe(UNKNOWN_FAILURE_REASON_VI);
    expect(tierLabel("tier9")).toBe("Cách đọc khác");
    expect(tierLabel(null)).toBeNull();
    expect(sourceKindLabel("study_guide")).toBe("Loại tài liệu khác");
    expect(crossCheckLabel("x")).toBe("Trạng thái đối chiếu chưa rõ");
  });

  it("danh sách lý do giữ thứ tự backend và gộp nhãn chung trùng nhau", () => {
    expect(reviewReasonLabels(["TOKEN_MISMATCH", "LOW_CONF_LINES"])).toEqual([
      REVIEW_REASON_LABEL_VI.TOKEN_MISMATCH,
      REVIEW_REASON_LABEL_VI.LOW_CONF_LINES,
    ]);
    expect(reviewReasonLabels(["X1", "X2"])).toEqual([UNKNOWN_REVIEW_REASON_VI]);
  });

  it("lý do lỗi trang và loại tài liệu có nhãn không lộ mã", () => {
    for (const code of ["PAGE_TIMEOUT", "PAGE_ERROR", "OCR_ENGINE_MISSING"]) {
      expect(failureReasonLabel(code)).not.toBe(UNKNOWN_FAILURE_REASON_VI);
      expectNoRawCode(failureReasonLabel(code));
    }
    for (const kind of SOURCE_KINDS) expectNoRawCode(sourceKindLabel(kind));
  });
});

describe("pageWarnings", () => {
  const base: PageSummaryView = {
    pageNumber: 1,
    status: "ok",
    tierUsed: "text_layer",
    reviewReasons: [],
    confirmed: false,
  };

  it("trang đọc tốt từ lớp chữ không có cảnh báo", () => {
    expect(pageWarnings(base)).toEqual([]);
  });

  it("trang OCR có nhãn đọc bằng OCR, không kèm câu nhắc ghi cứng; lý do thành nhãn tiếng Việt", () => {
    const warnings = pageWarnings({
      ...base,
      status: "needs_review",
      tierUsed: "ocr",
      reviewReasons: ["CROSS_CHECK_UNAVAILABLE", "LOW_OCR_SCORE"],
    });
    expect(warnings.map((w) => w.tone)).toEqual(["info", "warning", "warning"]);
    expect(warnings[0]?.label).toBe("Đọc bằng OCR (ảnh scan)");
    expect(warnings[1]?.label).toBe(REVIEW_REASON_LABEL_VI.CROSS_CHECK_UNAVAILABLE);
    for (const warning of warnings) expectNoRawCode(warning.label);
  });

  it("trang lỗi: kèm lý do khi đã biết, không thì chỉ cách xem lý do", () => {
    const failed = { ...base, status: "failed" as const, tierUsed: null };
    expect(pageWarnings(failed)[0]).toMatchObject({ tone: "danger", label: "Đọc lỗi; mở trang để xem lý do" });
    const withReason = pageWarnings({ ...failed, failureReason: "OCR_ENGINE_MISSING" });
    expect(withReason[0]?.label).toContain("Tesseract");
  });
});

describe("checkDocumentFile", () => {
  const pdf = { name: "slide.pdf", type: "application/pdf", size: 1024 };

  it("nhận PDF hợp lệ, kể cả MIME rỗng hay octet-stream và đuôi viết hoa", () => {
    expect(checkDocumentFile(pdf)).toEqual({ ok: true });
    expect(checkDocumentFile({ ...pdf, type: "" })).toEqual({ ok: true });
    expect(checkDocumentFile({ ...pdf, type: "application/octet-stream" })).toEqual({ ok: true });
    expect(checkDocumentFile({ ...pdf, name: "SLIDE.PDF" })).toEqual({ ok: true });
    expect(checkDocumentFile({ ...pdf, size: DOCUMENT_UPLOAD_MAX_BYTES })).toEqual({ ok: true });
  });

  it("từ chối file rỗng, quá 50 MiB, sai đuôi, sai MIME, tên quá dài", () => {
    const cases = [
      { ...pdf, size: 0 },
      { ...pdf, size: DOCUMENT_UPLOAD_MAX_BYTES + 1 },
      { ...pdf, name: "anh.png", type: "image/png" },
      { ...pdf, name: "slide.pdf.exe" },
      { ...pdf, type: "image/png" },
      { ...pdf, name: `${"a".repeat(252)}.pdf` },
    ];
    for (const file of cases) {
      const result = checkDocumentFile(file);
      expect(result.ok).toBe(false);
      if (!result.ok) expect(result.message.length).toBeGreaterThan(0);
    }
    expect(checkDocumentFile({ ...pdf, name: `${"a".repeat(251)}.pdf` })).toEqual({ ok: true });
  });

  it("đếm độ dài tên theo code point như backend (chữ ngoài BMP tính một ký tự)", () => {
    const astral = String.fromCodePoint(0x20000);
    expect(checkDocumentFile({ ...pdf, name: `${astral.repeat(251)}.pdf` })).toEqual({ ok: true });
  });
});

describe("checkImportForm", () => {
  const valid = { subjectCode: "MAU101", chapterCode: "ch2.1", sourceKind: "textbook", title: "" };

  it("form hợp lệ không có lỗi", () => {
    expect(checkImportForm(valid)).toEqual({});
  });

  it("chương bắt buộc và đúng mẫu ch1 / ch2.1; loại tài liệu bắt buộc", () => {
    expect(checkImportForm({ ...valid, chapterCode: " " }).chapter).toBeDefined();
    for (const chapter of ["Ch1", "ch", "ch123", "ch1.234", "chương 1"]) {
      expect(checkImportForm({ ...valid, chapterCode: chapter }).chapter).toBeDefined();
    }
    expect(checkImportForm({ ...valid, chapterCode: " ch12 " }).chapter).toBeUndefined();
    expect(checkImportForm({ ...valid, sourceKind: "" }).sourceKind).toBeDefined();
    expect(checkImportForm({ ...valid, sourceKind: "study_guide" }).sourceKind).toBeDefined();
    expect(checkImportForm({ ...valid, subjectCode: "mau101" }).subject).toBeDefined();
    expect(checkImportForm({ ...valid, title: "x".repeat(301) }).title).toBeDefined();
  });
});

describe("điều kiện sửa dòng và xác nhận (§6.5)", () => {
  const lines = [line(0, "Dòng một")];

  it("chỉ sửa được trang ok hoặc needs_review chưa xác nhận", () => {
    expect(lineEditBlock({ status: "ok", confirmed: false })).toBeNull();
    expect(lineEditBlock({ status: "needs_review", confirmed: false })).toBeNull();
    expect(lineEditBlock({ status: "failed", confirmed: false })).toBe("failed");
    expect(lineEditBlock({ status: "pending", confirmed: false })).toBe("pending");
    expect(lineEditBlock({ status: "ok", confirmed: true })).toBe("confirmed");
    expect(lineEditBlock({ status: "unknown", confirmed: false })).toBe("unknown");
    expect(EDIT_BLOCK_MESSAGE_VI.failed).toContain("xoá tài liệu");
  });

  it("xác nhận trang cần có ít nhất một dòng có chữ", () => {
    expect(canConfirmPage({ status: "needs_review", confirmed: false, lines })).toBe(true);
    expect(confirmPageBlock({ status: "ok", confirmed: false, lines: [line(0, "  ")] })).toBe("no_text");
    expect(confirmPageBlock({ status: "ok", confirmed: false, lines: [] })).toBe("no_text");
    expect(confirmPageBlock({ status: "failed", confirmed: false, lines })).toBe("failed");
    expect(confirmPageBlock({ status: "ok", confirmed: true, lines })).toBe("confirmed");
    for (const message of Object.values(CONFIRM_PAGE_BLOCK_MESSAGE_VI)) expect(message.length).toBeGreaterThan(0);
  });

  it("lý do 'còn dòng chưa lưu' có câu nhắc đúng chữ và không suy từ dữ liệu trang", () => {
    expect(CONFIRM_PAGE_BLOCK_MESSAGE_VI.unsaved_lines).toBe("Còn dòng chưa lưu. Lưu hoặc xoá trước khi xác nhận");
    expect(confirmPageBlock({ status: "needs_review", confirmed: false, lines })).toBeNull();
    expect(CONFIRM_PAGE_BLOCK_MESSAGE_VI.no_text).toContain("chưa có dòng nào có chữ");
    expect(CONFIRM_PAGE_BLOCK_MESSAGE_VI.confirmed).toBe("Trang đã được xác nhận.");
  });

  it("xác nhận tài liệu: còn trang đang đọc thì chặn; chỉ bật khi còn trang ok chưa xác nhận", () => {
    const page = (status: PageSummaryView["status"], confirmed = false): PageSummaryView => ({
      pageNumber: 1,
      status,
      tierUsed: null,
      reviewReasons: [],
      confirmed,
    });
    const doc = (status: DocumentView["status"], pages: PageSummaryView[]) => ({ status, pages });
    expect(confirmDocumentBlock(doc("processing", [page("ok")]))).toBe("processing");
    expect(confirmDocumentBlock(doc("review", [page("ok"), page("pending")]))).toBe("processing");
    expect(confirmDocumentBlock(doc("review", [page("ok"), page("needs_review")]))).toBeNull();
    expect(confirmDocumentBlock(doc("review", [page("ok", true), page("needs_review"), page("failed")]))).toBe(
      "nothing_to_confirm",
    );
    expect(CONFIRM_DOCUMENT_BLOCK_MESSAGE_VI.nothing_to_confirm).toContain("từng trang");
  });
});

describe("buildLineEdits", () => {
  const lines = [line(0, "Một"), line(1, "Hai"), line(2, "Ba")];

  it("chỉ gửi dòng đã đổi, sắp theo line_index, bỏ chỉ số không có trên trang", () => {
    const drafts = new Map<number, string>([
      [2, "Ba đã sửa"],
      [0, "Một"],
      [7, "không có"],
      [1, ""],
    ]);
    expect(buildLineEdits(lines, drafts)).toEqual([
      { line_index: 1, text: "" },
      { line_index: 2, text: "Ba đã sửa" },
    ]);
  });

  it("so và gửi sau NFC: bản nháp NFD cùng chữ không tính là đổi", () => {
    const viLines = [line(0, "Kiểm tra".normalize("NFC"))];
    expect(buildLineEdits(viLines, new Map([[0, "Kiểm tra".normalize("NFD")]]))).toEqual([]);
    const edits = buildLineEdits(viLines, new Map([[0, "Kiểm tra lại".normalize("NFD")]]));
    expect(edits[0]?.text).toBe("Kiểm tra lại".normalize("NFC"));
  });

  it("checkLineText giới hạn 2000 ký tự", () => {
    expect(checkLineText("a".repeat(LINE_TEXT_MAX_CHARS))).toBeNull();
    expect(checkLineText("a".repeat(LINE_TEXT_MAX_CHARS + 1))).not.toBeNull();
  });
});

describe("tô dòng theo dữ liệu backend", () => {
  it("dòng conf thấp theo cờ LOW_LINE_CONF, không tự so conf với ngưỡng", () => {
    const low = { ...line(0, "mờ", ["LOW_LINE_CONF"]), conf: 0.95 };
    const unflagged = { ...line(1, "rõ"), conf: 0.1 };
    const byLine = mismatchesByLine([]);
    expect(lineHighlight(low, byLine).lowConf).toBe(true);
    expect(lineHighlight(unflagged, byLine).lowConf).toBe(false);
  });

  it("token lệch theo cờ hoặc theo cross_check.mismatches", () => {
    const byLine = mismatchesByLine([
      { lineIndex: 2, primaryValue: "7", secondaryValue: "1" },
      { lineIndex: 2, primaryValue: "12", secondaryValue: null },
    ]);
    expect(byLine.get(2)?.map((m) => m.primaryValue)).toEqual(["7", "12"]);
    expect(lineHighlight(line(2, "x"), byLine).mismatch).toBe(true);
    expect(lineHighlight(line(3, "x", ["TOKEN_MISMATCH"]), byLine).mismatch).toBe(true);
    const labels = lineHighlight(line(4, "x", ["HIGH_LINE_CER", "NEW_FLAG"]), byLine).labels;
    expect(labels).toHaveLength(2);
    for (const label of labels) expectNoRawCode(label);
  });
});

describe("thứ tự lý do khoá khi trang đã xác nhận mà trạng thái lạ hoặc lỗi", () => {
  const lines = [line(0, "Dòng một")];

  it("lý do theo trạng thái trang đứng trước 'đã xác nhận'", () => {
    expect(lineEditBlock({ status: "unknown", confirmed: true })).toBe("unknown");
    expect(lineEditBlock({ status: "failed", confirmed: true })).toBe("failed");
    expect(lineEditBlock({ status: "pending", confirmed: true })).toBe("pending");
    expect(confirmPageBlock({ status: "unknown", confirmed: true, lines })).toBe("unknown");
    expect(confirmPageBlock({ status: "failed", confirmed: true, lines })).toBe("failed");
  });
});

describe("canConfirmPage và thông điệp khoá sửa", () => {
  const lines = [line(0, "Dòng một")];

  it("canConfirmPage false với trang failed, pending, unknown và trang ok đã xác nhận", () => {
    for (const status of ["failed", "pending", "unknown"] as const) {
      expect(canConfirmPage({ status, confirmed: false, lines })).toBe(false);
    }
    expect(canConfirmPage({ status: "ok", confirmed: true, lines })).toBe(false);
    expect(canConfirmPage({ status: "ok", confirmed: false, lines: [] })).toBe(false);
    expect(canConfirmPage({ status: "ok", confirmed: false, lines })).toBe(true);
  });

  it("EDIT_BLOCK_MESSAGE_VI: các thông điệp đôi một khác nhau; `confirmed` nói trang đã xác nhận", () => {
    const messages = Object.values(EDIT_BLOCK_MESSAGE_VI);
    expect(new Set(messages).size).toBe(messages.length);
    for (const message of messages) expect(message.trim().length).toBeGreaterThan(0);
    expect(EDIT_BLOCK_MESSAGE_VI.confirmed).toContain("Trang đã xác nhận");
  });
});
