"use client";

import { type FormEvent, useState } from "react";

import { CapabilityNotice } from "@/components/common/CapabilityNotice";
import { USE_MOCKS } from "@/lib/config";
import { MAX_THEORY_SLOTS } from "@/lib/limits";
import {
  buildBlueprintCreate,
  DEFAULT_QUESTION_COUNT,
  defaultFormatChoice,
  EXAM_FORMAT_LABEL_VI,
  FORMAT_CHOICE_LABEL_VI,
  type FormatChoice,
  formatChoices,
  MIN_QUESTION_COUNT,
} from "@/lib/practice";
import { isCapabilityBlocked, type RuntimeView } from "@/lib/runtime";
import type { BlueprintCreate, Subject } from "@/types/contract-pending";

import styles from "./Practice.module.css";

interface BlueprintBuilderProps {
  subject: Subject;
  runtime: RuntimeView | null;
  busy: boolean;
  onCreate: (payload: BlueprintCreate) => void;
}

/**
 * Form tạo đề luyện tập lý thuyết: chương, số câu và hình thức (điền sẵn theo `exam_format` của môn). Chấm tự luận
 * không khả dụng thì lựa chọn có tự luận bị ẩn kèm lý do của backend, và giao diện không tự đổi sang trắc nghiệm.
 */
export function BlueprintBuilder({ subject, runtime, busy, onCreate }: BlueprintBuilderProps) {
  const essayAvailable = !isCapabilityBlocked(runtime, "essay_grading_offline");
  const generationBlocked = isCapabilityBlocked(runtime, "theory_generation");
  const allowed = formatChoices(essayAvailable);
  const [chapterCode, setChapterCode] = useState("");
  const [count, setCount] = useState(String(DEFAULT_QUESTION_COUNT));
  const [choice, setChoice] = useState<FormatChoice | null>(() => defaultFormatChoice(subject.exam_format, allowed));
  const [formError, setFormError] = useState<string | null>(null);
  // Lựa chọn đã lưu có thể không còn được phép (khả năng vừa đổi sau khi kiểm tra lại backend): coi như chưa chọn.
  const effectiveChoice = choice !== null && allowed.includes(choice) ? choice : null;

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
      {!essayAvailable && <CapabilityNotice runtime={runtime} name="essay_grading_offline" />}
      {!essayAvailable && subject.exam_format !== "mcq" && (
        <p className={styles.note}>
          Môn này {EXAM_FORMAT_LABEL_VI[subject.exam_format]} nhưng chấm tự luận chưa dùng được, nên chỉ còn trắc
          nghiệm. Giao diện không tự đổi hình thức thay bạn: chọn trắc nghiệm nếu vẫn muốn luyện.
        </p>
      )}
      {USE_MOCKS && (
        <p className={styles.note}>
          Dữ liệu mẫu: đề tạo ra luôn là cùng một bộ câu mẫu, không theo chương, số câu hay hình thức chọn ở đây.
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
