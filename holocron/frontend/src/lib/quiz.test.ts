import { describe, expect, it } from "vitest";
import { localError } from "./errors";
import { ESSAY_LIMITATION_VI } from "./essay";
import { initialPracticeState, type PracticeState } from "./practice";
import type { EssayGradeView, EssayQuestion, McqQuestion, PracticeCitation, PracticeQuestion } from "./practice-data";
import {
  answerAnnouncement,
  distractorNotes,
  essayErrorPlacement,
  nextLabel,
  optionStates,
  practiceFullscreen,
  type QuizAction,
  quizHint,
  quizKeyEffect,
  quizReducer,
  RESULT_LEGEND_VI,
  resultAriaLabel,
  resultRows,
  resultStars,
  ringCenterLabel,
  sourceCaption,
} from "./quiz";
import { parsePracticeSession, practiceSessionHref } from "./subject-route";
import { breadcrumbForLocation, breadcrumbForPath, HOME_CRUMB } from "./subject-view";

function citation(page: number, docId = "doc_0123456789abcdef"): PracticeCitation {
  return { doc_id: docId, page_number: page, verbatim_quote: `Trích dẫn trang ${page}`, chunk_id: null };
}

function mcq(id: string, kind: "theory" | "computational" = "theory", withCitations = true): McqQuestion {
  const cite = (page: number) => (withCitations ? citation(page) : null);
  return {
    kind: "mcq",
    twinId: id,
    slotIndex: 0,
    questionKind: kind,
    stem: `Câu hỏi ${id}`,
    solution: "Lời giải",
    citation: citation(14),
    artifacts: [],
    options: { A: "Một", B: "Hai", C: "Ba", D: "Bốn" },
    correctKey: "B",
    misconceptions: {
      A: { code: "M1", label: "Nhầm A", rationale: "Vì A", citation: cite(3) },
      C: { code: "M2", label: "Nhầm C", rationale: "Vì C", citation: cite(4) },
      D: { code: "M3", label: "Nhầm D", rationale: "Vì D", citation: cite(5) },
    },
  };
}

function essay(id: string): EssayQuestion {
  return {
    kind: "essay",
    twinId: id,
    slotIndex: 1,
    questionKind: "theory",
    stem: `Trình bày ${id}`,
    solution: "Định nghĩa",
    citation: citation(21),
    artifacts: [],
    term: "Ngắt",
    points: [
      { index: 0, text: "Ý một" },
      { index: 1, text: "Ý hai" },
    ],
  };
}

function grade(id: string, matched: number, total: number): EssayGradeView {
  return {
    twinId: id,
    matchedCount: matched,
    totalPoints: total,
    matched: [],
    missing: [],
    citation: citation(21),
  };
}

function run(state: PracticeState, ...actions: QuizAction[]): PracticeState {
  return actions.reduce(quizReducer, state);
}

function start(...questions: PracticeQuestion[]): PracticeState {
  return initialPracticeState(questions);
}

function gradeEssay(state: PracticeState, id: string, matched: number, total: number): PracticeState {
  return run(
    state,
    { type: "edit_essay", text: "câu trả lời" },
    { type: "essay_started", twinId: id, submission: 1 },
    { type: "essay_graded", twinId: id, submission: 1, result: grade(id, matched, total) },
  );
}

