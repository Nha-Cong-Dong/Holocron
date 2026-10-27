/**
 * Logic thuần của màn luyện tập (F4): dựng yêu cầu tạo đề, trạng thái bài làm, chính sách ẩn/hiện đáp án và ánh xạ
 * phím tắt. Không tính con số nào của đề, đáp án, lời giải hay kết quả chấm (I1):
 * - đúng/sai của MCQ4 là phép so khoá đã chọn với `correct_key` do backend trả;
 * - kết quả tự luận là `EssayGradeView` backend trả, giữ nguyên;
 * - không đếm tổng số câu đúng của lượt (kế hoạch UI mục 9 câu 3: chờ người dùng quyết).
 */

import { invalidResponseError, type UiError } from "@/lib/errors";
import { essayHeadline } from "@/lib/essay";
import { CHAPTER_CODE_PATTERN, MAX_THEORY_SLOTS } from "@/lib/limits";
import type { EssayGradeView, PracticeQuestion } from "@/lib/practice-data";
import type { CapabilityView } from "@/lib/runtime";
import type { ShortcutAction, ShortcutScope } from "@/lib/shortcuts";
import type { BlueprintCreate, ExamFormat, TheorySlotFormat, TheorySlotSpec } from "@/types/contract-pending";
import type { OptionKey } from "@/types/holocron";

// ----------------------------------------------------------------------------------------- tạo đề

/** Lựa chọn hình thức trên form: một hình thức cho mọi câu, hoặc kết hợp trắc nghiệm và tự luận. */
export type FormatChoice = "MCQ4" | "ESSAY" | "mixed";

export const FORMAT_CHOICE_LABEL_VI: Record<FormatChoice, string> = {
  MCQ4: "Trắc nghiệm (4 phương án)",
  ESSAY: "Tự luận (trình bày khái niệm)",
  mixed: "Kết hợp trắc nghiệm và tự luận",
};

export const EXAM_FORMAT_LABEL_VI: Record<ExamFormat, string> = {
  mcq: "thi trắc nghiệm",
  essay: "thi tự luận",
  mixed: "thi kết hợp",
};

/** Số câu mặc định của một đề luyện tập. */
export const DEFAULT_QUESTION_COUNT = 5;
export const MIN_QUESTION_COUNT = 1;

/**
 * Các hình thức được chọn. Chấm tự luận không khả dụng thì bỏ mọi lựa chọn có tự luận; KHÔNG đổi sang trắc nghiệm thay
 * người dùng (spec §1.5: code không tự hạ ESSAY xuống MCQ4).
 */
export function formatChoices(essayAvailable: boolean): FormatChoice[] {
  return essayAvailable ? ["MCQ4", "ESSAY", "mixed"] : ["MCQ4"];
}

/**
 * Lựa chọn điền sẵn theo `exam_format` của môn (chỉ để điền sẵn; yêu cầu gửi đi luôn ghi hình thức tường minh).
 * Lựa chọn mặc định không được phép (ví dụ môn thi tự luận khi chấm tự luận tắt) thì trả `null`: người dùng tự chọn.
 */
export function defaultFormatChoice(examFormat: ExamFormat, allowed: readonly FormatChoice[]): FormatChoice | null {
  const preferred: FormatChoice = examFormat === "mcq" ? "MCQ4" : examFormat === "essay" ? "ESSAY" : "mixed";
  return allowed.includes(preferred) ? preferred : null;
}

/** Dòng giải thích khi môn thi tự luận hoặc kết hợp nhưng chưa dùng được tự luận (đề chỉ gồm trắc nghiệm). */
export const ESSAY_NOT_AVAILABLE_VI = "Tự luận chưa có trong bản này";

/**
 * Chỉ cho chọn câu tự luận khi backend báo rõ khả năng chấm tự luận offline là khả dụng. Backend chưa báo danh sách
 * khả năng (`unknown`) hay báo không khả dụng thì chỉ còn trắc nghiệm, để luồng mặc định không gửi slot ESSAY rồi nhận
 * 409 (`CapabilityError`).
 */
export function essayChoiceAvailable(essayGrading: CapabilityView): boolean {
  return essayGrading.state === "available";
}

/**
 * Lựa chọn điền sẵn của form tạo đề. Tự luận dùng được thì theo `exam_format` của môn (`defaultFormatChoice`); không
 * thì luôn là trắc nghiệm, kể cả môn thi tự luận hay kết hợp (giao diện kèm dòng `ESSAY_NOT_AVAILABLE_VI`).
 */
