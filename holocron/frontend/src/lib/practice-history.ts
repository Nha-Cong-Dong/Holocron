/**
 * Logic thuần của lịch sử làm bài và ôn câu sai (spec §5.17, §6.12). Đúng/sai (`outcome`) và mọi số đếm do backend
 * trả; ở đây chỉ dựng request, kiểm dạng response và dựng nhãn hiển thị, KHÔNG so khoá hay đếm lại (I1).
 */

import type { AnswerEntry } from "@/lib/practice";
import { type PracticeQuestion, parseTwin } from "@/lib/practice-data";
import type {
  AnswerOutcome,
  OptionKey,
  PracticeAnswer,
  PracticeAttempt,
  PracticeAttemptCreate,
  PracticeAttemptSummary,
  PracticeMode,
} from "@/types/holocron";

/** Số lượt mỗi trang của màn lịch sử (`limit` của §6.12 là 1–200). */
export const HISTORY_PAGE_SIZE = 20;
/** Số câu ôn tải mỗi lần (`limit` mặc định của §6.12; danh sách do backend giữ, tải lại sau mỗi lần ôn). */
export const REVIEW_PAGE_SIZE = 50;

const OPTION_KEYS: readonly OptionKey[] = ["A", "B", "C", "D"];

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isCount(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 0;
}

function optionKeyOf(value: unknown): OptionKey | null {
  return OPTION_KEYS.find((key) => key === value) ?? null;
}

// ------------------------------------------------------------------------------------------- request

/**
 * Dựng body `POST /v1/practice/attempts` từ các câu trắc nghiệm đã nộp. Chỉ MCQ4 ghi lượt được (§5.17): câu tự luận
 * bị bỏ. Câu chưa chọn không được gửi. `null` khi không còn câu nào để ghi. Request KHÔNG mang đúng/sai hay
 * `correct_key` (I1).
 */
export function buildAttemptCreate(
  mode: PracticeMode,
  batchId: string | null,
  questions: readonly PracticeQuestion[],
  entries: readonly AnswerEntry[],
): PracticeAttemptCreate | null {
  if (mode === "batch" && batchId === null) return null;
  const answers: PracticeAttemptCreate["answers"] = [];
  questions.forEach((question, position) => {
    const entry = entries[position];
    if (question.kind !== "mcq" || entry?.kind !== "mcq" || !entry.submitted || entry.selected === null) return;
    answers.push({ twin_id: question.twinId, chosen_key: entry.selected });
  });
  if (answers.length === 0) return null;
  return { mode, batch_id: mode === "batch" ? batchId : null, answers };
}

// ------------------------------------------------------------------------------------------ response

function parseAnswer(value: unknown): PracticeAnswer | null {
  if (!isRecord(value)) return null;
  const correctKey = optionKeyOf(value.correct_key);
  const chosen = optionKeyOf(value.chosen_key);
  const { outcome } = value;
  if (
    !isCount(value.ordinal) ||
    typeof value.twin_id !== "string" ||
    typeof value.batch_id !== "string" ||
    !isCount(value.slot_index) ||
    correctKey === null ||
    (outcome !== "correct" && outcome !== "incorrect" && outcome !== "skipped") ||
    (value.chosen_key !== null && chosen === null)
  ) {
    return null;
  }
  return {
    ordinal: value.ordinal,
    twin_id: value.twin_id,
    batch_id: value.batch_id,
    slot_index: value.slot_index,
    chosen_key: chosen,
    correct_key: correctKey,
    outcome,
  };
}

/** `PracticeAttemptSummary` (§5.17); `null` khi sai dạng. */
export function parseAttemptSummary(value: unknown): PracticeAttemptSummary | null {
  if (!isRecord(value)) return null;
  const { attempt_id, subject_code, mode, batch_id, blueprint_id, submitted_at } = value;
  if (
    typeof attempt_id !== "string" ||
    typeof subject_code !== "string" ||
    (mode !== "batch" && mode !== "review") ||
    (batch_id !== null && typeof batch_id !== "string") ||
    (blueprint_id !== null && typeof blueprint_id !== "string") ||
    typeof submitted_at !== "string" ||
    !isCount(value.total_count) ||
    !isCount(value.correct_count) ||
    !isCount(value.incorrect_count) ||
    !isCount(value.skipped_count)
  ) {
    return null;
  }
  return {
    attempt_id,
    subject_code,
    mode,
    batch_id,
    blueprint_id,
    submitted_at,
    total_count: value.total_count,
    correct_count: value.correct_count,
    incorrect_count: value.incorrect_count,
    skipped_count: value.skipped_count,
  };
}

/** `PracticeAttempt` (`POST` trả về); `null` khi sai dạng. */
export function parseAttempt(value: unknown): PracticeAttempt | null {
  const summary = parseAttemptSummary(value);
  if (summary === null || !isRecord(value) || !Array.isArray(value.answers)) return null;
  const answers: PracticeAnswer[] = [];
  for (const raw of value.answers) {
    const answer = parseAnswer(raw);
    if (answer === null) return null;
    answers.push(answer);
  }
  return { ...summary, answers };
}

export interface AttemptPageView {
  total: number;
  attempts: PracticeAttemptSummary[];
}

/** `PracticeAttemptPage`; `null` khi sai dạng. */
export function parseAttemptPage(value: unknown): AttemptPageView | null {
  if (!isRecord(value) || !isCount(value.total) || !Array.isArray(value.attempts)) return null;
  const attempts: PracticeAttemptSummary[] = [];
  for (const raw of value.attempts) {
    const attempt = parseAttemptSummary(raw);
    if (attempt === null) return null;
    attempts.push(attempt);
  }
  return { total: value.total, attempts };
}