describe("quizReducer", () => {
  it("pick = chọn rồi khoá ngay; không nhận lần chọn thứ hai", () => {
    const picked = run(start(mcq("q1"), mcq("q2")), { type: "pick", key: "A" });
    expect(picked.entries[0]).toEqual({ kind: "mcq", selected: "A", submitted: true });
    const again = quizReducer(picked, { type: "pick", key: "B" });
    expect(again).toBe(picked);
  });

  it("next chỉ sang câu khi câu đang mở đã trả lời (giữ Enter không nhảy qua câu chưa làm)", () => {
    const state = start(mcq("q1"), mcq("q2"), mcq("q3"));
    expect(quizReducer(state, { type: "next" })).toBe(state);
    const second = run(state, { type: "pick", key: "B" }, { type: "next" });
    expect(second.index).toBe(1);
    expect(run(second, { type: "next" }, { type: "next" }, { type: "next" }).index).toBe(1);
  });

  it("câu cuối đã trả lời: next ⇒ màn kết quả", () => {
    const done = run(start(mcq("q1")), { type: "pick", key: "C" }, { type: "next" });
    expect(done.finished).toBe(true);
    expect(quizReducer(done, { type: "pick", key: "A" })).toBe(done);
  });

  it("tự luận: đã chấm mới sang câu; chấm lỗi thì bỏ qua được (câu tính Chưa làm)", () => {
    const state = start(essay("e1"), mcq("q2"));
    expect(quizReducer(state, { type: "skip_essay" })).toBe(state);
    const failed = run(
      state,
      { type: "edit_essay", text: "x" },
      { type: "essay_started", twinId: "e1", submission: 1 },
      { type: "essay_failed", twinId: "e1", submission: 1, error: localError("mất kết nối") },
    );
    expect(quizReducer(failed, { type: "next" })).toBe(failed);
    const skipped = quizReducer(failed, { type: "skip_essay" });
    expect(skipped.index).toBe(1);
    expect(resultRows(skipped)[0]?.status).toBe("unanswered");
    const graded = gradeEssay(start(essay("e1"), mcq("q2")), "e1", 1, 2);
    expect(quizReducer(graded, { type: "next" }).index).toBe(1);
  });

  it("bỏ qua chỉ áp cho câu tự luận đang có lỗi", () => {
    const state = start(mcq("q1"), mcq("q2"));
    expect(quizReducer(state, { type: "skip_essay" })).toBe(state);
  });
});

describe("quizKeyEffect", () => {
  it("phím số chọn và khoá ngay; Enter/N sang câu; C mở nguồn; Ctrl+Enter chấm", () => {
    expect(quizKeyEffect({ type: "select_option", key: "B" })).toEqual({
      kind: "dispatch",
      action: { type: "pick", key: "B" },
    });
    expect(quizKeyEffect({ type: "next" })).toEqual({ kind: "dispatch", action: { type: "next" } });
    expect(quizKeyEffect({ type: "open_citation" })).toEqual({ kind: "dispatch", action: { type: "toggle_source" } });
    expect(quizKeyEffect({ type: "submit_essay" })).toEqual({ kind: "submit_essay" });
  });

  it("không còn bước Nộp riêng; phím của khung app không thuộc màn này", () => {
    expect(quizKeyEffect({ type: "submit" })).toBeNull();
    expect(quizKeyEffect({ type: "open_palette" })).toBeNull();
    expect(quizKeyEffect({ type: "show_help" })).toBeNull();
    expect(quizKeyEffect({ type: "close" })).toBeNull();
  });
});

describe("optionStates", () => {
  const question = mcq("q1");

  it("chưa chọn: không lộ đáp án", () => {
    const states = optionStates(question, { kind: "mcq", selected: null, submitted: false });
    expect(states.map((option) => option.tone)).toEqual(["idle", "idle", "idle", "idle"]);
    expect(states.every((option) => option.verdict === null && !option.locked)).toBe(true);
    expect(states[0]?.ariaLabel).toBe("Phương án A (phím 1): Một");
  });

  it("chọn sai: phương án chọn có chữ Bạn chọn, đáp án đúng vẫn hiện, còn lại mờ", () => {
    const states = optionStates(question, { kind: "mcq", selected: "C", submitted: true });
    expect(states.map((option) => option.tone)).toEqual(["dim", "right", "wrong", "dim"]);
    expect(states[1]?.verdict).toBe("Đáp án đúng");
    expect(states[2]?.verdict).toBe("Bạn chọn");
    expect(states[2]?.ariaLabel).toBe("Phương án C (phím 3): Ba, bạn chọn, sai");
    expect(states.every((option) => option.locked)).toBe(true);
  });

  it("chọn đúng: chỉ một phương án sáng, nhãn đọc ghi cả bạn chọn", () => {
    const states = optionStates(question, { kind: "mcq", selected: "B", submitted: true });
    expect(states.map((option) => option.tone)).toEqual(["dim", "right", "dim", "dim"]);
    expect(states[1]?.ariaLabel).toBe("Phương án B (phím 2): Hai, đáp án đúng, bạn chọn");
  });
});

