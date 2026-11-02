/**
 * Mã chương cho form nhập tài liệu (§6.5) và form tạo đề ôn tập: đoán chương từ tên file, dựng danh sách lựa chọn và
 * sắp xếp theo số chương (ch1 < ch2 < ch2.1 < ch10). Không rẽ nhánh theo môn: chỉ đọc tên file và mã chương.
 */

import { ApiError } from "@/lib/errors";
import { CHAPTER_CODE_PATTERN } from "@/lib/limits";
import { stripDiacritics } from "@/lib/subject-code";

/** Số chương luôn có trong danh sách chọn khi nhập tài liệu (ch1…ch20). */
export const DEFAULT_CHAPTER_COUNT = 20;

/**
 * Từ khoá đứng trước số chương trong tên file, sau khi bỏ dấu và viết thường. Từ dài đứng trước để "chuong" không bị
 * đọc thành "c" + "huong".
 */
const CHAPTER_IN_NAME =
  /(?:^|[^a-z0-9])(?:chuong|chapter|chap|lecture|lec|bai|ch|c)[\s._-]*0*([1-9][0-9]?)(?:\.([0-9]{1,2}))?(?![0-9])/;

/** Bỏ phần mở rộng cuối (".pdf") để số trong đuôi không bị đọc thành chương con. */
function withoutExtension(filename: string): string {
  return filename.replace(/\.[A-Za-z0-9]{1,5}$/, "");
}

/**
 * Đoán mã chương từ tên file: "Chuong-3_…", "chương 3", "ch3", "ch.3", "Bài 03", "Chapter 12", "C3", "Lecture 3"…
 * ⇒ "ch3"/"ch12"; có phần con ("ch2.1") thì giữ. Không đoán được ⇒ `null`.
 */
export function guessChapterCode(filename: string): string | null {
  const base = stripDiacritics(withoutExtension(filename.trim())).toLowerCase();
  const match = CHAPTER_IN_NAME.exec(base);
  if (match === null) return null;
  const major = match[1];
  if (major === undefined) return null;
  const minor = match[2];
  const code = minor === undefined ? `ch${major}` : `ch${major}.${Number(minor)}`;
  return CHAPTER_CODE_PATTERN.test(code) ? code : null;
}

function chapterSortKey(code: string): [number, number] {
  const match = /^ch([0-9]{1,2})(?:\.([0-9]{1,2}))?$/.exec(code);
  if (match === null) return [Number.MAX_SAFE_INTEGER, Number.MAX_SAFE_INTEGER];
  return [Number(match[1]), match[2] === undefined ? -1 : Number(match[2])];
}

/** So hai mã chương theo số: ch1 < ch2 < ch2.1 < ch10; mã sai dạng xếp cuối theo thứ tự chữ. */
export function compareChapterCodes(left: string, right: string): number {
  const [leftMajor, leftMinor] = chapterSortKey(left);
  const [rightMajor, rightMinor] = chapterSortKey(right);
  if (leftMajor !== rightMajor) return leftMajor < rightMajor ? -1 : 1;
  if (leftMinor !== rightMinor) return leftMinor < rightMinor ? -1 : 1;
  return left < right ? -1 : left > right ? 1 : 0;
}

/** Mã chương hợp lệ, khử trùng, sắp theo số chương. Phần tử `null` hay sai mẫu bị bỏ. */
export function sortedChapterCodes(codes: readonly (string | null)[]): string[] {
  const unique = new Set<string>();
  for (const code of codes) {
    if (code !== null && CHAPTER_CODE_PATTERN.test(code)) unique.add(code);
  }
  return [...unique].sort(compareChapterCodes);
}

/** Các chương đã có tài liệu của môn, lấy từ `chapterCode` của danh sách tài liệu (§6.5). */
export function chaptersOfDocuments(documents: readonly { chapterCode: string | null }[]): string[] {
  return sortedChapterCodes(documents.map((document) => document.chapterCode));
}

/** Lựa chọn của ô chương khi nhập tài liệu: ch1…ch20, các chương môn đã có và chương vừa đoán. */
export function importChapterOptions(existing: readonly string[], guessed: string | null): string[] {
  const defaults = Array.from({ length: DEFAULT_CHAPTER_COUNT }, (_, index) => `ch${index + 1}`);
  return sortedChapterCodes([...defaults, ...existing, guessed]);
}

/** Chương được chọn sau khi nạp lại danh sách: giữ chương cũ nếu còn, không thì chương đầu tiên (hoặc rỗng). */
export function keepOrFirstChapter(current: string, chapters: readonly string[]): string {
  return chapters.includes(current) ? current : (chapters[0] ?? "");
}

/** Hai lời gọi có sẵn trong `lib/api.ts` mà việc nạp chương cần; test thay bằng stub. */
export interface ChapterSource {
  listDocuments: (subjectCode: string) => Promise<readonly { docId: string }[]>;
  getDocument: (documentId: string) => Promise<{ chapterCode: string | null }>;
}

/** Số lời gọi `GET /v1/documents/{document_id}` chạy song song tối đa khi nạp chương. */
export const CHAPTER_LOAD_CONCURRENCY = 4;

/** 409 của `GET /v1/documents/{document_id}`: tài liệu không có `chapter_code` (nhập trước khi chương bắt buộc). */
function isChapterMissing(error: unknown): boolean {
  return error instanceof ApiError && error.status === 409;
}

/**
 * Các chương đã có tài liệu của môn. `GET /v1/documents` (`DocumentSummary`, §6.5) chưa có `chapter_code`, nên đọc
 * chương của từng tài liệu qua `GET /v1/documents/{document_id}`, tối đa `CHAPTER_LOAD_CONCURRENCY` lời gọi cùng lúc.
 * Tài liệu không có chương (409) bị bỏ qua; mọi tài liệu đều bị bỏ qua thì trả rỗng như môn chưa có tài liệu. Lỗi khác
 * của từng tài liệu cũng bỏ qua, trừ khi không đọc được tài liệu nào: khi đó ném lỗi đầu tiên (không báo nhầm là rỗng).
 */
export async function loadSubjectChapters(source: ChapterSource, subjectCode: string): Promise<string[]> {
  const summaries = await source.listDocuments(subjectCode);
  const documents: { chapterCode: string | null }[] = [];
  const failures: unknown[] = [];
  let next = 0;
  async function worker(): Promise<void> {
    while (next < summaries.length) {
      const summary = summaries[next];
      next += 1;
      if (summary === undefined) continue;
      try {
        documents.push(await source.getDocument(summary.docId));
      } catch (error) {
        if (!isChapterMissing(error)) failures.push(error);
      }
    }
  }
  const workers = Math.min(CHAPTER_LOAD_CONCURRENCY, summaries.length);
  await Promise.all(Array.from({ length: workers }, () => worker()));
  if (documents.length === 0 && failures.length > 0) throw failures[0];
  return chaptersOfDocuments(documents);
}
