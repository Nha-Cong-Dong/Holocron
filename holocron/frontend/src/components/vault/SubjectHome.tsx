"use client";

import { useRouter } from "next/navigation";
import { type FormEvent, useId, useState } from "react";

import { SUBJECT_CODE_PATTERN } from "@/lib/limits";
import { subjectHref } from "@/lib/subject-route";

import styles from "./Vault.module.css";

/** Địa chỉ mặc định khi mở một môn theo mã: tab Tài liệu của trang môn. */
function subjectDocumentsTab(subjectCode: string): string {
  return subjectHref(subjectCode, "documents");
}

/**
 * Mở một môn theo mã khi chưa tải được danh sách môn (môn có thể đã được tạo ngầm khi nhập tài liệu). `hrefFor` dựng
 * địa chỉ của môn; mặc định là tab Tài liệu của trang môn.
 */
export function OpenByCode({ hrefFor = subjectDocumentsTab }: { hrefFor?: (subjectCode: string) => string }) {
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
    router.push(hrefFor(normalized));
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
