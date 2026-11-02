/**
 * Kiểm dạng `TwinBatch` THẬT của backend (quyết định 14(b): union `TwinQuestion | TheoryTwin` theo `question_kind`).
 *
 * Fixture `src/mocks/fixtures/theory-batch.real.json` sinh MỘT lần ngày 2026-10-28 từ backend của nhánh
 * `feat/frontend-practice-real-backend` (commit cdb3e31): kho in-memory, hai tài liệu một chương (ch1 năm cặp, ch2 ba
 * cặp khái niệm đã xác nhận, văn bản tự viết như `tests/test_theory_twins.py`), `POST /v1/blueprints` với ba slot lý
 * thuyết ch1, ch2, ch3 rồi `POST /v1/blueprints/{id}/twins` với `{"count": 1, "base_seed": 20261028}`; ghi
 * `TwinBatch.model_dump_json()`. Slot ch3 không có cặp nào nên thành `ReviewTicket`. Lệnh (trong `holocron/backend`):
 *   PYTHONPATH=<worktree>/holocron/backend .venv/Scripts/python.exe gen_theory_batch.py <đường dẫn fixture>
 * (script tạm, không commit). Sinh lại khi contract `TwinBatch` đổi.
 */

import { describe, expect, it } from "vitest";

import REAL_BATCH from "@/mocks/fixtures/theory-batch.real.json";

import { ticketLine } from "./practice";
import { type McqQuestion, type PracticeQuestion, parsePracticeBatch, parseTwin } from "./practice-data";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Bản sao sâu của batch thật dưới dạng `unknown`, để sửa từng field trong test mà không ép kiểu. */
function batchCopy(): Record<string, unknown> {
  const copy: unknown = JSON.parse(JSON.stringify(REAL_BATCH));
  if (!isRecord(copy)) throw new Error("batch thật phải là object");
  return copy;
}

function twinsOf(batch: Record<string, unknown>): Record<string, unknown>[] {
  const twins = batch.twins;
  if (!Array.isArray(twins)) throw new Error("batch thật phải có twins");
  return twins.filter(isRecord);
}

/** Twin lý thuyết đầu tiên của batch thật (bản sao) và `question` của nó. */
function firstTheoryTwin(): { twin: Record<string, unknown>; question: Record<string, unknown> } {
  const [twin] = twinsOf(batchCopy());
  if (twin === undefined || !isRecord(twin.question)) throw new Error("batch thật phải có câu lý thuyết");
  return { twin, question: twin.question };
}

function expectMalformed(twin: unknown): void {
  expect(parseTwin(twin)).toEqual({ kind: "malformed" });
}

function mcq(question: PracticeQuestion | undefined): McqQuestion {
  if (question?.kind !== "mcq") throw new Error("phải là câu trắc nghiệm");
  return question;
}

/** `TwinQuestion` câu tính toán của backend: không có `question_kind`, không có `question`. */
const COMPUTATIONAL_TWIN = {
  twin_id: "twin_tinh_toan",
  blueprint_id: "bp_94105a969f4f4b7c",
  slot_index: 2,
  template_id: "tpl_mau",
  topic_key: "mau.dang_bai.gia_tri",
  seed: 7,
  format: "MCQ4",
  stem: "Câu tính toán mẫu do sandbox sinh, chỉ để kiểm dạng.",
  options: { A: "1", B: "2", C: "3", D: "4" },
  correct_key: "B",
  numerical_answer: null,
  distractor_to_misconception_map: {
    A: { misconception_code: "MAU_NHAM_A", label_vi: "Nhầm A", rationale_vi: "Lý do nhầm phương án A." },
    C: { misconception_code: "MAU_NHAM_C", label_vi: "Nhầm C", rationale_vi: "Lý do nhầm phương án C." },
    D: { misconception_code: "MAU_NHAM_D", label_vi: "Nhầm D", rationale_vi: "Lý do nhầm phương án D." },
  },
  solution_explanation_vi: "Lời giải do backend trả.",
  citation: { doc_id: "doc_mau", page_number: 3, verbatim_quote: "Đoạn nguồn nguyên văn.", chunk_id: null },
  trace: {
    sandbox_duration_ms: 1,
    llm_attempts: 0,
    seed_rerolls: 0,
    template_sha256: "0".repeat(64),
    provenance: "precomputed_verified",
  },
  review_status: "AUTO_PASS",
  generated_at: "2026-10-28T08:00:00Z",
  gantt: [],
};

