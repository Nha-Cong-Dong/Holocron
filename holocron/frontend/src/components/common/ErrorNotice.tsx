"use client";

import { actionLabel, keyedMessages, type UiError } from "@/lib/errors";

import styles from "./ErrorNotice.module.css";

interface ErrorNoticeProps {
  error: UiError;
  /** Gửi lại đúng thao tác vừa lỗi (nút "Thử lại"). Không truyền thì lỗi cần thử lại không có nút. */
  onRetry?: () => void;
  /** Đọc lại bản mới nhất từ backend (nút "Tải lại bản mới nhất"); KHÔNG được gửi lại thao tác ghi vừa lỗi. */
  onReload?: () => void;
}

/**
 * Lỗi đã phân loại (lib/errors.ts): thông điệp ngắn tiếng Việt và nút theo loại lỗi, không tự thử lại. Mỗi loại nút
 * có hàm riêng, để nút "Tải lại" không thể vô tình gửi lại một thao tác ghi.
 */
export function ErrorNotice({ error, onRetry, onReload }: ErrorNoticeProps) {
  const label = actionLabel(error.action);
  const handler = error.action === "retry" ? onRetry : error.action === "reload" ? onReload : undefined;
  const tone = error.kind === "llm_upstream" || error.kind === "capability" ? "warning" : "danger";
  return (
    <div className={styles.notice} data-tone={tone} role="alert">
      <p>{error.message}</p>
      {error.issues.length > 0 && (
        <ul className={styles.issues}>
          {keyedMessages(error.issues).map(({ key, message }) => (
            <li key={key}>{message}</li>
          ))}
        </ul>
      )}
      {label !== null && handler !== undefined && (
        <button type="button" className={styles.action} onClick={handler}>
          {label}
        </button>
      )}
    </div>
  );
}
