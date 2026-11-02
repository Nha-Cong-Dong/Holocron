import { describe, expect, it } from "vitest";

import { clampIndex, type DropdownKeyState, dropdownKeyAction } from "./dropdown-keys";

const closed: DropdownKeyState = { open: false, active: -1, selected: 2, count: 4 };
const open: DropdownKeyState = { open: true, active: 1, selected: 2, count: 4 };

describe("clampIndex", () => {
  it("kẹp chỉ số vào khoảng của bảng", () => {
    expect(clampIndex(-1, 4)).toBe(0);
    expect(clampIndex(2, 4)).toBe(2);
    expect(clampIndex(9, 4)).toBe(3);
    expect(clampIndex(Number.NaN, 4)).toBe(0);
    expect(clampIndex(1.5, 4)).toBe(0);
  });
});

describe("dropdownKeyAction khi bảng đóng", () => {
  it("↓, ↑, Enter, Space mở ở dòng đang chọn", () => {
    for (const key of ["ArrowDown", "ArrowUp", "Enter", " "]) {
      expect(dropdownKeyAction(key, closed)).toEqual({ type: "open", active: 2 });
    }
  });

  it("chưa chọn dòng nào thì mở ở dòng đầu", () => {
    expect(dropdownKeyAction("ArrowDown", { ...closed, selected: -1 })).toEqual({ type: "open", active: 0 });
  });

  it("Home mở ở dòng đầu, End ở dòng cuối", () => {
    expect(dropdownKeyAction("Home", closed)).toEqual({ type: "open", active: 0 });
    expect(dropdownKeyAction("End", closed)).toEqual({ type: "open", active: 3 });
  });

  it("phím khác và Esc không làm gì", () => {
    expect(dropdownKeyAction("a", closed)).toBeNull();
    expect(dropdownKeyAction("Escape", closed)).toBeNull();
    expect(dropdownKeyAction("Tab", closed)).toBeNull();
  });
});

describe("dropdownKeyAction khi bảng mở", () => {
  it("↓/↑ đi một dòng và dừng ở hai đầu", () => {
    expect(dropdownKeyAction("ArrowDown", open)).toEqual({ type: "move", active: 2 });
    expect(dropdownKeyAction("ArrowUp", open)).toEqual({ type: "move", active: 0 });
    expect(dropdownKeyAction("ArrowDown", { ...open, active: 3 })).toEqual({ type: "move", active: 3 });
    expect(dropdownKeyAction("ArrowUp", { ...open, active: 0 })).toEqual({ type: "move", active: 0 });
  });

  it("chưa trỏ dòng nào thì ↓/↑ trỏ vào dòng đang chọn", () => {
    expect(dropdownKeyAction("ArrowDown", { ...open, active: -1 })).toEqual({ type: "move", active: 2 });
    expect(dropdownKeyAction("ArrowUp", { ...open, active: -1 })).toEqual({ type: "move", active: 2 });
  });

  it("Home/End về đầu/cuối", () => {
    expect(dropdownKeyAction("Home", open)).toEqual({ type: "move", active: 0 });
    expect(dropdownKeyAction("End", open)).toEqual({ type: "move", active: 3 });
  });

  it("Enter và Space chọn dòng đang trỏ", () => {
    expect(dropdownKeyAction("Enter", open)).toEqual({ type: "select", index: 1 });
    expect(dropdownKeyAction(" ", open)).toEqual({ type: "select", index: 1 });
  });

  it("Esc đóng và trả focus, Tab đóng và để focus đi tiếp", () => {
    expect(dropdownKeyAction("Escape", open)).toEqual({ type: "close", restoreFocus: true });
    expect(dropdownKeyAction("Tab", open)).toEqual({ type: "close", restoreFocus: false });
  });

  it("chỉ số trỏ vượt khoảng bị kẹp lại trước khi xử lý", () => {
    expect(dropdownKeyAction("Enter", { ...open, active: 9 })).toEqual({ type: "select", index: 3 });
  });

  it("phím khác không làm gì", () => {
    expect(dropdownKeyAction("a", open)).toBeNull();
    expect(dropdownKeyAction("ArrowLeft", open)).toBeNull();
  });
});

describe("dropdownKeyAction với bảng rỗng", () => {
  it("không mở bảng rỗng", () => {
    expect(dropdownKeyAction("ArrowDown", { ...closed, count: 0 })).toBeNull();
  });

  it("bảng rỗng đang mở vẫn đóng được bằng Esc và Tab", () => {
    const empty = { ...open, count: 0 };
    expect(dropdownKeyAction("Escape", empty)).toEqual({ type: "close", restoreFocus: true });
    expect(dropdownKeyAction("Tab", empty)).toEqual({ type: "close", restoreFocus: false });
    expect(dropdownKeyAction("Enter", empty)).toBeNull();
  });
});
