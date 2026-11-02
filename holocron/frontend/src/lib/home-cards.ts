/**
 * Dữ liệu thẻ môn ở trang chủ "Bầu trời của bạn" (design-handoff/README.md màn 1). Chỉ dùng số backend đã trả: số tài
 * liệu là độ dài danh sách `GET /v1/documents`, số khái niệm đã nhận là số cặp `confirmed` của
 * `GET /v1/vault/concept-pairs`, lần cập nhật là `Subject.updated_at`, tiến độ đọc là `page_count − pages_pending` trên
 * `page_count` của chính tài liệu đó (CLAUDE.md §2.1). Không có số "đã ôn" hay "% đúng" vì backend chưa trả.
 */

import type { DocumentSummaryView } from "@/lib/documents-data";
import type { UiError } from "@/lib/errors";
import { subjectHref } from "@/lib/subject-route";
import type { ConceptPair, Subject } from "@/types/holocron";

/** Số môn tải số liệu cùng lúc (mỗi môn hai lời gọi song song). */
export const SUBJECT_STATS_CONCURRENCY = 4;
/** Số dòng "Đang đọc …" tối đa trên một thẻ; phần còn lại gộp thành một dòng đếm. */
export const MAX_READING_LINES = 2;

/** Kết quả tải số liệu của một môn; thiếu trong bảng thì coi như đang tải. */
export type SubjectStats =
  | { kind: "loading" }
  | { kind: "error"; error: UiError }
  | { kind: "ready"; documents: readonly DocumentSummaryView[]; pairs: readonly ConceptPair[] };

export interface ReadingLine {
  docId: string;
  title: string;
  /** Câu hiển thị, ví dụ "Đang đọc Bài 2: 12/40 trang". */
  text: string;
  /** Có số trang hợp lệ để vẽ thanh tiến độ; `null` khi backend chưa biết số trang hoặc số đếm không khớp. */
  progress: { pagesRead: number; pageCount: number } | null;
}

export type CardStats =
  | { kind: "loading" }
  | { kind: "error" }
  | {
      kind: "ready";
      documentCount: number;
      confirmedCount: number;
      reading: ReadingLine[];
      /** Số tài liệu đang đọc không có dòng riêng (vượt `MAX_READING_LINES`). */
      moreReading: number;
      pairs: readonly ConceptPair[];
    };

export interface CardAction {
  label: string;
  href: string;
  ariaLabel: string;
}

export interface HomeCard {
  code: string;
  name: string;
  /** "Cập nhật 29/10/2026"; `null` khi `updated_at` không đọc được. */
  updatedLabel: string | null;
  stats: CardStats;
  action: CardAction;
  /** Đúng một thẻ trên trang mang nút chính. */
  primary: boolean;
}

/** Tên hiển thị của môn: `display_name` (đã kiểm khác rỗng ở ranh giới), dự phòng mã môn. */
export function subjectName(subject: Subject): string {
  const name = subject.display_name.trim();
  return name === "" ? subject.subject_code : name;
}

function twoDigits(value: number): string {
  return String(value).padStart(2, "0");
}

/** Ngày theo kiểu Việt Nam `dd/mm/yyyy` (giờ máy); `null` khi chuỗi thời gian không đọc được. */
export function formatVietnameseDate(iso: string): string | null {
  const time = Date.parse(iso);
  if (Number.isNaN(time)) return null;
  const date = new Date(time);
  return `${twoDigits(date.getDate())}/${twoDigits(date.getMonth() + 1)}/${date.getFullYear()}`;
}

/**
 * Môn mang nút chính: môn mở gần nhất nếu còn trong danh sách, nếu không thì môn có `updated_at` mới nhất (bằng nhau
 * thì môn đứng trước theo thứ tự backend); `updated_at` không đọc được xếp sau cùng. Danh sách rỗng ⇒ `null`.
 */
export function pickPrimarySubject(subjects: readonly Subject[], recentCode: string | null): string | null {
  if (recentCode !== null && subjects.some((subject) => subject.subject_code === recentCode)) return recentCode;
  let best: { code: string; time: number } | null = null;
  for (const subject of subjects) {
    const parsed = Date.parse(subject.updated_at);
    const time = Number.isNaN(parsed) ? Number.NEGATIVE_INFINITY : parsed;
    if (best === null || time > best.time) best = { code: subject.subject_code, time };
  }
  return best?.code ?? null;
}

