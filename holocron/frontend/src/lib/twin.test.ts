import { describe, expect, it } from "vitest";

import type { AttemptLog, GanttSegment, ReviewTicket, TwinBatch, TwinQuestion } from "@/types/holocron";

import {
  clampTwinCount,
  formatNumericalAnswer,
  GANTT_PALETTE_SIZE,
  ganttDescription,
  ganttLayout,
  groupTwinsBySlot,
  optionViews,
  PROVENANCE_LABELS,
  REVIEW_STATUS_LABELS,
  shortHash,
  ticketAttemptSummary,
  tickPercent,
} from "./twin";

const SEGMENTS: GanttSegment[] = [
  { process: "P1", start: 0, end: 3 },
  { process: "P2", start: 3, end: 6 },
  { process: "P1", start: 6, end: 7 },
  { process: "P3", start: 9, end: 12 },
];

function twin(overrides: Partial<TwinQuestion> = {}): TwinQuestion {
  return {
    twin_id: "twin_1",
    blueprint_id: "bp_1",
    slot_index: 1,
    template_id: "tpl_1",
    topic_key: "os.cpu_sched.rr.avg_waiting_time",
    seed: 20268338,
    format: "MCQ4",
    stem: "Cho các tiến trình sau …",
    options: { A: "11.25 ms", B: "6.25 ms", C: "2.25 ms", D: "4.50 ms" },
    correct_key: "B",
    numerical_answer: null,
    distractor_to_misconception_map: {
      A: { misconception_code: "WAIT_CONFUSED_WITH_TURNAROUND", label_vi: "Nhầm với turnaround", rationale_vi: "…" },
      C: { misconception_code: "WAIT_EQUALS_FIRST_RESPONSE", label_vi: "Nhầm với response", rationale_vi: "…" },
      D: { misconception_code: "RR_TREATED_AS_FCFS", label_vi: "Coi RR như FCFS", rationale_vi: "…" },
    },
    solution_explanation_vi: "Gantt: …",
    citation: { doc_id: "doc_1", page_number: 14, verbatim_quote: "Hết q mà chưa xong…", chunk_id: null },
    trace: {
      sandbox_duration_ms: 42,
      llm_attempts: 1,
      seed_rerolls: 1,
      template_sha256: "0123456789abcdef0123456789abcdef",
      provenance: "llm_generated",
    },
    review_status: "AUTO_PASS",
    generated_at: "2026-01-01T00:00:00Z",
    gantt: SEGMENTS,
    ...overrides,
  };
}

function ticket(slotIndex: number, overrides: Partial<ReviewTicket> = {}): ReviewTicket {
  return {
    blueprint_id: "bp_1",
    slot_index: slotIndex,
    topic_key: "os.cpu_sched.rr.avg_waiting_time",
    reason: "không có nguồn trích dẫn",
    attempts: [],
    ...overrides,
  };
}

describe("ganttLayout", () => {
  it("quy đoạn thành phần trăm trục thời gian mà không đổi số backend trả", () => {
    const layout = ganttLayout(SEGMENTS);
    expect(layout.axisEnd).toBe(12);
    const first = layout.bars[0];
    expect(first).toEqual({
      process: "P1",
      start: 0,
      end: 3,
      leftPercent: 0,
      widthPercent: 25,
      colorIndex: 0,
    });
    const last = layout.bars[3];
    expect(last?.leftPercent).toBe(75);
    expect(last?.widthPercent).toBe(25);
  });

  it("để khoảng CPU rảnh trống (không có thanh nào phủ 7 đến 9)", () => {
    const layout = ganttLayout(SEGMENTS);
    const covered = layout.bars.some((bar) => bar.start < 9 && bar.end > 7);
    expect(covered).toBe(false);
  });

  it("gán cùng màu cho cùng tiến trình và liệt kê tiến trình theo thứ tự xuất hiện", () => {
    const layout = ganttLayout(SEGMENTS);
    expect(layout.processes).toEqual(["P1", "P2", "P3"]);
    expect(layout.bars[0]?.colorIndex).toBe(layout.bars[2]?.colorIndex);
    expect(layout.bars[1]?.colorIndex).toBe(1);
  });

  it("xoay vòng bảng màu khi nhiều tiến trình hơn số màu", () => {
    const many: GanttSegment[] = Array.from({ length: GANTT_PALETTE_SIZE + 2 }, (_, index) => ({
      process: `P${index + 1}`,
      start: index,
      end: index + 1,
    }));
    const layout = ganttLayout(many);
    expect(layout.bars[GANTT_PALETTE_SIZE]?.colorIndex).toBe(0);
    expect(layout.bars.every((bar) => bar.colorIndex < GANTT_PALETTE_SIZE)).toBe(true);
  });

  it("lấy các mốc trục là các đầu mút duy nhất, tăng dần", () => {
    expect(ganttLayout(SEGMENTS).ticks).toEqual([0, 3, 6, 7, 9, 12]);
  });

  it("không chia cho 0 khi không có đoạn nào", () => {
    expect(ganttLayout([])).toEqual({ bars: [], processes: [], axisEnd: 0, ticks: [] });
  });
});

