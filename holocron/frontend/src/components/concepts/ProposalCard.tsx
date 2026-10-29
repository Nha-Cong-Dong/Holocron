"use client";

import { type AnimationEvent, type KeyboardEvent, useEffect, useId, useRef, useState } from "react";

import { VerbatimQuote } from "@/components/common/VerbatimQuote";
import { Button, Field } from "@/components/ui";
import { type ConceptView, renameKeyAction } from "@/lib/concept-board";
import { type ActionGate, pairCitation, termLengthOk, termSegments } from "@/lib/pairs";
import { pageRangeLabel } from "@/lib/vault";
import type { ConceptPair } from "@/types/holocron";

import styles from "./ConceptsTab.module.css";

/** Thuộc tính đánh dấu thẻ của một khái niệm (để đặt focus theo mã khái niệm). */
export const CONCEPT_CARD_ATTR = "data-concept-id";
/** Thuộc tính đánh dấu nút "Sửa"/"Sửa tên" của một khái niệm (Esc khi sửa thì focus trở về đây). */
export const CONCEPT_EDIT_ATTR = "data-concept-edit";

/** Dòng hướng dẫn trên khung sửa: chỉ sửa được tên. */
export const RENAME_HINT_VI = "Muốn đổi đoạn trích thì bỏ khái niệm này rồi tạo lại.";

export type CardLeaving = "accept" | "reject" | null;

interface ProposalCardProps {
  pair: ConceptPair;
  view: ConceptView;
  docTitle: string | null;
  /** Đang gửi nhận/bỏ của riêng thẻ này hoặc của "Nhận tất cả". */
  busy: boolean;
  leaving: CardLeaving;
  /** Câu lỗi còn giữ trên thẻ (nhận hay bỏ không thành); kèm "Thử lại". */
  error: string | null;
  onAccept: (term: string | null) => void;
  onReject: () => void;
  onRetry: () => void;
  /** Animation rời đi (`m-concept-accept`/`m-concept-reject`) đã chạy xong. */
  onLeft: () => void;
}

function gateReason(gate: ActionGate): string | null {
  return gate.shown && !gate.enabled ? gate.reason : null;
}

/** Trích dẫn của khái niệm: nguyên văn đã kiểm chứng khi tra được đoạn; không thì chữ thường kèm câu chưa kiểm chứng. */
export function ConceptQuote({
  pair,
  view,
  docTitle,
}: {
  pair: ConceptPair;
  view: ConceptView;
  docTitle: string | null;
}) {
  if (view.chunk === null) {
    return (
      <div>
        <p className={styles.plainQuote}>{pair.definition_quote}</p>
        <p className={styles.unverified}>{view.unverified}</p>
      </div>
    );
  }
  return (
    <VerbatimQuote
      citation={pairCitation(pair, view.chunk)}
      sourceTitle={docTitle}
      pageLabel={pageRangeLabel(view.chunk)}
      highlights={termSegments(pair.definition_quote, pair.term_spans)}
    />
  );
}

interface RenameFormProps {
  initial: string;
  submitLabel: string;
  busy: boolean;
  onSave: (term: string) => void;
  onCancel: () => void;
}

/** Khung sửa tên tại chỗ: Enter ở ô nhập lưu, Esc ở bất kỳ đâu trong khung huỷ (focus trở về nút đã mở khung). */
export function RenameForm({ initial, submitLabel, busy, onSave, onCancel }: RenameFormProps) {
  const id = useId();
  const [draft, setDraft] = useState(initial);
  const [error, setError] = useState<string | undefined>(undefined);

  useEffect(() => {
    const input = document.getElementById(`${id}-term`);
    if (input instanceof HTMLInputElement) input.select();
  }, [id]);

  function save(): void {
    const check = termLengthOk(draft);
    if (!check.ok) {
      setError(check.message);
      return;
    }
    setError(undefined);
    onSave(draft.trim());
  }

  // Esc ở bất kỳ đâu trong khung (ô nhập, "Huỷ", nút lưu) đều huỷ; Enter chỉ lưu khi đang ở ô nhập.
  function onKeyDown(event: KeyboardEvent<HTMLFieldSetElement>): void {
    const action = renameKeyAction(event.key, event.target instanceof HTMLInputElement);
    if (action === null) return;
    event.preventDefault();
    if (action === "cancel") onCancel();
    else save();
  }

  return (
    <fieldset className={styles.renameFrame} aria-label="Sửa tên khái niệm" onKeyDown={onKeyDown}>
      <Field
        id={`${id}-term`}
        label="Tên khái niệm"
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        error={error}
        hint={RENAME_HINT_VI}
      />
      <div className={styles.actions}>
        <button type="button" className={styles.textButton} onClick={onCancel}>
          Huỷ
        </button>
        <Button variant="secondary" busy={busy} busyLabel="Đang lưu" onClick={save}>
          {submitLabel}
        </Button>
      </div>
    </fieldset>
  );
}

