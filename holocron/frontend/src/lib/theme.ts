/**
 * Theme sáng/tối và giảm chuyển động (design-handoff/README.md mục 3). Lựa chọn lưu trong `localStorage`; mọi lần
 * đọc/ghi bọc try/catch vì kho có thể bị chặn (cửa sổ riêng tư, chính sách trình duyệt), khi đó dùng mặc định.
 */

export type ThemeChoice = "auto" | "light" | "dark";

export const THEME_STORAGE_KEY = "holocron.theme";
export const MOTION_STORAGE_KEY = "holocron.motion";

const THEME_ORDER: readonly ThemeChoice[] = ["auto", "light", "dark"];

const THEME_LABELS: Record<ThemeChoice, string> = {
  auto: "Theo Windows",
  light: "Sáng",
  dark: "Tối",
};

export function parseTheme(value: string | null): ThemeChoice {
  return value === "light" || value === "dark" ? value : "auto";
}

/** Xoay vòng Theo Windows → Sáng → Tối → Theo Windows. */
export function nextTheme(current: ThemeChoice): ThemeChoice {
  const index = THEME_ORDER.indexOf(current);
  return THEME_ORDER[(index + 1) % THEME_ORDER.length] ?? "auto";
}

export function themeLabel(choice: ThemeChoice): string {
  return THEME_LABELS[choice];
}

/** Nhãn cho nút icon đổi theme: nói trạng thái hiện tại và trạng thái sau khi bấm. */
export function themeToggleLabel(current: ThemeChoice): string {
  return `Giao diện: ${themeLabel(current)}. Bấm để chuyển sang ${themeLabel(nextTheme(current))}`;
}

export interface KeyValueStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

export function readTheme(storage: KeyValueStorage | null): ThemeChoice {
  if (storage === null) return "auto";
  try {
    return parseTheme(storage.getItem(THEME_STORAGE_KEY));
  } catch {
    return "auto";
  }
}

/** Ghi lựa chọn; trả `false` khi kho không ghi được (lựa chọn vẫn áp cho phiên hiện tại). */
export function writeTheme(storage: KeyValueStorage | null, choice: ThemeChoice): boolean {
  if (storage === null) return false;
  try {
    storage.setItem(THEME_STORAGE_KEY, choice);
    return true;
  } catch {
    return false;
  }
}

/**
 * Sự kiện phát trên `window` sau khi theme hoặc giảm chuyển động đổi trong tab hiện tại (nút trên thanh trên, mục Giao
 * diện trong Cài đặt), để các nơi khác đọc lại giá trị trên `<html>` mà không phải dùng chung state.
 */
export const THEME_CHANGE_EVENT = "holocron:theme-change";

/** Phát `THEME_CHANGE_EVENT`; `target` mặc định là `window` (truyền tham số để test). */
export function announceThemeChange(target: EventTarget = window): void {
  target.dispatchEvent(new CustomEvent(THEME_CHANGE_EVENT));
}

/** Giá trị lưu cho giảm chuyển động: `reduce` = bật trong app, `auto` = theo cài đặt của hệ điều hành. */
const MOTION_REDUCE = "reduce";
const MOTION_AUTO = "auto";

/** Người dùng đã bật "Giảm chuyển động" trong app; kho bị chặn, không có hay giá trị lạ ⇒ `false`. */
export function readMotion(storage: KeyValueStorage | null): boolean {
  if (storage === null) return false;
  try {
    return storage.getItem(MOTION_STORAGE_KEY) === MOTION_REDUCE;
  } catch {
    return false;
  }
}

/** Ghi lựa chọn giảm chuyển động; trả `false` khi kho không ghi được (lựa chọn vẫn áp cho phiên hiện tại). */
export function writeMotion(storage: KeyValueStorage | null, reduce: boolean): boolean {
  if (storage === null) return false;
  try {
    storage.setItem(MOTION_STORAGE_KEY, reduce ? MOTION_REDUCE : MOTION_AUTO);
    return true;
  } catch {
    return false;
  }
}

/** Phần tử gốc nhận `data-theme`/`data-motion` (`document.documentElement`; test truyền bản giả). */
export interface ThemeRoot {
  dataset: Record<string, string | undefined>;
  removeAttribute(name: string): void;
}

export function applyTheme(root: ThemeRoot, choice: ThemeChoice): void {
  root.dataset.theme = choice;
}

/**
 * Bật ⇒ `data-motion="reduce"`; tắt ⇒ xoá `data-motion` (về theo hệ điều hành). `prefers-reduced-motion` của hệ điều
 * hành vẫn thắng trong `motion.css`: tắt công tắc không bật lại chuyển động khi Windows đang giảm chuyển động.
 */
export function applyMotion(root: ThemeRoot, reduce: boolean): void {
  if (reduce) root.dataset.motion = MOTION_REDUCE;
  else root.removeAttribute("data-motion");
}

/** `localStorage` của trình duyệt, hoặc `null` khi không có hay bị chặn (cửa sổ riêng tư, chính sách trình duyệt). */
export function browserStorage(): KeyValueStorage | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}
