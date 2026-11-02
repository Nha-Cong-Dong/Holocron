/**
 * Logic thuần của phần thiết lập đề ở tab Ôn tập (design-handoff/README.md màn 8): chip chương kèm số khái niệm đã
 * nhận, lựa chọn mặc định, dòng tóm tắt, hình thức được chọn, dựng `BlueprintCreate` nhiều chương, và câu báo cho câu
 * chưa soạn được.
 *
 * Không tính con số nào của đề hay đáp án (I1): số khái niệm, số chương là số đếm danh sách khái niệm backend trả; số
 * câu là lựa chọn của người dùng. Không rẽ nhánh theo môn hay chương (CLAUDE.md §1).
 */

import type { UiError } from "@/lib/errors";
import { CHAPTER_CODE_PATTERN, MAX_THEORY_SLOTS } from "@/lib/limits";
import {
  type BuilderResult,
  type FormatChoice,
  MIN_QUESTION_COUNT,
  prefilledFormatChoice,
  slotFormat,
} from "@/lib/practice";
import type { PracticeBlueprintSlot, PracticeTicket } from "@/lib/practice-data";
import { chapterLabel, countChapters, type SkyPair } from "@/lib/sky";
import type { ExamFormat, TheorySlotSpec } from "@/types/holocron";

/** Số câu chọn được (chip). Backend nhận tối đa `MAX_THEORY_SLOTS` câu lý thuyết mỗi đề. */
export const QUESTION_COUNTS = [5, 10, 20] as const;

/** Thứ tự chip hình thức. */
export const FORMAT_OPTIONS: readonly FormatChoice[] = ["MCQ4", "ESSAY", "mixed"];

export const NO_CHAPTER_SELECTED_VI = "Chọn ít nhất 1 chòm sao.";

// ------------------------------------------------------------------------------------ chương

export interface ChapterChip {
  code: string;
  /** "Chương N" (backend không có tên chương). */
  label: string;
  /** Số khái niệm đã nhận của chương (đếm danh sách backend trả). */
  confirmed: number;
  /** Chương có khái niệm nhưng chưa nhận cái nào: chưa ra đề được. */
  disabled: boolean;
}

/** Một chip cho mỗi chương có khái niệm (mọi trạng thái), theo thứ tự tự nhiên của mã chương. */
export function chapterChips(pairs: readonly SkyPair[]): ChapterChip[] {
  return countChapters(pairs).map((chapter) => ({
    code: chapter.code,
    label: chapterLabel(chapter.code),
    confirmed: chapter.confirmed,
    disabled: chapter.confirmed === 0,
  }));
}

/** Mặc định chọn mọi chương có ít nhất 1 khái niệm đã nhận. */
export function defaultSelection(chips: readonly ChapterChip[]): string[] {
  return chips.filter((chip) => !chip.disabled).map((chip) => chip.code);
}

/** Chương đang chọn theo thứ tự chip, bỏ chương không còn chọn được (ví dụ vừa bỏ hết khái niệm của chương đó). */
export function selectedChapters(chips: readonly ChapterChip[], selected: ReadonlySet<string>): ChapterChip[] {
  return chips.filter((chip) => !chip.disabled && selected.has(chip.code));
}

/** Tổng số khái niệm đã nhận (mọi chương); 0 ⇒ trạng thái rỗng "Chưa đủ khái niệm để ra đề". */
export function confirmedTotal(chips: readonly ChapterChip[]): number {
  return chips.reduce((sum, chip) => sum + chip.confirmed, 0);
}

/** Dòng tóm tắt: "Đề {N} câu từ {M} khái niệm của {K} chòm sao." (không có thời gian làm bài). */
export function setupSummary(count: number, chips: readonly ChapterChip[], selected: ReadonlySet<string>): string {
  const chosen = selectedChapters(chips, selected);
  if (chosen.length === 0) return NO_CHAPTER_SELECTED_VI;
  return `Đề ${count} câu từ ${confirmedTotal(chosen)} khái niệm của ${chosen.length} chòm sao.`;
}

// ------------------------------------------------------------------------------------ hình thức

export interface FormatOption {
  choice: FormatChoice;
  /** Lựa chọn có tự luận khi chấm tự luận không khả dụng: hiện nhưng không chọn được (không tự đổi sang trắc nghiệm). */
  disabled: boolean;
}

export function formatOptions(essayAvailable: boolean): FormatOption[] {
  return FORMAT_OPTIONS.map((choice) => ({ choice, disabled: !essayAvailable && choice !== "MCQ4" }));
}

