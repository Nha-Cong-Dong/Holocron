/**
 * Trạng thái của handler giả, chỉ sống trong phiên chạy (kế hoạch UI mục 3.3). Gỡ cùng thư mục `src/mocks/`.
 *
 * Kịch bản bù `RuntimeInfo` chọn bằng query `?mock_runtime=offline|llm|no_tesseract` trên URL đang mở, để xem được
 * cả ba trạng thái khả năng mà không phải build lại. Không có hoặc sai giá trị thì dùng kịch bản dự phòng do nơi gọi
 * truyền vào (khớp cờ `llm_configured` backend thật trả), mặc định `offline`.
 */

import type { Subject } from "@/types/contract-pending";

import { MOCK_IMPORT_DOC_ID, type MockDocumentStep } from "./fixtures/documents";
import { RUNTIME_SCENARIOS, type RuntimeScenario } from "./fixtures/runtime";

export const MOCK_RUNTIME_QUERY = "mock_runtime";

export const DEFAULT_RUNTIME_SCENARIO: RuntimeScenario = "offline";

export function scenarioFromSearch(
  search: string,
  fallback: RuntimeScenario = DEFAULT_RUNTIME_SCENARIO,
): RuntimeScenario {
  const value = new URLSearchParams(search).get(MOCK_RUNTIME_QUERY);
  return RUNTIME_SCENARIOS.find((scenario) => scenario === value) ?? fallback;
}

/** Kịch bản hiện tại; chỉ đọc `window` khi handler giả chạy, không đọc lúc render. */
export function currentRuntimeScenario(fallback: RuntimeScenario = DEFAULT_RUNTIME_SCENARIO): RuntimeScenario {
  if (typeof window === "undefined") return fallback;
  return scenarioFromSearch(window.location.search, fallback);
}

/**
 * Kịch bản của màn luyện tập, chọn bằng query `?mock_practice=…`: `normal` (mặc định) trả batch mẫu đủ dạng câu;
 * `empty` trả batch không có câu nào vì chương chưa có cặp khái niệm đã xác nhận; `unapproved` trả 409 như khi bộ đề
 * dựa trên đề thi chưa được duyệt.
 */
export const PRACTICE_SCENARIOS = ["normal", "empty", "unapproved"] as const;

export type PracticeScenario = (typeof PRACTICE_SCENARIOS)[number];

export const MOCK_PRACTICE_QUERY = "mock_practice";

export function practiceScenarioFromSearch(search: string): PracticeScenario {
  const value = new URLSearchParams(search).get(MOCK_PRACTICE_QUERY);
  return PRACTICE_SCENARIOS.find((scenario) => scenario === value) ?? "normal";
}

export function currentPracticeScenario(): PracticeScenario {
  if (typeof window === "undefined") return "normal";
  return practiceScenarioFromSearch(window.location.search);
}

/**
 * Mã các blueprint do handler giả tạo trong phiên: chỉ những mã này được sinh câu bằng dữ liệu mẫu, và chỉ câu của
 * chúng tìm trích dẫn trong chunk mẫu.
 */
const mockBlueprintIds = new Set<string>();

export function rememberMockBlueprint(blueprintId: string): void {
  mockBlueprintIds.add(blueprintId);
}

export function isMockBlueprint(blueprintId: string): boolean {
  return mockBlueprintIds.has(blueprintId);
}

// ------------------------------------------------------------------------- môn học và tài liệu mẫu

/** Môn do handler giả `POST /v1/subjects` tạo trong phiên (M3). */
export const createdSubjects: Subject[] = [];

/** Mã tài liệu mẫu (M4, M5): chỉ những mã này dùng fixture; mọi tài liệu khác gọi backend thật. */
const MOCK_DOCUMENT_IDS: ReadonlySet<string> = new Set([MOCK_IMPORT_DOC_ID]);

export function isMockDocument(documentId: string): boolean {
  return MOCK_DOCUMENT_IDS.has(documentId);
}

/**
 * Trạng thái phiên của tài liệu mẫu: bước hiện tại của kịch bản cố định (`fixtures/documents.ts`) và cờ đã xoá. Bước
 * chỉ tiến theo đúng thứ tự kịch bản; handler không dựng bản ghi mới mà trả bản dựng sẵn của bước.
 */
export interface MockDocumentSession {
  deleted: boolean;
  step: MockDocumentStep;
}

const documentSessions = new Map<string, MockDocumentSession>();

/** Phiên của tài liệu mẫu; lần đầu ở bước đầu của kịch bản. */
export function mockDocumentSession(documentId: string): MockDocumentSession {
  const existing = documentSessions.get(documentId);
  if (existing !== undefined) return existing;
  const session: MockDocumentSession = { deleted: false, step: "initial" };
  documentSessions.set(documentId, session);
  return session;
}

/** Đưa mọi tài liệu mẫu về bước đầu (để test không phụ thuộc thứ tự chạy). */
export function resetMockDocumentSessions(): void {
  documentSessions.clear();
}
