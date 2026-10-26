"use client";

import { useCallback, useEffect, useRef } from "react";

import { ErrorNotice } from "@/components/common/ErrorNotice";
import { Loading } from "@/components/common/LoadState";
import { USE_MOCKS } from "@/lib/config";
import { essayResultView } from "@/lib/essay";
import type { AnswerEntry } from "@/lib/practice";
import type { EssayQuestion } from "@/lib/practice-data";

import { CitationPanel } from "./CitationPanel";
import styles from "./Practice.module.css";

type EssayEntry = Extract<AnswerEntry, { kind: "essay" }>;

interface EssayQuestionViewProps {
  question: EssayQuestion;
  entry: EssayEntry;
  subjectCode: string;
  chapterCode: string | null;
  sourceOpen: boolean;
  onEdit: (text: string) => void;
  onSubmit: () => void;
  onToggleSource: () => void;
}

/**
 * Một câu tự luận "Trình bày khái niệm X": ô trả lời được focus sẵn, Ctrl+Enter nộp, backend chấm offline
 * (`POST /v1/twins/{id}/essay-grade`). Kết quả chỉ là "Khớp x/y ý" cùng các ý khớp và còn thiếu, không có điểm (§5.14).
 * Các ý của đáp án mẫu, lời giải và trích dẫn bị ẩn tới khi có kết quả (§6.9).
 */
export function EssayQuestionView({
  question,
  entry,
  subjectCode,
  chapterCode,
  sourceOpen,
  onEdit,
  onSubmit,
  onToggleSource,
}: EssayQuestionViewProps) {
  const resultRef = useRef<HTMLDivElement>(null);
  const graded = entry.status === "graded" && entry.result !== null;

  // Focus ô trả lời khi câu tự luận được mở (thay cho `autoFocus`); callback ổn định nên chỉ chạy khi ô được gắn mới.
  const focusOnMount = useCallback((element: HTMLTextAreaElement | null) => {
    element?.focus();
  }, []);

  useEffect(() => {
    if (graded) resultRef.current?.focus();
  }, [graded]);

  if (entry.status !== "graded" || entry.result === null) {
    const grading = entry.status === "grading";
    const answerId = `answer-${question.twinId}`;
    return (
      <form
        className={styles.form}
        onSubmit={(event) => {
          event.preventDefault();
          onSubmit();
        }}
      >
        <label className={styles.field} htmlFor={answerId}>
          Câu trả lời của bạn
        </label>
        <textarea
          id={answerId}
          ref={focusOnMount}
          className={styles.answer}
          value={entry.draft}
          onChange={(event) => onEdit(event.target.value)}
          readOnly={grading}
          aria-describedby={`${answerId}-hint`}
          spellCheck
        />
        <p id={`${answerId}-hint`} className={styles.hint}>
          Viết bằng lời của bạn rồi bấm Ctrl+Enter để nộp. Các ý của đáp án mẫu và trích dẫn nguồn hiện sau khi chấm.
        </p>
        {entry.error !== null && <ErrorNotice error={entry.error} onRetry={onSubmit} />}
        {grading ? (
          <Loading label="Đang chấm câu trả lời…" />
        ) : (
          <div className={styles.actions}>
            <button type="submit" className={styles.primary}>
              Nộp để chấm (Ctrl+Enter)
            </button>
          </div>
        )}
      </form>
    );
  }

  const view = essayResultView(entry.result);
  return (
    <div ref={resultRef} className={styles.result} tabIndex={-1} aria-live="polite">
      {USE_MOCKS && (
        <p className={styles.note}>
          <strong>Dữ liệu mẫu:</strong> kết quả dưới đây là kết quả cố định dựng sẵn cho câu này, không phải kết quả
          chấm câu trả lời bạn vừa viết.
        </p>
      )}
      <p className={styles.verdict}>{view.headline}</p>
      <p className={styles.limitation}>{view.limitation}</p>

      <section className={styles.source} aria-label="Câu trả lời của bạn">
        <h3 className={styles.sectionTitle}>Câu trả lời của bạn</h3>
        <p className={styles.answerReadOnly}>{entry.draft}</p>
      </section>

      <section className={styles.source} aria-label={view.missingTitle}>
        <h3 className={styles.sectionTitle}>{view.missingTitle}</h3>
        {view.missingEmpty !== null ? (
          <p className={styles.muted}>{view.missingEmpty}</p>
        ) : (
          <ul className={styles.points}>
            {view.missing.map((point) => (
              <li key={point.index}>{point.text}</li>
            ))}
          </ul>
        )}
      </section>

      <section className={styles.source} aria-label={view.matchedTitle}>
        <h3 className={styles.sectionTitle}>{view.matchedTitle}</h3>
        {view.matchedEmpty !== null ? (
          <p className={styles.muted}>{view.matchedEmpty}</p>
        ) : (
          <ul className={styles.points}>
            {view.matched.map((point) => (
              <li key={point.index}>{point.text}</li>
            ))}
          </ul>
        )}
      </section>

      <CitationPanel
        citation={view.citation}
        subjectCode={subjectCode}
        chapterCode={chapterCode}
        open={sourceOpen}
        onToggle={onToggleSource}
      />
    </div>
  );
}
