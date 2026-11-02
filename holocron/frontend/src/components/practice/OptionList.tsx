"use client";

import { Icon } from "@/components/common/Icon";
import type { OptionState } from "@/lib/quiz";
import type { OptionKey } from "@/types/holocron";

import styles from "./Quiz.module.css";

interface OptionListProps {
  options: readonly OptionState[];
  /** Bấm một phương án: chọn và khoá ngay (một bước). */
  onPick: (key: OptionKey) => void;
  /** Dòng đọc kết quả ngay sau khi chọn (`aria-live="assertive"`). */
  announcement: string;
}

const STAR_PATH = "M12 3l2.2 6.8L21 12l-6.8 2.2L12 21l-2.2-6.8L3 12l6.8-2.2z";

/**
 * Bốn phương án là nút thật (`<button>`), phím số 1–4 hiện cạnh chữ. Sau khi chọn: đáp án đúng nền lan
 * `m-option is-right` + sao nhỏ + chữ "Đáp án đúng"; phương án sai đã chọn rung `m-option is-wrong` + chữ "Bạn chọn";
 * các phương án còn lại mờ. Đúng/sai luôn có chữ và icon, không chỉ màu.
 */
export function OptionList({ options, onPick, announcement }: OptionListProps) {
  return (
    <>
      <fieldset className={styles.options}>
        <legend className="visually-hidden">Các đáp án</legend>
        {options.map((option) => {
          const motion = option.tone === "right" ? "is-right" : option.tone === "wrong" ? "is-wrong" : "";
          return (
            <button
              key={option.key}
              type="button"
              className={`m-option ${motion} ${styles.option}`}
              data-tone={option.tone}
              disabled={option.locked}
              aria-label={option.ariaLabel}
              onClick={() => onPick(option.key)}
            >
              <span className={styles.kbd} aria-hidden="true">
                {option.digit}
              </span>
              <span className={styles.optionText}>{option.text}</span>
              {option.verdict !== null && (
                <span className={styles.verdict} data-tone={option.tone}>
                  {option.tone === "right" ? (
                    <svg className={`m-star-new ${styles.verdictStar}`} viewBox="0 0 24 24" aria-hidden="true">
                      <path d={STAR_PATH} fill="currentColor" />
                    </svg>
                  ) : (
                    <Icon name="wrong" size={18} />
                  )}
                  {option.verdict}
                </span>
              )}
            </button>
          );
        })}
      </fieldset>
      <p className="visually-hidden" aria-live="assertive">
        {announcement}
      </p>
    </>
  );
}
