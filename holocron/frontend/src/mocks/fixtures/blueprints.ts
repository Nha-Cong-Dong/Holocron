/**
 * `ExamBlueprint` mẫu cho `POST /v1/blueprints` (§5.9, §6.7; M7 của kế hoạch UI): chỉ có slot lý thuyết của môn
 * `MAU101`, chương `ch1`. Handler giả luôn trả đúng bản này, không dựng lại theo lựa chọn trên form (không tính số).
 * Gỡ cùng thư mục `src/mocks/` khi backend có `POST /v1/blueprints`.
 */

import type { BlueprintSlotPending, ExamBlueprintPending } from "@/types/contract-pending";
import type { TheorySlotSpec } from "@/types/holocron";

type TheorySlotFormat = NonNullable<TheorySlotSpec["format"]>;

export const MOCK_BLUEPRINT_ID = "bp_mau101_ch1_luyen_tap";

function theorySlot(slotIndex: number, format: TheorySlotFormat, pageNumber: number | null): BlueprintSlotPending {
  return {
    slot_index: slotIndex,
    source_page_number: pageNumber,
    format,
    chapter_code: "ch1",
    topic_key: null,
    cognitive_level: "remember",
    question_kind: "theory",
    non_cloneable: false,
    non_cloneable_reason: null,
    distractor_archetypes: [],
    self_solve: null,
    mapping_confidence: 1,
    review_status: "NEEDS_REVIEW",
  };
}

export const BLUEPRINT_FIXTURE: ExamBlueprintPending = {
  blueprint_id: MOCK_BLUEPRINT_ID,
  source_exam_doc_id: "doc_4f1c2a9e7b3d5c80",
  subject_code: "MAU101",
  total_questions: 5,
  duration_minutes: null,
  slots: [
    theorySlot(1, "MCQ4", null),
    theorySlot(2, "MCQ4", null),
    theorySlot(3, "ESSAY", null),
    theorySlot(4, "ESSAY", null),
    theorySlot(5, "MCQ4", null),
  ],
  created_at: "2026-10-26T08:29:00Z",
};
