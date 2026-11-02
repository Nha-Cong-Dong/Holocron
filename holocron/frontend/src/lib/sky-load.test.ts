/** Tải bầu trời của trang môn: hết giờ ⇒ lỗi (hoặc giữ bầu trời cũ), trả lời muộn vẫn hiện bầu trời. */

import { describe, expect, it } from "vitest";

import type { SkyPair } from "./sky";
import {
  INITIAL_SKY_LOAD,
  SKY_LOAD_TIMEOUT_MS,
  type SkyLoadEvent,
  type SkyLoadState,
  skyLoadReducer,
} from "./sky-load";

const PAIRS: readonly SkyPair[] = [];
const READY: SkyLoadState = { kind: "ready", pairs: PAIRS, stale: false };

function run(events: SkyLoadEvent[], start: SkyLoadState = INITIAL_SKY_LOAD): SkyLoadState {
  return events.reduce(skyLoadReducer, start);
}

describe("skyLoadReducer", () => {
  it("hết giờ 15 s mà chưa có bầu trời ⇒ lỗi", () => {
    expect(SKY_LOAD_TIMEOUT_MS).toBe(15_000);
    expect(run([{ type: "started" }, { type: "timedOut" }])).toEqual({ kind: "error" });
  });

  it("hết giờ khi đã có bầu trời ⇒ giữ bầu trời cũ, đánh dấu chưa cập nhật", () => {
    expect(run([{ type: "started" }, { type: "timedOut" }], READY)).toEqual({ ...READY, stale: true });
  });

  it("trả lời đến muộn sau khi hết giờ ⇒ bầu trời hiện như thường", () => {
    const pairs: readonly SkyPair[] = [];
    expect(run([{ type: "started" }, { type: "timedOut" }, { type: "succeeded", pairs }])).toEqual({
      kind: "ready",
      pairs,
      stale: false,
    });
    expect(run([{ type: "timedOut" }, { type: "succeeded", pairs }], READY)).toEqual({
      kind: "ready",
      pairs,
      stale: false,
    });
  });

  it("lỗi giống hết giờ: chưa có bầu trời ⇒ lỗi, đã có ⇒ giữ kèm cờ cũ", () => {
    expect(run([{ type: "failed" }])).toEqual({ kind: "error" });
    expect(run([{ type: "failed" }], READY)).toEqual({ ...READY, stale: true });
  });

  it("hết giờ rồi lỗi (hoặc ngược lại) không đổi trạng thái thêm lần nữa", () => {
    const error = run([{ type: "timedOut" }]);
    expect(skyLoadReducer(error, { type: "failed" })).toBe(error);
    const stale = run([{ type: "timedOut" }], READY);
    expect(skyLoadReducer(stale, { type: "failed" })).toBe(stale);
  });

  it("bắt đầu tải lại: đang lỗi ⇒ đang tải; đã có bầu trời ⇒ giữ nguyên trong lúc tải", () => {
    expect(run([{ type: "started" }], { kind: "error" })).toEqual({ kind: "loading" });
    expect(skyLoadReducer(READY, { type: "started" })).toBe(READY);
    expect(skyLoadReducer(INITIAL_SKY_LOAD, { type: "started" })).toBe(INITIAL_SKY_LOAD);
  });
});
