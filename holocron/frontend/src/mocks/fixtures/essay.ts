/**
 * `EssayGradeResult` mẫu cho `POST /v1/twins/{twin_id}/essay-grade` (§5.14, §6.9; M9 của kế hoạch UI).
 * Handler giả chọn kết quả theo `twin_id`, KHÔNG theo nội dung câu trả lời (kế hoạch UI mục 3.3), nên frontend không
 * chép một phần bộ chấm. Các số `ngram_coverage` là giá trị dựng sẵn, chỉ để đúng dạng contract; giao diện bỏ chúng ở
 * bước kiểm dạng.
 */

import type { EssayGradeResult, EssayGradingThresholds } from "@/types/contract-pending";

import { DEFINITIONS } from "./chunks";
import { ESSAY_SPACED_REVIEW_TWIN_ID, ESSAY_TWO_COLUMN_TWIN_ID, TWIN_BATCH_FIXTURE } from "./twins";

/** Ngưỡng mặc định của §5.14 (đề xuất, chưa hiệu chỉnh). */
const THRESHOLDS: EssayGradingThresholds = {
  point_min_syllables: 4,
  point_max_syllables: 40,
  bigram_weight: 0.5,
  trigram_weight: 0.5,
  point_match_min: 0.5,
};

function citationOf(twinId: string) {
  const twin = TWIN_BATCH_FIXTURE.twins.find((item) => item.twin_id === twinId);
  if (twin === undefined) throw new Error("Câu tự luận mẫu không có trong batch mẫu.");
  return twin.citation;
}

/** Khớp 1/2 ý: câu trả lời mẫu nhắc tới khoảng cách giữa các lần ôn nhưng không nói thời điểm ôn. */
const spacedReviewResult: EssayGradeResult = {
  twin_id: ESSAY_SPACED_REVIEW_TWIN_ID,
  grader: "offline_ngram",
  grader_version: "ngram_v1",
  thresholds: THRESHOLDS,
  total_points: 2,
  matched_count: 1,
  matched_points: [
    {
      point_index: 0,
      text: "Ôn tập giãn cách là cách sắp xếp các lần ôn cùng một nội dung cách nhau ngày càng xa",
      ngram_coverage: { bigram: 0.7368, trigram: 0.6111, combined: 0.674 },
    },
  ],
  missing_points: [
    {
      point_index: 1,
      text: "Mỗi lần ôn nên diễn ra đúng lúc kiến thức sắp bị quên",
      ngram_coverage: { bigram: 0.0833, trigram: 0, combined: 0.0417 },
    },
  ],
  answer_sha256: "3b8f1e0c7a9d2b4e6f8a1c3e5d7b9f0a2c4e6b8d0f1a3c5e7b9d2f4a6c8e0b1d",
  citation: citationOf(ESSAY_SPACED_REVIEW_TWIN_ID),
};

/** Khớp 2/2 ý. */
const twoColumnResult: EssayGradeResult = {
  twin_id: ESSAY_TWO_COLUMN_TWIN_ID,
  grader: "offline_ngram",
  grader_version: "ngram_v1",
  thresholds: THRESHOLDS,
  total_points: 2,
  matched_count: 2,
  matched_points: [
    {
      point_index: 0,
      text: "Ghi chú hai cột là cách chia trang ghi thành cột ý chính và cột chi tiết",
      ngram_coverage: { bigram: 0.875, trigram: 0.8, combined: 0.8375 },
    },
    {
      point_index: 1,
      text: "Cuối trang có một dòng tóm tắt bằng lời của người học",
      ngram_coverage: { bigram: 0.6364, trigram: 0.5, combined: 0.5682 },
    },
  ],
  missing_points: [],
  answer_sha256: "9c2e4a6b8d0f1e3c5a7b9d2f4e6a8c0b1d3f5e7a9c2b4d6f8e0a1c3b5d7f9e2a",
  citation: citationOf(ESSAY_TWO_COLUMN_TWIN_ID),
};

export const ESSAY_GRADE_FIXTURES: Record<string, EssayGradeResult> = {
  [ESSAY_SPACED_REVIEW_TWIN_ID]: spacedReviewResult,
  [ESSAY_TWO_COLUMN_TWIN_ID]: twoColumnResult,
};

/** Định nghĩa gốc của từng câu tự luận mẫu, để test kiểm các ý là chuỗi con của trích dẫn. */
export const ESSAY_SOURCE_QUOTES: Record<string, string> = {
  [ESSAY_SPACED_REVIEW_TWIN_ID]: DEFINITIONS.spacedReview,
  [ESSAY_TWO_COLUMN_TWIN_ID]: DEFINITIONS.twoColumnNotes,
};
