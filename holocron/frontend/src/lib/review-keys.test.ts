/** Phím E/←/→ của màn Xem lại; không phím nào xác nhận trang (I5, quyết định 2026-10-29 câu 17 A: chỉ xác nhận bằng nút). */

import { describe, expect, it } from "vitest";

import { classifyReviewFocus, lineToEdit, type ReviewFocus, type ReviewKeyInput, reviewKeyAction } from "./review-keys";

const BASE: ReviewKeyInput = {
  key: "Enter",
  repeat: false,
  altKey: false,
  ctrlKey: false,
  metaKey: false,
  focus: "region",
  overlayOpen: false,
  busy: false,
  hasDraft: false,
  canEdit: true,
  hasPrevious: true,
  hasNext: true,
};

function action(change: Partial<ReviewKeyInput>) {
  return reviewKeyAction({ ...BASE, ...change });
}

describe("không có phím nào xác nhận trang", () => {
  const FOCUSES: readonly ReviewFocus[] = ["region", "control", "editable", "outside"];
  const KEYS = [
    "Enter",
    " ",
    "Spacebar",
    "NumpadEnter",
    "Tab",
    "Shift",
    "Control",
    "k",
    "K",
    "c",
    "C",
    "y",
    "Y",
    "?",
    "Escape",
    "ArrowDown",
    "ArrowUp",
    "PageDown",
    "e",
    "E",
    "ArrowLeft",
    "ArrowRight",
  ];
  const MODIFIERS: readonly Partial<ReviewKeyInput>[] = [
    {},
    { ctrlKey: true },
    { altKey: true },
    { metaKey: true },
    { repeat: true },
  ];

  it("mọi phím, mọi chỗ focus, mọi phím bổ trợ: chỉ có sửa dòng hoặc sang trang, không có xác nhận", () => {
    const seen = new Set<string>();
    for (const key of KEYS) {
      for (const focus of FOCUSES) {
        for (const modifier of MODIFIERS) {
          const result = action({ key, focus, ...modifier });
          if (result !== null) seen.add(result.type);
        }
      }
    }
    expect([...seen].sort()).toEqual(["edit", "next", "previous"]);
  });

  it("Enter không làm gì ở vùng xem lại hay trên nút (trình duyệt tự bấm nút đang focus)", () => {
    for (const focus of FOCUSES) expect(action({ key: "Enter", focus })).toBeNull();
    expect(action({ key: " ", focus: "control" })).toBeNull();
  });

  it("Shift hoặc Ctrl+K rồi Enter: không phím nào trong chuỗi xác nhận trang", () => {
    expect(action({ key: "Shift" })).toBeNull();
    expect(action({ key: "k", ctrlKey: true })).toBeNull();
    expect(action({ key: "Enter" })).toBeNull();
  });
});

describe("E và mũi tên", () => {
  it("E mở sửa khi sửa được, kể cả khi focus ở một nút trong vùng", () => {
    expect(action({ key: "e" })).toEqual({ type: "edit" });
    expect(action({ key: "E", focus: "control" })).toEqual({ type: "edit" });
    expect(action({ key: "e", canEdit: false })).toBeNull();
    expect(action({ key: "e", focus: "editable" })).toBeNull();
    expect(action({ key: "e", busy: true })).toBeNull();
    expect(action({ key: "e", hasDraft: true })).toBeNull();
    expect(action({ key: "e", focus: "outside" })).toBeNull();
  });

  it("←/→ chỉ chạy trong vùng xem lại, khi có trang cần xem ở phía đó và không có nháp", () => {
    expect(action({ key: "ArrowLeft" })).toEqual({ type: "previous" });
    expect(action({ key: "ArrowRight", focus: "control" })).toEqual({ type: "next" });
    expect(action({ key: "ArrowLeft", hasPrevious: false })).toBeNull();
    expect(action({ key: "ArrowRight", hasNext: false })).toBeNull();
    expect(action({ key: "ArrowRight", focus: "outside" })).toBeNull();
    expect(action({ key: "ArrowRight", focus: "editable" })).toBeNull();
    expect(action({ key: "ArrowRight", hasDraft: true })).toBeNull();
    expect(action({ key: "ArrowRight", repeat: true })).toBeNull();
  });

  it("phím khác không làm gì", () => {
    expect(action({ key: " " })).toBeNull();
    expect(action({ key: "Escape" })).toBeNull();
  });
});

