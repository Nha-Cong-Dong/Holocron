"use client";

import { ErrorNotice } from "@/components/common/ErrorNotice";
import type { UiError } from "@/lib/errors";
import { type PairOperation, pairErrorAction } from "@/lib/pairs";

import styles from "./Pairs.module.css";

interface PairErrorNoticeProps {
  error: UiError;
  operation: PairOperation;
  /** Gửi lại đúng thao tác (chỉ xác nhận, từ chối). */
  onRetry: () => void;
  /** Đọc lại danh sách cặp từ backend; KHÔNG gửi lại thao tác ghi. */
  onReloadList: () => void;
  /** Đọc lại cả chunk của môn lẫn danh sách cặp. */
  onReloadAll: () => void;
}

/**
 * Lỗi của một thao tác ghi với cặp: thông điệp qua `ErrorNotice` (không dùng nút chung của nó), còn nút chọn theo
 * `pairErrorAction`, để tạo cặp lỗi mạng không bao giờ có "Thử lại" (có thể tạo trùng).
 */
export function PairErrorNotice({ error, operation, onRetry, onReloadList, onReloadAll }: PairErrorNoticeProps) {
  const action = pairErrorAction(operation, error);
  const handler =
    action === null
      ? null
      : action.kind === "retry"
        ? onRetry
        : action.kind === "reload_list"
          ? onReloadList
          : onReloadAll;
  return (
    <div>
      <ErrorNotice error={{ ...error, action: "none" }} />
      {action !== null && handler !== null && (
        <div className={styles.actions}>
          <button type="button" className={styles.secondary} onClick={handler}>
            {action.label}
          </button>
        </div>
      )}
    </div>
  );
}
