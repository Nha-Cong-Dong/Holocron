/**
 * Logic thuần của danh sách cặp khái niệm `proposed` (T2.3; spec §5.8, §6.6): chọn và bỏ chọn nhiều cặp, nhóm "Cần
 * kiểm tra", hàng đợi xác nhận hoặc từ chối tuần tự, điều kiện các nút, đích điều hướng sang ôn tập và đọc danh sách
 * job. Không có endpoint xử lý theo lô: mỗi cặp là một lời gọi `confirm` hoặc `reject` riêng, gọi lần lượt.
 * Mọi con số hiển thị lấy từ backend hoặc đếm số cặp đã chọn (không phải con số của nội dung câu hỏi, I1).
 */

import { screenErrorText } from "@/lib/concept-board";
import { toUiError, type UiError } from "@/lib/errors";
import { type ProposalJobView, parseProposalJob } from "@/lib/import-result";
import { legacyRedirectTarget } from "@/lib/legacy-routes";
import type { ConceptPair, PairGate } from "@/types/holocron";

// ------------------------------------------------------------------------------------ nhóm "Cần kiểm tra"

/** Cặp thuộc nhóm "Cần kiểm tra": `failed_gates` không rỗng. Lọc theo field backend trả, không tự tính cổng. */
export function needsCheck(pair: Pick<ConceptPair, "failed_gates">): boolean {
  return pair.failed_gates.length > 0;
}

export interface ProposalGroups {
  ready: ConceptPair[];
  needsCheck: ConceptPair[];
}

/** Tách cặp theo `failed_gates`, giữ thứ tự backend trả trong từng nhóm. */
export function splitProposals(pairs: readonly ConceptPair[]): ProposalGroups {
  const groups: ProposalGroups = { ready: [], needsCheck: [] };
  for (const pair of pairs) (needsCheck(pair) ? groups.needsCheck : groups.ready).push(pair);
  return groups;
}

/** Câu ngắn cho từng mã cổng (đề xuất suy ra); mã lạ ⇒ câu chung, không lộ mã thô. */
export const PAIR_GATE_LABEL_VI: Record<PairGate, string> = {
  G1_SOURCE: "đoạn nguồn chưa chắc khớp",
  G2_GLYPH: "có ký tự lạ trong đoạn",
  G3_OCCLUDED: "chữ có thể bị che",
  G4_COLUMN_MIX: "đoạn có thể lẫn cột",
  G5_NUMBERS: "có số cần xem lại",
  EXERCISE: "có vẻ là đề bài, không phải định nghĩa",
};

export const PAIR_GATE_UNKNOWN_VI = "cần xem lại";

export function gateLabel(gate: string): string {
  const entry = Object.entries(PAIR_GATE_LABEL_VI).find(([code]) => code === gate);
  return entry === undefined ? PAIR_GATE_UNKNOWN_VI : entry[1];
}

/** Các lý do một cặp cần kiểm tra, dạng một câu; `null` khi cặp không thuộc nhóm. */
export function needsCheckReason(pair: Pick<ConceptPair, "failed_gates">): string | null {
  if (!needsCheck(pair)) return null;
  const labels = [...new Set(pair.failed_gates.map(gateLabel))];
  return `Cần kiểm tra: ${labels.join("; ")}.`;
}

// ------------------------------------------------------------------------------------ chọn nhiều cặp

export interface SelectionState {
  selected: ReadonlySet<string>;
  /** Cặp đã được xét mặc định một lần: cặp quay lại danh sách sau khi tải lại không bị tích lại. */
  seen: ReadonlySet<string>;
}

export const EMPTY_SELECTION: SelectionState = { selected: new Set(), seen: new Set() };

export type SelectionEvent =
  | { type: "sync"; pairs: readonly ConceptPair[] }
  | { type: "toggle"; pairId: string }
  | { type: "set_many"; pairIds: readonly string[]; on: boolean }
  | { type: "remove"; pairIds: readonly string[] };

