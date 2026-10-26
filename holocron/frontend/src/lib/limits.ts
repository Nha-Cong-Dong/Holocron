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
export const DOCUMENT_UPLOAD_MAX_BYTES = DOCUMENT_UPLOAD_MAX_MIB * BYTES_PER_MIB;
export const DOCUMENT_MAX_PAGES = 300;

/** `title` của `POST /v1/documents` (§6.5): 1–300 ký tự, bỏ trống thì backend lấy tên file bỏ đuôi. */
export const DOCUMENT_TITLE_MAX_CHARS = 300;

/** `PATCH …/pages/{n}/lines` (§6.5): mỗi dòng tối đa 2000 ký tự, tối đa 500 dòng gõ thêm mỗi lần. */
export const LINE_TEXT_MAX_CHARS = 2000;
export const APPENDED_LINES_MAX = 500;

/** `document_id` = "doc_" + sha256[:16] (§5.7). */
export const DOCUMENT_ID_PATTERN = /^doc_[0-9a-f]{16}$/;

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

/** `Subject.display_name` (§5.14): 1–200 ký tự. */
export const SUBJECT_DISPLAY_NAME_MAX_CHARS = 200;

/** `GET /v1/vault/search` (§6.6): `q` dài 1–200 ký tự, `limit` 1–50 (mặc định 20). */
export const VAULT_SEARCH_MAX_CHARS = 200;
export const VAULT_SEARCH_LIMIT = 20;

/** `GET /v1/vault/chunks` (§6.6): số chunk mỗi trang khi duyệt Vault (`limit` 1–200, mặc định 50). */
export const VAULT_BROWSE_PAGE_SIZE = 50;

/**
 * `topic_keys` của `PATCH /v1/vault/chunks/{chunk_id}/labels` (§6.6). Spec chưa ghi các số này; chép từ backend:
 * mẫu `TOPIC_KEY_PATTERN` ở `holocron_core/domain/schemas.py`, `MAX_TOPIC_KEYS` (32 khoá mỗi request) và
 * `MAX_TOPIC_KEY_CHARS` (128 ký tự mỗi khoá) ở `holocron_core/domain/vault.py`.
 */
export const TOPIC_KEY_PATTERN = /^[a-z0-9_]+(\.[a-z0-9_]+){2,}$/;
export const TOPIC_KEYS_MAX = 32;
export const TOPIC_KEY_MAX_CHARS = 128;
