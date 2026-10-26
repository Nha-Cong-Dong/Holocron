/**
 * Giới hạn do backend cưỡng chế, chép theo spec để giao diện báo trước và viết câu lỗi đúng đơn vị. Đây là chỗ DUY
 * NHẤT ghi các số này ở frontend; component và `lib/errors.ts` đọc từ đây, không ghi cứng lại.
 *
 * Backend vẫn là nơi kiểm cuối cùng: giao diện chỉ dùng các số này để báo sớm, không thay cho kiểm tra ở backend.
 */

/** Một MiB (spec §6.0 dùng MiB: 10 MiB cho upload đề, 50 MiB cho nhập tài liệu). */
export const BYTES_PER_MIB = 1024 * 1024;

/** `POST /v1/ingestion/upload` (§6.1, §6.0): ảnh hoặc PDF đề tối đa 10 MiB. */
export const EXAM_UPLOAD_MAX_MIB = 10;
export const EXAM_UPLOAD_MAX_BYTES = EXAM_UPLOAD_MAX_MIB * BYTES_PER_MIB;

/** `POST /v1/documents` (§6.5, ADR-008 mục 4): PDF tối đa 50 MiB và 300 trang. */
export const DOCUMENT_UPLOAD_MAX_MIB = 50;
export const DOCUMENT_MAX_PAGES = 300;

/** Tên file tối đa 255 ký tự (§6.5). */
export const FILENAME_MAX_CHARS = 255;

/** `quantum` của upload đề: số nguyên trong khoảng này (§6.1). */
export const QUANTUM_MIN = 1;
export const QUANTUM_MAX = 20;

/** `BlueprintCreate.theory_slots` tối đa 50 phần tử (§5.9). */
export const MAX_THEORY_SLOTS = 50;

/** Mẫu `chapter_code` của `TheorySlotSpec`, `KnowledgeChunk`, `ConceptPair` (§5.8, §5.9). */
export const CHAPTER_CODE_PATTERN = /^ch[0-9]{1,2}(\.[0-9]{1,2})?$/;

/** Mẫu `subject_code` (§5.14). */
export const SUBJECT_CODE_PATTERN = /^[A-Z]{2,6}[0-9]{2,4}$/;
