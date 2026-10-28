"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { Icon } from "@/components/common/Icon";
import { Accordion, Chip, Switch } from "@/components/ui";
import {
  announceThemeChange,
  applyMotion,
  applyTheme,
  browserStorage,
  parseTheme,
  THEME_CHANGE_EVENT,
  type ThemeChoice,
  themeLabel,
  writeMotion,
  writeTheme,
} from "@/lib/theme";

import { MachineDiagnostics } from "./MachineDiagnostics";
import styles from "./SettingsView.module.css";

const THEME_CHOICES: readonly ThemeChoice[] = ["auto", "light", "dark"];

/** Đọc lựa chọn đang áp trên `<html>` (do `public/theme-init.js` hoặc lần đổi gần nhất đặt). */
function appliedChoices(): { theme: ThemeChoice; reduce: boolean } {
  const root = document.documentElement;
  return { theme: parseTheme(root.dataset.theme ?? null), reduce: root.dataset.motion === "reduce" };
}

/**
 * Cài đặt (design-handoff/README.md màn 11): Giao diện (3 chip chọn theme + công tắc Giảm chuyển động), Máy của bạn
 * (chẩn đoán máy, chuyển từ trang chủ sang; neo `#may-cua-ban`) và mục Thử nghiệm thu gọn chứa các màn đã gỡ khỏi
 * điều hướng chính. Đồng bộ hai chiều với nút theme trên thanh trên qua
 * `THEME_CHANGE_EVENT`. Dùng `Chip` (radio), `Switch` và `Accordion` của bộ thành phần dùng chung.
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
    // Tab khác đổi theme hay giảm chuyển động: khung và nút theme áp lên <html> rồi phát `THEME_CHANGE_EVENT`.
    sync();
    window.addEventListener(THEME_CHANGE_EVENT, sync);
    return () => window.removeEventListener(THEME_CHANGE_EVENT, sync);
  }, []);

  function pickTheme(choice: ThemeChoice): void {
    applyTheme(document.documentElement, choice);
    writeTheme(browserStorage(), choice);
    setTheme(choice);
    announceThemeChange();
  }

  function setReduceMotion(next: boolean): void {
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
          <legend className="visually-hidden">Giao diện</legend>
          {THEME_CHOICES.map((choice) => (
            <Chip
              key={choice}
              kind="radio"
              name="settings-theme"
              value={choice}
              checked={theme === choice}
              onSelect={() => pickTheme(choice)}
            >
              {themeLabel(choice)}
            </Chip>
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
          <Switch
            checked={reduce}
            onChange={setReduceMotion}
            labelledBy="settings-motion"
            describedBy="settings-motion-hint"
          />
        </div>
      </section>

      <MachineDiagnostics />

      <Accordion
        className={styles.lab}
        summary={
          <>
            <Icon name="experiment" size={20} />
            Thử nghiệm
          </>
        }
      >
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
      </Accordion>
    </section>
  );
}
