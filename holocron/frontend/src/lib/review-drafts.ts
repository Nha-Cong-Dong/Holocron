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

import { type ConfirmDocumentBlock, confirmDocumentBlock, type EditBlock, lineEditBlock } from "@/lib/documents";
import type { DocumentView, LineView, PageSummaryView, PageView } from "@/lib/documents-data";
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
 * Mở ô "Sửa" cho một dòng, chữ ban đầu là chữ hiện tại của dòng (cũng là `baseText`). "Sửa lại từ bản mới" khi xung đột
 * cũng đi qua đây với chữ mới của dòng (ô của cùng dòng được thay). Trang đang có ô sửa (mở hay xung đột) của DÒNG KHÁC
 * thì giữ nguyên, không thay: nháp đó không bao giờ mất im lặng (giao diện khoá nút "Sửa" theo `lineEditDenial`).
 */
export function startLineEdit(drafts: ReviewDrafts, pageNumber: number, lineIndex: number, text: string): ReviewDrafts {
  const draft = draftFor(drafts, pageNumber);
  if (draft.editing !== null && draft.editing.lineIndex !== lineIndex) return drafts;
  return withDraft(drafts, pageNumber, {
    ...draft,
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
 * Ô "Sửa" đối chiếu với bản trang đang hiện:
 * - `locked`: trang không còn sửa được (`lineEditBlock` khác `null`, ví dụ vừa được xác nhận ở nơi khác): ô sửa đóng,
 *   hiện câu báo kèm chữ đã gõ và nút "Bỏ chữ đã gõ" (`lockedDraftNotice`);
 * - `open`: dòng còn nguyên chữ lúc mở: ô sửa mở như thường;
 * - `matched`: dòng đã mang đúng chữ người dùng đang gõ (ví dụ bản trả về ngay sau khi chính ô này lưu xong): nháp đã
 *   khớp server, ô sửa ĐÓNG (`PagePanel` gọi `closeLineEdit`). Không giữ ô mở với `baseText` cũ, vì gõ tiếp sẽ làm ô
 *   chuyển `changed` giữa chừng;
 * - `changed`: dòng đã đổi ở nơi khác; ô sửa đóng, hiện chữ mới của dòng và chữ người dùng đang gõ;
 * - `removed`: dòng không còn trên trang.
 * Các trạng thái xung đột (`changed`, `removed`, `locked`) vẫn là nháp chưa lưu (khoá nút xác nhận) cho tới khi người
 * dùng sửa lại hoặc bỏ chữ đã gõ. So sánh sau chuẩn hoá NFC, như `buildLineEdits`.
 */
export type LineEditState =
  | { kind: "open" }
  | { kind: "matched" }
  | { kind: "changed" }
  | { kind: "removed" }
  | { kind: "locked" };

export function lineEditState(
  editing: LineEditDraft,
  lines: readonly Pick<LineView, "lineIndex" | "text">[],
  editBlock: EditBlock | null,
): LineEditState {
  if (editBlock !== null) return { kind: "locked" };
  const line = lines.find((item) => item.lineIndex === editing.lineIndex);
  if (line === undefined) return { kind: "removed" };
  const current = line.text.normalize("NFC");
  if (current === editing.baseText.normalize("NFC")) return { kind: "open" };
  if (current === editing.text.normalize("NFC")) return { kind: "matched" };
  return { kind: "changed" };
}

/**
 * Dòng đang giữ ô "Sửa" của trang và lý do (bằng chữ) khoá nút "Sửa" của mọi dòng khác. Một trang chỉ có một ô sửa, nên
 * khi ô đó còn mở (`open`) hay còn chữ chưa lưu sau xung đột (`changed`, `removed`, `locked`), mở ô ở dòng khác sẽ làm
 * mất nháp: nút "Sửa" của dòng khác khoá kèm lý do cho tới khi người dùng lưu, huỷ, sửa lại hoặc bỏ chữ đã gõ.
 * `matched` (nháp đã khớp server, ô sắp đóng) không khoá.
 */
export interface LineEditLock {
  /** Dòng đang giữ ô sửa; nút của chính dòng này ("Sửa lại từ bản mới") không bị khoá. */
  lineIndex: number;
  /** Câu giải thích hiện cạnh danh sách dòng, nút "Sửa" bị khoá trỏ tới qua `aria-describedby`. */
  reason: string;
}

export function lineEditLock(
  draft: PageDraft,
  lines: readonly Pick<LineView, "lineIndex" | "text">[],
  editBlock: EditBlock | null,
): LineEditLock | null {
  const editing = draft.editing;
  if (editing === null) return null;
  const line = editing.lineIndex;
  switch (lineEditState(editing, lines, editBlock).kind) {
    case "matched":
      return null;
    case "open":
      return { lineIndex: line, reason: `Đang sửa dòng ${line}. Lưu hoặc huỷ trước khi sửa dòng khác.` };
    case "changed":
      return { lineIndex: line, reason: `Dòng ${line} còn chữ chưa lưu. Sửa lại hoặc bỏ chữ đã gõ trước.` };
    case "removed":
    case "locked":
      return { lineIndex: line, reason: `Dòng ${line} còn chữ chưa lưu. Bỏ chữ đã gõ trước khi sửa dòng khác.` };
  }
}

/**
 * Lý do nút "Sửa" của dòng `lineIndex` bị khoá; `null` khi bấm được. Thuộc tính `disabled`, `aria-describedby` và
 * handler của nút cùng gọi hàm này.
 */
export function lineEditDenial(lock: LineEditLock | null, lineIndex: number): string | null {
  if (lock === null || lock.lineIndex === lineIndex) return null;
  return lock.reason;
}

/** Trang còn nháp chưa lưu: cùng điều kiện với nút xác nhận trang (`unsavedDrafts`). */
export function pageHasDraft(draft: PageDraft): boolean {
  const flags = unsavedDrafts({ appendText: draft.appendText, editingLine: draft.editing?.lineIndex ?? null });
  return flags.appendDraft || flags.editing;
}

/** Nháp còn lại trên trang không sửa được nữa: hiện thành câu báo kèm chữ đã gõ và nút "Bỏ chữ đã gõ". */
export interface LockedDraftNotice {
  /** Ô "Sửa" đang mở lúc trang bị khoá (dòng nào, chữ đã gõ); `null` khi không có. */
  editing: LineEditDraft | null;
  /** Chữ của ô "Thêm dòng vào cuối trang"; `null` khi trống hoặc chỉ có khoảng trắng (không tính là nháp). */
  appendText: string | null;
}

/**
 * Câu báo nháp trên trang không sửa được nữa (ô sửa và ô thêm dòng đều không hiện); `null` khi trang còn sửa được (nháp
 * hiện ngay trong ô) hoặc trang không có nháp. Không tự lưu hộ, không tự bỏ chữ: người dùng bấm "Bỏ chữ đã gõ".
 */
export function lockedDraftNotice(draft: PageDraft, editBlock: EditBlock | null): LockedDraftNotice | null {
  if (editBlock === null || !pageHasDraft(draft)) return null;
  return {
    editing: draft.editing,
    appendText: draft.appendText.trim() === "" ? null : draft.appendText,
  };
}

/**
 * Câu cho vùng thông báo cố định của trang (`role="status"`, luôn có trong trang, rỗng khi không có gì): báo xung đột
 * hay nháp trên trang bị khoá. Không đọc lại chữ đã gõ (chữ đó hiện ngay tại câu báo). Chuỗi rỗng khi không có gì cần
 * báo. `state` là `lineEditState` của ô sửa (`null` khi không mở ô nào). Câu cấp trang mang số trang, để hai trang cùng
 * bị khoá vẫn ra hai câu khác nhau (vùng đổi chữ nên trình đọc màn hình đọc).
 */
function draftStatusMessage(
  pageNumber: number,
  draft: PageDraft,
  state: LineEditState | null,
  editBlock: EditBlock | null,
): string {
  const lineIndex = draft.editing?.lineIndex;
  if (lineIndex !== undefined && state?.kind === "changed") {
    return `Dòng ${lineIndex} vừa được sửa ở nơi khác. Chữ bạn đang gõ chưa lưu.`;
  }
  if (lineIndex !== undefined && state?.kind === "removed") {
    return `Dòng ${lineIndex} không còn trên trang này. Chữ bạn đang gõ chưa lưu.`;
  }
  if (lockedDraftNotice(draft, editBlock) !== null) {
    return `Trang ${pageNumber} không sửa được nữa. Chữ bạn đã gõ chưa lưu.`;
  }
  return "";
}

/** `draftStatusMessage` cho trang đang hiện: tự đối chiếu ô sửa với các dòng và lý do khoá sửa của trang. */
export function pageDraftStatusMessage(
  page: Pick<PageView, "pageNumber" | "status" | "confirmed"> & {
    lines: readonly Pick<LineView, "lineIndex" | "text">[];
  },
  draft: PageDraft,
): string {
  const editBlock = lineEditBlock(page);
  const state = draft.editing === null ? null : lineEditState(draft.editing, page.lines, editBlock);
  return draftStatusMessage(page.pageNumber, draft, state, editBlock);
}

/** Chữ đang có trong vùng thông báo cố định và trang mà chữ đó thuộc về. */
export interface Announcement {
  /** Trang của chữ; `null` khi chưa có trang nào (lúc đầu, hay đang mở trang cần xem đầu tiên). */
  pageNumber: number | null;
  text: string;
}

export const NO_ANNOUNCEMENT: Announcement = { pageNumber: null, text: "" };

/** Trang mà màn đang hướng tới và câu báo của nó; `message` là `null` khi trang chưa hiện (đang tải, lỗi). */
export interface ShownPageMessage {
  pageNumber: number | null;
  message: string | null;
}

/**
 * Chữ mới của vùng thông báo cố định (`role="status"` ở `PageReview`, không mount lại khi vùng trang vẽ lại):
 * - đổi sang trang khác (kể cả lúc trang mới còn đang tải): bỏ chữ của trang cũ, lấy câu của trang mới (`""` khi chưa
 *   có), không để câu xung đột của trang cũ nằm lại trong vùng khi đã sang trang khác;
 * - cùng trang mà chưa có câu (đang tải lại, lỗi): giữ chữ cũ, để tải lại xong không đọc lại xung đột đã báo;
 * - cùng trang, câu không đổi (ví dụ sau một lần ghi không liên quan): trả đúng `previous`, chữ trong vùng không đổi nên
 *   trình đọc màn hình không đọc lại.
 */
export function nextAnnouncement(previous: Announcement, shown: ShownPageMessage): Announcement {
  if (shown.pageNumber !== previous.pageNumber) return { pageNumber: shown.pageNumber, text: shown.message ?? "" };
  if (shown.message === null || shown.message === previous.text) return previous;
  return { pageNumber: shown.pageNumber, text: shown.message };
}

/**
 * Các trang còn nháp, theo thứ tự trang của tài liệu; trang không thuộc tài liệu thì không tính. Nhất quán với giao
 * diện: trang còn sửa được hiện nháp ngay trong ô (thêm dòng, sửa, xung đột), trang không sửa được nữa hiện
 * `lockedDraftNotice`, nên mọi trang có nháp đều còn thấy nháp và nút "Bỏ chữ đã gõ".
 */
export function pagesWithDrafts(drafts: ReviewDrafts, pages: readonly Pick<PageSummaryView, "pageNumber">[]): number[] {
  return pages.filter((page) => pageHasDraft(draftFor(drafts, page.pageNumber))).map((page) => page.pageNumber);
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
export function anyPageHasDraft(drafts: ReviewDrafts, pages: readonly Pick<PageSummaryView, "pageNumber">[]): boolean {
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
