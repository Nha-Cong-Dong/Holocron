/**
 * Logic thuần của màn Làm bài và Kết quả (design-handoff/README.md màn 9–10). Trạng thái bài làm vẫn là `PracticeState`
 * của `lib/practice.ts` (reducer không đổi); file này chỉ ghép thao tác của giao diện mới lên đó và dựng chữ hiển thị.
 *
 * Quy tắc (I1, I3):
 * - đúng/sai của câu trắc nghiệm chỉ là `answerOutcome` (so khoá đã chọn với `correct_key` backend trả);
 * - câu tự luận chỉ có "Khớp x/y ý" với hai số backend trả, luôn là sao tím, không bao giờ là "Đúng";
 * - không đếm số câu đúng của lượt, không có con số tổng; số hiện ra chỉ là số backend trả, hoặc vị trí và độ dài
 *   của danh sách câu backend trả (Câu k/N, "{N} câu");
 * - phương án nhiễu của câu lý thuyết chỉ hiện giải thích khi có trích dẫn nguyên văn đi kèm;
 * - chú thích nguồn chỉ có số trang và tên tài liệu, không bao giờ hiện mã tài liệu.
 */

import type { UiError } from "@/lib/errors";
import { essayHeadline } from "@/lib/essay";
import {
  type AnswerEntry,
  answerOutcome,
  isAnswered,
  type PracticeAction,
  type PracticeState,
  practiceReducer,
} from "@/lib/practice";
import type { McqQuestion, PracticeCitation, PracticeMisconception, PracticeQuestion } from "@/lib/practice-data";
import type { ShortcutAction } from "@/lib/shortcuts";
import type { PracticeSessionKind } from "@/lib/subject-route";
import type { SubjectTab } from "@/lib/tabs";
import { OPTION_KEYS } from "@/lib/twin";
import type { OptionKey } from "@/types/holocron";

// ------------------------------------------------------------------------------------------ thao tác

/**
 * Thao tác của màn Làm bài. `pick` chọn và khoá ngay một phương án (một bước, không còn nút "Nộp"); `next` chỉ sang
 * câu khi câu đang mở đã trả lời; `skip_essay` bỏ qua câu tự luận chấm lỗi (câu đó tính "Chưa làm").
 */
export type QuizAction =
  | { type: "pick"; key: OptionKey }
  | { type: "next" }
  | { type: "skip_essay" }
  | { type: "toggle_source" }
  | Extract<
      PracticeAction,
      { type: "edit_essay" | "essay_started" | "essay_graded" | "essay_failed" | "essay_rejected" }
    >;

function currentEntry(state: PracticeState): AnswerEntry | undefined {
  return state.entries[state.index];
}

export function quizReducer(state: PracticeState, action: QuizAction): PracticeState {
  const entry = currentEntry(state);
  switch (action.type) {
    case "pick": {
      // Đã khoá thì không nhận lần chọn thứ hai (bấm lại hay phím khác đều bỏ qua).
      if (state.finished || entry?.kind !== "mcq" || entry.submitted) return state;
      return practiceReducer(practiceReducer(state, { type: "select", key: action.key }), { type: "submit_mcq" });
    }
    case "next":
      // Giữ Enter (lặp phím) không nhảy qua câu chưa làm: chỉ sang câu khi câu đang mở đã trả lời.
      if (state.finished || entry === undefined || !isAnswered(entry)) return state;
      return practiceReducer(state, { type: "next" });
    case "skip_essay":
      if (state.finished || entry?.kind !== "essay" || entry.status !== "editing" || entry.error === null) return state;
      return practiceReducer(state, { type: "next" });
    default:
      return practiceReducer(state, action);
  }
}

/** Việc của một phím tắt trên màn Làm bài; `submit_essay` cần gọi chấm nên trả riêng để component gửi yêu cầu. */
export type QuizKeyEffect = { kind: "dispatch"; action: QuizAction } | { kind: "submit_essay" };

