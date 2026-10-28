import { progressAriaMax, progressAriaNow, progressRatio, progressValueText } from "@/lib/progress";

import styles from "./Progress.module.css";

const STAR_PATH = "M12 2l2.6 7.4L22 12l-7.4 2.6L12 22l-2.6-7.4L2 12l7.4-2.6z";

export interface ProgressProps {
  /** Số đã xong, do backend trả (hoặc đếm phần tử đang có trên màn); component không tự tính số hiển thị. */
  value: number;
  max: number;
  /** Đơn vị trong chữ "x/y <đơn vị>", ví dụ "trang", "câu", "dòng". */
  unit: string;
  /** Tên thanh cho trình đọc màn hình, ví dụ "Tiến độ đọc Bài giảng chương 2". */
  label: string;
  /** Đang làm: sọc chạy trên phần đã xong. */
  active?: boolean;
  /** Hiện chữ "x/y <đơn vị>" dưới thanh. */
  showText?: boolean;
  className?: string;
}

/**
 * Thanh tiến độ `role="progressbar"` (`aria-valuenow/min/max`, `aria-valuetext` "x/y <đơn vị>"). Phần đã xong dài ra
 * mượt, có sọc chạy khi đang làm, một ngôi sao nhỏ đi ở đầu thanh. Tỉ lệ chỉ dùng cho bố cục (`lib/progress.ts`).
 */
export function Progress({ value, max, unit, label, active = false, showText = false, className }: ProgressProps) {
  const ratio = progressRatio(value, max);
  const text = progressValueText(value, max, unit);
  return (
    <div className={[styles.root, className ?? ""].filter((part) => part !== "").join(" ")}>
      <div
        role="progressbar"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={progressAriaMax(max)}
        aria-valuenow={progressAriaNow(value, max)}
        aria-valuetext={text}
        className={`m-progress ${styles.track}`}
        data-active={active ? "true" : undefined}
      >
        <div className={`m-progress-fill ${styles.fill}`} style={{ transform: `scaleX(${ratio})` }} />
        <div className="m-progress-tip" style={{ transform: `translateX(${ratio * 100}%)` }} aria-hidden="true">
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d={STAR_PATH} fill="currentColor" />
          </svg>
        </div>
      </div>
      {showText && (
        <p className={styles.text} aria-hidden="true">
          {text}
        </p>
      )}
    </div>
  );
}
