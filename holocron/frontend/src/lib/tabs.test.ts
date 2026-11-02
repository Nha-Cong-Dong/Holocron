import { describe, expect, it } from "vitest";

import { DEFAULT_SUBJECT_TAB, paneDirection, parseSubjectTab, SUBJECT_TABS, tabIndexForKey } from "./tabs";

describe("tabIndexForKey", () => {
  it("←/→ đi vòng, Home về đầu, End về cuối", () => {
    expect(tabIndexForKey("ArrowRight", 0, 3)).toBe(1);
    expect(tabIndexForKey("ArrowRight", 2, 3)).toBe(0);
    expect(tabIndexForKey("ArrowLeft", 0, 3)).toBe(2);
    expect(tabIndexForKey("ArrowLeft", 2, 3)).toBe(1);
    expect(tabIndexForKey("Home", 2, 3)).toBe(0);
    expect(tabIndexForKey("End", 0, 3)).toBe(2);
  });

  it("phím khác (kể cả ↑/↓, Tab, Enter) không xử lý", () => {
    for (const key of ["ArrowUp", "ArrowDown", "Tab", "Enter", " ", "a"]) {
      expect(tabIndexForKey(key, 1, 3)).toBeNull();
    }
  });

  it("hàng rỗng không xử lý; chỉ số ngoài khoảng được kẹp lại", () => {
    expect(tabIndexForKey("ArrowRight", 0, 0)).toBeNull();
    expect(tabIndexForKey("ArrowRight", 7, 3)).toBe(0);
    expect(tabIndexForKey("ArrowLeft", -4, 3)).toBe(2);
    expect(tabIndexForKey("ArrowRight", 0, 1)).toBe(0);
  });
});

describe("paneDirection", () => {
  it("sang phải hoặc đứng yên ⇒ r, sang trái ⇒ l", () => {
    expect(paneDirection(0, 2)).toBe("r");
    expect(paneDirection(1, 1)).toBe("r");
    expect(paneDirection(2, 0)).toBe("l");
  });
});

describe("parseSubjectTab", () => {
  it("nhận đúng ba phần, theo thứ tự Tài liệu · Khái niệm · Ôn tập", () => {
    expect(SUBJECT_TABS).toEqual(["documents", "concepts", "practice"]);
    expect(parseSubjectTab("concepts")).toBe("concepts");
    expect(parseSubjectTab(" practice ")).toBe("practice");
    expect(parseSubjectTab("documents")).toBe("documents");
  });

  it("thiếu hoặc giá trị lạ ⇒ Tài liệu", () => {
    expect(DEFAULT_SUBJECT_TAB).toBe("documents");
    expect(parseSubjectTab(null)).toBe("documents");
    expect(parseSubjectTab("")).toBe("documents");
    expect(parseSubjectTab("Concepts")).toBe("documents");
    expect(parseSubjectTab("chunks")).toBe("documents");
  });
});
