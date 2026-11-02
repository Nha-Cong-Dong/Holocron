/**
 * Trạng thái tải bầu trời của trang môn (`GET /v1/vault/concept-pairs`, design-handoff/README.md màn 3). Hàm thuần:
 * trạng thái trước + sự kiện ⇒ trạng thái sau; component chỉ nối sự kiện (bắt đầu tải, trả lời, lỗi, hết giờ).
 *
 * - Lời gọi treo không được giữ ô trời ở "Đang tải" mãi: hết `SKY_LOAD_TIMEOUT_MS` thì báo lỗi, hoặc giữ bầu trời cũ
 *   kèm cờ `stale` nếu đã có bầu trời.
 * - Trả lời đến muộn (sau khi đã hết giờ) vẫn được nhận: bầu trời hiện như thường.
 */

import type { SkyPair } from "@/lib/sky";

/** Thời gian chờ tối đa một lần tải bầu trời trước khi báo lỗi. */
export const SKY_LOAD_TIMEOUT_MS = 15_000;

export type SkyLoadState =
  | { kind: "loading" }
  | { kind: "error" }
  /** `stale`: lần tải lại gần nhất lỗi hoặc hết giờ, đang giữ bầu trời của lần trước. */
  | { kind: "ready"; pairs: readonly SkyPair[]; stale: boolean };

/**
 * Sự kiện của một lần tải. `started`: đang báo lỗi thì về "Đang tải"; đã có bầu trời thì giữ nguyên trong lúc tải lại.
 */
export type SkyLoadEvent =
  | { type: "started" }
  | { type: "succeeded"; pairs: readonly SkyPair[] }
  | { type: "failed" }
  | { type: "timedOut" };

export const INITIAL_SKY_LOAD: SkyLoadState = { kind: "loading" };

export function skyLoadReducer(state: SkyLoadState, event: SkyLoadEvent): SkyLoadState {
  switch (event.type) {
    case "started":
      return state.kind === "error" ? { kind: "loading" } : state;
    case "succeeded":
      return { kind: "ready", pairs: event.pairs, stale: false };
    case "failed":
    case "timedOut":
      if (state.kind === "ready") return state.stale ? state : { ...state, stale: true };
      return state.kind === "error" ? state : { kind: "error" };
  }
}