export function prefilledFormatChoice(examFormat: ExamFormat, essayAvailable: boolean): FormatChoice {
  if (!essayAvailable) return "MCQ4";
  return defaultFormatChoice(examFormat, formatChoices(true)) ?? "MCQ4";
}

export interface BuilderInput {
  subjectCode: string;
  chapterCode: string;
  questionCount: number;
  choice: FormatChoice | null;
}

export type BuilderResult = { ok: true; payload: BlueprintCreate } | { ok: false; message: string };

/** Hình thức của câu thứ `position` (từ 0): "kết hợp" xen kẽ trắc nghiệm rồi tự luận. */
function slotFormat(choice: FormatChoice, position: number): TheorySlotFormat {
  if (choice === "mixed") return position % 2 === 0 ? "MCQ4" : "ESSAY";
  return choice;
}

/** Kiểm form rồi dựng `BlueprintCreate` chỉ có slot lý thuyết (lát cắt dọc §4, §6.7). */
export function buildBlueprintCreate(input: BuilderInput): BuilderResult {
  const chapterCode = input.chapterCode.trim();
  if (!CHAPTER_CODE_PATTERN.test(chapterCode)) {
    return { ok: false, message: "Mã chương có dạng ch1, ch12 hoặc ch2.1 (chữ thường, không dấu cách)." };
  }
  const count = input.questionCount;
  if (!Number.isInteger(count) || count < MIN_QUESTION_COUNT || count > MAX_THEORY_SLOTS) {
    return { ok: false, message: `Số câu là số nguyên từ ${MIN_QUESTION_COUNT} đến ${MAX_THEORY_SLOTS}.` };
  }
  if (input.choice === null) {
    return { ok: false, message: "Chọn hình thức câu hỏi." };
  }
  const choice = input.choice;
  const theorySlots: TheorySlotSpec[] = Array.from({ length: count }, (_, position) => ({
    chapter_code: chapterCode,
    cognitive_level: "remember",
    format: slotFormat(choice, position),
  }));
  return { ok: true, payload: { subject_code: input.subjectCode, item_ids: [], theory_slots: theorySlots } };
}

// ---------------------------------------------------------------------------------- trạng thái bài làm

export type EssayStatus = "editing" | "grading" | "graded";

export type AnswerEntry =
  | { kind: "mcq"; selected: OptionKey | null; submitted: boolean }
  | {
      kind: "essay";
      draft: string;
      status: EssayStatus;
      result: EssayGradeView | null;
      error: UiError | null;
      /**
       * Mã lượt gửi chấm gần nhất của câu này (`null` khi chưa gửi). Phản hồi mang mã khác là của lượt cũ (ví dụ gửi
       * trước khi "Làm lại từ đầu") và bị bỏ, để kết quả cũ không bị gán cho câu trả lời mới.
       */
      submission: number | null;
    };

export interface PracticeState {
  questions: PracticeQuestion[];
  entries: AnswerEntry[];
  /** Câu đang mở (từ 0). */
  index: number;
  /** Khối "xem đoạn nguồn" của câu đang mở. */
  sourceOpen: boolean;
  /** Đã đi qua câu cuối: hiện danh sách kết quả từng câu. */
  finished: boolean;
}

export type PracticeAction =
  | { type: "select"; key: OptionKey }
  | { type: "submit_mcq" }
  | { type: "edit_essay"; text: string }
  | { type: "essay_started"; twinId: string; submission: number }
  | { type: "essay_graded"; twinId: string; submission: number; result: EssayGradeView }
  | { type: "essay_failed"; twinId: string; submission: number; error: UiError }
  | { type: "essay_rejected"; error: UiError }
  | { type: "next" }
  | { type: "previous" }
  | { type: "go_to"; index: number }
  | { type: "toggle_source" }
  | { type: "restart" };

/** Kết quả chấm trả về mang `twin_id` của câu khác: coi là phản hồi không hợp lệ, cho nộp lại (giữ hành động "Thử lại"). */
export function mismatchedGradeError(): UiError {
  return {
    ...invalidResponseError(),
    message: "Phản hồi chấm không hợp lệ: kết quả trả về không thuộc câu này. Bản nháp vẫn còn, bấm nộp để chấm lại.",
  };
}

function emptyEntry(question: PracticeQuestion): AnswerEntry {
  return question.kind === "mcq"
    ? { kind: "mcq", selected: null, submitted: false }
    : { kind: "essay", draft: "", status: "editing", result: null, error: null, submission: null };
}

export function initialPracticeState(questions: PracticeQuestion[]): PracticeState {
  return { questions, entries: questions.map(emptyEntry), index: 0, sourceOpen: false, finished: false };
}

