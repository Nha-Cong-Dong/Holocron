/**
 * Dựng phần hiển thị kết quả chấm tự luận offline (spec §5.14, "Hiển thị (đã duyệt 2026-10-25)").
 *
 * Quy tắc cứng:
 * - Chỉ có câu "Khớp x/y ý" với x = `matched_count`, y = `total_points` do backend trả; frontend không đếm, không quy
 *   ra thang điểm, không tính phần trăm, không dùng màu theo ngưỡng.
 * - Liệt kê ý đã khớp và ý còn thiếu bằng văn bản nguyên văn của tài liệu (backend trả).
 * - Luôn ghi giới hạn của cách chấm: diễn đạt khác tài liệu có thể bị tính là thiếu ý.
 * - `ngram_coverage` đã bị bỏ ở bước kiểm dạng (`lib/practice-data.ts`), nên không thể lọt vào đây.
 */

import type { EssayGradeView, EssayPointView, PracticeCitation } from "@/lib/practice-data";

/** Câu giới hạn bắt buộc (§5.14). */
export const ESSAY_LIMITATION_VI =
  "Chấm theo mức trùng từ với tài liệu. Nếu bạn diễn đạt khác, ý đúng vẫn có thể bị tính là thiếu; hãy tự đối chiếu với trích dẫn.";

export const ESSAY_MATCHED_TITLE_VI = "Ý đã khớp với tài liệu";
export const ESSAY_MISSING_TITLE_VI = "Ý chưa thấy trong câu trả lời (nguyên văn tài liệu)";
export const ESSAY_NONE_MATCHED_VI = "Chưa có ý nào khớp.";
export const ESSAY_NONE_MISSING_VI = "Không còn ý nào thiếu.";

export interface EssayResultView {
  /** "Khớp {matched_count}/{total_points} ý". */
  headline: string;
  matchedTitle: string;
  matched: EssayPointView[];
  /** Câu thay cho danh sách rỗng, `null` khi có ít nhất một ý. */
  matchedEmpty: string | null;
  missingTitle: string;
  missing: EssayPointView[];
  missingEmpty: string | null;
  limitation: string;
  citation: PracticeCitation;
}

/** Câu tóm tắt, dùng cả ở màn kết quả từng câu và danh sách cuối lượt. */
export function essayHeadline(result: Pick<EssayGradeView, "matchedCount" | "totalPoints">): string {
  return `Khớp ${result.matchedCount}/${result.totalPoints} ý`;
}

export function essayResultView(result: EssayGradeView): EssayResultView {
  return {
    headline: essayHeadline(result),
    matchedTitle: ESSAY_MATCHED_TITLE_VI,
    matched: result.matched,
    matchedEmpty: result.matched.length === 0 ? ESSAY_NONE_MATCHED_VI : null,
    missingTitle: ESSAY_MISSING_TITLE_VI,
    missing: result.missing,
    missingEmpty: result.missing.length === 0 ? ESSAY_NONE_MISSING_VI : null,
    limitation: ESSAY_LIMITATION_VI,
    citation: result.citation,
  };
}

/** Mọi chuỗi hệ thống tự viết trong phần kết quả (không gồm văn bản nguyên văn của tài liệu); dùng cho test. */
export function essaySystemStrings(view: EssayResultView): string[] {
  return [
    view.headline,
    view.matchedTitle,
    view.matchedEmpty,
    view.missingTitle,
    view.missingEmpty,
    view.limitation,
  ].filter((text): text is string => text !== null);
}

/** Mọi chuỗi sẽ hiển thị, kể cả văn bản các ý và trích dẫn. */
export function essayDisplayedStrings(view: EssayResultView): string[] {
  return [
    ...essaySystemStrings(view),
    ...view.matched.map((point) => point.text),
    ...view.missing.map((point) => point.text),
    view.citation.verbatim_quote,
    `tr. ${view.citation.page_number}`,
  ];
}
