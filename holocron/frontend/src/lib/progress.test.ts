import { describe, expect, it } from "vitest";

import { progressAriaMax, progressAriaNow, progressRatio, progressValueText } from "./progress";

describe("progressRatio", () => {
  it("chia value cho max và kẹp trong [0, 1]", () => {
    expect(progressRatio(12, 40)).toBe(0.3);
    expect(progressRatio(0, 40)).toBe(0);
    expect(progressRatio(40, 40)).toBe(1);
    expect(progressRatio(50, 40)).toBe(1);
    expect(progressRatio(-3, 40)).toBe(0);
  });

  it("max không dương hoặc số không hữu hạn ⇒ 0", () => {
    expect(progressRatio(3, 0)).toBe(0);
    expect(progressRatio(3, -1)).toBe(0);
    expect(progressRatio(Number.NaN, 10)).toBe(0);
    expect(progressRatio(3, Number.POSITIVE_INFINITY)).toBe(0);
  });
});

describe("progressAriaMax", () => {
  it("giữ max dương hữu hạn, còn lại là 0", () => {
    expect(progressAriaMax(40)).toBe(40);
    expect(progressAriaMax(0)).toBe(0);
    expect(progressAriaMax(-5)).toBe(0);
    expect(progressAriaMax(Number.NaN)).toBe(0);
  });
});

describe("progressAriaNow", () => {
  it("giữ nguyên value trong khoảng, ngoài khoảng lấy biên", () => {
    expect(progressAriaNow(12, 40)).toBe(12);
    expect(progressAriaNow(50, 40)).toBe(40);
    expect(progressAriaNow(-2, 40)).toBe(0);
    expect(progressAriaNow(Number.NaN, 40)).toBe(0);
    expect(progressAriaNow(5, 0)).toBe(0);
  });
});

describe("progressValueText", () => {
  it("ghép đúng hai số được truyền vào và đơn vị", () => {
    expect(progressValueText(12, 40, "trang")).toBe("12/40 trang");
    expect(progressValueText(3, 10, " câu ")).toBe("3/10 câu");
  });

  it("đơn vị rỗng chỉ còn x/y", () => {
    expect(progressValueText(3, 10, "")).toBe("3/10");
  });

  it("không làm tròn hay đổi số", () => {
    expect(progressValueText(2.5, 7, "file")).toBe("2.5/7 file");
    expect(progressValueText(50, 40, "trang")).toBe("50/40 trang");
  });

  it("số không hữu hạn hiện dấu ?", () => {
    expect(progressValueText(Number.NaN, 10, "câu")).toBe("?/10 câu");
  });
});
