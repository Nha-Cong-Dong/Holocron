"use client";

import { useEffect, useState } from "react";

import { Icon } from "@/components/common/Icon";
import {
  nextTheme,
  parseTheme,
  readTheme,
  THEME_STORAGE_KEY,
  type ThemeChoice,
  themeToggleLabel,
  writeTheme,
} from "@/lib/theme";

import styles from "./Sidebar.module.css";

function browserStorage(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

/**
 * Nút đổi theme (design-handoff/README.md mục 3): xoay vòng Theo Windows → Sáng → Tối. Đổi là đổi biến tức thì trên
 * `<html data-theme>`, không animate màu.
 */
export function ThemeToggle() {
  const [theme, setTheme] = useState<ThemeChoice>("auto");

  // `public/theme-init.js` đã đặt `data-theme` trước khi vẽ; đọc lại từ đó để nhãn khớp giao diện đang hiện,
  // kho bị chặn thì vẫn đúng. Tab khác đổi theme (sự kiện `storage`) thì đồng bộ theo.
  useEffect(() => {
    setTheme(parseTheme(document.documentElement.dataset.theme ?? null));
    function onStorage(event: StorageEvent): void {
      if (event.key !== THEME_STORAGE_KEY) return;
      const choice = readTheme(browserStorage());
      document.documentElement.dataset.theme = choice;
      setTheme(choice);
    }
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  function onClick(): void {
    const choice = nextTheme(theme);
    document.documentElement.dataset.theme = choice;
    writeTheme(browserStorage(), choice);
    setTheme(choice);
  }

  return (
    <button
      type="button"
      className={`m-btn-icon ${styles.themeToggle}`}
      onClick={onClick}
      aria-label={themeToggleLabel(theme)}
      title={themeToggleLabel(theme)}
    >
      <Icon name="progress" size={20} />
    </button>
  );
}
