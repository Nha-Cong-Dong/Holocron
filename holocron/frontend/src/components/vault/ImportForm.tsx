"use client";

import { type DragEvent, type FormEvent, useId, useRef, useState } from "react";

import { ErrorNotice } from "@/components/common/ErrorNotice";
import { api, toUiError } from "@/lib/api";
import {
  checkDocumentFile,
  checkImportForm,
  type ImportFormField,
  SOURCE_KIND_LABEL_VI,
  SOURCE_KINDS,
} from "@/lib/documents";
import type { DocumentImportOutcome } from "@/lib/documents-data";
import type { UiError } from "@/lib/errors";
import { DOCUMENT_MAX_PAGES, DOCUMENT_UPLOAD_MAX_MIB } from "@/lib/limits";

import styles from "./Vault.module.css";

interface ImportFormProps {
  subjectCode: string;
  onImported: (outcome: DocumentImportOutcome) => void;
}

/**
 * Form `POST /v1/documents` (§6.5): file PDF (kéo-thả hoặc chọn), mã chương bắt buộc, loại tài liệu bắt buộc, tên tuỳ
 * chọn. Kiểm phía client trước khi gửi (`lib/documents.ts`); backend vẫn kiểm cuối cùng và trả 413/415/422/409.
 */
export function ImportForm({ subjectCode, onImported }: ImportFormProps) {
  const ids = useId();
  const fileInput = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const [chapter, setChapter] = useState("");
  const [sourceKind, setSourceKind] = useState("");
  const [title, setTitle] = useState("");
  const [errors, setErrors] = useState<Partial<Record<ImportFormField, string>>>({});
  const [submitError, setSubmitError] = useState<UiError | null>(null);
  const [submitting, setSubmitting] = useState(false);

  function pick(candidate: File | null): void {
    setSubmitError(null);
    if (candidate === null) {
      setFile(null);
      setFileError(null);
      return;
    }
    const check = checkDocumentFile(candidate);
    setFile(check.ok ? candidate : null);
    setFileError(check.ok ? null : check.message);
  }

  function onDrop(event: DragEvent<HTMLFormElement>): void {
    event.preventDefault();
    setDragging(false);
    if (submitting) return;
    const files = event.dataTransfer.files;
    if (files.length > 1) {
      setFile(null);
      setFileError("Mỗi lần chỉ nhập một file (một chương). Kéo từng file một.");
      return;
    }
    pick(files.item(0));
  }

  async function submit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (file === null) {
      setFileError(fileError ?? "Chọn hoặc kéo một file PDF vào đây.");
      return;
    }
    const formErrors = checkImportForm({ subjectCode, chapterCode: chapter, sourceKind, title });
    setErrors(formErrors);
    if (Object.keys(formErrors).length > 0) return;
    setSubmitError(null);
    setSubmitting(true);
    try {
      const trimmedTitle = title.trim();
      const outcome = await api.importDocument({
        file,
        subjectCode,
        sourceKind,
        chapterCode: chapter.trim(),
        title: trimmedTitle === "" ? null : trimmedTitle,
      });
      setFile(null);
      setTitle("");
      if (fileInput.current !== null) fileInput.current.value = "";
      onImported(outcome);
    } catch (caught) {
      setSubmitError(toUiError(caught));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    // Cả form là đích kéo-thả bằng chuột; người dùng bàn phím dùng nút "Chọn file PDF" trong vùng thả.
    <form
      className={styles.form}
      onSubmit={(event) => void submit(event)}
      noValidate
      onDragOver={(event) => {
        event.preventDefault();
        if (!submitting) setDragging(true);
      }}
      onDragLeave={(event) => {
        // Chỉ tắt tô sáng khi con trỏ rời hẳn form, không phải khi đi qua phần tử con.
        const next = event.relatedTarget;
        if (!(next instanceof Node && event.currentTarget.contains(next))) setDragging(false);
      }}
      onDrop={onDrop}
    >
      <div className={styles.dropZone} data-active={dragging}>
        <p className={styles.dropTitle}>Kéo một file PDF vào đây</p>
        <p className={styles.hint}>
          Tối đa {DOCUMENT_UPLOAD_MAX_MIB} MiB và {DOCUMENT_MAX_PAGES} trang. PDF có mật khẩu mở không nhập được.
        </p>
        <input
          ref={fileInput}
          id={`${ids}-file`}
          type="file"
          accept="application/pdf,.pdf"
          className="visually-hidden"
          tabIndex={-1}
          aria-hidden="true"
          onChange={(event) => pick(event.target.files?.item(0) ?? null)}
          disabled={submitting}
        />
        <button
          type="button"
          className={styles.secondary}
          onClick={() => fileInput.current?.click()}
          disabled={submitting}
          aria-describedby={`${ids}-file-status`}
        >
          Chọn file PDF
        </button>
        <output id={`${ids}-file-status`} className={fileError === null ? styles.hint : styles.fieldError}>
          {fileError ?? (file === null ? "Chưa chọn file." : <span className={styles.fileName}>{file.name}</span>)}
        </output>
      </div>

      <div className={styles.fields}>
        <label className={styles.field}>
          <span className={styles.fieldLabel}>Chương</span>
          <input
            className={`${styles.input} ${styles.codeInput}`}
            value={chapter}
            onChange={(event) => setChapter(event.target.value)}
            aria-invalid={errors.chapter !== undefined}
            aria-describedby={`${ids}-chapter-hint`}
            placeholder="ch1"
            spellCheck={false}
            autoComplete="off"
            required
            disabled={submitting}
          />
          <span id={`${ids}-chapter-hint`} className={errors.chapter === undefined ? styles.hint : styles.fieldError}>
            {errors.chapter ?? "Ví dụ ch1, ch12 hoặc ch2.1. Kho tri thức xếp văn bản theo mã này."}
          </span>
        </label>
        <label className={styles.field}>
          <span className={styles.fieldLabel}>Loại tài liệu</span>
          <select
            className={styles.select}
            value={sourceKind}
            onChange={(event) => setSourceKind(event.target.value)}
            aria-invalid={errors.sourceKind !== undefined}
            aria-describedby={`${ids}-kind-hint`}
            required
            disabled={submitting}
          >
            <option value="">Chọn loại tài liệu</option>
            {SOURCE_KINDS.map((kind) => (
              <option key={kind} value={kind}>
                {SOURCE_KIND_LABEL_VI[kind]}
              </option>
            ))}
          </select>
          <span id={`${ids}-kind-hint`} className={errors.sourceKind === undefined ? styles.hint : styles.fieldError}>
            {errors.sourceKind ?? "Đề thi cũ không được cắt vào kho tri thức."}
          </span>
        </label>
        <label className={styles.field}>
          <span className={styles.fieldLabel}>Tên tài liệu (không bắt buộc)</span>
          <input
            className={`${styles.input} ${styles.wideInput}`}
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            aria-invalid={errors.title !== undefined}
            aria-describedby={`${ids}-title-hint`}
            disabled={submitting}
          />
          <span id={`${ids}-title-hint`} className={errors.title === undefined ? styles.hint : styles.fieldError}>
            {errors.title ?? "Bỏ trống thì dùng tên file."}
          </span>
        </label>
      </div>
      {errors.subject !== undefined && <p className={styles.fieldError}>{errors.subject}</p>}
      {submitError !== null && <ErrorNotice error={submitError} />}
      <div className={styles.actions}>
        <button type="submit" className={styles.primary} disabled={submitting || file === null}>
          {submitting ? "Đang gửi tài liệu…" : "Nhập tài liệu"}
        </button>
      </div>
    </form>
  );
}
