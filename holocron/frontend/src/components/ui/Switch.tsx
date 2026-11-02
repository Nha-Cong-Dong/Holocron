"use client";

import { useState } from "react";

import styles from "./Switch.module.css";

const STAR_PATH = "M12 2l2.6 7.4L22 12l-7.4 2.6L12 22l-2.6-7.4L2 12l7.4-2.6z";

interface SwitchBase {
  checked: boolean;
  onChange: (next: boolean) => void;
  /** `id` của dòng giải thích (gắn `aria-describedby`). */
  describedBy?: string;
  disabled?: boolean;
  className?: string;
}

/** Công tắc PHẢI có tên: nhãn riêng (`label`, đọc cho trình đọc màn hình) hoặc `id` của chữ nhìn thấy (`labelledBy`). */
export type SwitchProps = SwitchBase & ({ label: string; labelledBy?: never } | { labelledBy: string; label?: never });

/** Lần giãn kế tiếp: đổi qua lại 1 ↔ 2 để CSS chạy lại animation mà không cần hẹn giờ. */
type Pop = 0 | 1 | 2;

/**
 * Công tắc `<button role="switch" aria-checked>`: núm giãn dài giữa đường như giọt nước, khi bật thì ngôi sao xoay vào
 * núm. Vùng bấm rộng 44 px dù rãnh nhỏ hơn.
 */
export function Switch(props: SwitchProps) {
  const { checked, onChange, describedBy, disabled, className } = props;
  const [pop, setPop] = useState<Pop>(0);
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={props.label}
      aria-labelledby={props.labelledBy}
      aria-describedby={describedBy}
      disabled={disabled}
      data-pop={pop === 0 ? undefined : String(pop)}
      className={["m-switch", styles.switch, className ?? ""].filter((part) => part !== "").join(" ")}
      onClick={() => {
        setPop((current) => (current === 1 ? 2 : 1));
        onChange(!checked);
      }}
    >
      <span className={`m-switch-knob ${styles.knob}`}>
        <svg className={`m-switch-star ${styles.star}`} viewBox="0 0 24 24" aria-hidden="true">
          <path d={STAR_PATH} fill="currentColor" />
        </svg>
      </span>
    </button>
  );
}
