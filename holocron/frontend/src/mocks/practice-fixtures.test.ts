/**
 * Fixture của màn luyện tập phải đúng chính contract (kế hoạch UI mục 3.3): I2 cho câu MCQ4 lý thuyết, I3 cho mọi
 * trích dẫn, luật tách ý của câu tự luận và validator của `EssayGradeResult`. Fixture sai thì giao diện học một dạng
 * dữ liệu backend sẽ không bao giờ trả.
 */

import { describe, expect, it } from "vitest";

import { parseBlueprint, parsePracticeBatch } from "@/lib/practice-data";
import type { CitationPending, OptionKeyPending, TwinQuestionPending } from "@/types/contract-pending";

import { BLUEPRINT_FIXTURE, MOCK_BLUEPRINT_ID } from "./fixtures/blueprints";
import { CHUNK_FIXTURES } from "./fixtures/chunks";
import { ESSAY_GRADE_FIXTURES, ESSAY_SOURCE_QUOTES } from "./fixtures/essay";
import {
  EMPTY_TWIN_BATCH_FIXTURE,
  THEORY_MISCONCEPTIONS,
  THEORY_TICKET_REASON,
  TWIN_BATCH_FIXTURE,
  UNAPPROVED_SOURCE_BODY,
} from "./fixtures/twins";
import { mockApi, mockPracticeBatch } from "./index";
import { isMockBlueprint, practiceScenarioFromSearch } from "./store";

const OPTION_KEYS: readonly OptionKeyPending[] = ["A", "B", "C", "D"];

/** Quy tắc so khớp của I3 (spec §0): NFC rồi gộp khoảng trắng, sau đó mới kiểm chuỗi con. */
function normalized(text: string): string {
  return text.normalize("NFC").replace(/\s+/g, " ").trim();
}

function isVerbatimIn(quote: string, source: string): boolean {
  return normalized(source).includes(normalized(quote));
}

function expectCitationInChunk(citation: CitationPending): void {
  const chunk = CHUNK_FIXTURES.find((item) => item.chunk_id === citation.chunk_id);
  expect(chunk, `chunk ${citation.chunk_id} phải có trong chunk mẫu`).toBeDefined();
  if (chunk === undefined) return;
  expect(isVerbatimIn(citation.verbatim_quote, chunk.raw_text)).toBe(true);
  expect(citation.page_number).toBe(chunk.page_number);
  expect(citation.doc_id).toBe(chunk.doc_id);
  expect(citation.verbatim_quote.length).toBeGreaterThanOrEqual(8);
  expect(citation.verbatim_quote.length).toBeLessThanOrEqual(600);
}

const mcqTwins = TWIN_BATCH_FIXTURE.twins.filter((twin) => twin.format === "MCQ4");
const essayTwins = TWIN_BATCH_FIXTURE.twins.filter((twin) => twin.format === "ESSAY");

describe("chunk mẫu (§5.8, I3)", () => {
  it("verbatim_quote của mỗi chunk là chuỗi con của raw_text; mã môn và chương đúng mẫu", () => {
    for (const chunk of CHUNK_FIXTURES) {
      expect(isVerbatimIn(chunk.verbatim_quote, chunk.raw_text)).toBe(true);
      expect(chunk.subject_code).toMatch(/^[A-Z]{2,6}[0-9]{2,4}$/);
      expect(chunk.chapter_code).toMatch(/^ch[0-9]{1,2}(\.[0-9]{1,2})?$/);
    }
  });
});

