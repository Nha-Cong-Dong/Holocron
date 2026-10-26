"use client";

import { useCallback, useEffect, useReducer, useRef } from "react";

import { describeTarget } from "@/components/shell/keyboard";
import { useShell } from "@/components/shell/ShellContext";
import { api, toUiError } from "@/lib/api";
import { localError } from "@/lib/errors";
import {
  entrySummary,
  essaySubmission,
  initialPracticeState,
  isAnswered,
  type PracticeState,
  practiceKeyEffect,
  practiceReducer,
  practiceScope,
} from "@/lib/practice";
import type { PracticeBlueprint, PracticeQuestion } from "@/lib/practice-data";
import { resolveShortcut, targetKindOf } from "@/lib/shortcuts";

import { EssayQuestionView } from "./EssayQuestionView";
import { McqQuestionView } from "./McqQuestionView";
import styles from "./Practice.module.css";

interface PracticeSessionProps {
  questions: PracticeQuestion[];
  blueprint: PracticeBlueprint;
  subjectCode: string;
  onNewExam: () => void;
}

const KIND_LABEL: Record<PracticeQuestion["kind"], string> = {
  mcq: "Trắc nghiệm",
  essay: "Tự luận",
};

/**
 * Làm bài từng câu một. Phím tắt (kế hoạch UI mục 5.7) chỉ có tác dụng khi không mở hộp lệnh hay bảng phím tắt, và
 * phím một ký tự không có tác dụng khi đang gõ trong ô trả lời.
 */
export function PracticeSession({ questions, blueprint, subjectCode, onNewExam }: PracticeSessionProps) {
  const { overlayOpen } = useShell();
  const [state, dispatch] = useReducer(practiceReducer, questions, initialPracticeState);
  const headingRef = useRef<HTMLHeadingElement>(null);
  // Bản mới nhất của trạng thái cho handler phím (gắn một lần) và cho lời gọi chấm bất đồng bộ.
  const stateRef = useRef<PracticeState>(state);
  useEffect(() => {
    stateRef.current = state;
  }, [state]);

  const chapterOf = useCallback(
    (slotIndex: number): string | null =>
      blueprint.slots.find((slot) => slot.slotIndex === slotIndex)?.chapterCode ?? null,
    [blueprint],
  );

  const submitEssay = useCallback(() => {
    const current = stateRef.current;
    const question = current.questions[current.index];
    const entry = current.entries[current.index];
    if (current.finished || question?.kind !== "essay" || entry?.kind !== "essay" || entry.status !== "editing") return;
    const submission = essaySubmission(entry.draft);
    if (!submission.ok) {
      dispatch({ type: "essay_rejected", error: localError(submission.message) });
      return;
    }
    const twinId = question.twinId;
    dispatch({ type: "essay_started", twinId });
    api
      .gradeEssay(twinId, submission.answer)
      .then((result) => dispatch({ type: "essay_graded", twinId, result }))
      .catch((error: unknown) => dispatch({ type: "essay_failed", twinId, error: toUiError(error) }));
  }, []);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent): void {
      if (overlayOpen || event.defaultPrevented) return;
      const action = resolveShortcut(
        {
          key: event.key,
          ctrlKey: event.ctrlKey,
          metaKey: event.metaKey,
          altKey: event.altKey,
          target: targetKindOf(describeTarget(event.target)),
        },
        { overlayOpen: false, scope: practiceScope(stateRef.current) },
      );
      const effect = action === null ? null : practiceKeyEffect(action);
      if (effect === null) return;
      event.preventDefault();
      if (effect.kind === "submit_essay") submitEssay();
      else dispatch(effect.action);
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [overlayOpen, submitEssay]);

  // Khi chuyển câu (không phải câu tự luận đang viết, ô trả lời tự nhận focus), đưa focus về tiêu đề câu.
  const currentQuestion = state.questions[state.index];
  const currentKind = currentQuestion?.kind;
  const currentTwinId = currentQuestion?.twinId;
  useEffect(() => {
    if (state.finished || currentTwinId === undefined) return;
    if (currentKind === "mcq") headingRef.current?.focus();
  }, [currentTwinId, state.finished, currentKind]);

  if (state.finished) {
    return (
      <SessionSummary
        state={state}
        onReview={(index) => dispatch({ type: "go_to", index })}
        onRestart={() => dispatch({ type: "restart" })}
        onNewExam={onNewExam}
      />
    );
  }

  const entry = state.entries[state.index];
  if (currentQuestion === undefined || entry === undefined) return null;
  const isLast = state.index === state.questions.length - 1;
  const chapterCode = chapterOf(currentQuestion.slotIndex);

  return (
    <div className={styles.page}>
      <nav aria-label="Các câu trong đề">
        <ol className={styles.progress}>
          {state.questions.map((question, index) => {
            const item = state.entries[index];
            const done = item !== undefined && isAnswered(item);
            return (
              <li key={question.twinId}>
                <button
                  type="button"
                  className={styles.stepButton}
                  aria-current={index === state.index ? "step" : undefined}
                  aria-label={`Câu ${index + 1}${done ? ", đã làm" : ""}`}
                  data-done={done}
                  onClick={() => dispatch({ type: "go_to", index })}
                >
                  {index + 1}
                </button>
              </li>
            );
          })}
        </ol>
      </nav>

      <article className={styles.panel} aria-labelledby="practice-question-title">
        <header className={styles.questionHeader}>
          <h2 id="practice-question-title" ref={headingRef} className={styles.questionTitle} tabIndex={-1}>
            Câu {state.index + 1}/{state.questions.length}
          </h2>
          <span className={styles.kindLabel}>
            {KIND_LABEL[currentQuestion.kind]}
            {chapterCode !== null && ` · chương ${chapterCode}`}
          </span>
        </header>
        <p className={styles.stem}>{currentQuestion.stem}</p>

        {currentQuestion.kind === "mcq" && entry.kind === "mcq" && (
          <McqQuestionView
            key={currentQuestion.twinId}
            question={currentQuestion}
            selected={entry.selected}
            submitted={entry.submitted}
            subjectCode={subjectCode}
            chapterCode={chapterCode}
            sourceOpen={state.sourceOpen}
            onSelect={(key) => dispatch({ type: "select", key })}
            onSubmit={() => dispatch({ type: "submit_mcq" })}
            onToggleSource={() => dispatch({ type: "toggle_source" })}
          />
        )}
        {currentQuestion.kind === "essay" && entry.kind === "essay" && (
          <EssayQuestionView
            key={currentQuestion.twinId}
            question={currentQuestion}
            entry={entry}
            subjectCode={subjectCode}
            chapterCode={chapterCode}
            sourceOpen={state.sourceOpen}
            onEdit={(text) => dispatch({ type: "edit_essay", text })}
            onSubmit={submitEssay}
            onToggleSource={() => dispatch({ type: "toggle_source" })}
          />
        )}
      </article>

      <div className={styles.navigation}>
        <button
          type="button"
          className={styles.secondary}
          onClick={() => dispatch({ type: "previous" })}
          disabled={state.index === 0}
        >
          Câu trước
        </button>
        <button type="button" className={styles.primary} onClick={() => dispatch({ type: "next" })}>
          {isLast ? "Xem lại cả lượt (N)" : "Câu kế tiếp (N)"}
        </button>
      </div>
    </div>
  );
}

