/**
 * Logic thuần của màn nhập tài liệu và xem lại trang (kế hoạch UI mục F2; spec §5.7, §6.5):
 * - nhãn tiếng Việt cho mọi mã enum của contract (bảng `Record<…>` để TypeScript bắt thiếu mã; mã lạ ⇒ nhãn chung,
 *   không lộ mã thô);
 * - kiểm file và form phía client trước khi gửi (chỉ để báo sớm; backend vẫn kiểm cuối cùng);
 * - điều kiện bật/tắt nút sửa dòng, xác nhận trang, xác nhận tài liệu theo đúng các mã 409 của §6.5;
 * - dựng `edits` chỉ gồm dòng đã đổi.
 *
 * Không so ngưỡng nào (`LINE_CONF_MIN`, `OCR_SCORE_MIN`…): dòng conf thấp hay token lệch nhận biết qua `flags` và
 * `cross_check.mismatches` do backend tính.
 */

import type { DocumentStatusView, DocumentView, LineView, MismatchView, PageStatusView } from "@/lib/documents-data";
import {
  CHAPTER_CODE_PATTERN,
  DOCUMENT_TITLE_MAX_CHARS,
  DOCUMENT_UPLOAD_MAX_BYTES,
  DOCUMENT_UPLOAD_MAX_MIB,
  FILENAME_MAX_CHARS,
  LINE_TEXT_MAX_CHARS,
  SUBJECT_CODE_PATTERN,
} from "@/lib/limits";
import type { LineEdit, ReviewReasonPending } from "@/types/contract-pending";
import type { CrossCheckStatus, FailureReason, LineEngine, LineFlag, SourceKind, TierUsed } from "@/types/holocron";

// ------------------------------------------------------------------------------------------ nhãn

export const REVIEW_REASON_LABEL_VI: Record<ReviewReasonPending, string> = {
  LOW_TEXT_SCORE: "Lớp chữ của PDF có dấu hiệu lỗi phông hoặc thiếu chữ",
  LOW_OCR_SCORE: "OCR đọc trang này với độ tin cậy thấp",
  LOW_LINE_COVERAGE: "Hai lượt đọc ra số dòng chênh nhau, có thể sót dòng",
  HIGH_INK_OUTSIDE_TEXT: "Có nhiều nét mực ngoài vùng chữ (hình vẽ, chữ viết tay) chưa được đối chiếu",
  FIGURE_ON_SCAN: "Trang scan có hình vẽ; chữ trong hình không được đọc",
  FORMULA_ON_SCAN: "Trang scan có công thức; OCR dễ đọc sai ký hiệu",
  TIER3_UNAVAILABLE: "Trang có bảng, công thức hoặc hình mà bản này chưa đọc riêng được",
  LOW_CONF_LINES: "Có dòng độ tin cậy thấp (chữ mờ hoặc viết tay)",
  TOKEN_MISMATCH: "Hai lượt đọc lệch nhau ở số hoặc mã",
  CROSS_CHECK_UNAVAILABLE: "Không có lượt đọc thứ hai để đối chiếu số",
  HIGH_PAGE_CER: "Hai lượt đọc ra văn bản khác nhau nhiều",
  OCR_ENGINE_MISSING: "Máy chưa có Tesseract nên trang được đọc bằng mô hình đọc ảnh",
};

export const UNKNOWN_REVIEW_REASON_VI = "Trang cần xem kỹ vì một lý do giao diện này chưa nhận ra";

export const FAILURE_REASON_LABEL_VI: Record<FailureReason, string> = {
  PAGE_TIMEOUT: "Đọc trang này quá thời gian cho phép",
  PAGE_ERROR: "Bộ đọc gặp lỗi ở trang này",
  OCR_ENGINE_MISSING: "Trang là ảnh scan nhưng máy chưa có Tesseract (hoặc thiếu dữ liệu tiếng Việt) để đọc",
};

export const UNKNOWN_FAILURE_REASON_VI = "Trang đọc lỗi vì một lý do giao diện này chưa nhận ra";

