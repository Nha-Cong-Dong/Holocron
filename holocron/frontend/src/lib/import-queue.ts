/**
 * Logic thuần của tab Tài liệu ở trang môn (design-handoff/README.md màn 4, 5):
 * - hàng đợi nhập nhiều PDF: thêm, sửa (tên, chương, loại), bỏ dòng; kiểm từng dòng trước khi gửi; gửi LẦN LƯỢT từng
 *   file qua `POST /v1/documents` (§6.5) và ghi kết quả từng dòng; lỗi một dòng không dừng cả hàng; "Thử lại" từng dòng;
 * - danh sách "Đang đọc": tài liệu `processing` (tiến độ theo trang lấy từ `readingLines` của `lib/home-cards.ts`) và
 *   tài liệu vừa đọc xong trong lúc màn đang mở.
 *
 * Không tính số nào ngoài việc đếm dòng đang có trên màn; số trang do backend trả.
 */

import { guessChapterCode } from "@/lib/chapters";
import { charLength, checkDocumentFile, type FileLike } from "@/lib/documents";
import type { DocumentStatusView, DocumentSummaryView } from "@/lib/documents-data";
import type { UiError } from "@/lib/errors";
import { type ReadingLine, readingLines } from "@/lib/home-cards";
import { CHAPTER_CODE_PATTERN, DOCUMENT_TITLE_MAX_CHARS } from "@/lib/limits";
import type { SourceKind } from "@/types/holocron";

// ------------------------------------------------------------------------------------------ dòng

/**
 * Trạng thái một dòng: `ready` (gửi được), `invalid` (còn lỗi phải sửa hoặc bỏ dòng), `sending` (đang gửi), `created`
 * (202, tài liệu mới), `existing` (200, cùng nội dung đã có ở môn này), `conflict` (409, cùng nội dung ở môn khác hoặc
 * chương khác), `failed` (lỗi khác).
 */
export type ImportRowStatus = "ready" | "invalid" | "sending" | "created" | "existing" | "conflict" | "failed";

/** Loại tài liệu mặc định của dòng mới: "Slide bài giảng". */
export const DEFAULT_SOURCE_KIND: SourceKind = "lecture_slides";

export interface ImportRow<F extends FileLike = FileLike> {
  id: string;
  subjectCode: string;
  file: F;
  /** Tên tài liệu gửi làm `title`; điền sẵn từ tên file bỏ đuôi, người dùng sửa tại chỗ. */
  title: string;
  /** Mã chương đoán từ tên file hoặc người dùng chọn; `null` = "Chưa rõ chương". */
  chapterCode: string | null;
  /** Chương do hệ thống đoán (chưa sửa tay). */
  chapterGuessed: boolean;
  sourceKind: SourceKind;
  status: ImportRowStatus;
  /** Người dùng đã bấm "Bắt đầu đọc": dòng `ready` chờ tới lượt gửi, không sửa được nữa. */
  queued: boolean;
  /** Lỗi trước khi gửi (`invalid`): việc gì sai và cách sửa. */
  problem: string | null;
  /** Lỗi khi gửi (`failed`, `conflict`). */
  error: UiError | null;
  /** Tài liệu backend trả (`created`, `existing`). */
  documentId: string | null;
}

export interface ImportQueueState<F extends FileLike = FileLike> {
  rows: ImportRow<F>[];
  /** Số dùng để tạo id dòng tiếp theo. */
  nextId: number;
  /** Số file đã được backend nhận theo từng môn, tăng sau mỗi lần gửi thành công: màn Tài liệu tải lại danh sách. */
  completions: Readonly<Record<string, number>>;
}

export function initialImportQueue<F extends FileLike = FileLike>(): ImportQueueState<F> {
  return { rows: [], nextId: 1, completions: {} };
}

/** Tên tài liệu mặc định: tên file bỏ đuôi `.pdf`, bỏ khoảng trắng hai đầu. */
export function defaultTitle(fileName: string): string {
  return fileName
    .trim()
    .replace(/\.pdf$/i, "")
    .trim();
}

