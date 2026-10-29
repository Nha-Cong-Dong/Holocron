"use client";

import { Button, Progress } from "@/components/ui";

import styles from "./Quiz.module.css";

interface QuizHeaderProps {
  /** Vị trí câu đang mở (từ 0) trong danh sách câu backend trả. */
  index: number;
  /** Số câu của đề (độ dài danh sách backend trả). */
  total: number;
  /** Câu đang mở đã trả lời: thanh tiến độ tính cả câu này. */
  answered: boolean;
  /** "Thoát": về màn thiết lập đề, không hỏi lại (kết quả không được lưu). */
  onExit: () => void;
}

/** Thanh trên của màn Làm bài: "Thoát" · thanh tiến độ · "Câu k/N" (đọc bằng `aria-live`). */
export function QuizHeader({ index, total, answered, onExit }: QuizHeaderProps) {
  return (
    <div className={styles.top}>
      <Button variant="secondary" onClick={onExit}>
        <svg className={styles.backIcon} viewBox="0 0 24 24" aria-hidden="true">
          <path d="M15 6l-6 6 6 6" />
        </svg>
        Thoát
      </Button>
      <Progress
        value={index + (answered ? 1 : 0)}
        max={total}
        unit="câu"
        label="Tiến độ bài làm"
        className={styles.bar}
      />
      <span className={styles.count} aria-live="polite">
        Câu {index + 1}/{total}
      </span>
    </div>
  );
}
