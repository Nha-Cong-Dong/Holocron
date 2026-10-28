import { describe, expect, it } from "vitest";

import {
  buildSkyLayout,
  chapterLabel,
  compareChapterCodes,
  countChapters,
  MINI_SKY,
  SKY_HEIGHT,
  SKY_WIDTH,
  type SkyPair,
  STARS_PER_CONSTELLATION,
} from "./sky";

function pairs(chapter: string, confirmed: number, proposed = 0, rejected = 0): SkyPair[] {
  return [
    ...Array.from({ length: confirmed }, (): SkyPair => ({ chapter_code: chapter, status: "confirmed" })),
    ...Array.from({ length: proposed }, (): SkyPair => ({ chapter_code: chapter, status: "proposed" })),
    ...Array.from({ length: rejected }, (): SkyPair => ({ chapter_code: chapter, status: "rejected" })),
  ];
}

describe("chapterLabel", () => {
  it("mã chuẩn ⇒ Chương N hoặc N.M", () => {
    expect(chapterLabel("ch2")).toBe("Chương 2");
    expect(chapterLabel("ch10")).toBe("Chương 10");
    expect(chapterLabel("ch2.1")).toBe("Chương 2.1");
    expect(chapterLabel("ch02")).toBe("Chương 2");
  });

  it("mã lạ giữ nguyên, mã rỗng có nhãn riêng", () => {
    expect(chapterLabel("phu-luc")).toBe("phu-luc");
    expect(chapterLabel("CH2")).toBe("CH2");
    expect(chapterLabel("ch2.1.3")).toBe("ch2.1.3");
    expect(chapterLabel("")).toBe("Chưa rõ chương");
  });
});

describe("compareChapterCodes", () => {
  it("sắp xếp tự nhiên theo phần số", () => {
    const codes = ["ch10", "ch2", "ch1", "ch2.10", "ch2.9", "ch2.1"];
    expect([...codes].sort(compareChapterCodes)).toEqual(["ch1", "ch2", "ch2.1", "ch2.9", "ch2.10", "ch10"]);
  });

  it("mã lạ vẫn có thứ tự ổn định", () => {
    expect(compareChapterCodes("abc", "abc")).toBe(0);
    expect(compareChapterCodes("abc", "abd")).toBe(-1);
    expect(compareChapterCodes("ch2", "ch02")).toBe(-1);
    expect(compareChapterCodes("ch", "ch1")).toBe(-1);
  });
});

describe("countChapters", () => {
  it("đếm confirmed và proposed theo chương, bỏ rejected khỏi số sao", () => {
    const chapters = countChapters([...pairs("ch10", 1), ...pairs("ch2", 3, 2, 4)]);
    expect(chapters).toEqual([
      { code: "ch2", label: "Chương 2", confirmed: 3, proposed: 2 },
      { code: "ch10", label: "Chương 10", confirmed: 1, proposed: 0 },
    ]);
  });
});

