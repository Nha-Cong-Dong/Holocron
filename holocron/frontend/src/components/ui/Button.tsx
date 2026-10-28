"use client";

import { type ButtonHTMLAttributes, type MouseEvent, type ReactNode, useState } from "react";

import styles from "./Button.module.css";

type NativeButtonProps = Omit<ButtonHTMLAttributes<HTMLButtonElement>, "children" | "className">;

interface TextButtonProps extends NativeButtonProps {
  className?: string;
  children: ReactNode;
  /** Đang xử lý: ba chấm nhún, `aria-busy`, bấm không có tác dụng nhưng nút vẫn nhận focus. */
  busy?: boolean;
  /** Chữ khi đang xử lý, ví dụ "Đang soạn đề"; mặc định giữ chữ của nút. */
  busyLabel?: string;
}

export interface PrimaryButtonProps extends TextButtonProps {
  variant: "primary";
  arrow?: never;
}

export interface SecondaryButtonProps extends TextButtonProps {
  variant: "secondary";
  /** Mũi tên sau chữ (nút dẫn sang bước tiếp, ví dụ "Ôn tiếp"); rê chuột thì mũi tên nhích sang phải. */
  arrow?: boolean;
}

export interface IconButtonProps extends NativeButtonProps {
  variant: "icon";
  className?: string;
  /** Nút chỉ có icon PHẢI có tên đọc được. */
  "aria-label": string;
  children: ReactNode;
}

export type ButtonProps = PrimaryButtonProps | SecondaryButtonProps | IconButtonProps;

/** Góc của 8 tia bụi sao (nút chính, kiểu C). */
const BURST_ANGLES = [0, 45, 90, 135, 180, 225, 270, 315] as const;

function Dots() {
  return (
    <span className="m-dots" aria-hidden="true">
      <i />
      <i />
      <i />
    </span>
  );
}

function Arrow() {
  return (
    <svg className={`m-btn-arrow ${styles.arrow}`} viewBox="0 0 24 24" aria-hidden="true">
      <path d="M5 12h14M13 6l6 6-6 6" />
    </svg>
  );
}

function joinClasses(...names: readonly (string | false | undefined)[]): string {
  return names.filter((name) => typeof name === "string" && name !== "").join(" ");
}

/**
 * Nút dùng chung. `primary`: nhấc nhẹ + ánh quét + bụi sao khi bấm (viền sáng khi rê); `secondary`: phủ màu + mũi tên
 * đẩy; `icon`: nền tròn nở ra (bắt buộc `aria-label`). `busy` (chính/phụ): ba chấm nhún, `aria-busy`, giữ focus.
 * Mỗi màn chỉ một nút `primary`.
 */
export function Button(props: ButtonProps) {
  const [burst, setBurst] = useState(0);

  if (props.variant === "icon") {
    const { variant: _variant, className, children, type = "button", ...rest } = props;
    return (
      <button {...rest} type={type} className={joinClasses("m-btn-icon", styles.icon, className)}>
        {children}
      </button>
    );
  }

  const {
    variant,
    arrow = false,
    busy = false,
    busyLabel,
    className,
    children,
    onClick,
    type = "button",
    ...native
  } = props;
  const isPrimary = variant === "primary";

  function handleClick(event: MouseEvent<HTMLButtonElement>): void {
    if (busy) {
      event.preventDefault();
      return;
    }
    if (isPrimary) setBurst((count) => count + 1);
    onClick?.(event);
  }

  return (
    <button
      {...native}
      type={type}
      className={joinClasses(
        isPrimary ? "m-btn-primary" : "m-btn-secondary",
        isPrimary ? styles.primary : styles.secondary,
        className,
      )}
      aria-busy={busy || undefined}
      aria-disabled={busy || native["aria-disabled"] || undefined}
      onClick={handleClick}
    >
      {isPrimary && <span className="m-btn-sheen" aria-hidden="true" />}
      <span className="m-btn-label">
        {busy && busyLabel !== undefined ? busyLabel : children}
        {busy && <Dots />}
      </span>
      {arrow && !busy && <Arrow />}
      {isPrimary && burst > 0 && (
        <span key={burst} className="m-burst" aria-hidden="true">
          {BURST_ANGLES.map((angle) => (
            <span key={angle} style={{ transform: `rotate(${angle}deg)` }}>
              <i />
            </span>
          ))}
        </span>
      )}
    </button>
  );
}
