"use client";

import { useCallback, useEffect, useReducer, useRef, useState } from "react";

import { HolocronMark } from "@/components/brand/HolocronMark";
import { describeTarget } from "@/components/shell/keyboard";
import { useShell } from "@/components/shell/ShellContext";
import { Button } from "@/components/ui";
import { api, toUiError } from "@/lib/api";
import { localError } from "@/lib/errors";
import { createLatestCounter } from "@/lib/latest";
import { essaySubmission, initialPracticeState, isAnswered, type PracticeState, practiceScope } from "@/lib/practice";
import type { PracticeBlueprint, PracticeQuestion } from "@/lib/practice-data";
import { essayErrorPlacement, nextLabel, quizHint, quizKeyEffect, quizReducer, resultRows } from "@/lib/quiz";
import { resolveShortcut, targetKindOf } from "@/lib/shortcuts";
import { chapterLabel } from "@/lib/sky";
import type { PracticeSessionKind } from "@/lib/subject-route";

import { EssayQuestionView } from "./EssayQuestionView";
import { McqQuestionView } from "./McqQuestionView";
import styles from "./Quiz.module.css";
import { QuizHeader } from "./QuizHeader";
import { ResultScreen } from "./ResultScreen";
import { SourceAside } from "./SourceAside";

interface PracticeSessionProps {
  questions: PracticeQuestion[];
  blueprint: PracticeBlueprint;
  /** Môn của đề (ADR-006 mục 6): để tìm đoạn nguồn và tên tài liệu. */
  subjectCode: string;
  /** "Thoát" khi đang làm hoặc "Làm đề mới" ở Kết quả: về thiết lập đề. */
  onExit: () => void;
  /** Báo bài làm đang ở màn làm đề hay màn kết quả. */
  onPhaseChange: (phase: PracticeSessionKind) => void;
}

/** `id` của nút chính "Câu tiếp"/"Xem kết quả": nhận focus ngay khi câu đã trả lời, để Enter đi tiếp. */
const NEXT_BUTTON_ID = "quiz-next";

/**
 * Làm bài (màn 9) rồi Kết quả (màn 10). Một câu một màn: thanh trên, thẻ câu hỏi và cột "Nguồn". Phím tắt (kế hoạch UI
 * mục 5.7) chỉ có tác dụng khi không mở hộp lệnh hay bảng phím tắt; phím một ký tự không có tác dụng khi đang gõ trong
 * ô trả lời. Trả lời xong thì focus sang nút chính, nên Enter là cú bấm gốc của nút đó; giữ Enter không nhảy qua câu
 * chưa làm vì nút chỉ có khi câu đã trả lời và reducer chỉ sang câu khi câu đang mở đã trả lời.
 */
