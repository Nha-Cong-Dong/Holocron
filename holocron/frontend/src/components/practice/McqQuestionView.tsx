"use client";

import { type FormEvent, type KeyboardEvent, useEffect, useRef } from "react";

import { VerbatimQuote } from "@/components/common/VerbatimQuote";
import { withContentKeys } from "@/lib/keys";
import { answerOutcome, revealPolicy } from "@/lib/practice";
import type { McqQuestion } from "@/lib/practice-data";
import { OPTION_KEYS } from "@/lib/twin";
import type { OptionKey } from "@/types/holocron";

import { ArtifactView } from "./ArtifactView";
import { CitationPanel } from "./CitationPanel";
import styles from "./Practice.module.css";

interface McqQuestionViewProps {
  question: McqQuestion;
  selected: OptionKey | null;
  submitted: boolean;
  subjectCode: string;
  chapterCode: string | null;
  sourceOpen: boolean;
  onSelect: (key: OptionKey) => void;
  onSubmit: () => void;
  onToggleSource: () => void;
}

/**
 * Một câu trắc nghiệm: chọn phương án (chuột, phím 1–4 hoặc A–D), nộp (Enter). Sau khi nộp mới hiện đáp án đúng,
 * misconception của phương án đã chọn, giải thích và trích dẫn. Đúng/sai là phép so khoá với `correct_key` backend trả.
 */
export function McqQuestionView({
  question,
  selected,
  submitted,
  subjectCode,
  chapterCode,
  sourceOpen,
  onSelect,
  onSubmit,
  onToggleSource,
}: McqQuestionViewProps) {
  const resultRef = useRef<HTMLDivElement>(null);
  const reveal = revealPolicy({ kind: "mcq", selected, submitted });

  // Sau khi nộp, đưa focus tới kết quả để Enter/N đi tiếp và trình đọc màn hình đọc kết quả.
  useEffect(() => {
    if (submitted) resultRef.current?.focus();
  }, [submitted]);

  function handleSubmit(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    onSubmit();
  }

  // Enter trên ô chọn phương án cũng nộp (trình duyệt không tự nộp form khi focus ở ô radio).
  function handleOptionKey(event: KeyboardEvent<HTMLInputElement>): void {
    if (event.key === "Enter" && !event.ctrlKey && !event.metaKey && !event.altKey) {
      event.preventDefault();
      onSubmit();
    }
  }

  if (!submitted) {
    return (
      <form className={styles.form} onSubmit={handleSubmit}>
        <fieldset className={styles.options}>
          <legend className="visually-hidden">Chọn một phương án</legend>
          {OPTION_KEYS.map((key, position) => (
            <label key={key} className={styles.option}>
              <input
                type="radio"
                name={`option-${question.twinId}`}
                value={key}
                checked={selected === key}
                onChange={() => onSelect(key)}
                onKeyDown={handleOptionKey}
              />
              <span className={styles.optionKey}>
                {key}
                <span className="visually-hidden"> (phím {position + 1})</span>.
              </span>
              <span>{question.options[key]}</span>
            </label>
          ))}
        </fieldset>
        <div className={styles.actions}>
          <button type="submit" className={styles.primary} disabled={selected === null}>
            Nộp (Enter)
          </button>
          <span className={styles.hint}>
            Phím 1–4 hoặc A–D để chọn. Đáp án, giải thích và trích dẫn hiện sau khi nộp.
          </span>
        </div>
      </form>
    );
  }

  const outcome = selected === null ? null : answerOutcome(selected, question.correctKey);
  const chosenMisconception = selected === null ? undefined : question.misconceptions[selected];
  const otherDistractors = OPTION_KEYS.filter((key) => key !== question.correctKey && key !== selected);

  return (
    <div ref={resultRef} className={styles.result} tabIndex={-1} aria-live="polite">
      {outcome !== null && (
        <p className={styles.verdict} data-outcome={outcome}>
          {outcome === "correct"
            ? `Đúng. Bạn chọn ${selected}.`
            : `Sai. Bạn chọn ${selected}; đáp án đúng là ${question.correctKey}.`}
        </p>
      )}

      <ul className={styles.reviewList} aria-label="Các phương án">
        {OPTION_KEYS.map((key) => {
          const isCorrect = reveal.correctKey && key === question.correctKey;
          const isChosenWrong = key === selected && !isCorrect;
          return (
            <li
              key={key}
              className={styles.reviewOption}
              data-state={isCorrect ? "correct" : isChosenWrong ? "chosen-wrong" : "other"}
            >
              <span>
                <span className={styles.optionKey}>{key}.</span> {question.options[key]}
              </span>
              {isCorrect && <span className={styles.tag}>Đáp án đúng{key === selected ? " (bạn chọn)" : ""}</span>}
              {isChosenWrong && <span className={styles.tag}>Bạn chọn</span>}
            </li>
          );
        })}
      </ul>

      {reveal.misconceptions && chosenMisconception !== undefined && (
        <section className={styles.misconception} aria-label="Nhầm lẫn của phương án đã chọn">
          <span className={styles.misconceptionLabel}>
            Phương án {selected}: {chosenMisconception.label}
          </span>
          <span>{chosenMisconception.rationale}</span>
          {chosenMisconception.citation !== null && <VerbatimQuote citation={chosenMisconception.citation} />}
        </section>
      )}

      {reveal.solution && (
        <section className={styles.source} aria-label="Giải thích">
          <h3 className={styles.sectionTitle}>Giải thích</h3>
          <p className={styles.stem}>{question.solution}</p>
          {withContentKeys(question.artifacts, (artifact) => JSON.stringify(artifact)).map(({ key, item }) => (
            <ArtifactView key={key} artifact={item} />
          ))}
        </section>
      )}

      {reveal.citation && (
        <CitationPanel
          citation={question.citation}
          subjectCode={subjectCode}
          chapterCode={chapterCode}
          open={sourceOpen}
          onToggle={onToggleSource}
        />
      )}

      {reveal.misconceptions && otherDistractors.length > 0 && (
        <details className={styles.details}>
          <summary>Vì sao các phương án sai khác không đúng</summary>
          {otherDistractors.map((key) => {
            const misconception = question.misconceptions[key];
            if (misconception === undefined) return null;
            return (
              <div key={key} className={styles.misconception}>
                <span className={styles.misconceptionLabel}>
                  Phương án {key}: {misconception.label}
                </span>
                <span>{misconception.rationale}</span>
                {misconception.citation !== null && <VerbatimQuote citation={misconception.citation} />}
              </div>
            );
          })}
        </details>
      )}
    </div>
  );
}
