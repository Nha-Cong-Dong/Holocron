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
import {
  type ChunkPageView,
  type EssayGradeView,
  type PracticeBatch,
  type PracticeBlueprint,
  parseBlueprint,
  parseChunkPage,
  parseEssayGradeResult,
  parsePracticeBatch,
} from "@/lib/practice-data";
import { parseSubjectList } from "@/lib/subjects";

import { BLUEPRINT_FIXTURE } from "./fixtures/blueprints";
import { CHUNK_FIXTURES } from "./fixtures/chunks";
import { ESSAY_GRADE_FIXTURES } from "./fixtures/essay";
import { RUNTIME_FIXTURES } from "./fixtures/runtime";
import { SUBJECT_FIXTURES } from "./fixtures/subjects";
import { EMPTY_TWIN_BATCH_FIXTURE, TWIN_BATCH_FIXTURE, UNAPPROVED_SOURCE_BODY } from "./fixtures/twins";
import { currentPracticeScenario, currentRuntimeScenario, rememberMockBlueprint } from "./store";

/** Kết quả kiểm dạng của fixture; fixture sai dạng là lỗi của chính fixture, báo như phản hồi không hợp lệ. */
function checked<T>(value: T | null): T {
  if (value === null) throw new ApiError(200, invalidResponseError());
  return value;
}

/** M7: `POST /v1/blueprints` (§6.7) chưa có. Luôn trả blueprint mẫu, không dựng theo lựa chọn trên form. */
function createBlueprint(): Promise<PracticeBlueprint> {
  const blueprint = checked(parseBlueprint(structuredClone(BLUEPRINT_FIXTURE)));
  rememberMockBlueprint(blueprint.blueprintId);
  return Promise.resolve(blueprint);
}

/**
 * M8: câu lý thuyết chưa được nối vào `POST /v1/blueprints/{id}/twins`. Chỉ dùng cho blueprint do `createBlueprint`
 * giả tạo (xem `createApi` trong `lib/api.ts`); blueprint thật vẫn gọi backend.
 */
export function mockPracticeBatch(): Promise<PracticeBatch> {
  const scenario = currentPracticeScenario();
  if (scenario === "unapproved") {
    return Promise.reject(
      new ApiError(409, classifyHttpError(409, UNAPPROVED_SOURCE_BODY, { context: "twin_generation" })),
    );
  }
  const fixture = scenario === "empty" ? EMPTY_TWIN_BATCH_FIXTURE : TWIN_BATCH_FIXTURE;
  return Promise.resolve(checked(parsePracticeBatch(structuredClone(fixture))));
}

/**
 * M9: `POST /v1/twins/{twin_id}/essay-grade` (§6.9) chưa có. Chọn kết quả theo `twin_id`, không theo câu trả lời, nên
 * khi bật mock khối kết quả tự luận và form tạo đề ghi rõ đây là kết quả cố định dựng sẵn, không phải chấm thật.
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

/** `GET /v1/vault/chunks` (§6.6) chưa có ở backend của nhánh này. Luôn trả toàn bộ chunk mẫu trong một trang. */
function listVaultChunks(): Promise<ChunkPageView> {
  const page = { total: CHUNK_FIXTURES.length, chunks: structuredClone(CHUNK_FIXTURES) };
  return Promise.resolve(checked(parseChunkPage(page)));
}

/** Endpoint backend chưa có, thay trọn bằng fixture. */
export const mockApi: Partial<HolocronApi> = {
  // M3: `GET /v1/subjects` (§6.8) chưa có.
  listSubjects: () => Promise.resolve(checked(parseSubjectList(structuredClone(SUBJECT_FIXTURES)))),
  createBlueprint,
  gradeEssay,
  listVaultChunks,
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
