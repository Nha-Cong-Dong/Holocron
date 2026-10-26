/**
 * Handler giả cho tài liệu mẫu (M4, M5; spec §6.5). `lib/api.ts` chỉ gọi các hàm này cho mã tài liệu mẫu
 * (`isMockDocument`); mọi tài liệu khác gọi backend thật.
 *
 * Handler không tính `status`, `review_reasons` hay số đếm nào: nó chỉ đi theo MỘT kịch bản tuyến tính cố định
 * (`MOCK_DOCUMENT_SCENARIO`: xem trang → sửa dòng trang 2 → xác nhận theo lô ⇒ `confirmed`) và trả bản dựng sẵn của
 * bước hiện tại. Thao tác ngoài kịch bản trả bản hiện tại, không đổi gì. Ngoài ra chỉ kiểm các điều kiện lỗi 404/409
 * của contract (tài liệu đã xoá, trang không có, trang đang đọc, đọc lỗi, đã xác nhận, lệch `version`).
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

import { MOCK_DOCUMENT_SCENARIO, MOCK_IMPORT_SUBJECT, type MockDocumentSnapshot } from "./fixtures/documents";
import { type MockDocumentSession, mockDocumentSession } from "./store";

function checked<T>(value: T | null): T {
  if (value === null) throw new ApiError(200, invalidResponseError());
  return value;
}

function reject(status: number, detail: string, options: ClassifyOptions): Promise<never> {
  return Promise.reject(new ApiError(status, classifyHttpError(status, { detail }, options)));
}

const REVIEW: ClassifyOptions = { context: "document_review" };
const EDIT_VERSIONED: ClassifyOptions = { context: "document_review", versioned: true };
const CONFIRM_PAGE: ClassifyOptions = { context: "page_confirm", versioned: true };
const CONFIRM_DOCUMENT: ClassifyOptions = { context: "document_confirm" };

function liveSession(documentId: string): MockDocumentSession | null {
  const session = mockDocumentSession(documentId);
  return session.deleted ? null : session;
}

function current(session: MockDocumentSession): MockDocumentSnapshot {
  return MOCK_DOCUMENT_SCENARIO[session.step];
}

function pageOf(session: MockDocumentSession, pageNumber: number): PageExtractionPending | undefined {
  return current(session).pages.find((page) => page.page_number === pageNumber);
}

function documentView(session: MockDocumentSession): DocumentView {
  return checked(parseDocumentImport(structuredClone(current(session).document)));
}

function pageView(page: PageExtractionPending): PageView {
  return checked(parsePageExtraction(structuredClone(page)));
}

/** Tài liệu mẫu của môn (chỉ môn của fixture, khi chưa bị xoá trong phiên): tóm tắt dựng sẵn của bước hiện tại. */
function listDocuments(subjectCode: string): DocumentSummaryView[] {
  if (subjectCode !== MOCK_IMPORT_SUBJECT) return [];
  const session = mockDocumentSession(MOCK_DOCUMENT_SCENARIO.initial.summary.meta.doc_id);
  if (session.deleted) return [];
  return checked(parseDocumentSummaries([structuredClone(current(session).summary)]));
}

function getDocument(documentId: string): Promise<DocumentView> {
  const session = liveSession(documentId);
  if (session === null) return reject(404, "không tìm thấy tài liệu", REVIEW);
  return Promise.resolve(documentView(session));
}

function getDocumentPage(documentId: string, pageNumber: number): Promise<PageView> {
  const session = liveSession(documentId);
  const page = session === null ? undefined : pageOf(session, pageNumber);
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
 * M5: `PATCH …/lines`. Bước "sửa dòng trang 2" của kịch bản chuyển sang bản dựng sẵn sau khi sửa (không theo chữ người
 * dùng gõ; màn Vault báo rõ khi bật dữ liệu mẫu). Sửa trang khác, hay sửa lại trang 2, trả trang hiện tại.
 */
function patchPageLines(documentId: string, pageNumber: number, expectedVersion: number): Promise<PageView> {
  const session = liveSession(documentId);
  const page = session === null ? undefined : pageOf(session, pageNumber);
  if (session === null || page === undefined) return reject(404, "không tìm thấy tài liệu hoặc trang", REVIEW);
  const conflict = pageConflict(page, expectedVersion, false);
  if (conflict !== null) return reject(409, conflict, EDIT_VERSIONED);
  if (session.step === "initial" && pageNumber === 2) session.step = "page2_edited";
  return Promise.resolve(pageView(pageOf(session, pageNumber) ?? page));
}

/** M5: xác nhận từng trang không thuộc kịch bản: sau khi kiểm lỗi của contract, trả trang hiện tại. */
function confirmPage(documentId: string, pageNumber: number, expectedVersion: number): Promise<PageView> {
  const session = liveSession(documentId);
  const page = session === null ? undefined : pageOf(session, pageNumber);
  if (session === null || page === undefined) return reject(404, "không tìm thấy tài liệu hoặc trang", REVIEW);
  if (page.confirmed && page.status !== "failed") return Promise.resolve(pageView(page));
  const conflict = pageConflict(page, expectedVersion, true);
  if (conflict !== null) return reject(409, conflict, CONFIRM_PAGE);
  if (!page.lines.some((line) => line.text.trim() !== "")) {
    return reject(409, "trang không có dòng nào có văn bản", CONFIRM_PAGE);
  }
  return Promise.resolve(pageView(page));
}

/** M5: bước "xác nhận theo lô" của kịch bản (sau khi đã sửa trang 2) chuyển sang bản `confirmed` dựng sẵn. */
function confirmDocument(documentId: string): Promise<DocumentView> {
  const session = liveSession(documentId);
  if (session === null) return reject(404, "không tìm thấy tài liệu", CONFIRM_DOCUMENT);
  if (session.step === "page2_edited") session.step = "confirmed";
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
