/**
 * `TwinBatch.twins` là union `TwinQuestion | TheoryTwin` (quyết định 14(b)): Twin Viewer gom cả hai theo slot, nhận ra
 * câu lý thuyết theo `question` và đọc phương án của nó mà không tính lại gì (I1).
 */

import { describe, expect, it } from "vitest";

import type { TheoryTwin, TwinBatch, TwinQuestion } from "@/types/holocron";

import { groupTwinsBySlot, isTheoryTwin, optionViews, THEORY_FORM_LABELS } from "./twin";

const CITATION = {
  doc_id: "doc_aaaaaaaaaaaaaaaa",
  page_number: 1,
  verbatim_quote: "Trích dẫn nguyên văn.",
  chunk_id: null,
};

const THEORY_TWIN: TheoryTwin = {
  question_kind: "theory",
  twin_id: "twin_ly_thuyet",
  blueprint_id: "bp_1",
  slot_index: 1,
  format: "MCQ4",
  question: {
    question_kind: "theory",
    form: "definition_choice",
    subject_code: "SSH1110",
    chapter_code: "ch1",
    seed: 20261028,
    generator_version: "theory_mcq4_v2",
    pool_sha256: "a".repeat(64),
    stem: "Phát biểu nào là định nghĩa của «Không gian»?",
    options: { A: "Định nghĩa A", B: "Định nghĩa B", C: "Định nghĩa C", D: "Định nghĩa D" },
    correct_key: "C",
    distractor_to_misconception_map: {
      A: { misconception_code: "THEORY_SAME_CHAPTER_CONCEPT", label_vi: "Cùng chương", rationale_vi: "Lý do A." },
      B: { misconception_code: "THEORY_SAME_CHAPTER_CONCEPT", label_vi: "Cùng chương", rationale_vi: "Lý do B." },
      D: { misconception_code: "THEORY_SAME_CHAPTER_CONCEPT", label_vi: "Cùng chương", rationale_vi: "Lý do D." },
    },
    distractor_citations: { A: CITATION, B: CITATION, D: CITATION },
    solution_explanation_vi: "Định nghĩa trong tài liệu.",
    citation: CITATION,
  },
  review_status: "AUTO_PASS",
  generated_at: "2026-10-28T08:00:00Z",
};

const COMPUTATIONAL_TWIN: TwinQuestion = {
  twin_id: "twin_tinh_toan",
  blueprint_id: "bp_1",
  slot_index: 2,
  template_id: "tpl_mau",
  topic_key: "mau.dang_bai.gia_tri",
  seed: 1,
  format: "NUMERICAL",
  stem: "Câu tính toán mẫu do sandbox sinh, chỉ để gom nhóm.",
  options: null,
  correct_key: null,
  numerical_answer: { value: 6.25, tolerance_abs: 0.01, unit: null, decimals: 2 },
  distractor_to_misconception_map: {},
  solution_explanation_vi: "Lời giải do backend trả.",
  citation: CITATION,
  trace: {
    sandbox_duration_ms: 1,
    llm_attempts: 0,
    seed_rerolls: 0,
    template_sha256: "0".repeat(64),
    provenance: "precomputed_verified",
  },
  review_status: "AUTO_PASS",
  generated_at: "2026-10-28T08:00:00Z",
  gantt: [],
};

describe("TwinBatch union trong Twin Viewer", () => {
  it("gom câu lý thuyết và câu tính toán theo slot; ticket lý thuyết có topic_key null", () => {
    const batch: TwinBatch = {
      batch_id: "tb_0123456789abcdef",
      blueprint_id: "bp_1",
      base_seed: 1,
      twins: [COMPUTATIONAL_TWIN, THEORY_TWIN],
      review_tickets: [
        { blueprint_id: "bp_1", slot_index: 3, topic_key: null, reason: "không đủ căn cứ", attempts: [] },
      ],
      created_at: "2026-10-28T08:00:00Z",
    };
    const groups = groupTwinsBySlot(batch);
    expect(groups.map((group) => group.slotIndex)).toEqual([1, 2, 3]);
    expect(groups[0]?.twins.map(isTheoryTwin)).toEqual([true]);
    expect(groups[1]?.twins.map(isTheoryTwin)).toEqual([false]);
    expect(groups[2]?.ticket?.topic_key).toBeNull();
  });

  it("phương án của câu lý thuyết: giữ thứ tự A–D, đáp án và badge đúng như backend trả", () => {
    const views = optionViews(THEORY_TWIN.question);
    expect(views.map((view) => view.key)).toEqual(["A", "B", "C", "D"]);
    expect(views.filter((view) => view.isCorrect).map((view) => view.key)).toEqual(["C"]);
    expect(views.find((view) => view.key === "C")?.misconception).toBeNull();
    expect(views.find((view) => view.key === "A")?.misconception?.rationale_vi).toBe("Lý do A.");
    expect(THEORY_FORM_LABELS[THEORY_TWIN.question.form]).toBe("Chọn định nghĩa đúng");
  });
});
