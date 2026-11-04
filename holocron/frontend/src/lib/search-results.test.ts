import { describe, expect, it } from "vitest";

import type { Block, BlockSearchHit, MatchSpan } from "@/types/holocron";

import { blockTypeLabel, groupHits, highlightParts, isUncertain, showsCrop, toggleType } from "./search-results";

function block(overrides: Partial<Block> = {}): Block {
  return {
    block_id: "b1",
    document_id: "d1",
    page_number: 3,
    type: "paragraph",
    bbox: null,
    text: "xin chào thế giới",
    line_refs: [],
    label_line_refs: [],
    labels: [],
    level: null,
    list_depth: null,
    cells: null,
    caption_of: null,
    semantic_label: null,
    flags: [],
    crop_ref: null,
    source: "text_layer",
    ocr_text: null,
    reading_order: 0,
    ...overrides,
  };
}

function hit(sectionId: string, documentId = "d1", overrides: Partial<Block> = {}): BlockSearchHit {
  return {
    block: block({ document_id: documentId, ...overrides }),
    document_title: "Slide",
    chapter_code: "1",
    section_path: [
      { section_id: "c", title: "Chương 1" },
      { section_id: sectionId, title: null },
    ],
    match_kind: "exact",
    match_spans: [],
    score: 1,
  };
}

function span(start: number, end: number, target: MatchSpan["target"] = "text"): MatchSpan {
  return { target, row: null, cell_index: null, label_index: null, start, end };
}

describe("groupHits", () => {
  it("gom theo (tài liệu, mục) và giữ thứ tự xuất hiện", () => {
    const groups = groupHits([hit("s1"), hit("s2"), hit("s1"), hit("s1", "d2")]);
    expect(groups.map((group) => group.hits.length)).toEqual([2, 1, 1]);
    expect(groups[0]?.path).toBe("Chương 1 › (Chưa có tiêu đề)");
  });
});

describe("highlightParts", () => {
  it("cắt đúng theo khoảng backend trả và không mất chữ", () => {
    const parts = highlightParts("xin chào thế giới", [span(4, 8)]);
    expect(parts).toEqual([
      { text: "xin ", match: false },
      { text: "chào", match: true },
      { text: " thế giới", match: false },
    ]);
    expect(parts.map((part) => part.text).join("")).toBe("xin chào thế giới");
  });

  it("bỏ khoảng đích khác, khoảng chồng và kẹp khoảng quá cỡ", () => {
    expect(highlightParts("abc", [span(0, 1, "cell")])).toEqual([{ text: "abc", match: false }]);
    expect(
      highlightParts("abcdef", [span(0, 3), span(2, 4)])
        .map((part) => part.text)
        .join(""),
    ).toBe("abcdef");
    expect(highlightParts("abc", [span(1, 99)])).toEqual([
      { text: "a", match: false },
      { text: "bc", match: true },
    ]);
  });
});

describe("nhãn, cờ và bộ lọc", () => {
  it("nhãn loại khối tiếng Việt", () => {
    expect(blockTypeLabel("table")).toBe("Bảng");
    expect(blockTypeLabel("figure")).toBe("Hình");
  });

  it("toggleType bật tắt và giữ thứ tự cố định", () => {
    expect(toggleType(["code"], "paragraph")).toEqual(["paragraph", "code"]);
    expect(toggleType(["paragraph", "code"], "paragraph")).toEqual(["code"]);
  });

  it("cờ chưa chắc làm khối cần ảnh gốc; cờ khác thì không", () => {
    expect(isUncertain(block({ flags: ["table_unresolved"] }))).toBe(true);
    expect(isUncertain(block({ flags: ["human_edited"] }))).toBe(false);
    expect(showsCrop(block({ type: "figure", crop_ref: "a" }))).toBe(true);
    expect(showsCrop(block({ type: "paragraph", crop_ref: "a" }))).toBe(false);
    expect(showsCrop(block({ type: "paragraph", crop_ref: "a", flags: ["layout_uncertain"] }))).toBe(true);
    expect(showsCrop(block({ type: "figure", crop_ref: null }))).toBe(false);
  });
});