describe("distractorNotes", () => {
  it("chưa chọn hoặc chọn đúng: không có phương án đã chọn để giải thích", () => {
    expect(distractorNotes(mcq("q"), { kind: "mcq", selected: null, submitted: false })).toEqual({
      chosen: null,
      others: [],
    });
    const right = distractorNotes(mcq("q"), { kind: "mcq", selected: "B", submitted: true });
    expect(right.chosen).toBeNull();
    expect(right.others.map((note) => note.key)).toEqual(["A", "C", "D"]);
  });

  it("chọn sai: giải thích phương án đã chọn kèm trích dẫn, các phương án sai khác riêng", () => {
    const notes = distractorNotes(mcq("q"), { kind: "mcq", selected: "C", submitted: true });
    expect(notes.chosen).toEqual({ key: "C", label: "Nhầm C", rationale: "Vì C", citation: citation(4) });
    expect(notes.others.map((note) => note.key)).toEqual(["A", "D"]);
  });

  it("câu lý thuyết thiếu trích dẫn: không hiện như khẳng định kiến thức (I3)", () => {
    const notes = distractorNotes(mcq("q", "theory", false), { kind: "mcq", selected: "A", submitted: true });
    expect(notes).toEqual({ chosen: null, others: [] });
  });

  it("câu tính toán: nhầm lẫn do bộ sinh câu ghi, không có trích dẫn vẫn hiện", () => {
    const notes = distractorNotes(mcq("q", "computational", false), { kind: "mcq", selected: "A", submitted: true });
    expect(notes.chosen).toEqual({ key: "A", label: "Nhầm A", rationale: "Vì A", citation: null });
    expect(notes.others).toHaveLength(2);
  });
});

describe("chữ của câu", () => {
  it("dòng đọc kết quả: đúng, chưa đúng (kèm đáp án đúng), chưa trả lời", () => {
    const question = mcq("q1");
    expect(answerAnnouncement(question, { kind: "mcq", selected: "B", submitted: true })).toBe(
      "Đúng. Nguồn: trang 14.",
    );
    expect(answerAnnouncement(question, { kind: "mcq", selected: "A", submitted: true })).toBe(
      "Chưa đúng. Đáp án đúng là B, “Hai”. Nguồn: trang 14.",
    );
    expect(answerAnnouncement(question, { kind: "mcq", selected: null, submitted: false })).toBe("");
  });

  it("tự luận: đã chấm đọc Khớp x/y ý, chưa chấm thì rỗng", () => {
    const graded = gradeEssay(start(essay("e1")), "e1", 2, 5);
    const entry = graded.entries[0];
    if (entry === undefined) throw new Error("thiếu câu");
    expect(answerAnnouncement(essay("e1"), entry)).toBe("Khớp 2/5 ý. Nguồn: trang 21.");
    const fresh = start(essay("e1")).entries[0];
    if (fresh === undefined) throw new Error("thiếu câu");
    expect(answerAnnouncement(essay("e1"), fresh)).toBe("");
  });

  it("gợi ý và nút chính theo trạng thái", () => {
    expect(quizHint({ kind: "mcq", selected: null, submitted: false })).toBe("Bấm đáp án hoặc phím 1–4.");
    expect(quizHint({ kind: "mcq", selected: "A", submitted: true })).toBe("Enter để đi tiếp.");
    const editing = start(essay("e1")).entries[0];
    if (editing === undefined) throw new Error("thiếu câu");
    expect(quizHint(editing)).toBe("Viết xong thì bấm Chấm ý.");
    expect(nextLabel(start(mcq("q1"), mcq("q2")))).toBe("Câu tiếp");
    expect(nextLabel({ ...start(mcq("q1"), mcq("q2")), index: 1 })).toBe("Xem kết quả");
    expect(nextLabel(start(mcq("q1")))).toBe("Xem kết quả");
  });

  it("lỗi chấm: lỗi dữ liệu nhập ở ô trả lời, lỗi khác ở khung lỗi", () => {
    expect(essayErrorPlacement(localError("Câu trả lời đang trống."))).toBe("field");
    expect(essayErrorPlacement({ ...localError("x"), kind: "network" })).toBe("screen");
    expect(essayErrorPlacement({ ...localError("x"), kind: "server" })).toBe("screen");
  });

  it("chú thích nguồn: số trang và tên tài liệu, không bao giờ mã tài liệu", () => {
    const titles = new Map([["doc_0123456789abcdef", "Bài giảng chương 2"]]);
    expect(sourceCaption(citation(7), titles)).toEqual({ pageLabel: "Trang 7", title: "Bài giảng chương 2" });
    const unknown = sourceCaption(citation(7, "doc_ffffffffffffffff"), titles);
    expect(unknown).toEqual({ pageLabel: "Trang 7", title: null });
    expect(JSON.stringify(unknown)).not.toContain("doc_");
    expect(sourceCaption(citation(7), new Map([["doc_0123456789abcdef", "  "]])).title).toBeNull();
  });
});

