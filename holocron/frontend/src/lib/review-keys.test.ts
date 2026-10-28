/** Phím Enter/E/←/→ của màn Xem lại: Enter chỉ xác nhận khi đủ điều kiện (I5: xác nhận phải có chủ ý). */

import { describe, expect, it } from "vitest";

import { classifyReviewFocus, lineToEdit, type ReviewKeyInput, reviewKeyAction } from "./review-keys";

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
  confirmBlock: null,
  hasSkippedLines: false,
  canEdit: true,
  hasPrevious: true,
  hasNext: true,
};

function action(change: Partial<ReviewKeyInput>) {
  return reviewKeyAction({ ...BASE, ...change });
}

describe("Enter xác nhận trang", () => {
  it("xác nhận khi focus ở vùng xem lại và đủ điều kiện", () => {
    expect(action({})).toEqual({ type: "confirm" });
  });

  it.each<[string, Partial<ReviewKeyInput>]>([
    ["focus ở nút khác (trình duyệt tự bấm nút đó)", { focus: "control" }],
    ["focus ở ô nhập", { focus: "editable" }],
    ["focus ngoài vùng (hàng tab)", { focus: "outside" }],
    ["phím giữ lặp lại", { repeat: true }],
    ["đang có thao tác ghi", { busy: true }],
    ["còn nháp chưa lưu", { hasDraft: true }],
    ["trang đang đọc", { confirmBlock: "pending" }],
    ["trang đọc lỗi", { confirmBlock: "failed" }],
    ["trang đã xác nhận", { confirmBlock: "confirmed" }],
    ["trang không có chữ", { confirmBlock: "no_text" }],
    ["trạng thái lạ", { confirmBlock: "unknown" }],
    ["có dòng sai dạng bị bỏ", { hasSkippedLines: true }],
    ["có hộp thoại đang mở", { overlayOpen: true }],
    ["Ctrl+Enter", { ctrlKey: true }],
    ["Alt+Enter", { altKey: true }],
    ["Meta+Enter", { metaKey: true }],
  ])("không xác nhận khi %s", (_name, change) => {
    expect(action(change)).toBeNull();
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
