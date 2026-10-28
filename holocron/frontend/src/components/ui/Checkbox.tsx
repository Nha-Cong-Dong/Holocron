"use client";

import { type ReactNode, useId, useState } from "react";

import styles from "./Checkbox.module.css";

/** Góc của 6 hạt sao bắn ra khi đánh dấu. */
const SPARK_ANGLES = [0, 60, 120, 180, 240, 300] as const;

export interface CheckboxProps {
  checked: boolean;
  onChange: (next: boolean) => void;
  children: ReactNode;
  /** Dòng giải thích dưới nhãn (gắn `aria-describedby`). */
  hint?: string;
  disabled?: boolean;
  name?: string;
  className?: string;
}

/**
 * Ô đánh dấu là `<input type="checkbox">` thật trong nhãn. Ô lún khi nhấn, dấu tích bật nảy; khi đánh dấu, ô xoay 45°
 * như ngôi sao và bắn 6 hạt. Animation chỉ chạy sau lần bấm đầu (`data-animate`), không chạy khi màn vừa mở.
 */
export function Checkbox({ checked, onChange, children, hint, disabled, name, className }: CheckboxProps) {
  const [touched, setTouched] = useState(false);
  const hintId = useId();
  return (
    <label
      className={["m-check", styles.check, disabled === true ? styles.disabled : "", className ?? ""]
        .filter((part) => part !== "")
        .join(" ")}
      data-animate={touched ? "" : undefined}
    >
      <input
        type="checkbox"
        className={styles.input}
        checked={checked}
        disabled={disabled}
        name={name}
        aria-describedby={hint === undefined ? undefined : hintId}
        onChange={(event) => {
          setTouched(true);
          onChange(event.target.checked);
        }}
      />
      <span className={`m-check-box ${styles.box}`} aria-hidden="true">
        <svg className={`m-check-tick ${styles.tick}`} viewBox="0 0 24 24" aria-hidden="true">
          <path d="M6 12.5l4 4 8-9" />
        </svg>
      </span>
      <span className="m-check-burst" aria-hidden="true">
        {SPARK_ANGLES.map((angle) => (
          <span key={angle} style={{ transform: `rotate(${angle}deg)` }}>
            <i />
          </span>
        ))}
      </span>
      <span className={styles.text}>
        {children}
        {hint !== undefined && (
          <span id={hintId} className={styles.hint}>
            {hint}
          </span>
        )}
      </span>
    </label>
  );
}
