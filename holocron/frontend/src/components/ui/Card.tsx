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
 * Thẻ: nghiêng nhẹ theo con trỏ chuột (tối đa 8°), viền vàng tím chạy vòng quanh khi rê chuột hoặc khi focus bàn phím
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
    function reset(): void {
      element?.style.removeProperty("--m-tilt-x");
      element?.style.removeProperty("--m-tilt-y");
    }
    function onPointerMove(event: PointerEvent): void {
      if (element === null || event.pointerType !== "mouse") return;
      const box = element.getBoundingClientRect();
      if (box.width === 0 || box.height === 0) return;
      const x = (event.clientX - box.left) / box.width - 0.5;
      const y = (event.clientY - box.top) / box.height - 0.5;
      element.style.setProperty("--m-tilt-x", `${(-y * 2 * MAX_TILT_DEG).toFixed(1)}deg`);
      element.style.setProperty("--m-tilt-y", `${(x * 2 * MAX_TILT_DEG).toFixed(1)}deg`);
    }
    element.addEventListener("pointermove", onPointerMove);
    element.addEventListener("pointerleave", reset);
    return () => {
      element.removeEventListener("pointermove", onPointerMove);
      element.removeEventListener("pointerleave", reset);
      reset();
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
