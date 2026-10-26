"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { type FormEvent, useId, useState } from "react";

import { ErrorNotice } from "@/components/common/ErrorNotice";
import { EmptyState, Loading } from "@/components/common/LoadState";
import { useShell } from "@/components/shell/ShellContext";
import { api, toUiError } from "@/lib/api";
import type { UiError } from "@/lib/errors";
import { SUBJECT_CODE_PATTERN } from "@/lib/limits";
import { checkSubjectForm, EXAM_FORMAT_OPTION_VI, EXAM_FORMATS } from "@/lib/subjects";
import { vaultHref } from "@/lib/vault";
import type { ExamFormat } from "@/types/contract-pending";

import styles from "./Vault.module.css";

/** Chưa chọn môn: danh sách môn, tạo môn mới (§6.8) và mở môn theo mã khi backend chưa có danh sách môn. */
export function SubjectHome() {
  const { subjects, reloadSubjects } = useShell();
  const subjectsMissing = subjects.kind === "error" && subjects.error.kind === "not_found";

  return (
    <>
      <section className={styles.panel} aria-labelledby="vault-subjects">
        <h2 id="vault-subjects">Chọn môn học</h2>
        {subjects.kind === "loading" && <Loading label="Đang tải danh sách môn…" />}
        {subjects.kind === "error" && subjectsMissing && (
          <p className={styles.muted}>
            Backend đang chạy chưa có danh sách môn. Mở môn bằng mã môn ở dưới; môn được tạo khi bạn nhập tài liệu đầu
            tiên của môn đó.
          </p>
        )}
        {subjects.kind === "error" && !subjectsMissing && (
          <ErrorNotice error={subjects.error} onRetry={reloadSubjects} onReload={reloadSubjects} />
        )}
        {subjects.kind === "ready" && subjects.subjects.length === 0 && (
          <EmptyState title="Chưa có môn học nào.">Tạo môn ở dưới, rồi kéo file PDF tài liệu vào môn đó.</EmptyState>
        )}
        {subjects.kind === "ready" && subjects.subjects.length > 0 && (
          <ul className={styles.subjectList}>
            {subjects.subjects.map((subject) => (
              <li key={subject.subject_code}>
                <Link className={styles.subjectLink} href={vaultHref({ subject: subject.subject_code })}>
                  <span className={styles.code}>{subject.subject_code}</span>
                  <span>{subject.display_name}</span>
                  <span className={styles.muted}>{EXAM_FORMAT_OPTION_VI[subject.exam_format]}</span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>
      <CreateSubjectForm />
      {subjects.kind === "error" && <OpenByCode />}
    </>
  );
}

function CreateSubjectForm() {
  const router = useRouter();
  const { reloadSubjects, selectSubject } = useShell();
  const ids = useId();
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [format, setFormat] = useState<ExamFormat>("mixed");
  const [fieldErrors, setFieldErrors] = useState<Partial<Record<"code" | "name", string>>>({});
  const [error, setError] = useState<UiError | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    const check = checkSubjectForm({ subjectCode: code, displayName: name, examFormat: format });
    if (!check.ok) {
      setFieldErrors(check.errors);
      return;
    }
    setFieldErrors({});
    setError(null);
    setSubmitting(true);
    try {
      const subject = await api.createSubject(check.payload);
      reloadSubjects();
      selectSubject(subject.subject_code);
      router.push(vaultHref({ subject: subject.subject_code }));
    } catch (caught) {
      setError(toUiError(caught));
      setSubmitting(false);
    }
  }

  return (
    <section className={styles.panel} aria-labelledby={`${ids}-title`}>
      <h2 id={`${ids}-title`}>Tạo môn mới</h2>
      <form className={styles.form} onSubmit={(event) => void submit(event)} noValidate>
        <div className={styles.fields}>
          <label className={styles.field}>
            <span className={styles.fieldLabel}>Mã môn</span>
            <input
              className={`${styles.input} ${styles.codeInput}`}
              value={code}
              onChange={(event) => setCode(event.target.value)}
              aria-invalid={fieldErrors.code !== undefined}
              aria-describedby={`${ids}-code-hint`}
              autoComplete="off"
              spellCheck={false}
              required
            />
            <span id={`${ids}-code-hint`} className={fieldErrors.code === undefined ? styles.hint : styles.fieldError}>
              {fieldErrors.code ?? "2–6 chữ cái rồi 2–4 chữ số, ví dụ MAU101."}
            </span>
          </label>
          <label className={styles.field}>
            <span className={styles.fieldLabel}>Tên môn (không bắt buộc)</span>
            <input
              className={`${styles.input} ${styles.wideInput}`}
              value={name}
              onChange={(event) => setName(event.target.value)}
              aria-invalid={fieldErrors.name !== undefined}
              aria-describedby={`${ids}-name-hint`}
            />
            <span id={`${ids}-name-hint`} className={fieldErrors.name === undefined ? styles.hint : styles.fieldError}>
              {fieldErrors.name ?? "Bỏ trống thì dùng mã môn làm tên."}
            </span>
          </label>
        </div>
        <fieldset className={styles.choices}>
          <legend>Hình thức thi</legend>
          {EXAM_FORMATS.map((value) => (
            <label key={value} className={styles.choice}>
              <input
                type="radio"
                name={`${ids}-format`}
                value={value}
                checked={format === value}
                onChange={() => setFormat(value)}
              />
              {EXAM_FORMAT_OPTION_VI[value]}
            </label>
          ))}
          <span className={styles.hint}>
            Chỉ dùng làm lựa chọn mặc định khi tạo đề luyện tập; đổi sau không ảnh hưởng đề đã tạo.
          </span>
        </fieldset>
        {error !== null && <ErrorNotice error={error} />}
        <div className={styles.actions}>
          <button type="submit" className={styles.primary} disabled={submitting}>
            {submitting ? "Đang tạo môn…" : "Tạo môn"}
          </button>
        </div>
      </form>
    </section>
  );
}

/** Mở một môn theo mã khi backend chưa trả được danh sách môn (môn có thể đã được tạo ngầm khi nhập tài liệu). */
function OpenByCode() {
  const router = useRouter();
  const ids = useId();
  const [code, setCode] = useState("");
  const [invalid, setInvalid] = useState(false);

  function submit(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    const normalized = code.trim().toUpperCase();
    if (!SUBJECT_CODE_PATTERN.test(normalized)) {
      setInvalid(true);
      return;
    }
    setInvalid(false);
    router.push(vaultHref({ subject: normalized }));
  }

  return (
    <section className={styles.panel} aria-labelledby={`${ids}-title`}>
      <h2 id={`${ids}-title`}>Mở môn theo mã</h2>
      <form className={styles.actions} onSubmit={submit} noValidate>
        <label className={styles.field}>
          <span className={styles.fieldLabel}>Mã môn</span>
          <input
            className={`${styles.input} ${styles.codeInput}`}
            value={code}
            onChange={(event) => setCode(event.target.value)}
            aria-invalid={invalid}
            aria-describedby={`${ids}-hint`}
            spellCheck={false}
          />
          <span id={`${ids}-hint`} className={invalid ? styles.fieldError : styles.hint}>
            {invalid ? "Mã môn gồm 2–6 chữ cái rồi 2–4 chữ số, ví dụ MAU101." : "Ví dụ MAU101."}
          </span>
        </label>
        <button type="submit" className={styles.secondary}>
          Mở môn
        </button>
      </form>
    </section>
  );
}
