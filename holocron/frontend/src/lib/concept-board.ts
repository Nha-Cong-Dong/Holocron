/**
 * Logic thuần của tab Khái niệm (design-handoff/README.md màn 7): gom khái niệm theo trạng thái, trạng thái của màn,
 * cách hiện một khái niệm theo kết quả tra đoạn nguồn (I3), lối vào được hiện, focus sau khi một thẻ rời đi, câu thông
 * báo và hàng gửi "Nhận tất cả".
 *
 * Mọi số hiện ra (số khái niệm, số đã nhận) là số đếm danh sách backend trả; trang và chương lấy từ backend (I1).
 */

import { toUiError, type UiError } from "@/lib/errors";
import {
  type ChunkLookup,
  type ChunkMapStatus,
  lookupChunk,
  lookupLabel,
  type PairActions,
  pairActions,
} from "@/lib/pairs";
import { citationCaption } from "@/lib/passage";
import { chapterLabel } from "@/lib/sky";
import type { ChunkView } from "@/lib/vault";
import type { ConceptPair } from "@/types/holocron";

// ------------------------------------------------------------------------------------ nhóm và màn

export interface ConceptGroups {
  proposed: ConceptPair[];
  confirmed: ConceptPair[];
  rejected: ConceptPair[];
}

/** Gom theo `status`, giữ thứ tự backend trả trong từng nhóm. */
export function groupPairs(pairs: readonly ConceptPair[]): ConceptGroups {
  const groups: ConceptGroups = { proposed: [], confirmed: [], rejected: [] };
  for (const pair of pairs) groups[pair.status].push(pair);
  return groups;
}

/**
 * Khái niệm vừa thành `confirmed` giữa hai lần tải danh sách (để dòng mới ở cột "Đã nhận" có animation vào). Lần tải
 * đầu (`previous = null`) không có dòng mới.
 */
export function newlyConfirmed(previous: readonly ConceptPair[] | null, next: readonly ConceptPair[]): Set<string> {
  if (previous === null) return new Set();
  const before = new Set(previous.filter((pair) => pair.status === "confirmed").map((pair) => pair.pair_id));
  return new Set(
    next.filter((pair) => pair.status === "confirmed" && !before.has(pair.pair_id)).map((pair) => pair.pair_id),
  );
}

export type ConceptsScreen = "loading" | "error" | "empty" | "ready";

/**
 * Trạng thái của tab: danh sách đang tải ⇒ đang tải; lỗi ⇒ lỗi; chưa có khái niệm nào và môn chưa có đoạn văn nào
 * (đã tải đủ) ⇒ rỗng ("Thêm tài liệu"); còn lại ⇒ thành công (có khái niệm, hoặc có đoạn văn để tự tạo).
 */
export function conceptsScreen(
  list: "loading" | "error" | "ready",
  pairCount: number,
  mapStatus: ChunkMapStatus | "loading",
  chunkCount: number,
): ConceptsScreen {
  if (list !== "ready") return list;
  if (pairCount > 0) return "ready";
  if (mapStatus === "loading") return "loading";
  return mapStatus === "complete" && chunkCount === 0 ? "empty" : "ready";
}

// ------------------------------------------------------------------------------------ một khái niệm

export const SOURCE_LABEL_VI: Record<ConceptPair["proposed_by"], string> = {
  llm: "Holocron đề xuất",
  user: "Do bạn tạo",
};

export interface ConceptView {
  lookup: ChunkLookup;
  /** Đoạn nguồn khi tra được; `null` thì KHÔNG hiện trích dẫn như đã kiểm chứng và KHÔNG hiện số trang. */
  chunk: ChunkView | null;
  /** "Chương 2 · tr. 14" khi tra được đoạn; chỉ "Chương 2" khi không. */
  caption: string;
  /** Câu báo chưa kiểm chứng được trang (khi không tra được đoạn); `null` khi đã tra được. */
  unverified: string | null;
  actions: PairActions;
  sourceLabel: string;
}