/**
 * Phím 1–4/A–D chọn và khoá ngay; Enter, N sang câu (chỉ khi đã trả lời, `resolveShortcut` lo ngữ cảnh); C mở đoạn
 * nguồn; Ctrl+Enter chấm câu tự luận. Màn này không còn bước "Nộp" riêng nên `submit` không có tác dụng.
 */
export function quizKeyEffect(action: ShortcutAction): QuizKeyEffect | null {
  switch (action.type) {
    case "select_option":
      return { kind: "dispatch", action: { type: "pick", key: action.key } };
    case "next":
      return { kind: "dispatch", action: { type: "next" } };
    case "open_citation":
      return { kind: "dispatch", action: { type: "toggle_source" } };
    case "submit_essay":
      return { kind: "submit_essay" };
    case "submit":
    case "open_palette":
    case "show_help":
    case "close":
      return null;
  }
}

// ------------------------------------------------------------------------------------------ phương án

export type OptionTone = "idle" | "right" | "wrong" | "dim";

export interface OptionState {
  key: OptionKey;
  /** Phím số tương ứng (1–4), theo vị trí của phương án. */
  digit: number;
  text: string;
  tone: OptionTone;
  /** Chữ đi kèm màu (đúng/sai luôn có chữ): "Đáp án đúng", "Bạn chọn" hoặc không có. */
  verdict: "Đáp án đúng" | "Bạn chọn" | null;
  /** Đã khoá: không bấm được nữa. */
  locked: boolean;
  ariaLabel: string;
}

/** Trạng thái hiển thị bốn phương án của câu trắc nghiệm; trước khi chọn không lộ đáp án. */
export function optionStates(question: McqQuestion, entry: Extract<AnswerEntry, { kind: "mcq" }>): OptionState[] {
  const locked = entry.submitted && entry.selected !== null;
  return OPTION_KEYS.map((key, position) => {
    const text = question.options[key];
    const isRight = locked && key === question.correctKey;
    const isWrong = locked && key === entry.selected && !isRight;
    const tone: OptionTone = !locked ? "idle" : isRight ? "right" : isWrong ? "wrong" : "dim";
    const suffix = isRight
      ? key === entry.selected
        ? ", đáp án đúng, bạn chọn"
        : ", đáp án đúng"
      : isWrong
        ? ", bạn chọn, sai"
        : "";
    return {
      key,
      digit: position + 1,
      text,
      tone,
      verdict: isRight ? "Đáp án đúng" : isWrong ? "Bạn chọn" : null,
      locked,
      ariaLabel: `Phương án ${key} (phím ${position + 1}): ${text}${suffix}`,
    };
  });
}

/** Giải thích một phương án nhiễu. `cited`: câu lý thuyết, kèm trích dẫn nguyên văn; `computed`: câu tính toán. */
export interface DistractorNote {
  key: OptionKey;
  label: string;
  rationale: string;
  /** Trích dẫn nguyên văn của khái niệm ứng với phương án nhiễu; `null` chỉ với câu tính toán. */
  citation: PracticeCitation | null;
}

function noteOf(question: McqQuestion, key: OptionKey, misconception: PracticeMisconception): DistractorNote | null {
  // Câu lý thuyết: giải thích là khẳng định kiến thức nên PHẢI kèm trích dẫn (I3); thiếu thì không hiện.
  if (question.questionKind === "theory" && misconception.citation === null) return null;
  return { key, label: misconception.label, rationale: misconception.rationale, citation: misconception.citation };
}

/**
 * Giải thích của các phương án nhiễu sau khi đã chọn: `chosen` là phương án sai người học chọn (nếu có), `others` là
 * các phương án sai còn lại (trong mục thu gọn). Trước khi chọn thì không có gì.
 */
export function distractorNotes(
  question: McqQuestion,
  entry: Extract<AnswerEntry, { kind: "mcq" }>,
): { chosen: DistractorNote | null; others: DistractorNote[] } {
  if (!entry.submitted || entry.selected === null) return { chosen: null, others: [] };
  const selected = entry.selected;
  const chosenMisconception = selected === question.correctKey ? undefined : question.misconceptions[selected];
  const chosen = chosenMisconception === undefined ? null : noteOf(question, selected, chosenMisconception);
  const others: DistractorNote[] = [];
  for (const key of OPTION_KEYS) {
    if (key === question.correctKey || key === selected) continue;
    const misconception = question.misconceptions[key];
    const note = misconception === undefined ? null : noteOf(question, key, misconception);
    if (note !== null) others.push(note);
  }
  return { chosen, others };
}