export const LINE_FLAG_LABEL_VI: Record<LineFlag, string> = {
  LOW_LINE_CONF: "độ tin cậy thấp",
  TOKEN_MISMATCH: "số hoặc mã lệch giữa hai lượt đọc",
  HIGH_LINE_CER: "hai lượt đọc khác nhau nhiều",
};

export const UNKNOWN_LINE_FLAG_VI = "cờ cảnh báo khác";

export const PAGE_STATUS_LABEL_VI: Record<PageStatusView, string> = {
  pending: "Đang đọc",
  ok: "Đọc tốt",
  needs_review: "Cần xem kỹ",
  failed: "Đọc lỗi",
  unknown: "Chưa rõ trạng thái",
};

export const DOCUMENT_STATUS_LABEL_VI: Record<DocumentStatusView, string> = {
  processing: "Đang đọc",
  review: "Chờ xác nhận",
  confirmed: "Đã xác nhận",
  failed: "Không đọc được",
  unknown: "Chưa rõ trạng thái",
};

export const TIER_LABEL_VI: Record<TierUsed, string> = {
  text_layer: "Lớp chữ của PDF",
  ocr: "OCR (ảnh scan)",
  layout: "Đọc theo bố cục",
  vlm: "Mô hình đọc ảnh (VLM)",
};

export const UNKNOWN_TIER_VI = "Cách đọc khác";

export const ENGINE_LABEL_VI: Record<LineEngine, string> = {
  text_layer: "lớp chữ",
  tesseract: "Tesseract",
  vlm: "mô hình đọc ảnh",
  human: "người dùng gõ",
};

export const CROSS_CHECK_LABEL_VI: Record<CrossCheckStatus, string> = {
  ok: "Hai lượt đọc khớp nhau ở số và mã",
  mismatch: "Hai lượt đọc lệch nhau ở số hoặc mã",
  unavailable: "Không có lượt đọc thứ hai để đối chiếu",
  not_applicable: "Không cần đối chiếu (đọc từ lớp chữ của PDF)",
};

export const UNKNOWN_CROSS_CHECK_VI = "Trạng thái đối chiếu chưa rõ";

/** Loại tài liệu theo `SourceKind` (§5.8), đúng thứ tự khai báo. `study_guide` còn đề xuất nên chưa có ở đây. */
export const SOURCE_KINDS: readonly SourceKind[] = ["textbook", "lecture_slides", "past_exam", "lab_manual"];

export const SOURCE_KIND_LABEL_VI: Record<SourceKind, string> = {
  textbook: "Giáo trình",
  lecture_slides: "Slide bài giảng",
  past_exam: "Đề thi cũ",
  lab_manual: "Hướng dẫn thực hành",
};

export const UNKNOWN_SOURCE_KIND_VI = "Loại tài liệu khác";

/** Câu nhắc cố định cho trang đọc bằng OCR: bộ dữ liệu tiếng Việt được phép thiếu hai chữ hiếm này (§5.7). */
export const OCR_RARE_LETTERS_NOTE_VI = "OCR có thể không nhận được chữ hiếm Ỵ, ỵ; kiểm lại các từ có chữ này.";

/** Nhãn của một mã; mã không có trong bảng (kể cả tên thuộc tính của `Object.prototype`) ⇒ nhãn chung. */
function labelFrom(table: Readonly<Record<string, string>>, code: string, fallback: string): string {
  return Object.hasOwn(table, code) ? (table[code] ?? fallback) : fallback;
}

export function reviewReasonLabel(code: string): string {
  return labelFrom(REVIEW_REASON_LABEL_VI, code, UNKNOWN_REVIEW_REASON_VI);
}

/** Nhãn của danh sách lý do, giữ thứ tự backend; hai mã lạ cùng ra nhãn chung thì chỉ hiện một lần. */
export function reviewReasonLabels(codes: readonly string[]): string[] {
  const labels: string[] = [];
  for (const code of codes) {
    const label = reviewReasonLabel(code);
    if (!labels.includes(label)) labels.push(label);
  }
  return labels;
}

export function failureReasonLabel(code: string | null): string {
  return code === null
    ? UNKNOWN_FAILURE_REASON_VI
    : labelFrom(FAILURE_REASON_LABEL_VI, code, UNKNOWN_FAILURE_REASON_VI);
}

