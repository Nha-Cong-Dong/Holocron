"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { CapabilityNotice } from "@/components/common/CapabilityNotice";
import { Button, Chip, EmptyState, ErrorState, Skeleton } from "@/components/ui";
import { api } from "@/lib/api";
import { screenErrorText } from "@/lib/concept-board";
import { USE_MOCKS } from "@/lib/config";
import type { UiError } from "@/lib/errors";
import { createLatestCounter } from "@/lib/latest";
import { runLatest } from "@/lib/pairs";
import {
  DEFAULT_QUESTION_COUNT,
  ESSAY_NOT_AVAILABLE_VI,
  essayChoiceAvailable,
  FORMAT_CHOICE_LABEL_VI,
  type FormatChoice,
} from "@/lib/practice";
import {
  buildMultiChapterBlueprint,
  chapterChips,
  confirmedTotal,
  defaultSelection,
  formatOptions,
  initialFormat,
  QUESTION_COUNTS,
  selectedChapters,
  setupSummary,
} from "@/lib/practice-setup";
import { capabilityOf, isCapabilityBlocked, type RuntimeView } from "@/lib/runtime";
import type { BlueprintCreate, ConceptPair, Subject } from "@/types/holocron";

import styles from "./PracticeSetup.module.css";

const SKELETON_CHIPS = ["a", "b", "c"] as const;

type LoadState = { kind: "loading" } | { kind: "error"; error: UiError } | { kind: "ready"; pairs: ConceptPair[] };

interface PracticeSetupProps {
  subject: Subject;
  runtime: RuntimeView | null;
  /** Đề đã dựng: tạo bộ đề rồi soạn câu. */
  onStart: (payload: BlueprintCreate) => void;
  /** Sang tab Khái niệm của môn (trạng thái rỗng). */
  onBrowseConcepts: () => void;
}

/**
 * Thiết lập đề ôn tập (design-handoff/README.md màn 8): chọn chòm sao (chương có khái niệm đã nhận), số câu 5/10/20 và
 * hình thức, đọc dòng tóm tắt rồi bấm "Bắt đầu". Danh sách chương lấy từ khái niệm của môn (mọi trạng thái), số khái
 * niệm là số đã nhận của từng chương; không bắt gõ mã chương.
 */
export function PracticeSetup({ subject, runtime, onStart, onBrowseConcepts }: PracticeSetupProps) {
  const [load, setLoad] = useState<LoadState>({ kind: "loading" });
  const calls = useRef(createLatestCounter());
  const subjectCode = subject.subject_code;

  const reload = useCallback(() => {
    setLoad({ kind: "loading" });
    void runLatest(
      calls.current,
      () => api.listConceptPairs({ subjectCode, chapterCode: null, status: null }),
      (outcome) =>
        setLoad(outcome.ok ? { kind: "ready", pairs: outcome.value.pairs } : { kind: "error", error: outcome.error }),
    );
  }, [subjectCode]);

  useEffect(() => {
    reload();
    const counter = calls.current;
    return () => counter.invalidate();
  }, [reload]);

  if (load.kind === "loading") return <SetupLoading />;
  if (load.kind === "error") {
    return (
      <ErrorState
        title="Không tải được khái niệm của môn"
        fix={screenErrorText(load.error)}
        onRetry={reload}
        technical={load.error.diagnostic}
      />
    );
  }
  return (
    <SetupForm
      subject={subject}
      runtime={runtime}
      pairs={load.pairs}
      onStart={onStart}
      onBrowseConcepts={onBrowseConcepts}
    />
  );
}

function SetupLoading() {
  return (
    <div className={styles.loading} aria-busy="true">
      <Skeleton width="30%" height={22} />
      <div className={styles.skeletonRow} aria-hidden="true">
        {SKELETON_CHIPS.map((key) => (
          <Skeleton key={key} width={150} height={44} />
        ))}
      </div>
      <Skeleton width="25%" height={22} />
      <div className={styles.skeletonRow} aria-hidden="true">
        {SKELETON_CHIPS.map((key) => (
          <Skeleton key={key} width={80} height={44} />
        ))}
      </div>
      <p className={styles.note}>Đang tải khái niệm của môn…</p>
    </div>
  );
}

interface SetupFormProps {
  subject: Subject;
  runtime: RuntimeView | null;
  pairs: readonly ConceptPair[];
  onStart: (payload: BlueprintCreate) => void;
  onBrowseConcepts: () => void;
}

