import { describe, expect, it } from "vitest";

import {
  GLOBAL_SCOPE,
  type KeyInput,
  optionKeyFromKey,
  resolveShortcut,
  SHORTCUT_HELP,
  type ShortcutContext,
  type ShortcutScope,
  type TargetKind,
  targetKindOf,
} from "./shortcuts";

function key(value: string, overrides: Partial<KeyInput> = {}): KeyInput {
  return { key: value, ctrlKey: false, metaKey: false, altKey: false, target: "other", ...overrides };
}

function ctx(scope: ShortcutScope = GLOBAL_SCOPE, overlayOpen = false): ShortcutContext {
  return { scope, overlayOpen };
}

const UNANSWERED: ShortcutScope = { kind: "mcq", phase: "unanswered" };
const SELECTED: ShortcutScope = { kind: "mcq", phase: "selected" };
const SUBMITTED: ShortcutScope = { kind: "mcq", phase: "submitted" };

describe("targetKindOf", () => {
  it("phân loại ô nhập, nút và phần tử khác", () => {
    const cases: [Parameters<typeof targetKindOf>[0], TargetKind][] = [
      [null, "other"],
      [{ tagName: "TEXTAREA", isContentEditable: false, inputType: null }, "editable"],
      [{ tagName: "select", isContentEditable: false, inputType: null }, "editable"],
      [{ tagName: "INPUT", isContentEditable: false, inputType: "text" }, "editable"],
      [{ tagName: "INPUT", isContentEditable: false, inputType: "number" }, "editable"],
      [{ tagName: "INPUT", isContentEditable: false, inputType: null }, "editable"],
      [{ tagName: "INPUT", isContentEditable: false, inputType: "checkbox" }, "interactive"],
      [{ tagName: "INPUT", isContentEditable: false, inputType: "SUBMIT" }, "interactive"],
      [{ tagName: "DIV", isContentEditable: true, inputType: null }, "editable"],
      [{ tagName: "BUTTON", isContentEditable: false, inputType: null }, "interactive"],
      [{ tagName: "A", isContentEditable: false, inputType: null }, "interactive"],
      [{ tagName: "BODY", isContentEditable: false, inputType: null }, "other"],
    ];
    for (const [target, expected] of cases) {
      expect(targetKindOf(target)).toBe(expected);
    }
  });
});

describe("optionKeyFromKey", () => {
  it("1–4 và A–D (mọi kiểu chữ) thành khoá phương án", () => {
    expect(["1", "2", "3", "4"].map(optionKeyFromKey)).toEqual(["A", "B", "C", "D"]);
    expect(["a", "B", "c", "D"].map(optionKeyFromKey)).toEqual(["A", "B", "C", "D"]);
    expect(["5", "0", "e", "Enter", ""].map(optionKeyFromKey)).toEqual([null, null, null, null, null]);
  });
});

describe("resolveShortcut: phím toàn cục", () => {
  it("Ctrl+K hoặc Cmd+K mở hộp lệnh ở mọi nơi, kể cả trong ô nhập và khi đang mở hộp thoại", () => {
    expect(resolveShortcut(key("k", { ctrlKey: true }), ctx())).toEqual({ type: "open_palette" });
    expect(resolveShortcut(key("K", { metaKey: true, target: "editable" }), ctx())).toEqual({ type: "open_palette" });
    expect(resolveShortcut(key("k", { ctrlKey: true }), ctx(GLOBAL_SCOPE, true))).toEqual({ type: "open_palette" });
    expect(resolveShortcut(key("k", { ctrlKey: true, altKey: true }), ctx())).toBeNull();
  });

  it("Esc luôn là đóng hoặc huỷ", () => {
    expect(resolveShortcut(key("Escape"), ctx())).toEqual({ type: "close" });
    expect(resolveShortcut(key("Escape", { target: "editable" }), ctx())).toEqual({ type: "close" });
    expect(resolveShortcut(key("Escape"), ctx(SUBMITTED, true))).toEqual({ type: "close" });
  });

  it("? mở bảng phím tắt, trừ khi đang gõ trong ô nhập", () => {
    expect(resolveShortcut(key("?"), ctx())).toEqual({ type: "show_help" });
    expect(resolveShortcut(key("?", { target: "interactive" }), ctx())).toEqual({ type: "show_help" });
    expect(resolveShortcut(key("?", { target: "editable" }), ctx())).toBeNull();
    expect(resolveShortcut(key("?", { ctrlKey: true }), ctx())).toBeNull();
  });

  it("khi đang mở hộp thoại, phím khác không có tác dụng", () => {
    expect(resolveShortcut(key("?"), ctx(GLOBAL_SCOPE, true))).toBeNull();
    expect(resolveShortcut(key("1"), ctx(UNANSWERED, true))).toBeNull();
    expect(resolveShortcut(key("Enter"), ctx(SELECTED, true))).toBeNull();
  });

  it("ở màn không phải luyện tập, phím chọn phương án và Enter không làm gì", () => {
    for (const value of ["1", "a", "n", "c", "Enter"]) {
      expect(resolveShortcut(key(value), ctx())).toBeNull();
    }
  });
});