describe("buildSkyLayout", () => {
  it("0 chương: không có chòm, nhãn đọc nói chưa có khái niệm", () => {
    const layout = buildSkyLayout("Vi xử lý", []);
    expect(layout.width).toBe(SKY_WIDTH);
    expect(layout.height).toBe(SKY_HEIGHT);
    expect(layout.constellations).toEqual([]);
    expect(layout.hiddenLabel).toBeNull();
    expect(layout.ariaLabel).toBe("Bầu trời môn Vi xử lý: chưa có khái niệm nào.");
  });

  it("chương có 0 sao (chỉ cặp bị bỏ): 8 sao mờ, không có nét nối", () => {
    const layout = buildSkyLayout("Môn A", pairs("ch1", 0, 0, 2));
    expect(layout.constellations).toHaveLength(1);
    const only = layout.constellations[0];
    expect(only?.stars.map((star) => star.kind)).toEqual(Array(STARS_PER_CONSTELLATION).fill("empty"));
    expect(only?.lines).toEqual([]);
    expect(only?.detail).toBe("0 sao");
    expect(layout.ariaLabel).toBe("Bầu trời môn Môn A, 1 chương: Chương 1 có 0 sao.");
  });

  it("1 chương: sao vàng trước, tím sau, nét nối giữa các sao vàng liên tiếp", () => {
    const layout = buildSkyLayout("Môn A", pairs("ch1", 3, 2));
    const only = layout.constellations[0];
    expect(only?.stars.map((star) => star.kind)).toEqual([
      "confirmed",
      "confirmed",
      "confirmed",
      "proposed",
      "proposed",
      "empty",
      "empty",
      "empty",
    ]);
    expect(only?.lines).toHaveLength(2);
    expect(only?.overflow).toBe(false);
    expect(only?.detail).toBe("3 sao · 2 chờ duyệt");
    expect(layout.ariaLabel).toBe("Bầu trời môn Môn A, 1 chương: Chương 1 có 3 sao, 2 khái niệm chờ duyệt.");
    for (const star of only?.stars ?? []) {
      expect(star.x).toBeGreaterThanOrEqual(0);
      expect(star.x).toBeLessThanOrEqual(SKY_WIDTH);
      expect(star.y).toBeGreaterThanOrEqual(0);
      expect(star.y).toBeLessThan(layout.labelNameY);
    }
  });

  it("≥ 8 đã nhận kèm chờ duyệt: chỉ vẽ 8 sao vàng, nhãn ghi số thật", () => {
    const layout = buildSkyLayout("Môn A", pairs("ch3", 11, 4));
    const only = layout.constellations[0];
    expect(only?.stars.filter((star) => star.kind === "confirmed")).toHaveLength(8);
    expect(only?.stars.filter((star) => star.kind === "proposed")).toHaveLength(0);
    expect(only?.lines).toHaveLength(7);
    expect(only?.overflow).toBe(true);
    expect(only?.detail).toBe("11 đã nhận · 4 chờ duyệt");
    expect(layout.ariaLabel).toContain("Chương 3 có 11 sao, 4 khái niệm chờ duyệt");
  });

  it("đã nhận ít hơn 8 nhưng tổng vượt 8: tím lấp chỗ còn lại, nhãn ghi số thật", () => {
    const only = buildSkyLayout("Môn A", pairs("ch1", 5, 6)).constellations[0];
    expect(only?.stars.filter((star) => star.kind === "confirmed")).toHaveLength(5);
    expect(only?.stars.filter((star) => star.kind === "proposed")).toHaveLength(3);
    expect(only?.detail).toBe("5 đã nhận · 6 chờ duyệt");
  });

  it("12 chương: vẽ 5 chòm đầu theo thứ tự tự nhiên, nhãn +7 chương, nhãn đọc đủ 12 chương", () => {
    const all = Array.from({ length: 12 }, (_, index) => pairs(`ch${12 - index}`, index + 1)).flat();
    const layout = buildSkyLayout("Môn B", all);
    expect(layout.constellations.map((item) => item.chapter.code)).toEqual(["ch1", "ch2", "ch3", "ch4", "ch5"]);
    expect(layout.hiddenChapters).toBe(7);
    expect(layout.hiddenLabel).toBe("+7 chương");
    for (let chapter = 1; chapter <= 12; chapter += 1) {
      expect(layout.ariaLabel).toContain(`Chương ${chapter} có ${13 - chapter} sao`);
    }
    expect(layout.ariaLabel.startsWith("Bầu trời môn Môn B, 12 chương: Chương 1 có 12 sao;")).toBe(true);
    const centers = layout.constellations.map((item) => item.centerX);
    expect(centers).toEqual([79, 237, 395, 553, 711]);
    for (const item of layout.constellations) {
      const xs = item.stars.map((star) => star.x);
      expect(Math.min(...xs)).toBeGreaterThanOrEqual(item.centerX - item.slotWidth / 2);
      expect(Math.max(...xs)).toBeLessThanOrEqual(item.centerX + item.slotWidth / 2);
    }
  });

  it("mã chương lạ: nhãn là mã gốc, xếp sau mã chuẩn theo thứ tự ký tự", () => {
    const layout = buildSkyLayout("Môn C", [...pairs("phu-luc", 1), ...pairs("ch1", 1)]);
    expect(layout.constellations.map((item) => item.name)).toEqual(["Chương 1", "phu-luc"]);
    expect(layout.ariaLabel).toContain("phu-luc có 1 sao");
  });

  it("bầu trời mini: tối đa 3 chòm, không nhãn chương, vẫn đọc đủ số", () => {
    const all = [...pairs("ch1", 1), ...pairs("ch2", 2), ...pairs("ch3", 3), ...pairs("ch4", 4)];
    const layout = buildSkyLayout("Môn D", all, MINI_SKY);
    expect(layout.constellations).toHaveLength(3);
    expect(layout.withLabels).toBe(false);
    expect(layout.hiddenLabel).toBe("+1 chương");
    expect(layout.ariaLabel).toContain("Chương 4 có 4 sao");
  });

  it("tất định: cùng dữ liệu cho cùng bố cục", () => {
    const input = [...pairs("ch2", 4, 1), ...pairs("ch1", 2)];
    expect(buildSkyLayout("X", input)).toEqual(buildSkyLayout("X", [...input].reverse()));
  });
});
