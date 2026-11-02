import { describe, expect, it } from "vitest";

import { parseConceptPair } from "./pairs";

const BASE = {
  pair_id: "cp_1",
  chunk_id: "chk_1",
  subject_code: "MAU101",
  chapter_code: "ch2",
  term: "Thuật ngữ",
  definition_quote: "Đoạn định nghĩa nguyên văn.",
  term_spans: [],
  status: "proposed",
  proposed_by: "llm",
  mapping_confidence: null,
  version: 1,
  created_at: "",
  confirmed_at: null,
};

describe("parseConceptPair: pre_selected và failed_gates (§5.8)", () => {
  it("giữ đúng giá trị backend trả", () => {
    const pair = parseConceptPair({ ...BASE, pre_selected: true, failed_gates: [] });
    expect(pair?.pre_selected).toBe(true);
    expect(pair?.failed_gates).toEqual([]);
    const gated = parseConceptPair({ ...BASE, pre_selected: false, failed_gates: ["G1_SOURCE", "EXERCISE"] });
    expect(gated?.failed_gates).toEqual(["G1_SOURCE", "EXERCISE"]);
  });

  it("pre_selected không phải boolean thì là false", () => {
    for (const raw of [undefined, null, "true", 1, {}]) {
      expect(parseConceptPair({ ...BASE, pre_selected: raw, failed_gates: [] })?.pre_selected).toBe(false);
    }
  });

  it("failed_gates không phải mảng thì rỗng; phần tử lạ bị bỏ", () => {
    for (const raw of [undefined, null, "G1_SOURCE", { 0: "G1_SOURCE" }]) {
      expect(parseConceptPair({ ...BASE, failed_gates: raw })?.failed_gates).toEqual([]);
    }
    const mixed = parseConceptPair({
      ...BASE,
      failed_gates: ["G2_GLYPH", "G9", 3, null, "g4_column_mix", "G5_NUMBERS"],
    });
    expect(mixed?.failed_gates).toEqual(["G2_GLYPH", "G5_NUMBERS"]);
  });
});
