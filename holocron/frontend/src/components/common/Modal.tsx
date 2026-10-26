"use client";

import { type ReactNode, useEffect, useRef } from "react";

import styles from "./Modal.module.css";

interface ModalProps {
  open: boolean;
  onClose: () => void;
  /** `id` của tiêu đề hộp thoại. */
  labelledBy: string;
  children: ReactNode;
  width?: "narrow" | "wide";
}

/**
 * Hộp thoại dùng `<dialog>` gốc ở chế độ modal: trình duyệt giữ focus trong hộp, `Esc` đóng, focus trở lại phần tử
 * trước đó khi đóng. Nội dung chỉ dựng khi mở nên mỗi lần mở bắt đầu từ trạng thái sạch.
 */
export function Modal({ open, onClose, labelledBy, children, width = "narrow" }: ModalProps) {
  const ref = useRef<HTMLDialogElement>(null);
  // Sự kiện `close` đến sau, bất đồng bộ: phân biệt lần đóng do chính component với lần trình duyệt tự đóng.
  const closingRef = useRef(false);

  useEffect(() => {
    const dialog = ref.current;
    if (dialog === null) return;
    if (open && !dialog.open) {
      dialog.showModal();
    } else if (!open && dialog.open) {
      closingRef.current = true;
      dialog.close();
    }
  }, [open]);

  return (
    <dialog
      ref={ref}
      className={styles.dialog}
      data-width={width}
      aria-labelledby={labelledBy}
      onClose={() => {
        if (closingRef.current) {
          closingRef.current = false;
          return;
        }
        onClose();
      }}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
    >
      {open && children}
    </dialog>
  );
}
