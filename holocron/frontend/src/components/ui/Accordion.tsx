import type { ReactNode } from "react";

import styles from "./Accordion.module.css";

export interface AccordionProps {
  /** Dòng tiêu đề luôn nhìn thấy (ví dụ "Chi tiết kỹ thuật", "Tuỳ chọn nâng cao", "Thử nghiệm"). */
  summary: ReactNode;
  children: ReactNode;
  defaultOpen?: boolean;
  className?: string;
}

/**
 * Mục thu gọn bằng `<details>/<summary>` gốc (bàn phím và trình đọc màn hình có sẵn). Mở: mũi tên xoay, nội dung trượt
 * xuống, từng dòng con hiện lần lượt.
 */
export function Accordion({ summary, children, defaultOpen = false, className }: AccordionProps) {
  return (
    <details
      className={["m-accordion", styles.accordion, className ?? ""].filter((part) => part !== "").join(" ")}
      open={defaultOpen}
    >
      <summary className={styles.summary}>
        <svg className={`m-chevron ${styles.chevron}`} viewBox="0 0 24 24" aria-hidden="true">
          <path d="M9 6l6 6-6 6" />
        </svg>
        {summary}
      </summary>
      <div className={`m-accordion-body ${styles.body}`}>{children}</div>
    </details>
  );
}