/** Một câu cần ôn, đã đổi `twin` thành câu hiển thị được bằng thành phần câu hỏi của màn luyện tập. */
export interface ReviewItem {
  question: PracticeQuestion;
  batchId: string;
  lastAttemptId: string;
  lastAnsweredAt: string;
  lastChosenKey: OptionKey;
}

export interface ReviewPageView {
  /** `total` do backend trả (số câu cần ôn khớp bộ lọc), không đếm lại ở frontend. */
  total: number;
  items: ReviewItem[];
  /** Số câu backend trả mà màn này chưa hiển thị được (sai dạng hoặc hình thức chưa hỗ trợ). */
  undisplayable: number;
}

/** `ReviewQuestionPage`; câu sai dạng bị bỏ và được báo ở `undisplayable`. `null` khi cấp ngoài sai dạng. */
export function parseReviewPage(value: unknown): ReviewPageView | null {
  if (!isRecord(value) || !isCount(value.total) || !Array.isArray(value.questions)) return null;
  const items: ReviewItem[] = [];
  let undisplayable = 0;
  for (const raw of value.questions) {
    if (!isRecord(raw)) {
      undisplayable += 1;
      continue;
    }
    const parsed = parseTwin(raw.twin);
    const chosen = optionKeyOf(raw.last_chosen_key);
    if (
      parsed.kind !== "ok" ||
      parsed.question.kind !== "mcq" ||
      chosen === null ||
      typeof raw.batch_id !== "string" ||
      typeof raw.last_attempt_id !== "string" ||
      typeof raw.last_answered_at !== "string"
    ) {
      undisplayable += 1;
      continue;
    }
    items.push({
      question: parsed.question,
      batchId: raw.batch_id,
      lastAttemptId: raw.last_attempt_id,
      lastAnsweredAt: raw.last_answered_at,
      lastChosenKey: chosen,
    });
  }
  return { total: value.total, items, undisplayable };
}

// -------------------------------------------------------------------------------------------- nhãn

export const OUTCOME_LABEL_VI: Record<AnswerOutcome, string> = {
  correct: "Đúng",
  incorrect: "Sai",
  skipped: "Bỏ qua",
};

export const MODE_LABEL_VI: Record<PracticeMode, string> = {
  batch: "Làm đề",
  review: "Ôn câu sai",
};

/** Thời điểm hiển thị theo giờ máy (giờ server là UTC); chuỗi không đọc được thì giữ nguyên, không đoán. */
export function formatSubmittedAt(iso: string): string {
  const time = Date.parse(iso);
  if (Number.isNaN(time)) return iso;
  return new Date(time).toLocaleString("vi-VN", { dateStyle: "short", timeStyle: "short" });
}

/** Dòng kết quả của một lượt: chỉ ghép số đếm backend trả, không tính thêm. */
export function attemptCountsLine(attempt: PracticeAttemptSummary): string {
  const parts = [`${attempt.correct_count} đúng`, `${attempt.incorrect_count} sai`];
  if (attempt.skipped_count > 0) parts.push(`${attempt.skipped_count} bỏ qua`);
  return `${parts.join(", ")} (${attempt.total_count} câu)`;
}

/** `offset` của trang kế tiếp, hoặc `null` khi đã hết (theo `total` backend trả). */
export function nextOffset(offset: number, shown: number, total: number): number | null {
  const next = offset + shown;
  return shown > 0 && next < total ? next : null;
}

// ------------------------------------------------------------------------- hàng đợi lượt chưa lưu

export type PendingStatus = "saving" | "failed" | "saved";

/** Một lượt làm bài đang gửi, gửi lỗi hoặc đã lưu; khoá theo bộ đề để mỗi bộ đề chỉ ghi một lượt. */
export interface PendingAttempt {
  key: string;
  body: PracticeAttemptCreate;
  status: PendingStatus;
}

export type PendingAction =
  | { type: "enqueue"; key: string; body: PracticeAttemptCreate }
  | { type: "retry"; key: string }
  | { type: "saved"; key: string }
  | { type: "failed"; key: string };

function setStatus(queue: readonly PendingAttempt[], key: string, from: PendingStatus, to: PendingStatus) {
  return queue.map((item) => (item.key === key && item.status === from ? { ...item, status: to } : item));
}

/**
 * Hàng đợi lượt làm bài sống ở PracticeView (không mất khi rời màn làm bài). `enqueue` bỏ qua khoá đã có (đang gửi,
 * lỗi hay đã lưu) nên rời bài nhiều đường không ghi trùng lượt; `retry` chỉ áp cho lượt đang lỗi.
 */
export function pendingReducer(queue: readonly PendingAttempt[], action: PendingAction): PendingAttempt[] {
  switch (action.type) {
    case "enqueue":
      if (queue.some((item) => item.key === action.key)) return [...queue];
      return [...queue, { key: action.key, body: action.body, status: "saving" }];
    case "retry":
      return setStatus(queue, action.key, "failed", "saving");
    case "saved":
      return setStatus(queue, action.key, "saving", "saved");
    case "failed":
      return setStatus(queue, action.key, "saving", "failed");
  }
}

/** Các lượt chưa lưu xong (đang gửi hoặc lỗi), để báo ở màn thiết lập đề. */
export function unsavedAttempts(queue: readonly PendingAttempt[]): PendingAttempt[] {
  return queue.filter((item) => item.status !== "saved");
}

/** Trạng thái ghi của một bộ đề: `idle` khi chưa có lượt nào. */
export function attemptStatusFor(queue: readonly PendingAttempt[], key: string): PendingStatus | "idle" {
  return queue.find((item) => item.key === key)?.status ?? "idle";
}