/** Dòng "Đang đọc" cho mỗi tài liệu `processing`, theo thứ tự backend trả. */
export function readingLines(documents: readonly DocumentSummaryView[]): ReadingLine[] {
  return documents
    .filter((document) => document.status === "processing")
    .map((document) => {
      const valid = document.pageCount > 0 && document.pagesPending <= document.pageCount;
      if (!valid)
        return { docId: document.docId, title: document.title, text: `Đang đọc ${document.title}`, progress: null };
      const pagesRead = document.pageCount - document.pagesPending;
      return {
        docId: document.docId,
        title: document.title,
        text: `Đang đọc ${document.title}: ${pagesRead}/${document.pageCount} trang`,
        progress: { pagesRead, pageCount: document.pageCount },
      };
    });
}

function cardStats(stats: SubjectStats | undefined): CardStats {
  if (stats === undefined || stats.kind === "loading") return { kind: "loading" };
  if (stats.kind === "error") return { kind: "error" };
  const reading = readingLines(stats.documents);
  return {
    kind: "ready",
    documentCount: stats.documents.length,
    confirmedCount: stats.pairs.filter((pair) => pair.status === "confirmed").length,
    reading: reading.slice(0, MAX_READING_LINES),
    moreReading: Math.max(0, reading.length - MAX_READING_LINES),
    pairs: stats.pairs,
  };
}

/**
 * Nút của thẻ: có ít nhất một khái niệm đã nhận ⇒ "Ôn tiếp" (tab Ôn tập); chưa có ⇒ "Thêm tài liệu" (tab Tài liệu);
 * chưa biết (đang tải, lỗi) ⇒ "Mở môn" (tab Tài liệu), để không hứa điều chưa chắc.
 */
export function cardAction(code: string, name: string, stats: CardStats): CardAction {
  if (stats.kind === "ready" && stats.confirmedCount > 0) {
    return { label: "Ôn tiếp", href: subjectHref(code, "practice"), ariaLabel: `Ôn tiếp môn ${name}` };
  }
  if (stats.kind === "ready") {
    return { label: "Thêm tài liệu", href: subjectHref(code, "documents"), ariaLabel: `Thêm tài liệu cho môn ${name}` };
  }
  return { label: "Mở môn", href: subjectHref(code, "documents"), ariaLabel: `Mở môn ${name}` };
}

/** Thẻ môn theo thứ tự backend trả danh sách môn; số liệu từng môn độc lập (một môn lỗi không ảnh hưởng môn khác). */
export function buildHomeCards(
  subjects: readonly Subject[],
  stats: Readonly<Record<string, SubjectStats>>,
  recentCode: string | null,
): HomeCard[] {
  const primary = pickPrimarySubject(subjects, recentCode);
  return subjects.map((subject) => {
    const code = subject.subject_code;
    const name = subjectName(subject);
    const view = cardStats(stats[code]);
    const date = formatVietnameseDate(subject.updated_at);
    return {
      code,
      name,
      updatedLabel: date === null ? null : `Cập nhật ${date}`,
      stats: view,
      action: cardAction(code, name, view),
      primary: code === primary,
    };
  });
}

/**
 * Chạy `task` cho từng phần tử, tối đa `limit` việc cùng lúc, theo thứ tự danh sách. `task` tự bắt lỗi của nó (mỗi
 * thẻ giữ trạng thái lỗi riêng); lỗi lọt ra ngoài làm promise trả về bị từ chối.
 */
export async function runWithLimit<T>(
  items: readonly T[],
  limit: number,
  task: (item: T) => Promise<void>,
): Promise<void> {
  const size = Math.max(1, Math.floor(limit));
  let next = 0;
  async function worker(): Promise<void> {
    while (next < items.length) {
      const item = items[next];
      next += 1;
      if (item !== undefined) await task(item);
    }
  }
  await Promise.all(Array.from({ length: Math.min(size, items.length) }, () => worker()));
}