// --------------------------------------------------------------------------------------- chữ của câu

/** Dòng `aria-live` đọc kết quả ngay sau khi trả lời; chưa trả lời ⇒ chuỗi rỗng. */
export function answerAnnouncement(question: PracticeQuestion, entry: AnswerEntry): string {
  const source = `Nguồn: trang ${question.citation.page_number}.`;
  if (question.kind === "mcq" && entry.kind === "mcq") {
    if (!entry.submitted || entry.selected === null) return "";
    if (answerOutcome(entry.selected, question.correctKey) === "correct") return `Đúng. ${source}`;
    const right = question.options[question.correctKey];
    return `Chưa đúng. Đáp án đúng là ${question.correctKey}, “${right}”. ${source}`;
  }
  if (entry.kind === "essay" && entry.status === "graded" && entry.result !== null) {
    return `${essayHeadline(entry.result)}. ${source}`;
  }
  return "";
}

/** Gợi ý một dòng cạnh nút chính của câu. */
export function quizHint(entry: AnswerEntry): string {
  if (entry.kind === "mcq") return entry.submitted ? "Enter để đi tiếp." : "Bấm đáp án hoặc phím 1–4.";
  if (entry.status === "graded") return "Enter để đi tiếp.";
  if (entry.status === "grading") return "Đang chấm câu trả lời…";
  if (entry.error !== null && essayErrorPlacement(entry.error) === "screen") return "Bấm Thử lại, hoặc sang câu tiếp.";
  return "Viết xong thì bấm Chấm ý.";
}

/** Chữ của nút chính sau khi trả lời: câu cuối ⇒ "Xem kết quả". */
export function nextLabel(state: Pick<PracticeState, "index" | "questions">): string {
  return state.index >= state.questions.length - 1 ? "Xem kết quả" : "Câu tiếp";
}

/**
 * Lỗi chấm tự luận hiện ở đâu: lỗi dữ liệu nhập (câu trả lời trống, quá dài) ⇒ ngay dưới ô trả lời, sửa rồi chấm lại;
 * lỗi khác (mất kết nối, máy chủ, phản hồi lạ) ⇒ khung lỗi "Không chấm được câu này" kèm "Thử lại" và "Sang câu tiếp".
 */
export function essayErrorPlacement(error: UiError): "field" | "screen" {
  return error.kind === "validation" ? "field" : "screen";
}

export interface SourceCaption {
  /** "Trang N" (số trang backend trả). */
  pageLabel: string;
  /** Tên tài liệu khi đã tải được; không có thì `null` (KHÔNG thay bằng mã tài liệu). */
  title: string | null;
}

/** Chú thích của trích dẫn trong khung "Nguồn": số trang và tên tài liệu nếu biết, không bao giờ mã tài liệu. */
export function sourceCaption(citation: PracticeCitation, titles: ReadonlyMap<string, string>): SourceCaption {
  const title = titles.get(citation.doc_id)?.trim() ?? "";
  return { pageLabel: `Trang ${citation.page_number}`, title: title === "" ? null : title };
}

// ------------------------------------------------------------------------------------------ kết quả

export type ResultStatus = "correct" | "incorrect" | "essay" | "unanswered";

export interface ResultRow {
  twinId: string;
  status: ResultStatus;
  /** "Đúng", "Sai", "Khớp x/y ý" (số backend trả) hoặc "Chưa làm". */
  statusLabel: string;
  /** "Câu i. {stem}". */
  text: string;
  /** "Trang {citation.page_number}". */
  page: string;
}

