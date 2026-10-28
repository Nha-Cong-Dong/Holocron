/**
 * Logic phím thuần của bảng thả xuống `Dropdown` (listbox tuỳ biến, mẫu "select-only combobox" của ARIA APG): một
 * phím ⇒ một hành động. Component chỉ áp hành động trả về (mở, chuyển dòng đang trỏ, chọn, đóng).
 */

export interface DropdownKeyState {
  open: boolean;
  /** Dòng đang trỏ (`aria-activedescendant`) khi bảng mở; -1 khi chưa trỏ dòng nào. */
  active: number;
  /** Dòng đang được chọn; -1 khi chưa chọn. */
  selected: number;
  /** Số dòng của bảng. */
  count: number;
}

export type DropdownKeyAction =
  /** Mở bảng và trỏ vào dòng `active`. */
  | { type: "open"; active: number }
  /** Bảng đang mở: trỏ sang dòng `active`. */
  | { type: "move"; active: number }
  /** Chọn dòng `index`, đóng bảng, trả focus về nút mở. */
  | { type: "select"; index: number }
  /** Đóng bảng không đổi lựa chọn; `restoreFocus` = đưa focus về nút mở (Esc), không đưa khi rời bằng Tab. */
  | { type: "close"; restoreFocus: boolean };

/** Kẹp chỉ số vào `[0, count - 1]`; -1 (chưa chọn) hoặc giá trị lạ ⇒ 0. Gọi với `count > 0`. */
export function clampIndex(index: number, count: number): number {
  if (!Number.isInteger(index) || index < 0) return 0;
  return Math.min(index, count - 1);
}

/**
 * Hành động cho phím `key` (giá trị `KeyboardEvent.key`). `null` = không xử lý, để trình duyệt làm việc mặc định.
 *
 * Bảng đóng: ↓, ↑, Enter, Space mở ở dòng đang chọn; Home mở ở dòng đầu, End ở dòng cuối.
 * Bảng mở: ↓/↑ đi một dòng (dừng ở hai đầu, không đi vòng), Home/End về đầu/cuối, Enter/Space chọn, Esc đóng và trả
 * focus về nút mở, Tab đóng và để focus đi tiếp.
 */
export function dropdownKeyAction(key: string, state: DropdownKeyState): DropdownKeyAction | null {
  const { open, count } = state;
  if (count <= 0) {
    if (!open) return null;
    if (key === "Escape") return { type: "close", restoreFocus: true };
    if (key === "Tab") return { type: "close", restoreFocus: false };
    return null;
  }
  if (!open) {
    switch (key) {
      case "ArrowDown":
      case "ArrowUp":
      case "Enter":
      case " ":
        return { type: "open", active: clampIndex(state.selected, count) };
      case "Home":
        return { type: "open", active: 0 };
      case "End":
        return { type: "open", active: count - 1 };
      default:
        return null;
    }
  }
  const from = state.active < 0 ? clampIndex(state.selected, count) : clampIndex(state.active, count);
  switch (key) {
    case "ArrowDown":
      return { type: "move", active: state.active < 0 ? from : Math.min(from + 1, count - 1) };
    case "ArrowUp":
      return { type: "move", active: state.active < 0 ? from : Math.max(from - 1, 0) };
    case "Home":
      return { type: "move", active: 0 };
    case "End":
      return { type: "move", active: count - 1 };
    case "Enter":
    case " ":
      return { type: "select", index: from };
    case "Escape":
      return { type: "close", restoreFocus: true };
    case "Tab":
      return { type: "close", restoreFocus: false };
    default:
      return null;
  }
}