describe("TwinBatch thật của backend (TheoryTwin)", () => {
  it("TheoryTwin hợp lệ: thành câu trắc nghiệm lý thuyết, giữ nguyên số và chữ backend trả", () => {
    const batch = parsePracticeBatch(batchCopy());
    expect(batch).not.toBeNull();
    if (batch === null) return;
    expect(batch.batchId).toBe(REAL_BATCH.batch_id);
    expect(batch.skipped).toEqual({ unsupported: 0, malformed: 0 });
    expect(batch.questions).toHaveLength(REAL_BATCH.twins.length);
    REAL_BATCH.twins.forEach((raw, position) => {
      const question = mcq(batch.questions[position]);
      expect(question.twinId).toBe(raw.twin_id);
      expect(question.slotIndex).toBe(raw.slot_index);
      expect(question.questionKind).toBe("theory");
      expect(question.stem).toBe(raw.question.stem);
      expect(question.options).toEqual(raw.question.options);
      expect(question.correctKey).toBe(raw.question.correct_key);
      expect(question.citation).toEqual(raw.question.citation);
      expect(question.artifacts).toEqual([]);
    });
  });

  it("trích dẫn của phương án nhiễu lấy từ distractor_citations, gộp vào misconception cùng khoá", () => {
    const batch = parsePracticeBatch(batchCopy());
    const question = mcq(batch?.questions[0]);
    const raw = REAL_BATCH.twins[0]?.question;
    if (raw === undefined) throw new Error("thiếu câu");
    const citations: Record<string, unknown> = raw.distractor_citations;
    const map: Record<string, unknown> = raw.distractor_to_misconception_map;
    const wrong = (["A", "B", "C", "D"] as const).filter((key) => key !== question.correctKey);
    expect(Object.keys(question.misconceptions).sort()).toEqual([...wrong]);
    for (const key of wrong) {
      const misconception = question.misconceptions[key];
      expect(misconception?.citation).toEqual(citations[key]);
      const ref = map[key];
      expect(isRecord(ref) && misconception?.code === ref.misconception_code).toBe(true);
    }
    expect(question.misconceptions[question.correctKey]).toBeUndefined();
  });

  it("thiếu question_kind khi có question thì câu bị bỏ và được đếm", () => {
    const batch = batchCopy();
    const [first] = twinsOf(batch);
    if (first === undefined) throw new Error("thiếu câu");
    delete first.question_kind;
    const parsed = parsePracticeBatch(batch);
    expect(parsed?.skipped).toEqual({ unsupported: 0, malformed: 1 });
    expect(parsed?.questions).toHaveLength(REAL_BATCH.twins.length - 1);
  });

  it("thiếu trích dẫn cho một phương án sai (I3) thì câu bị bỏ", () => {
    const { twin, question } = firstTheoryTwin();
    const citations = question.distractor_citations;
    if (!isRecord(citations)) throw new Error("thiếu distractor_citations");
    const [key] = Object.keys(citations);
    if (key === undefined) throw new Error("thiếu khoá");
    delete citations[key];
    expectMalformed(twin);

    const withoutAll = firstTheoryTwin();
    delete withoutAll.question.distractor_citations;
    expectMalformed(withoutAll.twin);
  });

  it("trích dẫn cho đáp án đúng thay vì một phương án sai thì câu bị bỏ", () => {
    const { twin, question } = firstTheoryTwin();
    const citations = question.distractor_citations;
    const correct = question.correct_key;
    if (!isRecord(citations) || typeof correct !== "string") throw new Error("sai dạng");
    const [key] = Object.keys(citations);
    if (key === undefined) throw new Error("thiếu khoá");
    citations[correct] = citations[key];
    delete citations[key];
    expectMalformed(twin);
  });

  it("correct_key ngoài A–D, thiếu phương án hay misconception thiếu khoá thì câu bị bỏ (I2)", () => {
    const outside = firstTheoryTwin();
    outside.question.correct_key = "E";
    expectMalformed(outside.twin);

    const threeOptions = firstTheoryTwin();
    const options = threeOptions.question.options;
    if (!isRecord(options)) throw new Error("thiếu options");
    delete options.D;
    expectMalformed(threeOptions.twin);

    const map = firstTheoryTwin();
    const refs = map.question.distractor_to_misconception_map;
    if (!isRecord(refs)) throw new Error("thiếu map");
    const [key] = Object.keys(refs);
    if (key === undefined) throw new Error("thiếu khoá");
    delete refs[key];
    expectMalformed(map.twin);
  });

  it("trích dẫn thiếu số trang (của đáp án hoặc của phương án nhiễu) thì câu bị bỏ", () => {
    const answer = firstTheoryTwin();
    const citation = answer.question.citation;
    if (!isRecord(citation)) throw new Error("thiếu citation");
    delete citation.page_number;
    expectMalformed(answer.twin);

    const distractor = firstTheoryTwin();
    const citations = distractor.question.distractor_citations;
    if (!isRecord(citations)) throw new Error("thiếu distractor_citations");
    const [key] = Object.keys(citations);
    const one = key === undefined ? undefined : citations[key];
    if (!isRecord(one)) throw new Error("thiếu trích dẫn");
    one.page_number = 0;
    expectMalformed(distractor.twin);
  });

  it("question_kind lạ ở câu bên trong, hay format không phải MCQ4, thì câu bị bỏ", () => {
    const inner = firstTheoryTwin();
    inner.question.question_kind = "computational";
    expectMalformed(inner.twin);

    const essay = firstTheoryTwin();
    essay.twin.format = "ESSAY";
    expectMalformed(essay.twin);
  });

  it("union lẫn TwinQuestion (không có question_kind) và TheoryTwin: cả hai đều hiển thị được", () => {
    const batch = batchCopy();
    batch.twins = [...twinsOf(batch), structuredClone(COMPUTATIONAL_TWIN)];
    const parsed = parsePracticeBatch(batch);
    expect(parsed?.skipped).toEqual({ unsupported: 0, malformed: 0 });
    const kinds = parsed?.questions.map((question) => `${question.slotIndex}:${question.questionKind}`);
    expect(kinds).toEqual(["1:theory", "2:theory", "2:computational"]);
    const computational = mcq(parsed?.questions[2]);
    expect(computational.correctKey).toBe("B");
    expect(computational.misconceptions.A?.citation).toBeNull();
  });

  it("ticket lý thuyết (topic_key null) hiện nguyên lý do của backend kèm gợi ý cố định", () => {
    const parsed = parsePracticeBatch(batchCopy());
    const [ticket] = parsed?.tickets ?? [];
    const [raw] = REAL_BATCH.review_tickets;
    expect(ticket?.theory).toBe(true);
    expect(ticket?.reason).toBe(raw?.reason);
    if (ticket === undefined || raw === undefined) return;
    const line = ticketLine(ticket);
    expect(line.startsWith(raw.reason)).toBe(true);
    expect(line).toContain("khái niệm đã nhận ở tab Khái niệm của môn");
  });

  it("batch rỗng (mọi slot thành ticket) vẫn được nhận: không có câu, còn lý do để hiển thị", () => {
    const batch = batchCopy();
    batch.twins = [];
    const parsed = parsePracticeBatch(batch);
    expect(parsed).not.toBeNull();
    expect(parsed?.questions).toEqual([]);
    expect(parsed?.skipped).toEqual({ unsupported: 0, malformed: 0 });
    expect(parsed?.tickets.map((ticket) => ticket.slotIndex)).toEqual([3]);
  });
});
