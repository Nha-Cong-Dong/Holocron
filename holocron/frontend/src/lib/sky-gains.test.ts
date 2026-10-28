import { describe, expect, it } from "vitest";

import { buildSkyLayout, type SkyPair } from "./sky";
import { newStarFlags, skyGains } from "./sky-gains";

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
