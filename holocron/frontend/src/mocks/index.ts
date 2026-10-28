/**
 * Dữ liệu mẫu cho phần backend CHƯA có (kế hoạch UI mục 3.1–3.3). `lib/api.ts` chỉ dùng module này khi bật
 * `NEXT_PUBLIC_HOLOCRON_MOCKS`. Handler chỉ trả bản sao fixture dựng sẵn, không tính số nào; dữ liệu trả về vẫn đi
 * qua đúng bước kiểm dạng ở ranh giới như dữ liệu thật.
 *
 * Gỡ: khi backend có một endpoint hay field, xoá phần tương ứng ở đây cùng fixture của nó; khi hết, xoá thư mục này,
 * dòng dùng nó trong `lib/api.ts` và cờ trong `lib/config.ts`.
 */

import type { HolocronApi } from "@/lib/api";
import { ApiError, classifyHttpError, invalidResponseError } from "@/lib/errors";
import { type EssayGradeView, parseEssayGradeResult } from "@/lib/practice-data";

import { ESSAY_GRADE_FIXTURES } from "./fixtures/essay";
import { RUNTIME_FIXTURES } from "./fixtures/runtime";
import { currentRuntimeScenario } from "./store";

export { mockDocumentApi } from "./documents";

/** Kết quả kiểm dạng của fixture; fixture sai dạng là lỗi của chính fixture, báo như phản hồi không hợp lệ. */
function checked<T>(value: T | null): T {
  if (value === null) throw new ApiError(200, invalidResponseError());
  return value;
}

/**
 * M9: `POST /v1/twins/{twin_id}/essay-grade` (§6.9) chưa có. Chọn kết quả theo `twin_id`, không theo câu trả lời, nên
 * khi bật mock khối kết quả tự luận ghi rõ đây là kết quả cố định dựng sẵn, không phải chấm thật.
 */
function gradeEssay(twinId: string): Promise<EssayGradeView> {
  const fixture = ESSAY_GRADE_FIXTURES[twinId];
  if (fixture === undefined) {
    return Promise.reject(
      new ApiError(404, classifyHttpError(404, { detail: "không tìm thấy twin" }, { context: "essay_grade" })),
    );
  }
  return Promise.resolve(checked(parseEssayGradeResult(structuredClone(fixture))));
}

/**
 * Endpoint backend chưa có, thay trọn bằng fixture. Môn học (`/v1/subjects`), tạo bộ đề (`POST /v1/blueprints`),
 * sinh câu và chunk của Vault đã có ở backend nên luôn gọi backend thật.
 */
export const mockApi: Partial<HolocronApi> = {
  gradeEssay,
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
