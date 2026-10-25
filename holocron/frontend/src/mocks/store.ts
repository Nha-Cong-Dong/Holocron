/**
 * Trạng thái của handler giả, chỉ sống trong phiên chạy (kế hoạch UI mục 3.3). Gỡ cùng thư mục `src/mocks/`.
 *
 * Kịch bản `RuntimeInfo` chọn bằng query `?mock_runtime=offline|llm|no_tesseract` trên URL đang mở, để xem được
 * cả ba trạng thái khả năng mà không phải build lại; không có hoặc sai giá trị thì dùng `offline`.
 */

import { RUNTIME_SCENARIOS, type RuntimeScenario } from "./fixtures/runtime";

export const MOCK_RUNTIME_QUERY = "mock_runtime";

export const DEFAULT_RUNTIME_SCENARIO: RuntimeScenario = "offline";

export function scenarioFromSearch(search: string): RuntimeScenario {
  const value = new URLSearchParams(search).get(MOCK_RUNTIME_QUERY);
  return RUNTIME_SCENARIOS.find((scenario) => scenario === value) ?? DEFAULT_RUNTIME_SCENARIO;
}

/** Kịch bản hiện tại; chỉ đọc `window` khi handler giả chạy, không đọc lúc render. */
export function currentRuntimeScenario(): RuntimeScenario {
  if (typeof window === "undefined") return DEFAULT_RUNTIME_SCENARIO;
  return scenarioFromSearch(window.location.search);
}
