/**
 * Phím tắt của màn Xem lại (design-handoff/README.md màn 6): Enter = xác nhận trang đang xem, E = sửa dòng, ←/→ =
 * trang cần xem trước/sau. Quyết định bằng hàm thuần để test; component chỉ mô tả phím và nơi đang focus.
 *
 * Quy tắc an toàn (I5: trang `needs_review` chỉ được xác nhận khi người dùng chủ ý):
 * - Enter chỉ xác nhận khi focus nằm ở chính vùng xem lại (không ở ô nhập, ô chọn hay một nút khác: Enter trên nút để
 *   trình duyệt bấm đúng nút đó), không phải phím giữ lặp lại, không có thao tác ghi đang chạy, không có nháp chưa lưu,
 *   trang xác nhận được (`confirmPageBlock === null`) và không có dòng sai dạng bị bỏ.
 * - Enter chỉ xác nhận trang mà người dùng đã chủ động tương tác SAU khi trang hiện ra (`armed`, xem `armsReview`) và
 *   không sớm hơn `REVIEW_ENTER_GUARD_MS` kể từ lúc trang hiện ra. Tự chuyển sang trang kế sau khi xác nhận, mở trang từ
 *   danh sách hay vẽ lại trang sau khi lưu dòng đều là trang "mới hiện": Enter kế tiếp không xác nhận trang chưa đọc.
 * - Phím có Ctrl/Alt/Meta, phím giữ lặp lại, hoặc khi có hộp thoại/bảng lệnh đang mở thì không làm gì.
 * - ←/→ và E chỉ chạy khi focus ở trong vùng xem lại (không ở hàng tab, không ở ô nhập).
 */

import type { ConfirmPageBlock } from "@/lib/documents";

/** Nơi đang focus so với vùng xem lại. */
export type ReviewFocus =
  /** Chính vùng xem lại, hoặc phần tử không tương tác bên trong. */
  | "region"
  /** Nút, liên kết hay điều khiển khác bên trong vùng. */
  | "control"
  /** Ô nhập, ô chọn, vùng sửa chữ bên trong vùng. */
  | "editable"
  /** Ngoài vùng xem lại (hàng tab, thanh trên…). */
  | "outside";

export interface FocusTarget {
  /** Phần tử đang focus nằm trong vùng xem lại (kể cả chính vùng). */
  insideRegion: boolean;
  /** Phần tử đang focus là chính vùng xem lại. */
  isRegion: boolean;
  /** `tagName` viết hoa như DOM trả. */
  tagName: string;
  isContentEditable: boolean;
  /** Thuộc tính `role`, nếu có. */
  role: string | null;
}

const EDITABLE_TAGS: ReadonlySet<string> = new Set(["INPUT", "TEXTAREA", "SELECT"]);
const CONTROL_TAGS: ReadonlySet<string> = new Set(["BUTTON", "A", "SUMMARY", "DETAILS"]);
const EDITABLE_ROLES: ReadonlySet<string> = new Set(["textbox", "combobox", "listbox", "searchbox", "spinbutton"]);
const CONTROL_ROLES: ReadonlySet<string> = new Set([
  "button",
  "link",
  "tab",
  "checkbox",
  "radio",
  "switch",
  "menuitem",
  "option",
  "slider",
]);

/** Phân loại phần tử đang focus. */
export function classifyReviewFocus(target: FocusTarget): ReviewFocus {
  if (!target.insideRegion) return "outside";
  if (target.isRegion) return "region";
  const tag = target.tagName.toUpperCase();
  const role = target.role?.trim().toLowerCase() ?? "";
  if (target.isContentEditable || EDITABLE_TAGS.has(tag) || EDITABLE_ROLES.has(role)) return "editable";
  if (CONTROL_TAGS.has(tag) || CONTROL_ROLES.has(role)) return "control";
  return "region";
}

