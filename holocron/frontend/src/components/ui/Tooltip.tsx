"use client";

import { type ReactNode, useEffect, useId, useRef, useState } from "react";

import styles from "./Tooltip.module.css";

/** Props gắn lên phần tử kích hoạt (thường là `Button variant="icon"`). */
export interface TooltipTriggerProps {
  "aria-describedby": string;
  onFocus: () => void;
  onBlur: () => void;
}

export interface TooltipProps {
  /** Một câu giải thích ngắn. */
  content: string;
  /** Vẽ phần tử kích hoạt, rải `trigger` lên nó để nối `aria-describedby` và theo dõi focus. */
  children: (trigger: TooltipTriggerProps) => ReactNode;
  className?: string;
}

/**
 * Gợi ý nổi: hiện khi rê chuột hoặc focus vào phần tử kích hoạt (chờ 0,4 s rồi nổi lên có nảy, nên rê ngang qua
 * không bị nháy). Rê từ phần tử sang chính gợi ý vẫn giữ gợi ý (có vùng cầu nối trong suốt qua khoảng hở), `Esc` ẩn
 * cho tới khi rời chuột hoặc focus đi chỗ khác (WCAG 1.4.13). Chữ gợi ý luôn có trong DOM (`role="tooltip"`) để trình
 * đọc màn hình đọc qua `aria-describedby`.
 */
export function Tooltip({ content, children, className }: TooltipProps) {
  const tipId = useId();
  const hostRef = useRef<HTMLSpanElement>(null);
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  const showing = (hovered || focused) && !dismissed;

  // Rê chuột tính trên cả vùng (phần tử kích hoạt + gợi ý); nghe trực tiếp trên vùng vì đây không phải thao tác.
  useEffect(() => {
    const host = hostRef.current;
    if (host === null) return;
    function onPointerEnter(): void {
      setHovered(true);
    }
    function onPointerLeave(): void {
      setHovered(false);
      setDismissed(false);
    }
    host.addEventListener("pointerenter", onPointerEnter);
    host.addEventListener("pointerleave", onPointerLeave);
    return () => {
      host.removeEventListener("pointerenter", onPointerEnter);
      host.removeEventListener("pointerleave", onPointerLeave);
    };
  }, []);

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
    onFocus: () => setFocused(true),
    onBlur: () => {
      setFocused(false);
      setDismissed(false);
    },
  };

  return (
    <span
      ref={hostRef}
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
