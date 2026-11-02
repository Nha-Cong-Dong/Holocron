"use client";

import { type KeyboardEvent, useCallback, useRef } from "react";

import { Icon } from "@/components/common/Icon";
import { Button, ErrorState } from "@/components/ui";
import { screenErrorText } from "@/lib/concept-board";
import { USE_MOCKS } from "@/lib/config";
import { ESSAY_LIMITATION_VI, essayHeadline } from "@/lib/essay";
import type { AnswerEntry } from "@/lib/practice";
import type { EssayPointView, EssayQuestion } from "@/lib/practice-data";
import { answerHeldKey, essayErrorPlacement } from "@/lib/quiz";

import styles from "./Quiz.module.css";

type EssayEntry = Extract<AnswerEntry, { kind: "essay" }>;

interface EssayQuestionViewProps {
  question: EssayQuestion;
  entry: EssayEntry;
  onEdit: (text: string) => void;
  /** Gửi chấm lại ("Thử lại" sau lỗi). */
  onRetry: () => void;
  /** Bỏ qua câu đang chấm lỗi: sang câu tiếp, câu này tính "Chưa làm". */
  onSkip: () => void;
}

interface PointLine {
  point: EssayPointView;
  matched: boolean;
}

/** Các ý theo thứ tự của tài liệu, mỗi ý ghi đã có hay còn thiếu (văn bản ý do backend trả, nguyên văn). */
function pointLines(matched: readonly EssayPointView[], missing: readonly EssayPointView[]): PointLine[] {
  return [
    ...matched.map((point) => ({ point, matched: true })),
    ...missing.map((point) => ({ point, matched: false })),
  ].sort((a, b) => a.point.index - b.point.index);
}

/**
 * Một câu tự luận (màn 9): ô "Câu trả lời của bạn" nhận focus khi mở câu, Ctrl+Enter hoặc "Chấm ý" để chấm offline
 * (`POST /v1/twins/{id}/essay-grade`). Kết quả chỉ là "Khớp x/y ý" với hai số backend trả, kèm câu nói rõ đây không
 * phải điểm và giới hạn của cách chấm (§5.14), rồi từng ý "Có:" hoặc "Còn thiếu:". Chấm lỗi: khung lỗi kèm "Thử lại"
 * và "Sang câu tiếp".
 */
export function EssayQuestionView({ question, entry, onEdit, onRetry, onSkip }: EssayQuestionViewProps) {
  const answerId = `answer-${question.twinId}`;
  const graded = entry.status === "graded" && entry.result !== null;
  const placement = entry.error === null ? null : essayErrorPlacement(entry.error);

  // Focus ô trả lời khi câu tự luận được mở (thay cho `autoFocus`); callback ổn định nên chỉ chạy khi ô được gắn mới.
  const focusOnMount = useCallback((element: HTMLTextAreaElement | null) => {
    element?.focus();
  }, []);

  // Câu mở bằng phím đang giữ (giữ Enter trên "Câu tiếp"): bỏ các lần lặp của phím đó cho tới lần nhấn mới đầu tiên,
  // để nháp không bị ghi dòng trống. Khung câu gắn `key` theo câu nên mỗi câu bắt đầu ở trạng thái "vừa mở".
  const heldKeyArmed = useRef(true);
  function onAnswerKeyDown(event: KeyboardEvent<HTMLTextAreaElement>): void {
    const guard = answerHeldKey(heldKeyArmed.current, event.repeat);
    heldKeyArmed.current = guard.armed;
    if (guard.block) event.preventDefault();
  }

  return (
    <>
      <div className={styles.field}>
        <label className={styles.fieldLabel} htmlFor={answerId}>
          Câu trả lời của bạn
        </label>
        <textarea
          id={answerId}
          ref={focusOnMount}
          className={styles.answer}
          value={entry.draft}
          onChange={(event) => onEdit(event.target.value)}
          onKeyDown={onAnswerKeyDown}
          readOnly={entry.status !== "editing"}
          aria-describedby={`${answerId}-hint`}
          aria-invalid={placement === "field" || undefined}
          spellCheck
        />
        <span id={`${answerId}-hint`} className={styles.caption}>
          Viết ý chính là đủ. Ctrl + Enter để chấm.
        </span>
        {entry.error !== null && placement === "field" && (
          <p className={styles.fieldError} role="alert">
            {entry.error.message}
          </p>
        )}
      </div>

      {entry.error !== null && placement === "screen" && (
        <>
          <ErrorState
            title="Không chấm được câu này"
            fix={screenErrorText(entry.error)}
            onRetry={onRetry}
            technical={entry.error.diagnostic}
          />
          <div className={styles.skipActions}>
            <Button variant="secondary" onClick={onSkip}>
              Sang câu tiếp
            </Button>
          </div>
        </>
      )}

      {graded && entry.result !== null && (
        <div className={styles.essayResult} role="status">
          {USE_MOCKS && (
            <p className={styles.mockNote}>
              <strong>Dữ liệu mẫu:</strong> kết quả dưới đây là kết quả cố định dựng sẵn cho câu này, không phải kết quả
              chấm câu trả lời bạn vừa viết.
            </p>
          )}
          <div className={styles.essayHead}>
            <span className={styles.matchBadge}>{essayHeadline(entry.result)}</span>
            <span className={styles.caption}>So với ý trong tài liệu. Đây không phải điểm chấm.</span>
          </div>
          <p className={styles.caption}>{ESSAY_LIMITATION_VI}</p>
          <ul className={`m-points ${styles.points}`}>
            {pointLines(entry.result.matched, entry.result.missing).map(({ point, matched }) => (
              <li key={point.index} data-matched={matched}>
                <span className={styles.pointIcon} data-matched={matched}>
                  <Icon name={matched ? "correct" : "warning"} size={20} />
                </span>
                <span>
                  <b>{matched ? "Có:" : "Còn thiếu:"}</b> {point.text}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </>
  );
}
