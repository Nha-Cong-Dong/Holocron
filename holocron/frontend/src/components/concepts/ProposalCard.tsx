"use client";

import {
  type AnimationEvent,
  createContext,
  type KeyboardEvent,
  type Ref,
  useContext,
  useEffect,
  useId,
  useRef,
  useState,
} from "react";

import { VerbatimQuote } from "@/components/common/VerbatimQuote";
import { Button, Field } from "@/components/ui";
import { type ConceptView, renameCloseFocus, renameKeyAction } from "@/lib/concept-board";
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

/**
 * Thẻ đề xuất báo lên tab Khái niệm khi mở/đóng khung sửa tên (`pairId`, `editing`), để "Nhận tất cả" bỏ qua thẻ đang
 * sửa thay vì nhận hộ dưới tên cũ. Không có nơi nhận (`null`) thì thẻ không báo.
 */
export const EditingConceptsContext = createContext<((pairId: string, editing: boolean) => void) | null>(null);

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
  /** Hộp chọn của thẻ (nhận hoặc từ chối nhiều cặp một lượt). */
  selected: boolean;
  /** Chọn được: đoạn nguồn tra được, thẻ không bận, không chờ cập nhật. */
  selectable: boolean;
  onToggle: () => void;
  /** Tên đã sửa nhưng chưa gửi (gửi khi nhận); `null` ⇒ giữ tên backend đề xuất. */
  termDraft: string | null;
  onSaveTerm: (term: string) => void;
  /** Mở đoạn gốc của thẻ và tô sáng trích dẫn. */
  onShowSource: () => void;
  /** Lý do thẻ thuộc nhóm "Cần kiểm tra"; `null` khi không thuộc nhóm. */
  checkReason: string | null;
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
  /** Mã đoạn lý do khoá khi nút lưu đang khoá (ví dụ khái niệm đã ghi, chờ cập nhật danh sách); `null` khi lưu được. */
  lockedReasonId: string | null;
  onSave: (term: string) => void;
  onCancel: () => void;
  /** Khung (`fieldset`): để biết focus còn nằm trong khung khi khung phải đóng. */
  frameRef?: Ref<HTMLFieldSetElement>;
}

/**
 * Khung sửa tên tại chỗ: Enter ở ô nhập lưu, Esc ở bất kỳ đâu trong khung huỷ (focus trở về nút đã mở khung). Nút lưu
 * khoá (`lockedReasonId`) thì vẫn nhận focus, đọc kèm lý do, và cả nút lẫn Enter đều không lưu.
 */
export function RenameForm({
  initial,
  submitLabel,
  busy,
  lockedReasonId,
  onSave,
  onCancel,
  frameRef,
}: RenameFormProps) {
  const id = useId();
  const [draft, setDraft] = useState(initial);
  const [error, setError] = useState<string | undefined>(undefined);

  useEffect(() => {
    const input = document.getElementById(`${id}-term`);
    if (input instanceof HTMLInputElement) input.select();
  }, [id]);

  function save(): void {
    if (busy || lockedReasonId !== null) return;
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
    <fieldset ref={frameRef} className={styles.renameFrame} aria-label="Sửa tên khái niệm" onKeyDown={onKeyDown}>
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
        <Button
          variant="secondary"
          busy={busy}
          busyLabel="Đang lưu"
          aria-disabled={lockedReasonId !== null || undefined}
          aria-describedby={lockedReasonId ?? undefined}
          onClick={save}
        >
          {submitLabel}
        </Button>
      </div>
    </fieldset>
  );
}

