import { readFileSync } from "node:fs";
import { join } from "node:path";
import { runInNewContext } from "node:vm";

import { describe, expect, it } from "vitest";

import {
  announceThemeChange,
  applyMotion,
  applyTheme,
  type KeyValueStorage,
  MOTION_STORAGE_KEY,
  nextTheme,
  parseTheme,
  readMotion,
  readTheme,
  THEME_CHANGE_EVENT,
  THEME_STORAGE_KEY,
  type ThemeRoot,
  themeToggleLabel,
  writeMotion,
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

  function runInitScript(storage: KeyValueStorage): Record<string, string> {
    const script = readFileSync(join(__dirname, "..", "..", "public", "theme-init.js"), "utf-8");
    const dataset: Record<string, string> = { theme: "auto" };
    runInNewContext(script, { document: { documentElement: { dataset } }, localStorage: storage });
    return dataset;
  }

  it("theme-init.js áp theme và giảm chuyển động đã lưu", () => {
    const dataset = runInitScript(memoryStorage({ [THEME_STORAGE_KEY]: "dark", [MOTION_STORAGE_KEY]: "reduce" }));
    expect(dataset).toEqual({ theme: "dark", motion: "reduce" });
  });

  it("theme-init.js bỏ qua giá trị lạ và giữ mặc định auto", () => {
    expect(runInitScript(memoryStorage({ [THEME_STORAGE_KEY]: "neon" }))).toEqual({ theme: "auto" });
  });

  it("theme-init.js không ném lỗi khi kho bị chặn", () => {
    expect(runInitScript(blocked)).toEqual({ theme: "auto" });
  });

  it("public/theme-init.js dùng đúng khoá lưu của lib/theme.ts", () => {
    const script = readFileSync(join(__dirname, "..", "..", "public", "theme-init.js"), "utf-8");
    expect(script).toContain(`localStorage.getItem("${THEME_STORAGE_KEY}")`);
    expect(script).toContain(`localStorage.getItem("${MOTION_STORAGE_KEY}")`);
    expect(script).toContain("catch (e)");
  });
});

describe("giảm chuyển động", () => {
  it("đọc và ghi lựa chọn: bật lưu reduce, tắt lưu auto (theo hệ điều hành)", () => {
    const storage = memoryStorage();
    expect(readMotion(storage)).toBe(false);
    expect(writeMotion(storage, true)).toBe(true);
    expect(storage.data[MOTION_STORAGE_KEY]).toBe("reduce");
    expect(readMotion(storage)).toBe(true);
    expect(writeMotion(storage, false)).toBe(true);
    expect(storage.data[MOTION_STORAGE_KEY]).toBe("auto");
    expect(readMotion(storage)).toBe(false);
  });

  it("kho bị chặn, không có hoặc giá trị lạ: coi như tắt, không ném lỗi", () => {
    expect(readMotion(blocked)).toBe(false);
    expect(writeMotion(blocked, true)).toBe(false);
    expect(readMotion(null)).toBe(false);
    expect(writeMotion(null, true)).toBe(false);
    expect(readMotion(memoryStorage({ [MOTION_STORAGE_KEY]: "REDUCE" }))).toBe(false);
  });

  it("giá trị ghi ra khớp điều kiện của theme-init.js", () => {
    const storage = memoryStorage({ [THEME_STORAGE_KEY]: "light" });
    writeMotion(storage, true);
    const script = readFileSync(join(__dirname, "..", "..", "public", "theme-init.js"), "utf-8");
    const dataset: Record<string, string> = { theme: "auto" };
    runInNewContext(script, { document: { documentElement: { dataset } }, localStorage: storage });
    expect(dataset).toEqual({ theme: "light", motion: "reduce" });
    writeMotion(storage, false);
    const after: Record<string, string> = { theme: "auto" };
    runInNewContext(script, { document: { documentElement: { dataset: after } }, localStorage: storage });
    expect(after).toEqual({ theme: "light" });
  });

  function fakeRoot(): ThemeRoot & { removed: string[] } {
    const dataset: Record<string, string | undefined> = { theme: "auto" };
    const removed: string[] = [];
    return {
      dataset,
      removed,
      removeAttribute: (name) => {
        removed.push(name);
        if (name === "data-motion") delete dataset.motion;
      },
    };
  }

  it("applyMotion: bật đặt data-motion=reduce, tắt xoá hẳn thuộc tính", () => {
    const root = fakeRoot();
    applyMotion(root, true);
    expect(root.dataset.motion).toBe("reduce");
    applyMotion(root, false);
    expect(root.removed).toEqual(["data-motion"]);
    expect("motion" in root.dataset).toBe(false);
  });

  it("applyTheme đặt data-theme", () => {
    const root = fakeRoot();
    applyTheme(root, "dark");
    expect(root.dataset.theme).toBe("dark");
  });
});

describe("sự kiện đổi giao diện", () => {
  it("announceThemeChange phát đúng THEME_CHANGE_EVENT trên target được truyền", () => {
    const target = new EventTarget();
    const seen: string[] = [];
    target.addEventListener(THEME_CHANGE_EVENT, (event) => seen.push(event.type));
    announceThemeChange(target);
    expect(seen).toEqual([THEME_CHANGE_EVENT]);
  });
});
