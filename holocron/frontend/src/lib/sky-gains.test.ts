import { describe, expect, it } from "vitest";

import { buildSkyLayout, type SkyPair } from "./sky";
import { EMPTY_SKY_TRACK, NO_SKY_GAINS, newStarFlags, skyGains, trackedGains, trackSkyLayout } from "./sky-gains";

function pairs(chapter: string, confirmed: number, proposed = 0): SkyPair[] {
  return [
    ...Array.from({ length: confirmed }, (): SkyPair => ({ chapter_code: chapter, status: "confirmed" })),
    ...Array.from({ length: proposed }, (): SkyPair => ({ chapter_code: chapter, status: "proposed" })),
  ];
}

function layout(list: SkyPair[]) {
  return buildSkyLayout("Môn mẫu", list);
}

describe("skyGains", () => {
  it("lần vẽ đầu không có sao mới", () => {
    expect(skyGains(null, layout(pairs("ch1", 3))).total).toBe(0);
  });

  it("không đổi ⇒ 0; giảm ⇒ 0", () => {
    const base = layout(pairs("ch1", 3));
    expect(skyGains(base, layout(pairs("ch1", 3))).total).toBe(0);
    expect(skyGains(base, layout(pairs("ch1", 1))).total).toBe(0);
  });

  it("nhận thêm trong chương có sẵn: đếm đúng số sao vàng vẽ thêm", () => {
    const gains = skyGains(layout(pairs("ch1", 2, 3)), layout(pairs("ch1", 4, 1)));
    expect(gains.total).toBe(2);
    expect(gains.byChapter.get("ch1")).toBe(2);
  });

  it("chương mới: mọi sao vàng của chương đó là mới", () => {
    const gains = skyGains(layout(pairs("ch1", 2)), layout([...pairs("ch1", 2), ...pairs("ch2", 3)]));
    expect(gains.total).toBe(3);
    expect([...gains.byChapter.entries()]).toEqual([["ch2", 3]]);
  });

  it("chòm đã đủ 8 sao: nhận thêm không sáng thêm sao nào", () => {
    expect(skyGains(layout(pairs("ch1", 8)), layout(pairs("ch1", 11))).total).toBe(0);
    expect(skyGains(layout(pairs("ch1", 6)), layout(pairs("ch1", 11))).total).toBe(2);
  });

  it("chương thứ 6 trở đi không vẽ nên không tính", () => {
    const five = ["ch1", "ch2", "ch3", "ch4", "ch5"].flatMap((code) => pairs(code, 1));
    expect(skyGains(layout(five), layout([...five, ...pairs("ch6", 4)])).total).toBe(0);
  });
});

describe("newStarFlags", () => {
  it("đánh dấu đúng các sao vàng cuối cùng", () => {
    const constellation = layout(pairs("ch1", 4, 2)).constellations[0];
    expect(constellation).toBeDefined();
    if (constellation === undefined) return;
    expect(newStarFlags(constellation, 2)).toEqual([false, false, true, true, false, false, false, false]);
    expect(newStarFlags(constellation, 0).some(Boolean)).toBe(false);
    expect(newStarFlags(constellation, 9).filter(Boolean)).toHaveLength(4);
  });
});

describe("trackSkyLayout và trackedGains: sao mới tính ngay khi bố cục đổi", () => {
  it("lần vẽ đầu không có sao mới; bố cục mới ⇒ so với bố cục trước", () => {
    const first = layout(pairs("ch1", 2));
    const step1 = trackSkyLayout(EMPTY_SKY_TRACK, first);
    expect(step1).toEqual({ layout: first, previous: null });
    expect(trackedGains(step1).total).toBe(0);
    const second = layout(pairs("ch1", 4));
    const step2 = trackSkyLayout(step1, second);
    expect(step2).toEqual({ layout: second, previous: first });
    expect(trackedGains(step2).total).toBe(2);
  });

  it("cùng bố cục hoặc chưa có bố cục ⇒ giữ nguyên đối tượng (không cập nhật, sao mới không phát lại)", () => {
    const first = layout(pairs("ch1", 2));
    const step1 = trackSkyLayout(EMPTY_SKY_TRACK, first);
    expect(trackSkyLayout(step1, first)).toBe(step1);
    expect(trackSkyLayout(step1, null)).toBe(step1);
    expect(trackSkyLayout(EMPTY_SKY_TRACK, null)).toBe(EMPTY_SKY_TRACK);
    expect(trackedGains(EMPTY_SKY_TRACK)).toBe(NO_SKY_GAINS);
  });
});