describe("kết quả", () => {
  function finishedMixed(): PracticeState {
    let state = start(mcq("q1"), mcq("q2"), essay("e3"), essay("e4"));
    state = run(state, { type: "pick", key: "B" }, { type: "next" }, { type: "pick", key: "D" }, { type: "next" });
    state = gradeEssay(state, "e3", 3, 3);
    state = run(
      state,
      { type: "next" },
      { type: "edit_essay", text: "x" },
      { type: "essay_started", twinId: "e4", submission: 2 },
      { type: "essay_failed", twinId: "e4", submission: 2, error: { ...localError("x"), kind: "network" } },
      { type: "skip_essay" },
    );
    return state;
  }

  it("danh sách: trạng thái, Câu i. {stem}, Trang N", () => {
    const state = finishedMixed();
    expect(state.finished).toBe(true);
    expect(resultRows(state)).toEqual([
      { twinId: "q1", status: "correct", statusLabel: "Đúng", text: "Câu 1. Câu hỏi q1", page: "Trang 14" },
      { twinId: "q2", status: "incorrect", statusLabel: "Sai", text: "Câu 2. Câu hỏi q2", page: "Trang 14" },
      { twinId: "e3", status: "essay", statusLabel: "Khớp 3/3 ý", text: "Câu 3. Trình bày e3", page: "Trang 21" },
      { twinId: "e4", status: "unanswered", statusLabel: "Chưa làm", text: "Câu 4. Trình bày e4", page: "Trang 21" },
    ]);
  });

  it("tự luận khớp đủ ý vẫn là sao tím, không bao giờ là Đúng", () => {
    const rows = resultRows(finishedMixed());
    expect(resultStars(rows).map((star) => star.tone)).toEqual(["gold", "empty", "violet", "dim"]);
    expect(rows[2]?.statusLabel).not.toBe("Đúng");
  });

  it("sao chia đều vòng, trễ tăng dần", () => {
    const stars = resultStars(resultRows(finishedMixed()));
    expect(stars.map((star) => star.angle)).toEqual([0, 90, 180, 270]);
    expect(stars.map((star) => star.delayMs)).toEqual([120, 210, 300, 390]);
  });

  it("đề một câu: một sao ở góc 0, giữa vòng là 1 câu", () => {
    const state = run(start(mcq("q1")), { type: "pick", key: "A" }, { type: "next" });
    const rows = resultRows(state);
    expect(resultStars(rows)).toEqual([{ twinId: "q1", tone: "empty", angle: 0, delayMs: 120 }]);
    expect(ringCenterLabel(rows)).toBe("1 câu");
    expect(resultAriaLabel(rows)).toBe("Vòng sao của đề 1 câu: Câu 1 sai.");
  });

  it("nhãn đọc của vòng sao đọc từng câu, không có tổng số câu đúng", () => {
    const label = resultAriaLabel(resultRows(finishedMixed()));
    expect(label).toBe("Vòng sao của đề 4 câu: Câu 1 đúng; Câu 2 sai; Câu 3 tự luận, khớp 3/3 ý; Câu 4 chưa làm.");
    expect(label).not.toMatch(/câu đúng|trên/);
  });

  it("chú giải theo quyết định: tự luận là sao tím, có sao mờ", () => {
    expect(RESULT_LEGEND_VI.join(" · ")).toBe(
      "Sao vàng: đúng · Sao rỗng: sai · Sao tím: câu tự luận (xem số ý khớp) · Sao mờ: chưa làm",
    );
  });

  it("câu giới hạn của chấm tự luận vẫn có sẵn để hiện cạnh kết quả", () => {
    expect(ESSAY_LIMITATION_VI).toContain("diễn đạt khác");
  });
});

