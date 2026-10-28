import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import {
  type KeyValueStorage,
  MOTION_STORAGE_KEY,
  nextTheme,
  parseTheme,
  readTheme,
  THEME_STORAGE_KEY,
  themeToggleLabel,
  writeTheme,
} from "./theme";

function memoryStorage(initial: Record<string, string> = {}): KeyValueStorage & { data: Record<string, string> } {
  const data = { ...initial };
  return {
    data,
    getItem: (key) => data[key] ?? null,
    setItem: (key, value) => {
      data[key] = value;
    },
  };
}

const blocked: KeyValueStorage = {
  getItem: () => {
    throw new Error("SecurityError");
  },
  setItem: () => {
    throw new Error("SecurityError");
  },
};

describe("theme", () => {
  it("parseTheme chỉ nhận light/dark, còn lại là auto", () => {
    expect(parseTheme("light")).toBe("light");
    expect(parseTheme("dark")).toBe("dark");
    expect(parseTheme("auto")).toBe("auto");
    expect(parseTheme(null)).toBe("auto");
    expect(parseTheme("DARK")).toBe("auto");
    expect(parseTheme("")).toBe("auto");
  });

  it("nextTheme xoay vòng Theo Windows → Sáng → Tối → Theo Windows", () => {
    expect(nextTheme("auto")).toBe("light");
    expect(nextTheme("light")).toBe("dark");
    expect(nextTheme("dark")).toBe("auto");
  });

  it("nhãn nút nói trạng thái hiện tại và trạng thái kế tiếp", () => {
    expect(themeToggleLabel("auto")).toBe("Giao diện: Theo Windows. Bấm để chuyển sang Sáng");
    expect(themeToggleLabel("dark")).toBe("Giao diện: Tối. Bấm để chuyển sang Theo Windows");
  });

  it("đọc và ghi lựa chọn qua kho", () => {
    const storage = memoryStorage();
    expect(readTheme(storage)).toBe("auto");
    expect(writeTheme(storage, "dark")).toBe(true);
    expect(storage.data[THEME_STORAGE_KEY]).toBe("dark");
    expect(readTheme(storage)).toBe("dark");
  });

  it("kho bị chặn hoặc không có: dùng mặc định, không ném lỗi", () => {
    expect(readTheme(blocked)).toBe("auto");
    expect(writeTheme(blocked, "light")).toBe(false);
    expect(readTheme(null)).toBe("auto");
    expect(writeTheme(null, "light")).toBe(false);
  });

  it("giá trị lạ trong kho bị bỏ qua", () => {
    expect(readTheme(memoryStorage({ [THEME_STORAGE_KEY]: "neon" }))).toBe("auto");
  });

  it("public/theme-init.js dùng đúng khoá lưu của lib/theme.ts", () => {
    const script = readFileSync(join(__dirname, "..", "..", "public", "theme-init.js"), "utf-8");
    expect(script).toContain(`localStorage.getItem("${THEME_STORAGE_KEY}")`);
    expect(script).toContain(`localStorage.getItem("${MOTION_STORAGE_KEY}")`);
    expect(script).toContain("catch (e)");
  });
});
