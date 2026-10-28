"use client";

import { type ReactNode, useEffect, useId, useState } from "react";

import styles from "./Tooltip.module.css";

/** Props gắn lên phần tử kích hoạt (thường là `Button variant="icon"`). */
export interface TooltipTriggerProps {
  "aria-describedby": string;
  onMouseEnter: () => void;
  onMouseLeave: () => void;
  onFocus: () => void;
  onBlur: () => void;
}

export interface TooltipProps {
  /** Một câu giải thích ngắn. */
  content: string;
  /** Vẽ phần tử kích hoạt, rải `trigger` lên nó để nối `aria-describedby` và theo dõi rê chuột, focus. */
  children: (trigger: TooltipTriggerProps) => ReactNode;
  className?: string;
}

/**
 * Gợi ý nổi: hiện khi rê chuột hoặc focus vào phần tử kích hoạt (chờ 0,4 s rồi nổi lên có nảy, nên rê ngang qua
 * không bị nháy), `Esc` ẩn cho tới khi rời chuột hoặc focus đi chỗ khác. Chữ gợi ý luôn có trong DOM (`role="tooltip"`)
 * để trình đọc màn hình đọc qua `aria-describedby`.
 */
export function Tooltip({ content, children, className }: TooltipProps) {
  const tipId = useId();
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  const showing = (hovered || focused) && !dismissed;

  // Esc khi đang rê chuột (focus có thể ở nơi khác) hoặc đang focus: nghe ở cửa sổ khi gợi ý có thể đang hiện.
  useEffect(() => {
    if (!showing) return;
    function onKeyDown(event: KeyboardEvent): void {
      if (event.key === "Escape") setDismissed(true);
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [showing]);

  const trigger: TooltipTriggerProps = {
    "aria-describedby": tipId,
    onMouseEnter: () => setHovered(true),
    onMouseLeave: () => {
      setHovered(false);
      setDismissed(false);
    },
    onFocus: () => setFocused(true),
    onBlur: () => {
      setFocused(false);
      setDismissed(false);
    },
  };

  return (
    <span
      className={["m-tooltip-host", className ?? ""].filter((part) => part !== "").join(" ")}
      data-dismissed={dismissed ? "" : undefined}
    >
      {children(trigger)}
      <span id={tipId} role="tooltip" className={`m-tooltip ${styles.tip}`}>
        {content}
      </span>
    </span>
  );
}
