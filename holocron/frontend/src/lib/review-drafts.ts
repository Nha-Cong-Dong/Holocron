/**
 * Nháp chưa lưu của màn Xem lại, giữ theo số trang ở `PageReview` (I5; quyết định 2026-10-29 "Lỗi ô thêm dòng" và
 * "PR 5 — nút Xác nhận các trang đọc tốt"):
 * - mỗi trang có chữ của ô "Thêm dòng vào cuối trang" và ô "Sửa" đang mở (dòng nào, chữ đang gõ);
 * - nháp nằm ngoài vùng xem một trang nên không mất khi vùng đó vẽ lại (lưu dòng khác làm `version` tăng) hay khi đổi
 *   trang rồi quay lại;
 * - còn nháp ở BẤT KỲ trang nào thì nút "Xác nhận các trang đọc tốt" khoá (`bulkConfirmBlock`). Không tự lưu hộ.
 *
 * Mọi hàm trả bản mới, không sửa bản cũ. Trang không còn nháp thì bị gỡ khỏi bảng.
 */

import { type ConfirmDocumentBlock, confirmDocumentBlock, lineEditBlock } from "@/lib/documents";
import type { DocumentView, PageSummaryView } from "@/lib/documents-data";
import { unsavedDrafts } from "@/lib/review-confirm";

/** Ô "Sửa" đang mở: dòng nào và chữ đang gõ (mở mà chưa đổi chữ vẫn tính là nháp). */
export interface LineEditDraft {
  lineIndex: number;
  text: string;
}

export interface PageDraft {
  /** Chữ trong ô "Thêm dòng vào cuối trang" (chỉ khoảng trắng coi như trống). */
  appendText: string;
  /** Ô "Sửa" đang mở; `null` khi không mở ô nào. */
  editing: LineEditDraft | null;
}

/** Nháp theo số trang. */
export type ReviewDrafts = ReadonlyMap<number, PageDraft>;

export const EMPTY_PAGE_DRAFT: PageDraft = { appendText: "", editing: null };

export const NO_DRAFTS: ReviewDrafts = new Map();

/** Nháp của một trang; trang chưa có nháp thì trả nháp rỗng. */
export function draftFor(drafts: ReviewDrafts, pageNumber: number): PageDraft {
  return drafts.get(pageNumber) ?? EMPTY_PAGE_DRAFT;
}

function withDraft(drafts: ReviewDrafts, pageNumber: number, draft: PageDraft): ReviewDrafts {
  const next = new Map(drafts);
  if (draft.appendText === "" && draft.editing === null) next.delete(pageNumber);
  else next.set(pageNumber, draft);
  return next;
}

/** Đặt chữ của ô "Thêm dòng vào cuối trang"; `""` sau khi thêm dòng thành công hoặc người dùng xoá hết chữ. */
export function setAppendText(drafts: ReviewDrafts, pageNumber: number, text: string): ReviewDrafts {
  return withDraft(drafts, pageNumber, { ...draftFor(drafts, pageNumber), appendText: text });
}

/** Mở ô "Sửa" cho một dòng, chữ ban đầu là chữ hiện tại của dòng. Ô đang mở (nếu có) được thay. */
export function startLineEdit(drafts: ReviewDrafts, pageNumber: number, lineIndex: number, text: string): ReviewDrafts {
  return withDraft(drafts, pageNumber, { ...draftFor(drafts, pageNumber), editing: { lineIndex, text } });
}

/** Đổi chữ của ô "Sửa" đang mở; không có ô nào mở thì giữ nguyên. */
export function setLineEditText(drafts: ReviewDrafts, pageNumber: number, text: string): ReviewDrafts {
  const draft = draftFor(drafts, pageNumber);
  if (draft.editing === null) return drafts;
  return withDraft(drafts, pageNumber, { ...draft, editing: { ...draft.editing, text } });
}

/**
 * Đóng ô "Sửa" của đúng dòng `lineIndex` (huỷ, hoặc đã lưu xong); ô đang mở cho dòng khác thì giữ nguyên, để kết quả lưu
 * về muộn không đóng nhầm ô người dùng vừa mở.
 */
export function closeLineEdit(drafts: ReviewDrafts, pageNumber: number, lineIndex: number): ReviewDrafts {
  const draft = draftFor(drafts, pageNumber);
  if (draft.editing?.lineIndex !== lineIndex) return drafts;
  return withDraft(drafts, pageNumber, { ...draft, editing: null });
}

/** Trang còn nháp chưa lưu: cùng điều kiện với nút xác nhận trang (`unsavedDrafts`). */
export function pageHasDraft(draft: PageDraft): boolean {
  const flags = unsavedDrafts({ appendText: draft.appendText, editingLine: draft.editing?.lineIndex ?? null });
  return flags.appendDraft || flags.editing;
}

/**
 * Các trang còn nháp, theo thứ tự trang của tài liệu. Chỉ tính trang còn sửa được (`lineEditBlock` là `null`): trang đã
 * xác nhận ở nơi khác hay đọc lỗi thì nháp không lưu được nữa và không có ô nào hiện ra để xoá, nên không giữ nút khoá.
 */
export function pagesWithDrafts(
  drafts: ReviewDrafts,
  pages: readonly Pick<PageSummaryView, "pageNumber" | "status" | "confirmed">[],
): number[] {
  return pages
    .filter((page) => lineEditBlock(page) === null && pageHasDraft(draftFor(drafts, page.pageNumber)))
    .map((page) => page.pageNumber);
}

/** Còn nháp ở bất kỳ trang nào của tài liệu. */
export function anyPageHasDraft(
  drafts: ReviewDrafts,
  pages: readonly Pick<PageSummaryView, "pageNumber" | "status" | "confirmed">[],
): boolean {
  return pagesWithDrafts(drafts, pages).length > 0;
}

/** Lý do khoá nút "Xác nhận các trang đọc tốt": lý do suy từ tài liệu trước, rồi "còn dòng chưa lưu". */
export type BulkConfirmBlock = ConfirmDocumentBlock | "unsaved_lines";

/**
 * Lý do khoá nút "Xác nhận các trang đọc tốt"; `null` khi bấm được. Thuộc tính `disabled` và handler `onClick` của nút
 * cùng gọi hàm này. Không tự lưu hộ nháp nào.
 */
export function bulkConfirmBlock(
  document: Pick<DocumentView, "status" | "pages">,
  drafts: ReviewDrafts,
): BulkConfirmBlock | null {
  const block = confirmDocumentBlock(document);
  if (block !== null) return block;
  return anyPageHasDraft(drafts, document.pages) ? "unsaved_lines" : null;
}