export function lineFlagLabel(code: string): string {
  return labelFrom(LINE_FLAG_LABEL_VI, code, UNKNOWN_LINE_FLAG_VI);
}

export function tierLabel(code: string | null): string | null {
  return code === null ? null : labelFrom(TIER_LABEL_VI, code, UNKNOWN_TIER_VI);
}

export function engineLabel(code: string): string {
  return labelFrom(ENGINE_LABEL_VI, code, "bộ đọc khác");
}

export function crossCheckLabel(code: string): string {
  return labelFrom(CROSS_CHECK_LABEL_VI, code, UNKNOWN_CROSS_CHECK_VI);
}

export function sourceKindLabel(code: string): string {
  return labelFrom(SOURCE_KIND_LABEL_VI, code, UNKNOWN_SOURCE_KIND_VI);
}

// ----------------------------------------------------------------------------- cảnh báo theo trang

export type WarningTone = "info" | "warning" | "danger";

export interface PageWarning {
  key: string;
  label: string;
  tone: WarningTone;
}

interface WarningInput {
  status: PageStatusView;
  tierUsed: string | null;
  reviewReasons: readonly string[];
  /** Chỉ có khi đã đọc `PageExtraction`; tóm tắt trang (`PageSummary`) không có lý do lỗi. */
  failureReason?: string | null;
}

/**
 * Cảnh báo của một trang, theo thứ tự: đọc lỗi (kèm lý do nếu biết), đọc bằng OCR (kèm câu nhắc chữ hiếm), rồi từng
 * lý do cần xem kỹ. Chỉ dựa vào mã backend trả.
 */
export function pageWarnings(page: WarningInput): PageWarning[] {
  const warnings: PageWarning[] = [];
  if (page.status === "failed") {
    const label =
      page.failureReason === undefined
        ? "Đọc lỗi; mở trang để xem lý do"
        : `Đọc lỗi: ${failureReasonLabel(page.failureReason)}`;
    warnings.push({ key: "failed", label, tone: "danger" });
  }
  if (page.tierUsed === "ocr") {
    warnings.push({ key: "ocr", label: `Đọc bằng OCR (ảnh scan). ${OCR_RARE_LETTERS_NOTE_VI}`, tone: "info" });
  }
  for (const label of reviewReasonLabels(page.reviewReasons)) {
    warnings.push({ key: `reason:${label}`, label, tone: "warning" });
  }
  return warnings;
}

// ----------------------------------------------------------------------------------- kiểm phía client

export interface FileLike {
  name: string;
  type: string;
  size: number;
}

export type Check = { ok: true } | { ok: false; message: string };

/** MIME trình duyệt có thể gán cho PDF; rỗng và `application/octet-stream` backend cũng nhận (§6.5 bước 2). */
const ACCEPTED_PDF_TYPES: readonly string[] = ["application/pdf", "application/octet-stream", ""];

/** Độ dài theo code point, như `len()` của Python mà backend dùng để kiểm. */
export function charLength(text: string): number {
  return [...text].length;
}

/**
 * Kiểm file trước khi gửi `POST /v1/documents`: rỗng, quá 50 MiB, đuôi không phải `.pdf`, MIME khai báo không phải
 * PDF, tên file quá 255 ký tự. Backend vẫn kiểm nội dung (magic bytes `%PDF-`, số trang, mật khẩu).
 */
export function checkDocumentFile(file: FileLike): Check {
  if (file.size <= 0) return { ok: false, message: "File rỗng. Chọn file PDF có nội dung." };
  if (file.size > DOCUMENT_UPLOAD_MAX_BYTES) {
    return {
      ok: false,
      message: `File vượt ${DOCUMENT_UPLOAD_MAX_MIB} MiB. Tách tài liệu thành nhiều file nhỏ hơn rồi nhập từng file.`,
    };
  }
  if (!/\.pdf$/i.test(file.name.trim())) {
    return { ok: false, message: "Chỉ nhận file PDF (đuôi .pdf). Ảnh đề thi nhập ở mục “Nhập đề thi”." };
  }
  if (!ACCEPTED_PDF_TYPES.includes(file.type.trim().toLowerCase())) {
    return { ok: false, message: "File này không phải PDF theo trình duyệt. Chọn đúng file PDF." };
  }
  if (charLength(file.name) > FILENAME_MAX_CHARS) {
    return { ok: false, message: `Tên file dài quá ${FILENAME_MAX_CHARS} ký tự. Đổi tên file ngắn hơn rồi chọn lại.` };
  }
  return { ok: true };
}

