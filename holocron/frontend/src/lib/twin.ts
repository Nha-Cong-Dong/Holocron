import type {
  BatchTwin,
  GanttSegment,
  MisconceptionRef,
  NumericalAnswer,
  OptionKey,
  ReviewStatus,
  ReviewTicket,
  TemplateProvenance,
  TheoryQuestionForm,
  TheoryTwin,
  TwinBatch,
  TwinQuestion,
} from "@/types/holocron";

/**
 * Logic thuần của Twin Viewer. Frontend CHỈ hiển thị số do backend trả (I1): module này chỉ quy đổi hình học để vẽ
 * và gom dữ liệu để hiển thị, KHÔNG tính WT, TAT, đáp án hay bất kỳ con số nào của đề.
 */

export const OPTION_KEYS: readonly OptionKey[] = ["A", "B", "C", "D"];

/** Số màu trong bảng màu Gantt (`--gantt-1` … `--gantt-6` ở `app/globals.css`). */
export const GANTT_PALETTE_SIZE = 6;

export const MIN_TWIN_COUNT = 1;
export const MAX_TWIN_COUNT = 10;
export const DEFAULT_TWIN_COUNT = 3;

export const REVIEW_STATUS_LABELS: Record<ReviewStatus, string> = {
  AUTO_PASS: "Tự động đạt",
  AUTO_FALLBACK: "Dùng mẫu sinh câu dự phòng",
  NEEDS_REVIEW: "Cần xem lại",
  APPROVED: "Đã duyệt",
  REJECTED: "Bị từ chối",
};

export const THEORY_FORM_LABELS: Record<TheoryQuestionForm, string> = {
  concept_match: "Ghép khái niệm",
  fill_blank: "Điền khuyết",
  definition_choice: "Chọn định nghĩa đúng",
};

export const PROVENANCE_LABELS: Record<TemplateProvenance, string> = {
  llm_generated: "Mẫu sinh câu do LLM viết",
  precomputed_verified: "Mẫu sinh câu đã kiểm chứng sẵn",
};

export interface GanttBar {
  process: string;
  start: number;
  end: number;
  leftPercent: number;
  widthPercent: number;
  colorIndex: number;
}

export interface GanttLayout {
  bars: GanttBar[];
  /** Tiến trình theo thứ tự xuất hiện đầu tiên (dùng cho chú giải). */
  processes: string[];
  /** Đầu mút lớn nhất của trục thời gian (đơn vị của backend); 0 khi không có đoạn nào. */
  axisEnd: number;
  /** Các mốc đầu mút duy nhất, tăng dần. */
  ticks: number[];
}

/** Quy các đoạn Gantt do backend trả thành phần trăm trên trục thời gian `[0, axisEnd]`. Khoảng rảnh để trống. */
export function ganttLayout(segments: readonly GanttSegment[]): GanttLayout {
  const processes: string[] = [];
  for (const segment of segments) {
    if (!processes.includes(segment.process)) {
      processes.push(segment.process);
    }
  }
  const axisEnd = segments.reduce((max, segment) => Math.max(max, segment.end), 0);
  const bars: GanttBar[] =
    axisEnd === 0
      ? []
      : segments.map((segment) => ({
          process: segment.process,
          start: segment.start,
          end: segment.end,
          leftPercent: (segment.start / axisEnd) * 100,
          widthPercent: ((segment.end - segment.start) / axisEnd) * 100,
          colorIndex: processes.indexOf(segment.process) % GANTT_PALETTE_SIZE,
        }));
  const ticks = [...new Set(segments.flatMap((segment) => [segment.start, segment.end]))].sort((a, b) => a - b);
  return { bars, processes, axisEnd, ticks };
}

/** Vị trí (%) của một mốc trên trục `[0, axisEnd]`; 0 khi trục rỗng. */
export function tickPercent(tick: number, axisEnd: number): number {
  return axisEnd === 0 ? 0 : (tick / axisEnd) * 100;
}

/** Văn bản thay thế cho sơ đồ Gantt (trình đọc màn hình): liệt kê từng đoạn. */
export function ganttDescription(segments: readonly GanttSegment[]): string {
  if (segments.length === 0) {
    return "Không có đoạn Gantt nào.";
  }
  const parts = segments.map((segment) => `${segment.process} từ ${segment.start} đến ${segment.end}`);
  return `Sơ đồ Gantt gồm ${segments.length} đoạn: ${parts.join("; ")}.`;
}

export interface OptionView {
  key: OptionKey;
  text: string;
  isCorrect: boolean;
  /** Chỉ phương án sai mới có badge; nguồn duy nhất là `distractor_to_misconception_map` của backend. */
  misconception: MisconceptionRef | null;
}

/** Phần phương án của một câu: `TwinQuestion` (câu tính toán) hoặc `TheoryQuestion` (câu lý thuyết). */
export type OptionSource = Pick<TwinQuestion, "distractor_to_misconception_map"> & {
  options: TwinQuestion["options"];
  correct_key: TwinQuestion["correct_key"];
};

export function optionViews(twin: OptionSource): OptionView[] {
  const options = twin.options;
  if (options === null) {
    return [];
  }
  return OPTION_KEYS.map((key) => ({
    key,
    text: options[key],
    isCorrect: twin.correct_key === key,
    misconception: twin.distractor_to_misconception_map[key] ?? null,
  }));
}

/** Định dạng đáp án số backend trả theo `decimals` và đơn vị; không làm tròn hay tính lại giá trị. */
export function formatNumericalAnswer(answer: NumericalAnswer): string {
  const text = answer.value.toFixed(answer.decimals);
  return answer.unit === null ? text : `${text} ${answer.unit}`;
}

/** Câu lý thuyết của `TwinBatch.twins` (quyết định 14(b)): có `question`; câu tính toán thì không. */
export function isTheoryTwin(twin: BatchTwin): twin is TheoryTwin {
  return "question" in twin;
}

export interface SlotGroup {
  slotIndex: number;
  twins: BatchTwin[];
  ticket: ReviewTicket | null;
}

/** Gom twin và ticket theo slot, sắp theo `slot_index`; slot chỉ có ticket vẫn xuất hiện. */
export function groupTwinsBySlot(batch: TwinBatch): SlotGroup[] {
  const groups = new Map<number, SlotGroup>();
  const groupFor = (slotIndex: number): SlotGroup => {
    const existing = groups.get(slotIndex);
    if (existing !== undefined) {
      return existing;
    }
    const created: SlotGroup = { slotIndex, twins: [], ticket: null };
    groups.set(slotIndex, created);
    return created;
  };
  for (const twin of batch.twins) {
    groupFor(twin.slot_index).twins.push(twin);
  }
  for (const ticket of batch.review_tickets) {
    groupFor(ticket.slot_index).ticket = ticket;
  }
  return [...groups.values()].sort((a, b) => a.slotIndex - b.slotIndex);
}

export function ticketAttemptSummary(ticket: ReviewTicket): string[] {
  return ticket.attempts.map((attempt) => `Lượt ${attempt.attempt}: ${attempt.outcome}`);
}

export function shortHash(sha256: string): string {
  return sha256.slice(0, 12);
}

/** Ép số lượng twin về số nguyên trong [1, 10]; giá trị không hữu hạn dùng mặc định 3 (khớp spec §6.3). */
export function clampTwinCount(value: number): number {
  if (!Number.isFinite(value)) {
    return DEFAULT_TWIN_COUNT;
  }
  return Math.min(MAX_TWIN_COUNT, Math.max(MIN_TWIN_COUNT, Math.trunc(value)));
}
