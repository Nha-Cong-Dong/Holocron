/**
 * Ánh xạ phím tắt thuần (kế hoạch UI mục 5.7). Component chỉ mô tả phím vừa bấm và ngữ cảnh hiện tại, rồi thực hiện
 * hành động hàm này trả về. Phím một ký tự không có tác dụng khi focus ở ô nhập, để gõ chữ không chọn nhầm phương án.
 */

import type { OptionKey } from "@/types/holocron";

/** Loại phần tử đang giữ focus: ô nhập (`input`, `textarea`, `select`, `contenteditable`), nút/liên kết, hay khác. */
export type TargetKind = "editable" | "interactive" | "other";

export interface KeyInput {
  key: string;
  ctrlKey: boolean;
  metaKey: boolean;
  altKey: boolean;
  target: TargetKind;
}

/**
 * Ngữ cảnh của màn đang mở. Màn luyện tập (F4) truyền `mcq` hoặc `essay`; các màn khác dùng `global`.
 * `essay.phase` không ghi thì là `answering` (đang viết câu trả lời); `graded` là đã có kết quả chấm.
 */
export type ShortcutScope =
  | { kind: "global" }
  | { kind: "mcq"; phase: "unanswered" | "selected" | "submitted" }
  | { kind: "essay"; phase?: "answering" | "graded" };

/** Câu đang mở đã nộp (trắc nghiệm) hoặc đã chấm (tự luận): `N` sang câu kế, `C` mở trích dẫn. */
function isAnsweredScope(scope: ShortcutScope): boolean {
  return (scope.kind === "mcq" && scope.phase === "submitted") || (scope.kind === "essay" && scope.phase === "graded");
}

export interface ShortcutContext {
  /** Đang mở hộp thoại hoặc hộp lệnh: chỉ còn `Esc` và `Ctrl+K` có tác dụng. */
  overlayOpen: boolean;
  scope: ShortcutScope;
}

export type ShortcutAction =
  | { type: "open_palette" }
  | { type: "show_help" }
  | { type: "close" }
  | { type: "select_option"; key: OptionKey }
  | { type: "submit" }
  | { type: "next" }
  | { type: "open_citation" }
  | { type: "submit_essay" };

export const GLOBAL_SCOPE: ShortcutScope = { kind: "global" };

/** Mô tả tối thiểu của phần tử đang giữ focus, để hàm phân loại không phụ thuộc DOM. */
export interface TargetDescriptor {
  tagName: string;
  isContentEditable: boolean;
  /** Thuộc tính `type` của `input`, nếu có. */
  inputType: string | null;
}

const BUTTON_LIKE_INPUTS: readonly string[] = ["button", "submit", "reset", "checkbox", "radio", "file", "color"];
const INTERACTIVE_TAGS: readonly string[] = ["BUTTON", "A", "SUMMARY"];

export function targetKindOf(target: TargetDescriptor | null): TargetKind {
  if (target === null) return "other";
  if (target.isContentEditable) return "editable";
  const tag = target.tagName.toUpperCase();
  if (tag === "TEXTAREA" || tag === "SELECT") return "editable";
  if (tag === "INPUT") {
    return BUTTON_LIKE_INPUTS.includes((target.inputType ?? "text").toLowerCase()) ? "interactive" : "editable";
  }
  return INTERACTIVE_TAGS.includes(tag) ? "interactive" : "other";
}

const OPTION_BY_KEY: Record<string, OptionKey> = {
  "1": "A",
  "2": "B",
  "3": "C",
  "4": "D",
  a: "A",
  b: "B",
  c: "C",
  d: "D",
};

/** `1`–`4` hoặc `A`–`D` (không phân biệt hoa thường) → khoá phương án; phím khác → `null`. */
export function optionKeyFromKey(key: string): OptionKey | null {
  return OPTION_BY_KEY[key.toLowerCase()] ?? null;
}

export function resolveShortcut(input: KeyInput, context: ShortcutContext): ShortcutAction | null {
  const command = input.ctrlKey || input.metaKey;
  const key = input.key;

  if (command && !input.altKey && key.toLowerCase() === "k") return { type: "open_palette" };
  if (key === "Escape") return { type: "close" };
  if (context.overlayOpen) return null;

  const scope = context.scope;
  if (scope.kind === "essay" && scope.phase !== "graded" && command && key === "Enter") {
    return { type: "submit_essay" };
  }
  if (command || input.altKey) return null;

  if (key === "Enter") {
    // Enter trên nút, liên kết hay ô nhập giữ hành vi gốc của trình duyệt.
    if (input.target !== "other") return null;
    if (isAnsweredScope(scope)) return { type: "next" };
    if (scope.kind === "mcq" && scope.phase === "selected") return { type: "submit" };
    return null;
  }

  if (input.target === "editable") return null;
  if (key === "?") return { type: "show_help" };

  if (isAnsweredScope(scope)) {
    const lower = key.toLowerCase();
    if (lower === "n") return { type: "next" };
    if (lower === "c") return { type: "open_citation" };
    return null;
  }
  if (scope.kind === "mcq") {
    const option = optionKeyFromKey(key);
    if (option !== null) return { type: "select_option", key: option };
  }
  return null;
}

export interface ShortcutHelpEntry {
  keys: string[];
  context: string;
  action: string;
}

/** Bảng hiển thị trong hộp thoại "?"; cùng nguồn với `resolveShortcut`. */
export const SHORTCUT_HELP: readonly ShortcutHelpEntry[] = [
  { keys: ["Ctrl+K"], context: "Mọi nơi", action: "Mở hộp lệnh: đi tới màn hình khác" },
  { keys: ["?"], context: "Mọi nơi, trừ khi đang gõ trong ô nhập", action: "Mở bảng phím tắt này" },
  { keys: ["Esc"], context: "Hộp thoại, hộp lệnh, ô đang sửa", action: "Đóng hoặc huỷ sửa" },
  { keys: ["1–4", "A–D"], context: "Câu trắc nghiệm chưa nộp", action: "Chọn phương án tương ứng" },
  { keys: ["Enter"], context: "Câu trắc nghiệm đã chọn phương án", action: "Nộp; đã nộp thì sang câu kế tiếp" },
  { keys: ["Ctrl+Enter"], context: "Ô trả lời tự luận", action: "Nộp câu trả lời" },
  { keys: ["N"], context: "Câu đã nộp hoặc đã chấm", action: "Sang câu kế tiếp" },
  { keys: ["C"], context: "Câu đã nộp hoặc đã chấm", action: "Mở hoặc đóng đoạn nguồn của trích dẫn" },
  { keys: ["Enter", "Esc"], context: "Ô đang sửa trong bảng kiểm duyệt", action: "Lưu hoặc huỷ" },
];
