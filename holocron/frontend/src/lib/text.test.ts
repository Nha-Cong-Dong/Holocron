import { describe, expect, it } from "vitest";

import { highlightSegments, splitsSurrogate } from "./text";

describe("highlightSegments", () => {
  it("highlightSegments: ghép lại đúng văn bản, bỏ khoảng sai", () => {
    const text = "a𝑥 biến c";
    const segments = highlightSegments(text, [
      [4, 8],
      [2, 3],
      [5, 6],
      [0, 99],
    ]);
    expect(segments.map((segment) => segment.text).join("")).toBe(text);
    expect(segments.filter((segment) => segment.marked).map((segment) => segment.text)).toEqual(["biến"]);
    expect(segments.map((segment) => segment.start)).toEqual([0, 4, 8]);
    expect(highlightSegments("", [])).toEqual([{ start: 0, text: "", marked: false }]);
    expect(highlightSegments("ab", [[0, 2]])).toEqual([{ start: 0, text: "ab", marked: true }]);
  });

  it("không truyền khoảng nào: một đoạn thường đúng bằng văn bản", () => {
    expect(highlightSegments("Tiến trình", [])).toEqual([{ start: 0, text: "Tiến trình", marked: false }]);
  });
});

describe("splitsSurrogate", () => {
  it("chỉ đúng khi vị trí rơi giữa cặp surrogate", () => {
    const text = "a𝑥b";
    expect(splitsSurrogate(text, 2)).toBe(true);
    expect(splitsSurrogate(text, 1)).toBe(false);
    expect(splitsSurrogate(text, 3)).toBe(false);
    expect(splitsSurrogate(text, 0)).toBe(false);
    expect(splitsSurrogate(text, text.length)).toBe(false);
  });
});
