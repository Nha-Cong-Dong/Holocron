/**
 * Dữ liệu mẫu cho phần backend CHƯA có (kế hoạch UI mục 3.1–3.3). `lib/api.ts` chỉ dùng module này khi bật
 * `NEXT_PUBLIC_HOLOCRON_MOCKS`. Handler chỉ trả bản sao fixture dựng sẵn, không tính số nào.
 *
 * Gỡ: khi backend có một endpoint hay field, xoá phần tương ứng ở đây cùng fixture của nó; khi hết, xoá thư mục này,
 * dòng dùng nó trong `lib/api.ts` và cờ trong `lib/config.ts`.
 */

import type { HolocronApi } from "@/lib/api";

import { RUNTIME_FIXTURES } from "./fixtures/runtime";
import { SUBJECT_FIXTURES } from "./fixtures/subjects";
import { currentRuntimeScenario } from "./store";

/** Endpoint backend chưa có, thay trọn bằng fixture. */
export const mockApi: Partial<HolocronApi> = {
  // M3: `GET /v1/subjects` (§6.8) chưa có.
  listSubjects: () => Promise.resolve(structuredClone(SUBJECT_FIXTURES)),
};

/** Field của `GET /v1/runtime` (§5.12) mà backend chưa trả; chỉ những field này được bù từ fixture. */
export const RUNTIME_GAP_FIELDS = ["capabilities", "mock_extractor"] as const;

/**
 * M1: `/v1/runtime` đã có ở backend nhưng chưa trả `capabilities` và `mock_extractor`. Hàm này nhận thân JSON thật
 * (chưa kiểm dạng) và chỉ bù field backend CHƯA trả từ fixture của kịch bản hiện tại; field backend đã trả (kể cả
 * sai dạng) giữ nguyên để bước kiểm dạng xử lý. Thân không phải object thì trả nguyên văn.
 */
export function fillRuntimeGaps(raw: unknown): unknown {
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) return raw;
  const llmConfigured = "llm_configured" in raw && raw.llm_configured === true;
  const fixture = RUNTIME_FIXTURES[currentRuntimeScenario(llmConfigured ? "llm" : "offline")];
  const filled: Record<string, unknown> = { ...raw };
  for (const field of RUNTIME_GAP_FIELDS) {
    if (filled[field] === undefined) {
      filled[field] = structuredClone(fixture[field]);
    }
  }
  return filled;
}
