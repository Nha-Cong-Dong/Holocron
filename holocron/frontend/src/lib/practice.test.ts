import { describe, expect, it } from "vitest";

import { ESSAY_GRADE_FIXTURES } from "@/mocks/fixtures/essay";
import { ESSAY_SPACED_REVIEW_TWIN_ID, TWIN_BATCH_FIXTURE } from "@/mocks/fixtures/twins";

import { localError } from "./errors";
import { createLatestCounter } from "./latest";
import {
  answerOutcome,
  appendSentence,
  buildBlueprintCreate,
  defaultFormatChoice,
  entrySummary,
  essaySubmission,
  formatChoices,
  initialPracticeState,
  isAnswered,
  mismatchedGradeError,
  type PracticeAction,
  type PracticeState,
  practiceKeyEffect,
  practiceReducer,
  practiceScope,
  revealPolicy,
  VAULT_HINT_VI,
} from "./practice";
import { type EssayGradeView, type PracticeQuestion, parseEssayGradeResult, parsePracticeBatch } from "./practice-data";
import { type KeyInput, resolveShortcut, type ShortcutScope } from "./shortcuts";

function questions(): PracticeQuestion[] {
  const batch = parsePracticeBatch(structuredClone(TWIN_BATCH_FIXTURE));
  if (batch === null) throw new Error("batch mẫu phải hợp lệ");
  return batch.questions;
}

function gradeView(): EssayGradeView {
  const view = parseEssayGradeResult(structuredClone(ESSAY_GRADE_FIXTURES[ESSAY_SPACED_REVIEW_TWIN_ID]));
  if (view === null) throw new Error("kết quả chấm mẫu phải hợp lệ");
  return view;
}

function run(state: PracticeState, ...actions: PracticeAction[]): PracticeState {
  return actions.reduce(practiceReducer, state);
}

describe("buildBlueprintCreate", () => {
  const base = { subjectCode: "MAU101", chapterCode: "ch1", questionCount: 3, choice: "MCQ4" as const };

  it("dựng slot lý thuyết với hình thức tường minh cho từng câu", () => {
    const result = buildBlueprintCreate(base);
    expect(result).toEqual({
      ok: true,
      payload: {
        subject_code: "MAU101",
        item_ids: [],
        theory_slots: [1, 2, 3].map(() => ({ chapter_code: "ch1", cognitive_level: "remember", format: "MCQ4" })),
      },
    });
  });

  it("kết hợp: xen kẽ trắc nghiệm rồi tự luận", () => {
    const result = buildBlueprintCreate({ ...base, questionCount: 4, choice: "mixed" });
    expect(result.ok && result.payload.theory_slots?.map((slot) => slot.format)).toEqual([
      "MCQ4",
      "ESSAY",
      "MCQ4",
      "ESSAY",
    ]);
  });

  it("từ chối mã chương sai mẫu, số câu ngoài khoảng và chưa chọn hình thức", () => {
    for (const chapterCode of ["", "Ch1", "chuong 1", "ch123", "ch1.234"]) {
      expect(buildBlueprintCreate({ ...base, chapterCode }).ok).toBe(false);
    }
    for (const questionCount of [0, 51, 2.5, Number.NaN]) {
      expect(buildBlueprintCreate({ ...base, questionCount }).ok).toBe(false);
    }
    expect(buildBlueprintCreate({ ...base, choice: null }).ok).toBe(false);
    expect(buildBlueprintCreate({ ...base, chapterCode: "  ch2.1 " }).ok).toBe(true);
  });
});

describe("hình thức theo exam_format", () => {
  it("điền sẵn theo môn khi được phép", () => {
    const all = formatChoices(true);
    expect(defaultFormatChoice("mcq", all)).toBe("MCQ4");
    expect(defaultFormatChoice("essay", all)).toBe("ESSAY");
    expect(defaultFormatChoice("mixed", all)).toBe("mixed");
  });

  it("chấm tự luận tắt: ẩn lựa chọn có tự luận và KHÔNG tự đổi môn tự luận sang trắc nghiệm", () => {
    const allowed = formatChoices(false);
    expect(allowed).toEqual(["MCQ4"]);
    expect(defaultFormatChoice("essay", allowed)).toBeNull();
    expect(defaultFormatChoice("mixed", allowed)).toBeNull();
    expect(defaultFormatChoice("mcq", allowed)).toBe("MCQ4");
  });
});