export interface ImportFormInput {
  subjectCode: string;
  chapterCode: string;
  sourceKind: string;
  title: string;
}

export type ImportFormField = "subject" | "chapter" | "sourceKind" | "title";

/** Lỗi theo từng ô của form nhập tài liệu; rỗng khi gửi được. */
export function checkImportForm(input: ImportFormInput): Partial<Record<ImportFormField, string>> {
  const errors: Partial<Record<ImportFormField, string>> = {};
  if (!SUBJECT_CODE_PATTERN.test(input.subjectCode)) {
    errors.subject = "Mã môn không hợp lệ. Chọn lại môn ở thanh bên.";
  }
  const chapter = input.chapterCode.trim();
  if (chapter === "") {
    errors.chapter = "Nhập chương của tài liệu, ví dụ ch1 hoặc ch2.1. Mỗi file là một chương.";
  } else if (!CHAPTER_CODE_PATTERN.test(chapter)) {
    errors.chapter = "Mã chương phải có dạng ch1, ch12 hoặc ch2.1 (chữ ch viết thường, tối đa hai chữ số mỗi phần).";
  }
  if (!SOURCE_KINDS.some((kind) => kind === input.sourceKind)) {
    errors.sourceKind = "Chọn loại tài liệu.";
  }
  if (charLength(input.title.trim()) > DOCUMENT_TITLE_MAX_CHARS) {
    errors.title = `Tên tài liệu tối đa ${DOCUMENT_TITLE_MAX_CHARS} ký tự.`;
  }
  return errors;
}

// ----------------------------------------------------------------------- sửa dòng và xác nhận (§6.5)

interface PageStateInput {
  status: PageStatusView;
  confirmed: boolean;
  lines: readonly LineView[];
}

/** Vì sao chưa sửa dòng được; `null` khi sửa được. Khớp các trường hợp 409 của `PATCH …/lines` (§6.5). */
export type EditBlock = "pending" | "failed" | "confirmed" | "unknown";

export function lineEditBlock(page: Pick<PageStateInput, "status" | "confirmed">): EditBlock | null {
  if (page.status === "pending") return "pending";
  if (page.status === "failed") return "failed";
  if (page.status === "unknown") return "unknown";
  if (page.confirmed) return "confirmed";
  return null;
}

export const EDIT_BLOCK_MESSAGE_VI: Record<EditBlock, string> = {
  pending: "Trang đang được đọc, chưa sửa được. Tải lại khi đọc xong.",
  failed:
    "Trang này đọc lỗi nên không sửa và không xác nhận được. Muốn có văn bản của trang, xoá tài liệu rồi nhập lại file (ví dụ sau khi cài Tesseract).",
  confirmed: "Trang đã xác nhận nên không sửa được nữa. Muốn đọc lại, xoá tài liệu rồi nhập lại.",
  unknown: "Giao diện này chưa nhận ra trạng thái của trang nên tạm khoá sửa và xác nhận.",
};

/** Trang có ít nhất một dòng có chữ (backend trả 409 khi xác nhận trang không có dòng nào có văn bản). */
export function hasTextLine(lines: readonly LineView[]): boolean {
  return lines.some((line) => line.text.trim() !== "");
}

export type ConfirmPageBlock = EditBlock | "no_text";

/** Vì sao chưa xác nhận trang được; `null` khi xác nhận được (trang `ok` hoặc `needs_review`, chưa xác nhận, có chữ). */
export function confirmPageBlock(page: PageStateInput): ConfirmPageBlock | null {
  const block = lineEditBlock(page);
  if (block !== null) return block;
  return hasTextLine(page.lines) ? null : "no_text";
}

