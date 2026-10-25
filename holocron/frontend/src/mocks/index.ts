/**
 * Handler giả cho endpoint backend CHƯA có (kế hoạch UI mục 3.1–3.3). `lib/api.ts` gộp object này vào `api` chỉ khi
 * bật `NEXT_PUBLIC_HOLOCRON_MOCKS`. Handler chỉ trả bản sao fixture dựng sẵn, không tính số nào.
 *
 * Gỡ: khi backend có một endpoint, xoá phần tử tương ứng ở đây cùng fixture của nó; khi hết phần tử, xoá thư mục này,
 * dòng gộp trong `lib/api.ts` và cờ trong `lib/config.ts`.
 */

import type { HolocronApi } from "@/lib/api";

import { RUNTIME_FIXTURES } from "./fixtures/runtime";
import { SUBJECT_FIXTURES } from "./fixtures/subjects";
import { currentRuntimeScenario } from "./store";

export const mockApi: Partial<HolocronApi> = {
  // M1: `capabilities` và `mock_extractor` (§5.12) chưa có trong `/v1/runtime` của backend.
  getRuntime: () => Promise.resolve(structuredClone(RUNTIME_FIXTURES[currentRuntimeScenario()])),
  // M3: `GET /v1/subjects` (§6.8) chưa có.
  listSubjects: () => Promise.resolve(structuredClone(SUBJECT_FIXTURES)),
};