describe("TwinBatch mẫu (§5.11, §6.7)", () => {
  it("có câu MCQ4 và câu tự luận, đều là câu lý thuyết không có template hay topic", () => {
    expect(mcqTwins.length).toBeGreaterThanOrEqual(2);
    expect(essayTwins.length).toBeGreaterThanOrEqual(2);
    for (const twin of TWIN_BATCH_FIXTURE.twins) {
      expect(twin.question_kind).toBe("theory");
      expect(twin.template_id).toBeNull();
      expect(twin.topic_key).toBeNull();
      expect(twin.trace.kind).toBe("theory");
      expect(twin.artifacts).toEqual([]);
      expect(twin.blueprint_id).toBe(MOCK_BLUEPRINT_ID);
    }
    expect(TWIN_BATCH_FIXTURE.batch_id).toMatch(/^tb_[0-9a-f]{16}$/);
    if (TWIN_BATCH_FIXTURE.twins[0]?.trace.kind === "theory") {
      expect(TWIN_BATCH_FIXTURE.twins[0].trace.pool_sha256).toMatch(/^[0-9a-f]{64}$/);
    }
  });

  it("I3: trích dẫn của câu và của từng phương án nhiễu là chuỗi con của chunk nguồn", () => {
    for (const twin of TWIN_BATCH_FIXTURE.twins) {
      expectCitationInChunk(twin.citation);
      for (const ref of Object.values(twin.distractor_to_misconception_map)) {
        if (ref.citation !== null) expectCitationInChunk(ref.citation);
      }
    }
  });

  it("I2: mỗi phương án sai của MCQ4 mang đúng một mã thuộc THEORY_MISCONCEPTIONS; map không chứa đáp án đúng", () => {
    for (const twin of mcqTwins) {
      const correct = twin.correct_key;
      expect(correct).not.toBeNull();
      const keys = Object.keys(twin.distractor_to_misconception_map).sort();
      expect(keys).toEqual(OPTION_KEYS.filter((key) => key !== correct));
      for (const ref of Object.values(twin.distractor_to_misconception_map)) {
        expect(THEORY_MISCONCEPTIONS).toContain(ref.misconception_code);
        expect(ref.citation).not.toBeNull();
      }
    }
  });

  it("MCQ4: bốn phương án khác nhau sau chuẩn hoá; tên khái niệm trong phương án định nghĩa được che", () => {
    for (const twin of mcqTwins) {
      const options = twin.options;
      expect(options).not.toBeNull();
      if (options === null) continue;
      expect(new Set(OPTION_KEYS.map((key) => normalized(options[key]).toLocaleLowerCase("vi"))).size).toBe(4);
    }
    const definitionChoice = mcqTwins.find((twin) => twin.stem.startsWith("Phát biểu nào là định nghĩa"));
    for (const text of Object.values(definitionChoice?.options ?? {})) {
      expect(text.startsWith("____")).toBe(true);
    }
  });

  it("câu tự luận: không có phương án; các ý là chuỗi con liên tục của định nghĩa, chỉ số liên tục từ 0", () => {
    for (const twin of essayTwins) {
      expect(twin.options).toBeNull();
      expect(twin.correct_key).toBeNull();
      expect(twin.distractor_to_misconception_map).toEqual({});
      expect(twin.essay).not.toBeNull();
      const essay = twin.essay;
      if (essay === null) continue;
      expect(twin.stem).toBe(`Trình bày khái niệm «${essay.term}».`);
      expect(twin.solution_explanation_vi).toBe(twin.citation.verbatim_quote);
      expect(essay.points.map((point) => point.point_index)).toEqual(essay.points.map((_, index) => index));
      for (const point of essay.points) {
        expect(isVerbatimIn(point.text, twin.citation.verbatim_quote)).toBe(true);
        expect(point.syllable_count).toBeGreaterThanOrEqual(4);
      }
      expect(ESSAY_SOURCE_QUOTES[twin.twin_id]).toBe(twin.citation.verbatim_quote);
    }
  });

  it("ReviewTicket của slot lý thuyết có topic_key null và lý do của bộ sinh", () => {
    for (const batch of [TWIN_BATCH_FIXTURE, EMPTY_TWIN_BATCH_FIXTURE]) {
      for (const ticket of batch.review_tickets) {
        expect(ticket.topic_key).toBeNull();
        expect(ticket.reason).toBe(THEORY_TICKET_REASON);
      }
    }
    expect(EMPTY_TWIN_BATCH_FIXTURE.twins).toEqual([]);
  });

  it("đi qua bước kiểm dạng ở ranh giới mà không câu nào bị bỏ", () => {
    for (const batch of [TWIN_BATCH_FIXTURE, EMPTY_TWIN_BATCH_FIXTURE]) {
      const parsed = parsePracticeBatch(structuredClone(batch));
      expect(parsed?.skipped).toEqual({ unsupported: 0, malformed: 0 });
      expect(parsed?.questions).toHaveLength(batch.twins.length);
    }
  });
});