/** Câu đã nộp (MCQ) hoặc đã chấm (tự luận): từ lúc này mới hiện đáp án, trích dẫn và giải thích. */
export function isAnswered(entry: AnswerEntry): boolean {
  return entry.kind === "mcq" ? entry.submitted : entry.status === "graded";
}

function replaceEntry(state: PracticeState, index: number, entry: AnswerEntry): PracticeState {
  const entries = state.entries.map((current, position) => (position === index ? entry : current));
  return { ...state, entries };
}

function moveTo(state: PracticeState, index: number): PracticeState {
  if (index < 0 || index >= state.questions.length) return state;
  return { ...state, index, sourceOpen: false, finished: false };
}

export function practiceReducer(state: PracticeState, action: PracticeAction): PracticeState {
  const entry = state.entries[state.index];
  switch (action.type) {
    case "select":
      if (state.finished || entry?.kind !== "mcq" || entry.submitted) return state;
      return replaceEntry(state, state.index, { ...entry, selected: action.key });
    case "submit_mcq":
      if (state.finished || entry?.kind !== "mcq" || entry.submitted || entry.selected === null) return state;
      return replaceEntry(state, state.index, { ...entry, submitted: true });
    case "edit_essay":
      if (state.finished || entry?.kind !== "essay" || entry.status !== "editing") return state;
      return replaceEntry(state, state.index, { ...entry, draft: action.text, error: null });
    case "essay_rejected":
      // Câu trả lời không gửi được (kiểm ở giao diện, ví dụ đang trống): giữ bản nháp, chỉ báo lỗi.
      if (state.finished || entry?.kind !== "essay" || entry.status !== "editing") return state;
      return replaceEntry(state, state.index, { ...entry, error: action.error });
    case "essay_started":
    case "essay_graded":
    case "essay_failed": {
      // Kết quả chấm có thể về sau khi người dùng đã chuyển câu: tìm đúng câu theo `twin_id`, không theo câu đang mở.
      const index = state.questions.findIndex((question) => question.twinId === action.twinId);
      const target = state.entries[index];
      if (target?.kind !== "essay") return state;
      if (action.type === "essay_started") {
        if (target.status !== "editing") return state;
        return replaceEntry(state, index, { ...target, status: "grading", error: null, submission: action.submission });
      }
      // Chỉ phản hồi của lượt gửi đang chờ mới được áp; lượt cũ (kể cả trước "Làm lại từ đầu") bị bỏ.
      if (target.status !== "grading" || target.submission !== action.submission) return state;
      if (action.type === "essay_graded" && action.result.twinId === action.twinId) {
        return replaceEntry(state, index, { ...target, status: "graded", result: action.result, error: null });
      }
      // Lỗi chấm, hoặc kết quả mang `twin_id` của câu khác (phản hồi không hợp lệ): giữ bản nháp để nộp lại.
      const error = action.type === "essay_failed" ? action.error : mismatchedGradeError();
      return replaceEntry(state, index, { ...target, status: "editing", error });
    }
    case "next":
      if (state.finished) return state;
      if (state.index >= state.questions.length - 1) return { ...state, sourceOpen: false, finished: true };
      return moveTo(state, state.index + 1);
    case "previous":
      if (state.finished) return moveTo(state, state.questions.length - 1);
      return moveTo(state, state.index - 1);
    case "go_to":
      return moveTo(state, action.index);
    case "toggle_source":
      if (state.finished || entry === undefined || !isAnswered(entry)) return state;
      return { ...state, sourceOpen: !state.sourceOpen };
    case "restart":
      return initialPracticeState(state.questions);
  }
}

/** Câu trả lời tự luận gửi đi được hay không; chỉ kiểm rỗng, độ dài tối đa do backend kiểm (cấu hình, §6.9). */
export type EssaySubmission = { ok: true; answer: string } | { ok: false; message: string };

export function essaySubmission(draft: string): EssaySubmission {
  if (draft.trim() === "") return { ok: false, message: "Câu trả lời đang trống. Viết câu trả lời rồi nộp lại." };
  return { ok: true, answer: draft };
}

// ------------------------------------------------------------------------------- slot không sinh được

/**
 * Phần gợi ý nói rõ giao diện chưa có màn tạo cặp khái niệm. Không gợi ý xác nhận nhãn chunk: việc đó không tạo ra
 * cặp khái niệm.
 */
export const PAIR_SCREEN_MISSING_VI = "Giao diện chưa có màn tạo cặp khái niệm nên chưa bổ sung được từ đây.";

