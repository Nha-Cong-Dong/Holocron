import { describe, expect, it } from "vitest";

import type { SelfSolveStatus } from "@/types/holocron";

import { selfSolveStatusLabel } from "./gate";

describe("selfSolveStatusLabel", () => {
  it("mọi trạng thái có nhãn tiếng Việt, không lộ mã enum", () => {
    const statuses: SelfSolveStatus[] = ["MATCHED", "NO_MATCH", "AMBIGUOUS", "SKIPPED"];
    const labels = statuses.map(selfSolveStatusLabel);
    expect(new Set(labels).size).toBe(statuses.length);
    for (const label of labels) {
      expect(label).not.toMatch(/MATCHED|NO_MATCH|AMBIGUOUS|SKIPPED|_/);
      expect(label.length).toBeGreaterThan(0);
    }
  });

  it("mã lạ từ backend mới hơn không lộ ra và không ném lỗi", () => {
    expect(selfSolveStatusLabel("PARTIAL")).toBe("chưa rõ kết quả");
    expect(selfSolveStatusLabel("")).toBe("chưa rõ kết quả");
  });
});
