/**
 * Logic thuần của hàng tab (design-handoff/README.md màn 3): phím ⇒ chỉ số tab, hướng trượt của nội dung khi đổi tab,
 * và đọc tham số `tab` của trang môn. Component `Tabs` chỉ gọi các hàm này.
 */

/** Ba phần của trang môn, theo thứ tự hiển thị. */
export const SUBJECT_TABS = ["documents", "concepts", "practice"] as const;

export type SubjectTab = (typeof SUBJECT_TABS)[number];

export const DEFAULT_SUBJECT_TAB: SubjectTab = "documents";

/** Đọc `?tab=`: giá trị lạ hoặc thiếu ⇒ tab mặc định (Tài liệu). */
export function parseSubjectTab(value: string | null): SubjectTab {
  const trimmed = value?.trim() ?? "";
  return SUBJECT_TABS.find((tab) => tab === trimmed) ?? DEFAULT_SUBJECT_TAB;
}

/**
 * Chỉ số tab nhận focus sau khi bấm phím trong hàng tab ngang (mẫu ARIA tabs): ←/→ đi vòng, Home về đầu, End về cuối.
 * Phím khác hoặc hàng rỗng ⇒ `null` (không xử lý, để trình duyệt làm việc mặc định).
 */
export function tabIndexForKey(key: string, current: number, count: number): number | null {
  if (count <= 0) return null;
  const from = Math.min(Math.max(current, 0), count - 1);
  switch (key) {
    case "ArrowRight":
      return (from + 1) % count;
    case "ArrowLeft":
      return (from - 1 + count) % count;
    case "Home":
      return 0;
    case "End":
      return count - 1;
    default:
      return null;
  }
}

export type PaneDirection = "r" | "l";

/** Hướng trượt của nội dung: sang tab bên phải (hoặc giữ nguyên) ⇒ `r`, sang tab bên trái ⇒ `l`. */
export function paneDirection(previous: number, next: number): PaneDirection {
  return next < previous ? "l" : "r";
}
