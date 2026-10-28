/**
 * Nháp chưa lưu của màn Xem lại, giữ theo số trang ở `PageReview` (I5; quyết định 2026-10-29 "Lỗi ô thêm dòng" và
 * "PR 5 — nút Xác nhận các trang đọc tốt"):
 * - mỗi trang có chữ của ô "Thêm dòng vào cuối trang" và ô "Sửa" đang mở (dòng nào, chữ đang gõ, chữ của dòng lúc mở);
 * - nháp nằm ngoài vùng xem một trang nên không mất khi vùng đó vẽ lại (lưu dòng khác làm `version` tăng) hay khi đổi
 *   trang rồi quay lại;
 * - còn nháp ở BẤT KỲ trang nào thì nút "Xác nhận các trang đọc tốt" khoá (`bulkConfirmBlock`). Không tự lưu hộ.
 *
 * Mọi hàm trả bản mới, không sửa bản cũ. Trang không còn nháp thì bị gỡ khỏi bảng.
 */

import { type ConfirmDocumentBlock, confirmDocumentBlock, lineEditBlock } from "@/lib/documents";
import type { DocumentView, LineView, PageSummaryView } from "@/lib/documents-data";
import { unsavedDrafts } from "@/lib/review-confirm";

/** Ô "Sửa" đang mở: dòng nào và chữ đang gõ (mở mà chưa đổi chữ vẫn tính là nháp). */
export interface LineEditDraft {
  lineIndex: number;
  text: string;
  /**
   * Chữ của dòng lúc mở ô sửa. Dữ liệu trang tải lại (409, poll, lưu dòng khác) mà dòng đã khác chữ này thì ô sửa
   * đóng và hiện xung đột (`lineEditState`), không cho "Lưu" ghi đè im lặng bản mới.
   */
  baseText: string;
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

/**
 * Mở ô "Sửa" cho một dòng, chữ ban đầu là chữ hiện tại của dòng (cũng là `baseText`). Ô đang mở (nếu có) được thay;
 * "Sửa lại từ bản mới" khi xung đột cũng đi qua đây với chữ mới của dòng.
 */
export function startLineEdit(drafts: ReviewDrafts, pageNumber: number, lineIndex: number, text: string): ReviewDrafts {
  return withDraft(drafts, pageNumber, {
    ...draftFor(drafts, pageNumber),
    editing: { lineIndex, text, baseText: text },
  });
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

/**
 * Ô "Sửa" đối chiếu với các dòng của bản trang đang hiện:
 * - `open`: dòng còn nguyên chữ lúc mở (hoặc đã mang đúng chữ người dùng gõ, ví dụ bản trả về ngay sau khi chính ô này
 *   lưu xong): ô sửa mở như thường;
 * - `changed`: dòng đã đổi ở nơi khác; ô sửa đóng, hiện chữ mới của dòng và chữ người dùng đang gõ;
 * - `removed`: dòng không còn trên trang.
 * Hai trạng thái xung đột vẫn là nháp chưa lưu (khoá nút xác nhận) cho tới khi người dùng sửa lại hoặc bỏ chữ đã gõ.
 * So sánh sau chuẩn hoá NFC, như `buildLineEdits`.
 */
export type LineEditState = { kind: "open" } | { kind: "changed"; currentText: string } | { kind: "removed" };

export function lineEditState(
  editing: LineEditDraft,
  lines: readonly Pick<LineView, "lineIndex" | "text">[],
): LineEditState {
  const line = lines.find((item) => item.lineIndex === editing.lineIndex);
  if (line === undefined) return { kind: "removed" };
  const current = line.text.normalize("NFC");
  if (current === editing.baseText.normalize("NFC") || current === editing.text.normalize("NFC")) {
    return { kind: "open" };
  }
  return { kind: "changed", currentText: line.text };
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

/** Nhãn chữ cạnh trang còn nháp trong danh sách trang (không chỉ dựa vào màu; nằm trong tên truy cập của link). */
export const DRAFT_PAGE_LABEL_VI = "còn dòng chưa lưu";

/** Số trang tối đa liệt kê trong câu dưới nút "Xác nhận các trang đọc tốt"; trang còn lại gộp thành "và N trang khác". */
export const DRAFT_PAGES_LISTED = 8;

/**
 * Câu ngắn liệt kê trang còn nháp, hiện thêm dưới dòng nhắc khi nút "Xác nhận các trang đọc tốt" khoá vì nháp (dòng
 * nhắc giữ nguyên chữ của quyết định). Ví dụ `[2, 5]` ⇒ "Trang 2, 5 còn dòng chưa lưu."; danh sách rỗng ⇒ `null`.
 */
export function draftPagesNote(pages: readonly number[]): string | null {
  if (pages.length === 0) return null;
  const listed = pages.slice(0, DRAFT_PAGES_LISTED);
  const rest = pages.length - listed.length;
  const list = `Trang ${listed.join(", ")}`;
  return rest > 0 ? `${list} và ${rest} trang khác ${DRAFT_PAGE_LABEL_VI}.` : `${list} ${DRAFT_PAGE_LABEL_VI}.`;
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
