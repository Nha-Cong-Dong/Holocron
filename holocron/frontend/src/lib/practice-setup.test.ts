import { describe, expect, it } from "vitest";

import { DEFAULT_QUESTION_COUNT } from "./practice";
import type { PracticeBlueprintSlot, PracticeTicket } from "./practice-data";
import {
  buildMultiChapterBlueprint,
  chapterChips,
  confirmedTotal,
  defaultSelection,
  failureTechnical,
  formatOptions,
  initialFormat,
  NO_CHAPTER_SELECTED_VI,
  QUESTION_COUNTS,
  selectedChapters,
  setupSummary,
  ticketTechnical,
  ticketText,
} from "./practice-setup";
import type { SkyPair } from "./sky";

const PAIRS: SkyPair[] = [
  { chapter_code: "ch10", status: "confirmed" },
  { chapter_code: "ch2", status: "confirmed" },
  { chapter_code: "ch2", status: "confirmed" },
  { chapter_code: "ch2", status: "proposed" },
  { chapter_code: "ch3", status: "proposed" },
  { chapter_code: "ch3", status: "rejected" },
];

describe("chip chương", () => {
  const chips = chapterChips(PAIRS);

  it("một chip mỗi chương có khái niệm, thứ tự tự nhiên, chỉ 'Chương N' và số đã nhận", () => {
    expect(chips).toEqual([
      { code: "ch2", label: "Chương 2", confirmed: 2, disabled: false },
      { code: "ch3", label: "Chương 3", confirmed: 0, disabled: true },
      { code: "ch10", label: "Chương 10", confirmed: 1, disabled: false },
    ]);
  });

  it("mặc định chọn mọi chương có ít nhất 1 khái niệm đã nhận", () => {
    expect(defaultSelection(chips)).toEqual(["ch2", "ch10"]);
    expect(confirmedTotal(chips)).toBe(3);
    expect(confirmedTotal(chapterChips([]))).toBe(0);
  });

  it("chương bị khoá không tính dù có trong lựa chọn", () => {
    expect(selectedChapters(chips, new Set(["ch3", "ch10"])).map((chip) => chip.code)).toEqual(["ch10"]);
  });
});

describe("setupSummary", () => {
  const chips = chapterChips(PAIRS);

  it("số câu người dùng chọn, số khái niệm và số chòm sao đếm từ danh sách; không có số phút", () => {
    const summary = setupSummary(10, chips, new Set(["ch2", "ch10"]));
    expect(summary).toBe("Đề 10 câu từ 3 khái niệm của 2 chòm sao.");
    expect(summary).not.toMatch(/phút/);
  });

  it("chưa chọn chương nào ⇒ nhắc chọn", () => {
    expect(setupSummary(5, chips, new Set())).toBe(NO_CHAPTER_SELECTED_VI);
    expect(setupSummary(5, chips, new Set(["ch3"]))).toBe(NO_CHAPTER_SELECTED_VI);
  });

  it("chip số câu 5/10/20, mặc định nằm trong đó", () => {
    expect([...QUESTION_COUNTS]).toEqual([5, 10, 20]);
    expect(QUESTION_COUNTS).toContain(DEFAULT_QUESTION_COUNT);
  });
});

describe("hình thức", () => {
  it("chấm tự luận không khả dụng: lựa chọn có tự luận bị khoá, không bị bỏ", () => {
    expect(formatOptions(false)).toEqual([
      { choice: "MCQ4", disabled: false },
      { choice: "ESSAY", disabled: true },
      { choice: "mixed", disabled: true },
    ]);
    expect(formatOptions(true).every((option) => !option.disabled)).toBe(true);
  });

  it("môn mixed (mặc định khi tạo môn): Trắc nghiệm khi tự luận không khả dụng, Kết hợp khi khả dụng", () => {
    expect(initialFormat("mixed", false)).toBe("MCQ4");
    expect(initialFormat("mixed", true)).toBe("mixed");
  });

  it("môn thi tự luận hay trắc nghiệm theo hình thức của môn khi tự luận khả dụng", () => {
    expect(initialFormat("essay", true)).toBe("ESSAY");
    expect(initialFormat("essay", false)).toBe("MCQ4");
    expect(initialFormat("mcq", true)).toBe("MCQ4");
  });
});