function rowStatus(
  question: PracticeQuestion,
  entry: AnswerEntry | undefined,
): { status: ResultStatus; label: string } {
  if (entry?.kind === "mcq" && question.kind === "mcq" && entry.submitted && entry.selected !== null) {
    return answerOutcome(entry.selected, question.correctKey) === "correct"
      ? { status: "correct", label: "Đúng" }
      : { status: "incorrect", label: "Sai" };
  }
  if (entry?.kind === "essay" && entry.status === "graded" && entry.result !== null) {
    return { status: "essay", label: essayHeadline(entry.result) };
  }
  return { status: "unanswered", label: "Chưa làm" };
}

/** Danh sách từng câu ở màn Kết quả, theo thứ tự backend trả. */
export function resultRows(state: Pick<PracticeState, "questions" | "entries">): ResultRow[] {
  return state.questions.map((question, index) => {
    const { status, label } = rowStatus(question, state.entries[index]);
    return {
      twinId: question.twinId,
      status,
      statusLabel: label,
      text: `Câu ${index + 1}. ${question.stem}`,
      page: `Trang ${question.citation.page_number}`,
    };
  });
}

/** Màu sao: vàng = trắc nghiệm đúng, rỗng = trắc nghiệm sai, tím = tự luận đã chấm, mờ = chưa làm. */
export type StarTone = "gold" | "empty" | "violet" | "dim";

const STAR_TONE: Record<ResultStatus, StarTone> = {
  correct: "gold",
  incorrect: "empty",
  essay: "violet",
  unanswered: "dim",
};

export interface ResultStar {
  twinId: string;
  tone: StarTone;
  /** Góc đặt sao trên vòng (bố cục), chia đều theo số câu. */
  angle: number;
  /** Trễ của hiệu ứng hiện sao (bố cục): 120 ms + 90 ms mỗi câu. */
  delayMs: number;
}

/** Mỗi câu một sao, xếp đều quanh vòng theo thứ tự câu. */
export function resultStars(rows: readonly ResultRow[]): ResultStar[] {
  const count = Math.max(1, rows.length);
  return rows.map((row, index) => ({
    twinId: row.twinId,
    tone: STAR_TONE[row.status],
    angle: (index * 360) / count,
    delayMs: 120 + index * 90,
  }));
}

const STATUS_SPOKEN: Record<Exclude<ResultStatus, "essay">, string> = {
  correct: "đúng",
  incorrect: "sai",
  unanswered: "chưa làm",
};

/** Nhãn đọc của vòng sao (`role="img"`): số câu của đề rồi từng câu. Không có tổng số câu đúng. */
export function resultAriaLabel(rows: readonly ResultRow[]): string {
  const parts = rows.map((row, index) => {
    const spoken = row.status === "essay" ? `tự luận, ${row.statusLabel.toLowerCase()}` : STATUS_SPOKEN[row.status];
    return `Câu ${index + 1} ${spoken}`;
  });
  return `Vòng sao của đề ${rows.length} câu: ${parts.join("; ")}.`;
}

/** Chữ giữa vòng sao: số câu của đề (độ dài danh sách backend trả), không phải số câu đúng. */
export function ringCenterLabel(rows: readonly ResultRow[]): string {
  return `${rows.length} câu`;
}

/** Chú giải dưới vòng sao. */
export const RESULT_LEGEND_VI = [
  "Sao vàng: đúng",
  "Sao rỗng: sai",
  "Sao tím: câu tự luận (xem số ý khớp)",
  "Sao mờ: chưa làm",
] as const;

/** Toast khi bấm "Ôn lại câu sai" (chưa có ở bản này). */
export const REVIEW_WRONG_SOON_VI = "Ôn lại câu sai sẽ có ở bản sau";

// ------------------------------------------------------------------------------------ trang môn

/**
 * Trang môn chuyển sang toàn màn (ẩn bầu trời và hàng tab, giữ tab mounted) khi tab Ôn tập đang làm đề hoặc xem kết
 * quả. `session` là phần màn Ôn tập báo lên (nguồn chân lý), không phải tham số trên địa chỉ.
 */
export function practiceFullscreen(tab: SubjectTab, session: PracticeSessionKind | null): boolean {
  return tab === "practice" && session !== null;
}
