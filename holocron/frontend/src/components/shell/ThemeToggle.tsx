"use client";

import { useEffect, useState } from "react";

import { Icon } from "@/components/common/Icon";
import { nextTheme, readTheme, type ThemeChoice, themeToggleLabel, writeTheme } from "@/lib/theme";

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

  useEffect(() => {
    setTheme(readTheme(browserStorage()));
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
