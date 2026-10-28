"use client";

import { type ReactNode, useEffect, useId, useRef, useState } from "react";

import { useReducedMotion } from "@/hooks/motion-hooks";

import styles from "./Dialog.module.css";

/** Dùng khi không đọc được `--d-short` từ CSS. */
const DEFAULT_CLOSE_MS = 200;

/** Đọc một token thời lượng CSS ("200ms", "0.2s") ra mili giây. */
function cssDurationMs(element: Element, token: string): number {
  const raw = getComputedStyle(element).getPropertyValue(token).trim();
  const match = /^(\d+(?:\.\d+)?)(ms|s)$/.exec(raw);
  if (match === null) return DEFAULT_CLOSE_MS;
  const amount = Number(match[1]);
  return match[2] === "s" ? amount * 1000 : amount;
}

export interface DialogProps {
  open: boolean;
  /** Người dùng muốn đóng (Esc hoặc nút trong hộp thoại gọi). Component cha đặt `open=false`. */
  onClose: () => void;
  title: string;
  /** Một dòng dưới tiêu đề, nói việc cần làm. */
  description?: string;
  children?: ReactNode;
  /** Hàng nút cuối hộp thoại (đúng một nút chính). */
  footer?: ReactNode;
  className?: string;
}

/**
 * Hộp thoại bọc `<dialog>` gốc mở bằng `showModal()`: bẫy focus, `Esc` và trả focus về chỗ cũ là việc của trình duyệt.
 * Mở: lật xuống như nắp khối mở ra, nền mờ là `::backdrop`. Đóng: thêm `is-closing`, chờ `animationend` (dự phòng hẹn
 * giờ bằng `--d-short`) rồi mới `close()`; giảm chuyển động thì đóng ngay. Nội dung giữ nguyên trong lúc đóng.
 */
export function Dialog({ open, onClose, title, description, children, footer, className }: DialogProps) {
  const ref = useRef<HTMLDialogElement>(null);
  const reduced = useReducedMotion();
  const baseId = useId();
  const titleId = `${baseId}-title`;
  const descriptionId = `${baseId}-description`;
  // Nội dung dựng từ lúc mở tới khi animation đóng xong, nên mỗi lần mở bắt đầu từ trạng thái sạch.
  const [rendered, setRendered] = useState(open);
  if (open && !rendered) setRendered(true);
  // Sự kiện `close` gốc đến sau, bất đồng bộ: phân biệt lần đóng do component với lần trình duyệt tự đóng.
  const closingRef = useRef(false);

  useEffect(() => {
    const dialog = ref.current;
    if (dialog === null) return;
    if (open) {
      dialog.classList.remove("is-closing");
      if (!dialog.open) dialog.showModal();
      return;
    }
    if (!dialog.open) return;
    if (reduced) {
      closingRef.current = true;
      dialog.close();
      setRendered(false);
      return;
    }
    let finished = false;
    function finish(): void {
      if (finished || dialog === null) return;
      finished = true;
      dialog.removeEventListener("animationend", onAnimationEnd);
      window.clearTimeout(timer);
      dialog.classList.remove("is-closing");
      closingRef.current = true;
      dialog.close();
      setRendered(false);
    }
    function onAnimationEnd(event: AnimationEvent): void {
      if (event.target === dialog && event.pseudoElement === "") finish();
    }
    dialog.classList.add("is-closing");
    dialog.addEventListener("animationend", onAnimationEnd);
    const timer = window.setTimeout(finish, cssDurationMs(dialog, "--d-short"));
    return () => {
      if (finished) return;
      dialog.removeEventListener("animationend", onAnimationEnd);
      window.clearTimeout(timer);
      dialog.classList.remove("is-closing");
    };
  }, [open, reduced]);

  return (
    <dialog
      ref={ref}
      className={["m-dialog", styles.dialog, className ?? ""].filter((part) => part !== "").join(" ")}
      aria-labelledby={titleId}
      aria-describedby={description === undefined ? undefined : descriptionId}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onClose={() => {
        if (closingRef.current) {
          closingRef.current = false;
          return;
        }
        // Trình duyệt tự đóng (ví dụ `<form method="dialog">`): gỡ nội dung như lần đóng thường rồi báo component cha.
        ref.current?.classList.remove("is-closing");
        setRendered(false);
        onClose();
      }}
    >
      {rendered && (
        <div className={styles.inner}>
          <h2 id={titleId} className={styles.title}>
            {title}
          </h2>
          {description !== undefined && (
            <p id={descriptionId} className={styles.description}>
              {description}
            </p>
          )}
          {children !== undefined && <div className={styles.body}>{children}</div>}
          {footer !== undefined && <div className={styles.footer}>{footer}</div>}
        </div>
      )}
    </dialog>
  );
}