/**
 * Hình thức chọn sẵn. `Subject.exam_format` luôn có (môn tạo không chọn hình thức mặc định là `mixed`, §6.8):
 * - chấm tự luận khả dụng ⇒ theo hình thức thi của môn (`mixed` ⇒ "Kết hợp");
 * - chấm tự luận không khả dụng ⇒ "Trắc nghiệm" với mọi môn (lựa chọn có tự luận bị khoá, không gửi slot ESSAY).
 */
export function initialFormat(examFormat: ExamFormat, essayAvailable: boolean): FormatChoice {
  return prefilledFormatChoice(examFormat, essayAvailable);
}

// ------------------------------------------------------------------------------------ dựng đề

export interface MultiChapterInput {
  subjectCode: string;
  /** Mã chương theo thứ tự chip. */
  chapters: readonly string[];
  count: number;
  choice: FormatChoice | null;
}

/**
 * `BlueprintCreate` chỉ có slot lý thuyết, rải đều các chương: câu thứ i (từ 0) thuộc chương `chapters[i % k]`, hình
 * thức `slotFormat(choice, ⌊i / k⌋)` (với "Kết hợp", mỗi chương xen kẽ trắc nghiệm rồi tự luận), mức nhận thức
 * `remember`.
 */
export function buildMultiChapterBlueprint(input: MultiChapterInput): BuilderResult {
  const chapters = input.chapters;
  if (chapters.length === 0) return { ok: false, message: NO_CHAPTER_SELECTED_VI };
  if (chapters.some((code) => !CHAPTER_CODE_PATTERN.test(code))) {
    return {
      ok: false,
      message: "Có chương mang mã không đúng dạng nên chưa ra đề được. Bỏ chọn chương đó rồi thử lại.",
    };
  }
  const count = input.count;
  if (!Number.isInteger(count) || count < MIN_QUESTION_COUNT || count > MAX_THEORY_SLOTS) {
    return { ok: false, message: `Số câu là số nguyên từ ${MIN_QUESTION_COUNT} đến ${MAX_THEORY_SLOTS}.` };
  }
  const choice = input.choice;
  if (choice === null) return { ok: false, message: "Chọn hình thức câu hỏi." };
  const k = chapters.length;
  const theorySlots: TheorySlotSpec[] = Array.from({ length: count }, (_, index) => ({
    chapter_code: chapters[index % k] ?? "",
    cognitive_level: "remember",
    format: slotFormat(choice, Math.floor(index / k)),
  }));
  return { ok: true, payload: { subject_code: input.subjectCode, item_ids: [], theory_slots: theorySlots } };
}

// ------------------------------------------------------------------------------------ câu chưa soạn được

/**
 * Câu báo cho một câu chưa soạn được (`review_tickets`). Backend ghi lý do bằng văn xuôi tự do, không có mã lý do,
 * nên KHÔNG so chuỗi lý do để đoán nguyên nhân: câu lý thuyết dùng một câu chung kèm việc nên làm (nhận thêm khái
 * niệm ở chương của câu đó); câu khác gợi ý tạo đề khác. Lý do nguyên văn chỉ hiện trong "Chi tiết kỹ thuật".
 */
export function ticketText(ticket: PracticeTicket, slots: readonly PracticeBlueprintSlot[]): string {
  const position = `Câu thứ ${ticket.slotIndex}`;
  if (!ticket.theory) return `${position} chưa soạn được trong lượt này. Bấm “Tạo đề khác” để soạn lại.`;
  const chapter = slots.find((slot) => slot.slotIndex === ticket.slotIndex)?.chapterCode;
  if (chapter === undefined) {
    return `${position} chưa soạn được từ các khái niệm đã nhận. Nhận thêm khái niệm ở tab Khái niệm rồi tạo đề lại.`;
  }
  const label = chapterLabel(chapter);
  return `${position} (${label}) chưa soạn được từ các khái niệm đã nhận. Nhận thêm khái niệm của ${label} ở tab Khái niệm rồi tạo đề lại.`;
}

/** Nguyên văn lý do backend ghi, để đặt trong "Chi tiết kỹ thuật". */
export function ticketTechnical(tickets: readonly PracticeTicket[]): string {
  return tickets.map((ticket) => `#${ticket.slotIndex}: ${ticket.reason}`).join("\n");
}

/**
 * "Chi tiết kỹ thuật" khi chưa soạn được đề: mã lỗi (`diagnostic`) rồi danh sách vấn đề do bước kiểm duyệt đề viết
 * (`issues`), mỗi mục một dòng. Câu hướng dẫn chính không chứa các mục này. Không có gì ⇒ `undefined` (không hiện khối).
 */
export function failureTechnical(error: Pick<UiError, "diagnostic" | "issues">): string | undefined {
  const lines = [...(error.diagnostic === undefined ? [] : [error.diagnostic]), ...error.issues];
  return lines.length === 0 ? undefined : lines.join("\n");
}
