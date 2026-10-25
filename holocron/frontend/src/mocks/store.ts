/**
 * Trạng thái của handler giả, chỉ sống trong phiên chạy (kế hoạch UI mục 3.3). Gỡ cùng thư mục `src/mocks/`.
 *
 * Kịch bản bù `RuntimeInfo` chọn bằng query `?mock_runtime=offline|llm|no_tesseract` trên URL đang mở, để xem được
 * cả ba trạng thái khả năng mà không phải build lại. Không có hoặc sai giá trị thì dùng kịch bản dự phòng do nơi gọi
 * truyền vào (khớp cờ `llm_configured` backend thật trả), mặc định `offline`.
 */

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
