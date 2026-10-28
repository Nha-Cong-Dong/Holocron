/**
 * `TwinBatch` mẫu theo §5.11, §5.14 và §6.7 (M8, M9 của kế hoạch UI) cho blueprint mẫu ở `blueprints.ts`:
 * - slot 1: câu lý thuyết MCQ4 dạng "chọn định nghĩa đúng" (phương án nhiễu cùng chương);
 * - slot 2: câu lý thuyết MCQ4 dạng "ghép khái niệm" (một phương án nhiễu cùng môn, khác chương);
 * - slot 3, 4: câu tự luận "Trình bày khái niệm X" với các ý tách nguyên văn từ định nghĩa;
 * - slot 5: `ReviewTicket` "không đủ căn cứ trong Vault".
 *
 * Fixture dựng sẵn, không tính số: thứ tự phương án, `correct_key`, các ý và `syllable_count` được viết tay theo đúng
 * luật của contract (mặt nạ `____` cho tên khái niệm trong phương án, mã misconception thuộc `THEORY_MISCONCEPTIONS`).
 * Gỡ cùng thư mục `src/mocks/` khi backend nối câu lý thuyết vào `POST /v1/blueprints/{id}/twins`.
 */

import type {
  MisconceptionRefPending,
  TheoryTrace,
  TwinBatchPending,
  TwinQuestionPending,
} from "@/types/contract-pending";
import type { Citation } from "@/types/holocron";

import { MOCK_BLUEPRINT_ID } from "./blueprints";
import { chunkOf, DEFINITIONS } from "./chunks";

/** Danh sách mã misconception cố định của câu lý thuyết (ADR-007 mục 2), chép từ bộ sinh ở backend. */
export const THEORY_MISCONCEPTIONS = ["THEORY_SAME_CHAPTER_CONCEPT", "THEORY_SAME_SUBJECT_CONCEPT"] as const;

const BLANK = "____";

function citationFor(quote: string): Citation {
  const source = chunkOf(quote);
  return { doc_id: source.doc_id, page_number: source.page_number, verbatim_quote: quote, chunk_id: source.chunk_id };
}

function masked(quote: string, term: string): string {
  return quote.split(term).join(BLANK);
}

function sameChapter(term: string, quote: string): MisconceptionRefPending {
  return {
    misconception_code: "THEORY_SAME_CHAPTER_CONCEPT",
    label_vi: "Khái niệm khác cùng chương",
    rationale_vi: `«${term}»: ${quote}`,
    citation: citationFor(quote),
  };
}

function sameSubject(term: string, quote: string): MisconceptionRefPending {
  return {
    misconception_code: "THEORY_SAME_SUBJECT_CONCEPT",
    label_vi: "Khái niệm khác cùng môn",
    rationale_vi: `«${term}»: ${quote}`,
    citation: citationFor(quote),
  };
}

const TRACE: TheoryTrace = {
  kind: "theory",
  generator_version: "theory_mcq4_v2",
  pool_sha256: "6f0b3c1d9e8a7f6b5c4d3e2f1a0b9c8d7e6f5a4b3c2d1e0f9a8b7c6d5e4f3a2b",
  seed_rerolls: 0,
};

const GENERATED_AT = "2026-10-26T08:30:00Z";

function theoryBase(twinId: string, slotIndex: number, seed: number) {
  return {
    twin_id: twinId,
    blueprint_id: MOCK_BLUEPRINT_ID,
    slot_index: slotIndex,
    question_kind: "theory" as const,
    template_id: null,
    topic_key: null,
    seed,
    numerical_answer: null,
    trace: TRACE,
    review_status: "AUTO_PASS" as const,
    generated_at: GENERATED_AT,
    artifacts: [],
  };
}

const definitionChoice: TwinQuestionPending = {
  ...theoryBase("twin_mau_01", 1, 4815162342),
  format: "MCQ4",
  stem: "Phát biểu nào là định nghĩa của «Tự kiểm tra»?",
  options: {
    A: masked(DEFINITIONS.interleaving, "Học xen kẽ"),
    B: masked(DEFINITIONS.selfTest, "Tự kiểm tra"),
    C: masked(DEFINITIONS.spacedReview, "Ôn tập giãn cách"),
    D: masked(DEFINITIONS.twoColumnNotes, "Ghi chú hai cột"),
  },
  correct_key: "B",
  distractor_to_misconception_map: {
    A: sameChapter("Học xen kẽ", DEFINITIONS.interleaving),
    C: sameChapter("Ôn tập giãn cách", DEFINITIONS.spacedReview),
    D: sameChapter("Ghi chú hai cột", DEFINITIONS.twoColumnNotes),
  },
  solution_explanation_vi: DEFINITIONS.selfTest,
  citation: citationFor(DEFINITIONS.selfTest),
  essay: null,
};