/** Nhãn của một mã chương: "ch2" ⇒ "Chương 2", "ch2.1" ⇒ "Chương 2.1"; `null` ⇒ "Chưa rõ chương". */
export function chapterLabel(code: string | null): string {
  if (code === null) return "Chưa rõ chương";
  const match = /^ch([0-9]{1,2}(?:\.[0-9]{1,2})?)$/.exec(code);
  return match === null ? code : `Chương ${match[1]}`;
}

export const CHAPTER_MISSING_VI = "Chưa rõ chương của file này. Chọn chương trong danh sách.";
export const TITLE_TOO_LONG_VI = `Tên tài liệu dài quá ${DOCUMENT_TITLE_MAX_CHARS} ký tự. Rút gọn tên rồi đọc.`;

/** Lỗi của dòng trước khi gửi; `null` khi gửi được. Lỗi của file (không phải PDF, quá lớn) đứng trước. */
export function rowProblem(row: Pick<ImportRow, "file" | "title" | "chapterCode">): string | null {
  const fileCheck = checkDocumentFile(row.file);
  if (!fileCheck.ok) return fileCheck.message;
  if (charLength(row.title.trim()) > DOCUMENT_TITLE_MAX_CHARS) return TITLE_TOO_LONG_VI;
  if (row.chapterCode === null || !CHAPTER_CODE_PATTERN.test(row.chapterCode)) return CHAPTER_MISSING_VI;
  return null;
}

/** Dòng chưa gửi (đang sửa) có thể sửa, bỏ; dòng đã xếp hàng hay đang gửi thì không. */
export function isStaged(row: Pick<ImportRow, "status" | "queued">): boolean {
  return (row.status === "ready" || row.status === "invalid") && !row.queued;
}

/** Dòng còn chờ gửi hoặc đang gửi (đã bấm "Bắt đầu đọc"). */
export function isPending(row: Pick<ImportRow, "status" | "queued">): boolean {
  return row.status === "sending" || (row.status === "ready" && row.queued);
}

/** Dòng đã có kết quả từ backend. */
export function isSettled(row: Pick<ImportRow, "status">): boolean {
  return row.status === "created" || row.status === "existing" || row.status === "conflict" || row.status === "failed";
}

function validated<F extends FileLike>(row: ImportRow<F>): ImportRow<F> {
  const problem = rowProblem(row);
  return { ...row, problem, status: problem === null ? "ready" : "invalid" };
}

// ------------------------------------------------------------------------------------------ reducer

export interface RowEdit {
  title?: string;
  chapterCode?: string | null;
  sourceKind?: SourceKind;
}

export type ImportQueueAction<F extends FileLike = FileLike> =
  | { type: "add"; subjectCode: string; files: readonly F[] }
  | { type: "remove"; id: string }
  | { type: "edit"; id: string; edit: RowEdit }
  | { type: "start"; subjectCode: string }
  | { type: "send_started"; id: string }
  | { type: "send_succeeded"; id: string; created: boolean; documentId: string }
  | { type: "send_failed"; id: string; error: UiError }
  | { type: "retry"; id: string }
  | { type: "clear_settled"; subjectCode: string };

function updateRow<F extends FileLike>(
  state: ImportQueueState<F>,
  id: string,
  change: (row: ImportRow<F>) => ImportRow<F> | null,
): ImportQueueState<F> {
  let changed = false;
  const rows = state.rows.map((row) => {
    if (row.id !== id) return row;
    const next = change(row);
    if (next === null) return row;
    changed = true;
    return next;
  });
  return changed ? { ...state, rows } : state;
}

/**
 * Reducer của hàng đợi nhập. Thao tác không hợp lệ với trạng thái hiện tại (sửa dòng đang gửi, bỏ dòng đang gửi, bắt
 * đầu gửi dòng chưa xếp hàng…) trả lại đúng state cũ.
 */
