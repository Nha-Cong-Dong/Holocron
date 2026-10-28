"use client";

import { type FormEvent, useState } from "react";

import { CapabilityNotice } from "@/components/common/CapabilityNotice";
import { USE_MOCKS } from "@/lib/config";
import { MAX_THEORY_SLOTS } from "@/lib/limits";
import {
  buildBlueprintCreate,
  DEFAULT_QUESTION_COUNT,
  ESSAY_NOT_AVAILABLE_VI,
  EXAM_FORMAT_LABEL_VI,
  essayChoiceAvailable,
  FORMAT_CHOICE_LABEL_VI,
  type FormatChoice,
  formatChoices,
  MIN_QUESTION_COUNT,
  prefilledFormatChoice,
} from "@/lib/practice";
import { capabilityOf, isCapabilityBlocked, type RuntimeView } from "@/lib/runtime";
import type { BlueprintCreate, Subject } from "@/types/holocron";

import styles from "./Practice.module.css";

interface BlueprintBuilderProps {
  subject: Subject;
  runtime: RuntimeView | null;
  busy: boolean;
  onCreate: (payload: BlueprintCreate) => void;
}

/**
 * Form tạo đề luyện tập lý thuyết: chương, số câu và hình thức. Lựa chọn có tự luận chỉ hiện khi backend báo rõ chấm
 * tự luận offline khả dụng (khi đó điền sẵn theo `exam_format` của môn); backend chưa báo hoặc báo không khả dụng thì
 * chỉ có và điền sẵn trắc nghiệm, kèm dòng giải thích với môn thi tự luận hay kết hợp, để luồng mặc định không gặp 409.
 */
export function BlueprintBuilder({ subject, runtime, busy, onCreate }: BlueprintBuilderProps) {
  const essayAvailable = essayChoiceAvailable(capabilityOf(runtime, "essay_grading_offline"));
  const generationBlocked = isCapabilityBlocked(runtime, "theory_generation");
  const allowed = formatChoices(essayAvailable);
  const [chapterCode, setChapterCode] = useState("");
  const [count, setCount] = useState(String(DEFAULT_QUESTION_COUNT));
  const [choice, setChoice] = useState<FormatChoice | null>(() =>
    prefilledFormatChoice(subject.exam_format, essayAvailable),
  );
  const [formError, setFormError] = useState<string | null>(null);
  // Lựa chọn đã lưu có thể không còn được phép (khả năng vừa đổi sau khi kiểm tra lại backend): về trắc nghiệm.
  const effectiveChoice =
    choice !== null && allowed.includes(choice) ? choice : prefilledFormatChoice(subject.exam_format, essayAvailable);

  function handleSubmit(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    const result = buildBlueprintCreate({
      subjectCode: subject.subject_code,
      chapterCode,
      questionCount: Number(count),
      choice: effectiveChoice,
    });
    if (!result.ok) {
      setFormError(result.message);
      return;
    }
    setFormError(null);
    onCreate(result.payload);
  }

  return (
    <section className={styles.panel} aria-labelledby="builder-title">
      <h2 id="builder-title">Tạo đề luyện tập</h2>
      <p className={styles.muted}>
        Môn <strong>{subject.display_name}</strong> ({subject.subject_code}, {EXAM_FORMAT_LABEL_VI[subject.exam_format]}
        ). Câu hỏi được ghép từ các cặp khái niệm đã xác nhận trong chương bạn chọn; đáp án luôn là trích dẫn nguyên văn
        từ tài liệu.
      </p>
      <CapabilityNotice runtime={runtime} name="theory_generation" />
      {isCapabilityBlocked(runtime, "essay_grading_offline") && (
        <CapabilityNotice runtime={runtime} name="essay_grading_offline" />
      )}
      {!essayAvailable && subject.exam_format !== "mcq" && (
        <p className={styles.note}>
          {ESSAY_NOT_AVAILABLE_VI}. Môn này {EXAM_FORMAT_LABEL_VI[subject.exam_format]}, nhưng đề luyện tập ở đây chỉ
          gồm câu trắc nghiệm.
        </p>
      )}
      {USE_MOCKS && (
        <p className={styles.note}>
          Dữ liệu mẫu đang bật, nhưng môn học, bộ đề và câu hỏi ở màn này luôn lấy từ backend đang chạy, không lấy từ dữ
          liệu mẫu.
        </p>
      )}
      <form className={styles.form} onSubmit={handleSubmit} noValidate>
        <div className={styles.fields}>
          <label className={styles.field}>
            Mã chương
            <input
              className={`${styles.input} ${styles.chapterInput}`}
              value={chapterCode}
              onChange={(event) => setChapterCode(event.target.value)}
              placeholder="ch1"
              autoComplete="off"
              spellCheck={false}
              required
              disabled={busy}
            />
            <span className={styles.hint}>Ví dụ ch1, ch12 hoặc ch2.1, như khi nhập tài liệu.</span>
          </label>
          <label className={styles.field}>
            Số câu
            <input
              className={`${styles.input} ${styles.countInput}`}
              type="number"
              inputMode="numeric"
              min={MIN_QUESTION_COUNT}
              max={MAX_THEORY_SLOTS}
              value={count}
              onChange={(event) => setCount(event.target.value)}
              disabled={busy}
            />
            <span className={styles.hint}>
              Từ {MIN_QUESTION_COUNT} đến {MAX_THEORY_SLOTS}.
            </span>
          </label>
        </div>
        <fieldset className={styles.choices} disabled={busy}>
          <legend>Hình thức</legend>
          {allowed.map((option) => (
            <label key={option} className={styles.choice}>
              <input
                type="radio"
                name="format-choice"
                value={option}
                checked={effectiveChoice === option}
                onChange={() => setChoice(option)}
              />
              {FORMAT_CHOICE_LABEL_VI[option]}
            </label>
          ))}
        </fieldset>
        {formError !== null && (
          <p className={styles.formError} role="alert">
            {formError}
          </p>
        )}
        <div className={styles.actions}>
          <button type="submit" className={styles.primary} disabled={busy || generationBlocked}>
            {busy ? "Đang tạo đề…" : "Tạo đề và bắt đầu"}
          </button>
        </div>
      </form>
    </section>
  );
}
