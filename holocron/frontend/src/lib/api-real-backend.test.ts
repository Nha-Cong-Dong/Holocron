/**
 * Môn học, tạo bộ đề và sinh câu luôn gọi backend thật, kể cả khi bật dữ liệu mẫu; lỗi 409/422/404 của luồng tạo đề
 * và sinh câu có câu riêng kèm mã lỗi để báo. `fetch` giả, không gọi mạng.
 *
 * Batch dùng ở đây là fixture `src/mocks/fixtures/theory-batch.real.json` (nguồn và lệnh sinh: đầu file
 * `lib/practice-data-real.test.ts`).
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import REAL_BATCH from "@/mocks/fixtures/theory-batch.real.json";
import type { ExamBlueprint, Subject } from "@/types/holocron";

import { ApiError, createApi } from "./api";
import { DEFAULT_API_BASE } from "./config";
import { appendSentence, THEORY_TICKET_HINT_VI } from "./practice";

interface FetchCall {
  url: string;
  init: RequestInit | undefined;
}

function stubFetch(status: number, body: unknown): FetchCall[] {
  const calls: FetchCall[] = [];
  vi.stubGlobal("fetch", (input: string, init?: RequestInit) => {
    calls.push({ url: input, init });
    return Promise.resolve(
      new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }),
    );
  });
  return calls;
}

async function captureError(promise: Promise<unknown>): Promise<ApiError> {
  try {
    await promise;
  } catch (error) {
    if (error instanceof ApiError) return error;
    throw error;
  }
  throw new Error("lời gọi không ném lỗi");
}

afterEach(() => {
  vi.unstubAllGlobals();
});

const SUBJECT: Subject = {
  subject_code: "SSH1110",
  display_name: "Triết học",
  exam_format: "mixed",
  version: 1,
  created_at: "2026-10-28T08:00:00Z",
  updated_at: "2026-10-28T08:00:00Z",
};

/** `ExamBlueprint` như `POST /v1/blueprints` của nhánh trả: chỉ slot lý thuyết, kèm bốn computed field. */
const BLUEPRINT: ExamBlueprint = {
  blueprint_id: REAL_BATCH.blueprint_id,
  source_exam_doc_id: null,
  subject_code: "SSH1110",
  total_questions: 2,
  duration_minutes: null,
  slots: [1, 2].map((slotIndex) => ({
    slot_index: slotIndex,
    is_computational: false,
    question_kind: "theory",
    chapter_code: `ch${slotIndex}`,
    source_chunk_id: null,
    format: "MCQ4",
    topic_key: null,
    cognitive_level: "remember",
    non_cloneable: false,
    mapping_confidence: 1,
    review_status: "NEEDS_REVIEW",
  })),
  created_at: "2026-10-28T08:00:00Z",
  chapter_distribution: { ch1: 1, ch2: 1 },
  format_distribution: { MCQ4: 2 },
  cloneable_ratio: 1,
  mean_mapping_confidence: 1,
};

describe("bật dữ liệu mẫu vẫn gọi backend thật cho môn học, tạo bộ đề và sinh câu", () => {
  for (const useMocks of [false, true]) {
    it(`listSubjects và createSubject gọi /v1/subjects (mock ${useMocks ? "bật" : "tắt"})`, async () => {
      const api = createApi(useMocks);
      const list = stubFetch(200, [SUBJECT]);
      expect(await api.listSubjects()).toEqual([SUBJECT]);
      expect(list[0]?.url).toBe(`${DEFAULT_API_BASE}/v1/subjects`);

      const created = stubFetch(201, SUBJECT);
      await api.createSubject({ subject_code: "SSH1110", exam_format: "mixed" });
      expect(created[0]?.url).toBe(`${DEFAULT_API_BASE}/v1/subjects`);
      expect(created[0]?.init?.method).toBe("POST");
    });

    it(`createBlueprint gửi body và đọc ExamBlueprint thật (mock ${useMocks ? "bật" : "tắt"})`, async () => {
      const api = createApi(useMocks);
      const calls = stubFetch(201, BLUEPRINT);
      const payload = {
        subject_code: "SSH1110",
        item_ids: [],
        theory_slots: [
          { chapter_code: "ch1", format: "MCQ4" as const },
          { chapter_code: "ch2", format: "MCQ4" as const },
        ],
      };
      const blueprint = await api.createBlueprint(payload);
      expect(calls[0]?.url).toBe(`${DEFAULT_API_BASE}/v1/blueprints`);
      expect(calls[0]?.init?.body).toBe(JSON.stringify(payload));
      expect(blueprint).toEqual({
        blueprintId: BLUEPRINT.blueprint_id,
        subjectCode: "SSH1110",
        slots: [
          { slotIndex: 1, chapterCode: "ch1", format: "MCQ4" },
          { slotIndex: 2, chapterCode: "ch2", format: "MCQ4" },
        ],
      });
    });

    it(`generatePracticeBatch đọc TwinBatch thật của backend (mock ${useMocks ? "bật" : "tắt"})`, async () => {
      const calls = stubFetch(201, REAL_BATCH);
      const batch = await createApi(useMocks).generatePracticeBatch(REAL_BATCH.blueprint_id, { count: 1 }, "k");
      expect(calls).toHaveLength(1);
      expect(batch.questions.map((question) => question.twinId)).toEqual(REAL_BATCH.twins.map((t) => t.twin_id));
      expect(batch.tickets.map((ticket) => ticket.theory)).toEqual([true]);
    });

    it(`listVaultChunks luôn gọi backend (mock ${useMocks ? "bật" : "tắt"})`, async () => {
      const calls = stubFetch(200, { total: 0, chunks: [] });
      await createApi(useMocks).listVaultChunks({ subjectCode: "SSH1110", chapterCode: "ch1", offset: 0, limit: 50 });
      expect(calls[0]?.url).toBe(
        `${DEFAULT_API_BASE}/v1/vault/chunks?subject_code=SSH1110&offset=0&limit=50&chapter_code=ch1`,
      );
    });
  }
});

