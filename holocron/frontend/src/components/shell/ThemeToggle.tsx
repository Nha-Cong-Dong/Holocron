"use client";

import { useEffect, useState } from "react";

import { Icon } from "@/components/common/Icon";
import {
  announceThemeChange,
  applyTheme,
  browserStorage,
  nextTheme,
  parseTheme,
  readTheme,
  THEME_CHANGE_EVENT,
  THEME_STORAGE_KEY,
  type ThemeChoice,
  themeToggleLabel,
  writeTheme,
} from "@/lib/theme";

import styles from "./TopBar.module.css";

/**
 * Nút đổi theme trên thanh trên (design-handoff/README.md mục 3): xoay vòng Theo Windows → Sáng → Tối. Đổi là đổi biến
 * tức thì trên `<html data-theme>`, không animate màu.
 */
export function ThemeToggle() {
  const [theme, setTheme] = useState<ThemeChoice>("auto");

  // `public/theme-init.js` đã đặt `data-theme` trước khi vẽ; đọc lại từ đó để nhãn khớp giao diện đang hiện, kho bị
  // chặn thì vẫn đúng. Cài đặt đổi theme trong tab này (`THEME_CHANGE_EVENT`) hoặc tab khác đổi (sự kiện `storage`)
  // thì đồng bộ theo.
  useEffect(() => {
    const root = document.documentElement;
    setTheme(parseTheme(root.dataset.theme ?? null));
    function onStorage(event: StorageEvent): void {
      if (event.key !== THEME_STORAGE_KEY) return;
      const choice = readTheme(browserStorage());
      applyTheme(root, choice);
      setTheme(choice);
      announceThemeChange();
    }
    function onThemeChange(): void {
      setTheme(parseTheme(root.dataset.theme ?? null));
    }
    window.addEventListener("storage", onStorage);
    window.addEventListener(THEME_CHANGE_EVENT, onThemeChange);
    return () => {
      window.removeEventListener("storage", onStorage);
      window.removeEventListener(THEME_CHANGE_EVENT, onThemeChange);
    };
  }, []);

  function onClick(): void {
    const choice = nextTheme(theme);
    applyTheme(document.documentElement, choice);
    writeTheme(browserStorage(), choice);
    setTheme(choice);
    announceThemeChange();
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
