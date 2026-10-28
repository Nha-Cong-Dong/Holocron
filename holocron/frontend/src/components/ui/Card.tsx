"use client";

import { type ReactNode, useEffect, useRef } from "react";

import { useReducedMotion } from "@/hooks/motion-hooks";

import styles from "./Card.module.css";

/** Góc nghiêng tối đa (độ) theo mỗi trục khi rê chuột tới mép thẻ. */
const MAX_TILT_DEG = 8;

export interface CardProps {
  children: ReactNode;
  className?: string;
  /** Thẻ nghiêng theo con trỏ chuột; mặc định bật. Tắt khi thẻ chứa vùng cần rê chuột chính xác (ví dụ bôi chữ). */
  tilt?: boolean;
  /** Thẻ là một mục trong danh sách (`<li>`) hay khối độc lập (`<div>`). */
  as?: "div" | "li" | "article";
}

/**
 * Thẻ: nghiêng nhẹ theo con trỏ chuột (tối đa 8°), viền vàng chạy vòng quanh khi rê chuột hoặc khi focus bàn phím
 * ở trong thẻ (viền chạy trong lớp `.m-card-ring` riêng; thẻ không cắt overflow nên bảng thả xuống, gợi ý nổi bên trong
 * vẫn tràn ra được). Giảm chuyển động: không nghiêng, viền không chạy. Hành động của thẻ là nút hoặc link bên trong.
 */
export function Card({ children, className, tilt = true, as: Tag = "div" }: CardProps) {
  const ref = useRef<HTMLElement | null>(null);
  const reduced = useReducedMotion();

  // Nghiêng là hiệu ứng trang trí theo con trỏ, không phải thao tác: nghe sự kiện con trỏ trực tiếp trên phần tử.
  useEffect(() => {
    const element = ref.current;
    if (element === null || !tilt || reduced) return;
    // Hộp của thẻ đo lúc con trỏ vào (thẻ chưa nghiêng), không đo lại hộp đang nghiêng mỗi lần di chuột.
    let box: DOMRect | null = null;
    function clearTilt(): void {
      element?.style.removeProperty("--m-tilt-x");
      element?.style.removeProperty("--m-tilt-y");
    }
    function onPointerEnter(): void {
      box = element?.getBoundingClientRect() ?? null;
    }
    function onPointerMove(event: PointerEvent): void {
      if (element === null || event.pointerType !== "mouse") return;
      // Vào trước khi effect gắn sự kiện, hoặc vừa cuộn trang: đo lại.
      box ??= element.getBoundingClientRect();
      if (box.width === 0 || box.height === 0) return;
      const x = Math.min(Math.max((event.clientX - box.left) / box.width, 0), 1) - 0.5;
      const y = Math.min(Math.max((event.clientY - box.top) / box.height, 0), 1) - 0.5;
      element.style.setProperty("--m-tilt-x", `${(-y * 2 * MAX_TILT_DEG).toFixed(1)}deg`);
      element.style.setProperty("--m-tilt-y", `${(x * 2 * MAX_TILT_DEG).toFixed(1)}deg`);
    }
    function onPointerLeave(): void {
      box = null;
      clearTilt();
    }
    // Cuộn trang khi đang rê: hộp đã đo không còn đúng chỗ, bỏ nghiêng và đo lại ở lần di chuột kế tiếp.
    function onScroll(): void {
      if (box === null) return;
      box = null;
      clearTilt();
    }
    element.addEventListener("pointerenter", onPointerEnter);
    element.addEventListener("pointermove", onPointerMove);
    element.addEventListener("pointerleave", onPointerLeave);
    window.addEventListener("scroll", onScroll, { capture: true, passive: true });
    return () => {
      element.removeEventListener("pointerenter", onPointerEnter);
      element.removeEventListener("pointermove", onPointerMove);
      element.removeEventListener("pointerleave", onPointerLeave);
      window.removeEventListener("scroll", onScroll, { capture: true });
      clearTilt();
    };
  }, [tilt, reduced]);

  return (
    <Tag
      ref={(element: HTMLElement | null) => {
        ref.current = element;
      }}
      className={["m-card", styles.card, className ?? ""].filter((part) => part !== "").join(" ")}
    >
      <span className="m-card-ring" aria-hidden="true" />
      {children}
    </Tag>
  );
}
