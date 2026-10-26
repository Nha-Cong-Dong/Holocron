/**
 * Handler giả cho tài liệu mẫu (M4, M5; spec §6.5). `lib/api.ts` chỉ gọi các hàm này cho mã tài liệu mẫu
 * (`isMockDocument`); mọi tài liệu khác gọi backend thật. Handler chỉ chọn giữa các bản fixture dựng sẵn và kiểm các
 * điều kiện lỗi 409/404 của contract; không tính lại `review_reasons`, `status` của trang hay số đếm nào.
 * Dữ liệu trả về đi qua đúng bước kiểm dạng như dữ liệu thật.
 */

import {
  type DocumentSummaryView,
  type DocumentView,
  type PageView,
  parseDocumentImport,
  parseDocumentSummaries,
  parsePageExtraction,
} from "@/lib/documents-data";
import { ApiError, type ClassifyOptions, classifyHttpError, invalidResponseError } from "@/lib/errors";
import type { PageExtractionPending } from "@/types/contract-pending";

import {
  confirmedPage,
  EDITED_PAGES,
  MOCK_DOCUMENT_SUMMARY,
  MOCK_IMPORT_SUBJECT,
  mockDocumentImport,
} from "./fixtures/documents";
import { type MockDocumentSession, mockDocumentSession } from "./store";

function checked<T>(value: T | null): T {
  if (value === null) throw new ApiError(200, invalidResponseError());
  return value;
}

function reject(status: number, detail: string, options: ClassifyOptions): Promise<never> {
  return Promise.reject(new ApiError(status, classifyHttpError(status, { detail }, options)));
}

const REVIEW: ClassifyOptions = { context: "document_review" };
const REVIEW_VERSIONED: ClassifyOptions = { context: "document_review", versioned: true };

function liveSession(documentId: string): MockDocumentSession | null {
  const session = mockDocumentSession(documentId);
  return session.deleted ? null : session;
}

function sortedPages(session: MockDocumentSession): PageExtractionPending[] {
  return [...session.pages.values()].sort((a, b) => a.page_number - b.page_number);
}

/** `confirmed` khi mọi trang có văn bản (không lỗi) đã xác nhận, ngược lại `review` (§6.5 "Xác nhận"). */
function documentStatus(pages: readonly PageExtractionPending[]): "review" | "confirmed" {
  const textPages = pages.filter((page) => page.status !== "failed");
  return textPages.length > 0 && textPages.every((page) => page.confirmed) ? "confirmed" : "review";
}

function documentView(session: MockDocumentSession): DocumentView {
  const pages = sortedPages(session);
  return checked(parseDocumentImport(mockDocumentImport(pages, documentStatus(pages))));
}

function pageView(page: PageExtractionPending): PageView {
  return checked(parsePageExtraction(structuredClone(page)));
}

/** Tài liệu mẫu của môn (chỉ môn của fixture, khi chưa bị xoá trong phiên). Số đếm là bản dựng sẵn. */
function listDocuments(subjectCode: string): DocumentSummaryView[] {
  if (subjectCode !== MOCK_IMPORT_SUBJECT || mockDocumentSession(MOCK_DOCUMENT_SUMMARY.meta.doc_id).deleted) return [];
  return checked(parseDocumentSummaries([structuredClone(MOCK_DOCUMENT_SUMMARY)]));
}

function getDocument(documentId: string): Promise<DocumentView> {
  const session = liveSession(documentId);
  if (session === null) return reject(404, "không tìm thấy tài liệu", REVIEW);
  return Promise.resolve(documentView(session));
}

function getDocumentPage(documentId: string, pageNumber: number): Promise<PageView> {
  const page = liveSession(documentId)?.pages.get(pageNumber);
  if (page === undefined) return reject(404, "không tìm thấy tài liệu hoặc trang", REVIEW);
  return Promise.resolve(pageView(page));
}

/** Lỗi 409 chung của sửa dòng và xác nhận trang (§6.5): trang đang đọc, đọc lỗi, đã xác nhận, hay lệch version. */
function pageConflict(page: PageExtractionPending, expectedVersion: number, allowConfirmed: boolean): string | null {
  if (page.status === "pending") return "trang còn đang đọc";
  if (page.status === "failed") return "trang đọc lỗi không sửa hay xác nhận được";
  if (page.confirmed && !allowConfirmed) return "trang đã xác nhận";
  if (page.version !== expectedVersion) return `version lệch: hiện tại ${page.version}`;
  return null;
}

/**
 * M5: `PATCH …/lines` trả bản "sau khi sửa" dựng sẵn của trang (không theo chữ người dùng gõ; giao diện báo rõ khi
 * bật dữ liệu mẫu). Trang đã ở bản sau khi sửa thì trả lại nguyên trang.
 */
function patchPageLines(documentId: string, pageNumber: number, expectedVersion: number): Promise<PageView> {
  const session = liveSession(documentId);
  const page = session?.pages.get(pageNumber);
  if (session === null || page === undefined) return reject(404, "không tìm thấy tài liệu hoặc trang", REVIEW);
  const conflict = pageConflict(page, expectedVersion, false);
  if (conflict !== null) return reject(409, conflict, REVIEW_VERSIONED);
  const edited = EDITED_PAGES[pageNumber];
  const next = edited !== undefined && page.version < edited.version ? structuredClone(edited) : page;
  session.pages.set(pageNumber, next);
  return Promise.resolve(pageView(next));
}

/** M5: xác nhận một trang (`ok` hoặc `needs_review`); gọi lại trên trang đã xác nhận trả 200 không đổi gì. */
function confirmPage(documentId: string, pageNumber: number, expectedVersion: number): Promise<PageView> {
  const session = liveSession(documentId);
  const page = session?.pages.get(pageNumber);
  if (session === null || page === undefined) return reject(404, "không tìm thấy tài liệu hoặc trang", REVIEW);
  if (page.confirmed && page.status !== "failed") return Promise.resolve(pageView(page));
  const conflict = pageConflict(page, expectedVersion, true);
  if (conflict !== null) return reject(409, conflict, REVIEW_VERSIONED);
  if (!page.lines.some((line) => line.text.trim() !== "")) {
    return reject(409, "trang không có dòng nào có văn bản", REVIEW_VERSIONED);
  }
  const next = confirmedPage(page);
  session.pages.set(pageNumber, next);
  return Promise.resolve(pageView(next));
}

/** M5: xác nhận mọi trang `ok` chưa xác nhận; còn trang đang đọc thì 409. */
function confirmDocument(documentId: string): Promise<DocumentView> {
  const session = liveSession(documentId);
  if (session === null) return reject(404, "không tìm thấy tài liệu", REVIEW);
  const pages = sortedPages(session);
  if (pages.some((page) => page.status === "pending")) return reject(409, "tài liệu còn trang đang đọc", REVIEW);
  for (const page of pages) {
    if (page.status === "ok" && !page.confirmed) session.pages.set(page.page_number, confirmedPage(page));
  }
  return Promise.resolve(documentView(session));
}

function deleteDocument(documentId: string): Promise<void> {
  const session = liveSession(documentId);
  if (session === null) return reject(404, "không tìm thấy tài liệu", { context: "document_delete" });
  session.deleted = true;
  return Promise.resolve();
}

/** Handler của tài liệu mẫu, chỉ dùng cho mã tài liệu mẫu (xem `createApi` trong `lib/api.ts`). */
export const mockDocumentApi = {
  listDocuments,
  getDocument,
  getDocumentPage,
  patchPageLines,
  confirmPage,
  confirmDocument,
  deleteDocument,
};
