/**
 * Tài liệu mẫu cho bộ nhập PDF (M4, M5 của kế hoạch UI; spec §5.7, §6.5). Văn bản tự viết, không lấy từ tài liệu có
 * bản quyền; mã môn trung tính. Mọi con số (điểm, `version`, số đếm) là giá trị dựng sẵn theo contract, handler giả
 * không tính lại số nào: sửa dòng trả bản "sau khi sửa" dựng sẵn, xác nhận chỉ đặt `confirmed` và mốc thời gian cố
 * định. Có test theo validator của §5.7 (`mocks/documents-fixtures.test.ts`).
 *
 * Bốn trang: (1) `ok`, đọc từ lớp chữ; (2) `needs_review`, OCR, có dòng conf thấp và token lệch; (3) `needs_review` vì
 * `LOW_TEXT_SCORE`; (4) `failed` vì `OCR_ENGINE_MISSING`.
 */

import type { DocumentImportPending, PageExtractionPending, PageSummaryPending } from "@/types/contract-pending";
import type { DocumentMeta, DocumentSummary, ExtractedLine } from "@/types/holocron";

export const MOCK_IMPORT_DOC_ID = "doc_5e2a7c9d1b3f4a68";

export const MOCK_IMPORT_SUBJECT = "MAU101";

/** Mốc thời gian cố định của mọi lần xác nhận trong dữ liệu mẫu. */
export const MOCK_CONFIRMED_AT = "2026-10-26T09:00:00Z";

export const MOCK_DOCUMENT_META: DocumentMeta = {
  doc_id: MOCK_IMPORT_DOC_ID,
  subject_code: MOCK_IMPORT_SUBJECT,
  title: "Tài liệu mẫu: Lịch ôn tập",
  source_kind: "lecture_slides",
  sha256: "5e2a7c9d1b3f4a68c0d1e2f3a4b5c6d7e8f9a0b1c2d3e4f5a6b7c8d9e0f1a2b3",
  page_count: 4,
  has_text_layer: true,
  uploaded_at: "2026-10-26T08:00:00Z",
};

function line(
  lineIndex: number,
  text: string,
  conf: number,
  engine: ExtractedLine["engine"],
  flags: ExtractedLine["flags"] = [],
  editedByHuman = false,
): ExtractedLine {
  const top = 0.08 + lineIndex * 0.06;
  return {
    line_index: lineIndex,
    text,
    bbox: [0.08, top, 0.92, top + 0.04],
    conf,
    engine,
    flags,
    edited_by_human: editedByHuman,
  };
}

const PAGE_1: PageExtractionPending = {
  document_id: MOCK_IMPORT_DOC_ID,
  page_number: 1,
  version: 1,
  status: "ok",
  tier_used: "text_layer",
  text_score: 0.97,
  ocr_score: null,
  coverage_ratio: null,
  ink_outside_text_ratio: null,
  has_figure: false,
  has_table: false,
  has_formula: false,
  lines: [
    line(0, "Bài 3. Lập lịch ôn tập", 0.97, "text_layer"),
    line(1, "Lịch ôn tập là bảng ghi các ngày ôn lại một nội dung sau lần học đầu tiên.", 0.97, "text_layer"),
    line(2, "Khoảng cách giữa hai lần ôn tăng dần theo thời gian.", 0.97, "text_layer"),
  ],
  tables: [],
  cross_check: { status: "not_applicable", secondary_engine: null, mismatches: [] },
  review_reasons: [],
  failure_reason: null,
  confirmed: false,
  confirmed_at: null,
};

const PAGE_2: PageExtractionPending = {
  document_id: MOCK_IMPORT_DOC_ID,
  page_number: 2,
  version: 1,
  status: "needs_review",
  tier_used: "ocr",
  text_score: 0.31,
  ocr_score: 0.86,
  coverage_ratio: 1,
  ink_outside_text_ratio: 0.04,
  has_figure: false,
  has_table: false,
  has_formula: false,
  lines: [
    line(0, "Ví dụ một lịch ôn", 0.94, "tesseract"),
    line(1, "Lần ôn thứ hai sau 3 ngày, lần thứ ba sau 7 ngày.", 0.9, "tesseract", ["TOKEN_MISMATCH"]),
    line(2, "Ghi chú tay: ôn lại phần tự kiếm tra", 0.52, "tesseract", ["LOW_LINE_CONF"]),
  ],
  tables: [],
  cross_check: {
    status: "mismatch",
    secondary_engine: "rapidocr",
    mismatches: [{ line_index: 1, primary_value: "7", secondary_value: "1" }],
  },
  review_reasons: ["LOW_CONF_LINES", "TOKEN_MISMATCH"],
  failure_reason: null,
  confirmed: false,
  confirmed_at: null,
};

/**
 * Trang 2 "sau khi sửa" dựng sẵn theo quy tắc 26/10 của §6.5: hai dòng có cờ đã được người dùng sửa
 * (`edited_by_human`, xoá `flags`), token lệch của dòng đó bị bỏ; backend tính lại lý do và trạng thái (ở đây là bản
 * dựng sẵn của kết quả đó), `version` tăng 1.
 */