/** Khoảng tối thiểu từ lúc trang hiện ra tới lúc Enter được xác nhận trang. */
export const REVIEW_ENTER_GUARD_MS = 600;

/** Sự kiện trong vùng xem lại, dùng để biết người dùng đã chủ động tương tác với trang đang hiện. */
export type ReviewInteraction =
  | { type: "pointerdown" }
  | { type: "keydown"; key: string; repeat: boolean }
  /** `fromElement`: focus đi từ một phần tử khác vào vùng (người dùng bấm Tab, bấm chuột), không phải đặt bằng code. */
  | { type: "focusin"; fromElement: boolean };

/**
 * Sự kiện có "mở khoá" Enter cho trang đang hiện không: bấm chuột trong vùng, một phím khác Enter (không lặp), hoặc focus
 * do người dùng chuyển vào vùng. Enter và focus đặt bằng code (khi trang mới hiện) không mở khoá.
 */
export function armsReview(event: ReviewInteraction): boolean {
  switch (event.type) {
    case "pointerdown":
      return true;
    case "keydown":
      return event.key !== "Enter" && !event.repeat;
    case "focusin":
      return event.fromElement;
  }
}

export interface ReviewKeyInput {
  key: string;
  repeat: boolean;
  altKey: boolean;
  ctrlKey: boolean;
  metaKey: boolean;
  focus: ReviewFocus;
  /** Hộp thoại, bảng lệnh hay bảng phím tắt đang mở. */
  overlayOpen: boolean;
  /** Đang có thao tác ghi (lưu dòng, xác nhận). */
  busy: boolean;
  /** Có dòng đang sửa hoặc dòng gõ thêm chưa lưu. */
  hasDraft: boolean;
  confirmBlock: ConfirmPageBlock | null;
  hasSkippedLines: boolean;
  /** Trang sửa được (`lineEditBlock === null`) và có dòng để sửa. */
  canEdit: boolean;
  hasPrevious: boolean;
  hasNext: boolean;
  /** Người dùng đã chủ động tương tác với trang này sau khi nó hiện ra (`armsReview`). */
  armed: boolean;
  /** Số mili giây từ lúc trang hiện ra (vùng xem lại mount) tới lúc bấm phím. */
  msSinceShown: number;
}

export type ReviewKeyAction = { type: "confirm" } | { type: "edit" } | { type: "previous" } | { type: "next" };

/** Hành động của một phím ở màn Xem lại; `null` khi phím không làm gì (để trình duyệt xử lý như thường). */
export function reviewKeyAction(input: ReviewKeyInput): ReviewKeyAction | null {
  if (input.altKey || input.ctrlKey || input.metaKey || input.repeat || input.overlayOpen) return null;
  if (input.focus === "outside" || input.focus === "editable") return null;
  switch (input.key) {
    case "Enter": {
      if (input.focus !== "region") return null;
      const allowed =
        input.armed &&
        input.msSinceShown >= REVIEW_ENTER_GUARD_MS &&
        !input.busy &&
        !input.hasDraft &&
        input.confirmBlock === null &&
        !input.hasSkippedLines;
      return allowed ? { type: "confirm" } : null;
    }
    case "e":
    case "E":
      return input.canEdit && !input.busy && !input.hasDraft ? { type: "edit" } : null;
    case "ArrowLeft":
      return input.hasPrevious && !input.hasDraft ? { type: "previous" } : null;
    case "ArrowRight":
      return input.hasNext && !input.hasDraft ? { type: "next" } : null;
    default:
      return null;
  }
}

/** Dòng mở để sửa khi bấm E: dòng nghi ngờ đầu tiên (conf thấp hoặc lệch giữa hai lượt đọc), không có thì dòng đầu. */
export function lineToEdit(lines: readonly { lineIndex: number; flagged: boolean }[]): number | null {
  const flagged = lines.find((line) => line.flagged);
  return (flagged ?? lines[0])?.lineIndex ?? null;
}
