"use client";

import { type KeyboardEvent, useCallback, useEffect, useId, useRef, useState } from "react";

import { api, toUiError } from "@/lib/api";
import type { UiError } from "@/lib/errors";
import { canSubmit, quoteLengthOk, termLengthOk } from "@/lib/pairs";
import type { ChunkView } from "@/lib/vault";
import type { ConceptPair } from "@/types/holocron";

import { ChunkHighlighter } from "./ChunkHighlighter";
import { PairErrorNotice } from "./PairErrorNotice";
import styles from "./Pairs.module.css";

export type EditorTarget = { mode: "create"; chunk: ChunkView } | { mode: "edit"; chunk: ChunkView; pair: ConceptPair };

interface PairEditorProps {
  target: EditorTarget;
  /** Lưu thành công: cặp backend trả (đã kiểm dạng). */
  onSaved: (pair: ConceptPair) => void;
  /** Huỷ sửa (nút "Huỷ" hoặc Esc); chỉ có ở chế độ sửa. */
  onCancel: () => void;
  onReloadList: () => void;
  onReloadAll: () => void;
}

interface FieldErrors {
  term?: string;
  quote?: string;
}

/**
 * Khung nội tuyến (không phải hộp thoại) để tạo cặp từ chunk hoặc sửa một cặp rồi xác nhận. Đoạn định nghĩa lấy bằng
 * cách bôi trong văn bản chunk, hoặc gõ, dán vào ô "Nhập hoặc dán định nghĩa"; đoạn chưa lưu chỉ hiện ở khung xem
 * trước có nhãn "chưa lưu", không hiện như trích dẫn đã kiểm chứng. Gửi đúng chuỗi người dùng chọn; backend gộp khoảng
 * trắng và kiểm nguyên văn (I3). Esc ở chế độ sửa huỷ và trả focus về nút "Sửa" (do màn cha làm).
 */
export function PairEditor({ target, onSaved, onCancel, onReloadList, onReloadAll }: PairEditorProps) {
  const ids = useId();
  const editing = target.mode === "edit" ? target.pair : null;
  const [term, setTerm] = useState(editing?.term ?? "");
  const [quote, setQuote] = useState(editing?.definition_quote ?? "");
  const [selectionError, setSelectionError] = useState<string | null>(null);
  const [errors, setErrors] = useState<FieldErrors>({});
  const [pending, setPending] = useState(false);
  const [saveError, setSaveError] = useState<UiError | null>(null);
  const termInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (editing !== null) termInput.current?.focus();
  }, [editing]);

  const onSelect = useCallback((selected: string) => {
    setQuote(selected);
    setSelectionError(null);
    setErrors((current) => ({ ...current, quote: undefined }));
  }, []);
  const onInvalid = useCallback((message: string) => setSelectionError(message), []);

  async function save(): Promise<void> {
    if (!canSubmit({ pending })) return;
    const termCheck = termLengthOk(term);
    const quoteCheck = quoteLengthOk(quote);
    const next: FieldErrors = {};
    if (!termCheck.ok) next.term = termCheck.message;
    if (!quoteCheck.ok) next.quote = quoteCheck.message;
    setErrors(next);
    if (!termCheck.ok || !quoteCheck.ok) return;
    setPending(true);
    setSaveError(null);
    try {
      const saved =
        editing === null
          ? await api.createConceptPair(target.chunk.chunkId, term, quote)
          : await api.confirmConceptPair(editing.pair_id, editing.version, {
              term: term === editing.term ? undefined : term,
              definitionQuote: quote === editing.definition_quote ? undefined : quote,
            });
      setPending(false);
      onSaved(saved);
    } catch (caught) {
      setSaveError(toUiError(caught));
      setPending(false);
    }
  }

  function onKeyDown(event: KeyboardEvent<HTMLFormElement>): void {
    if (event.key !== "Escape" || editing === null || pending) return;
    event.preventDefault();
    event.stopPropagation();
    onCancel();
  }

  const title = editing === null ? "Tạo cặp từ chunk này" : `Sửa cặp “${editing.term}”`;

  return (
    <div className={styles.preview}>
      <ChunkHighlighter chunk={target.chunk} onSelect={onSelect} onInvalid={onInvalid} />
      {selectionError !== null && (
        <p className={styles.fieldError} role="status">
          {selectionError}
        </p>
      )}
      <form
        className={styles.form}
        onSubmit={(event) => {
          event.preventDefault();
          void save();
        }}
        onKeyDown={onKeyDown}
        noValidate
        aria-labelledby={`${ids}-title`}
      >
        <h3 id={`${ids}-title`}>{title}</h3>
        <label className={styles.field}>
          <span className={styles.fieldLabel}>Thuật ngữ</span>
          <input
            ref={termInput}
            className={styles.input}
            value={term}
            onChange={(event) => setTerm(event.target.value)}
            aria-invalid={errors.term !== undefined}
            aria-describedby={`${ids}-term`}
            disabled={pending}
          />
          <span id={`${ids}-term`} className={errors.term === undefined ? styles.hint : styles.fieldError}>
            {errors.term ?? "Khái niệm mà đoạn bên dưới định nghĩa."}
          </span>
        </label>
        <div className={styles.field}>
          <span className={styles.fieldLabel}>
            Đoạn định nghĩa <span className={styles.badge}>chưa lưu</span>
          </span>
          {pairQuotePreview(quote)}
        </div>
        <label className={styles.field}>
          <span className={styles.fieldLabel}>Nhập hoặc dán định nghĩa</span>
          <textarea
            className={`${styles.input} ${styles.textarea}`}
            value={quote}
            onChange={(event) => {
              setQuote(event.target.value);
              setSelectionError(null);
            }}
            aria-invalid={errors.quote !== undefined}
            aria-describedby={`${ids}-quote`}
            disabled={pending}
          />
          <span id={`${ids}-quote`} className={errors.quote === undefined ? styles.hint : styles.fieldError}>
            {errors.quote ??
              "Bôi một đoạn trong văn bản của chunk ở trên, hoặc gõ, dán vào đây. Đoạn phải trùng nguyên văn với văn bản của chunk."}
          </span>
        </label>
        {saveError !== null && (
          <PairErrorNotice
            error={saveError}
            operation={editing === null ? "create" : "confirm"}
            onRetry={() => void save()}
            onReloadList={onReloadList}
            onReloadAll={onReloadAll}
          />
        )}
        <div className={styles.actions}>
          <button type="submit" className={styles.primary} disabled={!canSubmit({ pending })}>
            {pending ? "Đang lưu…" : "Lưu và xác nhận"}
          </button>
          {editing !== null && (
            <button type="button" className={styles.secondary} onClick={onCancel} disabled={pending}>
              Huỷ
            </button>
          )}
        </div>
        <p className={styles.hint}>
          {editing === null
            ? "Cặp do bạn bôi được xác nhận ngay."
            : "Lưu sẽ xác nhận cặp với thuật ngữ và đoạn định nghĩa ở trên. Esc để huỷ sửa."}
        </p>
      </form>
    </div>
  );
}

/** Khung xem trước đoạn chưa lưu: chữ thường, giữ xuống dòng, không qua `VerbatimQuote`. */
function pairQuotePreview(quote: string) {
  return quote === "" ? (
    <p className={styles.hint}>Chưa có đoạn nào. Bôi trong văn bản của chunk hoặc nhập ở ô dưới.</p>
  ) : (
    <p className={styles.previewText}>{quote}</p>
  );
}