describe("practiceReducer: trắc nghiệm", () => {
  it("chọn, đổi lựa chọn, nộp; sau khi nộp không đổi được nữa", () => {
    const state = run(
      initialPracticeState(questions()),
      { type: "select", key: "A" },
      { type: "select", key: "C" },
      { type: "submit_mcq" },
      { type: "select", key: "B" },
    );
    expect(state.entries[0]).toEqual({ kind: "mcq", selected: "C", submitted: true });
  });

  it("chưa chọn thì không nộp được", () => {
    const state = run(initialPracticeState(questions()), { type: "submit_mcq" });
    expect(state.entries[0]).toEqual({ kind: "mcq", selected: null, submitted: false });
  });

  it("chuyển câu đóng khối đoạn nguồn; đi qua câu cuối thì kết thúc lượt; quay lại được", () => {
    let state = run(initialPracticeState(questions()), { type: "select", key: "B" }, { type: "submit_mcq" });
    state = run(state, { type: "toggle_source" });
    expect(state.sourceOpen).toBe(true);
    state = run(state, { type: "next" });
    expect(state.index).toBe(1);
    expect(state.sourceOpen).toBe(false);
    state = run(state, { type: "next" }, { type: "next" }, { type: "next" });
    expect(state.finished).toBe(true);
    state = run(state, { type: "previous" });
    expect(state.finished).toBe(false);
    expect(state.index).toBe(3);
    state = run(state, { type: "go_to", index: 0 });
    expect(state.entries[0]).toEqual({ kind: "mcq", selected: "B", submitted: true });
  });

  it("chưa nộp thì không mở được đoạn nguồn (trích dẫn là đáp án)", () => {
    expect(run(initialPracticeState(questions()), { type: "toggle_source" }).sourceOpen).toBe(false);
  });

  it("làm lại từ đầu xoá mọi câu trả lời", () => {
    const state = run(
      initialPracticeState(questions()),
      { type: "select", key: "B" },
      { type: "submit_mcq" },
      {
        type: "restart",
      },
    );
    expect(state).toEqual(initialPracticeState(questions()));
  });
});

describe("practiceReducer: tự luận", () => {
  const atEssay = (): PracticeState => run(initialPracticeState(questions()), { type: "go_to", index: 2 });
  const twinId = ESSAY_SPACED_REVIEW_TWIN_ID;

  it("soạn, gửi chấm, nhận kết quả theo twin_id", () => {
    let state = run(
      atEssay(),
      { type: "edit_essay", text: "Ôn cách nhau ngày càng xa" },
      { type: "essay_started", twinId, submission: 1 },
    );
    expect(state.entries[2]).toMatchObject({ kind: "essay", status: "grading", submission: 1 });
    state = run(state, { type: "edit_essay", text: "sửa khi đang chấm" });
    expect(state.entries[2]).toMatchObject({ draft: "Ôn cách nhau ngày càng xa" });
    state = run(state, { type: "essay_graded", twinId, submission: 1, result: gradeView() });
    expect(state.entries[2]).toMatchObject({ status: "graded", result: { matchedCount: 1, totalPoints: 2 } });
  });

  it("kết quả về sau khi đã chuyển câu vẫn gắn đúng câu tự luận đó", () => {
    let state = run(
      atEssay(),
      { type: "edit_essay", text: "x" },
      { type: "essay_started", twinId, submission: 1 },
      { type: "next" },
    );
    state = run(state, { type: "essay_graded", twinId, submission: 1, result: gradeView() });
    expect(state.index).toBe(3);
    expect(state.entries[2]).toMatchObject({ status: "graded" });
    expect(state.entries[3]).toMatchObject({ status: "editing" });
  });

  it("lỗi chấm giữ nguyên bản nháp để sửa rồi nộp lại", () => {
    const error = localError("Câu trả lời đang trống.");
    const state = run(
      atEssay(),
      { type: "edit_essay", text: "bản nháp" },
      { type: "essay_started", twinId, submission: 1 },
      { type: "essay_failed", twinId, submission: 1, error },
    );
    expect(state.entries[2]).toEqual({
      kind: "essay",
      draft: "bản nháp",
      status: "editing",
      result: null,
      error,
      submission: 1,
    });
  });

  it("kết quả khi câu chưa gửi chấm thì bị bỏ qua", () => {
    const state = run(atEssay(), { type: "essay_graded", twinId, submission: 1, result: gradeView() });
    expect(state.entries[2]).toMatchObject({ status: "editing", result: null, error: null });
  });

  it("kết quả mang twin_id của câu khác: báo phản hồi không hợp lệ, giữ bản nháp, cho nộp lại", () => {
    const mismatched = { ...gradeView(), twinId: "twin_khac" };
    let state = run(
      atEssay(),
      { type: "edit_essay", text: "bản nháp" },
      { type: "essay_started", twinId, submission: 1 },
      { type: "essay_graded", twinId, submission: 1, result: mismatched },
    );
    expect(state.entries[2]).toEqual({
      kind: "essay",
      draft: "bản nháp",
      status: "editing",
      result: null,
      error: mismatchedGradeError(),
      submission: 1,
    });
    expect(mismatchedGradeError().message).toContain("không hợp lệ");
    expect(mismatchedGradeError().action).toBe("retry");
    // Nộp lại được và lần này nhận kết quả đúng câu.
    state = run(
      state,
      { type: "essay_started", twinId, submission: 2 },
      { type: "essay_graded", twinId, submission: 2, result: gradeView() },
    );
    expect(state.entries[2]).toMatchObject({ status: "graded", draft: "bản nháp", error: null });
  });

  it("sau khi làm lại từ đầu, phản hồi của lượt gửi cũ không được gán cho câu trả lời mới", () => {
    const counter = createLatestCounter();
    const oldTicket = counter.begin();
    let state = run(
      atEssay(),
      { type: "edit_essay", text: "câu trả lời cũ" },
      { type: "essay_started", twinId, submission: oldTicket },
      { type: "restart" },
      { type: "go_to", index: 2 },
    );
    // Phản hồi cũ về khi câu đã được làm lại nhưng chưa gửi: bỏ qua.
    state = run(state, { type: "essay_graded", twinId, submission: oldTicket, result: gradeView() });
    expect(state.entries[2]).toMatchObject({ status: "editing", result: null });

    const newTicket = counter.begin();
    state = run(
      state,
      { type: "edit_essay", text: "câu trả lời mới" },
      { type: "essay_started", twinId, submission: newTicket },
    );
    // Phản hồi cũ (thành công hay lỗi) về khi lượt mới đang chờ: vẫn bỏ qua, câu vẫn đang chấm lượt mới.
    state = run(
      state,
      { type: "essay_graded", twinId, submission: oldTicket, result: gradeView() },
      { type: "essay_failed", twinId, submission: oldTicket, error: localError("lỗi cũ") },
    );
    expect(state.entries[2]).toMatchObject({ status: "grading", submission: newTicket, error: null, result: null });
    state = run(state, { type: "essay_graded", twinId, submission: newTicket, result: gradeView() });
    expect(state.entries[2]).toMatchObject({ status: "graded", draft: "câu trả lời mới" });
  });
});

