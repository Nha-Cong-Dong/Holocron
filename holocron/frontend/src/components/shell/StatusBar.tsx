"use client";

import type { StatusItem } from "@/lib/runtime";

import styles from "./StatusBar.module.css";

interface StatusBarProps {
  items: StatusItem[];
  canRetry: boolean;
  onRetry: () => void;
}

/** Thanh trạng thái dưới cùng: backend, LLM, OCR, chế độ offline, dữ liệu mẫu. Mỗi ô luôn có chữ đi kèm màu. */
export function StatusBar({ items, canRetry, onRetry }: StatusBarProps) {
  return (
    <footer className={styles.bar}>
      <ul className={styles.items} aria-label="Trạng thái hệ thống" aria-live="polite">
        {items.map((item) => (
          <li key={item.id} className={styles.item} data-tone={item.tone}>
            <span className={styles.dot} aria-hidden="true" />
            <span className={styles.label}>{item.label}:</span> <span>{item.value}</span>
          </li>
        ))}
      </ul>
      {canRetry && (
        <button type="button" className={styles.retry} onClick={onRetry}>
          Kiểm tra lại
        </button>
      )}
    </footer>
  );
}