/**
 * Trạng thái chọn. `sync` nhận các cặp `proposed` hiện có: bỏ lựa chọn của cặp đã rời danh sách; cặp mới thấy lần đầu
 * chỉ được tích sẵn khi `pre_selected = true` (máy tích sẵn, người dùng vẫn phải bấm nút nhận), còn lại mặc định
 * không tích.
 */
export function selectionReducer(state: SelectionState, event: SelectionEvent): SelectionState {
  switch (event.type) {
    case "sync": {
      const present = new Set(event.pairs.map((pair) => pair.pair_id));
      const selected = new Set([...state.selected].filter((id) => present.has(id)));
      const seen = new Set([...state.seen].filter((id) => present.has(id)));
      for (const pair of event.pairs) {
        if (seen.has(pair.pair_id)) continue;
        seen.add(pair.pair_id);
        if (pair.pre_selected) selected.add(pair.pair_id);
      }
      return { selected, seen };
    }
    case "toggle": {
      const selected = new Set(state.selected);
      if (selected.has(event.pairId)) selected.delete(event.pairId);
      else selected.add(event.pairId);
      return { ...state, selected };
    }
    case "set_many": {
      const selected = new Set(state.selected);
      for (const id of event.pairIds) {
        if (event.on) selected.add(id);
        else selected.delete(id);
      }
      return { ...state, selected };
    }
    case "remove": {
      const gone = new Set(event.pairIds);
      return { ...state, selected: new Set([...state.selected].filter((id) => !gone.has(id))) };
    }
  }
}

/** Trạng thái hộp "Chọn tất cả" (ba trạng thái) theo các cặp chọn được. Không có cặp nào chọn được ⇒ `none`. */
export type SelectAllState = "none" | "some" | "all";

export function selectAllState(selectableIds: readonly string[], selected: ReadonlySet<string>): SelectAllState {
  const count = selectableIds.filter((id) => selected.has(id)).length;
  if (count === 0) return "none";
  return count === selectableIds.length ? "all" : "some";
}

/** Các cặp chọn được và đang được chọn, theo thứ tự của `selectableIds`. */
export function chosenIds(selectableIds: readonly string[], selected: ReadonlySet<string>): string[] {
  return selectableIds.filter((id) => selected.has(id));
}

/** Bấm hộp "Chọn tất cả": đang chọn hết ⇒ bỏ chọn hết; còn lại ⇒ chọn hết các cặp chọn được. */
export function selectAllEvent(selectableIds: readonly string[], selected: ReadonlySet<string>): SelectionEvent {
  return { type: "set_many", pairIds: selectableIds, on: selectAllState(selectableIds, selected) !== "all" };
}

// ------------------------------------------------------------------------------------ điều kiện các nút

export interface BatchButtons {
  acceptEnabled: boolean;
  rejectEnabled: boolean;
  /** Lý do hai nút khoá, đọc cùng nút; `null` khi bấm được. */
  reason: string | null;
}

export const BATCH_RUNNING_VI = "Đang gửi các khái niệm đã chọn. Đợi một chút.";
export const BATCH_NONE_SELECTED_VI = "Chọn ít nhất một khái niệm.";
export const BATCH_EDITING_VI = "Có khái niệm đang sửa tên. Bấm Lưu tên hoặc Huỷ trước khi chấp nhận.";

export function batchButtons(state: { chosen: number; running: boolean; editing: boolean }): BatchButtons {
  if (state.running) return { acceptEnabled: false, rejectEnabled: false, reason: BATCH_RUNNING_VI };
  if (state.chosen === 0) return { acceptEnabled: false, rejectEnabled: false, reason: BATCH_NONE_SELECTED_VI };
  if (state.editing) return { acceptEnabled: false, rejectEnabled: false, reason: BATCH_EDITING_VI };
  return { acceptEnabled: true, rejectEnabled: true, reason: null };
}

/** Cặp nào trong số đã chọn đang mở khung sửa tên (nháp đang gõ chưa lưu). */
export function editingAmong(chosen: readonly string[], editing: ReadonlySet<string>): string[] {
  return chosen.filter((id) => editing.has(id));
}