/**
 * Thẻ một khái niệm đề xuất: thuật ngữ, trích dẫn nguyên văn kèm trang (khi tra được đoạn), nguồn đề xuất, và ba nút
 * Bỏ / Sửa / Nhận. "Sửa" chỉ đổi tên ("Lưu và nhận"). Nút bị khoá theo `pairActions` thì vẫn nhận focus và có lý do bằng
 * chữ. Thẻ rời đi bằng `m-concept-accept` hoặc `m-concept-reject`.
 */
export function ProposalCard({
  pair,
  view,
  docTitle,
  busy,
  leaving,
  error,
  onAccept,
  onReject,
  onRetry,
  onLeft,
}: ProposalCardProps) {
  const ids = useId();
  const [editing, setEditing] = useState(false);
  const returnFocus = useRef(false);
  const editRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (editing || !returnFocus.current) return;
    returnFocus.current = false;
    editRef.current?.focus();
  }, [editing]);

  const acceptReason = gateReason(view.actions.confirm);
  const editReason = gateReason(view.actions.edit);
  const rejectReason = gateReason(view.actions.reject);
  const reason = acceptReason ?? editReason ?? rejectReason;
  const reasonId = `${ids}-reason`;
  const leavingClass = leaving === "accept" ? "m-concept-accept" : leaving === "reject" ? "m-concept-reject" : "";

  function onAnimationEnd(event: AnimationEvent<HTMLElement>): void {
    if (leaving !== null && event.target === event.currentTarget) onLeft();
  }

  function cancelEdit(): void {
    returnFocus.current = true;
    setEditing(false);
  }

  return (
    <li>
      <article
        className={[styles.card, leavingClass].filter((name) => name !== "").join(" ")}
        aria-label={pair.term}
        tabIndex={-1}
        data-concept-id={pair.pair_id}
        onAnimationEnd={onAnimationEnd}
      >
        <div className={styles.cardHead}>
          {!editing && <h3 className={styles.term}>{pair.term}</h3>}
          <span className={styles.source}>{view.sourceLabel}</span>
        </div>
        {editing && (
          <RenameForm
            initial={pair.term}
            submitLabel="Lưu và nhận"
            busy={busy}
            onSave={(term) => onAccept(term)}
            onCancel={cancelEdit}
          />
        )}
        <ConceptQuote pair={pair} view={view} docTitle={docTitle} />
        {reason !== null && (
          <p id={reasonId} className={styles.reason}>
            {reason}
          </p>
        )}
        {error !== null && (
          <div className={styles.actions}>
            <p className={styles.error} role="alert">
              {error}
            </p>
            <Button variant="secondary" busy={busy} busyLabel="Đang thử lại" onClick={onRetry}>
              Thử lại
            </Button>
          </div>
        )}
        {!editing && (
          <div className={styles.actions}>
            <button
              type="button"
              className={styles.textButton}
              aria-disabled={busy || rejectReason !== null || undefined}
              aria-describedby={rejectReason !== null ? reasonId : undefined}
              onClick={() => {
                if (!busy && rejectReason === null) onReject();
              }}
            >
              Bỏ
            </button>
            <button
              ref={editRef}
              type="button"
              className={styles.textButton}
              aria-disabled={busy || editReason !== null || undefined}
              aria-describedby={editReason !== null ? reasonId : undefined}
              data-concept-edit={pair.pair_id}
              onClick={() => {
                if (!busy && editReason === null) setEditing(true);
              }}
            >
              Sửa
            </button>
            <Button
              variant="secondary"
              busy={busy}
              busyLabel="Đang nhận"
              aria-disabled={acceptReason !== null || undefined}
              aria-describedby={acceptReason !== null ? reasonId : undefined}
              onClick={() => onAccept(null)}
            >
              Nhận
            </Button>
          </div>
        )}
      </article>
    </li>
  );
}