export function canConfirmPage(page: PageStateInput): boolean {
  return confirmPageBlock(page) === null;
}

export const CONFIRM_PAGE_BLOCK_MESSAGE_VI: Record<ConfirmPageBlock, string> = {
  ...EDIT_BLOCK_MESSAGE_VI,
  confirmed: "Trang đã được xác nhận.",
  no_text: "Trang chưa có dòng nào có chữ nên chưa xác nhận được. Thêm dòng ở dưới nếu trang có nội dung.",
};

export type ConfirmDocumentBlock = "processing" | "nothing_to_confirm";

/**
 * Xác nhận cả tài liệu chỉ xác nhận các trang `ok` chưa xác nhận (§6.5); backend trả 409 khi còn trang đang đọc.
 * Không còn trang `ok` nào chưa xác nhận thì nút không có tác dụng nên tắt.
 */
export function confirmDocumentBlock(document: Pick<DocumentView, "status" | "pages">): ConfirmDocumentBlock | null {
  if (document.status === "processing" || document.pages.some((page) => page.status === "pending")) {
    return "processing";
  }
  return document.pages.some((page) => page.status === "ok" && !page.confirmed) ? null : "nothing_to_confirm";
}

export const CONFIRM_DOCUMENT_BLOCK_MESSAGE_VI: Record<ConfirmDocumentBlock, string> = {
  processing: "Tài liệu còn trang đang đọc. Chờ đọc xong rồi xác nhận.",
  nothing_to_confirm:
    "Không còn trang đọc tốt nào chờ xác nhận. Trang cần xem kỹ phải được xem và xác nhận từng trang.",
};

/** Lỗi của nội dung một dòng trước khi gửi; `null` khi hợp lệ. */
export function checkLineText(text: string): string | null {
  if (charLength(text) > LINE_TEXT_MAX_CHARS) return `Mỗi dòng tối đa ${LINE_TEXT_MAX_CHARS} ký tự.`;
  return null;
}

/**
 * `edits` của `PATCH …/lines`: chỉ dòng có bản nháp khác văn bản hiện tại (so sau NFC, vì backend chuẩn hoá NFC),
 * bỏ chỉ số không có trên trang, sắp theo `line_index`. Văn bản gửi đi là bản nháp đã chuẩn hoá NFC.
 */
export function buildLineEdits(lines: readonly LineView[], drafts: ReadonlyMap<number, string>): LineEdit[] {
  const edits: LineEdit[] = [];
  for (const line of lines) {
    const draft = drafts.get(line.lineIndex);
    if (draft === undefined) continue;
    const text = draft.normalize("NFC");
    if (text !== line.text.normalize("NFC")) edits.push({ line_index: line.lineIndex, text });
  }
  return edits.sort((a, b) => a.line_index - b.line_index);
}

/** Token lệch theo dòng (từ `cross_check.mismatches`, giữ thứ tự backend). */
export function mismatchesByLine(mismatches: readonly MismatchView[]): Map<number, MismatchView[]> {
  const byLine = new Map<number, MismatchView[]>();
  for (const mismatch of mismatches) {
    const list = byLine.get(mismatch.lineIndex) ?? [];
    list.push(mismatch);
    byLine.set(mismatch.lineIndex, list);
  }
  return byLine;
}

export interface LineHighlight {
  /** Backend gắn cờ `LOW_LINE_CONF`. */
  lowConf: boolean;
  /** Backend gắn cờ `TOKEN_MISMATCH` hoặc có token lệch trỏ tới dòng này. */
  mismatch: boolean;
  /** Mọi cờ của dòng thành nhãn tiếng Việt. */
  labels: string[];
}

export function lineHighlight(line: LineView, mismatches: ReadonlyMap<number, readonly MismatchView[]>): LineHighlight {
  const labels: string[] = [];
  for (const flag of line.flags) {
    const label = lineFlagLabel(flag);
    if (!labels.includes(label)) labels.push(label);
  }
  return {
    lowConf: line.flags.includes("LOW_LINE_CONF"),
    mismatch: line.flags.includes("TOKEN_MISMATCH") || (mismatches.get(line.lineIndex)?.length ?? 0) > 0,
    labels,
  };
}
