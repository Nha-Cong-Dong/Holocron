import { describe, expect, it } from "vitest";

import type { AnswerEntry } from "@/lib/practice";
import type { McqQuestion, PracticeQuestion } from "@/lib/practice-data";
import {
  attemptCountsLine,
  attemptStatusFor,
  buildAttemptCreate,
  formatSubmittedAt,
  nextOffset,
  parseAttempt,
  parseAttemptPage,
  parseReviewPage,
  pendingReducer,
  unsavedAttempts,
} from "@/lib/practice-history";

const citation = { doc_id: "d1", page_number: 2, verbatim_quote: "Trích nguyên văn", chunk_id: null };

function mcq(twinId: string): McqQuestion {
  return {
    kind: "mcq",
    twinId,
    slotIndex: 1,
    questionKind: "theory",
    stem: "Câu?",
    solution: "x",
    citation,
    artifacts: [],
    options: { A: "a", B: "b", C: "c", D: "d" },
    correctKey: "A",
    misconceptions: {},
  };
}

const essay: PracticeQuestion = {
  kind: "essay",
  twinId: "t_e",
  slotIndex: 2,
  questionKind: "theory",
  stem: "?",
  solution: "",
  citation,
  artifacts: [],
  term: "x",
  points: [],
};

describe("buildAttemptCreate", () => {
  const questions: PracticeQuestion[] = [mcq("t1"), essay, mcq("t2")];
  const entries: AnswerEntry[] = [
    { kind: "mcq", selected: "B", submitted: true },
    { kind: "essay", draft: "", status: "editing", result: null, error: null, submission: null },
    { kind: "mcq", selected: null, submitted: false },
  ];

  it("chỉ gửi câu trắc nghiệm đã nộp, không có đúng/sai", () => {
    const body = buildAttemptCreate("batch", "tb_0123456789abcdef", questions, entries);
    expect(body).toEqual({
      mode: "batch",
      batch_id: "tb_0123456789abcdef",
      answers: [{ twin_id: "t1", chosen_key: "B" }],
    });
  });

  it("mode review đặt batch_id null", () => {
    expect(buildAttemptCreate("review", "tb_x", questions, entries)?.batch_id).toBeNull();
  });

  it("thiếu batch_id ở mode batch hoặc không có câu nào thì không gửi", () => {
    expect(buildAttemptCreate("batch", null, questions, entries)).toBeNull();
    expect(buildAttemptCreate("batch", "tb_x", [essay], entries.slice(1, 2))).toBeNull();
  });
});

const summary = {
  attempt_id: "pa_0123456789abcdef",
  subject_code: "MAU101",
  mode: "batch",
  batch_id: "tb_0123456789abcdef",
  blueprint_id: "bp_1",
  submitted_at: "2026-11-04T01:00:00Z",
  total_count: 3,
  correct_count: 2,
  incorrect_count: 1,
  skipped_count: 0,
};

describe("parse", () => {
  it("nhận lượt và trang lượt đúng dạng, từ chối sai dạng", () => {
    expect(parseAttemptPage({ total: 1, attempts: [summary] })?.attempts).toHaveLength(1);
    expect(parseAttemptPage({ total: 1, attempts: [{ ...summary, mode: "x" }] })).toBeNull();
    expect(parseAttemptPage({ total: "1", attempts: [] })).toBeNull();
  });

  it("lượt chi tiết cần answers hợp lệ", () => {
    const answer = {
      ordinal: 0,
      twin_id: "t1",
      batch_id: "tb_x",
      slot_index: 1,
      chosen_key: "B",
      correct_key: "A",
      outcome: "incorrect",
    };
    expect(parseAttempt({ ...summary, answers: [answer] })?.answers[0]?.outcome).toBe("incorrect");
    expect(parseAttempt({ ...summary, answers: [{ ...answer, outcome: "?" }] })).toBeNull();
  });

  it("trang ôn bỏ và đếm câu không hiển thị được, giữ total của backend", () => {
    const page = parseReviewPage({ total: 5, questions: [{ twin: {}, batch_id: "b" }, 3] });
    expect(page).toEqual({ total: 5, items: [], undisplayable: 2 });
    expect(parseReviewPage({ questions: [] })).toBeNull();
  });
});

describe("nhãn và phân trang", () => {
  it("ghép số đếm backend trả", () => {
    const page = parseAttemptPage({ total: 1, attempts: [summary] });
    const attempt = page?.attempts[0];
    expect(attempt).toBeDefined();
    if (attempt !== undefined) expect(attemptCountsLine(attempt)).toBe("2 đúng, 1 sai (3 câu)");
  });

  it("thời gian không đọc được giữ nguyên", () => {
    expect(formatSubmittedAt("không phải ngày")).toBe("không phải ngày");
  });

  it("nextOffset theo total", () => {
    expect(nextOffset(0, 20, 45)).toBe(20);
    expect(nextOffset(40, 5, 45)).toBeNull();
    expect(nextOffset(0, 0, 45)).toBeNull();
  });
});

describe("pendingReducer", () => {
  const body = { mode: "batch" as const, batch_id: "b1", answers: [{ twin_id: "t1", chosen_key: "A" as const }] };

  it("enqueue thêm lượt đang gửi và bỏ qua khoá trùng", () => {
    const once = pendingReducer([], { type: "enqueue", key: "b1", body });
    expect(once).toEqual([{ key: "b1", body, status: "saving" }]);
    expect(pendingReducer(once, { type: "enqueue", key: "b1", body })).toEqual(once);
  });

  it("gửi lỗi thì giữ lượt, retry đưa về đang gửi, thành công thì không còn chưa lưu", () => {
    let queue = pendingReducer([], { type: "enqueue", key: "b1", body });
    queue = pendingReducer(queue, { type: "failed", key: "b1" });
    expect(attemptStatusFor(queue, "b1")).toBe("failed");
    expect(unsavedAttempts(queue)).toHaveLength(1);
    queue = pendingReducer(queue, { type: "retry", key: "b1" });
    expect(attemptStatusFor(queue, "b1")).toBe("saving");
    queue = pendingReducer(queue, { type: "saved", key: "b1" });
    expect(attemptStatusFor(queue, "b1")).toBe("saved");
    expect(unsavedAttempts(queue)).toEqual([]);
  });

  it("retry chỉ áp cho lượt đang lỗi; saved/failed chỉ áp cho lượt đang gửi", () => {
    const saving = pendingReducer([], { type: "enqueue", key: "b1", body });
    expect(pendingReducer(saving, { type: "retry", key: "b1" })).toEqual(saving);
    const saved = pendingReducer(saving, { type: "saved", key: "b1" });
    expect(pendingReducer(saved, { type: "failed", key: "b1" })).toEqual(saved);
  });

  it("khoá lạ: trạng thái idle", () => {
    expect(attemptStatusFor([], "x")).toBe("idle");
  });
});