function SetupForm({ subject, runtime, pairs, onStart, onBrowseConcepts }: SetupFormProps) {
  const chips = useMemo(() => chapterChips(pairs), [pairs]);
  const essayAvailable = essayChoiceAvailable(capabilityOf(runtime, "essay_grading_offline"));
  const generationBlocked = isCapabilityBlocked(runtime, "theory_generation");
  const options = formatOptions(essayAvailable);
  const [selected, setSelected] = useState<ReadonlySet<string>>(() => new Set(defaultSelection(chips)));
  const [count, setCount] = useState<number>(DEFAULT_QUESTION_COUNT);
  const [choice, setChoice] = useState<FormatChoice | null>(null);
  const [formError, setFormError] = useState<string | null>(null);

  if (confirmedTotal(chips) === 0) {
    return (
      <EmptyState
        title="Chưa đủ khái niệm để ra đề"
        description="Nhận ít nhất 1 khái niệm ở tab Khái niệm."
        action={{ label: "Duyệt khái niệm", onClick: onBrowseConcepts }}
      />
    );
  }

  // Lựa chọn đã lưu có thể không còn chọn được (khả năng vừa đổi): về hình thức chọn sẵn.
  const chosenOption = options.find((option) => option.choice === choice);
  const effectiveChoice =
    chosenOption !== undefined && !chosenOption.disabled
      ? chosenOption.choice
      : initialFormat(subject.exam_format, essayAvailable);
  const chosenChapters = selectedChapters(chips, selected);
  const startBlocked = chosenChapters.length === 0 || generationBlocked;

  function toggle(code: string, on: boolean): void {
    setSelected((previous) => {
      const next = new Set(previous);
      if (on) next.add(code);
      else next.delete(code);
      return next;
    });
  }

  function start(): void {
    if (startBlocked) return;
    const result = buildMultiChapterBlueprint({
      subjectCode: subject.subject_code,
      chapters: chosenChapters.map((chip) => chip.code),
      count,
      choice: effectiveChoice,
    });
    if (!result.ok) {
      setFormError(result.message);
      return;
    }
    setFormError(null);
    onStart(result.payload);
  }

  return (
    <section className={styles.panel} aria-label="Thiết lập đề ôn tập">
      <CapabilityNotice runtime={runtime} name="theory_generation" />
      {USE_MOCKS && (
        <p className={styles.note}>
          Dữ liệu mẫu đang bật, nhưng môn học, đề và câu hỏi ở màn này luôn lấy từ Holocron đang chạy, không lấy từ dữ
          liệu mẫu.
        </p>
      )}
      <fieldset className={styles.group} aria-labelledby="practice-chapters-title">
        <h2 id="practice-chapters-title" className={styles.title}>
          Ôn chòm sao nào?
        </h2>
        <div className={styles.chips}>
          {chips.map((chip) => (
            <Chip
              key={chip.code}
              kind="toggle"
              pressed={!chip.disabled && selected.has(chip.code)}
              disabled={chip.disabled}
              onToggle={(next) => toggle(chip.code, next)}
            >
              {chip.label} <span className={styles.count}>{chip.confirmed} khái niệm</span>
            </Chip>
          ))}
        </div>
      </fieldset>
      <div className={styles.group} role="radiogroup" aria-labelledby="practice-count-title">
        <h2 id="practice-count-title" className={styles.title}>
          Bao nhiêu câu?
        </h2>
        <div className={styles.chips}>
          {QUESTION_COUNTS.map((value) => (
            <Chip
              key={value}
              kind="radio"
              name="practice-count"
              value={String(value)}
              checked={count === value}
              className={styles.countChip}
              onSelect={() => setCount(value)}
            >
              {value} câu
            </Chip>
          ))}
        </div>
      </div>
      <div className={styles.group} role="radiogroup" aria-labelledby="practice-format-title">
        <h2 id="practice-format-title" className={styles.title}>
          Hình thức
        </h2>
        <div className={styles.chips}>
          {options.map((option) => (
            <Chip
              key={option.choice}
              kind="radio"
              name="practice-format"
              value={option.choice}
              checked={effectiveChoice === option.choice}
              disabled={option.disabled}
              onSelect={() => setChoice(option.choice)}
            >
              {FORMAT_CHOICE_LABEL_VI[option.choice]}
            </Chip>
          ))}
        </div>
        {!essayAvailable && (
          <p className={styles.note}>
            {ESSAY_NOT_AVAILABLE_VI}, nên Tự luận và Kết hợp đang khoá; đề chỉ gồm câu trắc nghiệm.
          </p>
        )}
      </div>
      {formError !== null && (
        <p className={styles.error} role="alert">
          {formError}
        </p>
      )}
      <div className={styles.footer}>
        <p className={styles.summary} aria-live="polite">
          {setupSummary(count, chips, selected)}
        </p>
        <Button variant="primary" className={styles.start} aria-disabled={startBlocked || undefined} onClick={start}>
          Bắt đầu
        </Button>
      </div>
    </section>
  );
}