describe("classifyReviewFocus", () => {
  const inside = { insideRegion: true, isRegion: false, isContentEditable: false, role: null };

  it("phân loại nơi đang focus", () => {
    expect(classifyReviewFocus({ ...inside, isRegion: true, tagName: "SECTION" })).toBe("region");
    expect(classifyReviewFocus({ ...inside, tagName: "P" })).toBe("region");
    expect(classifyReviewFocus({ ...inside, tagName: "INPUT" })).toBe("editable");
    expect(classifyReviewFocus({ ...inside, tagName: "textarea" })).toBe("editable");
    expect(classifyReviewFocus({ ...inside, tagName: "SELECT" })).toBe("editable");
    expect(classifyReviewFocus({ ...inside, tagName: "DIV", isContentEditable: true })).toBe("editable");
    expect(classifyReviewFocus({ ...inside, tagName: "DIV", role: "listbox" })).toBe("editable");
    expect(classifyReviewFocus({ ...inside, tagName: "BUTTON" })).toBe("control");
    expect(classifyReviewFocus({ ...inside, tagName: "A" })).toBe("control");
    expect(classifyReviewFocus({ ...inside, tagName: "DIV", role: "button" })).toBe("control");
    expect(
      classifyReviewFocus({
        insideRegion: false,
        isRegion: false,
        tagName: "BUTTON",
        isContentEditable: false,
        role: "tab",
      }),
    ).toBe("outside");
  });
});

describe("lineToEdit", () => {
  it("dòng nghi ngờ đầu tiên, không có thì dòng đầu, không có dòng thì null", () => {
    expect(
      lineToEdit([
        { lineIndex: 0, flagged: false },
        { lineIndex: 1, flagged: true },
      ]),
    ).toBe(1);
    expect(lineToEdit([{ lineIndex: 4, flagged: false }])).toBe(4);
    expect(lineToEdit([])).toBeNull();
  });
});

describe("phím bổ trợ và lớp phủ chặn E, ←, →", () => {
  const ACTIVE_KEYS = ["e", "E", "ArrowLeft", "ArrowRight"];
  const BLOCKERS: readonly [string, Partial<ReviewKeyInput>][] = [
    ["hộp thoại hay bảng lệnh đang mở", { overlayOpen: true }],
    ["Alt", { altKey: true }],
    ["Ctrl", { ctrlKey: true }],
    ["Meta", { metaKey: true }],
  ];

  it("không có bổ trợ thì cả bốn phím đều có hành động (đối chứng)", () => {
    for (const key of ACTIVE_KEYS) expect(action({ key })).not.toBeNull();
  });

  it.each(BLOCKERS)("%s ⇒ E, ←, → không làm gì", (_name, blocker) => {
    for (const key of ACTIVE_KEYS) expect(action({ key, ...blocker })).toBeNull();
  });

  it("← có nháp chưa lưu ⇒ không sang trang", () => {
    expect(action({ key: "ArrowLeft", hasDraft: true })).toBeNull();
  });
});

describe("classifyReviewFocus chuẩn hoá role", () => {
  const inside = { insideRegion: true, isRegion: false, isContentEditable: false, tagName: "DIV" };

  it("role có khoảng trắng đầu/cuối hoặc viết hoa vẫn được nhận", () => {
    expect(classifyReviewFocus({ ...inside, role: " Textbox " })).toBe("editable");
    expect(classifyReviewFocus({ ...inside, role: " textbox " })).toBe("editable");
    expect(classifyReviewFocus({ ...inside, role: "Textbox" })).toBe("editable");
    expect(classifyReviewFocus({ ...inside, role: "BUTTON" })).toBe("control");
    expect(classifyReviewFocus({ ...inside, role: " button " })).toBe("control");
  });
});