describe("ExamBlueprint mẫu (§5.9)", () => {
  it("chỉ có slot lý thuyết; slot của batch mẫu đều có trong blueprint", () => {
    expect(BLUEPRINT_FIXTURE.total_questions).toBe(BLUEPRINT_FIXTURE.slots.length);
    for (const slot of BLUEPRINT_FIXTURE.slots) {
      expect(slot.question_kind).toBe("theory");
      expect(slot.topic_key).toBeNull();
      expect(slot.self_solve).toBeNull();
      expect(slot.distractor_archetypes).toEqual([]);
      expect(["MCQ4", "ESSAY"]).toContain(slot.format);
    }
    const slotFormats = new Map(BLUEPRINT_FIXTURE.slots.map((slot) => [slot.slot_index, slot.format]));
    const twins: TwinQuestionPending[] = TWIN_BATCH_FIXTURE.twins;
    for (const twin of twins) {
      expect(slotFormats.get(twin.slot_index)).toBe(twin.format);
    }
    expect(parseBlueprint(BLUEPRINT_FIXTURE)).not.toBeNull();
  });
});

describe("EssayGradeResult mẫu (§5.14)", () => {
  it("validator của contract: matched_count + len(missing) = total_points, chỉ số ý phủ đúng 0..total-1", () => {
    for (const result of Object.values(ESSAY_GRADE_FIXTURES)) {
      expect(result.matched_count).toBe(result.matched_points.length);
      expect(result.matched_count + result.missing_points.length).toBe(result.total_points);
      const indices = [...result.matched_points, ...result.missing_points].map((p) => p.point_index).sort();
      expect(indices).toEqual(Array.from({ length: result.total_points }, (_, index) => index));
      expect(result.answer_sha256).toMatch(/^[0-9a-f]{64}$/);
      const twin = TWIN_BATCH_FIXTURE.twins.find((item) => item.twin_id === result.twin_id);
      expect(twin?.format).toBe("ESSAY");
      expect(result.citation).toEqual(twin?.citation);
      for (const point of [...result.matched_points, ...result.missing_points]) {
        expect(twin?.essay?.points[point.point_index]?.text).toBe(point.text);
      }
    }
  });

  it("có một kết quả khớp một phần và một kết quả khớp đủ", () => {
    const pairs = Object.values(ESSAY_GRADE_FIXTURES).map((r) => [r.matched_count, r.total_points]);
    expect(pairs).toContainEqual([1, 2]);
    expect(pairs).toContainEqual([2, 2]);
  });
});

describe("handler giả của màn luyện tập", () => {
  it("tạo bộ đề ghi nhớ mã blueprint mẫu để sinh câu bằng dữ liệu mẫu", async () => {
    const blueprint = await mockApi.createBlueprint?.({ subject_code: "MAU101", theory_slots: [] });
    expect(blueprint?.blueprintId).toBe(MOCK_BLUEPRINT_ID);
    expect(isMockBlueprint(MOCK_BLUEPRINT_ID)).toBe(true);
    expect(isMockBlueprint("bp_that_tu_backend")).toBe(false);
  });

  it("chấm tự luận chọn kết quả theo twin_id, không theo câu trả lời; twin lạ là không tìm thấy", async () => {
    const [twinId] = Object.keys(ESSAY_GRADE_FIXTURES);
    if (twinId === undefined) throw new Error("thiếu kết quả mẫu");
    const first = await mockApi.gradeEssay?.(twinId, "câu trả lời bất kỳ");
    const second = await mockApi.gradeEssay?.(twinId, "một câu trả lời khác hẳn");
    expect(first).toEqual(second);
    await expect(mockApi.gradeEssay?.("twin_khong_co", "x")).rejects.toMatchObject({ info: { kind: "not_found" } });
  });

  it("kịch bản luyện tập chọn theo query; mặc định là batch đủ dạng câu", async () => {
    expect(practiceScenarioFromSearch("?mock_practice=empty")).toBe("empty");
    expect(practiceScenarioFromSearch("?mock_practice=unapproved")).toBe("unapproved");
    expect(practiceScenarioFromSearch("?mock_practice=la")).toBe("normal");
    const batch = await mockPracticeBatch();
    expect(batch.questions).toHaveLength(TWIN_BATCH_FIXTURE.twins.length);
    expect(UNAPPROVED_SOURCE_BODY.detail.length).toBeGreaterThan(0);
  });

  it("chunk mẫu đi qua kiểm dạng nguyên vẹn", async () => {
    const page = await mockApi.listVaultChunks?.({ subjectCode: "MAU101", chapterCode: "ch1", offset: 0, limit: 200 });
    expect(page?.chunks.map((c) => c.rawText)).toEqual(CHUNK_FIXTURES.map((c) => c.raw_text));
  });
});