describe("buildMultiChapterBlueprint", () => {
  function slots(chapters: string[], count: number, choice: "MCQ4" | "ESSAY" | "mixed") {
    const result = buildMultiChapterBlueprint({ subjectCode: "MAU101", chapters, count, choice });
    if (!result.ok) throw new Error(result.message);
    return result.payload;
  }

  it("1 chương: mọi câu cùng chương, mức remember, không có câu tính toán", () => {
    const payload = slots(["ch2"], 5, "MCQ4");
    expect(payload.subject_code).toBe("MAU101");
    expect(payload.item_ids).toEqual([]);
    expect(payload.theory_slots).toHaveLength(5);
    for (const slot of payload.theory_slots ?? []) {
      expect(slot).toEqual({ chapter_code: "ch2", cognitive_level: "remember", format: "MCQ4" });
    }
  });

  it("3 chương × 10 câu: rải lần lượt theo chương", () => {
    const payload = slots(["ch1", "ch2", "ch10"], 10, "MCQ4");
    expect(payload.theory_slots?.map((slot) => slot.chapter_code)).toEqual([
      "ch1",
      "ch2",
      "ch10",
      "ch1",
      "ch2",
      "ch10",
      "ch1",
      "ch2",
      "ch10",
      "ch1",
    ]);
  });

  it("Kết hợp với 2 chương: mỗi chương xen kẽ trắc nghiệm rồi tự luận", () => {
    const payload = slots(["ch1", "ch2"], 6, "mixed");
    expect(payload.theory_slots?.map((slot) => `${slot.chapter_code}:${slot.format}`)).toEqual([
      "ch1:MCQ4",
      "ch2:MCQ4",
      "ch1:ESSAY",
      "ch2:ESSAY",
      "ch1:MCQ4",
      "ch2:MCQ4",
    ]);
  });

  it("0 chương, hình thức chưa chọn, số câu ngoài giới hạn, mã chương sai dạng ⇒ không dựng", () => {
    const base = { subjectCode: "MAU101", chapters: ["ch1"], count: 5, choice: "MCQ4" as const };
    expect(buildMultiChapterBlueprint({ ...base, chapters: [] })).toEqual({
      ok: false,
      message: NO_CHAPTER_SELECTED_VI,
    });
    expect(buildMultiChapterBlueprint({ ...base, choice: null })).toMatchObject({ ok: false });
    expect(buildMultiChapterBlueprint({ ...base, count: 51 })).toMatchObject({ ok: false });
    expect(buildMultiChapterBlueprint({ ...base, count: 0 })).toMatchObject({ ok: false });
    expect(buildMultiChapterBlueprint({ ...base, chapters: ["chuong1"] })).toMatchObject({ ok: false });
    expect(buildMultiChapterBlueprint({ ...base, count: 50 }).ok).toBe(true);
  });
});

