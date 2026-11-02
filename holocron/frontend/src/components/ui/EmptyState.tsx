"use client";

import type { ReactNode } from "react";

import { Button } from "./Button";
import styles from "./EmptyState.module.css";

export interface EmptyStateAction {
  label: string;
  onClick: () => void;
}

export interface EmptyStateProps {
  /** Nói ngắn màn đang trống vì sao, ví dụ "Chưa có tài liệu nào". */
  title: string;
  /** Một câu nói bước tiếp theo. */
  description?: string;
  /** Đúng một hành động (nút chính của màn). */
  action: EmptyStateAction;
  /** Hình minh hoạ riêng; mặc định là một chòm sao nhỏ. */
  illustration?: ReactNode;
  className?: string;
}

/** Chòm sao nhỏ: ba sao nối nét, một sao chờ (viền tím). Chỉ trang trí. */
function Constellation() {
  return (
    <svg className={styles.art} viewBox="0 0 120 80" aria-hidden="true">
      <path className={styles.artLine} d="M18 58L50 26L82 44L104 16" />
      <path className={styles.artStar} d="M18 50l2.4 5.6L26 58l-5.6 2.4L18 66l-2.4-5.6L10 58l5.6-2.4z" />
      <path className={styles.artStar} d="M50 18l2.4 5.6L58 26l-5.6 2.4L50 34l-2.4-5.6L42 26l5.6-2.4z" />
      <path className={styles.artStar} d="M82 36l2.4 5.6L90 44l-5.6 2.4L82 52l-2.4-5.6L74 44l5.6-2.4z" />
      <path className={styles.artPending} d="M104 8l2.4 5.6L112 16l-5.6 2.4L104 24l-2.4-5.6L96 16l5.6-2.4z" />
    </svg>
  );
}

/** Trạng thái rỗng: hình minh hoạ, một câu nói vì sao trống, đúng một hành động để đi tiếp. */
export function EmptyState({ title, description, action, illustration, className }: EmptyStateProps) {
  return (
    <section className={[styles.root, className ?? ""].filter((part) => part !== "").join(" ")}>
      {illustration ?? <Constellation />}
      <h2 className={styles.title}>{title}</h2>
      {description !== undefined && <p className={styles.description}>{description}</p>}
      <Button variant="primary" onClick={action.onClick}>
        {action.label}
      </Button>
    </section>
  );
}