// ------------------------------------------------------------------------------------ hàng đợi tuần tự

export type BatchOp = "accept" | "reject";

export interface BatchItem {
  pairId: string;
  version: number;
  /** Tên đã sửa (chỉ khi nhận); `null` ⇒ giữ tên backend đề xuất. */
  term: string | null;
}

export type StopKind = "not_found" | "quote_mismatch" | "other";

export interface BatchStop {
  pairId: string;
  kind: StopKind;
  error: UiError;
}

export interface BatchOutcome {
  /** Đã ghi xong; các cặp này rời danh sách `proposed` ngay (không idempotent: không gửi lại). */
  done: string[];
  /** Lệch phiên bản hoặc trạng thái (409): cặp bị bỏ qua, phải tải lại cặp đó. */
  conflicts: string[];
  /** Lỗi khác: hàng đợi dừng ở đây. */
  stopped: BatchStop | null;
  /** Chưa gửi vì hàng đợi đã dừng; vẫn được chọn. */
  untouched: string[];
}

export interface BatchProgress {
  /** Thứ tự (từ 1) của cặp đang gửi. */
  current: number;
  total: number;
}

function stopKindOf(error: UiError): StopKind {
  if (error.kind === "not_found") return "not_found";
  if (error.kind === "validation") return "quote_mismatch";
  return "other";
}

/**
 * Gửi lần lượt từng cặp qua `send`. Lỗi 409 (lệch phiên bản/trạng thái) chỉ bỏ qua cặp đó và đi tiếp; mọi lỗi khác (404,
 * 422, mạng, máy chủ) dừng hàng đợi, các cặp chưa gửi nằm ở `untouched`. Cặp đã ghi xong không bao giờ bị gửi lại.
 */
export async function runBatch(
  items: readonly BatchItem[],
  send: (item: BatchItem) => Promise<unknown>,
  onProgress: (progress: BatchProgress) => void = () => undefined,
): Promise<BatchOutcome> {
  const outcome: BatchOutcome = { done: [], conflicts: [], stopped: null, untouched: [] };
  const seen = new Set<string>();
  const queue = items.filter((item) => {
    if (seen.has(item.pairId)) return false;
    seen.add(item.pairId);
    return true;
  });
  for (const [index, item] of queue.entries()) {
    onProgress({ current: index + 1, total: queue.length });
    try {
      await send(item);
      outcome.done.push(item.pairId);
    } catch (caught) {
      const error = toUiError(caught);
      if (error.kind === "version_conflict" || error.kind === "state_conflict") {
        outcome.conflicts.push(item.pairId);
        continue;
      }
      outcome.stopped = { pairId: item.pairId, kind: stopKindOf(error), error };
      outcome.untouched = queue.slice(index + 1).map((rest) => rest.pairId);
      break;
    }
  }
  return outcome;
}

/** Thanh tiến độ cho trình đọc màn hình: "Đang chấp nhận 3/12…". */
export function progressText(op: BatchOp, progress: BatchProgress): string {
  return `${op === "accept" ? "Đang chấp nhận" : "Đang từ chối"} ${progress.current}/${progress.total}…`;
}

/** Tất cả cặp đã chọn đã nhận xong, không cặp nào lệch hay dừng giữa chừng. */
export function batchCompleted(outcome: BatchOutcome): boolean {
  return outcome.conflicts.length === 0 && outcome.stopped === null && outcome.untouched.length === 0;
}

/** Chỉ điều hướng sang ôn tập khi là lượt nhận, đã nhận ít nhất một cặp và hàng đợi chạy trọn. */
export function shouldOpenPractice(op: BatchOp, outcome: BatchOutcome): boolean {
  return op === "accept" && outcome.done.length > 0 && batchCompleted(outcome);
}

