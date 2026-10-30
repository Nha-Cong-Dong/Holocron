"use client";

import { useEffect, useRef } from "react";

import { Icon, type IconName } from "@/components/common/Icon";
import { Button, useToast } from "@/components/ui";
import {
  RESULT_LEGEND_VI,
  REVIEW_WRONG_SOON_VI,
  type ResultRow,
  type ResultStatus,
  resultAriaLabel,
  resultStars,
  ringCenterLabel,
} from "@/lib/quiz";

import styles from "./Quiz.module.css";

const STAR_PATH = "M12 2l2.6 7.4L22 12l-7.4 2.6L12 22l-2.6-7.4L2 12l7.4-2.6z";

const ROW_ICON: Record<ResultStatus, IconName> = {
  correct: "correct",
  incorrect: "wrong",
  essay: "progress",
  unanswered: "warning",
};

interface ResultScreenProps {
  rows: readonly ResultRow[];
  /** "Làm đề mới": về màn thiết lập đề. */
  onNewExam: () => void;
}

/**
 * Kết quả (màn 10): vòng sao mỗi câu một sao (vàng = trắc nghiệm đúng, rỗng = sai, tím = tự luận, mờ = chưa làm), giữa
 * vòng là số câu của đề (không có số câu đúng hay điểm), danh sách từng câu kèm số trang. "Ôn lại câu sai" chưa có
 * (`aria-disabled`, vẫn nhận focus, bấm báo "sẽ có ở bản sau"); nút chính "Làm đề mới". Kết quả không được lưu.
 */
export function ResultScreen({ rows, onNewExam }: ResultScreenProps) {
  const toast = useToast();
  const headingRef = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    headingRef.current?.focus();
  }, []);

  return (
    <section className={styles.screen} aria-labelledby="quiz-result-title">
      <h2 id="quiz-result-title" ref={headingRef} className={styles.resultTitle} tabIndex={-1}>
        Kết quả
      </h2>
      <div className={styles.resultGrid}>
        <div className={styles.ringPanel}>
          <div className={styles.ring} role="img" aria-label={resultAriaLabel(rows)}>
            {resultStars(rows).map((star) => (
              <span key={star.twinId} className={styles.segPos} style={{ transform: `rotate(${star.angle}deg)` }}>
                <span
                  className={`m-result-star ${styles.seg}`}
                  data-tone={star.tone}
                  style={{ animationDelay: `${star.delayMs}ms` }}
                >
                  <svg viewBox="0 0 24 24" aria-hidden="true">
                    <path d={STAR_PATH} />
                  </svg>
                </span>
              </span>
            ))}
            <div className={styles.ringCenter} aria-hidden="true">
              <span className={styles.big}>{ringCenterLabel(rows)}</span>
            </div>
          </div>
          <ul className={styles.legend}>
            {RESULT_LEGEND_VI.map((text) => (
              <li key={text}>{text}</li>
            ))}
          </ul>
        </div>

        <div className={styles.resultSide}>
          <ol className={styles.rows} aria-label="Kết quả từng câu">
            {rows.map((row) => (
              <li key={row.twinId} className={styles.row}>
                <span className={styles.rowIcon} data-status={row.status}>
                  <Icon name={ROW_ICON[row.status]} size={20} />
                </span>
                <span className={styles.rowStatus}>{row.statusLabel}</span>
                <span className={styles.rowText} title={row.text}>
                  {row.text}
                </span>
                <span className={styles.rowPage}>{row.page}</span>
              </li>
            ))}
          </ol>
          <div className={styles.resultActions}>
            <button
              type="button"
              className={`m-btn-secondary ${styles.soonButton}`}
              aria-disabled="true"
              onClick={() => toast.show(REVIEW_WRONG_SOON_VI)}
            >
              Ôn lại câu sai <span className={styles.soon}>Sắp có</span>
            </button>
            <Button variant="primary" onClick={onNewExam}>
              Làm đề mới
            </Button>
          </div>
        </div>
      </div>
    </section>
  );
}