const PAGE_2_EDITED: PageExtractionPending = {
  ...PAGE_2,
  version: 2,
  status: "ok",
  lines: [
    line(0, "Ví dụ một lịch ôn", 0.94, "tesseract"),
    line(1, "Lần ôn thứ hai sau 3 ngày, lần thứ ba sau 7 ngày.", 0.9, "tesseract", [], true),
    line(2, "Ghi chú tay: ôn lại phần tự kiểm tra", 0.52, "tesseract", [], true),
  ],
  cross_check: { status: "ok", secondary_engine: "rapidocr", mismatches: [] },
  review_reasons: [],
};

const PAGE_3: PageExtractionPending = {
  document_id: MOCK_IMPORT_DOC_ID,
  page_number: 3,
  version: 1,
  status: "needs_review",
  tier_used: "text_layer",
  text_score: 0.82,
  ocr_score: null,
  coverage_ratio: null,
  ink_outside_text_ratio: null,
  has_figure: false,
  has_table: false,
  has_formula: false,
  lines: [
    line(0, "Mẹo: đặt nhắc lịch ngay sau buổi học đầu tiên.", 0.82, "text_layer"),
    line(1, "Ôn đúng hẹn quan trọng hơn ôn lâu.", 0.82, "text_layer"),
  ],
  tables: [],
  cross_check: { status: "not_applicable", secondary_engine: null, mismatches: [] },
  review_reasons: ["LOW_TEXT_SCORE"],
  failure_reason: null,
  confirmed: false,
  confirmed_at: null,
};

/** Trang 3 "sau khi sửa": dòng 0 do người dùng sửa; lý do cấp trang `LOW_TEXT_SCORE` giữ nguyên (§6.5). */
const PAGE_3_EDITED: PageExtractionPending = {
  ...PAGE_3,
  version: 2,
  lines: [
    line(0, "Mẹo: đặt nhắc lịch ngay sau buổi học đầu tiên.", 0.82, "text_layer", [], true),
    line(1, "Ôn đúng hẹn quan trọng hơn ôn lâu.", 0.82, "text_layer"),
  ],
};

/** Trang 1 "sau khi sửa": dòng 0 do người dùng sửa; trang vẫn `ok`. */
const PAGE_1_EDITED: PageExtractionPending = {
  ...PAGE_1,
  version: 2,
  lines: [
    line(0, "Bài 3. Lập lịch ôn tập", 0.97, "text_layer", [], true),
    line(1, "Lịch ôn tập là bảng ghi các ngày ôn lại một nội dung sau lần học đầu tiên.", 0.97, "text_layer"),
    line(2, "Khoảng cách giữa hai lần ôn tăng dần theo thời gian.", 0.97, "text_layer"),
  ],
};

const PAGE_4: PageExtractionPending = {
  document_id: MOCK_IMPORT_DOC_ID,
  page_number: 4,
  version: 1,
  status: "failed",
  tier_used: null,
  text_score: 0.08,
  ocr_score: null,
  coverage_ratio: null,
  ink_outside_text_ratio: null,
  has_figure: false,
  has_table: false,
  has_formula: false,
  lines: [],
  tables: [],
  cross_check: { status: "unavailable", secondary_engine: null, mismatches: [] },
  review_reasons: [],
  failure_reason: "OCR_ENGINE_MISSING",
  confirmed: false,
  confirmed_at: null,
};

export const INITIAL_PAGES: readonly PageExtractionPending[] = [PAGE_1, PAGE_2, PAGE_3, PAGE_4];

/** Bản "sau khi sửa" dựng sẵn của từng trang (theo `page_number`); trang lỗi không có vì không sửa được. */
export const EDITED_PAGES: Readonly<Record<number, PageExtractionPending>> = {
  1: PAGE_1_EDITED,
  2: PAGE_2_EDITED,
  3: PAGE_3_EDITED,
};

/** Bản đã xác nhận của một trang: chỉ đặt `confirmed` và mốc thời gian cố định, không đổi field nào khác. */
export function confirmedPage(page: PageExtractionPending): PageExtractionPending {
  return { ...structuredClone(page), confirmed: true, confirmed_at: MOCK_CONFIRMED_AT };
}

/** Tóm tắt một trang (`PageSummary`): chép field, không tính gì. */
export function pageSummaryOf(page: PageExtractionPending): PageSummaryPending {
  return {
    page_number: page.page_number,
    status: page.status,
    tier_used: page.tier_used,
    review_reasons: [...page.review_reasons],
    confirmed: page.confirmed,
  };
}

/** `DocumentImport` của tài liệu mẫu theo các trang hiện tại; `status` truyền vào từ handler. */
export function mockDocumentImport(
  pages: readonly PageExtractionPending[],
  status: DocumentImportPending["status"],
): DocumentImportPending {
  return {
    meta: structuredClone(MOCK_DOCUMENT_META),
    filename: "tai-lieu-mau-lich-on-tap.pdf",
    size_bytes: 182_340,
    output_language: "vi",
    chapter_code: "ch1",
    status,
    pages: pages.map(pageSummaryOf),
  };
}

/**
 * `DocumentSummary` dựng sẵn cho trạng thái ban đầu. Số đếm là giá trị cố định của fixture (có test đối chiếu với
 * `INITIAL_PAGES`); handler giả không đếm lại sau khi sửa hay xác nhận.
 */
export const MOCK_DOCUMENT_SUMMARY: DocumentSummary = {
  meta: MOCK_DOCUMENT_META,
  status: "review",
  pages_pending: 0,
  pages_needs_review: 2,
  pages_failed: 1,
  pages_confirmed: 0,
};
