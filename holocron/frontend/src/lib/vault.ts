/**
 * Logic thuần của màn Vault (spec §5.8, §6.6): kiểm dạng `KnowledgeChunk`, `ChunkListResponse`, `SearchHit` ở ranh
 * giới, dựng query cho `GET /v1/vault/chunks` và `GET /v1/vault/search`, phân trang bằng `offset`/`limit` của backend,
 * nhóm chunk theo chương (giữ thứ tự backend trả), địa chỉ của màn `/vault` qua query string (static export).
 */

import { charLength } from "@/lib/documents";
import { CHAPTER_CODE_PATTERN, DOCUMENT_ID_PATTERN, SUBJECT_CODE_PATTERN, VAULT_SEARCH_MAX_CHARS } from "@/lib/limits";
import type { ChunkCategory, LabelStatus, SearchIndex } from "@/types/holocron";

// ------------------------------------------------------------------------------------- kiểm dạng

export interface ChunkView {
  chunkId: string;
  docId: string;
  subjectCode: string;
  chapterCode: string;
  pageNumber: number;
  pageEnd: number | null;
  rawText: string;
  verbatimQuote: string;
  /** Mã `ChunkCategory` hoặc mã lạ. */
  category: string;
  topicKeys: string[];
  /** `proposed`, `confirmed` hoặc mã lạ. */
  labelStatus: string;
}

export interface ChunkListView {
  total: number;
  chunks: ChunkView[];
  /** Có chunk sai dạng đã bị bỏ. */
  hasSkipped: boolean;
}

export interface SearchHitView {
  chunk: ChunkView;
  index: SearchIndex | "unknown";
}

export interface SearchResultView {
  hits: SearchHitView[];
  hasSkipped: boolean;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isText(value: unknown): value is string {
  return typeof value === "string" && value.trim() !== "";
}

function isInteger(value: unknown, min: number): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= min;
}

/** Một `KnowledgeChunk` (§5.8); `null` khi thiếu định danh, số trang, văn bản hay trích dẫn. */
export function parseKnowledgeChunk(value: unknown): ChunkView | null {
  if (!isRecord(value)) return null;
  const { chunk_id, doc_id, subject_code, chapter_code, page_number, page_end, raw_text, verbatim_quote } = value;
  if (!isText(chunk_id) || !isText(doc_id) || !isText(subject_code) || !isText(chapter_code)) return null;
  if (!isInteger(page_number, 1) || !isText(raw_text) || !isText(verbatim_quote)) return null;
  const topicKeys = Array.isArray(value.topic_keys) ? value.topic_keys.filter(isText) : [];
  return {
    chunkId: chunk_id,
    docId: doc_id,
    subjectCode: subject_code,
    chapterCode: chapter_code,
    pageNumber: page_number,
    pageEnd: isInteger(page_end, 1) ? page_end : null,
    rawText: raw_text,
    verbatimQuote: verbatim_quote,
    category: isText(value.category) ? value.category : "unknown",
    topicKeys,
    labelStatus: isText(value.label_status) ? value.label_status : "unknown",
  };
}

/** `200 {total, chunks}` của `GET /v1/vault/chunks`; `null` khi sai dạng ở cấp ngoài cùng. */
export function parseChunkList(value: unknown): ChunkListView | null {
  if (!isRecord(value) || !isInteger(value.total, 0) || !Array.isArray(value.chunks)) return null;
  const chunks: ChunkView[] = [];
  for (const raw of value.chunks) {
    const chunk = parseKnowledgeChunk(raw);
    if (chunk !== null) chunks.push(chunk);
  }
  return { total: value.total, chunks, hasSkipped: chunks.length !== value.chunks.length };
}

const SEARCH_INDEXES: readonly SearchIndex[] = ["primary", "no_diacritics"];

/** `200 list[SearchHit]` của `GET /v1/vault/search`. `score` bị bỏ ngay tại đây (§6.6: UI không hiển thị điểm). */
export function parseSearchHits(value: unknown): SearchResultView | null {
  if (!Array.isArray(value)) return null;
  const hits: SearchHitView[] = [];
  for (const raw of value) {
    if (!isRecord(raw)) continue;
    const chunk = parseKnowledgeChunk(raw.chunk);
    if (chunk === null) continue;
    hits.push({ chunk, index: SEARCH_INDEXES.find((index) => index === raw.index) ?? "unknown" });
  }
  return { hits, hasSkipped: hits.length !== value.length };
}

// ------------------------------------------------------------------------------------------ nhãn

export const LABEL_STATUS_LABEL_VI: Record<LabelStatus, string> = {
  proposed: "Nhãn đề xuất, chưa xác nhận",
  confirmed: "Nhãn đã xác nhận",
};

export const CATEGORY_LABEL_VI: Record<ChunkCategory, string> = {
  THEORY: "Lý thuyết",
  PRACTICE: "Bài tập, ví dụ",
};

function labelFrom(table: Readonly<Record<string, string>>, code: string, fallback: string): string {
  return Object.hasOwn(table, code) ? (table[code] ?? fallback) : fallback;
}

export function labelStatusLabel(code: string): string {
  return labelFrom(LABEL_STATUS_LABEL_VI, code, "Trạng thái nhãn chưa rõ");
}

export function categoryLabel(code: string): string {
  return labelFrom(CATEGORY_LABEL_VI, code, "Loại nội dung khác");
}