describe("tickPercent", () => {
  it("quy mốc về phần trăm trục và an toàn với trục rỗng", () => {
    expect(tickPercent(3, 12)).toBe(25);
    expect(tickPercent(12, 12)).toBe(100);
    expect(tickPercent(5, 0)).toBe(0);
  });
});

describe("ganttDescription", () => {
  it("liệt kê từng đoạn bằng văn bản cho trình đọc màn hình", () => {
    const text = ganttDescription(SEGMENTS);
    expect(text).toContain("4 đoạn");
    expect(text).toContain("P1 từ 0 đến 3");
    expect(text).toContain("P3 từ 9 đến 12");
  });

  it("nói rõ khi không có đoạn", () => {
    expect(ganttDescription([])).toBe("Không có đoạn Gantt nào.");
  });
});

describe("optionViews", () => {
  it("giữ thứ tự A–D, đánh dấu đáp án đúng và gắn badge misconception cho phương án sai", () => {
    const views = optionViews(twin());
    expect(views.map((view) => view.key)).toEqual(["A", "B", "C", "D"]);
    expect(views.filter((view) => view.isCorrect).map((view) => view.key)).toEqual(["B"]);
    expect(views[1]?.misconception).toBeNull();
    expect(views[0]?.misconception?.misconception_code).toBe("WAIT_CONFUSED_WITH_TURNAROUND");
    expect(views.every((view) => view.isCorrect || view.misconception !== null)).toBe(true);
  });

  it("trả rỗng cho câu NUMERICAL", () => {
    expect(optionViews(twin({ format: "NUMERICAL", options: null, correct_key: null }))).toEqual([]);
  });
});

describe("formatNumericalAnswer", () => {
  it("chỉ định dạng số backend trả theo `decimals` và đơn vị", () => {
    expect(formatNumericalAnswer({ value: 6.25, tolerance_abs: 0.01, unit: "ms", decimals: 2 })).toBe("6.25 ms");
    expect(formatNumericalAnswer({ value: 6, tolerance_abs: 0, unit: null, decimals: 0 })).toBe("6");
  });
});

describe("groupTwinsBySlot", () => {
  it("gom twin theo slot, sắp theo slot_index và gắn ticket vào đúng slot", () => {
    const batch: TwinBatch = {
      batch_id: "batch_1",
      created_at: "2026-10-28T00:00:00Z",
      blueprint_id: "bp_1",
      base_seed: 1,
      twins: [
        twin({ twin_id: "a", slot_index: 2 }),
        twin({ twin_id: "b", slot_index: 1 }),
        twin({ twin_id: "c", slot_index: 2 }),
      ],
      review_tickets: [ticket(3), ticket(2, { reason: "một phần" })],
    };
    const groups = groupTwinsBySlot(batch);
    expect(groups.map((group) => group.slotIndex)).toEqual([1, 2, 3]);
    expect(groups[1]?.twins.map((item) => item.twin_id)).toEqual(["a", "c"]);
    expect(groups[1]?.ticket?.reason).toBe("một phần");
    expect(groups[2]?.twins).toEqual([]);
    expect(groups[2]?.ticket?.slot_index).toBe(3);
    expect(groups[0]?.ticket).toBeNull();
  });

  it("trả rỗng cho batch rỗng", () => {
    expect(
      groupTwinsBySlot({
        batch_id: "batch_0",
        created_at: "2026-10-28T00:00:00Z",
        blueprint_id: "bp",
        base_seed: 0,
        twins: [],
        review_tickets: [],
      }),
    ).toEqual([]);
  });
});

describe("nhãn hiển thị", () => {
  it("phủ mọi trạng thái review và nguồn gốc template", () => {
    expect(Object.keys(REVIEW_STATUS_LABELS).sort()).toEqual(
      ["APPROVED", "AUTO_FALLBACK", "AUTO_PASS", "NEEDS_REVIEW", "REJECTED"].sort(),
    );
    expect(Object.keys(PROVENANCE_LABELS).sort()).toEqual(["llm_generated", "precomputed_verified"]);
  });

  it("tóm tắt các lượt thử của một ticket", () => {
    const attempts: AttemptLog[] = [
      {
        attempt: 1,
        provenance: "llm_generated",
        template_sha256: null,
        outcome: "schema_invalid",
        seeds_tried: 0,
        assertion_histogram: {},
        feedback_sent: [],
      },
    ];
    expect(ticketAttemptSummary(ticket(1, { attempts }))).toEqual(["Lượt 1: schema_invalid"]);
    expect(ticketAttemptSummary(ticket(1))).toEqual([]);
  });

  it("rút gọn hash template", () => {
    expect(shortHash("0123456789abcdef0123456789abcdef")).toBe("0123456789ab");
  });
});

describe("clampTwinCount", () => {
  it("giữ số nguyên trong 1–10 và dùng mặc định 3 khi không hợp lệ", () => {
    expect(clampTwinCount(5)).toBe(5);
    expect(clampTwinCount(0)).toBe(1);
    expect(clampTwinCount(99)).toBe(10);
    expect(clampTwinCount(2.7)).toBe(2);
    expect(clampTwinCount(Number.NaN)).toBe(3);
    expect(clampTwinCount(Number.POSITIVE_INFINITY)).toBe(3);
  });
});