describe("resolveShortcut: câu trắc nghiệm", () => {
  it("chọn phương án khi chưa nộp", () => {
    expect(resolveShortcut(key("3"), ctx(UNANSWERED))).toEqual({ type: "select_option", key: "C" });
    expect(resolveShortcut(key("b"), ctx(SELECTED))).toEqual({ type: "select_option", key: "B" });
  });

  it("phím một ký tự bị bỏ qua trong ô nhập hoặc khi có phím bổ trợ", () => {
    expect(resolveShortcut(key("1", { target: "editable" }), ctx(UNANSWERED))).toBeNull();
    expect(resolveShortcut(key("a", { ctrlKey: true }), ctx(UNANSWERED))).toBeNull();
    expect(resolveShortcut(key("a", { altKey: true }), ctx(UNANSWERED))).toBeNull();
    expect(resolveShortcut(key("n", { target: "editable" }), ctx(SUBMITTED))).toBeNull();
  });

  it("Enter: nộp khi đã chọn, sang câu kế khi đã nộp, không làm gì khi chưa chọn", () => {
    expect(resolveShortcut(key("Enter"), ctx(SELECTED))).toEqual({ type: "submit" });
    expect(resolveShortcut(key("Enter"), ctx(SUBMITTED))).toEqual({ type: "next" });
    expect(resolveShortcut(key("Enter"), ctx(UNANSWERED))).toBeNull();
  });

  it("Enter trên nút hoặc ô nhập giữ hành vi gốc", () => {
    expect(resolveShortcut(key("Enter", { target: "interactive" }), ctx(SELECTED))).toBeNull();
    expect(resolveShortcut(key("Enter", { target: "editable" }), ctx(SUBMITTED))).toBeNull();
  });

  it("đã nộp: N sang câu kế, C mở trích dẫn, không đổi phương án nữa", () => {
    expect(resolveShortcut(key("n"), ctx(SUBMITTED))).toEqual({ type: "next" });
    expect(resolveShortcut(key("N"), ctx(SUBMITTED))).toEqual({ type: "next" });
    expect(resolveShortcut(key("c"), ctx(SUBMITTED))).toEqual({ type: "open_citation" });
    expect(resolveShortcut(key("1"), ctx(SUBMITTED))).toBeNull();
    expect(resolveShortcut(key("a"), ctx(SUBMITTED))).toBeNull();
  });

  it("chưa nộp: C là chọn phương án C, không phải mở trích dẫn", () => {
    expect(resolveShortcut(key("c"), ctx(UNANSWERED))).toEqual({ type: "select_option", key: "C" });
    expect(resolveShortcut(key("n"), ctx(UNANSWERED))).toBeNull();
  });
});

describe("resolveShortcut: câu tự luận", () => {
  it("Ctrl+Enter nộp ngay trong ô trả lời; chữ thường không chọn gì", () => {
    expect(resolveShortcut(key("Enter", { ctrlKey: true, target: "editable" }), ctx({ kind: "essay" }))).toEqual({
      type: "submit_essay",
    });
    expect(resolveShortcut(key("Enter", { target: "editable" }), ctx({ kind: "essay" }))).toBeNull();
    expect(resolveShortcut(key("a", { target: "editable" }), ctx({ kind: "essay" }))).toBeNull();
    expect(resolveShortcut(key("Enter", { ctrlKey: true }), ctx())).toBeNull();
  });
});

describe("SHORTCUT_HELP", () => {
  it("liệt kê đủ các phím của nền tảng", () => {
    const keys = SHORTCUT_HELP.flatMap((entry) => entry.keys);
    for (const expected of ["Ctrl+K", "?", "Esc", "1–4", "A–D", "Enter", "Ctrl+Enter", "N", "C"]) {
      expect(keys).toContain(expected);
    }
  });
});
