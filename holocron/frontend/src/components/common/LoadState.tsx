import type { ReactNode } from "react";

import styles from "./LoadState.module.css";

/** Trạng thái đang tải: khung tĩnh kèm chữ cho trình đọc màn hình (không có hiệu ứng nhấp nháy). */
export function Loading({ label = "Đang tải…" }: { label?: string }) {
  return (
    <output className={styles.loading} aria-live="polite">
      <span className={styles.bar} aria-hidden="true" />
      <span>{label}</span>
    </output>
  );
}

interface EmptyStateProps {
  /** Vì sao trống. */
  title: string;
  /** Bước tiếp theo (chữ, liên kết hoặc nút). */
  children: ReactNode;
}

/** Trạng thái trống: một câu nói vì sao trống và một hành động kế tiếp. */
export function EmptyState({ title, children }: EmptyStateProps) {
  return (
    <div className={styles.empty}>
      <p className={styles.emptyTitle}>{title}</p>
      <div className={styles.emptyNext}>{children}</div>
    </div>
  );
}
