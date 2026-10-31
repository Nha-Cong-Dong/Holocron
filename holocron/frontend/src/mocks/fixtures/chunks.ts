/**
 * Chunk mẫu của Knowledge Vault (§5.8) cho màn luyện tập: văn bản tự viết về phương pháp học, không lấy từ tài liệu có
 * bản quyền. Mọi trích dẫn của `twins.ts` và `essay.ts` là chuỗi con của `raw_text` ở đây (I3, có test).
 * Gỡ cùng thư mục `src/mocks/` khi backend có `GET /v1/vault/chunks` (§6.6).
 */

import type { KnowledgeChunk } from "@/types/holocron";

export const MOCK_DOC_ID = "doc_4f1c2a9e7b3d5c80";

/** Định nghĩa nguyên văn của các cặp khái niệm mẫu (`definition_quote`); mỗi câu nằm nguyên trong một chunk. */
export const DEFINITIONS = {
  spacedReview:
    "Ôn tập giãn cách là cách sắp xếp các lần ôn cùng một nội dung cách nhau ngày càng xa. Mỗi lần ôn nên diễn ra đúng lúc kiến thức sắp bị quên.",
  selfTest:
    "Tự kiểm tra là việc chủ động nhớ lại nội dung mà không nhìn tài liệu, rồi mới đối chiếu với tài liệu để tìm chỗ sai.",
  interleaving:
    "Học xen kẽ là cách trộn nhiều dạng bài hoặc nhiều chủ đề trong cùng một buổi học, thay vì làm liên tiếp một dạng cho tới khi xong.",
  twoColumnNotes:
    "Ghi chú hai cột là cách chia trang ghi thành cột ý chính và cột chi tiết. Cuối trang có một dòng tóm tắt bằng lời của người học.",
  mindMap:
    "Sơ đồ tư duy là hình vẽ đặt chủ đề chính ở giữa, các ý phụ toả ra thành nhánh, mỗi nhánh có thể chia tiếp thành ý nhỏ hơn.",
} as const;

function chunk(
  chunkId: string,
  chapterCode: string,
  pageNumber: number,
  rawText: string,
  verbatimQuote: string,
): KnowledgeChunk {
  return {
    chunk_id: chunkId,
    doc_id: MOCK_DOC_ID,
    subject_code: "MAU101",
    chapter_code: chapterCode,
    page_number: pageNumber,
    page_end: null,
    raw_text: rawText,
    verbatim_quote: verbatimQuote,
    category: "THEORY",
    tags: ["#theory"],
    topic_keys: [],
    linked_chunk_ids: [],
    token_count: 64,
    embedding_model: null,
    label_status: "confirmed",
    mapping_confidence: null,
    version: 1,
    is_exercise: null,
  };
}

export const CHUNK_FIXTURES: KnowledgeChunk[] = [
  chunk(
    "chk_mau101_p3",
    "ch1",
    3,
    `Bài 1. Ghi nhớ lâu dài\n\n${DEFINITIONS.spacedReview}\n${DEFINITIONS.selfTest}\nHai cách này dùng tốt nhất khi đi cùng nhau.`,
    DEFINITIONS.spacedReview,
  ),
  chunk(
    "chk_mau101_p4",
    "ch1",
    4,
    `${DEFINITIONS.interleaving}\n\n${DEFINITIONS.twoColumnNotes}`,
    DEFINITIONS.interleaving,
  ),
  chunk("chk_mau101_p7", "ch2", 7, `Bài 2. Sắp xếp ý\n\n${DEFINITIONS.mindMap}`, DEFINITIONS.mindMap),
];

/** Chunk chứa mỗi định nghĩa, để fixture dựng `Citation` đúng `chunk_id` và số trang. */
export function chunkOf(quote: string): KnowledgeChunk {
  const found = CHUNK_FIXTURES.find((item) => item.raw_text.includes(quote));
  if (found === undefined) throw new Error("Định nghĩa mẫu không nằm trong chunk mẫu nào.");
  return found;
}