/**
 * Gợi ý chung cho slot lý thuyết không sinh được: nêu đúng tình trạng (chương chưa có cặp khái niệm đã xác nhận, màn tạo
 * cặp chưa có); không nêu ngưỡng cụ thể (lý do cụ thể do backend ghi).
 */
export const VAULT_HINT_VI = `Chương này chưa có đủ cặp khái niệm đã xác nhận. ${PAIR_SCREEN_MISSING_VI}`;

/**
 * Nối lý do backend ghi với một câu gợi ý: thêm dấu chấm khi lý do chưa kết thúc bằng dấu câu, để không ra "..".
 * Lý do giữ nguyên văn (chỉ bỏ khoảng trắng thừa ở cuối).
 */
export function appendSentence(reason: string, sentence: string): string {
  const base = reason.trimEnd();
  if (base === "") return sentence;
  return /[.!?…:;]$/u.test(base) ? `${base} ${sentence}` : `${base}. ${sentence}`;
}

// ------------------------------------------------------------------------------------ kết quả từng câu

export type McqOutcome = "correct" | "incorrect";

/** Đúng/sai là phép so khoá đã chọn với `correct_key` backend trả; không tính gì thêm. */
export function answerOutcome(selected: OptionKey, correctKey: OptionKey): McqOutcome {
  return selected === correctKey ? "correct" : "incorrect";
}

export interface RevealPolicy {
  correctKey: boolean;
  misconceptions: boolean;
  citation: boolean;
  solution: boolean;
  essayPoints: boolean;
}

/**
 * Trước khi nộp: ẩn `correct_key`, badge misconception, trích dẫn (với câu lý thuyết, trích dẫn chính là đáp án), lời
 * giải và các ý của câu tự luận (§6.9). Sau khi nộp: hiện tất cả.
 */
export function revealPolicy(entry: AnswerEntry): RevealPolicy {
  const shown = isAnswered(entry);
  return {
    correctKey: shown && entry.kind === "mcq",
    misconceptions: shown && entry.kind === "mcq",
    citation: shown,
    solution: shown,
    essayPoints: shown && entry.kind === "essay",
  };
}

/** Nhãn kết quả một câu cho danh sách cuối lượt; không có con số tổng. */
export function entrySummary(question: PracticeQuestion, entry: AnswerEntry): string {
  if (entry.kind === "mcq") {
    if (!entry.submitted || entry.selected === null || question.kind !== "mcq") return "Chưa làm";
    return answerOutcome(entry.selected, question.correctKey) === "correct"
      ? `Đúng (chọn ${entry.selected})`
      : `Sai (chọn ${entry.selected}, đáp án ${question.correctKey})`;
  }
  if (entry.status !== "graded" || entry.result === null) return "Chưa làm";
  return essayHeadline(entry.result);
}

// ------------------------------------------------------------------------------------------- phím tắt

/** Ngữ cảnh phím tắt của câu đang mở (kế hoạch UI mục 5.7). Màn danh sách cuối lượt dùng ngữ cảnh chung. */
export function practiceScope(state: PracticeState): ShortcutScope {
  const entry = state.entries[state.index];
  if (state.finished || entry === undefined) return { kind: "global" };
  if (entry.kind === "essay") return { kind: "essay", phase: entry.status === "graded" ? "graded" : "answering" };
  if (entry.submitted) return { kind: "mcq", phase: "submitted" };
  return { kind: "mcq", phase: entry.selected === null ? "unanswered" : "selected" };
}

/**
 * Hành động của màn luyện tập ứng với một phím tắt; `null` khi phím đó không thuộc màn này (`Ctrl+K`, `?`, `Esc`
 * do khung app và hộp thoại xử lý). `submit_essay` cần gọi backend nên trả riêng để component gửi yêu cầu chấm.
 */
export type PracticeKeyEffect = { kind: "dispatch"; action: PracticeAction } | { kind: "submit_essay" };

export function practiceKeyEffect(action: ShortcutAction): PracticeKeyEffect | null {
  switch (action.type) {
    case "select_option":
      return { kind: "dispatch", action: { type: "select", key: action.key } };
    case "submit":
      return { kind: "dispatch", action: { type: "submit_mcq" } };
    case "next":
      return { kind: "dispatch", action: { type: "next" } };
    case "open_citation":
      return { kind: "dispatch", action: { type: "toggle_source" } };
    case "submit_essay":
      return { kind: "submit_essay" };
    case "open_palette":
    case "show_help":
    case "close":
      return null;
  }
}
