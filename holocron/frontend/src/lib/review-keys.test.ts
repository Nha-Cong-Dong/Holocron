/** Phím Enter/E/←/→ của màn Xem lại: Enter chỉ xác nhận khi đủ điều kiện (I5: xác nhận phải có chủ ý). */

import { describe, expect, it } from "vitest";

import {
  armsReview,
  classifyReviewFocus,
  lineToEdit,
  REVIEW_ENTER_GUARD_MS,
  type ReviewKeyInput,
  reviewKeyAction,
} from "./review-keys";

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
  armed: true,
  msSinceShown: 5000,
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

describe("Enter sau khi trang mới hiện (I5: không xác nhận trang chưa đọc)", () => {
  it("Enter ngay sau khi tự chuyển sang trang kế ⇒ không xác nhận, kể cả khi đã tương tác", () => {
    expect(action({ armed: true, msSinceShown: 0 })).toBeNull();
    expect(action({ armed: true, msSinceShown: REVIEW_ENTER_GUARD_MS - 1 })).toBeNull();
  });

  it("trang mới hiện, người dùng chưa tương tác ⇒ Enter không xác nhận dù đã lâu", () => {
    expect(action({ armed: false, msSinceShown: 60_000 })).toBeNull();
  });

  it("người dùng đã tương tác sau khi trang hiện và đủ thời gian ⇒ Enter xác nhận", () => {
    expect(action({ armed: true, msSinceShown: REVIEW_ENTER_GUARD_MS })).toEqual({ type: "confirm" });
  });

  it("sau khi lưu dòng (trang vẽ lại, bắt đầu lại chưa tương tác) ⇒ Enter không tự xác nhận", () => {
    expect(action({ armed: false, msSinceShown: 0 })).toBeNull();
    expect(action({ armed: false, msSinceShown: REVIEW_ENTER_GUARD_MS * 10 })).toBeNull();
  });

  it("E và mũi tên không cần mở khoá (không ghi gì)", () => {
    expect(action({ key: "e", armed: false, msSinceShown: 0 })).toEqual({ type: "edit" });
    expect(action({ key: "ArrowRight", armed: false, msSinceShown: 0 })).toEqual({ type: "next" });
  });
});

describe("armsReview", () => {
  it("bấm chuột, phím khác Enter và focus do người dùng chuyển vào thì mở khoá", () => {
    expect(armsReview({ type: "pointerdown" })).toBe(true);
    expect(armsReview({ type: "keydown", key: "Tab", repeat: false })).toBe(true);
    expect(armsReview({ type: "keydown", key: "ArrowDown", repeat: false })).toBe(true);
    expect(armsReview({ type: "focusin", fromElement: true })).toBe(true);
  });

  it("Enter, phím giữ lặp lại và focus đặt bằng code không mở khoá", () => {
    expect(armsReview({ type: "keydown", key: "Enter", repeat: false })).toBe(false);
    expect(armsReview({ type: "keydown", key: "Tab", repeat: true })).toBe(false);
    expect(armsReview({ type: "focusin", fromElement: false })).toBe(false);
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
