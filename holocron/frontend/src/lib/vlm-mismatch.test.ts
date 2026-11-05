import { describe, expect, it, vi } from "vitest";
import { VLM_MISMATCH_PAGE } from "@/mocks/fixtures/documents";
import type { VlmMismatchView } from "./documents-data";

import { parsePageExtraction } from "./documents-data";
import {
  type ConfirmState,
  groupMismatchesByBlock,
  keepMismatchPatch,
  mismatchCellLabel,
  mismatchDoneMessage,
  type PatchPageLinesFn,
  reduceConfirm,
  rejectBlockPatch,
  writeMismatchPatch,
} from "./vlm-mismatch";

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

describe("writeMismatchPatch: đúng thứ tự kept rồi rejected", () => {
  function run(patch: ReturnType<typeof keepMismatchPatch>): unknown[] {
    const page = parsePageExtraction(structuredClone(VLM_MISMATCH_PAGE));
    if (page === null) throw new Error("fixture không hợp lệ");
    const spy = vi.fn<PatchPageLinesFn>(() => Promise.resolve(page));
    void writeMismatchPatch(spy, "doc1", 2, 5, patch);
    expect(spy).toHaveBeenCalledTimes(1);
    return spy.mock.calls[0] ?? [];
  }
  it("giữ một chỗ lệch ⇒ kept=[id], rejected=[]", () => {
    expect(run(keepMismatchPatch("m1"))).toEqual(["doc1", 2, 5, [], [], ["m1"], []]);
  });
  it("bỏ một khối ⇒ kept=[], rejected=[id]", () => {
    expect(run(rejectBlockPatch("b1"))).toEqual(["doc1", 2, 5, [], [], [], ["b1"]]);
  });
  it("thông báo theo loại thao tác", () => {
    expect(mismatchDoneMessage(keepMismatchPatch("m1"))).toBe("Đã giữ giá trị này.");
    expect(mismatchDoneMessage(rejectBlockPatch("b1"))).toBe("Đã bỏ khối AI đọc.");
  });
});

describe("reduceConfirm: bước xác nhận 'Bỏ khối AI đọc'", () => {
  const idle: ConfirmState = { confirming: null };
  it("bấm 'Bỏ khối AI đọc' chỉ mở bước hỏi, không gửi", () => {
    expect(reduceConfirm(idle, { type: "askReject", blockId: "b1" })).toEqual({
      state: { confirming: "b1" },
      patch: null,
    });
  });
  it("không qua bước hỏi thì 'xác nhận' không gửi", () => {
    expect(reduceConfirm(idle, { type: "confirmReject", blockId: "b1" }).patch).toBeNull();
  });
  it("xác nhận khối khác với khối đang hỏi không gửi", () => {
    expect(reduceConfirm({ confirming: "b1" }, { type: "confirmReject", blockId: "b2" }).patch).toBeNull();
  });
  it("xác nhận đúng khối ⇒ gửi bỏ đúng khối đó và đóng bước hỏi", () => {
    expect(reduceConfirm({ confirming: "b1" }, { type: "confirmReject", blockId: "b1" })).toEqual({
      state: { confirming: null },
      patch: { keptMismatches: [], rejectedVlmBlocks: ["b1"] },
    });
  });
  it("huỷ đóng bước hỏi, không gửi", () => {
    expect(reduceConfirm({ confirming: "b1" }, { type: "cancel" })).toEqual({
      state: { confirming: null },
      patch: null,
    });
  });
  it("'Giữ giá trị này' gửi ngay đúng một chỗ lệch", () => {
    expect(reduceConfirm(idle, { type: "keep", mismatchId: "m1" }).patch).toEqual({
      keptMismatches: ["m1"],
      rejectedVlmBlocks: [],
    });
  });
});

describe("parsePageExtraction: chỗ lệch VLM", () => {
  it("fixture riêng có TABLE_TOKEN_MISMATCH và hai chỗ lệch hợp lệ", () => {
    const page = parsePageExtraction(structuredClone(VLM_MISMATCH_PAGE));
    expect(page?.reviewReasons).toContain("TABLE_TOKEN_MISMATCH");
    expect(page?.vlmMismatches).toHaveLength(2);
  });
  it("phần tử sai dạng bị bỏ nhưng trang giữ nguyên các chỗ lệch hợp lệ", () => {
    const raw: Record<string, unknown> = structuredClone({ ...VLM_MISMATCH_PAGE });
    raw.vlm_mismatches = [
      ...VLM_MISMATCH_PAGE.vlm_mismatches,
      { mismatch_id: "x", block_id: "b", token_kind: "weird" },
      42,
    ];
    expect(parsePageExtraction(raw)?.vlmMismatches).toHaveLength(2);
  });
});