/** Nhãn trang của chunk: `tr. 3` hoặc `tr. 3–5` (hai số đều do backend trả). */
export function pageRangeLabel(chunk: Pick<ChunkView, "pageNumber" | "pageEnd">): string {
  return chunk.pageEnd === null || chunk.pageEnd === chunk.pageNumber
    ? `tr. ${chunk.pageNumber}`
    : `tr. ${chunk.pageNumber}–${chunk.pageEnd}`;
}

// ------------------------------------------------------------------------------------- query

export interface ChunkFilter {
  subjectCode: string;
  chapterCode: string | null;
  labelStatus: LabelStatus | null;
  topicKeys: readonly string[];
  offset: number;
  limit: number;
}

/** Query string của `GET /v1/vault/chunks` (§6.6); `topic_key` lặp lại cho từng khoá. */
export function chunkQueryString(filter: ChunkFilter): string {
  const params = new URLSearchParams({ subject_code: filter.subjectCode });
  if (filter.chapterCode !== null) params.set("chapter_code", filter.chapterCode);
  for (const key of filter.topicKeys) params.append("topic_key", key);
  if (filter.labelStatus !== null) params.set("label_status", filter.labelStatus);
  params.set("offset", String(filter.offset));
  params.set("limit", String(filter.limit));
  return params.toString();
}

export interface SearchQuery {
  q: string;
  subjectCode: string;
  topicKeys: readonly string[];
  limit: number;
}

/** Query string của `GET /v1/vault/search` (§6.6). */
export function searchQueryString(query: SearchQuery): string {
  const params = new URLSearchParams({ q: query.q, subject_code: query.subjectCode });
  for (const key of query.topicKeys) params.append("topic_key", key);
  params.set("limit", String(query.limit));
  return params.toString();
}

export type SearchTextCheck = { ok: true; q: string } | { ok: false; message: string };

/** Truy vấn tìm kiếm: bỏ khoảng trắng hai đầu, dài 1–200 ký tự (§6.6). */
export function checkSearchText(text: string): SearchTextCheck {
  const q = text.trim();
  if (q === "") return { ok: false, message: "Nhập từ cần tìm." };
  if (charLength(q) > VAULT_SEARCH_MAX_CHARS) {
    return { ok: false, message: `Từ cần tìm tối đa ${VAULT_SEARCH_MAX_CHARS} ký tự.` };
  }
  return { ok: true, q };
}

/** Lọc theo chương: rỗng ⇒ không lọc; sai mẫu ⇒ lỗi để báo trên ô nhập. */
export function parseChapterFilter(text: string): { ok: true; chapter: string | null } | { ok: false } {
  const chapter = text.trim();
  if (chapter === "") return { ok: true, chapter: null };
  return CHAPTER_CODE_PATTERN.test(chapter) ? { ok: true, chapter } : { ok: false };
}

/** Còn trang sau không: theo `total` backend trả và số chunk đã nhận ở trang hiện tại. */
export function hasNextPage(offset: number, received: number, total: number): boolean {
  return received > 0 && offset + received < total;
}

export function previousOffset(offset: number, limit: number): number {
  return Math.max(0, offset - limit);
}

export interface ChapterGroup {
  chapterCode: string;
  chunks: ChunkView[];
}

/** Nhóm chunk theo `chapter_code`, nhóm theo thứ tự xuất hiện đầu tiên, chunk trong nhóm giữ thứ tự backend. */
export function groupByChapter(chunks: readonly ChunkView[]): ChapterGroup[] {
  const groups: ChapterGroup[] = [];
  for (const chunk of chunks) {
    const group = groups.find((item) => item.chapterCode === chunk.chapterCode);
    if (group === undefined) groups.push({ chapterCode: chunk.chapterCode, chunks: [chunk] });
    else group.chunks.push(chunk);
  }
  return groups;
}

// ---------------------------------------------------------------------------- địa chỉ màn /vault

export type VaultTab = "documents" | "chunks";

export interface VaultLocation {
  subject: string | null;
  doc: string | null;
  page: number | null;
  tab: VaultTab;
}

interface SearchParamsLike {
  get: (name: string) => string | null;
}

/** Đọc `?subject=&doc=&page=&tab=`; giá trị sai mẫu bị bỏ (coi như không có), không ném lỗi. */
export function parseVaultLocation(params: SearchParamsLike): VaultLocation {
  const subject = params.get("subject")?.trim() ?? "";
  const doc = params.get("doc")?.trim() ?? "";
  const page = params.get("page")?.trim() ?? "";
  return {
    subject: SUBJECT_CODE_PATTERN.test(subject) ? subject : null,
    doc: DOCUMENT_ID_PATTERN.test(doc) ? doc : null,
    page: /^[1-9][0-9]{0,3}$/.test(page) ? Number(page) : null,
    tab: params.get("tab") === "chunks" ? "chunks" : "documents",
  };
}

/** Địa chỉ của màn Vault; chỉ ghi tham số có giá trị, `tab` mặc định (`documents`) không ghi. */
export function vaultHref(location: Partial<VaultLocation>): string {
  const params = new URLSearchParams();
  if (location.subject) params.set("subject", location.subject);
  if (location.doc) params.set("doc", location.doc);
  if (location.page !== undefined && location.page !== null) params.set("page", String(location.page));
  if (location.tab === "chunks") params.set("tab", "chunks");
  const query = params.toString();
  return query === "" ? "/vault" : `/vault?${query}`;
}
