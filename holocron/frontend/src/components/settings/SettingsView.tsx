"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { Icon } from "@/components/common/Icon";
import {
  announceThemeChange,
  applyMotion,
  applyTheme,
  browserStorage,
  MOTION_STORAGE_KEY,
  parseTheme,
  readMotion,
  THEME_CHANGE_EVENT,
  type ThemeChoice,
  themeLabel,
  writeMotion,
  writeTheme,
} from "@/lib/theme";

import styles from "./SettingsView.module.css";

const THEME_CHOICES: readonly ThemeChoice[] = ["auto", "light", "dark"];

const STAR_PATH = "M12 3l2.2 6.8L21 12l-6.8 2.2L12 21l-2.2-6.8L3 12l6.8-2.2z";

function Star({ className }: { className: string | undefined }) {
  return (
    <svg className={className} viewBox="0 0 24 24" aria-hidden="true">
      <path d={STAR_PATH} fill="currentColor" />
    </svg>
  );
}

/** Đọc lựa chọn đang áp trên `<html>` (do `public/theme-init.js` hoặc lần đổi gần nhất đặt). */
function appliedChoices(): { theme: ThemeChoice; reduce: boolean } {
  const root = document.documentElement;
  return { theme: parseTheme(root.dataset.theme ?? null), reduce: root.dataset.motion === "reduce" };
}

/**
 * Cài đặt (design-handoff/README.md màn 11): Giao diện (3 chip chọn theme + công tắc Giảm chuyển động) và mục Thử
 * nghiệm thu gọn chứa các màn đã gỡ khỏi điều hướng chính. Đồng bộ hai chiều với nút theme trên thanh trên qua
 * `THEME_CHANGE_EVENT`. Chip radio và công tắc làm tại chỗ; PR bộ thành phần dùng chung sẽ thay.
 */
export function SettingsView() {
  const [theme, setTheme] = useState<ThemeChoice>("auto");
  const [reduce, setReduce] = useState(false);

  useEffect(() => {
    function sync(): void {
      const applied = appliedChoices();
      setTheme(applied.theme);
      setReduce(applied.reduce);
    }
    // Tab khác đổi giảm chuyển động: áp theo kho. (Theme đổi ở tab khác do nút trên thanh trên áp rồi phát sự kiện.)
    function onStorage(event: StorageEvent): void {
      if (event.key !== MOTION_STORAGE_KEY) return;
      applyMotion(document.documentElement, readMotion(browserStorage()));
      sync();
    }
    sync();
    window.addEventListener(THEME_CHANGE_EVENT, sync);
    window.addEventListener("storage", onStorage);
    return () => {
      window.removeEventListener(THEME_CHANGE_EVENT, sync);
      window.removeEventListener("storage", onStorage);
    };
  }, []);

  function pickTheme(choice: ThemeChoice): void {
    applyTheme(document.documentElement, choice);
    writeTheme(browserStorage(), choice);
    setTheme(choice);
    announceThemeChange();
  }

  function toggleReduce(): void {
    const next = !reduce;
    applyMotion(document.documentElement, next);
    writeMotion(browserStorage(), next);
    setReduce(next);
    announceThemeChange();
  }

  return (
    <section className={styles.page} aria-labelledby="settings-title">
      <h1 id="settings-title" className={styles.title}>
        Cài đặt
      </h1>

      <section className={styles.panel} aria-labelledby="settings-appearance">
        <div>
          <h2 id="settings-appearance" className={styles.heading}>
            Giao diện
          </h2>
          <p className={styles.caption}>
            Chọn sáng hoặc tối, hoặc để tự theo Windows. Đổi nhanh bằng nút cạnh “Cài đặt”.
          </p>
        </div>
        <fieldset className={styles.chips}>
          <legend className={styles.visuallyHidden}>Giao diện</legend>
          {THEME_CHOICES.map((choice) => (
            <label key={choice} className={`m-chip ${styles.chip}`}>
              <input
                type="radio"
                name="settings-theme"
                className={styles.radio}
                value={choice}
                checked={theme === choice}
                onChange={() => pickTheme(choice)}
              />
              {themeLabel(choice)}
              <Star className={`m-chip-star ${styles.chipStar}`} />
            </label>
          ))}
        </fieldset>
        <div className={styles.row}>
          <div>
            <p id="settings-motion" className={styles.rowTitle}>
              Giảm chuyển động
            </p>
            <p id="settings-motion-hint" className={styles.caption}>
              Tắt trượt, nảy, sao băng và logo xoay. Mặc định theo cài đặt của Windows.
            </p>
          </div>
          <button
            type="button"
            role="switch"
            aria-checked={reduce}
            aria-labelledby="settings-motion"
            aria-describedby="settings-motion-hint"
            className={`m-switch ${styles.switch}`}
            onClick={toggleReduce}
          >
            <span className={`m-switch-knob ${styles.knob}`}>
              <Star className={styles.knobStar} />
            </span>
          </button>
        </div>
      </section>

      <details className={`m-accordion ${styles.lab}`}>
        <summary className={styles.labSummary}>
          <svg className={`m-chevron ${styles.chevron}`} viewBox="0 0 24 24" aria-hidden="true">
            <path d="M9 6l6 6-6 6" />
          </svg>
          <Icon name="experiment" size={20} />
          Thử nghiệm
        </summary>
        <div className={styles.labBody}>
          <p className={styles.caption}>Tính năng đang làm, kết quả có thể chưa chính xác.</p>
          <ul className={styles.labList}>
            <li>
              <Link href="/ingestion" className={styles.labLink}>
                Nhập đề thi cũ
              </Link>
              <p className={styles.caption}>Đưa đề các năm trước vào để luyện theo dạng đề.</p>
            </li>
            <li>
              <Link href="/twins" className={styles.labLink}>
                Câu hỏi song sinh
              </Link>
              <p className={styles.caption}>Từ một câu đã làm, tạo câu tương tự để kiểm tra lại.</p>
            </li>
          </ul>
        </div>
      </details>
    </section>
  );
}