/**
 * Cách hiện một khái niệm ở mọi nhóm (đề xuất, đã nhận, đã bỏ) theo cùng một luật: tra được đoạn nguồn thì có trích
 * dẫn nguyên văn kèm trang; không tra được (đoạn bị xoá, chưa tải hết hay tải lỗi) thì chữ thường, câu chưa kiểm chứng
 * và nút theo `pairActions` (khoá kèm lý do).
 */
export function conceptView(
  pair: ConceptPair,
  mapStatus: ChunkMapStatus | "loading",
  map: ReadonlyMap<string, ChunkView>,
): ConceptView {
  const lookup = lookupChunk(mapStatus, map, pair.chunk_id);
  const found = lookup.kind === "found" ? lookup.chunk : null;
  return {
    lookup,
    chunk: found,
    caption: found === null ? chapterLabel(pair.chapter_code) : citationCaption(pair.chapter_code, found),
    unverified: lookup.kind === "found" ? null : lookupLabel(lookup),
    actions: pairActions(pair.status, lookup),
    sourceLabel: SOURCE_LABEL_VI[pair.proposed_by],
  };
}

// ------------------------------------------------------------------------------------ lối vào

export type ConceptEntrance = "manual_create" | "auto_proposal";

/**
 * Lối vào tạo khái niệm được hiện. Tự tạo (bôi đoạn hoặc chọn câu) luôn có; đề xuất tự động chỉ hiện khi khả năng đó
 * có sẵn, không có thì ẩn hẳn (không nút mờ, không câu giải thích).
 */
export function visibleConceptEntrances(options: { autoProposalAvailable: boolean }): ConceptEntrance[] {
  return options.autoProposalAvailable ? ["auto_proposal", "manual_create"] : ["manual_create"];
}

// ------------------------------------------------------------------------------------ focus và câu chữ

/** Thẻ nhận focus sau khi thẻ `removedId` rời danh sách: thẻ kế tiếp; hết thẻ phía sau ⇒ `null` (tiêu đề khối). */
export function focusAfterRemoval(ids: readonly string[], removedId: string): string | null {
  const index = ids.indexOf(removedId);
  if (index === -1) return null;
  return ids[index + 1] ?? null;
}

/** Thông báo sau khi nhận: số khái niệm backend đã nhận; thêm số sao nếu bầu trời thực sự sáng thêm. */
export function acceptToast(accepted: number, starsLit: number): string {
  const base = `Đã nhận ${accepted} khái niệm`;
  return starsLit > 0 ? `${base}, ${starsLit} ngôi sao sáng lên` : base;
}

/**
 * Câu "vì sao + cách sửa" hiện cho người dùng: lỗi kết nối, máy chủ, kho bận hay lỗi lạ dùng câu cố định (không lộ từ
 * kỹ thuật); lỗi có câu riêng theo luồng giữ thông điệp đã phân loại. Mã kỹ thuật nằm trong "Chi tiết kỹ thuật".
 */
export function screenErrorText(error: UiError): string {
  switch (error.kind) {
    case "network":
    case "server":
      return "Holocron chưa chạy xong hoặc đã tắt. Mở lại Holocron rồi bấm Thử lại.";
    case "busy":
      return "Thư mục dữ liệu của Holocron đang bận. Đợi vài giây rồi bấm Thử lại.";
    case "forbidden":
      return "Holocron từ chối yêu cầu từ cửa sổ này. Mở lại Holocron trên máy rồi bấm Thử lại.";
    case "capability":
    case "llm_upstream":
      return "Tính năng này chưa dùng được trên máy này. Mở Cài đặt để xem cần cài hay bật thêm gì.";
    case "unknown":
      return "Holocron trả lời không như mong đợi. Bấm Thử lại; nếu vẫn lỗi, mở lại Holocron.";
    default:
      return error.message;
  }
}

/** Câu trên thẻ khi khái niệm vừa đổi ở nơi khác (409 lệch phiên bản). */
export const CONCEPT_CONFLICT_VI = "Khái niệm này vừa đổi ở nơi khác nên chưa ghi được. Xem lại rồi bấm Thử lại.";

/** Câu lỗi trên một thẻ sau khi nhận hay bỏ không thành. */
export function conceptErrorText(error: UiError): string {
  return error.kind === "version_conflict" ? CONCEPT_CONFLICT_VI : screenErrorText(error);
}