describe("lỗi của luồng tạo đề và sinh câu", () => {
  it("409 CapabilityError khi có slot ESSAY: lỗi khả năng, không có nút gửi lại, kèm mã lỗi để báo", async () => {
    stubFetch(409, {
      detail: {
        code: "CAPABILITY_UNAVAILABLE",
        capability: "essay_grading_offline",
        reason: "not_implemented",
        message: "Khả năng essay_grading_offline không khả dụng.",
      },
    });
    const error = await captureError(
      createApi(false).createBlueprint({
        subject_code: "SSH1110",
        theory_slots: [{ chapter_code: "ch1", format: "ESSAY" }],
      }),
    );
    expect(error.info.kind).toBe("capability");
    expect(error.info.action).toBe("none");
    expect(error.info.capability).toEqual({ name: "essay_grading_offline", reason: "not_implemented" });
    expect(error.info.diagnostic).toBe(
      "HTTP 409 · CapabilityError · essay_grading_offline · not_implemented · blueprint_create",
    );
  });

  it("422 khi tạo đề: câu riêng của luồng tạo đề, mã lỗi giữ field lỗi", async () => {
    stubFetch(422, {
      detail: [{ loc: ["body", "theory_slots", 0, "chapter_code"], msg: "x", type: "string_pattern_mismatch" }],
    });
    const error = await captureError(createApi(false).createBlueprint({ subject_code: "SSH1110", theory_slots: [] }));
    expect(error.info.kind).toBe("validation");
    expect(error.info.message).toContain("mã chương");
    expect(error.info.diagnostic).toContain("body.theory_slots.0.chapter_code:string_pattern_mismatch");
  });

  it("404 blueprint khi sinh câu: câu riêng, mã lỗi giữ detail của backend", async () => {
    stubFetch(404, { detail: "không tìm thấy blueprint" });
    const error = await captureError(createApi(false).generatePracticeBatch("bp_khong_co", { count: 1 }, null));
    expect(error.info.kind).toBe("not_found");
    expect(error.info.message).toContain("tạo lại bộ đề");
    expect(error.info.diagnostic).toBe("HTTP 404 · không tìm thấy blueprint · twin_generation");
  });
});

describe("lý do ticket của slot lý thuyết", () => {
  it("nối gợi ý cố định sau lý do backend ghi, không ra hai dấu chấm và giữ nguyên văn lý do", () => {
    expect(appendSentence("không đủ căn cứ trong Vault", THEORY_TICKET_HINT_VI)).toBe(
      `không đủ căn cứ trong Vault. ${THEORY_TICKET_HINT_VI}`,
    );
    expect(appendSentence("Chương có ít hơn 4 khái niệm.", THEORY_TICKET_HINT_VI)).toBe(
      `Chương có ít hơn 4 khái niệm. ${THEORY_TICKET_HINT_VI}`,
    );
    expect(appendSentence("Thiếu dữ liệu…  ", THEORY_TICKET_HINT_VI)).toBe(`Thiếu dữ liệu… ${THEORY_TICKET_HINT_VI}`);
    expect(appendSentence("", THEORY_TICKET_HINT_VI)).toBe(THEORY_TICKET_HINT_VI);
  });

  it("gợi ý cố định không ghi cứng ngưỡng số khái niệm và không hướng sang xác nhận nhãn chunk", () => {
    expect(THEORY_TICKET_HINT_VI).not.toMatch(/\d|bốn|ít nhất|nhãn|chunk/u);
  });
});