describe("câu chưa soạn được", () => {
  const slotsOf: PracticeBlueprintSlot[] = [
    { slotIndex: 1, chapterCode: "ch2", format: "MCQ4" },
    { slotIndex: 2, chapterCode: "ch3", format: "MCQ4" },
  ];
  const reason = "không đủ căn cứ: chương ch3 chỉ có 1 khái niệm đã xác nhận";

  it("câu lý thuyết: câu chung kèm việc nên làm ở đúng chương; không có chữ 'lỗi', không chép lý do", () => {
    const ticket: PracticeTicket = { slotIndex: 2, reason, theory: true };
    const text = ticketText(ticket, slotsOf);
    expect(text).toBe(
      "Câu thứ 2 (Chương 3) chưa soạn được từ các khái niệm đã nhận. Nhận thêm khái niệm của Chương 3 ở tab Khái niệm rồi tạo đề lại.",
    );
    expect(text).not.toMatch(/lỗi/i);
    expect(text).not.toContain(reason);
  });

  it("không biết chương của câu: vẫn nói việc nên làm", () => {
    const text = ticketText({ slotIndex: 9, reason, theory: true }, slotsOf);
    expect(text).toContain("Nhận thêm khái niệm ở tab Khái niệm");
    expect(text).not.toMatch(/lỗi/i);
  });

  it("câu không phải lý thuyết: gợi ý tạo đề khác", () => {
    const text = ticketText({ slotIndex: 1, reason: "hết lượt thử", theory: false }, slotsOf);
    expect(text).toContain("Tạo đề khác");
    expect(text).not.toMatch(/lỗi/i);
    expect(text).not.toContain("hết lượt thử");
  });

  it("nguyên văn lý do chỉ nằm ở phần kỹ thuật", () => {
    expect(ticketTechnical([{ slotIndex: 2, reason, theory: true }])).toBe(`#2: ${reason}`);
  });
});

describe("setupSummary: đếm theo chương đang chọn", () => {
  const chips = chapterChips(PAIRS);

  it("một chương ⇒ số khái niệm của riêng chương đó", () => {
    expect(setupSummary(10, chips, new Set(["ch2"]))).toBe("Đề 10 câu từ 2 khái niệm của 1 chòm sao.");
  });

  it("chương bị khoá trong lựa chọn không tính vào số khái niệm lẫn số chòm sao", () => {
    expect(setupSummary(10, chips, new Set(["ch2", "ch3", "ch10"]))).toBe("Đề 10 câu từ 3 khái niệm của 2 chòm sao.");
  });
});

describe("buildMultiChapterBlueprint: biên", () => {
  const base = { subjectCode: "MAU101", chapters: ["ch1"], count: 5, choice: "MCQ4" as const };

  it("một mã chương sai dạng trong nhiều chương ⇒ không dựng", () => {
    expect(buildMultiChapterBlueprint({ ...base, chapters: ["ch1", "chuong1"] })).toMatchObject({ ok: false });
  });

  it("1 câu ⇒ dựng được, đúng 1 câu", () => {
    const result = buildMultiChapterBlueprint({ ...base, count: 1 });
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.payload.theory_slots).toHaveLength(1);
  });

  it("số câu không nguyên hoặc NaN ⇒ không dựng", () => {
    expect(buildMultiChapterBlueprint({ ...base, count: 2.5 })).toMatchObject({ ok: false });
    expect(buildMultiChapterBlueprint({ ...base, count: Number.NaN })).toMatchObject({ ok: false });
  });
});

describe("chi tiết kỹ thuật", () => {
  it("nhiều câu chưa soạn được ⇒ mỗi câu một dòng", () => {
    expect(
      ticketTechnical([
        { slotIndex: 1, reason: "a", theory: true },
        { slotIndex: 2, reason: "b", theory: false },
      ]),
    ).toBe("#1: a\n#2: b");
  });

  it("chưa soạn được đề: mã lỗi rồi các vấn đề của bước kiểm duyệt, mỗi mục một dòng; không có gì ⇒ không hiện", () => {
    expect(failureTechnical({ diagnostic: "HTTP 409 · BlockingIssues", issues: ["Thiếu bảng", "Sai đơn vị"] })).toBe(
      "HTTP 409 · BlockingIssues\nThiếu bảng\nSai đơn vị",
    );
    expect(failureTechnical({ issues: ["Thiếu bảng"] })).toBe("Thiếu bảng");
    expect(failureTechnical({ diagnostic: "HTTP 500", issues: [] })).toBe("HTTP 500");
    expect(failureTechnical({ issues: [] })).toBeUndefined();
  });
});