interface SessionSummaryProps {
  state: PracticeState;
  onReview: (index: number) => void;
  onRestart: () => void;
  onNewExam: () => void;
}

/** Cuối lượt: kết quả từng câu (đúng/sai, hoặc "Khớp x/y ý"), không có con số tổng do giao diện đếm. */
function SessionSummary({ state, onReview, onRestart, onNewExam }: SessionSummaryProps) {
  return (
    <section className={styles.panel} aria-labelledby="practice-summary-title">
      <h2 id="practice-summary-title">Xem lại lượt luyện tập</h2>
      <p className={styles.muted}>Kết quả từng câu. Chọn “Xem lại” để mở lại câu đó cùng giải thích và trích dẫn.</p>
      <ol className={styles.summaryList}>
        {state.questions.map((question, index) => {
          const entry = state.entries[index];
          return (
            <li key={question.twinId} className={styles.summaryItem}>
              <span>Câu {index + 1}</span>
              <span className={styles.summaryStem}>
                {entry === undefined ? "Chưa làm" : entrySummary(question, entry)}
              </span>
              <button type="button" className={styles.linkButton} onClick={() => onReview(index)}>
                Xem lại
              </button>
            </li>
          );
        })}
      </ol>
      <div className={styles.actions}>
        <button type="button" className={styles.secondary} onClick={onRestart}>
          Làm lại từ đầu
        </button>
        <button type="button" className={styles.primary} onClick={onNewExam}>
          Tạo đề mới
        </button>
      </div>
    </section>
  );
}