describe("kết quả và chính sách hiện đáp án", () => {
  it("đúng/sai chỉ là phép so khoá với correct_key", () => {
    expect(answerOutcome("B", "B")).toBe("correct");
    expect(answerOutcome("A", "B")).toBe("incorrect");
  });

  it("trước khi nộp ẩn đáp án, misconception, trích dẫn, lời giải và các ý tự luận", () => {
    const state = initialPracticeState(questions());
    for (const entry of state.entries) {
      expect(revealPolicy(entry)).toEqual({
        correctKey: false,
        misconceptions: false,
        citation: false,
        solution: false,
        essayPoints: false,
      });
      expect(isAnswered(entry)).toBe(false);
    }
    const submitted = run(state, { type: "select", key: "A" }, { type: "submit_mcq" });
    const first = submitted.entries[0];
    if (first === undefined) throw new Error("thiếu câu đầu");
    expect(revealPolicy(first)).toMatchObject({ correctKey: true, citation: true });
  });

  it("nhãn từng câu cuối lượt không có con số tổng", () => {
    const all = questions();
    let state = run(initialPracticeState(all), { type: "select", key: "A" }, { type: "submit_mcq" }, { type: "next" });
    state = run(state, { type: "select", key: "C" }, { type: "submit_mcq" });
    const labels = all.map((question, index) => {
      const entry = state.entries[index];
      return entry === undefined ? "" : entrySummary(question, entry);
    });
    expect(labels).toEqual(["Sai (chọn A, đáp án B)", "Đúng (chọn C)", "Chưa làm", "Chưa làm"]);
  });
});

