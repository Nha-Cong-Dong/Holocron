import { actionLabel, type UiError } from "@/lib/errors";

import styles from "./ErrorNotice.module.css";

interface ErrorNoticeProps {
  error: UiError;
  /** Chạy khi bấm nút hành động (tải lại hoặc thử lại); không truyền thì không có nút. */
  onAction?: () => void;
}

/** Lỗi đã phân loại (lib/errors.ts): thông điệp ngắn tiếng Việt và nút theo loại lỗi, không tự thử lại. */
export function ErrorNotice({ error, onAction }: ErrorNoticeProps) {
  const label = actionLabel(error.action);
  const tone = error.kind === "llm_upstream" || error.kind === "capability" ? "warning" : "danger";
  return (
    <div className={styles.notice} data-tone={tone} role="alert">
      <p>{error.message}</p>
      {error.issues.length > 0 && (
        <ul className={styles.issues}>
          {error.issues.map((issue) => (
            <li key={issue}>{issue}</li>
          ))}
        </ul>
      )}
      {label !== null && onAction !== undefined && (
        <button type="button" className={styles.action} onClick={onAction}>
          {label}
        </button>
      )}
    </div>
  );
}
