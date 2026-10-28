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