describe("phiên làm bài trên địa chỉ", () => {
  const query = (text: string) => new URLSearchParams(text);

  it("chỉ nhận quiz hoặc result trong tab Ôn tập; giá trị lạ ⇒ null", () => {
    expect(parsePracticeSession(query("code=VXL01&tab=practice&session=quiz"))).toBe("quiz");
    expect(parsePracticeSession(query("code=VXL01&tab=practice&session=result"))).toBe("result");
    expect(parsePracticeSession(query("code=VXL01&tab=practice&session=xyz"))).toBeNull();
    expect(parsePracticeSession(query("code=VXL01&tab=practice"))).toBeNull();
    expect(parsePracticeSession(query("code=VXL01&tab=concepts&session=quiz"))).toBeNull();
  });

  it("dựng địa chỉ tab Ôn tập kèm hoặc bỏ session", () => {
    expect(practiceSessionHref("VXL01", "quiz")).toBe("/subject?code=VXL01&tab=practice&session=quiz");
    expect(practiceSessionHref("VXL01", null)).toBe("/subject?code=VXL01&tab=practice");
  });

  it("trang môn chỉ toàn màn khi tab Ôn tập đang làm đề hoặc xem kết quả", () => {
    expect(practiceFullscreen("practice", "quiz")).toBe(true);
    expect(practiceFullscreen("practice", "result")).toBe(true);
    expect(practiceFullscreen("practice", null)).toBe(false);
    expect(practiceFullscreen("concepts", "quiz")).toBe(false);
  });
});

describe("breadcrumb khi đang làm bài", () => {
  const READY = {
    kind: "ready" as const,
    subjects: [
      {
        subject_code: "VXL01",
        display_name: "Vi xử lý",
        exam_format: "mcq" as const,
        version: 1,
        created_at: "2026-10-01T00:00:00Z",
        updated_at: "2026-10-01T00:00:00Z",
      },
    ],
  };

  it("Môn học › {môn} (liên kết về tab Ôn tập) › Đang ôn | Kết quả", () => {
    expect(breadcrumbForPath("/subject", "VXL01", READY, "quiz")).toEqual([
      HOME_CRUMB,
      { label: "Vi xử lý", href: "/subject?code=VXL01&tab=practice" },
      { label: "Đang ôn", href: null },
    ]);
    expect(
      breadcrumbForLocation("/subject/", new URLSearchParams("code=VXL01&tab=practice&session=result"), READY)[2],
    ).toEqual({ label: "Kết quả", href: null });
  });

  it("không có session hoặc thiếu mã môn: giữ breadcrumb cũ", () => {
    expect(breadcrumbForPath("/subject", "VXL01", READY)).toEqual([HOME_CRUMB, { label: "Vi xử lý", href: null }]);
    expect(breadcrumbForPath("/subject", null, READY, "quiz")).toEqual([
      HOME_CRUMB,
      { label: "Chưa rõ môn", href: null },
    ]);
  });
});