const STOP_TEXT_VI: Record<StopKind, string> = {
  not_found: "Một khái niệm không còn trong kho nên việc gửi dừng ở đó.",
  quote_mismatch:
    "Một khái niệm có đoạn trích không khớp nguyên văn (hoặc tên không hợp lệ) nên chưa nhận được; việc gửi dừng ở đó.",
  other: "",
};

/** Câu tóm tắt sau một lượt (ghi vào vùng `aria-live`). Số đếm là số cặp đã gửi, do code đếm. */
export function batchSummary(op: BatchOp, outcome: BatchOutcome): string {
  const verb = op === "accept" ? "chấp nhận" : "từ chối";
  const parts: string[] = [];
  parts.push(
    outcome.done.length === 0 ? `Chưa ${verb} được khái niệm nào.` : `Đã ${verb} ${outcome.done.length} khái niệm.`,
  );
  if (outcome.conflicts.length > 0) {
    parts.push(
      `${outcome.conflicts.length} khái niệm vừa đổi ở nơi khác nên đã tải lại; xem lại rồi bấm ${verb} lần nữa.`,
    );
  }
  if (outcome.stopped !== null) {
    const fixed = STOP_TEXT_VI[outcome.stopped.kind];
    parts.push(fixed !== "" ? fixed : `Việc gửi dừng giữa chừng. ${screenErrorText(outcome.stopped.error)}`);
    if (outcome.untouched.length > 0) {
      parts.push(`${outcome.untouched.length} khái niệm chưa gửi vẫn đang được chọn.`);
    }
  }
  return parts.join(" ");
}

/**
 * Cặp cần bỏ chọn sau lượt gửi: cặp đã ghi (rời danh sách), cặp không còn trong kho hoặc có đoạn trích không khớp (gửi lại
 * vẫn lỗi). Cặp lệch phiên bản, lỗi tạm và cặp chưa gửi vẫn được chọn.
 */
export function deselectAfter(outcome: BatchOutcome): string[] {
  const ids = [...outcome.done];
  if (outcome.stopped !== null && outcome.stopped.kind !== "other") ids.push(outcome.stopped.pairId);
  return ids;
}

/** Danh sách `proposed` sau khi cặp đã ghi xong rời đi ngay (không chờ lượt tải lại). */
export function withoutSettled<T extends { pair_id: string }>(
  proposed: readonly T[],
  settled: ReadonlySet<string>,
): T[] {
  return proposed.filter((pair) => !settled.has(pair.pair_id));
}

// ------------------------------------------------------------------------------------ điều hướng

/**
 * Địa chỉ tab Ôn tập của môn, lấy qua `legacyRedirectTarget` cho route `/practice` (môn truyền vào làm "môn gần nhất"):
 * không tự viết URL, không dùng route cũ.
 */
export function practiceTarget(subjectCode: string): string {
  return legacyRedirectTarget("/practice", new URLSearchParams(), subjectCode);
}

// ------------------------------------------------------------------------------------ job đề xuất của môn

/** `200 list[ProposalJob]` của `GET /v1/vault/concept-pairs/jobs`; phần tử sai dạng bị bỏ, thân không phải mảng ⇒ `null`. */
export function parseProposalJobList(value: unknown): ProposalJobView[] | null {
  if (!Array.isArray(value)) return null;
  const jobs: ProposalJobView[] = [];
  for (const raw of value) {
    const job = parseProposalJob(raw);
    if (job !== null) jobs.push(job);
  }
  return jobs;
}

/** Job mới nhất của môn: backend trả mới nhất trước (§6.6), nên là phần tử đầu. */
export function latestJob(jobs: readonly ProposalJobView[]): ProposalJobView | null {
  return jobs[0] ?? null;
}

export const LLM_NEEDED_VI = "Cần cấu hình LLM để tự đề xuất";

/** Lối vào tạo job chỉ hiện khi khả năng báo rõ là khả dụng; không báo hoặc không khả dụng thì ẩn. */
export function canStartJob(state: "available" | "unavailable" | "unknown", latest: ProposalJobView | null): boolean {
  return state === "available" && (latest === null || (latest.status !== "queued" && latest.status !== "running"));
}
