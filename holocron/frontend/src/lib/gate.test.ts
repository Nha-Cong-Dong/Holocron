import { describe, expect, it } from "vitest";

import type { GateIssue, GateReport, GateStatus, IngestionItem } from "@/types/holocron";

import { canApprove, cellKey, cellText, groupIssues, hasBlocking, rowCount } from "./gate";

function issue(overrides: Partial<GateIssue> = {}): GateIssue {
  return {
    code: "DUAL_PASS_MISMATCH",
    severity: "blocking",
    message_vi: "Hai lượt đọc khác nhau.",
    row: 1,
    col: 2,
    alternatives: [],
    ...overrides,
  };
}

function report(status: GateStatus, issues: GateIssue[]): GateReport {
  return { table_id: "tbl_x", table_version: 1, status, issues };
}

function item(overrides: Partial<IngestionItem> = {}): IngestionItem {
  return {
    item_id: "ing_x",
    source_doc_id: "doc_x",
    page_number: 1,
    topic_key: "os.cpu_sched.rr.avg_waiting_time",
    stem_latex: "Round Robin với $q = 4$ ms.",
    options: { A: "1", B: "2", C: "3", D: "4" },
    table_primary: {
      table_id: "tbl_x",
      page_number: 1,
      version: 1,
      headers: ["Tiến trình", "Thời điểm đến", "Burst time"],
      cells: [
        { row: 0, col: 0, text: "P1", confidence: 1, verified_by_human: false },
        { row: 1, col: 2, text: "9", confidence: 1, verified_by_human: false },
      ],
    },
    table_secondary: null,
    page_text_layer: null,
    solver_inputs: { quantum: 4 },
    non_cloneable: false,
    non_cloneable_reason: null,
    problem: null,
    self_solve: null,
    report: report("AUTO_PASS", []),
    audits: [],
    approved: false,
    ...overrides,
  };
}

describe("canApprove (bất biến I5)", () => {
  it("cho phép khi AUTO_PASS, chưa duyệt, không non-cloneable", () => {
    expect(canApprove(item())).toBe(true);
  });

  it("cho phép khi chỉ có WARNING (NEEDS_REVIEW)", () => {
    const warning = issue({ severity: "warning", code: "LOW_CONFIDENCE" });
    expect(canApprove(item({ report: report("NEEDS_REVIEW", [warning]) }))).toBe(true);
  });

  it("chặn khi status BLOCKED", () => {
    expect(canApprove(item({ report: report("BLOCKED", [issue()]) }))).toBe(false);
  });

  it("chặn khi còn issue blocking dù status báo khác (phòng thủ)", () => {
    expect(canApprove(item({ report: report("AUTO_PASS", [issue()]) }))).toBe(false);
  });

  it("chặn khi chưa có report", () => {
    expect(canApprove(item({ report: null }))).toBe(false);
  });

  it("chặn khi đã approve hoặc bị gắn non-cloneable", () => {
    expect(canApprove(item({ approved: true }))).toBe(false);
    expect(canApprove(item({ non_cloneable: true, non_cloneable_reason: "đề cần hình vẽ" }))).toBe(false);
  });
});

describe("hasBlocking", () => {
  it("coi report null là blocking", () => {
    expect(hasBlocking(null)).toBe(true);
  });

  it("false khi không có issue blocking", () => {
    expect(hasBlocking(report("AUTO_PASS", []))).toBe(false);
  });
});

describe("groupIssues", () => {
  it("gom theo ô và tách issue cấp bảng", () => {
    const cellIssue = issue();
    const tableIssue = issue({ code: "SELF_SOLVE_MISMATCH", row: null, col: null });
    const grouped = groupIssues(report("BLOCKED", [cellIssue, tableIssue]));
    expect(grouped.byCell.get(cellKey(1, 2))).toEqual([cellIssue]);
    expect(grouped.tableLevel).toEqual([tableIssue]);
  });

  it("xếp nhiều issue của cùng một ô vào cùng bucket", () => {
    const first = issue();
    const second = issue({ code: "LOW_CONFIDENCE", severity: "warning" });
    expect(groupIssues(report("BLOCKED", [first, second])).byCell.get(cellKey(1, 2))).toEqual([first, second]);
  });

  it("report null cho kết quả rỗng", () => {
    const grouped = groupIssues(null);
    expect(grouped.byCell.size).toBe(0);
    expect(grouped.tableLevel).toEqual([]);
  });
});

describe("rowCount và cellText", () => {
  it("rowCount lấy dòng lớn nhất + 1", () => {
    expect(rowCount(item().table_primary)).toBe(2);
  });

  it("cellText trả null khi ô không tồn tại", () => {
    const table = item().table_primary;
    expect(cellText(table, 1, 2)).toBe("9");
    expect(cellText(table, 5, 5)).toBeNull();
  });
});