export function importQueueReducer<F extends FileLike>(
  state: ImportQueueState<F>,
  action: ImportQueueAction<F>,
): ImportQueueState<F> {
  switch (action.type) {
    case "add": {
      if (action.files.length === 0) return state;
      let nextId = state.nextId;
      const added = action.files.map((file) => {
        const chapterCode = guessChapterCode(file.name);
        const row: ImportRow<F> = {
          id: `import-${nextId}`,
          subjectCode: action.subjectCode,
          file,
          title: defaultTitle(file.name),
          chapterCode,
          chapterGuessed: chapterCode !== null,
          sourceKind: DEFAULT_SOURCE_KIND,
          status: "ready",
          queued: false,
          problem: null,
          error: null,
          documentId: null,
        };
        nextId += 1;
        return validated(row);
      });
      return { ...state, rows: [...state.rows, ...added], nextId };
    }
    case "remove": {
      const target = state.rows.find((row) => row.id === action.id);
      if (target === undefined || target.status === "sending") return state;
      return { ...state, rows: state.rows.filter((row) => row.id !== action.id) };
    }
    case "edit":
      return updateRow(state, action.id, (row) => {
        if (!isStaged(row)) return null;
        const next: ImportRow<F> = { ...row };
        if (action.edit.title !== undefined) next.title = action.edit.title;
        if (action.edit.chapterCode !== undefined) {
          next.chapterCode = action.edit.chapterCode;
          next.chapterGuessed = false;
        }
        if (action.edit.sourceKind !== undefined) next.sourceKind = action.edit.sourceKind;
        return validated(next);
      });
    case "start": {
      let changed = false;
      const rows = state.rows.map((row) => {
        if (row.subjectCode !== action.subjectCode || row.status !== "ready" || row.queued) return row;
        changed = true;
        return { ...row, queued: true };
      });
      return changed ? { ...state, rows } : state;
    }
    case "send_started":
      return updateRow(state, action.id, (row) =>
        row.status === "ready" && row.queued ? { ...row, status: "sending", error: null } : null,
      );
    case "send_succeeded": {
      const target = state.rows.find((row) => row.id === action.id);
      if (target === undefined || target.status !== "sending") return state;
      const next = updateRow(state, action.id, (row) => ({
        ...row,
        status: action.created ? "created" : "existing",
        queued: false,
        error: null,
        documentId: action.documentId,
      }));
      const count = state.completions[target.subjectCode] ?? 0;
      return { ...next, completions: { ...state.completions, [target.subjectCode]: count + 1 } };
    }
    case "send_failed":
      return updateRow(state, action.id, (row) =>
        row.status === "sending"
          ? {
              ...row,
              // 409 của `POST /v1/documents` được phân loại `duplicate` (`lib/errors.ts`): cùng nội dung đã ở môn khác
              // hoặc chương khác; gửi lại không giúp gì.
              status: action.error.kind === "duplicate" ? "conflict" : "failed",
              queued: false,
              error: action.error,
            }
          : null,
      );
    case "retry":
      return updateRow(state, action.id, (row) => {
        if (row.status !== "failed") return null;
        const next = validated({ ...row, error: null });
        return { ...next, queued: next.status === "ready" };
      });
    case "clear_settled": {
      const rows = state.rows.filter(
        (row) => row.subjectCode !== action.subjectCode || !(row.status === "created" || row.status === "existing"),
      );
      return rows.length === state.rows.length ? state : { ...state, rows };
    }
  }
}

// ------------------------------------------------------------------------------------------ chọn

/** Dòng gửi tiếp theo: dòng đã xếp hàng đứng trước nhất; đang có dòng gửi thì `null` (gửi lần lượt từng file). */
export function nextToSend<F extends FileLike>(state: ImportQueueState<F>): ImportRow<F> | null {
  if (state.rows.some((row) => row.status === "sending")) return null;
  return state.rows.find((row) => row.status === "ready" && row.queued) ?? null;
}

export function rowsOfSubject<F extends FileLike>(rows: readonly ImportRow<F>[], subjectCode: string): ImportRow<F>[] {
  return rows.filter((row) => row.subjectCode === subjectCode);
}

