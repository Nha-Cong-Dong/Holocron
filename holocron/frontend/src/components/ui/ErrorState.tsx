"use client";

import { Icon } from "@/components/common/Icon";

import { Accordion } from "./Accordion";
import { Button } from "./Button";
import styles from "./ErrorState.module.css";

export interface ErrorStateProps {
  /** Việc gì đã xảy ra, ví dụ "Không đọc được “Bài giảng ngắt.pdf”." */
  title: string;
  /** Vì sao và cách sửa, ví dụ "File đang đặt mật khẩu. Bỏ mật khẩu rồi bấm Thử lại." */
  fix: string;
  /** Làm lại đúng việc vừa lỗi (nút "Thử lại"). */
  onRetry: () => void;
  /** Mã lỗi, thông số kỹ thuật: chỉ hiện trong mục thu gọn "Chi tiết kỹ thuật". */
  technical?: string;
  busy?: boolean;
  className?: string;
}

/**
 * Trạng thái lỗi: việc gì xảy ra, cách sửa, nút "Thử lại" làm đúng điều nó nói. Không xin lỗi; mã kỹ thuật chỉ nằm
 * trong "Chi tiết kỹ thuật".
 */
export function ErrorState({ title, fix, onRetry, technical, busy = false, className }: ErrorStateProps) {
  return (
    <section className={[styles.root, className ?? ""].filter((part) => part !== "").join(" ")} role="alert">
      <div className={styles.head}>
        <span className={styles.icon}>
          <Icon name="warning" size={20} />
        </span>
        <div className={styles.text}>
          <h2 className={styles.title}>{title}</h2>
          <p className={styles.fix}>{fix}</p>
        </div>
      </div>
      <div className={styles.actions}>
        <Button variant="secondary" busy={busy} busyLabel="Đang thử lại" onClick={onRetry}>
          Thử lại
        </Button>
      </div>
      {technical !== undefined && technical.trim() !== "" && (
        <Accordion summary="Chi tiết kỹ thuật" className={styles.details}>
          <code className={styles.code}>{technical}</code>
        </Accordion>
      )}
    </section>
  );
}