// ------------------------------------------------------------------------------------ "Nhận tất cả"

export type BulkItemStatus = "queued" | "sending" | "accepted" | "conflict" | "failed" | "skipped";

export interface BulkItem {
  pairId: string;
  version: number;
  status: BulkItemStatus;
  /** Câu hiện trên thẻ khi `conflict`, `failed` hoặc `skipped`. */
  message: string | null;
}

export interface BulkState {
  running: boolean;
  items: BulkItem[];
}

export const IDLE_BULK: BulkState = { running: false, items: [] };

export interface BulkCandidate {
  pairId: string;
  version: number;
  /** Được nhận (đoạn nguồn tra được); khoá thì bỏ qua kèm lý do. */
  allowed: boolean;
  /** Lý do khoá khi `allowed = false`. */
  reason: string | null;
}

export type BulkEvent =
  | { type: "start"; candidates: readonly BulkCandidate[] }
  | { type: "sending"; pairId: string }
  | { type: "accepted"; pairId: string }
  | { type: "failed"; pairId: string; error: UiError }
  | { type: "finished" }
  | { type: "dismiss"; pairId: string };

function update(state: BulkState, pairId: string, change: Partial<BulkItem>): BulkState {
  return { ...state, items: state.items.map((item) => (item.pairId === pairId ? { ...item, ...change } : item)) };
}

/**
 * Hàng gửi "Nhận tất cả": gửi lần lượt từng khái niệm được phép; khái niệm khoá bị bỏ qua kèm lý do. Đang gửi thì
 * bấm lại không có tác dụng; một khái niệm có trong danh sách hai lần chỉ gửi một lần. Lỗi một khái niệm không dừng
 * hàng: 409 lệch phiên bản ⇒ `conflict`, lỗi khác ⇒ `failed`.
 */
export function bulkReducer(state: BulkState, event: BulkEvent): BulkState {
  switch (event.type) {
    case "start": {
      if (state.running) return state;
      const seen = new Set<string>();
      const items: BulkItem[] = [];
      for (const candidate of event.candidates) {
        if (seen.has(candidate.pairId)) continue;
        seen.add(candidate.pairId);
        items.push({
          pairId: candidate.pairId,
          version: candidate.version,
          status: candidate.allowed ? "queued" : "skipped",
          message: candidate.allowed ? null : candidate.reason,
        });
      }
      return { running: items.some((item) => item.status === "queued"), items };
    }
    case "sending":
      return update(state, event.pairId, { status: "sending" });
    case "accepted":
      return update(state, event.pairId, { status: "accepted", message: null });
    case "failed":
      return update(state, event.pairId, {
        status: event.error.kind === "version_conflict" ? "conflict" : "failed",
        message: conceptErrorText(event.error),
      });
    case "finished":
      return { ...state, running: false };
    case "dismiss":
      return { ...state, items: state.items.filter((item) => item.pairId !== event.pairId) };
  }
}

export function bulkCount(state: BulkState, status: BulkItemStatus): number {
  return state.items.filter((item) => item.status === status).length;
}

/** Câu lỗi còn giữ trên thẻ của khái niệm sau lượt "Nhận tất cả" (`null` khi không có). */
export function bulkMessageOf(state: BulkState, pairId: string): string | null {
  return state.items.find((item) => item.pairId === pairId)?.message ?? null;
}

/**
 * Gửi lần lượt các khái niệm `queued` của `state` qua `send` (nhận kèm `expected_version`), báo từng bước qua
 * `dispatch`, rồi báo `finished`. Lỗi một khái niệm không dừng các khái niệm sau.
 */
export async function sendQueued(
  state: BulkState,
  send: (pairId: string, version: number) => Promise<unknown>,
  dispatch: (event: BulkEvent) => void,
): Promise<void> {
  for (const item of state.items) {
    if (item.status !== "queued") continue;
    dispatch({ type: "sending", pairId: item.pairId });
    try {
      await send(item.pairId, item.version);
      dispatch({ type: "accepted", pairId: item.pairId });
    } catch (error) {
      dispatch({ type: "failed", pairId: item.pairId, error: toUiError(error) });
    }
  }
  dispatch({ type: "finished" });
}