describe("phím tắt của màn luyện tập", () => {
  function key(value: string, overrides: Partial<KeyInput> = {}): KeyInput {
    return { key: value, ctrlKey: false, metaKey: false, altKey: false, target: "other", ...overrides };
  }
  function press(scope: ShortcutScope, input: KeyInput) {
    const action = resolveShortcut(input, { overlayOpen: false, scope });
    return action === null ? null : practiceKeyEffect(action);
  }

  it("ngữ cảnh theo câu đang mở", () => {
    let state = initialPracticeState(questions());
    expect(practiceScope(state)).toEqual({ kind: "mcq", phase: "unanswered" });
    state = run(state, { type: "select", key: "A" });
    expect(practiceScope(state)).toEqual({ kind: "mcq", phase: "selected" });
    state = run(state, { type: "submit_mcq" });
    expect(practiceScope(state)).toEqual({ kind: "mcq", phase: "submitted" });
    state = run(state, { type: "go_to", index: 2 });
    expect(practiceScope(state)).toEqual({ kind: "essay", phase: "answering" });
    state = run(state, { type: "go_to", index: 3 }, { type: "next" });
    expect(practiceScope(state)).toEqual({ kind: "global" });
  });

  it("1–4, A–D chọn; Enter nộp; N, Enter sang câu; C mở đoạn nguồn", () => {
    expect(press({ kind: "mcq", phase: "unanswered" }, key("3"))).toEqual({
      kind: "dispatch",
      action: { type: "select", key: "C" },
    });
    expect(press({ kind: "mcq", phase: "selected" }, key("d"))).toEqual({
      kind: "dispatch",
      action: { type: "select", key: "D" },
    });
    expect(press({ kind: "mcq", phase: "selected" }, key("Enter"))).toEqual({
      kind: "dispatch",
      action: { type: "submit_mcq" },
    });
    expect(press({ kind: "mcq", phase: "submitted" }, key("n"))).toEqual({
      kind: "dispatch",
      action: { type: "next" },
    });
    expect(press({ kind: "mcq", phase: "submitted" }, key("Enter"))).toEqual({
      kind: "dispatch",
      action: { type: "next" },
    });
    expect(press({ kind: "mcq", phase: "submitted" }, key("C"))).toEqual({
      kind: "dispatch",
      action: { type: "toggle_source" },
    });
    expect(press({ kind: "mcq", phase: "submitted" }, key("2"))).toBeNull();
  });

  it("tự luận: gõ chữ trong ô không chọn phương án; Ctrl+Enter nộp; sau khi chấm N và C dùng được", () => {
    expect(press({ kind: "essay", phase: "answering" }, key("a", { target: "editable" }))).toBeNull();
    expect(press({ kind: "essay", phase: "answering" }, key("n", { target: "editable" }))).toBeNull();
    expect(press({ kind: "essay", phase: "answering" }, key("Enter", { ctrlKey: true, target: "editable" }))).toEqual({
      kind: "submit_essay",
    });
    expect(press({ kind: "essay", phase: "graded" }, key("Enter", { ctrlKey: true, target: "editable" }))).toBeNull();
    expect(press({ kind: "essay", phase: "graded" }, key("n"))).toEqual({ kind: "dispatch", action: { type: "next" } });
    expect(press({ kind: "essay", phase: "graded" }, key("c"))).toEqual({
      kind: "dispatch",
      action: { type: "toggle_source" },
    });
  });

  it("phím của khung app không thuộc màn luyện tập", () => {
    expect(press({ kind: "mcq", phase: "unanswered" }, key("k", { ctrlKey: true }))).toBeNull();
    expect(press({ kind: "mcq", phase: "unanswered" }, key("?"))).toBeNull();
    expect(press({ kind: "mcq", phase: "unanswered" }, key("Escape"))).toBeNull();
  });
});

describe("lý do slot không sinh được", () => {
  it("không ra hai dấu chấm khi lý do đã kết thúc bằng dấu câu", () => {
    expect(appendSentence("không đủ căn cứ trong Vault", VAULT_HINT_VI)).toBe(
      `không đủ căn cứ trong Vault. ${VAULT_HINT_VI}`,
    );
    expect(appendSentence("Chương có ít hơn 4 khái niệm.", VAULT_HINT_VI)).toBe(
      `Chương có ít hơn 4 khái niệm. ${VAULT_HINT_VI}`,
    );
    expect(appendSentence("Thiếu dữ liệu…  ", VAULT_HINT_VI)).toBe(`Thiếu dữ liệu… ${VAULT_HINT_VI}`);
    expect(appendSentence("", VAULT_HINT_VI)).toBe(VAULT_HINT_VI);
  });

  it("gợi ý chung không ghi cứng ngưỡng số khái niệm", () => {
    expect(VAULT_HINT_VI).not.toMatch(/\d|bốn|ít nhất/u);
  });
});

describe("gửi câu trả lời tự luận", () => {
  it("câu trả lời trống không được gửi; bản nháp giữ nguyên và có thông báo", () => {
    expect(essaySubmission("   ").ok).toBe(false);
    expect(essaySubmission("Ôn cách nhau xa dần")).toEqual({ ok: true, answer: "Ôn cách nhau xa dần" });
    const error = localError("Câu trả lời đang trống.");
    const state = run(
      initialPracticeState(questions()),
      { type: "go_to", index: 2 },
      { type: "edit_essay", text: "  " },
      { type: "essay_rejected", error },
    );
    expect(state.entries[2]).toEqual({
      kind: "essay",
      draft: "  ",
      status: "editing",
      result: null,
      error,
      submission: null,
    });
  });
});