/**
 * Thẻ một khái niệm đề xuất: hộp chọn, thuật ngữ, trích dẫn nguyên văn kèm trang (khi tra được đoạn), nguồn đề xuất, và
 * các nút Bỏ / Xem trong đoạn gốc / Sửa / Nhận. "Sửa" chỉ đổi tên ("Lưu tên": giữ nháp, gửi khi nhận). Nút bị khoá theo
 * `pairActions` thì vẫn nhận focus và có lý do bằng chữ. Thẻ rời đi bằng `m-concept-accept` hoặc `m-concept-reject`.
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
  selected,
  selectable,
  onToggle,
  termDraft,
  onSaveTerm,
  onShowSource,
  checkReason,
}: ProposalCardProps) {
  const ids = useId();
  const [editing, setEditing] = useState(false);
  const returnFocus = useRef<"edit" | "card" | null>(null);
  const editRef = useRef<HTMLButtonElement>(null);
  const cardRef = useRef<HTMLElement>(null);
  const frameRef = useRef<HTMLFieldSetElement>(null);
  const reportEditing = useContext(EditingConceptsContext);

  useEffect(() => {
    if (editing || returnFocus.current === null) return;
    const target = returnFocus.current;
    returnFocus.current = null;
    (target === "card" ? cardRef.current : editRef.current)?.focus();
  }, [editing]);

  // Báo tab Khái niệm thẻ này đang mở khung sửa tên, để "Nhận tất cả" bỏ qua thẻ (nháp đang gõ không mất).
  useEffect(() => {
    if (!editing || reportEditing === null) return;
    reportEditing(pair.pair_id, true);
    return () => reportEditing(pair.pair_id, false);
  }, [editing, reportEditing, pair.pair_id]);

  // "Lưu và nhận" ghi xong thì thẻ khoá chờ ngay: đóng khung sửa. Focus chỉ dời khi đang nằm trong khung: thẻ đang rời
  // đi ⇒ chính thẻ (không rơi về trang), thẻ ở lại (giảm chuyển động) ⇒ "Sửa" (đọc kèm lý do khoá).
  useEffect(() => {
    if (!view.stale || !editing) return;
    const focusInFrame = frameRef.current?.contains(document.activeElement) === true;
    returnFocus.current = renameCloseFocus(focusInFrame, leaving !== null);
    setEditing(false);
  }, [view.stale, editing, leaving]);

  const acceptReason = gateReason(view.actions.confirm);
  const editReason = gateReason(view.actions.edit);
  const rejectReason = gateReason(view.actions.reject);
  const reason = acceptReason ?? editReason ?? rejectReason;
  const shownTerm = termDraft ?? pair.term;
  const reasonId = `${ids}-reason`;
  const leavingClass = leaving === "accept" ? "m-concept-accept" : leaving === "reject" ? "m-concept-reject" : "";

  function onAnimationEnd(event: AnimationEvent<HTMLElement>): void {
    if (leaving !== null && event.target === event.currentTarget) onLeft();
  }

  function cancelEdit(): void {
    returnFocus.current = "edit";
    setEditing(false);
  }

  return (
    <li>
      <article
        ref={cardRef}
        className={[styles.card, leavingClass].filter((name) => name !== "").join(" ")}
        aria-label={shownTerm}
        tabIndex={-1}
        data-concept-id={pair.pair_id}
        onAnimationEnd={onAnimationEnd}
      >
        <div className={styles.cardHead}>
          <div className={styles.cardSelect}>
            <input
              type="checkbox"
              className={styles.check}
              checked={selected}
              disabled={!selectable || busy}
              aria-label={`Chọn khái niệm ${shownTerm}`}
              aria-describedby={!selectable && reason !== null ? reasonId : undefined}
              onChange={onToggle}
            />
            {!editing && <h3 className={styles.term}>{shownTerm}</h3>}
            {!editing && termDraft !== null && <span className={styles.draftTag}>(đã sửa tên)</span>}
          </div>
          <span className={styles.source}>{view.sourceLabel}</span>
        </div>
        {checkReason !== null && <p className={styles.checkNote}>{checkReason}</p>}
        {editing && (
          <RenameForm
            initial={shownTerm}
            submitLabel="Lưu tên"
            busy={busy}
            lockedReasonId={null}
            onSave={(term) => {
              returnFocus.current = "edit";
              onSaveTerm(term);
              setEditing(false);
            }}
            onCancel={cancelEdit}
            frameRef={frameRef}
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
            <button type="button" className={styles.textButton} onClick={onShowSource}>
              Xem trong đoạn gốc
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
              onClick={() => {
                if (!busy && acceptReason === null) onAccept(termDraft);
              }}
            >
              Nhận
            </Button>
          </div>
        )}
      </article>
    </li>
  );
}