const conceptMatch: TwinQuestionPending = {
  ...theoryBase("twin_mau_02", 2, 4815239311),
  format: "MCQ4",
  stem: `Khái niệm nào ứng với đoạn trích sau?\n\n> ${masked(DEFINITIONS.twoColumnNotes, "Ghi chú hai cột")}`,
  options: { A: "Học xen kẽ", B: "Sơ đồ tư duy", C: "Ghi chú hai cột", D: "Ôn tập giãn cách" },
  correct_key: "C",
  distractor_to_misconception_map: {
    A: sameChapter("Học xen kẽ", DEFINITIONS.interleaving),
    B: sameSubject("Sơ đồ tư duy", DEFINITIONS.mindMap),
    D: sameChapter("Ôn tập giãn cách", DEFINITIONS.spacedReview),
  },
  solution_explanation_vi: DEFINITIONS.twoColumnNotes,
  citation: citationFor(DEFINITIONS.twoColumnNotes),
  essay: null,
};

function essayQuestion(
  twinId: string,
  slotIndex: number,
  seed: number,
  pairId: string,
  term: string,
  quote: string,
  points: { text: string; syllable_count: number }[],
): TwinQuestionPending {
  return {
    ...theoryBase(twinId, slotIndex, seed),
    format: "ESSAY",
    stem: `Trình bày khái niệm «${term}».`,
    options: null,
    correct_key: null,
    distractor_to_misconception_map: {},
    solution_explanation_vi: quote,
    citation: citationFor(quote),
    essay: {
      essay_kind: "define_concept",
      pair_id: pairId,
      term,
      splitter_version: "1",
      points: points.map((point, index) => ({ point_index: index, ...point })),
    },
  };
}

export const ESSAY_SPACED_REVIEW_TWIN_ID = "twin_mau_03";
export const ESSAY_TWO_COLUMN_TWIN_ID = "twin_mau_04";

const essaySpacedReview = essayQuestion(
  ESSAY_SPACED_REVIEW_TWIN_ID,
  3,
  4815316280,
  "cp_mau_spaced_review",
  "Ôn tập giãn cách",
  DEFINITIONS.spacedReview,
  [
    {
      text: "Ôn tập giãn cách là cách sắp xếp các lần ôn cùng một nội dung cách nhau ngày càng xa",
      syllable_count: 20,
    },
    { text: "Mỗi lần ôn nên diễn ra đúng lúc kiến thức sắp bị quên", syllable_count: 13 },
  ],
);

const essayTwoColumn = essayQuestion(
  ESSAY_TWO_COLUMN_TWIN_ID,
  4,
  4815393249,
  "cp_mau_two_column",
  "Ghi chú hai cột",
  DEFINITIONS.twoColumnNotes,
  [
    { text: "Ghi chú hai cột là cách chia trang ghi thành cột ý chính và cột chi tiết", syllable_count: 17 },
    { text: "Cuối trang có một dòng tóm tắt bằng lời của người học", syllable_count: 12 },
  ],
);

export const THEORY_TICKET_REASON = "không đủ căn cứ trong Vault";

export const TWIN_BATCH_FIXTURE: TwinBatchPending = {
  batch_id: "tb_5a1e3c7d9b2f4e60",
  blueprint_id: MOCK_BLUEPRINT_ID,
  base_seed: 4815162342,
  twins: [definitionChoice, conceptMatch, essaySpacedReview, essayTwoColumn],
  review_tickets: [
    { blueprint_id: MOCK_BLUEPRINT_ID, slot_index: 5, topic_key: null, reason: THEORY_TICKET_REASON, attempts: [] },
  ],
  created_at: GENERATED_AT,
};

/** Kịch bản trống: mọi slot đều vào `review_tickets` vì chương chưa có cặp khái niệm đã xác nhận. */
export const EMPTY_TWIN_BATCH_FIXTURE: TwinBatchPending = {
  batch_id: "tb_0c9e8d7f6a5b4c30",
  blueprint_id: MOCK_BLUEPRINT_ID,
  base_seed: 4815162342,
  twins: [],
  review_tickets: [1, 2, 3, 4, 5].map((slotIndex) => ({
    blueprint_id: MOCK_BLUEPRINT_ID,
    slot_index: slotIndex,
    topic_key: null,
    reason: THEORY_TICKET_REASON,
    attempts: [],
  })),
  created_at: GENERATED_AT,
};
