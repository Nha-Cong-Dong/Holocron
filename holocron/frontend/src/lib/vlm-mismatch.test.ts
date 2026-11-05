import { describe, expect, it } from "vitest";

import type { VlmMismatchView } from "./documents-data";
import { groupMismatchesByBlock, keepMismatchPatch, mismatchCellLabel, rejectBlockPatch } from "./vlm-mismatch";

function item(id: string, block: string, over: Partial<VlmMismatchView> = {}): VlmMismatchView {
  return { mismatchId: id, blockId: block, tokenKind: "number", row: 1, col: 2, vlmValue: "7", ocrValue: "1", ...over };
}

describe("groupMismatchesByBlock", () => {
  it("gom theo khối, giữ thứ tự", () => {
    const groups = groupMismatchesByBlock([item("m1", "b2"), item("m2", "b1"), item("m3", "b2")]);
    expect(groups.map((g) => g.blockId)).toEqual(["b2", "b1"]);
    expect(groups[0]?.items.map((i) => i.mismatchId)).toEqual(["m1", "m3"]);
  });
  it("rỗng ⇒ không nhóm", () => {
    expect(groupMismatchesByBlock([])).toEqual([]);
  });
});

describe("payload", () => {
  it("giữ đúng một chỗ lệch", () => {
    expect(keepMismatchPatch("m1")).toEqual({ keptMismatches: ["m1"], rejectedVlmBlocks: [] });
  });
  it("bỏ đúng một khối", () => {
    expect(rejectBlockPatch("b1")).toEqual({ keptMismatches: [], rejectedVlmBlocks: ["b1"] });
  });
});

describe("mismatchCellLabel", () => {
  it("có vị trí ô", () => {
    expect(mismatchCellLabel(item("m1", "b1"))).toBe("Hàng 1, cột 2");
  });
  it("AI bỏ sót ⇒ không có vị trí", () => {
    expect(mismatchCellLabel(item("m1", "b1", { row: null, col: null, vlmValue: null }))).toBe("Ô bị AI bỏ sót");
  });
});