/** Còn file chưa gửi xong (đang sửa, chờ gửi, đang gửi): đóng hay tải lại cửa sổ sẽ mất các file này. */
export function hasUnsentRows(rows: readonly Pick<ImportRow, "status" | "queued">[]): boolean {
  return rows.some((row) => row.status === "ready" || row.status === "invalid" || row.status === "sending");
}

export type StartBlock = "empty" | "invalid";

export interface StartDecision {
  /** Số file sẽ gửi khi bấm "Bắt đầu đọc (N file)". */
  count: number;
  /** Số dòng chưa rõ chương. */
  unknownChapters: number;
  /** Vì sao chưa bắt đầu được; `null` khi bắt đầu được. */
  block: StartBlock | null;
}

/** Nút "Bắt đầu đọc": chỉ bật khi có ít nhất một dòng và mọi dòng đang sửa đều hợp lệ. */
export function startDecision(staged: readonly Pick<ImportRow, "status" | "chapterCode">[]): StartDecision {
  const count = staged.filter((row) => row.status === "ready").length;
  const invalid = staged.filter((row) => row.status === "invalid").length;
  const unknownChapters = staged.filter((row) => row.chapterCode === null).length;
  const block: StartBlock | null = staged.length === 0 ? "empty" : invalid > 0 ? "invalid" : null;
  return { count, unknownChapters, block };
}

export const START_BLOCK_MESSAGE_VI: Record<StartBlock, string> = {
  empty: "Thêm ít nhất một file PDF để bắt đầu đọc.",
  invalid: "Còn dòng cần sửa: chọn chương cho dòng tô vàng, hoặc bỏ file không nhận được khỏi danh sách.",
};

export interface QueueProgress {
  /** Số file đã có kết quả. */
  settled: number;
  /** Số file đã bấm gửi (chờ, đang gửi hoặc đã có kết quả). */
  total: number;
}

/** Tiến độ gửi của một môn: đếm dòng đã bấm gửi trên màn. */
export function queueProgress(rows: readonly Pick<ImportRow, "status" | "queued">[]): QueueProgress {
  const settled = rows.filter(isSettled).length;
  const pending = rows.filter(isPending).length;
  return { settled, total: settled + pending };
}

// ------------------------------------------------------------------------------------------ đang đọc (màn 5)

export type ReadingRowKind = "reading" | "done" | "failed";

export interface ReadingRow {
  docId: string;
  title: string;
  kind: ReadingRowKind;
  /** Chỉ có với `reading`: câu và số trang của `readingLines`. */
  line: ReadingLine | null;
  status: DocumentStatusView;
}

/**
 * Dòng của màn "Đang đọc" theo thứ tự backend trả: tài liệu đang đọc, và tài liệu đã thấy đang đọc trong lúc màn mở
 * (`watched`) nay đã đọc xong (`done`) hoặc không đọc được (`failed`).
 */
export function readingRows(documents: readonly DocumentSummaryView[], watched: ReadonlySet<string>): ReadingRow[] {
  const lines = new Map(readingLines(documents).map((line) => [line.docId, line]));
  const rows: ReadingRow[] = [];
  for (const document of documents) {
    const line = lines.get(document.docId);
    if (line !== undefined) {
      rows.push({ docId: document.docId, title: document.title, kind: "reading", line, status: document.status });
    } else if (watched.has(document.docId)) {
      const kind: ReadingRowKind = document.status === "failed" ? "failed" : "done";
      rows.push({ docId: document.docId, title: document.title, kind, line: null, status: document.status });
    }
  }
  return rows;
}

/** Tổng tiến độ của màn "Đang đọc": số tài liệu đã đọc xong trên số dòng đang hiện. */
export function readingTotal(rows: readonly Pick<ReadingRow, "kind">[]): { finished: number; total: number } {
  return { finished: rows.filter((row) => row.kind !== "reading").length, total: rows.length };
}
