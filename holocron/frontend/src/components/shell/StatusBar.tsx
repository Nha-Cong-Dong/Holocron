"use client";

import { useState } from "react";

import type { StatusItem } from "@/lib/runtime";

import styles from "./StatusBar.module.css";

interface StatusBarProps {
  items: StatusItem[];
  /** Nhãn `Holocron <version> (<commit>)`; `null` khi backend chưa báo. */
  buildLabel: string | null;
  /** Thư mục log launcher đang ghi; `null` thì không hiện khối "Thư mục log". */
  logDir: string | null;
  canRetry: boolean;
  onRetry: () => void;
}

/** Thanh trạng thái dưới cùng: backend, LLM, OCR, chế độ offline, dữ liệu mẫu. Mỗi ô luôn có chữ đi kèm màu. */
export function StatusBar({ items, buildLabel, logDir, canRetry, onRetry }: StatusBarProps) {
  const [copyState, setCopyState] = useState<"idle" | "copied" | "failed">("idle");

  const copyLogDir = () => {
    if (logDir === null) return;
    navigator.clipboard.writeText(logDir).then(
      () => setCopyState("copied"),
      () => setCopyState("failed"),
    );
  };

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
      {buildLabel !== null && <span className={styles.build}>{buildLabel}</span>}
      {logDir !== null && (
        <span className={styles.logDir}>
          <span className={styles.label}>Thư mục log:</span>{" "}
          <code className={styles.path} title={logDir}>
            {logDir}
          </code>
          <button type="button" className={styles.retry} onClick={copyLogDir}>
            {copyState === "copied" ? "Đã sao chép" : copyState === "failed" ? "Không sao chép được" : "Sao chép"}
          </button>
        </span>
      )}
      {canRetry && (
        <button type="button" className={styles.retry} onClick={onRetry}>
          Kiểm tra lại
        </button>
      )}
    </footer>
  );
}