export function PracticeSession({ questions, blueprint, subjectCode, onExit, onPhaseChange }: PracticeSessionProps) {
  const { overlayOpen } = useShell();
  const [state, dispatch] = useReducer(quizReducer, questions, initialPracticeState);
  const boxRef = useRef<HTMLDivElement>(null);
  // Bản mới nhất của trạng thái cho handler phím (gắn một lần) và cho lời gọi chấm bất đồng bộ.
  const stateRef = useRef<PracticeState>(state);
  useEffect(() => {
    stateRef.current = state;
  }, [state]);
  // Mã lượt gửi chấm, tăng dần trong cả phiên: reducer chỉ áp phản hồi mang đúng mã của lượt đang chờ ở câu đó.
  const submissions = useRef(createLatestCounter());

  // Tên tài liệu cho chú thích trích dẫn; không tải được thì chỉ ghi số trang, không chặn bài làm.
  const [titles, setTitles] = useState<ReadonlyMap<string, string>>(new Map());
  useEffect(() => {
    let alive = true;
    api.listDocuments(subjectCode).then(
      (documents) => {
        if (alive) setTitles(new Map(documents.map((document) => [document.docId, document.title])));
      },
      () => undefined,
    );
    return () => {
      alive = false;
    };
  }, [subjectCode]);

  useEffect(() => {
    onPhaseChange(state.finished ? "result" : "quiz");
  }, [state.finished, onPhaseChange]);

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
    const ticket = submissions.current.begin();
    dispatch({ type: "essay_started", twinId, submission: ticket });
    api
      .gradeEssay(twinId, submission.answer)
      .then((result) => dispatch({ type: "essay_graded", twinId, submission: ticket, result }))
      .catch((error: unknown) =>
        dispatch({ type: "essay_failed", twinId, submission: ticket, error: toUiError(error) }),
      );
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
      const effect = action === null ? null : quizKeyEffect(action);
      if (effect === null) return;
      event.preventDefault();
      if (effect.kind === "submit_essay") submitEssay();
      else dispatch(effect.action);
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [overlayOpen, submitEssay]);

  const question = state.questions[state.index];
  const entry = state.entries[state.index];
  const answered = entry !== undefined && isAnswered(entry);
  const twinId = question?.twinId;
  const kind = question?.kind;

  // Sang câu mới: câu trắc nghiệm đưa focus về khung câu (câu tự luận thì ô trả lời tự nhận focus và bỏ các lần lặp
  // của phím đang giữ từ "Câu tiếp", xem `answerHeldKey`).
  useEffect(() => {
    if (state.finished || twinId === undefined) return;
    if (kind === "mcq") boxRef.current?.focus();
  }, [twinId, kind, state.finished]);

  // Vừa trả lời: focus sang nút chính để Enter đi tiếp (không có bước này thì Enter mất tác dụng).
  useEffect(() => {
    if (answered && !state.finished) document.getElementById(NEXT_BUTTON_ID)?.focus();
  }, [answered, state.finished]);

  if (state.finished) return <ResultScreen rows={resultRows(state)} onNewExam={onExit} />;
  if (question === undefined || entry === undefined) return null;

  const chapterCode = chapterOf(question.slotIndex);
  const citation = entry.kind === "essay" && entry.result !== null ? entry.result.citation : question.citation;
  const grading = entry.kind === "essay" && entry.status === "grading";
  const needsCheck = entry.kind === "essay" && entry.status === "editing";
  const screenError = entry.kind === "essay" && entry.error !== null && essayErrorPlacement(entry.error) === "screen";

  return (
    <section className={styles.screen} aria-label="Làm bài">
      <QuizHeader index={state.index} total={state.questions.length} answered={answered} onExit={onExit} />
      <div key={question.twinId} ref={boxRef} className={styles.grid} tabIndex={-1}>
        <article className={styles.card} aria-labelledby={`stem-${question.twinId}`}>
          {chapterCode !== null && <span className={styles.chapter}>{chapterLabel(chapterCode)}</span>}
          <h2 id={`stem-${question.twinId}`} className={styles.stem}>
            {question.stem}
          </h2>

          {question.kind === "mcq" && entry.kind === "mcq" && (
            <McqQuestionView
              question={question}
              entry={entry}
              titles={titles}
              onPick={(key) => dispatch({ type: "pick", key })}
            />
          )}
          {question.kind === "essay" && entry.kind === "essay" && (
            <EssayQuestionView
              question={question}
              entry={entry}
              onEdit={(text) => dispatch({ type: "edit_essay", text })}
              onRetry={submitEssay}
              onSkip={() => dispatch({ type: "skip_essay" })}
            />
          )}

          <div className={styles.footer}>
            <span className={styles.caption}>{quizHint(entry)}</span>
            {grading && (
              <output className={styles.grading}>
                <HolocronMark size={24} spinning />
                Đang chấm…
              </output>
            )}
            {needsCheck && !screenError && (
              <Button variant="primary" onClick={submitEssay}>
                Chấm ý
              </Button>
            )}
            {answered && (
              <Button id={NEXT_BUTTON_ID} variant="primary" onClick={() => dispatch({ type: "next" })}>
                {nextLabel(state)} <span className={styles.kbd}>Enter</span>
              </Button>
            )}
          </div>
        </article>

        <SourceAside
          answered={answered}
          citation={citation}
          titles={titles}
          subjectCode={subjectCode}
          chapterCode={chapterCode}
          open={state.sourceOpen}
          onToggle={() => dispatch({ type: "toggle_source" })}
        />
      </div>
    </section>
  );
}
